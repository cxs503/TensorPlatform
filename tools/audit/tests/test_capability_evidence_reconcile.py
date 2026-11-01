"""Tests for tools/audit/capability_evidence_reconcile.py (no network, no live service)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "audit"))

import capability_evidence_reconcile as cer  # noqa: E402


NOW = 1_800_000_000.0


def _entry(**kw):
    base = {
        "id": "e1", "candidate_id": "cand", "candidate_version": "1.0.0",
        "mission_id": 1, "verifier_id": "tp-ver-x", "domain": "resistance",
        "verdict": "pass", "questions_passed": 10, "questions_failed": 0,
        "questions_total": 10, "pass_rate": 1.0,
        "evidence_hash": "a" * 64, "created_at": NOW - 3600,
    }
    base.update(kw)
    return base


TWO_CAP_REGISTRY = {
    "source": "test",
    "capabilities": [("calc.resistance", "REAL"), ("calc.stability", "REAL")],
}


def test_real_without_adjudication_is_p0_debt():
    res = cer.reconcile([], registry=TWO_CAP_REGISTRY, now=NOW)
    rules = {(d["rule"], d["capability"]) for d in res["debt"]}
    assert ("R1_UNSUBSTANTIATED_REAL", "calc.resistance") in rules
    assert ("R1_UNSUBSTANTIATED_REAL", "calc.stability") in rules
    assert res["summary"]["numeric_evidence_coverage"] == 0.0


def test_real_with_pass_adjudication_is_substantiated():
    res = cer.reconcile([_entry(domain="resistance")], registry=TWO_CAP_REGISTRY, now=NOW)
    row = {r["capability"]: r for r in res["rows"]}
    assert row["calc.resistance"]["status"] == "SUBSTANTIATED"
    assert row["calc.stability"]["status"] == "UNSUBSTANTIATED"
    assert res["summary"]["numeric_evidence_coverage"] == pytest.approx(0.5)


def test_fail_after_pass_contradicts_real_grade():
    ledger = [
        _entry(domain="stability", created_at=NOW - 10 * 86400, verdict="pass"),
        _entry(id="e2", domain="stability", created_at=NOW - 3600, verdict="fail",
               questions_passed=3, questions_failed=7, pass_rate=0.3),
    ]
    res = cer.reconcile(ledger, registry=TWO_CAP_REGISTRY, now=NOW)
    row = {r["capability"]: r for r in res["rows"]}["calc.stability"]
    assert row["status"] == "CONTRADICTED"
    assert any(d["rule"] == "R2_CONTRADICTED_BY_LEDGER" and d["priority"] == 0 for d in res["debt"])


def test_pass_after_fail_clears_contradiction():
    ledger = [
        _entry(domain="stability", created_at=NOW - 10 * 86400, verdict="fail",
               questions_passed=0, questions_failed=10, pass_rate=0.0),
        _entry(id="e2", domain="stability", created_at=NOW - 3600, verdict="pass"),
    ]
    res = cer.reconcile(ledger, registry=TWO_CAP_REGISTRY, now=NOW)
    row = {r["capability"]: r for r in res["rows"]}["calc.stability"]
    assert row["status"] == "SUBSTANTIATED"


def test_self_certification_is_rejected():
    bad = _entry(questions_total=0, questions_passed=0, pass_rate=0.0)
    res = cer.reconcile([bad], registry=TWO_CAP_REGISTRY, now=NOW)
    assert any(d["rule"] == "R5_INVALID_ADJUDICATION" for d in res["debt"])
    row = {r["capability"]: r for r in res["rows"]}["calc.resistance"]
    assert row["status"] == "UNSUBSTANTIATED"  # invalid entry cannot substantiate


def test_missing_evidence_hash_is_invalid():
    bad = _entry(evidence_hash="")
    res = cer.reconcile([bad], registry=TWO_CAP_REGISTRY, now=NOW)
    assert any(d["rule"] == "R5_INVALID_ADJUDICATION" for d in res["debt"])


def test_partial_pass_rate_flagged():
    ledger = [_entry(pass_rate=0.8, questions_passed=8, questions_failed=2)]
    res = cer.reconcile(ledger, registry=TWO_CAP_REGISTRY, now=NOW)
    row = {r["capability"]: r for r in res["rows"]}["calc.resistance"]
    assert row["status"] == "PARTIAL"
    assert any(d["rule"] == "R3_PARTIAL_ADJUDICATION" for d in res["debt"])


def test_stale_evidence_flagged():
    ledger = [_entry(created_at=NOW - 400 * 86400)]
    res = cer.reconcile(ledger, registry=TWO_CAP_REGISTRY, now=NOW, max_age_days=30)
    assert any(d["rule"] == "R7_STALE_EVIDENCE" for d in res["debt"])


def test_orphan_domain_flagged():
    res = cer.reconcile([_entry(domain="seakeeping")], registry=TWO_CAP_REGISTRY, now=NOW)
    assert any(d["rule"] == "R4_LEDGER_ORPHAN" for d in res["debt"])


def test_operational_capability_is_excluded_from_numeric_bar():
    reg = {"source": "t", "capabilities": [("dev.probe", "REAL")]}
    res = cer.reconcile([], registry=reg, now=NOW)
    assert res["rows"][0]["tier"] == "OPERATIONAL"
    assert res["summary"]["numeric_capabilities"] == 0
    assert not any(d["rule"] == "R1_UNSUBSTANTIATED_REAL" for d in res["debt"])


def test_sub_bar_grade_is_not_unsubstantiated():
    reg = {"source": "t", "capabilities": [("misc.stubs", "STUB")]}
    res = cer.reconcile([], registry=reg, now=NOW)
    assert res["rows"][0]["status"] == "SUB-BAR_GRADE"
    assert not any(d["rule"] == "R1_UNSUBSTANTIATED_REAL" for d in res["debt"])


def test_parse_error_line_does_not_crash(tmp_path):
    p = tmp_path / "ledger.jsonl"
    p.write_text('{"id": "ok", "domain": "resistance", "verifier_id": "v", "verdict": "pass",'
                 ' "questions_total": 3, "questions_passed": 3, "pass_rate": 1.0,'
                 ' "evidence_hash": "' + "b" * 64 + '", "created_at": 1.0}\nnot-json\n')
    ledger = cer.load_ledger(p)
    assert len(ledger) == 2
    res = cer.reconcile(ledger, registry=TWO_CAP_REGISTRY, now=NOW)
    assert any(d["rule"] == "R5_INVALID_ADJUDICATION" for d in res["debt"])


def test_markdown_and_cli_gate(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(_entry(domain="resistance")) + "\n")
    out = tmp_path / "out.json"
    md = tmp_path / "out.md"
    rc = cer.main(["--ledger", str(ledger), "--out", str(out), "--md", str(md),
                   "--fail-on-p0"])
    assert rc == 1  # calc.stability still unsubstantiated -> P0
    assert md.exists() and "数值证据覆盖率" in md.read_text(encoding="utf-8")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"]["numeric_substantiated"] == 1