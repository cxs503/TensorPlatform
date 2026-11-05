"""PR-merge completion gate (owner 2026-11-05: 推了 PR 的任务，PR 合并才算完成).

Evidence layers, each able to go red:
1. **契约层**：awaiting_pr_merge ∈ statuses 且 ∈ platform_only，且 ∉ terminal
   —— G-A 对拍（test_status_contracts.py）同时锁「迁移终态定义 == 契约」，
   漏改迁移 100 或词表任一侧都会在那边先红；
2. **迁移层**：100_pr_merge_gate.sql 以 096 棘轮风格 DROP+ADD 重宽 CHECK，
   含 awaiting_pr_merge；
3. **端点源码层**：pr-gate 三态转移 + 409 守卫 + mission_lifecycle_log 审计
   + pr-merge-queue 对账清单；closed 从 succeeded 亦落 failed（PR 关未并 =
   未完成，不因时序漏网）；
4. **运行时层**：MISSION_STATUSES 吸收契约新词、TERMINAL 不吸收（非终态）。

PG 行为级验证（真实转移 UPDATE 被约束接受）需要 PostgreSQL，CI 为 sqlite
按 TP_PR_GATE_PG_DSN 环境变量 skipif——容器/生产环境提供 DSN 即自动跑。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
CONTRACT = BACKEND / "contracts" / "mission_status_vocab.v1.json"
MIGRATION = BACKEND / "migrations" / "100_pr_merge_gate.sql"
MIGRATION_101 = BACKEND / "migrations" / "101_pr_merge_gate_log.sql"
LIFECYCLE = BACKEND / "api" / "tensor_mission_lifecycle.py"


def test_contract_adds_awaiting_pr_merge():
    doc = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert doc["version"] >= 2, "词表变更须升版"
    assert "awaiting_pr_merge" in doc["statuses"]
    assert "awaiting_pr_merge" in doc["platform_only"], "只由平台/守护置位，不由 worker 写"
    assert "awaiting_pr_merge" not in doc["terminal"], \
        "awaiting_pr_merge 必须是非终态——合并前任务不算完成"


def test_migration_100_widens_status_check():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "DROP CONSTRAINT IF EXISTS tensor_dev_missions_status_check" in sql
    m = re.search(r"CHECK \(status IN \(([^)]*)\)", sql)
    assert m, "100 必须重定义 status CHECK（096 棘轮风格）"
    states = {s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()}
    assert "awaiting_pr_merge" in states
    # 棘轮不缩词：096 终态 10 词全数保留
    assert {"queued", "claimed", "running", "awaiting_acceptance", "succeeded",
            "failed", "stopped", "revoked", "suspended", "orphan"} <= states


def test_migration_101_widens_lifecycle_log_action_check():
    """首跑实锤（2026-11-05 回填 175 单全 500）：pr-gate 审计行
    action='pr_gate' 撞 mission_lifecycle_log 的 095 六词 CHECK。
    101 棘轮增补，六词全保留。"""
    sql = MIGRATION_101.read_text(encoding="utf-8")
    assert "DROP CONSTRAINT IF EXISTS mission_lifecycle_log_action_check" in sql
    m = re.search(r"CHECK \(action IN \(([^)]*)\)", sql)
    assert m, "101 必须重定义 action CHECK"
    actions = {s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()}
    assert "pr_gate" in actions
    assert {"suspend", "resume", "write_off", "orphan_detect", "reclaim",
            "necessity_gate"} <= actions, "棘轮不缩词：095 六词全保留"


def test_endpoint_declares_three_transitions():
    src = LIFECYCLE.read_text(encoding="utf-8")
    assert '"/{mission_id}/pr-gate"' in src, "pr-gate 端点必须存在"
    assert '"/pr-merge-queue"' in src, "对账清单端点必须存在"
    assert 'require_role("ADMIN", "MANAGER")' in src, "转移必须 ADMIN/MANAGER 权"
    for needle, why in [
        ('new_status = "awaiting_pr_merge"', "open: succeeded → 临时成功"),
        ('new_status = "succeeded"', "merged: 合并后终判成功"),
        ('new_status = "failed"', "closed: PR 关未并 = 未完成"),
        ("pr-gate(open) needs status=succeeded", "open 的 409 守卫"),
        ("pr-gate(merged) cannot fire from", "merged 的 409 守卫"),
        ("pr-gate(closed) cannot fire from", "closed 的 409 守卫"),
        ("INSERT INTO mission_lifecycle_log", "每次上报必须留审计"),
    ]:
        assert needle in src, why
    # closed 从 succeeded 也必须落 failed：时序上若 open 上报丢失/晚到，
    # PR 被关未并的 mission 不得以 succeeded 漏网
    m = re.search(r"else:  # closed.*?raise HTTPException", src, re.S)
    assert m and '"awaiting_pr_merge", "succeeded", "failed"' in m.group(0), \
        "closed 转移必须覆盖 awaiting_pr_merge 与 succeeded 两态"


def test_runtime_vocab_picks_up_new_status():
    from api.tensor_missions import MISSION_STATUSES, TERMINAL
    assert "awaiting_pr_merge" in MISSION_STATUSES
    assert "awaiting_pr_merge" not in TERMINAL


@pytest.mark.skipif(not os.environ.get("TP_PR_GATE_PG_DSN"),
                    reason="PG 行为级验证需 TP_PR_GATE_PG_DSN（CI 为 sqlite）")
def test_pg_constraint_accepts_new_status():
    """真库行为：迁移 100 应用后，awaiting_pr_merge 的 UPDATE/INSERT 被约束
    接受、未知态仍被拒。探针行用后即删，不触存量单。"""
    DSN = os.environ["TP_PR_GATE_PG_DSN"]
    import psycopg2
    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid='tensor_dev_missions'::regclass "
                        "AND conname='tensor_dev_missions_status_check'")
            defin = cur.fetchone()[0]
            assert "awaiting_pr_merge" in defin, "约束未含新态（迁移 100 未应用?）"
            cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid='mission_lifecycle_log'::regclass "
                        "AND conname='mission_lifecycle_log_action_check'")
            logdef = cur.fetchone()[0]
            assert "pr_gate" in logdef, "log 约束未含 pr_gate（迁移 101 未应用?）"
            cur.execute("INSERT INTO tensor_dev_missions "
                        "(organization_id, goal, repo, status, origin) "
                        "VALUES (1, 'pr-gate-probe', 'TensorProbe', "
                        "'awaiting_pr_merge', 'owner') RETURNING id")
            pid = cur.fetchone()[0]
            cur.execute("INSERT INTO mission_lifecycle_log "
                        "(mission_id, action, detail) "
                        "VALUES (%s, 'pr_gate', '{}') ", (pid,))
            for st in ("succeeded", "awaiting_pr_merge", "failed"):
                cur.execute("UPDATE tensor_dev_missions SET status=%s "
                            "WHERE id=%s", (st, pid))
            with pytest.raises(Exception):
                cur.execute("UPDATE tensor_dev_missions SET status='bogus' "
                            "WHERE id=%s", (pid,))
            cur.execute("DELETE FROM mission_lifecycle_log WHERE mission_id=%s", (pid,))
            cur.execute("DELETE FROM tensor_dev_missions WHERE id=%s", (pid,))
        conn.commit()
    finally:
        conn.close()
