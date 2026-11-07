#!/usr/bin/env python3
"""coordinator_shadow v1 —— 脉搏采集层（IN27-H1）。

把「影子协调官」从 v0 原型（``/root/tp-agent/coordinator_shadow.py``：shell 拼 SQL、
``max(id)+1`` 造自增、无只读门、无立单、无自观测）提升为常驻 v1 的**第一层**：
**只读**地收三路脉搏，并把每个数字连同它的 SQL 原文与参数一起留痕。

三路脉搏
--------
1. **chronicle s1–s8 脉搏**：``chronicle_snapshots`` 最新一拍的 payload，取八个器官面
   （``s1_missions`` / ``s2_campaign`` / ``s3_capability`` / ``s4_fleet_pulse`` /
   ``s5_outcomes`` / ``s6_daemons`` / ``s7_voice`` / ``s8_harness``），并算**鲜度**
   （``age_minutes``）。陈旧脉搏是误报的主要来源，因此鲜度本身就是一个器官。
2. **daemon WARN 流**：按行尾有界读取调度/守护日志，解析 ``WARN`` / ``ERROR``，按
   **归一化签名**（去掉数字、hex、uuid、时间戳）聚簇计数——单条噪声不会成簇，
   簇形成才是「器官病了」的证据。
3. **零流器官计数**（本战役点名的三个）：
   - ``credit_backlog``：信用积压——已定谳（confirmed/rejected）但信用账本无对应
     ``eval:<id>|`` 流水的评价条数；
   - ``adoption_stall``：收养停滞——``status='queued'`` 且入池超过阈值仍未被人领的
     条数 + 最老未领年龄（分钟）；
   - ``declaration_rate``：声明率——窗口内完工任务中已声明（``report_id is not null``）
     的占比；分母为 0 时**无定义**（``None`` + 原因），不按 0% 处理。

边界（写清楚，不粉饰）
----------------------
- 所有 SQL 走 ``ReadOnlyRunner``：只放行单条 ``SELECT`` / ``WITH``，剥掉注释与字符串
  字面量后做**词边界**关键词扫描；``EXPLAIN ANALYZE`` 这类会执行的语句也不放行
  （只认 ``select``/``with`` 打头）。审计流水记录每条语句，供事后复核。
- 探针失败（表缺、权限不足、超时）→ 该器官 ``value=None`` 且 ``withheld`` 记原因，
  **绝不填 0**；0 与「看不见」在本模块里是两种东西。
- 本层不做判断、不写库、不立单：判断在 ``diagnose.py``，写库在 ``ledger.py``，
  立单在 ``filing.py``——单层单责，便于逐层给负控。
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

SCHEMA = "coordinator-shadow-pulse/1"

#: chronicle 八个器官面（s1–s8），缺面即标 ``absent``，不补默认值
CHRONICLE_FACETS: tuple[str, ...] = (
    "s1_missions",
    "s2_campaign",
    "s3_capability",
    "s4_fleet_pulse",
    "s5_outcomes",
    "s6_daemons",
    "s7_voice",
    "s8_harness",
)

#: 默认鲜度门：chronicle 超过该年龄即视为「脉搏陈旧」（诊断会被跳过，见 daemon.py）
DEFAULT_MAX_PULSE_AGE_MIN = 90.0


# ─────────────────────────────── 只读门 ───────────────────────────────


class ReadOnlyViolation(RuntimeError):
    """试图在只读通道上执行非只读语句。"""


_COMMENT_OR_LITERAL = re.compile(
    r"(--[^\n]*)"            # line comment
    r"|(/\*.*?\*/)"          # block comment
    r"|('(?:[^']|'')*')",    # single-quoted literal (SQL standard, '' escape)
    re.S,
)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|vacuum|"
    r"call|merge|comment|reindex|refresh|lock|begin|commit|rollback|savepoint|set|"
    r"reset|analyze|attach|pragma|into)\b",
    re.I,
)


def strip_sql_noise(sql: str) -> str:
    """剥掉注释与字符串字面量——避免「字符串里含 update」被误判，也避免藏语句。"""
    return _COMMENT_OR_LITERAL.sub(lambda m: " " if m.group(1) or m.group(2) else "''", sql)


def assert_read_only(sql: str) -> str:
    """单条 ``SELECT``/``WITH`` 放行，其余一律拒。返回规范化后的语句供留痕。"""
    body = strip_sql_noise(sql).strip()
    while body.endswith(";"):
        body = body[:-1].rstrip()
    if not body:
        raise ReadOnlyViolation("空语句")
    if ";" in body:
        raise ReadOnlyViolation("只读通道禁多语句（含 ';'）")
    head = re.match(r"\s*([a-zA-Z]+)", body)
    if not head or head.group(1).lower() not in ("select", "with"):
        raise ReadOnlyViolation(f"只读通道只放行 SELECT/WITH，实际：{body[:40]!r}")
    hit = _FORBIDDEN.search(body)
    if hit:
        raise ReadOnlyViolation(f"只读语句含写关键字 {hit.group(1)!r}")
    return " ".join(body.split())


class Runner(Protocol):  # pragma: no cover - 协议定义
    dialect: str

    def fetchall(self, sql: str, params: Mapping[str, Any] | None = None) -> Sequence[Sequence[Any]]:
        ...


class ReadOnlyRunner:
    """套在所有脉搏 SQL 外面的只读门 + 审计流水。"""

    def __init__(self, inner: Runner, audit: list[str] | None = None, max_statements: int = 200) -> None:
        self._inner = inner
        self.audit: list[str] = audit if audit is not None else []
        self.max_statements = max_statements

    @property
    def dialect(self) -> str:
        return getattr(self._inner, "dialect", "postgresql")

    def fetchall(self, sql: str, params: Mapping[str, Any] | None = None) -> Sequence[Sequence[Any]]:
        normalized = assert_read_only(sql)
        if len(self.audit) >= self.max_statements:  # 病态循环保护：不无限长审计
            raise ReadOnlyViolation("审计流水超长：疑似探针循环")
        self.audit.append(normalized)
        return self._inner.fetchall(sql, params)

    # 写操作在只读通道里不存在，显式拒（让误用立即炸而不是静默生效）
    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> bool:  # pragma: no cover
        raise ReadOnlyViolation("只读通道无 execute()")


class SqliteRunner:
    """测试与离线演练用的 sqlite 通道（engine-agnostic 探针的最小实现）。"""

    dialect = "sqlite"

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = None

    def fetchall(self, sql: str, params: Mapping[str, Any] | None = None):
        cur = self.conn.execute(sql, dict(params or {}))
        return cur.fetchall()

    def close(self) -> None:  # pragma: no cover
        self.conn.close()


_NAMED_PARAM = re.compile(r"(?<!:):([a-zA-Z_]\w*)")


def to_psycopg_placeholders(sql: str) -> str:
    """``:name`` → ``%(name)s``（psycopg 的命名占位符）。

    保留 ``::text``/``::timestamptz`` 这类 Postgres 类型转换（负向前查 ``(?<!:)``）。
    探针 SQL 用 ``:name`` 写是为了同一套语句在 sqlite（原生 ``:name``）与 Postgres
    上都能跑；换引擎只在这一处翻译，不动探针原文（探针原文要留痕迹给复算）。
    """
    return _NAMED_PARAM.sub(lambda m: f"%({m.group(1)})s", sql)


class PgRunner:
    """生产只读通道（psycopg v3 优先，v2 兜底；连接串来自 ``TP_DSN``）。"""

    dialect = "postgresql"

    def __init__(self, dsn: str | None = None) -> None:
        import os

        self._dsn = dsn or os.environ.get(
            "TP_DSN", "dbname=tensorplatform user=foundation host=/var/run/postgresql"
        )
        self._conn = None

    @property
    def conn(self):  # pragma: no cover - 薄包装
        if self._conn is None:
            try:
                import psycopg  # type: ignore

                self._conn = psycopg.connect(self._dsn, autocommit=True)
            except ImportError:  # pragma: no cover
                import psycopg2  # type: ignore

                self._conn = psycopg2.connect(self._dsn)
                self._conn.autocommit = True
        return self._conn

    def fetchall(self, sql: str, params: Mapping[str, Any] | None = None):  # pragma: no cover
        cur = self.conn.cursor()
        cur.execute(to_psycopg_placeholders(sql) if params else sql,
                    dict(params or {}) if params else None)
        rows = cur.fetchall()
        cur.close()
        return rows


# ─────────────────────────────── 工具 ───────────────────────────────


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        raw = raw.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            try:
                dt = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


_DIGITS = re.compile(r"\d+")


def normalize_signature(message: str, max_len: int = 96) -> str:
    """日志签名归一化：数字/uuid/hex → 占位符，折叠空白；同一病灶跨拍同签名。"""
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<uuid>", message, flags=re.I)
    text = re.sub(r"\b0x[0-9a-f]+\b", "<hex>", text, flags=re.I)
    text = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", "<ts>", text)
    text = _DIGITS.sub("#", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


# ─────────────────────────────── 数据形状 ───────────────────────────────


@dataclass
class Metric:
    """一个可复算的数字：值 + SQL 原文 + 参数 + （可选）无定义原因。"""

    name: str
    value: float | int | None
    sql: str
    params: Mapping[str, Any] = field(default_factory=dict)
    unit: str = "count"
    withheld: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "sql": " ".join(self.sql.split()),
            "params": dict(self.params),
        }
        if self.withheld:
            out["withheld"] = self.withheld
        if self.extra:
            out.update(dict(self.extra))
        return out

    @property
    def defined(self) -> bool:
        return self.value is not None


def _cluster_dict(cluster: Any) -> dict[str, Any]:
    """簇既可能是 ``WarnCluster``（生产解析），也可能是已序列化的 dict（合成/回放）。"""
    if isinstance(cluster, WarnCluster):
        return cluster.to_dict()
    return dict(cluster) if isinstance(cluster, Mapping) else {"signature": str(cluster)}


@dataclass
class WarnCluster:
    signature: str
    count: int
    level: str
    sample: str

    def to_dict(self) -> dict[str, Any]:
        return {"signature": self.signature, "count": self.count, "level": self.level, "sample": self.sample}


@dataclass
class Pulse:
    """一拍脉搏（v1 schema）。``to_dict()`` 即落库/喂 LLM 的形状。"""

    as_of: str
    organs: dict[str, Metric] = field(default_factory=dict)
    chronicle: dict[str, Any] = field(default_factory=dict)
    warn: dict[str, Any] = field(default_factory=dict)
    blocked: dict[str, Any] = field(default_factory=dict)
    audit: list[str] = field(default_factory=list)
    withheld: list[str] = field(default_factory=list)
    source: str = "live"

    # --- 数字集合：给「LLM 引用的数字是否在脉搏里」的地基校验用 -------------
    def numbers(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for name, metric in self.organs.items():
            if metric.defined:
                out[f"organs.{name}"] = float(metric.value)  # type: ignore[arg-type]
        for key in ("queued_open", "queued_stuck_2h", "running", "oldest_queued_minutes"):
            m = self.organs.get(key)
            if m is not None and m.defined:
                out[f"organs.{key}"] = float(m.value)  # type: ignore[arg-type]
        for facet, payload in self.chronicle.get("facets", {}).items():
            if isinstance(payload, Mapping):
                for k, v in payload.items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        out[f"chronicle.{facet}.{k}"] = float(v)
        if self.warn.get("warn_total") is not None:
            out["warn.warn_total"] = float(self.warn["warn_total"])
        if self.warn.get("error_total") is not None:
            out["warn.error_total"] = float(self.warn["error_total"])
        return out

    def value_set(self) -> set[float]:
        return set(self.numbers().values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "as_of": self.as_of,
            "source": self.source,
            "chronicle": self.chronicle,
            "organs": {k: v.to_dict() for k, v in self.organs.items()},
            "warn": self.warn,
            "blocked": self.blocked,
            "withheld": self.withheld,
            "audit_len": len(self.audit),
            "audit_head": self.audit[:8],
            "numbers": self.numbers(),
        }

    def digest(self) -> dict[str, Any]:
        """小体积摘要（落库 + 喂 LLM 的主干；数字一个不省，SQL 留在 organs 里）。"""
        def pick(name: str):
            m = self.organs.get(name)
            return None if m is None else m.value

        return {
            "as_of": self.as_of,
            "chronicle_ts": self.chronicle.get("ts"),
            "chronicle_age_minutes": self.chronicle.get("age_minutes"),
            "chronicle_stale": self.chronicle.get("stale"),
            "facets_present": self.chronicle.get("facets_present"),
            "credit_backlog": pick("credit_backlog"),
            "adoption_stall_minutes": pick("oldest_queued_minutes"),
            "adoption_stall_stuck": pick("queued_stuck_2h"),
            "declaration_rate": pick("declaration_rate"),
            "queued_open": pick("queued_open"),
            "running": pick("running"),
            "warn_total": self.warn.get("warn_total"),
            "error_total": self.warn.get("error_total"),
            "warn_clusters": [_cluster_dict(c) for c in (self.warn.get("clusters") or [])][:6],
            "blocked_nodes": self.blocked.get("counts", {}).get("blocked"),
            "withheld": self.withheld,
        }


# ─────────────────────────────── 探针 ───────────────────────────────


class ProbeFailed(RuntimeError):
    pass


def _ts_cast(dialect: str, param: str) -> str:
    return param if dialect == "sqlite" else f"CAST({param} AS timestamptz)"


def _json_text(dialect: str, column: str) -> str:
    return column if dialect == "sqlite" else f"{column}::text"


def _scalar(runner: Runner, sql: str, params: Mapping[str, Any] | None = None) -> Any:
    rows = runner.fetchall(sql, params or {})
    if not rows:
        return None
    row = rows[0]
    return row[0] if row else None


def _count_by(runner: Runner, sql: str, params: Mapping[str, Any] | None = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in runner.fetchall(sql, params or {}):
        if row and row[0] is not None:
            out[str(row[0])] = int(row[1] or 0)
    return out


@dataclass
class ProbeBook:
    """一组探针的值 + SQL 原文 + 参数（供证据链与复算）。"""

    metrics: dict[str, Metric]

    def metric(self, name: str, unit: str = "count", extra: Mapping[str, Any] | None = None,
               value: Any = None, withheld: str | None = None) -> Metric:
        sql = self.sql.get(name, "")
        params = dict(self.params.get(name, {}))
        m = Metric(name=name, value=value, sql=sql, params=params, unit=unit,
                   withheld=withheld, extra=dict(extra or {}))
        self.metrics[name] = m
        return m

    sql: dict[str, str] = field(default_factory=dict)
    params: dict[str, Mapping[str, Any]] = field(default_factory=dict)


def _record(book: ProbeBook, name: str, sql: str, params: Mapping[str, Any]) -> None:
    book.sql[name] = sql
    book.params[name] = dict(params)


def collect_organs(
    runner: ReadOnlyRunner,
    as_of: datetime,
    *,
    stall_minutes: float = 120.0,
    window_hours: float = 24.0,
    max_pulse_age_min: float = DEFAULT_MAX_PULSE_AGE_MIN,
) -> tuple[dict[str, Metric], dict[str, Any], dict[str, Any], list[str]]:
    """收三路脉搏里的「计数面」。返回 (organs, chronicle, blocked, withheld)。"""
    d = runner.dialect
    book = ProbeBook(metrics={})
    withheld: list[str] = []
    failed: set[str] = set()

    def safe(name: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception as exc:  # 探针失败 → 留白，不填 0
            failed.add(name)
            withheld.append(f"{name}:{type(exc).__name__}:{str(exc)[:120]}")
            return None

    def why(name: str) -> str | None:
        return "probe-failed" if name in failed else None

    # 1) chronicle 最新一拍 -------------------------------------------------
    chron_sql = (
        f"SELECT {_json_text(d, 'ts')} AS ts, {_json_text(d, 'payload')} AS payload "
        "FROM chronicle_snapshots ORDER BY ts DESC LIMIT 1"
    )
    _record(book, "chronicle", chron_sql, {})
    chronicle: dict[str, Any] = {"ts": None, "age_minutes": None, "stale": None,
                                 "facets": {}, "facets_present": [], "schema": None}
    row = safe("chronicle", lambda: runner.fetchall(chron_sql, {}))
    if row:
        ts_text, payload_text = (row[0][0], row[0][1])
        chronicle["ts"] = str(ts_text) if ts_text is not None else None
        ts = parse_ts(ts_text)
        if ts is not None:
            age = (as_of - ts).total_seconds() / 60.0
            chronicle["age_minutes"] = round(age, 2)
            chronicle["stale"] = bool(age > max_pulse_age_min)
        try:
            payload = json.loads(payload_text) if isinstance(payload_text, (str, bytes)) else (payload_text or {})
        except Exception:
            payload = {}
            withheld.append("chronicle:payload-unparsable")
        chronicle["schema"] = payload.get("schema")
        for facet in CHRONICLE_FACETS:
            if facet in payload:
                chronicle["facets"][facet] = payload[facet]
                chronicle["facets_present"].append(facet)
    else:
        withheld.append("chronicle:no-snapshot")

    # 2) 零流器官之一：收养停滞（queued 未领） -------------------------------
    cut_stall = iso(as_of - timedelta(minutes=stall_minutes))
    sql_stuck = (
        "SELECT count(*) FROM tensor_dev_missions WHERE status = 'queued' AND "
        f"created_at < {_ts_cast(d, ':cut')}"
    )
    _record(book, "queued_stuck_2h", sql_stuck, {"cut": cut_stall})
    stuck = safe("queued_stuck_2h", lambda: _scalar(runner, sql_stuck, {"cut": cut_stall}))
    book.metric("queued_stuck_2h", extra={"threshold_minutes": stall_minutes},
                value=None if stuck is None else int(stuck), withheld=why("queued_stuck_2h"))

    sql_oldest = "SELECT min(created_at) FROM tensor_dev_missions WHERE status = 'queued'"
    _record(book, "oldest_queued_minutes", sql_oldest, {})
    oldest_raw = safe("oldest_queued_minutes", lambda: _scalar(runner, sql_oldest, {}))
    oldest_dt = parse_ts(oldest_raw)
    oldest_min = None if oldest_dt is None else round((as_of - oldest_dt).total_seconds() / 60.0, 1)
    book.metric("oldest_queued_minutes", unit="minutes", value=oldest_min,
                extra={"oldest_created_at": str(oldest_raw) if oldest_raw is not None else None},
                withheld=why("oldest_queued_minutes") or (
                    None if (oldest_min is not None or oldest_raw is None) else "unparsable-ts"))

    sql_queued = "SELECT count(*) FROM tensor_dev_missions WHERE status = 'queued'"
    _record(book, "queued_open", sql_queued, {})
    queued_open = safe("queued_open", lambda: _scalar(runner, sql_queued, {}))
    book.metric("queued_open", value=None if queued_open is None else int(queued_open),
                withheld=why("queued_open"))

    sql_running = "SELECT count(*) FROM tensor_dev_missions WHERE status IN ('claimed','running')"
    _record(book, "running", sql_running, {})
    running = safe("running", lambda: _scalar(runner, sql_running, {}))
    book.metric("running", value=None if running is None else int(running), withheld=why("running"))

    # 3) 零流器官之二：信用积压 -------------------------------------------
    sql_credit = (
        "SELECT count(*) FROM outcome_evaluations e WHERE e.status IN ('confirmed','rejected') "
        "AND NOT EXISTS (SELECT 1 FROM tensor_credit_ledger l "
        "WHERE l.reason LIKE 'eval:' || e.evaluation_id || '|%')"
    )
    _record(book, "credit_backlog", sql_credit, {})
    credit = safe("credit_backlog", lambda: _scalar(runner, sql_credit, {}))
    book.metric("credit_backlog", extra={"zero_flow": True},
                value=None if credit is None else int(credit), withheld=why("credit_backlog"))

    # 4) 零流器官之三：声明率（完工 → 声明/报告） ---------------------------
    cut_win = iso(as_of - timedelta(hours=window_hours))
    sql_fin = (
        "SELECT count(*) FROM tensor_dev_missions WHERE finished_at IS NOT NULL "
        f"AND finished_at >= {_ts_cast(d, ':cut')}"
    )
    _record(book, "finished_window", sql_fin, {"cut": cut_win, "window_hours": window_hours})
    finished = safe("finished_window", lambda: _scalar(runner, sql_fin, {"cut": cut_win}))
    # 声明率的**权威口径**：平台把报告写在 ``tensor_reports``（按 mission_id 关联），
    # ``tensor_dev_missions.report_id`` 实测长期不维护（生产库 0/4907）。两个口径取并集，
    # 否则声明率会永远 0% → 变成一个必然误报的假警。
    sql_decl = (
        "SELECT count(*) FROM tensor_dev_missions m WHERE m.finished_at IS NOT NULL "
        f"AND m.finished_at >= {_ts_cast(d, ':cut')} AND (m.report_id IS NOT NULL "
        "OR EXISTS (SELECT 1 FROM tensor_reports r WHERE r.mission_id = m.id))"
    )
    _record(book, "declared_window", sql_decl, {"cut": cut_win, "window_hours": window_hours})
    declared = safe("declared_window", lambda: _scalar(runner, sql_decl, {"cut": cut_win}))

    finished_i = None if finished is None else int(finished)
    declared_i = None if declared is None else int(declared)
    if finished_i is None or declared_i is None:
        rate, reason = None, "probe-failed"
    elif finished_i == 0:
        rate, reason = None, "no-finished-missions-in-window"
    else:
        rate, reason = round(declared_i / finished_i, 4), None
    # 声明率 = 分子(已声明) / 分母(完工)；两个探针的 SQL 都留在 extra 里，保证可复算
    _record(book, "declaration_rate", sql_decl, {"cut": cut_win, "window_hours": window_hours})
    book.metric("declaration_rate", unit="ratio", value=rate,
                extra={"zero_flow": True, "finished_24h": finished_i, "declared_24h": declared_i,
                       "undefined_reason": reason, "window_hours": window_hours,
                       "sql_finished": " ".join(sql_fin.split()),
                       "sql_declared": " ".join(sql_decl.split())},
                withheld=reason if reason == "probe-failed" else None)

    # 5) 其余器官（缺表/缺列 → 留白，不崩） -------------------------------
    sql_cap = (
        "SELECT count(*) FROM tensor_capability_deltas "
        f"WHERE created_at >= {_ts_cast(d, ':cut')}"
    )
    _record(book, "capability_deltas_24h", sql_cap, {"cut": cut_win})
    cap = safe("capability_deltas_24h", lambda: _scalar(runner, sql_cap, {"cut": cut_win}))
    book.metric("capability_deltas_24h", extra={"zero_flow": True},
                value=None if cap is None else int(cap), withheld=why("capability_deltas_24h"))

    sql_await = "SELECT count(*) FROM tensor_dev_missions WHERE status = 'awaiting_pr_merge'"
    _record(book, "awaiting_pr_merge", sql_await, {})
    await_pr = safe("awaiting_pr_merge", lambda: _scalar(runner, sql_await, {}))
    book.metric("awaiting_pr_merge", value=None if await_pr is None else int(await_pr),
                withheld=why("awaiting_pr_merge"))

    sql_status = "SELECT status, count(*) FROM tensor_dev_missions GROUP BY status"
    _record(book, "mission_status_counts", sql_status, {})
    status_counts = safe("mission_status_counts", lambda: _count_by(runner, sql_status, {})) or {}
    book.metric("mission_status_counts", unit="map", value=sum(status_counts.values()) or 0,
                extra={"counts": status_counts})

    sql_nodes = "SELECT status, count(*) FROM campaign_nodes GROUP BY status"
    _record(book, "campaign_nodes", sql_nodes, {})
    node_counts = safe("campaign_nodes", lambda: _count_by(runner, sql_nodes, {})) or {}
    book.metric("campaign_nodes", unit="map", value=sum(node_counts.values()) or 0,
                extra={"counts": node_counts})
    # 阻塞口径（诊断单 7a90c537e3805175）-------------------------------------------
    # ``campaign_nodes.status='blocked'`` 是**两种语义的合流**，不能整列当「阻塞节点数」：
    #   (a) 任务级真阻塞：派发器把失败耗尽宽限的 task 节点打回 blocked
    #       （``block_reason`` = "mission failed x2" / "infra x5 (agent_lost)" /
    #       "hollow success x2"），需要裁决或复派 —— 这才是要报警的东西；
    #   (b) 门控停放：``node_type='gate'`` 由放门机制持有
    #       （ops/taskplane/gate_auto_release.py，FROM_STATUS='blocked'）：上游没全 done
    #       就等、判据工件缺失就等、治理门等人工双钥匙 —— 判据不满足**只等不放**是
    #       设计纪律，不是故障。生产库实测 11 blocked = 8 gate 停放 + 3 task 真阻塞。
    # 把 (b) 计入指标 ⇒ 只要有 5 条以上战役同时在飞，停放门就常年压线 →
    # 影子每拍读到同一签名（「连续 3 拍同签不消」正是这么来的）。
    # 故只数 (a)；(b) 进 extra.gate_held 留档，放门判据的权威实现只在 gate_auto_release
    # 一处，脉搏层不复制门语义（避免两套判据漂移）。
    sql_fault = (
        "SELECT count(*) FROM campaign_nodes WHERE status = 'blocked' "
        "AND coalesce(node_type, 'task') <> 'gate'"
    )
    _record(book, "campaign_blocked", sql_fault, {})
    _record(book, "campaign_pending", sql_nodes, {})
    fault_blocked = safe("campaign_blocked", lambda: _scalar(runner, sql_fault, {}))
    total_blocked = node_counts.get("blocked") if node_counts else None
    gate_held = (None if total_blocked is None or fault_blocked is None
                 else max(0, int(total_blocked) - int(fault_blocked)))
    book.metric("campaign_blocked",
                value=None if fault_blocked is None else int(fault_blocked),
                extra={"zero_flow": True, "blocked_total": total_blocked,
                       "gate_held": gate_held,
                       "scope": "task-level faults only; gate parking (放门持有) excluded"},
                withheld=why("campaign_blocked") or why("campaign_nodes"))
    book.metric("campaign_pending", value=node_counts.get("pending") if node_counts else None,
                withheld=why("campaign_nodes"))

    blocked = {"counts": node_counts, "blocked": node_counts.get("blocked", 0),
               "fault": None if fault_blocked is None else int(fault_blocked),
               "gate_held": gate_held,
               "pending": node_counts.get("pending", 0),
               "in_flight": node_counts.get("in-flight", 0)}

    return book.metrics, chronicle, blocked, withheld


# ─────────────────────────────── WARN 流 ───────────────────────────────

_TS_LINE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
_LEVEL = re.compile(r"\b(WARN|WARNING|ERROR|CRITICAL|FATAL)\b")


def parse_warn_lines(
    lines: Iterable[str],
    as_of: datetime,
    *,
    window_minutes: float = 60.0,
    max_clusters: int = 8,
) -> dict[str, Any]:
    """把日志行解析成「窗口内告警流」：总数 + 按归一键聚簇。"""
    window_start = as_of - timedelta(minutes=window_minutes)
    warn_total = error_total = undated = 0
    clusters: dict[str, dict[str, Any]] = {}
    for raw in lines:
        line = raw.rstrip("\n")
        if not line:
            continue
        m_level = _LEVEL.search(line)
        if not m_level:
            continue
        ts = None
        m_ts = _TS_LINE.match(line.strip())
        if m_ts:
            try:
                ts = datetime(*(int(m_ts.group(i)) for i in range(1, 7)), tzinfo=timezone.utc)
            except ValueError:
                ts = None
        if ts is not None and ts < window_start:
            continue
        if ts is None:
            undated += 1
        level_raw = m_level.group(1).upper()
        level = "high" if level_raw in ("ERROR", "CRITICAL", "FATAL") else "mid"
        if level == "high":
            error_total += 1
        else:
            warn_total += 1
        message = line[m_level.end():] if m_level.end() < len(line) else line
        message = message.strip(" :|\t-")
        sig = normalize_signature(message or level_raw)
        bucket = clusters.setdefault(sig, {"signature": sig, "count": 0, "level": level,
                                           "sample": line.strip()[:200]})
        bucket["count"] += 1
        if level == "high":
            bucket["level"] = "high"

    ordered = sorted(clusters.values(), key=lambda c: (-c["count"], -len(c["signature"])))[:max_clusters]
    return {
        "window_minutes": window_minutes,
        "warn_total": warn_total,
        "error_total": error_total,
        "undated_lines": undated,
        "clusters": [WarnCluster(**c) for c in ordered],
        "distinct_signatures": len(clusters),
    }


def _tail_lines(path: Path, max_lines: int = 600, max_bytes: int = 512 * 1024) -> list[str]:
    """有界读尾：不把整本日志吃进内存（v0 的 f.readlines() 隐患）。"""
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - max_bytes))
        chunk = fh.read().decode("utf-8", "replace")
    lines = chunk.splitlines()
    return lines[-max_lines:]


def read_warn_stream(
    paths: Sequence[str | Path],
    as_of: datetime,
    *,
    window_minutes: float = 60.0,
    max_clusters: int = 8,
    tail_lines: int = 600,
) -> dict[str, Any]:
    """读若干 daemon 日志的尾部，聚簇 WARN/ERROR；读不到就 withheld（不是 0 告警）。"""
    lines: list[str] = []
    read_ok: list[str] = []
    problems: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        try:
            lines.extend(_tail_lines(path, max_lines=tail_lines))
            read_ok.append(str(path))
        except FileNotFoundError:
            problems.append(f"{path}:not-found")
        except Exception as exc:
            problems.append(f"{path}:{type(exc).__name__}")
    parsed = parse_warn_lines(lines, as_of, window_minutes=window_minutes, max_clusters=max_clusters)
    parsed["paths_read"] = read_ok
    parsed["path_problems"] = problems
    if not read_ok:
        parsed["warn_total"] = None
        parsed["error_total"] = None
        parsed["withheld"] = "no-log-readable"
        parsed["clusters"] = []
    return parsed


# ─────────────────────────────── 总装 ───────────────────────────────


def collect_pulse(
    runner: ReadOnlyRunner,
    *,
    as_of: datetime | None = None,
    log_paths: Sequence[str | Path] = (),
    stall_minutes: float = 120.0,
    window_hours: float = 24.0,
    warn_window_minutes: float = 60.0,
    max_pulse_age_min: float = DEFAULT_MAX_PULSE_AGE_MIN,
    source: str = "live",
) -> Pulse:
    as_of = as_of or utc_now()
    organs, chronicle, blocked, withheld = collect_organs(
        runner, as_of, stall_minutes=stall_minutes, window_hours=window_hours,
        max_pulse_age_min=max_pulse_age_min,
    )
    warn = read_warn_stream(log_paths, as_of, window_minutes=warn_window_minutes)
    if warn.get("withheld"):
        withheld.append(f"warn:{warn['withheld']}")
    return Pulse(
        as_of=iso(as_of),
        organs=organs,
        chronicle=chronicle,
        warn=warn,
        blocked=blocked,
        audit=list(runner.audit),
        withheld=withheld,
        source=source,
    )