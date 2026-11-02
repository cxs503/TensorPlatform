"""极地验收判据判别力回归（mission 1855，verifier 席）。

被审对象（`docs/audit/ship_api_evidence_ledger.json` 中 evidence=UNCOVERED 的两个端点）：

* `GET /polar/acceptance/channel` → `services.polar.channel.verify_channel_concentrations`
* `GET /polar/acceptance/route`   → `services.polar.optimizer.verify_route_improvement`

本文件**不改任何生产代码**（船舶功能代码不在本仓修，见 EDIT_WHITELIST），只做两件事：

A. 把「判据 (b) 的 hold-out 相对误差 = 混合物密度比 ρ_b(c)/ρ_b(c_a) − 1」这条解析律
   钉成硬断言（数值符合到 1e-15）——这就是判据 (b) 全部信息内容的闭式表达；
B. 用变异电池（mutation battery）+ 反向控制（negative control）测**判别力**：
   判据 (e) 能把坏航线判红（硬断言）；判据 (b) 对物理参数变异无反应
   （以 `xfail(strict=True)` 钉住，实现方把参考式做成真正独立后 strict 会立刻转红提醒更新）。

运行：``python3 -m pytest tests/test_polar_acceptance_discrimination.py -q``
"""
from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from services.polar import channel as ch  # noqa: E402
from services.polar import optimizer as opt  # noqa: E402
from services.polar.constants import KNOT  # noqa: E402
from services.polar.ships import get_ship  # noqa: E402

SHIP_KEY = "handysize_bulker"
V_KN = 6.0
H_B = 0.80


@pytest.fixture(scope="module")
def ship():
    return get_ship(SHIP_KEY)


def _brash(ship, c, include_open_water=False):
    return ch.channel_resistance(
        ship, V_KN * KNOT, H_B, c, include_open_water=include_open_water
    )["brash_n"]


def _criterion_with_mutation(attr: str, factor: float, ship):
    """在变异上下文内同时取判据报告与 c=0.20 的绝对 brash 阻力。"""
    orig = getattr(ch, attr)
    try:
        setattr(ch, attr, orig * factor)
        rep = ch.verify_channel_concentrations(ship, v_kn=V_KN, h_b_m=H_B)
        r020 = _brash(ship, 0.20)
    finally:
        setattr(ch, attr, orig)
    return rep, r020


# ---------------------------------------------------------------------------
# A. 解析律（硬断言）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("c", [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70])
def test_holdout_residual_is_pure_density_ratio(ship, c):
    """判据 (b) 的 brash-only 相对误差必须精确等于 ρ_b(c)/ρ_b(c_a) − 1。

    k 在锚点 c_a=0.40 上拟合后，生产式的 A(c)=ρ_b(c)·g·h·B·T·Cb·(μ+sinβ) 与
    参考式的 A(c_a) 之比把 g·h·B·T·Cb·(μ+sinβ)·P(c)·D 全部约掉。
    """
    v_ms = V_KN * KNOT
    k = ch.calibrate_k(ship, v_ms, H_B, ch.ANCHOR_CONCENTRATION)
    prod = ch.channel_resistance(ship, v_ms, H_B, c, include_open_water=False)["brash_n"]
    ref = ch.channel_resistance_reference(
        ship, v_ms, H_B, c, k_cal=k, include_open_water=False
    )["brash_n"]
    measured = (prod - ref) / ref
    closed_form = ch.brash_density(c) / ch.brash_density(ch.ANCHOR_CONCENTRATION) - 1.0
    assert abs(measured - closed_form) < 1e-14


def test_criterion_b_passes_at_nominal(ship):
    rep = ch.verify_channel_concentrations(ship, v_kn=V_KN, h_b_m=H_B)
    assert rep["criterion"] == "b"
    assert rep["pass"] is True
    # 残余误差来自密度比，量级 3%（阈值 10%）——判据「通过」仅此含义
    assert abs(rep["max_abs_rel_error_pct"] - 100 * abs(
        ch.brash_density(0.70) / ch.brash_density(0.40) - 1.0
    )) < 0.6


# ---------------------------------------------------------------------------
# B. 判别力：判据 (b) 对物理参数变异必须无反应（当前实况 → xfail(strict)）
# ---------------------------------------------------------------------------
_NOMINAL_ERR = None


def _mutation_blind(attr: str, factor: float, ship, rel_shift: float = 0.05):
    """变异后判据仍 PASS，且绝对阻力漂移 ≥ rel_shift —— 即「物理变了，判据没变」。"""
    global _NOMINAL_ERR
    base_rep = ch.verify_channel_concentrations(ship, v_kn=V_KN, h_b_m=H_B)
    base_r = _brash(ship, 0.20)
    rep, r020 = _criterion_with_mutation(attr, factor, ship)
    shift = abs(r020 - base_r) / base_r
    assert shift > rel_shift, f"变异 {attr}×{factor} 未真正改变物理（shift={shift:.3%}）"
    # 期望：变异应让判据变红。当前实况不成立 ⇒ 用 xfail 钉住
    assert rep["pass"] is False, (
        f"{attr}×{factor} 把 c=0.20 的 brash 阻力改变了 {shift:.1%}，"
        f"判据 (b) 仍然 PASS（max_err={rep['max_abs_rel_error_pct']:.3f}%）"
    )


@pytest.mark.xfail(strict=True, reason="判据 (b) 的参考式与生产式共享全部物理项，只在锚点标定；"
                                     "浓度堆积指数变异 30% 仍 PASS（判别力为零）")
def test_criterion_b_should_reject_packing_exponent_mutation(ship):
    _mutation_blind("C_PACKING_EXP", 1.30, ship)


@pytest.mark.xfail(strict=True, reason="速度动力因子 C_VEL 翻倍后判据仍 PASS")
def test_criterion_b_should_reject_velocity_factor_mutation(ship):
    _mutation_blind("C_VEL", 2.0, ship)


@pytest.mark.xfail(strict=True, reason="brash/hull 摩擦系数 +50% 后判据仍 PASS")
def test_criterion_b_should_reject_friction_mutation(ship):
    _mutation_blind("MU_BRASH", 1.5, ship)


@pytest.mark.xfail(strict=True, reason="船宽放大 30% 后判据仍 PASS（几何项全部被约掉）")
def test_criterion_b_should_reject_beam_mutation(ship):
    mut_ship = dataclasses.replace(ship, beam_m=ship.beam_m * 1.3)
    base_r = _brash(ship, 0.20)
    rep = ch.verify_channel_concentrations(mut_ship, v_kn=V_KN, h_b_m=H_B)
    shift = abs(_brash(mut_ship, 0.20) - base_r) / base_r
    assert shift > 0.05
    assert rep["pass"] is False, f"船宽 +30% 使阻力变 {shift:.1%}，判据仍 PASS"


def _packing(c):
    return (c / ch.C_MAX_PACKING) ** ch.C_PACKING_EXP


def test_absolute_resistance_is_density_times_packing(ship):
    """绝对阻力 ∝ ρ_b(c)·P(c)：判据 (b) 只能看见 ρ_b，P(c) 在比值里被约掉。"""
    r = {c: _brash(ship, c) for c in (0.20, 0.40, 0.60)}
    for c in (0.20, 0.60):
        expected = (ch.brash_density(c) * _packing(c)) / (ch.brash_density(0.40) * _packing(0.40))
        assert abs(r[c] / r[0.40] - expected) < 1e-12


# ---------------------------------------------------------------------------
# C. 判据 (e)：反向控制必须有判别力（硬断言）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def route_ctx():
    from services.polar.ice_field import IceField
    from services.polar.optimizer import (
        DEFAULT_DEADLINE_H,
        DEFAULT_ROUTE_END_KM,
        DEFAULT_ROUTE_SHIP,
        DEFAULT_ROUTE_START_KM,
        _baseline_family,
        best_experience_route,
    )

    field = IceField.generate_scenario()
    rship = get_ship(DEFAULT_ROUTE_SHIP)
    baselines = _baseline_family(
        field, rship, DEFAULT_ROUTE_START_KM, DEFAULT_ROUTE_END_KM,
        deadline_h=DEFAULT_DEADLINE_H,
    )
    best = best_experience_route(baselines)
    nominal = opt.verify_route_improvement()
    return {"field": field, "ship": rship, "best": best, "nominal": nominal}


def _run_with_stub(route_ctx, fuel_scale):
    best = route_ctx["best"]
    plan = {
        "fuel_kg": best["fuel_t"] * 1000.0 * fuel_scale,
        "time_h": best["time_h"],
        "distance_km": best["distance_km"],
        "mean_h_m": 0.0,
        "lambda_kg_per_h": 0.0,
        "speed_by_ice": [],
        "points_km": [],
    }
    orig = opt.plan_voyage
    try:
        opt.plan_voyage = lambda *a, **k: plan
        return opt.verify_route_improvement(field=route_ctx["field"], ship=route_ctx["ship"])
    finally:
        opt.plan_voyage = orig


def test_criterion_e_passes_at_nominal(route_ctx):
    nom = route_ctx["nominal"]
    assert nom["criterion"] == "e"
    assert nom["pass"] is True
    assert nom["saving_vs_best_experience_pct"] > nom["threshold_pct"]
    # 基线族必须覆盖多样策略，否则「最佳经验航线」可能是稻草人
    assert len(nom["baselines"]) >= 10
    # 等 ETA 前提的容差登记（优化解允许轻微越界，量级 ≤0.01 h）
    assert abs(nom["optimized"]["time_h"] - nom["deadline_h"]) < 0.05


def test_criterion_e_rejects_optimizer_replaced_by_baseline(route_ctx):
    rep = _run_with_stub(route_ctx, 1.0)
    assert rep["saving_vs_best_experience_pct"] == pytest.approx(0.0, abs=1e-9)
    assert rep["pass"] is False


def test_criterion_e_rejects_sub_threshold_saving(route_ctx):
    rep = _run_with_stub(route_ctx, 0.97)  # 只省 3% < 5% 门限
    assert rep["saving_vs_best_experience_pct"] == pytest.approx(3.0, abs=1e-6)
    assert rep["pass"] is False


def test_criterion_e_boundary_is_strict(route_ctx):
    """正好 5.0% 必须判红（判据语义 saving > 5.0，严格不等号）。"""
    rep = _run_with_stub(route_ctx, 0.95)
    assert rep["saving_vs_best_experience_pct"] == pytest.approx(5.0, abs=1e-6)
    assert rep["pass"] is False


def test_discrimination_artifact_is_reproducible():
    """工件必须存在且与本次判据口径一致（防止报告与代码漂移）。"""
    import json

    art = ROOT / "docs/verification_debt/polar_acceptance/artifacts/discrimination.json"
    assert art.exists(), "先跑 docs/verification_debt/polar_acceptance/discriminate.py"
    d = json.loads(art.read_text())
    assert d["A_analytic_law"]["max_abs_diff"] < 1e-12
    assert d["B_mutation_battery"]["survival_rate"] == 1.0
    assert d["C_route_controls"]["negative_controls_all_fail"] is True