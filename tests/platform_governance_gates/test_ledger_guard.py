"""Ledger guard: thresholds, fail-closed demotions, audit chain, idempotence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.capability_auto_audit.append_only_log import AppendOnlyAuditLog
from tools.platform_governance_gates.evidence import (
    CapabilityEvidence,
    Contradiction,
    EvidenceItem,
    evaluate,
)
from tools.platform_governance_gates.ledger_guard import (
    DOWNGRADE,
    HOLD,
    UPGRADE,
    LedgerGuard,
    Thresholds,
    decide,
)


def item(kind: str, ref: str, **kwargs) -> EvidenceItem:
    sha = hashlib.sha256(ref.encode("utf-8")).hexdigest()
    return EvidenceItem(kind=kind, ref=ref, sha256=sha, **kwargs)


def complete(cap: str, level: str) -> CapabilityEvidence:
    return CapabilityEvidence(cap, level, [
        item("verification", f"{cap}/v", independent=True),
        item("cross_check", f"{cap}/x"),
        item("benchmark", f"{cap}/b"),
    ])


def minimal(cap: str, level: str) -> CapabilityEvidence:
    return CapabilityEvidence(cap, level, [item("verification", f"{cap}/v")])


def middle(cap: str, level: str) -> CapabilityEvidence:
    return CapabilityEvidence(cap, level, [item("cross_check", f"{cap}/x")])


def test_promote_from_each_below_real_level():
    for level in ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD"):
        decision = decide(evaluate(complete("demo.p", level)), level)
        assert decision.action == UPGRADE
        assert decision.to_level == "REAL"
        assert decision.reason_code == "COMPLETENESS_PROMOTE"


def test_real_with_complete_chain_holds():
    decision = decide(evaluate(complete("demo.p", "REAL")), "REAL")
    assert decision.action == HOLD
    assert decision.to_level == "REAL"


def test_low_completeness_demotes_to_withheld():
    decision = decide(evaluate(minimal("demo.d", "MOCK")), "MOCK")
    assert decision.action == DOWNGRADE
    assert decision.to_level == "WITHHELD"
    assert decision.reason_code == "INSUFFICIENT_COMPLETENESS"


def test_contradiction_demotes_to_mock():
    bundle = complete("demo.c", "REAL")
    bundle.contradictions.append(Contradiction(kind="replication_failed",
                                               ref="reports/v", reason="demo"))
    decision = decide(evaluate(bundle), "REAL")
    assert decision.action == DOWNGRADE
    assert decision.to_level == "MOCK"
    assert decision.reason_code == "CONTRADICTING_EVIDENCE"


def test_intermediate_holds_below_real():
    decision = decide(evaluate(middle("demo.m", "WITHHELD")), "WITHHELD")
    assert decision.action == HOLD
    assert decision.to_level == "WITHHELD"
    assert decision.reason_code == "INTERMEDIATE_COMPLETENESS_HOLD"


def test_intermediate_demotes_an_eroded_real():
    decision = decide(evaluate(middle("demo.m", "REAL")), "REAL")
    assert decision.action == DOWNGRADE
    assert decision.to_level == "WITHHELD"


def test_invalid_report_never_promotes():
    bad = CapabilityEvidence("", "MOCK", complete("demo.x", "MOCK").items)
    decision = decide(evaluate(bad), "MOCK")
    assert decision.action == DOWNGRADE
    assert decision.to_level == "WITHHELD"
    assert decision.reason_code == "INVALID_EVIDENCE"


def test_thresholds_reject_inverted_band():
    import pytest
    with pytest.raises(ValueError):
        Thresholds(promote=0.4, demote=0.8)


def test_audit_log_records_time_evidence_and_delta(tmp_path: Path):
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    guard = LedgerGuard(thresholds=Thresholds(), state_path=state, audit_path=audit)
    result = guard.run([complete("demo.a", "MOCK"), minimal("demo.b", "REAL")])
    assert result.promoted and result.demoted
    log = AppendOnlyAuditLog(audit)
    assert log.verify()["ok"] is True
    assert len(log.entries) == 2
    for entry in log.entries:
        assert entry.ts
        assert len(entry.evidence_digest) == 64
        for key in ("completeness", "completeness_before", "completeness_delta"):
            assert key in entry.payload
    assert log.entries[0].from_level == "MOCK"
    assert log.entries[0].to_level == "REAL"
    assert log.entries[1].to_level == "WITHHELD"


def test_second_run_is_idempotent_and_state_is_stable(tmp_path: Path):
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    guard = LedgerGuard(state_path=state, audit_path=audit)
    first = guard.run([complete("demo.a", "MOCK")])
    second = guard.run([complete("demo.a", "MOCK")])
    assert first.state_digest == second.state_digest
    assert second.decisions[0].action == HOLD
    assert json.loads(state.read_text())["levels"]["demo.a"] == "REAL"
    assert len(AppendOnlyAuditLog(audit).entries) == 2


def test_corrupt_state_file_starts_from_empty_ledger(tmp_path: Path):
    state = tmp_path / "state.json"
    state.write_text("{not json", encoding="utf-8")
    guard = LedgerGuard(state_path=state)
    assert guard.state["levels"] == {}
    decision = guard.run([complete("demo.a", "WITHHELD")]).decisions[0]
    assert decision.from_level == "WITHHELD"
    assert decision.to_level == "REAL"

def test_high_score_cannot_paper_over_missing_independent_verification():
    bundle = CapabilityEvidence("demo.crit", "WITHHELD", [
        item("verification", "demo.crit/v", independent=False),
        item("cross_check", "demo.crit/x"),
        item("benchmark", "demo.crit/b"),
    ])
    report = evaluate(bundle)
    assert report.score >= 0.8
    decision = decide(report, "WITHHELD")
    assert decision.action == HOLD
    assert decision.to_level == "WITHHELD"
    assert decision.reason_code == "CRITICAL_EVIDENCE_MISSING"


def test_high_score_with_missing_critical_demotes_an_existing_real():
    bundle = CapabilityEvidence("demo.crit", "REAL", [
        item("verification", "demo.crit/v", independent=False),
        item("cross_check", "demo.crit/x"),
        item("benchmark", "demo.crit/b"),
    ])
    decision = decide(evaluate(bundle), "REAL")
    assert decision.action == DOWNGRADE
    assert decision.to_level == "WITHHELD"
    assert decision.reason_code == "CRITICAL_EVIDENCE_MISSING"
