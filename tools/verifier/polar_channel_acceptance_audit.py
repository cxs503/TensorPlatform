"""m1862 — /polar/acceptance/channel（W3-N11 判据 b）独立复核审计。

被测对象（船舶平台仓，只读调用）::

    backend/services/polar/channel.py
      channel_resistance()           生产模型（brash 阻力）
      channel_resistance_reference() Riska/VTT 型参考式
      calibrate_k()                  单点标定常数 K
      verify_channel_concentrations() 判据 (b) 自证闸门

判据 (b) 的原声明：在锚定浓度 c=0.40 上标定唯一常数 K，然后在 c=0.20 / 0.70
两个 hold-out 浓度上比对生产式与参考式，|rel_err_total| < 10% 即通过。

审计问题（本脚本要机器裁决的 5 件事）：

A1 闭式恒等式：hold-out 误差是否**完全**由混合密度比决定？
   prod_brash / ref_brash = [rho_b(c) g h_b B T Cb (mu + sin beta) (c/c_max)^n f_v]
                          / [K rho_i g h_b^2 B          (c/c_max)^n f_v]
   标定后 K = rho_b(c0) T Cb (mu + sin beta) / (rho_i h_b)，故
       rel_err_brash(c) = rho_b(c) / rho_b(c0) - 1     （精确，与 n、f_v、h_b 无关）
   → 判据 (b) 的 hold-out 残差是**密度比恒等式**，判据 |rel| < 1e-12。

A2 判别力真空：把生产式的浓度指数 n 改成 1.0 / 2.5、删掉 T·Cb、删掉动速度因子
   f_v —— 判据 (b) 是否仍然通过？若全通过，说明该闸门对 brash 阻力物理零判别力。

A3 厚度维度不一致：生产式 brash 项 ∝ h_b^1，参考式 ∝ h_b^2（差一个长度量纲
   h_b）。因此在厚度 h_c 上标定的 K 用到评测厚度 h_e 上，必然引入精确因子
   h_c / h_e。判据 (b) 固定 h_b=0.8 m，所以这个因子被隐藏；本项做**厚度 hold-out**
   （在 h_c 标定、在 0.8 m 评测）量化误差。

A4 物理极限（可解析判据，非文献拟合）：
   (i) c -> 0 时 channel 总阻力 = 开阔水阻力（精确，非近似）；
   (ii) h_b -> 0 同上；(iii) 总阻力对 c、h_b、V 单调不减；(iv) 混合密度极限
   rho_b(0)=rho_w、rho_b(1)=rho_i。

A5 若判据 (a)-(e) 中 (b) 不可信，则 /polar/acceptance 的 pass 结论不能作为
   能力证据等级来源 —— 记录到证据等级建议（WITHHELD 保持）。

产出：artifacts/polar_channel_acceptance/audit.json（机器可读，无手抄数字）。
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for _p in (os.path.join(REPO, "backend"), REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.polar import channel as CH  # noqa: E402
from services.polar.constants import G, KNOT, RHO_ICE, RHO_WATER  # noqa: E402
from services.polar.ships import get_ship  # noqa: E402

V_KN = 6.0
V_MS = V_KN * KNOT
H_EVAL = 0.80                      # 判据 (b) 的固定评测厚度
H_GRID = [0.20, 0.40, 0.60, 0.80, 1.00, 1.20]
C_GRID = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70]
SHIP_KEY = "handysize_bulker"
TOL_PCT = 10.0                     # 判据 (b) 的原阈值


# ---------------------------------------------------------------------------
# 独立复算（不使用被测模块的任何中间量，只用公开输入 + 常物性）
# ---------------------------------------------------------------------------

def rho_b_model(c: float) -> float:
    """混合密度，独立写一遍（生产式与参考式的唯一物理差异项）。"""
    return c * RHO_ICE + (1.0 - c) * RHO_WATER


def brash_closed_form(ship, v_ms: float, h_b: float, c: float) -> float:
    """生产式 brash 项的独立闭式（按 docstring 的方程自己写，不 import 实现）。"""
    beta = math.atan(2.0 * ship.draft_m / ship.length_m)
    packing = (c / CH.C_MAX_PACKING) ** CH.C_PACKING_EXP
    dyn = 1.0 + CH.C_VEL * v_ms / math.sqrt(G * h_b)
    return (rho_b_model(c) * G * h_b * ship.beam_m * ship.draft_m * ship.cb
            * (CH.MU_BRASH + math.sin(beta)) * packing * dyn)


def ref_brash_closed_form(ship, v_ms: float, h_b: float, c: float, k_cal: float) -> float:
    """参考式 brash 项的独立闭式。"""
    packing = (c / CH.C_MAX_PACKING) ** CH.C_PACKING_EXP
    dyn = 1.0 + CH.C_VEL * v_ms / math.sqrt(G * h_b)
    return k_cal * RHO_ICE * G * h_b ** 2 * ship.beam_m * packing * dyn


def pct(a: float, b: float) -> float:
    return 100.0 * (a - b) / b


# ---------------------------------------------------------------------------
# A1 hold-out 残差 = 混合密度比恒等式
# ---------------------------------------------------------------------------

def audit_a1(ship) -> Dict[str, Any]:
    k = CH.calibrate_k(ship, V_MS, H_EVAL, CH.ANCHOR_CONCENTRATION)
    rows: List[Dict[str, Any]] = []
    worst = 0.0
    for c in C_GRID:
        prod = brash_closed_form(ship, V_MS, H_EVAL, c)
        ref = ref_brash_closed_form(ship, V_MS, H_EVAL, c, k)
        rel = pct(prod, ref)
        predicted = 100.0 * (rho_b_model(c) / rho_b_model(CH.ANCHOR_CONCENTRATION) - 1.0)
        resid = rel - predicted
        worst = max(worst, abs(resid))
        rows.append({
            "concentration": c,
            "brash_prod_n": round(prod, 3),
            "brash_ref_n": round(ref, 3),
            "rel_err_pct": round(rel, 10),
            "density_ratio_pred_pct": round(predicted, 10),
            "identity_residual_pct": round(resid, 12),
        })
    return {
        "claim": "rel_err_brash(c) == rho_b(c)/rho_b(c0) - 1 (exact, independent of n, f_v, h_b)",
        "k_cal": k,
        "rows": rows,
        "max_abs_identity_residual_pct": worst,
        "pass": worst < 1e-9,
        "interpretation": "hold-out 残差被密度比精确解释 -> 该闸门并不检验 brash 阻力物理",
    }


# ---------------------------------------------------------------------------
# A2 判别力矩阵：逐项扰动 brash 阻力物理结构，看判据 (b) 抓不抓得住
#    每个扰动都在锚点浓度重新标定 K（否则测的是标定误差，不是判别力）。
# ---------------------------------------------------------------------------

def _variant_brash(ship, v_ms: float, h_b: float, c: float, variant: str) -> float:
    """按 docstring 方程自己写一遍，逐项施加扰动。"""
    beta = math.atan(2.0 * ship.draft_m / ship.length_m)
    n = 1.5
    n_exp_h = 1          # 生产式厚度指数
    n_exp_b = 1          # 生产式宽度指数
    use_t_cb = True
    use_fv = True
    density = rho_b_model(c)
    if variant == "ref":
        pass
    elif variant == "conc_exp_1.0":
        n = 1.0
    elif variant == "conc_exp_2.5":
        n = 2.5
    elif variant == "thickness_exp_2":
        n_exp_h = 2
    elif variant == "breadth_exp_2":
        n_exp_b = 2
    elif variant == "drop_T_Cb":
        use_t_cb = False
    elif variant == "drop_fv":
        use_fv = False
    elif variant == "density_constant_rho_ice":
        density = RHO_ICE
    else:
        raise ValueError(variant)
    packing = (c / CH.C_MAX_PACKING) ** n
    dyn = 1.0 + CH.C_VEL * v_ms / math.sqrt(G * h_b) if use_fv else 1.0
    r = (density * G * h_b ** n_exp_h * ship.beam_m ** n_exp_b
         * (CH.MU_BRASH + math.sin(beta)) * packing * dyn)
    if use_t_cb:
        r *= ship.draft_m * ship.cb
    return r


VARIANTS = {
    "ref": "生产式原样（对照）",
    "conc_exp_1.0": "浓度指数 n: 1.5 -> 1.0",
    "conc_exp_2.5": "浓度指数 n: 1.5 -> 2.5",
    "thickness_exp_2": "厚度指数 h^1 -> h^2（与参考式一致）",
    "breadth_exp_2": "宽度指数 B^1 -> B^2",
    "drop_T_Cb": "删掉 T*Cb 项",
    "drop_fv": "删掉速度因子 f_v",
    "density_constant_rho_ice": "混合密度 rho_b(c) -> 常数 rho_i（更不物理）",
}


def audit_a2(ship) -> Dict[str, Any]:
    out: List[Dict[str, Any]] = []
    for variant, desc in VARIANTS.items():
        k = _variant_brash(ship, V_MS, H_EVAL, CH.ANCHOR_CONCENTRATION, variant) / \
            ref_brash_closed_form(ship, V_MS, H_EVAL, CH.ANCHOR_CONCENTRATION, 1.0)
        rows = []
        for c in (CH.ANCHOR_CONCENTRATION, *CH.HOLDOUT_CONCENTRATIONS):
            prod_b = _variant_brash(ship, V_MS, H_EVAL, c, variant)
            ref_b = ref_brash_closed_form(ship, V_MS, H_EVAL, c, k)
            ow = CH.channel_resistance(ship, V_MS, H_EVAL, c)["open_water_n"]
            rows.append({
                "concentration": c,
                "rel_err_brash_pct": round(pct(prod_b, ref_b), 6),
                "rel_err_total_pct": round(pct(prod_b + ow, ref_b + ow), 6),
            })
        max_tot = max(abs(r["rel_err_total_pct"]) for r in rows)
        out.append({
            "variant": variant,
            "description": desc,
            "rows": rows,
            "max_abs_rel_err_total_pct": max_tot,
            "criterion_b_pass": max_tot < TOL_PCT,
        })
    caught = [o for o in out if not o["criterion_b_pass"]]
    return {
        "claim": "判据 (b) 只对与参考式结构不一致的项敏感；被标定常数吸收的项一律放行",
        "threshold_pct": TOL_PCT,
        "variants": out,
        "n_variants": len(out),
        "n_caught": len(caught),
        "caught": [o["variant"] for o in caught],
        "passed_through": [o["variant"] for o in out if o["criterion_b_pass"]],
        "discriminating_power": f"{len(caught)}/{len(out)}",
        "zero_power": len(caught) == 0,
    }


# ---------------------------------------------------------------------------
# A5 建设性修法：把生产式流量纲与参考式对齐（h_b^2 * B），重做厚度 hold-out
# ---------------------------------------------------------------------------

def audit_a5(ship) -> Dict[str, Any]:
    def prod_b_consistent(v_ms, h_b, c):
        beta = math.atan(2.0 * ship.draft_m / ship.length_m)
        packing = (c / CH.C_MAX_PACKING) ** CH.C_PACKING_EXP
        dyn = 1.0 + CH.C_VEL * v_ms / math.sqrt(G * h_b)
        return (rho_b_model(c) * G * h_b ** 2 * ship.beam_m
                * (CH.MU_BRASH + math.sin(beta)) * packing * dyn)

    rows = []
    for h_c in H_GRID:
        k = prod_b_consistent(V_MS, h_c, CH.ANCHOR_CONCENTRATION) / \
            ref_brash_closed_form(ship, V_MS, h_c, CH.ANCHOR_CONCENTRATION, 1.0)
        prod = prod_b_consistent(V_MS, H_EVAL, CH.ANCHOR_CONCENTRATION)
        ref = ref_brash_closed_form(ship, V_MS, H_EVAL, CH.ANCHOR_CONCENTRATION, k)
        rows.append({
            "calibration_thickness_m": h_c,
            "rel_err_brash_pct": round(pct(prod, ref), 9),
        })
    worst = max(abs(r["rel_err_brash_pct"]) for r in rows)
    # 修法对阻力量级的影响：h^2*B / (h*B*T*Cb) = h/(T*Cb)
    ratio = H_EVAL / (ship.draft_m * ship.cb)
    return {
        "claim": "把生产式 brash 项改成 rho_b g h_b^2 B (mu+sin beta) (c/c_max)^n f_v 后，"
                 "厚度 hold-out 不再失效（标定常数与厚度无关）",
        "rows": rows,
        "max_abs_rel_err_brash_pct": worst,
        "thickness_holdout_pass": worst < 5.0,
        "magnitude_change_at_h_eval": round(ratio, 6),
        "magnitude_note": "同 h_b/T/Cb 下新式 = 旧式 * h_b/(T*Cb)，需重标定 K 并复核绝对量级",
    }


# ---------------------------------------------------------------------------
# A3 厚度 hold-out：在 h_c 标定、在 h_e 评测 -> 精确因子 h_c/h_e
# ---------------------------------------------------------------------------

def audit_a3(ship) -> Dict[str, Any]:
    rows = []
    worst = 0.0
    for h_c in H_GRID:
        k = CH.calibrate_k(ship, V_MS, h_c, CH.ANCHOR_CONCENTRATION)
        prod = brash_closed_form(ship, V_MS, H_EVAL, CH.ANCHOR_CONCENTRATION)
        ref = ref_brash_closed_form(ship, V_MS, H_EVAL, CH.ANCHOR_CONCENTRATION, k)
        rel = pct(prod, ref)
        predicted = 100.0 * (h_c / H_EVAL - 1.0)
        # 判据 (b) 口径：总阻力相对误差（含开阔水项）
        ow = CH.channel_resistance(ship, V_MS, H_EVAL, CH.ANCHOR_CONCENTRATION)["open_water_n"]
        rel_total = pct(prod + ow, ref + ow)
        worst = max(worst, abs(rel_total))
        rows.append({
            "calibration_thickness_m": h_c,
            "evaluation_thickness_m": H_EVAL,
            "k_cal": k,
            "rel_err_brash_pct": round(rel, 6),
            "predicted_hc_over_he_pct": round(predicted, 6),
            "rel_err_total_pct": round(rel_total, 6),
        })
    return {
        "claim": "厚度 hold-out 误差 = h_cal/h_eval - 1（维度不一致的直接后果）",
        "k_times_h": [round(r["k_cal"] * r["calibration_thickness_m"], 9) for r in rows],
        "k_times_h_is_constant": max(r["k_cal"] * r["calibration_thickness_m"] for r in rows)
        - min(r["k_cal"] * r["calibration_thickness_m"] for r in rows) < 1e-9,
        "rows": rows,
        "max_abs_rel_err_total_pct": worst,
        "criterion_b_pass_under_thickness_holdout": worst < TOL_PCT,
        "interpretation": "判据 (b) 只在单一标定厚度上成立；厚度一换即失效",
    }


# ---------------------------------------------------------------------------
# A4 物理极限（解析判据）
# ---------------------------------------------------------------------------

def audit_a4(ship) -> Dict[str, Any]:
    ow = CH.channel_resistance(ship, V_MS, H_EVAL, 0.0)["total_n"]
    at_c0 = CH.channel_resistance(ship, V_MS, H_EVAL, 1e-12)["total_n"]
    at_h0 = CH.channel_resistance(ship, V_MS, 0.0, 0.5)["total_n"]
    d_density = {
        "rho_b(0)": rho_b_model(0.0), "rho_w": RHO_WATER,
        "rho_b(1)": rho_b_model(1.0), "rho_i": RHO_ICE,
    }
    monotone_c = all(
        CH.channel_resistance(ship, V_MS, H_EVAL, 0.1 * i)["total_n"]
        <= CH.channel_resistance(ship, V_MS, H_EVAL, 0.1 * (i + 1))["total_n"] + 1e-9
        for i in range(10))
    monotone_h = all(
        CH.channel_resistance(ship, V_MS, 0.1 * i, 0.5)["total_n"]
        <= CH.channel_resistance(ship, V_MS, 0.1 * (i + 1), 0.5)["total_n"] + 1e-9
        for i in range(1, 12))
    monotone_v = all(
        CH.channel_resistance(ship, 0.5 * i + 0.5, H_EVAL, 0.5)["total_n"]
        <= CH.channel_resistance(ship, 0.5 * (i + 1) + 0.5, H_EVAL, 0.5)["total_n"] + 1e-9
        for i in range(10))
    return {
        "channel_at_c0_n": ow,
        "channel_at_c_1e-12_n": at_c0,
        "limit_c_to_zero_rel_err": abs(at_c0 - ow) / ow,
        "channel_at_h0_n": at_h0,
        "limit_h_to_zero_rel_err": abs(at_h0 - ow) / ow,
        "density_limits": d_density,
        "monotone_in_concentration": monotone_c,
        "monotone_in_thickness": monotone_h,
        "monotone_in_speed": monotone_v,
        "pass": (abs(at_c0 - ow) / ow < 1e-9 and abs(at_h0 - ow) / ow < 1e-9
                 and monotone_c and monotone_h and monotone_v),
    }


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def run_audit(write: bool = True) -> Dict[str, Any]:
    ship = get_ship(SHIP_KEY)
    a1 = audit_a1(ship)
    a2 = audit_a2(ship)
    a3 = audit_a3(ship)
    a4 = audit_a4(ship)
    a5 = audit_a5(ship)

    # 判据 (b) 自报值（只作为"被审计对象"，不作基准）
    reported = CH.verify_channel_concentrations(ship, v_kn=V_KN, h_b_m=H_EVAL)

    verdict = {
        "a1_holdout_is_density_identity": a1["pass"],
        "a2_criterion_b_discriminating_power": a2["discriminating_power"],
        "a2_a5_thickness_holdout_becomes_vacuous": a5["thickness_holdout_pass"],
        "a3_criterion_b_is_thickness_pinned": not a3["criterion_b_pass_under_thickness_holdout"],
        "a4_physical_limits_hold": a4["pass"],
        "reported_criterion_b_pass": reported["pass"],
        "reported_max_abs_rel_err_pct": reported["max_abs_rel_error_pct"],
    }
    verdict["evidence_grade_for_/polar/acceptance/channel"] = (
        "WITHHELD" if not a3["criterion_b_pass_under_thickness_holdout"]
        else "ANCHORED"
    )

    out = {
        "mission": "m1862",
        "subject": "backend/services/polar/channel.py :: verify_channel_concentrations (W3-N11 criterion b)",
        "ship": ship.key,
        "settings": {"speed_kn": V_KN, "h_eval_m": H_EVAL, "tol_pct": TOL_PCT,
                     "anchor_concentration": CH.ANCHOR_CONCENTRATION,
                     "holdout_concentrations": list(CH.HOLDOUT_CONCENTRATIONS)},
        "A1_density_identity": a1,
        "A2_vacuity": a2,
        "A3_thickness_holdout": a3,
        "A4_physical_limits": a4,
        "A5_consistent_fix": a5,
        "reported_by_subject": reported,
        "verdict": verdict,
    }
    if write:
        d = os.path.join(REPO, "artifacts", "polar_channel_acceptance")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "audit.json"), "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
    return out


if __name__ == "__main__":
    res = run_audit()
    print(json.dumps(res["verdict"], ensure_ascii=False, indent=2))
    print("A1 residual max %:", res["A1_density_identity"]["max_abs_identity_residual_pct"])
    print("A3 max |err| %:", res["A3_thickness_holdout"]["max_abs_rel_err_total_pct"])
    print("A3 k*h constant:", res["A3_thickness_holdout"]["k_times_h_is_constant"])
    print("A2 power:", res["A2_vacuity"]["discriminating_power"],
          "caught:", res["A2_vacuity"]["caught"], "passed_through:", res["A2_vacuity"]["passed_through"])
    print("A5 thickness-holdout max %:", res["A5_consistent_fix"]["max_abs_rel_err_brash_pct"])