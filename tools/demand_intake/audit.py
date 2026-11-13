"""audit —— 事后抽样审计：可领锚的「真相门」（FEAT-DEMAND-AUTOINTAKE v1.1）。

为什么门要在事后而不是事前
--------------------------
v1 把每个候选锚扣在 ``feature_ledger.status='pending_review'`` 等采认：建议停在半路、
单子不产生，agent 的独立思考在帐上等价于零。本模块补的是**另一半**——锚一旦过了事前门
进了可领面（``open``），就不是「免检」：按日帽抽样重审，判据不合格则 ``dropped``，
并把它带出来的未终态单 ``revoked``，全过程留 ``chronicle_snapshots``。

    ingest ─► gate（事前，min_chars=4）─► open ─► **audit（事后，min_chars=8，帽 20/日）**
                                                     ├─ pass ─► 保持 open（账本留痕）
                                                     └─ fail ─► dropped + 未终态单 revoked

三条纪律（规格即代码）
----------------------
A1 **审计不是全量**：日帽 ``AUDIT_DAILY_CAP=20``，超出的部分以 ``uncovered`` 数字**如实
   报出**——抽样覆盖率是 honest 指标，不许用「审了 N 条」掩盖「还有 M 条没审」。
A2 **回撤不倒改历史**：``revoke_missions`` 只 UPDATE ``status NOT IN (终态)`` 的行；已
   ``succeeded`` 的单子留在原地（它也已经被消费过），被撤的是**还没跑的单**。
A3 **判据不复制**：裁决直接复用 :func:`tools.demand_intake.gate.evaluate`，只把验收字符
   下限从 4 提到 8。两套判据各写一遍必然漂移——「门前放行、门后被同一句话判死」是比
   噪声更坏的事。

零领域语义：本模块只认 slug 形状、量化验收、状态机与计数；任何域词条都来自 DB 注入的行。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import gate as G
from . import store as S

DROPPED = "dropped"
PASS, FAIL = "pass", "fail"


def revoke_missions(t: S.Transport, mission_ids: Sequence[int], *,
                    schema: str = "") -> List[int]:
    """把**未终态**单置 ``revoked``；返回真正被撤的 id 列表。

    ``RETURNING id`` 的 psql ``-tA`` 输出里，0 行时会带一行命令标签（``UPDATE 0``），
    所以只认「整行是纯数字」的行——不靠 split 后 ``isdigit`` 猜。
    """
    ids = sorted({int(i) for i in mission_ids if i})
    if not ids:
        return []
    idlist = ",".join(str(i) for i in ids)
    terminal = ",".join(S.sql_literal(s) for s in S.TERMINAL_MISSION_STATUSES)
    out = t.execute(
        f"UPDATE {S.q('tensor_dev_missions', schema)} "
        f"SET status = 'revoked', finished_at = now() "
        f"WHERE id IN ({idlist}) AND status NOT IN ({terminal}) RETURNING id;")
    return [int(ln) for ln in (x.strip() for x in (out or "").splitlines()) if ln.isdigit()]


def _candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"slug": row.get("slug") or "", "title": row.get("title") or "",
            "acceptance": row.get("acceptance") or ""}


def run_audit(t: S.Transport, *, daily_cap: int = S.AUDIT_DAILY_CAP,
              limit: Optional[int] = None, dry_run: bool = False,
              min_acceptance_chars: int = S.AUDIT_MIN_ACCEPTANCE_CHARS,
              schema: str = "") -> Dict[str, Any]:
    """抽一批待审锚裁决并回写；返回摘要（含日帽、覆盖缺口、原因码分布）。

    ``dry_run=True`` 时**一条行都不写**（只读全链），用于上线前排演与红队。
    """
    used = S.count_audits_today(t, schema)
    budget = max(0, int(daily_cap) - used)
    if limit is not None:
        budget = min(budget, max(0, int(limit)))
    pending_total = len(S.fetch_audit_candidates(t, 10 ** 6, schema=schema))
    summary: Dict[str, Any] = {
        "daily_cap": int(daily_cap), "audits_used_today": used, "budget": budget,
        "pending_total": pending_total, "sampled": 0, "passed": 0, "failed": 0,
        "dropped": 0, "revoked_missions": [], "reasons": {}, "decisions": [],
        "uncovered": pending_total, "dry_run": bool(dry_run),
    }
    if budget <= 0:
        return summary                      # 帽用尽：如实返回（uncovered=pending_total）

    rows = S.fetch_audit_candidates(t, budget, schema=schema)
    existing = S.fetch_existing_anchors(t, schema)
    decisions: List[G.Decision] = []
    for row in rows:
        cand = _candidate(row)
        others = [r for r in existing if str(r.get("slug") or "") != cand["slug"]]
        d = G.evaluate(cand, others, min_acceptance_chars=min_acceptance_chars)
        decisions.append(d)
        summary["sampled"] += 1
        summary["decisions"].append({"slug": d.slug, "verdict": d.verdict,
                                     "reasons": d.reasons})
        if d.ok:
            summary["passed"] += 1
            if not dry_run:
                t.execute(
                    f"UPDATE {S.q('demand_intake_log', schema)} "
                    f"SET audit_verdict = '{PASS}', audit_reason = '', "
                    f"    audited_at = now(), updated_at = now() "
                    f"WHERE id = {int(row['log_id'])};")
            continue

        summary["failed"] += 1
        summary["dropped"] += 1
        reason = ",".join(d.reasons) or "audit_fail"
        if dry_run:
            continue
        t.execute(
            f"UPDATE {S.q('feature_ledger', schema)} "
            f"SET status = {S.sql_literal(DROPPED)}, "
            f"    evidence = left(coalesce(evidence, '') || "
            f"    {S.sql_literal(' | audit_fail:' + reason)}, 4000) "
            f"WHERE slug = {S.sql_literal(cand['slug'])};")
        t.execute(
            f"UPDATE {S.q('demand_intake_log', schema)} "
            f"SET audit_verdict = '{FAIL}', audit_reason = {S.sql_literal(reason)}, "
            f"    audited_at = now(), updated_at = now() "
            f"WHERE id = {int(row['log_id'])};")
        mids = [row.get("mission_id")] + S.fetch_slug_mission_ids(t, cand["slug"], schema)
        revoked = revoke_missions(t, [m for m in mids if m], schema=schema)
        summary["revoked_missions"].extend(revoked)
        S.chronicle(t, "audit_fail",
                    {"slug": cand["slug"], "reasons": d.reasons,
                     "mission_ids": revoked, "evidence": d.evidence}, schema=schema)

    summary["reasons"] = G.gate_reason_codes(decisions)
    if not dry_run:
        summary["uncovered"] = len(S.fetch_audit_candidates(t, 10 ** 6, schema=schema))
        S.chronicle(t, "audit_run",
                    {k: summary[k] for k in ("sampled", "passed", "failed", "dropped",
                                             "uncovered", "budget")}
                    | {"revoked": len(summary["revoked_missions"]),
                       "audits_used_today": used + summary["sampled"]}, schema=schema)
    return summary
