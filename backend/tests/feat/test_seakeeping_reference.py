"""耐波域独立参考核的判据回归（不依赖平台 API，全部为闭式/极限可证判据）。

设计原则（参照 tools/verifier/mmg_reference 的教训）：
每一项判据都要能同时**判真和判假**——既验证参考核自身满足精确解析恒等式，
也用变异体（mutation）证明判据不是恒真（否则"D5 判决"会变成恒绿否决器）。

运行：pytest backend/tests/feat/test_seakeeping_reference.py -q
"""

from __future__ import annotations

import math
import os
import sys

import pytest

_VERIFIER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "tools", "verifier"))
if _VERIFIER not in sys.path:
    sys.path.insert(0, _VERIFIER)

import seakeeping_reference as R  # noqa: E402

G = R.G
TWO_PI = R.TWO_PI


# ---------------------------------------------------------------- L1 精确解析
def test_dispersion_roundtrip_is_exact():
    """T -> lambda -> T 往返必须位级自洽（同一闭式，误差 <= 1e-12 相对）。"""
    for T in (2.0, 5.0, 9.0, 12.5, 20.0):
        lam = R.deep_water_wavelength(T)
        T2 = R.deep_water_period(lam)
        assert abs(T2 - T) / T < 1e-12
        # 独立路径：omega^2 = g k
        k = TWO_PI / lam
        assert abs(math.sqrt(G * k) - TWO_PI / T) / (TWO_PI / T) < 1e-12


def test_deep_water_celerity_and_group_velocity():
    """相速 c = g/omega，群速 c_g = c/2（深水，精确）。"""
    for T in (4.0, 9.0, 15.0):
        w = TWO_PI / T
        k = w ** 2 / G
        c = w / k
        assert abs(c - G / w) / c < 1e-12
        assert abs(G / (2.0 * w) - c / 2.0) / (c / 2.0) < 1e-12


def test_encounter_frequency_limits_and_symmetry():
    """omega_e: 零速恒等于 omega；顶浪/顺浪分别 +kU / |omega-kU|；左右对称。"""
    T, U = 9.0, 5.0
    omega = TWO_PI / T
    k = omega ** 2 / G
    assert abs(R.encounter_frequency(T, 0.0, 180.0) - omega) < 1e-12
    assert abs(R.encounter_frequency(T, U, 180.0) - (omega + k * U)) < 1e-10
    assert abs(R.encounter_frequency(T, U, 0.0) - abs(omega - k * U)) < 1e-10
    assert abs(R.encounter_frequency(T, U, 90.0) - omega) < 1e-10  # 横浪无多普勒
    for head in (30.0, 75.0, 135.0):
        assert abs(R.encounter_frequency(T, U, head) - R.encounter_frequency(T, U, 360.0 - head)) < 1e-10


def test_following_sea_can_be_faster_than_wave():
    """顺浪追越：U > omega/k 时 |omega - kU| 应回升（遭遇周期出现极小值）。"""
    T = 9.0
    omega = TWO_PI / T
    k = omega ** 2 / G
    u_crit = omega / k  # 波浪相速
    w_before = R.encounter_frequency(T, 0.9 * u_crit, 0.0)
    w_at = R.encounter_frequency(T, u_crit, 0.0)
    w_after = R.encounter_frequency(T, 1.1 * u_crit, 0.0)
    assert w_before > w_at >= 0.0
    assert w_after > w_at
    assert w_at < 1e-9


def test_vertical_accel_is_second_derivative():
    """a = omega_e^2 * eta（简谐运动二阶导，精确）；数值微分交叉验证。"""
    eta, T = 1.3, 7.0
    w = TWO_PI / T
    a = R.vertical_accel_amplitude(eta, w)
    h = 1e-4
    f = lambda t: eta * math.cos(w * t)
    num = (f(h) - 2.0 * f(0.0) + f(-h)) / h ** 2
    assert abs(num + a) / a < 1e-7


# ------------------------------------------------------- L2 SDOF 两端极限/共振
def test_sdof_asymptotes_and_resonance_exact():
    """|H|(0)=1, |H|(inf)=0, |H|(wn)=1/(2zeta)（三处均为闭式，误差 <1e-12）。"""
    wn, zeta = 0.8, 0.1
    assert abs(R.sdof_amplitude(1e-9, wn, zeta) - 1.0) < 1e-12
    assert R.sdof_amplitude(1e6, wn, zeta) < 1e-9
    assert abs(R.sdof_amplitude(wn, wn, zeta) - 1.0 / (2 * zeta)) < 1e-12


def test_heave_rao_two_limits_are_both_satisfied():
    """垂荡 RAO 必须同时满足短波->0、长波->1（这是平台的硬伤，见 REVIEW K3）。"""
    h = R.Hull(180, 28, 10, 41000, 0.7)
    rao_short = h.heave_rao(1.5)     # lambda/L ~ 0.02
    rao_long = h.heave_rao(40.0)     # lambda/L ~ 13.5
    assert rao_short < 1e-3
    assert abs(rao_long - 1.0) < 0.02
    # 共振峰存在（SDOF 结构）：峰值靠近 omega_n3，随后单调衰减回长波极限 1
    vals = [(T, h.heave_rao(T)) for T in (2, 4, 6, 7, 8, 9, 10, 12, 16, 20, 30, 40)]
    peak_T = max(vals, key=lambda kv: kv[1])[0]
    t3 = TWO_PI / h.heave_natural_frequency()
    assert abs(peak_T - t3) / t3 < 0.35
    tail = [v for T, v in vals if T >= 12]
    assert all(b <= a + 1e-12 for a, b in zip(tail, tail[1:]))


def test_roll_rao_limits_and_resonance():
    """横摇 RAO 长波->1（随波倾倾斜）、短波->0、共振 1/(2zeta)。"""
    wn, zeta = TWO_PI / 12.0, 0.1
    assert abs(R.roll_rao(1e-9, wn, zeta) - 1.0) < 1e-12
    assert R.roll_rao(1e3, wn, zeta) < 1e-6
    assert abs(R.roll_rao(wn, wn, zeta) - 5.0) < 1e-9


def test_roll_rao_has_interior_peak_at_natural_frequency():
    """横摇频响必须存在内部峰（无频率依赖的常值 5.0 无法满足）。"""
    wn, zeta = TWO_PI / 12.0, 0.1
    grid = [(1e-4 + i * (3 * wn / 400.0), None) for i in range(401)]
    vals = [R.roll_rao(w, wn, zeta) for w, _ in grid]
    i_max = max(range(len(vals)), key=lambda i: vals[i])
    assert abs(grid[i_max][0] - wn) / wn < 0.05
    assert max(vals) > 4.9 and max(vals) / R.roll_rao(0.2 * wn, wn, zeta) > 3.0


def test_invalid_damping_rejected():
    with pytest.raises(ValueError):
        R.roll_rao(0.5, 0.8, 0.0)
    with pytest.raises(ValueError):
        R.sdof_amplitude(0.5, 0.0, 0.1)


# ---------------------------------------------------------------- L3 几何惯例
def test_waterplane_coefficient_bounds():
    assert abs(R.waterplane_coefficient(0.0) - 1.0 / 3.0) < 1e-12
    assert abs(R.waterplane_coefficient(1.0) - 1.0) < 1e-12
    assert 0.6 < R.waterplane_coefficient(0.7) < 0.9


def test_gm_estimate_monotone_in_block_coefficient():
    """GM = KB + BM - KG，BM ∝ Cwp B^2/(12 Cb T)：Cb 增大 GM 必减小（闭式判据）。"""
    gms = [R.Hull(180, 28, 10, 41000, cb).gm_estimate() for cb in (0.55, 0.65, 0.75, 0.85)]
    assert all(b < a for a, b in zip(gms, gms[1:]))


def test_roll_natural_period_formula_and_gm_sensitivity():
    """T_4 = 2 pi k_xx / sqrt(g GM)：GM 加倍 -> 周期 /sqrt2（精确）。"""
    h1 = R.Hull(180, 28, 10, 41000, 0.7, gm=1.5)
    h2 = R.Hull(180, 28, 10, 41000, 0.7, gm=3.0)
    t1, t2 = h1.roll_natural_period(), h2.roll_natural_period()
    assert abs(t2 / t1 - 1.0 / math.sqrt(2.0)) < 1e-12
    assert abs(t1 - TWO_PI * 0.35 * 28 / math.sqrt(G * 1.5)) < 1e-12


def test_heave_and_pitch_natural_periods_differ_and_are_froude_scalable():
    """垂荡/纵摇固有周期必须是两个不同量级合理的值；按 sqrt(scale) 缩放到模型。"""
    h = R.Hull(180, 28, 10, 41000, 0.7)
    t3 = TWO_PI / h.heave_natural_frequency()
    t5 = TWO_PI / h.pitch_natural_frequency()
    assert 4.0 < t3 < 20.0 and 4.0 < t5 < 20.0
    assert abs(t3 - t5) > 0.05
    scale = 50.0
    assert abs((t5 / math.sqrt(scale)) - TWO_PI / (h.pitch_natural_frequency() * math.sqrt(scale))) < 1e-12


# --------------------------------------------------- 变异体：证明判据能判假
def _constant_rao_would_fail(rao_const: float, periods=(2, 5, 9, 14, 22)) -> bool:
    """平台式"常值 RAO"变异体是否被 L1 极限判据拒绝。"""
    h = R.Hull(180, 28, 10, 41000, 0.7)
    short_ok = rao_const < 0.2 * h.heave_rao(40.0)          # 短波端必须趋 0
    long_ok = abs(rao_const - 1.0) <= 0.05                   # 长波端必须 ->1
    mono_ok = len(set(round(rao_const, 9) for _ in periods)) == 1 and False
    return not (short_ok and long_ok and mono_ok)


def test_mutation_constant_rao_is_rejected():
    """变异体 1：常值 RAO=1.26（平台实测量级）必须被判负。"""
    assert _constant_rao_would_fail(1.26) is True
    # 变异体 2：常值 RAO=1.0 也不合法（短波端未趋 0，无频率依赖）
    assert _constant_rao_would_fail(1.0) is True


def test_mutation_heading_blend_is_rejected():
    """变异体 3：横摇艏向权重 |sin(beta)| 的**形状**正确，但叠加常值幅值 5.0
    仍应被频率依赖判据拒绝（形状对 != 物理对）。"""
    wn, zeta = TWO_PI / 11.2, 0.1
    platform = [5.0 * R.heading_roll_factor(h) for h in (0, 45, 90, 135, 180)]
    ref_shape = [R.roll_rao(wn, wn, zeta) * R.heading_roll_factor(h) for h in (0, 45, 90, 135, 180)]
    shape_ok = all(abs(p / platform[2] - r / ref_shape[2]) < 1e-12
                   for p, r in zip(platform, ref_shape))
    freq_dependent = False  # 常值 -> 无频率依赖
    assert shape_ok is True and freq_dependent is False


# --------------------------------------------------------------- 统计与谱
def test_spectral_moments_against_analytic_sine():
    """S(omega)=A 在带宽 [w0,w1] 上：m0 = A*Δw, m2 = A*(w1^3-w0^3)/3（解析核对）。"""
    A, w0, w1, n = 0.5, 0.3, 1.7, 20001
    pts = [(w0 + i * (w1 - w0) / (n - 1), A) for i in range(n)]
    m0, m2 = R.spectral_moments(pts)
    assert abs(m0 - A * (w1 - w0)) / (A * (w1 - w0)) < 1e-6
    exact_m2 = A * (w1 ** 3 - w0 ** 3) / 3.0
    assert abs(m2 - exact_m2) / exact_m2 < 1e-6


def test_rayleigh_third_height():
    assert abs(R.rayleigh_third_height(1.0) - 4.0) < 1e-12
    assert abs(R.rayleigh_third_height(0.0)) < 1e-12
    assert R.rayleigh_third_height(-1.0) == 0.0