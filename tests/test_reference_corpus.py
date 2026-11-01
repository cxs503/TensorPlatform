"""
参考值语料库判据测试 —— 让「阻力精度欠账」变成机器可裁的判据。

覆盖：
  1) 语料完整性：每条 sha256 锚 + 容差自洽；
  2) A 类解析锚：ITTC-1957 摩擦线 / ITTC-1978 相关补贴 与 40 位精度独立求值一致（<0.05%）；
  3) B 类交叉锚：平台引擎 vs 独立第二实现（零代码复用）在 5 条基准船型上 R_T 一致（<0.5%）；
  4) 判据有牙齿：历史实现（legacy_worktree / legacy_deployed）必须被判为不通过——
     若将来有人把参考值改松，本条会先红，防止「基准被调软」；
  5) API 层：/calculations/resistance 的分解式 R_T = R_friction + R_residual 自洽，
     且越界几何（L/B<3 或 >12）显式 400，不许静默外推。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import resistance_holtrop as engine                      # noqa: E402
from tools.validate import holtrop_ref_independent as indep           # noqa: E402
from tools.validate import legacy_resistance as legacy                # noqa: E402
from tools.validate import reference_corpus as corpus_mod             # noqa: E402

CORPUS = corpus_mod.load()
ENTRIES = CORPUS["entries"]
HULL_ENTRIES = [e for e in ENTRIES if e["class"] == "cross_method"]


def _load_calc_module():
    """直接按文件路径加载 calculations.py，避开 api/__init__ 的数据库依赖。"""
    path = os.path.join(ROOT, "backend", "api", "calculations.py")
    spec = importlib.util.spec_from_file_location("calc_mod_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# 1) 语料完整性
# --------------------------------------------------------------------------
def test_corpus_entries_have_valid_anchor_and_tolerance():
    assert ENTRIES, "corpus must not be empty"
    for e in ENTRIES:
        assert e["sha256"] == corpus_mod._anchor(e), f"{e['case_id']} sha256 mismatch"
        assert 0.0 < e["tolerance_pct"] <= 15.0, f"{e['case_id']} tolerance out of range"
        assert e["class"] in ("analytic", "cross_method")


def test_corpus_declares_withheld_experimental_class():
    """实验基准缺失必须显式声明，不许假装有。"""
    withheld = CORPUS.get("withheld", [])
    assert any(w["class"] == "experimental" for w in withheld)
    assert all(w.get("why") and w.get("needed") for w in withheld)


# --------------------------------------------------------------------------
# 2) 解析锚
# --------------------------------------------------------------------------
@pytest.mark.parametrize("entry", [e for e in ENTRIES if e["class"] == "analytic"],
                         ids=lambda e: e["case_id"])
def test_analytic_anchor(entry):
    ref = entry["reference"]["value"]
    if entry["quantity"] == "ittc57_cf":
        got = engine.ittc57_cf(entry["inputs"]["re"])
    else:
        got = engine.correlation_allowance_ca(entry["inputs"]["L"])
    err = abs(got - ref) / abs(ref) * 100.0
    assert err <= entry["tolerance_pct"], f"{entry['case_id']}: err={err:.5f}% > {entry['tolerance_pct']}%"


# --------------------------------------------------------------------------
# 3) 交叉锚（双实现）
# --------------------------------------------------------------------------
@pytest.mark.parametrize("entry", HULL_ENTRIES, ids=lambda e: e["case_id"])
def test_engine_matches_independent_implementation(entry):
    i = entry["inputs"]
    eng = engine.compute_resistance(engine.ResistanceInput(
        length=i["L"], beam=i["B"], draft=i["T"], cb=i["cb"],
        speed_ms=i["Vs_ms"], displacement_t=i["disp_t"],
        i_e_deg=i["i_e_deg"], lcb_pct=i["lcb_pct"]))
    idp = indep.rt_holtrop(i["L"], i["B"], i["T"], i["cb"], i["Vs_ms"],
                           disp_t=i["disp_t"], ie=i["i_e_deg"], lcb=i["lcb_pct"])
    ref = entry["reference"]["rt_n"]
    err_eng = abs(eng["rt"] - ref) / ref * 100.0
    err_idp = abs(idp["rt"] - ref) / ref * 100.0
    assert err_eng <= entry["tolerance_pct"], f"engine err {err_eng:.3f}%"
    assert err_idp <= entry["tolerance_pct"], f"independent err {err_idp:.3f}%"
    # 两实现对彼此也必须一致（笔误会在这里暴露）
    assert abs(eng["rt"] - idp["rt"]) / idp["rt"] * 100.0 <= entry["tolerance_pct"]
    # 分解守恒：R_T = R_F(1+k1) + R_W + R_A
    total = eng["rf"] + eng["rr"] + eng["ra"]
    assert abs(total - eng["rt"]) / eng["rt"] < 1e-9


# --------------------------------------------------------------------------
# 4) 有牙齿：历史实现必须被判不通过
# --------------------------------------------------------------------------
@pytest.mark.parametrize("entry", HULL_ENTRIES, ids=lambda e: e["case_id"])
def test_legacy_deployed_fails_the_criterion(entry):
    """部署态 fudge Cr 必须被同一条判据判为不通过（判据的分辨力证明）。"""
    i = entry["inputs"]
    ref = entry["reference"]["rt_n"]
    ld = legacy.legacy_deployed(i["L"], i["B"], i["T"], i["cb"], i["Vs_ms"] / 0.5144)
    err = abs(ld["rt"] - ref) / ref * 100.0
    assert err > 100.0, f"legacy_deployed only {err:.1f}% off on {entry['case_id']}"
    # 且它的剩余阻力系数比参考实现大 3 倍以上（量级错误，而非 5% 级调参）
    eng = engine.compute_resistance(engine.ResistanceInput(
        length=i["L"], beam=i["B"], draft=i["T"], cb=i["cb"], speed_ms=i["Vs_ms"],
        displacement_t=i["disp_t"], i_e_deg=i["i_e_deg"], lcb_pct=i["lcb_pct"]))
    assert ld["cr"] > 3.0 * eng["cr"], f"expected Cr inflation, got {ld['cr']:.5f} vs {eng['cr']:.5f}"


@pytest.mark.parametrize("entry", HULL_ENTRIES, ids=lambda e: e["case_id"])
def test_legacy_worktree_geometry_defect_is_real_even_when_total_cancels(entry):
    """旧工作树实现在湿表面积 +48% 的同时 Cr 小一个量级：总阻力「碰巧」接近。

    这正是「自洽测试通过≠验证通过」的教科书例子——本条把两个分量缺陷钉住，
    防止有人看到总阻力接近就宣布无需修正。
    """
    i = entry["inputs"]
    lw = legacy.legacy_worktree(i["L"], i["B"], i["T"], i["cb"], i["Vs_ms"],
                               rho=1025.0, nu=1.19e-6)
    eng = engine.compute_resistance(engine.ResistanceInput(
        length=i["L"], beam=i["B"], draft=i["T"], cb=i["cb"], speed_ms=i["Vs_ms"],
        displacement_t=i["disp_t"], i_e_deg=i["i_e_deg"], lcb_pct=i["lcb_pct"]))
    s_err = abs(lw["wetted_surface"] - eng["wetted_surface"]) / eng["wetted_surface"]
    cr_ratio = (lw["cr"] + 1e-12) / eng["cr"]
    assert s_err > 0.25, f"S defect {s_err*100:.1f}% on {entry['case_id']}"
    assert cr_ratio < 0.2 or cr_ratio > 5.0, f"Cr ratio {cr_ratio:.3f} unexpected"


def test_deployed_kvlcc2_matches_recorded_live_regression():
    """部署态回归快照（2026-11-01 实测 8001）：KVLCC2@15kn rt=5512.42 kN, cr=0.004744。

    本地 legacy 快照必须复现该数，否则说明我们对「被复核对象」理解错了。
    """
    ld = legacy.legacy_deployed(320.0, 58.0, 20.8, 0.8098, 15.0)
    assert ld["rt"] / 1e3 == pytest.approx(5512.42, rel=2e-3)
    assert ld["cr"] == pytest.approx(0.004744, abs=5e-6)


# --------------------------------------------------------------------------
# 5) API 层
# --------------------------------------------------------------------------
def test_api_resistance_decomposition_and_no_silent_extrapolation():
    mod = _load_calc_module()
    import asyncio
    from fastapi import HTTPException

    req = mod.ResistanceRequest(length=320.0, beam=58.0, draft=20.8, cb=0.8098,
                                speed_ms=15.0 * 0.5144,
                                water_density=1025.0, kinematic_viscosity=1.19e-6)
    out = asyncio.run(mod.calc_resistance(req))
    assert out.total_resistance_n == pytest.approx(
        out.frictional_resistance_n + out.residual_resistance_n, rel=1e-6)
    # KVLCC2 设计点应落在独立方法 ±15% 内（旧的 5512 kN 不在带内）
    assert 1400e3 <= out.total_resistance_n <= 2200e3
    assert out.froude_number == pytest.approx(0.1377, abs=2e-3)

    bad = mod.ResistanceRequest(length=320.0, beam=10.0, draft=20.8, cb=0.8098,
                               speed_ms=7.0)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.calc_resistance(bad))
    assert exc.value.status_code == 400 and "L/B" in exc.value.detail