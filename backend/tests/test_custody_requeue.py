"""Merge custody — 推送 PR 的 agent 管到底（owner 2026-11-05）.

Evidence layers, each able to go red:
1. **端点源码层**：custody-requeue 单一转移 awaiting_pr_merge → queued，
   409 守卫（非 awaiting 不吃）、轮次帽（max_rounds，超出 409 回退 heal 派单）、
   goal 追加【管到底·第N轮】段（追加非覆写）、budget 增量=max(旧上限, 已用+grant)、
   mission_lifecycle_log 审计；
2. **迁移层**：102_custody_requeue_action.sql 以 101 棘轮风格拓宽 action
   CHECK，七词全保留 + custody_requeue（#600 首跑 CheckViolation 同病预防）；
3. **契约层**：custody 不新增状态词——queued 本就在词表，词表不动。

PG 行为级验证需要 PostgreSQL，CI 为 sqlite 按 TP_PR_GATE_PG_DSN 环境变量
skipif（与 test_pr_gate.py 同法）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
LIFECYCLE = BACKEND / "api" / "tensor_mission_lifecycle.py"
MIGRATION_102 = BACKEND / "migrations" / "102_custody_requeue_action.sql"

PG_DSN = os.environ.get("TP_PR_GATE_PG_DSN")


def _src() -> str:
    return LIFECYCLE.read_text(encoding="utf-8")


def test_migration_102_widens_action_check_ratchet():
    sql = MIGRATION_102.read_text(encoding="utf-8")
    assert "DROP CONSTRAINT IF EXISTS mission_lifecycle_log_action_check" in sql
    m = re.search(r"CHECK \(action IN \(([^)]*)\)", sql)
    assert m, "102 必须重定义 action CHECK（101 棘轮风格）"
    words = {s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()}
    assert "custody_requeue" in words
    # 棘轮不缩词：101 七词全数保留
    assert {
        "suspend",
        "resume",
        "write_off",
        "orphan_detect",
        "reclaim",
        "necessity_gate",
        "pr_gate",
    } <= words


def test_endpoint_declares_single_transition_and_guards():
    src = _src()
    assert "custody-requeue" in src, "路由必须挂 /{mission_id}/custody-requeue"
    assert "custody-requeue needs status=awaiting_pr_merge" in src, (
        "非 awaiting_pr_merge 必 409（merged/failed 是终态路径，不回流）"
    )
    assert "custody rounds exhausted" in src, "轮次帽超限必 409（防乒乓）"
    assert re.search(r"SET status = 'queued', claim_token = NULL", src), (
        "唯一转移：awaiting_pr_merge → queued 且清 claim"
    )
    assert "goal = goal || :append" in src, "goal 追加而非覆写（原任务上下文保留）"
    assert "【管到底·第{rnd}轮】" in src, "追加段必须可辨识（管到底轮次标记）"


def test_endpoint_budget_grant_is_runway_not_blind_add():
    """grant 语义 = 保底跑道：max(旧上限, 已用+grant)——旧上限更高时不降。"""
    src = _src()
    assert "budget[bkey] = max(int(budget.get(bkey, 0) or 0), used[key] + add)" in src
    assert (
        "max_tool_calls" in src and "max_wall_seconds" in src and "max_tokens" in src
    ), "budget 键名与 missions 默认值同源"


def test_round_counter_reads_lifecycle_log():
    """轮次计数 = 历史 custody_requeue 审计行 + 1（幂等可查，不靠内存）。"""
    src = _src()
    m = re.search(
        r"SELECT count\(\*\) AS n FROM mission_lifecycle_log\s*\n?\s*"
        r"WHERE mission_id = :mid AND action = 'custody_requeue'",
        src,
    )
    assert m, "轮次必须从 append-only 审计表数出"


@pytest.mark.skipif(not PG_DSN, reason="PG DSN 未提供（sqlite CI 跳过）")
def test_pg_constraint_accepts_custody_action():
    """真库行为：迁移 102 应用后 action='custody_requeue' 审计行被 CHECK
    接受。探针行用后即删，不触存量单。"""
    import psycopg2

    conn = psycopg2.connect(PG_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid='mission_lifecycle_log'::regclass "
                "AND conname='mission_lifecycle_log_action_check'"
            )
            defin = cur.fetchone()[0]
            assert "custody_requeue" in defin, (
                "约束未含 custody_requeue（迁移 102 未应用?）"
            )
            cur.execute(
                "INSERT INTO mission_lifecycle_log "
                "(mission_id, action, detail) "
                "VALUES (0, 'custody_requeue', '{\"selftest\": true}')"
            )
            cur.execute(
                "DELETE FROM mission_lifecycle_log "
                "WHERE mission_id = 0 AND action = 'custody_requeue'"
            )
        conn.commit()
    finally:
        conn.close()
