"""Criterion #3: the closure-rate KPI is real, invariant-checked, and unfakeable.

The upgrade half must not be a status field. These tests inject deliberately bad
"pass" claims and assert the KPI refuses to move:

* one independent source (copy trap)          -> rejected
* failing acceptance check                    -> rejected
* evidence without a content hash             -> rejected
* fabricated pass with empty checks           -> rejected
and a genuinely good claim does move it.
"""
from __future__ import annotations

import copy

from tools.evidence_governance.model import GovernanceLedger
from tools.evidence_governance.runner import build_pipeline
from tools.evidence_governance.upgrade import UpgradeEngine

from .conftest import PKG, ROOT, load


def _engine(config, root=ROOT):
    return UpgradeEngine(config, root=root)


def test_loopback_closure_rate_and_invariant(demo_config):
    scanner, _c, _p, engine = build_pipeline(demo_config, ROOT)
    ledger = engine.run(scanner.scan().items)
    ledger.validate()
    assert ledger.n_withheld_total == 8
    assert ledger.n_upgraded == 3
    assert ledger.n_failed == 1
    assert ledger.n_remaining == 4
    assert abs(ledger.closure_rate - 3 / 8) < 1e-9


def test_each_rejection_reason_is_explicit(demo_config):
    from tools.evidence_governance.runner import build_pipeline

    scanner, _c, _p, engine = build_pipeline(demo_config, ROOT)
    ledger = engine.run(scanner.scan().items)
    outcomes = {u.capability_id: u for u in ledger.updates}
    assert outcomes["synth.delta"].outcome == "claim_rejected"
    assert "independent evidence source" in outcomes["synth.delta"].reason
    assert outcomes["synth.epsilon"].outcome == "claim_rejected"
    assert "acceptance checks failed" in outcomes["synth.epsilon"].reason
    assert outcomes["synth.zeta"].outcome == "claim_rejected"
    assert "content hash" in outcomes["synth.zeta"].reason
    assert outcomes["synth.gamma"].outcome == "fail"
    assert outcomes["synth.theta"].outcome == "no_outcome"
    for cid in ("synth.alpha", "synth.beta", "synth.eta"):
        assert outcomes[cid].outcome == "pass"
        assert outcomes[cid].after_level == "REAL"


def test_fabricated_pass_cannot_move_the_kpi(tmp_path):
    """An empty pass with no checks and no evidence must not upgrade anything."""
    manifest = {"outcomes": [{
        "capability_id": "synth.alpha",
        "status": "pass",
        "reason": "trust me",
        "evidence_refs": [
            {"source_id": "f-1", "sha256": "a" * 64},
            {"source_id": "f-2", "sha256": "b" * 64},
        ],
        "checks": [],
    }]}
    path = tmp_path / "manifest.json"
    path.write_text(__import__("json").dumps(manifest), encoding="utf-8")

    cfg = copy.deepcopy(load(PKG / "fixtures" / "governance.demo.v1.json"))
    cfg["verification"]["manifest_path"] = str(path)
    scanner, _c, _p, engine = build_pipeline(cfg, ROOT)
    ledger = engine.run(scanner.scan().items)
    assert ledger.n_upgraded == 0
    assert ledger.closure_rate == 0.0
    rejected = [u for u in ledger.updates if u.outcome == "claim_rejected"]
    assert len(rejected) == 1 and "no acceptance checks" in rejected[0].reason


def test_outcomes_for_unscanned_capabilities_are_flagged(demo_config, tmp_path):
    manifest = {"outcomes": [{
        "capability_id": "synth.not_in_library",
        "status": "pass",
        "evidence_refs": [{"source_id": "x", "sha256": "0" * 64},
                          {"source_id": "y", "sha256": "1" * 64}],
        "checks": [{"name": "c", "passed": True}],
    }]}
    path = tmp_path / "manifest.json"
    path.write_text(__import__("json").dumps(manifest), encoding="utf-8")
    cfg = copy.deepcopy(demo_config)
    cfg["verification"]["manifest_path"] = str(path)
    scanner, _c, _p, engine = build_pipeline(cfg, ROOT)
    ledger = engine.run(scanner.scan().items)
    assert ledger.n_upgraded == 0
    assert ledger.orphan_outcomes == ["synth.not_in_library"]


def test_empty_manifest_keeps_the_real_library_honest(real_config):
    scanner, _c, _p, engine = build_pipeline(real_config, ROOT)
    ledger = engine.run(scanner.scan().items)
    assert ledger.n_withheld_total == 19
    assert ledger.n_upgraded == 0
    assert ledger.closure_rate == 0.0
    assert ledger.n_remaining == 19
    assert engine.load_outcomes()[1]["n_outcomes"] == 0


def test_ledger_refuses_a_broken_invariant():
    try:
        GovernanceLedger(
            n_withheld_total=5, n_upgraded=1, n_failed=1, n_remaining=1,
            closure_rate=0.2, run_digest="x",
        ).validate()
    except ValueError as exc:
        assert "invariant" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invariant violation must raise")


def test_ledger_refuses_a_wrong_closure_rate():
    try:
        GovernanceLedger(
            n_withheld_total=4, n_upgraded=1, n_failed=0, n_remaining=3,
            closure_rate=0.5, run_digest="x",
        ).validate()
    except ValueError as exc:
        assert "closure_rate" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("closure-rate mismatch must raise")