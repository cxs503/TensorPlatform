#!/usr/bin/env python3
"""``tools/blocked_node_triage.py`` 的合成夹具测试。

纪律：**零领域词条** —— 节点名/原因码前缀之外的文本全部是合成词（``node-A``、
``synthetic gate`` 之类），域内容只经 DB 注入，绝不进代码/测试。

覆盖六面：
1. 分类表与边界（前缀、大小写、多空白、None、不可识别）；
2. 覆盖度（各类之和 == blocked 总数，无未分类残留）；
3. 决策映射（requeue / stay_blocked / never）；
4. 复活预算与批次上限；
5. dry-run 只读（夹具快照前后逐字节相等、零写调用）；
6. 事件写入正确性 + 幂等（二次 apply 决策数 = 0）。

另有一条**真 PG 语法/语义回路**（``test_pg_scratch_apply_rollback``）：在临时 schema 里
建同构表、灌合成夹具、执行本模块生成的 apply SQL、断言状态翻转与事件 detail，
最后 ``ROLLBACK``——本机没有 psql/PG 时自动 skip，绝不碰生产行。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import blocked_node_triage as T  # noqa: E402

TOOL_PATH = REPO_ROOT / "tools" / "blocked_node_triage.py"


# ── 夹具 ────────────────────────────────────────────────────────────────────
def node(nid: int, reason, *, plan_id: int = 7, ext: str = "", revivals: int = 0,
         retries: int = 4, name: str = "") -> dict:
    return {
        "id": nid,
        "plan_id": plan_id,
        "node": ext or f"node-{nid}",
        "name": name or f"synthetic node {nid}",
        "block_reason": reason,
        "infra_retries": retries,
        "infra_revivals": revivals,
        "event_revivals": 0,
    }


FIXTURE_ROWS = [
    node(101, "infra x5 (synthetic_lost)"),
    node(102, "infra x5 (synthetic_relay)"),
    node(103, "mission failed x2"),
    node(104, "mission stopped x2"),
    node(105, "hollow success x2"),
    node(106, "synthetic gate: manual release required"),
    node(107, None),
    node(108, "   INFRA   X5   (synthetic_case)  "),
]


class FakeStore(T.Store):
    """内存 store：语义与 PG 版一致（含 append-only 事件表与快照）。"""

    name = "fake"

    def __init__(self, rows, events=None):
        self.nodes = {}
        for r in rows:
            rec = dict(r)
            rec.setdefault("status", "blocked")
            self.nodes[rec["id"]] = rec
        self.events = list(events or [])
        self.write_log = []

    # -- 内部 ---------------------------------------------------------------
    def _view(self, rec):
        ev = sum(1 for e in self.events
                 if e["event_type"] == T.EVENT_BLOCK_RESOLVED
                 and e["detail"].get("node_id") == rec["id"])
        return dict(rec, event_revivals=ev)

    def snapshot(self) -> str:
        return json.dumps({"nodes": self.nodes, "events": self.events},
                          sort_keys=True, ensure_ascii=False, default=str)

    # -- Store 接口 ---------------------------------------------------------
    def fetch_blocked(self):
        rows = [self._view(r) for r in self.nodes.values() if r["status"] == "blocked"]
        rows.sort(key=lambda r: r["id"])
        return rows, len(rows)

    def blocked_count(self) -> int:
        return sum(1 for r in self.nodes.values() if r["status"] == "blocked")

    def apply(self, plan):
        self.write_log.append({"op": "apply", "selected": plan["selected_count"]})
        applied = 0
        for d in plan["selected_for_requeue"]:
            rec = self.nodes[d["id"]]
            if rec["status"] != "blocked":      # 守卫与 SQL 的 WHERE status='blocked' 等价
                continue
            rec["status"] = "pending"
            rec["block_reason"] = None
            rec["infra_revivals"] = d["revival_index"]
            self.events.append({
                "event_type": T.EVENT_BLOCK_RESOLVED,
                "plan_id": d["plan_id"],
                "detail": dict(d["event_detail"]),
            })
            applied += 1
        return {"applied": applied, "sql": T.apply_sql(plan), "output": ""}


# ── 1. 分类表 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("reason,expected", [
    ("infra x5 (synthetic_lost)", T.INFRA_RETRYABLE),
    ("infra x3 (synthetic_relay)", T.INFRA_RETRYABLE),
    ("INFRA X5 (synthetic_case)", T.INFRA_RETRYABLE),
    ("  infra   x5   (  spaced  )", T.INFRA_RETRYABLE),
    ("mission failed x2", T.SUBSTANTIVE_FAIL),
    ("Mission Failed x4", T.SUBSTANTIVE_FAIL),
    ("mission stopped x2", T.STOPPED),
    ("hollow success x2", T.HOLLOW),
    ("synthetic gate: manual release required", T.MANUAL_GATE),
    ("unrecognised-reason", T.MANUAL_GATE),
    ("infra", T.MANUAL_GATE),          # 没有 'x' 数字段：不是本模块认的 infra 形态
    ("mission failure x2", T.MANUAL_GATE),
    ("", T.MANUAL_GATE),
    (None, T.MANUAL_GATE),
])
def test_classify_block_reason(reason, expected):
    assert T.classify_block_reason(reason) == expected


def test_class_table_is_total():
    """任何输入都落在五类之内（分类函数是全集映射，不会返回第六种值）。"""
    probes = [None, "", "infra x1", "mission failed x2", "mission stopped x9",
              "hollow success x2", "something else entirely", "infra x"]
    for probe in probes:
        assert T.classify_block_reason(probe) in T.CLASSES


# ── 2. 覆盖度 ───────────────────────────────────────────────────────────────
def test_coverage_sum_equals_blocked_total():
    plan = T.build_plan(FIXTURE_ROWS, total_blocked=len(FIXTURE_ROWS))
    assert sum(plan["by_class"].values()) == len(FIXTURE_ROWS)
    assert plan["classified_total"] == len(FIXTURE_ROWS)
    assert plan["coverage_ok"] is True
    assert set(plan["by_class"]) == set(T.CLASSES)      # 五类键齐备（含零计数类）


def test_coverage_flags_shortfall():
    """分类数 != 同刻 blocked 计数 → coverage_ok False（供 CLI 以退出码 3 报警）。"""
    plan = T.build_plan(FIXTURE_ROWS[:3], total_blocked=99)
    assert plan["coverage_ok"] is False


def test_every_row_yields_one_decision():
    plan = T.build_plan(FIXTURE_ROWS)
    ids = [d["id"] for d in plan["decisions"]]
    assert sorted(ids) == sorted(r["id"] for r in FIXTURE_ROWS)
    assert all(d["decision"] in (T.REQUEUE, T.STAY_BLOCKED, T.NEVER) for d in plan["decisions"])


# ── 3. 决策映射 ─────────────────────────────────────────────────────────────
def test_decision_mapping_per_class():
    plan = T.build_plan(FIXTURE_ROWS, max_batch=100)
    by_id = {d["id"]: d for d in plan["decisions"]}
    assert by_id[101]["decision"] == T.REQUEUE and by_id[101]["class"] == T.INFRA_RETRYABLE
    assert by_id[102]["decision"] == T.REQUEUE
    assert by_id[108]["decision"] == T.REQUEUE          # 大小写/多空白规整后仍识别
    assert by_id[103]["decision"] == T.STAY_BLOCKED     # 实质失败：只出建议
    assert by_id[104]["decision"] == T.STAY_BLOCKED     # 被叫停：不自动重排
    assert by_id[105]["decision"] == T.STAY_BLOCKED     # 空转成功：不重试
    assert by_id[106]["decision"] == T.NEVER            # 人工门：永不动
    assert by_id[107]["decision"] == T.NEVER            # 无原因：按人工门处理


def test_stay_blocked_and_never_have_no_event_payload():
    plan = T.build_plan(FIXTURE_ROWS, max_batch=100)
    for d in plan["decisions"]:
        if d["decision"] == T.REQUEUE:
            assert d["event_detail"]["cause"] == T.CAUSE_INFRA_RETRY
        else:
            assert "event_detail" not in d
            assert d["revival_index"] is None


# ── 4. 预算与批次 ───────────────────────────────────────────────────────────
def test_revival_budget_blocks_after_max_revivals():
    rows = [node(201, "infra x6 (synthetic)", revivals=2),   # 2 < 3 → 还可复活
            node(202, "infra x6 (synthetic)", revivals=3),   # 3 == 3 → 到顶
            node(203, "infra x6 (synthetic)", revivals=9)]
    plan = T.build_plan(rows, max_revivals=3, max_batch=100)
    by_id = {d["id"]: d for d in plan["decisions"]}
    assert by_id[201]["decision"] == T.REQUEUE and by_id[201]["revival_index"] == 3
    assert by_id[202]["decision"] == T.STAY_BLOCKED
    assert by_id[203]["decision"] == T.STAY_BLOCKED
    assert "budget exhausted" in by_id[202]["reason"]
    assert plan["selected_count"] == 1


def test_revival_budget_from_event_count_is_monotonic():
    """事件计数与 metadata 计数取大者：任一侧丢失都不超发复活。"""
    rows = [dict(node(301, "infra x5 (synthetic)", revivals=0), event_revivals=3)]
    plan = T.build_plan(rows, max_revivals=3, max_batch=10)
    assert plan["decisions"][0]["decision"] == T.STAY_BLOCKED

    rows = [dict(node(302, "infra x5 (synthetic)", revivals=2), event_revivals=0)]
    plan = T.build_plan(rows, max_revivals=3, max_batch=10)
    assert plan["decisions"][0]["revivals"] == 2
    assert plan["decisions"][0]["decision"] == T.REQUEUE


def test_max_batch_is_hard_cap_and_deterministic():
    rows = [node(1000 + i, "infra x5 (synthetic)") for i in range(60)]
    plan = T.build_plan(rows, max_batch=5)
    assert plan["selected_count"] == 5
    assert plan["deferred_by_batch"] == 55
    assert plan["by_decision"][T.REQUEUE] == 5
    assert plan["by_decision"][T.STAY_BLOCKED] == 55
    assert [d["id"] for d in plan["selected_for_requeue"]] == [1000, 1001, 1002, 1003, 1004]
    # 确定性：同样输入两次结果逐键一致
    assert T.apply_sql(plan) == T.apply_sql(T.build_plan(rows, max_batch=5))


def test_negative_budgets_rejected():
    with pytest.raises(ValueError):
        T.build_plan([], max_revivals=-1)
    with pytest.raises(ValueError):
        T.build_plan([], max_batch=-1)


# ── 5. dry-run 只读 ─────────────────────────────────────────────────────────
def test_dry_run_writes_nothing():
    store = FakeStore(FIXTURE_ROWS)
    before = store.snapshot()
    result = T.run(store, apply=False, max_batch=100)
    after = store.snapshot()

    assert before == after                      # 夹具快照前后逐字节相同
    assert store.write_log == []                # 零写调用
    assert result["mode"] == "dry-run"
    assert result["read_only_ok"] is True
    assert result["blocked_before"] == result["blocked_after"] == len(FIXTURE_ROWS)
    assert result["requeued"] == 0
    assert "applied_sql" not in result


def test_transport_query_rejects_write_sql():
    """只读闸：query() 遇到非 SELECT 直接抛 ReadOnlyViolation（不靠约定靠断言）。"""

    class Recorder(T.Transport):
        name = "recorder"

        def __init__(self):
            self.calls = []

        def query(self, sql):
            self.calls.append(sql)
            return []

        def execute(self, sql):
            raise AssertionError("execute() must not be called on a read-only path")

    tr = T.PsqlTransport(db="synthetic_db", password="x")  # 构造不触碰网络
    with pytest.raises(T.ReadOnlyViolation):
        tr.query("UPDATE campaign_nodes SET status='pending'")
    assert T._is_select("SELECT 1") and T._is_select("  with x as (select 1) select * from x")
    assert not T._is_select("DELETE FROM campaign_nodes")


# ── 6. apply：事件写入正确性 + 幂等 ─────────────────────────────────────────
def test_apply_requeues_only_infra_and_writes_events():
    store = FakeStore(FIXTURE_ROWS)
    result = T.run(store, apply=True, max_batch=100, max_revivals=3)

    assert result["mode"] == "apply"
    assert result["requeued"] == 3                      # 101 / 102 / 108
    assert result["blocked_before"] == len(FIXTURE_ROWS)
    assert result["blocked_after"] == len(FIXTURE_ROWS) - 3

    statuses = {nid: rec["status"] for nid, rec in store.nodes.items()}
    assert statuses[101] == statuses[102] == statuses[108] == "pending"
    for nid in (103, 104, 105, 106, 107):               # 其余一律不动
        assert statuses[nid] == "blocked"
    assert store.nodes[106]["block_reason"].startswith("synthetic gate")  # 原文保留

    events = [e for e in store.events if e["event_type"] == T.EVENT_BLOCK_RESOLVED]
    assert len(events) == 3
    for ev in events:
        d = ev["detail"]
        assert d["from"] == "blocked" and d["to"] == "pending"
        assert d["cause"] == "infra_retry"
        assert isinstance(d["revival"], int) and d["revival"] >= 1
        assert d["node_id"] in (101, 102, 108)
        assert d["node"].startswith("node-")            # 审计字段：节点标识
        assert T.classify_block_reason(d["block_reason"]) == T.INFRA_RETRYABLE
        assert ev["plan_id"] == 7
    assert {e["detail"]["node_id"] for e in events} == {101, 102, 108}
    assert all(e["detail"]["revival"] == 1 for e in events)   # 首次复活 = 1


def test_apply_is_idempotent():
    store = FakeStore(FIXTURE_ROWS)
    first = T.run(store, apply=True, max_batch=100)
    assert first["requeued"] == 3

    second = T.run(store, apply=True, max_batch=100)
    assert second["selected_count"] == 0                # 二次 apply 决策数 = 0
    assert second["requeued"] == 0
    assert second["blocked_before"] == second["blocked_after"] == len(FIXTURE_ROWS) - 3
    assert len([e for e in store.events if e["event_type"] == T.EVENT_BLOCK_RESOLVED]) == 3
    assert T.apply_sql(T.build_plan(store.fetch_blocked()[0])) == ""   # 无可写语句


def test_apply_respects_batch_across_rounds():
    """批次上限分轮消费：两轮各 3 → 共 6，第三轮无候选。"""
    rows = [node(500 + i, "infra x5 (synthetic)") for i in range(6)]
    store = FakeStore(rows)
    r1 = T.run(store, apply=True, max_batch=3)
    r2 = T.run(store, apply=True, max_batch=3)
    r3 = T.run(store, apply=True, max_batch=3)
    assert (r1["requeued"], r2["requeued"], r3["requeued"]) == (3, 3, 0)
    assert r1["deferred_by_batch"] == 3 and r2["deferred_by_batch"] == 0
    assert store.blocked_count() == 0


def test_apply_never_touches_manual_gate_or_substantive():
    rows = [node(601, "synthetic gate: owner release required"),
            node(602, "mission failed x2"),
            node(603, "mission stopped x2"),
            node(604, "hollow success x2")]
    store = FakeStore(rows)
    result = T.run(store, apply=True, max_batch=100)
    assert result["requeued"] == 0
    assert store.blocked_count() == 4
    assert store.events == []
    assert result["by_decision"] == {T.REQUEUE: 0, T.STAY_BLOCKED: 3, T.NEVER: 1}


# ── 7. SQL 渲染 ─────────────────────────────────────────────────────────────
def test_requeue_sql_has_guard_and_contract_literals():
    plan = T.build_plan([node(701, "infra x5 (synthetic's quote)")], max_batch=10)
    sql = T.apply_sql(plan)
    assert sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")
    assert "AND status = 'blocked'" in sql                 # 并发守卫
    assert "'block_resolved'" in sql
    assert "'infra_retry'" in sql
    assert "infra_revivals" in sql
    assert "jsonb_build_object(" in sql
    assert "synthetic''s quote" in sql                     # 单引号双写转义
    assert "to_jsonb(1::int)" in sql                       # revival 序号 = revivals+1
    assert T.apply_sql(plan, wrap_transaction=False).startswith("WITH upd AS")


def test_empty_plan_renders_no_sql():
    plan = T.build_plan([node(801, "mission failed x2")])
    assert T.apply_sql(plan) == ""


# ── 8. CLI 契约（A6）───────────────────────────────────────────────────────
def test_cli_help_exit_code_and_flags():
    proc = subprocess.run([sys.executable, str(TOOL_PATH), "--help"],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0
    for flag in ("--dry-run", "--apply", "--max-revivals", "--max-batch"):
        assert flag in proc.stdout


# ── 9. 真 PG 回路（临时 schema，跑完 ROLLBACK；无 psql 则 skip）─────────────
def _pg_available() -> bool:
    if not shutil.which("psql"):
        return False
    script = "SELECT 1;"
    try:
        proc = subprocess.run(["su", "postgres", "-c", "psql -tA -c 'SELECT 1'"],
                              capture_output=True, text=True, timeout=20)
        return proc.returncode == 0
    except Exception:  # pragma: no cover - 环境缺件
        return False


@pytest.mark.skipif(not _pg_available(), reason="psql/local PG not available")
def test_pg_scratch_apply_rollback():
    """生成的 apply SQL 在真 PG 上跑：状态翻转 + 事件 detail + 二次执行零影响，最后 ROLLBACK。"""
    schema = "triage_scratch_" + uuid.uuid4().hex[:12]
    plan = T.build_plan([node(9001, "infra x5 (synthetic_lost)"),
                         node(9002, "synthetic gate: manual")],
                        max_batch=10, max_revivals=3)
    apply_stmt = T.apply_sql(plan, wrap_transaction=False)

    script = "\n".join([
        "BEGIN;",
        f"CREATE SCHEMA {schema};",
        f"SET LOCAL search_path TO {schema};",
        "CREATE TABLE campaign_nodes (id bigint PRIMARY KEY, plan_id bigint NOT NULL,"
        " status text NOT NULL, mission_id bigint, block_reason text, actual_end timestamptz,"
        " updated_at timestamptz NOT NULL DEFAULT now(), metadata jsonb NOT NULL DEFAULT '{}'::jsonb);",
        "CREATE TABLE campaign_plans (id bigint PRIMARY KEY, organization_id bigint);",
        "CREATE TABLE campaign_events (id bigserial PRIMARY KEY, organization_id bigint NOT NULL,"
        " plan_id bigint NOT NULL, event_type text NOT NULL, detail jsonb NOT NULL DEFAULT '{}'::jsonb);",
        "INSERT INTO campaign_plans (id, organization_id) VALUES (77, 4242);",
        "INSERT INTO campaign_nodes (id, plan_id, status, block_reason) VALUES"
        " (9001, 77, 'blocked', 'infra x5 (synthetic_lost)'),"
        " (9002, 77, 'blocked', 'synthetic gate: manual');",
        apply_stmt,
        apply_stmt,      # 二次执行：守卫使 UPDATE 命中 0 行 → 不写第二条事件
        # 服务端先转 text 并去掉格式换行：psql 会给 json 加换行，客户端按行取结果
        "SELECT replace(json_agg(t)::text, E'\\n', '') FROM ("
        "  SELECT id, status, block_reason, metadata->>'infra_revivals' AS rev FROM campaign_nodes"
        "   ORDER BY id) t;",
        "SELECT replace(json_agg(t)::text, E'\\n', '') FROM ("
        "  SELECT event_type, plan_id, organization_id, detail FROM campaign_events ORDER BY id) t;",
        "ROLLBACK;",
    ]) + "\n"

    tr = T.PsqlTransport()
    out = tr.execute(script)
    # psql -tA 也会打印命令标签（BEGIN/CREATE TABLE/INSERT 0 1），只取 JSON 结果行
    payloads = [ln for ln in out.splitlines() if ln.lstrip().startswith("[")]
    assert len(payloads) == 2, f"unexpected psql output: {out[:400]!r}"
    nodes_json = json.loads(payloads[0])
    events_json = json.loads(payloads[1])

    by_id = {r["id"]: r for r in nodes_json}
    assert by_id[9001]["status"] == "pending"
    assert by_id[9001]["block_reason"] is None
    assert by_id[9001]["rev"] == "1"                    # metadata 复活计数落库
    assert by_id[9002]["status"] == "blocked"           # 人工门一字未动

    assert len(events_json) == 1                        # 二次执行未重复记账
    ev = events_json[0]
    assert ev["event_type"] == "block_resolved"
    assert ev["plan_id"] == 77 and ev["organization_id"] == 4242
    detail = ev["detail"]
    assert (detail["from"], detail["to"], detail["cause"]) == ("blocked", "pending", "infra_retry")
    assert detail["revival"] == 1
    assert detail["node_id"] == 9001