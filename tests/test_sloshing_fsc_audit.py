"""液舱自由液面修正 / 滚-液晃链的解析恒等式闸门（审计单 m2029）。

判据全部是**无自由参数**的解析恒等式，不采用"跑到什么数就认什么数"的回归口径：

A1  i_FS（矩形自由液面面积二次矩）闭式 vs 逐带数值求积；
A2  GG₀ = Σρ_j i_j /(ρ_sea ∇)，∇ 必须是 m³（kg→t 换算不可省）；
A3  静态液晃反作用力矩 = ρ_l g i_FS φ；
A4  ω₁² = g k₁ tanh(k₁ h)（浅水/深水两极限）；
A5  等效摆长 l = g/ω₁²。

另有两条**现状判据**（钉住已知缺口，修好即红→提示更新本文件）：
D2  现网 2-DOF 单摆方程无船体激励 ⇒ β ≡ 0、静态反作用为 0。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_spec = importlib.util.spec_from_file_location(
    "sloshing_fsc_audit", os.path.join(_ROOT, "tools", "audit", "sloshing_fsc_audit.py"))
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

from backend.services.ice_integration import nodes_downstream as nd  # noqa: E402


P = dict(A.N03_DEFAULTS)


# --------------------------------------------------------------------------
# A1 / A2 自由液面面积二次矩与自由液面修正
# --------------------------------------------------------------------------
def test_A1_free_surface_inertia_closed_form_matches_quadrature():
    i_cf = A.free_surface_inertia_rect(30.0, 40.0)
    i_q = A.free_surface_inertia_quadrature(30.0, 40.0, n=200000)
    assert i_cf == pytest.approx(160_000.0, rel=1e-12)
    assert i_q == pytest.approx(i_cf, rel=1e-6)


def test_A2_nabla_requires_kg_to_tonne_conversion():
    nabla_true = P["ship_mass"] / 1000.0 / 1.025
    nabla_true_t = P["ship_mass"] / 1000.0 / 1.025      # t -> m^3
    assert nabla_true == pytest.approx(nabla_true_t)
    assert nabla_true == pytest.approx(117_073.17, rel=1e-6)   # 120000 t / 1.025 t/m^3


def test_A2_fsc_identity_value():
    """GG₀ = ρ_l i_FS /(ρ_sea ∇) = 0.6267 m（默认 N03 工况）。"""
    i_fs = A.free_surface_inertia_rect(P["tank_length"], P["tank_width"])
    nabla = P["ship_mass"] / 1000.0 / 1.025
    fsc = A.free_surface_correction(P["liquid_density"] * i_fs, A.RHO_SEA, nabla)
    assert fsc == pytest.approx(0.62667, rel=1e-4)
    assert fsc == pytest.approx(P["liquid_density"] * i_fs / (1025.0 * nabla), rel=1e-12)


def test_A2_shipped_formula_was_low_by_exactly_1000():
    """历史算式 ship_mass/1.025 的偏差必须是恰好 1000 倍（量纲缺陷的指纹）。"""
    shipped = A.run_node03_fsc_as_shipped(P)
    i_fs = A.free_surface_inertia_rect(P["tank_length"], P["tank_width"])
    fsc_true = A.free_surface_correction(P["liquid_density"] * i_fs, A.RHO_SEA,
                                         P["ship_mass"] / 1000.0 / 1.025)
    assert fsc_true / shipped["fsc_as_shipped"] == pytest.approx(1000.0, rel=1e-9)


def test_A2_run_node03_now_uses_true_nabla():
    """修后：服务侧 fsc 必须与解析恒等式一致（相对 1e-12）。"""
    t = np.linspace(0.0, 20.0, 2001)
    res = nd.run_node03(1e7 * np.sin(2 * math.pi * t / 8.0), t)
    i_fs = P["tank_length"] * P["tank_width"] ** 3 / 12.0
    fsc_true = P["liquid_density"] * i_fs / (1025.0 * (P["ship_mass"] / 1000.0 / 1.025))
    assert res.meta["free_surface_correction"] == pytest.approx(fsc_true, rel=1e-12)
    assert res.payload["gm_corrected"] == pytest.approx(res.meta["gz_curve"]["GM0"] - fsc_true, rel=1e-12)


def test_A2_gm_overestimate_magnitude():
    """修正前后 GM 差 = fsc_true - fsc_shipped ≈ 0.626 m（占 GM₀ 的 23.3%）。"""
    g = A.gz_and_gm(P)
    shipped = A.run_node03_fsc_as_shipped(P)["fsc_as_shipped"]
    i_fs = A.free_surface_inertia_rect(P["tank_length"], P["tank_width"])
    fsc_true = A.free_surface_correction(P["liquid_density"] * i_fs, A.RHO_SEA,
                                        P["ship_mass"] / 1000.0 / 1.025)
    delta = fsc_true - shipped
    assert delta == pytest.approx(0.62604, abs=1e-4)
    assert delta / g["GM0"] == pytest.approx(0.2323, abs=1e-3)


# --------------------------------------------------------------------------
# A3 / A4 / A5 静态反作用与频率恒等式
# --------------------------------------------------------------------------
def test_A3_static_liquid_moment_identity():
    """静态液晃反作用力矩 = ρ_l g i_FS φ（φ=5° 时 6.438e7 N·m）。"""
    i_fs = A.free_surface_inertia_rect(P["tank_length"], P["tank_width"])
    m = A.correct_static_liquid_moment(P, i_fs, P["liquid_density"], math.radians(5.0))
    assert m == pytest.approx(P["liquid_density"] * A.G * i_fs * math.radians(5.0), rel=1e-12)
    assert m == pytest.approx(6.438e7, rel=1e-3)


def test_A3_pivot_height_from_static_identity():
    """z_p = ρ_l i_FS / m_s；与 m_s = ρ_l L B h 联立后 z_p = B²/(12h)。"""
    i_fs = A.free_surface_inertia_rect(P["tank_length"], P["tank_width"])
    h = 6.0
    m_s = P["liquid_density"] * P["tank_length"] * P["tank_width"] * h
    z_p = A.pivot_height_from_static_identity(i_fs, P["liquid_density"], m_s)
    assert z_p == pytest.approx(P["tank_width"] ** 2 / (12.0 * h), rel=1e-12)
    assert z_p == pytest.approx(22.2222, rel=1e-4)


def test_A4_dispersion_limits():
    L, n = 40.0, 1
    k = n * math.pi / L
    shallow = A.omega_analytic(L, 1e-3, n)
    deep = A.omega_analytic(L, 1e5, n)
    assert shallow == pytest.approx(math.sqrt(A.G * k * k * 1e-3), rel=1e-5)
    assert deep == pytest.approx(math.sqrt(A.G * k), rel=1e-6)
    assert shallow < A.omega_analytic(L, 6.0, n) < deep


def test_A5_pendulum_length_identity():
    """l = g/ω²；并与矩形舱解析式 l = (L/π)coth(πh/L) 对齐（同一恒等式两种写法）。"""
    # ω 必须低于深水截止 sqrt(g k) = 0.8777 rad/s，否则不存在实液深
    for w in (0.4, 0.6, 0.85):
        l = A.pendulum_length(w)
        h = A._depth_from_omega(w, 40.0)
        k = math.pi / 40.0
        l_geom = (1.0 / k) / math.tanh(k * h)
        assert l == pytest.approx(l_geom, rel=1e-9)


# --------------------------------------------------------------------------
# D2 现状判据（缺口钉住；修好本组应红，提示更新文档与用例）
# --------------------------------------------------------------------------
def test_D2_shipped_pendulum_has_no_hull_excitation():
    t = np.linspace(0.0, 60.0, 6001)
    r = A.shipped_pendulum_free_response(t, P["slosh_omega"], 0.0)
    assert r["beta_max"] == pytest.approx(0.0, abs=1e-14)
    assert r["reaction_max"] == pytest.approx(0.0, abs=1e-9)


def test_D2_shipped_eigen_roots_are_decoupled():
    e = A.shipped_eigen_roots(P, 3.384e6)
    assert e["omega_low"] == pytest.approx(min(e["omega_roll"], e["omega_slosh"]), rel=1e-9)
    assert e["omega_high"] == pytest.approx(max(e["omega_roll"], e["omega_slosh"]), rel=1e-9)


def test_D2_correct_system_is_coupled_and_reproduces_static_limit():
    """正确耦合系统：两根本征值不再等于 sqrt(C/I)、sqrt(g/l)，但在 m→0 极限退化回去。"""
    i_fs = A.free_surface_inertia_rect(P["tank_length"], P["tank_width"])
    m_s = P["liquid_density"] * P["tank_length"] * P["tank_width"] * 6.0
    ec = A.correct_eigen_roots(P, m_s, i_fs, P["liquid_density"])
    es = A.shipped_eigen_roots(P, m_s)
    assert ec["omega_low"] != pytest.approx(min(es["omega_roll"], es["omega_slosh"]), rel=1e-4)
    # 二次方程常数项 = C·G ⇒ 低频根由液体刚度主导而非退化
    I = A.ship_roll_stiffness(P)["I"]
    C = A.ship_roll_stiffness(P)["C"]
    l = A.pendulum_length(P["slosh_omega"])
    assert ec["omega_low"] ** 2 * ec["omega_high"] ** 2 == pytest.approx(C * A.G / (I * l), rel=1e-9)


def test_D2_roll_amplification_is_unity_consequence():
    """缺口后果：液晃对横摇无反作用 ⇒ roll_amplification_factor ≈ 1。"""
    t = np.linspace(0.0, 40.0, 4001)
    res = nd.run_node03(1e7 * np.sin(2 * math.pi * t / 12.0), t)
    assert res.payload["roll_amplification_factor"] == pytest.approx(1.0, abs=0.01)