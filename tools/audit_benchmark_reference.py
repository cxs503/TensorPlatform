#!/usr/bin/env python3
"""Audit the ship-resistance benchmark reference dataset (physics-based).

The dataset ``backend/data/benchmark_reference.json`` is the foundation of every
resistance validation performed on this platform (see
``backend/api/domain/ship_benchmark.py``).  A reference case is only usable if it
is *internally* consistent and physically admissible.  This tool enforces three
machine-checkable criteria that require **no literature lookup**, only first
principles:

G1  Fluid declaration completeness
    Every case must declare the fluid it was measured in (``fluid.rho``,
    ``fluid.nu``) and its scale (``model`` / ``full``).  Without these, the
    Reynolds number and hence the friction floor are undefined.

G2  Friction floor (non-negotiable lower bound)
    Total resistance cannot be smaller than the ITTC-1957 flat-plate friction of
    the wetted surface, because the form factor is >= 1 and the residual
    (wave/viscous-pressure) resistance is >= 0:

        Ct >= Cf(Re),     Cf = 0.075 / (log10(Re) - 2)^2

    Any case whose implied total-resistance coefficient violates this is
    physically impossible and must not be used as a reference.

G3  Ct <-> Rt coherence
    The two experimental columns must describe the same physical measurement:

        Rt = Ct * 0.5 * rho * S * V^2

    When the wetted surface ``S`` is published in the case geometry the check is
    exact.  When it is not, ``S`` is estimated with the prismatic girth estimate

        S_est = L * (B + 2T) * sqrt(Cb)          (conventional hulls, 0.45<=Cb<=0.85)

    and the implied surface ``S_imp = Rt / (0.5 rho V^2 Ct)`` is compared with
    ``S_est``.  The estimate is only used to detect *gross* incoherence (a factor
    of ~4x-9x), never to claim a precise wetted surface.

G4  Geometry/kinematics consistency
    The declared Froude number must equal ``Fn = V / sqrt(g L)`` for the declared
    length (2% tolerance), and a displacement-hull benchmark case must sit at
    ``Fn <= 0.6``.  A case that mixes full-scale speeds with model-scale length
    is unusable no matter how self-consistent its coefficients look: the Froude
    number drives the residual-resistance term, so errors there are amplified by
    ``Fn**2.5``.

Exit status
-----------
0   dataset is self-consistent with the ``rt_status`` annotations it declares
    (i.e. every inconsistency is explicitly quarantined).
1   an undeclared inconsistency was found, or a required field is missing.
2   dataset file not readable / empty.

Usage
-----
    python3 tools/audit_benchmark_reference.py            # audit, print table
    python3 tools/audit_benchmark_reference.py --json out.json
    python3 tools/audit_benchmark_reference.py --write    # refresh annotations
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPO_ROOT / "backend" / "data" / "benchmark_reference.json"

# Tolerances for the coherence criterion.
SURFACE_RATIO_TOL = 0.30   # |S_imp / S_est - 1| beyond this => inconsistent
CT_REL_TOL = 0.05          # 5% between the two experimental columns
FROUDE_REL_TOL = 0.02      # 2% between declared and recomputed Froude number
FROUDE_MAX_DISPLACEMENT = 0.60  # displacement-hull plausibility ceiling

FRICTION_FLOOR_TOL = 1e-9  # purely numerical


def ittc57_cf(reynolds: float) -> float:
    """ITTC-1957 flat-plate frictional resistance coefficient."""
    return 0.075 / (math.log10(reynolds) - 2.0) ** 2


def estimate_wetted_surface(case: Dict[str, Any]) -> float | None:
    """Prismatic girth estimate S_est = L (B + 2T) sqrt(Cb)."""
    g = case.get("geometry") or {}
    L, B, T, Cb = g.get("length_m"), g.get("beam_m"), g.get("draft_m"), g.get("cb")
    if None in (L, B, T, Cb) or Cb <= 0:
        return None
    return L * (B + 2.0 * T) * math.sqrt(Cb)


def audit_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """Run G1-G3 on a single reference case."""
    cid = case.get("case_id", "<unnamed>")
    problems: List[str] = []
    out: Dict[str, Any] = {"case_id": cid, "ship_type": case.get("ship_type")}

    # ---- G1: fluid + scale declaration -------------------------------------
    fluid = case.get("fluid") or {}
    rho, nu = fluid.get("rho"), fluid.get("nu")
    scale = case.get("scale")
    if not rho or not nu:
        problems.append("G1 missing fluid declaration (fluid.rho / fluid.nu)")
    if scale not in ("model", "full"):
        problems.append("G1 missing/unknown scale (expected 'model' or 'full')")

    geom = case.get("geometry") or {}
    L = geom.get("length_m")
    V = case.get("speed_ms")
    Ct = case.get("experimental_ct")
    Rt = case.get("experimental_rt_n")
    out.update({"speed_ms": V, "length_m": L, "experimental_ct": Ct,
                "experimental_rt_n": Rt, "scale": scale})

    if not (rho and nu and L and V):
        out["verdict"] = "uncheckable"
        out["problems"] = problems
        return out

    # ---- Reynolds number and friction floor --------------------------------
    Re = V * L / nu
    Cf = ittc57_cf(Re)
    q = 0.5 * rho * V * V
    out.update({"reynolds": Re, "cf_ittc57": Cf, "dynamic_pressure_pa": q})

    # ---- G3: wetted surface (published or estimated) -----------------------
    S_pub = geom.get("wetted_surface_m2")
    S_est = S_pub if S_pub else estimate_wetted_surface(case)
    surface_source = "published" if S_pub else "estimated"

    if Ct and Rt and S_est:
        # Implied coefficient from the Rt column, at the available surface.
        Ct_implied = Rt / (q * S_est)
        floor_ok = Ct_implied >= Cf * (1.0 - FRICTION_FLOOR_TOL)
        ratio = Ct_implied / Ct
        coherent = abs(ratio - 1.0) <= CT_REL_TOL
        out.update({
            "wetted_surface_m2": S_est,
            "wetted_surface_source": surface_source,
            "ct_implied_from_rt": Ct_implied,
            "ct_ratio_implied_over_published": ratio,
            "friction_floor_ok": floor_ok,
            "ct_rt_coherent": coherent,
        })
        if not floor_ok:
            problems.append(
                "G2 friction floor violated: Ct_implied=%.5f < Cf(Re)=%.5f"
                " (total resistance below flat-plate friction)"
                % (Ct_implied, Cf))
        if not coherent:
            problems.append(
                "G3 Ct/Rt incoherent: Ct_implied/Ct=%.2f (tol %.0f%%) at %s surface"
                % (ratio, CT_REL_TOL * 100, surface_source))
        # Implied surface, informative when S was estimated.
        if not S_pub:
            out["wetted_surface_implied_m2"] = Rt / (q * Ct)
            out["surface_ratio_implied_over_est"] = out["wetted_surface_implied_m2"] / S_est
    elif Ct and Rt:
        problems.append("G3 cannot check Ct/Rt coherence: geometry incomplete")
        out["ct_rt_coherent"] = False
    else:
        out["ct_rt_coherent"] = None

    # Ct itself must sit above the friction floor (independent of Rt column).
    if Ct:
        out["ct_above_friction_floor"] = Ct >= Cf
        if Ct < Cf:
            problems.append("G2 published Ct=%.5f below Cf(Re)=%.5f" % (Ct, Cf))

    # ---- G4: geometry/kinematics consistency -------------------------------
    Fn_calc = V / math.sqrt(9.81 * L)
    Fn_decl = case.get("froude_number")
    # The Fn<=0.6 ceiling is a displacement-hull criterion; axisymmetric bodies
    # (submarine hulls) are legitimately towed up to Fn~1.
    hull_class = case.get("hull_class", "displacement")
    out["hull_class"] = hull_class
    out["froude_calc"] = Fn_calc
    out["froude_declared"] = Fn_decl
    froude_ok = True
    if hull_class == "displacement" and Fn_calc > FROUDE_MAX_DISPLACEMENT:
        froude_ok = False
        problems.append(
            "G4 non-physical Froude number: Fn=%.3f > %.2f for a displacement hull"
            % (Fn_calc, FROUDE_MAX_DISPLACEMENT))
    if Fn_decl is not None:
        frel = Fn_decl / Fn_calc
        out["froude_ratio_declared_over_calc"] = frel
        # implied reference length behind the declared Froude number
        out["length_implied_by_froude_m"] = V * V / (9.81 * Fn_decl ** 2) if Fn_decl else None
        if abs(frel - 1.0) > FROUDE_REL_TOL:
            froude_ok = False
            problems.append(
                "G4 declared Fn=%.3f != V/sqrt(gL)=%.3f (ratio %.2f, implied length "
                "%.1f m vs declared L=%.2f m)"
                % (Fn_decl, Fn_calc, frel, V * V / (9.81 * Fn_decl ** 2), L))
    out["froude_ok"] = froude_ok

    coherent_flag = out.get("ct_rt_coherent")
    if not froude_ok and not coherent_flag:
        out["verdict"] = "unusable"
    elif not froude_ok:
        out["verdict"] = "kinematically_inconsistent"
    elif coherent_flag and not problems:
        out["verdict"] = "coherent"
    else:
        out["verdict"] = "rt_inconsistent"
    out["problems"] = problems
    out["declared_rt_status"] = case.get("rt_status")
    return out


def _expected(verdict: str) -> tuple:
    """Expected (rt_status, froude_status) declarations for a verdict."""
    rt = {
        "coherent": "verified_coherent",
        "rt_inconsistent": "quarantined_inconsistent",
        "kinematically_inconsistent": "verified_coherent",
        "unusable": "quarantined_inconsistent",
        "uncheckable": "uncheckable",
    }[verdict]
    fr = {
        "coherent": "verified_consistent",
        "rt_inconsistent": "verified_consistent",
        "kinematically_inconsistent": "quarantined_inconsistent",
        "unusable": "quarantined_inconsistent",
        "uncheckable": "uncheckable",
    }[verdict]
    return rt, fr


def audit_dataset(data: Dict[str, Any]) -> Dict[str, Any]:
    cases = data.get("cases") or []
    results = [audit_case(c) for c in cases]
    violations: List[str] = []
    for case, res in zip(cases, results):
        exp_rt, exp_fn = _expected(res["verdict"])
        if case.get("rt_status") != exp_rt:
            violations.append("%s: rt_status=%r, expected %r (%s)"
                              % (res["case_id"], case.get("rt_status"), exp_rt,
                                 res["verdict"]))
        if case.get("froude_status") != exp_fn:
            violations.append("%s: froude_status=%r, expected %r (%s)"
                              % (res["case_id"], case.get("froude_status"), exp_fn,
                                 res["verdict"]))
        if res["verdict"] != "coherent" and not case.get("rt_note"):
            violations.append("%s: %s without an explanatory note"
                              % (res["case_id"], res["verdict"]))
    summary = {
        "cases_total": len(cases),
        "coherent": sum(r["verdict"] == "coherent" for r in results),
        "rt_inconsistent": sum(r["verdict"] == "rt_inconsistent" for r in results),
        "kinematically_inconsistent": sum(
            r["verdict"] == "kinematically_inconsistent" for r in results),
        "unusable": sum(r["verdict"] == "unusable" for r in results),
        "uncheckable": sum(r["verdict"] == "uncheckable" for r in results),
        "physically_usable": sum(r["verdict"] == "coherent" for r in results),
        "undeclared_violations": violations,
    }
    return {
        "dataset": str(DEFAULT_DATASET.name),
        "dataset_version": data.get("version"),
        "criteria": {
            "G1": "fluid(fluid.rho, fluid.nu) and scale declared",
            "G2": "Ct_implied >= Cf(Re), ITTC-1957 friction floor",
            "G3": "|Ct_implied/Ct - 1| <= %.2f; S_est = L(B+2T)sqrt(Cb)" % CT_REL_TOL,
            "G4": "|Fn_declared/V/sqrt(gL) - 1| <= %.2f and Fn <= %.2f"
                  % (FROUDE_REL_TOL, FROUDE_MAX_DISPLACEMENT),
        },
        "summary": summary,
        "cases": results,
    }


def annotate(data: Dict[str, Any], report: Dict[str, Any]) -> None:
    """Write audit verdicts back onto the dataset (numeric values are untouched)."""
    by_id = {r["case_id"]: r for r in report["cases"]}
    for case in data.get("cases", []):
        res = by_id.get(case.get("case_id"))
        if not res:
            continue
        rt_status, froude_status = _expected(res["verdict"])
        case["rt_status"] = rt_status
        case["froude_status"] = froude_status
        if res["verdict"] == "coherent":
            case.pop("rt_note", None)
        else:
            notes = list(res["problems"])
            notes.append(
                "consume experimental_ct only, and only for cases whose verdict is "
                "'coherent'; re-source this case from the primary publication "
                "before using it as a benchmark (tools/audit_benchmark_reference.py)")
            case["rt_note"] = "; ".join(notes)
        # A derived, physically admissible Rt is offered alongside the quarantined
        # column: Rt = Ct * 0.5 * rho * S * V^2.  It is a *derived* quantity, so its
        # accuracy is bounded by the wetted-surface estimate, not by the
        # unverified resistance column.
        if isinstance(res.get("wetted_surface_m2"), float) and res.get("experimental_ct"):
            q = res["dynamic_pressure_pa"]
            case["experimental_rt_derived_n"] = round(
                res["experimental_ct"] * q * res["wetted_surface_m2"], 4)
            case["experimental_rt_derived_note"] = (
                "derived: Ct*0.5*rho*S*V^2 with S=%s (%.4f m^2); not a "
                "measurement" % (res.get("wetted_surface_source"),
                                 res["wetted_surface_m2"]))
    data["audit"] = {
        "tool": "tools/audit_benchmark_reference.py",
        "summary": report["summary"],
    }


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--json", dest="json_out", default=None,
                    help="write the full audit report to this path")
    ap.add_argument("--write", action="store_true",
                    help="write verdicts back into the dataset file")
    args = ap.parse_args(argv)

    path = Path(args.dataset)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("ERROR: cannot read dataset %s: %s" % (path, exc), file=sys.stderr)
        return 2
    if not data.get("cases"):
        print("ERROR: dataset has no cases", file=sys.stderr)
        return 2

    report = audit_dataset(data)

    print("%-12s %-10s %10s %8s %8s %8s %6s  %s" %
          ("case", "type", "Re", "Cf", "Ct", "Ct_imp", "Fn", "verdict"))
    for r in report["cases"]:
        def _f(key, fmt):
            val = r.get(key)
            return fmt % val if isinstance(val, (int, float)) else "%8s" % "n/a"
        print("%-12s %-10s %s %s %s %s %s  %s" % (
            r["case_id"], r.get("ship_type", ""), _f("reynolds", "%10.2e"),
            _f("cf_ittc57", "%8.5f"), _f("experimental_ct", "%8.5f"),
            _f("ct_implied_from_rt", "%8.5f"), _f("froude_calc", "%6.3f"),
            r["verdict"]))
        for p in r["problems"]:
            print("    - %s" % p)
    s = report["summary"]
    print("\nsummary: total=%d coherent=%d rt_inconsistent=%d "
          "kinematically_inconsistent=%d unusable=%d uncheckable=%d"
          % (s["cases_total"], s["coherent"], s["rt_inconsistent"],
             s["kinematically_inconsistent"], s["unusable"], s["uncheckable"]))
    for v in s["undeclared_violations"]:
        print("VIOLATION: %s" % v)

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("audit report -> %s" % args.json_out)

    if args.write:
        annotate(data, report)
        # Re-audit so the stored summary reflects the annotated state.
        report = audit_dataset(data)
        s = report["summary"]
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        print("annotations -> %s" % path)
        print("post-annotation violations: %d"
              % len(report["summary"]["undeclared_violations"]))

    return 1 if s["undeclared_violations"] else 0


if __name__ == "__main__":
    sys.exit(main())