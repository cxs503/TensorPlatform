#!/usr/bin/env python3
"""Verify the resistance kernel against the benchmark reference set — end to end.

This is the check the platform never actually ran.  It drives the *real* gate
(``api/domain/ship_benchmark.benchmark_validate``), not a re-implementation, so what
is reported here is exactly what ``POST /api/v1/calculations/benchmark_validate``
returns to a client.

Outputs (default ``artifacts/benchmark_audit/``):
    verification.json   per-case table + per-family summary (machine readable)
    ct_vs_fn.png        Ct(Fn): kernel, experimental references, ITTC-57 friction floor
    error_by_case.png   relative error per case with the 15 % acceptance band

Nothing here is hand-copied: every number in the report is read back from these files.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
DATASET = BACKEND / "data" / "benchmark_reference.json"
OUT_DIR = REPO_ROOT / "artifacts" / "benchmark_audit"

ACCEPT_PCT = 15.0


# ---------------------------------------------------------------------------
# Import the gate without booting the whole FastAPI app (which needs the DB stack).
# ---------------------------------------------------------------------------
def load_gate_module():
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    for name, sub in (("api", "api"), ("api.domain", "api/domain")):
        if name not in sys.modules:
            pkg = types.ModuleType(name)
            pkg.__path__ = [str(BACKEND / sub)]          # bypass api/__init__ (DB import)
            sys.modules[name] = pkg
    path = BACKEND / "api" / "domain" / "ship_benchmark.py"
    spec = importlib.util.spec_from_file_location("api.domain.ship_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["api.domain.ship_benchmark"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def run_verification() -> Dict[str, Any]:
    gate = load_gate_module()
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    cases_by_id = {c["case_id"]: c for c in dataset["cases"]}
    geo_by_id = {}
    for st in gate.SHIP_GEOMETRY:
        for cid, case in cases_by_id.items():
            if case["ship_type"] == st:
                geo_by_id[cid] = gate._case_geometry(st, case)

    families: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []

    for ship_type in gate.SHIP_GEOMETRY:
        resp = gate.benchmark_validate(
            gate.BenchmarkValidateRequest(ship_type=ship_type, benchmark_id="all"))
        data = resp.model_dump(by_alias=True)
        scored = []
        for r in data["results"]:
            case = cases_by_id[r["case_id"]]
            geo = geo_by_id[r["case_id"]]
            from services.ship_resistance_core import compute_resistance_core, check_geometry
            diag = check_geometry(geo["length"], geo["beam"], geo["draft"], geo["cb"], r["speed_ms"])
            core = compute_resistance_core(
                geo["length"], geo["beam"], geo["draft"], geo["cb"], r["speed_ms"],
                float(case.get("water_density", 998.0)),
                float(case.get("kinematic_viscosity", 1.004e-6)))
            row = {
                "case_id": r["case_id"],
                "ship_type": ship_type,
                "speed_ms": r["speed_ms"],
                "froude": round(core["froude_number"], 4),
                "reynolds": core["reynolds_number"],
                "cf_ittc57": round(core["friction_coeff"], 6),
                "wetted_surface_m2": round(core["wetted_surface_m2"], 4),
                "calc_ct": r["calc_ct"],
                "ref_ct": r["ref_ct"],
                "error_pct": r["error_pct"],
                "pass": r["pass"],
                "geometry_ok": diag["ok"],
                "note": r.get("note", ""),
            }
            rows.append(row)
            scored.append(row)
        errs = [r["error_pct"] for r in scored]
        families.append({
            "ship_type": ship_type,
            "scored_cases": len(scored),
            "excluded_cases": data["summary"].get("excluded_cases", []),
            "passed": data["summary"]["passed"],
            "pass_rate_pct": data["summary"]["pass_rate"],
            "mean_error_pct": round(sum(errs) / len(errs), 2) if errs else None,
            "max_error_pct": round(max(errs), 2) if errs else None,
            "honest_fail_cases": data["summary"].get("honest_fail_cases", []),
            "algorithm_upgrade_recommended": data["summary"]["algorithm_upgrade_recommended"],
        })

    return {
        "tool": "tools/verify_resistance_accuracy.py",
        "dataset": str(DATASET.relative_to(REPO_ROOT)),
        "acceptance_threshold_pct": ACCEPT_PCT,
        "kernel": "services.ship_resistance_core.compute_resistance_core",
        "families": families,
        "cases": rows,
        "totals": {
            "scored": len(rows),
            "passed": sum(r["pass"] for r in rows),
            "failed": sum(not r["pass"] for r in rows),
            "mean_error_pct": round(sum(r["error_pct"] for r in rows) / len(rows), 2) if rows else None,
        },
    }


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def make_figures(result: Dict[str, Any], out_dir: Path) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    produced = []
    styles = {"SUBOFF": "o", "DTMB-5415": "s", "KVLCC2": "^"}
    colors = {"SUBOFF": "tab:blue", "DTMB-5415": "tab:orange", "KVLCC2": "tab:green"}

    # --- Figure 1: Ct versus Froude number ------------------------------------
    fig, ax = plt.subplots(figsize=(7.2, 5.0))
    for st, marker in styles.items():
        sub = [r for r in result["cases"] if r["ship_type"] == st]
        if not sub:
            continue
        ax.plot([r["froude"] for r in sub], [r["ref_ct"] for r in sub],
                marker=marker, ls="none", ms=8, mfc="none", mew=1.6,
                color=colors[st], label=f"{st} reference Ct (experiment)")
        ax.plot([r["froude"] for r in sub], [r["calc_ct"] for r in sub],
                marker=marker, ls="--", ms=5, color=colors[st], alpha=0.85,
                label=f"{st} kernel Ct")
        ax.plot([r["froude"] for r in sub], [r["cf_ittc57"] for r in sub],
                ls=":", color=colors[st], alpha=0.7, lw=1.4,
                label=f"{st} ITTC-57 friction floor Cf")
    ax.set_xlabel("Froude number  Fn = V/sqrt(gL)")
    ax.set_ylabel("Total resistance coefficient  $C_T$")
    ax.set_title("Resistance kernel vs experimental references\n"
                 "(hollow markers = experiment, dashed = kernel, dotted = friction floor)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, ncol=1, loc="upper left")
    p = out_dir / "ct_vs_fn.png"
    fig.tight_layout()
    fig.savefig(p, dpi=150)
    plt.close(fig)
    produced.append(str(p))

    # --- Figure 2: error per case ---------------------------------------------
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    ordered = sorted(result["cases"], key=lambda r: (r["ship_type"], r["case_id"]))
    labels = [f"{r['case_id']}\nFn={r['froude']:.2f}" for r in ordered]
    values = [r["error_pct"] for r in ordered]
    bars = ax.bar(range(len(ordered)), values,
                  color=["tab:green" if r["pass"] else "tab:red" for r in ordered])
    ax.axhline(ACCEPT_PCT, color="k", ls="--", lw=1.3,
               label=f"{ACCEPT_PCT:.0f}% acceptance threshold")
    ax.set_xticks(range(len(ordered)))
    ax.set_xticklabels(labels, fontsize=7, rotation=45, ha="right")
    ax.set_ylabel("relative error |Ct_calc - Ct_ref| / Ct_ref  (%)")
    ax.set_title("Per-case error against the experimental reference\n(green = pass, red = fail)")
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.1f}", ha="center", fontsize=6.5)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    p = out_dir / "error_by_case.png"
    fig.tight_layout()
    fig.savefig(p, dpi=150)
    plt.close(fig)
    produced.append(str(p))

    # --- Figure 3: how much resistance above friction does each side claim? ----
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    for st, marker in styles.items():
        sub = [r for r in result["cases"] if r["ship_type"] == st]
        if not sub:
            continue
        fn = [r["froude"] for r in sub]
        ax.plot(fn, [r["ref_ct"] / r["cf_ittc57"] for r in sub], marker=marker, ls="none",
                ms=9, mfc="none", mew=1.6, color=colors[st], label=f"{st} reference Ct/Cf")
        ax.plot(fn, [r["calc_ct"] / r["cf_ittc57"] for r in sub], marker=marker, ls="--",
                ms=5, color=colors[st], alpha=0.85, label=f"{st} kernel Ct/Cf")
    ax.set_xlabel("Froude number  Fn")
    ax.set_ylabel("$C_T / C_F$  (resistance above the ITTC-57 friction line)")
    ax.set_title("What the two sides claim above pure friction\n"
                 "(kernel residual-resistance fit is calibrated at high Fn only)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)
    proc = out_dir / "ct_over_cf_vs_fn.png"
    fig.tight_layout()
    fig.savefig(proc, dpi=150)
    plt.close(fig)
    produced.append(str(proc))
    return produced


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result = run_verification()
    result["figures"] = make_figures(result, OUT_DIR)
    (OUT_DIR / "verification.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"{'case':13s}{'Fn':>7s}{'Ct_calc':>10s}{'Ct_ref':>10s}{'err%':>8s}  pass")
    for r in result["cases"]:
        print(f"{r['case_id']:13s}{r['froude']:7.3f}{r['calc_ct']:10.5f}"
              f"{r['ref_ct']:10.5f}{r['error_pct']:8.1f}  {r['pass']}")
    print()
    for f in result["families"]:
        print(f"{f['ship_type']:12s} n={f['scored_cases']:2d} pass={f['passed']:2d} "
              f"pass_rate={f['pass_rate_pct']:5.1f}% mean_err={f['mean_error_pct']:6.2f}% "
              f"max_err={f['max_error_pct']:7.2f}% excluded={len(f['excluded_cases'])}")
    print(f"\ntotals: {result['totals']}")
    print(f"figures: {result['figures']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())