"""m1948 回归测试：MMG 参考核的舵来流（尾流加速）结构 + 回转圆 A 类恒等式。

覆盖三类判据：
1. **结构缺陷证据**（可复算、不依赖文献）：被检核的尾流加速因子与 J 无关，
   而 MMG 标准表达式 ``1 + 8K_T/(πJ²)`` 必须随 J（即随航速）强烈变化。
2. **A 类数值锚**（与文献无关）：Newton 定常平衡残差、RK4 与 Newton 一致、
   回转圆的直径/直角弦/闭合恒等式（旋转不变口径）。
3. **口径缺陷证据**：`turning_indices` 旧口径 ``2|y|@180°`` 恰为圆直径的 2 倍。

Run: python3 -m pytest tests/verifier/test_mmg_turning_inflow_audit.py -q
"""
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "verifier"))

import mmg_reference as R                                  # noqa: E402
import mmg_turning_inflow_audit as AUDIT                   # noqa: E402

L, U, DELTA = 320.0, 7.97, 35.0


@pytest.fixture(scope="module")
def n_p():
    return R.self_propulsion_n(R.MMGParams())


# ------------------------------------------------- 1. 结构缺陷证据（D1）
def test_shipped_inflow_factor_is_j_independent_defect(n_p):
    """被检核转写因子在 u'∈[0.3,1.0] 上几乎不变 —— 结构性缺陷的直接证据。

        物理因子 sqrt(1 + 8K_T/(πJ²)) 在 J→0 时发散，散布必须 ≫ 10%；
        被检核转写等价位 ``sqrt(1 + 16 K_T/π)``，与 J 无关，散布仅 ~7%。
    """
    struct = AUDIT.race_structure()
    spread = struct["factor_spread_over_u_scan"]
    assert spread["as_shipped"] < 0.10                      # 缺陷：近乎 J 无关
    assert spread["half"] > 1.0                             # 物理因子：强 J 依赖
    assert spread["quarter"] > 0.5
    # 转写恒等式：as_shipped == sqrt(1 + 16 K_T/π)，逐点 1e-12
    assert struct["as_shipped_identity_dev"] < 1e-12


def test_physical_inflow_grows_as_speed_drops(n_p):
    """J 相关因子必须随 u' 下降单调增大（螺旋桨负荷加重）。"""
    prm = R.MMGParams(rudder_inflow="quarter")
    vals = [prm.rudder_inflow_factor(u, n_p) for u in (1.0, 0.8, 0.6, 0.45, 0.3)]
    assert all(a < b for a, b in zip(vals, vals[1:])), vals
    assert vals[-1] / vals[0] > 1.5


def test_one_half_exponent_is_inadmissible(n_p):
    """指数 1/2 给出 u_R/u = 3.32（舵来流超过船速 3 倍）→ 物理不可准入。

    该判定不依赖文献：舵处来流被尾流加速，|u_R| 不可能远超船舶前进速度，
    且该分支的定常平衡解出现 u' < 0（倒航），自相矛盾。
    """
    c = R.MMGParams().c
    b0 = c["eps"] * (1.0 - c["w_P0"])
    half = R.MMGParams(rudder_inflow="half").rudder_inflow_factor(1.0, n_p) * b0
    quarter = R.MMGParams(rudder_inflow="quarter").rudder_inflow_factor(1.0, n_p) * b0
    shipped = R.MMGParams(rudder_inflow="as_shipped").rudder_inflow_factor(1.0, n_p) * b0
    assert half > 1.5
    assert quarter < 1.2
    assert shipped < 1.1
    eq_half = AUDIT.equilibrium(R.MMGParams(rudder_inflow="half"), n_p, DELTA,
                                guess=(0.4, -0.03, 0.2))
    assert eq_half["u_p"] < 0.0          # 自相矛盾的分支


# ------------------------------------------------- 2. A 类数值锚
def test_equilibrium_residual_is_machine_zero(n_p):
    for m in ("as_shipped", "quarter"):
        eq = AUDIT.equilibrium(AUDIT._prm(m), n_p, DELTA)
        assert eq["residual"] < 1e-10, (m, eq["residual"])


def test_rk4_steady_state_agrees_with_newton(n_p):
    """RK4 长时解与 Newton 平衡解一致（积分器无缺陷）。

    容差 1% 覆盖 t'=300 时尚未完全收敛的残余暂态（audit 用 t'=400，
    残差 <1e-3）；Newton 解本身残差为机器零，见上一条测试。
    """
    prm = R.MMGParams()
    run = R.manoeuvre(prm, n_p, DELTA, tau_max=300.0, heading_limit_deg=1e9)
    idx = R.turning_indices(run, L, U)
    eq = AUDIT.equilibrium(prm, n_p, DELTA,
                           guess=(idx["u_end"], float(run["hist"][-1, 1]), idx["r_end_p"]))
    rel = abs(idx["steady_diameter"] / L - eq["D_steady_L"]) / eq["D_steady_L"]
    assert rel < 1e-2, rel


def test_circle_identities_closed(n_p):
    """回转圆几何恒等式：180° 弦 = 2R、90° 弦 = R√2、360° 闭合。"""
    prm = R.MMGParams()
    run = R.manoeuvre(prm, n_p, DELTA, tau_max=400.0, heading_limit_deg=1e9)
    geo = AUDIT.circle_identities(run)
    assert geo["diameter_chord_rel_err"] < 1e-4
    assert geo["right_chord_rel_err"] < 5e-3
    assert geo["closure_rel"] < 5e-3


# ------------------------------------------------- 3. 口径缺陷证据（D2）
def test_shipped_tactical_diameter_is_doubled(n_p):
    """定常圆上 |y|@180° 就是直径 D；被检核口径返回 2×|y| = 2D。"""
    proof = AUDIT.steady_circle_proof("as_shipped", n_p)
    assert proof["rel_err_vs_D"] < 0.02                    # |y|@180 ≈ D
    assert proof["shipped_over_D"] == pytest.approx(2.0, abs=0.05)


def test_turning_indices_exposes_circle_consistent_diameter(n_p):
    """`tactical_diameter_ittc` 必须恰为旧键的一半（口径可追溯，不静默改数）。"""
    prm = R.MMGParams()
    run = R.manoeuvre(prm, n_p, DELTA, tau_max=40.0, heading_limit_deg=1e9)
    idx = R.turning_indices(run, L, U)
    assert idx["tactical_diameter_ittc"] == pytest.approx(
        idx["tactical_diameter"] / 2.0, rel=1e-12)


# ------------------------------------------------- 4. 齐格扎超调记账缺陷（D3）
def test_zigzag_overshoot_tracks_the_actual_peak(n_p):
    """首个超调峰是航向的**最小值**（舵在负向），不是恢复后的最大值。

    历史上 phase 1 取 max，导致超调角被记成 ~0.08°（与轨迹自相矛盾）。
    """
    zz = R.zigzag_indices(R.MMGParams(), n_p, 20.0, 10.0, tau_max=30.0, U=U)
    h = zz["hist"]
    psi_deg = np.degrees(h[:, 5])
    assert zz["first_overshoot_deg"] > 5.0
    # 轨迹真实极值与记账一致（容差 = 一个时间步的航向增量）
    assert zz["first_overshoot_deg"] == pytest.approx(-psi_deg.min() - 10.0, abs=0.5)
    assert zz["second_overshoot_deg"] == pytest.approx(psi_deg.max() - 10.0, abs=0.5)


# ------------------------------------------------- 5. 基准数值回归
def test_steady_diameters_pinned(n_p):
    """KVLCC2 35° 定常回转直径的回归锚（转写变体敏感性）。"""
    d_ship = AUDIT.equilibrium(AUDIT._prm("as_shipped"), n_p, DELTA)["D_steady_L"]
    d_quar = AUDIT.equilibrium(AUDIT._prm("quarter"), n_p, DELTA,
                               guess=(0.4, -0.03, 0.2))["D_steady_L"]
    d_none = AUDIT.equilibrium(AUDIT._prm("none"), n_p, DELTA)["D_steady_L"]
    assert d_ship == pytest.approx(5.025, abs=0.05)
    assert d_quar == pytest.approx(2.785, abs=0.05)
    assert d_none == pytest.approx(6.331, abs=0.05)
    # 单选一个 c：被检核转写 D/L 落在 SIMMAN 带外（> 带上沿），故 WITHHELD 成立
    lo, hi = AUDIT.AUD.BAND["tactical_diameter_L"]
    assert d_ship > hi