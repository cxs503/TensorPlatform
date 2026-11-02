"""m1687 — 冰阻力三实现交叉审计的判据固化为回归测试。

判据分三类（详见 docs/reports/m1687_ice_resistance_three_way_audit.md）：
  * S*：结构自洽——分量求和 == total、零厚度极限、厚度/航速单调
  * L*：船长标度律分歧必须被显式登记（防未来「悄悄统一」）
  * C*：常数可追溯——0.55 单点标定折扣不得静默消失；跨实现分歧必须留档
"""
from __future__ import annotations

import pytest

from tools.audit import m1687_ice_three_way as A


@pytest.fixture(scope="module")
def audit():
    return A.run()


def test_S1_component_sums_close(audit):
    """每个实现的 total 必须等于其自报分量之和（相对误差 < 1e-9）。"""
    for name, err in audit["criteria"]["G3_component_sum_rel_err"].items():
        assert err < 1e-9, f"{name} 分量求和与 total 不自洽: {err:g}"


def test_S2_zero_thickness_limit(audit):
    """h -> 0 时冰阻力必须 -> 0（量纲/常数项错误的探测器）。

    TP-POLAR-1 在 h=1e-6 处仍有 ~23 N：这不是量纲错，而是其速度放大因子
    ``1 + C_VEL*v/sqrt(g h)`` 在 h->0 时发散（该式适用域 h >> v^2/g ≈ 0.68 m @5 kn）。
    判据因此写成「相对设计点 <1e-4」并单独锁定该残差的存在与量级。
    """
    total_design = max(audit["totals_N"].values())
    for name, r in audit["criteria"]["G1_zero_thickness_N"].items():
        assert abs(r) / total_design < 1e-4, f"{name} 零厚度极限非零: {r:g} N"
    resid = audit["criteria"]["G1_zero_thickness_N"]["TP-POLAR-1"]
    assert 1.0 < resid < 100.0, f"TP-POLAR-1 发散残差量级改变: {resid:g} N"


def test_S3_monotone_in_thickness(audit):
    for name, ok in audit["criteria"]["G2_monotone_in_h"].items():
        assert ok, f"{name} 在 h∈[0.05,2.0] m 上非单调"


def test_S4_speed_increases_resistance(audit):
    """速度项必须使阻力单调增（三个实现都声明了速度依赖）。"""
    for name, pts in audit["speed_sweep"].items():
        r = [p["R_N"] for p in pts]
        assert all(b >= a for a, b in zip(r, r[1:])), name


def test_L1_length_scaling_divergence_is_registered(audit):
    """结构性分歧登记：digital_twin 的 crushing+breaking ∝ B/L，随船长**下降**；
    TP-POLAR-1 只有 ~-0.03（来自 T/L 的 buttock-rise 代理），Riska 严格 0。
    三实现对同一物理量给出不同的船长标度律 ⇒ 不可互换。"""
    ex = {k: v["exponent_dlnR_dlnL"] for k, v in audit["l_scaling"].items()}
    dt = ex["Lindqvist(dt,c=0.55)"]
    polar = ex["TP-POLAR-1"]
    riska = ex["Riska(sem)"]
    assert dt < -0.10, f"digital_twin 的 L 指数应显著为负，实测 {dt:.3f}"
    assert abs(polar) < 0.10, f"TP-POLAR-1 的 L 指数应≈0，实测 {polar:.3f}"
    assert abs(riska) < 1e-9, f"Riska 的 L 指数应严格为 0，实测 {riska:.3f}"
    assert (polar - dt) > 0.10


def test_L2_doubling_length_halves_dt_breaking():
    """固定 B/T/Cb/h/σ 下，dt 实现的 crushing+breaking 精确服从 1/L 律。"""
    v = A.V_KN * A.KN
    r180 = A.model_b_lindqvist_dt(v, A.H_M, A.SIGMA_C, 1.0, 180.0)
    r360 = A.model_b_lindqvist_dt(v, A.H_M, A.SIGMA_C, 1.0, 360.0)
    ratio = (r360["crushing"] + r360["breaking"]) / (r180["crushing"] + r180["breaking"])
    assert abs(ratio - 0.5) < 1e-12


def test_C1_submersion_calibration_traceable(audit):
    """0.55 单点标定必须可被审计：Rs_raw/Rs == 1/0.55。"""
    cal = audit["calibration"]
    assert cal["SUBMERSION_COEFF"] == pytest.approx(0.55, abs=1e-12)
    assert cal["Rs_raw_over_Rs"] == pytest.approx(1.0 / 0.55, rel=1e-12)


def test_C2_calibration_uplift_bounded(audit):
    """去掉标定后总量上升幅度应落在 (0, 100%) 的有限区间（不是发散修补）。"""
    up = audit["calibration"]["total_uplift_if_coeff_1_pct"]
    assert 0.0 < up < 100.0


def test_C3_cross_model_spread_is_large(audit):
    """同设计点三实现相对分歧必须 >15%——绝对量级不可宣称 REAL 的机器证据。"""
    sp = audit["cross_model_spread"]
    assert sp["ratio"] > 1.15, sp
    assert 0.0 < sp["min_N"] < sp["max_N"]


def test_C4_beam_dependence_differs_between_models():
    """B 依赖性的显式登记（三套实现互不相同）：

    * TP-POLAR-1：crushing = C_cr σ_c h²(sinα+μcosα) —— **完全不含 B**（纯 h² 律）；
      breaking ∝ B（渠道宽度）；submersion ∝ B。
    * Riska(sem)：breaking ∝ ρ_i g h² B —— 含 B。
    * Lindqvist(dt)：crushing/breaking ∝ B/L —— 含 B 但被 L 归一。
    """
    v = A.V_KN * A.KN
    a = A.model_a_polar(A.polar_ship(180.0, beam_m=27.0), v, A.H_M, A.SIGMA_C)
    b = A.model_a_polar(A.polar_ship(180.0, beam_m=54.0), v, A.H_M, A.SIGMA_C)
    assert b["crushing"] / a["crushing"] == pytest.approx(1.0, rel=1e-9)
    assert b["breaking"] / a["breaking"] == pytest.approx(2.0, rel=1e-9)
    assert b["submersion"] / a["submersion"] > 1.5


def test_C5_documented_calibration_target_vs_other_models(audit):
    """digital_twin 自称标定到 0.6 m/90%/5 kn ≈ 1 MN；用同一冰况检查，
    并登记「另两套实现不在该量级带内」这一事实（不粉饰）。"""
    fits = [n for n, r in audit["totals_N"].items() if 0.5e6 <= r <= 1.5e6]
    assert "Lindqvist(dt,c=0.55)" in fits, audit["totals_N"]
    outside = [n for n, r in audit["totals_N"].items() if not 0.5e6 <= r <= 1.5e6]
    assert len(outside) >= 2, "跨实现分歧未登记"


def test_C6_polar_channel_factor_only_scales_breaking(audit):
    """channel_factor<1 只该动 breaking 分量（护航破冰道语义）。"""
    v = A.V_KN * A.KN
    ship = A.polar_ship()
    full = A.model_a_polar(ship, v, A.H_M, A.SIGMA_C, conc=1.0)
    half = A.model_a_polar(ship, v, A.H_M, A.SIGMA_C, conc=0.5)
    assert half["crushing"] == pytest.approx(full["crushing"], rel=1e-12)
    assert half["submersion"] == pytest.approx(full["submersion"], rel=1e-12)
    assert half["breaking"] == pytest.approx(0.5 * full["breaking"], rel=1e-9)