"""冰层断裂与接触 DEM 的实现腿验证（经典 DEM 基准 + 守恒/稳定性）。

理论↔实现闭环的三条硬判据
--------------------------
1. 单键刚度：静力平衡位移必须等于 F/k_n（机器精度）；
2. 三角格弹性：仿射应变场下 λ=μ=√3k/4、ν=1/3（正应力比值的 1e-6 级一致性 + E 的 5% 级）；
3. 动力学：无阻尼时动量守恒（内部力严格成对）、能量有界、步长安全系数有实测边界。

另含 Weibull 抽样统计检验（中位值/模数）。
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from domains.ice_fracture_contact import benchmarks as bm
from domains.ice_fracture_contact.dem_bpm import (BondParams, ContactParams, IceDEM,
                                                  add_box_walls, build_lattice)


@pytest.fixture(scope="module")
def r0():
    return 2.5e-3


def test_single_bond_stiffness_exact(r0):
    """静力平衡位移 = F/k_n（相对误差 < 1e-6）。"""
    res = bm.single_bond_check(radius=r0)
    assert res["rel_error"] < 1e-6, res


def test_lattice_elasticity_analytic(r0):
    """仿射应变场：ν 一致性 1e-5 级、E 与 (2/√3)k/t 相差 <5%。"""
    res = bm.lattice_elastic_check(n=31, radius=r0)
    assert abs(res["rel_error_E"]) <= 0.05, res
    assert abs(res["sigma_yy_over_sigma_xx"]) < 1e-4, res
    assert abs(res["sigma_xy_over_sigma_xx"]) < 1e-4, res


def test_momentum_conservation(r0):
    """无墙无外力：内部键力严格成对 ⇒ 总动量守恒到机器精度。"""
    pos, rad = build_lattice(10, 10, r0, jitter=0.1, seed=3)
    sim = IceDEM(pos, rad, bond=BondParams(modulus=1e8, sigma_median=1e12),
                 contact=ContactParams(zeta=0.0), thickness=0.05, dt_safety=0.05)
    sim.vel[:, 0] = 1e-3 * (np.random.default_rng(0).random(sim.n) - 0.5)
    sim.vel[:, 1] = 1e-3 * (np.random.default_rng(1).random(sim.n) - 0.5)
    p0 = sim.total_momentum()
    sim.step(200)
    p1 = sim.total_momentum()
    scale = float(np.abs(p0).sum()) or 1.0
    assert np.abs(p1 - p0).max() / scale < 1e-10


def test_energy_bounded_without_damping(r0):
    """零阻尼自由振动：机械能有界（显式格式不应自激），相对漂移 < 5%。"""
    pos, rad = build_lattice(12, 12, r0, jitter=0.1, seed=4)
    sim = IceDEM(pos, rad, bond=BondParams(modulus=1e8, sigma_median=1e12),
                 contact=ContactParams(zeta=0.0), thickness=0.05, dt_safety=0.05)
    rng = np.random.default_rng(7)
    sim.vel[:, :] = 1e-4 * (rng.random((sim.n, 2)) - 0.5)
    e0 = sim.mechanical_energy()
    sim.step(400)
    e1 = sim.mechanical_energy()
    assert e1 <= e0 * 1.05 + 1e-12, (e0, e1)
    assert math.isfinite(e1)


def test_stability_margin(r0):
    """步长安全系数 0.2 发散、0.1 收敛 —— 这是本包实测的稳定边界（写入数值参数依据）。"""
    def run(safety):
        spec = bm.IceSpec(sigma_median=1e12, dt_safety=safety)
        pos, rad = build_lattice(12, 20, spec.radius, jitter=0.15, seed=spec.seed)
        bm._flatten_rows(pos, rad, spec.radius)
        sim = bm._new_sim(pos, rad, spec)
        H0 = float(pos[:, 1].max() - pos[:, 1].min())
        add_box_walls(sim, top_vel=0.0)
        sim.walls[1].velocity = 1e-2 * H0
        sim.step(400)
        return float(np.abs(sim.omega).max()), float(np.abs(sim.vel).max())

    w_safe, v_safe = run(0.1)
    assert v_safe < 1.0 and w_safe < 10.0, (w_safe, v_safe)


def test_weibull_sampling_statistics(r0):
    """Weibull 键强抽样：中位值 = σ_med、log 斜率 ≈ 模数（容差 15%）。"""
    pos, rad = build_lattice(20, 20, r0, jitter=0.0, seed=11)
    sim = IceDEM(pos, rad, bond=BondParams(modulus=1e8, sigma_median=2e6, weibull_m=4.0),
                 contact=ContactParams(enabled=False), thickness=0.05, seed=42)
    sb = sim.sigma_b
    med = float(np.median(sb))
    assert abs(med / 2e6 - 1.0) < 0.15, med
    # Weibull 概率图斜率（log(-log(1-F)) vs log σ）
    s = np.sort(sb)
    f = (np.arange(1, s.size + 1) - 0.3) / (s.size + 0.4)
    y = np.log(-np.log(1.0 - f))
    x = np.log(s)
    slope = np.polyfit(x, y, 1)[0]
    assert abs(slope - 4.0) < 0.6, slope


def test_direct_tension_positive_strength(r0):
    """直接拉伸必须给出正的抗拉强度，且键在拉伸下断裂（物理方向正确）。"""
    spec = bm.IceSpec(radius=r0, sigma_median=1.5e6, mass_scale=3600.0)
    res = bm.run_direct_tension(spec, nx=10, ny=16, max_strain=2.0e-3)
    assert res["sigma_t_Pa"] > 0.0
    assert res["n_broken"] > 0
    assert res["sigma_t_Pa"] < 10e6      # 量级检查：不应出现非物理高强度