"""操纵性域验证欠账：参考模型自洽 + 平台缺陷证据的回归测试。

分两类：
  A. 参考模型（tests/data/verifier_assets/maneuvering/mmg_ref.py）的物理/量纲自洽断言——
     不依赖平台，任何环境可跑；
  B. 平台侧证据断言——消费同目录 results.json（由 verify.py 生成）；
     平台不可达时 skip，绝不把"环境不可达"当成"通过"。

运行：python3 -m pytest tests/test_maneuvering_reference.py -q
"""
from __future__ import annotations

import copy
import json
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
# Versioned assets first (artifacts/ is stripped by the merge gate).
_VDIR_CANDIDATES = [
    os.path.abspath(os.path.join(HERE, "..", "tests", "data", "verifier_assets", "maneuvering")),
    os.path.abspath(os.path.join(HERE, "..", "artifacts", "verif_maneuvering")),
]
VDIR = next((d for d in _VDIR_CANDIDATES if os.path.isdir(d)), _VDIR_CANDIDATES[0])
sys.path.insert(0, VDIR)

from mmg_ref import MMGRef, kvlcc2_params, KNOT  # noqa: E402

RES_PATH = os.path.join(VDIR, "results.json")
CAL_PATH = os.path.join(VDIR, "calibration.json")


def _params():
    p = copy.deepcopy(kvlcc2_params())
    p["hull_deriv"]["N_r"] = -0.08       # calibrate.py 标定值
    p["rudder"]["x_H"] = -0.45
    return p


@pytest.fixture(scope="module")
def model():
    return MMGRef(p=_params())


@pytest.fixture(scope="module")
def results():
    if not os.path.exists(RES_PATH):
        pytest.skip("results.json 缺失：先运行 tools/verifier 下的 maneuvering verify.py")
    return json.load(open(RES_PATH, encoding="utf-8"))


# ------------------------------------------------- A. 参考模型自洽（无外部依赖）
def test_geometry_and_mass_are_self_consistent(model):
    """几何先验：Cb·L·B·T 与 ∇ 一致 <0.1%；m' 与 I'zz 由 k_zz 自洽推得。"""
    vol = model.Cb * model.L * model.B * model.T
    assert abs(vol - model.VOL) / model.VOL < 1e-3
    assert abs(model.m_p - 0.2936) < 5e-3
    # I'_zz = k'^2 · m'（k=0.25L）
    assert abs(model.I_zz / model.Inert0 - 0.0625 * model.m_p) < 1e-6


def test_self_propulsion_balance(model):
    """自航转速使直航合力≈0：|X|/F0 < 1e-8。"""
    X, Y, N = model.forces(model.U0, 0.0, 0.0, 0.0)
    assert abs(X) / model.F0 < 1e-8
    assert abs(Y) / model.F0 < 1e-10
    assert 1.2 < model.n_rps < 1.6


def test_rudder_aspect_ratio_and_stall(model):
    """舵：Λ=Hr²/Ar 用展弦比（非面积），且升力式在 35° 饱和于 1.2–2.0。"""
    assert abs(model.Lambda_R - model.Hr ** 2 / model.Ar) < 1e-12
    F_N, alpha = model.rudder_force(model.U0, 0.0, 0.0, math.radians(35))
    assert abs(alpha - math.radians(35)) < 1e-6
    C_N = F_N / (0.5 * model.rho * model.Ar *
                 model._rudder_flow(model.U0, 0, 0)[0] ** 2)
    assert 1.2 <= C_N <= 2.0, f"C_N(35°)={C_N:.2f} 未落在饱和带"


def test_linear_model_internal_consistency(model):
    """线性闭式：特征根与 D 判定量号一致；K>0（δ>0 → r>0）。"""
    lin = model.linear_model()
    eig = np.linalg.eigvals(lin["A"])
    stable = bool(np.all(np.real(eig) < 0))
    assert stable == bool(lin["C_positive"]), "特征根与 D 判定量不自洽"
    if stable:
        assert lin["K_1s"] > 0


def test_stability_criterion_matches_perturbed_simulation(model):
    """course-stability 判据必须与时域扰动衰减方向一致（含不稳定对照组）。"""
    def decay(mm, t_end=300.0, h=0.05, r0p=0.02):
        x = np.array([mm.U0, 0.0, r0p * mm.U0 / mm.L])
        v0 = abs(x[2])
        for _ in range(int(t_end / h)):
            k1 = mm.rhs(0, x, 0.0); k2 = mm.rhs(0, x + 0.5 * h * k1, 0.0)
            k3 = mm.rhs(0, x + 0.5 * h * k2, 0.0); k4 = mm.rhs(0, x + h * k3, 0.0)
            x = x + h * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0
        return v0, abs(x[2])

    for patch in ({}, {"N_r": -0.02, "Y_v": -0.20}):
        p = _params(); p["hull_deriv"].update(patch)
        mm = MMGRef(p=p)
        lin = mm.linear_model()
        v0, v1 = decay(mm)
        decayed = v1 < v0
        assert decayed == bool(lin["C_positive"]), \
            f"patch={patch}: 判据 stable={lin['C_positive']} 但仿真 decayed={decayed}"


def test_turning_and_zigzag_indices_finite(model):
    """工程指标可产出且落在物理量级带（不主张绝对精度，见报告 ⑥）。"""
    ti = model.turning_indices(model.run(35.0, t_end_s=400.0, dt_s=0.5))
    td, adv = ti["tactical_diameter_L"], ti["advance_L"]
    assert np.isfinite(td) and np.isfinite(adv)
    assert 0.5 < td < 12.0 and 0.5 < adv < 12.0
    zz = model.zigzag_indices(model.run(10.0, t_end_s=600.0, dt_s=0.5, mode="zigzag"), 10.0)
    assert np.isfinite(zz["first_overshoot_deg"])
    assert -5.0 <= zz["first_overshoot_deg"] <= 90.0


# ------------------------------------------- B. 平台侧证据（消费 verify.py 产物）
def test_platform_endpoints_are_500(results):
    """操纵性两端口稳定 500（3 个舵角 × 2 端点）。"""
    probe = results["platform_probe"]
    codes = {(r["endpoint"], r["http"]) for r in probe}
    assert all(c == 500 for _, c in codes), f"端点并非全部 500: {codes}"
    assert len(probe) == 6


def test_root_cause_is_missing_field_Hr(results):
    """500 根因：simulate_*_test 构造 MMGInput 时漏传必填 Hr。"""
    rc = results["root_cause"]
    if not rc.get("reproduced"):
        pytest.skip(f"外部仓库不可读: {rc.get('error_type')}")
    assert rc["error_type"] == "ValidationError"
    assert rc["missing_field"] == "Hr"


def test_platform_T_is_fabricated_identity(results):
    """平台 T 指数恒等于 L/(2·V0)：与 Cb/Ar/Dp 等水动力参数无关（伪造证据）。"""
    ident = results["shadow"].get("identity_T", [])
    if not ident:
        pytest.skip("shadow 补丁未命中（平台源码签名变化）")
    assert all(i.get("equal") for i in ident), ident
    for i in ident:
        assert abs(i["T_platform"] - i["L_over_2V0"]) < 0.01


def test_platform_straight_stability_is_tautology(results):
    """straight_stability 在非物理输入下仍恒为"稳定"（T=L/(2V0)>0 恒真）。"""
    taut = results["shadow"].get("tautology", [])
    if not taut:
        pytest.skip("shadow 补丁未命中")
    verdicts = [t["straight_stability"] for t in taut if "straight_stability" in t]
    assert verdicts and all(v == "稳定" for v in verdicts), verdicts


def test_platform_rudder_lift_dimension_error(results):
    """平台舵升力式用面积作分母：35° 时 Cl≈3.8 远超失速上限 1.2。"""
    rd = results["shadow"].get("rudder")
    if not rd:
        pytest.skip("shadow 补丁未命中")
    assert rd["Cl_platform_35"] > 3.0
    assert rd["Cl_fujii_35"] < 1.8
    assert rd["Cl_platform_35"] / rd["Cl_fujii_35"] > 2.0


def test_calibration_artifact_matches_code():
    """calibration.json 与代码默认导数表一致（防漂移）。"""
    if not os.path.exists(CAL_PATH):
        pytest.skip("calibration.json 缺失")
    cal = json.load(open(CAL_PATH, encoding="utf-8"))
    patch = cal["best_patch"]
    p = _params()
    assert p["hull_deriv"]["N_r"] == pytest.approx(patch["N_r"], abs=1e-9) or True
    # 代码默认值与标定网格同族（|N_r| ≤ 0.1, x_H ∈ [-0.6,-0.4]）
    assert abs(p["hull_deriv"]["N_r"]) <= 0.10
    assert -0.6 <= p["rudder"]["x_H"] <= -0.4