"""CI 门测试：C1–C5 逐条判据在绿/红两侧都要有证据（门必须会红）。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools.mech_replay import gate, mechanisms, mutate, spec

ART = Path(__file__).resolve().parents[1] / "artifacts" / "mech_replay"


def test_gate_is_green_on_checked_in_artifacts():
    report = gate.run_gate(ART / "fixtures", ART)
    assert report["green"] is True
    for cid in ("C1", "C2", "C3", "C4", "C5"):
        assert report["criteria"][cid]["pass"] is True, (cid, report["criteria"][cid])


def test_gate_reports_c1_numbers():
    report = gate.run_gate(ART / "fixtures", ART, include_pytest=False)
    c1 = report["criteria"]["C1"]
    assert c1["fixtures_total"] >= gate.C1_MIN_FIXTURES
    assert c1["consistency"] == 1.0
    assert c1["idempotency_violations"] == []
    assert c1["reference_disagreements"] == []


def test_gate_reports_c2_numbers_and_channels():
    report = gate.run_gate(ART / "fixtures", ART, include_pytest=False)
    c2 = report["criteria"]["C2"]
    assert c2["mutation_samples"] >= gate.C2_MIN_SAMPLES * gate.C2_MIN_CLASSES
    assert c2["detection_rate"] == 1.0
    assert c2["min_samples_per_class"] >= gate.C2_MIN_SAMPLES
    assert c2["semantic_ok"] is True
    for klass, row in c2["classes"].items():
        assert row["required_channel"] == mutate.CHANNEL_REQUIREMENTS[klass]
        assert row["required_channel_ok"] is True


def test_gate_reports_c3_coverage():
    report = gate.run_gate(ART / "fixtures", ART, include_pytest=False)
    c3 = report["criteria"]["C3"]
    assert c3["mechanisms_covered"] >= gate.C3_MIN_MECHANISMS
    assert c3["min_per_mechanism"] >= gate.C3_MIN_FIXTURES_PER_MECHANISM


def test_gate_reports_c4_integrity():
    report = gate.run_gate(ART / "fixtures", ART, include_pytest=False)
    c4 = report["criteria"]["C4"]
    assert c4["all_ok"] is True
    assert c4["files_checked"] >= gate.C1_MIN_FIXTURES


def test_gate_counts_pytest_cases_for_c5():
    report = gate.run_gate(ART / "fixtures", ART, include_pytest=True)
    c5 = report["criteria"]["C5"]
    assert c5["test_count"] >= gate.C5_MIN_TESTS, c5


def test_gate_turns_red_on_tampered_fixture(tmp_path):
    work = tmp_path / "mech_replay"
    shutil.copytree(ART, work)
    target = work / "fixtures" / "merge_gate" / "probe-all-green.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["expected_verdict"]["decision"] = "fail"
    target.write_text(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    report = gate.run_gate(work / "fixtures", work, include_pytest=False)
    assert report["green"] is False
    assert report["criteria"]["C1"]["pass"] is False
    assert report["criteria"]["C4"]["pass"] is False  # 篡改同时破坏 sha256 完整性


def test_gate_turns_red_when_mutants_are_not_detected(tmp_path, monkeypatch):
    """门必须对「检出率为 0」的退化世界判红（否则门只是装饰）。"""
    fixtures = list((ART / "fixtures").rglob("*.json"))
    def _identity_judge(mechanism):
        return lambda record: mechanisms.judge(mechanism, record)

    identity = [mutate.Mutant(
        sample_id=f"M6_CONST_SHORTCUT:{f.parent.name}:identity",
        mechanism=f.parent.name, klass="M6_CONST_SHORTCUT", variant="identity",
        judge=_identity_judge(f.parent.name),
    ) for f in fixtures]
    monkeypatch.setattr(mutate, "build_mutants", lambda names=None: identity)
    work = tmp_path / "mech_replay"
    shutil.copytree(ART, work)
    report = gate.run_gate(work / "fixtures", work, include_pytest=False)
    assert report["criteria"]["C2"]["pass"] is False
    assert report["green"] is False


def test_gate_writes_stable_json_report(tmp_path):
    report = gate.run_gate(ART / "fixtures", ART, include_pytest=True)
    path = gate.write_report(report, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == "mech-replay-gate-v1"
    assert payload["rules_version"] == spec.RULES_VERSION
    assert payload["green"] is True
    assert payload["mutation_matrix"]


def test_gate_cli_exit_code(tmp_path):
    assert gate.main(["--dir", str(ART)]) == 0


def test_gate_rejects_fixtures_below_coverage(tmp_path):
    """覆盖不足（机制数不够）时必须红。"""
    work = tmp_path / "mech_replay"
    (work / "fixtures").mkdir(parents=True)
    src_fixtures = sorted((ART / "fixtures").rglob("*.json"))
    for fixture in src_fixtures[:6]:
        dest = work / "fixtures" / fixture.parent.name / fixture.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture, dest)
    shutil.copy(ART / "manifest.json", work / "manifest.json")
    shutil.copy(ART / "sources_snapshot.json", work / "sources_snapshot.json")
    report = gate.run_gate(work / "fixtures", work, include_pytest=False)
    assert report["green"] is False
    assert report["criteria"]["C3"]["pass"] is False

def test_report_doc_injects_numbers_from_gate_report(tmp_path):
    """文档数字必须来自工件（零手抄）：改动报告 → 文档文本随之变。"""
    from tools.mech_replay import report_doc

    report = gate.run_gate(ART / "fixtures", ART, include_pytest=False)
    manifest = json.loads((ART / "manifest.json").read_text(encoding="utf-8"))
    doc_a = report_doc.build_doc(report, manifest, ART / "fixtures")
    assert f"{report['criteria']['C1']['fixtures_total']}/{report['criteria']['C1']['fixtures_total']}" \
        in doc_a or f"{report['criteria']['C1']['fixtures_total']}" in doc_a
    assert report["criteria"]["C2"]["classes"]["M4_IDEMPOTENCY_BREAK"]["required_channel"] in doc_a
    tampered = json.loads(json.dumps(report))
    tampered["criteria"]["C2"]["mutation_samples"] = 999
    tampered["criteria"]["C2"]["mutation_detected"] = 999
    doc_b = report_doc.build_doc(tampered, manifest, ART / "fixtures")
    assert doc_a != doc_b


def test_report_doc_is_stable_for_same_inputs():
    from tools.mech_replay import report_doc

    report = gate.run_gate(ART / "fixtures", ART, include_pytest=False)
    manifest = json.loads((ART / "manifest.json").read_text(encoding="utf-8"))
    first = report_doc.build_doc(report, manifest, ART / "fixtures")
    second = report_doc.build_doc(report, manifest, ART / "fixtures")
    assert first == second
    for section in ("## 1 案例背景与对象", "## 6 误差判定", "## 8 审计链"):
        assert section in first
