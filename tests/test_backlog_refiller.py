"""backlog_refiller 测试套件（≥8 用例）。

覆盖：
  1. 队列深度判定（正例：pending=0, idle≥6 → 触发）
  2. 队列深度判定（负例：pending>0 → 不触发）
  3. 队列深度判定（负例：idle<6 → 不触发）
  4. 队列深度判定（负例：pending>0 且 idle<6 → 不触发）
  5. 2026-11-07 03:18 停摆案例回放（MUST 触发）
  6. 正常饱和态（MUST 不触发）
  7. Goal 生成验证（≥80 字 + 判据 + honest FAIL + 零域语义）
  8. ext_id 去重验证
  9. 仓白名单验证
  10. 审计日志记录与回滚
  11. 缺口账本聚合
  12. Daemon 存活证明
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 确保 src 在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from backlog_refiller.audit import AuditLogger
from backlog_refiller.gap_ledger import GapLedgerSource
from backlog_refiller.mission_generator import MissionGenerator, GOAL_TEMPLATE
from backlog_refiller.daemon import BacklogRefillerDaemon


# ───────────────────────── 辅助函数 ─────────────────────────

def make_mock_session():
    """创建模拟数据库会话。"""
    session = MagicMock()
    session.execute = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.close = MagicMock()
    return session


def make_mock_result(scalar_value=None, mappings_first=None, mappings_all=None, first=None):
    """创建模拟查询结果。"""
    result = MagicMock()
    if scalar_value is not None:
        result.scalar = MagicMock(return_value=scalar_value)
    if mappings_first is not None:
        result.mappings = MagicMock(return_value=MagicMock(first=MagicMock(return_value=mappings_first)))
    if mappings_all is not None:
        result.mappings = MagicMock(return_value=MagicMock(all=MagicMock(return_value=mappings_all)))
    if first is not None:
        result.first = MagicMock(return_value=first)
    return result


# ───────────────────────── 测试用例 ─────────────────────────

class TestQueueDepthTrigger:
    """测试队列深度判定逻辑。"""

    def test_v1_pending_zero_idle_six_should_trigger(self):
        """V1: pending=0, idle=6 → MUST 触发（正例）。"""
        daemon = BacklogRefillerDaemon(
            db_session_factory=make_mock_session,
            pending_threshold=0,
            idle_worker_threshold=6,
        )
        assert daemon.check_queue_depth_static(0, 6) is True
        assert daemon.check_queue_depth_static(0, 7) is True
        assert daemon.check_queue_depth_static(0, 10) is True

    def test_v2_pending_one_should_not_trigger(self):
        """V2: pending=1 → MUST 不触发（负例）。"""
        daemon = BacklogRefillerDaemon(
            db_session_factory=make_mock_session,
            pending_threshold=0,
            idle_worker_threshold=6,
        )
        assert daemon.check_queue_depth_static(1, 10) is False
        assert daemon.check_queue_depth_static(5, 10) is False

    def test_v3_idle_five_should_not_trigger(self):
        """V3: idle=5 → MUST 不触发（负例）。"""
        daemon = BacklogRefillerDaemon(
            db_session_factory=make_mock_session,
            pending_threshold=0,
            idle_worker_threshold=6,
        )
        assert daemon.check_queue_depth_static(0, 5) is False
        assert daemon.check_queue_depth_static(0, 3) is False
        assert daemon.check_queue_depth_static(0, 0) is False

    def test_v4_both_conditions_fail(self):
        """V4: pending>0 且 idle<6 → MUST 不触发（负例）。"""
        daemon = BacklogRefillerDaemon(
            db_session_factory=make_mock_session,
            pending_threshold=0,
            idle_worker_threshold=6,
        )
        assert daemon.check_queue_depth_static(3, 2) is False
        assert daemon.check_queue_depth_static(1, 5) is False


class TestStallCaseReplay:
    """2026-11-07 03:18 停摆案例回放。"""

    def test_v5_stall_case_must_trigger(self):
        """V5: 2026-11-07 03:18 停摆案例 → MUST 触发补给。

        停摆场景：后端停摆导致所有 worker 空闲（idle≥6），
        且无 pending 任务（pending=0）。
        """
        daemon = BacklogRefillerDaemon(
            db_session_factory=make_mock_session,
            pending_threshold=0,
            idle_worker_threshold=6,
        )
        # 停摆场景：无 pending 任务，所有 worker 空闲
        assert daemon.check_queue_depth_static(0, 6) is True
        assert daemon.check_queue_depth_static(0, 8) is True
        assert daemon.check_queue_depth_static(0, 12) is True


class TestSaturatedState:
    """正常饱和态测试。"""

    def test_v6_saturated_must_not_trigger(self):
        """V6: 正常饱和态 → MUST 不触发（负例）。

        饱和场景：有 pending 任务或 worker 不足。
        """
        daemon = BacklogRefillerDaemon(
            db_session_factory=make_mock_session,
            pending_threshold=0,
            idle_worker_threshold=6,
        )
        # 有 pending 任务
        assert daemon.check_queue_depth_static(1, 10) is False
        assert daemon.check_queue_depth_static(5, 10) is False
        # worker 不足
        assert daemon.check_queue_depth_static(0, 5) is False
        assert daemon.check_queue_depth_static(0, 3) is False


class TestGoalGeneration:
    """Goal 生成验证。"""

    def test_v7_goal_min_length_80(self):
        """V7: Goal 必须 ≥80 字。"""
        generator = MissionGenerator(make_mock_session())
        gap = {
            "source": "capability_ledger",
            "ext_id": "test:capability_1",
            "repo": "TensorPlatform",
            "title": "测试缺口",
            "description": "这是一个测试缺口描述",
            "priority": 1,
            "metadata": {},
        }
        goal = generator.generate_goal(gap)
        assert len(goal) >= 80, f"Goal 长度 {len(goal)} < 80"

    def test_v8_goal_contains_criteria(self):
        """V8: Goal 必须包含判据。"""
        generator = MissionGenerator(make_mock_session())
        gap = {
            "source": "test",
            "ext_id": "test:1",
            "repo": "TensorPlatform",
            "title": "测试",
            "description": "描述",
            "priority": 1,
            "metadata": {},
        }
        goal = generator.generate_goal(gap)
        assert "验收判据" in goal
        assert "pytest" in goal

    def test_v9_goal_contains_honest_fail(self):
        """V9: Goal 必须包含 honest FAIL 纪律。"""
        generator = MissionGenerator(make_mock_session())
        gap = {
            "source": "test",
            "ext_id": "test:1",
            "repo": "TensorPlatform",
            "title": "测试",
            "description": "描述",
            "priority": 1,
            "metadata": {},
        }
        goal = generator.generate_goal(gap)
        assert "honest FAIL" in goal
        assert "不可覆盖" in goal

    def test_v10_goal_zero_domain_semantics(self):
        """V10: Goal 必须包含零域语义句。"""
        generator = MissionGenerator(make_mock_session())
        gap = {
            "source": "test",
            "ext_id": "test:1",
            "repo": "TensorPlatform",
            "title": "测试",
            "description": "描述",
            "priority": 1,
            "metadata": {},
        }
        goal = generator.generate_goal(gap)
        assert "零领域语义" in goal
        assert "平台基础设施任务" in goal


class TestExtIdDedup:
    """ext_id 去重验证。"""

    def test_v11_ext_id_hash_consistency(self):
        """V11: 相同 ext_id 产生相同哈希。"""
        generator = MissionGenerator(make_mock_session())
        hash1 = generator.compute_ext_id_hash("test:capability_1")
        hash2 = generator.compute_ext_id_hash("test:capability_1")
        assert hash1 == hash2
        assert len(hash1) == 16  # 截断到 16 字符

    def test_v12_ext_id_hash_uniqueness(self):
        """V12: 不同 ext_id 产生不同哈希。"""
        generator = MissionGenerator(make_mock_session())
        hash1 = generator.compute_ext_id_hash("test:capability_1")
        hash2 = generator.compute_ext_id_hash("test:capability_2")
        assert hash1 != hash2


class TestRepoWhitelist:
    """仓白名单验证。"""

    def test_v13_whitelist_allow(self):
        """V13: 白名单内仓库应允许。"""
        generator = MissionGenerator(make_mock_session())
        assert generator.validate_repo("TensorPlatform") is True
        assert generator.validate_repo("TensorLBM") is True
        assert generator.validate_repo("TensorBEM") is True
        assert generator.validate_repo("ship-performance-platform") is True

    def test_v14_whitelist_reject(self):
        """V14: 白名单外仓库应拒绝。"""
        generator = MissionGenerator(make_mock_session())
        assert generator.validate_repo("UnknownRepo") is False
        assert generator.validate_repo("") is False


class TestAuditLogger:
    """审计日志测试。"""

    def test_v15_audit_log_and_retrieve(self):
        """V15: 审计日志记录与检索。"""
        session = make_mock_session()
        session.execute = MagicMock(return_value=make_mock_result(scalar_value=1))
        audit = AuditLogger(session)
        audit_id = audit.log(
            action="refill",
            mission_id=123,
            ext_id="test:1",
            details={"key": "value"},
            rollback_sql="DELETE FROM tensor_dev_missions WHERE id=123;",
        )
        assert audit_id == 1
        session.commit.assert_called()

    def test_v16_rollback_sql_generation(self):
        """V16: 回滚 SQL 生成。"""
        generator = MissionGenerator(make_mock_session())
        rollback_sql = generator.build_rollback_sql(42)
        assert "DELETE FROM tensor_dev_missions WHERE id = 42" in rollback_sql
        assert "DELETE FROM tensor_agent_sessions" in rollback_sql


class TestGapLedger:
    """缺口账本聚合测试。"""

    def test_v17_gap_ledger_filter_by_whitelist(self):
        """V17: 缺口账本按白名单过滤。"""
        session = make_mock_session()
        source = GapLedgerSource(session)
        gaps = [
            {"repo": "TensorPlatform", "ext_id": "1"},
            {"repo": "TensorLBM", "ext_id": "2"},
            {"repo": "UnknownRepo", "ext_id": "3"},
        ]
        filtered = source.filter_by_whitelist(gaps)
        assert len(filtered) == 2
        assert all(g["repo"] in source.REPO_WHITELIST for g in filtered)


class TestDaemonLiveness:
    """Daemon 存活证明测试。"""

    def test_v18_daemon_uptime(self):
        """V18: Daemon 存活时间计算。"""
        daemon = BacklogRefillerDaemon(
            db_session_factory=make_mock_session,
        )
        uptime = daemon.uptime_seconds
        assert uptime >= 0
        assert isinstance(uptime, float)

    def test_v19_daemon_run_once_structure(self):
        """V19: run_once 返回结构正确。"""
        session = make_mock_session()
        session.execute = MagicMock(return_value=make_mock_result(scalar_value=0))
        daemon = BacklogRefillerDaemon(
            db_session_factory=lambda: session,
            pending_threshold=0,
            idle_worker_threshold=6,
        )
        result = daemon.run_once()
        assert "triggered" in result
        assert "queue_depth" in result
        assert "generated_missions" in result
        assert "cycle_count" in result
        assert result["cycle_count"] == 1


class TestMissionGeneratorParams:
    """Mission 生成参数测试。"""

    def test_v20_mission_params_structure(self):
        """V20: 生成的 mission 参数结构正确。"""
        session = make_mock_session()
        # Mock is_duplicate to return False (not a duplicate)
        with patch.object(MissionGenerator, 'is_duplicate', return_value=False):
            generator = MissionGenerator(session)
            gap = {
                "source": "capability_ledger",
                "ext_id": "test:capability_1",
                "repo": "TensorPlatform",
                "title": "测试缺口",
                "description": "测试描述",
                "priority": 1,
                "metadata": {},
            }
            params = generator.generate_mission_params(gap)
            assert params is not None
            assert "goal" in params
            assert "repo" in params
            assert "origin" in params
            assert params["origin"] == "backlog_refiller"
            assert params["repo"] == "TensorPlatform"
            assert len(params["goal"]) >= 80

    def test_v21_mission_params_reject_non_whitelist(self):
        """V21: 非白名单仓库返回 None。"""
        session = make_mock_session()
        generator = MissionGenerator(session)
        gap = {
            "source": "test",
            "ext_id": "test:1",
            "repo": "UnknownRepo",
            "title": "测试",
            "description": "描述",
            "priority": 1,
            "metadata": {},
        }
        params = generator.generate_mission_params(gap)
        assert params is None

    def test_v22_mission_params_reject_duplicate(self):
        """V22: 重复 ext_id 返回 None。"""
        session = make_mock_session()
        with patch.object(MissionGenerator, 'is_duplicate', return_value=True):
            generator = MissionGenerator(session)
            gap = {
                "source": "test",
                "ext_id": "test:dup",
                "repo": "TensorPlatform",
                "title": "测试",
                "description": "描述",
                "priority": 1,
                "metadata": {},
            }
            params = generator.generate_mission_params(gap)
            assert params is None


# ───────────────────────── 运行入口 ─────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
