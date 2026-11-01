"""unit tests：m1490 修复规格（src/ship_perf）的解析校核。

覆盖
----
* KB 的**两个解析极限**：矩形浮箱 Cb=1 → T/2；V 形楔 Cb=0.5 → 2T/3
* 大倾角 GZ：wall-sided **有效域内精确复现**（θ ≤ atan(T/B) 且无甲板入水）
* 大倾角 GZ：90° 极限解析值 GZ = D/2 − KG（浮箱侧躺）
* 消失角：GZ=0 真实根，且随 KG 单调变化（非 60° 常数）
* ITTC-57：Cf(1e7) 与公开值一致
* Holtrop 域：水下回转体在 surface 分支必须显式拒绝
* 水下回转体：Ct/Cf 落在 [(1+k), (1+k)+Ca/Cf+app] 区间
* BEMT：KT 单调降、KQ>0、η0 ≤ 致动盘上界、J0 随 P/D 增大
* 推力平衡：T(1−t)=Rt 反解收敛
* conformance：ref 12/12 PASS（判据自检）
"""

from __future__ import annotations

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ship_perf import conformance as cf  # noqa: E402
from ship_perf import hydrostatics as hs  # noqa: E402
from ship_perf import propeller as pr  # noqa: E402
from ship_perf import resistance as rs  # noqa: E402


# ---------------------------------------------------------------- KB


def test_kb_box_limit_is_half_draft():
    """矩形浮箱（Cb=1）：KB = T/2，恰好回退到解析值。"""
    for t in (3.0, 6.0, 16.0):
        assert hs.kb_from_cb(t, 1.0) == pytest.approx(t / 2.0, rel=1e-12)


def test_kb_wedge_limit_is_two_thirds():
    """V 形楔（Cb=0.5）：KB = 2T/3（另一半解析极限）。"""
    assert hs.kb_from_cb(6.0, 0.5) == pytest.approx(2.0 * 6.0 / 3.0, rel=1e-12)


def test_kb_monotone_decreasing_in_cb():
    vals = [hs.kb_from_cb(10.0, cb) for cb in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)]
    assert all(a > b for a, b in zip(vals, vals[1:]))


def test_kb_matches_trapezoid_first_moment():
    """KB 与梯形剖面一阶矩一致（几何自洽，不是拍脑袋插值）。"""
    b, t, cb = 16.0, 12.0, 0.7
    r = hs.evaluate_stability(b, t, 30.0, cb, kg=8.0, angles=[0.0])
    yk = b * (cb - 0.5)
    area = 2 * 0.5 * (yk + b / 2) * t
    zb = t * (yk + b) / (3.0 * (yk + b / 2))  # 梯形形心高（底半宽 yk、顶半宽 B/2）
    assert area == pytest.approx(cb * b * t)
    assert r.kb == pytest.approx(zb, rel=1e-12)


# ---------------------------------------------------------------- 大倾角 GZ


def test_gz_reproduces_wall_sided_exactly_in_valid_domain():
    """浮箱：θ ≤ atan(T/B) 且未甲板入水时，wall-sided 精确 ⇒ 数值解须逐点吻合。"""
    b, t, d, cb, kg = 16.0, 16.0, 26.0, 1.0, 8.0
    ang = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0]
    r = hs.evaluate_stability(b, t, d, cb, kg, angles=ang)
    ws = hs.wall_sided_gz(ang, r.gm, r.bm)
    assert math.degrees(math.atan(t / b)) == pytest.approx(45.0)
    for a, g, w in zip(ang, r.gz, ws):
        assert g == pytest.approx(w, abs=1e-6), f"θ={a}° 偏离 wall-sided"


def test_gz_at_ninety_degrees_matches_analytic():
    """浮箱侧躺到 90°：浸没宽度 = B·T/D，浮心 z=D/2 ⇒ GZ = D/2 − KG。"""
    b, t, d, kg = 16.0, 6.0, 10.0, 6.0
    r = hs.evaluate_stability(b, t, d, 1.0, kg, angles=[0.0, 90.0])
    assert r.gz[-1] == pytest.approx(d / 2.0 - kg, abs=1e-6)


def test_gz_small_angle_slope_equals_gm():
    b, t, d, cb, kg = 16.0, 16.0, 26.0, 1.0, 8.0
    r = hs.evaluate_stability(b, t, d, cb, kg, angles=[0.0, 0.5])
    slope = (r.gz[1] - r.gz[0]) / math.radians(0.5)
    assert slope == pytest.approx(r.gm, rel=5e-3)


def test_vanishing_angle_is_real_root_and_depends_on_kg():
    """消失角必须是 GZ=0 的根，且随 KG 变化（拒绝 60° 常数封顶）。"""
    b, t, d, cb = 16.0, 6.0, 10.0, 1.0
    a = hs.evaluate_stability(b, t, d, cb, 6.0)
    bcase = hs.evaluate_stability(b, t, d, cb, 5.6)
    assert a.vanishing_angle is not None and bcase.vanishing_angle is not None
    assert abs(hs.gz_at(a.angles, a.gz, a.vanishing_angle)) < 1e-3
    assert abs(hs.gz_at(bcase.angles, bcase.gz, bcase.vanishing_angle)) < 1e-3
    assert abs(bcase.vanishing_angle - a.vanishing_angle) > 5.0


def test_high_freeboard_box_has_no_vanishing_angle_below_90():
    """干舷足够高时 0–90° 内无 GZ=0 根 ⇒ 必须返回 None，不得编造角度。"""
    r = hs.evaluate_stability(16.0, 6.0, 16.0, 1.0, 5.0)
    assert r.gz[-1] > 0.0
    assert r.vanishing_angle is None


# ---------------------------------------------------------------- 阻力


def test_ittc57_reference_value():
    """ITTC-57 在 Re=1e7 处 Cf=0.00300（公开值 3.0e-3 量级）。"""
    assert rs.ittc57_cf(1e7) == pytest.approx(0.003002, abs=5e-5)


def test_holtrop_domain_rejects_submerged_body_on_surface_branch():
    h = cf.suboff_hull()
    why = rs.holtrop_domain(h, target="surface")
    assert why, "圆剖面回转体不得进入水面船兴波回归"
    with pytest.raises(rs.OutOfDomainError):
        rs.holtrop_surface_resistance(h, 5.0)


def test_submerged_body_ct_bounded_by_friction_and_allowances():
    """Ct/Cf 必须落在 [(1+k), (1+k)+Ca/Cf+app/Cf] 内（摩擦主导，无兴波）。"""
    h = cf.suboff_hull()
    p = rs.submerged_body_resistance(h, 5.0, appendages=rs.SUBOFF_APPENDAGES)
    app = rs.appendage_drag_area(rs.SUBOFF_APPENDAGES) / p.surface
    upper = 1.20 + (rs.roughness_allowance(h.lwl) + app) / p.cf
    assert p.ct_over_cf >= 1.20 - 1e-9
    assert p.ct_over_cf <= upper + 1e-6
    assert 0.0040 <= p.ct <= 0.0050, "SUBOFF AFF-8 模型尺度 Ct 应在 0.0044 量级"


def test_surface_ship_cr_is_small_at_moderate_froude():
    """Fn=0.23、Cb=0.7 时 Cr 应为 O(1e-3)；分段线性越域式给出 6.5e-3（错 4×）。"""
    p = rs.holtrop_surface_resistance(cf.ship_hull(), cf.CASE_SHIP.payload["speed_ms"])
    cr = p.ct - p.form_factor_1pk * p.cf - rs.roughness_allowance(cf.ship_hull().lwl)
    assert 4e-4 <= cr <= 3e-3


# ---------------------------------------------------------------- 推进


def test_bemt_kt_monotone_and_kq_positive():
    ks = [pr.open_water(j, 4.0, 4, 1.0, 0.55) for j in (0.0, 0.2, 0.4, 0.6, 0.8)]
    assert all(a.kt > b.kt for a, b in zip(ks, ks[1:]))
    assert all(k.kq > 0.0 for k in ks)


def test_bemt_eta_never_exceeds_actuator_disk():
    for j in (0.1, 0.3, 0.5, 0.7, 0.9):
        o = pr.open_water(j, 4.0, 4, 1.0, 0.55)
        a0 = math.pi * 16.0 / 4.0
        ct = 8.0 * o.kt / (math.pi * j * j)
        assert o.eta0 <= pr.ideal_efficiency(ct) + 1e-9
        assert o.eta0 <= 0.998


def test_bemt_zero_thrust_j_increases_with_pitch():
    j0_lo = pr.zero_thrust_j(4.0, 4, 0.8, 0.55)
    j0_hi = pr.zero_thrust_j(4.0, 4, 1.2, 0.55)
    assert j0_hi > j0_lo


def test_thrust_balance_holds_at_operating_point():
    rp = rs.holtrop_surface_resistance(cf.ship_hull(), cf.CASE_SHIP.payload["speed_ms"])
    pt = pr.solve_n_for_thrust(rp.rt, cf.CASE_SHIP.payload["speed_ms"], 4.0, 0.70)
    assert pt.equilibrium_error < 1e-3
    assert 0.10 <= pt.kt <= 0.35
    assert 0.5 <= pt.j <= 1.2
    assert pt.eta0 < pt.eta_ideal <= 1.0


# ---------------------------------------------------------------- 判据自检


def test_conformance_ref_gate_all_pass():
    res = cf.run_all(cf.RefTarget())
    s = cf.summarize(res)
    assert s["required_total"] == 12
    assert s["gate_pass"], cf.format_table(res)
    assert all(s["flip_pass"].values()) and all(s["regression_pass"].values())
    assert s["extra_pass"] == s["extra_total"]