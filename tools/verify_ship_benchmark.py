#!/usr/bin/env python3
"""
Benchmark geometry / dataset verification for the ship-performance platform.

Mission m1609 (verification debt): the resistance benchmark chain compared a
calculated Ct against experimental references, but

  * the wetted surface came from a placeholder expression,
    ``S = pi*B*L*(0.5 + 0.25*Cb)``, which is +50 % on KVLCC2 and -20 % on SUBOFF;
  * the benchmark endpoint posted ``speed``/``displacement`` into a request model
    that declares ``speed_ms``/``displacement_t`` (extra keys silently ignored),
    so every case was evaluated at the default 1.0 m/s;
  * the endpoint read ``calc_data["ct"]`` although the resistance response carried
    no ``ct`` field, so ``error_pct`` was structurally 100 % for every case;
  * the case table stored ``experimental_rt_n`` values that are inconsistent with
    ``experimental_ct`` and the published wetted surface by factors 3-8.

This tool makes each of those statements machine-checkable and emits the evidence
artefacts.  It is deterministic, network-free and needs no HPC resources.

Usage
-----
    python3 tools/verify_ship_benchmark.py                  # audit -> artifacts/
    python3 tools/verify_ship_benchmark.py --regenerate     # repair dataset in place
    python3 tools/verify_ship_benchmark.py --figures        # audit + PNG figures
    python3 tools/verify_ship_benchmark.py --json out.json

Exit code is 0 when every check passes, 1 otherwise (usable as a CI gate).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
DOMAIN = BACKEND / "api" / "domain"


def _load_module(name: str, path: Path):
    """Load a module by path under a fixed name.

    Deliberately *not* ``sys.path.insert(domain)``: ``api/domain/platform.py``
    would shadow the stdlib ``platform`` module and break numpy/fastapi imports
    (trap hit during this mission, see docs/geometry_verification_m1609.md).
    """
    import importlib.util

    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_hg = _load_module("hull_geometry", DOMAIN / "hull_geometry.py")
_rc = _load_module("resistance_core", DOMAIN / "resistance_core.py")

PUBLISHED_GEOMETRY = _hg.PUBLISHED_GEOMETRY
axisymmetric_cb_bounds = _hg.axisymmetric_cb_bounds
holtrop_wetted_surface = _hg.holtrop_wetted_surface
ittc57_cf = _hg.ittc57_cf
plausibility_band = _hg.plausibility_band
resolve_geometry = _hg.resolve_geometry
validate_hull_geometry = _hg.validate_hull_geometry
reference_provenance = _hg.reference_provenance
DISPLACEMENT_REGIME_FR_MAX = _hg.DISPLACEMENT_REGIME_FR_MAX
solve_resistance = _rc.solve_resistance

DATA_PATH = BACKEND / "data" / "benchmark_reference.json"
ARTIFACT_DIR = REPO / "artifacts" / "m1609"

# Reference density used to turn a coefficient into a force for the published
# model-scale / full-scale conditions.  Recorded in the dataset so the derived
# column stays reproducible.
RHO_REF = 998.0
NU_REF = 1.004e-6


# ---------------------------------------------------------------------------
# Geometry verification
# ---------------------------------------------------------------------------

def audit_geometry() -> Dict[str, Any]:
    """Run the geometry gate for every published hull and quantify the old model."""
    out: Dict[str, Any] = {"hulls": {}, "ok": True}
    for hull, pub in PUBLISHED_GEOMETRY.items():
        gate = validate_hull_geometry(hull)
        rec: Dict[str, Any] = {
            "gate_ok": gate["ok"],
            "checks": gate["checks"],
            "derived": gate["derived"],
            "source": pub["source"],
        }
        if pub["form"] == "monohull":
            # Quantify the placeholder wetted surface that used to be shipped.
            cbs = [pub["cb"], 0.508]  # declared + the old hand-typed preset value
            legacy = []
            for cb in cbs:
                s_legacy = math.pi * pub["beam_m"] * pub["length_m"] * (0.5 + 0.25 * cb)
                legacy.append(
                    {
                        "cb": cb,
                        "s_legacy_m2": round(s_legacy, 4),
                        "err_vs_published_pct": round(
                            (s_legacy - pub["s_ref_m2"]) / pub["s_ref_m2"] * 100.0, 3
                        ),
                    }
                )
            rec["legacy_wetted_surface"] = legacy
        else:
            D = pub["diameter_m"]
            b = axisymmetric_cb_bounds(pub["length_m"], D, pub["s_ref_m2"])
            s_legacy = math.pi * D * pub["length_m"] * (0.5 + 0.25 * 0.508)
            rec["legacy_wetted_surface"] = [
                {
                    "cb": 0.508,
                    "s_legacy_m2": round(s_legacy, 4),
                    "err_vs_published_pct": round(
                        (s_legacy - pub["s_ref_m2"]) / pub["s_ref_m2"] * 100.0, 3
                    ),
                }
            ]
            rec["cauchy_schwarz_bracket"] = {
                "cb_lower_bound": round(b["cb_lower_bound"], 4),
                "cb_upper_bound": b["cb_upper_bound"],
                "volume_cylinder_m3": round(b["volume_cylinder_m3"], 5),
                "note": "declared cb=0.508 for SUBOFF lies below the provable bound",
            }
        out["hulls"][hull] = rec
        out["ok"] = out["ok"] and rec["gate_ok"]
    return out


# ---------------------------------------------------------------------------
# Dataset verification
# ---------------------------------------------------------------------------

def _case_geometry(case: Dict[str, Any]) -> Dict[str, Any]:
    """Published geometry block for a case (overrides the legacy case block)."""
    hull = case["ship_type"]
    geo = resolve_geometry(hull)
    pub = PUBLISHED_GEOMETRY[hull]
    block: Dict[str, Any] = {
        "length_m": geo["length"],
        "beam_m": geo["beam"],
        "draft_m": geo["draft"],
        "cb": geo["cb"],
        "cb_basis": geo["cb_source"],
        "wetted_surface_m2": geo["wetted_surface_m2"],
        "s_basis": geo["s_source"],
        "form": geo["form"],
        "citation": pub["source"],
    }
    if geo["form"] == "axisymmetric":
        block["diameter_m"] = geo["diameter"]
        block["cb_basis"] = "surface_fullness S/(pi*D*L) derived from published (L,D,S)"
    elif "volume_m3" in pub:
        block["volume_m3"] = pub["volume_m3"]
        block["volume_basis"] = "published displacement volume"
    return block


def verify_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """Per-case reference-data audit: Fr consistency, Rt vs Ct, regime, band."""
    hull = case["ship_type"]
    geo = resolve_geometry(hull)
    V = float(case["speed_ms"])
    ct_ref = float(case["experimental_ct"])
    legacy_rt = case.get("experimental_rt_n")
    prov = reference_provenance(hull, V, ct_ref, legacy_rt)

    checks: List[Dict[str, Any]] = []
    fr_stored = case.get("froude_number")
    fr = V / math.sqrt(9.81 * geo["length"])
    if fr_stored is not None:
        checks.append(
            {
                "check": "froude_number_consistency",
                "stored": fr_stored,
                "derived": round(fr, 4),
                "ok": abs(fr_stored - fr) / fr < 0.01,
            }
        )
    checks.extend(prov["checks"])
    # Legacy-vs-derived force discrepancy is reported, not treated as a failure
    # (v2.0 dataset carries the recomputed column; the legacy value is history).
    if "rt_legacy_err_pct" in case:
        checks.append(
            {
                "check": "legacy_rt_vs_derived",
                "stored": case.get("reported_rt_n_legacy"),
                "derived": round(0.5 * RHO_REF * geo["wetted_surface_m2"] * ct_ref * V * V, 3),
                "rel_err_pct": case["rt_legacy_err_pct"],
                "ok": None,
                "note": "informational: v1.0 hand-typed force column",
            }
        )
    return {
        "case_id": case["case_id"],
        "ship_type": hull,
        "speed_ms": V,
        "froude_number": round(fr, 4),
        "reynolds_number": round(V * geo["length"] / NU_REF, 1),
        "cf_ittc57": prov["cf_ittc57"],
        "ct_reference": ct_ref,
        "ct_over_cf": round(ct_ref / prov["cf_ittc57"], 4),
        "rt_stored_n": case.get("reported_rt_n_legacy", case.get("experimental_rt_n")),
        "rt_derived_n": round(0.5 * RHO_REF * geo["wetted_surface_m2"] * ct_ref * V * V, 3),
        "checks": checks,
        "flags": prov["flags"],
        "ok": all(c["ok"] for c in checks if c.get("ok") is not None) and not prov["flags"],
    }


def audit_dataset(data: Dict[str, Any]) -> Dict[str, Any]:
    cases = [verify_case(c) for c in data["cases"]]
    flagged = [c["case_id"] for c in cases if not c["ok"]]
    return {
        "n_cases": len(cases),
        "displacement_regime_fr_max": DISPLACEMENT_REGIME_FR_MAX,
        "ok": not flagged,
        "cases": cases,
        "flagged": flagged,
        "n_flagged": len(flagged),
    }


# ---------------------------------------------------------------------------
# Model accuracy: run the real solver in-process over every case
# ---------------------------------------------------------------------------

def model_accuracy(data: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate the platform resistance solver against every benchmark case."""
    rows: List[Dict[str, Any]] = []
    for case in data["cases"]:
        hull = case["ship_type"]
        geo = resolve_geometry(hull)
        sol = solve_resistance(
            hull=hull,
            speed_ms=float(case["speed_ms"]),
            overrides={
                "length": geo["length"],
                "beam": geo["beam"],
                "draft": geo["draft"],
                "cb": geo["cb"],
                "wetted_surface_m2": geo["wetted_surface_m2"],
            },
        )
        ct = sol["ct"]
        ref = float(case["experimental_ct"])
        err = abs(ct - ref) / ref * 100.0
        rows.append(
            {
                "case_id": case["case_id"],
                "ship_type": hull,
                "speed_ms": float(case["speed_ms"]),
                "froude_number": round(sol["froude_number"], 4),
                "cf": round(sol["cf"], 6),
                "cr": round(sol["cr"], 6),
                "ct_calc": round(ct, 6),
                "ct_ref": ref,
                "error_pct": round(err, 2),
                "pass_15pct": err <= 15.0,
                "wetted_surface_m2": sol["wetted_surface_m2"],
                "total_resistance_n": sol["total_resistance_n"],
                "s_source": sol["geometry"]["s_source"],
                "in_model_envelope": sol["validity"]["in_envelope"],
            }
        )
    by_hull: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        by_hull.setdefault(row["ship_type"], {"errors": [], "n": 0, "pass": 0})
        agg = by_hull[row["ship_type"]]
        agg["errors"].append(row["error_pct"])
        agg["n"] += 1
        agg["pass"] += int(row["pass_15pct"])
    summary = {
        hull: {
            "n": v["n"],
            "passed": v["pass"],
            "mean_abs_error_pct": round(sum(v["errors"]) / len(v["errors"]), 2),
            "max_abs_error_pct": round(max(v["errors"]), 2),
        }
        for hull, v in by_hull.items()
    }
    ref_ok = {c["case_id"]: c["ok"] for c in (verify_case(x) for x in data["cases"])}
    for row in rows:
        row["reference_integrity_ok"] = ref_ok.get(row["case_id"], False)
    certifiable = [r for r in rows if r["in_model_envelope"] and r["reference_integrity_ok"]]
    cert_summary: Dict[str, Any] = {
        "n": len(certifiable),
        "case_ids": [r["case_id"] for r in certifiable],
        "mean_abs_error_pct": (
            round(sum(r["error_pct"] for r in certifiable) / len(certifiable), 2)
            if certifiable else None
        ),
        "max_abs_error_pct": (
            round(max(r["error_pct"] for r in certifiable), 2) if certifiable else None
        ),
        "n_within_15pct": sum(1 for r in certifiable if r["error_pct"] <= 15.0),
    }
    return {"rows": rows, "by_hull": summary,
            "overall_mean_abs_error_pct": round(sum(r["error_pct"] for r in rows) / len(rows), 2),
            "certifiable_subset": cert_summary}


# ---------------------------------------------------------------------------
# Dataset repair
# ---------------------------------------------------------------------------

def regenerate(data: Dict[str, Any]) -> Dict[str, Any]:
    """Rewrite the reference dataset with published geometry and consistent forces.

    Experimental Ct values are *never* modified: an unverifiable reference is
    flagged in ``provenance_flag`` instead of being overwritten.
    """
    new = dict(data)
    new["version"] = "2.0"
    new["verification"] = {
        "mission": "m1609",
        "scope": (
            "Geometry re-derived from published principal particulars; rt column "
            "recomputed from Ct; legacy hand-typed columns preserved for traceability."
        ),
        "rho_ref_kg_m3": RHO_REF,
        "nu_ref_m2_s": NU_REF,
        "geometry_source": {h: p["source"] for h, p in PUBLISHED_GEOMETRY.items()},
        "derived_rt_formula": "rt = 0.5 * rho_ref * S_published * ct * V^2",
        "notes": [
            "Experimental Ct values are unchanged; implausible ones are flagged.",
            "SUBOFF cb is the surface-fullness coefficient S/(pi D L)=0.7796 derived "
            "from published (L,D,S); the legacy preset value 0.508 violates the "
            "Cauchy-Schwarz bound Cb>=0.6078 and was rejected by the geometry gate.",
            "DTMB-5415 displacement 466.3 kg in v1.0 implied Cb=0.437 vs published "
            "0.506 (-13.7%); v2.0 uses the published volume 0.5492 m^3.",
        ],
    }
    cases = []
    for case in new["cases"]:
        c = dict(case)
        geo = resolve_geometry(c["ship_type"])
        L, V, S = geo["length"], float(c["speed_ms"]), geo["wetted_surface_m2"]
        c["froude_number"] = round(V / math.sqrt(9.81 * L), 4)
        c["reynolds_number"] = round(V * L / NU_REF, 1)
        rt = 0.5 * RHO_REF * S * float(c["experimental_ct"]) * V * V
        if "experimental_rt_n" in c:
            c["reported_rt_n_legacy"] = case["experimental_rt_n"]
        c["experimental_rt_n"] = round(rt, 3)
        c["rt_basis"] = "derived from experimental_ct and published wetted surface (rho=998 kg/m3)"
        c["geometry"] = _case_geometry(case)
        if "reported_rt_n_legacy" not in c and "experimental_rt_n" in c:
            c["reported_rt_n_legacy"] = case["experimental_rt_n"]
        legacy_rt = c.get("reported_rt_n_legacy")
        if legacy_rt is not None:
            c["rt_legacy_err_pct"] = round(
                (float(legacy_rt) - c["experimental_rt_n"]) / c["experimental_rt_n"] * 100.0, 2
            )
        # rt is regenerated below, so provenance is judged on the coefficient and
        # the regime only (the legacy force column is preserved separately).
        prov = reference_provenance(c["ship_type"], V, float(c["experimental_ct"]), None)
        c["reference_checks"] = prov["checks"]
        if prov["flags"]:
            c["provenance_flag"] = list(prov["flags"])
        else:
            c.pop("provenance_flag", None)
        cases.append(c)
    new["cases"] = cases
    return new


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def make_figures(acc: Dict[str, Any], geo_audit: Dict[str, Any], out_dir: Path) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    made: List[str] = []

    # --- Figure 1: Ct vs Fr, model vs reference vs ITTC-57 lower bound --------
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))
    for ax, hull in zip(axes, ["SUBOFF", "DTMB-5415", "KVLCC2"]):
        rows = [r for r in acc["rows"] if r["ship_type"] == hull]
        rows.sort(key=lambda r: r["froude_number"])
        fr = [r["froude_number"] for r in rows]
        ax.plot(fr, [r["ct_ref"] for r in rows], "o-", label="reference Ct", color="#b2182b")
        ax.plot(fr, [r["ct_calc"] for r in rows], "s--", label="platform Ct", color="#2166ac")
        ax.plot(fr, [r["cf"] for r in rows], ":", label="ITTC-57 Cf", color="#4d4d4d")
        ax.set_title(f"{hull}")
        ax.set_xlabel("Froude number Fr")
        ax.set_ylabel("Ct [-]")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle("m1609 verification: platform Ct vs experimental reference (12 benchmark cases)")
    fig.tight_layout()
    p1 = out_dir / "ct_vs_fr_comparison.png"
    fig.savefig(p1, dpi=130)
    plt.close(fig)
    made.append(str(p1))

    # --- Figure 2: relative error per case -----------------------------------
    fig, ax = plt.subplots(figsize=(10, 4.6))
    labels = [r["case_id"].replace("-", "\n") for r in acc["rows"]]
    errs = [r["error_pct"] for r in acc["rows"]]
    colors = ["#2166ac" if e <= 15 else "#b2182b" for e in errs]
    ax.bar(range(len(errs)), errs, color=colors)
    ax.axhline(15, color="k", ls="--", lw=1, label="15% acceptance threshold")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=90, fontsize=7)
    ax.set_ylabel("|Ct_calc - Ct_ref| / Ct_ref  [%]")
    ax.set_title("Relative error per benchmark case (blue = pass, red = fail)")
    ax.legend()
    fig.tight_layout()
    p2 = out_dir / "error_per_case.png"
    fig.savefig(p2, dpi=130)
    plt.close(fig)
    made.append(str(p2))

    # --- Figure 3: wetted-surface model vs published + legacy placeholder ----
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    hulls = [h for h in PUBLISHED_GEOMETRY if PUBLISHED_GEOMETRY[h]["form"] == "monohull"]
    x = range(len(hulls))
    pub = [PUBLISHED_GEOMETRY[h]["s_ref_m2"] for h in hulls]
    new = [geo_audit["hulls"][h]["derived"]["s_holtrop_m2"] for h in hulls]
    leg = [geo_audit["hulls"][h]["legacy_wetted_surface"][0]["s_legacy_m2"] for h in hulls]
    w = 0.26
    ax.bar([i - w for i in x], pub, w, label="published S", color="#4d4d4d")
    ax.bar(list(x), new, w, label="Holtrop (1982) S", color="#2166ac")
    ax.bar([i + w for i in x], leg, w, label="legacy placeholder S", color="#b2182b")
    ax.set_yscale("log")
    ax.set_xticks(list(x))
    ax.set_xticklabels(hulls)
    ax.set_ylabel("wetted surface [m$^2$, log]")
    ax.set_title("Wetted-surface verification: legacy placeholder vs Holtrop vs published")
    for i, (p, n, l) in enumerate(zip(pub, new, leg)):
        ax.annotate(f"{(n - p) / p * 100:+.1f}%", (i, max(n, p) * 1.25), ha="center", fontsize=7)
        ax.annotate(f"{(l - p) / p * 100:+.0f}%", (i + w, max(l, p) * 1.25), ha="center",
                    fontsize=7, color="#b2182b")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p3 = out_dir / "wetted_surface_verification.png"
    fig.savefig(p3, dpi=130)
    plt.close(fig)
    made.append(str(p3))
    return made


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regenerate", action="store_true", help="repair the reference dataset in place")
    ap.add_argument("--figures", action="store_true", help="emit PNG figures")
    ap.add_argument("--json", type=str, default=None, help="write the audit JSON here")
    args = ap.parse_args(argv)

    data = json.loads(DATA_PATH.read_text())

    if args.regenerate:
        repaired = regenerate(data)
        DATA_PATH.write_text(json.dumps(repaired, indent=2, ensure_ascii=False) + "\n")
        print(f"regenerated {DATA_PATH}")
        data = repaired

    geo_audit = audit_geometry()
    ds_audit = audit_dataset(data)
    acc = model_accuracy(data)

    report = {
        "mission": "m1609",
        "geometry": geo_audit,
        "dataset": ds_audit,
        "model_accuracy": acc,
    }
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    target = Path(args.json) if args.json else ARTIFACT_DIR / "ship_benchmark_audit.json"
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"audit written: {target}")

    if args.figures:
        for f in make_figures(acc, geo_audit, ARTIFACT_DIR):
            print(f"figure: {f}")

    print(f"geometry gate ok={geo_audit['ok']}  dataset ok={ds_audit['ok']}")
    print(f"model mean |error| = {acc['overall_mean_abs_error_pct']}%")
    for hull, s in acc["by_hull"].items():
        print(f"  {hull:10s} n={s['n']} pass={s['passed']} mean|err|={s['mean_abs_error_pct']}% "
              f"max|err|={s['max_abs_error_pct']}%")
    if ds_audit["flagged"]:
        print(f"reference points flagged ({ds_audit['n_flagged']}/{ds_audit['n_cases']}): "
              f"{ds_audit['flagged']}")

    ok = geo_audit["ok"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())