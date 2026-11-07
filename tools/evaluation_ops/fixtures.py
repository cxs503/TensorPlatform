"""评价时效性（IN35-N07）的确定性夹具：合成队列 + 评价记录流。

为什么用合成夹具而不是线上 PG
------------------------------
本节点要机器裁的三件事（cohort 不变性 / in-flight 延迟 / 积压排空）都需要**可控的**
队列状态：注入恰好 N 行 pending、把某一批 mission 的 ``finished_at`` 摆在跨越 ISO 周的
固定位置、让一部分成果「证据不足」以观察排空率的天花板。线上库是活体（每单都在变），
同一判据跑两次会得到不同数字 → 不可复算。因此判据全部在固定种子的 SQLite 夹具上跑，
线上数据只用于口径对照（见 ``docs/IN35-N07_eval_timeliness.md`` §局限）。

口径（与 ``cohorts``/``inflight`` 完全一致，夹具不自造规则）
-----------------------------------------------------------
- 只写三张表：``tensor_dev_missions``（合成队列）、``outcome_evaluations``（评价记录流，
  用 ``tools.outcome_evaluations.DDL``）、以及本节点新增的两张运维表
  （``eval_inflight_events`` / ``eval_drain_runs``，由 ``triggers.ensure_tables`` 建）。
- mission 一律 ``repo='eval-timeliness-fixture'``，与线上数据无交集。
- 没有 ``tensor_reports`` / ``tensor_capability_deltas`` 表：评价器走「只有平台事实 →
  E1 + S1」的兜底分支（``inflight.default_evaluator``），这也是线上绝大多数单的实际形态。
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from tools import outcome_evaluations as oe

FIXTURE_REPO = "eval-timeliness-fixture"
#: 固定观测时刻：2026-11-07（周五，ISO 第 45 周）09:00 UTC
DEFAULT_AS_OF = "2026-11-07T09:00:00+00:00"
DEFAULT_SEED = 7

MISSION_DDL = """
CREATE TABLE IF NOT EXISTS tensor_dev_missions (
    id              BIGINT PRIMARY KEY,
    repo            TEXT,
    status          TEXT,
    claimed_by      TEXT,
    organization_id BIGINT,
    finished_at     TEXT,
    report_id       BIGINT,
    result          TEXT,
    fail_stage      TEXT,
    report_owed     BOOLEAN DEFAULT FALSE
);
"""

DECIDABLE_RESULT = {"checks": {"run": "pytest -q tests/", "outcome": "passed"},
                    "accepted": True, "on_disk_changes": ["tools/x.py"]}


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


#: 每路径一个 Engine（SQLite 允许多连接；这里复用，避免夹具被 GC 提前回收）
_ENGINES: dict[str, Any] = {}


def connect(path: str):
    """打开夹具库并返回 Connection（同一路径复用 Engine）。"""
    from sqlalchemy import create_engine

    key = str(path)
    engine = _ENGINES.get(key)
    if engine is None:
        engine = create_engine(f"sqlite:///{key}", future=True)
        _ENGINES[key] = engine
    return engine.connect()


def ensure_mission_table(conn) -> None:
    from sqlalchemy import text

    for stmt in [s.strip() for s in MISSION_DDL.split(";") if s.strip()]:
        conn.execute(text(stmt))
    for stmt in [s.strip() for s in oe.DDL.split(";") if s.strip()]:
        conn.execute(text(stmt))
    conn.commit()


def _insert_mission(conn, *, mid: int, finished: datetime, status: str = "succeeded",
                    result: dict[str, Any] | None = None, claimed_by: str = "fixture-agent") -> None:
    from sqlalchemy import text

    conn.execute(
        text(
            "INSERT INTO tensor_dev_missions "
            "(id, repo, status, claimed_by, organization_id, finished_at, report_id, result) "
            "VALUES (:id, :repo, :status, :who, :org, :fin, NULL, :res)"
        ),
        {"id": mid, "repo": FIXTURE_REPO, "status": status, "who": claimed_by,
         "org": 1, "fin": _iso(finished),
         "res": json.dumps(result if result is not None else DECIDABLE_RESULT)},
    )


def _insert_record(conn, *, mid: int, status: str, finished: datetime,
                   decided_at: datetime | None = None) -> None:
    """直接写评价记录行（绕过判据）：夹具要的是「队列状态」，不是「判据正确性」。"""
    from sqlalchemy import text

    if status == "pending":
        record = oe.pending_record_from_mission(
            {"id": mid, "repo": FIXTURE_REPO, "claimed_by": "fixture-agent",
             "organization_id": 1, "finished_at": _iso(finished)})
    else:
        record = {
            "evaluation_id": oe.evaluation_id_for(oe.artifact_id_for_mission(mid)),
            "artifact_id": oe.artifact_id_for_mission(mid),
            "mission_id": int(mid),
            "contributor": "fixture-agent",
            "grade": "S1",
            "e_level": "E1",
            "grade_reason": (
                "合成夹具：规则化扫描（§7.1 结构判据，非人工/同行评审）登记执行性成果；"
                "此行为夹具预置，用于构造覆盖率与时延分布，不代表真实评审结论。"
            ),
            "evidence_refs": oe.dumps_json(
                [{"kind": "run", "ref": f"mission:{mid}#result.checks.run"}]),
            "reviewer": "fixture-scanner",
            "reviewer_kind": "auto_scan",
            "review_status": "unreviewed",
            "baseline_or_scope": "执行性成果：交付物与验收判据逐条对应",
            "boundary": "合成夹具数据，不构成对任何真实成果的判定。",
            "status": status,
            "reason_codes": oe.dumps_json([]),
            "reason_source": "rule_derived",
            "repo": FIXTURE_REPO,
            "organization_id": 1,
            "window_finished_at": None,
            "created_at": _iso(finished),
            "decided_at": _iso(decided_at or finished),
        }
    conn.execute(text(oe.insert_if_absent_sql()), record)


def build(path: str, *, as_of: str = DEFAULT_AS_OF, seed: int = DEFAULT_SEED,
          per_week: int = 40, backlog: int = 100, unrecorded: int = 30) -> dict[str, Any]:
    """造一个跨越 3 个 ISO 周 + 一段 pending 积压 + 一批未评价 mission 的固定夹具。

    组成（``as_of=2026-11-07T09:00Z``）：

    ============  ======  ============  ======================
    分组           周       mission 数    记录
    ============  ======  ============  ======================
    decided-W43    2026-W43   40            10 confirmed
    decided-W44    2026-W44   40            20 confirmed
    decided-W45    2026-W45   40            20 confirmed
    backlog-W45    2026-W45  100           100 pending
    unrecorded-W45 2026-W45   30             无记录
    ============  ======  ============  ======================
    """
    rng = random.Random(seed)
    as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(timezone.utc)
    conn = connect(path)
    ensure_mission_table(conn)

    # 3 个 ISO 周的起点（周一 00:00 UTC），最后一周边「进行中」
    mondays: list[datetime] = []
    monday = (as_of_dt - timedelta(days=as_of_dt.weekday())).replace(hour=0, minute=0, second=0)
    for k in (2, 1, 0):
        mondays.append(monday - timedelta(days=7 * k))

    decided_by_week = {0: 10, 1: 20, 2: 20}
    mid = 1
    janitor: dict[str, Any] = {"missions": 0, "confirmed": 0, "pending": 0, "unrecorded": 0}
    latencies: list[float] = []

    for wk, start in enumerate(mondays):
        week_end = min(start + timedelta(days=7), as_of_dt) - timedelta(minutes=1)
        span = max((week_end - start).total_seconds(), 3600.0)
        for i in range(per_week):
            finished = start + timedelta(seconds=span * (i + 0.5) / per_week)
            _insert_mission(conn, mid=mid, finished=finished)
            janitor["missions"] += 1
            if i < decided_by_week[wk]:
                lag = rng.uniform(0.5, 96.0)
                _insert_record(conn, mid=mid, status="confirmed", finished=finished,
                               decided_at=finished + timedelta(hours=lag))
                janitor["confirmed"] += 1
                latencies.append(lag)
            mid += 1

    # 积压：100 张已完结但只有 pending 行（其中 8 张证据不足 → 排空率天花板 92%）
    week45 = mondays[-1]
    for i in range(backlog):
        finished = week45 + timedelta(hours=1 + (i % 40) * 0.9)
        undecidable = i % 100 in {3, 17, 29, 41, 53, 67, 79, 91}
        _insert_mission(conn, mid=mid, finished=finished,
                        result={} if undecidable else DECIDABLE_RESULT)
        _insert_record(conn, mid=mid, status="pending", finished=finished)
        janitor["pending"] += 1
        janitor["missions"] += 1
        mid += 1

    # 未记录：30 张已完结、完全没有评价记录（in-flight 事件驱动要拾取的对象）
    for i in range(unrecorded):
        finished = week45 + timedelta(hours=30 + (i % 20) * 0.4)
        if finished > as_of_dt:
            finished = as_of_dt - timedelta(minutes=5 + i)
        _insert_mission(conn, mid=mid, finished=finished)
        janitor["unrecorded"] += 1
        janitor["missions"] += 1
        mid += 1

    conn.commit()
    return {
        "schema": "eval-timeliness-fixture/1",
        "path": str(path),
        "repo": FIXTURE_REPO,
        "as_of": _iso(as_of_dt),
        "seed": int(seed),
        "counts": janitor,
        "weeks": [_iso(s) for s in mondays],
        "preset_confirmed_latencies_hours": [round(x, 6) for x in latencies],
        "backlog_undecidable_missions": 8,
        "backlog_decidable_ceiling_share": (
            round((backlog - 8) / backlog, 6) if backlog else None),
    }


def connect_existing(path: str):
    """打开已造好的夹具（只读语义由调用方保证）。"""
    return connect(path)