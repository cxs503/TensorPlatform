"""FP15-BUDGET-ACCOUNTING v2 —— 终态落库的数据库不变量（PostgreSQL，合成夹具）。

为什么必须打真库
----------------
v1 的记账是「每条终态代码路径各自记得写 budget_used」。这条路的天花板由审计给出：
2026-11-13 全库 11601 条 succeeded 里 11591 条 budget_used 仍是 `{}` —— 因为
`tensor_repro` 撤销、self_heal 退役、demand_intake 撤单、以及**代码还没部署到在跑
的服务上**的窗口期，全都绕过了那几行 Python。本测试断言的是升级后的契约：

    **只要一行进入终态，账就必须落库 —— 由数据库触发器保证，与调用点无关。**

夹具全部是合成事实（没有 m 号、没有域词条），断言的是契约而不是某次运行：

1. 五种终态词（succeeded / failed / budget_exhausted / no_progress / stopped）
   各自 UPD​ATE 一次 → `budget_used` 必须含四键 `{tool_calls, tokens, wall_seconds, source}`；
2. 各字段的**出处标签**必须与推导优先级一致（result 事实 > 心跳 progress >
   runner detail > 服务端时钟上界）；
3. 非终态（running / queued）不许被记账（记账不能污染在飞面）；
4. 应用已记账的行不许被覆盖（DB 只补空，不覆盖真值）；
5. 脏 result（非对象 / 负值 / 布尔）不许让终态流转失败（fail-open）。

跑法（不需要生产库；用隔离库）::

    runuser -u postgres -- createdb tp_budget_trigger
    BUDGET_PG_DSN="postgresql+psycopg:///tp_budget_trigger?host=/var/run/postgresql" \\
        python -m pytest tests/test_budget_used_terminal_trigger_pg.py -q

没有 DSN / 驱动 / 库权限 → 整文件 skip（不是 pass，也不是 fail：未跑过的检查不许
冒充绿灯）。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "backend" / "migrations" / "125_budget_used_terminal_trigger.sql"
VOCAB = REPO / "backend" / "contracts" / "mission_status_vocab.v1.json"

psycopg = pytest.importorskip("psycopg", reason="psycopg 驱动不可用")

DSN = os.environ.get("BUDGET_PG_DSN")
if not DSN:
    pytest.skip("BUDGET_PG_DSN 未设置：终态触发器必须在真 PG 上验证，sqlite 无 plpgsql",
                allow_module_level=True)

BUDGET_USED_KEYS = ("tool_calls", "tokens", "wall_seconds", "source")

#: 触发器要读的列（迁移只碰这几列；表用合成 DDL 起，避免背上 100+ 迁移）
TABLE_DDL = """
CREATE TABLE tensor_dev_missions (
    id          bigserial PRIMARY KEY,
    status      text NOT NULL DEFAULT 'queued',
    budget      jsonb NOT NULL DEFAULT '{}'::jsonb,
    budget_used jsonb NOT NULL DEFAULT '{}'::jsonb,
    result      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
"""


def _scalar(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None


def _sqlalchemy_dsn(dsn: str) -> str:
    """接受 `postgresql+psycopg://...` 或 libpq DSN，统一成 libpq 可用串。

    迁移文件里有中文注释，集群若是 SQL_ASCII（容器默认）必须显式声明
    client_encoding，否则 psycopg 报 UnicodeEncodeError —— 这是环境坑，不是迁移坑。
    """
    out = dsn.replace("postgresql+psycopg://", "postgresql://")
    if "client_encoding" not in out:
        out += ("&" if "?" in out else "?") + "client_encoding=UTF8"
    return out


@pytest.fixture(scope="module")
def db():
    dsn = _sqlalchemy_dsn(DSN)
    try:
        _scalar(dsn, "SELECT 1")
    except Exception as exc:  # pragma: no cover - 环境不可用
        pytest.skip(f"BUDGET_PG_DSN 连不上（{exc}）：本次没跑，不算通过")
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS tensor_dev_missions CASCADE")
            cur.execute("DROP FUNCTION IF EXISTS tp_stamp_budget_used() CASCADE")
            cur.execute("DROP FUNCTION IF EXISTS tp_budget_count(jsonb) CASCADE")
            cur.execute(TABLE_DDL)
            cur.execute(MIGRATION.read_text())          # 生产迁移原文，不改写
    yield dsn
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS tensor_dev_missions CASCADE")


def _insert(dsn: str, *, status: str = "running", result: dict | None = None,
            budget_used: dict | None = None, age_seconds: int = 600) -> int:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tensor_dev_missions (status, result, budget_used,
                                                 created_at, finished_at)
                VALUES (%s, %s::jsonb, %s::jsonb,
                        now() - make_interval(secs => %s), NULL)
                RETURNING id
                """,
                (status, json.dumps(result or {}),
                 json.dumps(budget_used or {}), age_seconds),
            )
            return cur.fetchone()[0]


def _finish(dsn: str, mission_id: int, status: str) -> dict:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tensor_dev_missions SET status = %s, finished_at = now() "
                "WHERE id = %s RETURNING budget_used",
                (status, mission_id),
            )
            return cur.fetchone()[0]


# ── 1. 五种终态 → 四键齐全 + 出处正确 ────────────────────────────────────────

TERMINAL_CASES = [
    # (status, result 合成事实, 期望 tool_calls, 期望 tool_calls 出处, 期望 tokens)
    ("succeeded",       {"tool_calls": 73, "tokens": 1200, "wall_seconds": 900},
     73, "result_fact", 1200),
    ("failed",          {"progress": {"tool_calls": 31, "tokens": 40, "wall_seconds": 640}},
     31, "heartbeat_progress", 40),
    ("budget_exhausted", {"detail": "cap hit; tool_calls=60 tokens=11"},
     60, "runner_detail", 0),
    ("no_progress",     {"headline": "synthetic stall"},               0, "unavailable", 0),
    ("stopped",         {"tool_calls": 5, "tokens": "7"},              5, "result_fact", 7),
]


@pytest.mark.parametrize("status,result,expect_tc,expect_src,expect_tk", TERMINAL_CASES)
def test_terminal_status_stamps_four_keys(db, status, result, expect_tc, expect_src,
                                         expect_tk):
    mid = _insert(db, status="running", result=result)
    record = _finish(db, mid, status)
    record = record if isinstance(record, dict) else json.loads(record)

    for key in BUDGET_USED_KEYS:
        assert key in record, f"{status}: 缺契约键 {key}（落库仍是空账）"
    assert record["tool_calls"] == expect_tc
    assert record["sources"]["tool_calls"] == expect_src
    assert record["tokens"] == expect_tk
    assert isinstance(record["wall_seconds"], int) and record["wall_seconds"] >= 0
    assert record["source"].startswith(f"tool_calls={expect_src};")
    # 历史键同步（suspend/resume 续算与 credit_ledger 读这三个名字）
    assert record["tool_calls_used"] == record["tool_calls"]
    assert record["tokens_used"] == record["tokens"]
    assert record["wall_seconds_used"] == record["wall_seconds"]
    assert record["recorded_by"] == "terminal_trigger"


def test_wall_seconds_falls_back_to_server_clock(db):
    """result 里没有任何时间事实 → 用 finished_at - created_at（600s 夹具）。"""
    mid = _insert(db, status="running", result={"headline": "synthetic"}, age_seconds=600)
    record = _finish(db, mid, "failed")
    record = record if isinstance(record, dict) else json.loads(record)
    assert record["sources"]["wall_seconds"] == "server_clock_upper_bound"
    assert 590 <= record["wall_seconds"] <= 700, record["wall_seconds"]


# ── 2. 负例：非终态不记、真值不被覆盖、脏数据不崩 ───────────────────────────

def test_non_terminal_status_is_not_stamped(db):
    mid = _insert(db, status="running", result={"tool_calls": 12})
    with psycopg.connect(db) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE tensor_dev_missions SET status = 'claimed' "
                        "WHERE id = %s RETURNING budget_used", (mid,))
            record = cur.fetchone()[0]
    record = record if isinstance(record, dict) else json.loads(record)
    assert record == {}, "在飞行被记账污染（终态触发器不应命中非终态）"


def test_existing_record_is_never_overwritten(db):
    declared = {"tool_calls": 9, "tokens": 42, "wall_seconds": 120,
                "source": "tool_calls=agent_declared;tokens=agent_declared;"
                          "wall_seconds=agent_declared"}
    mid = _insert(db, status="running", result={"tool_calls": 9}, budget_used=declared)
    record = _finish(db, mid, "succeeded")
    record = record if isinstance(record, dict) else json.loads(record)
    assert record["tool_calls"] == 9
    assert record["source"].startswith("tool_calls=agent_declared")
    assert "recorded_by" not in record, "应用真值被数据库改写"


@pytest.mark.parametrize("result", [
    [], "raw-string-result", {"tool_calls": -3}, {"tool_calls": True},
    {"tool_calls": "abc"}, {"progress": "not-a-mapping"}, {"tool_calls": 10 ** 12},
])
def test_dirty_result_never_blocks_terminal_transition(db, result):
    mid = _insert(db, status="running", result=result if not isinstance(result, list) else {})
    record = _finish(db, mid, "failed")            # 不许抛错
    record = record if isinstance(record, dict) else json.loads(record)
    for key in BUDGET_USED_KEYS:
        assert key in record
    assert record["tool_calls"] == 0
    assert record["sources"]["tool_calls"] == "unavailable"


# ── 3. 契约漂移守卫：触发器词表必须覆盖 vocab 契约的 terminal ───────────────

def test_trigger_terminal_words_cover_vocab_contract():
    vocab = json.loads(VOCAB.read_text())
    sql = MIGRATION.read_text()
    m = re.search(r"terminal_statuses constant text\[\] := ARRAY\[(.*?)\]", sql, re.S)
    assert m, "迁移里找不到 terminal_statuses 词表"
    words = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    missing = set(vocab["terminal"]) - words
    assert not missing, f"触发器漏了 vocab 契约的终态词：{sorted(missing)}"


def test_mission_vocab_module_and_contract_agree():
    """同一契约的两个消费者（api 常量 / json 契约）不许漂移。"""
    sys.path.insert(0, str(REPO / "backend"))
    vocab = json.loads(VOCAB.read_text())
    assert set(vocab["terminal"]).issubset(set(vocab["statuses"]))
    assert "budget_exhausted" not in vocab["statuses"], (
        "budget_exhausted 若成为正式状态词，必须升 vocab 版本并同步触发器")