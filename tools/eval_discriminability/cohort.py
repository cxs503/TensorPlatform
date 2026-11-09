"""样本构造：在既有外部效度 cohort 之上**加维度**，不改标签语义。

标签与特征一律复用 :mod:`tools.external_validity.ledger`（单一契约源，避免
两套 outcome 定义打架）；本模块只补三件事：

1. **分解维度**：``repo`` / ``agent``（``claimed_by``）/ ``domain``（战役计划
   ``physics_scope`` 标签，数据驱动、仓内无任何域词常量）/ ``model``；
2. **快照即契约**：导出 JSON（gzip）并记 sha256，离线复算不碰库；
3. **时刻纪律复核**：``finished_at <= t_label``，违规行整条剔除并计数。

维度取值全部从库里读出当字符串用——`eval_discriminability` 对域内容零假设，
测试用合成域名（``repo-a``/``agent-1``）即可全部覆盖。
"""
from __future__ import annotations

import datetime as _dt
import gzip
import hashlib
import json
from typing import Any, Iterable, Mapping

from sqlalchemy import text

from tools.external_validity import ledger as _ledger

SCHEMA_VERSION = "eval-disc.v1"

#: 分解维度（顺序即报告顺序）；值为 cohort 行里的键名
DIMENSIONS: tuple[str, ...] = ("repo", "agent_family", "agent", "domain", "model",
                               "origin")

#: 每个维度允许的最小层规模（正/负各）——不足则记 WITHHELD，不硬算
MIN_STRATUM_POS = 10
MIN_STRATUM_NEG = 10

SQL_DIMS = """
WITH node_plan AS (
    SELECT DISTINCT n.mission_id AS mission_id,
           p.physics_scope AS physics_scope,
           p.name AS plan_title
    FROM campaign_nodes n
    JOIN campaign_plans p ON p.id::text = n.plan_id::text   -- nodes.plan_id → plans.id
    WHERE n.mission_id IS NOT NULL
)
SELECT m.id AS mission_id,
       COALESCE(NULLIF(m.claimed_by, ''), '(unassigned)') AS agent,
       COALESCE(NULLIF(m.model, ''), '(unknown)')        AS model,
       COALESCE(NULLIF(m.origin, ''), '(unknown)')       AS origin,
       np.physics_scope                                   AS physics_scope,
       np.plan_title                                      AS plan_title
FROM tensor_dev_missions m
LEFT JOIN node_plan np ON np.mission_id = m.id
WHERE m.finished_at IS NOT NULL
"""


def _as_list(value: Any) -> list[str]:
    """``physics_scope`` 可能是 JSON 数组 / 字符串 / NULL → 统一成字符串列表。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return []
        try:
            parsed = json.loads(v)
        except (TypeError, ValueError):
            return [v]
        return _as_list(parsed)
    return [str(value)]


def agent_family(agent: str) -> str:
    """agent 名 → 家族（去掉末段编号，如 ``tp-impl-3`` → ``tp-impl``）。

    纯字符串规则、对内容零假设（不认任何角色词义），只为把小层并成可用规模。
    """
    parts = str(agent).split("-")
    return "-".join(parts[:-1]) if len(parts) > 1 else str(agent)


def fetch_dimensions(conn) -> dict[int, dict[str, Any]]:
    """从只读连接取维度表：``{mission_id: {agent, model, domain, plan_title}}``。

    ``domain`` 取战役计划 ``physics_scope`` 的**第一个标签**（多标签时取首标签，
    完整取证留在 ``plan_title``）；无战役映射的 mission 记 ``(no_campaign)``。
    """
    out: dict[int, dict[str, Any]] = {}
    for row in conn.execute(text(SQL_DIMS)).fetchall():
        mid, agent, model, origin, scope, plan_title = row
        tags = _as_list(scope)
        out[int(mid)] = {
            "agent": str(agent),
            "agent_family": agent_family(str(agent)),
            "model": str(model),
            "origin": str(origin),
            "domain": tags[0] if tags else "(no_campaign)",
            "domain_tags": tags,
            "plan_title": plan_title,
        }
    return out


def build_study(conn, *, as_of: str | None = None) -> list[dict[str, Any]]:
    """cohort（标签+特征） × 维度 → 研究表。

    只保留**下游结果可观测**的行（``resolved`` 为真 / 标签非 pending），
    其余行计入 :func:`retention` 的删失统计——不把「不可观测」当「负类」。
    """
    cohort = _ledger.build_cohort(conn, as_of=as_of)
    dims = fetch_dimensions(conn)
    rows: list[dict[str, Any]] = []
    for rec in cohort:
        mid = int(rec["mission_id"])
        dim = dims.get(mid, {"agent": "(unassigned)", "agent_family": "(unassigned)",
                             "model": "(unknown)", "origin": "(unknown)",
                             "domain": "(no_campaign)", "domain_tags": [],
                             "plan_title": None})
        outcome = rec.get("outcome")
        rows.append({
            "mission_id": mid,
            "day": str(rec.get("day")),
            "repo": str(rec.get("repo") or "(none)"),
            "agent": dim["agent"],
            "agent_family": dim["agent_family"],
            "model": dim["model"],
            "origin": dim["origin"],
            "domain": dim["domain"],
            "domain_tags": dim["domain_tags"],
            "plan_title": dim["plan_title"],
            "grade": rec.get("grade"),
            "e_level": rec.get("e_level"),
            "status": rec.get("status"),
            "outcome": outcome,
            # 主标签：交付被下游整合（1） vs 未被整合（0）；pending = 删失
            "y": 1 if outcome == "merged" else (0 if outcome in
                                               ("rework", "discarded", "reverted") else None),
            # 次标签：返工/回退/丢弃（缺陷面）
            "x": [float(v) for v in rec.get("x") or []],
            "evidence": rec.get("evidence") or [],
        })
    return rows


def labeled(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """只保留标签可观测（``y`` 非 None）的行。"""
    out = [dict(r) for r in rows if r.get("y") is not None]
    out.sort(key=lambda r: (r["day"], r["mission_id"]))
    return out


def retention(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """样本留存与求偿统计：多少行进分析、多少行因删失被剔除（如实记 WITHHELD）。"""
    rows = list(rows)
    n = len(rows)
    kept = [r for r in rows if r.get("y") is not None]
    by_outcome: dict[str, int] = {}
    for r in rows:
        by_outcome[str(r.get("outcome"))] = by_outcome.get(str(r.get("outcome")), 0) + 1
    by_day: dict[str, dict[str, int]] = {}
    for r in kept:
        d = by_day.setdefault(r["day"], {"n": 0, "pos": 0, "neg": 0})
        d["n"] += 1
        d["pos" if r["y"] == 1 else "neg"] += 1
    return {
        "n_rows": n,
        "n_labeled": len(kept),
        "n_censored": n - len(kept),
        "labeled_rate": (len(kept) / n) if n else float("nan"),
        "by_outcome": dict(sorted(by_outcome.items(), key=lambda kv: -kv[1])),
        "by_day": dict(sorted(by_day.items())),
        "days_span": ([min(r["day"] for r in rows), max(r["day"] for r in rows)]
                      if rows else [None, None]),
    }


# ───────────────────────────────── 快照 ─────────────────────────────────

def snapshot_payload(rows: list[dict[str, Any]], *, as_of: str | None,
                     first_grades_feature: str = "") -> dict[str, Any]:
    """快照字典（含元信息与留存统计；不含任何用户名/密钥）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "feature_contract": list(getattr(_ledger, "FEATURE_NAMES", ())),
        "dimensions": list(DIMENSIONS),
        "retention": retention(rows),
        "rows": rows,
        "note": first_grades_feature,
    }


def dump_snapshot(path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """写 gzip JSON 快照并返回 ``{path, sha256, bytes}``（审计链锚点）。"""
    text_json = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    raw = text_json.encode("utf-8")
    with gzip.open(path, "wb", compresslevel=9) as fh:
        fh.write(raw)
    return {"path": path, "sha256": hashlib.sha256(raw).hexdigest(),
            "uncompressed_bytes": len(raw)}


def load_snapshot(path: str) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def snapshot_sha256(path: str) -> str:
    with gzip.open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()