"""评价时效性契约（IN35-N07）：事件驱动 in-flight 评价 + 积压排空 SLO。

两个契约，一个模块
------------------
**A. 事件驱动增量评价（in-flight）**：mission 进入终态（``succeeded``/``failed``/``done``）
的那一刻就调一次 ``on_mission_terminal()``，**不做全仓扫描**：

1. 该 mission 无有效评价记录 → 先落一行 ``pending``（可审计「进了队列」）；
2. 若终态是成功且有证据 → 立刻用同一套结构判据就地判定（``pending`` 行原地升级）；
3. 无论判定与否，都写一行事件（``eval_inflight_events``）记录
   ``事件时刻 → 判定时刻`` 的时延，供 P50/P95/P99 分位与面板取用。

失败单（``failed``/``stopped``）**只入队不判定**：没有交付物的单子不该由机器扫描
替它写等级；事件行 ``outcome='queued'`` 如实标注，留给独立审查者。

**B. 积压排空 SLO（backlog drain）**：``pending`` 行数 > ``threshold``（默认 50）时
自动触发批量评价，目标「24 h 内排空率 ≥ 90%」。``drain_backlog()`` 逐批调用
``inflight``（有``limit`` 上限，不一次刷库），把每次排空写成一行
``eval_drain_runs``：``pending_before/after``、``drained``、``drain_share``、
``elapsed_seconds``、``met``。排空率的天花板是「证据是否足够」：证据不足的成果
按纪律保持 ``pending``（不许为了刷指标把它写成 rejected），所以 ``drain_share`` 可能
< 1 —— 这个差额必须如实进报告，不能靠放宽判据抹平。

不做什么（纪律）
----------------
- **不碰调度面**：不改 ``tensor_dev_missions`` 的任何列（不改状态、不改排队序）；
  ``inflight.missions_digest`` 用于前后指纹比对。
- **不新增评价判据**：等级/证据规则仍在 ``tools.outcome_evaluations.decide`` 与
  ``inflight.default_evaluator``；本模块只负责「什么时候评」「评完记账」。
- **不改既有 API**：``outcome_evaluations`` 的端点与 ``summarize()`` 输出不变
  （判据 J4 用 ``contract_baseline`` 冻结的基线逐字段比对）。

边界（不粉饰）
--------------
- 时延是**本进程墙钟**测的事件→判定耗时（``time.monotonic``），不含 API/网关排队；
  跨进程部署时它衡量的是「评价器本身」的增量成本，不是端到端用户体验。
- ``eval_inflight_events`` 是**追加日志**，不参与判据计算；事件表丢行只会让分位样本
  变少，不会改变覆盖率（覆盖率只由 mission + 记录流决定）。
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from tools import outcome_evaluations as oe

from . import cohorts as ch
from . import inflight

SCHEMA = "eval-timeliness/1"
EVENT_TABLE = "eval_inflight_events"
DRAIN_TABLE = "eval_drain_runs"
TAB_MISSIONS = "tensor_dev_missions"
TAB_EVALS = "outcome_evaluations"

#: 触发增量评价的终态（``done`` 是部分执行器的写法，与 ``succeeded`` 同义）
TERMINAL_STATUSES: tuple[str, ...] = ("succeeded", "failed", "done", "completed", "stopped")
#: 其中「有交付物、可以机器判定」的终态
DELIVERED_STATUSES: tuple[str, ...] = ("succeeded", "done", "completed")

#: 积压阈值与排空目标（任务书第 ④ 条）
DEFAULT_BACKLOG_THRESHOLD = 50
DRAIN_TARGET_SHARE = 0.90
DRAIN_WINDOW_HOURS = 24.0

EVENT_OUTCOMES: tuple[str, ...] = (
    "decided",       # 已就地判定（confirmed/candidate）
    "queued",        # 只入队（失败单/无证据）
    "undecidable",   # 证据不足，保持 pending
    "rejected",      # 判据不通过，留痕
    "skipped",       # 非终态/数据缺失
    "error",
)

DDL_SQLITE_PG = f"""
CREATE TABLE IF NOT EXISTS {EVENT_TABLE} (
    event_id        TEXT PRIMARY KEY,
    mission_id      BIGINT NOT NULL,
    trigger_status  TEXT NOT NULL,
    event_at        TEXT NOT NULL,
    finished_at     TEXT,
    decided_at      TEXT,
    outcome         TEXT NOT NULL,
    evaluation_status TEXT,
    grade           TEXT,
    e_level         TEXT,
    latency_seconds REAL,
    repo            TEXT,
    detail          TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_inflight_event_at
    ON {EVENT_TABLE} (event_at);

CREATE INDEX IF NOT EXISTS idx_eval_inflight_mission
    ON {EVENT_TABLE} (mission_id);

CREATE TABLE IF NOT EXISTS {DRAIN_TABLE} (
    run_id          TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    trigger_kind    TEXT NOT NULL,
    threshold_rows  INTEGER,
    pending_before  INTEGER,
    pending_after   INTEGER,
    drained         INTEGER,
    undecidable     INTEGER,
    rejected        INTEGER,
    drain_share     REAL,
    elapsed_seconds REAL,
    target_share    REAL,
    window_hours    REAL,
    met             BOOLEAN,
    dry_run         BOOLEAN,
    detail          TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_drain_started
    ON {DRAIN_TABLE} (started_at);
"""


# ───────────────────────────── 小工具 ─────────────────────────────

def _rows(conn, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import text

    return [dict(r) for r in conn.execute(text(sql), dict(params or {})).mappings().all()]


def _scalar(conn, sql: str, params: Mapping[str, Any] | None = None) -> Any:
    from sqlalchemy import text

    row = conn.execute(text(sql), dict(params or {})).first()
    return None if row is None else row[0]


def _now() -> str:
    return ch.now_utc()


def _b(value: bool) -> Any:
    return bool(value)


def ensure_tables(conn) -> None:
    """幂等建两张运维表（SQLite / PostgreSQL 通用子集；不触碰既有表）。"""
    from sqlalchemy import text

    for stmt in [s.strip() for s in DDL_SQLITE_PG.split(";") if s.strip()]:
        conn.execute(text(stmt))
    conn.commit()


def has_table(conn, name: str) -> bool:
    """表是否存在（面板/排空读面在运维表未建时退化为空，不抛异常）。

    ``conn`` 既可能是 Connection 也可能是 Session（API 层传的是 Session，直接
    ``inspect(Session)`` 不可用），所以先取绑定再 inspect；探测失败返回 False。
    """
    from sqlalchemy import inspect

    try:
        bind = conn.get_bind() if hasattr(conn, "get_bind") else conn
        return bool(inspect(bind).has_table(name))
    except Exception:  # pragma: no cover - 引擎不支持 inspector
        return inflight.has_table(conn, name)


def pending_rows(conn) -> int:
    return int(_scalar(
        conn, f"SELECT count(*) FROM {TAB_EVALS} WHERE status = :s", {"s": "pending"}) or 0)


def backlog_state(conn, *, threshold: int = DEFAULT_BACKLOG_THRESHOLD) -> dict[str, Any]:
    """当前积压与是否越过阈值（纯读，不改库）。"""
    n = pending_rows(conn)
    return {
        "pending_rows": n,
        "threshold": int(threshold),
        "over_threshold": n > int(threshold),
        "shortfall_to_target": max(int(threshold) - n, 0),
    }


def latency_percentiles(values: Sequence[float]) -> dict[str, Any]:
    """事件时延分位（与 ``cohorts.percentile`` 同口径：线性插值）。"""
    xs = [float(v) for v in values if v is not None]
    if not xs:
        return {"n": 0, "p50": None, "p95": None, "p99": None, "max": None, "min": None}
    return {
        "n": len(xs),
        "p50": ch.percentile(xs, 0.50),
        "p95": ch.percentile(xs, 0.95),
        "p99": ch.percentile(xs, 0.99),
        "max": max(xs),
        "min": min(xs),
    }


def histogram(values: Iterable[float], *, edges: Sequence[float] = (0.05, 0.2, 1.0, 5.0, 60.0)
              ) -> list[dict[str, Any]]:
    """事件时延直方图（右开区间；最后一个桶 ``+inf``）。"""
    xs = [float(v) for v in values]
    bounds = list(edges)
    out = []
    prev = 0.0
    for edge in bounds:
        out.append({"lo": prev, "hi": edge, "n": sum(1 for x in xs if prev <= x < edge)})
        prev = edge
    out.append({"lo": prev, "hi": None, "n": sum(1 for x in xs if x >= prev)})
    return out


# ─────────────────── A. 事件驱动 in-flight 增量评价 ───────────────────

def _mission_row(conn, mission_id: int) -> dict[str, Any] | None:
    """取单张 mission（复用 inflight 的列选择器，保证评价器看到的字段一处定义）。"""
    base = inflight._mission_select(conn)
    rows = _rows(conn, f"{base}WHERE m.id = :mid", {"mid": int(mission_id)})
    return inflight._normalize(rows[0]) if rows else None


DECIDED_STATUSES: tuple[str, ...] = ("confirmed", "candidate")


def _active_record(conn, mission_id: int) -> dict[str, Any] | None:
    """该 mission 的唯一有效记录行（``status <> 'rejected'``），无则 None。"""
    if not inflight.has_table(conn, TAB_EVALS):
        return None
    rows = _rows(
        conn,
        f"SELECT status, decided_at, grade, e_level FROM {TAB_EVALS} "
        "WHERE mission_id = :mid AND status <> 'rejected' "
        "ORDER BY (decided_at IS NULL), decided_at DESC LIMIT 1",
        {"mid": int(mission_id)},
    )
    return rows[0] if rows else None


def _record_event(conn, event: Mapping[str, Any]) -> None:
    from sqlalchemy import text

    conn.execute(
        text(
            f"INSERT INTO {EVENT_TABLE} (event_id, mission_id, trigger_status, event_at, "
            "finished_at, decided_at, outcome, evaluation_status, grade, e_level, "
            "latency_seconds, repo, detail) VALUES (:event_id, :mission_id, :trigger_status, "
            ":event_at, :finished_at, :decided_at, :outcome, :evaluation_status, :grade, "
            ":e_level, :latency_seconds, :repo, :detail)"
        ),
        dict(event),
    )


def on_mission_terminal(conn, mission_id: int, *, status: str | None = None,
                        evaluator: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
                        commit: bool = False, event_at: str | None = None,
                        source: str = "hook") -> dict[str, Any]:
    """**单张** mission 的增量评价（事件驱动入口；幂等，可重放）。

    ``status`` 缺省时读库里当前状态。返回事件字典（同时落 ``eval_inflight_events``）。
    """
    started = time.monotonic()
    stamp = event_at or _now()
    mission = _mission_row(conn, mission_id)
    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "mission_id": int(mission_id),
        "trigger_status": str(status or "unknown"),
        "event_at": stamp,
        "finished_at": None,
        "decided_at": None,
        "outcome": "skipped",
        "evaluation_status": None,
        "grade": None,
        "e_level": None,
        "latency_seconds": None,
        "repo": None,
        "detail": None,
    }
    if mission is None:
        event["detail"] = json.dumps({"reason": "mission_not_found", "source": source},
                                     ensure_ascii=False)
        if commit:
            ensure_tables(conn)
            _record_event(conn, event)
            conn.commit()
        return event

    evaluator = inflight.default_evaluator if evaluator is None else evaluator
    st = str(status or mission.get("status") or "")
    event["trigger_status"] = st
    event["repo"] = mission.get("repo")
    event["finished_at"] = ch.isots(mission.get("finished_at"))

    if st not in TERMINAL_STATUSES:
        event["detail"] = json.dumps({"reason": "not_terminal", "status": st}, ensure_ascii=False)
        if commit:
            ensure_tables(conn)
            _record_event(conn, event)
            conn.commit()
        return event

    delivered = st in DELIVERED_STATUSES
    active = _active_record(conn, int(mission_id))
    active_status = str(active["status"]) if active else None
    finished = mission.get("finished_at")

    stats = {"queued": 0, "decided": 0, "rejected": 0, "undecidable": 0, "errors": [], "items": []}
    if active_status in DECIDED_STATUSES:
        # 幂等：已判定的成果不因重复事件被「降级」回 pending（store_record 的 UPDATE 语义
        # 是「无条件就地升级」，所以这里必须先挡一层）
        event["outcome"] = "decided"
        event["detail"] = json.dumps({"reason": "already_decided", "status": active_status},
                                     ensure_ascii=False)
    elif not delivered:
        # 失败单：只入队（无交付物 → 机器扫描不写等级）
        if active_status is None:
            stats = inflight.apply(conn, {"unrecorded": [mission], "pending": [],
                                          "counts": {"unrecorded_available": 1,
                                                     "unrecorded_selected": 1,
                                                     "pending_available": 0}},
                                   evaluator=None, dry_run=False, commit=False)
        event["outcome"] = "queued"
        event["detail"] = json.dumps(
            {"reason": "failed_mission_no_deliverable",
             "note": "失败单只入队，等级留给独立审查者；机器扫描不得替失败单写等级"},
            ensure_ascii=False)
        active = _active_record(conn, int(mission_id))
        active_status = str(active["status"]) if active else None
    elif active_status == "pending":
        stats = inflight.apply(conn, {"unrecorded": [], "pending": [mission],
                                      "counts": {"unrecorded_available": 0,
                                                 "unrecorded_selected": 0,
                                                 "pending_available": 1}},
                               evaluator=evaluator, dry_run=False, commit=False)
    else:
        stats = inflight.apply(conn, {"unrecorded": [mission], "pending": [],
                                      "counts": {"unrecorded_available": 1,
                                                 "unrecorded_selected": 1,
                                                 "pending_available": 0}},
                               evaluator=evaluator, dry_run=False, commit=False)

    if event["outcome"] == "decided" and event["detail"]:
        record = active
    else:
        record = _active_record(conn, int(mission_id))
    eval_status = str(record["status"]) if record else None
    decided_at = ch.isots(record["decided_at"]) if record else None

    if event["outcome"] not in ("decided", "queued"):
        if stats["errors"]:
            event["outcome"] = "error"
            event["detail"] = json.dumps(stats["errors"][:3], ensure_ascii=False)
        elif eval_status in DECIDED_STATUSES:
            event["outcome"] = "decided"
        elif eval_status == "rejected":
            event["outcome"] = "rejected"
        elif stats["undecidable"]:
            event["outcome"] = "undecidable"
            event["detail"] = json.dumps({"reason": "insufficient_evidence"}, ensure_ascii=False)
        else:
            event["outcome"] = "queued"

    event["evaluation_status"] = eval_status
    event["decided_at"] = decided_at
    event["grade"] = record.get("grade") if record else None
    event["e_level"] = record.get("e_level") if record else None
    event["latency_seconds"] = round(time.monotonic() - started, 6)

    if commit:
        ensure_tables(conn)
        _record_event(conn, event)
        conn.commit()
    return event


def catch_up(conn, *, limit: int = 200, evaluator=None, commit: bool = False,
             statuses: Sequence[str] = ("succeeded",)) -> dict[str, Any]:
    """补偿扫描：终态但**从未进过事件表**的 mission（进程崩/表新上线时用）。

    这不是常规路径（常规路径是 ``on_mission_terminal`` 的钩子），只为「事件丢了怎么补」
    留一条有界、可重放的通道。
    """
    placeholders = ", ".join(f":s{i}" for i in range(len(statuses)))
    params: dict[str, Any] = {f"s{i}": s for i, s in enumerate(statuses)}
    params["lim"] = int(limit)
    rows = _rows(
        conn,
        f"SELECT m.id, m.status FROM {TAB_MISSIONS} m WHERE m.status IN ({placeholders}) "
        f"AND m.finished_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {EVENT_TABLE} e "
        f"WHERE e.mission_id = m.id) ORDER BY m.finished_at DESC, m.id DESC LIMIT :lim",
        params,
    )
    events = [on_mission_terminal(conn, int(r["id"]), status=str(r["status"]),
                                 evaluator=evaluator, commit=False, source="catch_up")
              for r in rows]
    if commit and events:
        ensure_tables(conn)
        for e in events:
            _record_event(conn, e)
        conn.commit()
    counts: dict[str, int] = {}
    for e in events:
        counts[e["outcome"]] = counts.get(e["outcome"], 0) + 1
    return {
        "schema": f"{SCHEMA}#catch-up",
        "scanned": len(rows),
        "events": len(events),
        "outcome_counts": counts,
        "latency": latency_percentiles([e["latency_seconds"] for e in events
                                        if e["latency_seconds"] is not None]),
    }


# ───────────────────── B. 积压排空 SLO ─────────────────────

def should_drain(conn, *, threshold: int = DEFAULT_BACKLOG_THRESHOLD) -> dict[str, Any]:
    state = backlog_state(conn, threshold=threshold)
    state["trigger"] = bool(state["over_threshold"])
    state["rule"] = f"pending_rows > {int(threshold)} ⇒ 自动批量评价"
    return state


def _pending_missions(conn, *, repo: str | None = None) -> list[dict[str, Any]]:
    """当前只有 ``pending`` 记录的 mission 行（排空的对象集合，一次取全便于固定分母）。"""
    base = inflight._mission_select(conn)
    params: dict[str, Any] = {}
    repo_clause = ""
    if repo:
        repo_clause = "AND m.repo = :repo\n"
        params["repo"] = repo
    rows = _rows(
        conn,
        f"{base}WHERE m.status = 'succeeded' AND m.finished_at IS NOT NULL\n{repo_clause}"
        f"  AND EXISTS (SELECT 1 FROM {TAB_EVALS} e2 WHERE e2.mission_id = m.id "
        f"AND e2.status = 'pending')\n"
        "ORDER BY m.finished_at ASC, m.id ASC",
        params,
    )
    return [inflight._normalize(r) for r in rows]


def _decided_mission_ids(conn, mission_ids: Sequence[int]) -> set[int]:
    if not mission_ids:
        return set()
    placeholders = ", ".join(f":m{i}" for i in range(len(mission_ids)))
    params = {f"m{i}": int(v) for i, v in enumerate(mission_ids)}
    rows = _rows(
        conn,
        f"SELECT DISTINCT mission_id FROM {TAB_EVALS} WHERE status IN ('confirmed','candidate') "
        f"AND mission_id IN ({placeholders})",
        params,
    )
    return {int(r["mission_id"]) for r in rows}


def drain_backlog(conn, *, threshold: int = DEFAULT_BACKLOG_THRESHOLD, force: bool = False,
                  limit: int = 0, batch: int = 100, evaluator=None, commit: bool = True,
                  trigger_kind: str | None = None, repo: str | None = None,
                  dry_run: bool = False) -> dict[str, Any]:
    """越过阈值就排空**起始 pending 集合**（直到 ``pending <= threshold`` 或耗尽 ``limit``）。

    口径钉死：排空的分子/分母只在**本次开始时就已 pending** 的那批 mission 上取
    （``pending_before`` = 该批行数，``drained`` = 其中被判定出结果的张数），
    否则「一边排空一边有新的 pending 进队」会让 ``drain_share`` 失去意义。
    ``pending_after`` 仍报库里当时的真实 pending 行数（可能因新事件而反弹，如实记录）。
    """
    ensure_tables(conn)
    started_at = _now()
    t0 = time.monotonic()
    evaluator = inflight.default_evaluator if evaluator is None else evaluator
    initial = _pending_missions(conn, repo=repo)
    initial_ids = [int(m["id"]) for m in initial]
    before = len(initial_ids)
    db_pending_before = pending_rows(conn)
    kind = trigger_kind or ("forced" if force else ("threshold" if before > threshold else "noop"))
    triggered = bool(force or before > threshold)
    applied_total = {"queued": 0, "decided": 0, "rejected": 0, "undecidable": 0, "errors": 0}
    batches: list[dict[str, Any]] = []

    if triggered:
        queue = list(initial)
        budget = None if not limit else int(limit)
        while queue:
            chunk, queue = queue[:max(int(batch), 1)], queue[max(int(batch), 1):]
            if budget is not None:
                chunk, budget = chunk[:budget], budget - len(chunk)
                if not chunk:
                    break
            st = inflight.apply(conn, {"unrecorded": [], "pending": chunk,
                                       "counts": {"unrecorded_available": 0,
                                                  "unrecorded_selected": 0,
                                                  "pending_available": len(chunk)}},
                                evaluator=evaluator, dry_run=dry_run, commit=commit)
            applied_total["queued"] += int(st["queued"])
            applied_total["decided"] += int(st["decided"])
            applied_total["rejected"] += int(st["rejected"])
            applied_total["undecidable"] += int(st["undecidable"])
            applied_total["errors"] += len(st["errors"])
            batches.append({"batch": len(batches) + 1, "size": len(chunk),
                            "applied": {k: v for k, v in st.items() if k != "items"},
                            "db_pending_after": pending_rows(conn)})
            if pending_rows(conn) <= threshold and budget is None:
                break

    decided_ids = _decided_mission_ids(conn, initial_ids) if initial_ids else set()
    drained = len(decided_ids)
    after = pending_rows(conn)
    elapsed = round(time.monotonic() - t0, 6)
    share = round(drained / before, 6) if before else None
    run = {
        "schema": f"{SCHEMA}#drain-run",
        "run_id": str(uuid.uuid4()),
        "started_at": started_at,
        "finished_at": _now(),
        "trigger_kind": kind,
        "threshold_rows": int(threshold),
        "pending_before": before,
        "pending_after": after,
        "db_pending_before": db_pending_before,
        "drained": drained,
        "undecidable": applied_total["undecidable"],
        "rejected": applied_total["rejected"],
        "drain_share": share,
        "elapsed_seconds": elapsed,
        "target_share": DRAIN_TARGET_SHARE,
        "window_hours": DRAIN_WINDOW_HOURS,
        "met": bool(share is not None and share >= DRAIN_TARGET_SHARE
                    and elapsed <= DRAIN_WINDOW_HOURS * 3600),
        "dry_run": bool(dry_run),
        "triggered": triggered,
        "applied": applied_total,
        "batches": batches,
    }
    run["detail"] = json.dumps(
        {"batches": len(batches), "rule": f"pending_rows > {int(threshold)}",
         "drain_ceiling": "证据不足的成果保持 pending，不为了指标写 rejected",
         "note": "drained/pending_before 只在起始 pending 集合上取口径"}, ensure_ascii=False)

    if commit and not dry_run:
        from sqlalchemy import text

        conn.execute(
            text(
                f"INSERT INTO {DRAIN_TABLE} (run_id, started_at, finished_at, trigger_kind, "
                "threshold_rows, pending_before, pending_after, drained, undecidable, rejected, "
                "drain_share, elapsed_seconds, target_share, window_hours, met, dry_run, detail) "
                "VALUES (:run_id, :started_at, :finished_at, :trigger_kind, :threshold_rows, "
                ":pending_before, :pending_after, :drained, :undecidable, :rejected, "
                ":drain_share, :elapsed_seconds, :target_share, :window_hours, :met, :dry_run, "
                ":detail)"
            ),
            {**{k: run[k] for k in ("run_id", "started_at", "finished_at", "trigger_kind",
                                    "threshold_rows", "pending_before", "pending_after",
                                    "drained", "undecidable", "rejected", "drain_share",
                                    "elapsed_seconds", "target_share", "window_hours",
                                    "detail")},
             "met": _b(run["met"]), "dry_run": _b(run["dry_run"])},
        )
        conn.commit()
    return run


def drain_runs(conn, *, limit: int = 50) -> list[dict[str, Any]]:
    if not has_table(conn, DRAIN_TABLE):
        return []
    return _rows(
        conn, f"SELECT * FROM {DRAIN_TABLE} ORDER BY started_at DESC LIMIT :lim",
        {"lim": int(limit)})


def drain_slo(conn, *, target_share: float = DRAIN_TARGET_SHARE,
              window_hours: float = DRAIN_WINDOW_HOURS) -> dict[str, Any]:
    """最近一次排空的 SLO 判定（无排空记录 → 三态 ``undefined``，不折成 ok）。"""
    runs = drain_runs(conn, limit=1)
    if not runs:
        return {"schema": f"{SCHEMA}#drain-slo", "state": "undefined",
                "reason": "no_drain_run", "target_share": target_share,
                "window_hours": window_hours}
    run = runs[0]
    share = run["drain_share"]
    ok = (share is not None and float(share) >= float(target_share)
          and float(run["elapsed_seconds"] or 0) <= float(window_hours) * 3600)
    return {
        "schema": f"{SCHEMA}#drain-slo",
        "state": "ok" if ok else "breach",
        "run_id": run["run_id"],
        "started_at": run["started_at"],
        "pending_before": int(run["pending_before"] or 0),
        "pending_after": int(run["pending_after"] or 0),
        "drained": int(run["drained"] or 0),
        "drain_share": share,
        "elapsed_seconds": run["elapsed_seconds"],
        "target_share": float(target_share),
        "window_hours": float(window_hours),
        "met": bool(ok),
    }


def events(conn, *, limit: int = 5000, since: str | None = None) -> list[dict[str, Any]]:
    if not has_table(conn, EVENT_TABLE):
        return []
    sql = f"SELECT * FROM {EVENT_TABLE}"
    params: dict[str, Any] = {"lim": int(limit)}
    if since:
        sql += " WHERE event_at >= :since"
        params["since"] = since
    sql += " ORDER BY event_at, event_id LIMIT :lim"
    return _rows(conn, sql, params)


def inflight_slo(conn, *, target_p95_seconds: float = 60.0, limit: int = 5000,
                 since: str | None = None) -> dict[str, Any]:
    """in-flight 增量评价延迟 SLO（判据 J2：P95 < 60 s）。"""
    evs = events(conn, limit=limit, since=since)
    lat = latency_percentiles([e["latency_seconds"] for e in evs
                               if e.get("latency_seconds") is not None])
    decided = [e for e in evs if e.get("outcome") == "decided"]
    state = "undefined" if lat["p95"] is None else ("ok" if lat["p95"] < target_p95_seconds else "breach")
    return {
        "schema": f"{SCHEMA}#inflight-slo",
        "state": state,
        "target_p95_seconds": float(target_p95_seconds),
        "events": len(evs),
        "decided": len(decided),
        "outcome_counts": _count(evs, "outcome"),
        "latency_seconds": lat,
        "histogram": histogram([e["latency_seconds"] for e in evs
                                if e.get("latency_seconds") is not None]),
        "met": state == "ok",
    }


def _count(rows: Iterable[Mapping[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = str(r.get(key))
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


__all__ = [
    "DEFAULT_BACKLOG_THRESHOLD",
    "DRAIN_TARGET_SHARE",
    "DRAIN_WINDOW_HOURS",
    "DECIDED_STATUSES",
    "DELIVERED_STATUSES",
    "EVENT_OUTCOMES",
    "EVENT_TABLE",
    "DRAIN_TABLE",
    "TERMINAL_STATUSES",
    "backlog_state",
    "catch_up",
    "drain_backlog",
    "drain_runs",
    "_decided_mission_ids",
    "_pending_missions",
    "drain_slo",
    "ensure_tables",
    "events",
    "has_table",
    "histogram",
    "inflight_slo",
    "latency_percentiles",
    "on_mission_terminal",
    "pending_rows",
    "should_drain",
]