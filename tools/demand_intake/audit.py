"""audit —— 事后审计（v2 改造②）：抽样帽 20/日，不过则 dropped + revoke + chronicle。

为什么是「事后」而不是 v1 的「事前 pending_review」
--------------------------------------------------
事前门的结构性问题不是严格，而是**延迟即否决**：建议停在 ``pending_review`` 等采认，
没人来采认就永远不产生单子，agent 的独立思考在账上等于零；而真正该被否的东西恰恰
是「说得响、机器裁不了」的锚，这种事前也判不准（判据要对着既有锚面才成立）。

v2 的选择：铸造即单子（快），审计抽样在后（慢而狠）。抽样帽 20/日 是**成本闸**——
它决定了覆盖能力，所以必须诚实公开「今天还剩多少没被审」：摘要里
``uncovered`` = 待审计总量，覆盖缺口不允许静默。

处置链（审计不过）
------------------
1. 锚 ``feature_ledger.status='dropped'``（退出可领面，原因码进 evidence）；
2. 该锚建立的所有**未终态**单 ``tensor_dev_missions.status='revoked'``（终态单不动：
   succeeded 的单子是既成事实，revoked 不能倒改历史；failed/stopped 已结算）；
3. ``tensor_agent_integrity.strikes + 1`` 并同步 ``worker_credit_tiers``（mint.py 的
   单调降档）；strike ≥ 3 时帽降为 1/日——下一次铸造就吃到降级；
4. ``chronicle_snapshots`` 留一条 ``audit_fail`` 事件（谁、哪条锚、什么原因码、撤了哪些单）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import chronicle, judge
from .mint import apply_strike
from .store import (AUDIT_DAILY_CAP, DEFAULT_ORG_ID, Transport, count_audits_today,
                    fetch_audit_candidates, fetch_existing_anchors, q, sql_literal)

DROPPED = "dropped"


def revoke_missions(t: Transport, mission_ids: List[int], *, schema: str = "") -> List[int]:
    """把未终态单置 ``revoked``（终态单不在 UPDATE 范围，历史不被倒改）。"""
    ids = sorted({int(i) for i in mission_ids if i})
    if not ids:
        return []
    idlist = ",".join(str(i) for i in ids)
    out = t.execute(
        f"UPDATE {q('tensor_dev_missions', schema)} "
        f"SET status = 'revoked', finished_at = now() "
        f"WHERE id IN ({idlist}) AND status NOT IN "
        f"('succeeded','failed','stopped','revoked','orphan') RETURNING id;")
    # psql -tA 在 0 行时会输出命令标签 "UPDATE 0"：只认纯数字行，别把标签当 id
    return [int(ln) for ln in (x.strip() for x in out.splitlines()) if ln.isdigit()]


def run_audit(t: Transport, *, daily_cap: int = AUDIT_DAILY_CAP, limit: Optional[int] = None,
              mint_kind: str = "agent", slugs: Optional[List[str]] = None,
              dry_run: bool = False, schema: str = "") -> Dict[str, Any]:
    """抽一批待审锚裁决；返回摘要（含覆盖缺口与原因码分布）。"""
    used = count_audits_today(t, schema)
    budget = max(0, int(daily_cap) - used)
    if limit is not None:
        budget = min(budget, max(0, int(limit)))
    pending_total = len(fetch_audit_candidates(t, 100000, mint_kind=mint_kind,
                                               slugs=slugs, schema=schema))
    summary: Dict[str, Any] = {
        "daily_cap": int(daily_cap), "audits_used_today": used, "budget": budget,
        "pending_total": pending_total, "sampled": 0, "passed": 0, "failed": 0,
        "dropped": 0, "revoked_missions": [], "strikes_applied": [],
        "reasons": {}, "decisions": [], "chronicle_errors": 0, "dry_run": dry_run,
        "uncovered": pending_total,
    }
    if budget <= 0:
        return summary

    rows = fetch_audit_candidates(t, budget, mint_kind=mint_kind, slugs=slugs, schema=schema)
    summary["sampled"] = 0
    existing = fetch_existing_anchors(t, schema)
    decisions: List[judge.Decision] = []
    for row in rows:
        cand = {"slug": row["slug"], "title": row["title"], "acceptance": row["acceptance"]}
        d = judge.evaluate(cand, existing)
        decisions.append(d)
        summary["sampled"] += 1
        reason = ",".join(d.reasons) if d.reasons else "pass"
        if dry_run:
            summary["decisions"].append({"slug": d.slug, "verdict": d.verdict,
                                         "reasons": d.reasons})
            if d.ok:
                summary["passed"] += 1
            else:
                summary["failed"] += 1
            continue
        if d.ok:
            summary["passed"] += 1
            t.execute(
                f"UPDATE {q('demand_intake_log', schema)} SET audit_verdict = 'pass', "
                f"audit_reason = '', audited_at = now(), updated_at = now() "
                f"WHERE id = {int(row['log_id'])};")
        else:
            summary["failed"] += 1
            summary["dropped"] += 1
            t.execute(
                f"UPDATE {q('feature_ledger', schema)} SET status = {sql_literal(DROPPED)}, "
                f"evidence = left(coalesce(evidence, '') || "
                f"{sql_literal(' | audit_fail:' + reason)}, 4000) "
                f"WHERE slug = {sql_literal(row['slug'])};")
            t.execute(
                f"UPDATE {q('demand_intake_log', schema)} SET audit_verdict = 'fail', "
                f"audit_reason = {sql_literal(reason)}, audited_at = now(), updated_at = now() "
                f"WHERE id = {int(row['log_id'])};")
            mids = [row.get("mission_id")] + _ledger_mission_ids(t, row["slug"], schema)
            revoked = revoke_missions(t, [m for m in mids if m], schema=schema)
            summary["revoked_missions"].extend(revoked)
            agent = str(row.get("minted_by") or "")
            if agent:
                st = apply_strike(t, agent, org_id=int(row.get("org_id") or DEFAULT_ORG_ID),
                                  note=f"audit_fail:{row['slug']}:{reason}", schema=schema)
                summary["strikes_applied"].append({"agent": agent, **st})
            err = chronicle.record(
                t, "audit_fail", {"slug": row["slug"], "reasons": d.reasons,
                                  "agent": agent, "mission_ids": revoked,
                                  "mint_kind": mint_kind, "evidence": d.evidence},
                schema=schema)
            if err:
                summary["chronicle_errors"] += 1
        summary["decisions"].append({"slug": d.slug, "verdict": d.verdict,
                                     "reasons": d.reasons})

    summary["reasons"] = judge.reason_codes(decisions)
    if not dry_run:
        remaining = len(fetch_audit_candidates(t, 100000, mint_kind=mint_kind,
                                               slugs=slugs, schema=schema))
        summary["uncovered"] = remaining
        err = chronicle.record(t, "audit_run",
                               {"sampled": summary["sampled"], "passed": summary["passed"],
                                "failed": summary["failed"],
                                "revoked": len(summary["revoked_missions"]),
                                "audits_used_today": used + summary["sampled"],
                                "uncovered": remaining}, schema=schema)
        if err:
            summary["chronicle_errors"] += 1
    return summary


def _ledger_mission_ids(t: Transport, slug: str, schema: str = "") -> List[int]:
    rows = t.query(
        f"SELECT mission_id FROM {q('feature_ledger', schema)} "
        f"WHERE slug = {sql_literal(slug)} AND mission_id IS NOT NULL")
    return [int(r["mission_id"]) for r in rows]