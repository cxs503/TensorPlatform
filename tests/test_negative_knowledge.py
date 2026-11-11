"""否定性知识台账单元测试。

测试覆盖：
1. 存储层：登记/查询/状态更新/幂等
2. 检索层：语义检索/gate 判决
3. 配置层：阈值加载
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

# 确保 tools 在 path 中
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.negative_knowledge import (
    NegativeKnowledgeConfig,
    NegativeKnowledgeSearcher,
    NegativeKnowledgeStore,
    load_config,
)
from tools.negative_knowledge.store import FAILURE_KINDS, STATUSES
from tools.negative_knowledge.search import normalize, shingles


@pytest.fixture
def store():
    """创建内存 SQLite store。"""
    db_path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE negative_knowledge_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id VARCHAR(64) NOT NULL UNIQUE,
            mission_id BIGINT,
            plan_id BIGINT,
            node_id VARCHAR(128),
            title VARCHAR(500) NOT NULL,
            hypothesis TEXT NOT NULL,
            falsification_evidence TEXT,
            failure_reason TEXT,
            failure_kind VARCHAR(64) NOT NULL,
            applicable_conditions TEXT NOT NULL DEFAULT '{}',
            keywords TEXT NOT NULL DEFAULT '[]',
            domain_tags TEXT NOT NULL DEFAULT '[]',
            differentiation_required TEXT NOT NULL DEFAULT '[]',
            source_artifacts TEXT NOT NULL DEFAULT '[]',
            confidence FLOAT NOT NULL DEFAULT 0.8,
            schema_version VARCHAR(16) NOT NULL DEFAULT '1.0',
            registered_by VARCHAR(128),
            registered_at DATETIME NOT NULL DEFAULT (datetime('now')),
            updated_at DATETIME NOT NULL DEFAULT (datetime('now')),
            status VARCHAR(24) NOT NULL DEFAULT 'active'
        )
    """)
    conn.commit()
    conn.close()

    cfg = load_config()
    from sqlalchemy import create_engine as sa_create
    engine = sa_create(f"sqlite:///{db_path}")

    store = NegativeKnowledgeStore.for_engine(engine, cfg)

    yield store

    os.unlink(db_path)


class TestNegativeKnowledgeStore:
    """存储层测试。"""

    def test_register_and_find(self, store):
        """登记并查找条目。"""
        entry, created = store.register(
            title="测试假设",
            hypothesis="这是一个测试假设",
            failure_kind="hypothesis_falsified",
            falsification_evidence="测试证据",
            failure_reason="测试根因",
            keywords=["test"],
            source_artifacts=["test.json"],
            confidence=0.9,
            registered_by="test",
        )
        assert created is True
        assert entry.title == "测试假设"
        assert entry.failure_kind == "hypothesis_falsified"
        assert entry.confidence == 0.9

        # 查找
        found = store.find_by_entry_id(entry.entry_id)
        assert found is not None
        assert found.title == "测试假设"

    def test_idempotent_register(self, store):
        """同一 mission+node 不重复登记。"""
        entry1, created1 = store.register(
            title="测试",
            hypothesis="假设",
            failure_kind="method_failed",
            mission_id=100,
            node_id="node1",
            registered_by="test",
        )
        assert created1 is True

        entry2, created2 = store.register(
            title="测试2",
            hypothesis="假设2",
            failure_kind="method_failed",
            mission_id=100,
            node_id="node1",
            registered_by="test",
        )
        assert created2 is False  # 幂等

    def test_list_entries(self, store):
        """列表查询。"""
        for i in range(5):
            store.register(
                title=f"测试{i}",
                hypothesis=f"假设{i}",
                failure_kind="hypothesis_falsified",
                registered_by="test",
            )

        entries = store.list_entries(limit=10)
        assert len(entries) == 5

    def test_update_status(self, store):
        """状态更新。"""
        entry, _ = store.register(
            title="测试",
            hypothesis="假设",
            failure_kind="blocked",
            registered_by="test",
        )
        assert entry.status == "active"

        updated = store.update_status(entry.entry_id, "superseded")
        assert updated.status == "superseded"

    def test_invalid_failure_kind(self, store):
        """非法 failure_kind 抛异常。"""
        with pytest.raises(ValueError):
            store.register(
                title="测试",
                hypothesis="假设",
                failure_kind="invalid_kind",
                registered_by="test",
            )

    def test_stats(self, store):
        """统计信息。"""
        store.register(title="A", hypothesis="H1", failure_kind="hypothesis_falsified", registered_by="test")
        store.register(title="B", hypothesis="H2", failure_kind="method_failed", registered_by="test")
        store.register(title="C", hypothesis="H3", failure_kind="hypothesis_falsified", registered_by="test")

        stats = store.stats()
        assert stats["total"] == 3
        assert stats["by_failure_kind"]["hypothesis_falsified"] == 2
        assert stats["by_failure_kind"]["method_failed"] == 1


class TestSearchHelpers:
    """检索辅助函数测试。"""

    def test_normalize(self):
        """归一化。"""
        assert normalize("Hello World") == "hello world"
        assert normalize("**bold**") == "bold"
        assert normalize("测试") == "测试"

    def test_shingles(self):
        """n-gram 切分。"""
        s = shingles("hello", 3)
        assert "hel" in s
        assert "ell" in s
        assert "llo" in s
        assert len(s) == 3

    def test_shingles_short(self):
        """短文本 shingle。"""
        s = shingles("ab", 4)
        assert s == frozenset({"ab"})


class TestNegativeKnowledgeSearcher:
    """检索层测试。"""

    def test_search_basic(self, store):
        """基本检索——用足够长的查询文本。"""
        store.register(
            title="Sobol序列问题",
            hypothesis="Sobol序列可以用于AB估计",
            failure_kind="hypothesis_falsified",
            falsification_evidence="散度恶化",
            keywords=["sobol", "sequence"],
            registered_by="test",
        )
        store.register(
            title="校准问题",
            hypothesis="Platt校准有效",
            failure_kind="method_failed",
            falsification_evidence="AUC翻转",
            keywords=["calibration", "platt"],
            registered_by="test",
        )

        cfg = load_config()
        searcher = NegativeKnowledgeSearcher(store, cfg)
        # 用较长的查询文本（包含 sobol 序列）
        hits = searcher.search("Sobol序列用于估计的问题", top_k=5, min_score=0.1)
        assert len(hits) >= 1
        # 第一个命中应该是 Sobol 相关条目
        assert "sobol" in " ".join(hits[0].entry.keywords).lower()

    def test_gate_check_pass(self, store):
        """Gate 判决：通过。"""
        store.register(
            title="不相关条目",
            hypothesis="不相关假设",
            failure_kind="blocked",
            keywords=["unrelated"],
            registered_by="test",
        )

        cfg = load_config()
        searcher = NegativeKnowledgeSearcher(store, cfg)
        verdict = searcher.gate_check(
            proposal_title="全新方法",
            proposal_description="这是一个全新的方法",
            proposal_keywords=["new", "method"],
        )
        assert verdict.action == "pass"

    def test_gate_check_reject(self, store):
        """Gate 判决：拒绝（高度相似）。"""
        store.register(
            title="Sobol序列不能用于AB估计",
            hypothesis="Sobol序列可以用于AB估计",
            failure_kind="hypothesis_falsified",
            falsification_evidence="散度从24%恶化到500%",
            keywords=["sobol", "sequence", "ab"],
            registered_by="test",
        )

        cfg = load_config()
        searcher = NegativeKnowledgeSearcher(store, cfg)
        # 用几乎相同的标题做查询
        verdict = searcher.gate_check(
            proposal_title="Sobol序列用于AB估计",
            proposal_description="使用Sobol序列做AB估计",
            proposal_keywords=["sobol", "sequence"],
        )
        # 由于标题高度相似，应该触发 review 或 reject
        assert verdict.action in ("review", "reject")


class TestConfig:
    """配置层测试。"""

    def test_load_config(self):
        """配置加载。"""
        cfg = load_config()
        assert cfg.gate_reject_threshold > 0
        assert cfg.gate_review_threshold > 0
        assert cfg.search_min_score > 0

    def test_failure_kinds(self):
        """失败类型枚举。"""
        assert "hypothesis_falsified" in FAILURE_KINDS
        assert "method_failed" in FAILURE_KINDS
        assert "blocked" in FAILURE_KINDS
        assert "infeasible" in FAILURE_KINDS
        assert "regression" in FAILURE_KINDS
        assert len(FAILURE_KINDS) == 5

    def test_statuses(self):
        """状态枚举。"""
        assert "active" in STATUSES
        assert "superseded" in STATUSES
        assert "retracted" in STATUSES
        assert len(STATUSES) == 3
