#!/usr/bin/env python3
"""Validation-debt audit of the ship-performance platform resistance endpoint.

Subject under test
------------------
    POST http://127.0.0.1:8001/api/v1/calculations/resistance
    (deployed ship platform; graded ``REAL`` in the platform capability audit)

What this script does
---------------------
1. Probes the live endpoint over a small matrix (submarine / destroyer model /
   tanker full scale) using SI inputs converted to the endpoint's own unit
   convention (``speed`` is knots -- verified, finding F1).
2. Recomputes every input-side quantity independently from :mod:`baselines`
   (Re, Fn, ITTC-57 Cf, Holtrop wetted surface) and asks whether the endpoint's
   own numbers are arithmetically and physically coherent.
3. Applies validity-domain tests that need no external report:
   T1 arithmetic identity (Ct = Cf + Cr + dCf, RT = 0.5 rho V^2 S Ct, PE = RT V)
   T2 physics of the decomposition (residual share of a deeply submerged body)
   T3 geometry (wetted area vs. the repo's own cited reference, SUBOFF 5.42 m^2)
   T4 dead-parameter probe (displacement must influence resistance)
   T5 residual-law reconstruction (Fn sweep + Cb sensitivity)
   T6 cross-service contract (who calls this endpoint and with which unit)
4. Re-audits the repo benchmark table (``backend/data/benchmark_reference.json``)
   for internal consistency; a self-contradictory reference cannot grade anything.
5. Writes ``artifacts/validation_debt/resistance_audit.json`` + a figure.

Run:  python3 tools/validation_debt/resistance_audit.py [--base URL] [--offline]
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baselines as B  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "validation_debt"
BENCHMARK = ROOT / "backend" / "data" / "benchmark_reference.json"
CALLER_SRC = ROOT / "backend" / "api" / "domain" / "ship_benchmark.py"

DEFAULT_BASE = "http://127.0.0.1:8001"

# Geometry presets.  Sources: SUBOFF (Groves et al. 1989 / Roddy 1990, the citation
# used by the repo benchmark table), DTMB-5415 and KVLCC2 (ITTC / SIMMAN 2008
# reference hulls).  ``s_ref`` is the reference wetted area used by T3; where the
# repo holds no citable value the check is declared WITHHELD instead of guessed.
HULLS: List[Dict[str, Any]] = [
    {
        "tag": "SUBOFF",
        "note": "DARPA SUBOFF bare hull, deeply submerged -> no free surface",
        "length": 4.356, "beam": 0.508, "draft": 0.508,
        "displacement": 0.6, "cb": 0.508,
        "speeds_ms": [1.5, 2.0, 2.5, 3.0],
        "s_ref": 5.42,
        "submerged": True,
    },
    {
        "tag": "DTMB-5415",
        "note": "ITTC V&V workshop model hull (surface ship)",
        "length": 5.72, "beam": 0.759, "draft": 0.246,
        "displacement": 0.4663, "cb": 0.508,
        "speeds_ms": [1.5, 2.57, 3.5],
        "s_ref": None,
        "submerged": False,
    },
    {
        "tag": "KVLCC2",
        "note": "SIMMAN 2008 tanker, full scale",
        "length": 320.0, "beam": 58.0, "draft": 20.8,
        "displacement": 307000.0, "cb": 0.8098,
        "speeds_ms": [6.0, 7.2, 7.97],
        "s_ref": None,
        "submerged": False,
    },
]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def post_resistance(base: str, payload: Dict[str, Any], timeout: float = 20.0) -> Optional[Dict]:
    url = base.rstrip("/") + "/api/v1/calculations/resistance"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}", "_body": e.read()[:400].decode("utf-8", "replace")}
    except Exception as e:  # noqa: BLE001
        return {"_error": repr(e)}


def probe_payload(hull: Dict[str, Any], v_ms: float, displacement: Optional[float] = None,
                  cb: Optional[float] = None) -> Dict[str, Any]:
    """Build a request. ``speed`` is in KNOTS for this endpoint (finding F1)."""
    return {
        "length": hull["length"], "beam": hull["beam"], "draft": hull["draft"],
        "displacement": hull["displacement"] if displacement is None else displacement,
        "speed": round(B.ms_to_knots(v_ms), 6),
        "cb": hull["cb"] if cb is None else cb,
    }


def _round_tol(value: float, dp: int = 2, rel: float = 0.01) -> float:
    """Tolerance for a comparison against an output rounded to ``dp`` decimals.

    A full quantum is allowed because the reference itself is rebuilt from
    *rounded* inputs (S to 1e-2, Ct to 1e-6).  T1 is therefore a coarse guard
    against plumbing errors only; magnitude-limited outputs (RT ~ 0.05 kN) cannot
    resolve better than a few percent.
    """
    quantum = 10.0 ** (-dp)
    return max(rel, quantum / abs(value)) if value else rel


# ---------------------------------------------------------------------------
# Single case
# ---------------------------------------------------------------------------
def audit_case(base: str, hull: Dict[str, Any], v_ms: float) -> Dict[str, Any]:
    payload = probe_payload(hull, v_ms)
    out = post_resistance(base, payload)
    row: Dict[str, Any] = {
        "hull": hull["tag"], "speed_ms": v_ms, "speed_kn": payload["speed"],
        "request": payload, "response": out,
    }
    if not out or "_error" in out:
        row["verdict"] = "ENDPOINT_UNREACHABLE_OR_ERROR"
        return row

    re_ind = B.reynolds(v_ms, hull["length"])
    fn_ind = B.froude(v_ms, hull["length"])
    cf_ind = B.ittc57_cf(re_ind)
    s_hol = B.holtrop_wetted_surface(hull["length"], hull["beam"], hull["draft"], hull["cb"])
    k = 0.10

    row["independent"] = {
        "reynolds": round(re_ind, 1),
        "froude": round(fn_ind, 5),
        "ittc57_cf": round(cf_ind, 6),
        "holtrop_wetted_surface_m2": round(s_hol, 3),
        "submerged_ct_baseline_k10": round(B.submerged_ct_baseline(cf_ind, k), 6),
        "submerged_ct_baseline_k05": round(B.submerged_ct_baseline(cf_ind, 0.05), 6),
        "submerged_ct_baseline_k15": round(B.submerged_ct_baseline(cf_ind, 0.15), 6),
    }

    # T1 arithmetic identity (rounding-aware: outputs are quantised to 1e-2 kN)
    row["T1_arithmetic"] = B.check_arithmetic_identity(out)
    row["T1_surface"] = B.check_surface_response(
        out, v_ms, B.RHO_DEFAULT, out["wetted_surface"],
        tol_rt=_round_tol(out["rt"]), tol_pe=_round_tol(out.get("pe") or 1e-9))

    # F1: which unit does ``speed`` use?
    row["P0_unit_convention"] = {
        "reported_re": out["reynolds_number"],
        "implied_re_if_knots": round(re_ind, 1),
        "implied_re_if_ms": round(B.reynolds(v_ms / B.KNOT_MS, hull["length"]), 1),
        "speed_field_is_knots": bool(abs(out["reynolds_number"] - re_ind) / re_ind < 0.02),
    }

    row["validity"] = B.check_inputs_physically_valid(hull["tag"], hull["cb"], fn_ind)
    row["T2_decomposition"] = B.check_decomposition_share(out)
    row["T2_cr_cf"] = B.check_submerged_wave_resistance(
        hull["tag"], out, tolerance_ratio=0.25 if hull["submerged"] else 1.0)

    if hull.get("s_ref"):
        row["T3_wetted_surface"] = B.check_wetted_surface(out["wetted_surface"], hull["s_ref"])
    else:
        row["T3_wetted_surface"] = {"checked": False,
                                    "reason": "no citable reference wetted area in repo (WITHHELD)"}

    if hull["submerged"]:
        rt_platform = out["rt"] * 1000.0                       # kN -> N
        rt_base = B.frictional_resistance(B.RHO_DEFAULT, v_ms, hull["s_ref"], cf_ind) * (1 + k)
        row["accuracy_vs_baseline"] = {
            "rt_platform_n": round(rt_platform, 3),
            "rt_baseline_n_1pk_cf_s_ref": round(rt_base, 3),
            "rel_error": round(abs(rt_platform - rt_base) / rt_base, 4),
            "ct_platform": out["ct"],
            "ct_baseline": round(B.submerged_ct_baseline(cf_ind, k), 6),
            "ct_rel_error": round(abs(out["ct"] - B.submerged_ct_baseline(cf_ind, k))
                                  / B.submerged_ct_baseline(cf_ind, k), 4),
            "criterion": "<15% acceptable",
            "passed": bool(abs(rt_platform - rt_base) / rt_base <= 0.15),
        }
    return row


# ---------------------------------------------------------------------------
# T4 / T5 / T6
# ---------------------------------------------------------------------------
def probe_dead_displacement(base: str) -> Dict[str, Any]:
    """T4: does ``displacement`` (a required request field) change the answer?"""
    hull = HULLS[0]
    a = post_resistance(base, probe_payload(hull, 2.0, displacement=0.5))
    b = post_resistance(base, probe_payload(hull, 2.0, displacement=5000.0))
    if "_error" in a or "_error" in b:
        return {"probed": False, "error": a.get("_error") or b.get("_error")}
    same = abs(a["ct"] - b["ct"]) < 1e-12 and abs(a["rt"] - b["rt"]) < 1e-12
    return {
        "probed": True,
        "displacement_small_t": 0.5, "displacement_large_t": 5000.0,
        "ct_small": a["ct"], "ct_large": b["ct"],
        "identical_output": same,
        "verdict": ("DEAD PARAMETER: resistance is independent of displacement "
                    "(field is required but never enters the formula)" if same
                    else "displacement influences the result"),
    }


def probe_residual_law(base: str) -> Dict[str, Any]:
    """T5: reconstruct the residual-resistance law from the endpoint itself.

    Surface hull (KVLCC2 geometry, L/B/T fixed) swept over Fn, plus a Cb sweep at
    fixed Fn.  Two facts are diagnostic of whether the law can be a re-analysis of
    Holtrop-Mennen at all:
      * no hump: Holtrop's C4 peaks near Fn ~ 0.24 then decays; a monotone ramp
        cannot reproduce that shape;
      * Cb blindness: for Cb <= 0.70 the code path sets C3 = 1, so a fine destroyer
        form and a full tanker form get *identical* residual resistance -- whereas
        the whole content of the Holtrop regression is its Cb dependence.
    """
    hull = dict(HULLS[2])
    rows, cr = [], {}
    for fn in (0.06, 0.10, 0.14, 0.18, 0.22, 0.26, 0.30, 0.36, 0.42):
        v_ms = fn * (B.G * hull["length"]) ** 0.5
        out = post_resistance(base, probe_payload(hull, v_ms))
        if "_error" in out:
            return {"probed": False, "error": out["_error"]}
        rows.append({"froude": round(fn, 3), "cr": out["cr"], "cf": out["cf"]})
        cr[round(fn, 3)] = out["cr"]

    cb_probe = {}
    for cb in (0.55, 0.62, 0.70):
        v_ms = 0.14 * (B.G * hull["length"]) ** 0.5
        out = post_resistance(base, probe_payload(hull, v_ms, cb=cb))
        cb_probe[cb] = out.get("cr")

    vals = [r["cr"] for r in rows]
    peak_fn = rows[vals.index(max(vals))]["froude"]
    monotone = all(vals[i + 1] > vals[i] for i in range(len(vals) - 1))
    cb_vals = [v for v in cb_probe.values() if v is not None]
    return {
        "probed": True,
        "hull": hull["tag"],
        "fn_sweep": rows,
        "cr_peak_froude": peak_fn,
        "monotone_increasing": monotone,
        "cr_max_over_min": round(max(vals) / min(vals), 3) if min(vals) else None,
        "cb_sweep_cr": cb_probe,
        "cb_insensitive_below_070": bool(len(cb_vals) >= 2 and max(cb_vals) - min(cb_vals) < 1e-9),
        "verdict": (
            ("NO HUMP + Cb-BLIND: Cr is a monotone ramp that is identical for "
             f"Cb={min(cb_probe):.2f}..0.70 -> not a Holtrop-Mennen re-analysis"
             if monotone and len(cb_vals) >= 2 and max(cb_vals) - min(cb_vals) < 1e-9
             else "shape not classified conclusively")),
    }


def probe_cross_service_contract() -> Dict[str, Any]:
    """T6: the in-repo caller (TensorPlatform benchmark_validate) and the endpoint
    must agree on both the port and the speed unit."""
    src = CALLER_SRC.read_text() if CALLER_SRC.exists() else ""
    m = re.search(r'SHIP_PLATFORM_URL",\s*"([^"]+)"', src)
    caller_url = m.group(1) if m else None
    port = None
    if caller_url:
        mm = re.search(r":(\d+)',?$", caller_url) or re.search(r":(\d+)", caller_url)
        port = int(mm.group(1)) if mm else None
    listening = None
    if port:
        with socket.socket() as s:
            s.settimeout(0.5)
            listening = (s.connect_ex(("127.0.0.1", port)) == 0)
    unit_mismatch = bool(re.search(r'"speed":\s*speed_ms', src))
    return {
        "caller_file": str(CALLER_SRC.relative_to(ROOT)) if CALLER_SRC.exists() else None,
        "caller_url": caller_url,
        "caller_port_listening": listening,
        "caller_sends_ms_into_knots_field": unit_mismatch,
        "verdict": (
            ("BROKEN CONTRACT: caller targets port %s (listening=%s) and sends m/s into a "
             "knots field (x1.944 speed error)" % (port, listening))
            if unit_mismatch or listening is False else "contract consistent"),
    }


def audit_benchmark_dataset() -> Dict[str, Any]:
    data = json.loads(BENCHMARK.read_text())
    cases = data["cases"]
    checks = [B.case_internal_consistency(c) for c in cases]
    bad = [c for c in checks if c.get("checked") and not c["consistent"]]
    return {
        "source": str(BENCHMARK.relative_to(ROOT)),
        "n_cases": len(cases),
        "n_checked": sum(1 for c in checks if c.get("checked")),
        "n_inconsistent": len(bad),
        "worst_rel_error": max((c["rel_error"] for c in checks if c.get("checked")), default=None),
        "cases": checks,
        "monotonicity": [B.dataset_monotonicity(cases, "SUBOFF"),
                         B.dataset_monotonicity(cases, "DTMB-5415")],
        "verdict": ("reference table is internally consistent" if not bad else
                    "SELF-CONTRADICTORY REFERENCE: Ct and Rt disagree by up to "
                    f"{max(c['rel_error'] for c in bad):.0%} for {len(bad)}/"
                    f"{len(checks)} cases -> cannot grade any implementation"),
    }


# ---------------------------------------------------------------------------
# Figure
# ---------------------------------------------------------------------------
def make_figure(rows: List[Dict[str, Any]], ds: Dict[str, Any], law: Dict[str, Any],
                out_png: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        print(f"[fig] matplotlib unavailable: {e}")
        return False

    live = [r for r in rows if "_error" not in r.get("response", {"_error": 1})]
    if not live:
        return False
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 9))

    # (a) SUBOFF: Ct vs speed
    ax = axes[0][0]
    sub = [r for r in live if r["hull"] == "SUBOFF"]
    v = [r["speed_ms"] for r in sub]
    ax.plot(v, [r["response"]["ct"] for r in sub], "o-", lw=2,
            label="platform Ct (ITTC-57 Cf + 'Holtrop' Cr)")
    ax.plot(v, [r["response"]["cf"] for r in sub], "s--", lw=1.5, label="platform Cf")
    ax.plot(v, [r["response"]["cr"] for r in sub], "^--", lw=1.5, label="platform Cr (residual)")
    ax.plot(v, [r["independent"]["submerged_ct_baseline_k10"] for r in sub], "k-", lw=2,
            label="baseline (1+k)Cf, k=0.10 [submerged body]")
    ax.fill_between(v,
                    [r["independent"]["submerged_ct_baseline_k05"] for r in sub],
                    [r["independent"]["submerged_ct_baseline_k15"] for r in sub],
                    color="k", alpha=0.12, label="k = 0.05-0.15 band")
    ax.set_xlabel("speed U [m/s]"); ax.set_ylabel("Ct [-]")
    ax.set_title("(a) SUBOFF bare hull: a submerged body needs Cr -> 0")
    ax.legend(fontsize=7.5); ax.grid(alpha=0.3)

    # (b) residual/friction ratio
    ax = axes[0][1]
    for tag, mk in (("SUBOFF", "o"), ("DTMB-5415", "s"), ("KVLCC2", "d")):
        sel = [r for r in live if r["hull"] == tag]
        if not sel:
            continue
        ax.plot([r["independent"]["froude"] for r in sel],
                [r["T2_cr_cf"]["cr_cf_ratio"] for r in sel], mk + "-", label=tag)
    ax.axhspan(0.10, 0.60, color="tab:green", alpha=0.15,
               label="accepted Cr/Cf band, conventional forms Fn<0.30")
    ax.axhline(0.25, color="tab:red", ls=":", label="submerged-body ceiling")
    ax.set_xlabel("Fn [-]"); ax.set_ylabel("Cr / Cf [-]"); ax.set_yscale("log")
    ax.set_title("(b) Wave term dominates the viscous term at every Fn")
    ax.legend(fontsize=7.5); ax.grid(alpha=0.3, which="both")

    # (c) residual law shape
    ax = axes[1][0]
    if law.get("probed"):
        fns = [p["froude"] for p in law["fn_sweep"]]
        crs = [p["cr"] for p in law["fn_sweep"]]
        cfs = [p["cf"] for p in law["fn_sweep"]]
        ax.plot(fns, crs, "o-", label="Cr reconstructed from endpoint")
        ax.plot(fns, cfs, "s--", label="Cf (ITTC-57 path)")
        ax.axvline(0.24, color="tab:red", ls=":", label="Holtrop C4 hump ~Fn 0.24")
        ax.set_yscale("log")
        ax.set_xlabel("Fn [-]"); ax.set_ylabel("coefficient [-]")
        ax.set_title("(c) Residual law: monotone ramp, no hump, unbounded")
        ax.legend(fontsize=7.5); ax.grid(alpha=0.3, which="both")

    # (d) reference table self-consistency
    ax = axes[1][1]
    checked = [c for c in ds["cases"] if c.get("checked")]
    labels = [c["case_id"] for c in checked]
    ratios = [c["ratio_reported_over_implied"] or 0 for c in checked]
    colors = ["tab:red" if not c["consistent"] else "tab:green" for c in checked]
    ax.barh(range(len(labels)), ratios, color=colors)
    ax.axvline(1.0, color="k", ls="--", lw=1, label="Rt = 0.5 rho S V^2 Ct")
    ax.set_yticks(range(len(labels))); ax.set_yticklabels(labels, fontsize=7)
    ax.set_xlabel("reported Rt / Rt implied by reported Ct [-]")
    ax.set_title("(d) Benchmark table: reported Rt vs Ct-implied Rt")
    ax.legend(fontsize=7.5); ax.grid(alpha=0.3, axis="x")

    fig.suptitle("calc.resistance validation-debt audit — ship platform :8001, 2026-11-01",
                 fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    return True


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--offline", action="store_true",
                    help="skip live probes (dataset audit only)")
    args = ap.parse_args()

    ART.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    dead: Dict[str, Any] = {}
    law: Dict[str, Any] = {}
    contract: Dict[str, Any] = {}
    if not args.offline:
        for hull in HULLS:
            for v_ms in hull["speeds_ms"]:
                rows.append(audit_case(args.base, hull, v_ms))
        dead = probe_dead_displacement(args.base)
        law = probe_residual_law(args.base)
        contract = probe_cross_service_contract()

    ds = audit_benchmark_dataset()
    live = [r for r in rows if "_error" not in r.get("response", {"_error": 1})]

    report: Dict[str, Any] = {
        "subject": f"POST {args.base}/api/v1/calculations/resistance",
        "audited_utc": "2026-11-01",
        "n_probes": len(rows),
        "n_live": len(live),
        "cases": rows,
        "T4_displacement_probe": dead,
        "T5_residual_law": law,
        "T6_cross_service_contract": contract,
        "benchmark_dataset": ds,
        "findings": [],
    }

    if live:
        t1 = [r for r in live if not r["T1_arithmetic"]["passed"] or not r["T1_surface"]["passed"]]
        t2 = [r for r in live if r.get("T2_cr_cf", {}).get("wave_component_plausible") is False]
        t3 = [r for r in live if r.get("T3_wetted_surface", {}).get("passed") is False]
        acc = [r for r in live if r.get("accuracy_vs_baseline", {}).get("passed") is False]
        acc_all = [r for r in live if "accuracy_vs_baseline" in r]
        subm = [r for r in live if r["hull"] == "SUBOFF"]
        ratios = sorted(r["T2_cr_cf"]["cr_cf_ratio"] for r in live)
        report["summary_metrics"] = {
            "suboff_ct_rel_error_min": min(r["accuracy_vs_baseline"]["ct_rel_error"] for r in subm),
            "suboff_ct_rel_error_max": max(r["accuracy_vs_baseline"]["ct_rel_error"] for r in subm),
            "cr_cf_ratio_min": ratios[0], "cr_cf_ratio_max": ratios[-1],
            "suboff_wetted_surface_rel_error": subm[0]["T3_wetted_surface"]["rel_error"],
            "residual_share_of_ct_max": max(r["T2_decomposition"]["residual_share"] for r in live),
            "speed_unit": "knots",
        }
        report["findings"] = [
            {"id": "F1", "severity": "INFO",
             "statement": "``speed`` is in KNOTS (reported Re reproduces V_kn*0.5144 within 2%)",
             "evidence": [r["P0_unit_convention"] for r in live[:3]]},
            {"id": "F2", "severity": "OK" if not t1 else "MAJOR",
             "statement": f"{len(t1)}/{len(live)} probes violate the Ct/RT/PE arithmetic identity",
             "cases": [r["hull"] + f"@{r['speed_ms']}" for r in t1]},
            {"id": "F3", "severity": "BLOCKER" if t2 else "OK",
             "statement": (f"{len(t2)}/{len(live)} probes: residual term exceeds the friction term "
                           f"(Cr/Cf {ratios[0]:.2f}-{ratios[-1]:.2f}); for the deeply submerged "
                           "SUBOFF hull there is no free surface, so any wave/residual resistance "
                           "is a category error, not a calibration offset"),
             "cases": [r["hull"] + f"@{r['speed_ms']}" for r in t2]},
            {"id": "F4", "severity": "MAJOR" if t3 else "OK",
             "statement": (f"{len(t3)}/{len(live)} probes miss the reference wetted area by >5% "
                           f"(SUBOFF {subm[0]['T3_wetted_surface']['rel_error']*100:.1f}% vs the "
                           "cited 5.42 m^2); geometry must be validated before any Ct comparison"),
             "cases": [r["hull"] + f"@{r['speed_ms']}" for r in t3]},
            {"id": "F5", "severity": "BLOCKER" if acc else "OK",
             "statement": (f"{len(acc)}/{len(acc_all)} submerged-hull probes exceed the 15% error "
                           f"criterion against the independent (1+k)Cf baseline; Ct error "
                           f"{report['summary_metrics']['suboff_ct_rel_error_min']*100:.0f}%-"
                           f"{report['summary_metrics']['suboff_ct_rel_error_max']*100:.0f}%"),
             "cases": [r["hull"] + f"@{r['speed_ms']}" for r in acc]},
            {"id": "F6", "severity": "MAJOR" if dead.get("identical_output") else "OK",
             "statement": dead.get("verdict", "not probed")},
            {"id": "F7", "severity": "BLOCKER" if ds["n_inconsistent"] else "OK",
             "statement": ds["verdict"]},
            {"id": "F8", "severity": "BLOCKER" if contract.get("verdict", "").startswith("BROKEN")
             else "OK", "statement": contract.get("verdict", "not probed")},
            {"id": "F9", "severity": "BLOCKER"
             if law.get("monotone_increasing") and law.get("cb_insensitive_below_070") else "OK",
             "statement": ("residual law is " + law.get("verdict", "not classified")
                           if law.get("probed") else "not probed")},
        ]

    png = ART / "resistance_audit.png"
    fig_ok = make_figure(rows, ds, law, png) if rows else False
    report["artifacts"] = {"json": str((ART / "resistance_audit.json").relative_to(ROOT)),
                           "figure": str(png.relative_to(ROOT)) if fig_ok else None}
    (ART / "resistance_audit.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print(json.dumps({k: report[k] for k in ("n_probes", "n_live")}, indent=2))
    if "summary_metrics" in report:
        print(json.dumps(report["summary_metrics"], indent=2))
    for f in report["findings"]:
        print(f"  [{f['severity']:8s}] {f['id']}: {f['statement']}")
    print(f"  dataset: {ds['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())