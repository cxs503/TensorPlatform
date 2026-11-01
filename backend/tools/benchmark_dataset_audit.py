"""Internal-consistency auditor for the ship resistance benchmark reference dataset.

Why this tool exists
--------------------
``backend/data/benchmark_reference.json`` is the *yardstick* used by
``POST /api/v1/calculations/benchmark_validate`` to decide PASS/FAIL of the
platform's resistance calculation (criterion: error <= 15 %).  A yardstick that
is internally inconsistent silently converts a calculation error into a metric
error: the endpoint will report PASS/FAIL against numbers that cannot all be
true at the same time.

This auditor therefore does **not** need external literature.  It only checks
relations that must hold by definition between columns of the same row:

  * Froude number      Fr   = V / sqrt(g * L)
  * Reynolds number    Re   = V * L / nu
  * ITTC-57 friction   Cf   = 0.075 / (log10(Re) - 2)^2      (hard lower bound
                              for Ct of a towed hull: Ct = (1+k)Cf + Cw >= Cf)
  * Towing force       Rt   = Ct * 0.5 * rho * V^2 * S
  * Block coefficient  Cb   = m / (rho * L * B * T)

Anything that violates one of those is a defect of the dataset, not a matter of
opinion.  Reference values that cannot be cross-checked locally are reported as
NOT_VERIFIABLE instead of being guessed.

Usage
-----
    python3 -m tools.benchmark_dataset_audit data/benchmark_reference.json
    python3 backend/tools/benchmark_dataset_audit.py --json out.json --md out.md
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- physical constants (documented, single source of truth) ----------------
G = 9.81                    # m/s^2, gravity
RHO_SEA = 1025.0            # kg/m^3, sea water density used by the platform
NU = 1.139e-6               # m^2/s, kinematic viscosity of water at 15 C (ITTC-57)

# --- tolerances ------------------------------------------------------------
FR_REL_TOL = 0.01           # 1 % on the Froude-number identity
RT_REL_TOL = 0.05           # 5 % on Rt = Ct*0.5*rho*V^2*S
CB_REL_TOL = 0.03           # 3 % on Cb = m/(rho L B T)
FR_MAX_PLAUSIBLE = 0.50     # above this a towing-tank benchmark row is not a
                            # displacement-hull row any more (planing / cavity)
CT_OVER_CF_MIN = 1.0        # Ct can never be below the ITTC-57 friction floor
CT_OVER_CF_MAX = 3.0        # (1+k)Cf + Cw above ~3 Cf is implausible

SEVERITY_ERROR = "ERROR"    # physically impossible / contradictory -> must fix
SEVERITY_WARN = "WARN"      # implausible or unverifiable -> must be annotated

DEFAULT_DATASET = Path(__file__).resolve().parent.parent / "data" / "benchmark_reference.json"


def ittc57_cf(reynolds: float) -> float:
    """ITTC-1957 model-ship friction coefficient."""
    if reynolds <= 1e4:
        raise ValueError(f"Reynolds number out of ITTC-57 validity: {reynolds}")
    return 0.075 / (math.log10(reynolds) - 2.0) ** 2


@dataclass
class Finding:
    check: str
    severity: str
    message: str
    filed: Optional[float] = None
    expected: Optional[float] = None
    rel_error_pct: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
        }
        if self.filed is not None:
            d["filed"] = self.filed
        if self.expected is not None:
            d["expected"] = self.expected
        if self.rel_error_pct is not None:
            d["rel_error_pct"] = self.rel_error_pct
        return d


@dataclass
class CaseAudit:
    case_id: str
    ship_type: str
    derived: Dict[str, Any] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_ERROR]

    @property
    def status(self) -> str:
        """Dataset-level verdict for this row (see README of data/)."""
        if self.errors:
            return "inconsistent_unresolved"
        if (self.derived.get("froude_number") or 0.0) > FR_MAX_PLAUSIBLE:
            return "out_of_validity_range"
        return "verified"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "ship_type": self.ship_type,
            "derived": self.derived,
            "findings": [f.as_dict() for f in self.findings],
            "n_errors": len(self.errors),
            "status": self.status,
        }


def _rel(filed: float, expected: float) -> float:
    if expected == 0:
        return float("inf")
    return abs(filed - expected) / abs(expected) * 100.0


def audit_case(case: Dict[str, Any]) -> CaseAudit:
    """Check one benchmark row against the identities listed in the module docstring."""
    geom = case.get("geometry", {}) or {}
    speed = float(case.get("speed_ms") or 0.0)
    length = float(geom.get("length_m") or 0.0)
    out = CaseAudit(case_id=case.get("case_id", "?"), ship_type=case.get("ship_type", "?"))

    if speed <= 0 or length <= 0:
        out.findings.append(Finding("input", SEVERITY_ERROR, "speed_ms or length_m missing"))
        return out

    fr = speed / math.sqrt(G * length)
    re = speed * length / NU
    cf = ittc57_cf(re)
    ct = float(case.get("experimental_ct") or 0.0)
    out.derived.update(
        froude_number=round(fr, 4),
        reynolds_number=re,
        cf_ittc57=round(cf, 6),
        ct_over_cf=round(ct / cf, 3) if cf else None,
    )

    # 1) Froude identity ---------------------------------------------------
    fr_filed = case.get("froude_number")
    if fr_filed is not None:
        err = _rel(float(fr_filed), fr)
        if err > FR_REL_TOL * 100.0:
            out.findings.append(Finding(
                "froude_identity", SEVERITY_ERROR,
                f"Fr filed {fr_filed} incompatible with V={speed} m/s, L={length} m",
                filed=fr_filed, expected=round(fr, 4), rel_error_pct=round(err, 2),
            ))

    # 2) Froude plausibility ----------------------------------------------
    if fr > FR_MAX_PLAUSIBLE:
        out.findings.append(Finding(
            "froude_range", SEVERITY_WARN,
            f"Fr={fr:.3f} exceeds {FR_MAX_PLAUSIBLE} : row is outside any towing-tank "
            "displacement-hull regime, Ct not usable as a benchmark target",
            expected=FR_MAX_PLAUSIBLE, filed=round(fr, 4),
        ))

    # 3) Friction floor ----------------------------------------------------
    ratio = ct / cf if cf else 0.0
    if ct <= 0:
        out.findings.append(Finding("ct_positive", SEVERITY_ERROR, "experimental_ct must be > 0"))
    elif ratio < CT_OVER_CF_MIN:
        out.findings.append(Finding(
            "friction_floor", SEVERITY_ERROR,
            f"Ct/Cf={ratio:.3f} < 1 : total resistance below the ITTC-57 friction floor",
            filed=round(ct, 6), expected=round(cf, 6), rel_error_pct=round((1 - ratio) * 100, 2),
        ))
    elif ratio > CT_OVER_CF_MAX:
        out.findings.append(Finding(
            "form_factor_plausibility", SEVERITY_WARN,
            f"Ct/Cf={ratio:.2f} > {CT_OVER_CF_MAX} : form + wave resistance implausibly large",
            filed=round(ratio, 3),
        ))

    # 4) Towing-force closure ---------------------------------------------
    rt_filed = case.get("experimental_rt_n")
    surface = geom.get("wetted_surface_m2")
    if surface:
        rt_calc = ct * 0.5 * RHO_SEA * speed ** 2 * float(surface)
        out.derived["rt_from_ct_n"] = round(rt_calc, 2)
        if rt_filed is not None:
            err = _rel(float(rt_filed), rt_calc)
            out.derived["rt_consistency_pct"] = round(err, 2)
            if err > RT_REL_TOL * 100.0:
                out.findings.append(Finding(
                    "rt_closure", SEVERITY_ERROR,
                    f"Rt filed {rt_filed} N contradicts Ct*0.5*rho*V^2*S with S={surface} m^2",
                    filed=float(rt_filed), expected=round(rt_calc, 2), rel_error_pct=round(err, 2),
                ))
    elif rt_filed is not None:
        out.derived["rt_status"] = "not_verifiable_no_wetted_surface"

    # 5) Block coefficient closure ----------------------------------------
    lb = geom.get("beam_m")
    draft = geom.get("draft_m")
    mass = None
    if geom.get("displacement_t") is not None:
        mass = float(geom["displacement_t"]) * 1000.0
    elif geom.get("displacement_kg") is not None:
        mass = float(geom["displacement_kg"])
    if lb and draft and mass:
        cb_calc = mass / (RHO_SEA * length * float(lb) * float(draft))
        out.derived["cb_from_displacement"] = round(cb_calc, 4)
        if geom.get("cb"):
            # density that would be required to reconcile the filed cb with the
            # filed displacement: outside 1000-1026 kg/m^3 is physically impossible
            out.derived["cb_implied_density_kg_m3"] = round(
                mass / (float(geom["cb"]) * length * float(lb) * float(draft)), 1)
        out.derived["displacement_implied_by_filed_cb_t"] = (
            round(float(geom.get("cb", cb_calc)) * RHO_SEA * length * float(lb) * float(draft) / 1000.0, 1)
        )
        cb_filed = geom.get("cb")
        if cb_filed is not None:
            err = _rel(float(cb_filed), cb_calc)
            out.derived["cb_consistency_pct"] = round(err, 2)
            if err > CB_REL_TOL * 100.0:
                out.findings.append(Finding(
                    "cb_closure", SEVERITY_ERROR,
                    f"cb filed {cb_filed} incompatible with displacement and L*B*T "
                    f"(implies {out.derived['displacement_implied_by_filed_cb_t']} t)",
                    filed=float(cb_filed), expected=round(cb_calc, 4), rel_error_pct=round(err, 2),
                ))
    elif geom.get("diameter_m"):
        # Body of revolution (submarine hull): a ship block coefficient is not
        # defined; report the equivalent volume-based value for information only.
        diameter = float(geom["diameter_m"])
        amid = math.pi * diameter ** 2 / 4.0
        volume = geom.get("displacement_m3")
        if volume is not None:
            out.derived["cb_body_of_revolution"] = round(float(volume) / (length * amid), 4)
        out.derived["cb_status"] = "not_applicable_body_of_revolution"

    return out


def audit_dataset(path: Path | str = DEFAULT_DATASET) -> Dict[str, Any]:
    """Audit every row of a benchmark dataset file and summarise the result."""
    path = Path(path)
    data = json.loads(path.read_text())
    audits = [audit_case(c) for c in data.get("cases", [])]

    by_check: Dict[str, int] = {}
    for a in audits:
        for f in a.findings:
            key = f"{f.check}:{f.severity}"
            by_check[key] = by_check.get(key, 0) + 1

    return {
        "dataset": str(path),
        "dataset_version": data.get("version"),
        "constants": {"g": G, "rho_sea": RHO_SEA, "nu": NU},
        "tolerances": {
            "fr_rel_pct": FR_REL_TOL * 100,
            "rt_rel_pct": RT_REL_TOL * 100,
            "cb_rel_pct": CB_REL_TOL * 100,
            "fr_max_plausible": FR_MAX_PLAUSIBLE,
            "ct_over_cf_range": [CT_OVER_CF_MIN, CT_OVER_CF_MAX],
        },
        "cases": [a.as_dict() for a in audits],
        "summary": {
            "n_cases": len(audits),
            "n_cases_with_errors": sum(1 for a in audits if a.errors),
            "n_findings_by_check": dict(sorted(by_check.items())),
            "status_counts": {
                s: sum(1 for a in audits if a.status == s)
                for s in ("verified", "out_of_validity_range", "inconsistent_unresolved")
            },
        },
    }


def audit_geometry_presets(presets: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Check ``SHIP_GEOMETRY`` presets of the endpoint (Cb vs L/B/T/displacement).

    Geometry must be validated before any Cb-dependent method (Holtrop) is fed
    with it - a wrong Cb enters every regression term of the method.
    """
    report: Dict[str, Any] = {}
    for name, g in presets.items():
        entry: Dict[str, Any] = {"geometry": g}
        length, beam, draft = g.get("length"), g.get("beam"), g.get("draft")
        disp = g.get("displacement")
        if not (length and beam and draft and disp):
            entry.update(applicable=False, reason="missing L/B/T/displacement", validated=False)
            report[name] = entry
            continue
        unit = str(g.get("displacement_unit", "kg"))
        mass_kg = float(disp) * (1000.0 if unit == "t" else 1.0)
        entry["displacement_unit"] = unit
        cb_calc = mass_kg / (RHO_SEA * float(length) * float(beam) * float(draft))
        entry["cb_from_geometry"] = round(cb_calc, 4)
        entry["cb_filed"] = g.get("cb")
        if g.get("cb") is None:
            entry.update(applicable=True, validated=False, reason="cb not declared")
            report[name] = entry
            continue
        err = _rel(float(g["cb"]), cb_calc)
        entry["cb_consistency_pct"] = round(err, 2)
        # displacement of a body of revolution is a volume proxy in this preset;
        # flag it explicitly instead of pretending it is a mass.
        if str(name).upper().startswith("SUBOFF"):
            entry.update(
                applicable=False,
                validated=False,
                reason="body of revolution: displacement/cb preset fields are placeholders, "
                       "ship Cb is undefined; exclude from Cb-driven methods",
            )
        else:
            entry["validated"] = err <= CB_REL_TOL * 100.0
            if not entry["validated"]:
                entry["reason"] = (
                    f"cb {g['cb']} inconsistent with displacement/L*B*T "
                    f"(implies cb={cb_calc:.4f})"
                )
        report[name] = entry
    return report


def to_markdown(report: Dict[str, Any]) -> str:
    """Render the audit as a compact markdown table set (for reports/PRs)."""
    lines = [
        f"# Benchmark dataset audit — {Path(report['dataset']).name} (v{report['dataset_version']})",
        "",
        "Constants: g={g} m/s^2, rho={rho_sea} kg/m^3, nu={nu} m^2/s".format(**report["constants"]),
        "",
        "| case | ship | Fr filed | Fr derived | dFr % | Re | Cf(ITTC-57) | Ct/Cf | Rt filed | Rt derived | dRt % | Cb filed | Cb derived | status |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in report["cases"]:
        d = c["derived"]
        found = {f["check"]: f for f in c["findings"]}
        fr_f = found.get("froude_identity", {}).get("filed")
        rt_f = found.get("rt_closure", {}).get("filed")
        cb_f = found.get("cb_closure", {}).get("filed")
        lines.append("| {id} | {st} | {ff} | {fr} | {dfr} | {re:.3e} | {cf} | {ratio} | {rf} | {rd} | {drt} | {cbf} | {cbd} | {status} |".format(
            id=c["case_id"], st=c["ship_type"],
            ff=("--" if fr_f is None else fr_f), fr=d.get("froude_number"),
            dfr=found.get("froude_identity", {}).get("rel_error_pct", 0.0),
            re=d["reynolds_number"], cf=d["cf_ittc57"], ratio=d.get("ct_over_cf"),
            rf=("--" if rt_f is None else rt_f), rd=d.get("rt_from_ct_n", "--"),
            drt=d.get("rt_consistency_pct", "--"),
            cbf=("--" if cb_f is None else cb_f), cbd=d.get("cb_from_displacement", "--"),
            status=c["status"],
        ))
    s = report["summary"]
    lines += [
        "",
        f"Cases: {s['n_cases']} total, {s['n_cases_with_errors']} with ERROR-level contradictions; "
        f"status counts: {s['status_counts']}",
        "",
        "Findings: " + json.dumps(s["n_findings_by_check"], ensure_ascii=False),
    ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dataset", nargs="?", default=str(DEFAULT_DATASET))
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--md", dest="md_out", default=None)
    ap.add_argument("--with-presets", action="store_true", help="also audit API geometry presets")
    args = ap.parse_args(argv)

    report = audit_dataset(args.dataset)
    if args.with_presets:
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from api.domain.ship_benchmark import SHIP_GEOMETRY, validate_geometry_preset

            report["geometry_presets"] = validate_geometry_preset(SHIP_GEOMETRY)
        except Exception as exc:  # pragma: no cover - import path issues only
            report["geometry_presets_error"] = f"{type(exc).__name__}: {exc}"

    md = to_markdown(report)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2))
    if args.md_out:
        Path(args.md_out).write_text(md)
    print(md)
    print(f"[audit] {report['summary']['n_cases_with_errors']} case(s) with ERROR-level contradictions")
    return 1 if report["summary"]["n_cases_with_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())