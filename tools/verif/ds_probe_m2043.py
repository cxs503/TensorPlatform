"""破舱稳性 GZ 验证欠账清偿探针（mission 2043, verifier 席）。

对 ``backend/api/damage_stability.py`` 的破舱稳性内核做**独立精确复核**：
平台 2D 截面梯形积分 vs 本仓 ``tools/verif/ds_exact_reference.py`` 的断点精确积分，
再加解析锚（损失浮力 t'/GM 闭式、完整船壁式闭式、SOLAS Reg.6/7.2 规则值）。

用法：
    python3 tools/verif/ds_probe_m2043.py            # 写 evidence.json + verdict.json + 图
    python3 tools/verif/ds_probe_m2043.py --tag before   # 写 evidence_before.json（修复前留痕）
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "verif"))

import ds_exact_reference as REF  # noqa: E402

import importlib.util  # noqa: E402

_DS_PATH = os.path.join(ROOT, "backend", "api", "damage_stability.py")
_spec = importlib.util.spec_from_file_location("ds_platform_module", _DS_PATH)
_ds = importlib.util.module_from_spec(_spec)
sys.modules["ds_platform_module"] = _ds
_spec.loader.exec_module(_ds)

Compartment = _ds.Compartment
DamageScenario = _ds.DamageScenario
DamageStabilityCalculator = _ds.DamageStabilityCalculator
DamageStabilityInput = _ds.DamageStabilityInput
ShipData = _ds.ShipData
ProbabilisticInput = _ds.ProbabilisticInput
ProbabilisticDamageStability = _ds.ProbabilisticDamageStability
solas2009_required_index = _ds.solas2009_required_index

OUT = os.path.join(ROOT, "artifacts", "verif", "damage_stability_gz_m2043")

SHIP = dict(L=100.0, B=20.0, D=8.0, T=6.0, Cb=1.0, LCG=0.0, TCG=0.0)
DISPL = 1.025 * 100.0 * 20.0 * 6.0

# (name, l, b, z_b, z_t, mu, KG, TCG)
CASES = [
    ("C1_intact_mu0", 20.0, 12.0, 0.5, 7.0, 0.00, 5.0, 0.0),
    ("C2_centre_mu095_KG5", 20.0, 12.0, 0.5, 7.0, 0.95, 5.0, 0.0),
    ("C3_centre_mu095_KG55", 20.0, 12.0, 0.5, 7.0, 0.95, 5.5, 0.0),
    ("C4_centre_mu050_KG5", 20.0, 12.0, 0.5, 7.0, 0.50, 5.0, 0.0),
    ("C5_side_mu095_TCG4", 20.0, 12.0, 0.5, 7.0, 0.95, 5.0, 4.0),
    ("C6_long_mu095", 40.0, 12.0, 0.5, 7.0, 0.95, 5.0, 0.0),
]


def build(case):
    name, l, b, z_b, z_t, mu, kg, tcg = case
    h = z_t - z_b
    ship = ShipData(**SHIP, VCG=kg, displacement=DISPL,
                    compartments=[Compartment(name="C", volume=l * b * h, LCG=0.0,
                                              TCG=tcg, VCG=z_b + h / 2.0,
                                              permeability=mu, location="mid",
                                              length=l, breadth=b, height=h)])
    damage = DamageScenario(damaged_compartments=["C"], damage_location="side")
    barge = REF.Barge(L=SHIP["L"], B=SHIP["B"], D=SHIP["D"], T=SHIP["T"], KG=kg)
    void = REF.Void(l=l, b=b, z_b=z_b, z_t=z_t, y_c=tcg, mu=mu)
    return name, ship, damage, barge, void


def code_view(calc, ship, damage):
    """取平台内核在指定矩阵下的全部量（不经 HTTP，直接调用实现）。"""
    g = calc._intact_geometry(ship)
    flood = calc._flooding_state(ship, damage)
    calc._yb_cache = {}
    t_prime = calc._parallel_sinkage_keel_draft(ship, g, flood)
    if hasattr(calc, "_free_surface_correction"):
        gg0 = calc._free_surface_correction(flood, g)
    else:  # 修复前版本：直接把破损舱自由液面惯性矩当 GG0 扣减
        gg0 = flood["i_x_free"] / g["nabla_ws"] if g["nabla_ws"] > 0 else 0.0
    kg = ship.VCG
    heel = calc._find_heel(ship, g, kg, gg0, flood)
    cap = calc._deck_edge_angle(ship, g, t_prime)
    curve = calc._gz_curve(ship, g, kg, gg0, flood, heel, cap)
    res = calc.analyze(DamageStabilityInput(ship=ship, damage=damage))
    d = res.model_dump() if hasattr(res, "model_dump") else res.dict()
    return {
        "draft": float(d.get("final_draft", t_prime)),
        "t_prime_internal": float(t_prime),
        "trim": float(d.get("final_trim", 0.0)),
        "heel": float(d.get("final_heel", heel)),
        "gm": float(d.get("GM_damage", 0.0)),
        "gz_max": float(d.get("GZ_max", curve["gz_max"])),
        "gz_range": float(d.get("GZ_range", curve["range"])),
        "s": float(d.get("s_factor", 0.0)),
        "GZ0": float(d.get("GZ_0", 0.0)) if "GZ_0" in d else None,
        "gg0": float(gg0),
        "cap": float(cap),
        "A_loss": float(flood["A_loss"]),
        "i_x_free": float(flood["i_x_free"]),
        "_gz_at": lambda th: float(calc._gz(ship, g, float(th), kg, gg0, flood)),
        "curve": curve,
    }


def rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) if abs(b) > 1e-12 else (0.0 if abs(a) < 1e-12 else float("inf"))


def run(tag: str) -> Dict:
    os.makedirs(OUT, exist_ok=True)
    calc = DamageStabilityCalculator()
    rows: List[Dict] = []
    curves: Dict[str, Dict] = {}

    for case in CASES:
        name, ship, damage, barge, void = build(case)
        cv = code_view(calc, ship, damage)

        # ---- 参考（精确积分）----
        heel_ref = REF.equilibrium_heel(barge, void)
        draft_ref = REF.solve_waterline(barge, math.radians(heel_ref), void) if abs(heel_ref) < 1e-6 \
            else REF.solve_waterline(barge, math.radians(heel_ref), void)
        cap_ref = REF.deck_edge_angle(barge, draft_ref)
        curve_ref = REF.gz_curve(barge, void, heel_ref, cap_ref)
        gm_ref = REF.analytic_gm(barge, void)
        draft_exact = REF.solve_waterline(barge, 0.0, void)
        draft_analytic = (REF.analytic_draft(barge, void)
                          if (void and void.mu > 0 and REF.closed_form_valid(barge, void))
                          else None)

        # ---- 逐角 GZ 对比（0°..cap°，1°）----
        ang = np.arange(0.0, max(1.0, curve_ref["range"] + heel_ref) + 1e-9, 1.0)
        ang = [float(a) for a in ang]
        gz_code = [cv["_gz_at"](a) for a in ang]
        gz_ref = [REF.gz(barge, a, void) for a in ang]
        d_gz = [c - r for c, r in zip(gz_code, gz_ref)]
        rel_gz = [rel(c, r) for c, r in zip(gz_code, gz_ref) if abs(r) > 0.05]

        # ---- 完整船壁式闭式锚（μ=0 时必须逐点吻合）----
        if void is None or void.mu == 0.0:
            gm0 = barge.T / 2.0 + barge.I_T0 / barge.nabla0 - barge.KG
            bm0 = barge.I_T0 / barge.nabla0
            wall = [REF.wall_sided_gz(barge, a, gm0, bm0) for a in ang]
            wall_dev = max(abs(c - w) for c, w in zip(gz_code, wall))
        else:
            wall_dev = None

        # ---- s 因子：平台 vs 由参考 GZmax/Range 复算 ----
        s_ref = REF.solas_s_index(curve_ref["gz_max"], curve_ref["range"])
        s_from_code_curve = REF.solas_s_index(cv["gz_max"], cv["gz_range"])

        rows.append({
            "case": name,
            "geometry": {"l": void.l, "b": void.b, "z_b": void.z_b, "z_t": void.z_t,
                         "mu": void.mu, "KG": barge.KG, "y_c": void.y_c},
            "draft": {"code": cv["draft"], "ref_exact": draft_exact,
                      "closed_form": draft_analytic,
                      "rel_dev": rel(cv["draft"], draft_exact)},
            "gm": {"code": cv["gm"], "ref_exact": gm_ref, "analytic": gm_ref,
                   "rel_dev": rel(cv["gm"], gm_ref), "abs_dev": cv["gm"] - gm_ref},
            "gg0_deducted_by_code": cv["gg0"],
            "gz_curve": {"angles": ang, "code": gz_code, "ref": gz_ref,
                         "delta": d_gz,
                         "max_abs_delta": max(abs(x) for x in d_gz),
                         "max_rel_delta_where_gz_gt_005": (max(rel_gz) if rel_gz else None)},
            "gz_max": {"code": cv["gz_max"], "ref": curve_ref["gz_max"],
                       "rel_dev": rel(cv["gz_max"], curve_ref["gz_max"])},
            "gz_range": {"code": cv["gz_range"], "ref": curve_ref["range"],
                         "rel_dev": rel(cv["gz_range"], curve_ref["range"])},
            "heel": {"code": cv["heel"], "ref": heel_ref,
                     "rel_dev": rel(cv["heel"], heel_ref) if abs(heel_ref) > 1e-9 else None,
                     "abs_dev_deg": cv["heel"] - heel_ref},
            "s": {"code": cv["s"], "from_code_gz": s_from_code_curve, "from_ref_gz": s_ref},
            "wall_sided_dev_mu0": wall_dev,
            "deck_edge_cap": {"code": cv["cap"], "ref": cap_ref},
            "i_x_free": cv["i_x_free"], "A_loss": cv["A_loss"],
        })
        curves[name] = {"code": gz_code, "ref": gz_ref, "angles": ang}

    # ---- 规则量（与实现无关的规范常数）----
    rules = []
    for ls in (100.0, 150.0, 200.0, 250.0):
        rules.append({"Ls": ls, "code": solas2009_required_index(ls),
                      "solas": REF.solas_required_index(ls),
                      "rel_dev": rel(solas2009_required_index(ls), REF.solas_required_index(ls))})

    # ---- A 指数物理性（概率破舱）----
    probity = _probity(calc)

    ev = {"tag": tag, "cases": rows, "curves": curves, "rules_R": rules,
          "probabilistic_physicality": probity}
    fn = "evidence_before.json" if tag == "before" else "evidence.json"
    with open(os.path.join(OUT, fn), "w") as f:
        json.dump(ev, f, ensure_ascii=False, indent=1)
    return ev


def _probity(calc) -> Dict:
    """A=Σp·s 的物理性探针：缺省 p、越界 p、Σp>1。"""
    name, ship, damage, barge, void = build(CASES[1])
    out: Dict[str, object] = {}
    zone = {"name": "Z1", "damaged_compartments": ["C"]}
    try:
        pds = ProbabilisticDamageStability()
        try:
            res = pds.calculate_A(ProbabilisticInput(ship=ship, damage_zones=[zone]))
            out["missing_p"] = {"accepted": True, "A": float(res.A_value)}
        except Exception as e:  # noqa: BLE001
            out["missing_p"] = {"accepted": False,
                                "error": type(e).__name__,
                                "detail": str(getattr(e, "detail", e))[:120]}
        try:
            res = pds.calculate_A(ProbabilisticInput(
                ship=ship, damage_zones=[dict(zone, probability=1.7)]))
            out["p_out_of_range"] = {"accepted": True, "A": float(res.A_value)}
        except Exception as e:  # noqa: BLE001
            out["p_out_of_range"] = {"accepted": False, "error": type(e).__name__,
                                     "detail": str(getattr(e, "detail", e))[:120]}
        zones = [dict(zone, name=f"Z{i}", probability=0.2) for i in range(8)]
        try:
            res = pds.calculate_A(ProbabilisticInput(ship=ship, damage_zones=zones))
            out["sum_p_gt_one"] = {"accepted": True, "sum_p": 1.6, "A": float(res.A_value),
                                   "A_exceeds_one": bool(res.A_value > 1.0)}
        except Exception as e:  # noqa: BLE001
            out["sum_p_gt_one"] = {"accepted": False, "error": type(e).__name__,
                                   "detail": str(getattr(e, "detail", e))[:120]}
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def verdict(ev: Dict) -> Dict:
    """机器判据：逐项误差 vs 验收带（3%）。"""
    crit, worst = [], 0.0
    for row in ev["cases"]:
        n = row["case"]
        gates = [
            ("J1_draft", row["draft"]["rel_dev"], 0.03),
            ("J2_GM", row["gm"]["rel_dev"], 0.03),
            ("J3_GZ_curve_max_rel", row["gz_curve"]["max_rel_delta_where_gz_gt_005"], 0.03),
        ]
        for key, val, tol in gates:
            if val is None:
                continue
            ok = val <= tol
            crit.append({"case": n, "criterion": key, "value": val, "tol": tol,
                         "basis": "relative", "pass": ok})
            if not ok:
                worst = max(worst, val)
        # GZmax / Range：残存稳性接近消失时以**绝对带**判定（相对误差在分母→0 时无意义）；
        # 绝对带 = 0.02 m / 1.0 deg，对应 SOLAS Reg.7.2 判据 GZmax>=0.10 m 的 20% 与 Range>=16 deg 的 1/16。
        for key, val, abs_val, tol, abs_tol in (
                ("J4_GZmax", row["gz_max"]["rel_dev"], row["gz_max"]["code"] - row["gz_max"]["ref"], 0.03, 0.02),
                ("J5_Range", row["gz_range"]["rel_dev"], row["gz_range"]["code"] - row["gz_range"]["ref"], 0.03, 1.0)):
            ok_rel = val is not None and val <= tol
            ok_abs = abs(abs_val) <= abs_tol
            crit.append({"case": n, "criterion": key, "rel": val, "abs": abs_val,
                         "tol": tol, "abs_tol": abs_tol,
                         "basis": "relative" if ok_rel else ("absolute" if ok_abs else "fail"),
                         "pass": bool(ok_rel or ok_abs)})
            if not (ok_rel or ok_abs) and val is not None:
                worst = max(worst, val)
        # s 因子（决策量）：双方同为 0（残存稳性不足）或相对差 <=3%
        ok_s = (row["s"]["code"] == 0.0 and row["s"]["from_ref_gz"] == 0.0) or \
            (row["s"]["from_ref_gz"] > 0 and
             abs(row["s"]["code"] - row["s"]["from_ref_gz"]) / row["s"]["from_ref_gz"] <= 0.03)
        crit.append({"case": n, "criterion": "J12_s_factor",
                     "code": row["s"]["code"], "ref": row["s"]["from_ref_gz"], "pass": bool(ok_s)})
        if not ok_s:
            worst = max(worst, 1.0)
        # 横倾：绝对值 0.5° 或 3%
        h = row["heel"]
        hv = h["rel_dev"] if h["rel_dev"] is not None else abs(h["abs_dev_deg"]) / 3.0
        ok = (hv is not None and hv <= 0.03) or abs(h["abs_dev_deg"]) <= 0.5
        crit.append({"case": n, "criterion": "J6_heel", "value": h["abs_dev_deg"],
                     "tol_deg": 0.5, "pass": ok})
        if not ok:
            worst = max(worst, abs(h["abs_dev_deg"]) / 3.0)
    pass_n = sum(1 for c in crit if c["pass"])
    v = {"n_criteria": len(crit), "n_pass": pass_n,
         "pass_rate": round(pass_n / len(crit), 4),
         "worst_rel_dev": worst,
         "grade": "REAL" if pass_n == len(crit) else ("PARTIAL" if pass_n >= 0.7 * len(crit) else "MOCK"),
         "criteria": crit}
    return v


def cfg_check(ev: Dict) -> List[Dict]:
    """配置/规则层判据（与几何无关）。"""
    out = []
    for r in ev["rules_R"]:
        out.append({"criterion": f"J8_R(Ls={r['Ls']:.0f})", "rel_dev": r["rel_dev"],
                    "pass": r["rel_dev"] <= 1e-12})
    pr = ev["probabilistic_physicality"]
    mp = pr.get("missing_p", {})
    out.append({"criterion": "J9_no_silent_default_p",
                "pass": not mp.get("accepted", True),
                "note": "缺省 p 必须 422（不得静默默认 0.1）"})
    oor = pr.get("p_out_of_range", {})
    out.append({"criterion": "J10_p_in_range_0_1", "pass": not oor.get("accepted", True),
                "note": "p∉[0,1] 必须 422"})
    sp = pr.get("sum_p_gt_one", {})
    out.append({"criterion": "J11_sum_p_le_1",
                "pass": not (sp.get("accepted") and sp.get("A_exceeds_one")),
                "note": "Σp>1 会使 A>1（非物理），必须拒绝或显式标注"})
    return out


def figures(ev: Dict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = [c["case"] for c in ev["cases"]]
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))
    for key, row in zip(names, ev["cases"]):
        if key.startswith("C1"):
            continue
        ax = axes[0]
        ax.plot(row["gz_curve"]["angles"], row["gz_curve"]["ref"], "-", lw=1.8,
                label=f"{key} 精确参考")
        ax.plot(row["gz_curve"]["angles"], row["gz_curve"]["code"], "--", lw=1.4,
                label=f"{key} 平台")
    axes[0].set_xlabel("heel angle (deg)")
    axes[0].set_ylabel("GZ (m)")
    axes[0].set_title("Residual GZ: platform kernel vs exact breakpoint-integral reference")
    axes[0].grid(alpha=0.3)
    axes[0].legend(fontsize=6.5, ncol=2)

    for key, row in zip(names, ev["cases"]):
        axes[1].plot(row["gz_curve"]["angles"], row["gz_curve"]["delta"], "-o", ms=2.2,
                     lw=1.2, label=key)
    axes[1].axhspan(-0.01, 0.01, color="tab:green", alpha=0.12, label="±0.01 m 带")
    axes[1].set_xlabel("heel angle (deg)")
    axes[1].set_ylabel("dGZ = platform - reference (m)")
    axes[1].set_title("per-angle deviation (band ±0.01 m ~ 3% of GZmax)")
    axes[1].grid(alpha=0.3)
    axes[1].legend(fontsize=6.5)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_gz_vs_reference.png"), dpi=150)
    plt.close(fig)

    # GM / s 汇总
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.2))
    gm_c = [r["gm"]["code"] for r in ev["cases"]]
    gm_r = [r["gm"]["ref_exact"] for r in ev["cases"]]
    x = np.arange(len(names))
    axes[0].bar(x - 0.2, gm_r, 0.4, label="exact reference GM")
    axes[0].bar(x + 0.2, gm_c, 0.4, label="platform GM_damage")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels([n.split("_")[0] for n in names])
    axes[0].set_ylabel("GM_d (m)")
    axes[0].set_title("Damaged initial GM_d vs exact reference")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.3, axis="y")
    for i, (a, b) in enumerate(zip(gm_c, gm_r)):
        axes[0].text(i, max(a, b) + 0.05, f"{(a - b) / abs(b) * 100:+.1f}%",
                     ha="center", fontsize=7)

    axes[1].bar(x - 0.2, [r["s"]["from_ref_gz"] for r in ev["cases"]], 0.4, label="s from reference GZmax/Range")
    axes[1].bar(x + 0.2, [r["s"]["code"] for r in ev["cases"]], 0.4, label="s platform")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels([n.split("_")[0] for n in names])
    axes[1].set_ylabel("s factor")
    axes[1].set_title("Reg.7.2 survival factor s vs reference")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_gm_and_s.png"), dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="after")
    a = ap.parse_args()
    ev = run(a.tag)
    v = verdict(ev)
    v["config_criteria"] = cfg_check(ev)
    v["n_pass"] += sum(1 for c in v["config_criteria"] if c["pass"])
    v["n_criteria"] += len(v["config_criteria"])
    v["pass_rate"] = round(v["n_pass"] / v["n_criteria"], 4)
    v["grade"] = "REAL" if v["n_pass"] == v["n_criteria"] else (
        "PARTIAL" if v["n_pass"] >= 0.7 * v["n_criteria"] else "MOCK")
    fn = "verdict_before.json" if a.tag == "before" else "verdict.json"
    with open(os.path.join(OUT, fn), "w") as f:
        json.dump(v, f, ensure_ascii=False, indent=1)
    figures(ev)
    print(json.dumps({k: v[k] for k in ("n_criteria", "n_pass", "pass_rate", "grade", "worst_rel_dev")},
                     ensure_ascii=False))
    for c in v["criteria"]:
        if not c["pass"]:
            print("FAIL", c)