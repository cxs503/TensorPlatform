#!/usr/bin/env python3
"""agent_health_meter.py —— 席位健康度仪表（FEAT-AGENT-HARNESS，只计量不裁决）。

一句话
------
按「席位」落 7d 滚动健康快照（``tensor_agent_health``），标出三类病席位，并把
**健康分的数学定义**写成可复算的 ``score_components``；归因不到的列如实置 0 并
写明缺口原因（``memory_attribution``），绝不用估算值填。

为什么需要它
------------
个体记忆层是空壳 + 席位健康度无保障，是 agent 能力偏弱的主因之一（owner
2026-11-12 定谳）。要修先要能看见：谁在重复失败、谁成功率长期趴地板、谁的记忆
从未被用上。本仪表只做计量与标记，**不做处置**（处置归协调线的门诊/换班）。

席位标识口径（以产库真列为准）
------------------------------
- ``tensor_dev_missions.claimed_by`` 是唯一在产数据里有值的席位标识列（7d 窗口内
  ``agent_session_id`` 全为 NULL；14865 单全量核对见 docs/AGENT_HEALTH_METER.md §2），
  所以 ``agent_session_id → factory_resident_workers`` 这条链在产库是断的。
- ``claimed_by`` 与 ``factory_resident_workers.worker_id`` 同名可 join（93 席）。
- 无 ``claimed_by`` 的单进 ``unattributed`` 桶：只计量、**不参与病席位判定**
  （否则 437 条无主失败会把标记打爆成假阳性）。

失败/终结口径
-------------
- ``tensor_dev_missions.fail_stage`` 列**全量 14865 行皆 NULL** —— 真实拦路阶段落在
  ``result->>'fail_stage'``（窗口内 591 行：``budget_exhausted+forced_finish`` /
  ``no_progress+forced_finish`` / ``wall_exhausted+forced_finish``）。本仪表读后者，
  并把实际执行的聚合 SQL 原文落进 ``source_sql``（可复算）。
- 「问题单」（failures_total）= ``status <> 'succeeded'`` **或** 有 ``result->>'fail_stage'``。
  这一步是产库核对逼出来的：窗口内 478 条有 fail_stage 记录的行里 **460 条最终
  status='succeeded'**（强制终结后又验收/重派成功），另有 3426 条最终 failed 的行
  **没有** fail_stage 记录。若按最终状态取交集，会漏掉 96% 的强制终结事件，把
  RECUR_FAIL 打哑——所以按「事件」计量，并在 fail_stage_dist 里把无记录的失败单
  单列 ``(unspecified)`` 桶（每单只进一个桶，不重复计）。
- 强制终结（forced_finish_count）= 有 fail_stage **或** ``result ? 'reaped_by'``
  **或** ``result ? 'stop_condition'`` **或** ``status IN ('revoked','stopped')``。

健康分（数学定义，成文见 docs/AGENT_HEALTH_METER.md §3）
--------------------------------------------------------
四个 [0,1] 分量，缺失分量整体剔除并重归一化（不补默认值）：:

    S = 成功率     = succeeded / total              w=0.50
    C = 可控率     = 1 - forced / total             w=0.20
    A = 失败可归因率 = attributed / failures        w=0.10   （无失败记 1.0）
    M = 记忆活力   = min(1,(injected+hits)/total)   w=0.20   （源不可用→缺省）

    raw   = 100 * Σ(w_i·c_i) / Σ(w_i | c_i ≠ None)
    score = raw × Π penalty_k      penalty: RECUR_FAIL 0.65 / LOW_SUCCESS 0.60 /
                                            MEMORY_STARVED 0.75（乘性，下限 0）

标记（阈值可配，语义先于动手既定）：:

    RECUR_FAIL      同 fail_stage（**已识别**阶段，``(unspecified)`` 不计）× ≥3 / 7d
    LOW_SUCCESS     单数 ≥5 且成功率 < 地板（默认 0.30；产库席位中位数约 0.64）
    MEMORY_STARVED  单数 ≥10 且 memory_hits == 0（仅记忆源可用时可判；不可用→withheld）

幂等与守护
----------
- 日轮询落在 ``epoch % interval == 0`` 的槽上（不累积漂移）；同
  ``(org, seat, snapshot_date, window_days)`` 走 ``ON CONFLICT DO UPDATE``，
  重跑同一天只覆盖不新增。
- ``flock`` 独占锁：第二个实例直接退出（``--lock-timeout`` 内等不到就退）。
- 单拍异常收成 error 拍写日志，循环继续（错拍本身是证据，不自杀）。
- 每个动作写日志 + ``chronicle_snapshots`` 一条 payload（全动作留痕）。

用法：:

    python3 tools/agent/agent_health_meter.py --once                 # 拍一快照
    python3 tools/agent/agent_health_meter.py --once --dry-run       # 只算不写
    python3 tools/agent/agent_health_meter.py --daemon --interval 86400
    python3 tools/agent/agent_health_meter.py --weekly-report
    python3 tools/agent/agent_health_meter.py --once \\
        --source-schema rt_health --target-schema rt_health           # 红队靶场
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "agent-health-meter/1"

# ── 判据常量（文档化、可配置，随报告公开） ─────────────────────────────
WINDOW_DAYS = 7
WEIGHTS: dict[str, float] = {
    "success": 0.50,
    "controllability": 0.20,
    "attribution": 0.10,
    "memory": 0.20,
}
#: 病标记的乘性罚分（保留同一病组内的排序信息，避免硬上限把分数拉平成并列）
PENALTIES: dict[str, float] = {
    "RECUR_FAIL": 0.65,      # 同阶段反复拦路 = harness 没学到东西
    "LOW_SUCCESS": 0.60,     # 成功率长期趴地板
    "MEMORY_STARVED": 0.75,  # 记忆从未被用上
}
DEFAULT_SUCCESS_FLOOR = 0.30          # 成功率地板（产库席位中位数 ~0.64）
MIN_MISSIONS_LOW_SUCCESS = 5
MIN_MISSIONS_MEMORY_STARVED = 10
RECUR_MIN_COUNT = 3
UNSPECIFIED_STAGE = "(unspecified)"
UNATTRIBUTED = "unattributed"

DEFAULT_STATE_DIR = "/var/lib/tp-agent-health"
DEFAULT_LOG = "/var/log/tp-fleet/agent_health_meter.log"
DEFAULT_DSN = os.environ.get(
    "TP_DSN", "dbname=tensorplatform user=foundation host=/var/run/postgresql"
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ─────────────────────── 分数与标记（纯函数，可单测） ───────────────────────


@dataclass(frozen=True)
class SeatMetrics:
    """一个席位在窗口内的原始计量（全部来自 SQL，不含估算）。"""

    seat_key: str
    organization_id: int | None = None
    missions_total: int = 0
    missions_succeeded: int = 0
    failures_total: int = 0
    stage_attributed: int = 0
    forced_finish_count: int = 0
    recur_stage: str | None = None
    recur_stage_count: int = 0
    fail_stage_dist: Mapping[str, int] = field(default_factory=dict)
    lessons_written: int = 0
    lessons_injected: int = 0
    memory_hits: int = 0
    memory_lessons_stock: int = 0
    memory_key_missions: int = 0

    @property
    def seat_kind(self) -> str:
        return UNATTRIBUTED if self.seat_key == UNATTRIBUTED else "worker"

    @property
    def success_rate(self) -> float | None:
        return None if self.missions_total <= 0 else self.missions_succeeded / self.missions_total


def _ratio(num: int, den: int) -> float | None:
    return None if den <= 0 else num / den


def markers_for(
    m: SeatMetrics,
    *,
    success_floor: float = DEFAULT_SUCCESS_FLOOR,
    memory_available: bool = True,
) -> tuple[list[str], dict[str, Any]]:
    """病席位标记。返回 (markers, markers_meta)。

    ``markers_meta`` 记「为什么没打标」——尤其源不可用时的 withheld，
    免得把「看不见」误读成「健康」。
    """
    meta: dict[str, Any] = {}
    if m.seat_kind == UNATTRIBUTED:
        meta["suppressed"] = "unattributed_bucket"
        meta["note"] = "无席位标识的欠账桶：只计量，不参与病席位判定（避免假阳性）"
        return [], meta

    markers: list[str] = []
    if m.recur_stage_count >= RECUR_MIN_COUNT and m.recur_stage:
        markers.append("RECUR_FAIL")
        meta["RECUR_FAIL"] = {
            "stage": m.recur_stage,
            "count": m.recur_stage_count,
            "threshold": RECUR_MIN_COUNT,
        }
    elif m.recur_stage_count and not m.recur_stage:
        meta["RECUR_FAIL"] = {
            "withheld": "stage_unspecified",
            "count": m.recur_stage_count,
            "note": "失败未记 fail_stage，无法判定『同阶段』重复；记入数据质量缺口",
        }

    if m.missions_total >= MIN_MISSIONS_LOW_SUCCESS and m.success_rate is not None:
        if m.success_rate < success_floor:
            markers.append("LOW_SUCCESS")
            meta["LOW_SUCCESS"] = {
                "success_rate": round(m.success_rate, 4),
                "floor": success_floor,
                "missions_total": m.missions_total,
            }
    else:
        meta["LOW_SUCCESS"] = {
            "withheld": "insufficient_missions",
            "missions_total": m.missions_total,
            "need": MIN_MISSIONS_LOW_SUCCESS,
        }

    if not memory_available:
        meta["MEMORY_STARVED"] = {
            "withheld": "memory_source_unavailable",
            "note": "记忆源窗口内无任何记录：命中数无法区分『没命中』与『看不见』，故不打标",
        }
    elif m.missions_total >= MIN_MISSIONS_MEMORY_STARVED and m.memory_hits == 0:
        markers.append("MEMORY_STARVED")
        meta["MEMORY_STARVED"] = {
            "memory_hits": 0,
            "lessons_injected": m.lessons_injected,
            "lessons_written": m.lessons_written,
            "missions_total": m.missions_total,
        }
    else:
        meta["MEMORY_STARVED"] = {
            "withheld": "insufficient_missions_or_hits_present",
            "missions_total": m.missions_total,
            "memory_hits": m.memory_hits,
            "need": MIN_MISSIONS_MEMORY_STARVED,
        }
    return markers, meta


def score_seat(
    m: SeatMetrics,
    *,
    markers: Sequence[str] = (),
    memory_available: bool = True,
) -> dict[str, Any]:
    """健康分：四分量加权 + 缺失重归一化 + 标记上限。返回可复算 components。

    无任务（total=0）→ score=None（无定义，不按 0 处理）。
    """
    if m.missions_total <= 0:
        return {
            "score": None,
            "raw": None,
            "components": {"success": None, "controllability": None,
                           "attribution": None, "memory": None},
            "weights_used": {},
            "penalties_applied": [],
            "reason": "no_missions_in_window",
        }
    comps: dict[str, float | None] = {
        "success": _ratio(m.missions_succeeded, m.missions_total),
        "controllability": 1.0 - (m.forced_finish_count / m.missions_total),
        "attribution": (1.0 if m.failures_total == 0
                        else m.stage_attributed / m.failures_total),
        "memory": (None if not memory_available
                   else min(1.0, (m.lessons_injected + m.memory_hits) / m.missions_total)),
    }
    used = {k: WEIGHTS[k] for k, v in comps.items() if v is not None}
    wsum = sum(used.values())
    raw = (None if wsum <= 0 else
           100.0 * sum(WEIGHTS[k] * v for k, v in comps.items() if v is not None) / wsum)
    pens = [(k, PENALTIES[k]) for k in markers if k in PENALTIES]
    score = raw
    if raw is not None and pens:
        factor = 1.0
        for _k, f in pens:
            factor *= f
        score = max(0.0, raw * factor)
    return {
        "score": None if score is None else round(score, 2),
        "raw": None if raw is None else round(raw, 2),
        "components": {k: (None if v is None else round(v, 4)) for k, v in comps.items()},
        "weights_used": used,
        "weights_renormalized": round(wsum, 4),
        "penalties_applied": [{"marker": k, "factor": f} for k, f in pens],
        "reason": "ok",
    }

# ─────────────── SQL（可复算；source/target schema 可换，供红队靶场） ───────────────


AGG_SQL = """
WITH m AS (
    SELECT organization_id,
           CASE WHEN COALESCE(btrim(claimed_by), '') = '' THEN '{unattributed}'
                ELSE btrim(claimed_by) END AS seat_key,
           status::text AS status,
           NULLIF(btrim(COALESCE(result->>'fail_stage', '')), '') AS fail_stage,
           (COALESCE((result ? 'reaped_by'), false)
            OR COALESCE((result ? 'stop_condition'), false)
            OR status::text IN ('revoked', 'stopped')) AS forced,
           SUBSTRING(goal FROM 'memory_key=([A-Za-z0-9][A-Za-z0-9._-]*)') AS memory_key
    FROM {src}.tensor_dev_missions
    WHERE created_at >= %(since)s AND created_at < %(until)s
),
agg AS (
    SELECT seat_key,
           min(organization_id) AS organization_id,
           count(*) AS missions_total,
           count(*) FILTER (WHERE status = 'succeeded') AS missions_succeeded,
           count(*) FILTER (WHERE status <> 'succeeded' OR fail_stage IS NOT NULL)
               AS failures_total,
           count(*) FILTER (WHERE fail_stage IS NOT NULL) AS stage_attributed,
           count(*) FILTER (WHERE fail_stage IS NOT NULL OR forced) AS forced_finish_count,
           count(*) FILTER (WHERE memory_key IS NOT NULL) AS memory_key_missions
    FROM m GROUP BY seat_key
),
stage AS (
    SELECT seat_key, COALESCE(fail_stage, '{unspecified}') AS stage, count(*) AS n
    FROM m WHERE status <> 'succeeded' OR fail_stage IS NOT NULL GROUP BY 1, 2
),
stage_dist AS (
    SELECT seat_key, jsonb_object_agg(stage, n) AS dist FROM stage GROUP BY 1
),
recur AS (
    SELECT DISTINCT ON (seat_key) seat_key, stage, n
    FROM stage
    WHERE stage <> '{unspecified}' AND n >= %(recur_min)s
    ORDER BY seat_key, n DESC, stage ASC
),
workers AS (
    SELECT worker_id AS seat_key,
           min(organization_id) AS organization_id,
           max(CASE WHEN jsonb_typeof(memory) = 'array' THEN jsonb_array_length(memory)
                    ELSE 0 END) AS memory_stock
    FROM {src}.factory_resident_workers
    GROUP BY worker_id
),
seats AS (
    SELECT seat_key FROM agg UNION SELECT seat_key FROM workers
)
SELECT COALESCE(a.organization_id, w.organization_id) AS organization_id,
       s.seat_key,
       COALESCE(a.missions_total, 0)        AS missions_total,
       COALESCE(a.missions_succeeded, 0)    AS missions_succeeded,
       COALESCE(a.failures_total, 0)        AS failures_total,
       COALESCE(a.stage_attributed, 0)      AS stage_attributed,
       COALESCE(a.forced_finish_count, 0)   AS forced_finish_count,
       COALESCE(a.memory_key_missions, 0)   AS memory_key_missions,
       COALESCE(sd.dist, '{}'::jsonb)       AS fail_stage_dist,
       r.stage                              AS recur_stage,
       COALESCE(r.n, 0)                     AS recur_stage_count,
       COALESCE(w.memory_stock, 0)          AS memory_lessons_stock
FROM seats s
LEFT JOIN agg a ON a.seat_key = s.seat_key
LEFT JOIN stage_dist sd ON sd.seat_key = s.seat_key
LEFT JOIN recur r ON r.seat_key = s.seat_key
LEFT JOIN workers w ON w.seat_key = s.seat_key
ORDER BY 1, 2
"""

MEMORY_SQL = """
SELECT COALESCE(w.worker_id, i.worker_id) AS seat_key,
       count(*) FILTER (WHERE e.event_type = 'lesson') AS lessons_written,
       count(*) FILTER (WHERE COALESCE((e.payload ? 'injected'), false)
                          OR COALESCE(e.payload->>'phase', '') = 'inject')
           AS lessons_injected,
       count(*) FILTER (WHERE COALESCE((e.payload ? 'hit'), false)
                          OR COALESCE(e.provenance ? 'hit', false)
                          OR COALESCE(e.payload->>'phase', '') = 'hit')
           AS memory_hits
FROM {src}.factory_memory_events e
LEFT JOIN {src}.factory_resident_workers w ON w.agent_session_id = e.session_id
LEFT JOIN {src}.factory_agent_identities i ON i.agent_id = e.agent_id
WHERE e.created_at >= %(since)s AND e.created_at < %(until)s
GROUP BY 1
"""

UPSERT_SQL = """
INSERT INTO {tgt}.tensor_agent_health (
    organization_id, seat_key, seat_kind, snapshot_date, window_days,
    missions_total, missions_succeeded, success_rate, fail_stage_dist,
    recur_stage, recur_stage_count, forced_finish_count, forced_finish_rate,
    stage_attributed, failures_total,
    lessons_written, lessons_injected, memory_hits, memory_lessons_stock,
    memory_key_missions, memory_attribution,
    health_score, score_components, markers, markers_meta, source_sql, computed_at
) VALUES (
    %(organization_id)s, %(seat_key)s, %(seat_kind)s, %(snapshot_date)s, %(window_days)s,
    %(missions_total)s, %(missions_succeeded)s, %(success_rate)s, %(fail_stage_dist)s::jsonb,
    %(recur_stage)s, %(recur_stage_count)s, %(forced_finish_count)s, %(forced_finish_rate)s,
    %(stage_attributed)s, %(failures_total)s,
    %(lessons_written)s, %(lessons_injected)s, %(memory_hits)s, %(memory_lessons_stock)s,
    %(memory_key_missions)s, %(memory_attribution)s::jsonb,
    %(health_score)s, %(score_components)s::jsonb, %(markers)s, %(markers_meta)s::jsonb,
    %(source_sql)s::jsonb, %(computed_at)s
)
ON CONFLICT (organization_id, seat_key, snapshot_date, window_days) DO UPDATE SET
    seat_kind = excluded.seat_kind,
    missions_total = excluded.missions_total,
    missions_succeeded = excluded.missions_succeeded,
    success_rate = excluded.success_rate,
    fail_stage_dist = excluded.fail_stage_dist,
    recur_stage = excluded.recur_stage,
    recur_stage_count = excluded.recur_stage_count,
    forced_finish_count = excluded.forced_finish_count,
    forced_finish_rate = excluded.forced_finish_rate,
    stage_attributed = excluded.stage_attributed,
    failures_total = excluded.failures_total,
    lessons_written = excluded.lessons_written,
    lessons_injected = excluded.lessons_injected,
    memory_hits = excluded.memory_hits,
    memory_lessons_stock = excluded.memory_lessons_stock,
    memory_key_missions = excluded.memory_key_missions,
    memory_attribution = excluded.memory_attribution,
    health_score = excluded.health_score,
    score_components = excluded.score_components,
    markers = excluded.markers,
    markers_meta = excluded.markers_meta,
    source_sql = excluded.source_sql,
    computed_at = excluded.computed_at
"""

#: 同 (snapshot_date, window_days) 先清后写：保证同日重跑=换一份完整快照，
#: 不残留上一轮有、这一轮没有的席位（否则「席位消失」会留下陈旧标记）。
PRUNE_SQL = """
DELETE FROM {tgt}.tensor_agent_health
WHERE snapshot_date = %(snapshot_date)s AND window_days = %(window_days)s
"""

CHRONICLE_SQL = """
INSERT INTO {tgt}.chronicle_snapshots (ts, payload) VALUES (%(ts)s, %(payload)s::jsonb)
"""

WEEKLY_SQL = """
SELECT week_start, organization_id, window_days, seats,
       recur_fail_seats, low_success_seats, memory_starved_seats, any_marker_seats,
       avg_score, median_score, min_score,
       missions_total, missions_succeeded, success_rate,
       forced_finish_count, forced_finish_rate,
       lessons_written, lessons_injected, memory_hits
FROM {tgt}.v_agent_health_weekly
ORDER BY week_start DESC, organization_id
LIMIT %(limit)s
"""

TOP_SEATS_SQL = """
SELECT seat_key, health_score, missions_total, missions_succeeded, success_rate,
       forced_finish_count, forced_finish_rate, markers
FROM {tgt}.v_agent_health_seat_latest
WHERE seat_kind = 'worker' AND (%(only_marked)s = false OR cardinality(markers) > 0)
ORDER BY health_score ASC NULLS LAST, seat_key
LIMIT %(limit)s
"""


def check_ident(name: str, what: str) -> str:
    if not _IDENT.match(name or ""):
        raise ValueError(f"{what} 非法标识符：{name!r}")
    return name


# ─────────────────────── 计量器（DB 交互层） ───────────────────────


class HealthMeter:
    """采集 → 打分 → 落快照 → chronicle → 周报，单层单责。"""

    def __init__(
        self,
        *,
        dsn: str | None = None,
        source_schema: str = "public",
        target_schema: str = "public",
        window_days: int = WINDOW_DAYS,
        success_floor: float = DEFAULT_SUCCESS_FLOOR,
        dry_run: bool = False,
        log_path: str | None = DEFAULT_LOG,
        conn: Any = None,
    ) -> None:
        self.dsn = dsn or DEFAULT_DSN
        self.src = check_ident(source_schema, "source_schema")
        self.tgt = check_ident(target_schema, "target_schema")
        self.window_days = window_days
        self.success_floor = success_floor
        self.dry_run = dry_run
        self.log_path = log_path
        self._conn = conn

    @property
    def conn(self):  # pragma: no cover - 薄封装
        if self._conn is None:
            try:
                import psycopg  # type: ignore

                self._conn = psycopg.connect(self.dsn, autocommit=True)
            except ImportError:
                import psycopg2  # type: ignore

                self._conn = psycopg2.connect(self.dsn)
                self._conn.autocommit = True
        return self._conn

    def fetchall(self, sql: str, params: Mapping[str, Any]) -> list[tuple]:
        cur = self.conn.cursor()
        cur.execute(sql, dict(params))
        rows = cur.fetchall()
        cur.close()
        return list(rows)

    def execute(self, sql: str, params: Mapping[str, Any]) -> int:
        cur = self.conn.cursor()
        cur.execute(sql, dict(params))
        n = cur.rowcount
        cur.close()
        return n

    def log(self, message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        if not self.log_path:
            return
        try:
            p = Path(self.log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    # -- 采集 --------------------------------------------------------------
    def window(self, as_of: date | None = None) -> tuple[datetime, datetime]:
        day = as_of or datetime.now(timezone.utc).date()
        end = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        return end - timedelta(days=self.window_days), end

    def collect(self, as_of: date | None = None) -> tuple[list[SeatMetrics], dict[str, Any]]:
        since, until = self.window(as_of)
        params = {"since": since, "until": until, "recur_min": RECUR_MIN_COUNT}
        sql = (AGG_SQL.replace("{src}", self.src)
                      .replace("{unattributed}", UNATTRIBUTED)
                      .replace("{unspecified}", UNSPECIFIED_STAGE))
        rows = self.fetchall(sql, params)
        seats: dict[str, SeatMetrics] = {}
        for (org, seat_key, total, ok, fails, attributed, forced, memkey,
             dist, recur_stage, recur_n, stock) in rows:
            seats[seat_key] = SeatMetrics(
                seat_key=seat_key,
                organization_id=org,
                missions_total=int(total or 0),
                missions_succeeded=int(ok or 0),
                failures_total=int(fails or 0),
                stage_attributed=int(attributed or 0),
                forced_finish_count=int(forced or 0),
                recur_stage=recur_stage,
                recur_stage_count=int(recur_n or 0),
                fail_stage_dist=dict(dist or {}),
                memory_lessons_stock=int(stock or 0),
                memory_key_missions=int(memkey or 0),
            )
        mem_status = self.attach_memory(seats, since, until)
        evidence = {
            "source_schema": self.src,
            "target_schema": self.tgt,
            "window": {"since": since.isoformat(), "until": until.isoformat(),
                       "window_days": self.window_days},
            "sql": {
                "aggregate": " ".join(sql.split()),
                "memory": " ".join(MEMORY_SQL.replace("{src}", self.src).split()),
                "params": {k: (v.isoformat() if isinstance(v, datetime) else v)
                           for k, v in params.items()},
            },
            "memory_status": mem_status,
        }
        return list(seats.values()), evidence

    def attach_memory(self, seats: dict[str, SeatMetrics], since: datetime,
                      until: datetime) -> dict[str, Any]:
        """记忆面：能归因就填；归因不到如实置 0 并写缺口原因（不抛异常打断快照）。"""
        sql = MEMORY_SQL.replace("{src}", self.src)
        status: dict[str, Any] = {
            "canonical_source": f"{self.src}.factory_memory_events",
        }
        try:
            rows = self.fetchall(sql, {"since": since, "until": until})
        except Exception as exc:
            status.update({
                "status": "source_unavailable",
                "reason": f"{type(exc).__name__}: {exc}",
                "note": "记忆源不可达：lessons_written/lessons_injected/memory_hits 如实置 0，"
                        "MEMORY_STARVED 记 withheld（不把『看不见』当成『没命中』）",
            })
            return status
        by_seat = {r[0]: r for r in rows if r[0]}
        for key, seat in seats.items():
            row = by_seat.get(key)
            if row:
                seat.__dict__["lessons_written"] = int(row[1] or 0)
                seat.__dict__["lessons_injected"] = int(row[2] or 0)
                seat.__dict__["memory_hits"] = int(row[3] or 0)
        total_events = sum(int(r[1] or 0) + int(r[2] or 0) + int(r[3] or 0) for r in by_seat.values())
        status.update({
            "status": "ok" if total_events > 0 else "table_empty",
            "seats_with_events": len(by_seat),
            "events_counted": total_events,
        })
        if total_events == 0:
            status["note"] = (
                "记忆源窗口内零记录：三列如实置 0（不是编造的 0，是空账），"
                "MEMORY_STARVED 记 withheld——区分『没命中』与『没有埋点/没有写入』"
            )
        return status

    # -- 落库 --------------------------------------------------------------
    def snapshot(self, as_of: date | None = None) -> dict[str, Any]:
        snap = as_of or datetime.now(timezone.utc).date()
        seats, evidence = self.collect(snap)
        memory_available = evidence["memory_status"].get("status") == "ok"
        mem_status = evidence["memory_status"]
        payloads: list[dict[str, Any]] = []
        for s in seats:
            markers, meta = markers_for(s, success_floor=self.success_floor,
                                        memory_available=memory_available)
            scored = score_seat(s, markers=markers, memory_available=memory_available)
            payloads.append({
                "organization_id": s.organization_id or 1,
                "seat_key": s.seat_key,
                "seat_kind": s.seat_kind,
                "snapshot_date": snap,
                "window_days": self.window_days,
                "missions_total": s.missions_total,
                "missions_succeeded": s.missions_succeeded,
                "success_rate": (None if s.missions_total <= 0
                                 else round(s.missions_succeeded / s.missions_total, 4)),
                "fail_stage_dist": json.dumps(s.fail_stage_dist, ensure_ascii=False),
                "recur_stage": s.recur_stage,
                "recur_stage_count": s.recur_stage_count,
                "forced_finish_count": s.forced_finish_count,
                "forced_finish_rate": (None if s.missions_total <= 0
                                       else round(s.forced_finish_count / s.missions_total, 4)),
                "stage_attributed": s.stage_attributed,
                "failures_total": s.failures_total,
                "lessons_written": s.lessons_written,
                "lessons_injected": s.lessons_injected,
                "memory_hits": s.memory_hits,
                "memory_lessons_stock": s.memory_lessons_stock,
                "memory_key_missions": s.memory_key_missions,
                "memory_attribution": json.dumps({
                    "canonical_source": mem_status["canonical_source"],
                    "canonical_status": mem_status.get("status"),
                    "note": mem_status.get("note", ""),
                    "zero_reason": ("" if (s.lessons_written or s.lessons_injected or s.memory_hits)
                                    else mem_status.get("status")),
                    "memory_lessons_stock_capped_at": 12,
                    "memory_key_missions_7d": s.memory_key_missions,
                }, ensure_ascii=False),
                "health_score": scored["score"],
                "score_components": json.dumps(scored, ensure_ascii=False),
                "markers": markers,
                "markers_meta": json.dumps(meta, ensure_ascii=False),
                "source_sql": json.dumps(evidence, ensure_ascii=False),
                "computed_at": datetime.now(timezone.utc),
            })
        written = 0
        if not self.dry_run:
            self.execute(PRUNE_SQL.replace("{tgt}", self.tgt),
                         {"snapshot_date": snap, "window_days": self.window_days})
            for p in payloads:
                written += self.execute(UPSERT_SQL.replace("{tgt}", self.tgt), p)
            self.chronicle(payloads, snap, evidence)
        summary = self._summarise(payloads, snap, evidence, written)
        self.log("snapshot " + json.dumps(summary, ensure_ascii=False))
        return summary

    def _summarise(self, payloads: Sequence[Mapping[str, Any]], snap: date,
                   evidence: Mapping[str, Any], written: int) -> dict[str, Any]:
        seats = [p for p in payloads if p["seat_kind"] == "worker"]
        marked = [p for p in seats if p["markers"]]
        scores = [p["health_score"] for p in seats if p["health_score"] is not None]
        with_mem = sum(1 for p in payloads if '"withheld": "memory_source_unavailable"' in p["markers_meta"])
        return {
            "schema": SCHEMA,
            "snapshot_date": snap.isoformat(),
            "window_days": self.window_days,
            "dry_run": self.dry_run,
            "rows_written": written,
            "seats": len(seats),
            "unattributed_missions": next(
                (p["missions_total"] for p in payloads if p["seat_kind"] == UNATTRIBUTED), 0),
            "marked_seats": len(marked),
            "markers": {k: sum(1 for p in seats if k in p["markers"])
                        for k in ("RECUR_FAIL", "LOW_SUCCESS", "MEMORY_STARVED")},
            "memory_starved_withheld": with_mem,
            "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
            "min_score": min(scores) if scores else None,
            "memory_status": evidence["memory_status"].get("status"),
            "success_floor": self.success_floor,
            "top_marked": [
                {"seat": p["seat_key"], "score": p["health_score"], "markers": p["markers"],
                 "missions": p["missions_total"], "success_rate": p["success_rate"]}
                for p in sorted(marked, key=lambda x: (x["health_score"] is None,
                                                       x["health_score"]))[:10]
            ],
        }

    def chronicle(self, payloads: Sequence[Mapping[str, Any]], snap: date,
                  evidence: Mapping[str, Any]) -> None:
        summary = self._summarise(payloads, snap, evidence, 0)
        summary["kind"] = "agent_health_meter"
        try:
            self.execute(CHRONICLE_SQL.replace("{tgt}", self.tgt),
                         {"ts": datetime.now(timezone.utc),
                          "payload": json.dumps(summary, ensure_ascii=False)})
            self.log("chronicle ok")
        except Exception as exc:  # chronicle 缺表不该阻断快照
            self.log(f"chronicle failed: {type(exc).__name__}: {exc}")

    # -- 周报面 ------------------------------------------------------------
    def weekly_report(self, *, limit: int = 8) -> str:
        weekly = self.fetchall(WEEKLY_SQL.replace("{tgt}", self.tgt), {"limit": limit})
        top = self.fetchall(TOP_SEATS_SQL.replace("{tgt}", self.tgt),
                            {"limit": 10, "only_marked": False})
        cols = ("week_start org win seats RECUR_FAIL LOW_SUCCESS MEMORY_STARVED any_marker "
                "avg median min missions ok rate forced force_rate lessons hits").split()
        lines = ["# 席位健康度周报（agent_health_meter）", "",
                 "## 周聚合（v_agent_health_weekly）", "",
                 "| " + " | ".join(cols) + " |",
                 "|" + "---|" * len(cols)]
        for r in weekly:
            lines.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
        lines += ["", "## 最低分席位（v_agent_health_seat_latest）", "",
                  "| seat | score | missions | ok | rate | forced | force_rate | markers |",
                  "|---|---|---|---|---|---|---|---|"]
        for seat, score, total, ok, rate, forced, frate, markers in top:
            lines.append(f"| {seat} | {score} | {total} | {ok} | {rate} | {forced} | {frate} | "
                         f"{','.join(markers or [])} |")
        lines.append("")
        return "\n".join(lines)


# ─────────────────────── 守护（日轮询 + flock 幂等） ───────────────────────


def align_next(now: datetime | None = None, interval_seconds: int = 86400) -> float:
    """距下一个对齐槽的秒数（严格 >0，避免同槽空转）。"""
    now = now or datetime.now(timezone.utc)
    epoch = now.timestamp()
    nxt = (int(epoch) // interval_seconds + 1) * interval_seconds
    return max(1.0, nxt - epoch)


class Lock:
    """flock 独占锁：第二个实例要么等 timeout，要么直接退出（幂等护栏）。"""

    def __init__(self, path: str | Path, *, timeout: float = 0.0) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self._fh = None

    def __enter__(self) -> "Lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fh.seek(0)
                self._fh.truncate()
                self._fh.write(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n")
                self._fh.flush()
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self._fh.close()
                    self._fh = None
                    raise LockBusy(str(self.path))
                time.sleep(0.05)

    def __exit__(self, *exc) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None


class LockBusy(RuntimeError):
    """另一个实例持锁中（正常退让，不是错误）。"""


class MeterDaemon:
    """日轮询：一拍 = 快照 + chronicle + 周报落盘；异常收成 error 拍不自杀。"""

    def __init__(self, meter: HealthMeter, *, lock_path: str, report_dir: str | None,
                 interval: int = 86400, log_path: str | None = DEFAULT_LOG) -> None:
        self.meter = meter
        self.lock_path = lock_path
        self.report_dir = report_dir
        self.interval = interval
        self.log_path = log_path
        self._stop = False

    def log(self, msg: str) -> None:
        self.meter.log(msg)

    def beat(self, as_of: date | None = None) -> dict[str, Any]:
        started = time.monotonic()
        try:
            with Lock(self.lock_path):
                summary = self.meter.snapshot(as_of)
                if self.report_dir:
                    md = self.meter.weekly_report()
                    p = Path(self.report_dir)
                    p.mkdir(parents=True, exist_ok=True)
                    out = p / f"agent_health_weekly_{(as_of or datetime.now(timezone.utc).date()).isoformat()}.md"
                    out.write_text(md, encoding="utf-8")
                    self.log(f"weekly_report -> {out}")
                    summary["weekly_report"] = str(out)
            summary["beat_seconds"] = round(time.monotonic() - started, 2)
            summary["status"] = "ok"
            return summary
        except LockBusy:
            self.log("beat skipped: lock busy")
            return {"status": "skipped", "reason": "lock_busy"}
        except Exception as exc:
            self.log(f"beat error: {type(exc).__name__}: {exc}")
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    def run_forever(self, *, max_beats: int = 0) -> int:
        def _stop(signum, _frame):
            self._stop = True
            self.log(f"signal {signum}: 本拍跑完退出")

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        beats = 0
        while not self._stop and (max_beats <= 0 or beats < max_beats):
            result = self.beat()
            beats += 1
            self.log("beat " + json.dumps(result, ensure_ascii=False)[:600])
            if self._stop or (max_beats > 0 and beats >= max_beats):
                break
            time.sleep(align_next(None, self.interval))
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="席位健康度仪表（只计量不裁决）")
    ap.add_argument("--once", action="store_true", help="跑一拍就退")
    ap.add_argument("--daemon", action="store_true", help="常驻日轮询")
    ap.add_argument("--interval", type=int, default=86400, help="轮询间隔秒（默认 86400）")
    ap.add_argument("--max-beats", type=int, default=0, help="0=无限（限次用）")
    ap.add_argument("--weekly-report", action="store_true", help="只出周报面 markdown")
    ap.add_argument("--report-dir", default=None, help="周报落盘目录")
    ap.add_argument("--as-of", default=None, help="快照日 YYYY-MM-DD（回填/复算用）")
    ap.add_argument("--dsn", default=None, help="PG 连接串（默认 TP_DSN）")
    ap.add_argument("--source-schema", default="public")
    ap.add_argument("--target-schema", default="public")
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--success-floor", type=float, default=DEFAULT_SUCCESS_FLOOR)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.add_argument("--lock-timeout", type=float, default=0.0)
    args = ap.parse_args(list(argv) if argv is not None else None)

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    meter = HealthMeter(
        dsn=args.dsn, source_schema=args.source_schema, target_schema=args.target_schema,
        window_days=args.window_days, success_floor=args.success_floor,
        dry_run=args.dry_run, log_path=args.log,
    )
    lock_path = str(Path(args.state_dir) / "agent_health_meter.lock")
    daemon = MeterDaemon(meter, lock_path=lock_path, report_dir=args.report_dir,
                         interval=args.interval, log_path=args.log)

    if args.weekly_report:
        print(meter.weekly_report())
        return 0
    if args.daemon:
        return daemon.run_forever(max_beats=args.max_beats)
    # 默认（含 --once）
    result = daemon.beat(as_of)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
