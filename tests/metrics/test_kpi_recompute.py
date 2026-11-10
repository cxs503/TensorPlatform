"""KPI 可复算替代测试（m9358）。"""
import pytest
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.metrics.kpi_recompute import (
    compute_denominator_sensitivity,
    recompute_tool_categories,
)


class TestDenominatorSensitivity:
    def test_success_rate_sensitivity(self):
        result = compute_denominator_sensitivity("success_rate", 8, 12, 10)
        assert result["baseline"] == pytest.approx(8/12, abs=1e-4)
        assert result["kpi_plus_10pct"] is not None
        assert result["kpi_minus_10pct"] is not None
        assert result["delta_plus"] < 0  # 分母增大，KPI 下降
        assert result["delta_minus"] > 0  # 分母减小，KPI 上升

    def test_zero_denominator(self):
        result = compute_denominator_sensitivity("test", 0, 0, 10)
        assert result["baseline"] is None

    def test_lineage_coverage_sensitivity(self):
        result = compute_denominator_sensitivity("lineage_coverage", 10, 12, 10)
        assert result["baseline"] == pytest.approx(10/12, abs=1e-4)

    def test_repro_rate_sensitivity(self):
        result = compute_denominator_sensitivity("repro_rate", 19, 20, 10)
        assert result["baseline"] == pytest.approx(19/20, abs=1e-4)


class TestRecomputeToolCategories:
    def test_returns_nonnegative(self):
        count = recompute_tool_categories()
        assert count >= 0
