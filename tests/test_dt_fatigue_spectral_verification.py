"""m1794 — 数字孪生谱疲劳闭合的独立复核（验证欠账清偿）。

被测对象（平台仓，非船舶仓）：``backend/services/digital_twin/models/fatigue.py``
的窄带 Rayleigh 双折线 Miner 闭合 :func:`rayleigh_bilinear_moment`，
它被 ``spectral_fatigue``（设计工况链）与 ``damage_from_stress_rms``（传感器/状态估计链）
两条入口共用。

复核方法：**不使用被测模块的任何中间量**，用两条独立数学路径复算
  (1) S 空间直接数值积分：D_cycle = ∫_0^∞ f_S(S)/N(S) dS，
      f_S(S) = S/(4 σ_a²) exp(-S²/(8 σ_a²))（Rayleigh 应力幅→范围，range=2×amplitude）；
  (2) 渐近指数法：σ_a ≪ S_knee 时全域落在 m=5 段 → D ∝ σ^5；σ_a ≫ S_knee 时 → D ∝ σ^3。
     双对数斜率是纯解析结论，任何 Γ/正则化实现错误（例如上/下不完全 Γ 用反、
     或 knee 判据用 1+m/2 反了）都会破坏它。
基准：DNV-RP-C203 曲线 F（空气）：log a₁ = 11.699、m₁ = 3、拐点 N = 10⁷，
即 S_knee = (10^11.699/10⁷)^(1/3) = 36.84 MPa。
"""
import math
import os
import sys

import pytest

BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from services.digital_twin.models import fatigue as F  # noqa: E402
from services.digital_twin.models.geometry import HullForm  # noqa: E402

SIGMAS = [2.0, 5.0, 9.13, 12.0, 20.0, 40.0]   # 9.13 MPa ≈ knee/2√2 附近


def _density(S, sigma_a):
    """Rayleigh range pdf, unit-normalised: ∫ f dS = 1."""
    return S / (4.0 * sigma_a ** 2) * math.exp(-S ** 2 / (8.0 * sigma_a ** 2))


def _independent_cycle_damage(sigma_a):
    """独立路径 1：自适应积分的 ∫ f(S)/N(S) dS（在拐点断开积分以消除导数间断）。"""
    from scipy.integrate import quad

    f = lambda S: _density(S, sigma_a) / F.sn_cycles(S)
    # epsabs=0 必须显式给出：损伤量级 ~1e-11..1e-6，默认 epsabs=1.49e-8 会让
    # quad 在低应力段直接"提前收敛"到 0，产生假的 1e-4 级偏差（本轮踩过的坑）。
    kw = dict(limit=400, epsabs=0.0, epsrel=1e-11)
    lo = quad(f, 0.0, F.S_KNEE, **kw)[0]
    hi = quad(f, F.S_KNEE, 80.0 * sigma_a + 40.0, **kw)[0]
    return lo + hi


def test_dnv_curve_f_knee_anchor():
    """DNV-RP-C203 曲线 F：S_knee = 36.84 MPa @ N = 1e7（文献锚）。"""
    assert F.SN_M == 3.0 and F.SN_M2 == 5.0
    assert F.SN_A == pytest.approx(10.0 ** 11.699, rel=1e-12)
    assert F.S_KNEE == pytest.approx(36.84, abs=0.05)
    assert F.sn_cycles(F.S_KNEE) == pytest.approx(1.0e7, rel=1e-9)


def test_knee_continuity_no_life_jump():
    """拐点两侧寿命必须连续（SN_A2 由连续性构造）。"""
    lo = F.sn_cycles(F.S_KNEE * (1.0 - 1e-10))
    hi = F.sn_cycles(F.S_KNEE * (1.0 + 1e-10))
    assert abs(hi - lo) / lo < 1e-8


def test_two_slope_moment_matches_independent_quadrature():
    """闭式双折线矩量 vs 独立自适应积分（期望 <1e-6）。"""
    for sigma_a in SIGMAS:
        mom = F.rayleigh_bilinear_moment(sigma_a)
        closed = mom["inv_life_m3"] + mom["inv_life_m5"]
        indep = _independent_cycle_damage(sigma_a)
        assert abs(closed - indep) / indep < 1e-6, (sigma_a, closed, indep, closed / indep - 1.0)


def test_asymptotic_exponents_are_3_and_5():
    """渐近指数：σ_a ≫ S_knee → D ∝ σ³；σ_a ≪ S_knee → D ∝ σ⁵。"""
    def d(sig):
        m = F.rayleigh_bilinear_moment(sig)
        return m["inv_life_m3"] + m["inv_life_m5"]

    lo, hi = 1.0, 2.0            # 全域 m=5 段（ranges ≪ knee）
    assert math.log(d(hi) / d(lo)) / math.log(hi / lo) == pytest.approx(5.0, abs=2e-3)
    lo, hi = 300.0, 600.0        # 全域 m=3 段（ranges ≫ knee）
    assert math.log(d(hi) / d(lo)) / math.log(hi / lo) == pytest.approx(3.0, abs=2e-3)


def test_cross_path_consistency_sensor_vs_design_chain():
    """传感器链 damage_from_stress_rms 与设计链 spectral_fatigue 窄带项必须同一口径。"""
    hull = HullForm(150.0, 24.0, 10.0, 0.75)
    Tp = 8.0
    sigma_rms = 8.0
    spec = F.spectral_fatigue(hull, Hs=5.0, Tp=Tp, ice_concentration=0.0,
                              exposure_s=86400.0, sigma_rms_mpa=sigma_rms * math.sqrt(2.0))
    sensor = F.damage_from_stress_rms(sigma_rms, Tz_s=Tp / 1.4)   # spectral 的 nu0 = 1.4/Tp
    assert spec["damage_narrowband"] == pytest.approx(sensor, rel=1e-9)


def test_wirsching_light_limits():
    """宽谱修正极限：ε→0 时 λ≡1；λ 随 ε 单调下降且 m=3 时落在 [0.6,1]。"""
    assert F.wirsching_light_factor(3.0, 0.0) == pytest.approx(1.0, rel=1e-12)
    lams = [F.wirsching_light_factor(3.0, e) for e in (0.0, 0.3, 0.6, 0.9)]
    assert all(a >= b for a, b in zip(lams, lams[1:]))
    assert 0.6 <= lams[-1] <= 1.0


def test_ice_cycles_add_damage_monotonically():
    """冰区附加循环：损伤随冰密集度/厚度单调增，敞水时严格为 0。"""
    hull = HullForm(150.0, 24.0, 10.0, 0.75)
    d0 = F.spectral_fatigue(hull, 5.0, 8.0, ice_concentration=0.0)["damage_ice"]
    d1 = F.spectral_fatigue(hull, 5.0, 8.0, ice_concentration=0.5)["damage_ice"]
    d2 = F.spectral_fatigue(hull, 5.0, 8.0, ice_concentration=0.9)["damage_ice"]
    assert d0 == 0.0 and 0.0 < d1 < d2
