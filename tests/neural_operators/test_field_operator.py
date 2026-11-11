"""通用（无领域语义）神经算子组件测试：谱卷积、泛函读出、守恒残差、标定。"""

from __future__ import annotations

import numpy as np
import pytest
import torch

from src.neural_operators.conservation import (
    ct_to_rt,
    divergence,
    divergence_penalty,
    momentum_balance_relative,
    momentum_deficit_integral,
    relative_error,
    summarize_error,
    symmetry_gate,
)
from src.neural_operators.field_operator import (
    FieldFNO2d,
    FieldFNOConfig,
    area_weights,
    integrate,
    mean_pool,
    nonneg,
)
from src.neural_operators.uncertainty import (
    calibrate,
    coverage,
    mean_interval_width,
    summarize,
)


# ---------------------------------------------------------------------------
# 谱卷积 / 泛函读出
# ---------------------------------------------------------------------------


def test_spectral_operator_preserves_grid_and_channels():
    """FNO 输出网格与通道数由配置决定（结构契约）。"""
    cfg = FieldFNOConfig(in_channels=5, out_channels=3, hidden_channels=8,
                         n_layers=2, n_modes_x=6, n_modes_z=3,
                         grid_x=16, grid_z=8)
    model = FieldFNO2d(cfg)
    x = torch.randn(4, 5, 16, 8)
    y = model(x)
    assert y.shape == (4, 3, 16, 8)
    assert torch.isfinite(y).all()


def test_integral_weights_are_normalized():
    """归一化面积权之和为 1（积分/均值泛函的定义）。"""
    w = area_weights(32, 8)
    assert w.shape == (1, 1, 32, 8)
    assert float(w.sum()) == pytest.approx(1.0, abs=1e-12)


def test_integrate_and_mean_pool_of_constant_field():
    """常值场的积分=常值、均值=常值（泛函读出的解析性质）。"""
    w = area_weights(16, 8)
    field = torch.full((2, 3, 16, 8), 2.5)
    assert torch.allclose(integrate(field, w), torch.full((2, 3), 2.5), atol=1e-6)
    assert torch.allclose(mean_pool(field, w), torch.full((2, 3), 2.5), atol=1e-6)


def test_nonneg_is_strictly_positive():
    """softplus 非负化对任意实数输入严格为正（硬非负约束）。"""
    raw = torch.linspace(-50, 50, 101)
    out = nonneg(raw + 0.0)
    assert (out > 0).all()
    assert float(out.max()) < 60.0


# ---------------------------------------------------------------------------
# 守恒残差
# ---------------------------------------------------------------------------


def test_divergence_of_uniform_field_is_zero():
    """均匀流 ∇·u = 0（不可压条件的最简解析检验）。"""
    u_x = torch.ones(2, 12, 6)
    u_z = torch.full((2, 12, 6), -0.3)
    div = divergence(u_x, u_z, dx=1 / 12, dz=1 / 6)
    assert float(div.abs().max()) < 1e-12


def test_divergence_matches_analytic_linear_field():
    """线性场 u=(a·x, b·z) 的散度应精确等于 a+b（解析值）。"""
    nx, nz = 24, 12
    x = torch.linspace(0, 1, nx).view(1, nx, 1)
    z = torch.linspace(0, 1, nz).view(1, 1, nz)
    a, b = 0.7, -0.25
    u_x = (a * x).expand(1, nx, nz)
    u_z = (b * z).expand(1, nx, nz)
    div = divergence(u_x, u_z, dx=1.0 / (nx - 1), dz=1.0 / (nz - 1))
    assert float(div[0, 1:-1, 1:-1].mean()) == pytest.approx(a + b, abs=1e-5)
    assert float(div.abs().max()) == pytest.approx(abs(a + b), abs=1e-4)


def test_divergence_penalty_positive_for_incompressible_violation():
    """随机场散度惩罚为正，均匀场为零（惩罚项判别力）。"""
    w = area_weights(16, 8)
    torch.manual_seed(0)
    rnd_x, rnd_z = torch.randn(3, 16, 8), torch.randn(3, 16, 8)
    assert float(divergence_penalty(rnd_x, rnd_z, 1 / 16, 1 / 8, w)) > 0.0
    flat = torch.ones(1, 16, 8)
    assert float(divergence_penalty(flat, flat, 1 / 16, 1 / 8, w)) < 1e-12


def test_momentum_deficit_matches_analytic_wake():
    """动量亏缺 ∫u(1-u)dA 对常值 u 有解析值 u(1-u)。"""
    w = area_weights(10, 5)
    u = torch.full((1, 10, 5), 0.2)
    mom = momentum_deficit_integral(u, w)
    assert float(mom) == pytest.approx(0.2 * 0.8, abs=1e-6)


def test_momentum_balance_relative_is_bounded_and_signed():
    """相对动量平衡残差 ∈[-1,1]；欠亏缺为正、过亏缺为负。"""
    w = area_weights(10, 5)
    # 阻力密度取 ∫u(1-u)dA 的值 → 残差应为 0
    u = torch.full((1, 10, 5), 0.5)
    target = float(momentum_deficit_integral(u, w))
    r_eq = torch.full((1, 10, 5), target)
    res = momentum_balance_relative(u, r_eq, w)
    assert float(res.abs().max()) < 1e-6
    # 阻力密度更大 → 亏缺不足 → 残差为负
    bigger = torch.full((1, 10, 5), target * 2.0)
    assert float(momentum_balance_relative(u, bigger, w)) < 0.0
    smaller = torch.full((1, 10, 5), target * 0.5)
    assert float(momentum_balance_relative(u, smaller, w)) > 0.0
    assert float(momentum_balance_relative(u, smaller, w)) <= 1.0


# ---------------------------------------------------------------------------
# 硬约束与量纲
# ---------------------------------------------------------------------------


def test_symmetry_gate_zero_at_head_and_following_seas():
    """对称工况门在 β=0/180° 恒为零，β=90° 为一（对称性硬约束基础）。"""
    beta = torch.tensor([0.0, 90.0, 180.0, 270.0, 30.0])
    gate = symmetry_gate(beta)
    assert float(gate[0]) == pytest.approx(0.0, abs=1e-7)
    assert float(gate[2]) == pytest.approx(0.0, abs=1e-6)
    assert float(gate[1]) == pytest.approx(1.0, abs=1e-6)
    assert float(gate[4]) == pytest.approx(0.5, abs=1e-6)


def test_ct_to_rt_dimensional_consistency():
    """R_T = ½ρV²SC_T 与解析算术逐位一致（量纲一致性）。"""
    ct = torch.tensor([0.0035, 0.01])
    s = torch.tensor([1500.0, 800.0])
    rt = ct_to_rt(ct, rho=1025.0, speed=5.0, wetted_surface=s)
    expected = torch.tensor([0.5 * 1025.0 * 25.0 * 1500.0 * 0.0035,
                             0.5 * 1025.0 * 25.0 * 800.0 * 0.01])
    assert torch.allclose(rt, expected, atol=1e-6)


def test_relative_error_summary():
    """误差口径：相对误差与 MAPE/p95 摘要一致。"""
    truth = torch.tensor([1.0, 2.0, 4.0])
    pred = torch.tensor([1.02, 1.90, 4.20])
    err = relative_error(pred, truth)
    assert float(err.mean()) == pytest.approx((0.02 + 0.05 + 0.05) / 3, rel=1e-6)
    mape, p95 = summarize_error(pred, truth)
    assert mape == pytest.approx(err.mean().item() * 100, rel=1e-5)
    assert p95 >= mape


# ---------------------------------------------------------------------------
# 不确定度标定
# ---------------------------------------------------------------------------


def test_conformal_calibration_reaches_nominal_coverage():
    """合成高斯噪声下，90% conformal 区间的经验覆盖率 ≥ 姿态名义值 -5%。"""
    rng = np.random.default_rng(7)
    truth = rng.uniform(0.1, 10.0, 4000)
    sigma = np.full_like(truth, 0.1)
    pred = truth * np.exp(rng.normal(0.0, 0.1, truth.size))
    cal = calibrate(pred[:2000], sigma[:2000], truth[:2000], level=0.90)
    iv = cal.interval(pred[2000:], sigma[2000:])
    cov = coverage(truth[2000:], iv["lower"], iv["upper"])
    assert cov >= 0.85, f"coverage {cov:.3f} below 0.85"
    assert cov <= 0.99
    assert mean_interval_width(iv["lower"], iv["upper"]) > 0.0


def test_conformal_quantile_monotone_in_level():
    """标定倍数随名义水平单调不减（区间包含关系）。"""
    rng = np.random.default_rng(11)
    truth = rng.uniform(1.0, 5.0, 500)
    pred = truth * np.exp(rng.normal(0.0, 0.2, truth.size))
    sigma = np.full_like(truth, 0.2)
    q80 = calibrate(pred, sigma, truth, level=0.80).q
    q95 = calibrate(pred, sigma, truth, level=0.95).q
    assert q95 >= q80 > 0.0


def test_uncertainty_summary_fields():
    """标定摘要字段齐全且量纲正确（报告口径契约）。"""
    rng = np.random.default_rng(3)
    truth = rng.uniform(0.5, 2.0, 200)
    pred = truth * np.exp(rng.normal(0, 0.15, truth.size))
    sigma = np.full_like(truth, 0.15)
    cal = calibrate(pred, sigma, truth, level=0.9)
    summary = summarize(cal, pred, sigma, truth)
    for key in ("coverage", "mean_interval_width", "mape_pct",
                "p95_relative_error_pct", "q", "n"):
        assert key in summary
    assert 0.0 <= summary["coverage"] <= 1.0
    assert summary["n"] == 200