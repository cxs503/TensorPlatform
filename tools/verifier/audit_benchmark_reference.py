#!/usr/bin/env python3
"""Benchmark reference dataset audit (verification debt payoff, tp-ver-8).

Motivation
----------
``backend/api/domain/ship_benchmark.py`` publishes a pass/fail verdict
(``pass = error_pct <= 15%``) for the platform's resistance model by comparing
the computed ``Ct`` against ``experimental_ct`` from
``backend/data/benchmark_reference.json``.  The same endpoint also publishes the
whole raw case dicts (``GET /calculations/benchmark_cases``), including
``experimental_rt_n`` (total resistance) and ``froude_number``.  Those two
columns had **never been checked against the geometry/coefficient columns**, so
a consumer could see a "verified" reference dataset in which the numbers are
mutually impossible.

This tool re-derives everything that is *exactly* derivable from the dataset
itself and reports per-case PASS / WARN / FAIL / WITHHELD:

C1 speed_unit          speed_ms == speed_kn * 0.514444            (tol 0.2%)
C2 froude_consistency  Fn_declared == V / sqrt(g*L)               (tol 0.5%)
C3 froude_physical     Fn <= 1.0 (model-test sanity bound)
C4 displacement_cb     displ == rho * Cb * L * B * T              (tol 10%, WARN)
C5 ct_rt_consistency   experimental_rt_n == 0.5*rho*V^2*S*Ct      (tol 5%)
C6 implied_wetted_area S_implied = Rt/(0.5 rho V^2 Ct) within a
                       plausibility band of the geometry-implied wetted
                       surface (ratio in [0.5, 2.0])
C7 preset_sanity       SHIP_GEOMETRY presets in ship_benchmark.py are not
                       degenerate (displacement must not be a copy of a
                       length-like dimension, Cb in (0.3, 0.9))
C8 friction_floor      analytic bound: Ct >= Cf_ITTC57(Re) and the form+wave
                       allowance Ct/Cf stays within [1.02, 2.5] (WARN outside,
                       FAIL below the bound)

A case whose ``data_status`` is anything other than ``"ok"`` is *declared*
unusable: its checks are reported WITHHELD with the declared reason and do not
count as failures (honest-FAIL discipline: hide nothing, declare everything).

Usage
-----
    python tools/verifier/audit_benchmark_reference.py \
        --out artifacts/verifier/benchmark_audit.json [--figures DIR] [--strict]

Exit code is 1 under ``--strict`` when any undeclared FAIL is present.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

G = 9.81
KNOT_TO_MS = 0.514444
# Tolerances (relative, dimensionless)
TOL_SPEED = 0.002
TOL_FROUDE = 0.005
TOL_DISPL = 0.10
TOL_CT_RT = 0.05
# Wetted-surface plausibility band, as a ratio of S_implied to a
# volume-based estimate S_est = K_S * nabla^(2/3) (K_S = 5.5 is central for
# full-form/merchant hulls; fine forms sit a little higher).  The band is wide
# on purpose: it is a plausibility screen, not a reference value.
K_S = 5.5
BAND_S = (0.5, 2.0)
FN_PHYS_LIMIT = 1.0
# Ct/Cf plausibility band: friction is an analytic lower bound; form factor plus
# wave resistance keeps total resistance within ~2.5x of the ITTC-57 friction line
CF_RATIO_BAND = (1.02, 2.5)


def ittc57_cf(re: float) -> float:
    """ITTC-1957 model-ship correlation line."""
    return 0.075 / (math.log10(re) - 2.0) ** 2


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _rel_err(actual: float, expected: float) -> float:
    if expected == 0:
        return 0.0 if actual == 0 else math.inf
    return abs(actual - expected) / abs(expected)


def _case_density(case: Dict[str, Any]) -> float:
    """Reference water density used by the case (default: sea water)."""
    rho = _num(case.get("reference_density_kg_m3"))
    return rho if rho else 1025.0


def _volume(case: Dict[str, Any]) -> Optional[float]:
    """Displaced volume [m^3] from the geometry block, if recoverable."""
    geo = case.get("geometry") or {}
    rho = _case_density(case)
    if _num(geo.get("displacement_kg")) is not None:
        return _num(geo["displacement_kg"]) / rho
    if _num(geo.get("displacement_t")) is not None:
        return _num(geo["displacement_t"]) * 1000.0 / rho
    return None


def _base_area(case: Dict[str, Any]) -> tuple[Optional[float], str]:
    """Best available wetted-surface yardstick and its provenance."""
    geo = case.get("geometry") or {}
    s = _num(geo.get("wetted_surface_m2"))
    if s is not None:
        return s, "stated_wetted_surface_m2"
    vol = _volume(case)
    if vol is not None and vol > 0:
        return K_S * vol ** (2.0 / 3.0), f"volume_estimate(K_S={K_S})"
    return None, "unavailable"


def _check(name: str, status: str, detail: str, **extra: Any) -> Dict[str, Any]:
    out = {"check": name, "status": status, "detail": detail}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


# ---------------------------------------------------------------------------
# per-case audit
# ---------------------------------------------------------------------------
def audit_case(case: Dict[str, Any]) -> Dict[str, Any]:
    cid = case.get("case_id", "?")
    geo = case.get("geometry") or {}
    rho = _case_density(case)
    declared_status = str(case.get("data_status", "ok"))
    checks: List[Dict[str, Any]] = []

    speed_ms = _num(case.get("speed_ms"))
    speed_kn = _num(case.get("speed_kn"))
    fn_declared = _num(case.get("froude_number"))
    ct = _num(case.get("experimental_ct"))
    rt = _num(case.get("experimental_rt_n"))
    length = _num(geo.get("length_m"))
    beam = _num(geo.get("beam_m"))
    draft = _num(geo.get("draft_m"))
    cb = _num(geo.get("cb"))

    # C1 speed unit consistency -------------------------------------------
    if speed_ms is not None and speed_kn is not None:
        expected = speed_kn * KNOT_TO_MS
        err = _rel_err(speed_ms, expected)
        checks.append(_check(
            "speed_unit", "PASS" if err <= TOL_SPEED else "FAIL",
            f"{speed_kn} kn -> {expected:.4f} m/s vs declared {speed_ms:.4f} m/s "
            f"(rel err {err * 100:.3f}%)",
            rel_err=err,
        ))
    else:
        checks.append(_check("speed_unit", "WITHHELD", "speed_kn/speed_ms not both present"))

    # C2 Froude consistency ------------------------------------------------
    fn_recomputed = None
    if speed_ms is not None and length:
        fn_recomputed = speed_ms / math.sqrt(G * length)
    if fn_recomputed is not None and fn_declared is not None:
        err = _rel_err(fn_declared, fn_recomputed)
        implied_length = (speed_ms / fn_declared) ** 2 / G if fn_declared else None
        checks.append(_check(
            "froude_consistency", "PASS" if err <= TOL_FROUDE else "FAIL",
            f"Fn declared {fn_declared:.4f} vs recomputed {fn_recomputed:.4f} from "
            f"V={speed_ms} m/s, L={length} m (rel err {err * 100:.2f}%)"
            + (f"; declared Fn implies L={implied_length:.1f} m" if err > TOL_FROUDE and implied_length else ""),
            rel_err=err,
        ))
    else:
        checks.append(_check("froude_consistency", "WITHHELD", "speed_ms or length_m missing"))

    # C3 Froude physical sanity -------------------------------------------
    fn_for_phys = fn_recomputed if fn_recomputed is not None else fn_declared
    if fn_for_phys is not None:
        checks.append(_check(
            "froude_physical", "PASS" if fn_for_phys <= FN_PHYS_LIMIT else "FAIL",
            f"Fn={fn_for_phys:.3f} vs sanity bound {FN_PHYS_LIMIT} (displacement hull / model test)",
            value=fn_for_phys,
        ))
    else:
        checks.append(_check("froude_physical", "WITHHELD", "no Froude number recoverable"))

    # C4 displacement vs Cb*L*B*T -----------------------------------------
    if cb and length and beam and draft:
        vol_geometric = cb * length * beam * draft
        vol_actual = _volume(case)
        if vol_actual is not None and vol_geometric > 0:
            err = _rel_err(vol_actual, vol_geometric)
            status = "PASS" if err <= TOL_DISPL else "WARN"
            checks.append(_check(
                "displacement_cb", status,
                f"Displaced volume {vol_actual:.4g} m^3 vs Cb*L*B*T = {vol_geometric:.4g} m^3 "
                f"(rel err {err * 100:.1f}%, tol {TOL_DISPL * 100:.0f}%; "
                "definitional differences Lpp/Lwl, appendages excluded may explain)",
                rel_err=err,
            ))
        else:
            checks.append(_check("displacement_cb", "WITHHELD", "no displacement in geometry"))
    else:
        checks.append(_check("displacement_cb", "WITHHELD", "Cb / L / B / T incomplete"))

    # C5 / C6 Ct <-> Rt consistency and implied wetted area ----------------
    s_stated = _num(geo.get("wetted_surface_m2"))
    base_area, area_source = _base_area(case)
    s_implied = None
    if rt is not None and ct and speed_ms:
        s_implied = rt / (0.5 * rho * speed_ms ** 2 * ct)

    if rt is not None and ct and speed_ms and s_stated:
        rt_expected = 0.5 * rho * speed_ms ** 2 * s_stated * ct
        err = _rel_err(rt, rt_expected)
        checks.append(_check(
            "ct_rt_consistency", "PASS" if err <= TOL_CT_RT else "FAIL",
            f"Rt stated {rt:.4g} N vs 0.5*rho*V^2*S*Ct = {rt_expected:.4g} N "
            f"(rho={rho:.0f}, S={s_stated} m^2, rel err {err * 100:.1f}%)",
            rel_err=err, s_implied_m2=s_implied,
        ))
    else:
        checks.append(_check(
            "ct_rt_consistency", "WITHHELD",
            "Rt absent or wetted surface unknown - identity Rt=0.5*rho*V^2*S*Ct not evaluable",
            s_implied_m2=s_implied,
        ))

    if s_implied is not None and base_area:
        ratio = s_implied / base_area
        low, high = BAND_S
        checks.append(_check(
            "implied_wetted_area",
            "PASS" if low <= ratio <= high else "FAIL",
            f"S implied by (Rt,Ct) = {s_implied:.4g} m^2 vs {area_source} "
            f"{base_area:.4g} m^2 (ratio {ratio:.3f}, band [{low},{high}])",
            ratio=ratio,
        ))
    else:
        checks.append(_check("implied_wetted_area", "WITHHELD",
                             "implied area or geometry yardstick unavailable"))

    # C8 friction floor (analytic ITTC-1957 bound) ------------------------
    nu = _num(case.get("reference_kinematic_viscosity_m2_s"))
    if ct and speed_ms and length and nu:
        re = speed_ms * length / nu
        cf = ittc57_cf(re)
        ratio_cf = ct / cf
        low, high = CF_RATIO_BAND
        if ratio_cf < low:
            status = "FAIL"
        elif ratio_cf > high:
            status = "WARN"
        else:
            status = "PASS"
        checks.append(_check(
            "friction_floor", status,
            f"Re={re:.3g}, Cf(ITTC-57)={cf:.5f}, Ct/Cf={ratio_cf:.3f} "
            f"(band [{low},{high}]; Ct below Cf is physically impossible)",
            re=re, cf=cf, ct_over_cf=ratio_cf,
        ))
    else:
        checks.append(_check("friction_floor", "WITHHELD",
                             "Ct, speed, length or kinematic viscosity missing"))

    # verdict --------------------------------------------------------------
    statuses = [c["status"] for c in checks]
    if declared_status != "ok":
        verdict = "WITHHELD"
    elif "FAIL" in statuses:
        verdict = "FAIL"
    elif "WARN" in statuses:
        verdict = "WARN"
    elif all(s == "WITHHELD" for s in statuses):
        verdict = "WITHHELD"
    else:
        verdict = "PASS"

    return {
        "case_id": cid,
        "ship_type": case.get("ship_type"),
        "declared_data_status": declared_status,
        "declared_note": case.get("data_status_reason"),
        "verdict": verdict,
        "froude_declared": fn_declared,
        "froude_recomputed": fn_recomputed,
        "rt_stated_n": rt,
        "rt_status": case.get("rt_status"),
        "s_implied_m2": s_implied,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# preset audit (SHIP_GEOMETRY in backend/api/domain/ship_benchmark.py)
# ---------------------------------------------------------------------------
def audit_presets(presets: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check that the geometry presets fed to the resistance API are sane.

    A preset whose ``displacement`` is bit-identical to a length-like dimension is
    the fingerprint of a copy/paste error (v1.0 SUBOFF had
    ``displacement = cb = diameter = 0.508``).  The displacement *unit* is also
    audited: v1.0 mixed kg (model scale) and tonnes (KVLCC2) with no declaration,
    which changes the payload sent to the resistance service by 1000x.
    """
    out = []
    for name, geo in presets.items():
        fails: List[str] = []
        warns: List[str] = []
        L, B, T = _num(geo.get("length")), _num(geo.get("beam")), _num(geo.get("draft"))
        disp, cb = _num(geo.get("displacement")), _num(geo.get("cb"))
        unit = geo.get("displacement_unit")
        if not (L and B and T and disp and cb):
            fails.append("missing dimension")
        else:
            for dim_name, dim in (("length", L), ("beam", B), ("draft", T)):
                if abs(disp - dim) <= 1e-9 * max(1.0, abs(dim)):
                    fails.append(f"displacement == {dim_name} ({dim}) - copy/paste fingerprint")
            cb_matches_dim = abs(cb - B) <= 1e-9 * max(1.0, abs(B)) or abs(cb - T) <= 1e-9 * max(1.0, abs(T))
            disp_matches_dim = any(abs(disp - d) <= 1e-9 * max(1.0, abs(d)) for d in (L, B, T))
            if cb_matches_dim and disp_matches_dim:
                fails.append(f"cb == {cb} and displacement both equal a hull dimension - "
                             "double copy/paste fingerprint")
            elif cb_matches_dim:
                warns.append(f"cb == {cb} numerically equals beam/draft; provenance assumed, "
                             "declared as assumption")
            if not 0.3 <= cb <= 0.9:
                fails.append(f"cb={cb} outside (0.3, 0.9)")

            vol = cb * L * B * T                       # displaced volume [m^3]
            mass_kg = 1000.0 * vol                     # fresh-water equivalent mass [kg]
            candidates = {"kg": mass_kg, "t": mass_kg / 1000.0}
            errs = {u: abs(disp - m) / m for u, m in candidates.items() if m > 0}
            best_unit, best_err = min(errs.items(), key=lambda kv: kv[1])
            if unit is None:
                warns.append(
                    "displacement unit not declared; best fit is "
                    f"'{best_unit}' ({best_err * 100:.1f}% vs 1000*Cb*L*B*T = {mass_kg:.4g} kg)"
                )
            elif unit not in candidates:
                fails.append(f"unknown displacement_unit '{unit}'")
            elif errs[unit] > 0.20:
                fails.append(
                    f"displacement {disp} {unit} vs 1000*Cb*L*B*T = {mass_kg:.4g} kg "
                    f"({errs[unit] * 100:.1f}% off, tol 20%)"
                )
            elif errs[unit] > 0.10:
                warns.append(
                    f"displacement {disp} {unit} vs 1000*Cb*L*B*T = {mass_kg:.4g} kg "
                    f"({errs[unit] * 100:.1f}% off; definitional/appendage differences possible)"
                )
            if unit is not None and best_unit != unit and best_err < 0.05:
                warns.append(f"declared unit '{unit}' but the number fits '{best_unit}' better")

        status = "FAIL" if fails else ("WARN" if warns else "PASS")
        out.append({
            "preset": name,
            "status": status,
            "fails": fails,
            "warns": warns,
            "geometry": geo,
            "declared_validity": geo.get("method_validity"),
        })
    return out


def audit_dataset(data: Dict[str, Any],
                  presets: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    cases = data.get("cases", [])
    case_reports = [audit_case(c) for c in cases]
    preset_reports = audit_presets(presets) if presets else []

    counts: Dict[str, int] = {}
    for r in case_reports:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    preset_fail = sum(1 for p in preset_reports if p["status"] == "FAIL")
    preset_warn = sum(1 for p in preset_reports if p["status"] == "WARN")
    undeclared_fail = sum(1 for r in case_reports if r["verdict"] == "FAIL")

    return {
        "dataset_version": data.get("version"),
        "dataset_description": data.get("description"),
        "generated_by": "tools/verifier/audit_benchmark_reference.py",
        "n_cases": len(cases),
        "case_verdict_counts": counts,
        "undeclared_fail_cases": undeclared_fail,
        "preset_fail_count": preset_fail,
        "preset_warn_count": preset_warn,
        "strict_pass": undeclared_fail == 0 and preset_fail == 0,
        "cases": case_reports,
        "presets": preset_reports,
    }


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def make_figures(report: Dict[str, Any], outdir: Path) -> List[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    cases = report["cases"]
    ok = [c for c in cases if c["s_implied_m2"] and c.get("rt_stated_n")]
    if ok:
        labels = [c["case_id"] for c in ok]
        base = []
        implied = []
        for c in ok:
            b, _ = _base_area(next(x for x in report["_raw_cases"] if x["case_id"] == c["case_id"]))
            base.append(b if b else float("nan"))
            implied.append(c["s_implied_m2"])
        fig, ax = plt.subplots(figsize=(8, 4.2))
        idx = range(len(labels))
        ax.semilogy(idx, base, "o-", label="geometry-implied wetted surface", color="#1f77b4")
        ax.semilogy(idx, implied, "s--", label="wetted surface implied by (Rt, Ct)", color="#d62728")
        ax.set_xticks(list(idx))
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
        ax.set_ylabel("wetted surface [m²]")
        ax.set_title("Reference dataset audit: Ct and Rt are mutually inconsistent")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        p = outdir / "benchmark_audit_wetted_area.png"
        fig.savefig(p, dpi=150)
        plt.close(fig)
        paths.append(p)

    fig, ax = plt.subplots(figsize=(8, 3.6))
    labels, decl, rec = [], [], []
    for c in cases:
        if c["froude_declared"] is not None and c["froude_recomputed"] is not None:
            labels.append(c["case_id"])
            decl.append(c["froude_declared"])
            rec.append(c["froude_recomputed"])
    if labels:
        x = range(len(labels))
        ax.plot(x, decl, "o-", label="Fn declared in dataset")
        ax.plot(x, rec, "x--", label="Fn = V/sqrt(gL) from stated geometry")
        ax.axhline(FN_PHYS_LIMIT, color="k", lw=0.8, ls=":", label="Fn = 1.0 sanity bound")
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
        ax.set_ylabel("Froude number")
        ax.set_title("Froude number column vs geometry-consistent recomputation")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        p = outdir / "benchmark_audit_froude.png"
        fig.savefig(p, dpi=150)
        plt.close(fig)
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_presets(module_path: Path) -> Optional[Dict[str, Dict[str, Any]]]:
    """Extract SHIP_GEOMETRY without importing FastAPI (lightweight parse)."""
    if not module_path.exists():
        return None
    import re
    src = module_path.read_text()
    m = re.search(r"SHIP_GEOMETRY\s*=\s*(\{.*?\n\})", src, re.S)
    if not m:
        return None
    try:
        return eval(m.group(1), {"__builtins__": {}}, {})  # literal dict only
    except Exception:
        return None


def main(argv: Optional[List[str]] = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", default=str(repo_root / "backend/data/benchmark_reference.json"))
    ap.add_argument("--presets-module",
                    default=str(repo_root / "backend/api/domain/ship_benchmark.py"))
    ap.add_argument("--out", default=None, help="write audit JSON here")
    ap.add_argument("--figures", default=None, help="write PNG figures into this directory")
    ap.add_argument("--strict", action="store_true", help="exit 1 on undeclared FAIL")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.dataset).read_text())
    presets = _load_presets(Path(args.presets_module))
    report = audit_dataset(data, presets)
    report["_raw_cases"] = data.get("cases", [])

    for c in report["cases"]:
        fails = [k["check"] for k in c["checks"] if k["status"] in ("FAIL", "WARN")]
        print(f"{c['case_id']:<14} {c['verdict']:<9} " + (", ".join(fails) if fails else "-"))
        for k in c["checks"]:
            if k["status"] in ("FAIL", "WARN"):
                print(f"    {k['status']:<5} {k['check']}: {k['detail']}")
    for p in report["presets"]:
        print(f"preset:{p['preset']:<12} {p['status']:<5} "
              + "; ".join(p["fails"] + p["warns"]))
    print(f"\ncases={report['n_cases']} verdicts={report['case_verdict_counts']} "
          f"undeclared_fail={report['undeclared_fail_cases']} preset_fail={report['preset_fail_count']} "
          f"strict_pass={report['strict_pass']}")

    if args.figures:
        for p in make_figures(report, Path(args.figures)):
            print(f"figure: {p}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in report.items() if not k.startswith("_")}
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"audit: {out}")

    if args.strict and not report["strict_pass"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())