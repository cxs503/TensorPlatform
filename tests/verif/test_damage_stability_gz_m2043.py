"""破舱稳性 GZ 验证欠账清偿 —— 机器判据 + 缺陷锁（mission 2043, verifier 席）。

三层证据：
  L1 参考实现自证：解析锚（损失浮力闭式 t'/GM、完整船壁式闭式）vs 断点精确积分；
  L2 平台 vs 参考：从 ``artifacts/verif/damage_stability_gz_m2043/evidence.json``
     复算（数字零手抄，全部从工件读）；
  L3 缺陷锁：三处已修缺陷一旦回归，测试立即失败。
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VERIF = os.path.join(ROOT, "tools", "verif")
sys.path.insert(0, ROOT)
sys.path.insert(0, VERIF)

import ds_exact_reference as REF  # noqa: E402

EV = os.path.join(ROOT, "artifacts", "verif", "damage_stability_gz_m2043", "evidence.json")
VERDICT = os.path.join(ROOT, "artifacts", "verif", "damage_stability_gz_m2043", "verdict.json")


def _module():
    path = os.path.join(ROOT, "backend", "api", "damage_stability.py")
    spec = importlib.util.spec_from_file_location("ds_platform_m2043", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ds_platform_m2043"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ds():
    return _module()


@pytest.fixture(scope="module")
def evidence():
    if not os.path.exists(EV):
        pytest.skip("evidence.json 缺失，先跑 tools/verif/ds_probe_m2043.py")
    with open(EV) as f:
        return json.load(f)


def _barge(kg=5.0):
    return REF.Barge(L=100.0, B=20.0, D=8.0, T=6.0, KG=kg)


def _void(mu=0.95, b=12.0, l=20.0, zb=0.5, zt=7.0, yc=0.0):
    return REF.Void(l=l, b=b, z_b=zb, z_t=zt, y_c=yc, mu=mu)


# ======================= L1：参考实现自证（解析锚 vs 精确积分） =======================

def test_ref_gm_matches_gz_slope_at_zero_heel():
    """GM 闭式 vs 数值 GZ 在 θ→0 的斜率：两条独立路线必须吻合（<1e-6）。"""
    barge, void = _barge(), _void()
    gm = REF.analytic_gm(barge, void)
    h = 1e-4
    slope = (REF.gz(barge, h, void) - REF.gz(barge, -h, void)) / (2 * h * math.pi / 180.0)
    assert slope == pytest.approx(gm, rel=1e-6)


def test_ref_intact_limit_matches_wall_sided_closed_form():
    """μ=0（未进水）时精确积分必须回到壁式闭式 GZ=sinθ(GM+½BM tan²θ)。"""
    barge = _barge()
    assert barge.nabla0 == pytest.approx(barge.L * barge.B * barge.T)   # Cb = 1
    gm0 = barge.T / 2.0 + barge.I_T0 / barge.nabla0 - barge.KG
    bm0 = barge.I_T0 / barge.nabla0
    cap = REF.deck_edge_angle(barge, barge.T)
    for a in (1.0, 3.0, 5.0, cap):
        assert REF.gz(barge, a, None) == pytest.approx(REF.wall_sided_gz(barge, a, gm0, bm0),
                                                       abs=2e-6)


def test_ref_draft_closed_forms_and_validity():
    """闭式 t' 的适用域：未饱和时与精确水线一致；饱和（水线追及舱顶）时必须弃用。"""
    barge, void = _barge(), _void()
    assert REF.closed_form_valid(barge, void) is False       # z_b=0.5>0 ⇒ 闭式不适用
    d_exact = REF.solve_waterline(barge, 0.0, void)
    # 精确解：B·d − α·b·(d−z_b) = B·T，α = μ·l/L
    alpha = void.mu * void.l / barge.L
    d_analytic = (barge.B * barge.T - alpha * void.b * void.z_b) / (barge.B - alpha * void.b)
    assert d_exact == pytest.approx(d_analytic, rel=1e-9)
    d_closed = REF.analytic_draft(barge, void)
    assert d_exact < d_closed                                # 闭式（假定贴龙骨）高报


# ======================= L2：平台 vs 参考（从工件复算，零手抄） =======================

def test_evidence_verdict_is_real_and_complete(evidence):
    with open(VERDICT) as f:
        v = json.load(f)
    assert v["n_criteria"] >= 45
    assert v["n_pass"] == v["n_criteria"], [c for c in v["criteria"] if not c["pass"]]
    assert v["grade"] == "REAL"
    assert len(evidence["cases"]) >= 6


def test_platform_matches_reference_on_all_key_quantities(evidence):
    worst = {"draft": 0.0, "gm": 0.0, "gz": 0.0}
    for row in evidence["cases"]:
        worst["draft"] = max(worst["draft"], row["draft"]["rel_dev"])
        worst["gm"] = max(worst["gm"], row["gm"]["rel_dev"])
        worst["gz"] = max(worst["gz"], row["gz_curve"]["max_abs_delta"])
    assert worst["draft"] < 0.03, worst
    assert worst["gm"] < 0.03, worst
    assert worst["gz"] < 0.01, worst           # m（≈3%·GZmax 量级绝对带）


def test_platform_survival_factor_agrees_with_reference(evidence):
    for row in evidence["cases"]:
        s_code, s_ref = row["s"]["code"], row["s"]["from_ref_gz"]
        if s_ref == 0.0:
            assert s_code == 0.0, row["case"]
        else:
            assert s_code == pytest.approx(s_ref, rel=0.03), row["case"]


def test_required_index_matches_solas_reg6(evidence):
    for r in evidence["rules_R"]:
        assert r["rel_dev"] <= 1e-12


# ======================= L3：缺陷锁（回归即失败） =======================

def test_lock_no_double_free_surface_on_breached_void(ds, evidence):
    """缺陷 DS-02：破损舱自由液面被「截面移除 + GG0 扣减」重复计入。

    破损舱与海连通 ⇒ 损失浮力法已在截面/水线面惯性矩里扣除，GG0 必须为 0。
    """
    calc = ds.DamageStabilityCalculator()
    g = calc._intact_geometry(ds.ShipData(
        L=100.0, B=20.0, T=6.0, D=8.0, Cb=1.0, LCG=0.0, TCG=0.0, VCG=5.0,
        displacement=1.025 * 100 * 20 * 6,
        compartments=[ds.Compartment(name="C", volume=20 * 12 * 6.5, LCG=0.0, TCG=0.0,
                                     VCG=3.75, permeability=0.95, location="mid",
                                     length=20.0, breadth=12.0, height=6.5)]))
    flood = calc._flooding_state(ds.ShipData(
        L=100.0, B=20.0, T=6.0, D=8.0, Cb=1.0, LCG=0.0, TCG=0.0, VCG=5.0,
        displacement=1.025 * 100 * 20 * 6,
        compartments=[ds.Compartment(name="C", volume=20 * 12 * 6.5, LCG=0.0, TCG=0.0,
                                     VCG=3.75, permeability=0.95, location="mid",
                                     length=20.0, breadth=12.0, height=6.5)]),
        ds.DamageScenario(damaged_compartments=["C"]))
    assert flood["i_x_free"] > 0
    assert calc._free_surface_correction(flood, g) == 0.0
    for row in evidence["cases"]:
        assert row["gg0_deducted_by_code"] == 0.0, row["case"]


def test_lock_draft_solved_not_closed_form_when_void_topped_out(ds):
    """缺陷 DS-03：闭式 t'=∇0/(Aw−ΣμA) 在舱底 z_b>0 / 舱顶被追上时高报终吃水。"""
    comp = ds.Compartment(name="C", volume=40 * 12 * 6.5, LCG=0.0, TCG=0.0, VCG=3.75,
                          permeability=0.95, location="mid",
                          length=40.0, breadth=12.0, height=6.5)
    ship = ds.ShipData(L=100.0, B=20.0, T=6.0, D=8.0, Cb=1.0, LCG=0.0, TCG=0.0, VCG=5.0,
                       displacement=1.025 * 100 * 20 * 6, compartments=[comp])
    out = ds.DamageStabilityCalculator().analyze(
        ds.DamageStabilityInput(ship=ship, damage=ds.DamageScenario(
            damaged_compartments=["C"])))
    closed = 100 * 20 * 6 / (100 * 20 - 0.95 * 40 * 12)
    barge = REF.Barge(L=100.0, B=20.0, D=8.0, T=6.0, KG=5.0)
    exact = REF.solve_waterline(barge, 0.0, _void(mu=0.95, b=12.0, l=40.0))
    assert out.final_draft == pytest.approx(exact, abs=0.02)   # 截面 241 节点梯形积分离散误差
    assert abs(closed - exact) / exact > 0.03          # 闭式解在此几何下确实高报 >3%


def test_lock_probabilistic_index_rejects_unphysical_inputs(ds):
    """缺陷 DS-04：p 静默默认 0.1、p∉[0,1]、Σp>1 ⇒ A 可 >1（非物理）。"""
    comp = ds.Compartment(name="C", volume=20 * 12 * 6.5, LCG=0.0, TCG=0.0, VCG=3.75,
                          permeability=0.95, location="mid",
                          length=20.0, breadth=12.0, height=6.5)
    ship = ds.ShipData(L=100.0, B=20.0, T=6.0, D=8.0, Cb=1.0, LCG=0.0, TCG=0.0, VCG=5.0,
                       displacement=1.025 * 100 * 20 * 6, compartments=[comp])
    pds = ds.ProbabilisticDamageStability()
    with pytest.raises(Exception):
        pds.calculate_A(ds.ProbabilisticInput(ship=ship, damage_zones=[
            {"name": "Z1", "damaged_compartments": ["C"]}]))          # 缺 p
    with pytest.raises(Exception):
        pds.calculate_A(ds.ProbabilisticInput(ship=ship, damage_zones=[
            {"name": "Z1", "damaged_compartments": ["C"], "probability": 1.7}]))
    with pytest.raises(Exception):
        pds.calculate_A(ds.ProbabilisticInput(ship=ship, damage_zones=[
            {"name": f"Z{i}", "damaged_compartments": ["C"], "probability": 0.2}
            for i in range(8)]))                                     # Σp = 1.6 > 1


def test_lock_figures_and_artifacts_exist():
    base = os.path.join(ROOT, "artifacts", "verif", "damage_stability_gz_m2043")
    for fn in ("evidence.json", "evidence_before.json", "verdict.json",
               "verdict_before.json", "fig_gz_vs_reference.png", "fig_gm_and_s.png"):
        path = os.path.join(base, fn)
        assert os.path.exists(path) and os.path.getsize(path) > 0, fn