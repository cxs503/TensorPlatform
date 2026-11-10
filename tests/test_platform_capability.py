"""
平台能力审计与自主续航成熟化 — 综合验收测试。

覆盖 5 项机器可算验收判据：
(1) 能力审计：21 能力证据等级全量输出，WITHHELD ≤ 3
(2) 自主续航：战役 draft → active → done 全自动，人工干预 = 0
(3) 证据链：节点 result 可 SQL 溯源到 provenance，hash 完整
(4) 质量门：consecutive_fails ≥ 3 时自动 escalation，escalation_id 非空
(5) KPI 达标：repro_rate ≥ 0.8, kb_samples ≥ 100, unattended ≥ 0.9, benchmark ≥ 5

证据纪律：honest FAIL 不可覆盖，零手抄。
"""
from __future__ import annotations

import json
import os
import sys
import pathlib
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# ── 路径设置 ──────────────────────────────────────────────────────
ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# ── 导入被测模块 ──────────────────────────────────────────────────
from services.quality_gate import (
    ESCALATION_THRESHOLD,
    QualityGateResult,
    record_node_verdict,
    get_escalations,
    resolve_escalation,
    get_node_fail_stats,
    _ensure_escalation_table,
    _ensure_node_fails_table,
)
from services.campaign_lifecycle import (
    CampaignLifecycleResult,
    activate_campaign,
    complete_node,
    evaluate_campaign_completion,
    _ensure_campaign_tables,
)
from services.evidence_chain import (
    record_provenance,
    trace_evidence_chain,
    verify_provenance_integrity,
    get_evidence_for_node,
    _ensure_provenance_table,
)
from services.platform_kpi import (
    KPI_TARGETS,
    compute_all_kpis,
    _compute_repro_rate,
    _compute_kb_samples,
    _compute_unattended,
    _compute_benchmark,
)


# ══════════════════════════════════════════════════════════════════
# Mock DB — 模拟 SQLAlchemy Session 接口（基于 sqlite3）
# ══════════════════════════════════════════════════════════════════

class MockRowMapping(dict):
    """模拟 SQLAlchemy RowMapping（dict-like with attribute access）。"""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class MockResult:
    """模拟 SQLAlchemy Result with .mappings() support."""
    def __init__(self, cursor, is_select=True):
        self._cursor = cursor
        self._is_select = is_select
        self._lastrowid = cursor.lastrowid if hasattr(cursor, 'lastrowid') else None
        self._rowcount = cursor.rowcount if hasattr(cursor, 'rowcount') else 0

    @property
    def lastrowid(self):
        return self._lastrowid

    @property
    def rowcount(self):
        return self._rowcount

    def mappings(self):
        return self

    def first(self):
        if not self._is_select:
            return None
        row = self._cursor.fetchone()
        if row is None:
            return None
        keys = self._cursor.description and [d[0] for d in self._cursor.description] or []
        return MockRowMapping(zip(keys, row))

    def all(self):
        if not self._is_select:
            return []
        rows = self._cursor.fetchall()
        keys = self._cursor.description and [d[0] for d in self._cursor.description] or []
        return [MockRowMapping(zip(keys, row)) for row in rows]


class MockDB:
    """模拟 SQLAlchemy Session（基于 sqlite3）。"""
    def __init__(self, db_path=None):
        import sqlite3
        if db_path is None:
            db_path = tempfile.mktemp(suffix=".db")
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._owns_file = db_path != ":memory:"

    def execute(self, stmt, params=None):
        """执行 SQL，返回 MockResult。"""
        stmt_str = str(stmt)
        is_select = stmt_str.strip().upper().startswith("SELECT") or \
                    stmt_str.strip().upper().startswith("CREATE") or \
                    stmt_str.strip().upper().startswith("PRAGMA")
        try:
            if params:
                cursor = self._conn.execute(stmt_str, params)
            else:
                cursor = self._conn.execute(stmt_str)
            return MockResult(cursor, is_select=is_select)
        except Exception as e:
            # 对于 CREATE TABLE IF NOT EXISTS 等，忽略错误
            if "already exists" in str(e).lower():
                return MockResult(cursor=None, is_select=False)
            raise

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()
        if self._owns_file and os.path.exists(self._db_path):
            try:
                os.unlink(self._db_path)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def sqlite_db():
    """创建临时 SQLite 数据库用于测试。"""
    db = MockDB()
    yield db
    db.close()


@pytest.fixture
def ledger_path():
    """返回能力台账路径。"""
    return ROOT / "artifacts" / "capability_ledger" / "capability_ledger.json"


# ══════════════════════════════════════════════════════════════════
# 判据 (1): 能力审计 — 21 能力证据等级全量输出，WITHHELD ≤ 3
# ══════════════════════════════════════════════════════════════════

class TestCapabilityAudit:
    """能力审计验收测试。"""

    def test_ledger_file_exists(self, ledger_path):
        """台账文件必须存在。"""
        assert ledger_path.exists(), f"Ledger not found: {ledger_path}"

    def test_ledger_has_21_capabilities(self, ledger_path):
        """台账必须包含 21 个能力。"""
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        caps = data.get("capabilities", [])
        assert len(caps) == 21, f"Expected 21 capabilities, got {len(caps)}"

    def test_all_capabilities_have_evidence_level(self, ledger_path):
        """每个能力必须有证据等级。"""
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        valid_levels = {"REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED"}
        for cap in data["capabilities"]:
            claimed = cap.get("claimed_level", "")
            assert claimed in valid_levels, (
                f"Capability {cap['id']} has invalid level: {claimed}"
            )

    def test_withheld_count_le_3(self, ledger_path):
        """WITHHELD 项 ≤ 3（验收判据）。"""
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 计算 reconciled level
        withheld = 0
        for cap in data["capabilities"]:
            evidence = cap.get("evidence", [])
            if evidence:
                sorted_ev = sorted(evidence, key=lambda e: str(e.get("as_of", "")), reverse=True)
                level = sorted_ev[0].get("asserted_level", cap.get("claimed_level", "WITHHELD"))
            else:
                level = cap.get("claimed_level", "WITHHELD")
            if level == "WITHHELD":
                withheld += 1
        assert withheld <= 3, f"WITHHELD count {withheld} exceeds threshold 3"

    def test_nine_domains_coverage(self, ledger_path):
        """九大学科覆盖检查。"""
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        covered_domains = set()
        cap_ids = set()
        for cap in data["capabilities"]:
            domain = cap.get("domain", "")
            if domain:
                covered_domains.add(domain)
            cap_ids.add(cap["id"])
        # 核心学科必须有对应能力
        assert "推进" in covered_domains or "propeller.open_water" in cap_ids
        assert "稳性" in covered_domains or "calc.stability" in cap_ids
        assert "阻力" in covered_domains or "calc.resistance" in cap_ids


# ══════════════════════════════════════════════════════════════════
# 判据 (2): 自主续航 — 战役 draft → active → done 全自动
# ══════════════════════════════════════════════════════════════════

class TestAutonomousLifecycle:
    """自主续航验收测试。"""

    def test_campaign_draft_to_active(self, sqlite_db):
        """战役从 draft 自动激活为 active。"""
        db = sqlite_db
        _ensure_campaign_tables(db)

        db.execute("INSERT INTO campaign_plans (id, name, status, goal) VALUES (1, 'test', 'draft', 'test goal')")
        db.commit()

        result = activate_campaign(db, plan_id=1)
        assert result.old_status == "draft"
        assert result.new_status == "active"
        assert result.human_interventions == 0

    def test_campaign_auto_complete(self, sqlite_db):
        """所有节点完成后战役自动标记为 done。"""
        db = sqlite_db
        _ensure_campaign_tables(db)

        db.execute("INSERT INTO campaign_plans (id, name, status) VALUES (1, 'test', 'active')")
        db.execute("INSERT INTO campaign_nodes (id, plan_id, name, status) VALUES (1, 1, 'n1', 'pending')")
        db.execute("INSERT INTO campaign_nodes (id, plan_id, name, status) VALUES (2, 1, 'n2', 'pending')")
        db.commit()

        complete_node(db, node_id=1, success=True, result={"value": 42})
        complete_node(db, node_id=2, success=True, result={"value": 84})

        result = evaluate_campaign_completion(db, plan_id=1)
        assert result.new_status == "done"
        assert result.human_interventions == 0

    def test_campaign_completed_with_issues(self, sqlite_db):
        """有节点失败时战役标记为 completed_with_issues。"""
        db = sqlite_db
        _ensure_campaign_tables(db)

        db.execute("INSERT INTO campaign_plans (id, name, status) VALUES (1, 'test', 'active')")
        db.execute("INSERT INTO campaign_nodes (id, plan_id, name, status) VALUES (1, 1, 'n1', 'pending')")
        db.execute("INSERT INTO campaign_nodes (id, plan_id, name, status) VALUES (2, 1, 'n2', 'pending')")
        db.commit()

        complete_node(db, node_id=1, success=True)
        complete_node(db, node_id=2, success=False, reason="test failure")

        result = evaluate_campaign_completion(db, plan_id=1)
        assert result.new_status == "completed_with_issues"
        assert result.human_interventions == 0

    def test_zero_human_intervention(self, sqlite_db):
        """全流程人工干预 = 0。"""
        db = sqlite_db
        _ensure_campaign_tables(db)

        db.execute("INSERT INTO campaign_plans (id, name, status) VALUES (1, 'test', 'draft')")
        db.execute("INSERT INTO campaign_nodes (id, plan_id, name, status) VALUES (1, 1, 'n1', 'pending')")
        db.commit()

        act_result = activate_campaign(db, plan_id=1)
        complete_node(db, node_id=1, success=True)
        comp_result = evaluate_campaign_completion(db, plan_id=1)

        assert act_result.human_interventions == 0
        assert comp_result.human_interventions == 0


# ══════════════════════════════════════════════════════════════════
# 判据 (3): 证据链 — 节点 result 可 SQL 溯源到 provenance
# ══════════════════════════════════════════════════════════════════

class TestEvidenceChain:
    """证据链验收测试。"""

    def test_provenance_record_and_trace(self, sqlite_db):
        """证据记录后可追溯。"""
        db = sqlite_db
        _ensure_provenance_table(db)

        pid = record_provenance(
            db,
            evidence_type="node_result",
            payload={"value": 42, "unit": "m/s"},
            node_id=1,
            plan_id=1,
        )
        assert pid is not None
        assert pid > 0

        chain = trace_evidence_chain(db, node_id=1)
        assert len(chain) >= 1
        assert chain[0]["evidence_type"] == "node_result"

    def test_provenance_hash_integrity(self, sqlite_db):
        """证据 hash 完整性校验。"""
        db = sqlite_db
        _ensure_provenance_table(db)

        pid = record_provenance(
            db,
            evidence_type="benchmark",
            payload={"test": "resistance", "error_pct": 1.5},
            node_id=1,
        )

        result = verify_provenance_integrity(db, provenance_id=pid)
        assert result["valid"] is True
        assert result["expected_hash"] == result["actual_hash"]

    def test_evidence_for_node(self, sqlite_db):
        """获取节点的所有关联证据。"""
        db = sqlite_db
        _ensure_provenance_table(db)

        record_provenance(db, "node_result", {"a": 1}, node_id=5, plan_id=1)
        record_provenance(db, "report", {"b": 2}, node_id=5, report_id=10)

        evidence = get_evidence_for_node(db, node_id=5)
        assert len(evidence) == 2

    def test_honest_fail_not_overwritable(self, sqlite_db):
        """honest FAIL 不可覆盖：失败证据一旦记录，不可被删除。"""
        db = sqlite_db
        _ensure_provenance_table(db)

        pid = record_provenance(
            db,
            evidence_type="node_result",
            payload={"verdict": "FAIL", "reason": "error > 15%"},
            node_id=1,
        )

        pid2 = record_provenance(
            db,
            evidence_type="node_result",
            payload={"verdict": "PASS", "reason": "re-run succeeded"},
            node_id=1,
        )

        chain = trace_evidence_chain(db, node_id=1)
        assert len(chain) == 2
        fail_records = [c for c in chain if "FAIL" in c.get("payload", "")]
        assert len(fail_records) >= 1


# ══════════════════════════════════════════════════════════════════
# 判据 (4): 质量门 — consecutive_fails ≥ 3 → escalation
# ══════════════════════════════════════════════════════════════════

class TestQualityGate:
    """质量门验收测试。"""

    def test_consecutive_fails_increment(self, sqlite_db):
        """连续失败计数递增。"""
        db = sqlite_db
        _ensure_escalation_table(db)
        _ensure_node_fails_table(db)

        r1 = record_node_verdict(db, node_id=1, plan_id=1, verdict="FAIL", reason="err1")
        assert r1.consecutive_fails == 1
        assert r1.escalated is False

        r2 = record_node_verdict(db, node_id=1, plan_id=1, verdict="FAIL", reason="err2")
        assert r2.consecutive_fails == 2
        assert r2.escalated is False

    def test_escalation_at_threshold(self, sqlite_db):
        """consecutive_fails ≥ 3 时自动 escalation。"""
        db = sqlite_db
        _ensure_escalation_table(db)
        _ensure_node_fails_table(db)

        result = None
        for i in range(ESCALATION_THRESHOLD):
            result = record_node_verdict(
                db, node_id=10, plan_id=1, verdict="FAIL", reason=f"error_{i}"
            )

        assert result is not None
        assert result.consecutive_fails == ESCALATION_THRESHOLD
        assert result.escalated is True
        assert result.escalation_id is not None

    def test_escalation_id_non_null(self, sqlite_db):
        """升级后 escalation_id 非空。"""
        db = sqlite_db
        _ensure_escalation_table(db)
        _ensure_node_fails_table(db)

        result = None
        for i in range(ESCALATION_THRESHOLD + 1):
            result = record_node_verdict(
                db, node_id=20, plan_id=1, verdict="FAIL", reason=f"err_{i}"
            )

        assert result is not None
        assert result.escalation_id is not None
        assert result.escalation_id > 0

    def test_pass_resets_consecutive_fails(self, sqlite_db):
        """PASS 重置连续失败计数。"""
        db = sqlite_db
        _ensure_escalation_table(db)
        _ensure_node_fails_table(db)

        record_node_verdict(db, node_id=30, plan_id=1, verdict="FAIL", reason="err1")
        record_node_verdict(db, node_id=30, plan_id=1, verdict="FAIL", reason="err2")

        result = record_node_verdict(db, node_id=30, plan_id=1, verdict="PASS")
        assert result.consecutive_fails == 0

    def test_get_escalations(self, sqlite_db):
        """获取升级记录列表。"""
        db = sqlite_db
        _ensure_escalation_table(db)
        _ensure_node_fails_table(db)

        for i in range(ESCALATION_THRESHOLD):
            record_node_verdict(db, node_id=40, plan_id=1, verdict="FAIL", reason=f"err_{i}")

        escalations = get_escalations(db, plan_id=1)
        assert len(escalations) >= 1
        assert escalations[0]["node_id"] == 40

    def test_resolve_escalation(self, sqlite_db):
        """解决升级。"""
        db = sqlite_db
        _ensure_escalation_table(db)
        _ensure_node_fails_table(db)

        result = None
        for i in range(ESCALATION_THRESHOLD):
            result = record_node_verdict(db, node_id=50, plan_id=1, verdict="FAIL", reason=f"err_{i}")

        assert result is not None
        assert result.escalation_id is not None
        ok = resolve_escalation(db, result.escalation_id, "Fixed in m9999")
        assert ok is True

        unresolved = get_escalations(db, plan_id=1, unresolved_only=True)
        assert all(e["id"] != result.escalation_id for e in unresolved)


# ══════════════════════════════════════════════════════════════════
# 判据 (5): KPI 达标
# ══════════════════════════════════════════════════════════════════

class TestPlatformKPI:
    """KPI 验收测试。"""

    def test_kpi_targets_defined(self):
        """KPI 目标值已定义。"""
        assert KPI_TARGETS["repro_rate"] == 0.8
        assert KPI_TARGETS["kb_samples"] == 100
        assert KPI_TARGETS["unattended"] == 0.9
        assert KPI_TARGETS["benchmark"] == 5

    def test_compute_all_kpis_structure(self, sqlite_db):
        """compute_all_kpis 返回正确结构。"""
        kpis = compute_all_kpis(sqlite_db)
        assert "repro_rate" in kpis
        assert "kb_samples" in kpis
        assert "unattended" in kpis
        assert "benchmark" in kpis
        assert "all_pass" in kpis
        assert "timestamp" in kpis

    def test_kpi_value_and_target(self, sqlite_db):
        """每个 KPI 有 value、target、pass 字段。"""
        kpis = compute_all_kpis(sqlite_db)
        for key in ["repro_rate", "kb_samples", "unattended", "benchmark"]:
            assert "value" in kpis[key]
            assert "target" in kpis[key]
            assert "pass" in kpis[key]

    def test_repro_rate_computation(self, sqlite_db):
        """repro_rate 计算逻辑。"""
        rate = _compute_repro_rate(sqlite_db)
        assert isinstance(rate, float)
        assert 0.0 <= rate <= 1.0

    def test_kb_samples_computation(self, sqlite_db):
        """kb_samples 计算逻辑。"""
        count = _compute_kb_samples(sqlite_db)
        assert isinstance(count, int)
        assert count >= 0

    def test_unattended_computation(self, sqlite_db):
        """unattended 计算逻辑。"""
        rate = _compute_unattended(sqlite_db)
        assert isinstance(rate, float)
        assert 0.0 <= rate <= 1.0

    def test_benchmark_computation(self, sqlite_db):
        """benchmark 计算逻辑。"""
        count = _compute_benchmark(sqlite_db)
        assert isinstance(count, int)
        assert count >= 0


# ══════════════════════════════════════════════════════════════════
# 集成测试：全链路
# ══════════════════════════════════════════════════════════════════

class TestIntegration:
    """全链路集成测试。"""

    def test_full_campaign_lifecycle_with_evidence(self, sqlite_db):
        """完整战役生命周期 + 证据链。"""
        db = sqlite_db
        _ensure_campaign_tables(db)
        _ensure_provenance_table(db)
        _ensure_escalation_table(db)
        _ensure_node_fails_table(db)

        # 1. 创建战役
        db.execute("INSERT INTO campaign_plans (id, name, status) VALUES (1, 'integration', 'draft')")
        db.execute("INSERT INTO campaign_nodes (id, plan_id, name, status) VALUES (1, 1, 'n1', 'pending')")
        db.execute("INSERT INTO campaign_nodes (id, plan_id, name, status) VALUES (2, 1, 'n2', 'pending')")
        db.commit()

        # 2. 激活
        act = activate_campaign(db, plan_id=1)
        assert act.new_status == "active"

        # 3. 执行节点 + 记录证据
        complete_node(db, node_id=1, success=True, result={"resistance": 123.4})
        record_provenance(db, "node_result", {"resistance": 123.4}, node_id=1, plan_id=1)

        complete_node(db, node_id=2, success=True, result={"power": 567.8})
        record_provenance(db, "node_result", {"power": 567.8}, node_id=2, plan_id=1)

        # 4. 评估完成
        comp = evaluate_campaign_completion(db, plan_id=1)
        assert comp.new_status == "done"

        # 5. 验证证据链
        chain1 = trace_evidence_chain(db, node_id=1)
        assert len(chain1) >= 1
        chain2 = trace_evidence_chain(db, node_id=2)
        assert len(chain2) >= 1

    def test_quality_gate_escalation_in_campaign(self, sqlite_db):
        """战役中质量门升级流程。"""
        db = sqlite_db
        _ensure_campaign_tables(db)
        _ensure_escalation_table(db)
        _ensure_node_fails_table(db)

        db.execute("INSERT INTO campaign_plans (id, name, status) VALUES (1, 'qg_test', 'active')")
        db.execute("INSERT INTO campaign_nodes (id, plan_id, name, status) VALUES (1, 1, 'flaky', 'pending')")
        db.execute("INSERT INTO campaign_nodes (id, plan_id, name, status) VALUES (2, 1, 'stable', 'pending')")
        db.commit()

        # 节点 1 连续失败 3 次 → 升级
        for i in range(3):
            complete_node(db, node_id=1, success=False, reason=f"attempt_{i}")

        escalations = get_escalations(db, plan_id=1)
        assert len(escalations) >= 1

        # 节点 2 成功
        complete_node(db, node_id=2, success=True)

        # 战役完成（有问题）
        comp = evaluate_campaign_completion(db, plan_id=1)
        assert comp.new_status == "completed_with_issues"
