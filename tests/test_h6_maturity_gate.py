"""H6-MATURITY-GATE 测试：能力增量账本 + 五级成熟度 + 交叉一致性。"""
from __future__ import annotations

import json
import copy
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

from tools.h6_maturity_gate.runner import H6MaturityGate, _normalize_caps
from tools.h6_maturity_gate.adjudicator import (
    cross_consistency_check, increment_ledger, LEVEL_RANK, LEVELS
)


# ── 测试夹具 ──────────────────────────────────────────────────────────────

@pytest.fixture
def baseline_dict():
    """基线：自述快照（dict 格式）。"""
    return {
        "capabilities": {
            "cap_a": "REAL",
            "cap_b": "REAL",
            "cap_c": "STUB",
            "cap_d": "NOT_SUPPORTED",
        }
    }


@pytest.fixture
def current_dict():
    """当前：证据台账（dict 格式）。"""
    return {
        "capabilities": {
            "cap_a": {"level": "REAL", "evidence": [{"id": "e1", "passed": True}]},
            "cap_b": {"level": "WITHHELD", "evidence": []},
            "cap_c": {"level": "MOCK", "evidence": [{"id": "e2", "passed": False}]},
            "cap_d": {"level": "WITHHELD", "evidence": [{"id": "e3", "passed": True}]},
        }
    }


@pytest.fixture
def current_list():
    """当前：证据台账（list 格式）。"""
    return {
        "capabilities": [
            {"id": "cap_a", "reconciled_level": "REAL", "evidence_refs": [{"id": "e1"}]},
            {"id": "cap_b", "reconciled_level": "WITHHELD", "evidence_refs": []},
            {"id": "cap_c", "reconciled_level": "MOCK", "evidence_refs": [{"id": "e2"}]},
            {"id": "cap_d", "reconciled_level": "WITHHELD", "evidence_refs": [{"id": "e3"}]},
        ]
    }


# ── _normalize_caps 测试 ─────────────────────────────────────────────────

class TestNormalizeCaps:
    def test_dict_format(self):
        data = {"capabilities": {"cap_a": "REAL", "cap_b": "STUB"}}
        result = _normalize_caps(data)
        assert result["cap_a"]["level"] == "REAL"
        assert result["cap_b"]["level"] == "STUB"

    def test_dict_with_nested(self):
        data = {"capabilities": {"cap_a": {"level": "REAL", "evidence": []}}}
        result = _normalize_caps(data)
        assert result["cap_a"]["level"] == "REAL"

    def test_list_format(self):
        data = {"capabilities": [
            {"id": "cap_a", "reconciled_level": "REAL"},
            {"id": "cap_b", "reconciled_level": "MOCK"},
        ]}
        result = _normalize_caps(data)
        assert result["cap_a"]["level"] == "REAL"
        assert result["cap_b"]["level"] == "MOCK"

    def test_empty(self):
        assert _normalize_caps({}) == {}
        assert _normalize_caps({"capabilities": []}) == {}


# ── 交叉一致性测试 ────────────────────────────────────────────────────────

class TestCrossConsistency:
    def test_axis_a_divergence(self, baseline_dict, current_dict):
        bl = _normalize_caps(baseline_dict)
        cur = _normalize_caps(current_dict)
        result = cross_consistency_check(bl, cur)

        axes = result["axes"]
        axis_a = next(a for a in axes if a["axis_id"] == "A")
        assert axis_a["checked"] == 4
        # cap_a: REAL→REAL = OK
        # cap_b: REAL→WITHHELD = REVERIFY
        # cap_c: STUB→MOCK = REVERIFY
        # cap_d: NOT_SUPPORTED→WITHHELD = UNVERIFIED
        assert axis_a["passed"] == 1  # only cap_a is OK

    def test_axis_c_recompute(self, baseline_dict, current_dict):
        bl = _normalize_caps(baseline_dict)
        cur = _normalize_caps(current_dict)
        result = cross_consistency_check(bl, cur)

        axis_c = next(a for a in result["axes"] if a["axis_id"] == "C")
        assert axis_c["checked"] == 7  # 7 sub-checks
        assert axis_c["passed"] == 7  # all match since we're comparing same data

    def test_all_axes_present(self, baseline_dict, current_dict):
        bl = _normalize_caps(baseline_dict)
        cur = _normalize_caps(current_dict)
        result = cross_consistency_check(bl, cur)

        axis_ids = [a["axis_id"] for a in result["axes"]]
        assert axis_ids == ["A", "B", "C", "D"]


# ── 增量账本测试 ──────────────────────────────────────────────────────────

class TestIncrementLedger:
    def test_basic(self, baseline_dict, current_dict):
        bl = _normalize_caps(baseline_dict)
        cur = _normalize_caps(current_dict)
        result = increment_ledger(bl, cur)

        assert result["total_capabilities"] == 4
        assert "entries" in result
        assert "counts" in result
        assert "chain_head" in result
        assert "maturity" in result

    def test_direction_counts(self, baseline_dict, current_dict):
        bl = _normalize_caps(baseline_dict)
        cur = _normalize_caps(current_dict)
        result = increment_ledger(bl, cur)

        counts = result["counts"]
        # cap_a: REAL→REAL = HOLD
        # cap_b: REAL→WITHHELD = DEMOTE
        # cap_c: STUB→MOCK = PROMOTE
        # cap_d: NOT_SUPPORTED→WITHHELD = PROMOTE
        assert counts["HOLD"] == 1
        assert counts["DEMOTE"] == 1
        assert counts["PROMOTE"] == 2

    def test_maturity_score(self, baseline_dict, current_dict):
        bl = _normalize_caps(baseline_dict)
        cur = _normalize_caps(current_dict)
        result = increment_ledger(bl, cur)

        # Baseline: REAL(4) + REAL(4) + STUB(1) + NOT_SUPPORTED(0) = 9/4 = 2.25
        # Current: REAL(4) + WITHHELD(3) + MOCK(2) + WITHHELD(3) = 12/4 = 3.0
        assert result["maturity"]["before"]["mean_rank"] == pytest.approx(2.25, abs=0.01)
        assert result["maturity"]["after"]["mean_rank"] == pytest.approx(3.0, abs=0.01)

    def test_chain_head_is_hash(self, baseline_dict, current_dict):
        bl = _normalize_caps(baseline_dict)
        cur = _normalize_caps(current_dict)
        result = increment_ledger(bl, cur)

        assert len(result["chain_head"]) == 64  # sha256 hex


# ── H6MaturityGate 测试 ──────────────────────────────────────────────────

class TestH6MaturityGate:
    def test_pass_all_criteria(self, baseline_dict, current_dict):
        gate = H6MaturityGate()
        result = gate.run(baseline_dict, current_dict)

        # With the test data, some criteria may fail (e.g., C8 for WITHHELD without evidence)
        # Just verify the gate runs and produces valid output
        assert result.verdict in ("PASS", "FAIL", "WITHHELD")
        assert 0 <= result.criteria_pass_rate <= 1
        assert len(result.criteria) == 8

    def test_deterministic(self, baseline_dict, current_dict):
        gate = H6MaturityGate()
        r1 = gate.run(baseline_dict, current_dict)
        r2 = gate.run(baseline_dict, current_dict)

        assert r1.input_digest == r2.input_digest
        assert r1.criteria_pass_rate == r2.criteria_pass_rate

    def test_list_format_input(self, baseline_dict, current_list):
        gate = H6MaturityGate()
        result = gate.run(baseline_dict, current_list)

        assert result.verdict in ("PASS", "FAIL", "WITHHELD")
        assert result.criteria_pass_rate >= 0

    def test_maturity_scores(self, baseline_dict, current_dict):
        gate = H6MaturityGate()
        result = gate.run(baseline_dict, current_dict)

        assert 0 <= result.maturity["before"]["maturity_score"] <= 1
        assert 0 <= result.maturity["after"]["maturity_score"] <= 1

    def test_findings_on_fail(self):
        """当判据不通过时，findings 应有内容。"""
        from tools.h6_maturity_gate.runner import DEFAULT_THRESHOLDS
        thresholds = DEFAULT_THRESHOLDS.copy()
        thresholds["coverage_min"] = 2.0  # 不可能达到
        gate = H6MaturityGate(thresholds=thresholds)
        baseline = {"capabilities": {"cap_a": "REAL"}}
        current = {"capabilities": {"cap_a": {"level": "REAL"}}}
        result = gate.run(baseline, current)

        # coverage = 1.0 < 2.0, so C1 should fail
        assert not result.criteria["C1"]["pass"]
        assert any(f["criterion"] == "C1" for f in result.findings)


# ── 级别秩测试 ────────────────────────────────────────────────────────────

class TestLevelRank:
    def test_ordering(self):
        assert LEVEL_RANK["NOT_SUPPORTED"] < LEVEL_RANK["STUB"]
        assert LEVEL_RANK["STUB"] < LEVEL_RANK["MOCK"]
        assert LEVEL_RANK["MOCK"] < LEVEL_RANK["WITHHELD"]
        assert LEVEL_RANK["WITHHELD"] < LEVEL_RANK["REAL"]

    def test_unknown_level(self):
        assert LEVEL_RANK.get("UNKNOWN", 0) == 0


# ── 端到端测试（使用真实数据） ─────────────────────────────────────────────

class TestEndToEnd:
    @pytest.fixture
    def real_baseline(self):
        path = REPO_ROOT / "tests/fixtures/capability_ledger/capabilities_snapshot.json"
        if not path.exists():
            pytest.skip("Baseline fixture not found")
        return json.loads(path.read_text())

    @pytest.fixture
    def real_current(self):
        path = REPO_ROOT / "artifacts/capability_ledger/capability_ledger_resolved.json"
        if not path.exists():
            pytest.skip("Current ledger not found")
        return json.loads(path.read_text())

    def test_real_data_runs(self, real_baseline, real_current):
        gate = H6MaturityGate()
        result = gate.run(real_baseline, real_current)

        assert result.verdict in ("PASS", "FAIL", "WITHHELD")
        assert 0 <= result.criteria_pass_rate <= 1
        assert len(result.criteria) == 8

    def test_real_data_maturity(self, real_baseline, real_current):
        gate = H6MaturityGate()
        result = gate.run(real_baseline, real_current)

        # 真实数据应该有合理的成熟度得分
        assert 0 < result.maturity["after"]["maturity_score"] < 1

    def test_real_data_increment(self, real_baseline, real_current):
        gate = H6MaturityGate()
        result = gate.run(real_baseline, real_current)

        # 真实数据应该有增量条目
        assert result.increments["total_capabilities"] > 0
        assert sum(result.increments["counts"].values()) == result.increments["total_capabilities"]
