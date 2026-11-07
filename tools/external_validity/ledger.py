"""生产库 → 外部效度 cohort（只读）。

数据源（线上 ``tensorplatform``，只读；本模块不发任何写请求）：

======================  ==========================================================
表                       用途
======================  ==========================================================
``tensor_dev_missions``  mission 本体：``repo`` / ``status`` / ``finished_at``
                         / ``result->'pr_gate'``（PR 状态与历史）/ ``goal``
``outcome_evaluations``  判级记录：``grade``(S-N,S1..S5) / ``e_level``(E0..E3)
                         / ``cross_model_verified`` / ``citation_check``
                         / ``artifact_check`` / ``reason_codes``
``mission_lifecycle_log`` 生命周期动作：``custody_requeue`` / ``orphan_detect`` → 重做
======================  ==========================================================

**标签一律来自判级记录之外的下游事实**（PR 状态、后续 mission 的返工点名、
生命周期重做记录），判级字段只作为特征（X），绝不参与标签（y）构造 —— 这是
external validity 与 internal consistency 的分界线。
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

#: S 级序（S-N = 证伪/否弃，序最低；S1 = 仅执行性证据；S5 最高）
GRADE_ORD: dict[str, int] = {"S-N": 0, "S1": 1, "S2": 2, "S3": 3, "S4": 4, "S5": 5}
#: E 级序（证据独立性等级）
E_ORD: dict[str, int] = {"E0": 0, "E1": 1, "E2": 2, "E3": 3}

#: 判级特征向量（全部由评价议事会自身产出，不含 mission 侧遥测）
FEATURE_NAMES: tuple[str, ...] = (
    "grade_ord",          # S 级序
    "e_level_ord",        # E 级序
    "cross_model",        # 是否跨模型复核
    "citation_ok",        # 引用核对是否通过
    "n_artifact_unverif", # 未核验产物数
    "n_reason_flags",     # 理由码/旗标数
)

_REVERT_RE = re.compile(r"revert|回退|撤销|退回|regress", re.I)
_REWORK_RE = re.compile(r"修复|诊断|回退|退回|撤销|返工|revert|regress|fix", re.I)
_MID_RE = re.compile(r"\bm(\d{3,5})\b")

SQL_MISSIONS = """
SELECT m.id,
       m.repo,
       m.status,
       to_char(m.finished_at, 'YYYY-MM-DD')      AS day,
       o.grade,
       o.e_level,
       o.cross_model_verified,
       o.citation_check,
       o.artifact_check,
       o.reason_codes,
       m.result -> 'pr_gate' ->> 'state'         AS pr_state,
       m.result -> 'pr_gate' -> 'history'         AS pr_hist,
       m.result::text                             AS result_text,
       m.goal
FROM tensor_dev_missions m
JOIN LATERAL (
    SELECT * FROM outcome_evaluations e
    WHERE e.mission_id = m.id
      AND e.grade IS NOT NULL
    ORDER BY e.created_at DESC
    LIMIT 1
) o ON TRUE
WHERE m.finished_at IS NOT NULL
"""

SQL_REQUESTS = """
SELECT mission_id, action
FROM mission_lifecycle_log
WHERE action IN ('custody_requeue', 'orphan_detect')
"""

SQL_GOALS = """
SELECT id, goal
FROM tensor_dev_missions
WHERE goal ~* '修复|诊断|回退|退回|撤销|返工|revert|regress'
"""


def grade_ordinal(grade: str | None) -> int:
    """S 级字符串 → 序（未知/NULL 记 1 = S1，保守乐观）。"""
    return GRADE_ORD.get(grade or "", 1)


def e_ordinal(level: str | None) -> int:
    """E 级字符串 → 序（未知/NULL 记 1 = E1）。"""
    return E_ORD.get(level or "", 1)


def _json(text: Any, default: Any) -> Any:
    if text is None:
        return default
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def _history_reverted(hist: Any) -> bool:
    """PR 历史里出现回退语义的备注 → 视为回退。"""
    for item in _json(hist, []) or []:
        note = str(item.get("note") or "")
        if _REVERT_RE.search(note):
            return True
    return False


def rework_targets(goal_rows: Iterable[tuple[int, str]]) -> dict[int, int]:
    """后续 mission 的「返工点名」→ {被点名 mission_id: 点名次数}。

    规则：goal 含返工语义词，且只点名 **更早** 的 mission（m<id> 且 id < 本单 id），
    避免把"本单被后续提到"误算。
    """
    out: dict[int, int] = {}
    for mid, goal in goal_rows:
        if not goal or not _REWORK_RE.search(goal):
            continue
        for cited in _MID_RE.findall(goal):
            cited_id = int(cited)
            if cited_id < int(mid):
                out[cited_id] = out.get(cited_id, 0) + 1
    return out


def classify_outcome(row: dict[str, Any], *, rework_hit: int = 0,
                     requeued: bool = False) -> dict[str, Any]:
    """单条 mission → 下游结果标签（与判级字段完全解耦）。

    Returns dict with ``merged`` / ``discarded`` / ``reverted`` / ``rework`` /
    ``outcome`` (四分类) / ``evidence`` (证据链，便于审计)。
    """
    pr_state = (row.get("pr_state") or "").strip().lower() or None
    # 回退证据只取该 mission 自己 PR 的历史备注（result 全文含"回退某个版本"这类
    # 任务描述，全文匹配会大量假阳性——见 docs §注意点）。
    reverted = pr_state == "reverted" or _history_reverted(row.get("pr_hist"))
    merged = pr_state == "merged"
    discarded = pr_state == "closed" or (
        pr_state is None and (row.get("status") or "") in ("revoked", "stopped")
    )
    rework = bool(rework_hit) or bool(requeued)
    evidence: list[str] = []
    if merged:
        evidence.append("pr_gate.state=merged")
    if discarded:
        evidence.append("pr_gate.state=closed" if pr_state == "closed"
                        else f"status={row.get('status')}&no_pr")
    if reverted:
        evidence.append("revert_marker")
    if rework_hit:
        evidence.append(f"rework_cited_x{rework_hit}")
    if requeued:
        evidence.append("lifecycle_requeue")

    if reverted:
        outcome = "reverted"
    elif rework:
        outcome = "rework"
    elif merged:
        outcome = "merged"
    elif discarded:
        outcome = "discarded"
    else:
        outcome = "pending"        # PR 仍 open / 尚未落地 —— 删失样本
    return {
        "merged": merged,
        "discarded": discarded,
        "reverted": reverted,
        "rework": rework,
        "outcome": outcome,
        "evidence": evidence,
    }


def cohort_features(row: dict[str, Any]) -> list[float]:
    """判级特征向量（顺序见 :data:`FEATURE_NAMES`）。"""
    cit = _json(row.get("citation_check"), {})
    art = _json(row.get("artifact_check"), {})
    flags = _json(row.get("reason_codes"), [])
    return [
        float(grade_ordinal(row.get("grade"))),
        float(e_ordinal(row.get("e_level"))),
        1.0 if row.get("cross_model_verified") else 0.0,
        1.0 if (isinstance(cit, dict) and cit.get("ok")) else 0.0,
        float(len(art) if isinstance(art, (dict, list)) else 0),
        float(len(flags) if isinstance(flags, list) else 0),
    ]


def build_cohort(conn, *, as_of: str | None = None) -> list[dict[str, Any]]:
    """从只读连接构造带标签的 cohort（一次遍历，纯读）。

    ``as_of`` 给定时只保留 ``day <= as_of`` 的样本（演练/回放用）。
    """
    raw = [dict(zip(
        ["id", "repo", "status", "day", "grade", "e_level", "cross_model_verified",
         "citation_check", "artifact_check", "reason_codes", "pr_state", "pr_hist",
         "result_text", "goal"], r))
        for r in _fetch(conn, SQL_MISSIONS)]
    rework = rework_targets((int(r[0]), r[1]) for r in _fetch(conn, SQL_GOALS))
    requeued = {int(r[0]) for r in _fetch(conn, SQL_REQUESTS)}

    cohort: list[dict[str, Any]] = []
    for row in raw:
        if as_of and (row["day"] or "") > as_of:
            continue
        cls = classify_outcome(row, rework_hit=rework.get(int(row["id"]), 0),
                               requeued=int(row["id"]) in requeued)
        observable_pr = (row.get("pr_state") or "").strip() != ""
        resolved = bool(observable_pr or cls["rework"] or cls["reverted"]) and \
            (row["status"] or "") in ("succeeded", "failed", "revoked", "stopped")
        item = {
            "mission_id": int(row["id"]),
            "repo": row["repo"],
            "day": row["day"],
            "grade": row["grade"],
            "e_level": row["e_level"],
            "status": row["status"],
            "pr_state": row["pr_state"],
            "x": cohort_features(row),
            "merged": cls["merged"],
            "discarded": cls["discarded"],
            "reverted": cls["reverted"],
            "rework": cls["rework"],
            "outcome": cls["outcome"],
            "evidence": cls["evidence"],
            # 观测性标记（决定样本进入哪个判据；见 acceptance._select）
            "observable_pr": observable_pr,
            "resolved": resolved,
            # 标签：
            #   y_merge  = 1 ⇔ PR 真的合入主干（成功合并）
            #   y_defect = 1 ⇔ 回退或有返工（判级"该拦下来"的那一类）
            "y_merge": 1 if cls["merged"] else 0,
            "y_defect": 1 if (cls["reverted"] or cls["rework"]) else 0,
        }
        cohort.append(item)
    cohort.sort(key=lambda d: (d["day"] or "", d["mission_id"]))
    return cohort


def _fetch(conn, sql: str):
    try:
        from sqlalchemy import text  # 延迟导入，测试可注入假连接
        return conn.execute(text(sql)).fetchall()
    except ModuleNotFoundError:  # pragma: no cover
        cur = conn.cursor()
        cur.execute(sql)
        return cur.fetchall()


def summarize(cohort: list[dict[str, Any]]) -> dict[str, Any]:
    """cohort 结构摘要（写进 artifacts，供审计核对）。"""
    from collections import Counter

    return {
        "n": len(cohort),
        "days": sorted({c["day"] for c in cohort if c["day"]}),
        "n_days": len({c["day"] for c in cohort if c["day"]}),
        "outcome_counts": dict(Counter(c["outcome"] for c in cohort)),
        "grade_counts": dict(Counter(c["grade"] for c in cohort)),
        "e_level_counts": dict(Counter(c["e_level"] for c in cohort)),
        "y_merge_positives": sum(c["y_merge"] for c in cohort),
        "y_defect_positives": sum(c["y_defect"] for c in cohort),
        "repos": dict(Counter(c["repo"] for c in cohort)),
    }