"""m2034 —— 参数横摇内核（src/ship_perf/parametric_roll.py）的解析锚点回归。

三层锚点，全部**不引用任何经验常数**：
  A 几何层：箱型浮箱极限 vs 已验证模块 ``hydrostatics`` 的闭式（KB=T/2、BM=B²/12T）；
            浮态守恒残差；波幅→0 回归静水；ΔGM 对 wave 长度/外飘的单调与峰化行为。
  B 动力学层：阻尼 Mathieu 主共振一阶解析阈 ``ε_cr = 4ζ``（a=1/4）；
            与独立 scipy solve_ivp 单值矩阵互校；Floquet 增长速率 vs 时域包络增长。
  C 判据层：端到端 assess 的判决与 ε/ε_cr 关系自洽、对 ζ 单调。
"""
from __future__ import annotations

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ship_perf import hydrostatics as H  # noqa: E402
from ship_perf import parametric_roll as PR  # noqa: E402

BOX = PR.FlaredBarge(L=100.0, B0=20.0, T=8.0, kg=6.0)
LARGE = PR.FlaredBarge(L=200.0, B0=32.0, T=9.0, kg=5.0, flare=1.0, taper=0.55,
                       section_height=2.0)


def rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) if b else abs(a)


# ======================================================================
# A 几何层
# ======================================================================


def test_still_water_box_matches_validated_hydrostatics():
    """箱型浮箱极限必须对上已验证的 hydrostatics 闭式（KB=T/2, BM=B²/12T）。"""
    st = PR.still_water_hydrostatics(BOX)
    assert rel(st["KB"], BOX.T / 2.0) < 1e-12
    assert rel(st["BM"], H.bm_transverse(BOX.B0, BOX.T, 1.0)) < 1e-12
    assert rel(st["volume"], BOX.L * BOX.B0 * BOX.T) < 1e-12
    assert rel(st["GM"], BOX.T / 2.0 + H.bm_transverse(BOX.B0, BOX.T, 1.0) - BOX.kg) < 1e-12


def test_still_water_tapered_hull_lcb_and_volume():
    """纵向锥化：体积 = ∫Bx dx·T，LCB 与解析一阶矩一致。"""
    hull = PR.FlaredBarge(L=120.0, B0=24.0, T=7.0, kg=5.0, taper=0.4)
    st = PR.still_water_hydrostatics(hull)
    vol = hull.T * hull.B0 * hull.L
    assert rel(st["volume"], vol) < 1e-10
    # ∫x·Bx·T dx / vol = βB0L²/6 / (B0L) ⇒ LCB = βL/6
    assert rel(st["lcb_x"], hull.taper * hull.L / 6.0) < 1e-5  # 梯形积分 O(h²)


def test_zero_amplitude_returns_still_water():
    n = 801
    st = PR.still_water_hydrostatics(LARGE, n=n)
    w = PR.wave_hydrostatics(LARGE, 0.0, LARGE.L, phase_rad=0.7, n=n)
    for key in ("KB", "BM", "GM"):
        assert rel(w[key], st[key]) < 1e-10, key
    assert abs(w["heave"]) < 1e-10 and abs(w["trim_slope"]) < 1e-12


@pytest.mark.parametrize("phase", [0.0, 0.9, 2.3, 4.1, 5.9])
def test_wave_floating_condition_conserves_volume_and_moment(phase):
    """heave+trim 解必须同时守恒体积与纵向矩（这是层 B 几何输入的可信前提）。"""
    w = PR.wave_hydrostatics(LARGE, 2.5, LARGE.L, phase_rad=phase)
    assert abs(w["volume_error_rel"]) < 1e-9
    assert w["newton_residual"] < 1e-9
    assert w["n_clipped"] == 0


def test_gm_phase_periodicity():
    """GM(φ) 关于波相位 2π 周期。"""
    a = PR.wave_hydrostatics(LARGE, 2.0, LARGE.L, phase_rad=0.31)["GM"]
    b = PR.wave_hydrostatics(LARGE, 2.0, LARGE.L, phase_rad=0.31 + 2.0 * math.pi)["GM"]
    assert rel(a, b) < 1e-12


def test_delta_gm_monotone_in_wave_amplitude():
    """ΔGM/GM 随波幅单调不减（几何层不得反物理）。"""
    ratios = [
        PR.gm_phase_sweep(LARGE, amp, LARGE.L, n_phase=12, n=401)["dGM_ratio"]
        for amp in (0.5, 1.0, 2.0, 3.0)
    ]
    assert ratios == sorted(ratios)
    assert ratios[0] < ratios[-1]


def test_delta_gm_monotone_in_flare():
    """外飘 κ 越大，水线面惯性矩随吃水变化越强 → ΔGM/GM 必须单调增大。"""
    ratios = []
    for k in (0.0, 0.45, 0.8, 1.0):
        hull = PR.FlaredBarge(L=200.0, B0=32.0, T=9.0, kg=5.0, flare=k, taper=0.55,
                             section_height=2.0)
        ratios.append(PR.gm_phase_sweep(hull, 2.0, 200.0, n_phase=12, n=401)["dGM_ratio"])
    assert ratios == sorted(ratios)
    # 垂直舷侧（κ=0）只能经由 KB 变化产生小量 ΔGM，必须显著小于强外飘
    assert ratios[-1] > 4.0 * ratios[0]


def test_delta_gm_peaks_near_ship_length():
    """经典结果：ΔGM 在 λ≈L 附近峰化，短波与长波两端都要衰减（机器可判）。"""
    def ratio(lam: float) -> float:
        return PR.gm_phase_sweep(LARGE, 2.0, lam, n_phase=16, n=401)["dGM_ratio"]

    r_short, r_peak, r_long = ratio(0.5 * LARGE.L), ratio(LARGE.L), ratio(2.0 * LARGE.L)
    assert r_peak > r_short
    assert r_peak > r_long


def test_large_amplitude_reports_section_clipping():
    """波幅超出剖面高度时必须给出 clipped 诊断，而不是静默截断。"""
    w = PR.wave_hydrostatics(LARGE, 0.5 * LARGE.h_max() + 1.0, LARGE.L)
    assert w["n_clipped"] > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"L": 0.0}, {"B0": -1.0}, {"T": -8.0}, {"flare": -0.1},
        {"taper": 1.0}, {"kg": -1.0}, {"kg": 100.0},
    ],
)
def test_hull_parameter_validation(kwargs):
    base = dict(L=100.0, B0=20.0, T=8.0, kg=6.0)
    base.update(kwargs)
    with pytest.raises(ValueError):
        PR.FlaredBarge(**base).validate()


def test_station_count_guard():
    with pytest.raises(ValueError):
        PR.still_water_hydrostatics(BOX, n=10)


# ======================================================================
# B 动力学层（Mathieu / Floquet）
# ======================================================================


@pytest.mark.parametrize("zeta", [0.005, 0.01, 0.02, 0.05])
def test_principal_resonance_threshold_matches_analytic_4zeta(zeta):
    """**核心解析锚**：主共振 a=1/4 处不稳定阈 ε_cr = 4ζ（一阶结果，实测偏差<0.5%）。"""
    eps_cr = PR.critical_eps(0.25, zeta, steps=600)
    assert eps_cr is not None
    assert rel(eps_cr, 4.0 * zeta) < 5e-3


def test_free_decay_matches_closed_form():
    """ε=0 时单值矩阵特征值必须精确等于 exp(-2π·ζ·ω_n)（λ_real=-ζω_n，周期 2π）。"""
    a = 0.25
    wn = math.sqrt(a)
    for zeta in (0.01, 0.05, 0.2):
        rho = PR.monodromy(0.0, a, zeta, steps=2400)["rho_max"]
        ref = math.exp(-2.0 * math.pi * zeta * wn)
        assert rel(rho, ref) < 5e-4  # RK4 离散误差（steps=2400）


def test_undamped_principal_region_unstable_for_any_eps():
    assert PR.monodromy(0.05, 0.25, 0.0, steps=600)["unstable"] is True
    assert abs(PR.monodromy(0.0, 0.25, 0.0, steps=600)["rho_max"] - 1.0) < 1e-9


def test_second_region_needs_much_larger_excitation():
    """a=1（ω_e=ω_n，第二不稳定区）阈值必须远大于主共振区（映射错误的探针）。"""
    e_principal = PR.critical_eps(0.25, 0.02, steps=600)
    e_second = PR.critical_eps(1.0, 0.02, steps=800, hi=1.2)
    assert e_principal is not None and e_second is not None
    assert e_second / e_principal > 5.0


def test_monodromy_matches_independent_scipy_integration():
    """与独立实现（scipy solve_ivp 高精度）逐点互校。"""
    sp = pytest.importorskip("scipy.integrate")
    np = pytest.importorskip("numpy")

    def rho_ref(eps: float, a: float, zeta: float) -> float:
        wn = math.sqrt(a)
        damp = 2.0 * zeta * wn

        def f(t, y):
            return [y[1], -damp * y[1] - a * (1.0 + eps * math.cos(t)) * y[0]]

        opts = dict(rtol=1e-12, atol=1e-14)
        c1 = sp.solve_ivp(f, [0.0, 2.0 * math.pi], [1.0, 0.0], **opts).y[:, -1]
        c2 = sp.solve_ivp(f, [0.0, 2.0 * math.pi], [0.0, 1.0], **opts).y[:, -1]
        mat = np.array([[c1[0], c2[0]], [c1[1], c2[1]]])
        return float(np.abs(np.linalg.eigvals(mat)).max())

    for eps, a, zeta in [(0.05, 0.25, 0.02), (0.3, 0.25, 0.02), (0.1, 1.0, 0.05)]:
        got = PR.monodromy(eps, a, zeta, steps=800)["rho_max"]
        assert rel(got, rho_ref(eps, a, zeta)) < 1e-8


def test_floquet_growth_rate_matches_time_domain_envelope():
    """Floquet 解析增长速率必须与时域 RK4 包络增长一致（<2%）。"""
    flo = PR.monodromy(0.4, 0.25, 0.02, steps=800)
    sim = PR.simulate_roll(0.4, 0.25, 0.02, n_periods=60, steps_per_period=480)
    assert flo["unstable"] is True
    assert rel(sim["env_growth_rate"], flo["growth_rate"]) < 0.02
    assert sim["theta_max_deg"] > 1.0  # 由 1° 起振确实增长


def test_subcritical_excitation_decays():
    sim = PR.simulate_roll(0.02, 0.25, 0.05, theta0_deg=5.0, n_periods=40)
    assert sim["env_growth_rate"] < 0.0
    flo = PR.monodromy(0.02, 0.25, 0.05)
    assert flo["unstable"] is False


def test_encounter_frequency_head_and_following_sea():
    """遭遇频率：顶浪增大、顺浪减小；速度为零时退化为波频。"""
    k = 2.0 * math.pi / 200.0
    w = math.sqrt(PR.G * k)
    assert PR.encounter_frequency(w, k, 8.0, 0.0) > w
    assert PR.encounter_frequency(w, k, 8.0, 180.0) < w
    assert rel(PR.encounter_frequency(w, k, 0.0, 90.0), w) < 1e-12


def test_mathieu_state_relations():
    k = 2.0 * math.pi / 200.0
    w = math.sqrt(PR.G * k)
    st = PR.mathieu_state(0.1, 5.0, 15.0, w, 8.0, 0.0, 200.0)
    assert rel(st["a_mathieu"], (st["omega_n"] / st["omega_e"]) ** 2) < 1e-12
    assert rel(st["detuning"], 0.5 / math.sqrt(st["a_mathieu"])) < 1e-12
    assert rel(st["omega_n"], 2.0 * math.pi / 15.0) < 1e-12


@pytest.mark.parametrize(
    "kwargs",
    [{"t_roll": 0.0}, {"gm_mean": -1.0}, {"t_roll": -5.0}],
)
def test_mathieu_state_validation(kwargs):
    args = dict(dgm_ratio=0.1, gm_mean=5.0, t_roll=15.0, wave_omega=0.55, speed=8.0,
                heading_deg=0.0, wavelength=200.0)
    args.update(kwargs)
    with pytest.raises(ValueError):
        PR.mathieu_state(**args)


def test_monodromy_validation():
    with pytest.raises(ValueError):
        PR.monodromy(0.1, 0.25, 0.02, steps=10)
    with pytest.raises(ValueError):
        PR.monodromy(-0.1, 0.25, 0.02)
    with pytest.raises(ValueError):
        PR.monodromy(0.1, 0.25, 1.2)


# ======================================================================
# C 判据层（端到端）
# ======================================================================


def _assess(zeta: float, **kw):
    return PR.assess_parametric_roll(
        LARGE, wave_height=5.0, wavelength=LARGE.L, t_roll=15.6, zeta=zeta,
        speed=8.0, heading_deg=0.0, n_phase=24, n=401, steps=600, **kw
    )


def test_assess_verdict_is_coherent_with_thresholds():
    res = _assess(0.02)
    eps = res["mathieu"]["eps"]
    eps_cr = res["eps_critical"]
    assert res["geometry_layer"]["dGM_ratio"] == pytest.approx(eps)
    assert res["criteria"]["eps_gt_numeric_threshold"] == (eps > eps_cr)
    assert res["verdict"] == ("SUSCEPTIBLE" if res["floquet"]["unstable"] else "NOT_SUSCEPTIBLE")
    assert res["criteria"]["primary_resonance_eps_gt_4zeta"] == (eps > 4.0 * 0.02)


def test_assess_susceptibility_monotone_in_damping():
    """阻尼越大越难起振：判决随 ζ 单调（先 SUSCEPTIBLE 后 NOT_SUSCEPTIBLE）。"""
    verdicts = [_assess(z)["verdict"] for z in (0.005, 0.02, 0.1)]
    assert verdicts[0] == "SUSCEPTIBLE"
    assert verdicts[-1] == "NOT_SUSCEPTIBLE"
    assert verdicts == sorted(verdicts, reverse=True) or verdicts == [
        "SUSCEPTIBLE", "SUSCEPTIBLE", "NOT_SUSCEPTIBLE"
    ]


def test_assess_reproducible():
    a, b = _assess(0.02), _assess(0.02)
    assert a["floquet"]["rho_max"] == b["floquet"]["rho_max"]
    assert a["verdict"] == b["verdict"]


def test_assess_rejects_nonpositive_wave_height():
    with pytest.raises(ValueError):
        PR.assess_parametric_roll(LARGE, wave_height=0.0, wavelength=200.0,
                                  t_roll=15.0, zeta=0.05)