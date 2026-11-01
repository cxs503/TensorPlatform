"""漂移指标（PSI/KS）验证套件 —— 判据全部为闭式解/platform 参照实现。

对应审计报告 docs/reports/DRIFT_METRICS_AUDIT.md 的 F1~F3：
  F1 联合等宽箱 PSI 与标准（基准分位数箱）PSI 最大偏差 +2792%
  F2 基准分位数箱在离散数据上塌缩成单箱 → PSI 恒 0（完全分离判成无漂移）
  F3 三处 eps 兜底（1e-10/1e-4/1e-6）导致同名指标不同值
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from services.drift_metrics import ks_detailed, ks_statistic, psi, psi_detailed

SEED = 20261101


def _scenarios():
    rng = np.random.default_rng(SEED)
    base = rng.normal(0.0, 1.0, 1000)
    other = np.random.default_rng(SEED + 1)
    return base, other


# ───────────── T1 无漂移基准：同分布应当为 0 ─────────────

def test_identical_samples_give_zero_psi_exactly():
    rng = np.random.default_rng(1)
    data = rng.normal(0.0, 1.0, 800)
    assert psi(data, data) == pytest.approx(0.0, abs=1e-12)


def test_same_distribution_resample_is_small():
    base, other = _scenarios()
    value = psi(base, other.normal(0.0, 1.0, 1000))
    assert value < 0.02, value  # 抽样噪声量级，远低于 0.1 稳定阈值


# ───────────── T2 两箱闭式解逐位复核（解析基准） ─────────────

@pytest.mark.parametrize("p_ref,p_cur", [(0.4, 0.7), (0.5, 0.2), (0.3, 0.35)])
def test_two_bin_closed_form(p_ref, p_cur):
    n = 10_000
    ref = np.concatenate([np.zeros(int(n * p_ref)), np.ones(n - int(n * p_ref))])
    cur = np.concatenate([np.zeros(int(n * p_cur)), np.ones(n - int(n * p_cur))])
    closed = ((p_cur - p_ref) * math.log(p_cur / p_ref)
              + ((1 - p_cur) - (1 - p_ref)) * math.log((1 - p_cur) / (1 - p_ref)))
    detailed = psi_detailed(ref, cur)
    # 离散取值 → 走 distinct_values 分箱（F2 回归守卫：绝不能是 0）
    assert detailed["strategy"] == "distinct_values"
    assert detailed["status"] == "ok"
    assert detailed["psi"] == pytest.approx(closed, abs=2e-3)
    assert detailed["psi"] > 0.0


def test_degenerate_discrete_drift_is_not_reported_as_zero():
    """F2 回归：基准只有两个取值时，完全分离必须给出非零 PSI。"""
    ref = np.concatenate([np.zeros(400), np.ones(600)])
    cur = np.ones(1000)          # 完全分离
    detailed = psi_detailed(ref, cur)
    assert detailed["psi"] > 0.5, detailed
    # 旧实现（基准分位数箱，去重后仅 2 个边界）会返回 0.0
    assert detailed["bins"] >= 2


def test_constant_reference_is_flagged_not_silently_zero():
    detailed = psi_detailed(np.full(100, 3.0), np.array([1.0, 5.0, 9.0]))
    assert detailed["status"] == "constant_reference"
    assert detailed["psi"] == 0.0


# ───────────── T3 漂移单调性与阈值标定 ─────────────

def test_psi_monotone_in_mean_shift():
    base, other = _scenarios()
    values = [psi(base, other.normal(shift, 1.0, 1000)) for shift in (0.0, 0.5, 1.0, 2.0)]
    assert values == sorted(values), values
    assert values[2] > 0.2        # 1σ 位移应触发平台告警阈值
    assert values[3] > values[2] + 1.0


def test_bin_edges_are_fixed_by_reference_only():
    """分箱边界只由基准决定 → 不同窗口之间可比（旧联合箱实现不满足）。"""
    base, other = _scenarios()
    cur_a = other.normal(0.3, 1.0, 500)
    cur_b = other.normal(0.3, 1.0, 500) * 1.0 + 2.0  # 同形状、不同位置
    edges_a = psi_detailed(base, cur_a)["edges"]
    edges_b = psi_detailed(base, cur_b)["edges"]
    assert edges_a == edges_b
    assert edges_a[0] == -math.inf and edges_a[-1] == math.inf


def test_platform_legacy_equal_width_diverges_on_tail_extension():
    """F1 证据固化：支撑域外扩场景，联合等宽箱 PSI 相对标准定义偏差 > 1000%。"""
    base, other = _scenarios()
    cur = np.concatenate([other.normal(0.0, 1.0, 950), other.uniform(3.0, 6.0, 50)])

    # 旧实现（model_monitoring.py 联合等宽箱）等价复刻
    lo, hi = min(base.min(), cur.min()), max(base.max(), cur.max())
    edges = np.linspace(lo, hi, 11)
    rh, _ = np.histogram(base, bins=edges)
    ch, _ = np.histogram(cur, bins=edges)
    pr = rh / len(base) + 1e-10
    pc = ch / len(cur) + 1e-10
    legacy = float(np.sum((pc - pr) * np.log(pc / pr)))

    standard = psi(base, cur)
    assert standard > 0.0
    assert abs(legacy - standard) / standard > 5.0


# ───────────── T4 KS 与 scipy 逐位一致 ─────────────

def test_ks_matches_scipy_and_numpy_fallback():
    base, other = _scenarios()
    cur = other.normal(0.7, 1.3, 600)
    result = ks_detailed(base, cur)
    assert result["status"] == "ok"
    assert 0.0 <= result["statistic"] <= 1.0

    ref_sorted, cur_sorted = np.sort(base), np.sort(cur)
    all_vals = np.sort(np.concatenate([ref_sorted, cur_sorted]))
    cdf_ref = np.searchsorted(ref_sorted, all_vals, side="right") / ref_sorted.size
    cdf_cur = np.searchsorted(cur_sorted, all_vals, side="right") / cur_sorted.size
    assert ks_statistic(base, cur) == pytest.approx(float(np.max(np.abs(cdf_ref - cdf_cur))), abs=1e-12)

    try:
        from scipy.stats import ks_2samp
    except ImportError:  # pragma: no cover
        pytest.skip("scipy 不可用")
    assert result["statistic"] == pytest.approx(float(ks_2samp(base, cur).statistic), abs=1e-12)


def test_ks_zero_for_identical_and_one_for_disjoint():
    assert ks_statistic([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(0.0)
    assert ks_statistic([0.0] * 50, [5.0] * 50) == pytest.approx(1.0)


def test_empty_input_does_not_raise():
    assert psi([], [1.0, 2.0]) == 0.0
    assert ks_detailed([], [])["status"] == "empty_input"

# ───────────── T5 三处平台调用方确实委托到共享实现 ─────────────

def test_platform_callers_delegate_to_shared_impl():
    base, other = _scenarios()
    cur = other.normal(1.2, 1.0, 700)
    expected_psi = psi(base, cur)
    expected_ks = ks_statistic(base, cur)

    try:
        from api import model_monitoring
    except Exception as exc:  # pragma: no cover - 依赖缺失（cryptography 等）时跳过
        pytest.skip(f"平台模块不可导入: {exc}")
    assert model_monitoring._compute_psi(list(base), list(cur)) == pytest.approx(expected_psi, abs=1e-12)
    assert model_monitoring._compute_ks_drift(list(base), list(cur)) == pytest.approx(expected_ks, abs=1e-12)

    from services import drift_detection_service, data_analysis_service
    import pandas as pd

    detector = drift_detection_service.DriftDetectionService(reference_data=list(base))
    assert detector._psi_test(list(cur))["psi"] == pytest.approx(expected_psi, abs=1e-12)
    assert data_analysis_service._psi(pd.Series(base), pd.Series(cur), 10) == pytest.approx(expected_psi, abs=1e-12)


# ───────────── T6 结构级校验（不依赖运行时依赖是否装齐） ─────────────

def test_call_sites_have_no_local_psi_math_left():
    """三个调用方不得再保留本地的 PSI/KS 数学（防止实现再次分叉）。"""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    targets = {
        "api/model_monitoring.py": {"_compute_psi", "_compute_ks_drift"},
        "services/drift_detection_service.py": {"_psi_test"},
        "services/data_analysis_service.py": {"_psi"},
    }
    for rel, funcs in targets.items():
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in funcs:
                body_src = ast.get_source_segment((root / rel).read_text(encoding="utf-8"), node) or ""
                assert "drift_metrics" in body_src, f"{rel}::{node.name} 未委托共享实现"
                assert "np.histogram" not in body_src, f"{rel}::{node.name} 仍保留本地分箱数学"
                assert "searchsorted" not in body_src, f"{rel}::{node.name} 仍保留本地 CDF 数学"
