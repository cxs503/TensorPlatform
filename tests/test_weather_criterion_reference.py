# -*- coding: utf-8 -*-
"""IMO 天气衡准 (IS Code 2008 A.2.3) 独立参考实现的判据测试。

这些测试固定三件事：
 1. 参考实现本身正确（对闭式解/手算值 <1e-9）；
 2. 判据对输入单调、可翻转（不是常数伪影）；
 3. 平台 /api/v1/dynamic-stability/weather-criterion 的表达与规范的差距
    被钉住（含一条 false-PASS 回归与一条 500 合同缺陷）。
"""
import json
import os
import sys
import urllib.error
import urllib.request

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "verif", "weather"))

from imo_weather import (GUST_FACTOR, P_STD, GZCurve, criterion_sweep,  # noqa: E402
                         int_gz_minus_level, linear_gz_area_analytic,
                         platform_expression, weather_criterion,
                         wind_heeling_lever)

API = "http://127.0.0.1:8001/api/v1/dynamic-stability/weather-criterion"


# ---------------- 1. 定义式 ----------------
def test_wind_heeling_lever_matches_hand_calc():
    lw1 = wind_heeling_lever(504.0, 500.0, 9.0, 8000.0)
    hand = 504.0 * 500.0 * 9.0 / (1000.0 * 9.80665 * 8000.0)
    assert abs(lw1 - hand) / hand < 1e-12


def test_gust_lever_is_1_5_lw1():
    curve = GZCurve(np.arange(0, 61, 10.0), np.array([0, .1, .2, .3, .28, .18, 0.0]))
    lw1 = wind_heeling_lever(P_STD, 500.0, 8.0, 5000.0)
    r = weather_criterion(curve, lw1, 10.0)
    assert r["lw2"] == pytest.approx(GUST_FACTOR * lw1, rel=1e-12)
    assert GUST_FACTOR == 1.5


# ---------------- 2. 交点与积分 ----------------
def test_crossings_exact_for_linear_gz():
    # GZ = 0.01*theta ; 与 level=0.2 的交点必须是 20 deg
    curve = GZCurve(np.arange(0, 61, 5.0), 0.01 * np.arange(0, 61, 5.0))
    cr = curve.crossings(0.2)
    assert len(cr) == 1 and abs(cr[0] - 20.0) < 1e-9


def test_area_matches_closed_form_linear_gz():
    slope, level = 0.01, 0.2
    curve = GZCurve(np.arange(0, 61, 2.0), slope * np.arange(0, 61, 2.0))
    got = int_gz_minus_level(curve, level, 10.0, 40.0)
    ref = linear_gz_area_analytic(10.0, 40.0, slope, level)
    assert abs(got - ref) / abs(ref) < 1e-9


def test_two_routes_converge_on_dense_sampling():
    th = np.arange(0, 61, 1.0)
    gz = 0.9 * (th / 60.0) * (1.0 - th / 90.0)   # 解析光滑曲线
    curve = GZCurve(th, gz)
    a_lin = int_gz_minus_level(curve, 0.15, 5.0, 45.0, route="linear")
    a_pch = int_gz_minus_level(curve, 0.15, 5.0, 45.0, route="pchip")
    assert abs(a_lin - a_pch) / abs(a_lin) < 5e-3


# ---------------- 3. 判据单调性 / 可翻转 ----------------
def test_criterion_flips_with_windage_area():
    th = np.array([0, 10, 20, 30, 40, 50, 60.0])
    gz = np.array([0, .13, .26, .34, .30, .18, 0.0])
    curve = GZCurve(th, gz)
    ratios = []
    for A in (300.0, 500.0, 700.0, 900.0, 1500.0):
        lw1 = wind_heeling_lever(P_STD, A, 10.0, 1800.0)
        ratios.append(weather_criterion(curve, lw1, 15.0)["ratio"])
    finite = [r for r in ratios if np.isfinite(r)]
    assert finite == sorted(finite, reverse=True), "ratio 必须随受风面积单调下降"
    assert ratios[0] > 1.0 > ratios[-1], "判据必须能在该扫描中翻转"


def test_non_compliant_when_gz_never_reaches_gust_lever():
    th = np.arange(0, 61, 10.0)
    gz = np.array([0, .1, .2, .3, .2, .1, 0.0])
    curve = GZCurve(th, gz)
    lw1 = wind_heeling_lever(P_STD, 2000.0, 10.0, 1800.0)   # lw2 远超 GZmax
    r = weather_criterion(curve, lw1, 15.0)
    assert r["area_b"] == 0.0 and r["compliant"] is False


def test_area_a_zero_when_rollback_angle_beyond_gust_crossing():
    th = np.arange(0, 71, 5.0)
    gz = 0.52 * np.sin(np.pi * th / 75.0)
    curve = GZCurve(th, gz)
    lw1 = wind_heeling_lever(P_STD, 1200.0, 9.0, 6000.0)     # lw2 ~0.139 m
    r = weather_criterion(curve, lw1, 25.0)                   # 回摆角已富余
    assert r["area_a"] == 0.0 and r["ratio"] == float("inf") and r["compliant"]


# ---------------- 4. 平台表达被钉住 ----------------
def _probe(payload):
    req = urllib.request.Request(API, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception:  # noqa: BLE001
        return None, None


def _body(keys=("heel", "GZ")):
    gz = [(0, 0.0), (10, .13), (20, .26), (30, .34), (40, .30), (50, .18), (60, 0.0)]
    return {"L": 60.0, "B": 13.0, "T": 3.2, "displacement": 1800.0,
            "GZ_curve": [{keys[0]: a, keys[1]: g} for a, g in gz],
            "wind_pressure": P_STD, "wind_area": 700.0, "wind_lever": 10.0}, gz


def test_platform_false_pass_regression():
    """边界船: 参考判不满足 (b/a<1)，平台算式判满足 -> 假通过。"""
    body, gz = _body()
    plat = platform_expression([p[0] for p in gz], [p[1] for p in gz],
                               P_STD, 700.0, 10.0, 1800.0)
    lw1 = wind_heeling_lever(P_STD, 700.0, 10.0, 1800.0)
    ref = weather_criterion(GZCurve(np.array([p[0] for p in gz], float),
                                    np.array([p[1] for p in gz], float)), lw1, 15.0)
    assert plat["compliant"] is True, "平台算式在此船况判通过（假通过证据）"
    assert ref["compliant"] is False, "规范判据在此船况判不满足"
    status, data = _probe(body)
    if status != 200:
        pytest.skip("平台 API 不可达")
    assert data["compliant"] is True
    assert abs(data["ratio"] - plat["ratio"]) / plat["ratio"] < 0.02


def test_platform_500_on_schema_legal_keys():
    """schema 允许任意 object 键，实现只认 heel/GZ -> KeyError 泄漏为 500。"""
    body, _ = _body(keys=("angle", "gz"))
    status, _ = _probe(body)
    if status is None:
        pytest.skip("平台 API 不可达")
    assert status == 500, f"期望复现合同缺陷(500)，实际 {status}"
