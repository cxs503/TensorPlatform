"""Auto ledger upgrade: REAL only on a valid passing chain; failures stay WITHHELD."""
from __future__ import annotations

import json

import pytest

from services.capability_governance.grading import (build_chain, digest_of, grade_probe,
                                                   sha256_file, validate_chain)
from services.capability_governance.ledger import CapabilityLedger
from services.capability_governance.probes import run_endpoint


class _Env:
    """Small helper: grade one frozen spec and seal the chain."""

    def __init__(self, test_report, tmp_path):
        self.test_report = test_report
        self.tmp_path = tmp_path
        self.routine_sha = sha256_file(
            __import__("pathlib").Path(__file__).resolve().parents[2]
            / "backend/services/capability_governance/probes.py")

    def chain(self, spec):
        verdict = grade_probe(run_endpoint(spec).to_dict(), spec, self.routine_sha,
                              digest_of(dict(spec)))
        return build_chain(verdict, test_report_path=self.test_report, mission_id=9811,
                           routine_source_sha256=self.routine_sha,
                           spec_digest=digest_of(dict(spec))), verdict

    def ledger(self):
        return CapabilityLedger(self.tmp_path / "ledger.json", self.tmp_path / "events.jsonl")


def _positive_spec():
    return {"endpoint_id": "ep-good", "routine": "quadrature", "grid": [16, 64, 256],
            "defect": "ok", "expected_level": "REAL", "tol_pct": 0.01,
            "min_order": 1.5, "min_spread": 1e-6}


def _negative_spec():
    return {"endpoint_id": "ep-bad", "routine": "quadrature", "grid": [16, 64, 256],
            "defect": "constant", "expected_level": "MOCK", "tol_pct": 0.01,
            "min_order": 1.5, "min_spread": 1e-6}


def test_passing_chain_creates_real_entry(test_report, tmp_path):
    env = _Env(test_report, tmp_path)
    chain, verdict = env.chain(_positive_spec())
    assert verdict["decided_level"] == "REAL"
    ledger = env.ledger()
    decision = ledger.apply(chain, 9811)
    assert decision["action"] == "created"
    assert decision["level"] == "REAL"
    entry = ledger.entry("ep-good")
    assert entry["level"] == "REAL"
    assert entry["evidence"][0]["test_report_sha256"] == chain["test_report"]["sha256"]
    assert entry["evidence"][0]["mission_id"] == 9811


def test_failing_chain_never_becomes_real(test_report, tmp_path):
    env = _Env(test_report, tmp_path)
    chain, verdict = env.chain(_negative_spec())
    assert verdict["decided_level"] == "WITHHELD"
    ledger = env.ledger()
    decision = ledger.apply(chain, 9811)
    assert decision["level"] == "WITHHELD"
    assert ledger.entry("ep-bad")["observed_level"] == "MOCK"
    assert ledger.summary()["by_level"].get("REAL", 0) == 0


def test_tampered_chain_is_rejected(test_report, tmp_path):
    env = _Env(test_report, tmp_path)
    chain, _ = env.chain(_positive_spec())
    tampered = json.loads(json.dumps(chain))
    tampered["measured_values"]["measured"][-1] *= 1.0001
    check = validate_chain(tampered)
    assert check["valid"] is False
    assert any("chain_digest" in p for p in check["problems"])
    ledger = env.ledger()
    decision = ledger.apply(tampered, 9811)
    assert decision["action"] == "rejected"
    assert ledger.entry("ep-good") is None


def test_forged_pass_flag_is_rejected(test_report, tmp_path):
    env = _Env(test_report, tmp_path)
    chain, _ = env.chain(_negative_spec())
    forged = json.loads(json.dumps(chain))
    forged["verdict"] = "PASS"
    forged["decided_level"] = "REAL"
    forged["chain_digest"] = digest_of({k: v for k, v in forged.items() if k != "chain_digest"})
    decision = env.ledger().apply(forged, 9811)
    assert decision["action"] == "rejected"
    assert any("criterion failed" in p for p in decision["problems"])


def test_report_file_mismatch_is_rejected(test_report, tmp_path):
    env = _Env(test_report, tmp_path)
    chain, _ = env.chain(_positive_spec())
    forged = json.loads(json.dumps(chain))
    forged["test_report"]["sha256"] = "0" * 64
    forged["chain_digest"] = digest_of({k: v for k, v in forged.items() if k != "chain_digest"})
    decision = env.ledger().apply(forged, 9811)
    assert decision["action"] == "rejected"
    assert any("does not match the file on disk" in p for p in decision["problems"])


def test_missing_report_file_is_rejected(test_report, tmp_path):
    env = _Env(test_report, tmp_path)
    chain, _ = env.chain(_positive_spec())
    vanished = json.loads(json.dumps(chain))
    vanished["test_report"]["path"] = str(tmp_path / "nope.json")
    vanished["chain_digest"] = digest_of({k: v for k, v in vanished.items() if k != "chain_digest"})
    decision = env.ledger().apply(vanished, 9811)
    assert decision["action"] == "rejected"
    assert any("test report not found" in p for p in decision["problems"])


def test_missing_test_report_digest_is_rejected(test_report, tmp_path):
    env = _Env(test_report, tmp_path)
    chain, _ = env.chain(_positive_spec())
    broken = json.loads(json.dumps(chain))
    broken["test_report"]["sha256"] = "not-a-digest"
    broken["chain_digest"] = digest_of({k: v for k, v in broken.items() if k != "chain_digest"})
    decision = env.ledger().apply(broken, 9811)
    assert decision["action"] == "rejected"


def test_reapply_is_idempotent(test_report, tmp_path):
    env = _Env(test_report, tmp_path)
    chain, _ = env.chain(_positive_spec())
    ledger = env.ledger()
    ledger.apply(chain, 9811)
    again = ledger.apply(chain, 9811)
    assert again["action"] == "noop"
    assert ledger.summary()["counters"]["created"] == 1
    assert ledger.summary()["counters"]["updated"] == 0
    assert ledger.summary()["applied_chains"] == 1
    events = [json.loads(l) for l in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert len(events) == 2  # one create + one noop, both auditable


def test_regression_is_recorded_not_swallowed(test_report, tmp_path):
    env = _Env(test_report, tmp_path)
    good, _ = env.chain(_positive_spec())
    ledger = env.ledger()
    ledger.apply(good, 9811)
    assert ledger.level_of("ep-good") == "REAL"

    failing = dict(good)
    failing["subject_id"] = "ep-good"
    failing["decided_level"] = "WITHHELD"
    failing["verdict"] = "FAIL"
    failing["acceptance_criteria"] = [dict(c, passed=False) for c in good["acceptance_criteria"]]
    failing["chain_digest"] = digest_of({k: v for k, v in failing.items() if k != "chain_digest"})
    decision = ledger.apply(failing, 9811)
    assert decision["action"] == "regressed"
    assert decision["level"] == "WITHHELD"
    entry = ledger.entry("ep-good")
    assert entry["regressions"] and entry["regressions"][0]["level_before"] == "REAL"
    assert ledger.summary()["counters"]["regression"] == 1