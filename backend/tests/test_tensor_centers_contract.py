"""四中心最小视图（PR-10 首期）后端契约 + E2E + SQL 对拍。

判据（先于实现，机器可裁）
--------------------------
E1 四页可访问：四个中心端点各自 HTTP 200，返回统一载荷契约
   （center / metrics[] / not_implemented[] / definitions / provenance）。
E2 零假数据（数字对拍）：``CROSSCHECK`` 里每一项指标都由**独立手写 SQL** 在
   同一份夹具上重算一遍，端点值必须与之逐项相等（判据项数 ≥ 6，实测见
   ``test_crosscheck_matrix_is_large_enough``）。
E3 域隔离：他 org 的任务/报告/增量不得出现在本 org 的载荷里。
E4 未实现面如实标注：缺表（质检结果面）→ ``status=not_implemented`` 且 value=None，
   不许写 0 冒充实测；创新实体 → ``implemented=False`` + 三条 not_implemented。
E5 质量门态是**算出来的**，不是写的：门内计数变化必须翻转门状态
   （阳性 → 阴性对照，证 E5 非恒真摆设）。

全程离线、内存 sqlite、合成域名（solver-alpha / agent-1 之类），不含任何领域词。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from api.deps import require_auth                      # noqa: E402
from api.tensor_centers import router as centers_router  # noqa: E402
from database.config import get_db                     # noqa: E402

ORG = 1
OTHER_ORG = 2
ADMIN = {"id": 9, "username": "centers-admin", "role": "ADMIN", "organization_id": ORG}

# --------------------------------------------------------------------------- #
# 合成夹具：两张 org 的组织面（各中心最小可判单元），域名全部合成
# --------------------------------------------------------------------------- #
SCHEMA = [
    "CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT)",
    "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, role TEXT, organization_id INTEGER)",
    """CREATE TABLE tensor_dev_missions (
        id INTEGER PRIMARY KEY, organization_id INTEGER, repo TEXT, goal TEXT, status TEXT,
        result TEXT, report_owed INTEGER, created_at TEXT)""",
    "CREATE TABLE tensor_agent_sessions (id INTEGER PRIMARY KEY, organization_id INTEGER, agent_name TEXT, status TEXT)",
    "CREATE TABLE tensor_approval_requests (id INTEGER PRIMARY KEY, organization_id INTEGER, kind TEXT, reason TEXT, status TEXT)",
    "CREATE TABLE tensor_executors (executor_id TEXT PRIMARY KEY, hostname TEXT, heartbeat_at TEXT)",
    "CREATE TABLE campaign_plans (id INTEGER PRIMARY KEY, organization_id INTEGER, name TEXT, status TEXT)",
    """CREATE TABLE campaign_nodes (
        id INTEGER PRIMARY KEY, plan_id INTEGER, milestone_id INTEGER,
        name TEXT, node_type TEXT, status TEXT, block_reason TEXT, consecutive_fails INTEGER,
        reproduction_verdict TEXT)""",
    "CREATE TABLE campaign_milestones (id INTEGER PRIMARY KEY, plan_id INTEGER, name TEXT, criteria TEXT, status TEXT)",
    """CREATE TABLE tensor_reports (
        id INTEGER PRIMARY KEY, organization_id INTEGER, title TEXT, status TEXT, qa_status TEXT,
        qa_rejection_reason TEXT, qa_checked_at TEXT, created_at TEXT)""",
    """CREATE TABLE tensor_capability_deltas (
        id INTEGER PRIMARY KEY, organization_id INTEGER, mission_id INTEGER, category TEXT,
        summary TEXT, reviewer TEXT, created_at TEXT)""",
    "CREATE TABLE tensor_repro_verdicts (id INTEGER PRIMARY KEY, organization_id INTEGER, mission_id INTEGER, verdict TEXT)",
    # quality_check_results 故意不建：走“缺表 → 未实现”容错分支（E4）
]

SEED = [
    "INSERT INTO organizations (id, name) VALUES (1, 'OrgAlpha'), (2, 'OrgBeta')",
    """INSERT INTO tensor_dev_missions (id, organization_id, repo, goal, status, result, report_owed, created_at) VALUES
       (1, 1, 'solver-alpha', 'goal-a1', 'succeeded', '{"headline":"h1"}', 1, '2026-01-01T00:00:00'),
       (2, 1, 'solver-beta',  'goal-a2', 'succeeded', '{"headline":"h2"}', 0, '2026-01-02T00:00:00'),
       (3, 1, 'kernel-gamma', 'goal-a3', 'failed',    NULL,                 1, '2026-01-03T00:00:00'),
       (4, 1, 'solver-alpha', 'goal-a4', 'running',   NULL,                 0, '2026-01-04T00:00:00'),
       (5, 1, 'solver-beta',  'goal-a5', 'succeeded', NULL,                 0, '2026-01-05T00:00:00'),
       (6, 2, 'solver-other', 'goal-b1', 'succeeded', NULL,                 0, '2026-01-06T00:00:00')""",
    """INSERT INTO tensor_agent_sessions (id, organization_id, agent_name, status) VALUES
       (1, 1, 'agent-1', 'active'), (2, 1, 'agent-2', 'completed'), (3, 1, 'agent-3', 'completed'),
       (4, 2, 'agent-x', 'active')""",
    """INSERT INTO tensor_approval_requests (id, organization_id, kind, reason, status) VALUES
       (1, 1, 'stage-gate', 'r1', 'pending'), (2, 1, 'stage-gate', 'r2', 'pending'),
       (3, 1, 'stage-gate', 'r3', 'approved'), (4, 2, 'stage-gate', 'rx', 'pending')""",
    """INSERT INTO tensor_executors (executor_id, hostname, heartbeat_at) VALUES
       ('exec-1', 'node-a', '2026-01-06T00:00:00'), ('exec-2', 'node-b', '2026-01-06T00:00:00')""",
    """INSERT INTO campaign_plans (id, organization_id, name, status) VALUES
       (1, 1, 'plan-alpha', 'active'), (2, 1, 'plan-beta', 'completed'), (3, 2, 'plan-other', 'active')""",
    """INSERT INTO campaign_nodes (id, plan_id, milestone_id, name, node_type, status,
                                 block_reason, consecutive_fails, reproduction_verdict) VALUES
       (1, 1, 1, 'node-1', 'exec', 'done',        NULL,        0, 'reproduced'),
       (2, 1, 1, 'node-2', 'exec', 'done',        NULL,        0, 'reproduced'),
       (3, 1, 2, 'node-3', 'exec', 'done',        NULL,        0, 'pending'),
       (4, 1, 2, 'node-4', 'exec', 'in-flight',   NULL,        1, 'pending'),
       (5, 1, 2, 'node-5', 'exec', 'blocked',     'chain-a',   3, 'failed'),
       (6, 3, 9, 'node-x', 'exec', 'done',        NULL,        0, 'reproduced')""",
    """INSERT INTO campaign_milestones (id, plan_id, name, criteria, status) VALUES
       (1, 1, 'gate-1', 'c1', 'done'), (2, 1, 'gate-2', 'c2', 'done'),
       (3, 1, 'gate-3', 'c3', 'in-flight'), (4, 3, 'gate-x', 'cx', 'done')""",
    """INSERT INTO tensor_reports (id, organization_id, title, status, qa_status, qa_rejection_reason,
                                  qa_checked_at, created_at) VALUES
       (1, 1, 'report-1', 'published', 'pending', NULL, NULL, '2026-01-01T00:00:00'),
       (2, 1, 'report-2', 'published', 'pending', NULL, NULL, '2026-01-02T00:00:00'),
       (3, 1, 'report-3', 'published', 'passed',  NULL, '2026-01-03T00:00:00', '2026-01-03T00:00:00'),
       (4, 1, 'report-4', 'final',     'rejected', 'reason-4', '2026-01-04T00:00:00', '2026-01-04T00:00:00'),
       (5, 1, 'report-5', 'returned',  NULL,      NULL, NULL, '2026-01-05T00:00:00'),
       (6, 2, 'report-x', 'published', 'pending', NULL, NULL, '2026-01-06T00:00:00')""",
    """INSERT INTO tensor_capability_deltas (id, organization_id, mission_id, category, summary, reviewer, created_at) VALUES
       (1, 1, 1, 'new_capability',      'delta-1', 'verifier', '2026-01-01T00:00:00'),
       (2, 1, 2, 'new_capability',      'delta-2', 'verifier', '2026-01-02T00:00:00'),
       (3, 1, 3, 'defect_fix',          'delta-3', 'verifier', '2026-01-03T00:00:00'),
       (4, 1, 5, 'reusable_knowledge',  'delta-4', 'verifier', '2026-01-04T00:00:00'),
       (5, 1, 4, 'no_substantive_delta','delta-5', 'verifier', '2026-01-05T00:00:00'),
       (6, 2, 6, 'new_capability',      'delta-x', 'verifier', '2026-01-06T00:00:00')""",
    # org1 无复现裁定（复现裁定门 → 未实现）；org2 有一条（隔离用）
    "INSERT INTO tensor_repro_verdicts (id, organization_id, mission_id, verdict) VALUES (1, 2, 6, 'reproduced')",
]

# StaticPool + check_same_thread=False：TestClient 在工作线程里跑端点，
# 内存库必须跨线程共享同一条连接。
ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture(scope="module")
def conn():
    with ENGINE.begin() as c:
        for ddl in SCHEMA:
            c.execute(text(ddl))
        for stmt in SEED:
            c.execute(text(stmt))
    yield ENGINE


@pytest.fixture()
def client(conn):
    Session = sessionmaker(bind=conn, future=True)
    app = FastAPI()
    app.include_router(centers_router)
    app.dependency_overrides[require_auth] = lambda: dict(ADMIN)
    app.dependency_overrides[get_db] = lambda: Session()
    return TestClient(app)


def _scalar(conn, sql: str):
    with conn.connect() as c:
        return c.execute(text(sql)).scalar()


def _fetch(client, center: str) -> dict:
    resp = client.get(f"/tensor/centers/{center}")
    assert resp.status_code == 200, f"{center}: {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body["center"] == center
    assert body["schema_version"]
    assert body["generated_at"]
    assert isinstance(body["metrics"], list) and body["metrics"], "metrics 不得为空"
    for m in body["metrics"]:
        # 每项指标必须自带口径（source）——页面靠它显示审计链
        assert {"key", "label", "value", "source", "status"} <= set(m), m
        assert m["source"].strip(), m
        assert m["status"] in {"ok", "not_implemented"}, m
    return body


def _metric(body: dict, key: str) -> dict:
    for m in body["metrics"]:
        if m["key"] == key:
            return m
    raise AssertionError(f"metric {key} 不在载荷中：{[m['key'] for m in body['metrics']]}")


# --------------------------------------------------------------------------- #
# E2 对拍矩阵：每项 = (中心, metric_key, 独立手写 SQL 或 (分子, 分母))
# --------------------------------------------------------------------------- #
CROSSCHECK: list[tuple[str, str, object]] = [
    # —— Organization ——
    ("organization", "missions_total",
     "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = 1"),
    ("organization", "missions_succeeded",
     "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = 1 AND status = 'succeeded'"),
    ("organization", "missions_failed",
     "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = 1 AND status = 'failed'"),
    ("organization", "missions_running",
     "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = 1 AND status = 'running'"),
    ("organization", "sessions_total",
     "SELECT count(*) FROM tensor_agent_sessions WHERE organization_id = 1"),
    ("organization", "sessions_active",
     "SELECT count(*) FROM tensor_agent_sessions WHERE organization_id = 1 AND status = 'active'"),
    ("organization", "executors_registered", "SELECT count(*) FROM tensor_executors"),
    ("organization", "approvals_pending",
     "SELECT count(*) FROM tensor_approval_requests WHERE organization_id = 1 AND status = 'pending'"),
    ("organization", "campaign_plans_total",
     "SELECT count(*) FROM campaign_plans WHERE organization_id = 1"),
    ("organization", "campaign_nodes_total",
     "SELECT count(*) FROM campaign_nodes n JOIN campaign_plans p ON p.id=n.plan_id WHERE p.organization_id = 1"),
    ("organization", "campaign_nodes_in_flight",
     "SELECT count(*) FROM campaign_nodes n JOIN campaign_plans p ON p.id=n.plan_id WHERE p.organization_id = 1 AND n.status = 'in-flight'"),
    ("organization", "campaign_nodes_blocked",
     "SELECT count(*) FROM campaign_nodes n JOIN campaign_plans p ON p.id=n.plan_id WHERE p.organization_id = 1 AND n.status = 'blocked'"),
    ("organization", "campaign_nodes_done",
     "SELECT count(*) FROM campaign_nodes n JOIN campaign_plans p ON p.id=n.plan_id WHERE p.organization_id = 1 AND n.status = 'done'"),
    ("organization", "campaign_milestones_total",
     "SELECT count(*) FROM campaign_milestones m JOIN campaign_plans p ON p.id=m.plan_id WHERE p.organization_id = 1"),
    ("organization", "campaign_milestones_done",
     "SELECT count(*) FROM campaign_milestones m JOIN campaign_plans p ON p.id=m.plan_id WHERE p.organization_id = 1 AND m.status = 'done'"),
    # —— Quality ——
    ("quality", "reports_total", "SELECT count(*) FROM tensor_reports WHERE organization_id = 1"),
    ("quality", "reports_qa_pending",
     "SELECT count(*) FROM tensor_reports WHERE organization_id = 1 AND qa_status = 'pending'"),
    ("quality", "reports_qa_passed",
     "SELECT count(*) FROM tensor_reports WHERE organization_id = 1 AND qa_status = 'passed'"),
    ("quality", "reports_qa_rejected",
     "SELECT count(*) FROM tensor_reports WHERE organization_id = 1 AND qa_status = 'rejected'"),
    ("quality", "reports_qa_undecided",
     "SELECT count(*) FROM tensor_reports WHERE organization_id = 1 AND qa_status IS NULL"),
    ("quality", "reports_qa_pass_rate",
     ("SELECT count(*) FROM tensor_reports WHERE organization_id = 1 AND qa_status = 'passed'",
      "SELECT count(*) FROM tensor_reports WHERE organization_id = 1 AND qa_status IN ('passed','rejected')")),
    ("quality", "missions_report_owed",
     "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = 1 AND report_owed = 1"),
    ("quality", "missions_report_settled",
     "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = 1 AND report_owed = 0"),
    ("quality", "repro_verdicts",
     "SELECT count(*) FROM tensor_repro_verdicts WHERE organization_id = 1"),
    # —— Capability ——
    ("capability", "deltas_total",
     "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = 1"),
    ("capability", "substantive_deltas",
     "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = 1 "
     "AND category <> 'no_substantive_delta'"),
    ("capability", "deltas_new_capability",
     "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = 1 AND category = 'new_capability'"),
    ("capability", "deltas_reusable_knowledge",
     "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = 1 AND category = 'reusable_knowledge'"),
    ("capability", "succeeded_missions",
     "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = 1 AND status = 'succeeded'"),
    ("capability", "covered_succeeded",
     "SELECT count(DISTINCT d.mission_id) FROM tensor_capability_deltas d "
     "JOIN tensor_dev_missions m ON m.id = d.mission_id "
     "WHERE d.organization_id = 1 AND m.status = 'succeeded'"),
    ("capability", "coverage_ratio",
     ("SELECT count(DISTINCT d.mission_id) FROM tensor_capability_deltas d "
      "JOIN tensor_dev_missions m ON m.id = d.mission_id "
      "WHERE d.organization_id = 1 AND m.status = 'succeeded'",
      "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = 1 AND status = 'succeeded'")),
    # —— Innovation（代理面） ——
    ("innovation", "knowledge_deltas",
     "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = 1 "
     "AND category IN ('new_capability','capability_extension','reusable_knowledge')"),
    ("innovation", "capability_deltas_total",
     "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = 1"),
    ("innovation", "absorption_rate",
     ("SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = 1 "
      "AND category IN ('new_capability','capability_extension','reusable_knowledge')",
      "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = 1")),
    ("innovation", "defect_fix_deltas",
     "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = 1 AND category = 'defect_fix'"),
    ("innovation", "falsified_hypothesis_deltas",
     "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = 1 "
     "AND category = 'falsified_hypothesis'"),
]


def test_crosscheck_matrix_is_large_enough():
    """判据项数下限：任务书要求抽查 ≥ 6 项，这里给全量矩阵。"""
    assert len(CROSSCHECK) >= 6
    assert {c for c, _, _ in CROSSCHECK} == {"organization", "quality", "capability", "innovation"}
    # 四个中心每个至少 5 项
    for center in ("organization", "quality", "capability", "innovation"):
        n = sum(1 for c, _, _ in CROSSCHECK if c == center)
        assert n >= 5, f"{center} 对拍项只有 {n} 项"


@pytest.mark.parametrize("center,key,expected_sql", CROSSCHECK,
                         ids=[f"{c}:{k}" for c, k, _ in CROSSCHECK])
def test_metric_matches_independent_sql(client, conn, center, key, expected_sql):
    """E2：端点值 == 独立手写 SQL 重算值（零假数据）。"""
    body = _fetch(client, center)
    got = _metric(body, key)["value"]
    if isinstance(expected_sql, tuple):
        num_sql, den_sql = expected_sql
        num, den = _scalar(conn, num_sql), _scalar(conn, den_sql)
        expected = round(num / den, 4) if den else None
    else:
        expected = _scalar(conn, expected_sql)
    assert got == expected, f"{center}.{key}: 端点 {got} != SQL {expected}"


# --------------------------------------------------------------------------- #
# E1 四页可访问（E2E：HTTP 层，四个端点各一条）
# --------------------------------------------------------------------------- #
def test_e2e_organization_center(client):
    body = _fetch(client, "organization")
    lists = body["lists"]
    assert body["implemented"] is True
    assert {n["name"] for n in lists["blocked_nodes"]} == {"node-5"}
    assert lists["blocked_nodes"][0]["block_reason"] == "chain-a"
    assert {n["name"] for n in lists["inflight_nodes"]} == {"node-4"}
    # 域隔离：他 org 的任务/节点不得出现
    repos = {m["repo"] for m in lists["recent_missions"]}
    assert "solver-other" not in repos
    assert all(n["plan_id"] == 1 for n in lists["blocked_nodes"] + lists["inflight_nodes"])
    assert body["provenance"]["data_origin"] == "platform_sql"
    assert body["not_implemented"] == []


def test_e2e_quality_center(client):
    body = _fetch(client, "quality")
    gates = {g["key"]: g for g in body["gates"]}
    assert set(gates) == {"report_qa_gate", "report_delivery_gate",
                          "campaign_milestone_gate", "repro_verdict_gate"}
    assert gates["report_qa_gate"]["state"] == "有积压"
    assert gates["report_delivery_gate"]["state"] == "有欠账"
    assert gates["campaign_milestone_gate"]["state"] == "在验"
    assert gates["repro_verdict_gate"]["state"] == "未实现"
    # 评价流队列只含本 org 的 pending 报告
    assert {r["title"] for r in body["lists"]["qa_queue"]} == {"report-1", "report-2"}
    assert {r["title"] for r in body["lists"]["recent_decided"]} == {"report-3", "report-4"}
    # 缺表 → 未实现（不写 0 冒充）
    quality_face = _metric(body, "quality_rule_checks")
    assert quality_face["status"] == "not_implemented" and quality_face["value"] is None
    assert [n["key"] for n in body["not_implemented"]] == ["quality_rule_checks"]


def test_e2e_capability_center(client):
    body = _fetch(client, "capability")
    assert body["implemented"] is True
    assert body["lists"]["by_category"] == {
        "new_capability": 2, "capability_extension": 0, "defect_fix": 1,
        "reusable_knowledge": 1, "falsified_hypothesis": 0, "no_substantive_delta": 1,
    }
    recent = body["lists"]["recent_deltas"]
    assert [d["id"] for d in recent] == [5, 4, 3, 2, 1]      # 新到旧
    assert recent[0]["mission_title"] == "goal-a4"           # 无 headline → 回落 goal
    assert recent[3]["mission_title"] == "h2"                # 有 headline → 优先 headline


def test_e2e_innovation_center(client):
    body = _fetch(client, "innovation")
    assert body["implemented"] is False
    assert {n["key"] for n in body["not_implemented"]} == {
        "proposal_intake", "autonomous_exploration", "innovation_output_ledger"}
    proposal = _metric(body, "innovation_proposals_total")
    assert proposal["status"] == "not_implemented" and proposal["value"] is None
    assert _metric(body, "knowledge_deltas")["value"] == 3
    assert _metric(body, "absorption_rate")["value"] == 0.6
    assert "吸收率" in body["definitions"]["absorption_rate"]
    assert {d["id"] for d in body["lists"]["absorption_evidence"]} == {1, 2, 4}


# --------------------------------------------------------------------------- #
# E5 阴性对照：门状态必须随门内计数翻转（证 E5 非恒真）
# --------------------------------------------------------------------------- #
def test_gate_state_flips_when_queue_drains(conn):
    """同一条门，注掉 pending 报告后必须从「有积压」翻成「畅通」——反向断言。"""
    Session = sessionmaker(bind=conn, future=True)
    db = Session()
    from api.tensor_centers import quality_center

    before = quality_center(db=db, user=dict(ADMIN))
    state_before = {g["key"]: g["state"] for g in before["gates"]}
    assert state_before["report_qa_gate"] == "有积压"

    with ENGINE.begin() as c:
        c.execute(text("UPDATE tensor_reports SET qa_status = 'passed' "
                       "WHERE organization_id = 1 AND qa_status = 'pending'"))
    try:
        after = quality_center(db=Session(), user=dict(ADMIN))
        state_after = {g["key"]: g["state"] for g in after["gates"]}
        assert state_after["report_qa_gate"] == "畅通", state_after
        assert _metric(after, "reports_qa_pending")["value"] == 0
        assert _metric(after, "reports_qa_passed")["value"] == 3
    finally:
        with ENGINE.begin() as c:   # 恢复夹具，保证用例序无关
            c.execute(text("UPDATE tensor_reports SET qa_status = 'pending' "
                           "WHERE organization_id = 1 AND id IN (1, 2)"))
    db.close()