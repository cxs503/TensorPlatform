"""语料转换测试：真实工件 → AIL 条目，来源追踪与覆盖率必须可核验（协议 §5）。"""
from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from tools.ail import convert, corpus
from tools.ail.convert import convert_records, coverage_report
from tools.ail.model import canonical


class CorpusSourceTests(unittest.TestCase):
    def setUp(self):
        self.records = corpus.extract_all()

    def test_all_three_corpora_present(self):
        counts = {}
        for record in self.records:
            counts[record.corpus] = counts.get(record.corpus, 0) + 1
        self.assertEqual(set(counts), {"tensorplatform", "tensorphysics", "tensordem"})
        for corpus_name, count in counts.items():
            self.assertGreater(count, 0, corpus_name)

    def test_every_record_carries_source_sha256_and_pointer(self):
        for record in self.records:
            self.assertEqual(len(record.sha256), 64, record.record_id)
            self.assertTrue(record.pointer, record.record_id)
            self.assertTrue(record.source_path, record.record_id)
            self.assertTrue(record.retrieved_at.endswith("Z"), record.record_id)

    def test_source_paths_exist_and_are_outside_the_platform_worktree(self):
        """语料来自真实文件：路径必须可解析（平台文件走仓库内相对路径）。"""
        for record in self.records:
            path = Path(record.source_path)
            if not path.is_absolute():
                candidate = corpus.ECOSYSTEM_ROOT / path
                if not candidate.is_file():
                    candidate = corpus.PLATFORM_ROOT / path
                path = candidate
            self.assertTrue(path.is_file(), record.source_path)

    def test_declared_sha256_matches_file_on_disk(self):
        """来源哈希可复核：抽查每个来源文件的哈希（慢，但这是"来源可追"的判据）。"""
        seen: dict[str, str] = {}
        for record in self.records:
            seen.setdefault(record.source_path, record.sha256)
        # 只抽查生态内文件（agent_memory 账本会随班次被 append，哈希按抽取时点固化）
        checked = 0
        for source_path, declared in seen.items():
            if "agent_memory" in source_path:
                continue
            path = Path(source_path)
            if not path.is_absolute():
                candidate = corpus.ECOSYSTEM_ROOT / path
                if not candidate.is_file():
                    candidate = corpus.PLATFORM_ROOT / path
                path = candidate
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(digest, declared, f"哈希不符（来源已变？）：{source_path}")
            checked += 1
        self.assertGreater(checked, 5)

    def test_extraction_is_deterministic(self):
        again = corpus.extract_all()
        self.assertEqual([r.to_dict() for r in self.records],
                         [r.to_dict() for r in again])


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.records = corpus.extract_all()
        self.grouped: dict[str, list] = {}
        for record in self.records:
            self.grouped.setdefault(record.corpus, []).append(record)

    def test_full_coverage_no_silent_skips(self):
        for name, subset in self.grouped.items():
            result = convert_records(subset, corpus=name)
            report = coverage_report(subset, result.items)
            self.assertEqual(report["coverage"], 1.0, f"{name}: {result.skipped[:3]}")
            self.assertEqual(result.skipped, [], name)

    def test_items_are_schema_valid_and_traceable(self):
        for name, subset in self.grouped.items():
            result = convert_records(subset, corpus=name)
            evidence_ids = {e.evidence_id for e in result.evidence}
            for item in result.items:
                self.assertTrue(set(item.evidence_refs) <= evidence_ids, item.item_id)
                self.assertEqual(item.status, "active")
                self.assertIn(item.sensitivity, ("private", "organization", "shared"))
                # source 必须指向真实来源记录（路径 + 指针）
                self.assertIn("#", item.source["locator"])
                self.assertEqual(len(item.source["sha256"]), 64)

    def test_conversion_is_deterministic(self):
        for name, subset in self.grouped.items():
            first = convert_records(subset, corpus=name)
            second = convert_records(subset, corpus=name)
            self.assertEqual(convert.item_digest(first.items),
                             convert.item_digest(second.items), name)

    def test_known_gold_values_survive_conversion(self):
        """真实判据数字必须逐字保真（不是"大约"）。"""
        platform_items = convert_records(self.grouped["tensorplatform"],
                                        corpus="tensorplatform").items
        by_topic = {i.topic: i.value for i in platform_items}
        self.assertEqual(by_topic.get("platform.guardian.count_identity"), 16)
        self.assertEqual(by_topic.get("platform.guardian.base_instances"), 6)
        self.assertEqual(by_topic.get("platform.shift.inflight_count"), 2)
        self.assertEqual(by_topic.get("platform.relay.http_code"), 200)
        self.assertEqual(by_topic.get("platform.shift.failed_24h"), 18)
        self.assertEqual(by_topic.get("platform.shift.gateway_unavailable_24h"), 17)

    def test_temporal_series_are_closed_not_conflicting(self):
        """同 topic 的历史序列 → 区间闭合（C2 演进），不得报成 C1 矛盾。"""
        from tools.ail.bundle import AILBundle

        result = convert_records(self.grouped["tensorplatform"], corpus="tensorplatform")
        bundle = AILBundle.create(bundle_id="t", session=result.session)
        for ev in result.evidence:
            bundle.add_evidence(ev)
        for item in result.items:
            bundle.add_item(item)
        self.assertEqual(bundle.detect_conflicts(), [])

    def test_verification_never_upgraded_by_converter(self):
        """I3：转换器不得把记录升格成 passed（除非判据报告自己写了 passed=true）。"""
        for name, subset in self.grouped.items():
            result = convert_records(subset, corpus=name)
            for item in result.items:
                if item.verification["status"] == "passed":
                    self.assertIn("validation_report", item.verification["validator"] or "")

    def test_all_quads_have_source_items(self):
        """每个探针维度的 gold 都必须能在条目里找到对应值（否则基准是在考空气）。"""
        from tools.ail.probes import PROBES

        by_corpus = {name: convert_records(subset, corpus=name)
                     for name, subset in self.grouped.items()}
        for probe in PROBES:
            items = by_corpus[probe.corpus].items
            values = {canonical(i.value) for i in items}
            gold = canonical(probe.gold["value"])
            hit = gold in values or any(
                str(probe.gold["value"]) in i.text or
                canonical(probe.gold["value"]) in canonical(i.value)
                for i in items)
            self.assertTrue(hit, f"{probe.probe_id} 的 gold 在 {probe.corpus} 条目里不存在")


if __name__ == "__main__":
    unittest.main()