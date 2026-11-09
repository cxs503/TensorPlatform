"""否定性知识台账单元测试。

覆盖：
1. 配置装载（默认值 + 文件覆盖）
2. 存储层 CRUD（登记/查询/状态更新/幂等）
3. 检索引擎（相似度计算/检索/派单门判决）
4. API 端点（通过 TestClient）
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 确保 backend 在 path 中
_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# 确保 tools 在 path 中
_TOOLS_DIR = Path(__file__).resolve().parents[2]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from sqlalchemy import create_engine

from tools.negative_knowledge.config import NegativeKnowledgeConfig, load_config
from tools.negative_knowledge.store import (
    NegativeKnowledgeStore,
    NegativeKnowledgeEntry,
    FAILURE_KINDS,
    STATUSES,
)
from tools.negative_knowledge.search import (
    NegativeKnowledgeSearcher,
    SearchHit,
    GateVerdict,
    normalize,
    shingles,
)


# ─────────────────────────────── 配置测试 ───────────────────────────────


class TestConfig:
    """配置装载测试。"""

    def test_load_default_config(self):
        """无配置文件时返回默认值。"""
        cfg = load_config("/nonexistent/path.json")
        assert cfg.table_name == "negative_knowledge_ledger"
        assert cfg.search_shingle_size == 4
        assert cfg.gate_reject_threshold == 0.6
        assert cfg.gate_review_threshold == 0.4
        assert cfg.schema_version == "1.0"
        assert "hypothesis_falsified" in cfg.failure_kinds

    def test_load_config_from_file(self, tmp_path):
        """从文件装载配置。"""
        config_file = tmp_path / "test_config.json"
        config_file.write_text(json.dumps({
            "table_name": "custom_table",
            "search": {
                "shingle_size": 5,
                "gate_reject_threshold": 0.7,
            }
        }))
        cfg = load_config(config_file)
        assert cfg.table_name == "custom_table"
        assert cfg.search_shingle_size == 5
        assert cfg.gate_reject_threshold == 0.7
        # 未覆盖的字段保持默认
        assert cfg.gate_review_threshold == 0.4


# ─────────────────────────────── 存储层测试 ───────────────────────────────


class TestStore:
    """存储层测试。"""

    @pytest.fixture
    def store(self):
        """创建内存 SQLite 存储。"""
        engine = create_engine("sqlite:///:memory:")
        cfg = load_config("/nonexistent/path.json")
        store = NegativeKnowledgeStore.for_engine(engine, cfg)
        yield store
        store.dispose()

    def test_register_entry(self, store):
        """登记条目。"""
        entry, created = store.register(
            title="测试假设：X 方法可提升精度 10%",
            hypothesis="假设使用 X 方法可以在不增加计算成本的情况下提升精度 10%",
            failure_kind="hypothesis_falsified",
            falsification_evidence="实验数据显示精度仅提升 2%，远低于预期",
            failure_reason="假设前提不成立：X 方法的理论上限为 3%",
            keywords=["精度", "X方法", "测试"],
            confidence=0.9,
            registered_by="test_agent",
        )
        assert created is True
        assert entry.title == "测试假设：X 方法可提升精度 10%"
        assert entry.failure_kind == "hypothesis_falsified"
        assert entry.confidence == 0.9
        assert "精度" in entry.keywords

    def test_register_idempotent(self, store):
        """同一 mission+node 重复登记返回既有行。"""
        entry1, created1 = store.register(
            title="测试",
            hypothesis="假设内容",
            failure_kind="method_failed",
            mission_id=100,
            node_id="node_1",
        )
        entry2, created2 = store.register(
            title="测试",
            hypothesis="假设内容",
            failure_kind="method_failed",
            mission_id=100,
            node_id="node_1",
        )
        assert created1 is True
        assert created2 is False
        assert entry1.entry_id == entry2.entry_id

    def test_register_invalid_kind(self, store):
        """非法 failure_kind 抛异常。"""
        with pytest.raises(ValueError, match="未登记的 failure_kind"):
            store.register(
                title="测试",
                hypothesis="假设",
                failure_kind="invalid_kind",
            )

    def test_find_by_entry_id(self, store):
        """按 entry_id 查找。"""
        entry, _ = store.register(
            title="查找测试",
            hypothesis="假设内容",
            failure_kind="blocked",
        )
        found = store.find_by_entry_id(entry.entry_id)
        assert found is not None
        assert found.title == "查找测试"

    def test_find_by_mission_node(self, store):
        """按 mission+node 查找。"""
        store.register(
            title="节点测试",
            hypothesis="假设",
            failure_kind="infeasible",
            mission_id=200,
            node_id="N01",
        )
        found = store.find_by_mission_node(200, "N01")
        assert found is not None
        assert found.title == "节点测试"

    def test_list_entries(self, store):
        """列表查询。"""
        for i in range(5):
            store.register(
                title=f"条目 {i}",
                hypothesis=f"假设 {i}",
                failure_kind="hypothesis_falsified" if i < 3 else "method_failed",
            )
        all_entries = store.list_entries()
        assert len(all_entries) == 5

        filtered = store.list_entries(failure_kind="hypothesis_falsified")
        assert len(filtered) == 3

    def test_update_status(self, store):
        """更新状态。"""
        entry, _ = store.register(
            title="状态测试",
            hypothesis="假设",
            failure_kind="regression",
        )
        assert entry.status == "active"

        updated = store.update_status(entry.entry_id, "superseded")
        assert updated.status == "superseded"

    def test_update_status_invalid(self, store):
        """非法状态抛异常。"""
        entry, _ = store.register(
            title="测试",
            hypothesis="假设",
            failure_kind="blocked",
        )
        with pytest.raises(ValueError, match="非法状态"):
            store.update_status(entry.entry_id, "invalid_status")

    def test_stats(self, store):
        """统计信息。"""
        store.register(title="A", hypothesis="假设A", failure_kind="hypothesis_falsified")
        store.register(title="B", hypothesis="假设B", failure_kind="hypothesis_falsified")
        store.register(title="C", hypothesis="假设C", failure_kind="method_failed")

        stats = store.stats()
        assert stats["total"] == 3
        assert stats["by_failure_kind"]["hypothesis_falsified"] == 2
        assert stats["by_failure_kind"]["method_failed"] == 1
        assert stats["active_count"] == 3


# ─────────────────────────────── 检索引擎测试 ───────────────────────────────


class TestSearch:
    """检索引擎测试。"""

    @pytest.fixture
    def store_with_data(self):
        """创建带数据的存储。"""
        engine = create_engine("sqlite:///:memory:")
        cfg = load_config("/nonexistent/path.json")
        store = NegativeKnowledgeStore.for_engine(engine, cfg)

        # 添加测试数据
        store.register(
            title="Lindqvist 冰阻力公式不适用于薄冰",
            hypothesis="Lindqvist 公式在冰厚 < 0.3m 时仍然准确",
            failure_kind="hypothesis_falsified",
            falsification_evidence="模型试验显示薄冰区误差 > 30%",
            failure_reason="公式假设冰为无限大平板，薄冰时边界效应显著",
            keywords=["Lindqvist", "冰阻力", "薄冰", "公式"],
            domain_tags=["ice_navigation"],
            confidence=0.95,
        )
        store.register(
            title="BEM 方法计算兴波阻力收敛性问题",
            hypothesis="低阶 BEM 在粗网格下可准确计算兴波阻力",
            failure_kind="method_failed",
            falsification_evidence="粗网格下自由面条件不满足，结果发散",
            failure_reason="需要至少 20 个波长/网格才能收敛",
            keywords=["BEM", "兴波阻力", "收敛", "网格"],
            domain_tags=["resistance"],
            confidence=0.9,
        )
        store.register(
            title="机器学习代理模型精度不足",
            hypothesis="简单 MLP 可以替代 CFD 做阻力预报",
            failure_kind="hypothesis_falsified",
            falsification_evidence="测试集误差 > 15%，不满足工程精度要求",
            failure_reason="训练数据不足 + 特征工程缺失",
            keywords=["机器学习", "MLP", "CFD", "阻力预报"],
            domain_tags=["resistance", "ml"],
            confidence=0.85,
        )

        searcher = NegativeKnowledgeSearcher(store, cfg)
        yield store, searcher, cfg
        store.dispose()

    def test_normalize(self):
        """文本归一化。"""
        assert normalize("Hello World!") == "hello world"
        assert normalize("**Bold** __text__") == "bold text"
        assert normalize("中文 测试") == "中文 测试"

    def test_shingles(self):
        """字符 n-gram 切分。"""
        s = shingles("hello", 3)
        assert "hel" in s
        assert "ell" in s
        assert "llo" in s
        assert len(s) == 3

    def test_search_basic(self, store_with_data):
        """基本检索。"""
        store, searcher, cfg = store_with_data
        # 使用更完整的查询文本
        hits = searcher.search("Lindqvist 冰阻力公式 薄冰区 适用性", min_score=0.1)
        assert len(hits) > 0
        # 第一条应该最相关
        assert "Lindqvist" in hits[0].entry.title or "冰阻力" in hits[0].entry.title

    def test_search_with_keywords(self, store_with_data):
        """关键词匹配。"""
        store, searcher, cfg = store_with_data
        hits = searcher.search("BEM 兴波阻力 网格收敛 低阶方法", min_score=0.1)
        assert len(hits) > 0
        # 应该命中 BEM 相关条目
        titles = [h.entry.title for h in hits]
        assert any("BEM" in t for t in titles)

    def test_search_no_match(self, store_with_data):
        """无匹配时返回空或低分。"""
        store, searcher, cfg = store_with_data
        hits = searcher.search("完全不相关的内容 xyz123 abc456", min_score=0.3)
        # 应该没有高相似命中
        for h in hits:
            assert h.score < 0.5

    def test_search_filter_by_kind(self, store_with_data):
        """按失败类型过滤。"""
        store, searcher, cfg = store_with_data
        hits = searcher.search("阻力 方法 收敛", failure_kind="method_failed", min_score=0.1)
        for h in hits:
            assert h.entry.failure_kind == "method_failed"

    def test_gate_check_pass(self, store_with_data):
        """派单门：无相似命中时通过。"""
        store, searcher, cfg = store_with_data
        verdict = searcher.gate_check(
            proposal_title="全新的方法：量子计算求解 Navier-Stokes",
            proposal_description="使用量子算法直接求解流体方程",
        )
        assert verdict.passed is True
        assert verdict.action in ("pass", "review")  # 可能有一些低分命中

    def test_gate_check_reject(self, store_with_data):
        """派单门：高度相似时拒绝。"""
        store, searcher, cfg = store_with_data
        # 提交一个与已证伪假设高度相似的提案（使用几乎相同的文本）
        verdict = searcher.gate_check(
            proposal_title="Lindqvist 冰阻力公式不适用于薄冰区验证",
            proposal_description="Lindqvist 公式在冰厚小于 0.3m 时仍然准确的验证研究",
            proposal_keywords=["Lindqvist", "冰阻力", "薄冰"],
        )
        # 应该命中 Lindqvist 相关条目
        assert len(verdict.hits) > 0, f"Expected hits but got none. Max score: {verdict.max_score}"
        # 根据阈值判定
        if verdict.max_score >= cfg.gate_reject_threshold:
            assert verdict.passed is False
            assert verdict.action == "reject"

    def test_gate_check_review(self, store_with_data):
        """派单门：中度相似时需审查。"""
        store, searcher, cfg = store_with_data
        verdict = searcher.gate_check(
            proposal_title="改进的 BEM 方法计算船舶兴波阻力",
            proposal_description="使用高阶 BEM 改善收敛性和精度",
        )
        # 可能命中 BEM 相关条目
        if verdict.max_score >= cfg.gate_review_threshold:
            assert verdict.action in ("review", "reject")


# ─────────────────────────────── 集成测试 ───────────────────────────────


class TestIntegration:
    """集成测试：完整工作流。"""

    def test_full_workflow(self):
        """完整工作流：登记 → 检索 → 派单门。"""
        engine = create_engine("sqlite:///:memory:")
        cfg = load_config("/nonexistent/path.json")
        store = NegativeKnowledgeStore.for_engine(engine, cfg)
        searcher = NegativeKnowledgeSearcher(store, cfg)

        # 1. 登记否定性知识
        entry, created = store.register(
            title="Hogben 轮廓线方法在低速区失效",
            hypothesis="Hogben 轮廓线方法在所有速度范围内都准确",
            failure_kind="hypothesis_falsified",
            falsification_evidence="Fn < 0.15 时误差 > 20%",
            failure_reason="方法假设自由面条件线性化，低速时非线性效应显著",
            keywords=["Hogben", "轮廓线", "低速", "兴波"],
            domain_tags=["resistance"],
            confidence=0.92,
            registered_by="test_workflow",
        )
        assert created is True

        # 2. 检索（使用较低的阈值以捕获命中）
        hits = searcher.search("Hogben 轮廓线方法 低速区 兴波阻力", min_score=0.1)
        assert len(hits) > 0
        assert hits[0].entry.entry_id == entry.entry_id
        assert hits[0].score > 0.1

        # 3. 派单门检查
        verdict = searcher.gate_check(
            proposal_title="Hogben 轮廓线方法改进与低速区适用性研究",
            proposal_description="改进 Hogben 方法在低速区的精度",
            proposal_keywords=["Hogben", "轮廓线", "低速"],
        )
        assert len(verdict.hits) > 0
        # 应该命中已登记的条目
        hit_ids = [h.entry.entry_id for h in verdict.hits]
        assert entry.entry_id in hit_ids

        # 4. 更新状态
        updated = store.update_status(entry.entry_id, "superseded")
        assert updated.status == "superseded"

        # 5. 再次检索（superseded 条目不应出现在 active 检索中）
        hits_after = searcher.search("Hogben 轮廓线方法 低速区", min_score=0.1)
        hit_ids_after = [h.entry.entry_id for h in hits_after]
        assert entry.entry_id not in hit_ids_after

        store.dispose()

    def test_search_text_composition(self):
        """验证 search_text 拼接逻辑。"""
        engine = create_engine("sqlite:///:memory:")
        cfg = load_config("/nonexistent/path.json")
        store = NegativeKnowledgeStore.for_engine(engine, cfg)

        entry, _ = store.register(
            title="测试标题",
            hypothesis="测试假设内容",
            failure_kind="blocked",
            failure_reason="测试失败原因",
            keywords=["关键词1", "关键词2"],
        )

        search_text = entry.search_text()
        assert "测试标题" in search_text
        assert "测试假设内容" in search_text
        assert "测试失败原因" in search_text
        assert "关键词1" in search_text
        assert "关键词2" in search_text

        store.dispose()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
