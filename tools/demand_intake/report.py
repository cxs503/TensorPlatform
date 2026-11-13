"""report —— 对照计量（v2 改造④）：agent 铸造 vs 协调者铸造的过审率/单子成功率。

这是「主体能力成长的量尺」，所以口径必须先钉死，再看数字：

- **过审率** = 审计 pass / (pass + fail)。分母只含**已审**锚：审计帽 20/日，未审的不是
  合格也不是不合格，把它算进分母等于用覆盖率冒充质量。覆盖率单列一栏（``audit_coverage``），
  覆盖率低时过审率不可当结论用——报告里显式打 ``provisional`` 标记。
- **单子成功率** = succeeded / finished，其中 finished 只数终态
  （succeeded / failed / stopped / revoked）。revoked 单独列出：它是纪律处置，和
  honest FAIL 语义不同（095 迁移的既有口径），混进失败率会让「造假/噪声锚」看起来
  只是「能力不足」。
- 全部数字由 SQL 聚合产出，正文零手抄（``cli report --json`` 的输出即报告源）。

对照对象：``mint_kind='agent'``（agent 自主铸造）与 ``mint_kind='coordinator'``
（协调者/人工铸造，同一张 ledger、同一套判据）。两侧口径一致才有可比性——这也是
为什么 v2 不另建 agent 专用表。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .store import Transport, q, sql_literal

FINISHED_STATUSES = "('succeeded','failed','stopped','revoked')"


def _rate(num: Optional[int], den: Optional[int]) -> Optional[float]:
    if not den:
        return None
    return round(float(num or 0) / float(den), 4)


def weekly_report(t: Transport, *, days: int = 7, start: str = "", end: str = "",
                  schema: str = "") -> Dict[str, Any]:
    """窗口内按 ``mint_kind`` 聚合铸造/审计/单子结果。"""
    t_log, t_led = q("demand_intake_log", schema), q("feature_ledger", schema)
    t_mis = q("tensor_dev_missions", schema)
    if start and end:
        lo, hi = sql_literal(start) + "::timestamptz", sql_literal(end) + "::timestamptz"
    else:
        lo = f"(now() - interval '{int(days)} days')"
        hi = "now()"
    win = f"l.created_at >= {lo} AND l.created_at < {hi}"

    mint_rows = t.query(
        f"SELECT l.mint_kind, "
        f"  count(*) FILTER (WHERE l.status IN ('minted','dispatch_failed')) AS minted, "
        f"  count(*) FILTER (WHERE l.status = 'cap_exceeded') AS cap_exceeded, "
        f"  count(*) FILTER (WHERE l.status = 'duplicate') AS duplicate, "
        f"  count(*) FILTER (WHERE l.status = 'rejected') AS rejected, "
        f"  count(*) FILTER (WHERE l.audit_verdict = 'pass') AS audit_pass, "
        f"  count(*) FILTER (WHERE l.audit_verdict = 'fail') AS audit_fail, "
        f"  count(*) FILTER (WHERE l.mission_id IS NOT NULL) AS missions "
        f"FROM {t_log} l WHERE {win} GROUP BY 1 ORDER BY 1")
    outcome_rows = t.query(
        f"SELECT l.mint_kind, "
        f"  count(*) FILTER (WHERE m.status = 'succeeded') AS succeeded, "
        f"  count(*) FILTER (WHERE m.status IN ('failed','stopped')) AS failed, "
        f"  count(*) FILTER (WHERE m.status = 'revoked') AS revoked, "
        f"  count(*) FILTER (WHERE m.status IN {FINISHED_STATUSES}) AS finished "
        f"FROM {t_log} l JOIN {t_mis} m ON m.id = l.mission_id "
        f"WHERE {win} GROUP BY 1 ORDER BY 1")
    dropped_rows = t.query(
        f"SELECT l.mint_kind, count(*) AS dropped FROM {t_log} l "
        f"JOIN {t_led} f ON f.slug = l.slug "
        f"WHERE {win} AND f.status = 'dropped' AND l.audit_verdict = 'fail' GROUP BY 1")

    outcomes = {r["mint_kind"]: r for r in outcome_rows}
    dropped = {r["mint_kind"]: int(r["dropped"] or 0) for r in dropped_rows}
    kinds = sorted(set([r["mint_kind"] for r in mint_rows]) | set(outcomes))
    rows: List[Dict[str, Any]] = []
    for k in kinds:
        m = next((r for r in mint_rows if r["mint_kind"] == k), {})
        o = outcomes.get(k, {})
        minted = int(m.get("minted") or 0)
        apex, afail = int(m.get("audit_pass") or 0), int(m.get("audit_fail") or 0)
        audited = apex + afail
        succ, fin = int(o.get("succeeded") or 0), int(o.get("finished") or 0)
        rows.append({
            "mint_kind": k,
            "minted": minted,
            "cap_exceeded": int(m.get("cap_exceeded") or 0),
            "duplicate": int(m.get("duplicate") or 0),
            "rejected": int(m.get("rejected") or 0),
            "missions_dispatched": int(m.get("missions") or 0),
            "audited": audited,
            "audit_pass": apex,
            "audit_fail": afail,
            "dropped": dropped.get(k, 0),
            "audit_coverage": _rate(audited, minted),
            "approval_rate": _rate(apex, audited),
            "provisional": audited < max(1, minted // 2),
            "finished": fin,
            "succeeded": succ,
            "failed": int(o.get("failed") or 0),
            "revoked": int(o.get("revoked") or 0),
            "success_rate": _rate(succ, fin),
        })

    by_kind = {r["mint_kind"]: r for r in rows}
    a, c = by_kind.get("agent", {}), by_kind.get("coordinator", {})
    comparison = {
        "approval_gap_pp": _pp(a.get("approval_rate"), c.get("approval_rate")),
        "success_gap_pp": _pp(a.get("success_rate"), c.get("success_rate")),
        "note": ("pp = 百分点（agent − coordinator）；None = 一侧无已审/终态样本，"
                 "不得当结论引用"),
    }
    return {"window": {"start": start or f"-{int(days)}d", "end": end or "now"},
            "rows": rows, "comparison": comparison}


def _pp(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round((a - b) * 100.0, 2)


def render_markdown(report: Dict[str, Any]) -> str:
    """周报对照表（Markdown）。数字全部来自 ``weekly_report`` 的 SQL 聚合。"""
    w = report["window"]
    out = [f"### 铸造对照（{w['start']} → {w['end']}）", "",
           "| 铸造方 | 铸造 | 帽拒 | 重复 | 已审 | 过审 | 不过 | 过审率 | 审计覆盖 | 撤单 | 建单 | 终态 | 成功 | 成功率 |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in report["rows"]:
        def fmt(v, pct=False):
            if v is None:
                return "n/a"
            return f"{v * 100:.1f}%" if pct else str(v)
        out.append(
            f"| {r['mint_kind']} | {r['minted']} | {r['cap_exceeded']} | {r['duplicate']} | "
            f"{r['audited']} | {r['audit_pass']} | {r['audit_fail']} | "
            f"{fmt(r['approval_rate'], True)} | {fmt(r['audit_coverage'], True)} | "
            f"{r['revoked']} | {r['missions_dispatched']} | {r['finished']} | "
            f"{r['succeeded']} | {fmt(r['success_rate'], True)} |")
    cmp = report["comparison"]
    out += ["", f"- 过审率差（agent − coordinator）：{cmp['approval_gap_pp']} pp",
            f"- 单子成功率差（agent − coordinator）：{cmp['success_gap_pp']} pp",
            f"- 口径：{cmp['note']}"]
    prov = [r["mint_kind"] for r in report["rows"] if r["provisional"]]
    if prov:
        out.append(f"- ⚠ 审计覆盖不足（过审率仅供 provisional 参考）：{', '.join(prov)}")
    return "\n".join(out)