"""MMG 舵来流 / 阻力-速度回正 / 战术直径口径 三条欠款的回归测试。

覆盖：
  D1  核内"来流加速因子"是 J 无关常数 1+16K_T/π，而文献式是 1+8K_T/(πJ²)（A 类代数）
  D2  纵荡方程缺阻力-速度回正 → 35° 回转 u'_ss=0.414，且 D1/D2 反向抵消
  D3  战术直径口径：核内 2·|y|(180°) 恰为 ITTC 口径的 2 倍，正是历史 5.92L 的来源

Run:  python3 -m pytest tests/verifier/test_mmg_inflow_audit.py -q
"""
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "verifier"))

import mmg_reference as R          # noqa: E402
import mmg_inflow_audit as M       # noqa: E402


@pytest.fixture(scope="module")
def prm_a():
    return M.make("A_legacy")


@pytest.fixture(scope="module")
def n_p(prm_a):
    return R.self_propulsion_n(prm_a)


@pytest.fixture(scope="module")
def turn_a(prm_a, n_p):
    return M.turning_ittc(prm_a, n_p, 35.0)


@pytest.fixture(scope="module")
def turn_e():
    prm = M.make("E_restoring_only")
    return M.turning_ittc(prm, R.self_propulsion_n(prm), 35.0)


# ---------------------------------------------------------------- D1 (A-class)
def test_legacy_race_is_j_independent(prm_a, n_p):
    """核内转写恒等于 1+16K_T/π：换 u 不改变它（对 J 不敏感 = 不是文献式）。"""
    seen = []
    for u in (1.0, 0.6, 0.3):
        T_p, J = prm_a.thrust_p(u, n_p)
        KT = prm_a.c["k0"] + prm_a.c["k1"] * J + prm_a.c["k2"] * J * J
        r_legacy = M.race_factor_legacy(T_p, prm_a.d_p, prm_a.D_p, n_p)
        assert r_legacy == pytest.approx(1.0 + 16.0 * KT / math.pi, rel=1e-12)
        seen.append(1.0 + 8.0 * KT / (math.pi * J * J))
    # 文献式在同一 u 区间跨量级，核内式不动
    assert max(seen) / min(seen) > 10.0


def test_mmg_inflow_stable_form_matches_closed_form(prm_a, n_p):
    """数值稳定式 u_R² = ε²[(1-w)²u² + 8K_T n'²D'²/π] 与闭式等价。"""
    for u in (1.0, 0.7, 0.4144, 0.2):
        T_p, J = prm_a.thrust_p(u, n_p)
        KT = prm_a.c["k0"] + prm_a.c["k1"] * J + prm_a.c["k2"] * J * J
        closed = (prm_a.c["eps"] * (1.0 - prm_a.c["w_P0"]) * u
                  * math.sqrt(1.0 + 8.0 * KT / (math.pi * J * J)))
        assert M.rudder_inflow_mmg(prm_a, u, n_p) == pytest.approx(closed, rel=1e-10)


def test_legacy_inflow_underestimates_rudder_force_at_design_point(prm_a, n_p):
    """设计点 J≈0.277：文献式给 u_R 的 1/(2J²)≈2 倍，法向力差 ≈4 倍。"""
    u = 1.0
    r_leg = M.rudder_inflow_legacy(prm_a, u, n_p)
    r_mmg = M.rudder_inflow_mmg(prm_a, u, n_p)
    ratio = r_mmg / r_leg
    assert 1.8 < ratio < 2.1, ratio
    assert ratio ** 2 > 3.5


def test_jet_dominated_inflow_vs_speed_decaying_legacy(prm_a, n_p):
    """文献式的 u_R 由螺旋桨射流主导（bollard 处有限、量级 ~2），几乎不随船速衰减；
    核内式则 ∝ u，回转中舵效按 u² 塌陷——这是 D1 在物理上的可观测差别。"""
    uR0 = M.rudder_inflow_mmg(prm_a, 0.0, n_p)
    assert 0.5 < uR0 < 5.0, uR0                       # bollard：射流仍在，不发散/不归零
    uR_mmg_slow = M.rudder_inflow_mmg(prm_a, 0.4144, n_p)
    uR_leg_slow = M.rudder_inflow_legacy(prm_a, 0.4144, n_p)
    assert uR_mmg_slow > 1.5                          # 射流主导：低船速仍保有舵效
    assert uR_leg_slow < 0.5                          # 核内式：低船速舵效近乎消失
    assert (uR_mmg_slow / uR_leg_slow) ** 2 > 10.0    # 舵法向力差一个量级


# ---------------------------------------------------------------- D2 (A-class)
def test_as_shipped_speed_loss_is_outside_literature_band(turn_a):
    """现有核的 35° 回转定常速度 u'=0.414，低于 SIMMAN 带 0.50–0.60。"""
    assert turn_a["indices"]["u_steady"] < 0.50
    assert turn_a["indices"]["u_steady"] == pytest.approx(0.414, abs=0.01)


def test_speed_restoring_brings_all_turning_indices_into_band(turn_e):
    """唯一改动：X_H 的 R0 → R0·u'²。四指标同时入带（B 类候选修复）。"""
    idx = turn_e["indices"]
    for key, band in (("advance_L", M.BAND["advance_L"]),
                      ("transfer_L", M.BAND["transfer_L"]),
                      ("tactical_diameter_L", M.BAND["tactical_diameter_L"]),
                      ("steady_diameter_L", M.BAND["steady_diameter_L"]),
                      ("u_steady", M.BAND["u_steady"])):
        lo, hi = band
        assert lo <= idx[key] <= hi, (key, idx[key], band)


def test_developed_turn_ratio_separates_broken_from_calibrated(turn_a, turn_e):
    """发展度判据 |y|(180°)/(2R_ss)：未发展 0.59 vs 已发展 0.92。"""
    assert turn_a["indices"]["developed_ratio"] == pytest.approx(0.59, abs=0.03)
    assert turn_e["indices"]["developed_ratio"] > 0.85


def test_cited_inflow_alone_drives_kernel_nonphysical():
    """把 D1 改成文献式而不补其它项：u'<0（船被倒退），即模型不可用。

    这是"单个常数修不了"的机器证据：说明 D1 与 D2 是**互相抵消**的一对缺陷。
    """
    prm = M.make("B_inflow_only")
    turn = M.turning_ittc(prm, R.self_propulsion_n(prm), 35.0)
    idx = turn["indices"]
    assert idx["diverged"] is True
    assert idx["u_min"] < 0.0
    assert 0.0 < idx["psi_at_first_negative_u_deg"] < 360.0


# ---------------------------------------------------------------- D3 (definition)
def test_tactical_diameter_legacy_is_exactly_double_ittc(turn_a):
    """核内 T_d = 2·|y|(180°)，ITTC 口径 = |y|(180°)：相差精确 2 倍。"""
    idx = turn_a["indices"]
    assert idx["tactical_diameter_legacy_L"] == pytest.approx(
        2.0 * idx["tactical_diameter_L"], rel=1e-12)


def test_documented_5p92_is_reproduced_and_explained(prm_a, n_p):
    """历史判决的 5.92L 被精确复现，且 = 2 × 2.961L（口径翻倍的算术后果）。"""
    run = R.manoeuvre(prm_a, n_p, 35.0)
    idx = R.turning_indices(run, prm_a.L, 1.0)
    assert idx["tactical_diameter"] / prm_a.L == pytest.approx(5.92, abs=0.02)
    assert idx["tactical_diameter"] == pytest.approx(2.0 * idx["tactical_diameter_ittc"],
                                                     rel=1e-12)
    assert idx["tactical_diameter_ittc"] / prm_a.L == pytest.approx(2.96, abs=0.02)


# ---------------------------------------------------------------- protocol
def test_autopilot_trim_holds_course(prm_a, n_p):
    """ITTC 试验初值必须是定常直航（航向误差与艏摇率都 ~0）。"""
    trim = M.autopilot_trim(prm_a, n_p)
    assert abs(trim["heading_err_deg"]) < 1e-6
    assert abs(trim["rate_p"]) < 1e-9


def test_rudder_rate_limit_does_not_change_indices_materially():
    """2.5°/s 限幅（ITTC 标准）vs 瞬时满舵：战术直径与定常直径差 <5%。"""
    prm = M.make("E_restoring_only")
    n = R.self_propulsion_n(prm)
    a = M.turning_ittc(prm, n, 35.0, rate_deg_s=2.5)["indices"]
    b = M.turning_ittc(prm, n, 35.0, rate_deg_s=1e6)["indices"]
    for key in ("tactical_diameter_L", "steady_diameter_L"):
        assert abs(a[key] - b[key]) / abs(b[key]) < 0.05, (key, a[key], b[key])


def test_zigzag_from_trimmed_course_differs_from_zero_rudder_start():
    """Z 形初值必须自定常直航：零舵直航起算会把超调角压到 ~0（既有 0.08° 的成因）。"""
    prm = M.make("A_legacy")
    n = R.self_propulsion_n(prm)
    os_ittc = M.zigzag_ittc(prm, n, 20.0, 20.0)["first_overshoot_deg"]
    os_kernel = R.zigzag_indices(prm, n, 20.0, 20.0)["first_overshoot_deg"]
    assert os_ittc > 20.0
    assert os_kernel < 1.0