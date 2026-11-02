"""优化器目标函数复核（m1601）判据的回归/失效测试。

每个判据至少一个"变异体判负"用例：注入物理上不可能的实现，判据必须翻成 FAIL，
证明判据不是恒真的装饰物。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit import optimizer_objective_audit as audit_mod  # noqa: E402

ART = ROOT / "artifacts" / "optimizer_objective_audit" / "optimizer_objective_audit.json"


@pytest.fixture(scope="module")
def result():
    return audit_mod.audit()


def _by(res, cid):
    for c in res["checks"]:
        if c["id"] == cid:
            return c
    raise KeyError(cid)


def test_verdict_is_mock(result):
    """核心结论：优化器 Ct 目标函数证据等级 = MOCK。"""
    assert result["verdict_evidence_level"] == "MOCK"
    assert set(result["failed"]) == {"C3", "C4", "C5", "C6"}


def test_geometry_identity_passes(result):
    """KCS 几何先过 Cb/L/B：L/B、B/T、Cb 都在 Holtrop 标定域，∇=Cb·L·B·T。"""
    c1 = _by(result, "C1")
    assert c1["pass"] is True
    v = c1["values"]
    assert 3.0 <= v["L_B"] <= 8.0 and 2.0 <= v["B_T"] <= 4.0 and 0.55 <= v["Cb"] <= 0.75
    g = result["inputs"]["geom"]
    assert v["nabla_m3"] == pytest.approx(g["Cb"] * g["L"] * g["B"] * g["T"], rel=1e-12)


def test_ca_dimensional_inconsistency(result):
    """Ca 以 [m²] 量加到 [N] 上：隐含系数比无量纲值低 ~2 个数量级。"""
    c3 = _by(result, "C3")
    assert c3["pass"] is False
    v = c3["values"]
    assert v["implied_coefficient"] < 1e-5 < v["Ca_dimensionless"]
    assert v["rel_err_pct_of_coefficient"] > 99.0
    assert v["Ra_correct_N"] > 10 * v["Ca_force_recorded_N"]


def test_ct_is_bare_friction_line(result):
    """mock 的 Ct 与 ITTC-57 Cf 相对差 <0.1%：残余+兴波被指数项抹平。"""
    mc = result["inputs"]["mock_components"]
    assert mc["Ct"] == pytest.approx(mc["Cf"], rel=1e-3)
    assert mc["Rw_N"] / mc["Rt_N"] < 1e-12
    c4 = _by(result, "C4")
    assert c4["pass"] is False and c4["values"]["Cr_Cf_mock"] < 1e-3


def test_ct_error_vs_canonical_exceeds_gate(result):
    """总阻力 vs 正典 Holtrop-1984 误差 >15%（平台不可接受门），且不满足 <3%。"""
    c5 = _by(result, "C5")
    assert c5["pass"] is False
    assert c5["values"]["err_pct_Ct"] > 15.0
    assert c5["values"]["err_pct_Rt"] > 15.0
    assert c5["values"]["under_3pct"] is False


def test_physical_lower_bound_violated(result):
    """Ct 未达到 (1+k)Cf 粘性下界 → 形阻被系统性漏算。"""
    c6 = _by(result, "C6")
    assert c6["pass"] is False
    assert c6["values"]["Ct_mock"] < c6["values"]["Cf_times_1pk"]


def test_speed_limits_hold(result):
    """速度极限/单调性这条对 mock 是过的：证明判据不是全盘判负。"""
    assert _by(result, "C7")["pass"] is True


def test_drift_gate_matches_artifact():
    """漂移闸：复算结果必须等于入库 JSON（否则入库产物被手改或不新鲜）。"""
    if not ART.exists():
        pytest.skip("入库 JSON 缺失")
    assert audit_mod.main(["--check"]) == 0


def _patched(fn):
    """把 optimizer 目标函数替换为变异体后取一条判据。"""
    with mock.patch("optimizer.interfaces.resistance_eval.holtrop_resistance", fn):
        return audit_mod.audit()


def test_mutation_inflated_wetted_surface_fails_geometry(result):
    """变异体：湿面积放大 4 倍 → C2 必须 FAIL（夹逼/异实现双保险）。"""
    orig = audit_mod._mock_path()["raw"]

    def bad(geom, V):
        r = dict(orig)
        r["S_wet"] = orig["S_wet"] * 4.0
        return r

    res = _patched(bad)
    assert _by(res, "C2")["pass"] is False


def test_mutation_velocity_independent_ct_fails_monotonicity(result):
    """变异体：Ct 与 V 无关 → C7（Ct(V) 单调下降）必须 FAIL。"""
    orig = audit_mod._mock_path()["raw"]

    def bad(geom, V):
        r = dict(orig)
        r["Ct"] = 0.004
        r["Rt_N"] = 0.5 * 1025.0 * V ** 2 * orig["S_wet"] * 0.004
        return r

    res = _patched(bad)
    assert _by(res, "C7")["pass"] is False


def test_mutation_correct_physics_flips_verdict(result):
    """变异体（正向）：把 Ct/Rt 换成正典值 → C5/C6 必须翻 PASS，证明判据可升不可降。"""
    can = result["inputs"]["canonical"]
    assert can["available"], "正典内核不可用则本用例无意义"

    def good(geom, V):
        mod = audit_mod._load_canonical()["module"]
        hg = mod.HullGeometry(name="kcs", l=geom["L"], b=geom["B"], t=geom["T"],
                              cb=geom["Cb"], cm=mod.estimate_cm(geom["Cb"]),
                              cwp=mod.estimate_cwp(geom["Cb"]))
        r = mod.holtrop1984_resistance(hg, V, rho=1025.0, nu=1.19e-6)
        return {"V_ms": V, "Fn": r.fn, "Re": r.re, "S_wet": r.s_ref, "Rf_N": r.rf,
                "Rr_N": r.rb + r.rtr, "Rw_N": r.rw, "Ca_N": r.ra, "Rt_N": r.rt,
                "Ct": r.ct, "Cf": r.cf, "Cr": r.cw}

    res = _patched(good)
    assert _by(res, "C5")["pass"] is True
    assert _by(res, "C6")["pass"] is True


def test_objective_stamps_evidence_level():
    """目标函数输出必须自带证据等级，下游不得把 MOCK 当 REAL 消费。"""
    from optimizer.interfaces.kcs_bow_param import KCS_BASELINE, generate_geometry
    from optimizer.interfaces.resistance_eval import evaluate_resistance

    import numpy as np

    out = evaluate_resistance(generate_geometry(np.zeros(8)), KCS_BASELINE["Fn_design"])
    assert out["evidence_level"] == "MOCK"
    assert out["physics_source"]
    assert "MOCK" in out["physics_source"] or "mock" in out["physics_source"]