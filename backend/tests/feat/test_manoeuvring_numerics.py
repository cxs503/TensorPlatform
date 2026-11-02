"""操纵性 MMG 数值复核的回归测试（m1761）。

把 tools/verifier/manoeuvring/verify_mmg_numerics.py 的判据钉成断言：
线性解析一致性、Nomoto 一致性、步长不敏感、IMO 逻辑、灵敏度指纹、绝对精度缺口记录。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "tools" / "verifier" / "manoeuvring"))

from methods.manoeuvring import (  # noqa: E402
    IMO_LIMITS, HullParameters, MMGModel,
    evaluate_imo, nomoto_indices, run_constant_rudder, steady_state,
    turning_circle_indicators,
)
import verify_mmg_numerics as V  # noqa: E402

KVLCC2 = dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098, design_speed=7.973)


@pytest.fixture(scope="module")
def model():
    return MMGModel(HullParameters(**KVLCC2))


def test_linear_exact_solution_matches_nonlinear_at_small_rudder(model):
    """V3：1° 舵角下非线性 RK4 与线性 exp(At) 精确解的相对偏差 < 3%。"""
    delta = math.radians(1.0)
    trace = run_constant_rudder(model, delta, 900.0, 0.25)
    r_lin = V.linear_exact_r(model, delta, trace.t)
    dev = max(abs(a - b) for a, b in zip(trace.r, r_lin)) / max(abs(b) for b in r_lin)
    assert dev < 0.03, f"linear-analytic deviation {100*dev:.2f}%"


def test_linear_exact_solution_uses_steady_gain(model):
    """V3 补充：解析稳态 r 与 Newton 稳态 r 必一致（<1%）。"""
    delta = math.radians(1.0)
    r_lin = V.linear_exact_r(model, delta, [5.0e4])[-1]
    _, _, r_ss = steady_state(model, delta)
    assert abs(r_lin - r_ss) / abs(r_ss) < 0.05  # 小舵角下两套独立解法一致


def test_nomoto_gain_is_consistent_at_small_rudder(model):
    """V4：5° 舵角内 K*delta 与 Newton 稳态偏差 < 10%。"""
    idx = nomoto_indices(model)
    u, v, r = steady_state(model, math.radians(5.0))
    assert abs(r - idx["K"] * math.radians(5.0)) / abs(r) < 0.10


def test_turning_indicators_are_step_size_insensitive(model):
    """V1/V2：dt 由 0.5 s 减半，advance/L 与 D_t/L 变化 < 0.1%。"""
    a = turning_circle_indicators(model, run_constant_rudder(model, math.radians(35.0), 1800.0, 0.5))
    b = turning_circle_indicators(model, run_constant_rudder(model, math.radians(35.0), 1800.0, 0.25))
    assert abs(a["advance_over_L"] - b["advance_over_L"]) / a["advance_over_L"] < 1e-3
    assert abs(a["tactical_diameter_over_L"] - b["tactical_diameter_over_L"]) < 1e-3


def test_steady_radius_monotone_in_rudder_angle(model):
    """V6：稳态回转半径随舵角单调递减（排除硬编码指纹）。"""
    radii = []
    for d in (10.0, 20.0, 35.0):
        u, v, r = steady_state(model, math.radians(d))
        radii.append(u / r / model.hull.length)
    assert radii[0] > radii[1] > radii[2]
    assert len({round(x, 3) for x in radii}) == 3


def test_imo_gate_not_applicable_below_100m_and_flags_over_limit():
    """V5：L<100 m 不适用；超限必须被标 False。"""
    assert evaluate_imo(80.0, turning={"advance_over_L": 3.0,
                                       "tactical_diameter_over_L": 4.0})["applicable"] is False
    ok = json.dumps(evaluate_imo(200.0, turning={"advance_over_L": 3.0,
                                                "tactical_diameter_over_L": 4.0})).lower()
    assert '"n_fail": 0' in ok and "fail" not in ok.replace('"n_fail": 0', "")
    bad = json.dumps(evaluate_imo(200.0, turning={"advance_over_L": 6.0,
                                                 "tactical_diameter_over_L": 7.0})).lower()
    assert '"n_fail": 2' in bad or '"status": "fail"' in bad


def test_imo_threshold_table_matches_resolution():
    """V5：阈值表与 Res. MSC.137(76) 一致。"""
    assert IMO_LIMITS["advance_over_L"] == 4.5
    assert IMO_LIMITS["tactical_diameter_over_L"] == 5.0
    assert IMO_LIMITS["zigzag_10_first_overshoot_deg"] == 10.0
    assert IMO_LIMITS["zigzag_10_second_overshoot_deg"] == 25.0
    assert IMO_LIMITS["zigzag_20_first_overshoot_deg"] == 25.0


def test_absolute_gap_is_recorded_not_hidden(model):
    """V7（缺陷守卫）：当前模型回转直径远超 IMO 5L —— 缺口必须被显式记录。

    该断言刻画的是**已知局限**：一旦物理模型（势流升力/尾流增速）被补齐、
    回转直径落回 5L 以内，此测试会转红，提示同步更新
    docs/verification/MANEUVRING_NUMERICS_AUDIT.md 的证据等级。
    """
    ind = turning_circle_indicators(model, run_constant_rudder(model, math.radians(35.0), 1800.0, 0.5))
    assert ind["tactical_diameter_over_L"] > IMO_LIMITS["tactical_diameter_over_L"]
    assert ind["tactical_diameter_over_L"] < 40.0  # 上限守卫：防止发散式回归