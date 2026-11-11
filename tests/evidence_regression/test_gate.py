"""Gate semantics: silent degradation, unbacked downgrade, unsupported upgrade, acks, latency."""
from __future__ import annotations

import time

from tools.evidence_regression import gate, mutation
from tools.evidence_regression.targets import DegradedTarget


def _install_degradation(monkeypatch, registry, capability_id: str, family: str) -> None:
    """Route one capability's probes through a degraded observable (MOCK injection)."""
    entry = registry.entries[capability_id]
    degraded = DegradedTarget(registry.target_for(capability_id), family,
                              expectations=mutation.expectations_of(entry))
    original = registry.run

    def run(capability_id_: str, target=None):  # noqa: ANN001
        if capability_id_ == capability_id and target is None:
            return original(capability_id_, target=degraded)
        return original(capability_id_, target=target)

    monkeypatch.setattr(registry, "run", run)


def test_audit_claims_ok_on_healthy_registry(registry, claims):
    result = gate.audit_claims(registry, claims)
    assert result.verdict == gate.OK
    assert {f.code for f in result.findings} == {"CLAIM_SUPPORTED"}
    assert result.red_findings == []


def test_silent_degradation_is_refuted(registry, monkeypatch):
    """Implementation rotted to MOCK while the ledger still claims REAL -> RED."""
    cid = "cap.alpha"
    _install_degradation(monkeypatch, registry, cid, "constant_freeze")
    result = gate.audit_claims(registry, {cid: "REAL"}, capabilities=[cid])
    assert result.verdict == gate.RED
    finding = result.red_findings[0]
    assert finding.code == "REFUTED_CLAIM"
    assert finding.probe_digest
    assert finding.failed_kinds


def test_claim_audit_ignores_non_real_claims(registry):
    result = gate.audit_claims(registry, {cid: "MOCK" for cid in registry.registered_capabilities()})
    assert result.verdict == gate.OK
    assert result.findings == []


def test_unsubstantiated_downgrade_is_blocked(registry):
    result = gate.evaluate_changes(registry, {"cap.beta": "REAL"}, {"cap.beta": "MOCK"})
    assert result.verdict == gate.RED
    assert result.red_findings[0].code == "UNSUBSTANTIATED_DOWNGRADE"
    assert result.red_findings[0].probe_passed is True


def test_fresh_ack_accepts_downgrade(registry):
    probe = gate.evaluate_changes(registry, {"cap.beta": "REAL"}, {"cap.beta": "MOCK"})
    digest = probe.probe_reports[0].digest()
    result = gate.evaluate_changes(registry, {"cap.beta": "REAL"}, {"cap.beta": "MOCK"},
                                   ack={"capability_id": "cap.beta", "probe_digest": digest,
                                        "reason": "evidence superseded upstream", "by": "tester"})
    assert result.verdict == gate.ACK
    assert result.red_findings == []
    assert result.findings[0].code == "ACKED_DOWNGRADE"


def test_stale_ack_is_rejected(registry):
    result = gate.evaluate_changes(registry, {"cap.beta": "REAL"}, {"cap.beta": "MOCK"},
                                   ack={"capability_id": "cap.beta", "probe_digest": "0" * 64,
                                        "reason": "old evidence"})
    assert result.verdict == gate.RED


def test_unsupported_upgrade_is_blocked(registry, monkeypatch):
    """MOCK -> REAL must be reproducible by probes, otherwise fail-closed."""
    cid = "cap.delta"
    _install_degradation(monkeypatch, registry, cid, "zero_fill")
    result = gate.evaluate_changes(registry, {cid: "MOCK"}, {cid: "REAL"})
    assert result.verdict == gate.RED
    assert result.red_findings[0].code == "UNSUPPORTED_UPGRADE"


def test_verified_upgrade_is_accepted(registry):
    result = gate.evaluate_changes(registry, {"cap.delta": "MOCK"}, {"cap.delta": "REAL"})
    assert result.verdict == gate.OK
    assert result.findings[0].code == "UPGRADE_VERIFIED"


def test_unregistered_capability_is_flagged_not_blocked(registry):
    result = gate.evaluate_changes(registry, {"cap.unknown": "REAL"}, {"cap.unknown": "MOCK"})
    assert result.verdict == gate.ACK
    assert result.findings[0].code == "UNCOVERED_CAPABILITY"
    assert result.uncovered == ["cap.unknown"]


def test_unchanged_levels_are_silent(registry):
    result = gate.evaluate_changes(registry, {"cap.alpha": "REAL"}, {"cap.alpha": "REAL"})
    assert result.verdict == gate.OK
    assert result.findings == []


def test_gate_latency_is_within_budget(registry):
    started = time.perf_counter()
    gate.evaluate_changes(registry, {"cap.alpha": "REAL"}, {"cap.alpha": "MOCK"})
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert elapsed_ms < 2000.0


def test_probe_coverage_reports_gaps(registry):
    coverage = gate.probe_coverage(registry, {"cap.alpha": "REAL", "cap.not_registered": "REAL"})
    assert coverage["registered_count"] == 5
    assert coverage["uncovered_real_claims"] == ["cap.not_registered"]