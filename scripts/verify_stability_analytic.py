#!/usr/bin/env python3
"""
稳性端点解析基准复核驱动（verification-debt harness）
=====================================================

对船体总体性能平台（:8001）稳性端点做**解析基准复核**，产出可复算证据：

    POST /api/v1/calculations/stability            ← 现役 serving 端点（审计对象）
    POST /api/v1/methods/hydrostatics/calculate    ← 精细模块端点（对照基准）

参考解（详见 tools/stability_bench/reference.py）
  R1 精确解：矩形横剖面驳船（Cb=Cm=Cw=1）。恒定排水体积下浸水剖面由 [0,D] 裁剪 +
     二分求水线得到，形心投影给出 KN，GZ = KN − KG·sinφ —— **零经验系数**。
     wall-sided 角域内再与闭式解 (GM+½BM tan²φ)sinφ 及多边形裁剪几何路径三方互校。
  R3 经验近似：Morrish KB / 中剖面 KB / 修正水线面惯性矩系数，仅用于离群判断。

判据：|相对误差| ≤ 3% PASS；> 15% FAIL（不可接受）；其间 WARN。

产物 artifacts/stability_analytic/：
  report.json  全部输入/参考值/平台值/相对误差/判定（报告直接引用，零手抄）
  fig1_gz_curves.png fig2_gz_error.png fig3_kb_methods.png fig4_rating_errors.png

用法：
  python3 scripts/verify_stability_analytic.py           # 探测在线 API
  python3 scripts/verify_stability_analytic.py --offline # 仅离线复刻模型
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.stability_bench import reference as ref  # noqa: E402

OUT = ROOT / "artifacts" / "stability_analytic"
SHIP_API = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")

TOL_PASS, TOL_FAIL = 0.03, 0.15
ANGLES = list(range(0, 61, 5))


# ---------------------------------------------------------------------------
def _post(path: str, body: dict, timeout: float = 30.0) -> Optional[dict]:
    req = urllib.request.Request(f"{SHIP_API}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] {path} 失败: {exc}", file=sys.stderr)
        return None


def verdict(err: float) -> str:
    e = abs(err)
    if e != e:            # NaN
        return "N/A"
    return "PASS" if e <= TOL_PASS else ("FAIL" if e > TOL_FAIL else "WARN")


def row(quantity, model, reference, basis, unit=""):
    e = ref.rel_err(model, reference) if model == model else float("nan")
    return {"quantity": quantity, "model": round(model, 6), "reference": round(reference, 6),
            "rel_error": round(e, 6), "rel_error_pct": round(100.0 * e, 3),
            "verdict": verdict(e), "basis": basis, "unit": unit}


# ---------------------------------------------------------------------------
def probe_serving(case: dict, online: bool) -> dict:
    L, B, T, Cb, KG = case["L"], case["B"], case["T"], case["Cb"], case["KG"]
    if online:
        resp = _post("/calculations/stability",
                     {"length": L, "beam": B, "draft": T,
                      "displacement": case["displacement"], "cb": Cb, "kg": KG})
        if resp is None:
            raise RuntimeError("serving 端点不可用")
        return {"source": "api:/calculations/stability", "raw": resp,
                "kb": resp["kb"], "bm": resp["bm"], "gm": resp["gm"],
                "gz_curve": resp.get("gz_curve"),
                "gz_max": resp.get("gz_max"), "gz_max_angle": resp.get("gz_max_angle"),
                "vanishing_angle": resp.get("vanishing_angle"),
                "area_0_30": resp.get("area_0_30"), "area_30_40": resp.get("area_30_40"),
                "area_0_40": resp.get("area_0_40"),
                "imo_criteria": resp.get("imo_criteria", {})}
    kb, bm = ref.platform_kb(T, Cb), ref.platform_bm(B, T, Cb)
    gm = kb + bm - KG
    gz = {float(a): ref.platform_gz(a, gm, kb, bm, KG, B) for a in ANGLES}
    vals = [gz[float(a)] for a in ANGLES]
    a0_30, a30_40, a0_40 = ref.platform_imo_areas(gz)
    return {"source": "offline:reference.platform_*", "raw": None,
            "kb": kb, "bm": bm, "gm": gm, "gz_curve": gz,
            "gz_max": max(vals), "gz_max_angle": float(ANGLES[vals.index(max(vals))]),
            "vanishing_angle": 60.0, "area_0_30": a0_30, "area_30_40": a30_40,
            "area_0_40": a0_40, "imo_criteria": {}}


def probe_hydrostatics(case: dict, online: bool) -> Optional[dict]:
    if not case.get("with_hydrostatics") or not online:
        return None
    Cb = case["Cb"]
    Cw = case.get("Cw") or (1.0 + 2.0 * Cb) / 3.0
    Cm = case.get("Cm") or 0.98
    resp = _post("/methods/hydrostatics/calculate",
                 {"Lpp": case["L"], "B": case["B"], "T": case["T"], "Cb": Cb,
                  "Cm": Cm, "Cw": Cw, "KG": case["KG"], "rho": case["rho"]})
    if resp is None:
        return None
    return {"source": "api:/methods/hydrostatics/calculate", "Cw": Cw, "Cm": Cm,
            "kb": resp["KB"], "bm": resp["BM"], "km": resp["KM"], "gm": resp["GM"],
            "displacement": resp["displacement"],
            "gz_curve": {float(k): v for k, v in (resp.get("GZ_curve") or {}).items()},
            "gz_max": resp.get("GZ_max"), "vanishing_angle": resp.get("vanishing_angle")}


# ---------------------------------------------------------------------------
def build_cases() -> List[dict]:
    box_a = ref.BoxBarge(L=100.0, B=20.0, T=8.0, D=25.0, KG=6.0)
    box_b = ref.BoxBarge(L=80.0, B=16.0, T=8.0, D=20.0, KG=5.5)
    return [
        {"id": "BOX-A", "kind": "box", "barge": box_a,
         "desc": "矩形驳船 A（精确基准，富裕干舷覆盖 0~60°）：L=100, B=20, T=8, D=25, KG=6",
         "L": 100.0, "B": 20.0, "T": 8.0, "D": 25.0, "KG": 6.0, "Cb": 1.0, "rho": 1025.0,
         "displacement": round(box_a.displacement_t, 2)},
        {"id": "BOX-B", "kind": "box", "barge": box_b,
         "desc": "矩形驳船 B（窄深型）：L=80, B=16, T=8, D=20, KG=5.5",
         "L": 80.0, "B": 16.0, "T": 8.0, "D": 20.0, "KG": 5.5, "Cb": 1.0, "rho": 1025.0,
         "displacement": round(box_b.displacement_t, 2)},
        {"id": "KCS", "kind": "real",
         "desc": "KCS 集装箱船标模（ITTC 几何：Lpp=230, B=32.2, T=10.8, Cb=0.651, Cw=0.82）",
         "L": 230.0, "B": 32.2, "T": 10.8, "D": 19.0, "KG": 12.0, "Cb": 0.651,
         "rho": 1025.0, "with_hydrostatics": True, "Cw": 0.82, "Cm": 0.985,
         "displacement": round(230.0 * 32.2 * 10.8 * 0.651 * 1025.0 / 1000.0, 2)},
        {"id": "AFRAMAX", "kind": "real",
         "desc": "Aframax 级油船（Lpp=250, B=44, T=15.0, Cb=0.82, KG=11.5）",
         "L": 250.0, "B": 44.0, "T": 15.0, "D": 24.0, "KG": 11.5, "Cb": 0.82,
         "rho": 1025.0, "with_hydrostatics": True,
         "displacement": round(250.0 * 44.0 * 15.0 * 0.82 * 1025.0 / 1000.0, 2)},
    ]


# ---------------------------------------------------------------------------
def verify_case(case: dict, online: bool) -> dict:
    serving = probe_serving(case, online)
    rows: List[dict] = []
    gz_compare: List[dict] = []
    notes: List[str] = []

    # 平台曲线：API 不返回曲线时用源码逐字复刻（其保真度单独核验）
    api_curve = serving.get("gz_curve")
    if isinstance(api_curve, dict) and api_curve:
        curve = {float(k): float(v) for k, v in api_curve.items()}
        curve_basis = "api"
    else:
        kb, bm, gm = serving["kb"], serving["bm"], serving["gm"]
        curve = {float(a): ref.platform_gz(a, gm, kb, bm, case["KG"], case["B"])
                 for a in ANGLES}
        curve_basis = "source-replica"

    is_box = case["kind"] == "box"
    if is_box:
        b: ref.BoxBarge = case["barge"]
        limit = ref.box_valid_heel_limit_deg(b)
        deck = ref.full_submersion_angle_deg(b)
        notes.append(f"wall-sided 有效角域上限 {limit:.2f}°，甲板边缘入水角 {deck:.2f}°")
        rows += [
            row("KB", serving["kb"], b.kb, "R1 精确解 KB=T/2", "m"),
            row("BM", serving["bm"], b.bm, "R1 精确解 BM=B²/(12T)", "m"),
            row("GM", serving["gm"], b.gm, "R1 精确解 GM=KB+BM−KG", "m"),
            row("displacement", case["displacement"], b.displacement_t,
                "R1 精确解 Δ=ρ·L·B·T", "t"),
        ]
        for a in ANGLES:
            if a > deck:      # 甲板浸水后不再属完整稳性评估域
                continue
            gz_ref = ref.heeled_box_gz(b, a)
            gz_ws = ref.wall_sided_gz(a, b.gm, b.bm)
            gz_plat = curve.get(float(a))
            if gz_plat is None:
                continue
            gz_compare.append({
                "phi_deg": a,
                "gz_reference_R1": round(gz_ref, 6),
                "gz_wallsided_closed_form": round(gz_ws, 6),
                "cross_check_gap_m": round(abs(gz_ws - gz_ref), 9) if a <= limit else None,
                "gz_platform": round(gz_plat, 6),
                "rel_error_pct": round(100.0 * ref.rel_err(gz_plat, gz_ref), 3),
            })
        for a in (10.0, 20.0, 30.0, 40.0, 50.0, 60.0):
            if a > deck:
                continue
            rows.append(row(f"GZ@{a:g}°", curve.get(a, float("nan")),
                            ref.heeled_box_gz(b, a),
                            "R1 exact (volume-preserving section geometry)" + ("" if online else "（离线复刻曲线）"),
                            "m"))
        fine = [(a * 0.25, ref.heeled_box_gz(b, a * 0.25)) for a in range(0, 241)]
        xs = [x for x, _ in fine]
        ys = [y for _, y in fine]
        rows += [
            row("Area_0_40", serving["area_0_40"],
                ref.area_under_curve(xs, ys, 0.0, 40.0), "R1 精确解 0.25° 积分", "m·rad"),
            row("Area_30_40", serving["area_30_40"],
                ref.area_under_curve(xs, ys, 30.0, 40.0), "R1 精确解 0.25° 积分", "m·rad"),
            row("Area_0_30", serving["area_0_30"],
                ref.area_under_curve(xs, ys, 0.0, 30.0), "R1 精确解 0.25° 积分", "m·rad"),
        ]
        gz_ref_max = max(ys)
        i_max = ys.index(gz_ref_max)
        rows.append(row("GZ_max", serving["gz_max"], gz_ref_max, "R1 精确解极值", "m"))
        rows.append({"quantity": "φ_GZmax", "model": serving["gz_max_angle"],
                     "reference": round(xs[i_max], 3),
                     "rel_error": None, "rel_error_pct": None,
                     "verdict": "PASS" if abs(serving["gz_max_angle"] - xs[i_max]) <= 2.5 else "WARN",
                     "basis": "R1 精确解极值角（容差 ±2.5°=半格）", "unit": "deg"})
    else:
        Cb, T, B = case["Cb"], case["T"], case["B"]
        Cw = case.get("Cw") or (1.0 + 2.0 * Cb) / 3.0
        Cm = case.get("Cm") or 0.98
        vol = case["L"] * B * T * Cb
        rows.append(row("displacement", case["displacement"], vol * case["rho"] / 1000.0,
                        "几何自洽性 Δ=ρ·L·B·T·Cb（输入一致性，非物理基准）", "t"))
        rows.append(row("KB", serving["kb"], ref.morrish_kb(T, Cb, Cw),
                        f"R3 Morrish（Cw={Cw:.3f}，经验式）", "m"))
        kb_sec = ref.section_type_kb(T, Cm)
        rows.append({"quantity": "KB(经验式互差)", "model": round(kb_sec, 4),
                     "reference": round(ref.morrish_kb(T, Cb, Cw), 4),
                     "rel_error": round(ref.rel_err(kb_sec, ref.morrish_kb(T, Cb, Cw)), 6),
                     "rel_error_pct": round(100 * ref.rel_err(kb_sec, ref.morrish_kb(T, Cb, Cw)), 3),
                     "verdict": verdict(ref.rel_err(kb_sec, ref.morrish_kb(T, Cb, Cw))),
                     "basis": "R3 两条独立经验式互差（≤5% 视为量级一致）", "unit": "m"})
        notes.append("无船体型线 ⇒ 无 R1 精确解；本案例仅做输入自洽性与经验式离群判断")

    # 复刻曲线保真度（仅在线且有 API 曲线时）
    hydro = probe_hydrostatics(case, online)
    if hydro and hydro.get("displacement"):
        vol = case["L"] * case["B"] * case["T"] * case["Cb"]
        rows.append(row("Δ(hydrostatics)", hydro["displacement"], vol * case["rho"] / 1000.0,
                        "几何自洽性 Δ=ρ·L·B·T·Cb（输入一致性）", "t"))

    return {"case": {k: v for k, v in case.items() if k != "barge"},
            "serving": {k: v for k, v in serving.items() if k not in ("gz_curve", "raw")},
            "serving_raw": serving.get("raw"),
            "curve_basis": curve_basis,
            "platform_gz_curve": {str(k): round(v, 6) for k, v in curve.items()},
            "hydrostatics": hydro,
            "checks": rows, "gz_compare": gz_compare, "notes": notes}


# ---------------------------------------------------------------------------
def make_figures(results: List[dict], path: Path) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.mkdir(parents=True, exist_ok=True)
    made = []
    r0 = next(x for x in results if x["case"]["id"] == "BOX-A")
    b = ref.BoxBarge(L=100.0, B=20.0, T=8.0, D=25.0, KG=6.0)
    deck = ref.full_submersion_angle_deg(b)
    phis = [a * 0.5 for a in range(0, int(deck * 2) + 1)]
    gz_r1 = [ref.heeled_box_gz(b, a) for a in phis]
    gz_ws = [ref.wall_sided_gz(a, b.gm, b.bm) for a in phis]
    gz_pl = [r0["platform_gz_curve"].get(str(float(a))) for a in phis]

    # fig1 GZ 曲线
    fig, ax = plt.subplots(figsize=(7.4, 4.6), dpi=140)
    ax.plot(phis, gz_r1, "k-", lw=2.4, label="R1 exact (volume-preserving section geometry)")
    ax.plot(phis, gz_ws, "b--", lw=1.3, label="wall-sided closed form (cross-check in valid range)")
    ax.plot(phis, gz_pl, "ro-", ms=3.2, lw=1.3, label="platform serving endpoint")
    lim = ref.box_valid_heel_limit_deg(b)
    ax.axvline(lim, color="grey", ls=":", lw=1)
    ax.text(lim + 0.8, 0.15, f"wall-sided limit {lim:.1f} deg", rotation=90, fontsize=8, color="grey")
    ax.set_xlabel("heel angle phi (deg)")
    ax.set_ylabel("righting lever GZ (m)")
    ax.set_title("BOX-A box barge: GZ vs exact solution (L=100, B=20, T=8, D=25, KG=6)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    f1 = path / "fig1_gz_curves.png"
    fig.savefig(f1)
    plt.close(fig)
    made.append(str(f1.relative_to(ROOT)))

    # fig2 GZ 误差
    fig, ax = plt.subplots(figsize=(7.4, 4.4), dpi=140)
    for res in results:
        pts = res["gz_compare"]
        if not pts:
            continue
        ax.plot([p["phi_deg"] for p in pts], [p["rel_error_pct"] for p in pts],
                "o-", ms=4, label=res["case"]["id"])
    for y, c, ls, lab in ((3, "green", "--", "+-3% pass line"), (15, "red", ":", "15% unacceptable")):
        ax.axhline(y, color=c, ls=ls, lw=1, label=lab)
    ax.set_xlabel("heel angle phi (deg)")
    ax.set_ylabel("GZ relative error vs exact (%)")
    ax.set_title("Platform GZ error grows with heel: sin^2(phi) used instead of tan^2(phi)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    f2 = path / "fig2_gz_error.png"
    fig.savefig(f2)
    plt.close(fig)
    made.append(str(f2.relative_to(ROOT)))

    # fig3 KB/T 方法族
    cbs = [0.50 + 0.005 * i for i in range(101)]
    T = 10.0
    fig, ax = plt.subplots(figsize=(7.4, 4.4), dpi=140)
    ax.plot(cbs, [ref.platform_kb(T, c) / T for c in cbs], "r-", lw=2.2,
            label="platform serving: KB = T(1 - Cb/3)")
    ax.plot(cbs, [ref.morrish_kb(T, c, (1 + 2 * c) / 3) / T for c in cbs], "b-", lw=2.2,
            label="Morrish: T(5/6 - Cb/(3Cw)), Cw=(1+2Cb)/3")
    ax.plot(cbs, [ref.section_type_kb(T, 0.98) / T for _ in cbs], "g--", lw=1.6,
            label="midship form: T(0.53+0.085(1-Cm)), Cm=0.98")
    ax.axhline(0.5, color="k", ls=":", lw=1, label="exact KB/T = 0.5 (rectangular Cb=1)")
    ax.set_xlabel("block coefficient Cb")
    ax.set_ylabel("KB / T")
    ax.set_title("KB formula family: serving formula above both independent empirical forms")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    f3 = path / "fig3_kb_methods.png"
    fig.savefig(f3)
    plt.close(fig)
    made.append(str(f3.relative_to(ROOT)))

    # fig4 逐量判定条图
    items = [c for c in r0["checks"]
             if c["quantity"] in ("KB", "BM", "GM", "GZ@10°", "GZ@20°", "GZ@30°",
                                  "GZ@40°", "Area_0_40", "Area_30_40", "GZ_max")]
    fig, ax = plt.subplots(figsize=(8.2, 4.4), dpi=140)
    labels = [c["quantity"] for c in items]
    vals = [c["rel_error_pct"] for c in items]
    colors = ["#c62828" if abs(v) > 15 else ("#ef6c00" if abs(v) > 3 else "#2e7d32")
              for v in vals]
    ax.bar(labels, vals, color=colors)
    for y, c, ls in ((3, "green", "--"), (-3, "green", "--"), (15, "red", ":")):
        ax.axhline(y, color=c, ls=ls, lw=1)
    ax.set_ylabel("relative error vs exact (%)")
    ax.set_title("BOX-A per-quantity verdict (red >15% / orange 3-15% / green <3%)")
    for i, v in enumerate(vals):
        ax.text(i, v + (1.5 if v >= 0 else -4.5), f"{v:.1f}%", ha="center", fontsize=7.5)
    ax.tick_params(axis="x", labelrotation=25, labelsize=8)
    ax.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    f4 = path / "fig4_rating_errors.png"
    fig.savefig(f4)
    plt.close(fig)
    made.append(str(f4.relative_to(ROOT)))
    return made


# ---------------------------------------------------------------------------
def selfcheck() -> dict:
    b = ref.BoxBarge(L=100.0, B=20.0, T=8.0, D=25.0, KG=6.0)
    limit = ref.box_valid_heel_limit_deg(b)
    gap_ws, gap_poly, area_dev = [], [], []
    for a in (5, 10, 15, 20, 25, 30, 35):
        if a > limit:
            continue
        g = ref.heeled_box_gz(b, a)
        gap_ws.append(abs(ref.wall_sided_gz(a, b.gm, b.bm) - g))
        gp = abs(ref.polygon_gz(b, a) - g)
        gap_poly.append(gp)
    for a in (0, 10, 20, 30, 40, 50, 59):
        area, _, _ = ref.heeled_box_centroid(b, a)
        area_dev.append(abs(area / (b.B * b.T) - 1.0))
    return {
        "reference_paths": ["wall-sided 闭式解", "剖面矩 Simpson 积分", "多边形裁剪几何"],
        "wall_sided_vs_general_max_gap_m": float(f"{max(gap_ws):.3e}"),
        "polygon_vs_general_max_gap_m": float(f"{max(gap_poly):.3e}"),
        "max_volume_conservation_dev": float(f"{max(area_dev):.3e}"),
        "wall_sided_valid_limit_deg": round(limit, 4),
        "deck_immersion_deg": round(ref.full_submersion_angle_deg(b), 4),
        "box_exact": {"KB": round(b.kb, 6), "BM": round(b.bm, 6), "GM": round(b.gm, 6),
                      "displacement_t": round(b.displacement_t, 3)},
    }


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    online = not args.offline
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"稳性解析基准复核 | {'在线 API' if online else '离线复刻'} | {SHIP_API}")
    sc = selfcheck()
    print(f"  自检：wall-sided gap {sc['wall_sided_vs_general_max_gap_m']:.2e} m；"
          f"几何路径 gap {sc['polygon_vs_general_max_gap_m']:.2e} m；"
          f"体积守恒偏差 {sc['max_volume_conservation_dev']:.2e}")

    results = []
    for c in build_cases():
        print(f"  案例 {c['id']} ...")
        results.append(verify_case(c, online))
    figs = make_figures(results, out)

    all_checks = [(r["case"]["id"], ch) for r in results for ch in r["checks"]]
    counts = {k: sum(1 for _, ch in all_checks if ch["verdict"] == k)
              for k in ("PASS", "WARN", "FAIL")}
    payload = {
        "harness": "scripts/verify_stability_analytic.py",
        "reference_module": "tools/stability_bench/reference.py",
        "mode": "online" if online else "offline",
        "ship_api_base": SHIP_API,
        "thresholds": {"pass_pct": TOL_PASS * 100, "unacceptable_pct": TOL_FAIL * 100},
        "selfcheck": sc, "cases": results,
        "summary": {"n_checks": len(all_checks), **{f"n_{k.lower()}": v for k, v in counts.items()},
                    "fail_items": [{"case": cid, **ch} for cid, ch in all_checks
                                   if ch["verdict"] == "FAIL"]},
        "figures": figs,
    }
    (out / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n判定：PASS {counts['PASS']} / WARN {counts['WARN']} / FAIL {counts['FAIL']}")
    for cid, ch in all_checks:
        if ch["verdict"] != "PASS":
            ep = "  n/a  " if ch["rel_error_pct"] is None else f"{ch['rel_error_pct']:+.2f}%"
            print(f"  [{ch['verdict']:4s}] {cid:8s} {ch['quantity']:<16s} "
                  f"{ch['model']:>10.4f} vs {ch['reference']:>10.4f} ({ep})")
    print(f"\n产物：{out/'report.json'}")
    for f in figs:
        print(f"       {f}")
    return 0 if counts["FAIL"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())