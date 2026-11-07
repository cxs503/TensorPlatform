#!/usr/bin/env python3
"""评价体系自身脉搏（IN24-E4）：chronicle_snapshots 的「评价面」+ 阈值告警 + 质量报表。

问题
----
``outcome_evaluations``（S×E 评价实体，见 ``tools/outcome_evaluations.py``）已经在记录
成果评价，但**评价体系自己无人观测**：覆盖率掉没掉、pending 积压堆到多老、独立复核
占比是否退化成 0（全机器自证）、S/E 分布是否塌缩——这些都没进编年史，也没有任何
阈值告警。本模块把「评价体系」当作被观测对象，按**拍**把它钉进 ``chronicle_snapshots``。

三件事，一个模块
----------------
1. **评价面（facet）**：``capture()`` 用一组 SQL 从平台库算出评价面全字段——覆盖率
   ``confirmed / succeeded``（窗口比）、S 分布（**不加权**分层计数）、E 级分布、
   独立复核占比、auto_scan 占比、pending 积压年龄（条数/最老/中位/P90）。每条 SQL
   原文与参数都留在 facet 的 ``sql`` / ``sql_params``；``recompute()`` 直接重跑这些
   SQL 原文并逐字段比对 → **SQL 可复算**。
2. **阈值与告警**：``evaluate_thresholds()`` 对三条阈值（覆盖率 < 60%、pending > 200、
   独立占比 < 50%）做机器判定；``emit_alerts()`` 只在**上升沿**发 digest 告警
   （同键持续越界只发一次，恢复后再次越界重新发）。``run_drill()`` 是负控演练：
   合成越界数据，验证「越界 = 每条阈值恰一次告警、重复拍 0 条」。
3. **周期质量报表入编年史**：``build_digest()`` 渲染人读质量报表；``write_snapshot()``
   把 ``{评价面 + digest + 快照链}`` 写入 ``chronicle_snapshots``。

落库形状（零迁移，向后兼容）
----------------------------
生产表 ``chronicle_snapshots`` 只有 ``(ts timestamptz, payload jsonb)`` 两列。因此
「增评价面」不加列，而是让 payload 携带带 schema 版本的**评价面块**与**快照链**
（``snapshot_id`` / ``prev_snapshot_id``）：老读取方按 ``schema`` 忽略未知块，新读取方按
``schema == "chronicle-eval-pulse/1"`` 取面。SQLite 侧由本模块建同形表
``(ts TEXT, payload TEXT)``，同一套代码两种引擎。

**零领域语义**：模块内无任何学科/物理/业务词；被观测主体由 ``--subject``（数据注入）
给定，等级语义由 ``outcome_evaluations`` 的调用方提供。测试夹具一律合成主体。

边界（写清楚，不粉饰）
----------------------
- ``independent_share`` / ``auto_scan_share`` 的分母是**窗口内 confirmed 记录数**；分母为 0
  时占比**无定义** → 记 ``None`` 并列入 ``undefined_metrics``，不当作 0%（否则「还没开始
  评价」会被误报成「独立复核缺失」）。
- 积压年龄自 ``created_at`` 起算到 ``as_of``，是「待评价记录躺了多久」的**下界**，不含
  mission 进入评价队列前的时间。
- 阈值是工程默认值（60% / 200 / 50%），可注入覆盖；它们不是学科判据，报告须写明当次阈值来源。
- 快照只证明「当时的数是什么」，不证明数对；评价本身的正确性由 ``outcome_evaluations`` 判据负责。

用法
----
    python3 tools/chronicle/eval_pulse.py capture --dsn "$DSN" --subject X --write
    python3 tools/chronicle/eval_pulse.py verify  --dsn "$DSN" --facet <facet.json>
    python3 tools/chronicle/eval_pulse.py drill   --json-out <drill.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]

# ─────────────────────────────── 常量 ───────────────────────────────

SCHEMA = "chronicle-eval-pulse/1"
DIGEST_SCHEMA = "chronicle-eval-digest/1"
FACET = "evaluation"

SNAPSHOT_TABLE = "chronicle_snapshots"
TAB_MISSIONS = "tensor_dev_missions"
TAB_EVALS = "outcome_evaluations"

S_GRADES: tuple[str, ...] = ("S1", "S2", "S3", "S4", "S5")
S_NEGATIVE = "S-N"
S_BUCKETS: tuple[str, ...] = S_GRADES + (S_NEGATIVE,)
E_LEVELS: tuple[str, ...] = ("E0", "E1", "E2", "E3", "E4")
REVIEWER_KINDS: tuple[str, ...] = ("independent", "auto_scan", "unspecified")

#: 三条阈值（工程默认，可注入覆盖）
DEFAULT_THRESHOLDS: dict[str, float] = {
    "coverage_min": 0.60,           # 覆盖率 < 60% → 告警
    "pending_max": 200.0,           # pending 积压 > 200 条 → 告警
    "independent_share_min": 0.50,  # 独立复核占比 < 50% → 告警
}

#: 评价面必须齐备的字段（「连续两拍含评价面全字段」的机器判据）
FACET_FIELDS: tuple[str, ...] = (
    "schema", "facet", "as_of", "subject", "window", "missions_in_window",
    "coverage", "records_in_window", "s_distribution", "e_distribution",
    "reviewer_mix", "independent_share", "auto_scan_share", "pending_backlog",
    "thresholds", "alert_states", "alerts", "undefined_metrics",
    "sql", "sql_params", "source", "facet_fingerprint",
)

ALERT_KEYS: tuple[str, ...] = ("coverage_low", "pending_backlog_high", "independent_share_low")

_JSON_CAST = {"postgresql": "CAST(:payload AS jsonb)", "sqlite": ":payload"}
_TS_CAST = {"postgresql": "CAST(:ts AS timestamptz)", "sqlite": ":ts"}


# ─────────────────────────────── 小工具 ───────────────────────────────

def now_utc() -> str:
    """UTC ISO8601（秒精度：字符串序 == 时序，跨引擎一致）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_ts(value: Any) -> datetime | None:
    """宽松解析 ISO8601（``Z`` / ``+00:00`` / 无时区按 UTC）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z",
                        "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _round(value: float | None, nd: int = 6) -> float | None:
    return None if value is None else round(float(value), nd)


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(obj: Any) -> str:
    """内容指纹（审计链用；不是签名，见模块头「边界」）。"""
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("empty values")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _dialect_of(conn) -> str:
    """从 SQLAlchemy Connection/Session/Engine 取方言名（默认 postgresql）。"""
    for obj in (conn, getattr(conn, "engine", None), getattr(conn, "bind", None)):
        if obj is None:
            continue
        name = getattr(getattr(obj, "dialect", None), "name", None)
        if name:
            if name.startswith("sqlite"):
                return "sqlite"
            if name.startswith("postgres"):
                return "postgresql"
            return str(name)
    return "postgresql"


def _rows(conn, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    from sqlalchemy import text

    return [dict(r) for r in conn.execute(text(sql), dict(params)).mappings().all()]


# ─────────────────────────────── SQL ───────────────────────────────

def _win_cte() -> str:
    """窗口 CTE：该主体最近 ``:window`` 张 succeeded mission。"""
    return (
        "\nWITH win AS (\n"
        f"    SELECT m.id AS mission_id, m.finished_at AS finished_at\n"
        f"    FROM {TAB_MISSIONS} m\n"
        "    WHERE m.status = 'succeeded' AND m.repo = :subject\n"
        "    ORDER BY m.finished_at DESC\n"
        "    LIMIT :window\n"
        ")\n"
    )


def pulse_queries(dialect: str = "postgresql") -> dict[str, str]:
    """评价面全部 SQL 原文（随 facet 落盘，供独立复算）。

    只做分层计数：不加权、不排名、不合成单一分数。
    """
    if dialect not in _JSON_CAST:
        raise ValueError(f"unsupported dialect: {dialect!r}")
    win = _win_cte()
    return {
        "window": win + "SELECT COUNT(*) AS missions FROM win",
        "coverage": win + f"""
SELECT
  (SELECT COUNT(*) FROM win) AS missions_succeeded,
  (SELECT COUNT(DISTINCT w.mission_id) FROM win w
     JOIN {TAB_EVALS} e ON e.mission_id = w.mission_id AND e.status = 'confirmed'
  ) AS missions_confirmed,
  (SELECT COUNT(DISTINCT w.mission_id) FROM win w
     JOIN {TAB_EVALS} e ON e.mission_id = w.mission_id AND e.status IN ('confirmed','candidate')
  ) AS missions_decided,
  (SELECT COUNT(DISTINCT w.mission_id) FROM win w
     JOIN {TAB_EVALS} e ON e.mission_id = w.mission_id AND e.status = 'pending'
  ) AS missions_pending,
  (SELECT COUNT(*) FROM win w WHERE NOT EXISTS (
     SELECT 1 FROM {TAB_EVALS} e WHERE e.mission_id = w.mission_id)
  ) AS missions_without_record
""",
        "records": win + f"""
SELECT e.status AS status, COUNT(*) AS n
FROM {TAB_EVALS} e JOIN win w ON w.mission_id = e.mission_id
GROUP BY e.status
""",
        "s_distribution": win + f"""
SELECT e.grade AS grade, COUNT(*) AS n
FROM {TAB_EVALS} e JOIN win w ON w.mission_id = e.mission_id
WHERE e.status = 'confirmed'
GROUP BY e.grade
""",
        "e_distribution": win + f"""
SELECT e.e_level AS e_level, COUNT(*) AS n
FROM {TAB_EVALS} e JOIN win w ON w.mission_id = e.mission_id
WHERE e.status = 'confirmed'
GROUP BY e.e_level
""",
        "reviewer_mix": win + f"""
SELECT COALESCE(e.reviewer_kind, 'unspecified') AS kind, COUNT(*) AS n
FROM {TAB_EVALS} e JOIN win w ON w.mission_id = e.mission_id
WHERE e.status = 'confirmed'
GROUP BY COALESCE(e.reviewer_kind, 'unspecified')
""",
        "pending_backlog": f"""
SELECT COUNT(*) AS n,
       MIN(created_at) AS oldest_created_at,
       MAX(created_at) AS newest_created_at
FROM {TAB_EVALS}
WHERE status = 'pending' AND repo = :subject
""",
        "pending_rows": f"""
SELECT evaluation_id, mission_id, grade, e_level, created_at
FROM {TAB_EVALS}
WHERE status = 'pending' AND repo = :subject
ORDER BY created_at ASC, evaluation_id ASC
""",
    }


# ─────────────────────────────── 采集 ───────────────────────────────

def capture(
    conn,
    *,
    subject: str,
    window: int = 30,
    as_of: str | None = None,
    thresholds: Mapping[str, float] | None = None,
    dialect: str | None = None,
    source: str = "platform_db",
) -> dict[str, Any]:
    """采一拍评价面（只读 SQL，不写库）。

    ``conn`` 需支持 ``execute(text, params).mappings().all()``（SQLAlchemy Connection/Session）。
    ``subject`` 是被观测主体键（仓名等），由调用方注入——模块内不含领域词。
    """
    dialect = dialect or _dialect_of(conn)
    th = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        th.update({str(k): float(v) for k, v in thresholds.items()})
    as_of = as_of or now_utc()
    queries = pulse_queries(dialect)
    params: dict[str, Any] = {"subject": subject, "window": int(window)}

    win_rows = _rows(conn, queries["window"], params)
    missions_in_window = int(win_rows[0]["missions"]) if win_rows else 0

    cov_r = _rows(conn, queries["coverage"], params)
    cov = cov_r[0] if cov_r else {}
    succeeded = int(cov.get("missions_succeeded") or 0)
    confirmed = int(cov.get("missions_confirmed") or 0)
    decided = int(cov.get("missions_decided") or 0)
    pending_m = int(cov.get("missions_pending") or 0)
    no_record = int(cov.get("missions_without_record") or 0)
    coverage_ratio = (confirmed / succeeded) if succeeded else None

    rec_counts = {str(r["status"]): int(r["n"]) for r in _rows(conn, queries["records"], params)}

    s_dist = {g: 0 for g in S_BUCKETS}
    for r in _rows(conn, queries["s_distribution"], params):
        s_dist[str(r["grade"])] = s_dist.get(str(r["grade"]), 0) + int(r["n"])
    e_dist = {e: 0 for e in E_LEVELS}
    for r in _rows(conn, queries["e_distribution"], params):
        e_dist[str(r["e_level"])] = e_dist.get(str(r["e_level"]), 0) + int(r["n"])

    rev = {k: 0 for k in REVIEWER_KINDS}
    for r in _rows(conn, queries["reviewer_mix"], params):
        rev[str(r["kind"])] = rev.get(str(r["kind"]), 0) + int(r["n"])
    confirmed_records = sum(rev.values())
    independent_share = (rev["independent"] / confirmed_records) if confirmed_records else None
    auto_scan_share = (rev["auto_scan"] / confirmed_records) if confirmed_records else None

    pb_r = _rows(conn, queries["pending_backlog"], params)
    pb = pb_r[0] if pb_r else {"n": 0, "oldest_created_at": None, "newest_created_at": None}
    pending_rows = _rows(conn, queries["pending_rows"], params)
    as_of_dt = _parse_ts(as_of) or datetime.now(timezone.utc)
    ages: list[float] = []
    for row in pending_rows:
        created = _parse_ts(row.get("created_at"))
        if created is not None:
            ages.append(max(0.0, (as_of_dt - created).total_seconds() / 3600.0))
    backlog = {
        "count": int(pb.get("n") or 0),
        "rows_counted": len(pending_rows),
        "oldest_created_at": (str(pb["oldest_created_at"]) if pb.get("oldest_created_at") else None),
        "newest_created_at": (str(pb["newest_created_at"]) if pb.get("newest_created_at") else None),
        "oldest_age_hours": _round(max(ages), 4) if ages else None,
        "median_age_hours": _round(statistics.median(ages), 4) if ages else None,
        "p90_age_hours": _round(_percentile(ages, 0.9), 4) if ages else None,
        "age_basis": "created_at -> as_of（记录生成起算；不含评价前排队时间）",
    }

    facet: dict[str, Any] = {
        "schema": SCHEMA,
        "facet": FACET,
        "as_of": as_of,
        "subject": subject,
        "window": int(window),
        "missions_in_window": missions_in_window,
        "coverage": {
            "missions_succeeded": succeeded,
            "missions_confirmed": confirmed,
            "missions_decided": decided,
            "missions_pending": pending_m,
            "missions_without_record": no_record,
            "ratio": _round(coverage_ratio),
            "definition": "confirmed / succeeded（窗口内 mission 数之比）",
        },
        "records_in_window": rec_counts,
        "s_distribution": {
            "counts": s_dist,
            "total": sum(s_dist.values()),
            "weighting": "none",
            "ranking": None,
            "note": "分层计数；candidate/pending 不计入分布；S-N 单列不混入 S1–S5。",
        },
        "e_distribution": {
            "counts": e_dist,
            "total": sum(e_dist.values()),
            "note": "confirmed 记录的 E 级分层计数（E0–E4）。",
        },
        "reviewer_mix": {
            "counts": rev,
            "total": confirmed_records,
            "note": "confirmed 记录按 reviewer_kind 分层；independent=独立复核，auto_scan=机器证据扫描。",
        },
        "independent_share": _round(independent_share),
        "auto_scan_share": _round(auto_scan_share),
        "pending_backlog": backlog,
        "thresholds": {k: float(v) for k, v in th.items()},
        "source": source,
        "sql": queries,
        "sql_params": params,
    }

    alerts, states, undefined = evaluate_thresholds(facet, th)
    facet["alerts"] = alerts
    facet["alert_states"] = states
    facet["undefined_metrics"] = undefined
    facet["facet_fingerprint"] = fingerprint(
        {k: facet[k] for k in ("coverage", "s_distribution", "e_distribution",
                               "independent_share", "auto_scan_share", "pending_backlog",
                               "thresholds")}
    )
    return facet# ──────────────────────────── 阈值与告警 ────────────────────────────

def evaluate_thresholds(
    facet: Mapping[str, Any], thresholds: Mapping[str, float] | None = None
) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    """三条阈值机器判定，返回 ``(alerts, states, undefined_metrics)``。

    - ``alerts``：只含越界项，每条带 ``key/metric/value/threshold/comparison/message``；
    - ``states``：三键全覆盖（``ok`` / ``breach`` / ``undefined``），供上升沿判定；
    - ``undefined_metrics``：分母为 0 的占比（无定义），**不**当作 0%。
    """
    th = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        th.update({str(k): float(v) for k, v in thresholds.items()})
    cov = facet.get("coverage") or {}
    backlog = facet.get("pending_backlog") or {}
    states: dict[str, str] = {}
    undefined: list[str] = []
    alerts: list[dict[str, Any]] = []

    ratio = cov.get("ratio")
    if ratio is None:
        states["coverage_low"] = "undefined"
        undefined.append("coverage")
    elif float(ratio) < th["coverage_min"]:
        states["coverage_low"] = "breach"
        alerts.append({
            "key": "coverage_low",
            "metric": "coverage.ratio",
            "value": float(ratio),
            "threshold": th["coverage_min"],
            "comparison": "<",
            "severity": "warning",
            "message": f"覆盖率 {float(ratio):.1%} < 阈值 {th['coverage_min']:.0%}",
        })
    else:
        states["coverage_low"] = "ok"

    pending = int(backlog.get("count") or 0)
    if float(pending) > th["pending_max"]:
        states["pending_backlog_high"] = "breach"
        alerts.append({
            "key": "pending_backlog_high",
            "metric": "pending_backlog.count",
            "value": pending,
            "threshold": th["pending_max"],
            "comparison": ">",
            "severity": "warning",
            "message": f"pending 积压 {pending} 条 > 阈值 {int(th['pending_max'])}",
        })
    else:
        states["pending_backlog_high"] = "ok"

    indep = facet.get("independent_share")
    if indep is None:
        states["independent_share_low"] = "undefined"
        undefined.append("independent_share")
    elif float(indep) < th["independent_share_min"]:
        states["independent_share_low"] = "breach"
        alerts.append({
            "key": "independent_share_low",
            "metric": "independent_share",
            "value": float(indep),
            "threshold": th["independent_share_min"],
            "comparison": "<",
            "severity": "warning",
            "message": f"独立复核占比 {float(indep):.1%} < 阈值 {th['independent_share_min']:.0%}",
        })
    else:
        states["independent_share_low"] = "ok"

    return alerts, states, undefined


def emit_alerts(
    alerts: Sequence[Mapping[str, Any]],
    *,
    previous_active_keys: Iterable[str] | None = None,
    ledger_path: str | Path | None = None,
    emitted_at: str | None = None,
) -> dict[str, Any]:
    """**上升沿**告警：只有上一拍没有、这一拍越界的键才发。

    同一异常连续多拍只告警一次（不刷屏）；恢复后再次越界会重新发（新的上升沿）。
    返回 ``{"emitted": [...], "suppressed": [...], "policy": ...}``；``ledger_path`` 给定时
    追加一行 JSONL 告警台账（不覆盖历史）。
    """
    prev = {str(k) for k in (previous_active_keys or ())}
    emitted: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    stamp = emitted_at or now_utc()
    for alert in alerts:
        key = str(alert.get("key"))
        entry = {"schema": DIGEST_SCHEMA, "kind": "digest_alert", "emitted_at": stamp,
                 "edge": "sustained" if key in prev else "rising", **dict(alert)}
        (suppressed if key in prev else emitted).append(entry)
    result = {
        "emitted": emitted,
        "suppressed": suppressed,
        "previous_active_keys": sorted(prev),
        "policy": "rising_edge_only：同键持续越界不重复告警；恢复后再次越界重新告警。",
        "ledger": None,
    }
    if ledger_path is not None:
        path = Path(ledger_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for entry in emitted:
                fh.write(_canonical(entry) + "\n")
        result["ledger"] = str(path)
    return result


def run_drill(
    *,
    thresholds: Mapping[str, float] | None = None,
    ledger_path: str | Path | None = None,
) -> dict[str, Any]:
    """阈值**负控演练**：合成三条阈值全部越界的数据，验证告警恰好一次。

    合成数据仅在本函数内构造并显式标注 ``source = "synthetic_drill"``，**不是**平台实测值；
    两次判定：首拍（无历史）→ 每键一条 ``emitted``；重拍（上拍全部越界）→ 0 条。
    """
    th = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        th.update({str(k): float(v) for k, v in thresholds.items()})
    synthetic: dict[str, Any] = {
        "schema": SCHEMA,
        "facet": FACET,
        "as_of": now_utc(),
        "subject": "SyntheticSubject",
        "window": 10,
        "missions_in_window": 10,
        "coverage": {"missions_succeeded": 10, "missions_confirmed": 2, "missions_decided": 2,
                     "missions_pending": 0, "missions_without_record": 8, "ratio": 0.2,
                     "definition": "confirmed / succeeded（窗口内 mission 数之比）"},
        "records_in_window": {"confirmed": 10},
        "s_distribution": {"counts": {"S1": 2, "S2": 0, "S3": 0, "S4": 0, "S5": 0, "S-N": 0},
                           "total": 2, "weighting": "none", "ranking": None},
        "e_distribution": {"counts": {"E0": 0, "E1": 0, "E2": 2, "E3": 0, "E4": 0}, "total": 2},
        "reviewer_mix": {"counts": {"independent": 0, "auto_scan": 10, "unspecified": 0}, "total": 10},
        "independent_share": 0.0,
        "auto_scan_share": 1.0,
        "pending_backlog": {"count": 250, "rows_counted": 0, "oldest_created_at": None,
                            "newest_created_at": None, "oldest_age_hours": 400.0,
                            "median_age_hours": 200.0, "p90_age_hours": 350.0},
        "thresholds": th,
        "source": "synthetic_drill",
        "sql": {},
        "sql_params": {},
    }
    alerts, states, undefined = evaluate_thresholds(synthetic, th)
    synthetic["alerts"] = alerts
    synthetic["alert_states"] = states
    synthetic["undefined_metrics"] = undefined
    synthetic["facet_fingerprint"] = fingerprint({k: synthetic[k] for k in (
        "coverage", "s_distribution", "e_distribution", "independent_share",
        "auto_scan_share", "pending_backlog", "thresholds")})

    first = emit_alerts(alerts, previous_active_keys=set(), ledger_path=ledger_path)
    second = emit_alerts(alerts, previous_active_keys=[a["key"] for a in alerts], ledger_path=None)
    keys_first = [a["key"] for a in first["emitted"]]
    return {
        "schema": DIGEST_SCHEMA,
        "kind": "threshold_drill",
        "as_of": synthetic["as_of"],
        "thresholds": th,
        "synthetic_facet": synthetic,
        "synthetic_facet_fingerprint": synthetic["facet_fingerprint"],
        "alert_states": states,
        "undefined_metrics": undefined,
        "breached_keys": [a["key"] for a in alerts],
        "first_edge": first,
        "second_edge": second,
        "exactly_once_ok": bool(
            sorted(keys_first) == sorted(a["key"] for a in alerts)
            and len(keys_first) == len(set(keys_first)) == len(alerts)
            and not second["emitted"]
        ),
        "note": "演练数据为合成负控（source=synthetic_drill），仅验证告警通路，不代表平台实测。",
    }


# ──────────────────────────── 质量报表 ────────────────────────────

def build_digest(facet: Mapping[str, Any]) -> str:
    """把一拍评价面渲染成周期质量报表（markdown；数字全部取自 facet）。"""
    cov = facet.get("coverage") or {}
    ratio = cov.get("ratio")
    backlog = facet.get("pending_backlog") or {}
    th = facet.get("thresholds") or DEFAULT_THRESHOLDS
    states = facet.get("alert_states") or {}
    alerts = facet.get("alerts") or []
    lines: list[str] = []
    lines.append(f"# 评价体系脉搏 · `{facet.get('subject')}`")
    lines.append("")
    lines.append(f"- 采集时刻（as_of）：`{facet.get('as_of')}` · 窗口：近 {facet.get('window')} 张 succeeded mission")
    lines.append(f"- 评价面 schema：`{facet.get('schema')}` · 数据源：`{facet.get('source')}`")
    lines.append("")
    lines.append("## 1. 覆盖率（confirmed / succeeded，窗口比）")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 窗口 succeeded mission | {cov.get('missions_succeeded', 0)} |")
    lines.append(f"| 已有 confirmed 评价 | {cov.get('missions_confirmed', 0)} |")
    lines.append(f"| 已判定（confirmed/candidate） | {cov.get('missions_decided', 0)} |")
    lines.append(f"| 仍 pending | {cov.get('missions_pending', 0)} |")
    lines.append(f"| 无评价记录 | {cov.get('missions_without_record', 0)} |")
    shown = "无定义" if ratio is None else f"{float(ratio):.1%}"
    lines.append(f"| **覆盖率** | **{shown}**（阈值 ≥ {float(th.get('coverage_min', 0)):.0%}） |")
    lines.append("")
    lines.append("## 2. S 等级分布（confirmed，不加权）")
    lines.append("")
    lines.append("| 等级 | 数量 |")
    lines.append("|---|---|")
    for g, n in (facet.get("s_distribution") or {}).get("counts", {}).items():
        lines.append(f"| {g} | {n} |")
    lines.append("")
    lines.append("## 3. E 级分布（confirmed）")
    lines.append("")
    lines.append("| 证据级 | 数量 |")
    lines.append("|---|---|")
    for e, n in (facet.get("e_distribution") or {}).get("counts", {}).items():
        lines.append(f"| {e} | {n} |")
    lines.append("")
    lines.append("## 4. 复核结构")
    lines.append("")
    mix = (facet.get("reviewer_mix") or {}).get("counts", {})
    indep = facet.get("independent_share")
    auto = facet.get("auto_scan_share")
    lines.append("| 复核者类型 | confirmed 记录 | 占比 |")
    lines.append("|---|---|---|")
    for k, n in mix.items():
        share = "—"
        if k == "independent" and indep is not None:
            share = f"{float(indep):.1%}"
        elif k == "auto_scan" and auto is not None:
            share = f"{float(auto):.1%}"
        lines.append(f"| {k} | {n} | {share} |")
    lines.append("")
    indep_s = "无定义" if indep is None else f"{float(indep):.1%}"
    auto_s = "无定义" if auto is None else f"{float(auto):.1%}"
    lines.append(f"- 独立复核占比 **{indep_s}**（阈值 ≥ {float(th.get('independent_share_min', 0)):.0%}）")
    lines.append(f"- auto_scan 占比 **{auto_s}**（机器证据扫描，非同行独立评审）")
    lines.append("")
    lines.append("## 5. pending 积压年龄")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 积压条数 | {backlog.get('count', 0)}（阈值 ≤ {int(float(th.get('pending_max', 0)))}） |")
    lines.append(f"| 最老记录 | `{backlog.get('oldest_created_at')}` |")
    lines.append(f"| 最老年龄 | {backlog.get('oldest_age_hours')} 小时 |")
    lines.append(f"| 中位年龄 | {backlog.get('median_age_hours')} 小时 |")
    lines.append(f"| P90 年龄 | {backlog.get('p90_age_hours')} 小时 |")
    lines.append("")
    lines.append("## 6. 阈值判定与 digest 告警")
    lines.append("")
    lines.append("| 阈值键 | 状态 |")
    lines.append("|---|---|")
    for key in ALERT_KEYS:
        lines.append(f"| {key} | {states.get(key, 'undefined')} |")
    lines.append("")
    if alerts:
        lines.append("**越界项：**")
        lines.append("")
        for a in alerts:
            lines.append(f"- `{a['key']}`：{a['message']}")
    else:
        lines.append("**无越界项。**")
    undefined = facet.get("undefined_metrics") or []
    if undefined:
        lines.append("")
        lines.append(f"> 无定义指标（分母为 0，不按 0% 处理）：{', '.join(undefined)}")
    lines.append("")
    lines.append("## 7. 口径声明")
    lines.append("")
    lines.append("- S 分布为分层计数，**不加权、不排名、不合成单一分数**；candidate/pending 不计入分布，S-N 单列。")
    lines.append("- 积压年龄以记录 `created_at` 起算到 `as_of`，是「待评价记录躺了多久」的下界。")
    lines.append("- 本报表数字全部来自 facet 内 SQL（原文见快照 payload 的 `sql` 段），可独立复算。")
    lines.append("")
    lines.append(f"评价面指纹：`{facet.get('facet_fingerprint', '—')}`")
    return "\n".join(lines)# ──────────────────────────── 快照落库 ────────────────────────────

_SNAPSHOT_DDL = {
    "postgresql": f"""
CREATE TABLE IF NOT EXISTS {SNAPSHOT_TABLE} (
    ts      timestamp with time zone NOT NULL DEFAULT now(),
    payload jsonb NOT NULL
)""",
    "sqlite": f"""
CREATE TABLE IF NOT EXISTS {SNAPSHOT_TABLE} (
    ts      TEXT NOT NULL,
    payload TEXT NOT NULL
)""",
}


def ensure_snapshot_table(conn, dialect: str | None = None) -> str:
    """建/复用编年史快照表（PG 侧与生产表同形 ``(ts, payload jsonb)``，零迁移）。"""
    from sqlalchemy import text

    dialect = dialect or _dialect_of(conn)
    if dialect not in _SNAPSHOT_DDL:
        raise ValueError(f"unsupported dialect: {dialect!r}")
    conn.execute(text(_SNAPSHOT_DDL[dialect]))
    return dialect


def write_snapshot(
    conn,
    facet: Mapping[str, Any],
    *,
    kind: str = "eval_pulse",
    digest_md: str | None = None,
    ts: str | None = None,
    prev_snapshot_id: str | None = None,
    alerts_result: Mapping[str, Any] | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    """写一拍评价面到 ``chronicle_snapshots``（payload = 评价面 + digest + 告警边沿 + 快照链）。"""
    from sqlalchemy import text

    dialect = ensure_snapshot_table(conn)
    snapshot_id = uuid.uuid4().hex[:16]
    stamp = ts or facet.get("as_of") or now_utc()
    payload = {
        "schema": SCHEMA,
        "facet": FACET,
        "kind": kind,
        "snapshot_id": snapshot_id,
        "prev_snapshot_id": prev_snapshot_id,
        "subject": facet.get("subject"),
        "as_of": stamp,
        "eval": dict(facet),
        "digest_md": digest_md if digest_md is not None else build_digest(facet),
        "alert_edge": ({
            "emitted": [a.get("key") for a in (alerts_result or {}).get("emitted", [])],
            "suppressed": [a.get("key") for a in (alerts_result or {}).get("suppressed", [])],
        } if alerts_result is not None else None),
    }
    sql = f"INSERT INTO {SNAPSHOT_TABLE} (ts, payload) VALUES ({_TS_CAST[dialect]}, {_JSON_CAST[dialect]})"
    conn.execute(text(sql), {"ts": stamp, "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True)})
    if commit:
        conn.commit()
    return {"snapshot_id": snapshot_id, "ts": stamp, "prev_snapshot_id": prev_snapshot_id,
            "kind": kind, "payload": payload, "insert_sql": sql}


def load_recent_snapshots(conn, *, limit: int = 2, kind: str | None = "eval_pulse") -> list[dict[str, Any]]:
    """按时间倒序取最近若干拍（payload 统一成 dict：PG jsonb / SQLite TEXT 同形）。"""
    from sqlalchemy import text

    rows = conn.execute(text(f"SELECT ts, payload FROM {SNAPSHOT_TABLE} ORDER BY ts DESC")).mappings().all()
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, (str, bytes)):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                continue
        if not isinstance(payload, dict):
            continue
        if kind is not None and payload.get("kind") != kind:
            continue
        out.append({"ts": str(row["ts"]), "payload": payload})
        if len(out) >= int(limit):
            break
    return out


def facet_complete(payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """校验一拍快照的评价面字段是否齐备；返回 ``(完整?, 缺失字段列表)``。"""
    facet = (payload or {}).get("eval")
    if not isinstance(facet, Mapping):
        return False, list(FACET_FIELDS)
    missing = [f for f in FACET_FIELDS if f not in facet]
    for key in ("coverage", "s_distribution", "e_distribution", "pending_backlog", "reviewer_mix"):
        if key in facet and not isinstance(facet[key], Mapping):
            missing.append(f"{key}:not_object")
    for key in ("independent_share", "auto_scan_share"):
        if key in facet and facet[key] is not None and not isinstance(facet[key], (int, float)):
            missing.append(f"{key}:not_number")
    if "sql" in facet and not (isinstance(facet["sql"], Mapping) and facet["sql"]):
        missing.append("sql:empty")
    return (not missing), missing


def consecutive_facet_check(conn, *, kind: str = "eval_pulse") -> dict[str, Any]:
    """判据①：连续两拍快照都含评价面全字段（附指纹与快照链校验）。"""
    snaps = load_recent_snapshots(conn, limit=2, kind=kind)
    report: dict[str, Any] = {
        "schema": DIGEST_SCHEMA,
        "kind": "consecutive_facet_check",
        "snapshots_found": len(snaps),
        "required_fields": list(FACET_FIELDS),
        "per_snapshot": [],
    }
    ok = True
    for snap in snaps:
        complete, missing = facet_complete(snap["payload"])
        facet = snap["payload"].get("eval") or {}
        report["per_snapshot"].append({
            "ts": snap["ts"],
            "snapshot_id": snap["payload"].get("snapshot_id"),
            "prev_snapshot_id": snap["payload"].get("prev_snapshot_id"),
            "facet_complete": complete,
            "missing_fields": missing,
            "facet_fields_present": len([f for f in FACET_FIELDS if f in facet]),
            "facet_fields_required": len(FACET_FIELDS),
            "facet_fingerprint": facet.get("facet_fingerprint"),
            "coverage_ratio": (facet.get("coverage") or {}).get("ratio"),
            "independent_share": facet.get("independent_share"),
            "pending_count": (facet.get("pending_backlog") or {}).get("count"),
            "alert_states": facet.get("alert_states"),
        })
        ok = ok and complete
    chained = len(snaps) == 2 and (
        snaps[0]["payload"].get("prev_snapshot_id") == snaps[1]["payload"].get("snapshot_id"))
    report["all_complete"] = bool(ok and len(snaps) == 2)
    report["chain_linked"] = bool(chained)
    return report


def recompute(conn, facet: Mapping[str, Any]) -> dict[str, Any]:
    """判据③：重跑 facet 自带 SQL **原文**并逐字段比对。

    直接执行 ``facet["sql"]``（不是再调一次 ``capture()``），所以能证明「报表里的数 ==
    这些 SQL 现在算出来的数」。中位/P90 年龄在 Python 侧由 ``pending_rows`` 逐条时间算，
    与其他引擎无关（SQLite 无 percentile_cont）。
    """
    from sqlalchemy import text

    params = dict(facet.get("sql_params") or {})
    sqls = facet.get("sql") or {}
    as_of = facet.get("as_of") or now_utc()
    as_of_dt = _parse_ts(as_of) or datetime.now(timezone.utc)
    diffs: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}

    def run(name: str) -> list[dict[str, Any]]:
        return [dict(r) for r in conn.execute(text(sqls[name]), params).mappings().all()]

    cov = (run("coverage") or [{}])[0]
    stated_cov = facet.get("coverage") or {}
    for k in ("missions_succeeded", "missions_confirmed", "missions_decided",
              "missions_pending", "missions_without_record"):
        got, want = int(cov.get(k) or 0), int(stated_cov.get(k) or 0)
        checks[f"coverage.{k}"] = {"sql": got, "facet": want}
        if got != want:
            diffs.append({"field": f"coverage.{k}", "sql": got, "facet": want})
    succeeded = int(cov.get("missions_succeeded") or 0)
    ratio_sql = _round((int(cov.get("missions_confirmed") or 0) / succeeded) if succeeded else None)
    ratio_facet = stated_cov.get("ratio")
    checks["coverage.ratio"] = {"sql": ratio_sql, "facet": ratio_facet}
    if (ratio_sql is None) != (ratio_facet is None) or (
            ratio_sql is not None and round(float(ratio_sql), 6) != round(float(ratio_facet), 6)):
        diffs.append({"field": "coverage.ratio", "sql": ratio_sql, "facet": ratio_facet})

    for name, domain, key in (("s_distribution", S_BUCKETS, "grade"),
                              ("e_distribution", E_LEVELS, "e_level")):
        counts = {d: 0 for d in domain}
        for r in run(name):
            counts[str(r[key])] = counts.get(str(r[key]), 0) + int(r["n"])
        stated = {k: int(v) for k, v in ((facet.get(name) or {}).get("counts") or {}).items()}
        checks[name] = {"sql": counts, "facet": stated}
        if counts != stated:
            diffs.append({"field": name, "sql": counts, "facet": stated})

    mix = {k: 0 for k in REVIEWER_KINDS}
    for r in run("reviewer_mix"):
        mix[str(r["kind"])] = mix.get(str(r["kind"]), 0) + int(r["n"])
    stated_mix = {k: int(v) for k, v in ((facet.get("reviewer_mix") or {}).get("counts") or {}).items()}
    checks["reviewer_mix"] = {"sql": mix, "facet": stated_mix}
    if mix != stated_mix:
        diffs.append({"field": "reviewer_mix", "sql": mix, "facet": stated_mix})
    total = sum(mix.values())
    for key, num in (("independent_share", mix["independent"]), ("auto_scan_share", mix["auto_scan"])):
        share = _round(num / total) if total else None
        stated = facet.get(key)
        checks[key] = {"sql": share, "facet": stated}
        if (share is None) != (stated is None) or (
                share is not None and stated is not None and round(float(share), 6) != round(float(stated), 6)):
            diffs.append({"field": key, "sql": share, "facet": stated})

    pb = (run("pending_backlog") or [{}])[0]
    rows = run("pending_rows")
    ages = []
    for row in rows:
        created = _parse_ts(row.get("created_at"))
        if created is not None:
            ages.append(max(0.0, (as_of_dt - created).total_seconds() / 3600.0))
    stated_pb = facet.get("pending_backlog") or {}
    sql_pb = {
        "count": int(pb.get("n") or 0),
        "rows_counted": len(rows),
        "oldest_age_hours": _round(max(ages), 4) if ages else None,
        "median_age_hours": _round(statistics.median(ages), 4) if ages else None,
        "p90_age_hours": _round(_percentile(ages, 0.9), 4) if ages else None,
    }
    checks["pending_backlog"] = {"sql": sql_pb, "facet": {k: stated_pb.get(k) for k in sql_pb}}
    for k, v in sql_pb.items():
        want = stated_pb.get(k)
        if (v is None) != (want is None) or (
                v is not None and want is not None and round(float(v), 4) != round(float(want), 4)):
            diffs.append({"field": f"pending_backlog.{k}", "sql": v, "facet": want})

    return {
        "schema": DIGEST_SCHEMA,
        "kind": "sql_recompute_check",
        "subject": facet.get("subject"),
        "as_of": as_of,
        "sql_used": sorted(sqls.keys()),
        "params": params,
        "checks": checks,
        "diffs": diffs,
        "ok": not diffs,
    }


# ──────────────────────────── 连接与 CLI ────────────────────────────

def connect(dsn: str):
    """SQLAlchemy 连接：PG 优先 pg8000（本机已装），退而 psycopg；sqlite 直连。"""
    from sqlalchemy import create_engine

    if dsn.startswith("postgresql://"):
        last_err: Exception | None = None
        for driver in ("postgresql+pg8000://", "postgresql+psycopg://", "postgresql+psycopg2://"):
            try:
                return create_engine(driver + dsn[len("postgresql://"):], future=True).connect()
            except Exception as exc:  # noqa: BLE001 — 逐个驱动回退
                last_err = exc
        raise SystemExit(f"无法建立数据库连接：{last_err}")
    return create_engine(dsn, future=True).connect()


def _load_previous_keys(conn, *, kind: str = "eval_pulse") -> list[str]:
    snaps = load_recent_snapshots(conn, limit=1, kind=kind)
    if not snaps:
        return []
    return list(((snaps[0]["payload"].get("eval") or {}).get("alerts") or []) and
                [a.get("key") for a in (snaps[0]["payload"].get("eval") or {}).get("alerts") or []])


def _cmd_capture(args) -> int:
    conn = connect(args.dsn)
    try:
        facet = capture(conn, subject=args.subject, window=args.window,
                        as_of=args.as_of, source=args.source)
        previous = _load_previous_keys(conn)
        alerts = facet["alerts"]
        edge = emit_alerts(alerts, previous_active_keys=previous,
                           ledger_path=args.ledger, emitted_at=args.now)
        digest = build_digest(facet)
        out = {"schema": SCHEMA, "kind": "eval_pulse_capture", "facet": facet,
               "digest_md": digest, "alert_edge": edge}
        if args.write:
            snap = write_snapshot(conn, facet, digest_md=digest, ts=args.as_of,
                                  prev_snapshot_id=args.prev_snapshot_id,
                                  alerts_result=edge, commit=True)
            out["snapshot"] = {k: snap[k] for k in ("snapshot_id", "ts", "prev_snapshot_id",
                                                    "kind", "insert_sql")}
            out["snapshot"]["payload_sha256"] = fingerprint(snap["payload"])
        if args.json_out:
            path = Path(args.json_out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            print(f"wrote {path}")
        if args.digest_out:
            path = Path(args.digest_out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(digest + "\n", encoding="utf-8")
            print(f"wrote {path}")
        print(json.dumps({"subject": facet["subject"], "coverage": facet["coverage"]["ratio"],
                          "independent_share": facet["independent_share"],
                          "pending_count": facet["pending_backlog"]["count"],
                          "alert_states": facet["alert_states"],
                          "emitted": [a["key"] for a in edge["emitted"]]}, ensure_ascii=False))
    finally:
        conn.close()
    return 0


def _cmd_verify(args) -> int:
    facet = json.loads(Path(args.facet).read_text(encoding="utf-8"))
    facet = facet.get("facet", facet)
    conn = connect(args.dsn)
    try:
        result = recompute(conn, facet)
    finally:
        conn.close()
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {path}")
    print(json.dumps({"ok": result["ok"], "diffs": result["diffs"]}, ensure_ascii=False))
    return 0 if result["ok"] else 1


def _cmd_drill(args) -> int:
    result = run_drill(ledger_path=args.ledger)
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {path}")
    print(json.dumps({"exactly_once_ok": result["exactly_once_ok"],
                      "breached_keys": result["breached_keys"],
                      "emitted": [a["key"] for a in result["first_edge"]["emitted"]],
                      "reemitted": [a["key"] for a in result["second_edge"]["emitted"]]},
                     ensure_ascii=False))
    return 0 if result["exactly_once_ok"] else 1


def _cmd_check(args) -> int:
    conn = connect(args.dsn)
    try:
        result = consecutive_facet_check(conn, kind=args.kind)
    finally:
        conn.close()
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {path}")
    print(json.dumps({"all_complete": result["all_complete"], "chain_linked": result["chain_linked"],
                      "snapshots_found": result["snapshots_found"]}, ensure_ascii=False))
    return 0 if result["all_complete"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="评价体系脉搏：评价面快照 / 阈值告警 / 质量报表")
    sub = ap.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture", help="采一拍评价面（--write 才写快照行）")
    cap.add_argument("--dsn", required=True)
    cap.add_argument("--subject", required=True)
    cap.add_argument("--window", type=int, default=30)
    cap.add_argument("--as-of", default=None)
    cap.add_argument("--now", default=None, help="告警时间戳（默认 now，演练/复现可固定）")
    cap.add_argument("--source", default="platform_db")
    cap.add_argument("--write", action="store_true")
    cap.add_argument("--prev-snapshot-id", default=None)
    cap.add_argument("--ledger", default=None)
    cap.add_argument("--json-out", default=None)
    cap.add_argument("--digest-out", default=None)
    cap.set_defaults(func=_cmd_capture)

    ver = sub.add_parser("verify", help="重跑 facet 自带 SQL 逐字段比对")
    ver.add_argument("--dsn", required=True)
    ver.add_argument("--facet", required=True)
    ver.add_argument("--json-out", default=None)
    ver.set_defaults(func=_cmd_verify)

    drl = sub.add_parser("drill", help="阈值负控演练")
    drl.add_argument("--ledger", default=None)
    drl.add_argument("--json-out", default=None)
    drl.set_defaults(func=_cmd_drill)

    chk = sub.add_parser("check", help="连续两拍评价面齐备性检查")
    chk.add_argument("--dsn", required=True)
    chk.add_argument("--kind", default="eval_pulse")
    chk.add_argument("--json-out", default=None)
    chk.set_defaults(func=_cmd_check)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ALERT_KEYS", "DEFAULT_THRESHOLDS", "DIGEST_SCHEMA", "E_LEVELS", "FACET", "FACET_FIELDS",
    "REVIEWER_KINDS", "SCHEMA", "SNAPSHOT_TABLE", "S_BUCKETS", "S_GRADES", "S_NEGATIVE",
    "build_digest", "capture", "consecutive_facet_check", "connect", "emit_alerts",
    "ensure_snapshot_table", "evaluate_thresholds", "facet_complete", "fingerprint",
    "load_recent_snapshots", "main", "now_utc", "pulse_queries", "recompute", "run_drill",
    "write_snapshot",
]