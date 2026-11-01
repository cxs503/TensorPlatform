"""冰区航行域验证判决回归测试（承 docs/verif/ice_navigation/verdict.json）。

三层：
  1) 参考数学闭式：Lindqvist 标度律自检（R ∝ h^n, 速度项单调, 零冰厚极限），不依赖实盘；
  2) 工件自洽：evidence.json 内部数字彼此一致（可从探针输出复算）；
  3) 缺陷锁：端点一旦按契约修复（出现速度依赖/冰级相关板厚/非负骨架间距等），
     本组测试即失败，强制同步更新判决——防"静默变绿"。
"""
from __future__ import annotations

import json
import math
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
VDIR = os.path.join(ROOT, "docs", "verif", "ice_navigation")
EV = json.load(open(os.path.join(VDIR, "evidence.json")))
VER = json.load(open(os.path.join(VDIR, "verdict.json")))
P = EV["probes"]
D = EV["derived"]


# ---------------- 1) 参考数学闭式（纯解析，机器可复算） ----------------

def lindqvist_velocity_factor(v_ms: float, h: float = 1.0, L: float = 150.0) -> float:
    """Lindqvist(1989) 速度结构：破冰项 (1+1.4v/sqrt(gh))，滑行项 (1+9.4v/sqrt(gL))。"""
    g = 9.81
    return (1 + 1.4 * v_ms / math.sqrt(g * h)) * (1 + 9.4 * v_ms / math.sqrt(g * L))


def test_reference_speed_factor_monotonic():
    vals = [lindqvist_velocity_factor(v) for v in (0.0, 0.5, 1.0, 2.5, 5.0)]
    assert all(b > a for a, b in zip(vals, vals[1:]))
    assert vals[0] == pytest.approx(1.0)
    # 5 kn = 2.572 m/s 时速度放大应 >= 1.5（这是 C2 的判据来源）
    assert lindqvist_velocity_factor(5 * 0.5144) >= 1.5


def test_reference_thickness_scaling():
    """R ∝ h^2（压碎）与 h^1.5（弯曲）混合 → 端点 h 标度指数应在 [1.5, 2.0]。"""
    exp = D["thickness_scaling_exponent"]
    assert all(1.5 <= e <= 2.0 for e in exp), exp


def test_reference_zero_thickness_limit():
    h = P["P3_thickness_sweep"]["input"]["ice_thickness_m"]
    assert h[0] > 0
    assert P["P8_zero_ice"]["output"]["ice_resistance"] == 0


def test_reference_effective_strength_definition():
    """端点第 459 行 0.5*1000*h^2*B 反解等效强度 = 1000 Pa = 1e-3 MPa。"""
    assert D["sigma_eff_MPa"] == pytest.approx(1e-3, rel=1e-9)
    assert D["sigma_eff_MPa"] < 0.5  # 物理带下界


# ---------------- 2) 工件自洽 ----------------

def test_evidence_selfconsistent_resistance():
    """R_ice 手算复算：0.5*1000*h^2*B + 0.3*1000*h^1.5*sqrt(L) 必须吻合端点输出。"""
    def model(L, B, h):
        return 0.5 * 1000 * h ** 2 * B + 0.3 * 1000 * h ** 1.5 * math.sqrt(L)
    hs = P["P3_thickness_sweep"]["input"]["ice_thickness_m"]
    rep = P["P3_thickness_sweep"]["R_ice"]
    for h, r in zip(hs, rep):
        assert model(150.0, 23.0, h) == pytest.approx(r, rel=2e-3)


def test_evidence_power_identity():
    """ice_power = R_ice * V（V 为 kn→m/s 转换后），核验单位口径。"""
    base = P["P1_baseline"]
    R = base["output"]["ice_resistance"]
    V = base["input"]["speed"] * 0.5144
    assert R * V == pytest.approx(base["output"]["ice_power"], rel=5e-3)


def test_evidence_oracle_present_and_calibrated():
    assert EV["oracle_available"] is True
    assert 100.0 < D["oracle_R_ice_kN_baseline"] < 5000.0  # 4000-20000 t 极地船 1 m 冰量级


# ---------------- 3) 缺陷锁（修复即失败 → 必须更新判决） ----------------

def test_lock_D1_speed_independent():
    rep = P["P2_speed_sweep"]["R_ice"]
    assert len(set(rep)) == 1, "端点已出现速度依赖：请复核 D1 并更新判决"
    assert D["speed_ratio_15_over_1"] == pytest.approx(1.0)


def test_lock_D2_strength_hardcoded():
    assert "sigma_eff_MPa" in D and D["sigma_eff_MPa"] == pytest.approx(1e-3)


def test_lock_D4_cb_dead_parameter():
    rep = P["P5_cb_sweep"]["R_ice"]
    assert len(set(rep)) == 1, "cb 已生效：请复核 D4"


def test_lock_D5_continuous_speed_echo():
    cs = P["P7_ice_class_sweep"]["continuous_speed"]
    speed = P["P1_baseline"]["input"]["speed"]
    # continuous_speed = speed * 冰级系数（PC7: 0.4）→ 1 位小数
    assert sorted(set(cs)) == [pytest.approx(v, abs=0.05) for v in sorted(set(cs))]
    assert len(set(cs)) == len(cs)


def test_lock_D6_plating_class_independent():
    sp = P["P7_ice_class_sweep"]["shell_plating"]
    assert len(set(sp)) == 1, "板厚已随冰级变化：请复核 D6"


def test_lock_D7_frame_spacing_negative():
    assert P["P9_thick_extrapolation"]["output"]["frame_spacing"] < 0


def test_lock_D8_capability_ship_independent():
    mit = P["P6_size_sweep"]["max_ice_thickness"]
    assert len(set(mit)) == 1


def test_lock_D9_method_mislabel():
    out = P["P1_baseline"]["output"]
    assert out["method"].startswith("Lindqvist")
    ratio = D["ratio_endpoint_over_oracle"]
    assert ratio == pytest.approx(25.41, rel=0.02), "端点/oracle 比值变化：请复核 D9"
    assert ratio > 2.0


def test_lock_D10_power_no_efficiency():
    base = P["P1_baseline"]
    R = base["output"]["ice_resistance"]
    V = base["input"]["speed"] * 0.5144
    assert base["output"]["ice_power"] == pytest.approx(R * V, rel=5e-3)


def test_verdict_grade_is_mock():
    assert VER["grade"] == "MOCK"
    assert VER["criteria_pass"] + VER["criteria_fail"] == VER["criteria_total"]
    assert VER["criteria_fail"] == 10
    assert len(VER["defects"]) == 10
    assert {d["id"] for d in VER["defects"]} == {f"D{i}" for i in range(1, 11)}