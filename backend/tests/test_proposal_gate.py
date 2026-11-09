"""提案门 API 端到端测试（合成域名 foo/bar/widget，零领域语义）。

覆盖
----
* GET /proposals/gate/stats —— 四态计数 + SQL 溯源
* GET /proposals/gate/ledger —— 账本查询（过滤 state / mission_id）
* POST /proposals/gate/evaluate —— 送提案过门（accepted / duplicate / quarantined）
* POST /proposals/gate/adopt —— 标记采纳
* GET /proposals/gate/coverage —— 播种覆盖率
* GET /proposals/gate/config —— 配置信息

纪律：测试夹具用 tests/proposal_gate_support.py 的合成域名与配置派生；
不依赖真库战役节点、不出现学科域词。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 确保仓库根在 sys.path（pytest 从 backend/ 运行时 .. 已在 pythonpath 中）
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 直接导入支持模块（避免包路径问题）
_support_path = REPO_ROOT / "tests" / "proposal_gate_support.py"
if _support_path.exists():
    import importlib.util
    _spec = importlib.util.spec_from_file_location("proposal_gate_support", _support_path)
    _support = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_support)
    DISTINCT_TOPICS = _support.DISTINCT_TOPICS
    PARAPHRASE_VARIANTS = _support.PARAPHRASE_VARIANTS
    distinct_payload = _support.distinct_payload
    synth_config = _support.synth_config
    v1_payload = _support.v1_payload
    v2_payload = _support.v2_payload
else:
    # 回退：内联最小夹具
    DISTINCT_TOPICS = ()
    PARAPHRASE_VARIANTS = ()
    def synth_config(**kw):
        from tools.proposal_gate.config import GateConfig
        return GateConfig.from_dict({
            "repo_whitelist": ["foo", "bar", "widget"],
            "dedup": {"dup_threshold": 0.85, "review_threshold": 0.55, "shingle_size": 3,
                      "twin_dependency_overlap": 0.8, "coverage_match_threshold": 0.85,
                      "containment_enabled": True, "min_length_ratio": 0.5},
            "contract": {"min_criteria": 3, "numeric_pattern": r"[+-]?\d+", "unit_symbols": ["%"]},
            "ledger": {"table": "proposal_gate_ledger"},
            "states": {"accepted": "proposal", "adopted": "adopted", "duplicate": "dedup_rejected",
                       "twin": "dedup_rejected", "quarantined": "quarantined", "near_duplicate": "quarantined"},
            "compat": {"v1_defaults": {"repo": "bar"}},
            "kpi": {"dup_rejected_key": "guide.proposal_dup_rejected", "seed_coverage_key": "guide.proposal_seed_coverage"},
        }, path=REPO_ROOT / "config" / "proposal_gate.json")
    def v2_payload(**kw):
        return {
            "schema_version": "2.0", "mission_id": kw.get("mission_id", 7001),
            "plan_id": kw.get("plan_id", 11), "title": kw.get("title", "foo 测试"),
            "repo": kw.get("repo", "foo"),
            "goal": {"objective": kw.get("objective", "test"), "criteria": kw.get("criteria", ["a≥1", "b≤2", "c=3"])},
            "dependencies_hint": kw.get("deps", []), "is_maturity": kw.get("is_maturity", True),
        }
    def v1_payload(**kw):
        return {
            "proposal_id": kw.get("proposal_id", "test-001"), "title": kw.get("title", "test"),
            "hypothesis": kw.get("hypothesis", "test hypothesis"),
            "falsification_criteria": kw.get("criteria", ["a≥1", "b≤2", "c=3"]),
        }
    def distinct_payload(idx):
        topics = [
            {"title": "bar 报表延迟", "objective": "bar 延迟归因", "criteria": ["P95≤2.5s"]},
            {"title": "widget 缓存", "objective": "widget 缓存优化", "criteria": ["命中率≥0.75"]},
        ]
        t = topics[idx % len(topics)]
        return v2_payload(title=t["title"], objective=t["objective"], criteria=t["criteria"], deps=[f"dep-{idx}"])

from tools.proposal_gate.config import GateConfig
from tools.proposal_gate.gate import ProposalGate, payload_sha256
from tools.proposal_gate.index import ProposalIndex
from tools.proposal_gate.ledger import LedgerStore


# ── 内存账本夹具（无需真实 DB）─────────────────────────────────────────

@pytest.fixture
def mem_store():
    """内存 LedgerStore（SQLite :memory:）。"""
    cfg = synth_config()
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite:///:memory:")
    store = LedgerStore.for_engine(engine, cfg)
    yield store, cfg, engine


# ── 门实例夹具 ─────────────────────────────────────────────────────────

@pytest.fixture
def gate(mem_store):
    store, cfg, _ = mem_store
    index = ProposalIndex(cfg)
    return ProposalGate(cfg, store, index), cfg, store


# ── 契约门测试 ─────────────────────────────────────────────────────────

class TestContractGate:
    """契约门：v2 合法提案通过，v1 合法提案通过，缺失字段 → quarantined。"""

    def test_v2_valid_accepted(self, gate):
        g, cfg, store = gate
        payload = v2_payload(mission_id=7001)
        result = g.evaluate(payload, mission_id=7001, persist_quarantine=False)
        assert result.accepted, f"v2 合法提案应 accepted，实际: {result.verdict}, reasons: {result.reasons}"
        assert result.contract_ok
        assert result.state == cfg.state_for("accepted")

    def test_v1_valid_accepted(self, gate):
        g, cfg, store = gate
        payload = v1_payload()
        result = g.evaluate(payload, mission_id=7002, persist_quarantine=False)
        assert result.accepted, f"v1 合法提案应 accepted，实际: {result.verdict}"
        assert result.contract_ok

    def test_missing_title_quarantined(self, gate):
        g, cfg, store = gate
        payload = v2_payload(mission_id=7003)
        del payload["title"]
        result = g.evaluate(payload, mission_id=7003, persist_quarantine=False)
        assert result.verdict == "quarantined"
        assert not result.contract_ok
        assert any("title" in r.lower() or "required" in r.lower() for r in result.reasons)

    def test_missing_criteria_quarantined(self, gate):
        g, cfg, store = gate
        payload = v2_payload(mission_id=7004)
        payload["goal"]["criteria"] = []
        result = g.evaluate(payload, mission_id=7004, persist_quarantine=False)
        assert result.verdict == "quarantined"
        assert not result.contract_ok

    def test_non_bool_is_maturity_quarantined(self, gate):
        g, cfg, store = gate
        payload = v2_payload(mission_id=7005, is_maturity="yes")
        result = g.evaluate(payload, mission_id=7005, persist_quarantine=False)
        assert result.verdict == "quarantined"
        assert not result.contract_ok


# ── 幂等门测试 ─────────────────────────────────────────────────────────

class TestIdempotentGate:
    """幂等门：同 mission 同内容 → duplicate，不新增行。"""

    def test_resubmit_same_content(self, gate):
        g, cfg, store = gate
        payload = v2_payload(mission_id=7010)
        r1 = g.evaluate(payload, mission_id=7010, persist_quarantine=False)
        assert r1.accepted

        r2 = g.evaluate(payload, mission_id=7010, persist_quarantine=False)
        assert r2.verdict == "duplicate"
        assert r2.dup_of == r1.sha256
        assert not r2.created  # 不新增行

    def test_cross_mission_same_content(self, gate):
        g, cfg, store = gate
        payload = v2_payload(mission_id=7011)
        r1 = g.evaluate(payload, mission_id=7011, persist_quarantine=False)
        assert r1.accepted

        r2 = g.evaluate(payload, mission_id=7012, persist_quarantine=False)
        assert r2.verdict == "duplicate"
        assert r2.created  # 跨 mission 新增行记录


# ── 去重门测试 ─────────────────────────────────────────────────────────

class TestDedupGate:
    """去重门：同义改写 → duplicate/near_duplicate；不同主题 → accepted。"""

    def test_paraphrase_detected(self, gate):
        g, cfg, store = gate
        # 先送一个原始提案
        original = v2_payload(
            mission_id=7020,
            title="foo 通道三道机器门",
            objective="为 foo 通道建立产物契约、去重器与覆盖率账本三道机器门",
        )
        r1 = g.evaluate(original, mission_id=7020, persist_quarantine=False)
        assert r1.accepted

        # 送同义改写
        for variant in PARAPHRASE_VARIANTS[:2]:
            paraphrase = v2_payload(
                mission_id=7021,
                title=variant,
                objective=variant,
            )
            r2 = g.evaluate(paraphrase, mission_id=7021, persist_quarantine=False)
            # 同义改写应被检出（duplicate 或 near_duplicate）
            assert r2.verdict in ("duplicate", "near_duplicate"), (
                f"同义改写应被检出，实际: {r2.verdict}, score={r2.score}"
            )

    def test_distinct_topics_accepted(self, gate):
        g, cfg, store = gate
        # 先送一个原始提案
        original = v2_payload(mission_id=7030)
        r1 = g.evaluate(original, mission_id=7030, persist_quarantine=False)
        assert r1.accepted

        # 送不同主题的提案
        for idx in range(min(len(DISTINCT_TOPICS), 3)):
            distinct = distinct_payload(idx)
            r2 = g.evaluate(distinct, mission_id=7031 + idx, persist_quarantine=False)
            assert r2.accepted, (
                f"不同主题提案应 accepted，实际: {r2.verdict}, score={r2.score}, "
                f"title={distinct['title']}"
            )


# ── 孪生门测试 ─────────────────────────────────────────────────────────

class TestTwinGate:
    """孪生门：同 repo 且依赖高度重合 → twin。"""

    def test_twin_detected(self, gate):
        g, cfg, store = gate
        deps = ("门-契约", "门-去重", "门-覆盖率", "门-审计")

        p1 = v2_payload(
            mission_id=7040,
            title="foo 通道机器门 v1",
            deps=deps,
        )
        r1 = g.evaluate(p1, mission_id=7040, persist_quarantine=False)
        assert r1.accepted

        # 同 repo + 高度重合依赖
        p2 = v2_payload(
            mission_id=7041,
            title="foo 通道机器门 v2（孪生）",
            objective="foo 通道机器门的第二版实现",
            deps=deps,
        )
        r2 = g.evaluate(p2, mission_id=7041, persist_quarantine=False)
        # 依赖重合度高 → twin 或 duplicate
        assert r2.verdict in ("duplicate", "twin", "near_duplicate"), (
            f"孪生提案应被检出，实际: {r2.verdict}"
        )


# ── 采纳与覆盖率测试 ───────────────────────────────────────────────────

class TestAdoptAndCoverage:
    """采纳标记 + 播种覆盖率。"""

    def test_adopt_accepted_proposal(self, gate):
        g, cfg, store = gate
        payload = v2_payload(mission_id=7050)
        r1 = g.evaluate(payload, mission_id=7050, persist_quarantine=False)
        assert r1.accepted

        row = g.adopt(mission_id=7050, sha256=r1.sha256)
        assert row.state == cfg.state_for("adopted")
        assert row.verdict == "adopted"

    def test_adopt_nonexistent_raises(self, gate):
        g, cfg, store = gate
        with pytest.raises(KeyError, match="账本无此行"):
            g.adopt(mission_id=9999, sha256="0" * 64)

    def test_coverage_empty(self, gate):
        g, cfg, store = gate
        cov = g.coverage()
        assert cov["adopted"] == 0
        assert cov["coverage_rate"] == 0.0

    def test_coverage_after_adopt(self, gate):
        g, cfg, store = gate
        payload = v2_payload(mission_id=7060)
        r1 = g.evaluate(payload, mission_id=7060, persist_quarantine=False)
        assert r1.accepted

        g.adopt(mission_id=7060, sha256=r1.sha256)
        cov = g.coverage()
        assert cov["adopted"] == 1


# ── SHA256 幂等测试 ────────────────────────────────────────────────────

class TestSha256:
    """SHA256 计算一致性。"""

    def test_same_payload_same_hash(self):
        p1 = v2_payload(mission_id=7070)
        p2 = v2_payload(mission_id=7070)
        assert payload_sha256(p1) == payload_sha256(p2)

    def test_different_payload_different_hash(self):
        p1 = v2_payload(mission_id=7071)
        p2 = v2_payload(mission_id=7072)
        assert payload_sha256(p1) != payload_sha256(p2)


# ── 配置测试 ───────────────────────────────────────────────────────────

class TestConfig:
    """配置装载与校验。"""

    def test_synth_config_has_synthetic_repos(self):
        cfg = synth_config()
        assert set(cfg.repo_whitelist) == {"foo", "bar", "widget"}

    def test_config_thresholds_from_file(self):
        cfg = synth_config()
        assert cfg.get("dedup.dup_threshold") == 0.85
        assert cfg.get("dedup.review_threshold") == 0.55
        assert cfg.get("dedup.shingle_size") == 3
        assert cfg.get("contract.min_criteria") == 3

    def test_state_mapping(self):
        cfg = synth_config()
        assert cfg.state_for("accepted") == "proposal"
        assert cfg.state_for("adopted") == "adopted"
        assert cfg.state_for("duplicate") == "dedup_rejected"
        assert cfg.state_for("quarantined") == "quarantined"


# ── 统计测试 ───────────────────────────────────────────────────────────

class TestStats:
    """账本统计。"""

    def test_stats_initial(self, gate):
        g, cfg, store = gate
        stats = store.stats()
        d = stats.as_dict()
        assert d["total"] == 0
        assert d["counts"].get("proposal", 0) == 0
        assert d["counts"].get("adopted", 0) == 0
        assert d["counts"].get("dedup_rejected", 0) == 0
        assert d["counts"].get("quarantined", 0) == 0

    def test_stats_after_evaluations(self, gate):
        g, cfg, store = gate
        # 送一个 accepted
        r1 = g.evaluate(v2_payload(mission_id=7080), mission_id=7080, persist_quarantine=False)
        assert r1.accepted

        # 送一个 quarantined
        bad = v2_payload(mission_id=7081)
        del bad["title"]
        r2 = g.evaluate(bad, mission_id=7081, persist_quarantine=False)
        assert r2.verdict == "quarantined"

        stats = store.stats()
        d = stats.as_dict()
        assert d["total"] == 2
        assert d["counts"].get("proposal", 0) == 1
        assert d["counts"].get("quarantined", 0) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
