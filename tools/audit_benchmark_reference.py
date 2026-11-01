#!/usr/bin/env python3
"""Audit the ship-resistance benchmark reference dataset for physical self-consistency.

Motivation
----------
The platform's accuracy gate (``POST /api/v1/calculations/benchmark_validate``) scores a
computed total-resistance coefficient against ``experimental_ct`` taken from
``backend/data/benchmark_reference.json``.  A reference value that is itself impossible
makes every error percentage in the gate meaningless, so before any accuracy statement
the reference set has to survive a set of **definitional** checks that need no
literature at all — only the identities of ship hydrodynamics and geometry.

Checks implemented (all literature-free, hence reproducible):

  C1 froude_identity   Fn must equal V / sqrt(g L).            (definition)
  C2 friction_floor    Ct >= Cf(Re) with Cf from ITTC-1957.    (R_T >= R_F > 0 always)
  C3 rt_identity       R_T = Ct * 0.5 rho S V^2 where the dataset supplies S.
  C4 rt_feasibility    implied S = R_T/(0.5 rho V^2 Ct) must exceed the geometric
                       lower bound for a wetted surface enclosing volume R:
                       S >= (36 pi)^(1/3) R^(2/3) - A_wp   (isoperimetric inequality,
                       corrected for the free surface replacing part of the closed hull).
  C5 geometry_cb       R/(L B T) must reproduce the stated Cb.  (definition)
  C6 speed_envelope    a towed surface hull with Fn > 1.0 is outside any documented
                       model-test regime -> the case cannot be scored.

Usage
-----
    python3 tools/audit_benchmark_reference.py                # audit, human report
    python3 tools/audit_benchmark_reference.py --json out.json
    python3 tools/audit_benchmark_reference.py --apply        # rewrite the dataset
    python3 tools/audit_benchmark_reference.py --check        # exit 1 on any ERROR

``--apply`` performs only *definitional* repairs: Fn is recomputed from V and L; a
resistance value is recomputed from Ct where the dataset supplies a wetted surface;
values that violate a rigorous bound are set to null and marked ``withheld`` instead of
being silently kept or invented.  Literature values (``experimental_ct``) are never
touched — an audit must not manufacture agreement with itself.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = REPO_ROOT / "backend" / "data" / "benchmark_reference.json"

# Fresh-water tow-tank properties at ~20 C (the standard model-test reference state).
G = 9.81
RHO_FW = 998.2
NU_FW = 1.004e-6

FN_MAX_SURFACE_HULL = 1.0   # beyond this no towed displacement hull exists
CB_TOL_REL = 0.03           # 3 % definitional tolerance for Cb = R/(L B T)
FN_TOL_REL = 0.005          # 0.5 % for Fn = V/sqrt(gL)
RT_TOL_REL = 0.25           # 25 % when S is only *estimated*, not supplied
WITHHOLD_TOL_REL = 0.25


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------
def ittc57_friction(Re: float) -> float:
    """ITTC-1957 model-ship correlation line."""
    return 0.075 / (math.log10(Re) - 2.0) ** 2


def reynolds(length: float, speed: float, nu: float = NU_FW) -> float:
    return speed * length / nu


def froude(length: float, speed: float) -> float:
    return speed / math.sqrt(G * length)


def model_wetted_surface(length: float, beam: float, cb: float) -> float:
    """Wetted-surface *estimate* used by the platform kernel (pi B L (0.5 + 0.25 Cb)).

    Only used for cross-checks: it is a fit, not a reference value.
    """
    return math.pi * beam * length * (0.5 + 0.25 * cb)


def isoperimetric_min_area(volume: float) -> float:
    """Lower bound on the area of *any* closed surface enclosing ``volume``."""
    return (36.0 * math.pi) ** (1.0 / 3.0) * volume ** (2.0 / 3.0)


def scenario_geometry(case: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Extract L, B, T, Cb, volume, wetted surface from a case record."""
    geo = case.get("geometry") or {}
    if not geo:
        return None
    L = geo.get("length_m")
    if L is None:
        return None
    D = geo.get("diameter_m")
    B = geo.get("beam_m", D)
    T = geo.get("draft_m", D)
    Cb = geo.get("cb")
    if B is None or T is None:
        return None
    if Cb is None:                      # derive from displacement when possible
        vol = geo.get("displacement_m3")
        if vol:
            Cb = vol / (L * B * T)
        else:
            return None
    vol = Cb * L * B * T
    return {
        "length": L, "beam": B, "draft": T, "cb": Cb, "volume": vol,
        "wetted_surface": geo.get("wetted_surface_m2"),
        "waterplane": 0.0 if case.get("hull_class") == "axisymmetric_body" else 0.7 * L * B,
    }


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def audit_case(case: Dict[str, Any]) -> Dict[str, Any]:
    cid = case.get("case_id", "?")
    V = case.get("speed_ms")
    geo = scenario_geometry(case)
    findings: List[Dict[str, str]] = []

    def add(severity: str, check: str, message: str) -> None:
        findings.append({"case_id": cid, "severity": severity, "check": check, "message": message})

    rec: Dict[str, Any] = {
        "case_id": cid,
        "ship_type": case.get("ship_type"),
        "speed_ms": V,
        "hull_class": case.get("hull_class", "surface_ship"),
        "findings": findings,
    }
    if geo is None or V is None:
        add("ERROR", "geometry", "case lacks speed_ms or usable geometry block")
        rec["status"] = "ERROR"
        return rec

    L, Cb = geo["length"], geo["cb"]
    Re = reynolds(L, V)
    Cf = ittc57_friction(Re)
    Fn = froude(L, V)
    S_model = model_wetted_surface(L, geo["beam"], Cb)

    ct = case.get("experimental_ct")
    rt = case.get("experimental_rt_n")

    rec.update({
        "reynolds": Re, "cf_ittc57": Cf, "froude_calculated": Fn,
        "ct_reference": ct, "rt_reference": rt,
        "wetted_surface_supplied": geo["wetted_surface"],
        "wetted_surface_model_estimate": S_model,
        "volume_m3": geo["volume"],
        "cb_from_volume": geo["volume"] / (L * geo["beam"] * geo["draft"]),
        "fn_stated": case.get("froude_number"),
        "fn_calculated": Fn,
    })

    # C1 Froude identity
    fn_stated = case.get("froude_number")
    if fn_stated is not None:
        rel = abs(fn_stated - Fn) / Fn
        rec["fn_rel_error"] = rel
        if rel > FN_TOL_REL:
            implied_L = (V / fn_stated) ** 2 / G
            add("ERROR", "froude_identity",
                f"Fn stated {fn_stated:.4f} but V/sqrt(gL) = {Fn:.4f} "
                f"(rel {rel*100:.1f}%; implied length {implied_L:.1f} m vs {L:.3f} m)")

    # C2 friction floor
    if ct is not None and ct > 0:
        if ct < Cf:
            add("ERROR", "friction_floor",
                f"Ct={ct:.5f} is below the ITTC-57 friction floor Cf={Cf:.5f} "
                "(R_T >= R_F is a strict inequality): the reference Ct is impossible")
        rec["ct_over_cf"] = ct / Cf

    # C5 geometry / Cb identity
    if (case.get("hull_class", "surface_ship") != "axisymmetric_body"
            and case.get("geometry", {}).get("cb") is not None and geo.get("volume")):
        stated_cb = case["geometry"]["cb"]
        rel = abs(rec["cb_from_volume"] - stated_cb) / stated_cb
        rec["cb_rel_error"] = rel
        if rel > CB_TOL_REL:
            add("ERROR", "geometry_cb",
                f"Cb stated {stated_cb:.4f} but R/(LBT)={rec['cb_from_volume']:.4f} "
                f"(rel {rel*100:.1f}%)")
    g_geo = case.get("geometry", {})
    mass_kg = g_geo.get("displacement_kg")
    if mass_kg is None and g_geo.get("displacement_t"):
        mass_kg = g_geo["displacement_t"] * 1000.0
    if mass_kg and geo.get("volume"):
        implied_vol = mass_kg / RHO_FW
        rel = abs(implied_vol - geo["volume"]) / geo["volume"]
        rec["displacement_rel_error"] = rel
        # WARN only: the density behind a quoted displacement is not part of the record.
        if rel > CB_TOL_REL:
            add("WARN", "geometry_displacement",
                f"displacement {mass_kg:g} kg (={implied_vol:.3f} m3 at rho={RHO_FW}) vs "
                f"Cb*L*B*T = {geo['volume']:.3f} m3 (rel {rel*100:.1f}%)")

    # C6 speed envelope
    if rec["hull_class"] != "axisymmetric_body" and 0.6 < Fn <= FN_MAX_SURFACE_HULL:
        add("WARN", "speed_envelope",
            f"Fn={Fn:.3f}: beyond the tow-tank range of a {L:.2f} m model; the case is "
            "scored but its reference Ct should be re-derived from the workshop condition")
    if rec["hull_class"] != "axisymmetric_body" and Fn > FN_MAX_SURFACE_HULL:
        add("ERROR", "speed_envelope",
            f"Fn={Fn:.3f} > {FN_MAX_SURFACE_HULL}: no towed displacement hull operates "
            "here; the case speed does not belong to the stated model geometry")

    # C3/C4 resistance identity and feasibility
    if rt and ct:
        S_implied = rt / (0.5 * RHO_FW * V * V * ct)
        rec["wetted_surface_implied"] = S_implied
        S_model_est = model_wetted_surface(L, geo["beam"], Cb)
        bound = isoperimetric_min_area(geo["volume"]) - geo["waterplane"]
        rec["wetted_surface_lower_bound"] = bound
        rel_model = (S_implied - S_model_est) / S_model_est
        rec["rt_vs_model_rel"] = rel_model

        if geo["wetted_surface"]:
            rel = abs(S_implied - geo["wetted_surface"]) / geo["wetted_surface"]
            rec["rt_rel_error_vs_supplied_s"] = rel
            if rel > RT_TOL_REL:
                add("ERROR", "rt_identity",
                    f"R_T={rt:g} N with Ct={ct:.5f} implies S={S_implied:.3f} m2, but the "
                    f"dataset supplies S={geo['wetted_surface']:.3f} m2 (rel {rel*100:.0f}%): "
                    "R_T and Ct contradict each other")
        elif S_implied < bound:
            add("ERROR", "rt_feasibility",
                f"R_T={rt:g} N with Ct={ct:.5f} implies S={S_implied:.0f} m2, below the "
                f"geometric lower bound {bound:.0f} m2 for a wetted surface enclosing "
                f"{geo['volume']:.0f} m3: impossible")
        elif abs(rel_model) > RT_TOL_REL:
            add("WARN", "rt_vs_model_surface",
                f"implied S={S_implied:.3f} m2 vs kernel estimate {S_model_est:.3f} m2 "
                f"(rel {rel_model*100:+.0f}%); R_T cannot be tied to Ct without a "
                "reference wetted surface")

    severities = {f["severity"] for f in findings}
    if case.get("excluded"):
        # Retained for traceability (we never delete evidence), but not scored: the
        # findings explain why, and its ERRORs must not block the dataset gate.
        rec["status"] = "EXCLUDED"
        rec["exclusion_reason"] = case.get("exclusion_reason", "")
    elif "ERROR" in severities:
        rec["status"] = "ERROR"
    elif "WARN" in severities:
        rec["status"] = "WARN"
    else:
        rec["status"] = "OK"
    return rec


def audit_dataset(dataset: Dict[str, Any]) -> Dict[str, Any]:
    records = [audit_case(c) for c in dataset.get("cases", [])]
    findings = [f for r in records for f in r["findings"]]
    return {
        "tool": "tools/audit_benchmark_reference.py",
        "generated": date.today().isoformat(),
        "dataset": dataset.get("description", ""),
        "constants": {"g": G, "rho_fresh_water": RHO_FW, "nu": NU_FW},
        "tolerances": {"fn": FN_TOL_REL, "cb": CB_TOL_REL, "rt": RT_TOL_REL},
        "totals": {
            "cases": len(records),
            "ok": sum(r["status"] == "OK" for r in records),
            "warn": sum(r["status"] == "WARN" for r in records),
            "error": sum(r["status"] == "ERROR" for r in records),
            "excluded": sum(r["status"] == "EXCLUDED" for r in records),
            "findings": len(findings),
        },
        "findings": findings,
        "cases": records,
    }


# ---------------------------------------------------------------------------
# Definitional repair (--apply)
# ---------------------------------------------------------------------------
def apply_fixes(dataset: Dict[str, Any]) -> Dict[str, Any]:
    """Repair only definitional contradictions; never invent literature values."""
    changes: List[str] = []
    for case in dataset.get("cases", []):
        cid = case.get("case_id", "?")
        geo = scenario_geometry(case)
        if geo is None or not case.get("speed_ms"):
            continue
        L, V = geo["length"], case["speed_ms"]
        Fn = froude(L, V)
        ct = case.get("experimental_ct")

        # C1: Fn is a definition, always recomputable.
        if case.get("froude_number") is not None:
            rel = abs(case["froude_number"] - Fn) / Fn
            if rel > FN_TOL_REL:
                changes.append(f"{cid}: froude_number {case['froude_number']:.4f} -> {Fn:.4f}")
                case["froude_number"] = round(Fn, 4)
        case["froude_number_computed"] = round(Fn, 4)

        # C3: R_T follows from Ct *definitionally* where S is supplied.
        rt = case.get("experimental_rt_n")
        if not rt or not ct:
            continue
        S_supplied = geo["wetted_surface"]
        S_implied = rt / (0.5 * RHO_FW * V * V * ct)
        bound = isoperimetric_min_area(geo["volume"]) - geo["waterplane"]

        if S_supplied:
            rel = abs(S_implied - S_supplied) / S_supplied
            if rel > RT_TOL_REL:
                rt_new = ct * 0.5 * RHO_FW * S_supplied * V * V
                changes.append(
                    f"{cid}: experimental_rt_n {rt:g} -> {rt_new:.2f} N "
                    f"(derived from Ct with supplied S={S_supplied:g} m2)")
                case["experimental_rt_n"] = round(rt_new, 2)
                case["rt_provenance"] = "derived: R_T = Ct * 0.5 * rho * S_supplied * V^2"
        elif S_implied < bound:
            changes.append(
                f"{cid}: experimental_rt_n {rt:g} -> null (implied S={S_implied:.0f} m2 < "
                f"geometric lower bound {bound:.0f} m2)")
            case["experimental_rt_n"] = None
            case["rt_provenance"] = ("withheld: original value violates the isoperimetric "
                                     "lower bound on wetted area; no reference S available")
        elif abs((S_implied - model_wetted_surface(L, geo["beam"], geo["cb"]))
                 / model_wetted_surface(L, geo["beam"], geo["cb"])) > WITHHOLD_TOL_REL:
            changes.append(
                f"{cid}: experimental_rt_n {rt:g} -> null (implied S={S_implied:.2f} m2 vs "
                "kernel estimate "
                f"{model_wetted_surface(L, geo['beam'], geo['cb']):.2f} m2)")
            case["experimental_rt_n"] = None
            case["rt_provenance"] = ("withheld: value inconsistent with its own Ct and no "
                                     "reference wetted surface is available")

        # C6: cases outside any physical regime are not scored.
        if case.get("hull_class", "surface_ship") != "axisymmetric_body" and Fn > FN_MAX_SURFACE_HULL:
            if not case.get("excluded"):
                changes.append(f"{cid}: excluded=true (Fn={Fn:.3f} > {FN_MAX_SURFACE_HULL})")
            case["excluded"] = True
            case["exclusion_reason"] = (
                f"Fn={Fn:.3f} exceeds {FN_MAX_SURFACE_HULL} for the stated model length "
                f"{L} m: the case combines full-scale ship speeds with model geometry, so "
                "neither a pass nor a fail says anything about the kernel")

    dataset["audit"] = {
        "tool": "tools/audit_benchmark_reference.py",
        "applied": date.today().isoformat(),
        "policy": ("definitional repairs only: Fn recomputed from V and L; R_T recomputed "
                   "from Ct where a reference wetted surface exists; impossible R_T values "
                   "withheld rather than invented; experimental Ct untouched"),
        "changes": changes,
    }
    return dataset


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def render_markdown(audit: Dict[str, Any]) -> str:
    lines = ["# Benchmark reference audit", ""]
    t = audit["totals"]
    lines.append(f"cases={t['cases']}  ok={t['ok']}  warn={t['warn']}  "
                 f"error={t['error']}  excluded={t['excluded']}")
    lines.append("")
    lines.append("| case | V (m/s) | Fn calc | Re | Cf | Ct ref | Ct/Cf | status |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for c in audit["cases"]:
        lines.append(
            f"| {c['case_id']} | {c.get('speed_ms', float('nan')):.3f} "
            f"| {c.get('fn_calculated', float('nan')):.3f} "
            f"| {c.get('reynolds', 0):.3e} | {c.get('cf_ittc57', float('nan')):.5f} "
            f"| {c.get('ct_reference', float('nan')):.5f} "
            f"| {c.get('ct_over_cf', float('nan')):.2f} | {c['status']} |")
    lines.append("")
    if audit["findings"]:
        lines.append("## Findings")
        lines.append("")
        for f in audit["findings"]:
            lines.append(f"- **[{f['severity']}] {f['case_id']} ({f['check']})** {f['message']}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--json", help="write machine-readable audit here")
    ap.add_argument("--markdown", help="write markdown audit here")
    ap.add_argument("--apply", action="store_true", help="rewrite the dataset with definitional fixes")
    ap.add_argument("--check", action="store_true", help="exit 1 if any ERROR finding")
    args = ap.parse_args(argv)

    path = Path(args.dataset)
    dataset = json.loads(path.read_text(encoding="utf-8"))

    if args.apply:
        dataset = apply_fixes(dataset)
        path.write_text(json.dumps(dataset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for ch in dataset["audit"]["changes"]:
            print("fix:", ch)
        print(f"{len(dataset['audit']['changes'])} definitional fix(es) written to {path}")

    audit = audit_dataset(dataset)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
    md = render_markdown(audit)
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(md, encoding="utf-8")
    print(md)
    if args.check and audit["totals"]["error"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())