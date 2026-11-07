#!/usr/bin/env python3
"""coordinator_shadow v1 —— ``campaign_blocked`` 口径回归测试（诊断单 7a90c537e3805175）。

影子在 2026-11-07T11:00 读到 ``campaign_blocked = 11 ≥ warn 5``，连续 3 拍同签不消。
复算生产库得：

    SELECT status, count(*) FROM campaign_nodes GROUP BY status;
    --  done 310 | in-flight 32 | blocked 11 | pending 10

11 个 blocked 里 **8 个是 ``node_type='gate'``（放门停放）**、3 个是 task 级真阻塞：

    gate : 129/144/153/170(artifact_missing) 165(no_criteria_declared) 285 340(等上游+双钥匙)
    task : 67/211(mission failed x2) 260(infra x5 agent_lost)

放门机制（``ops/taskplane/gate_auto_release.py``：``FROM_STATUS='blocked'``、
「判据不满足 → 继续等，绝不写 failed」）决定了 gate 停在 blocked 是**设计态**，不是故障；
只要 ≥5 条战役同时在飞，停放门就常年压线 → 指标永不回落 = 结构性误报。

本文件把修正后的口径钉死：
1. ``campaign_blocked`` 只数任务级真阻塞（``node_type <> 'gate'``）→ 生产形状 11 → 3；
2. 停放门不丢信息：``extra.blocked_total`` / ``extra.gate_held`` 留档；
3. 只有门停放时指标必须是 0（零流器官不该报）；
4. 老 schema（无 ``node_type`` 列）→ ``value=None`` + ``withheld``，**绝不填 0**；
5. 阈值不动（warn 5 / crit 12）：修的是口径，不是阈值。
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.coordinator_shadow import pulse as pu  # noqa: E402
from tools.coordinator_shadow import registry as reg  # noqa: E402

AS_OF = datetime(2026, 11, 7, 11, 0, tzinfo=timezone.utc)

DDL = """
CREATE TABLE campaign_nodes (id INTEGER PRIMARY KEY, status TEXT NOT NULL,
                             node_type TEXT NOT NULL DEFAULT 'task');
"""

#: 生产库 2026-11-07T11:00 的真实形状（status, node_type, 条数）
PROD_SHAPE = (
    ("done", "task", 310),
    ("in-flight", "task", 32),
    ("blocked", "gate", 8),     # 放门停放：artifact_missing / no_criteria_declared / 等上游
    ("blocked", "task", 3),     # 真阻塞：mission failed x2 / infra x5 (agent_lost)
    ("pending", "task", 10),
)


def _db(tmp_path, shape=PROD_SHAPE, *, node_type_column=True):
    path = tmp_path / "nodes.sqlite"
    con = sqlite3.connect(path)
    con.executescript(DDL if node_type_column else
                      "CREATE TABLE campaign_nodes (id INTEGER PRIMARY KEY, status TEXT NOT NULL);")
    nid = 0
    for status, node_type, count in shape:
        for _ in range(count):
            nid += 1
            if node_type_column:
                con.execute("INSERT INTO campaign_nodes (id, status, node_type) VALUES (?,?,?)",
                            (nid, status, node_type))
            else:
                con.execute("INSERT INTO campaign_nodes (id, status) VALUES (?,?)", (nid, status))
    con.commit()
    con.close()
    return path


def _organs(path):
    runner = pu.ReadOnlyRunner(pu.SqliteRunner(path))
    organs, _chronicle, blocked, withheld = pu.collect_organs(runner, as_of=AS_OF)
    return organs, blocked, withheld


def test_prod_shape_counts_task_faults_only(tmp_path):
    """生产形状复现：11 blocked → campaign_blocked=3（<warn 5），门停放另计 8。"""
    organs, blocked, _ = _organs(_db(tmp_path))
    m = organs["campaign_blocked"]
    assert m.value == 3, "只数 task 级真阻塞"
    assert m.extra["blocked_total"] == 11
    assert m.extra["gate_held"] == 8
    assert m.withheld is None
    # 回到阈值内，且没有动阈值（修口径不修阈值）
    spec = reg.spec("campaign_blocked")
    assert (spec.warn, spec.crit) == (5, 12)
    assert m.value < spec.warn
    assert reg.organ_of("campaign_blocked") == "s2_campaign"
    # 脉搏摘要里的原始 blocked 计数不丢（诊断单复算 SQL 仍能对上 11）
    assert blocked["blocked"] == 11
    assert blocked["fault"] == 3
    assert blocked["gate_held"] == 8
    assert blocked["counts"]["blocked"] == 11


def test_gate_parking_alone_is_not_an_alarm(tmp_path):
    """只有放门停放时指标必须是 0：停放是设计态，不是阻塞故障。"""
    shape = (("done", "task", 40), ("blocked", "gate", 6), ("in-flight", "task", 3))
    organs, blocked, _ = _organs(_db(tmp_path, shape))
    m = organs["campaign_blocked"]
    assert m.value == 0
    assert m.extra["blocked_total"] == 6
    assert m.extra["gate_held"] == 6
    assert m.value < reg.spec("campaign_blocked").warn
    assert blocked["fault"] == 0


def test_old_schema_withholds_instead_of_zero(tmp_path):
    """老 schema 缺 node_type：探针失败 → None + withheld，绝不填 0（看不见 ≠ 0）。"""
    organs, blocked, withheld = _organs(_db(tmp_path, node_type_column=False))
    m = organs["campaign_blocked"]
    assert m.value is None
    assert m.withheld == "probe-failed"
    assert any(w.startswith("campaign_blocked:") for w in withheld)
    # 原始 status 计数仍可用（另一条 SQL），但口径指标留白
    assert organs["campaign_nodes"].value == 363
    assert blocked["fault"] is None


def test_evidence_sql_is_about_task_faults(tmp_path):
    """证据链：指标 SQL 必须是「blocked 且非 gate」那条，可离线复算。"""
    organs, _, _ = _organs(_db(tmp_path))
    sql = " ".join(organs["campaign_blocked"].sql.split()).lower()
    assert "campaign_nodes" in sql
    assert "blocked" in sql
    assert "node_type" in sql and "gate" in sql
    # 指标台账/器官名不变，判读侧无需改字段
    assert reg.known_metric("campaign_blocked")