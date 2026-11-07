#!/usr/bin/env python3
"""四类审查工单台账（SPEC v1.2 §6.1/§6.5 的机器落点）。

四类审查：Execution Audit / Technical Review / Outcome Assessment /
Innovation Review。本单把 **Outcome Assessment** 与 **Innovation Review**
两类接入工单流（扩 R3 验证专员线：这两类的受理角色固定为 `verifier`，
与既有平台角色集合一致），并把 SPEC 的独立性要求落成机器拒收条件：

- 开单：审查类型已知、承受对象/生产者非空、按类型必填的载荷字段齐备；
  **Innovation Review 必含新颖性检索范围声明字段** `novelty_search_scope`
  （字符串 ≥ 8 字符，或 {"sources": [...], "period": "..."} 结构），
  否则开单被拒。
- 派单：受理角色必须等于该类型的规定角色；审查者 ≠ 生产者（独立性），
  否则拒收。
- 结单：必须在 IN_REVIEW（先派后结）、裁决 ∈ {approve, reject}、
  理由 ≥ 12 字符、带证据引用；结单后不可重复结（append-only 判决史）。

零领域语义：不出现任何领域词表；域标签只作为字符串记录，不参与判据分支。
台账是纯内存对象 + 可选 JSON 快照，时钟可注入（测试确定性）。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

# --- 四类审查（SPEC §6.1） ---------------------------------------------------
REVIEW_TYPES: Dict[str, str] = {
    "execution_audit": "是否真实执行、记录完整、资源与预算符合授权",
    "technical_review": "实现/设计/接口/测试/安全/可维护性是否满足要求",
    "outcome_assessment": "结果是否达到目标、是否产生价值、局限是否披露",
    "innovation_review": "新方法/新能力/新假设是否有对照实验与证据支持",
}

#: 各类型的受理角色 —— outcome_assessment / innovation_review 接入 R3 验证专员线
REQUIRED_REVIEWER_ROLE: Dict[str, str] = {
    "execution_audit": "operator",
    "technical_review": "reviewer",
    "outcome_assessment": "verifier",
    "innovation_review": "verifier",
}

#: 各类型开单必填载荷字段
REQUIRED_PAYLOAD_FIELDS: Dict[str, tuple[str, ...]] = {
    "execution_audit": ("evidence_refs",),
    "technical_review": ("diff_ref", "test_evidence"),
    "outcome_assessment": ("claimed_grade", "evidence_level", "acceptance_refs"),
    "innovation_review": ("novelty_search_scope", "hypothesis"),
}

#: Innovation Review 的新颖性检索范围声明字段名（SPEC §7.1 S5 要求）
NOVELTY_FIELD = "novelty_search_scope"
MIN_NOVELTY_CHARS = 8
MIN_RATIONALE_CHARS = 12

VERDICTS = ("approve", "reject")
OPEN = "open"
IN_REVIEW = "in_review"
CLOSED = "closed"
STATUSES = (OPEN, IN_REVIEW, CLOSED)

_GRADE_RE = re.compile(r"^(S[1-5]|S-N)$")
_EVIDENCE_RE = re.compile(r"^E[0-4]$")


class TicketRejected(ValueError):
    """开单/派单/结单被门拒收（携带逐条理由，便于机器判读）。"""

    def __init__(self, reasons: Sequence[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons) or "rejected")


class TicketStateError(ValueError):
    """非法的工单状态迁移（例如未派单就结单、重复结单）。"""


def default_clock() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# 载荷校验
# ---------------------------------------------------------------------------

def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def validate_novelty_scope(scope: Any) -> List[str]:
    """新颖性检索范围声明的机器校验（Innovation Review 必填）。"""
    if not _non_empty(scope):
        return [f"{NOVELTY_FIELD} 缺失：Innovation Review 必须声明新颖性检索范围"]
    reasons: List[str] = []
    if isinstance(scope, str):
        if len(scope.strip()) < MIN_NOVELTY_CHARS:
            reasons.append(
                f"{NOVELTY_FIELD} 过短（{len(scope.strip())}<{MIN_NOVELTY_CHARS} 字符）")
        return reasons
    if isinstance(scope, dict):
        if not _non_empty(scope.get("sources")):
            reasons.append(f"{NOVELTY_FIELD}.sources 为空：须列出检索来源")
        if not _non_empty(scope.get("period")):
            reasons.append(f"{NOVELTY_FIELD}.period 为空：须声明检索时段")
        return reasons
    reasons.append(f"{NOVELTY_FIELD} 类型非法（需字符串或结构体）")
    return reasons


def validate_payload(review_type: str, payload: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    for name in REQUIRED_PAYLOAD_FIELDS[review_type]:
        if not _non_empty((payload or {}).get(name)):
            reasons.append(f"缺必填字段 {name}")
    if review_type == "innovation_review":
        reasons.extend(validate_novelty_scope((payload or {}).get(NOVELTY_FIELD)))
    if review_type == "outcome_assessment":
        grade = (payload or {}).get("claimed_grade")
        ev = (payload or {}).get("evidence_level")
        if _non_empty(grade) and not _GRADE_RE.match(str(grade)):
            reasons.append(f"claimed_grade 非法: {grade!r}")
        if _non_empty(ev) and not _EVIDENCE_RE.match(str(ev)):
            reasons.append(f"evidence_level 非法: {ev!r}")
    return reasons


# ---------------------------------------------------------------------------
# 工单
# ---------------------------------------------------------------------------

@dataclass
class Ticket:
    ticket_id: str
    review_type: str
    subject_ref: str
    producer_id: str
    payload: Dict[str, Any]
    status: str = OPEN
    reviewer_id: Optional[str] = None
    reviewer_role: Optional[str] = None
    verdict: Optional[str] = None
    rationale: Optional[str] = None
    evidence_refs: List[str] = field(default_factory=list)
    created_at: float = 0.0
    assigned_at: Optional[float] = None
    closed_at: Optional[float] = None
    events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "review_type": self.review_type,
            "subject_ref": self.subject_ref,
            "producer_id": self.producer_id,
            "payload": self.payload,
            "status": self.status,
            "reviewer_id": self.reviewer_id,
            "reviewer_role": self.reviewer_role,
            "verdict": self.verdict,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
            "created_at": self.created_at,
            "assigned_at": self.assigned_at,
            "closed_at": self.closed_at,
            "events": [dict(e) for e in self.events],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Ticket":
        payload = {k: v for k, v in data.items()
                   if k not in {"events", "evidence_refs"}}
        payload["evidence_refs"] = list(data.get("evidence_refs") or [])
        payload["events"] = [dict(e) for e in (data.get("events") or [])]
        return cls(**payload)


class ReviewTicketLedger:
    """四类审查工单台账（append-only 事件史，可 JSON 快照）。"""

    def __init__(self, clock: Callable[[], float] = default_clock,
                 seq_start: int = 1):
        self._clock = clock
        self._seq = seq_start
        self._tickets: Dict[str, Ticket] = {}
        self._order: List[str] = []

    # -- 开单 ---------------------------------------------------------------
    def open(self, review_type: str, subject_ref: str, producer_id: str,
             payload: Optional[Dict[str, Any]] = None,
             ticket_id: Optional[str] = None) -> Ticket:
        reasons: List[str] = []
        if review_type not in REVIEW_TYPES:
            reasons.append(f"未知审查类型 {review_type!r}；允许 {tuple(REVIEW_TYPES)}")
            raise TicketRejected(reasons)
        if not _non_empty(subject_ref):
            reasons.append("subject_ref 为空")
        if not _non_empty(producer_id) or str(producer_id).strip() == "":
            reasons.append("producer_id 为空")
        body = dict(payload or {})
        if not isinstance(body, dict):
            reasons.append("payload 必须是对象")
            body = {}
        reasons.extend(validate_payload(review_type, body))
        if ticket_id is not None and ticket_id in self._tickets:
            reasons.append(f"ticket_id 重复: {ticket_id}")
        if reasons:
            raise TicketRejected(reasons)

        self._seq += 1
        tid = ticket_id or f"RT-{review_type}-{self._seq:04d}"
        now = self._clock()
        ticket = Ticket(ticket_id=tid, review_type=review_type,
                        subject_ref=str(subject_ref), producer_id=str(producer_id),
                        payload=body, status=OPEN, created_at=now)
        ticket.events.append({"at": now, "event": "opened",
                              "review_type": review_type,
                              "required_role": REQUIRED_REVIEWER_ROLE[review_type]})
        self._tickets[tid] = ticket
        self._order.append(tid)
        return ticket

    # -- 派单 ---------------------------------------------------------------
    def assign(self, ticket_id: str, reviewer_id: str, role: Optional[str] = None) -> Ticket:
        ticket = self.get(ticket_id)
        if ticket.status != OPEN:
            raise TicketStateError(
                f"{ticket_id} 状态 {ticket.status} 不可派单（需 {OPEN}）")
        reasons: List[str] = []
        required = REQUIRED_REVIEWER_ROLE[ticket.review_type]
        if not _non_empty(reviewer_id):
            reasons.append("reviewer_id 为空")
        if role is not None and role != required:
            reasons.append(
                f"角色不符：{ticket.review_type} 须由 {required} 受理（实为 {role}）")
        if str(reviewer_id).strip() and str(reviewer_id) == ticket.producer_id:
            reasons.append("独立性违例：审查者不得为成果生产者本人")
        if reasons:
            raise TicketRejected(reasons)

        now = self._clock()
        ticket.status = IN_REVIEW
        ticket.reviewer_id = str(reviewer_id)
        ticket.reviewer_role = required if role is None else role
        ticket.assigned_at = now
        ticket.events.append({"at": now, "event": "assigned",
                              "reviewer_id": ticket.reviewer_id,
                              "role": ticket.reviewer_role})
        return ticket

    # -- 结单 ---------------------------------------------------------------
    def close(self, ticket_id: str, verdict: str, rationale: str,
              evidence_refs: Iterable[str], reviewer_id: Optional[str] = None) -> Ticket:
        ticket = self.get(ticket_id)
        if ticket.status == CLOSED:
            raise TicketStateError(f"{ticket_id} 已结单：判决史 append-only，不可重结")
        if ticket.status != IN_REVIEW:
            raise TicketStateError(
                f"{ticket_id} 状态 {ticket.status} 不可结单（须先派单）")
        reasons: List[str] = []
        if verdict not in VERDICTS:
            reasons.append(f"裁决非法 {verdict!r}；允许 {VERDICTS}")
        if len((rationale or "").strip()) < MIN_RATIONALE_CHARS:
            reasons.append(
                f"结单理由缺失或过短（<{MIN_RATIONALE_CHARS} 字符）")
        refs = [str(r) for r in (evidence_refs or []) if str(r).strip()]
        if not refs:
            reasons.append("结单须带证据引用")
        if reviewer_id is not None and reviewer_id != ticket.reviewer_id:
            reasons.append(
                f"结单人 {reviewer_id} 与受理人 {ticket.reviewer_id} 不一致")
        if reasons:
            raise TicketRejected(reasons)

        now = self._clock()
        ticket.status = CLOSED
        ticket.verdict = verdict
        ticket.rationale = rationale.strip()
        ticket.evidence_refs = refs
        ticket.closed_at = now
        ticket.events.append({"at": now, "event": "closed", "verdict": verdict,
                              "reviewer_id": ticket.reviewer_id})
        return ticket

    # -- 查询/导出 ----------------------------------------------------------
    def get(self, ticket_id: str) -> Ticket:
        if ticket_id not in self._tickets:
            raise KeyError(f"unknown ticket {ticket_id}")
        return self._tickets[ticket_id]

    def all(self) -> List[Ticket]:
        return [self._tickets[t] for t in self._order]

    def by_type(self, review_type: str) -> List[Ticket]:
        return [t for t in self.all() if t.review_type == review_type]

    def stats(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"total": len(self._tickets),
                               "by_type": {}, "by_status": {}, "closed_by_verdict": {}}
        for rt in REVIEW_TYPES:
            out["by_type"][rt] = {"opened": 0, "closed": 0,
                                  "required_role": REQUIRED_REVIEWER_ROLE[rt]}
        for ticket in self.all():
            rec = out["by_type"][ticket.review_type]
            rec["opened"] += 1
            out["by_status"][ticket.status] = out["by_status"].get(ticket.status, 0) + 1
            if ticket.status == CLOSED:
                rec["closed"] += 1
                out["closed_by_verdict"][ticket.verdict] = \
                    out["closed_by_verdict"].get(ticket.verdict, 0) + 1
        out["all_types_openable_and_closable"] = all(
            v["opened"] > 0 and v["closed"] > 0 for v in out["by_type"].values())
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {"version": 1, "seq": self._seq,
                "tickets": [t.to_dict() for t in self.all()]}

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path,
             clock: Callable[[], float] = default_clock) -> "ReviewTicketLedger":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        led = cls(clock=clock, seq_start=int(data.get("seq", 0)))
        for rec in data.get("tickets", []):
            ticket = Ticket.from_dict(rec)
            led._tickets[ticket.ticket_id] = ticket
            led._order.append(ticket.ticket_id)
        return led


def review_types_doc() -> List[Dict[str, str]]:
    """四类审查清册（写进审计工件，机器可读）。"""
    return [{"review_type": rt, "scope": desc,
             "required_reviewer_role": REQUIRED_REVIEWER_ROLE[rt],
             "required_payload_fields": list(REQUIRED_PAYLOAD_FIELDS[rt]),
             "lifecycle": f"{OPEN} -> {IN_REVIEW} -> {CLOSED}"}
            for rt, desc in REVIEW_TYPES.items()]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI：open / assign / close / list / stats（存储路径 $REVIEW_TICKET_STORE）。"""
    import argparse
    import os

    ap = argparse.ArgumentParser(description="four-type review ticket ledger")
    ap.add_argument("action", choices=["open", "assign", "close", "list", "stats", "types"])
    ap.add_argument("--type", dest="review_type", help="审查类型")
    ap.add_argument("--subject", help="承受对象引用")
    ap.add_argument("--producer", help="生产者 id")
    ap.add_argument("--payload", help="载荷 JSON 文件或内联 JSON")
    ap.add_argument("--ticket", help="工单号")
    ap.add_argument("--reviewer", help="审查者 id")
    ap.add_argument("--role", help="受理角色")
    ap.add_argument("--verdict", help="裁决 approve|reject")
    ap.add_argument("--rationale", help="结单理由")
    ap.add_argument("--evidence", nargs="*", default=[], help="证据引用")
    args = ap.parse_args(argv)

    if args.action == "types":
        print(json.dumps(review_types_doc(), ensure_ascii=False, indent=2))
        return 0

    store = os.environ.get("REVIEW_TICKET_STORE", "agent_work/quality/review_tickets.json")
    path = Path(store)
    ledger = ReviewTicketLedger.load(path) if path.exists() else ReviewTicketLedger()

    try:
        if args.action == "open":
            payload: Dict[str, Any] = {}
            if args.payload:
                raw = (Path(args.payload).read_text(encoding="utf-8")
                       if Path(args.payload).exists() else args.payload)
                payload = json.loads(raw)
            ticket = ledger.open(args.review_type, args.subject, args.producer, payload)
        elif args.action == "assign":
            ticket = ledger.assign(args.ticket, args.reviewer, args.role)
        elif args.action == "close":
            ticket = ledger.close(args.ticket, args.verdict, args.rationale,
                                  args.evidence, reviewer_id=args.reviewer)
        else:
            ticket = None
    except (TicketRejected, TicketStateError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__,
                          "reasons": getattr(exc, "reasons", [str(exc)])},
                         ensure_ascii=False, indent=2))
        return 1

    ledger.save(path)
    if args.action in ("open", "assign", "close"):
        print(json.dumps({"ok": True, "ticket": ticket.to_dict()},
                         ensure_ascii=False, indent=2))
    elif args.action == "list":
        print(json.dumps(ledger.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(ledger.stats(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())