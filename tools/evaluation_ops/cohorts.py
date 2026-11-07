"""评价队列 cohort 不变量 + ``eval_cohorts`` 周级汇总表（IN38-N09 评价运维成熟化）。

核心不变量
----------
**cohort 成员资格只由 mission 的完结时刻决定**：``mission.finished_at`` 落在第 k 个
ISO 周（UTC，周一 00:00 起）⇒ 该 mission 属于 cohort ``YYYY-Www``。窗口（7/30/90 天）
只决定「显示哪些 cohort」，**不决定一个 cohort 里算什么**。所以同一 cohort 在任意视图
下的逐项数值必须完全相等 —— 这是本模块要机器化证明的东西（见 ``tools/evaluation_ops/
views.py`` 的 ``window_independence``）。

覆盖率定义（钉死，避免口径漂移）
--------------------------------
``coverage = missions_decided / missions_total``，其中

- ``missions_total``：该 cohort 内 succeeded 且 ``finished_at`` 可解析的 mission 数；
- ``missions_decided``：有**已判定**记录（``confirmed`` | ``candidate``）的 mission 数；
- ``pending`` 是积压、``rejected`` 是负例留痕，**都不进分子**；
- 分母为 0 时 ``coverage = None`` 并列入 ``undefined_metrics`` —— 不折成 0%（「还没
  开始评价」不是「覆盖率为零」）。

时效与积压（时延一律从 ``finished_at`` 起算）
--------------------------------------------
- ``p90_latency_hours``：已判定 mission 的 ``min(decided_at) - finished_at`` 的 P90
  （线性插值分位）。``decided_at < finished_at`` 记异常样本，剔出分位并计数。
- ``backlog_rows`` / ``backlog_age_*``：``pending`` 记录行数与其年龄
  ``as_of - finished_at``（**不**从 ``created_at`` 起算：积压年龄衡量的是成果等评价等了
  多久，不是记录躺了多久）。

冻结与漂移
----------
- ``frozen``：该 cohort 的 ISO 周已在 ``as_of`` 之前结束 ⇒ 成员集合固定。
- 冻结行比较 ``inputs_fingerprint``（mission 集合 + 记录状态/判定时刻的内容哈希）：

  - 指纹不变而重算值变 → **评分器漂移**，保留旧值并置 ``drift`` 标记（这是缺陷，不是
    数据变化；测试与验收都要求它为空）；
  - 指纹变（迟到评价）→ 记 ``late_arrivals``、``revision+1`` 覆盖，旧值留在
    ``previous`` 里备审计。

零领域语义：本模块只认 mission/评价记录的**结构字段**，不含学科/业务词。

边界（不粉饰）
--------------
- ``finished_at`` 缺失或不可解析的 mission 不进任何 cohort，只计入 ``excluded``。
- 覆盖率只说明「有没有评价」，不说明评得对不对；判据正确性由
  ``tools/outcome_evaluations.decide`` 负责。
- ``inputs_fingerprint`` 是内容哈希，不是签名，不能抗篡改，只用于漂移判定。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "eval-cohorts/1"
COHORT_TABLE = "eval_cohorts"
TAB_MISSIONS = "tensor_dev_missions"
TAB_EVALS = "outcome_evaluations"

#: 计入「已判定」的记录状态（candidate 合法登记，不计入分布但算已评价）
DECIDED_STATUSES: tuple[str, ...] = ("confirmed", "candidate")
PENDING_STATUS = "pending"

#: 周界缓冲（天）：发车即冻结的条件是 ISO 周已走完，即 week_start + 7 天 <= as_of
WEEK_DAYS = 7


# ─────────────────────────── 时间小工具 ───────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_ts(value: Any) -> datetime | None:
    """宽松解析 ISO8601 / datetime → UTC aware；不可解析返回 None。"""
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


def isots(value: Any) -> str | None:
    dt = parse_ts(value)
    return None if dt is None else dt.replace(microsecond=0).isoformat()


def percentile(values: Sequence[float], q: float) -> float | None:
    """线性插值分位（与 ``tools/chronicle/eval_pulse.py::_percentile`` 同口径）。"""
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * float(q)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _median(values: Sequence[float]) -> float | None:
    return percentile(values, 0.5)


def _round(value: float | None, nd: int = 6) -> float | None:
    return None if value is None else round(float(value), nd)


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


# ─────────────────────────── cohort 定义 ───────────────────────────

def cohort_key(finished_at: Any) -> str | None:
    """``finished_at`` → ``"YYYY-Www"``（ISO 周，UTC）。不可解析返回 None。"""
    dt = parse_ts(finished_at)
    if dt is None:
        return None
    iso = dt.date().isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


def week_bounds(key: str) -> tuple[date, date]:
    """cohort key → ``(周一, 下周一)`` 半开区间（UTC 日期）。"""
    year, _, week = str(key).partition("-W")
    try:
        start = date.fromisocalendar(int(year), int(week), 1)
    except (TypeError, ValueError) as exc:  # pragma: no cover - 输入由 cohort_key 产出
        raise ValueError(f"bad cohort key: {key!r}") from exc
    return start, start + timedelta(days=WEEK_DAYS)


def cohort_of(finished_at: Any) -> dict[str, Any] | None:
    key = cohort_key(finished_at)
    if key is None:
        return None
    start, end = week_bounds(key)
    return {"cohort_key": key, "week_start": start.isoformat(), "week_end": end.isoformat()}


def is_frozen(row: Mapping[str, Any], *, as_of: Any) -> bool:
    """ISO 周已在 ``as_of`` 之前走完 → 成员固定（可冻结）。"""
    as_of_dt = parse_ts(as_of)
    if as_of_dt is None:
        return False
    _, end = week_bounds(str(row["cohort_key"]))
    return end <= as_of_dt.date()


# ─────────────────────────── 读库 ───────────────────────────

def _rows(conn, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    from sqlalchemy import text

    return [dict(r) for r in conn.execute(text(sql), dict(params)).mappings().all()]


def fetch_missions(conn, *, repo: str | None = None, status: str = "succeeded",
                   limit: int | None = None) -> list[dict[str, Any]]:
    """只读拉 succeeded mission 的结构字段（坐标：id / repo / finished_at / 证据指针）。"""
    sql = (
        f"SELECT id, repo, status, claimed_by, organization_id, finished_at, report_id\n"
        f"FROM {TAB_MISSIONS}\n"
        f"WHERE status = :status AND finished_at IS NOT NULL\n"
    )
    params: dict[str, Any] = {"status": status}
    if repo:
        sql += "AND repo = :repo\n"
        params["repo"] = repo
    sql += "ORDER BY finished_at, id"
    if limit:
        sql = f"SELECT * FROM (\n{sql}\n) t LIMIT :lim"
        params["lim"] = int(limit)
    return _rows(conn, sql, params)


def fetch_records(conn, *, repo: str | None = None) -> list[dict[str, Any]]:
    """只读拉评价记录的结构字段（status/decided_at 决定覆盖率与时延）。"""
    sql = f"SELECT evaluation_id, artifact_id, mission_id, status, decided_at, created_at, reason_source, reviewer_kind\nFROM {TAB_EVALS}\nWHERE mission_id IS NOT NULL\n"
    params: dict[str, Any] = {}
    if repo:
        sql += "AND repo = :repo\n"
        params["repo"] = repo
    sql += "ORDER BY mission_id, decided_at, evaluation_id"
    return _rows(conn, sql, params)


# ─────────────────────────── 计算 ───────────────────────────

def _mission_id(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compute_cohorts(missions: Iterable[Mapping[str, Any]],
                    records: Iterable[Mapping[str, Any]],
                    *,
                    as_of: Any,
                    repo: str | None = None) -> dict[str, dict[str, Any]]:
    """纯函数：mission 行 + 记录行 → 逐 cohort 指标（无窗口参数，故与视图无关）。

    这是「队列不变量」的**唯一**实现：调用方不得传 window；想看某个视图就事后按
    ``cohort_key`` 过滤（``views.filter_view``）。
    """
    as_of_dt = parse_ts(as_of) or datetime.now(timezone.utc)
    excluded = {"missing_finished_at": 0, "unparsable_finished_at": 0}

    # 1) mission → cohort 分桶（成员资格只看 finished_at）
    buckets: dict[str, dict[str, Any]] = {}
    seen_missions: set[int] = set()
    for m in missions:
        mid = _mission_id(m.get("id", m.get("mission_id")))
        if mid is None or mid in seen_missions:
            continue
        seen_missions.add(mid)
        if repo and m.get("repo") not in (None, repo):
            continue
        if m.get("finished_at") in (None, ""):
            excluded["missing_finished_at"] += 1
            continue
        info = cohort_of(m.get("finished_at"))
        if info is None:
            excluded["unparsable_finished_at"] += 1
            continue
        bucket = buckets.setdefault(info["cohort_key"], {
            **info,
            "missions": [],
            "records": [],
        })
        bucket["missions"].append({
            "mission_id": mid,
            "finished_at": isots(m.get("finished_at")),
            "repo": m.get("repo"),
        })

    # 2) 记录按 mission 归到 cohort（记录自身不携带 cohort，避免两处口径）
    mid_to_key = {r["mission_id"]: key for key, b in buckets.items() for r in b["missions"]}
    orphan_records = 0
    for rec in records:
        mid = _mission_id(rec.get("mission_id"))
        key = mid_to_key.get(mid) if mid is not None else None
        if key is None:
            orphan_records += 1
            continue
        buckets[key]["records"].append({
            "mission_id": mid,
            "status": str(rec.get("status") or "").strip().lower(),
            "decided_at": isots(rec.get("decided_at")),
            "created_at": isots(rec.get("created_at")),
            "reason_source": rec.get("reason_source"),
        })

    out: dict[str, dict[str, Any]] = {}
    for key, bucket in buckets.items():
        out[key] = _cohort_metrics(bucket, as_of_dt=as_of_dt, repo=repo)

    result = {k: out[k] for k in sorted(out)}
    for row in result.values():
        row["excluded"] = dict(excluded)
        row["orphan_records"] = orphan_records
    return result


def _cohort_metrics(bucket: Mapping[str, Any], *, as_of_dt: datetime,
                    repo: str | None) -> dict[str, Any]:
    missions = list(bucket["missions"])
    records = list(bucket["records"])
    by_mission: dict[int, list[dict[str, Any]]] = {}
    for rec in records:
        by_mission.setdefault(int(rec["mission_id"]), []).append(rec)

    decided = pending = unrecorded = overlap = 0
    pending_rows = 0
    latencies: list[float] = []
    latency_anomalies: list[dict[str, Any]] = []
    decided_without_ts = 0
    backlog_ages: list[float] = []

    for m in missions:
        mid = int(m["mission_id"])
        finished = parse_ts(m["finished_at"])
        recs = by_mission.get(mid, [])
        statuses = {r["status"] for r in recs}
        if not recs:
            unrecorded += 1
        if statuses & set(DECIDED_STATUSES):
            decided += 1
            stamps = [parse_ts(r["decided_at"]) for r in recs
                      if r["status"] in DECIDED_STATUSES and r.get("decided_at")]
            stamps = [s for s in stamps if s is not None]
            if not stamps:
                decided_without_ts += 1
            elif finished is not None:
                first = min(stamps)
                hours = (first - finished).total_seconds() / 3600.0
                if hours < 0:
                    latency_anomalies.append({"mission_id": mid, "latency_hours": round(hours, 6)})
                else:
                    latencies.append(hours)
        pending_recs = [r for r in recs if r["status"] == PENDING_STATUS]
        if pending_recs and (statuses & set(DECIDED_STATUSES)):
            overlap += 1          # 同成果既有已判定行又有待评价行（多工件/多轮），如实计数
        if pending_recs:
            pending += 1
            pending_rows += len(pending_recs)
            if finished is not None:
                backlog_ages.append((as_of_dt - finished).total_seconds() / 3600.0)

    total = len(missions)
    undefined: list[str] = []
    coverage = _round(decided / total) if total else None
    if coverage is None:
        undefined.append("coverage")
    p90 = _round(percentile(latencies, 0.9))
    if p90 is None:
        undefined.append("p90_latency_hours")
    if not backlog_ages:
        undefined.append("backlog_age_hours")

    inputs_fingerprint = fingerprint({
        "cohort_key": bucket["cohort_key"],
        "missions": sorted([[m["mission_id"], m["finished_at"]] for m in missions]),
        "records": sorted([[r["mission_id"], r["status"], r["decided_at"]] for r in records]),
    })

    return {
        "schema": SCHEMA,
        "cohort_key": bucket["cohort_key"],
        "week_start": bucket["week_start"],
        "week_end": bucket["week_end"],
        "repo": repo,
        "as_of": as_of_dt.replace(microsecond=0).isoformat(),
        "missions_total": total,
        "missions_decided": decided,
        "missions_pending": pending,
        "missions_unrecorded": unrecorded,
        "missions_decided_and_pending": overlap,
        "decided_rows": sum(1 for r in records if r["status"] in DECIDED_STATUSES),
        "pending_rows": pending_rows,
        "coverage": coverage,
        "coverage_numerator": decided,
        "coverage_denominator": total,
        "p90_latency_hours": p90,
        "latency_n": len(latencies),
        "latency_median_hours": _round(_median(latencies)),
        "latency_max_hours": _round(max(latencies)) if latencies else None,
        "latency_anomalies": latency_anomalies,
        "decided_without_ts": decided_without_ts,
        "backlog_rows": pending_rows,
        "backlog_age_max_hours": _round(max(backlog_ages)) if backlog_ages else None,
        "backlog_age_median_hours": _round(_median(backlog_ages)),
        "undefined_metrics": undefined,
        "inputs_fingerprint": inputs_fingerprint,
    }


# ─────────────────────────── 落库（eval_cohorts） ───────────────────────────

#: 参与漂移比对/入库的指标字段（口径改动必须同时改这里 + SCHEMA 版本）
METRIC_FIELDS: tuple[str, ...] = (
    "missions_total", "missions_decided", "missions_pending", "missions_unrecorded",
    "pending_rows", "coverage", "p90_latency_hours", "backlog_rows",
    "backlog_age_max_hours",
)

DDL_POSTGRES = f"""
CREATE TABLE IF NOT EXISTS {COHORT_TABLE} (
    cohort_key            TEXT PRIMARY KEY,
    week_start            TEXT NOT NULL,
    week_end              TEXT NOT NULL,
    repo                  TEXT,
    missions_total        INTEGER NOT NULL DEFAULT 0,
    missions_decided      INTEGER NOT NULL DEFAULT 0,
    missions_pending      INTEGER NOT NULL DEFAULT 0,
    missions_unrecorded   INTEGER NOT NULL DEFAULT 0,
    coverage              DOUBLE PRECISION,
    p90_latency_hours     DOUBLE PRECISION,
    backlog_rows          INTEGER NOT NULL DEFAULT 0,
    backlog_age_max_hours DOUBLE PRECISION,
    frozen                BOOLEAN NOT NULL DEFAULT FALSE,
    frozen_at             TEXT,
    revision              INTEGER NOT NULL DEFAULT 1,
    inputs_fingerprint    TEXT NOT NULL,
    computed_at           TEXT NOT NULL,
    payload               TEXT NOT NULL DEFAULT '{{}}'
);
CREATE INDEX IF NOT EXISTS idx_eval_cohorts_repo ON {COHORT_TABLE} (repo, cohort_key);
"""

DDL_SQLITE = DDL_POSTGRES.replace("BOOLEAN", "INTEGER")


def ensure_table(conn, dialect: str | None = None) -> str:
    """幂等建表（PG/SQLite 同形；payload 一律 TEXT，跨引擎读回一致）。"""
    from sqlalchemy import text

    name = (dialect or getattr(getattr(conn, "dialect", None), "name", "") or "postgresql")
    ddl = DDL_SQLITE if str(name).startswith("sqlite") else DDL_POSTGRES
    for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
        conn.execute(text(stmt))
    return str(name)


def _json_load(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def load_stored(conn) -> dict[str, dict[str, Any]]:
    rows = _rows(conn, f"SELECT * FROM {COHORT_TABLE}", {})
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = _json_load(row.get("payload"), {}) or {}
        merged = {**payload, **{k: v for k, v in row.items() if k != "payload"}}
        merged["frozen"] = bool(row.get("frozen"))
        out[str(row["cohort_key"])] = merged
    return out


def diff_metrics(a: Mapping[str, Any], b: Mapping[str, Any],
                 fields: Sequence[str] = METRIC_FIELDS) -> dict[str, Any] | None:
    """逐字段严格比对（浮点按值相等，不做容差：视图无关性要求 0 差异）。"""
    diffs: dict[str, Any] = {}
    for f in fields:
        va, vb = a.get(f), b.get(f)
        if va != vb:
            diffs[f] = [va, vb]
    return diffs or None


def upsert_cohorts(conn, rows: Iterable[Mapping[str, Any]], *, as_of: Any,
                   freeze: bool = True, commit: bool = False) -> dict[str, Any]:
    """写 ``eval_cohorts``，冻结语义务必按模块头执行（不吞掉漂移）。

    返回 ``{"written": [...], "kept": [...], "drift": [...], "late_arrivals": [...]}``；
    ``drift`` 非空即评分器缺陷（同一输入算出不同数），必须当 FAIL 处理。
    """
    from sqlalchemy import text

    stored = load_stored(conn)
    stamp = now_utc()
    result: dict[str, Any] = {"written": [], "kept": [], "drift": [], "late_arrivals": []}
    for row in rows:
        key = str(row["cohort_key"])
        prev = stored.get(key)
        frozen = bool(freeze and is_frozen(row, as_of=as_of))
        if prev is None:
            _insert(conn, row, frozen=frozen, revision=1, stamp=stamp)
            result["written"].append(key)
            continue

        if prev.get("frozen"):
            if prev.get("inputs_fingerprint") == row.get("inputs_fingerprint"):
                diffs = diff_metrics(prev, row)
                if diffs:
                    result["drift"].append({"cohort_key": key, "fields": diffs})
                    result["kept"].append(key)
                    continue
                result["kept"].append(key)
                continue
            # 迟到评价：输入变了，允许 revision+1 覆盖，旧值留档
            revision = int(prev.get("revision") or 1) + 1
            _update(conn, row, prev=prev, frozen=True, revision=revision, stamp=stamp)
            result["late_arrivals"].append({
                "cohort_key": key,
                "revision": revision,
                "changed": diff_metrics(prev, row) or {},
            })
            continue

        revision = int(prev.get("revision") or 1) + 1
        _update(conn, row, prev=prev, frozen=frozen, revision=revision, stamp=stamp)
        result["written"].append(key)

    if commit:
        conn.commit()
    return result


def _bool_for(conn, value: bool) -> Any:
    """SQLite 无 BOOLEAN 类型：按 0/1 落库；PG 直接布尔。"""
    dialect = str(getattr(getattr(conn, "dialect", None), "name", "postgresql"))
    if dialect.startswith("sqlite"):
        return 1 if value else 0
    return bool(value)


def _insert(conn, row: Mapping[str, Any], *, frozen: bool, revision: int, stamp: str) -> None:
    from sqlalchemy import text

    params = _row_params(conn, row, frozen=frozen, revision=revision, stamp=stamp, prev=None)
    conn.execute(text(
        f"INSERT INTO {COHORT_TABLE} (cohort_key, week_start, week_end, repo, missions_total, "
        "missions_decided, missions_pending, missions_unrecorded, coverage, p90_latency_hours, "
        "backlog_rows, backlog_age_max_hours, frozen, frozen_at, revision, inputs_fingerprint, "
        "computed_at, payload) VALUES (:cohort_key, :week_start, :week_end, :repo, "
        ":missions_total, :missions_decided, :missions_pending, :missions_unrecorded, "
        ":coverage, :p90_latency_hours, :backlog_rows, :backlog_age_max_hours, :frozen, "
        ":frozen_at, :revision, :inputs_fingerprint, :computed_at, :payload)"
    ), params)


def _update(conn, row: Mapping[str, Any], *, prev: Mapping[str, Any], frozen: bool,
            revision: int, stamp: str) -> None:
    from sqlalchemy import text

    params = _row_params(conn, row, frozen=frozen, revision=revision, stamp=stamp, prev=prev)
    conn.execute(text(
        f"UPDATE {COHORT_TABLE} SET week_start = :week_start, week_end = :week_end, "
        "repo = :repo, missions_total = :missions_total, missions_decided = :missions_decided, "
        "missions_pending = :missions_pending, missions_unrecorded = :missions_unrecorded, "
        "coverage = :coverage, p90_latency_hours = :p90_latency_hours, "
        "backlog_rows = :backlog_rows, backlog_age_max_hours = :backlog_age_max_hours, "
        "frozen = :frozen, frozen_at = :frozen_at, revision = :revision, "
        "inputs_fingerprint = :inputs_fingerprint, computed_at = :computed_at, payload = :payload "
        "WHERE cohort_key = :cohort_key"
    ), params)


def _row_params(conn, row: Mapping[str, Any], *, frozen: bool, revision: int, stamp: str,
                prev: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(row)
    if prev is not None:
        payload["previous"] = {
            "revision": prev.get("revision"),
            "metrics": {f: prev.get(f) for f in METRIC_FIELDS},
            "inputs_fingerprint": prev.get("inputs_fingerprint"),
        }
    frozen_at = (prev or {}).get("frozen_at") if prev else None
    if frozen and not frozen_at:
        frozen_at = stamp
    return {
        "cohort_key": row["cohort_key"],
        "week_start": row["week_start"],
        "week_end": row["week_end"],
        "repo": row.get("repo"),
        "missions_total": int(row.get("missions_total") or 0),
        "missions_decided": int(row.get("missions_decided") or 0),
        "missions_pending": int(row.get("missions_pending") or 0),
        "missions_unrecorded": int(row.get("missions_unrecorded") or 0),
        "coverage": row.get("coverage"),
        "p90_latency_hours": row.get("p90_latency_hours"),
        "backlog_rows": int(row.get("backlog_rows") or 0),
        "backlog_age_max_hours": row.get("backlog_age_max_hours"),
        "frozen": _bool_for(conn, frozen),
        "frozen_at": frozen_at,
        "revision": int(revision),
        "inputs_fingerprint": str(row.get("inputs_fingerprint")),
        "computed_at": stamp,
        "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }


# ─────────────────────────── 独立复算（交叉验证） ───────────────────────────

SQL_COHORT_AGGREGATE = """
SELECT
    to_char(date_trunc('week', m.finished_at AT TIME ZONE 'UTC'), 'IYYY-"W"IW') AS cohort_key,
    count(*) AS missions_total,
    count(*) FILTER (WHERE d.mission_id IS NOT NULL) AS missions_decided,
    count(*) FILTER (WHERE p.mission_id IS NOT NULL) AS missions_pending
FROM tensor_dev_missions m
LEFT JOIN (
    SELECT mission_id, min(decided_at) AS first_decided_at
    FROM outcome_evaluations
    WHERE status IN ('confirmed','candidate') AND mission_id IS NOT NULL
    GROUP BY mission_id
) d ON d.mission_id = m.id
LEFT JOIN (
    SELECT DISTINCT mission_id FROM outcome_evaluations
    WHERE status = 'pending' AND mission_id IS NOT NULL
) p ON p.mission_id = m.id
WHERE m.status = 'succeeded' AND m.finished_at IS NOT NULL
GROUP BY 1
ORDER BY 1
"""

SQL_COHORT_P90 = """
SELECT
    to_char(date_trunc('week', m.finished_at AT TIME ZONE 'UTC'), 'IYYY-"W"IW') AS cohort_key,
    percentile_cont(0.9) WITHIN GROUP (
        ORDER BY extract(epoch FROM (CAST(d.first_decided_at AS TIMESTAMPTZ) - m.finished_at)) / 3600.0
    ) AS p90_latency_hours
FROM tensor_dev_missions m
JOIN (
    SELECT mission_id, min(decided_at) AS first_decided_at
    FROM outcome_evaluations
    WHERE status IN ('confirmed','candidate') AND mission_id IS NOT NULL
    GROUP BY mission_id
) d ON d.mission_id = m.id
WHERE m.status = 'succeeded' AND m.finished_at IS NOT NULL
  AND CAST(d.first_decided_at AS TIMESTAMPTZ) >= m.finished_at
GROUP BY 1
ORDER BY 1
"""


def sql_crosscheck(conn, cohorts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """用**独立 SQL 实现**复算 cohort 的 3+1 个量，与 Python 实现逐格比对。

    PG 专有（``date_trunc`` / ``percentile_cont``）；SQLite 直接报 not_supported，
    不静默跳过（交叉验证不许「看着像通过」）。
    """
    dialect = str(getattr(getattr(conn, "dialect", None), "name", "postgresql"))
    if dialect.startswith("sqlite"):
        return {"supported": False, "reason": "sqlite 无 date_trunc/percentile_cont，交叉验证需 PG"}
    agg = {r["cohort_key"]: dict(r) for r in _rows(conn, SQL_COHORT_AGGREGATE, {})}
    p90 = {r["cohort_key"]: dict(r) for r in _rows(conn, SQL_COHORT_P90, {})}
    fields_pairs = [
        ("missions_total", "missions_total"),
        ("missions_decided", "missions_decided"),
        ("missions_pending", "missions_pending"),
    ]
    diffs: list[dict[str, Any]] = []
    for key, row in sorted(cohorts.items()):
        sql_row = agg.get(key, {})
        for py_field, sql_field in fields_pairs:
            py_val = row.get(py_field)
            sql_val = sql_row.get(sql_field)
            if int(py_val or 0) != int(sql_val or 0):
                diffs.append({"cohort_key": key, "field": py_field,
                              "python": py_val, "sql": sql_val})
        py_p90 = row.get("p90_latency_hours")
        sql_p90 = p90.get(key, {}).get("p90_latency_hours")
        if py_p90 is None and sql_p90 is None:
            continue
        if py_p90 is None or sql_p90 is None or abs(float(py_p90) - float(sql_p90)) > 1e-3:
            diffs.append({"cohort_key": key, "field": "p90_latency_hours",
                          "python": py_p90, "sql": None if sql_p90 is None else round(float(sql_p90), 6)})
    return {
        "supported": True,
        "cohorts_compared": len(cohorts),
        "diffs": diffs,
        "identical": not diffs,
        "sql": {"aggregate": SQL_COHORT_AGGREGATE.strip(), "p90": SQL_COHORT_P90.strip()},
    }