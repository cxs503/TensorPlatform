"""C6 — the downstream error-decision gate is fail-closed on a missing caliber."""
from __future__ import annotations

import json
import os

import pytest

from tools.caliber_reader.gate import (ACCEPT_FAIL, ACCEPT_PASS, AuditLog,
                                       REJECT_INVALID, REJECT_MISSING,
                                       REJECT_NOT_ALLOWED, REJECT_SCALE_MISMATCH,
                                       event_sha256, judge_error, rejection_rate)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH = os.path.join(ROOT, "benchmarks/verified/data_assimilation")
RESULTS = os.path.join(BENCH, "results.json")

GOOD = {"scale": "reduced", "source": "case_01_declared_consistent",
        "verdict_id": "v-001", "error_decision_allowed": True}


@pytest.fixture(scope="module")
def results():
    if not os.path.isfile(RESULTS):
        pytest.skip("run tools.data_assimilation.run_benchmark first")
    with open(RESULTS, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_missing_caliber_is_rejected_without_an_error_number():
    res = judge_error(1.10, 1.00, caliber=None, case_id="no-caliber")
    assert res["decision"] == REJECT_MISSING
    assert res["error_pct"] is None and res["within_tolerance"] is None
    assert res["audit_event"]["sha256"]


def test_partial_caliber_is_rejected():
    res = judge_error(1.10, 1.00, caliber={"scale": "reduced"})
    assert res["decision"] == REJECT_INVALID
    assert res["error_pct"] is None


def test_unknown_scale_is_rejected():
    bad = dict(GOOD, scale="banana")
    res = judge_error(1.10, 1.00, caliber=bad)
    assert res["decision"] == REJECT_INVALID


def test_scale_mismatch_is_rejected():
    full = dict(GOOD, scale="full")
    res = judge_error(1.10, 1.00, caliber=full, observed_scale="reduced")
    assert res["decision"] == REJECT_SCALE_MISMATCH
    assert res["error_pct"] is None


def test_verdict_that_forbids_a_judgement_is_rejected():
    forbidden = dict(GOOD, error_decision_allowed=False)
    res = judge_error(1.10, 1.00, caliber=forbidden)
    assert res["decision"] == REJECT_NOT_ALLOWED


def test_good_caliber_produces_a_judgement():
    ok = judge_error(1.05, 1.00, caliber=GOOD, tolerance_pct=15.0)
    assert ok["decision"] == ACCEPT_PASS and ok["error_pct"] == pytest.approx(5.0)
    bad = judge_error(1.40, 1.00, caliber=GOOD, tolerance_pct=15.0)
    assert bad["decision"] == ACCEPT_FAIL and bad["within_tolerance"] is False


def test_audit_chain_is_tamper_evident():
    audit = AuditLog()
    for i in range(4):
        judge_error(1.0, 1.0, caliber=GOOD if i % 2 else None, case_id=f"c{i}", audit=audit)
    prev = ""
    for ev in audit.events:
        body = {k: v for k, v in ev.items() if k not in ("sha256", "prev_sha256")}
        assert ev["sha256"] == event_sha256(body)
        assert ev["prev_sha256"] == prev
        prev = ev["sha256"]


def test_negative_control_rejection_rate_is_one():
    """C6 live check: 100 calibers dropped -> 100% rejection."""
    cases = [{"case_id": f"n{i}", "observed": 1.0 + 0.001 * i, "reference": 1.0,
              "caliber": None} for i in range(100)]
    out = rejection_rate(cases)
    assert out["rejected_missing_rate"] == 1.0
    assert out["decisions"] == {REJECT_MISSING: 100}
    assert out["accepted"] == 0


def test_positive_control_accepts_everything():
    cases = [{"case_id": f"p{i}", "observed": 1.0 + 0.001 * i, "reference": 1.0,
              "caliber": GOOD, "observed_scale": "reduced"} for i in range(50)]
    out = rejection_rate(cases)
    assert out["accept_rate"] == 1.0


def test_audit_log_is_written_to_disk(tmp_path):
    path = tmp_path / "audit.jsonl"
    audit = AuditLog(path=str(path))
    judge_error(1.0, 1.0, caliber=None, case_id="x", audit=audit)
    judge_error(1.0, 1.0, caliber=GOOD, case_id="y", audit=audit)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["prev_sha256"] == ""
    assert second["prev_sha256"] == first["sha256"]


def test_c6_recorded_numbers(results):
    c6 = results["criteria"]["C6"]
    assert c6["pass"]
    assert c6["missing_caliber_rejection_rate"] == 1.0
    assert c6["positive_control_accept_rate"] == 1.0
    assert c6["partial_caliber_decision"] != ACCEPT_PASS
    assert c6["audit_chain_ok"] is True