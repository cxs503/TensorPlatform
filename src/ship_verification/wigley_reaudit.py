#!/usr/bin/env python3
"""Re-audit the ship platform against exact Wigley/pontoon references (m1813).

Pattern (same as ``tools/verifier/tp_ver_sea_man.py``): probe once -> freeze a
snapshot -> derive every verdict programmatically from the snapshot, so no number
in the ledger is ever hand-copied, and the whole ledger can be replayed offline
without touching the network.

What is checked, and what the reference is
------------------------------------------
GEO  geometry/buoyancy of the built-in parabolic hull
     exact: Cb = 4/9, nabla = 4 L B T / 9, wetted surface by spectral quadrature
RES  resistance prototype at Fn = 0.13 ... 0.29
     exact identities: Re = U L / nu, Fn = U / sqrt(g L), Rf + Rw = Rt,
     ITTC-57 friction law (implied wetted area must be speed independent),
     Froude invariance of the wave coefficient Cw (scale free),
     plus the Michell Cw(Fn) *shape* (absolute level is WITHHELD, see
     ``wigley_yardstick.normalization_span``).
STB  rectangular pontoon (Cb = Cm = 1)
     exact: KB = T/2, BM = B^2/(12T), KM = KB + BM, GM = KM - KG.

Usage
-----
    python3 -m src.ship_verification.wigley_reaudit            # live probe + write
    python3 -m src.ship_verification.wigley_reaudit --replay   # rebuild from snapshot
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import requests  # noqa: E402

if __package__ in (None, ""):  # allow `python3 path/to/file.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.ship_verification import wigley_yardstick as y  # noqa: E402
else:
    from . import wigley_yardstick as y

BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = (ROOT / "docs" / "verification_debt" / "wigley_michell" / "artifacts"
            / "reaudit_2026-11-02.json")
DOC = ROOT / "docs" / "verification" / "michell_wigley_yardstick_2026-11-02.md"
FIG = ROOT / "docs" / "verification" / "figs" / "michell_wigley_yardstick_2026-11-02.png"

L, B, T = y.STANDARD_L, y.STANDARD_B, y.STANDARD_T
KNOTS = [4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
FN_GRID = [0.10, 0.13, 0.16, 0.19, 0.22, 0.25, 0.28, 0.32]
PONT = {"L": 100.0, "B": 20.0, "T": 8.0, "D": 16.0, "Cb": 1.0, "Cm": 1.0}
PONT_KG = [3.0, 6.0, 9.0]


# ---------------------------------------------------------------------------
# probing (network side: kept dumb, no arithmetic)
# ---------------------------------------------------------------------------
def _post(path, body, timeout=30):
    try:
        r = requests.post(f"{BASE}/{path}", json=body, timeout=timeout)
        return r.status_code, (r.json() if "json" in r.headers.get("content-type", "") else r.text[:400])
    except Exception as exc:  # pragma: no cover
        return -1, repr(exc)


def _get(path, timeout=30):
    try:
        r = requests.get(f"{BASE}/{path}", timeout=timeout)
        return r.status_code, (r.json() if "json" in r.headers.get("content-type", "") else r.text[:400])
    except Exception as exc:  # pragma: no cover
        return -1, repr(exc)


def probe() -> dict:
    snap = {
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "api_base": BASE,
        "reference": {
            "wigley": {"L": L, "B": B, "T": T, "Cb_exact": y.exact_wigley_cb(),
                       "nabla_exact_m3": y.exact_wigley_volume(L, B, T),
                       "wetted_surface_exact_m2": y.exact_wigley_wetted_surface(L, B, T)},
            "pontoon": {**PONT, "KG_list": PONT_KG,
                        "KM_exact": y.pontoon_hydrostatics(PONT["B"], PONT["T"], 0.0)["KM"],
                        "wall_sided_valid_deg": y.pontoon_wall_sided_valid_deg(PONT["B"], PONT["T"])},
            "michell_curve": y.michell_cw_curve(FN_GRID),
            "michell_route_cross_check": [
                {"Fn": fn, "route_a_N": y.rw_route_a(fn * math.sqrt(y.G * L)),
                 "route_b_N": y.rw_route_b(fn * math.sqrt(y.G * L))}
                for fn in (0.15, 0.25)
            ],
            "normalization": y.normalization_span(),
            "rho": y.RHO, "nu": y.NU, "knot": y.KNOT, "g": y.G,
        },
        "platform": {},
    }
    code, data = _get("geometry/hydro-at/wigley")
    snap["platform"]["geometry"] = {"status": code, "data": data}

    disp_t = y.exact_wigley_volume(L, B, T) * y.RHO / 1000.0
    code, data = _post("resistance/predict", {
        "Lwl": L, "Lpp": L, "B": B, "T": T, "displacement": disp_t,
        "Cb": y.exact_wigley_cb(), "Cm": 2.0 / 3.0, "Cwp": 2.0 / 3.0, "speeds": KNOTS})
    snap["platform"]["resistance_full"] = {"status": code, "data": data}

    U_half = 0.5 * KNOTS[4] * y.KNOT  # Fn fixed, all linear dims halved
    fn_probe = U_half / math.sqrt(y.G * L)
    code, data = _post("resistance/predict", {
        "Lwl": 0.5 * L, "Lpp": 0.5 * L, "B": 0.5 * B, "T": 0.5 * T,
        "displacement": y.exact_wigley_volume(0.5 * L, 0.5 * B, 0.5 * T) * y.RHO / 1000.0,
        "Cb": y.exact_wigley_cb(), "Cm": 2.0 / 3.0, "Cwp": 2.0 / 3.0,
        "speeds": [KNOTS[4]]})
    snap["platform"]["resistance_half"] = {"status": code, "data": data, "Fn": fn_probe}

    snap["platform"]["stability"] = []
    for kg in PONT_KG:
        code, data = _post("stability/evaluate", {
            **PONT, "displacement": PONT["L"] * PONT["B"] * PONT["T"] * y.RHO / 1000.0,
            "KG": kg})
        snap["platform"]["stability"].append({"KG": kg, "status": code, "data": data})
    return snap


# ---------------------------------------------------------------------------
# verdict derivation (pure function of the snapshot, no network)
# ---------------------------------------------------------------------------
def _first(row, *keys):
    for k in keys:
        if isinstance(row, dict) and row.get(k) is not None:
            return row[k]
    return None


def identity_check(name: str, err, tol: float, *, basis: str = "", note: str = "") -> dict:
    """Verdict for an exact identity, where ``err`` is already a relative error."""
    rec = {"id": name, "measured": None, "reference": 0.0, "tol": tol,
           "basis": basis, "note": note}
    if err is None:
        rec.update(rel_err=None, status="ERROR")
        return rec
    e = float(err)
    if not math.isfinite(e):
        rec.update(rel_err=e, status="FAIL")
        return rec
    rec.update(rel_err=e, status="PASS" if e <= tol else "FAIL")
    return rec


def verdicts_from_snapshot(snap: dict) -> dict:
    ref = snap["reference"]
    geo_ref = ref["wigley"]
    out = {"GEO": [], "RES": [], "STB": []}

    # --- GEO -------------------------------------------------------------
    g = snap["platform"].get("geometry", {})
    gd = g.get("data") if isinstance(g.get("data"), dict) else {}
    hyd = gd.get("hydrostatics") or {}
    meta = gd.get("meta") or {}
    out["GEO"].append(y.verdict(
        "GEO-1 Cb of the parabolic hull", hyd.get("Cb"), geo_ref["Cb_exact"], 0.02,
        basis="exact Cb = 4/9 for y = B/2 (1-(2x/L)^2)(1-(z/T)^2)"))
    meta_vol = (y.exact_wigley_volume(meta["L"], meta["B"], meta["T"])
                if all(k in meta for k in ("L", "B", "T")) else geo_ref["nabla_exact_m3"])
    out["GEO"].append(y.verdict(
        "GEO-2 displaced volume [m^3]", hyd.get("displacement_volume"), meta_vol, 0.02,
        basis="exact nabla = 4 L B T / 9 on the dimensions the endpoint itself reports",
        note=f"endpoint meta dims = {meta}"))
    meta_s = (y.exact_wigley_wetted_surface(meta["L"], meta["B"], meta["T"])
              if all(k in meta for k in ("L", "B", "T")) else geo_ref["wetted_surface_exact_m2"])
    out["GEO"].append(y.verdict(
        "GEO-3 wetted surface [m^2]", hyd.get("wet_surface"), meta_s, 0.10,
        basis="spectrally converged quadrature of the exact Wigley surface"))
    out["GEO"].append(y.verdict(
        "GEO-4 dimensional fidelity (L returned / L asked)", meta.get("L"), L, 0.01,
        basis="requested model L must survive the hydrostatics call",
        note="unit-hull rescaling is itself a defect if dims are silently changed"))

    # --- RES -------------------------------------------------------------
    full = snap["platform"].get("resistance_full", {})
    rows = full.get("data") if isinstance(full.get("data"), list) else []
    s_exact = geo_ref["wetted_surface_exact_m2"]
    nu, rho, knot, gacc = ref["nu"], ref["rho"], ref["knot"], ref["g"]

    re_err, fn_err, clo_err, s_list, kt_list = [], [], [], [], []
    for row in rows:
        U = float(row["speed"]) * knot
        re_an = U * L / nu
        fn_an = U / math.sqrt(gacc * L)
        re_err.append(abs(float(row["reynolds_number"]) - re_an) / re_an)
        fn_err.append(abs(float(row["froude_number"]) - fn_an) / fn_an)
        Rf, Rw, Rt = (float(row["friction_resistance"]) * 1e3,
                      float(row["wave_resistance"]) * 1e3,
                      float(row["total_resistance"]) * 1e3)
        clo_err.append(abs(Rf + Rw - Rt) / max(abs(Rt), 1e-12))
        s_list.append(y.implied_wetted_area(Rf, U, float(row["reynolds_number"]), rho))
        kt_list.append({"speed_kn": row["speed"], "U_ms": U, "Fn": fn_an,
                        "Rw_N": Rw, "Rf_N": Rf, "Rt_N": Rt,
                        "cw": Rw / (0.5 * rho * s_exact * U * U),
                        "s_implied": s_list[-1],
                        "rw_ref_N": None, "ratio_rw": None})
    if kt_list:
        s_mean = sum(s_list) / len(s_list)
        s_scatter = max(abs(s - s_mean) for s in s_list) / s_mean
        for rec in kt_list:
            ref_row = min(ref["michell_curve"], key=lambda r: abs(r["Fn"] - rec["Fn"]))
            rec["rw_ref_N"] = ref_row["Rw_ref_N"]
            rec["ratio_rw"] = rec["Rw_N"] / ref_row["Rw_ref_N"]
    out["RES"].append(identity_check("RES-1 Reynolds number identity",
                                     max(re_err) if re_err else None, 0.005,
                                     basis="Re = U L / nu (exact)", note="max over speed sweep"))
    out["RES"].append(identity_check("RES-2 Froude number identity",
                                     max(fn_err) if fn_err else None, 0.005,
                                     basis="Fn = U / sqrt(g L) (exact)", note="max over speed sweep"))
    out["RES"].append(identity_check("RES-3 closure Rf + Rw = Rt",
                                     max(clo_err) if clo_err else None, 0.01,
                                     basis="exact identity", note="max over speed sweep"))
    if kt_list:
        s_mean = sum(s_list) / len(s_list)
        out["RES"].append(identity_check(
            "RES-4 implied wetted area is speed independent", s_scatter, 0.02,
            basis="ITTC-57 Cf(Re) is exact, so Rf/(0.5 rho Cf U^2) = S = const",
            note=f"max |S - mean| / mean, mean S = {s_mean:.1f} m^2"))
        out["RES"].append(y.verdict(
            "RES-5 implied wetted area vs exact Wigley S", s_mean, s_exact, 0.15,
            basis="exact spectral quadrature of the Wigley surface (15% platform standard)"))
    else:
        for name in ("RES-4 implied wetted area is speed independent",
                     "RES-5 implied wetted area vs exact Wigley S"):
            out["RES"].append({"id": name, "measured": None, "reference": None,
                               "tol": None, "basis": "", "note": "no rows returned",
                               "rel_err": None, "status": "ERROR"})

    # Froude invariance: full scale vs half scale at identical Fn
    half = snap["platform"].get("resistance_half", {})
    hrows = half.get("data") if isinstance(half.get("data"), list) else []
    if kt_list and hrows:
        Uh = float(hrows[0]["speed"]) * knot
        fn_h = Uh / math.sqrt(gacc * (0.5 * L))
        cw_h = float(hrows[0]["wave_resistance"]) * 1e3 / (
            0.5 * rho * y.exact_wigley_wetted_surface(0.5 * L, 0.5 * B, 0.5 * T) * Uh * Uh)
        full_row = min(kt_list, key=lambda r: abs(r["Fn"] - fn_h))
        out["RES"].append(y.verdict(
            "RES-6 Froude invariance of Cw (full vs half scale)", cw_h, full_row["cw"], 0.05,
            basis="Cw = Rw/(0.5 rho S U^2) must depend on Fn only (geometric similarity)",
            note=f"half-scale Fn = {fn_h:.4f} matched to full-scale Fn = {full_row['Fn']:.4f}"))
    else:
        out["RES"].append({"id": "RES-6 Froude invariance of Cw (full vs half scale)",
                           "measured": None, "reference": None, "tol": 0.05, "basis": "",
                           "note": "no half-scale row", "rel_err": None, "status": "ERROR"})

    ratios = [r["ratio_rw"] for r in kt_list if r["ratio_rw"]]
    out["RES"].append({
        "id": "RES-7 Michell Cw(Fn) absolute level", "measured": None, "reference": None,
        "tol": None, "basis": "Michell thin-ship integral (routes A/B agreed)",
        "note": ("absolute prefactor convention unresolved -> WITHHELD; "
                 f"ratio Rw_platform/Rw_Michell = {min(ratios):.2f}..{max(ratios):.2f}"
                 if ratios else "no sweep"),
        "rel_err": None, "status": "WITHHELD",
        "extra": {"ratio_min": min(ratios) if ratios else None,
                  "ratio_max": max(ratios) if ratios else None,
                  "ratio_mean": (sum(ratios) / len(ratios)) if ratios else None}})

    # --- STB -------------------------------------------------------------
    for item in snap["platform"].get("stability", []):
        kg = item["KG"]
        d = item["data"] if isinstance(item["data"], dict) else {}
        h = y.pontoon_hydrostatics(PONT["B"], PONT["T"], kg)
        gm = d.get("GM")
        km = (float(gm) + kg) if gm is not None else None
        out["STB"].append(y.verdict(
            f"STB-KM KG={kg}", km, h["KM"], 0.02,
            basis="exact pontoon KM = T/2 + B^2/(12T)", note="KM rebuilt as GM + KG"))
        out["STB"].append(y.verdict(
            f"STB-GM KG={kg}", gm, h["GM"], 0.05,
            basis="exact pontoon GM = KM - KG"))
        phi = d.get("GZ_angle")
        if phi is not None and phi <= ref["pontoon"]["wall_sided_valid_deg"] + 1e-9:
            out["STB"].append(y.verdict(
                f"STB-GZmax KG={kg}", d.get("GZ_max"),
                y.pontoon_wall_sided_gz(PONT["B"], PONT["T"], kg, phi), 0.05,
                basis="wall-sided GZ(phi) = sin(phi)(GM + BM/2 tan^2 phi)"))
        else:
            out["STB"].append({
                "id": f"STB-GZmax KG={kg}", "measured": d.get("GZ_max"),
                "reference": None, "tol": None,
                "basis": "wall-sided GZ valid only below the deck-edge dip",
                "note": (f"platform reports GZ_max at {phi} deg > validity limit "
                         f"{ref['pontoon']['wall_sided_valid_deg']:.2f} deg -> not comparable"),
                "rel_err": None, "status": "WITHHELD"})
    return out


# ---------------------------------------------------------------------------
# outputs
# ---------------------------------------------------------------------------
def _fmt(v, nd=4):
    if v is None:
        return "—"
    if isinstance(v, str):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if not math.isfinite(f):
        return "∞"
    return f"{f:.{nd}g}"


def make_figure(snap: dict, verd: dict) -> None:
    FIG.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(1, 2, figsize=(13.0, 4.8))

    ref = snap["reference"]
    fns = [r["Fn"] for r in ref["michell_curve"]]
    cws = [r["cw_ref"] for r in ref["michell_curve"]]
    ax[0].semilogy(fns, cws, "k-", lw=2, label="Michell thin-ship reference (Fn-shape)")

    rows = snap["platform"].get("resistance_full", {}).get("data")
    if isinstance(rows, list) and rows:
        nu, knot, gacc = ref["nu"], ref["knot"], ref["g"]
        s_ex = ref["wigley"]["wetted_surface_exact_m2"]
        pf = [(float(r["speed"]) * knot / math.sqrt(gacc * L),
               float(r["wave_resistance"]) * 1e3 / (
                   0.5 * ref["rho"] * s_ex * (float(r["speed"]) * knot) ** 2)) for r in rows]
        ax[0].semilogy([p[0] for p in pf], [p[1] for p in pf], "o-", ms=5,
                       label="platform, full scale (L=100 m)")
    hide = snap["platform"].get("resistance_half", {})
    if isinstance(hide.get("data"), list) and hide["data"]:
        U = float(hide["data"][0]["speed"]) * ref["knot"]
        cw = float(hide["data"][0]["wave_resistance"]) * 1e3 / (
            0.5 * ref["rho"] * y.exact_wigley_wetted_surface(0.5 * L, 0.5 * B, 0.5 * T) * U * U)
        fn = U / math.sqrt(ref["g"] * 0.5 * L)
        ax[0].semilogy([fn], [cw], "s", ms=9, mfc="none", mew=2,
                       label="platform, half scale (same Fn)")
        ax[0].annotate("same Fn(=%.3f)\nmust coincide" % fn, xy=(fn, cw),
                       xytext=(fn + 0.012, cw * 1.6), fontsize=8,
                       arrowprops=dict(arrowstyle="->", lw=0.8))
    ax[0].set_xlabel("Froude number  Fn")
    ax[0].set_ylabel(r"$C_w = R_w/(\frac{1}{2}\rho S U^2)$")
    ax[0].set_title("Wave-resistance coefficient: reference shape vs platform")
    ax[0].grid(alpha=0.3, which="both")
    ax[0].legend(fontsize=8, loc="lower right")

    labels, vals, colors = [], [], []
    for key in ("GEO", "RES", "STB"):
        for v in verd[key]:
            if v.get("rel_err") is None or not math.isfinite(v["rel_err"]):
                continue
            labels.append(v["id"].replace(" of the parabolic hull", "").replace(" KG=", " KG"))
            vals.append(max(v["rel_err"], 1e-6) * 100.0)
            colors.append({"PASS": "#2e7d32", "FAIL": "#c62828"}.get(v["status"], "#888"))
    xs = range(len(labels))
    ax[1].bar(xs, vals, color=colors)
    ax[1].set_yscale("log")
    ax[1].axhline(5.0, ls="--", lw=1, color="#555")
    ax[1].axhline(15.0, ls="-", lw=1, color="#b71c1c")
    ax[1].text(len(labels) - 0.4, 15.5, "15% unacceptable", ha="right", fontsize=7, color="#b71c1c")
    ax[1].set_xticks(list(xs))
    ax[1].set_xticklabels(labels, rotation=62, ha="right", fontsize=6.5)
    ax[1].set_ylabel("relative error vs exact reference [%]")
    ax[1].set_title("Exact-reference deviations (log scale)")
    ax[1].grid(alpha=0.3, axis="y", which="both")
    fig.tight_layout()
    fig.savefig(FIG, dpi=150)


def write_markdown(snap: dict, verd: dict) -> None:
    ref = snap["reference"]
    n_pass = sum(1 for k in verd for v in verd[k] if v["status"] == "PASS")
    n_fail = sum(1 for k in verd for v in verd[k] if v["status"] == "FAIL")
    n_with = sum(1 for k in verd for v in verd[k] if v["status"] == "WITHHELD")
    n_err = sum(1 for k in verd for v in verd[k] if v["status"] == "ERROR")

    lines = []
    A = lines.append
    A("# Michell/Wigley 基准尺修复 + 平台对精确解析基准复核（m1813）")
    A("")
    A(f"> 生成时间（UTC）：{snap['generated_utc']} ｜ 探针 API：`{snap['api_base']}`")
    A(f"> 工件：快照 `{SNAPSHOT.relative_to(ROOT)}`；图 `{FIG.relative_to(ROOT)}`")
    A("> 复算：`python3 -m src.ship_verification.wigley_reaudit --replay`（离线，不碰网络）")
    A("> 判决：PASS %d / FAIL %d / WITHHELD %d / ERROR %d" % (n_pass, n_fail, n_with, n_err))
    A("")
    A("## ① 案例介绍")
    A("")
    A("薄船理论（Michell 1898）的 Wigley 抛物线船型是船舶阻力领域唯一的**闭式可积**")
    A("基准：半宽 $y=\\frac{B}{2}\\left(1-(2x/L)^2\\right)\\left(1-(z/T)^2\\right)$，"
      "排水体积 $=4LBT/9$、$C_b=4/9$ 均为精确闭式，因此兴波阻力可以脱离任何经验回归独立复算。")
    A("")
    A("| 参数 | 值 | 精确参考 |")
    A("|---|---|---|")
    A(f"| L / B / T | {L} / {B} / {T} m（L/B=10, B/T=1.6） | — |")
    A(f"| Cb | 由 $\\nabla/(LBT)$ 反算 | **4/9 = {ref['wigley']['Cb_exact']:.6f}** |")
    A(f"| 排水体积 | — | **{ref['wigley']['nabla_exact_m3']:.4f} m³** |")
    A(f"| 湿表面积 | — | **{ref['wigley']['wetted_surface_exact_m2']:.3f} m²**（谱收敛求积）|")
    A(f"| 浮箱稳性算例 | L={PONT['L']}, B={PONT['B']}, T={PONT['T']}, Cb=Cm=1 | "
      f"KM = T/2 + B²/(12T) = {ref['pontoon']['KM_exact']:.4f} m |")
    A("")
    A("## ② 怎么做的")
    A("")
    A("1. **先修基准尺，再判平台。** 仓内唯一解析兴波基准（m1568 的 "
      "`docs/verification_debt/wigley_michell`）的第二条独立路线 `rw_double_quad` 从未被测试门")
    A("   覆盖，实测为坏：复振幅被 `float()` 强转丢掉虚部（`ComplexWarning`），而 $x$ 向积分核关于 $x$ 反对称，")
    A("   实部完全抵消 ⇒ 返回 $R_w\\sim10^{-28}\\,\\mathrm{N}$；另有一处多余的镜像因子 2（$\\Rightarrow 4\\times$）")
    A("   与一行算完即弃的 `phase = ...; del phase` 占位代码。**故 m1568 台账所依赖的“双路线互检”是空的。**")
    A("2. **修复 + 双路线互检**：路线 A（内层闭式积分 + θ 人工求积）与路线 B（二维面求积、复振幅累加、")
    A("   单侧半宽幅值与 prefactor 口径统一）现给出一致结果（下表）。")
    A("3. **加两条尺子无法自证的精确结构律**（与 prefactor 常数无关，故不受归一化争议影响）：")
    A("   $R_w\\propto B^2$（固定 $L,T,Fn$）与 Froude 缩放 $R_w(\\lambda L,\\lambda B,\\lambda T)=\\lambda^3 R_w$。")
    A("4. **一次探针 → 冻结快照 → 全部判决由快照程序化重算**（`--replay`），台账里没有手抄数字；")
    A("   判据只用精确闭式或精确恒等式，不使用任何经验回归当参考。")
    A("")
    A("### 基准尺自检（本次修复后）")
    A("")
    A("| Fn | 路线 A $R_w$ [N] | 路线 B $R_w$ [N] | 相对差 |")
    A("|---|---|---|---|")
    for r in ref["michell_route_cross_check"]:
        d = abs(r["route_b_N"] - r["route_a_N"]) / max(abs(r["route_a_N"]), 1e-300)
        A(f"| {r['Fn']:.2f} | {r['route_a_N']:.6e} | {r['route_b_N']:.6e} | {d*100:.2e}% |")
    A("")
    A("θ 求积加密（600→1200）相对增量最大 "
      f"{max(r['theta_refine_rel'] for r in ref['michell_curve'])*100:.2e}%，"
      "即参考曲线本身已达求积极限。")
    A("")
    A("### 归一化诚实声明")
    A(f"- 代码 prefactor = 8ρg²/(πU²)（单侧半宽幅值）；经典写法 4ρg²/(πU²)。"
      f"**绝对量级存在 2 倍口径不确定性（{ref['normalization']['evidence']}）**，")
    A("  离线无可引用的绝对 Wigley $R_w$ 表可裁；故本单对平台兴波阻力只做**无量纲/标度型判决**")
    A("  （RES-6 Froude 不变性、RES-7 比值区间），不宣称绝对 $C_w$ 精度。")
    A("")
    A("## ③ 计算结果（判决台账，全部由快照程序化注入）")
    A("")
    for key, title in (("GEO", "几何/浮性（精确闭式）"), ("RES", "阻力原型（精确恒等式 + 标度律）"),
                       ("STB", "浮箱稳性（精确解析）")):
        A(f"### {title}")
        A("")
        A("| 编号 | 判据 | 参考值 | 实测值 | 相对误差 | 容差 | 判决 | 证据 |")
        A("|---|---|---|---|---|---|---|---|")
        for v in verd[key]:
            ev = "REAL" if v["status"] == "PASS" and v.get("rel_err") is not None else (
                "WITHHELD" if v["status"] == "WITHHELD" else
                "REAL(参考)/FAIL" if v["status"] == "FAIL" else "—")
            A(f"| {v['id']} | {v.get('basis','')} | {_fmt(v.get('reference'))} | "
              f"{_fmt(v.get('measured'))} | "
              f"{('%.3g%%' % (v['rel_err']*100)) if v.get('rel_err') is not None and math.isfinite(v['rel_err']) else _fmt(v.get('rel_err'))} | "
              f"{('%g%%' % (v['tol']*100)) if v.get('tol') is not None else '—'} | "
              f"**{v['status']}** | {ev} |")
        A("")
        for v in verd[key]:
            if v.get("note"):
                A(f"- `{v['id']}`：{v['note']}")
        A("")
    A("### 平台兴波阻力 vs 参考（比值，非绝对）")
    A("")
    A("| 航速 [kn] | Fn | $R_w$ 平台 [N] | $R_w$ 参考 [N] | 比值 |")
    A("|---|---|---|---|---|")
    for rec in snap.get("_res_rows", []):
        A(f"| {rec['speed_kn']} | {rec['Fn']:.4f} | {rec['Rw_N']:.4g} | "
          f"{rec['rw_ref_N']:.4g} | {rec['ratio_rw']:.3f} |")
    A("")
    A("## ④ 结果图")
    A("")
    A(f"![yardstick]({FIG.relative_to(ROOT / 'docs' / 'verification')})")
    A("")
    A("左：Michell 参考 $C_w(Fn)$ 形状（黑）与平台 $C_w$（全尺度圆点、半尺度空心方块——同一 Fn 必须重合）；")
    A("右：各精确判据的相对误差（对数轴，5%/15% 两条阈值线）。")
    A("")
    A("## ⑤ 注意点与局限")
    A("")
    A("1. **m1568 台账的“双路线互检”结论不可信**：第二条路线当时返回 $\\sim10^{-28}$ N。已修复并被测试钉住；")
    A("   若有人引用 m1568 的“两条独立路线一致”作为证据，应改引本文件。")
    A("2. **绝对归一化未裁**（见上）：任何以本参考做绝对 $C_w$ 对比的下游，误差界为 $\\pm$ 2 倍口径。")
    A("3. **Wigley 是薄船算例**：$B/L=0.1$、$T/L=0.0625$，Michell 一阶薄船理论适用；实船（$B/L\\sim0.15$）误差会更大，")
    A("   且本理论不含破波/粘性/自由面非线性。")
    A("4. **浮箱 GZ 比较只在甲板边缘入水角以内**成立（本算例 "
      f"{ref['pontoon']['wall_sided_valid_deg']:.2f}°）；平台在 45° 报告 GZ_max 已越界，故记 WITHHELD。")
    A("5. 平台侧只做**读**（探针），本单未改任何船舶生产代码；修复只落在基准尺（验证侧）。")
    A("")
    A("## ⑥ 误差判定（是否 <3%）")
    A("")
    A("| 关键量 | 基准 | 相对误差 | <3% ? |")
    A("|---|---|---|---|")
    for key in ("GEO", "RES", "STB"):
        for v in verd[key]:
            if v.get("rel_err") is None:
                continue
            re = v["rel_err"]
            ans = "是" if (math.isfinite(re) and re < 0.03) else "**否**"
            A(f"| {v['id']} | {v.get('basis','')} | "
              f"{('%.3g%%' % (re*100)) if math.isfinite(re) else '∞'} | {ans} |")
    A("")
    A("**RES-7（兴波阻力绝对量级）无可用基准**：绝对 prefactor 口径离线不可裁，故记 WITHHELD，")
    A("不宣称任何 $<3\\%$ 结论；RES-6（Froude 不变性）是无量纲判据，其误差即表中数值。")
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--replay", action="store_true", help="rebuild verdicts from the frozen snapshot")
    ap.add_argument("--snapshot", default=str(SNAPSHOT))
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)

    if args.replay:
        snap = json.loads(Path(args.snapshot).read_text())
    else:
        snap = probe()
        Path(args.snapshot).parent.mkdir(parents=True, exist_ok=True)
        Path(args.snapshot).write_text(json.dumps(snap, indent=1, ensure_ascii=False))

    verd = verdicts_from_snapshot(snap)
    # expose the flat sweep table for the markdown (kept out of the snapshot)
    rows = snap["platform"].get("resistance_full", {}).get("data")
    if isinstance(rows, list) and rows:
        sweep = []
        for r in rows:
            U = float(r["speed"]) * snap["reference"]["knot"]
            fn = U / math.sqrt(snap["reference"]["g"] * L)
            ref_row = min(snap["reference"]["michell_curve"], key=lambda q: abs(q["Fn"] - fn))
            rw = float(r["wave_resistance"]) * 1e3
            sweep.append({"speed_kn": r["speed"], "Fn": fn, "Rw_N": rw,
                          "rw_ref_N": ref_row["Rw_ref_N"], "ratio_rw": rw / ref_row["Rw_ref_N"]})
        snap["_res_rows"] = sweep

    counts = {s: sum(1 for k in verd for v in verd[k] if v["status"] == s)
              for s in ("PASS", "FAIL", "WITHHELD", "ERROR")}
    print("verdicts:", counts)
    for k in ("GEO", "RES", "STB"):
        for v in verd[k]:
            re = v.get("rel_err")
            print(f"  {v['status']:<8} {v['id']:<52} "
                  f"{'—' if re is None else ('%.3g%%' % (re*100) if math.isfinite(re) else 'inf')}")
    if not args.no_write:
        make_figure(snap, verd)
        write_markdown(snap, verd)
        print("wrote", FIG.relative_to(ROOT), "and", DOC.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())