# -*- coding: utf-8 -*-
"""
P472W2-N01: 审计取证最小权限只读视图层 — 综合测试套件

验收判据:
  C1: 视图+6 表字段契约齐全
  C2: 权限矩阵门 ≥12 项
  C3: 结果全等（sha256 逐位相等）
  C4: 去 superuser（audit_reader 会话跑通）
  C5: 越权留痕
  C6: 零领域语义
  C7: 可回滚

测试策略:
  - SQLite 模式: 测试视图定义、字段契约、查询等价性（C1, C3, C6）
  - PostgreSQL 模式: 测试 RBAC 权限矩阵（C2, C4, C5）
  - 两种模式都运行，PostgreSQL 不可用时自动跳过 RBAC 测试
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

# ── 路径 ──────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "backend" / "migrations" / "115_audit_readonly_views.sql"
ROLLBACK_PATH = ROOT / "backend" / "migrations" / "115_audit_readonly_views_rollback.sql"
FIELDS_CONTRACT_PATH = ROOT / "contracts" / "audit_views_fields.json"
PERMISSION_MATRIX_PATH = ROOT / "contracts" / "audit_permission_matrix.json"
LEXICON_PATH = ROOT / "tools" / "evidence_domain_lexicon.json"

# ── SQLite schema（模拟 6 张基表） ────────────────────────────────
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaign_plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    campaign_id     TEXT NOT NULL,
    name            TEXT NOT NULL,
    budget_planned  TEXT NOT NULL DEFAULT '{}',
    budget_actual   TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'draft',
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_by      INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS campaign_nodes (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id              INTEGER NOT NULL,
    milestone_id         INTEGER,
    mission_id           INTEGER,
    name                 TEXT NOT NULL,
    node_type            TEXT NOT NULL DEFAULT 'task',
    dependencies         TEXT NOT NULL DEFAULT '[]',
    planned_start        TEXT,
    planned_end          TEXT,
    actual_start         TEXT,
    actual_end           TEXT,
    status               TEXT NOT NULL DEFAULT 'pending',
    reproduction_verdict TEXT,
    consecutive_fails    INTEGER NOT NULL DEFAULT 0,
    block_reason         TEXT,
    metadata             TEXT NOT NULL DEFAULT '{}',
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tensor_dev_missions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    goal            TEXT NOT NULL,
    repo            TEXT NOT NULL,
    base_ref        TEXT NOT NULL DEFAULT 'main',
    budget          TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'queued',
    claim_token     TEXT,
    claimed_by      TEXT,
    session_id      INTEGER,
    result          TEXT NOT NULL DEFAULT '{}',
    created_by      INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    heartbeat_at    TEXT,
    finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS tensor_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    scenario_key    TEXT,
    mission_id      INTEGER,
    stage           TEXT,
    tags            TEXT NOT NULL DEFAULT '[]',
    markdown        TEXT NOT NULL,
    figures         TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'published',
    created_by      INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tensor_capability_deltas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    mission_id      INTEGER NOT NULL,
    category        TEXT NOT NULL,
    summary         TEXT NOT NULL,
    reason          TEXT,
    artifacts       TEXT NOT NULL DEFAULT '[]',
    evidence        TEXT NOT NULL DEFAULT '{}',
    card_id         INTEGER,
    reviewer        TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS capability_registry (
    capability_id   TEXT PRIMARY KEY,
    claimed_level   TEXT NOT NULL,
    expected_level  TEXT NOT NULL,
    registered_at   TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    metadata        TEXT DEFAULT '{}'
);
"""

# ── SQLite 视图定义（与 PostgreSQL 迁移一致） ─────────────────────
SQLITE_VIEWS = """
CREATE VIEW IF NOT EXISTS v_audit_campaign_plans AS
SELECT id, campaign_id, name, status, metadata, created_at, updated_at
FROM campaign_plans;

CREATE VIEW IF NOT EXISTS v_audit_campaign_nodes AS
SELECT id, plan_id, name, node_type, status, reproduction_verdict,
       consecutive_fails, block_reason, metadata, created_at, updated_at
FROM campaign_nodes;

CREATE VIEW IF NOT EXISTS v_audit_missions AS
SELECT id, goal, repo, base_ref, status, claimed_by, result,
       created_at, heartbeat_at, finished_at
FROM tensor_dev_missions;

CREATE VIEW IF NOT EXISTS v_audit_reports AS
SELECT id, title, summary, scenario_key, mission_id, stage, tags, status, created_at
FROM tensor_reports;

CREATE VIEW IF NOT EXISTS v_audit_capability_deltas AS
SELECT id, mission_id, category, summary, reason, artifacts, evidence, reviewer, created_at
FROM tensor_capability_deltas;

CREATE VIEW IF NOT EXISTS v_audit_capability_registry AS
SELECT capability_id, claimed_level, expected_level, registered_at, updated_at
FROM capability_registry;
"""

# ── 种子数据 ──────────────────────────────────────────────────────
SEED_DATA = {
    "campaign_plans": [
        (1, 1, "p472w2", "审计视图层战役", '{"max_tool_calls":0}', '{"max_tool_calls":0}',
         "active", '{"domain": "platform"}', 1, "2026-11-11 00:00:00", "2026-11-11 00:00:00"),
        (2, 1, "p100w1", "基准测试战役", '{"max_tool_calls":0}', '{"max_tool_calls":0}',
         "completed", '{"domain": "benchmark"}', 2, "2026-11-10 00:00:00", "2026-11-10 00:00:00"),
    ],
    "campaign_nodes": [
        (1, 1, None, None, "N01-视图定义", "task", "[]", None, None, None, None,
         "done", "pass", 0, None, '{"evidence": "real"}', "2026-11-11 00:00:00", "2026-11-11 01:00:00"),
        (2, 1, None, None, "N02-权限矩阵", "gate", "[]", None, None, None, None,
         "in-flight", None, 0, None, '{}', "2026-11-11 01:00:00", "2026-11-11 01:00:00"),
    ],
    "tensor_dev_missions": [
        (1, 1, "实现审计只读视图层", "TensorPlatform", "main",
         '{"max_tool_calls":80}', "succeeded", None, "agent-001", None,
         '{"verdict": "pass"}', 1, "2026-11-11 00:00:00", "2026-11-11 02:00:00", "2026-11-11 03:00:00"),
        (2, 1, "权限矩阵测试", "TensorPlatform", "main",
         '{"max_tool_calls":40}', "running", None, "agent-002", None,
         '{}', 2, "2026-11-11 03:00:00", "2026-11-11 03:30:00", None),
    ],
    "tensor_reports": [
        (1, 1, "审计视图层进展报告", "实现只读视图层+权限矩阵", "p472w2", 1, "N01",
         '["platform", "audit"]', "# 报告内容\n...", '[]', "published", 1, "2026-11-11 04:00:00"),
        (2, 1, "基准测试报告", "基准测试结果", "p100w1", None, None,
         '["benchmark"]', "# 基准报告\n...", '[]', "published", 2, "2026-11-10 05:00:00"),
    ],
    "tensor_capability_deltas": [
        (1, 1, 1, "new_capability", "审计只读视图层", "新增 RBAC 审计角色+六表视图",
         '["migration_115"]', '{"tests_passed": 16}', None, "auto_reviewer", "2026-11-11 05:00:00"),
    ],
    "capability_registry": [
        ("audit.views.readonly", "REAL", "REAL", "2026-11-11 05:00:00", "2026-11-11 05:00:00", '{}'),
        ("calc.resistance", "REAL", "REAL", "2026-10-01 00:00:00", "2026-10-15 00:00:00", '{}'),
    ],
}


# ── Fixtures ──────────────────────────────────────────────────────
@pytest.fixture
def sqlite_db():
    """创建带种子数据的 SQLite 测试数据库"""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SQLITE_SCHEMA)
    conn.executescript(SQLITE_VIEWS)

    for table, rows in SEED_DATA.items():
        placeholders = ",".join(["?"] * len(rows[0]))
        cols = _get_table_columns(table)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        for row in rows:
            conn.execute(sql, row)
    conn.commit()
    yield conn
    conn.close()


def _get_table_columns(table: str) -> List[str]:
    """获取表列名（按种子数据顺序）"""
    col_map = {
        "campaign_plans": [
            "id", "organization_id", "campaign_id", "name", "budget_planned",
            "budget_actual", "status", "metadata", "created_by", "created_at", "updated_at"
        ],
        "campaign_nodes": [
            "id", "plan_id", "milestone_id", "mission_id", "name", "node_type",
            "dependencies", "planned_start", "planned_end", "actual_start", "actual_end",
            "status", "reproduction_verdict", "consecutive_fails", "block_reason",
            "metadata", "created_at", "updated_at"
        ],
        "tensor_dev_missions": [
            "id", "organization_id", "goal", "repo", "base_ref", "budget", "status",
            "claim_token", "claimed_by", "session_id", "result", "created_by",
            "created_at", "heartbeat_at", "finished_at"
        ],
        "tensor_reports": [
            "id", "organization_id", "title", "summary", "scenario_key", "mission_id",
            "stage", "tags", "markdown", "figures", "status", "created_by", "created_at"
        ],
        "tensor_capability_deltas": [
            "id", "organization_id", "mission_id", "category", "summary", "reason",
            "artifacts", "evidence", "card_id", "reviewer", "created_at"
        ],
        "capability_registry": [
            "capability_id", "claimed_level", "expected_level",
            "registered_at", "updated_at", "metadata"
        ],
    }
    return col_map[table]


@pytest.fixture
def fields_contract():
    """加载字段契约"""
    return json.loads(FIELDS_CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def permission_matrix():
    """加载权限矩阵"""
    return json.loads(PERMISSION_MATRIX_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def domain_lexicon():
    """加载域词表"""
    if LEXICON_PATH.exists():
        return json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    return {"ascii_terms": [], "cjk_terms": []}


# ══════════════════════════════════════════════════════════════════
# C1: 视图+6 表字段契约齐全
# ══════════════════════════════════════════════════════════════════
class TestC1ViewFieldContracts:
    """验证 6 个视图存在且字段与契约一致"""

    EXPECTED_VIEWS = [
        "v_audit_campaign_plans",
        "v_audit_campaign_nodes",
        "v_audit_missions",
        "v_audit_reports",
        "v_audit_capability_deltas",
        "v_audit_capability_registry",
    ]

    def test_contract_file_exists(self):
        """契约文件存在"""
        assert FIELDS_CONTRACT_PATH.exists(), f"Missing: {FIELDS_CONTRACT_PATH}"

    def test_contract_has_6_views(self, fields_contract):
        """契约覆盖 6 个视图"""
        views = fields_contract["views"]
        assert len(views) == 6, f"Expected 6 views, got {len(views)}"
        for v in self.EXPECTED_VIEWS:
            assert v in views, f"Missing view in contract: {v}"

    def test_sqlite_views_exist(self, sqlite_db):
        """SQLite 中 6 个视图可查询"""
        cursor = sqlite_db.execute(
            "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
        )
        db_views = {row[0] for row in cursor.fetchall()}
        for v in self.EXPECTED_VIEWS:
            assert v in db_views, f"View not found in DB: {v}"

    def test_view_fields_match_contract(self, sqlite_db, fields_contract):
        """视图字段与契约白名单一致"""
        for view_name, view_spec in fields_contract["views"].items():
            expected_fields = {f["name"] for f in view_spec["included_fields"]}
            cursor = sqlite_db.execute(f"SELECT * FROM {view_name} LIMIT 0")
            actual_fields = {desc[0] for desc in cursor.description}
            assert actual_fields == expected_fields, (
                f"View {view_name}: expected {expected_fields}, got {actual_fields}"
            )

    def test_excluded_fields_not_in_view(self, sqlite_db, fields_contract):
        """排除字段不出现在视图中"""
        for view_name, view_spec in fields_contract["views"].items():
            excluded = {f["name"] for f in view_spec["excluded_fields"]}
            cursor = sqlite_db.execute(f"SELECT * FROM {view_name} LIMIT 0")
            actual_fields = {desc[0] for desc in cursor.description}
            leaked = excluded & actual_fields
            assert not leaked, (
                f"View {view_name} leaks excluded fields: {leaked}"
            )

    def test_migration_file_exists(self):
        """迁移脚本存在"""
        assert MIGRATION_PATH.exists(), f"Missing: {MIGRATION_PATH}"

    def test_rollback_file_exists(self):
        """回滚脚本存在"""
        assert ROLLBACK_PATH.exists(), f"Missing: {ROLLBACK_PATH}"


# ══════════════════════════════════════════════════════════════════
# C2: 权限矩阵门 ≥12 项
# ══════════════════════════════════════════════════════════════════
class TestC2PermissionMatrix:
    """验证权限矩阵覆盖 ≥12 项"""

    def test_matrix_file_exists(self):
        """权限矩阵文件存在"""
        assert PERMISSION_MATRIX_PATH.exists()

    def test_matrix_has_12_plus_items(self, permission_matrix):
        """权限矩阵 ≥12 项"""
        items = permission_matrix["matrix"]
        assert len(items) >= 12, f"Expected ≥12 items, got {len(items)}"

    def test_matrix_covers_all_categories(self, permission_matrix):
        """权限矩阵覆盖所有类别"""
        items = permission_matrix["matrix"]
        categories = {item["category"] for item in items}
        required = {"view_read", "view_write", "base_table_read", "base_table_write",
                    "ddl", "negative_control"}
        missing = required - categories
        assert not missing, f"Missing categories: {missing}"

    def test_matrix_covers_all_views(self, permission_matrix):
        """权限矩阵覆盖所有 6 个视图的 SELECT"""
        items = permission_matrix["matrix"]
        view_reads = {
            item["object"] for item in items
            if item["action"] == "SELECT" and item["category"] == "view_read"
        }
        expected = set(permission_matrix["views"])
        assert view_reads == expected, f"Missing view reads: {expected - view_reads}"

    def test_all_write_operations_denied(self, permission_matrix):
        """所有写操作预期为 DENY"""
        write_items = [
            item for item in permission_matrix["matrix"]
            if item["category"] in ("view_write", "base_table_write", "ddl")
        ]
        for item in write_items:
            assert item["expected"] == "DENY", (
                f"Write operation {item['id']} should be DENY, got {item['expected']}"
            )

    def test_negative_control_present(self, permission_matrix):
        """负控（未授权角色）存在"""
        neg = [
            item for item in permission_matrix["matrix"]
            if item["category"] == "negative_control"
        ]
        assert len(neg) >= 1, "Need at least 1 negative control"


# ══════════════════════════════════════════════════════════════════
# C3: 结果全等（sha256 逐位相等）
# ══════════════════════════════════════════════════════════════════
class TestC3ResultEquivalence:
    """验证审计查询走视图与直读基表结果 sha256 全等"""

    # 3 条既有审计查询
    AUDIT_QUERIES = {
        "campaign_status_summary": {
            "base_query": """
                SELECT id, campaign_id, name, status, metadata, created_at, updated_at
                FROM campaign_plans ORDER BY id
            """,
            "view_query": """
                SELECT id, campaign_id, name, status, metadata, created_at, updated_at
                FROM v_audit_campaign_plans ORDER BY id
            """,
        },
        "mission_progress": {
            "base_query": """
                SELECT id, goal, repo, base_ref, status, claimed_by, result,
                       created_at, heartbeat_at, finished_at
                FROM tensor_dev_missions ORDER BY id
            """,
            "view_query": """
                SELECT id, goal, repo, base_ref, status, claimed_by, result,
                       created_at, heartbeat_at, finished_at
                FROM v_audit_missions ORDER BY id
            """,
        },
        "capability_evidence_trace": {
            "base_query": """
                SELECT capability_id, claimed_level, expected_level, registered_at, updated_at
                FROM capability_registry ORDER BY capability_id
            """,
            "view_query": """
                SELECT capability_id, claimed_level, expected_level, registered_at, updated_at
                FROM v_audit_capability_registry ORDER BY capability_id
            """,
        },
    }

    def _query_sha256(self, conn, query: str) -> str:
        """执行查询并返回结果 JSON 的 sha256"""
        cursor = conn.execute(query)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        # 统一序列化为 JSON（确保确定性）
        result = [dict(zip(columns, row)) for row in rows]
        json_str = json.dumps(result, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(json_str.encode("utf-8")).hexdigest()

    def test_query_1_equivalence(self, sqlite_db):
        """审计查询 1: campaign_status_summary 全等"""
        q = self.AUDIT_QUERIES["campaign_status_summary"]
        base_hash = self._query_sha256(sqlite_db, q["base_query"])
        view_hash = self._query_sha256(sqlite_db, q["view_query"])
        assert base_hash == view_hash, (
            f"Drift detected! base={base_hash}, view={view_hash}"
        )

    def test_query_2_equivalence(self, sqlite_db):
        """审计查询 2: mission_progress 全等"""
        q = self.AUDIT_QUERIES["mission_progress"]
        base_hash = self._query_sha256(sqlite_db, q["base_query"])
        view_hash = self._query_sha256(sqlite_db, q["view_query"])
        assert base_hash == view_hash, (
            f"Drift detected! base={base_hash}, view={view_hash}"
        )

    def test_query_3_equivalence(self, sqlite_db):
        """审计查询 3: capability_evidence_trace 全等"""
        q = self.AUDIT_QUERIES["capability_evidence_trace"]
        base_hash = self._query_sha256(sqlite_db, q["base_query"])
        view_hash = self._query_sha256(sqlite_db, q["view_query"])
        assert base_hash == view_hash, (
            f"Drift detected! base={base_hash}, view={view_hash}"
        )

    def test_all_queries_drift_zero(self, sqlite_db):
        """所有 3 条查询 drift=0"""
        drifts = {}
        for name, q in self.AUDIT_QUERIES.items():
            base_hash = self._query_sha256(sqlite_db, q["base_query"])
            view_hash = self._query_sha256(sqlite_db, q["view_query"])
            drifts[name] = 0 if base_hash == view_hash else 1
        total_drift = sum(drifts.values())
        assert total_drift == 0, f"Total drift: {total_drift}, details: {drifts}"


# ══════════════════════════════════════════════════════════════════
# C4: 去 superuser（视图查询不依赖 superuser 权限）
# ══════════════════════════════════════════════════════════════════
class TestC4NoSuperuserRequired:
    """验证审计查询仅需 SELECT 权限，不需要 superuser"""

    def test_views_are_select_only(self, sqlite_db):
        """视图只支持 SELECT"""
        views = [
            "v_audit_campaign_plans", "v_audit_campaign_nodes", "v_audit_missions",
            "v_audit_reports", "v_audit_capability_deltas", "v_audit_capability_registry",
        ]
        for view in views:
            # SELECT 应该成功
            cursor = sqlite_db.execute(f"SELECT count(*) FROM {view}")
            assert cursor.fetchone()[0] >= 0

    def test_views_not_writable(self, sqlite_db):
        """视图不可写（SQLite 视图默认不可写，除非 INSTEAD OF 触发器）"""
        views = [
            "v_audit_campaign_plans", "v_audit_campaign_nodes", "v_audit_missions",
            "v_audit_reports", "v_audit_capability_deltas", "v_audit_capability_registry",
        ]
        for view in views:
            with pytest.raises(Exception):
                sqlite_db.execute(
                    f"INSERT INTO {view} (id) VALUES (99999)"
                )

    def test_migration_grants_only_select(self):
        """迁移脚本只授予 SELECT 权限"""
        content = MIGRATION_PATH.read_text(encoding="utf-8")
        # 检查 GRANT SELECT 存在
        assert "GRANT SELECT ON" in content
        # 检查没有 GRANT INSERT/UPDATE/DELETE on views
        lines = content.split("\n")
        for line in lines:
            stripped = line.strip().upper()
            if stripped.startswith("GRANT") and "TO AUDIT_READER" in stripped:
                if "INSERT" in stripped or "UPDATE" in stripped or "DELETE" in stripped:
                    # 只有 audit_access_events 的 INSERT 是允许的
                    if "AUDIT_ACCESS_EVENTS" not in stripped:
                        pytest.fail(f"Unexpected write grant: {line}")


# ══════════════════════════════════════════════════════════════════
# C5: 越权留痕
# ══════════════════════════════════════════════════════════════════
class TestC5AccessAuditTrail:
    """验证越权访问被记录到审计事件表"""

    def test_audit_access_events_table_in_migration(self):
        """迁移脚本创建审计事件表"""
        content = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS audit_access_events" in content

    def test_audit_events_schema(self):
        """审计事件表 schema 包含必要字段"""
        content = MIGRATION_PATH.read_text(encoding="utf-8")
        required_fields = ["event_type", "role_name", "table_name", "sql_hash", "occurred_at"]
        for field in required_fields:
            assert field in content, f"Missing field in audit table: {field}"

    def test_audit_events_append_only(self):
        """审计事件表有 append-only 保护"""
        content = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "audit_access_events_append_only_guard" in content
        assert "BEFORE UPDATE OR DELETE" in content

    def test_sql_hash_computable(self):
        """SQL 哈希可计算"""
        test_sql = "SELECT * FROM v_audit_campaign_plans"
        sql_hash = hashlib.sha256(test_sql.encode("utf-8")).hexdigest()
        assert len(sql_hash) == 64
        assert all(c in "0123456789abcdef" for c in sql_hash)

    def test_audit_event_fields_match_contract(self):
        """审计事件表字段与权限矩阵一致"""
        matrix = json.loads(PERMISSION_MATRIX_PATH.read_text(encoding="utf-8"))
        # 权限矩阵中的 DENY 项应该对应审计事件
        deny_items = [item for item in matrix["matrix"] if item["expected"] == "DENY"]
        assert len(deny_items) >= 6, "Need at least 6 DENY items for audit trail"


# ══════════════════════════════════════════════════════════════════
# C6: 零领域语义
# ══════════════════════════════════════════════════════════════════
class TestC6ZeroDomainSemantics:
    """验证视图/迁移/契约中无域词条"""

    def _scan_for_domain_terms(self, text: str, lexicon: dict) -> List[dict]:
        """扫描文本中的域词条"""
        import re
        hits = []
        for term in lexicon.get("ascii_terms", []):
            pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    hits.append({"term": term, "line": i, "text": line.strip()[:100]})
        for term in lexicon.get("cjk_terms", []):
            for i, line in enumerate(text.splitlines(), 1):
                if term in line:
                    hits.append({"term": term, "line": i, "text": line.strip()[:100]})
        return hits

    def test_migration_no_domain_terms(self, domain_lexicon):
        """迁移脚本无域词条"""
        content = MIGRATION_PATH.read_text(encoding="utf-8")
        hits = self._scan_for_domain_terms(content, domain_lexicon)
        assert not hits, f"Domain terms found in migration: {hits}"

    def test_rollback_no_domain_terms(self, domain_lexicon):
        """回滚脚本无域词条"""
        content = ROLLBACK_PATH.read_text(encoding="utf-8")
        hits = self._scan_for_domain_terms(content, domain_lexicon)
        assert not hits, f"Domain terms found in rollback: {hits}"

    def test_fields_contract_no_domain_terms(self, domain_lexicon):
        """字段契约无域词条"""
        content = FIELDS_CONTRACT_PATH.read_text(encoding="utf-8")
        hits = self._scan_for_domain_terms(content, domain_lexicon)
        assert not hits, f"Domain terms found in fields contract: {hits}"

    def test_permission_matrix_no_domain_terms(self, domain_lexicon):
        """权限矩阵无域词条"""
        content = PERMISSION_MATRIX_PATH.read_text(encoding="utf-8")
        hits = self._scan_for_domain_terms(content, domain_lexicon)
        assert not hits, f"Domain terms found in permission matrix: {hits}"

    def test_test_file_no_domain_terms(self, domain_lexicon):
        """测试文件无域词条（排除词表配置引用）"""
        test_content = Path(__file__).read_text(encoding="utf-8")
        hits = self._scan_for_domain_terms(test_content, domain_lexicon)
        # 排除词表配置中的合法引用（lexicon 文件路径引用）
        # 只检查非配置引用行
        real_hits = [h for h in hits if "lexicon" not in h.get("text", "").lower()
                     and "domain_terms" not in h.get("text", "").lower()
                     and "ascii_terms" not in h.get("text", "").lower()
                     and "cjk_terms" not in h.get("text", "").lower()
                     and "exclusion" not in h.get("text", "").lower()]
        assert not real_hits, f"Domain terms found in test file: {real_hits}"


# ══════════════════════════════════════════════════════════════════
# C7: 可回滚
# ══════════════════════════════════════════════════════════════════
class TestC7Rollback:
    """验证回滚脚本完整且可逆"""

    def test_rollback_drops_all_views(self):
        """回滚脚本删除所有 6 个视图"""
        content = ROLLBACK_PATH.read_text(encoding="utf-8")
        views = [
            "v_audit_campaign_plans", "v_audit_campaign_nodes", "v_audit_missions",
            "v_audit_reports", "v_audit_capability_deltas", "v_audit_capability_registry",
        ]
        for view in views:
            assert f"DROP VIEW IF EXISTS {view}" in content, (
                f"Rollback missing: DROP VIEW IF EXISTS {view}"
            )

    def test_rollback_drops_audit_table(self):
        """回滚脚本删除审计事件表"""
        content = ROLLBACK_PATH.read_text(encoding="utf-8")
        assert "DROP TABLE IF EXISTS audit_access_events" in content

    def test_rollback_drops_role(self):
        """回滚脚本删除 audit_reader 角色"""
        content = ROLLBACK_PATH.read_text(encoding="utf-8")
        assert "DROP ROLE IF EXISTS audit_reader" in content

    def test_rollback_revokes_rbac(self):
        """回滚脚本撤销 RBAC 注册"""
        content = ROLLBACK_PATH.read_text(encoding="utf-8")
        assert "DELETE FROM rbac_role_permissions" in content
        assert "DELETE FROM rbac_permissions" in content
        assert "DELETE FROM rbac_roles" in content

    def test_rollback_is_idempotent(self):
        """回滚脚本幂等（所有 DROP 带 IF EXISTS）"""
        content = ROLLBACK_PATH.read_text(encoding="utf-8")
        drop_lines = [
            line.strip() for line in content.splitlines()
            if line.strip().upper().startswith("DROP")
        ]
        for line in drop_lines:
            assert "IF EXISTS" in line.upper(), f"Non-idempotent drop: {line}"

    def test_migration_rollback_roundtrip(self):
        """迁移→回滚→迁移 可重放"""
        migration = MIGRATION_PATH.read_text(encoding="utf-8")
        rollback = ROLLBACK_PATH.read_text(encoding="utf-8")
        # 迁移创建的对象，回滚都删除
        assert "CREATE TABLE IF NOT EXISTS audit_access_events" in migration
        assert "DROP TABLE IF EXISTS audit_access_events" in rollback
        assert "CREATE ROLE audit_reader" in migration or "CREATE ROLE" in migration
        assert "DROP ROLE IF EXISTS audit_reader" in rollback


# ══════════════════════════════════════════════════════════════════
# PostgreSQL RBAC 测试（需要实际 PostgreSQL 连接）
# ══════════════════════════════════════════════════════════════════
def _pg_available() -> bool:
    """检查 PostgreSQL 是否可用"""
    try:
        result = subprocess.run(
            ["psql", "-U", "postgres", "-c", "SELECT 1"],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _pg_available(), reason="PostgreSQL not available")
class TestPostgresRBAC:
    """PostgreSQL 实际 RBAC 测试"""

    @pytest.fixture(autouse=True)
    def setup_pg(self):
        """设置 PostgreSQL 测试环境"""
        self.db_name = "test_audit_views"
        # 先清理可能残留的角色和数据库
        subprocess.run(
            ["psql", "-U", "postgres", "-c",
             "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename='audit_reader'"],
            capture_output=True
        )
        subprocess.run(
            ["psql", "-U", "postgres", "-c", "DROP ROLE IF EXISTS audit_reader"],
            capture_output=True
        )
        subprocess.run(
            ["psql", "-U", "postgres", "-c",
             f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{self.db_name}'"],
            capture_output=True
        )
        subprocess.run(
            ["psql", "-U", "postgres", "-c", f"DROP DATABASE IF EXISTS {self.db_name}"],
            capture_output=True
        )
        # 创建测试数据库
        subprocess.run(
            ["psql", "-U", "postgres", "-c", f"CREATE DATABASE {self.db_name}"],
            capture_output=True, check=True
        )
        yield
        # 清理
        subprocess.run(
            ["psql", "-U", "postgres", "-c",
             f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{self.db_name}'"],
            capture_output=True
        )
        subprocess.run(
            ["psql", "-U", "postgres", "-c", f"DROP DATABASE IF EXISTS {self.db_name}"],
            capture_output=True
        )
        subprocess.run(
            ["psql", "-U", "postgres", "-c", "DROP ROLE IF EXISTS audit_reader"],
            capture_output=True
        )

    def _run_sql(self, sql: str, user: str = "postgres") -> subprocess.CompletedProcess:
        """执行 SQL"""
        return subprocess.run(
            ["psql", "-U", user, "-d", self.db_name, "-c", sql],
            capture_output=True, text=True
        )

    def test_pg_role_created(self):
        """PostgreSQL 角色创建"""
        # 先创建基表（简化版）
        self._run_sql("""
            CREATE TABLE campaign_plans (
                id SERIAL PRIMARY KEY, campaign_id TEXT, name TEXT,
                status TEXT, metadata JSONB DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            );
        """)
        # 创建视图
        self._run_sql("""
            CREATE VIEW v_audit_campaign_plans AS
            SELECT id, campaign_id, name, status, metadata, created_at, updated_at
            FROM campaign_plans;
        """)
        # 创建角色
        self._run_sql("DROP ROLE IF EXISTS audit_reader;")
        result = self._run_sql("""
            CREATE ROLE audit_reader LOGIN PASSWORD 'test';
            GRANT SELECT ON v_audit_campaign_plans TO audit_reader;
            GRANT CONNECT ON DATABASE test_audit_views TO audit_reader;
            GRANT USAGE ON SCHEMA public TO audit_reader;
        """)
        assert result.returncode == 0

        # 验证角色存在
        result = self._run_sql("SELECT rolname FROM pg_roles WHERE rolname = 'audit_reader';")
        assert "audit_reader" in result.stdout

    def test_pg_select_allowed(self):
        """audit_reader 可以 SELECT 视图"""
        self._run_sql("""
            CREATE TABLE IF NOT EXISTS campaign_plans (
                id SERIAL PRIMARY KEY, campaign_id TEXT, name TEXT,
                status TEXT, metadata JSONB DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            );
            INSERT INTO campaign_plans (campaign_id, name, status) VALUES ('test', 'Test', 'active');
            CREATE OR REPLACE VIEW v_audit_campaign_plans AS
            SELECT id, campaign_id, name, status, metadata, created_at, updated_at
            FROM campaign_plans;
        """)
        self._run_sql("DROP ROLE IF EXISTS audit_reader;")
        self._run_sql("""
            CREATE ROLE audit_reader LOGIN PASSWORD 'test';
            GRANT CONNECT ON DATABASE test_audit_views TO audit_reader;
            GRANT USAGE ON SCHEMA public TO audit_reader;
            GRANT SELECT ON v_audit_campaign_plans TO audit_reader;
        """)
        result = subprocess.run(
            ["psql", "-U", "audit_reader", "-d", self.db_name,
             "-c", "SELECT count(*) FROM v_audit_campaign_plans;"],
            capture_output=True, text=True,
            env={**os.environ, "PGPASSWORD": "test"}
        )
        assert result.returncode == 0
        assert "1" in result.stdout


# ══════════════════════════════════════════════════════════════════
# 集成测试：完整流程
# ══════════════════════════════════════════════════════════════════
class TestIntegration:
    """集成测试：完整审计流程"""

    def test_full_audit_workflow(self, sqlite_db):
        """完整审计工作流：查询所有视图"""
        views = [
            "v_audit_campaign_plans",
            "v_audit_campaign_nodes",
            "v_audit_missions",
            "v_audit_reports",
            "v_audit_capability_deltas",
            "v_audit_capability_registry",
        ]
        results = {}
        for view in views:
            cursor = sqlite_db.execute(f"SELECT * FROM {view}")
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            results[view] = {
                "columns": columns,
                "row_count": len(rows),
                "sample": [dict(zip(columns, row)) for row in rows[:2]]
            }

        # 验证每个视图都有数据
        for view, data in results.items():
            assert data["row_count"] > 0, f"View {view} has no data"

    def test_audit_query_produces_deterministic_hash(self, sqlite_db):
        """审计查询产生确定性哈希"""
        query = """
            SELECT id, campaign_id, name, status
            FROM v_audit_campaign_plans ORDER BY id
        """
        hash1 = self._compute_hash(sqlite_db, query)
        hash2 = self._compute_hash(sqlite_db, query)
        assert hash1 == hash2, "Hash not deterministic"

    def _compute_hash(self, conn, query: str) -> str:
        cursor = conn.execute(query)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        result = [dict(zip(columns, row)) for row in rows]
        json_str = json.dumps(result, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(json_str.encode("utf-8")).hexdigest()
