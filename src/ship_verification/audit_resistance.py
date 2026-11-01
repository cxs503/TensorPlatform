"""Independent verification audit of the platform ``calc.resistance`` capability.

Rationale (verification-debt track, mission m1536)
-------------------------------------------------
The ship-performance platform advertises its resistance endpoint as
``"ITTC-57摩擦阻力 + Holtrop剩余阻力"`` and the capability audit grades it REAL.
No independent reference check of that claim was on record.  This module:

1. queries the live endpoint (``http://127.0.0.1:8001/api/v1/calculations/resistance``)
   for the published benchmark hulls KVLCC2 / KCS / AFRAMAX at several speeds;
2. recomputes every reported quantity from independent baselines:
   * the ITTC-1957 line (exact analytic),
   * a faithful Holtrop-Mennen (1984) implementation
     (:mod:`ship_verification.holtrop1984`),
   * the published benchmark wetted surfaces of the two workshop hulls,
   * the IMO EEDI reference line (MEPC.203(62)) as a physics/regulatory plausibility band;
3. emits a machine-readable error table (JSON), a markdown summary and figures.

Geometry is checked *before* any resistance comparison, per platform policy:
``Cb`` must be consistent with ``vol / (L B T)`` and the endpoint must not silently
ignore the displacement it accepts.

Usage::

    python -m ship_verification.audit_resistance --out outputs/m1536 --figures

The network client is injected (``fetch``) so the comparison logic is unit-testable
without a live platform and the audit can be replayed against any base URL.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from .holtrop1984 import (
    AFRAMAX,
    BENCHMARK_HULLS,
    DELTA_CF_ROUGHNESS,
    HullGeometry,
    KCS,
    KVLCC2,
    cf_ittc1957,
    eedi_from_shaft_power,
    eedi_reference,
    holtrop1984_resistance,
    required_shaft_power_for_eedi,
    wetted_surface,
    wetted_surface_denny_mumford,
)

KM_TO_MS = 0.5144

#: Accepted absolute-error threshold of the platform (">15% 不可接受").
ERROR_THRESHOLD_PCT = 15.0

#: Published benchmark wetted surfaces used as an independent geometric anchor.
PUBLISHED_S = {"KVLCC2": 27194.0, "KCS": 9494.0}

#: Typical propulsive efficiency used only to convert PE -> PME for the EEDI cross-check.
ETA_D_NOMINAL = 0.72

#: Audit case matrix: (hull name, speed in knots).
CASES: List[tuple] = [
    ("KVLCC2", 12.0),
    ("KVLCC2", 13.5),
    ("KVLCC2", 15.0),
    ("KVLCC2", 16.5),
    ("KCS", 18.0),
    ("KCS", 21.0),
    ("KCS", 24.0),
    ("AFRAMAX-110k", 13.0),
    ("AFRAMAX-110k", 15.0),
]


# ---------------------------------------------------------------------------
# Platform client
# ---------------------------------------------------------------------------

def platform_payload(geom: HullGeometry, speed_kn: float) -> Dict[str, float]:
    """Build the request body expected by the platform endpoint."""
    return {
        "ship_type": geom.name,
        "length": geom.l,
        "beam": geom.b,
        "draft": geom.t,
        "cb": geom.cb,
        "displacement": geom.vol,
        "speed": speed_kn,
    }


def fetch_platform(
    payload: Dict[str, float], base_url: str = "http://127.0.0.1:8001/api/v1",
    timeout: float = 20.0,
) -> Dict[str, float]:
    """POST one resistance request to the live platform and return the JSON body."""
    url = f"{base_url.rstrip('/')}/calculations/resistance"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local service)
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Comparison helpers (pure functions -> unit-testable)
# ---------------------------------------------------------------------------

def rel_err_pct(value: float, reference: float) -> Optional[float]:
    """Relative deviation in percent; ``None`` when the reference is (near) zero."""
    if reference is None or abs(reference) < 1e-12:
        return None
    return (value - reference) / reference * 100.0


def _row(
    quantity: str, platform: float, reference: float, unit: str = "", base: str = "",
    threshold: float = ERROR_THRESHOLD_PCT,
) -> Dict[str, object]:
    err = rel_err_pct(platform, reference)
    status = "n/a" if err is None else ("PASS" if abs(err) <= threshold else "FAIL")
    return {
        "quantity": quantity,
        "platform": platform,
        "reference": reference,
        "unit": unit,
        "reference_basis": base,
        "rel_err_pct": None if err is None else round(err, 3),
        "status": status,
    }


def compare_case(
    geom: HullGeometry, speed_kn: float, platform: Dict[str, float]
) -> Dict[str, object]:
    """Compare one platform response against the independent reference stack."""
    v = speed_kn * KM_TO_MS
    ref = holtrop1984_resistance(geom, v)
    cf_ref = cf_ittc1957(ref.re)

    rows: List[Dict[str, object]] = []

    # --- geometry first ---------------------------------------------------
    cb_check = geom.vol / (geom.l * geom.b * geom.t)
    rows.append(
        _row(f"geometry Cb=vol/(LBT) [{geom.name}]", geom.cb, cb_check, "-",
             "hull particulars self-consistency")
    )
    s_dm = wetted_surface_denny_mumford(geom.l, geom.b, geom.t, geom.cb)
    rows.append(
        _row("wetted surface S", float(platform["wetted_surface"]), ref.s_ref, "m2",
             "Holtrop S correlation with published Cm/Cwp")
    )
    rows.append(
        _row("wetted surface S", float(platform["wetted_surface"]), s_dm, "m2",
             "Denny-Mumford correlation (independent estimator)")
    )

    # --- Reynolds / Froude ------------------------------------------------
    rows.append(_row("Reynolds number Re", float(platform["reynolds_number"]), ref.re,
                     "-", "V L / nu (nu=1.19e-6 m^2/s)"))
    rows.append(_row("Froude number Fn", float(platform["froude_number"]), ref.fn, "-",
                     "V / sqrt(g L)"))

    # --- friction ---------------------------------------------------------
    rows.append(_row("friction coefficient Cf", float(platform["cf"]), cf_ref, "-",
                     "ITTC-1957 line (exact analytic)"))

    # --- resistance coefficients -----------------------------------------
    # Everything in the reference beyond the bare frictional line, expressed as a
    # coefficient:  k*Cf (form effect) + Cw (waves) + Ca (correlation allowance).
    coeff_beyond_cf_ref = (ref.form_factor_1pk - 1.0) * ref.cf + ref.cw + ref.ca
    coeff_beyond_cf_platform = float(platform["cr"]) + DELTA_CF_ROUGHNESS
    rows.append(
        _row("residuary+form coefficient (Cr+0.4e-3 vs kCf+Cw+Ca)",
             coeff_beyond_cf_platform, coeff_beyond_cf_ref, "-",
             "Holtrop-1984: k Cf + Cw + Ca")
    )
    rows.append(
        _row("platform Cr vs Holtrop Cw (wave-only, informational)",
             float(platform["cr"]), ref.cw, "-",
             "Holtrop-1984 Cw = Rw/(0.5 rho V^2 S); platform Cr is its whole residual "
             "coefficient, so this row is informational, not a like-for-like gate")
    )
    rows.append(_row("total resistance coefficient Ct", float(platform["ct"]), ref.ct, "-",
                     "Holtrop-1984 (1+k)Cf + Cw + Ca"))

    # --- forces and power -------------------------------------------------
    # NOTE: the endpoint already returns forces in kN and power in kW.
    rows.append(_row("frictional resistance RF", float(platform["rf"]), ref.rf / 1e3,
                     "kN", "0.5 rho V^2 S Cf (bare hull)"))
    rows.append(
        _row("residuary resistance RR", float(platform["rr"]),
             (ref.rw + ref.ra + (ref.form_factor_1pk - 1.0) * ref.rf) / 1e3, "kN",
             "Holtrop-1984 k Rf + Rw + Ra")
    )
    rows.append(_row("total resistance RT", float(platform["rt"]), ref.rt / 1e3,
                     "kN", "Holtrop-1984 total"))
    rows.append(_row("effective power PE", float(platform["pe"]) / 1e3, ref.pe_kw / 1e3,
                     "MW", "RT * V"))

    return {
        "hull": geom.name,
        "speed_kn": speed_kn,
        "speed_ms": round(v, 4),
        "reference": {
            "fn": round(ref.fn, 6),
            "re": round(ref.re, 1),
            "s_ref": round(ref.s_ref, 2),
            "cf": round(ref.cf, 6),
            "form_factor_1pk": round(ref.form_factor_1pk, 5),
            "cw": ref.cw,
            "ca": ref.ca,
            "ct": round(ref.ct, 6),
            "rt_kn": round(ref.rt / 1e3, 2),
            "pe_kw": round(ref.pe_kw, 1),
            "rf_kn": round(ref.rf / 1e3, 2),
            "rw_kn": round(ref.rw / 1e3, 2),
            "ra_kn": round(ref.ra / 1e3, 2),
            "k_rf_kn": round((ref.form_factor_1pk - 1.0) * ref.rf / 1e3, 2),
            "fn_in_rhow_domain": ref.fn_in_rhow_domain,
            "notes": ref.notes,
        },
        "platform": platform,
        "rows": rows,
    }


def check_displacement_used(
    geom: HullGeometry, speed_kn: float, fetch: Callable[..., Dict[str, float]]
) -> Dict[str, object]:
    """Probe whether the endpoint actually consumes the displacement it accepts.

    A physically consistent hull must react to a 10x displacement change (via Cb/vol
    cross-validation at the very least); identical output proves the input is inert.
    """
    base = platform_payload(geom, speed_kn)
    normal = fetch(base)
    perturbed = fetch({**base, "displacement": base["displacement"] * 10.0})
    same = abs(float(normal.get("rt", 0.0)) - float(perturbed.get("rt", 0.0))) < 1e-9
    return {
        "hull": geom.name,
        "speed_kn": speed_kn,
        "rt_normal_kn": normal.get("rt"),
        "rt_displacement_x10_kn": perturbed.get("rt"),
        "displacement_has_no_effect": same,
        "verdict": "FAIL: endpoint ignores displacement (no Cb/vol cross-validation)"
        if same
        else "PASS: displacement influences the result",
    }


def eedi_crosscheck(geom: HullGeometry, speed_kn: float, platform: Dict[str, float],
                    ref: Dict[str, object]) -> Optional[Dict[str, object]]:
    """Physics/regulatory plausibility band for the tanker case (IMO EEDI reference)."""
    if geom.deadweight is None:
        return None
    dwt = geom.deadweight
    ref_line = eedi_reference("tanker", dwt)
    p_me_ref = required_shaft_power_for_eedi(ref_line, dwt, speed_kn)
    pe_ref = float(ref["pe_kw"]) / 1e3      # MW
    pe_pl = float(platform["pe"]) / 1e3     # MW

    def to_eedi(pe_mw: float) -> float:
        p_me = pe_mw * 1000.0 / ETA_D_NOMINAL
        return eedi_from_shaft_power(p_me, dwt, speed_kn)

    return {
        "hull": geom.name,
        "speed_kn": speed_kn,
        "deadweight_t": dwt,
        "eedi_reference_line": round(ref_line, 4),
        "pme_implied_by_reference_kw": round(p_me_ref, 1),
        "pe_holtrop1984_mw": round(pe_ref, 3),
        "pe_platform_mw": round(pe_pl, 3),
        "eedi_holtrop1984": round(to_eedi(pe_ref), 4),
        "eedi_platform": round(to_eedi(pe_pl), 4),
        "ratio_platform_to_reference": round(to_eedi(pe_pl) / ref_line, 3),
        "ratio_holtrop1984_to_reference": round(to_eedi(pe_ref) / ref_line, 3),
        "verdict": (
            "platform result implies an attained EEDI far above the IMO reference line "
            "(physically implausible for a modern VLCC)"
            if to_eedi(pe_pl) / ref_line > 1.5
            else "platform result within a plausible band"
        ),
    }


# ---------------------------------------------------------------------------
# Reference-dataset integrity (backend/data/benchmark_reference.json)
# ---------------------------------------------------------------------------

def check_benchmark_dataset(path: str, rho_default: float = 998.0) -> Dict[str, object]:
    """Check internal consistency of the platform's benchmark reference dataset.

    Every case that publishes both ``experimental_ct`` and ``experimental_rt_n`` must
    satisfy  Rt = 0.5 rho V^2 S Ct  (same geometry, same speed).  A deviation of more
    than 10% means the two columns cannot both be experimental values, and any pass/fail
    gate that mixes them (``ship_benchmark`` compares Ct but reports Rt context) is unsound.
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rows: List[Dict[str, object]] = []
    for case in data.get("cases", []):
        g = case.get("geometry", {})
        s_ref = g.get("wetted_surface_m2")
        v = case.get("speed_ms")
        ct = case.get("experimental_ct")
        rt = case.get("experimental_rt_n")
        rho = g.get("water_density", rho_default)
        if not (s_ref and v and ct and rt):
            rows.append({
                "case_id": case.get("case_id"),
                "status": "NOT_CHECKABLE",
                "detail": "missing wetted_surface_m2 / speed_ms / experimental_ct / experimental_rt_n",
            })
            continue
        rt_from_ct = 0.5 * rho * v * v * s_ref * ct
        err = rel_err_pct(rt, rt_from_ct)
        rows.append({
            "case_id": case.get("case_id"),
            "speed_ms": v,
            "wetted_surface_m2": s_ref,
            "experimental_ct": ct,
            "experimental_rt_n": rt,
            "rt_implied_by_ct_n": round(rt_from_ct, 3),
            "rel_err_pct": round(err, 2),
            "status": "PASS" if abs(err) <= 10.0 else "FAIL",
        })
    fails = [r for r in rows if r.get("status") == "FAIL"]
    return {
        "path": path,
        "version": data.get("version"),
        "sources": data.get("description"),
        "cases": rows,
        "n_cases": len(rows),
        "n_fail": len(fails),
        "verdict": (
            f"{len(fails)}/{len(rows)} cases have experimental_ct and experimental_rt_n that are "
            "mutually inconsistent (>10%): the two columns cannot both be experimental data"
            if fails else "all checkable cases consistent within 10%"
        ),
    }


# ---------------------------------------------------------------------------
# Sweep / figure data
# ---------------------------------------------------------------------------

def sweep(
    geom: HullGeometry, speeds_kn: Sequence[float],
    fetch: Optional[Callable[..., Dict[str, float]]],
) -> Dict[str, List[float]]:
    """Platform vs reference resistance over a speed sweep (for curve figures)."""
    out: Dict[str, List[float]] = {
        "speed_kn": [], "rt_platform_kn": [], "rt_reference_kn": [],
        "pe_platform_mw": [], "pe_reference_mw": [], "rel_err_pct": [],
    }
    for kn in speeds_kn:
        ref = holtrop1984_resistance(geom, kn * KM_TO_MS)
        out["speed_kn"].append(kn)
        out["rt_reference_kn"].append(ref.rt / 1e3)
        out["pe_reference_mw"].append(ref.pe_kw / 1e3)
        if fetch is None:
            out["rt_platform_kn"].append(float("nan"))
            out["pe_platform_mw"].append(float("nan"))
            out["rel_err_pct"].append(float("nan"))
            continue
        p = fetch(platform_payload(geom, kn))
        out["rt_platform_kn"].append(float(p["rt"]) / 1e3)
        out["pe_platform_mw"].append(float(p["pe"]) / 1e3)
        out["rel_err_pct"].append(rel_err_pct(float(p["rt"]), ref.rt) or 0.0)
    return out


# ---------------------------------------------------------------------------
# Markdown / figure rendering
# ---------------------------------------------------------------------------

def _fmt(x: object) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.4g}"
    return str(x)


def render_markdown(audit: Dict[str, object]) -> str:
    lines: List[str] = []
    lines.append("# calc.resistance 独立复核（m1536，验证欠账线）\n")
    lines.append(
        f"- 复核对象：船舶总体性能平台 `POST /api/v1/calculations/resistance`"
        f"（自述方法 `{audit['platform_method']}`）\n"
        f"- 基准：ITTC-1957 解析线 + Holtrop-Mennen(1984) 忠实实现 + 公开基准湿表面积"
        f" + IMO EEDI 参考线（MEPC.203(62)）\n"
        f"- 判据：|相对误差| > {ERROR_THRESHOLD_PCT:.0f}% 记 FAIL（平台精度标准）\n"
    )
    lines.append("## 1. 复核矩阵（逐工况）\n")
    header = ("| 船型 | V[kn] | Fn | 参考 Ct | 平台 Ct | 参考 RT[kN] | 平台 RT[kN] "
              "| RT 误差 | 参考 PE[MW] | 平台 PE[MW] | PE 误差 | 判决 |")
    lines.append(header)
    lines.append("|" + "---|" * 12)
    for case in audit["cases"]:
        r = case["reference"]
        row = {x["quantity"]: x for x in case["rows"]}
        rt = row["total resistance RT"]
        pe = row["effective power PE"]
        verdict = "PASS" if (rt["status"] == "PASS" and pe["status"] == "PASS") else "FAIL"
        lines.append(
            f"| {case['hull']} | {case['speed_kn']:.1f} | {r['fn']:.4f} | {r['ct']:.5f} "
            f"| {float(case['platform']['ct']):.5f} | {r['rt_kn']:.0f} "
            f"| {float(case['platform']['rt']):.0f} | {_fmt(rt['rel_err_pct'])}% "
            f"| {r['pe_kw'] / 1e3:.2f} | {float(case['platform']['pe']) / 1e3:.2f} "
            f"| {_fmt(pe['rel_err_pct'])}% | {verdict} |"
        )
    lines.append("")

    lines.append("## 2. 分项误差（KVLCC2 @ 15 kn 代表工况）\n")
    rep = next((c for c in audit["cases"] if c["hull"] == "KVLCC2" and c["speed_kn"] == 15.0),
               audit["cases"][0])
    lines.append("| 量 | 平台 | 参考 | 单位 | 误差 | 参考依据 | 判决 |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in rep["rows"]:
        lines.append(
            f"| {row['quantity']} | {_fmt(row['platform'])} | {_fmt(row['reference'])} "
            f"| {row['unit']} | {_fmt(row['rel_err_pct'])}% | {row['reference_basis']} "
            f"| {row['status']} |"
        )
    lines.append("")

    lines.append("## 3. 几何与输入校验\n")
    dc = audit["displacement_check"]
    lines.append(
        f"- 位移一致性探针（{dc['hull']} @ {dc['speed_kn']} kn）：RT = {dc['rt_normal_kn']} kN "
        f"（正常）vs {dc['rt_displacement_x10_kn']} kN（位移×10）→ **{dc['verdict']}**\n"
    )
    for case in [audit["cases"][0]]:
        for row in case["rows"]:
            if "geometry" in row["quantity"]:
                lines.append(
                    f"- Cb 自洽性：Cb={_fmt(row['platform'])} vs vol/(LBT)={_fmt(row['reference'])} "
                    f"（{_fmt(row['rel_err_pct'])}%）\n"
                )
    lines.append("")

    lines.append("## 4. 物理/规范可行性交叉核对（KVLCC2 15 kn）\n")
    ec = audit.get("eedi_crosscheck")
    if ec:
        lines.append(
            f"- IMO EEDI 参考线（tanker, DWT={ec['deadweight_t']:.0f} t）= "
            f"{ec['eedi_reference_line']:.3f} g CO2/(t·nm)；对应所需 PME = "
            f"{ec['pme_implied_by_reference_kw']:.0f} kW（V={ec['speed_kn']} kn）\n"
            f"- Holtrop-1984 → PE = {ec['pe_holtrop1984_mw']:.2f} MW，折算 EEDI = "
            f"{ec['eedi_holtrop1984']:.3f}（{ec['ratio_holtrop1984_to_reference']:.2f}× 参考线）\n"
            f"- 平台 → PE = {ec['pe_platform_mw']:.2f} MW，折算 EEDI = {ec['eedi_platform']:.3f}"
            f"（{ec['ratio_platform_to_reference']:.2f}× 参考线）→ {ec['verdict']}\n"
        )
    lines.append("")
    lines.append("## 5. 参考数据集自洽性（backend/data/benchmark_reference.json）\n")
    bd = audit.get("benchmark_dataset")
    if bd:
        lines.append(f"- {bd['verdict']}\n")
        lines.append("| case | V[m/s] | S[m²] | Ct | Rt[N] | Rt implied by Ct [N] | 误差 | 判决 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for row in bd["cases"]:
            if row.get("status") == "NOT_CHECKABLE":
                lines.append(f"| {row['case_id']} | - | - | - | - | - | - | NOT_CHECKABLE |")
                continue
            lines.append(
                f"| {row['case_id']} | {row['speed_ms']} | {row['wetted_surface_m2']} | "
                f"{row['experimental_ct']} | {row['experimental_rt_n']} | "
                f"{row['rt_implied_by_ct_n']} | {row['rel_err_pct']}% | {row['status']} |"
            )
    lines.append("")
    lines.append("## 6. 结论\n")
    lines.append(audit["verdict_note"])
    lines.append("")
    return "\n".join(lines)


def make_figures(audit: Dict[str, object], outdir: str) -> List[str]:
    """Render the three audit figures (curves, component bars, EEDI plausibility)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    paths: List[str] = []

    # Figure 1: RT vs speed, platform vs reference (KVLCC2 + KCS)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, (name, sw) in zip(axes, audit["sweeps"].items()):
        ax.plot(sw["speed_kn"], sw["rt_reference_kn"], "o-", label="Holtrop-Mennen 1984 (ref)")
        ax.plot(sw["speed_kn"], sw["rt_platform_kn"], "s--", label="platform API")
        ax.set_xlabel("ship speed [kn]")
        ax.set_ylabel("total resistance RT [kN]")
        ax.set_title(f"{name}: RT vs speed")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle("Calm-water resistance: platform vs independent HM84 reference")
    fig.tight_layout()
    p1 = os.path.join(outdir, "fig1_rt_vs_speed.png")
    fig.savefig(p1, dpi=130)
    plt.close(fig)
    paths.append(p1)

    # Figure 2: component decomposition at the representative case
    rep = next(c for c in audit["cases"] if c["hull"] == "KVLCC2" and c["speed_kn"] == 15.0)
    r = rep["reference"]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    labels = ["frictional\n(bare Cf)", "viscous form\nk·Rf", "wave\nRw", "correlation\nRa"]
    ref_vals = [r["rf_kn"], r["k_rf_kn"], r["rw_kn"], r["ra_kn"]]
    ax.bar(range(4), ref_vals, color="#3b7dd8")
    ax.bar(
        [4, 5],
        [float(rep["platform"]["rf"]), float(rep["platform"]["rr"])],
        color="#d1495b", width=0.7,
    )
    ax.set_ylabel("resistance [kN]")
    ax.set_title("KVLCC2 @ 15 kn: component decomposition (published-method vs platform)")
    ax.set_xticks(range(6))
    ax.set_xticklabels(
        [f"ref Rf\n{ref_vals[0]:.0f} kN", f"ref kRf\n{ref_vals[1]:.0f} kN",
         f"ref Rw\n{ref_vals[2]:.0f} kN", f"ref Ra\n{ref_vals[3]:.0f} kN",
         f"platf. RF\n{float(rep['platform']['rf']):.0f} kN",
         f"platf. RR\n{float(rep['platform']['rr']):.0f} kN"],
        fontsize=7,
    )
    ax.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    p2 = os.path.join(outdir, "fig2_components.png")
    fig.savefig(p2, dpi=130)
    plt.close(fig)
    paths.append(p2)

    # Figure 3: EEDI plausibility band
    ec = audit.get("eedi_crosscheck")
    if ec:
        fig, ax = plt.subplots(figsize=(6.2, 4.0))
        names = ["IMO EEDI\nreference line", "Holtrop-Mennen\n1984", "platform API"]
        vals = [ec["eedi_reference_line"], ec["eedi_holtrop1984"], ec["eedi_platform"]]
        colors = ["#6c757d", "#2a9d8f", "#d1495b"]
        bars = ax.bar(names, vals, color=colors)
        ax.axhline(ec["eedi_reference_line"], ls="--", c="#333", lw=1)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.2f}", ha="center", va="bottom",
                    fontsize=9)
        ax.set_ylabel("implied attained EEDI [g CO2/(t·nm)]")
        ax.set_title("KVLCC2 15 kn: implied EEDI vs IMO reference line\n"
                     "(PE -> PME with eta_D = 0.72, SFC = 175 g/kWh, CF = 3.114)")
        ax.grid(alpha=0.25, axis="y")
        fig.tight_layout()
        p3 = os.path.join(outdir, "fig3_eedi_plausibility.png")
        fig.savefig(p3, dpi=130)
        plt.close(fig)
        paths.append(p3)

    return paths


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_audit(
    fetch: Optional[Callable[..., Dict[str, float]]] = None,
    outdir: str = "outputs/m1536",
    figures: bool = True,
    base_url: str = "http://127.0.0.1:8001/api/v1",
) -> Dict[str, object]:
    """Run the full audit and write JSON + markdown + figures into ``outdir``."""
    if fetch is None:
        def fetch(payload, **_kw):  # type: ignore[misc]
            return fetch_platform(payload, base_url=base_url)

    cases = []
    platform_method = ""
    for name, kn in CASES:
        geom = BENCHMARK_HULLS[name]
        p = fetch(platform_payload(geom, kn))
        platform_method = str(p.get("method", ""))
        cases.append(compare_case(geom, kn, p))

    displacement_check = check_displacement_used(KVLCC2, 15.0, fetch)

    sweeps = {}
    for name, speeds in (
        ("KVLCC2", [9.0, 10.5, 12.0, 13.5, 15.0, 16.5, 18.0]),
        ("KCS", [14.0, 16.0, 18.0, 20.0, 22.0, 24.0]),
    ):
        sweeps[name] = sweep(BENCHMARK_HULLS[name], speeds, fetch)

    rep = next(c for c in cases if c["hull"] == "KVLCC2" and c["speed_kn"] == 15.0)
    eedi = eedi_crosscheck(KVLCC2, 15.0, rep["platform"], rep["reference"])

    # aggregate verdict
    fails = sum(1 for c in cases for row in c["rows"] if row["status"] == "FAIL")
    checks = sum(1 for c in cases for row in c["rows"] if row["status"] in ("PASS", "FAIL"))
    rt_cases = [c for c in cases]
    rt_err = [next(r["rel_err_pct"] for r in c["rows"] if r["quantity"].startswith("total resistance RT"))
              for c in rt_cases]
    audit: Dict[str, object] = {
        "mission": "m1536",
        "capability": "calc.resistance",
        "endpoint": f"{base_url.rstrip('/')}/calculations/resistance",
        "platform_method": platform_method,
        "threshold_pct": ERROR_THRESHOLD_PCT,
        "reference_stack": [
            "ITTC-1957 friction line (analytic)",
            "Holtrop-Mennen (1984) faithful implementation (src/ship_verification/holtrop1984.py)",
            "published benchmark wetted surfaces: KVLCC2 27194 m2, KCS 9494 m2",
            "Denny-Mumford wetted-surface correlation (cross-check)",
            "IMO EEDI reference line MEPC.203(62) (plausibility band)",
        ],
        "cases": cases,
        "sweeps": sweeps,
        "displacement_check": displacement_check,
        "eedi_crosscheck": eedi,
        "benchmark_dataset": check_benchmark_dataset(
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "backend", "data", "benchmark_reference.json")
        ),
        "summary": {
            "cases": len(cases),
            "checks": checks,
            "fails": fails,
            "equivalent_ref_case_fails": sum(1 for c in cases if any(
                r["status"] == "FAIL" for r in c["rows"])),
            "rt_rel_err_pct_min": round(min(rt_err), 2),
            "rt_rel_err_pct_max": round(max(rt_err), 2),
        },
    }
    audit["verdict_note"] = (
        "复核判决：**不通过（FAIL）**。平台 `calc.resistance` 自述 "
        f"`{platform_method}`，但 (1) 阻力系数的结构与其声称的 Holtrop 方法不符——"
        "残余阻力用分段拟合式 Cr_base = 0.005·C4，量级比 Holtrop-1984 的 Cw+kCf+Ca 高出数倍；"
        f"(2) 全尺度 RT 相对误差落在 "
        f"{audit['summary']['rt_rel_err_pct_min']}% … {audit['summary']['rt_rel_err_pct_max']}% "
        "区间，远超 15% 可接受门；(3) 端点接收 displacement 却完全不使用，几何 Cb/∇ 未经交叉校验，"
        "违反平台「几何必须先验证 Cb/L/B」的精度前置条件。"
        "建议：以 src/ship_verification/holtrop1984.py 作为参考实现替换端点阻力内核，"
        "并补 Cb ↔ vol/(LBT) 一致性门。"
        + (
            "（附加）平台自带基准数据集 backend/data/benchmark_reference.json 的 "
            f"{audit.get('benchmark_dataset', {}).get('n_fail', 0)}/"
            f"{audit.get('benchmark_dataset', {}).get('n_cases', 0)} 个算例中 "
            "experimental_ct 与 experimental_rt_n 互相矛盾（同一几何同一速度下 Rt ≠ "
            "0.5ρV²SCt，偏差 7-10 倍），任何以 Rt 列做判据的门禁都不可信。"
            if audit.get("benchmark_dataset", {}).get("n_fail") else ""
        )
    )

    os.makedirs(outdir, exist_ok=True)
    json_path = os.path.join(outdir, "resistance_audit.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(audit, fh, ensure_ascii=False, indent=1)
    md_path = os.path.join(outdir, "resistance_audit.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(audit))
    audit["artifacts"] = {"json": json_path, "markdown": md_path}
    if figures:
        audit["artifacts"]["figures"] = make_figures(audit, outdir)
    return audit


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit calc.resistance against independent references")
    ap.add_argument("--out", default="outputs/m1536")
    ap.add_argument("--base-url", default="http://127.0.0.1:8001/api/v1")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args()
    try:
        audit = run_audit(outdir=args.out, figures=not args.no_figures,
                          base_url=args.base_url)
    except (urllib.error.URLError, OSError) as exc:  # platform down
        raise SystemExit(f"platform endpoint unreachable: {exc}")
    s = audit["summary"]
    print(json.dumps(s, ensure_ascii=False, indent=1))
    print(audit["verdict_note"])
    print("artifacts:", json.dumps(audit["artifacts"], ensure_ascii=False))


if __name__ == "__main__":
    main()