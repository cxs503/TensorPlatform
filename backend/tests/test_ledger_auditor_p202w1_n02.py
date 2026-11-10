"""P202W1-N02 状态账本关系不变量审计与自愈 —— 判据用例（C1–C7）。

场景全部为**合成账本**（零领域语义）：两个战役 plan、五个里程碑、七个节点、
五个 mission、五份 report，其中注入 13 条关系违反（覆盖 10 条规则），
manifest（``EXPECTED``）与种子数据在同一个文件里成对出现——判据数字不手抄。

方言覆盖：默认 SQLite（快、hermetic）；若设置 ``LEDGER_AUDIT_PG_DSN``
（如 ``postgresql+psycopg:///tensorplatform``）则额外在真实 PostgreSQL 上
跑一次同等场景，验证 ``INT[]`` / ``JSONB`` 路径。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from audit.ledger_auditor import (  # noqa: E402
    LEDGER_TABLES,
    RULES,
    REPORT_SETTLE_TAG,
    LedgerAuditor,
    parse_int_array,
)

# ─────────────── 合成账本 DDL（与迁移 095/086/090 的列面对齐） ───────────────

SQLITE_DDL = [
    """CREATE TABLE campaign_plans (
        id INTEGER PRIMARY KEY, organization_id INTEGER, name TEXT,
        status TEXT DEFAULT 'active')""",
    """CREATE TABLE campaign_milestones (
        id INTEGER PRIMARY KEY,
        plan_id INTEGER NOT NULL REFERENCES campaign_plans(id),
        name TEXT, dependencies TEXT DEFAULT '[]', status TEXT DEFAULT 'pending',
        metadata TEXT DEFAULT '{}')""",
    """CREATE TABLE tensor_dev_missions (
        id INTEGER PRIMARY KEY, organization_id INTEGER, goal TEXT, repo TEXT,
        status TEXT DEFAULT 'queued', result TEXT DEFAULT '{}')""",
    """CREATE TABLE campaign_nodes (
        id INTEGER PRIMARY KEY,
        plan_id INTEGER NOT NULL REFERENCES campaign_plans(id),
        milestone_id INTEGER REFERENCES campaign_milestones(id),
        mission_id INTEGER REFERENCES tensor_dev_missions(id),
        name TEXT, node_type TEXT DEFAULT 'task', dependencies TEXT DEFAULT '[]',
        status TEXT DEFAULT 'pending' CHECK (status IN
            ('pending','in-flight','done','blocked','failed','orphan')),
        block_reason TEXT, metadata TEXT DEFAULT '{}')""",
    """CREATE TABLE tensor_reports (
        id INTEGER PRIMARY KEY, organization_id INTEGER, title TEXT,
        mission_id INTEGER, tags TEXT DEFAULT '[]', markdown TEXT DEFAULT '')""",
]

# 注入的 13 条违反 → 期望的规则命中分布（判据数字的唯一来源）
EXPECTED = {
    "INV-N1": 1,   # node 3 → plan 777（幽灵节点）
    "INV-N2": 1,   # node 4 → milestone 888（悬空里程碑）
    "INV-N3": 1,   # node 6 → milestone 3（属于 plan 2，跨战役悬挂）
    "INV-N4": 1,   # node 5 → mission 999（幽灵任务）
    "INV-N5": 2,   # node 5 deps [2,50,5]（缺 50 / 自引用 5）；node 7 deps [1]（跨 plan）
    "INV-M1": 1,   # milestone 4 → plan 99（幽灵里程碑）
    "INV-M2": 1,   # milestone 5 deps [6,2] → 6 不存在
    "INV-J1": 1,   # mission 4 result = 'not-json'
    "INV-R1": 1,   # report 12 → mission 555（幽灵 mission）
    "INV-R2": 3,   # report 10/11（可反向关联）、report 13（需标记）
}
EXPECTED_TOTAL = sum(EXPECTED.values())


def seed(conn) -> None:
    """种入合成账本（含 13 条注入违反）。"""
    for ddl in SQLITE_DDL:
        conn.execute(text(ddl))
    conn.execute(text("INSERT INTO campaign_plans (id, organization_id, name) VALUES "
                      "(1, 1, 'plan-A'), (2, 1, 'plan-B')"))
    # 里程碑：ms1/ms2 属 plan1，ms3 属 plan2，ms4 幽灵(plan 99)，ms5 依赖越界
    ms = [
        (1, 1, "ms1", "[]"),
        (2, 1, "ms2", "[]"),
        (3, 2, "ms3", "[]"),
        (4, 99, "ms-ghost", "[]"),
        (5, 1, "ms5", "[6,2]"),
    ]
    for mid, pid, name, deps in ms:
        conn.execute(
            text("INSERT INTO campaign_milestones (id, plan_id, name, dependencies, metadata)"
                 " VALUES (:i, :p, :n, :d, '{}')"),
            {"i": mid, "p": pid, "n": name, "d": deps},
        )
    # mission：4 号 result 非法 JSON（TEXT 列脏数据）；1/2/5 供反向索引
    missions = [
        (1, json.dumps({"report_id": 10})),
        (2, json.dumps({"report_id": 11})),
        (3, json.dumps({})),
        (4, "not-json"),
        (5, json.dumps({"report_ids": [12]})),
    ]
    for mid, result in missions:
        conn.execute(
            text("INSERT INTO tensor_dev_missions (id, organization_id, goal, repo, status, result)"
                 " VALUES (:i, 1, 'synthetic goal', 'Repo', 'succeeded', :r)"),
            {"i": mid, "r": result},
        )
    # 节点：n1/n2 健康；n3 幽灵 plan；n4 悬空里程碑；n5 幽灵 mission + 依赖越界；
    #       n6 跨 plan 里程碑；n7(plan2) 跨 plan 依赖
    nodes = [
        (1, 1, 1, 1, "[]", "done"),
        (2, 1, 2, 2, "[1]", "done"),
        (3, 777, None, 3, "[]", "in-flight"),
        (4, 1, 888, 4, "[2]", "pending"),
        (5, 1, 2, 999, "[2,50,5]", "pending"),
        (6, 1, 3, 5, "[1,2]", "pending"),
        (7, 2, 3, 3, "[1]", "pending"),
    ]
    for nid, pid, msid, mid, deps, status in nodes:
        conn.execute(
            text("INSERT INTO campaign_nodes (id, plan_id, milestone_id, mission_id, name,"
                 " dependencies, status) VALUES (:i, :p, :m, :mi, :n, :d, :s)"),
            {"i": nid, "p": pid, "m": msid, "mi": mid, "n": f"node {nid}",
             "d": deps, "s": status},
        )
    # 报告：10/11 待反向关联；12 幽灵 mission；13 无候选需标记；14 健康
    reports = [
        (10, None, "[]"),
        (11, None, "[]"),
        (12, 555, "[]"),
        (13, None, '["misc"]'),
        (14, 3, "[]"),
    ]
    for rid, mid, tags in reports:
        conn.execute(
            text("INSERT INTO tensor_reports (id, organization_id, title, mission_id, tags)"
                 " VALUES (:i, 1, :t, :m, :g)"),
            {"i": rid, "t": f"report {rid}", "m": mid, "g": tags},
        )


# ─────────────────────────── 夹具 ───────────────────────────


@pytest.fixture()
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path/'ledger.db'}")
    with eng.begin() as conn:
        seed(conn)
    return eng


@pytest.fixture()
def auditor(db, tmp_path):
    return LedgerAuditor(db, audit_log_path=tmp_path / "audit_log.jsonl", run_id="test-run")


# ─────────────────────────── C0 工具函数 ───────────────────────────


def test_parse_int_array_agrees_across_dialects():
    assert parse_int_array("{1,2,3}") == [1, 2, 3]
    assert parse_int_array("{}") == []
    assert parse_int_array("[4,5]") == [4, 5]
    assert parse_int_array("[]") == []
    assert parse_int_array(None) is None
    assert parse_int_array("{oops}") is None


# ─────────────────────────── C1 不变量扫描零遗漏 ───────────────────────────


def test_c1_scan_matches_injected_manifest(auditor):
    violations = auditor.scan()
    by_rule: dict[str, int] = {}
    for v in violations:
        by_rule[v.rule_id] = by_rule.get(v.rule_id, 0) + 1
    assert by_rule == EXPECTED
    assert len(violations) == EXPECTED_TOTAL
    # 每条规则都必须真的被扫描器覆盖到（防止规则登记但未实现）
    assert set(by_rule) == {r.rule_id for r in RULES if r.rule_id in EXPECTED}


def test_c1_fk_coverage_matrix_has_no_hole(auditor):
    cov = auditor.fk_coverage()
    assert cov["fks_total"] > 0
    assert cov["uncovered"] == []
    assert cov["coverage_pct"] == 100.0
    # 账本内关系 FK 与租户/身份 FK 必须分别列账（不允许静默略过）
    assert cov["ledger_fks"] + cov["tenant_fks"] == cov["fks_total"]
    covered_cols = {(c["table"], c["column"]) for c in cov["covered"]}
    assert ("campaign_nodes", "plan_id") in covered_cols
    assert ("campaign_nodes", "milestone_id") in covered_cols
    assert ("campaign_milestones", "plan_id") in covered_cols


# ─────────────────────────── C2 幽灵节点识别 ───────────────────────────


def test_c2_ghost_nodes_match_manual_sql(auditor, db):
    """与独立手写 SQL 对拍：集合完全相等（100% 一致）。"""
    scanned = {(v.rule_id, v.record_pk) for v in auditor.scan() if v.rule_id == "INV-N1"}
    with db.connect() as conn:
        manual = {
            (row[0], row[1])
            for row in conn.execute(text(
                """
                SELECT 'INV-N1', n.id
                FROM campaign_nodes n
                LEFT JOIN campaign_plans p ON p.id = n.plan_id
                WHERE p.id IS NULL AND COALESCE(n.status,'') <> 'orphan'
                """
            ))
        }
        manual_ms = {
            (row[0], row[1])
            for row in conn.execute(text(
                """
                SELECT 'INV-M1', m.id
                FROM campaign_milestones m
                LEFT JOIN campaign_plans p ON p.id = m.plan_id
                WHERE p.id IS NULL
                """
            ))
        }
    assert scanned == manual == {("INV-N1", 3)}
    assert manual_ms == {("INV-M1", 4)}


# ─────────────────────────── C3/C4 自愈与修复后不变量 ───────────────────────────


def test_c3_c4_heal_reaches_zero_residual(auditor, db):
    report = auditor.run()
    assert report["scan"]["total"] == EXPECTED_TOTAL
    assert report["heal"]["applied"] == EXPECTED_TOTAL, report["heal"]["skipped"]
    assert report["heal"]["skipped"] == 0
    assert report["criteria"]["C3_heal_rate_pct"] == 100.0
    assert report["verify"]["total"] == 0, report["verify"]["residual"]
    assert report["criteria"]["C4_residual_violations"] == 0

    with db.connect() as conn:
        # 自动修复类：物理改写
        deps = {
            r[0]: parse_int_array(r[1])
            for r in conn.execute(text("SELECT id, CAST(dependencies AS TEXT) "
                                       "FROM campaign_nodes WHERE id IN (5,7)"))
        }
        assert deps == {5: [2], 7: []}
        assert conn.execute(text("SELECT milestone_id FROM campaign_nodes WHERE id = 4")
                            ).scalar() is None
        assert conn.execute(text("SELECT mission_id FROM campaign_nodes WHERE id = 5")
                            ).scalar() is None
        assert conn.execute(text("SELECT milestone_id FROM campaign_nodes WHERE id = 6")
                            ).scalar() is None
        # 标记销账类：数据保留 + 销账旗标
        assert conn.execute(text("SELECT status FROM campaign_nodes WHERE id = 3")
                            ).scalar() == "orphan"
        assert conn.execute(text("SELECT plan_id FROM campaign_nodes WHERE id = 3")
                            ).scalar() == 777
        meta = json.loads(conn.execute(
            text("SELECT CAST(metadata AS TEXT) FROM campaign_milestones WHERE id = 4")
        ).scalar())
        assert "ledger_audit_settled" in meta
        result = json.loads(conn.execute(
            text("SELECT CAST(result AS TEXT) FROM tensor_dev_missions WHERE id = 4")
        ).scalar())
        assert result["_raw_text"] == "not-json"


def test_c3_second_run_is_idempotent(auditor, db):
    auditor.run()
    second = LedgerAuditor(db, run_id="test-run-2").run()
    assert second["scan"]["total"] == 0
    assert second["heal"]["applied"] == 0


# ─────────────────────────── C5 报告欠账清偿 ───────────────────────────


def test_c5_orphan_reports_all_resolved(auditor, db):
    report = auditor.run()
    before = report["report_debt"]["before"]
    assert before["orphan_reports"] == 4
    assert before["relinkable"] == 2      # 10/11：反向索引唯一命中，可建关联
    assert before["needs_mark"] == 2      # 12（幽灵 mission，只能标记）/ 13（无候选）
    assert report["criteria"]["C5_reports_linked"] == 2
    assert report["criteria"]["C5_reports_marked"] == 2
    assert report["criteria"]["C5_resolved_pct"] == 100.0

    after = report["report_debt"]["after"]
    assert after["orphan_reports"] == 2           # 仍未关联的两份，但已显式销账
    assert after["already_settled"] == 2
    assert after["needs_mark"] == 0 and after["relinkable"] == 0

    with db.connect() as conn:
        links = {
            r[0]: r[1]
            for r in conn.execute(text("SELECT id, mission_id FROM tensor_reports"
                                       " WHERE id IN (10,11)"))
        }
        assert links == {10: 1, 11: 2}            # 反向索引唯一命中
        tags = json.loads(conn.execute(
            text("SELECT CAST(tags AS TEXT) FROM tensor_reports WHERE id = 12")).scalar())
        assert REPORT_SETTLE_TAG in tags
        assert conn.execute(text("SELECT mission_id FROM tensor_reports WHERE id = 12")
                            ).scalar() == 555     # 原始脏值保留（可审计）


# ─────────────────────────── C6 审计日志 ───────────────────────────


def test_c6_audit_log_complete_and_hash_chained(auditor, db, tmp_path):
    report = auditor.run()
    assert report["audit_log"]["entries"] == EXPECTED_TOTAL
    assert report["audit_log"]["chain_ok"] is True

    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(text(
            "SELECT * FROM ledger_audit_log ORDER BY id")).mappings()]
    assert len(rows) == EXPECTED_TOTAL
    for r in rows:
        assert r["ts_utc"] and r["run_id"] == "test-run"
        assert r["rule_id"] in EXPECTED
        assert r["strategy"]
        assert json.loads(r["before_state"]).get("row_text")
        assert json.loads(r["after_state"])
        assert r["reason"]
    # JSONL 镜像同步落盘
    lines = (tmp_path / "audit_log.jsonl").read_text().splitlines()
    assert len(lines) == EXPECTED_TOTAL
    assert {json.loads(l)["entry_hash"] for l in lines} == {r["entry_hash"] for r in rows}


def test_c6_tamper_breaks_chain(auditor, db):
    auditor.run()
    assert auditor.audit_log_chain()["chain_ok"] is True
    with db.begin() as conn:
        conn.execute(text("UPDATE ledger_audit_log SET reason = 'tampered' WHERE id = 3"))
    assert auditor.audit_log_chain()["chain_ok"] is False


def test_c6_dry_run_rolls_back(auditor, db):
    before = auditor.scan()
    stats = auditor.heal(before, dry_run=True)
    assert stats["applied"] == 0 and stats["attempted"] == len(before)
    assert len(auditor.scan()) == len(before)
    assert auditor.audit_log_chain()["entries"] == 0


# ─────────────────────────── C7 负控：健康账本零违反 ───────────────────────────


def test_c7_negative_control_healthy_ledger(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path/'healthy.db'}")
    with eng.begin() as conn:
        for ddl in SQLITE_DDL:
            conn.execute(text(ddl))
        conn.execute(text("INSERT INTO campaign_plans (id, organization_id, name) "
                          "VALUES (1, 1, 'healthy-plan')"))
        conn.execute(text("INSERT INTO campaign_milestones (id, plan_id, name, dependencies)"
                          " VALUES (1, 1, 'ms1', '[]')"))
        conn.execute(text("INSERT INTO tensor_dev_missions (id, organization_id, goal, repo,"
                          " result) VALUES (1, 1, 'g', 'Repo', '{\"report_id\": 1}')"))
        conn.execute(text("INSERT INTO campaign_nodes (id, plan_id, milestone_id, mission_id,"
                          " name, dependencies) VALUES (1, 1, 1, 1, 'n1', '[]')"))
        conn.execute(text("INSERT INTO tensor_reports (id, organization_id, title, mission_id)"
                          " VALUES (1, 1, 'r1', 1)"))
    auditor = LedgerAuditor(eng, run_id="negative-control")
    report = auditor.run()
    assert report["scan"]["total"] == 0
    assert report["heal"]["applied"] == 0
    assert report["verify"]["total"] == 0
    assert report["report_debt"]["before"]["orphan_reports"] == 0
    assert report["audit_log"]["entries"] == 0
    assert report["fk_coverage"]["uncovered"] == []
