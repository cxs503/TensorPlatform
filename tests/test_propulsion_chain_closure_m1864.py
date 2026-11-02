"""m1864 · 推进功率链物理闭合闸门（hermetic，读 artifacts，不打网络）.

审计脚本: docs/verification_debt/propulsion_chain_closure/audit_propulsion_chain.py
判定依据: Oosterveld & van Oossanen (1975) B 系列回归（树内 tools/verification/
          propulsion/b_series_reference.py）+ 效率/功率恒等式（无单位、无自由参数）。

本文件做三件事：
1. **复算**（G1/G2）：不手抄任何数字，从 artifacts JSON 重算恒等式与 B 系列上界，
   容差 1e-9——保证报告里的每个比值都能被机器重建。
2. **正控**（G3）：构造一个物理自洽的合成算例（T=R_T/(1-t)、K_T/K_Q 取 B 系列
   J=0.5 点、P_E=η_D·P_D），要求 judge() 六条判据全 PASS——证明闸门不是永假。
3. **锁缺陷**（G4）：线上 :8001 的实测违例用 ``xfail(strict=True)`` 记录。修好之后
   这些用例会"意外通过"，pytest 立刻红，强制回来更新 FINDINGS.md（不留静默翻案）。
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.verification.propulsion.b_series_reference import kt_kq  # noqa: E402
from docs.verification_debt.propulsion_chain_closure.audit_propulsion_chain import (  # noqa: E402
    judge, b_series_envelope, RHO,
)

REC_PATH = os.path.join(ROOT, "artifacts", "m1864_propulsion_chain", "results.json")


@pytest.fixture(scope="module")
def rec():
    if not os.path.exists(REC_PATH):
        pytest.skip(f"audit artifacts missing: {REC_PATH}")
    with open(REC_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# --- G1: 恒等式复算（零手抄） ------------------------------------------------
def test_g1_power_closure_ratio_recomputed(rec):
    for row in rec["rows"]:
        measured = row["P_E_kW"] / (row["eta_D"] * row["P_D_kW"])
        recorded = row["checks"]["D4_power_chain_closure"]["value"]
        assert measured == pytest.approx(recorded, rel=1e-9), row["case"]["name"]
        assert row["eta_D"] == pytest.approx(row["propulsive_eff"] if "propulsive_eff" in row else row["eta_D"], rel=0)


def test_g1_efficiency_identity_from_coefficients(rec):
    """η_o 必须等于 J·K_T/(2π·K_Q)；由反解 K_T、K_Q 与端点自报 η_o 反推 J，检查 J>0 且有限。"""
    for row in rec["rows"]:
        hyp = row["unit_hypotheses"][row["main_unit_hypothesis"]]
        kt, kq, eta = hyp["Kt_implied"], hyp["Kq_implied"], row["eta_o"]
        j_implied = eta * 2.0 * math.pi * kq / kt
        assert 0.0 < j_implied < 1.5, row["case"]["name"]
        # 端点响应按 3~4 位小数返回，恒等式只能按浮点打印精度校验
        assert row["eta_D"] == pytest.approx(row["eta_o"] * row["eta_H"], rel=1e-3)


# --- G2: 参考侧（B 系列）自洽 ------------------------------------------------
def test_g2_b_series_envelope_reproduced_and_sane(rec):
    for row in rec["rows"]:
        c = row["case"]
        env = b_series_envelope(c["P_over_D"], c["AeAo"], int(c["Z"]))
        for key in ("Kt_max", "Kq_max", "eta_o_max"):
            assert env[key] == pytest.approx(row["b_series_envelope"][key], rel=1e-9)
        # 参考侧工程上界：任何真实螺旋桨开路效率都应 < 0.75
        assert env["eta_o_max"] < 0.75
        assert 0.25 < env["Kt_max"] < 0.60
        # B 系列回归本身在 J=0 处应给出该几何的 K_T(0) > 0
        kt0, _ = kt_kq(0.0, c["P_over_D"], c["AeAo"], int(c["Z"]))
        assert kt0 > 0.1


def test_g2_implied_coefficients_exceed_b_series_max(rec):
    """记录在案的缺陷：反解 K_T 超 B 系列上界，倍数与 JSON 一致（零手抄）。"""
    for row in rec["rows"]:
        hyp = row["unit_hypotheses"][row["main_unit_hypothesis"]]
        env = row["b_series_envelope"]
        assert hyp["Kt_over_Kt_max"] == pytest.approx(hyp["Kt_implied"] / env["Kt_max"], rel=1e-9)
        assert hyp["Kq_over_Kq_max"] == pytest.approx(hyp["Kq_implied"] / env["Kq_max"], rel=1e-9)


# --- G3: 正控（闸门可 PASS，非永假） -----------------------------------------
def test_g3_positive_control_physically_consistent_case_passes():
    """由 B 系列点 (J=0.5) 反推转速/航速/推力，构造一组物理自洽读数，闸门必须 6/6 PASS。"""
    base = dict(name="synthetic_consistent", L=320.0, B=58.0, T=20.8, nabla=312622.0,
                V_kn=15.5, Cb=0.8098, D=9.86, P_over_D=0.862, rpm=78.0, Z=5, AeAo=0.431)
    j_op, t_frac, w_frac, eta_H = 0.50, 0.22, 0.30, 1.10
    kt, kq = kt_kq(j_op, base["P_over_D"], base["AeAo"], base["Z"])
    D = base["D"]
    R_T_kN = 5940.0
    T_req_N = R_T_kN * 1e3 / (1.0 - t_frac)
    n_req = math.sqrt(T_req_N / (kt * RHO * D ** 4))          # 由推力闭合定转速
    V_req = j_op * n_req * D / (1.0 - w_frac)                  # 由 J 定航速
    case = dict(base, rpm=n_req * 60.0, V_kn=V_req * 3600.0 / 1852.0)
    Q_Nm = kq * RHO * n_req ** 2 * D ** 5
    eta_o = j_op * kt / (2.0 * math.pi * kq)
    eta_D = eta_o * eta_H
    P_D_kW = 2.0 * math.pi * n_req * Q_Nm / 1e3
    res = dict(rt=R_T_kN, pe=eta_D * P_D_kW)
    prop = dict(thrust=T_req_N / 1e3, torque=Q_Nm / 1e3, delivered_power=P_D_kW,
                open_water_eff=eta_o, hull_eff=eta_H, propulsive_eff=eta_D)
    out = judge(case, res, prop)
    fails = {k: v for k, v in out["checks"].items() if not v["ok"]}
    assert not fails, fails
    assert out["n_fail"] == 0
    assert 0.4 < eta_o < 0.75 and 0.5 < eta_D < 0.85      # 与实船量级一致


# --- G4: 线上违例（缺陷锁定；修好后本用例会转红，强制更新 FINDINGS） --------
@pytest.mark.xfail(strict=True, reason="m1864 已实测：线上推进端点 η_o>1（物理不可能），修复后应转 PASS")
def test_g4_live_open_water_efficiency_below_unity(rec):
    bad = [r["case"]["name"] for r in rec["rows"] if not r["checks"]["D1_eta_o_le_1"]["ok"]]
    assert not bad, bad


@pytest.mark.xfail(strict=True, reason="m1864 已实测：线上 η_D·P_D 与阻力端点 P_E 不闭合，修复后应转 PASS")
def test_g4_live_power_chain_closes(rec):
    bad = [r["case"]["name"] for r in rec["rows"] if not r["checks"]["D4_power_chain_closure"]["ok"]]
    assert not bad, bad


@pytest.mark.xfail(strict=True, reason="m1864 已实测：反解 K_T 超 B 系列上界，修复后应转 PASS")
def test_g4_live_thrust_matches_bseries_envelope(rec):
    bad = [r["case"]["name"] for r in rec["rows"] if not r["checks"]["D5_H1_kt_within_b_series"]["ok"]]
    assert not bad, bad


@pytest.mark.xfail(strict=True, reason="m1864 已实测：反解 (1-t) 越出 [0.65,1.0] 推力闭合带，修复后应转 PASS")
def test_g4_live_thrust_closure_band(rec):
    bad = [r["case"]["name"] for r in rec["rows"] if not r["checks"]["D6_H1_thrust_closure"]["ok"]]
    assert not bad, bad