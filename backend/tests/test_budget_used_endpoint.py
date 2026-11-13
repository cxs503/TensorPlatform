"""FP15-BUDGET-ACCOUNTING ①：终态转移真的落账（真 PG + 真路由函数）。

验收判据（机器可算，逐条断言）
----------------------------
1. 12 张合成终态单走真实代码路径（``complete_mission`` / ``stop_mission``）后
   ``budget_used`` 非空率 = 100%（抽 12 单，id 清单写进
   ``artifacts/fp15/new_terminal_budget_used_ids.json``）；
2. 每单 ``budget_used.tool_calls == result.tool_calls``（同一条 SQL 可对拍）；
3. ``list_missions`` 投影里 ``budget_used`` / ``fail_stage`` 可见——"持久化了
   但 API 看不见"等于没落账。

为什么必须真 Postgres
---------------------
生产 SQL 用 ``result || CAST(:summary AS jsonb)`` 合并 jsonb；sqlite 上会退化成
字符串拼接（``{}{...}``），`tool_calls == result.tool_calls` 的对拍结论不成立
——那样的绿是假绿。本文件用 ``TENSOR_TEST_PG_DSN`` 指向**专门的临时库**，
未设置时 skip（"没跑"如实标 skip，绝不标 passed）。

安全闸：若 DSN 指向生产库名（``tensorplatform``），本文件直接 skip —— 测试绝不
写生产 mission 表。
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
for path in (str(BACKEND), str(REPO)):
    if path not in sys.path:
        sys.path.insert(0, path)

PG_DSN = os.environ.get("TENSOR_TEST_PG_DSN", "")
ARTIFACT = REPO / "artifacts" / "fp15" / "new_terminal_budget_used_ids.json"

pytestmark = pytest.mark.skipif(
    not PG_DSN or PG_DSN.rstrip("/").endswith(("/tensorplatform", ":tensorplatform"))
    or "/tensorplatform?" in PG_DSN,
    reason="TENSOR_TEST_PG_DSN not set to a scratch Postgres DB (sqlite cannot "
           "emulate jsonb || jsonb, prod DB refuses)")

ORG = 1
UID = 4242
USER = {"id": UID, "username": "synth-agent", "role": "AGENT",
        "is_active": True, "organization_id": ORG}

DDL = [
    """CREATE TABLE IF NOT EXISTS tensor_dev_missions (
        id BIGSERIAL PRIMARY KEY,
        organization_id BIGINT NOT NULL,
        goal TEXT NOT NULL,
        repo VARCHAR(120) NOT NULL DEFAULT 'platform-lib',
        base_ref VARCHAR(120) NOT NULL DEFAULT 'main',
        co_repos TEXT[],
        parent_mission_id INTEGER,
        budget JSONB NOT NULL DEFAULT '{}'::jsonb,
        runner VARCHAR(16) NOT NULL DEFAULT 'native',
        origin VARCHAR(16) NOT NULL DEFAULT 'owner',
        model VARCHAR(64),
        agent_session_id UUID,
        worker_role TEXT,
        campaign_id BIGINT,
        campaign_phase TEXT,
        status VARCHAR(24) NOT NULL DEFAULT 'queued',
        claim_token UUID,
        claimed_by VARCHAR(120),
        claimed_user_id BIGINT,
        session_id BIGINT,
        result JSONB NOT NULL DEFAULT '{}'::jsonb,
        budget_used JSONB NOT NULL DEFAULT '{}'::jsonb,
        budget_frozen JSONB,
        fail_stage VARCHAR(60),
        report_owed BOOLEAN NOT NULL DEFAULT FALSE,
        report_id INTEGER,
        worktree_recyclable BOOLEAN NOT NULL DEFAULT FALSE,
        write_off_at TIMESTAMPTZ,
        suspended_at TIMESTAMPTZ,
        suspend_reason TEXT,
        suspended_by BIGINT,
        task_key TEXT,
        created_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        heartbeat_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ
    )""",
    """CREATE TABLE IF NOT EXISTS tensor_approval_requests (
        id BIGSERIAL PRIMARY KEY,
        organization_id BIGINT, mission_id BIGINT, scenario_id BIGINT,
        kind VARCHAR(40), reason TEXT, evidence JSONB,
        status VARCHAR(20) DEFAULT 'pending', requested_by VARCHAR(120),
        decided_by VARCHAR(120), decided_at TIMESTAMPTZ, decision_note TEXT,
        created_at TIMESTAMPTZ DEFAULT now())""",
]


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine, text

    eng = create_engine(PG_DSN, future=True)
    with eng.begin() as conn:
        for ddl in DDL:
            conn.execute(text(ddl))
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    from sqlalchemy.orm import sessionmaker

    session = sessionmaker(bind=engine, future=True)()
    yield session
    session.rollback()
    session.close()


def _seed_running(db, *, idx: int, cap: int, result: dict | None = None) -> tuple[int, str]:
    """Insert one synthetic running mission (synthetic task_key, no domain words)."""
    from sqlalchemy import text

    token = str(uuid.uuid4())
    mission_id = db.execute(
        text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, budget, status, claim_token, claimed_by,
                 claimed_user_id, result, task_key, created_at)
            VALUES (:org, :goal, 'platform-lib', CAST(:budget AS jsonb), 'running',
                    CAST(:tok AS uuid), 'synth-agent', :uid, CAST(:result AS jsonb),
                    :tk, now() - interval '40 minutes')
            RETURNING id
        """),
        {"org": ORG, "goal": f"synthetic budget-accounting fixture #{idx}",
         "budget": json.dumps({"max_tool_calls": cap, "max_wall_seconds": 7200,
                               "max_tokens": 3_000_000}),
         "tok": token, "uid": UID, "result": json.dumps(result or {}),
         "tk": f"synth.fp15.budget.{idx:03d}"},
    ).scalar()
    db.commit()
    return int(mission_id), token


def _read(db, mission_id: int) -> dict:
    from sqlalchemy import text

    row = db.execute(text("""
        SELECT status, fail_stage, budget_used, result,
               (result->>'tool_calls') AS result_tool_calls
        FROM tensor_dev_missions WHERE id = :mid
    """), {"mid": mission_id}).mappings().first()
    db.commit()
    return dict(row)


def test_terminal_transitions_persist_budget_used(db, engine):
    from api.tensor_missions import CompleteRequest, complete_mission, list_missions
    from api.tensor_missions import StopRequest, stop_mission

    recorded: list[dict] = []
    cases: list[tuple[str, dict, dict | None, int]] = []

    # (a) 4 张：agent 提请验收，summary 自带 tool_calls（result_fact）
    # (b) 3 张：失败单 + usage 自报（agent_declared）
    # (c) 2 张：只有心跳 progress 的用量事实（heartbeat_progress）
    # (d) 2 张：什么都没有 → 必须降级但四键齐全（unavailable）
    # (e) 1 张：owner 停止（/stop 路径同样要落账）
    plan = (
        [("accept", {"headline": "synthetic delivery", "tool_calls": 40 + i},
          None, 60) for i in range(4)]
        + [("failed", {"headline": "synthetic fail", "tool_calls": 55 + i},
            {"tool_calls": 55 + i, "tokens": 1000 * (i + 1), "wall_seconds": 600 + i},
            80) for i in range(3)]
        + [("failed_progress", {"headline": "synthetic progress"}, None, 60)
           for _ in range(2)]
        + [("failed_bare", {"headline": "synthetic bare"}, None, 60) for _ in range(2)]
        + [("stop", {}, None, 80)]
    )

    for idx, (kind, summary, usage, cap) in enumerate(plan, start=1):
        seeded = {"progress": {"tool_calls": 33 + idx, "wall_seconds": 900}} \
            if kind == "failed_progress" else None
        mission_id, token = _seed_running(db, idx=idx, cap=cap, result=seeded)
        if kind == "stop":
            stop_mission(mission_id, StopRequest(reason="synthetic owner stop"), db, USER)
        else:
            status = "succeeded" if kind == "accept" else "failed"
            body = CompleteRequest(claim_token=token, status=status,
                                   acceptance=(kind == "accept"), summary=summary,
                                   usage=usage,
                                   fail_stage=None if kind == "accept" else "tool_loop")
            complete_mission(mission_id, body, db, USER)
        cases.append({"kind": kind, "id": mission_id,
                      "progress_seed": (33 + idx) if kind == "failed_progress" else None})

    # ── 断言 1：非空率 100%（12/12）────────────────────────────────────────
    for case in cases:
        state = _read(db, case["id"])
        blob = state["budget_used"] or {}
        if isinstance(blob, str):
            blob = json.loads(blob)
        assert blob, f"mission {case['id']} ({case['kind']}) left budget_used empty"
        for key in ("tool_calls", "tokens", "wall_seconds", "source"):
            assert key in blob, f"mission {case['id']} missing {key}: {blob}"

        # ── 断言 2：tool_calls == result.tool_calls（有该键时逐单对拍）──────
        result_calls = state["result_tool_calls"]
        if result_calls is not None:
            assert int(blob["tool_calls"]) == int(result_calls), (
                f"mission {case['id']}: budget_used.tool_calls={blob['tool_calls']} "
                f"!= result.tool_calls={result_calls}")
        elif case["kind"] == "failed_progress":
            # 只有心跳 progress 的用量事实 → 从 progress 回算，来源如实标注
            assert blob["tool_calls"] == case["progress_seed"]
            assert "heartbeat_progress" in blob["source"]
        else:
            assert blob["tool_calls"] == 0
            assert "unavailable" in blob["source"]
        assert isinstance(blob["wall_seconds"], int) and blob["wall_seconds"] > 0

        case.update({"status": state["status"], "budget_used": blob,
                     "result_tool_calls": result_calls})
        recorded.append(case)

    nonempty = sum(1 for c in recorded if all(
        k in c["budget_used"] for k in ("tool_calls", "tokens", "wall_seconds", "source")))
    assert nonempty == len(recorded) == 12

    # ── 断言 3：API 投影可见（不是"只写库"）──────────────────────────────
    listing = list_missions(status="failed", limit=100, offset=0, task_key_like="synth.fp15.",
                            db=db, user=USER)
    visible = [item for item in listing["items"] if item.get("task_key", "").startswith("synth.fp15.")]
    assert visible, "synthetic missions not visible through the list API projection"
    for item in visible:
        assert item["budget_used"], f"API hid budget_used for mission {item['id']}"
        assert "tool_calls" in item["budget_used"]
        assert "fail_stage" in item

    # ── 证据工件（id 清单 + 查询命令，供外部复算）─────────────────────────
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "criterion": "new terminal missions with non-empty budget_used / total = 12/12",
        "rate_pct": 100.0,
        "mission_ids": [c["id"] for c in recorded],
        "cases": recorded,
        "query": ("SELECT id, status, budget_used->>'tool_calls' AS used "
                  "FROM tensor_dev_missions WHERE task_key LIKE 'synth.fp15.%' ORDER BY id"),
    }, ensure_ascii=False, indent=2))

    # 清理：本文件只在自己的临时库里造数据，跑完删掉自己的合成单
    from sqlalchemy import text
    db.execute(text("DELETE FROM tensor_dev_missions WHERE task_key LIKE 'synth.fp15.%'"))
    db.commit()


def test_stop_and_failure_paths_are_also_covered():
    """防退化：12 条用例必须覆盖 4 条终态路径（accept/failed/progress/stop）。"""
    assert ARTIFACT.exists(), "evidence artifact missing (test above must run first)"
    data = json.loads(ARTIFACT.read_text())
    kinds = {case["kind"] for case in data["cases"]}
    assert kinds == {"accept", "failed", "failed_progress", "failed_bare", "stop"}
    assert len(data["mission_ids"]) >= 12