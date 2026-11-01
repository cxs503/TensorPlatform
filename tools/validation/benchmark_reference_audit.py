#!/usr/bin/env python3
"""Benchmark reference audit: gate report + accuracy of the live resistance kernel.

Produces (all numbers programmatically injected, nothing hand-copied):
  artifacts/benchmark_reference_audit/audit.json
  artifacts/benchmark_reference_audit/fig1_reference_values.png
  artifacts/benchmark_reference_audit/fig2_ct_accuracy.png
  artifacts/benchmark_reference_audit/fig3_froude_conflict.png

Usage: PYTHONPATH=backend python3 tools/validation/benchmark_reference_audit.py
"""
from __future__ import annotations

import json
import math
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OUT = REPO / "artifacts" / "benchmark_reference_audit"
G = 9.81

# import the pure kernel without triggering the api package side effects
if "api" not in sys.modules:
    _pkg = types.ModuleType("api")
    _pkg.__path__ = [str(BACKEND / "api")]
    sys.modules["api"] = _pkg
sys.path.insert(0, str(BACKEND))

from api.calculations import compute_resistance  # noqa: E402
from services.benchmark_integrity import validate_dataset  # noqa: E402

DATA = BACKEND / "data" / "benchmark_reference.json"
V1 = Path("/tmp/bench_v1.json")  # pre-canonicalisation snapshot (if present)


def audit() -> dict:
    data = json.loads(DATA.read_text())
    report = validate_dataset(data, strict=True)

    prev = None
    if V1.exists():
        prev = validate_dataset(json.loads(V1.read_text()), strict=False)

    # live-kernel accuracy vs the reference Ct, with and without the validated S
    kernel = []
    for case in data["cases"]:
        geom = case["geometry"]
        cb = geom.get("cb", 0.508)
        for label, surface in (("preset_S", None), ("validated_S", geom.get("wetted_surface_m2"))):
            res = compute_resistance(
                length=geom["length_m"],
                beam=geom.get("beam_m") or geom.get("diameter_m"),
                draft=geom.get("draft_m") or geom.get("diameter_m"),
                cb=cb,
                speed_ms=case["speed_ms"],
                wetted_surface_m2=surface,
            )
            kernel.append(
                {
                    "case_id": case["case_id"],
                    "variant": label,
                    "ct_calc": res["ct"],
                    "ct_ref": case["experimental_ct"],
                    "error_pct": abs(res["ct"] - case["experimental_ct"]) / case["experimental_ct"] * 100,
                    "rt_calc_n": res["total_resistance_n"],
                    "rt_ref_n": case["experimental_rt_n"],
                    "wetted_surface_m2": res["wetted_surface_m2"],
                    "rt_error_pct": None if case["experimental_rt_n"] is None else abs(
                        res["total_resistance_n"] - case["experimental_rt_n"]
                    ) / case["experimental_rt_n"] * 100,
                    "within_15pct": abs(res["ct"] - case["experimental_ct"]) / case["experimental_ct"] * 100 <= 15.0,
                }
            )

    # Ct = Cf + Cr is independent of S -- prove it numerically, because it means a
    # Ct-based gate can never detect a wetted-area (geometry) error.
    paired = {}
    for row in kernel:
        paired.setdefault(row["case_id"], {})[row["variant"]] = row["ct_calc"]
    invariance = max(
        abs(v["preset_S"] - v["validated_S"]) / v["preset_S"] for v in paired.values()
    )

    suboff = [c for c in data["cases"] if c["ship_type"] == "SUBOFF"]
    dtmb = [c for c in data["cases"] if c["ship_type"] == "DTMB-5415"]
    return {
        "dataset_version": data["version"],
        "gate": {
            "n_cases": report["n_cases"],
            "n_violations": report["n_violations"],
            "resolved": report["resolved"],
            "withheld": report["withheld"],
            "by_status": report["by_status"],
            "claimable": report["claimable_cases"],
            "findings": report["findings"],
        },
        "pre_canonicalisation": None
        if prev is None
        else {
            "version": prev["version"],
            "n_violations": prev["n_violations"],
            "violating_rules": sorted({v["rule"] for v in prev["violations"]}),
            "n_violating_cases": len({v["case_id"] for v in prev["violations"]}),
        },
        "ct_invariance_to_wetted_surface_max_rel": invariance,
        "kernel": kernel,
        "suboff_reference": [
            {
                "case_id": c["case_id"],
                "speed_ms": c["speed_ms"],
                "rt_canonical_n": c["experimental_rt_n"],
                "rt_legacy_v1_n": c["legacy_values"]["experimental_rt_n_v1"],
                "delta_pct": (c["legacy_values"]["experimental_rt_n_v1"] - c["experimental_rt_n"])
                / c["experimental_rt_n"] * 100,
                "rt_source": c["rt_source"],
            }
            for c in suboff
        ],
        "dtmb_froude": [
            {
                "case_id": c["case_id"],
                "speed_ms": c["speed_ms"],
                "froude_recorded": c["froude_number"],
                "froude_recomputed_L_model": c["froude_number_recomputed"],
                "implied_length_m": c["legacy_values"]["froude_implied_length_m_v1"],
                "froude_ok_for_displacement_hull": c["froude_number_recomputed"] < 0.6,
            }
            for c in dtmb
        ],
    }


def figures(result: dict) -> list:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUT.mkdir(parents=True, exist_ok=True)
    paths = []

    # ---- fig 1: reference values, legacy vs canonical ----------------
    ref = result["suboff_reference"]
    ids = [r["case_id"] for r in ref]
    legacy = [r["rt_legacy_v1_n"] for r in ref]
    canon = [r["rt_canonical_n"] for r in ref]
    x = range(len(ids))
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.bar([i - 0.2 for i in x], legacy, width=0.4, label="v1.0 recorded Rt", color="#c0392b")
    ax.bar([i + 0.2 for i in x], canon, width=0.4, label="v3.0 identity Rt (rho=998.2)", color="#1e8449")
    for i, r in enumerate(ref):
        ax.annotate(f"{r['delta_pct']:.1f}%", (i, max(legacy[i], canon[i]) * 1.02),
                    ha="center", fontsize=8, color="#7f0000")
    ax.set_xticks(list(x), ids)
    ax.set_ylabel("total resistance $R_t$ [N]")
    ax.set_title("SUBOFF reference values: recorded vs identity-recomputed\n(labels = legacy deviation)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    p = OUT / "fig1_reference_values.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    paths.append(str(p))

    # ---- fig 2: live kernel accuracy (Ct and Rt) ------------------------
    sub = [k for k in result["kernel"] if k["case_id"].startswith("SUBOFF")]
    uni = [k for k in sub if k["variant"] == "preset_S"]
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.0))
    ax = axes[0]
    ax.plot([k["ct_ref"] for k in uni], [k["error_pct"] for k in uni], "o-", color="#1f618d")
    for k in uni:
        ax.annotate(k["case_id"], (k["ct_ref"], k["error_pct"]), fontsize=7,
                    xytext=(3, 3), textcoords="offset points")
    ax.axhline(15.0, ls="--", color="k", lw=1, label="platform 15% gate")
    ax.set_xlabel("reference $C_t$ [-]")
    ax.set_ylabel("|$C_t^{calc}-C_t^{ref}|/C_t^{ref}$ [%]")
    ax.set_title("$C_t$ error (independent of wetted area)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[1]
    ids = [k["case_id"] for k in uni]
    preset = [k["rt_error_pct"] for k in uni]
    valid = [k["rt_error_pct"] for k in sub if k["variant"] == "validated_S"]
    xx = range(len(ids))
    ax.bar([i - 0.2 for i in xx], preset, width=0.4, color="#c0392b",
           label="preset S (no validated area)")
    ax.bar([i + 0.2 for i in xx], valid, width=0.4, color="#1e8449", label="validated S=5.42 m2")
    ax.axhline(15.0, ls="--", color="k", lw=1, label="15% gate")
    ax.set_xticks(list(xx), ids)
    ax.set_ylabel("|$R_t^{calc}-R_t^{ref}|/R_t^{ref}$ [%]")
    ax.set_title("$R_t$ error: sensitive to the wetted-surface provenance")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    p = OUT / "fig2_ct_accuracy.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    paths.append(str(p))

    # ---- fig 3: DTMB-5415 Froude conflict -------------------------------
    d = result["dtmb_froude"]
    ids = [r["case_id"] for r in d]
    rec = [r["froude_recorded"] for r in d]
    rec_l = [r["froude_recomputed_L_model"] for r in d]
    x = range(len(ids))
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.plot(list(x), rec, "o-", color="#c0392b", label="recorded $F_n$ (implies L=181.2 m)")
    ax.plot(list(x), rec_l, "s-", color="#1f618d", label="recomputed $F_n$ with L=5.72 m")
    ax.axhline(0.6, ls="--", color="k", lw=1, label="displacement-hull limit $F_n$=0.6")
    ax.set_xticks(list(x), ids)
    ax.set_ylabel("Froude number [-]")
    ax.set_title("DTMB-5415 rows: speed/Froude column vs declared model length")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    p = OUT / "fig3_froude_conflict.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    paths.append(str(p))
    return paths


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    result = audit()
    result["figures"] = figures(result)
    (OUT / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "kernel"}, ensure_ascii=False, indent=2))
    print("\nkernel rows:")
    for k in result["kernel"]:
        print(f"  {k['case_id']:12s} {k['variant']:11s} ct={k['ct_calc']:.5f} ref={k['ct_ref']:.5f} "
              f"err={k['error_pct']:6.2f}% S={k['wetted_surface_m2']:.3f} 15%gate={k['within_15pct']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())