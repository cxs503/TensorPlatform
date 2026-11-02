"""m1662 — 批量链路敞水性能接线（MOCK 代理 → BEM 真实模型）判据锁。

背景：`backend/services/propeller_open_water.py`（m1393）已用叶元+动量 BEM 取代
硬编码代理，但 `tools/batch_scan/batch_executor.py::ShipCalculationEngine.calc_propeller`
**未接线**，千工况批量链路仍在返回
``KT = max(0.05, 0.5-0.3J)`` / ``KQ = max(0.01, 0.05+0.02J)`` / ``eta0 = clip(.,0.3,0.85)``。
本文件把「代理指纹」写成可机器裁决的负面判据（W1–W8），使任何回落立即变红。

判据基线：全部为量纲不变量、恒等式与解析上界（η_ideal = 2/(1+√(1+C_T))），
不引用任何文献表值，因此可离线自证。
"""
from __future__ import annotations

import math
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "batch_scan"))

from batch_executor import ShipCalculationEngine as Engine  # noqa: E402

BASE = {
    "diameter_m": 5.0,
    "pitch_ratio": 1.0,
    "blade_area_ratio": 0.55,
    "blade_count": 4,
    "rpm": 100.0,
}
KNOT = 0.5144


def _pt(**kw):
    return Engine.calc_propeller(dict(BASE, **kw))


# ── W1 模型身份 ────────────────────────────────────────────────────────
def test_w1_model_tag_is_bem():
    out = _pt(advance_speed_kn=6.0)
    assert out["model"].startswith("bem"), out["model"]


def test_w1_legacy_keys_preserved():
    """旧调用方（CaseExecutor/批量结果表）依赖这五个键，键名不得改。"""
    out = _pt(advance_speed_kn=6.0)
    for key in ("advance_coefficient", "thrust_coefficient", "torque_coefficient",
                "efficiency", "thrust_kn"):
        assert key in out and isinstance(out[key], (int, float)), key


# ── W2 代理指纹：K_Q/K_T ≡ 0.1 恒定 ────────────────────────────────────
def test_w2_torque_thrust_ratio_is_not_constant():
    """代理的 KQ = 0.1·KT 使比值恒为 0.1；真实螺旋桨随载荷/几何显著变化。"""
    ratios = []
    for pd in (0.8, 1.0, 1.2, 1.4):
        out = _pt(pitch_ratio=pd, advance_speed_kn=6.0)
        ratios.append(out["torque_coefficient"] / out["thrust_coefficient"])
    spread = max(ratios) - min(ratios)
    assert min(ratios) > 0.05, ratios
    assert spread > 0.02, f"KQ/KT 近乎恒定 ({ratios}) — 代理指纹未消除"


# ── W3 效率钳位：eta0 恒为 0.850 ───────────────────────────────────────
def test_w3_efficiency_is_not_clamped_to_085():
    etas = [_pt(advance_speed_kn=kn)["efficiency"] for kn in (2, 4, 6, 8, 10)]
    assert len(set(round(e, 4) for e in etas)) == len(etas), f"效率出现平台/钳位: {etas}"
    assert all(e < 0.85 for e in etas), etas             # 无 0.85 顶格
    assert etas == sorted(etas), "低 J 段效率应随 J 单调上升"  # 代理同样上升，故仅辅证


# ── W4 能量不变量：0 ≤ eta0 < eta_ideal（解析上界，必须严格成立）────────
def test_w4_efficiency_below_actuator_disk_bound():
    for kn in (0.5, 2, 4, 6, 8, 10):
        out = _pt(advance_speed_kn=kn)
        eta0 = out["efficiency"]
        eta_i = out["ideal_efficiency_bound"]
        assert 0.0 <= eta0 < eta_i, (kn, eta0, eta_i)
        # 恒等式自检：eta0 = J·KT/(2π·KQ)
        ident = out["advance_coefficient"] * out["thrust_coefficient"] / (
            2.0 * math.pi * out["torque_coefficient"])
        assert abs(ident - eta0) < 5e-4, (kn, ident, eta0)


# ── W5 载荷物理：K_T、K_Q 随 J 单调下降，且存在零推力 J0 ────────────────
def test_w5_coefficients_decrease_with_advance_ratio():
    pts = [_pt(advance_speed_kn=kn) for kn in (0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20)]
    kts = [p["thrust_coefficient"] for p in pts]
    kqs = [p["torque_coefficient"] for p in pts]
    assert all(b < a for a, b in zip(kts, kts[1:])), kts
    assert all(b < a for a, b in zip(kqs, kqs[1:])), kqs
    assert kts[0] > 0.0 and kts[-1] < 0.0, "J 扫掠须跨过零推力点 J0"


# ── W6 无量纲不变性（Froude 缩放：J 相同 ⇒ K_T/K_Q/η0 位级一致）────────
def test_w6_froude_scaling_invariance():
    a = Engine.calc_propeller(dict(BASE, advance_speed_kn=6.0))
    # D×2, n÷2 ⇒ n·D 不变 ⇒ (Va, J) 不变；敞水系数为无量纲量，必须逐位一致
    b = Engine.calc_propeller(dict(BASE, diameter_m=10.0, rpm=50.0, advance_speed_kn=6.0))
    assert a["advance_coefficient"] == b["advance_coefficient"]
    for key in ("thrust_coefficient", "torque_coefficient", "efficiency"):
        assert abs(a[key] - b[key]) <= 1e-4, (key, a[key], b[key])


# ── W7 失败即报错：模型不可导入时禁止回落代理 ──────────────────────────
def test_w7_fail_closed_without_bem_model(monkeypatch):
    cached_mod = Engine._BEM_MODULE
    cached_geom = dict(Engine._BEM_CACHE)
    try:
        Engine._BEM_MODULE = None
        Engine._BEM_CACHE.clear()
        monkeypatch.setitem(sys.modules, "services.propeller_open_water", None)
        with pytest.raises(RuntimeError, match="PROPELLER|BEM|importable|MOCK"):
            Engine.calc_propeller(dict(BASE, advance_speed_kn=6.0))
    finally:
        Engine._BEM_MODULE = cached_mod
        Engine._BEM_CACHE.clear()
        Engine._BEM_CACHE.update(cached_geom)


# ── W8 输入守卫：几何/转速越界必须 ValueError（不得静默出数）───────────
@pytest.mark.parametrize("bad", [
    {"diameter_m": 0.0, "rpm": 100},
    {"diameter_m": 5.0, "rpm": 0.0},
    {"diameter_m": 5.0, "rpm": 100, "advance_speed_kn": -1.0},
    {"diameter_m": 5.0, "rpm": 100, "blade_count": 9},
    {"diameter_m": 5.0, "rpm": 100, "blade_area_ratio": 2.0},
])
def test_w8_rejects_invalid_inputs(bad):
    with pytest.raises(ValueError):
        Engine.calc_propeller(dict(BASE, **bad))


# ── W9 批量执行器端到端：propeller 类工况走真实模型并可判定 ─────────────
def test_w9_case_executor_propeller_case_runs():
    from batch_executor import CaseExecutor

    executor = CaseExecutor()
    result = executor.execute({
        "case_id": "W9-prop", "category": "propeller",
        "params": dict(BASE, advance_speed_kn=6.0),
        "criteria": {"efficiency": {"min": 0.0, "max": 1.0}},
    })
    assert result.status.value == "success", result.error
    assert result.result["model"].startswith("bem")
    assert 0.0 < result.result["efficiency"] < 1.0