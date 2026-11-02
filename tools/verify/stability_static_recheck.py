#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静稳性端点复验门禁（m2056）：/stability/evaluate 修复后对闭式解的复核。

只读导入 ship-performance-platform 工作树里的 ``api.stability``（不启动服务、
不联网），把三条口径同时算出来：

1. ``closed_form`` —— 矩形浮箱闭式解（KB=T/2, BM=B^2/12T, GZ=sin(phi)(GM+BM tan^2/2)）；
2. ``legacy_mock`` —— 修复前源码里的三个表达式（KB=T(1-0.5(1-Cb))、GZ_max=1.5 GM sin45°、
   消失角=60+10 GM），逐式复刻，用于量化欠账幅度；
3. ``platform`` —— 修复后 ``evaluate_static_stability`` 的返回值。

判据：GM/BM/KB 相对闭式解 ≤3%（实测 ~1e-6），GZ 曲线逐点 ≤0.05 m，
消失角必须等于横倾角闭式解或为 null（不得臆造）。

用法：python3 tools/verify/stability_static_recheck.py [--ship-repo DIR]
产出：artifacts/verification_debt/stability_recheck/{results.json,fig_stability_recheck.png}
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

RHO = 1.025
BOX_CASES = [
    ("C1", 100.0, 10.0, 4.0), ("C2", 100.0, 10.0, 8.0), ("C3", 100.0, 10.0, 12.0),
    ("C4", 100.0, 20.0, 4.0), ("C5", 100.0, 20.0, 8.0), ("C6", 100.0, 20.0, 12.0),
    ("C7", 100.0, 30.0, 4.0), ("C8", 100.0, 30.0, 8.0), ("C9", 100.0, 30.0, 12.0),
]


def closed_form(B, T, KG):
    kb = T / 2.0
    bm = B ** 2 / (12.0 * T)
    return {"KB": kb, "BM": bm, "KM": kb + bm, "GM": kb + bm - KG}


def wall_sided_gz(gm, bm, phi_deg):
    p = math.radians(phi_deg)
    return math.sin(p) * (gm + 0.5 * bm * math.tan(p) ** 2)


def legacy_formulas(L, B, T, Cb, Cm, KG):
    """修复前 backend/api/stability.py::evaluate_stability 的三条表达式。"""
    kb = T * (1.0 - 0.5 * (1.0 - Cb))
    bm = B ** 2 / (12.0 * T) * Cb / Cm
    gm = kb + bm - KG
    return {"KB": kb, "BM": bm, "KM": kb + bm, "GM": gm,
            "GZ_max": gm * math.sin(math.radians(45.0)) * 1.5,
            "GZ_angle": 45.0, "vanishing_angle": 60.0 + gm * 10.0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ship-repo",
                    default=os.environ.get("SHIP_REPO", "/root/ship-m2056"))
    ap.add_argument("--out", default="artifacts/verification_debt/stability_recheck")
    args = ap.parse_args()
    sys.path.insert(0, os.path.join(args.ship_repo, "backend"))
    from api.stability import evaluate_static_stability, StabilityRequest  # noqa: E402
    from api.stability_imo import (  # noqa: E402
        GZCurveInput, calculate_gz_curve)

    os.makedirs(args.out, exist_ok=True)
    rows, judgments = [], {}

    for cid, L, B, T in BOX_CASES:
        KG = 0.4 * T
        D = 2.0 * T
        disp = RHO * L * B * T
        req = StabilityRequest(L=L, B=B, T=T, D=D, displacement=disp,
                               Cb=1.0, Cm=1.0, KG=KG)
        plat = evaluate_static_stability(req)
        ref = closed_form(B, T, KG)
        leg = legacy_formulas(L, B, T, 1.0, 1.0, KG)
        rows.append({
            "case": cid, "L": L, "B": B, "T": T, "KG": KG,
            "GM_ref": round(ref["GM"], 6),
            "GM_new": plat.GM,
            "err_new_pct": round(100 * abs(plat.GM - ref["GM"]) / abs(ref["GM"]), 6),
            "GM_legacy": round(leg["GM"], 6),
            "err_legacy_pct": round(100 * abs(leg["GM"] - ref["GM"]) / abs(ref["GM"]), 4),
            "KB_ref": round(ref["KB"], 6), "KB_new": plat.KB,
            "BM_ref": round(ref["BM"], 6), "BM_new": plat.BM,
            "legacy_KB_minus_ref": round(leg["KB"] - ref["KB"], 6),
            "GZ_max_new": plat.GZ_max, "GZ_max_legacy": round(leg["GZ_max"], 6),
            "GZ_angle_new": plat.GZ_angle,
            "vanishing_new": plat.vanishing_angle,
            "vanishing_legacy": round(leg["vanishing_angle"], 3),
            "phi_lim": plat.heel_validity_limit_deg,
        })

    judgments["J1_GM_rel_err_le_3pct"] = max(r["err_new_pct"] for r in rows) <= 3.0
    judgments["J1_max_GM_err_pct"] = max(r["err_new_pct"] for r in rows)
    judgments["J1_legacy_max_GM_err_pct"] = max(r["err_legacy_pct"] for r in rows)
    judgments["J2_legacy_KB_bias_is_T_over_2"] = all(
        abs(r["legacy_KB_minus_ref"] - r["T"] / 2.0) < 1e-6 for r in rows)

    # GZ 曲线逐点对闭式解
    L, B, T, KG = 100.0, 20.0, 8.0, 3.0
    gz = calculate_gz_curve(GZCurveInput(
        L=L, B=B, T=T, D=2 * T, displacement=RHO * L * B * T,
        Cb=1.0, Cm=1.0, KG=KG))
    ref = closed_form(B, T, KG)
    gz_cmp = []
    max_dev = 0.0
    for a, g in zip(gz.angles, gz.gz_values):
        analytic = wall_sided_gz(ref["GM"], ref["BM"], a)
        dev = abs(g - analytic)
        max_dev = max(max_dev, dev)
        gz_cmp.append({"angle": a, "gz_platform": g,
                       "gz_closed_form": round(analytic, 6),
                       "abs_dev": round(dev, 8)})
    judgments["J3_GZ_max_abs_dev_le_0p05m"] = max_dev <= 0.05
    judgments["J3_max_GZ_abs_dev_m"] = round(max_dev, 8)

    # 消失角：正 GM 浮箱有效角内不回零 -> 必须 null；负 GM 浮箱 = 横倾角闭式解
    pos = evaluate_static_stability(StabilityRequest(
        L=100.0, B=20.0, T=8.0, D=16.0, displacement=RHO * 100 * 20 * 8,
        Cb=1.0, Cm=1.0, KG=3.0))
    negKG = 9.0
    neg = evaluate_static_stability(StabilityRequest(
        L=100.0, B=20.0, T=8.0, D=16.0, displacement=RHO * 100 * 20 * 8,
        Cb=1.0, Cm=1.0, KG=negKG))
    ref_neg = closed_form(20.0, 8.0, negKG)
    loll_closed = math.degrees(math.atan(math.sqrt(-2 * ref_neg["GM"] / ref_neg["BM"])))
    judgments["J4_positive_GM_vanish_is_null"] = pos.vanishing_angle is None
    judgments["J4_negative_GM_loll_matches_closed_form"] = (
        neg.vanishing_angle is not None
        and abs(neg.vanishing_angle - loll_closed) <= 0.15)
    judgments["J4_loll_closed_form_deg"] = round(loll_closed, 4)
    judgments["J4_loll_platform_deg"] = neg.vanishing_angle
    judgments["J5_legacy_vanish_physically_impossible"] = [
        round(legacy_formulas(100.0, B, T, 1.0, 1.0, 0.4 * T)["vanishing_angle"], 1)
        for _, B, T in [(0, 10.0, 4.0), (0, 20.0, 8.0), (0, 30.0, 12.0)]]

    out = {"platform": "ship-performance-platform @ agent/m2056",
           "endpoint": "POST /api/v1/stability/evaluate",
           "kernel": "api.stability_core (KB=T(5/6-Cb/(3Cw)), BM=Cw B^2/(12 Cb T), "
                     "GZ=sin(phi)(GM+BM tan^2(phi)/2))",
           "cases": rows, "gz_curve_case": {"L": L, "B": B, "T": T, "KG": KG,
                                            "phi_lim": gz.heel_validity_limit_deg,
                                            "points": gz_cmp},
           "judgments": judgments,
           "all_pass": all(v is True for k, v in judgments.items()
                           if isinstance(v, bool))}
    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(judgments, ensure_ascii=False, indent=2))
    print("all_pass =", out["all_pass"])

    # 图：左 GZ 曲线（新/旧/闭式），右 GM 误差（旧 vs 新）
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                       # pragma: no cover
        print("matplotlib 不可用，跳过出图:", exc)
        return 0 if out["all_pass"] else 1
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.6))
    ax = axes[0]
    ax.plot([p["angle"] for p in gz_cmp], [p["gz_closed_form"] for p in gz_cmp],
            "k-", lw=2.2, label="closed form  sin(phi)(GM+BM tan²phi/2)")
    ax.plot([p["angle"] for p in gz_cmp], [p["gz_platform"] for p in gz_cmp],
            "o", ms=3.2, mfc="none", label="platform /stability/evaluate (m2056)")
    angles = list(range(0, 91, 5))
    ax.plot(angles, [legacy_formulas(100.0, 20.0, 8.0, 1.0, 1.0, 3.0)["GZ_max"]
                     * math.sin(math.radians(a)) /
                     math.sin(math.radians(45.0)) for a in angles],
            "r--", lw=1.6, label="legacy MOCK  GM·sinφ/… (linear, no tan²term)")
    ax.axvline(gz.heel_validity_limit_deg, color="0.4", ls=":",
               label=f"wall-sided limit {gz.heel_validity_limit_deg:.1f}° (deck edge)")
    ax.set_xlabel("heel φ [deg]"), ax.set_ylabel("GZ [m]")
    ax.set_title("Rectangular pontoon L=100 B=20 T=8 KG=3 (Cb=Cm=1)")
    ax.grid(alpha=0.3), ax.legend(fontsize=7.5, loc="upper left")
    ax = axes[1]
    ids = [r["case"] for r in rows]
    ax.semilogy(ids, [max(r["err_legacy_pct"], 1e-9) for r in rows], "rs-",
                label="legacy MOCK GM error [%]")
    ax.semilogy(ids, [max(r["err_new_pct"], 1e-9) for r in rows], "g^-",
                label="m2056 shared-kernel GM error [%]")
    ax.axhline(3.0, color="0.3", ls="--", lw=1.0, label="3 % acceptance line")
    ax.axhline(15.0, color="r", ls=":", lw=1.0, label="15 % unacceptable line")
    ax.set_xlabel("pontoon case (B/T = 1.25–7.5)"), ax.set_ylabel("|GM error| [%]")
    ax.set_title("GM error: legacy MOCK vs shared kernel (log scale)")
    ax.grid(alpha=0.3, which="both"), ax.legend(fontsize=7.5)
    fig.tight_layout()
    fig.savefig(os.path.join(args.out, "fig_stability_recheck.png"), dpi=140)
    print("figure ->", os.path.join(args.out, "fig_stability_recheck.png"))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())