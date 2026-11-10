"""流水线层：差异报告（C4）、零领域语义（C5）、与升级管线联动（C7）、CLI 闭环。

判据
----
* C4：生成 JSON 差异报告，含新增 / 更新 / 缺失能力列表；
* C5：扫描器不依赖任何域词条——换一套能力词汇，提取结构必须同构；
* C7：自动登记的能力可直接进入 ``capability_upgrade`` 自动升级管线
  （用**对方的**加载器/存储校验，不是自己校验自己）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.capability_discovery import export as export_mod
from tools.capability_discovery import pipeline as pipeline_mod
from tools.capability_discovery.markers import MarkerScanner
from tools.capability_discovery.plugin import load_outcome_records, load_outcomes
from tools.capability_discovery.store import Ledger

SUITE_REL = "tools/capability_discovery/fixtures/synthetic_suite"
SUITE = ROOT / SUITE_REL
OUTCOMES = ROOT / "tools/capability_discovery/fixtures/expected_outcomes.json"

# 手算的等级预期（依据 grading.DEFAULT_POLICY；见 docs/capability_discovery.md 推导表）
EXPECTED_LEVELS = {
    "synth.grid.trapezoid": "REAL",            # passed + declared REAL + anchors
    "synth.grid.richardson": "REAL",           # parametrised, passed + anchors
    "synth.algebra.vector_norm": "REAL",       # class marker, passed + anchors
    "synth.claims.no_anchor": "WITHHELD",      # REAL claimed but no anchor
    "synth.case.skipped": "WITHHELD",          # skipped
    "synth.case.xfail": "WITHHELD",            # xfailed
    "synth.case.contradiction": "MOCK",        # failed -> contradicted
    "synth.family.module_scope": "WITHHELD",   # module marker, no declared level
    "synth.dynamic.value": "WITHHELD",         # runtime-only marker, no anchors
    "derived.test_synthetic_core.test_bare_marker_case": "WITHHELD",  # bare marker
}


def run_pipeline(tmp_path: Path, roots=(SUITE_REL,), outcomes_path: Path | None = OUTCOMES, write=False):
    ledger = Ledger(path=tmp_path / "ledger.json")
    outcomes = load_outcomes(outcomes_path) if outcomes_path else {}
    items = load_outcome_records(outcomes_path) if outcomes_path else None
    return pipeline_mod.run(
        roots=[str(r) for r in roots],
        root=ROOT,
        outcomes=outcomes,
        outcome_items=items,
        ledger=ledger,
        write_ledger=write,
        run_id="test-run",
    )


class TestReport:
    def test_report_shape_and_counts(self, tmp_path):
        result = run_pipeline(tmp_path)
        rep = result.report
        assert rep["schema"] == "capability_discovery.report.v1"
        assert rep["run_id"] == "test-run"
        for key in ("added", "updated", "missing", "unchanged", "held_retracted", "diagnostics"):
            assert key in rep, f"差异报告缺少 {key}"
            assert isinstance(rep[key], list)
        s = rep["summary"]
        assert s["discovered"] == len(EXPECTED_LEVELS) == 10
        assert s["added"] == 10 and s["updated"] == 0 and s["missing"] == 0
        assert s["chain_ok"] is True
        assert s["marker_formats"]["param_marks"] >= 1

    def test_added_entries_are_complete(self, tmp_path):
        rep = run_pipeline(tmp_path).report
        required = {"capability_id", "level", "outcome_state", "outcomes", "tests", "anchors",
                    "marker_formats", "sources", "declared_level", "confidence"}
        for item in rep["added"]:
            assert required <= set(item), f"{item['capability_id']} 字段不全"
            assert item["sources"] and all(s.endswith(".py") for s in item["sources"])

    def test_levels_match_hand_computed_expectation(self, tmp_path):
        result = run_pipeline(tmp_path)
        got = {cid: result.grades[cid].level for cid in result.grades}
        assert got == EXPECTED_LEVELS

    def test_markdown_report_renders(self, tmp_path):
        md = pipeline_mod.report_markdown(run_pipeline(tmp_path).report)
        assert "# 能力自动发现报告" in md
        assert "## 2. 差异摘要" in md and "## 5. 缺失" in md
        assert "哈希链 | OK" in md

    def test_diagnostics_surface_unsupported_marker(self, tmp_path):
        rep = run_pipeline(tmp_path).report
        assert rep["summary"]["diagnostics"] >= 1
        assert any("字面量" in d["message"] for d in rep["diagnostics"])

    def test_runtime_discovery_covers_non_literal_id(self, tmp_path):
        result = run_pipeline(tmp_path)
        assert "synth.dynamic.value" in result.grades
        feat = [f for f in result.features if f.capability_id == "synth.dynamic.value"][0]
        assert feat.marker_formats == ["runtime_marker"]
        assert result.report["summary"]["runtime_records_added"] == 1


class TestDiffSemantics:
    def test_second_run_is_unchanged(self, tmp_path):
        ledger_path = tmp_path / "ledger.json"
        first = run_pipeline(tmp_path, write=True)
        snapshot = ledger_path.read_text(encoding="utf-8")
        second = pipeline_mod.run(
            roots=[SUITE_REL],
            root=ROOT,
            outcomes=load_outcomes(OUTCOMES),
            outcome_items=load_outcome_records(OUTCOMES),
            ledger=Ledger.load(ledger_path),
            write_ledger=True,
            run_id="test-run-2",
        ).report
        assert second["summary"]["added"] == 0
        assert second["summary"]["updated"] == 0
        assert second["summary"]["unchanged"] == 10
        last = ledger_path.read_text(encoding="utf-8")
        assert last == snapshot, "重复运行必须字节不变（自动登记无噪声）"
        assert first.report["summary"]["added"] == 10

    def test_missing_reported_when_tests_disappear(self, tmp_path):
        run_pipeline(tmp_path, write=False)  # 先建全量台账
        run_pipeline(tmp_path, write=True)
        ledger = Ledger.load(tmp_path / "ledger.json")
        # 只用 family 文件再跑一次 → 其余能力变为「缺失」
        partial = pipeline_mod.run(roots=[f"{SUITE_REL}/test_synthetic_family.py"], root=ROOT,
                                   outcomes=load_outcomes(OUTCOMES), ledger=ledger,
                                   outcome_items=load_outcome_records(OUTCOMES)).report
        missing_ids = {m["capability_id"] for m in partial["missing"]}
        assert "synth.grid.trapezoid" in missing_ids
        assert "synth.algebra.vector_norm" not in missing_ids
        assert ledger.get("synth.grid.trapezoid") is not None


class TestZeroDomainSemantics:
    """C5：把能力词汇整体换掉，提取结构必须同构（扫描器不认识任何领域词）。"""

    RENAME = {
        "synth": "alpha",
        "grid": "widget",
        "trapezoid": "trim",
        "richardson": "relax",
        "algebra": "band",
        "vector_norm": "scale",
        "claims": "promises",
        "no_anchor": "unpinned",
        "contradiction": "clash",
        "skipped": "bailed",
        "family": "kin",
        "module_scope": "file_scope",
        "dynamic": "runtimey",
        "bare": "plain",
        "core": "kernel",
        "synthetic": "invented",
    }

    def _transplant(self, dst: Path) -> Path:
        dst.mkdir(parents=True, exist_ok=True)
        for src in sorted(SUITE.glob("*.py")):
            text = src.read_text(encoding="utf-8")
            for old, new in self.RENAME.items():
                text = text.replace(old, new)
            (dst / src.name).write_text(text, encoding="utf-8")
        return dst

    def test_structure_is_isomorphic_under_vocabulary_swap(self, tmp_path):
        alt_dir = self._transplant(tmp_path / "alt_suite")
        base = self._signature(MarkerScanner(ROOT).scan_paths([SUITE]))
        alt_scanner = MarkerScanner(tmp_path)
        alt = self._signature(alt_scanner.scan_paths([alt_dir]))
        base_ids = {r.capability_id for r in MarkerScanner(ROOT).scan_paths([SUITE])}
        alt_ids = {r.capability_id for r in MarkerScanner(tmp_path).scan_paths([alt_dir])}
        assert base_ids and alt_ids and base_ids.isdisjoint(alt_ids), "两套词汇的能力标识不应重合"
        assert len(base) == len(alt), "换词汇后能力数量必须不变"
        assert sorted(base.values()) == sorted(alt.values())

    def _signature(self, records):
        names = {r.capability_id for r in records}
        for old, new in self.RENAME.items():
            names = {n.replace(old, new) for n in names}
        from tools.capability_discovery.extract import extract_features

        feats = extract_features(records)
        sig = {}
        for f in feats:
            key = f.capability_id
            for old, new in self.RENAME.items():
                key = key.replace(new, old)
            anchors = f.anchors
            for old, new in self.RENAME.items():
                anchors = [a.replace(new, old) for a in anchors]
            sig[key] = (
                len(f.tests),
                tuple(sorted(f.marker_formats)),
                tuple(sorted(anchors)),
                json.dumps(f.params, sort_keys=True),
                len(f.sources),
                f.declared_level,
            )
        return sig


class TestUpgradePipelineLinkage:
    """C7：自动登记 → 升级管线（用对方代码校验）。"""

    def test_export_validates_with_upgrade_pipeline(self, tmp_path):
        result = run_pipeline(tmp_path)
        docs = export_mod.export(
            result.features, result.grades,
            registry_path=tmp_path / "registry.json",
            evidence_path=tmp_path / "evidence.json",
        )
        checked = export_mod.validate_with_upgrade_pipeline(docs["registry"])
        assert checked["ok"] and checked["slots_applied"] == 10
        assert checked["criteria_applied"] == 10
        assert set(checked["slot_ids"]) == set(result.grades)
        reg = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
        assert {c["initial_level"] for c in reg["capabilities"]} == {"REAL", "MOCK", "WITHHELD"}
        for crit in reg["criteria"]:
            assert crit["spec"]["require_source_sha256"] is True
            assert crit["spec"]["evidence_kinds"] == ["test_outcome"]

    def test_evidence_feed_carries_provenance(self, tmp_path):
        result = run_pipeline(tmp_path)
        docs = export_mod.export(result.features, result.grades)
        items = docs["evidence"]["items"]
        assert len(items) == 10
        for item in items:
            assert item["kind"] == "test_outcome"
            assert len(item["source"]["sha256"]) == 64
            assert item["source"]["path"].startswith(SUITE_REL)
            assert item["metrics"]["tests"] >= 1
            assert item["observed_level"] in {"REAL", "MOCK", "WITHHELD"}

    def test_upgrade_pipeline_ingests_exported_evidence(self, tmp_path):
        from tools.capability_upgrade import EvidencePipeline, Store, apply_registry, load_registry

        result = run_pipeline(tmp_path)
        docs = export_mod.export(result.features, result.grades, registry_path=tmp_path / "r.json")
        store = Store(":memory:")
        try:
            apply_registry(store, load_registry(tmp_path / "r.json"))
            pipe = EvidencePipeline(store)
            item = docs["evidence"]["items"][0]
            outcome = pipe.ingest(
                capability_id=item["capability_id"],
                kind=item["kind"],
                metrics=item["metrics"],
                observed_level=item["observed_level"],
                source=item["source"],
                payload=item["payload"],
            )
            assert outcome, "升级管线必须记录一次 ingest"
            chain = pipe.chain(item["capability_id"])
            assert chain["capability"]["capability_id"] == item["capability_id"]
            assert chain["evidence"], "证据必须落库"
            assert chain["traceability"]["evidence_with_source"] >= 1
        finally:
            store.close()

    def test_export_skips_capabilities_without_source_sha(self, tmp_path):
        result = run_pipeline(tmp_path)
        broken = [f for f in result.features if f.capability_id == "synth.grid.trapezoid"][0]
        broken.sources = [{"path": "x.py", "sha256": ""}]
        docs = export_mod.export([broken], {"synth.grid.trapezoid": result.grades["synth.grid.trapezoid"]})
        assert docs["evidence"]["items"] == []


class TestCli:
    def _run(self, *args, cwd=ROOT):
        env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/local/bin:/usr/bin:/bin"}
        return subprocess.run([sys.executable, "-m", "tools.capability_discovery.cli", *args],
                              cwd=str(cwd), env=env, capture_output=True, text=True)

    def test_scan_cli(self, tmp_path):
        out = tmp_path / "scan.json"
        proc = self._run("scan", "--roots", SUITE_REL, "--json", str(out))
        assert proc.returncode == 0, proc.stderr
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["count"] == 13  # 11 个能力共 13 条声明记录
        assert "param_marks" in doc["formats"]

    def test_register_write_verify_retract_cycle(self, tmp_path):
        ledger = tmp_path / "ledger.json"
        out_dir = tmp_path / "artifacts"
        proc = self._run("register", "--roots", SUITE_REL, "--outcomes", str(OUTCOMES),
                         "--ledger", str(ledger), "--out-dir", str(out_dir), "--write")
        assert proc.returncode == 0, proc.stderr
        assert (out_dir / "discovery_report.json").exists()
        assert (out_dir / "discovery_report.md").exists()

        proc = self._run("verify", "--ledger", str(ledger))
        assert proc.returncode == 0 and "OK" in proc.stdout

        proc = self._run("retract", "--ledger", str(ledger), "--capability", "synth.grid.trapezoid",
                         "--reason", "marker was a copy-paste artefact")
        assert proc.returncode == 0
        proc = self._run("register", "--roots", SUITE_REL, "--outcomes", str(OUTCOMES),
                         "--ledger", str(ledger), "--out-dir", str(out_dir))
        assert "held_retracted=1" in proc.stdout

        proc = self._run("restore", "--ledger", str(ledger), "--capability", "synth.grid.trapezoid",
                         "--reason", "reviewer confirmed the marker")
        assert proc.returncode == 0
        proc = self._run("verify", "--ledger", str(ledger))
        assert proc.returncode == 0

    def test_export_upgrade_cli(self, tmp_path):
        ledger = tmp_path / "ledger.json"
        self._run("register", "--roots", SUITE_REL, "--outcomes", str(OUTCOMES), "--ledger", str(ledger), "--write")
        proc = self._run("export-upgrade", "--ledger", str(ledger),
                         "--out-registry", str(tmp_path / "reg.json"), "--validate")
        assert proc.returncode == 0, proc.stderr
        assert "校验通过" in proc.stdout
        reg = json.loads((tmp_path / "reg.json").read_text(encoding="utf-8"))
        assert len(reg["capabilities"]) == 10