"""基准与判分测试（基准协议 §2 判据、§3 四臂、§4 预算档）。"""
from __future__ import annotations

import unittest

from tools.ail import arms, probes
from tools.ail.arms import ARM_NAMES, run_arm
from tools.ail.corpus import extract_all, load_snapshot
from tools.ail.probes import PROBES, Probe, quad_coverage, score_context
from tools.ail.runner import TIERS, build_corpus_bundles, run_benchmark
from tools.ail.tokens import default_counter


class ProbeContractTests(unittest.TestCase):
    def test_four_quads_all_covered(self):
        counts = quad_coverage()
        for quad in ("state", "decision", "evidence", "next_action"):
            self.assertGreater(counts[quad], 0, quad)

    def test_every_probe_has_gold_and_locator(self):
        for probe in PROBES:
            self.assertIn("value", probe.gold)
            self.assertTrue(probe.question)
            self.assertIn(probe.corpus, ("tensorplatform", "tensorphysics", "tensordem"))
            self.assertIn(probe.intent,
                          ("state", "decision", "evidence", "next_action", "recall"))

    def test_probe_ids_unique(self):
        ids = [p.probe_id for p in PROBES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_gold_locators_point_at_real_source_files(self):
        from pathlib import Path
        from tools.ail import corpus

        for probe in PROBES:
            locator = probe.gold.get("evidence_locator")
            if not locator:
                continue
            candidate = corpus.ECOSYSTEM_ROOT / locator
            if not candidate.is_file():
                candidate = corpus.PLATFORM_ROOT / locator
            self.assertTrue(candidate.is_file(), f"{probe.probe_id}: {locator}")


class ScoreTests(unittest.TestCase):
    def probe(self, **gold):
        payload = {"value": 16, "must_contain": [], "must_not_contain": []}
        payload.update(gold)
        return Probe(probe_id="p", quad="state", corpus="tensorplatform", intent="state",
                     question="q", gold=payload)

    def test_missing_value_scores_zero_on_answerable(self):
        result = score_context(self.probe(), "上下文里没有任何数字")
        self.assertEqual(result["answerable"], 0.0)
        self.assertEqual(result["exact"], 0.0)
        # traceable/no_confident_error 为 n/a，按 1.0 计 → 2/4。n/a 的个数必须披露。
        self.assertEqual(result["score"], 0.5)
        self.assertEqual([k for k, v in result["applicable"].items() if not v],
                         ["traceable", "no_confident_error"])

    def test_exact_number_present(self):
        result = score_context(self.probe(), "platform.guardian.count=16")
        self.assertEqual(result["answerable"], 1.0)
        self.assertEqual(result["exact"], 1.0)

    def test_number_boundary_not_fooled_by_longer_number(self):
        result = score_context(self.probe(), "count=160 and 16.5")
        self.assertEqual(result["answerable"], 0.0)

    def test_must_not_contain_penalises_confident_error(self):
        result = score_context(self.probe(must_not_contain=["14"]), "count=16 or 14")
        self.assertEqual(result["no_confident_error"], 0.0)

    def test_string_value_matches(self):
        probe = self.probe(value="not_a_physics_benchmark")
        result = score_context(probe, "scientific_status=not_a_physics_benchmark")
        self.assertEqual(result["exact"], 1.0)

    def test_boolean_value_matches(self):
        probe = self.probe(value=True, must_contain=["true"])
        result = score_context(probe, "expired: true")
        self.assertEqual(result["answerable"], 1.0)

    def test_traceable_requires_locator_in_context(self):
        probe = self.probe(evidence_locator="operator_log/2026-10-23.m192.readings.json")
        without = score_context(probe, "count=16")
        with_it = score_context(probe, "count=16 src operator_log/2026-10-23.m192.readings.json")
        self.assertEqual(without["traceable"], 0.0)
        self.assertEqual(with_it["traceable"], 1.0)

    def test_applicability_is_reported(self):
        result = score_context(self.probe(), "16")
        self.assertFalse(result["applicable"]["traceable"])
        self.assertFalse(result["applicable"]["no_confident_error"])

    def test_score_is_average_of_four_terms(self):
        result = score_context(self.probe(), "16")
        expected = (result["answerable"] + result["exact"] + result["traceable"]
                    + result["no_confident_error"]) / 4
        self.assertAlmostEqual(result["score"], expected, places=6)


class ArmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = extract_all()
        cls.grouped: dict[str, list] = {}
        for record in cls.records:
            cls.grouped.setdefault(record.corpus, []).append(record)
        cls.bundles, _ = build_corpus_bundles(cls.records)
        cls.counter = default_counter()

    def test_four_arms_registered(self):
        self.assertEqual(set(ARM_NAMES), {"raw", "nl_summary", "json_struct", "ail"})

    def test_each_arm_is_deterministic(self):
        subset = self.grouped["tensorphysics"]
        bundle = self.bundles["tensorphysics"]
        for arm in ARM_NAMES:
            first = run_arm(arm, records=subset, bundle=bundle, token_budget=800,
                            counter=self.counter, intent="state", query="阶段",
                            now="2026-10-23T07:00:00Z")
            second = run_arm(arm, records=subset, bundle=bundle, token_budget=800,
                             counter=self.counter, intent="state", query="阶段",
                             now="2026-10-23T07:00:00Z")
            self.assertEqual(first.text, second.text, arm)
            self.assertEqual(first.tokens, second.tokens, arm)

    def test_budgeted_arms_respect_token_budget(self):
        subset = self.grouped["tensordem"]
        bundle = self.bundles["tensordem"]
        for arm in ARM_NAMES:
            for _, budget in TIERS:
                out = run_arm(arm, records=subset, bundle=bundle, token_budget=budget,
                              counter=self.counter, intent="evidence", query="protocol",
                              now="2026-10-23T07:00:00Z")
                self.assertLessEqual(out.tokens, budget, f"{arm} 超预算 {budget}")

    def test_raw_arm_is_by_far_the_most_expensive(self):
        subset = self.grouped["tensorplatform"]
        bundle = self.bundles["tensorplatform"]
        outs = {arm: run_arm(arm, records=subset, bundle=bundle, token_budget=None,
                             counter=self.counter, intent="recall", query="",
                             now="2026-10-23T07:00:00Z")
                for arm in ARM_NAMES}
        self.assertGreater(outs["raw"].tokens, outs["ail"].tokens)
        self.assertFalse(outs["raw"].truncated)   # 无预算臂即"自然成本"

    def test_nl_summary_is_smaller_than_raw(self):
        subset = self.grouped["tensorplatform"]
        out_raw = run_arm("raw", records=subset, bundle=None, token_budget=None,
                          counter=self.counter)
        out_nl = run_arm("nl_summary", records=subset, bundle=None, token_budget=None,
                         counter=self.counter)
        self.assertLess(out_nl.tokens, out_raw.tokens)

    def test_unknown_arm_raises(self):
        with self.assertRaises(KeyError):
            run_arm("nope", records=[], bundle=None, token_budget=100, counter=self.counter)


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = extract_all()
        cls.payload = run_benchmark(records=cls.records)

    def test_all_arms_all_tiers_all_probes_present(self):
        payload = self.payload
        self.assertEqual(len(payload["probes"]), len(PROBES))
        for probe in payload["probes"]:
            for arm in ARM_NAMES:
                self.assertIn(arm, probe["arms"])
                for tier_name, _ in TIERS:
                    cell = probe["arms"][arm]["tiers"][tier_name]
                    self.assertIn("score", cell)
                    self.assertIn("tokens", cell)

    def test_tokenizer_caliber_recorded(self):
        self.assertIn(self.payload["tokenizer"]["kind"], ("tiktoken", "char_estimate"))
        self.assertTrue(self.payload["tokenizer"]["note"])

    def test_summary_has_quad_breakdown(self):
        table = self.payload["summary"]["table"]
        for arm in ARM_NAMES:
            for quad in ("state", "decision", "evidence", "next_action"):
                self.assertIn(quad, table[arm]["by_quad_T2_normal"])

    def test_ail_beats_baselines_on_recovery_at_T2(self):
        """本阶段的核心判据：同预算下 AIL 的四元组恢复准确率高于三个基线。"""
        table = self.payload["summary"]["table"]
        ail = table["ail"]["tiers"]["T2_normal"]["recovery_accuracy"]
        for arm in ("raw", "nl_summary", "json_struct"):
            self.assertGreater(ail, table[arm]["tiers"]["T2_normal"]["recovery_accuracy"], arm)

    def test_latency_measured_for_every_cell(self):
        """Issue #74 判据之一：延迟必须实测，不能缺项也不能是编的常数。"""
        for probe in self.payload["probes"]:
            for arm in ARM_NAMES:
                for tier_name, _ in TIERS:
                    cell = probe["arms"][arm]["tiers"][tier_name]
                    self.assertIn("latency_ms_median", cell)
                    self.assertGreater(cell["latency_ms_median"], 0.0)
                    self.assertEqual(len(cell["latency_samples_ms"]), 3)
        table = self.payload["summary"]["table"]
        for arm in ARM_NAMES:
            for tier_name, _ in TIERS:
                self.assertIsNotNone(
                    table[arm]["tiers"][tier_name]["latency_ms_median_mean"])

    def test_ail_latency_same_order_as_raw(self):
        """AIL 装配是纯本地字符串操作，延迟应与原样拼接同一数量级（不做数量级外推）。"""
        table = self.payload["summary"]["table"]
        raw = table["raw"]["tiers"]["T2_normal"]["latency_ms_median_mean"]
        ail = table["ail"]["tiers"]["T2_normal"]["latency_ms_median_mean"]
        self.assertLess(ail, raw * 10, f"ail={ail} raw={raw}")

    def test_ail_uses_far_fewer_tokens_than_raw_natural(self):
        table = self.payload["summary"]["table"]
        self.assertLess(table["ail"]["natural_tokens_mean"],
                        table["raw"]["natural_tokens_mean"] / 3)
        # 并且同预算下准确率不低于 raw 的自然（无预算）准确率
        self.assertGreaterEqual(table["ail"]["tiers"]["T2_normal"]["recovery_accuracy"],
                                table["raw"]["natural_recovery_accuracy"] - 0.05)

    def test_limits_are_declared(self):
        joined = " ".join(self.payload["limits"])
        self.assertIn("NOT RUN", joined)
        self.assertIn("LLMLingua", joined)

    def test_corpus_metadata_has_checksums(self):
        for name, meta in self.payload["corpora"].items():
            self.assertEqual(len(meta["bundle_checksum"]), 64, name)
            self.assertEqual(meta["coverage"]["coverage"], 1.0, name)

    def test_bundles_roundtrip_from_jsonl(self):
        from tools.ail.bundle import AILBundle

        bundles, meta = build_corpus_bundles(self.records)
        for name, bundle in bundles.items():
            restored = AILBundle.from_jsonl(bundle.to_jsonl())
            self.assertEqual(restored.to_document()["checksum"], meta[name]["bundle_checksum"])


class SnapshotTests(unittest.TestCase):
    def test_snapshot_roundtrip_if_present(self):
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "tools/ail/corpus/records.snapshot.json"
        if not path.is_file():
            self.skipTest("快照未生成（先跑 scripts/run_ail_benchmark.py）")
        records, payload = load_snapshot(path)
        self.assertEqual(len(records), payload["record_count"])
        self.assertIn("sources", payload)
        for source in payload["sources"]:
            self.assertEqual(len(source["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()