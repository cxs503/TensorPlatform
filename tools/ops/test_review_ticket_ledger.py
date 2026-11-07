#!/usr/bin/env python3
"""review_ticket_ledger 的测试面：四类工单可开可结 + 拒收门可失败。

覆盖本单新增的两类接入：outcome_assessment / innovation_review（R3 验证专员
线），以及 Innovation Review 的新颖性检索范围必填字段。夹具域名合成。

跑法：python -m pytest tools/ops/test_review_ticket_ledger.py -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from review_ticket_ledger import (  # noqa: E402
    CLOSED, IN_REVIEW, NOVELTY_FIELD, OPEN, REQUIRED_REVIEWER_ROLE,
    REVIEW_TYPES, ReviewTicketLedger, TicketRejected, TicketStateError,
    review_types_doc, validate_novelty_scope,
)

RATIONALE = "按判据逐条复核，证据链完整，未发现未披露局限"
PAYLOADS = {
    "execution_audit": {"evidence_refs": ["artifacts/run/log.txt"]},
    "technical_review": {"diff_ref": "branch/agent-x", "test_evidence": "pytest -q 12 passed"},
    "outcome_assessment": {"claimed_grade": "S3", "evidence_level": "E2",
                           "acceptance_refs": ["artifacts/run/acceptance.json"]},
    "innovation_review": {"novelty_search_scope": {"sources": ["SYN-INDEX-1"],
                                                  "period": "2020-2026"},
                          "hypothesis": "合成假设：门拒收率随证据等级单调下降"},
}


def _ledger() -> ReviewTicketLedger:
    return ReviewTicketLedger(clock=lambda: 1_700_000_000.0)


def _open(led: ReviewTicketLedger, rt: str = "outcome_assessment") -> str:
    t = led.open(rt, subject_ref="subject/synthetic-alpha/out-1",
                 producer_id="agent-producer", payload=PAYLOADS[rt])
    return t.ticket_id


def _open_close(led: ReviewTicketLedger, rt: str) -> str:
    tid = _open(led, rt)
    led.assign(tid, "agent-verifier", REQUIRED_REVIEWER_ROLE[rt])
    led.close(tid, "approve", RATIONALE, ["artifacts/run/verdict.json"])
    return tid


# --- 四类工单可开可结 ------------------------------------------------------
@pytest.mark.parametrize("review_type", list(REVIEW_TYPES))
def test_each_review_type_can_open_and_close(review_type):
    led = _ledger()
    tid = _open_close(led, review_type)
    ticket = led.get(tid)
    assert ticket.review_type == review_type
    assert ticket.status == CLOSED and ticket.verdict == "approve"
    assert [e["event"] for e in ticket.events] == ["opened", "assigned", "closed"]
    stats = led.stats()
    assert stats["by_type"][review_type]["closed"] == 1


def test_four_types_all_closed_in_one_ledger():
    led = _ledger()
    for rt in REVIEW_TYPES:
        _open_close(led, rt)
    stats = led.stats()
    assert stats["total"] == 4
    assert stats["all_types_openable_and_closable"] is True
    assert stats["closed_by_verdict"] == {"approve": 4}


def test_new_types_use_verifier_role_r3_line():
    """Outcome/Innovation 两类接 R3 验证专员线。"""
    assert REQUIRED_REVIEWER_ROLE["outcome_assessment"] == "verifier"
    assert REQUIRED_REVIEWER_ROLE["innovation_review"] == "verifier"
    doc = {d["review_type"]: d for d in review_types_doc()}
    assert doc["innovation_review"]["required_reviewer_role"] == "verifier"
    assert NOVELTY_FIELD in doc["innovation_review"]["required_payload_fields"]


# --- 拒收门必须能失败 ------------------------------------------------------
@pytest.mark.mutation
def test_mutation_innovation_without_novelty_scope_is_rejected():
    led = _ledger()
    payload = dict(PAYLOADS["innovation_review"])
    payload.pop(NOVELTY_FIELD)
    with pytest.raises(TicketRejected) as exc:
        led.open("innovation_review", "subject/x", "agent-producer", payload)
    assert any(NOVELTY_FIELD in r for r in exc.value.reasons)


@pytest.mark.mutation
@pytest.mark.parametrize("scope", ["", "  ", {"sources": [], "period": "2020-2026"},
                                   {"sources": ["SYN-1"]}, 123])
def test_mutation_innovation_scope_must_be_substantive(scope):
    led = _ledger()
    payload = dict(PAYLOADS["innovation_review"])
    payload[NOVELTY_FIELD] = scope
    with pytest.raises(TicketRejected):
        led.open("innovation_review", "subject/x", "agent-producer", payload)
    assert validate_novelty_scope(scope) != []


@pytest.mark.mutation
def test_mutation_unknown_review_type_rejected():
    with pytest.raises(TicketRejected):
        _ledger().open("vibe_review", "subject/x", "agent-producer", {})


@pytest.mark.mutation
def test_mutation_missing_required_payload_field_rejected():
    led = _ledger()
    with pytest.raises(TicketRejected) as exc:
        led.open("outcome_assessment", "subject/x", "agent-producer",
                 {"claimed_grade": "S3"})   # 缺 evidence_level/acceptance_refs
    assert len(exc.value.reasons) >= 2


@pytest.mark.mutation
def test_mutation_wrong_role_cannot_take_outcome_assessment():
    led = _ledger()
    tid = _open(led, "outcome_assessment")
    with pytest.raises(TicketRejected):
        led.assign(tid, "agent-operator", "operator")   # 非 verifier 线


@pytest.mark.mutation
def test_mutation_self_review_is_rejected():
    led = _ledger()
    tid = _open(led, "innovation_review")
    with pytest.raises(TicketRejected) as exc:
        led.assign(tid, "agent-producer", "verifier")
    assert any("独立性" in r for r in exc.value.reasons)


@pytest.mark.mutation
def test_mutation_close_without_assignment_is_rejected():
    led = _ledger()
    tid = _open(led, "technical_review")
    with pytest.raises(TicketStateError):
        led.close(tid, "approve", RATIONALE, ["artifacts/run/v.json"])


@pytest.mark.mutation
@pytest.mark.parametrize("rationale,refs,verdict", [
    ("", ["artifacts/run/v.json"], "approve"),
    ("太短", ["artifacts/run/v.json"], "approve"),
    (RATIONALE, [], "approve"),
    (RATIONALE, ["artifacts/run/v.json"], "lgtm"),
])
def test_mutation_close_requires_valid_verdict_reason_evidence(rationale, refs, verdict):
    led = _ledger()
    tid = _open(led, "execution_audit")
    led.assign(tid, "agent-ops", "operator")
    with pytest.raises(TicketRejected):
        led.close(tid, verdict, rationale, refs)


@pytest.mark.mutation
def test_mutation_double_close_rejected_append_only():
    led = _ledger()
    tid = _open_close(led, "technical_review")
    with pytest.raises(TicketStateError):
        led.close(tid, "reject", RATIONALE, ["artifacts/run/v2.json"])


def test_reject_verdict_is_recorded_and_kept():
    led = _ledger()
    tid = _open(led, "outcome_assessment")
    led.assign(tid, "agent-verifier", "verifier")
    led.close(tid, "reject", "证据链断在制品来源，退回补证", ["artifacts/run/v.json"])
    t = led.get(tid)
    assert t.verdict == "reject" and t.status == CLOSED
    assert "退回补证" in t.rationale


def test_snapshot_roundtrip_preserves_state(tmp_path):
    led = _ledger()
    _open_close(led, "innovation_review")
    open_tid = _open(led, "outcome_assessment")
    led.assign(open_tid, "agent-verifier", "verifier")
    path = led.save(tmp_path / "tickets.json")
    back = ReviewTicketLedger.load(path, clock=lambda: 1_700_000_100.0)
    assert back.stats()["total"] == 2
    assert back.get(open_tid).status == IN_REVIEW
    assert back.stats()["by_type"]["innovation_review"]["closed"] == 1


# --- 零领域语义守卫 --------------------------------------------------------
DOMAIN_LEAK_WORDS = (
    "resistance", "seakeeping", "seaworthiness", "manoeuvring", "maneuvering",
    "propulsion", "hydrostatics", "cavitation", "ship", "hull", "suboff",
    "wigley", "ice-going", "fatigue",
)


def test_module_source_is_domain_neutral():
    for name in ("review_ticket_ledger.py", "outcome_evidence_gate.py"):
        src = (HERE / name).read_text(encoding="utf-8").lower()
        leaks = [w for w in DOMAIN_LEAK_WORDS if re.search(rf"\b{re.escape(w)}\b", src)]
        assert leaks == [], f"{name} 出现领域词: {leaks}"
    assert all(isinstance(v, dict) and v for v in PAYLOADS.values())