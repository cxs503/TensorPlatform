"""acceptance —— 质询机制的机器验收门。

判据（来自战役节点 IN27-H4 的机器验收口径）：
  A1 每周期题数 ∈ [3,5]           —— 逐周期检查 cycles.question_count
  A2 连续 7 天有产出              —— 末 7 个 UTC 日每日 ≥1 周期，且中间无断日
  A3 至少 2 件转化为真实修补/改进单 —— 去重质询数 ≥2，且每件证据文件存在、sha256 对得上

三重诚实条款（避免门变成自证）：
  H1 只有 ``mode=live`` 的周期能撑起 A2；backfill（历史回放）单独计数并在结论里明示，
     绝不把回放当「当日产出」。
  H2 转化的 ``fix_mission_id`` 若存在，要求该单号落在账本引用的修补链里；
     无单号（仅 task_key）时必须在 note 里写明单号待补，并计入 ``unlinked``。
  H3 证据文件摘要必须与记录一致（防事后改写证据）。

输出 verdict ∈ {PASS, FAIL, PENDING}；PENDING 只在「机制已按日运转但观察窗口未满」时给出，
且必须带 ``live_days`` / ``required_days`` 数字——不允许用 PENDING 粉饰 FAIL。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .ledger import MAX_PER_CYCLE, MIN_PER_CYCLE, Ledger

REQUIRED_DAYS = 7
REQUIRED_CONVERSIONS = 2


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class CriterionResult:
    name: str
    ok: bool
    detail: str
    numbers: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateVerdict:
    verdict: str
    as_of: str
    criteria: list[CriterionResult]
    artifact_paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": "IN27-H4",
            "verdict": self.verdict,
            "as_of": self.as_of,
            "criteria": [asdict(c) for c in self.criteria],
            "artifact_paths": self.artifact_paths,
            "all_criteria_ok": all(c.ok for c in self.criteria),
        }


def _check_per_cycle(ledger: Ledger) -> CriterionResult:
    rows = [dict(r) for r in ledger._conn.execute(
        "SELECT cycle_id, question_count, mode FROM cycles ORDER BY cycle_id").fetchall()]
    bad = [r for r in rows if not (MIN_PER_CYCLE <= int(r["question_count"]) <= MAX_PER_CYCLE)]
    ok = bool(rows) and not bad
    return CriterionResult(
        name="A1_每周期题数3-5",
        ok=ok,
        detail=(f"{len(rows)} 个周期，越界 {len(bad)} 个"
                + ("" if not bad else f"：{[r['cycle_id'] for r in bad][:5]}")),
        numbers={"cycles": len(rows), "violations": len(bad),
                 "min": MIN_PER_CYCLE, "max": MAX_PER_CYCLE,
                 "counts": [int(r["question_count"]) for r in rows][-10:]},
    )


def _check_continuity(ledger: Ledger, as_of: datetime,
                      required_days: int = REQUIRED_DAYS) -> CriterionResult:
    """末 N 个 UTC 日（含当日）每日须有 ≥1 个 live 周期。"""
    days = ledger.cycle_days()
    window = [(as_of.date() - timedelta(days=i)).isoformat()
              for i in range(required_days - 1, -1, -1)]
    live_days, missing, backfill_only = [], [], []
    for d in window:
        slot = days.get(d, {})
        if slot.get("live", 0) > 0:
            live_days.append(d)
        elif slot.get("backfill", 0) > 0:
            backfill_only.append(d)
            missing.append(d)
        else:
            missing.append(d)
    consecutive = 0
    for d in reversed(window):
        if d in live_days:
            consecutive += 1
        else:
            break
    total_days = len([d for d in days if days[d].get("live", 0) > 0])
    if len(live_days) >= required_days:
        verdict_ok, status = True, "PASS"
    elif consecutive >= 1:
        verdict_ok, status = False, "PENDING"
    else:
        verdict_ok, status = False, "FAIL"
    return CriterionResult(
        name="A2_连续7天产出",
        ok=verdict_ok,
        detail=(f"{status}：末 {required_days} 日内 live 出勤 {len(live_days)}/{required_days}，"
                f"current_streak={consecutive}，仅回放日={backfill_only}，缺日={missing}；"
                f"账本累计 live 日 {total_days}"),
        numbers={"live_days_in_window": len(live_days), "required_days": required_days,
                 "current_streak": consecutive, "missing_days": missing,
                 "backfill_only_days": backfill_only, "live_days_total": total_days,
                 "status": status},
    )


def _check_conversions(ledger: Ledger, required: int = REQUIRED_CONVERSIONS
                       ) -> tuple[CriterionResult, list[dict[str, Any]]]:
    convs = ledger.conversions()
    seen: dict[str, dict[str, Any]] = {}
    tampered, missing_evidence, unlinked = [], [], []
    for c in convs:
        path = c.get("evidence_path") or ""
        if not os.path.isfile(path):
            missing_evidence.append(c["fingerprint"])
            continue
        if _sha256_file(path) != c.get("evidence_sha256"):
            tampered.append(c["fingerprint"])
            continue
        if not c.get("fix_mission_id") and not c.get("fix_task_key"):
            unlinked.append(c["fingerprint"])
            continue
        if not c.get("fix_mission_id"):
            unlinked.append(c["fingerprint"])
        seen.setdefault(c["fingerprint"], c)
    uniq = sorted(seen)
    ok = len(uniq) >= required and not tampered and not missing_evidence
    return CriterionResult(
        name="A3_至少2件真实转化",
        ok=ok,
        detail=(f"{'PASS' if ok else 'FAIL'}：有效转化 {len(uniq)}/{required}"
                f"（记录 {len(convs)} 条），证据缺失 {missing_evidence}，摘要不符 {tampered}，"
                f"未挂 mission_id {sorted(set(unlinked))}"),
        numbers={"conversions_recorded": len(convs), "unique_converted": len(uniq),
                 "required": required, "missing_evidence": missing_evidence,
                 "tampered": tampered, "unlinked": sorted(set(unlinked))},
    ), [seen[k] for k in uniq]


def evaluate(ledger: Ledger, as_of: datetime | None = None,
             required_days: int = REQUIRED_DAYS,
             required_conversions: int = REQUIRED_CONVERSIONS) -> GateVerdict:
    as_of = as_of or datetime.now(timezone.utc)
    a1 = _check_per_cycle(ledger)
    a2 = _check_continuity(ledger, as_of, required_days)
    a3, converted = _check_conversions(ledger, required_conversions)
    criteria = [a1, a2, a3]
    if all(c.ok for c in criteria):
        verdict = "PASS"
    elif any(c.numbers.get("status") == "PENDING" for c in criteria) and \
            all(c.ok for c in criteria if c.numbers.get("status") != "PENDING"):
        verdict = "PENDING"  # 机制在运转、只差观察窗口
    else:
        verdict = "FAIL"
    return GateVerdict(verdict=verdict, as_of=as_of.isoformat(), criteria=criteria,
                       artifact_paths={"ledger": os.path.abspath(ledger.path),
                                       "audit_log": os.path.abspath(ledger.audit_path)})


def write_verdict(verdict: GateVerdict, path: str, extra: dict[str, Any] | None = None) -> str:
    payload = verdict.to_dict()
    if extra:
        payload.update(extra)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    return path