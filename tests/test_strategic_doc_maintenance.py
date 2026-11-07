"""战略文档自动维护 daemon 测试（≥6 用例）。

判据（先于实现，机器可裁）：
1. WORLD 行刷新：新时间戳替换旧 WORLD 行，格式正确。
2. 幂等：同一输入连跑两次，产出内容逐字节相同。
3. 人权线保护：原始人权线内容 MUST NOT 被改写（负例测试）。
4. 断链降级：health endpoint 不可用时不崩，使用本地时间。
5. 断链降级：数据库不可用时不崩，返回空战役态。
6. 战役态查询：SQLite 文件库能正确查询活跃计划/节点统计/最近门。
7. PANORAMA 战役态节刷新：新节替换旧节或追加到末尾。
8. 审计日志：刷新后审计日志文件存在且包含有效 JSON 行。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.strategic_doc_maintenance import (
    compute_content_hash,
    extract_human_rights_line,
    refresh_world_line,
    refresh_panorama_campaign_section,
    protect_human_rights,
    build_campaign_section,
    query_campaign_state,
    fetch_health_timestamp,
    run_refresh_cycle,
    write_audit_log,
)


# ───────────────────────── 夹具 ─────────────────────────

SAMPLE_BOOTSTRAP = """\
# AGENT_BOOTSTRAP —— 冷启动包 v1（2026-10-31 建）

WORLD: 2026-11-06 22:47:27 dispatcher 心跳正常（战役/池实时态查 campaign_plans/nodes 表）

> 借 owner 另一平台 /hello 握手设计

## 事业全貌 ECOSYSTEM-MAP

本生态=科研智能体事业

## 人权线

战略级变更（owner 令/北极星调整）保留人类令位不自动改写。
此节内容 MUST NOT 被 daemon 自动修改。

## 身份（who_you_are）

你是本平台的建设公民
"""

SAMPLE_PANORAMA = """\
# Tensor 系列全貌与愿景（2026-11-07 文档侦察综合）

## 一、愿景

总北极星

## 二、仓库地图

仓库地图内容

## 人权线

战略级变更保留人类令位不自动改写。

## 七、布局总图

布局内容
"""

SAMPLE_CAMPAIGN_STATE = {
    "active_plans": [
        {"id": 1, "name": "冰区航行经验链", "status": "active", "node_count": 12, "done_count": 5},
        {"id": 2, "name": "高保真仿真军", "status": "active", "node_count": 6, "done_count": 2},
    ],
    "node_stats": {"total": 18, "done": 7, "in_flight": 8, "blocked": 2, "failed": 1},
    "recent_gates": [
        {"plan_name": "冰区航行经验链", "node_name": "G1 验收门", "status": "done", "updated_at": "2026-11-07T00:00:00Z"},
    ],
    "query_ts": "2026-11-07T03:27:50+00:00",
}


# ───────────────────────── 1. WORLD 行刷新 ─────────────────────────

def test_world_line_refresh_replaces_old():
    """新时间戳必须替换旧 WORLD 行。"""
    new_ts = "2026-11-07 10:00:00 dispatcher 心跳正常（战役/池实时态查 campaign_plans/nodes 表）"
    result = refresh_world_line(SAMPLE_BOOTSTRAP, new_ts)
    assert f"WORLD: {new_ts}" in result
    assert "2026-11-06 22:47:27" not in result
    # 其他内容不变
    assert "## 事业全貌 ECOSYSTEM-MAP" in result
    assert "## 人权线" in result


def test_world_line_append_when_missing():
    """无 WORLD 行时，应在第一个 ## 前插入。"""
    content_no_world = """\
# AGENT_BOOTSTRAP

## 事业全貌

内容
"""
    ts = "2026-11-07 10:00:00 test"
    result = refresh_world_line(content_no_world, ts)
    assert f"WORLD: {ts}" in result
    # 在 ## 之前
    world_pos = result.index("WORLD:")
    hash_pos = result.index("## 事业全貌")
    assert world_pos < hash_pos


# ───────────────────────── 2. 幂等 ─────────────────────────

def test_refresh_is_idempotent():
    """同一输入连跑两次，产出内容逐字节相同。"""
    ts = "2026-11-07 10:00:00 test"
    a = refresh_world_line(SAMPLE_BOOTSTRAP, ts)
    b = refresh_world_line(SAMPLE_BOOTSTRAP, ts)
    assert a == b
    assert compute_content_hash(a) == compute_content_hash(b)


def test_panorama_refresh_idempotent():
    """PANORAMA 刷新也必须幂等。"""
    a = refresh_panorama_campaign_section(SAMPLE_PANORAMA, SAMPLE_CAMPAIGN_STATE)
    b = refresh_panorama_campaign_section(SAMPLE_PANORAMA, SAMPLE_CAMPAIGN_STATE)
    assert a == b


# ───────────────────────── 3. 人权线保护（负例） ─────────────────────────

def test_human_rights_line_preserved_in_bootstrap():
    """人权线内容 MUST NOT 被改写。"""
    original_rights = extract_human_rights_line(SAMPLE_BOOTSTRAP)
    assert original_rights is not None
    assert "MUST NOT 被 daemon 自动修改" in original_rights

    ts = "2026-11-07 10:00:00 test"
    new_content = refresh_world_line(SAMPLE_BOOTSTRAP, ts)
    new_content = protect_human_rights(new_content, original_rights)

    final_rights = extract_human_rights_line(new_content)
    assert final_rights == original_rights, "人权线被意外修改"


def test_human_rights_line_preserved_in_panorama():
    """PANORAMA 人权线也必须保护。"""
    original_rights = extract_human_rights_line(SAMPLE_PANORAMA)
    assert original_rights is not None

    new_content = refresh_panorama_campaign_section(SAMPLE_PANORAMA, SAMPLE_CAMPAIGN_STATE)
    new_content = protect_human_rights(new_content, original_rights)

    final_rights = extract_human_rights_line(new_content)
    assert final_rights == original_rights


def test_human_rights_appended_when_missing():
    """如果刷新后人权线消失，应追加回去。"""
    content_without_rights = """\
# AGENT_BOOTSTRAP

WORLD: 2026-11-07 10:00:00 test

## 事业全貌

内容
"""
    original_rights = "## 人权线\n\n战略级变更保留人类令位。\n"
    result = protect_human_rights(content_without_rights, original_rights)
    assert "## 人权线" in result
    assert "战略级变更保留人类令位" in result


# ───────────────────────── 4. 断链降级 ─────────────────────────

def test_fetch_health_degrades_gracefully():
    """health endpoint 不可用时不崩，返回本地时间。"""
    with patch("tools.strategic_doc_maintenance.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = Exception("connection refused")
        ts = fetch_health_timestamp("http://localhost:99999/health")
        # 应返回本地时间（ISO 格式）
        assert ts is not None
        # 验证是 ISO 格式
        datetime.fromisoformat(ts.replace("+00:00", "+00:00"))


def test_query_campaign_degrades_gracefully():
    """数据库不可用时不崩，返回空战役态。"""
    result = query_campaign_state("postgresql://bad:bad@localhost:5432/bad")
    assert result["active_plans"] == []
    assert result["node_stats"]["total"] == 0
    assert result["recent_gates"] == []
    assert "query_ts" in result


# ───────────────────────── 5. 战役态查询（SQLite 文件库） ─────────────────────────

def _setup_test_sqlite(db_path: str):
    """创建测试用 SQLite 文件库。"""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE campaign_plans (
            id INTEGER PRIMARY KEY,
            organization_id INTEGER DEFAULT 1,
            campaign_id INTEGER DEFAULT 1,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            updated_at TEXT
        );
        CREATE TABLE campaign_nodes (
            id INTEGER PRIMARY KEY,
            plan_id INTEGER NOT NULL REFERENCES campaign_plans(id),
            milestone_id INTEGER,
            mission_id INTEGER,
            name TEXT NOT NULL,
            node_type TEXT NOT NULL DEFAULT 'task',
            status TEXT NOT NULL DEFAULT 'pending',
            updated_at TEXT
        );
    """)
    # 插入测试数据
    conn.execute("INSERT INTO campaign_plans (id, name, status) VALUES (1, '测试计划A', 'active')")
    conn.execute("INSERT INTO campaign_plans (id, name, status) VALUES (2, '测试计划B', 'draft')")
    conn.execute("INSERT INTO campaign_plans (id, name, status) VALUES (3, '归档计划', 'archived')")

    conn.execute("INSERT INTO campaign_nodes (id, plan_id, name, node_type, status) VALUES (1, 1, '节点1', 'task', 'done')")
    conn.execute("INSERT INTO campaign_nodes (id, plan_id, name, node_type, status) VALUES (2, 1, '节点2', 'task', 'in-flight')")
    conn.execute("INSERT INTO campaign_nodes (id, plan_id, name, node_type, status) VALUES (3, 1, 'G1 门', 'gate', 'done')")
    conn.execute("INSERT INTO campaign_nodes (id, plan_id, name, node_type, status) VALUES (4, 2, '节点3', 'task', 'blocked')")
    conn.execute("INSERT INTO campaign_nodes (id, plan_id, name, node_type, status) VALUES (5, 2, '节点4', 'task', 'pending')")
    conn.commit()
    conn.close()


def test_query_campaign_state_sqlite(tmp_path):
    """SQLite 文件库能正确查询活跃计划/节点统计/最近门。"""
    db_path = str(tmp_path / "test_stratdoc.sqlite3")
    _setup_test_sqlite(db_path)

    result = query_campaign_state(f"sqlite:///{db_path}")

    # 活跃计划（active + draft）
    assert len(result["active_plans"]) == 2
    plan_names = {p["name"] for p in result["active_plans"]}
    assert "测试计划A" in plan_names
    assert "测试计划B" in plan_names

    # 节点统计
    assert result["node_stats"]["total"] == 5
    assert result["node_stats"]["done"] == 2
    assert result["node_stats"]["in_flight"] == 1
    assert result["node_stats"]["blocked"] == 1

    # 最近门
    assert len(result["recent_gates"]) == 1
    assert result["recent_gates"][0]["node_name"] == "G1 门"
    assert result["recent_gates"][0]["status"] == "done"


# ───────────────────────── 6. PANORAMA 战役态节刷新 ─────────────────────────

def test_panorama_campaign_section_build():
    """build_campaign_section 生成正确的 markdown。"""
    section = build_campaign_section(SAMPLE_CAMPAIGN_STATE)
    assert "## 战役态（自动刷新，SQL 溯源）" in section
    assert "冰区航行经验链" in section
    assert "高保真仿真军" in section
    assert "G1 验收门" in section
    assert "总计: 18" in section


def test_panorama_campaign_section_replace():
    """PANORAMA 中有战役态节时，应替换。"""
    content_with_section = SAMPLE_PANORAMA + "\n## 战役态（旧版）\n\n旧内容\n"
    result = refresh_panorama_campaign_section(content_with_section, SAMPLE_CAMPAIGN_STATE)
    assert "## 战役态（自动刷新，SQL 溯源）" in result
    assert "旧内容" not in result


def test_panorama_campaign_section_append():
    """PANORAMA 中无战役态节时，应追加到末尾。"""
    result = refresh_panorama_campaign_section(SAMPLE_PANORAMA, SAMPLE_CAMPAIGN_STATE)
    assert "## 战役态（自动刷新，SQL 溯源）" in result
    assert result.endswith("\n")


# ───────────────────────── 7. 审计日志 ─────────────────────────

def test_audit_log_write(tmp_path):
    """审计日志文件存在且包含有效 JSON 行。"""
    audit_path = str(tmp_path / "audit.jsonl")

    write_audit_log(audit_path, "test_action", "ok", {"key": "value"})
    content = Path(audit_path).read_text(encoding="utf-8")
    lines = content.strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["action"] == "test_action"
    assert entry["status"] == "ok"
    assert entry["details"]["key"] == "value"
    assert "ts" in entry


# ───────────────────────── 8. 完整刷新循环（干跑） ─────────────────────────

def test_refresh_cycle_dry_run(tmp_path):
    """干跑模式不写文件，但返回正确结果。"""
    bp = tmp_path / "AGENT_BOOTSTRAP.md"
    bp.write_text(SAMPLE_BOOTSTRAP, encoding="utf-8")
    pp = tmp_path / "PANORAMA.md"
    pp.write_text(SAMPLE_PANORAMA, encoding="utf-8")
    audit = tmp_path / "audit.jsonl"

    result = run_refresh_cycle(
        bootstrap_path=str(bp),
        panorama_path=str(pp),
        db_url="postgresql://bad:bad@localhost:5432/bad",
        health_url="http://localhost:99999/health",
        audit_path=str(audit),
        dry_run=True,
    )

    # 干跑不写文件
    assert bp.read_text(encoding="utf-8") == SAMPLE_BOOTSTRAP
    assert pp.read_text(encoding="utf-8") == SAMPLE_PANORAMA

    # 但结果应标记更新（内容会变化）
    assert result["bootstrap_updated"] is True
    assert result["panorama_updated"] is True
    assert result["human_rights_preserved"] is True

    # 审计日志存在
    assert audit.exists()


def test_refresh_cycle_idempotent_two_rounds(tmp_path):
    """连续两轮刷新（相同时间戳），第二轮应幂等（内容不变）。"""
    bp = tmp_path / "AGENT_BOOTSTRAP.md"
    bp.write_text(SAMPLE_BOOTSTRAP, encoding="utf-8")
    pp = tmp_path / "PANORAMA.md"
    pp.write_text(SAMPLE_PANORAMA, encoding="utf-8")
    audit = tmp_path / "audit.jsonl"

    fixed_ts = "2026-11-07 10:00:00 fixed_ts dispatcher 心跳正常（战役/池实时态查 campaign_plans/nodes 表）"

    with patch("tools.strategic_doc_maintenance.fetch_health_timestamp", return_value=fixed_ts):
        # 第一轮
        r1 = run_refresh_cycle(
            bootstrap_path=str(bp),
            panorama_path=str(pp),
            db_url="postgresql://bad:bad@localhost:5432/bad",
            health_url="http://localhost:99999/health",
            audit_path=str(audit),
            dry_run=False,
        )
        assert r1["bootstrap_updated"] is True

        bp_content_after_r1 = bp.read_text(encoding="utf-8")

        # 第二轮（相同时间戳）
        r2 = run_refresh_cycle(
            bootstrap_path=str(bp),
            panorama_path=str(pp),
            db_url="postgresql://bad:bad@localhost:5432/bad",
            health_url="http://localhost:99999/health",
            audit_path=str(audit),
            dry_run=False,
        )

        bp_content_after_r2 = bp.read_text(encoding="utf-8")

        # 第二轮内容应与第一轮后相同（幂等）
        assert bp_content_after_r1 == bp_content_after_r2

    # 关键：人权线必须保留
    final_rights = extract_human_rights_line(bp_content_after_r2)
    original_rights = extract_human_rights_line(SAMPLE_BOOTSTRAP)
    assert final_rights == original_rights, "人权线在两轮刷新后被修改"
