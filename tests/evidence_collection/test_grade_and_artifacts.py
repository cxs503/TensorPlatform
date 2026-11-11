"""定级器 / 验收工件 / 台账回写 的单元测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.evidence_collection import artifacts, grade, ledger

TH = {"real_tolerance_pct": 3.0, "accept_tolerance_pct": 15.0}


def _signals(**over):
    base = {"capability": "syn.alpha", "surface": {"declared": 1, "present": [{"pattern": "/a"}],
                                                  "absent": [], "status": "PASS"},
            "probes": [], "reference": None, "tests": None}
    base.update(over)
    return base


def _probe(criteria, executed=True):
    return {"id": "p", "method": "GET", "path": "/a", "executed": executed, "digest": "d" * 8,
            "samples": [], "criteria": criteria}


def _crit(cid, klass, verdict):
    return {"id": cid, "kind": "identity", "class": klass, "verdict": verdict,
            "measured": None, "threshold": None, "detail": "", "path": None}


def test_real_requires_external_reference():
    sig = _signals(probes=[_probe([_crit("c", "contract", "PASS"),
                                  _crit("i", "invariant", "PASS")])],
                   reference={"status": "PASS", "worst_pct": 0.4, "tolerance_pct": 3.0,
                              "sha256": "a" * 64, "n_values": 2})
    out = grade.grade(sig, TH)
    assert out["level"] == grade.REAL and "REFERENCE_VERIFIED" in out["reason_codes"]


def test_contract_only_is_withheld_not_real():
    sig = _signals(probes=[_probe([_crit("c", "contract", "PASS")])],
                   tests={"status": "PASSED", "totals": {"tests": 3, "passed": 3},
                          "modules_digest": "f" * 64})
    out = grade.grade(sig, TH)
    assert out["level"] == grade.WITHHELD
    assert any(r.startswith("CONTRACT_ONLY") for r in out["reason_codes"])


def test_invariant_failure_is_mock_even_with_good_reference():
    sig = _signals(probes=[_probe([_crit("i", "invariant", "FAIL")])],
                   reference={"status": "PASS", "worst_pct": 0.0, "tolerance_pct": 3.0,
                              "sha256": "b" * 64, "n_values": 1})
    out = grade.grade(sig, TH)
    assert out["level"] == grade.MOCK


def test_reference_over_accept_tolerance_is_mock():
    sig = _signals(probes=[_probe([_crit("c", "contract", "PASS")])],
                   reference={"status": "FAIL", "worst_pct": 68.9, "tolerance_pct": 3.0,
                              "sha256": "c" * 64, "n_values": 4})
    out = grade.grade(sig, TH)
    assert out["level"] == grade.MOCK and "REFERENCE_OVER_ACCEPT" in out["reason_codes"]


def test_reference_grey_zone_is_withheld():
    sig = _signals(probes=[_probe([_crit("c", "contract", "PASS")])],
                   reference={"status": "FAIL", "worst_pct": 8.0, "tolerance_pct": 3.0,
                              "sha256": "d" * 64, "n_values": 2})
    out = grade.grade(sig, TH)
    assert out["level"] == grade.WITHHELD and "BELOW_REAL_TOLERANCE" in out["reason_codes"]


def test_no_executed_evidence_is_withheld_with_explicit_code():
    sig = _signals(probes=[_probe([_crit("c", "contract", "PASS")], executed=False)],
                   surface=None)
    out = grade.grade(sig, TH)
    assert out["level"] == grade.WITHHELD and out["reason_codes"] == ["NO_EXECUTED_EVIDENCE"]


def test_surface_absent_is_withheld_not_not_supported():
    sig = _signals(surface={"declared": 1, "present": [], "absent": [{"pattern": "/a"}],
                            "status": "FAIL"})
    out = grade.grade(sig, TH)
    assert out["level"] == grade.WITHHELD and "SURFACE_ABSENT" in out["reason_codes"]


def test_failed_tests_block_real():
    sig = _signals(probes=[_probe([_crit("c", "contract", "PASS")])],
                   reference={"status": "PASS", "worst_pct": 0.2, "tolerance_pct": 3.0,
                              "sha256": "e" * 64, "n_values": 1},
                   tests={"status": "FAILED", "totals": {"tests": 4, "passed": 3, "failures": 1},
                          "modules_digest": "g" * 64})
    out = grade.grade(sig, TH)
    assert out["level"] == grade.WITHHELD and out["reason_codes"][0].startswith("EXEC_FAILED")


def test_artifacts_are_sealed_and_tamper_evident(tmp_path: Path):
    sig = _signals(probes=[_probe([_crit("c", "contract", "PASS")])])
    sig["verdict"] = grade.grade(sig, TH)
    written = artifacts.write_acceptance(tmp_path, "syn.alpha", sig,
                                         {"spec_sha256": "s", "ledger_sha256": None,
                                          "openapi_sha256": None})
    ap = Path(written["acceptance_path"])
    assert artifacts.verify_acceptance(ap)["sha256_match"] is True
    doc = json.loads(ap.read_text(encoding="utf-8"))
    doc["level"] = "REAL"
    ap.write_text(json.dumps(doc), encoding="utf-8")
    assert artifacts.verify_acceptance(ap)["sha256_match"] is False
    table = Path(written["criteria_table_path"]).read_text(encoding="utf-8")
    assert "判据表" in table and written["criteria_rows"] >= 1


def test_ledger_update_preserves_human_fields(tmp_path: Path):
    ledger_path = tmp_path / "ledger.json"
    original = {"schema_version": "2.0",
                "capabilities": [{"id": "syn.alpha", "claimed_level": "REAL",
                                  "evidence": [{"report_id": 1}]}]}
    ledger_path.write_text(json.dumps(original), encoding="utf-8")
    summary = {"ruleset": "ec-grade-v1", "spec_sha256": "s", "runtime": {"available": True},
               "coverage": {"covered": 1, "total": 1}, "level_distribution": {"REAL": 1}}
    records = {"syn.alpha": {"level": "REAL", "reason_codes": ["REFERENCE_VERIFIED"]}}
    block = ledger.build_block(summary, records, None)
    out = ledger.update_ledger(ledger_path, block)
    doc = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert doc["capabilities"] == original["capabilities"]      # 人审证据原样保留
    assert doc[ledger.BLOCK_KEY]["levels"] == {"syn.alpha": "REAL"}
    assert out["sha256_before"] != out["sha256_after"]


def test_ledger_block_chain_records_previous_digest(tmp_path: Path):
    summary = {"ruleset": "ec-grade-v1", "spec_sha256": "s", "runtime": {}, "coverage": {},
               "level_distribution": {}}
    first = ledger.build_block(summary, {"syn.alpha": {"level": "WITHHELD",
                                                       "reason_codes": []}}, None)
    second = ledger.build_block(summary, {"syn.alpha": {"level": "REAL",
                                                        "reason_codes": []}}, first)
    assert second["previous_block_sha256"] == first["block_sha256"]
    assert first["block_sha256"] != second["block_sha256"]


def test_ledger_view_written(tmp_path: Path):
    block = ledger.build_block({"ruleset": "r", "spec_sha256": "s", "runtime": {},
                                "coverage": {"covered": 1, "total": 1},
                                "level_distribution": {"REAL": 1}},
                               {"syn.alpha": {"level": "REAL", "reason_codes": ["x"],
                                              "acceptance_sha256": "a", "acceptance_path": "p",
                                              "criteria_table_path": "t", "criteria_rows": 2,
                                              "executed_evidence_items": 1}}, None)
    path = ledger.write_view(tmp_path, block)
    view = json.loads(path.read_text(encoding="utf-8"))
    assert view["levels"] == {"syn.alpha": "REAL"} and view["records"]["syn.alpha"]["criteria_rows"] == 2


def test_change_classification_reused_from_taxonomy():
    assert grade.classify_change("REAL", "MOCK") == "REFUTATION"
    assert grade.classify_change("WITHHELD", "REAL") == "UPGRADE"
    assert grade.is_downgrade("REAL", "WITHHELD") is True
    assert grade.tally(["REAL", "WITHHELD", "WITHHELD"])["WITHHELD"] == 2