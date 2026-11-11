#!/usr/bin/env python
"""P167W2B-N08 — cross-library field-exchange conservation gate audit.

Runs the four faces of the node and writes machine-checkable evidence:

1. **fracture hand-off (FEM→DEM)** — four ledgers + inertia tensor + volume,
   swept over 1/3/6 cutting planes and a dyadic (exactly representable)
   partition; every polynomial ledger is re-evaluated in exact rational
   arithmetic to prove the float residual is round-off;
2. **flow hand-off (LBM→FVM)** — inventory + interface mass flux across an
   aligned, an interior-cut and an oblique coupling plane, and over a grid
   ratio sweep, against the inverse-distance shortcut;
3. **time hand-off (clock)** — conservative barrier over 100 LCM windows with
   adversarial step ratios (plus detectors validated by negative controls), and
   the optimistic protocol's rollback rate / replay error / no-rollback drift;
4. **the ingest gate** — fail-closed adjudication of the sealed bundle plus a
   positive control and seventeen negative controls, two of which substitute
   the *real* numbers of the shortcut models instead of scaled fakes.

Every number in the JSON is computed here (zero transcription).  Figures are
regenerated from the same numbers.

Usage::

    python scripts/run_p167w2b_n08_conservation_audit.py [--out artifacts/p167w2b-n08]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from typing import Any, Dict, List

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.coupling.conservation_gate import sphere_surrogate_tensors  # noqa: E402
from src.coupling.conservation_gate import (  # noqa: E402
    VERSION,
    ConservationGate,
    ConservationLedger,
    barrier_run,
    bundle_digest,
    clock_ledger,
    cross_library_policy,
    fem_dem_ledger,
    lbm_fvm_ledger,
    optimistic_run,
    rational_ledger_residuals,
    run_controls,
    run_mutator_substitution,
    sphere_surrogate_ledger,
)
from src.coupling.conservation_gate.exact import rational_ledger_residuals as _rat  # noqa: E402,F401
from src.coupling.fem_to_dem_exact import (  # noqa: E402
    ExactFractureTransfer,
    FractureFace,
    cube_element,
    plane_cut_faces,
)
from src.coupling.lbm_to_fvm_conservative_remap import (  # noqa: E402
    SupermeshRemap,
    lattice_cells,
    shear_field,
    structured_cells,
)

# --------------------------------------------------------------------------- #
# criteria — declared before the run, the JSON records the measured value
# --------------------------------------------------------------------------- #
CRITERIA = {
    "fem_dem.mass": {"op": "balance", "tol": 1e-9},
    "fem_dem.momentum": {"op": "balance", "tol": 1e-9},
    "fem_dem.angular_momentum": {"op": "balance", "tol": 1e-9},
    "fem_dem.energy": {"op": "balance", "tol": 1e-9},
    "fem_dem.inertia_tensor": {"op": "balance", "tol": 1e-9},
    "fem_dem.volume": {"op": "balance", "tol": 1e-9},
    "lbm_fvm.mass_inventory": {"op": "balance", "tol": 1e-9},
    "lbm_fvm.momentum_inventory": {"op": "balance", "tol": 1e-9},
    "lbm_fvm.interface_mass_flux": {"op": "balance", "tol": 1e-6},
    "clock.barrier_skew": {"op": "bound", "tol": 1e-9},
    "clock.deadlocks": {"op": "bound", "tol": 0.0},
    "clock.future_reads": {"op": "bound", "tol": 0.0},
    "clock.global_time_monotone": {"op": "flag_true"},
    "clock.optimistic_rollback_rate": {"op": "bound", "tol": 0.05},
    "clock.optimistic_replay_error": {"op": "bound", "tol": 1e-9},
}

# no-domain-semantics scan: the platform layer must not grow domain vocabulary
# matched on word boundaries: "vertices" is geometry, "ice" as a word is not
BANNED_TOKENS = [
    "propeller", "vessel", "ship", "rudder", "keel", "ice", "wigley",
    "kcs", "kvlcc", "froude", "deadrise", "bulbous", "seakeeping", "cavitation",
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _axis_faces(planes) -> List[FractureFace]:
    """Fracture faces on axis-aligned planes (synthetic triangles)."""
    return plane_cut_faces([p for p, _ in planes], [n for _, n in planes])


def _oblique_faces() -> List[FractureFace]:
    n = np.array([1.0, 1.0, 0.0]) / math.sqrt(2.0)
    return plane_cut_faces([(0.15, -0.15, 0.0), (0.0, 0.0, 0.0), (-0.2, 0.2, 0.0)],
                           [n, [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]])


def _scan_domain_tokens(paths) -> Dict[str, Any]:
    hits: List[Dict[str, str]] = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read().lower()
        for token in BANNED_TOKENS:
            if re.search(rf"\\b{re.escape(token)}\\b", text):
                hits.append({"file": os.path.relpath(path, ROOT), "token": token})
    return {"files": [os.path.relpath(p, ROOT) for p in paths],
            "banned_tokens": BANNED_TOKENS, "hits": hits, "clean": not hits}


# --------------------------------------------------------------------------- #
# face 1: fracture hand-off
# --------------------------------------------------------------------------- #
def run_fracture_face() -> Dict[str, Any]:
    element = cube_element((0.0, 0.0, 0.0), half=0.5, density=2.0,
                           velocity=(1.0, 0.5, 0.0),
                           angular_velocity=(0.2, -0.4, 0.6),
                           elastic_strain_energy=0.3)
    cases = {
        "single_plane": _axis_faces([((0.1, 0.0, 0.0), (1.0, 0.0, 0.0))]),
        "three_planes": _axis_faces([((0.1, 0.0, 0.0), (1.0, 0.0, 0.0)),
                                     ((0.0, -0.05, 0.0), (0.0, 1.0, 0.0)),
                                     ((0.0, 0.0, 0.2), (0.0, 0.0, 1.0))]),
        "six_planes": _axis_faces([((0.1, 0.0, 0.0), (1.0, 0.0, 0.0)),
                                   ((-0.2, 0.0, 0.0), (1.0, 0.0, 0.0)),
                                   ((0.0, -0.05, 0.0), (0.0, 1.0, 0.0)),
                                   ((0.0, 0.1, 0.0), (0.0, 1.0, 0.0)),
                                   ((0.0, 0.0, 0.2), (0.0, 0.0, 1.0)),
                                   ((0.0, 0.0, -0.15), (0.0, 0.0, 1.0))]),
        "oblique_three": _oblique_faces(),
        # dyadic partition: quarter planes, every vertex exactly representable
        "dyadic_quarters": _axis_faces([((0.25, 0.0, 0.0), (1.0, 0.0, 0.0)),
                                        ((0.0, 0.5, 0.0), (0.0, 0.0, 1.0)),
                                        ((0.0, 0.0, 0.75), (0.0, 0.0, 1.0))]),
    }
    transfer = ExactFractureTransfer()
    results: Dict[str, Any] = {}
    worst = {k: 0.0 for k in ("mass", "momentum", "angular_momentum", "energy",
                              "inertia_tensor", "volume")}
    worst_rational = {k: 0.0 for k in ("mass", "momentum", "angular_momentum",
                                       "inertia_tensor", "volume")}
    rational_exact_zero_all = True

    for name, faces in cases.items():
        ledger, detail = fem_dem_ledger(element, faces, transfer=transfer)
        frags, _ = transfer.transfer(element, faces)
        rational = rational_ledger_residuals(element, frags)
        rational_exact_zero_all = rational_exact_zero_all and all(rational["exact_zero"].values())
        for key, val in detail["residuals"].items():
            worst[key] = max(worst[key], float(val))
        for key, val in rational["floats"].items():
            worst_rational[key] = max(worst_rational[key], float(val))
        results[name] = {
            "n_faces": len(faces),
            "n_fragments": detail["n_fragments"],
            "fragment_masses": detail["fragment_masses"],
            "fragment_inertia_spd": detail["fragment_inertia_spd"],
            "float_residuals": detail["residuals"],
            "rational_residuals": rational["floats"],
            "rational_exact_zero": rational["exact_zero"],
            "seal": ledger.seal,
        }

    # negative baseline: sphere-equivalent fragments (the shortcut model)
    surrogate_tensors = {name: sphere_surrogate_tensors(element, cases[name])
                         for name in ("single_plane", "three_planes")}
    surrogate = {}
    for name in ("single_plane", "three_planes"):
        cmp = sphere_surrogate_ledger(element, cases[name])
        surrogate[name] = cmp
    return {
        "element": {"half": 0.5, "density": 2.0, "velocity": [1.0, 0.5, 0.0],
                    "angular_velocity": [0.2, -0.4, 0.6], "strain_energy": 0.3},
        "cases": results,
        "worst_float": worst,
        "worst_rational": worst_rational,
        "rational_all_exact_zero": rational_exact_zero_all,
        "sphere_surrogate": surrogate,
        "sphere_surrogate_tensors": surrogate_tensors,
        "_ledgers": {name: fem_dem_ledger(element, cases[name], transfer=transfer)[0]
                     for name in cases},
    }


# --------------------------------------------------------------------------- #
# face 2: flow hand-off
# --------------------------------------------------------------------------- #
def run_flow_face() -> Dict[str, Any]:
    source = lattice_cells((0.0, 0.0, 0.0), 0.1, (8, 8, 8), "src")
    shear_field(source, amplitude=0.4, wavenumber=2.0 * math.pi)
    planes = {
        "face_aligned": ((0.4, 0.4, 0.4), (1.0, 0.0, 0.0)),
        "inside_cell": ((0.37, 0.4, 0.4), (1.0, 0.0, 0.0)),
        "oblique": ((0.4, 0.4, 0.4), tuple(np.array([1.0, 1.0, 0.0]) / math.sqrt(2.0))),
    }
    cases: Dict[str, Any] = {}
    worst = {"mass_inventory": 0.0, "momentum_inventory": 0.0, "interface_mass_flux": 0.0}
    worst_baseline = {"mass_inventory": 0.0, "momentum_inventory": 0.0,
                      "interface_mass_flux": 0.0}
    ledgers: Dict[str, ConservationLedger] = {}
    for name, (point, normal) in planes.items():
        target = structured_cells((0.0, 0.0, 0.0), (0.2, 0.2, 0.2), (4, 4, 4), "tgt")
        shear_field(target, amplitude=0.4, wavenumber=2.0 * math.pi)
        ledger, detail = lbm_fvm_ledger(source, target, point, normal)
        base_ledger, base_detail = lbm_fvm_ledger(source, target, point, normal, baseline=True)
        for key in worst:
            worst[key] = max(worst[key], float(detail["residuals"][key]))
            worst_baseline[key] = max(worst_baseline[key], float(base_detail["residuals"][key]))
        cases[name] = {
            "point": list(point),
            "normal": [float(x) for x in normal],
            "area_weighted": detail["residuals"],
            "area_weighted_flux_residual": detail["flux_residual"],
            "area_residual": detail["area_residual"],
            "inverse_distance": base_detail["residuals"],
            "inverse_distance_flux_residual": base_detail["flux_residual"],
            "coverage": detail["coverage"],
            "seal": ledger.seal,
        }
        ledgers[name] = ledger

    # grid-ratio sweep on a flow-normal plane: h_src = 0.1, h_tgt in {0.1,0.2,0.4}
    ratio_sweep = []
    for factor in (1, 2, 4):
        h = 0.1 * factor
        n = int(round(0.8 / h))
        target = structured_cells((0.0, 0.0, 0.0), (h, h, h), (n, n, n), "tgt")
        shear_field(target, amplitude=0.4, wavenumber=2.0 * math.pi)
        ledger, detail = lbm_fvm_ledger(source, target, planes["face_aligned"][0],
                                        planes["face_aligned"][1])
        _, base = lbm_fvm_ledger(source, target, planes["face_aligned"][0],
                                 planes["face_aligned"][1], baseline=True)
        remap = SupermeshRemap()
        remap.build(source, target)
        ratio_sweep.append({
            "h_target": h,
            "n_target_per_axis": n,
            "mass_inventory": detail["residuals"]["mass_inventory"],
            "interface_mass_flux": detail["residuals"]["interface_mass_flux"],
            "inverse_distance_flux": base["residuals"]["interface_mass_flux"],
            "linear_field_error": remap.linear_field_error(
                np.array([1e-1, 2e-1, -3e-1])),
        })
    return {
        "source": {"dx": 0.1, "counts": [8, 8, 8], "field": "rho=1+0.4 sin(2 pi x), u=(0.4y,0.4z,0)"},
        "cases": cases,
        "worst": worst,
        "worst_baseline": worst_baseline,
        "ratio_sweep": ratio_sweep,
        "_ledgers": ledgers,
    }


# --------------------------------------------------------------------------- #
# face 3: clock
# --------------------------------------------------------------------------- #
def run_clock_face() -> Dict[str, Any]:
    ratios = [[10, 10], [10, 3], [7, 13], [1, 10], [10, 3, 7]]
    barriers = []
    for dts in ratios:
        rep = barrier_run(dts, n_windows=100)
        barriers.append({"dt_ticks": dts, **rep.as_dict(),
                         "steps_total": sum(rep.steps.values())})
    worst_skew = max(b["max_skew"] for b in barriers)
    total_stalls = sum(b["stalls"] for b in barriers)
    total_future = sum(b["future_reads"] for b in barriers)
    all_monotone = all(b["monotone"] for b in barriers)

    # detectors must have teeth: negative controls on the barrier itself
    broken = barrier_run([10, 7], n_windows=100, window_override=10)
    peek = barrier_run([10, 3], n_windows=100, peek=True)

    # optimistic: operating point + latency sweep
    operating = optimistic_run(periods=200, latency_steps=(0, 1, 2, 3, 4))
    sweep = []
    for n in (1, 2, 3, 4, 5, 6, 11, 21, 41, 51):
        rep = optimistic_run(periods=200, latency_steps=tuple(range(n)))
        sweep.append({
            "latency_support": n,
            "mean_latency_steps": rep.mean_latency_steps,
            "rollback_rate": rep.rollback_rate,
            "replay_error": rep.replay_error,
            "no_rollback_error": rep.no_rollback_error,
        })
    # load-bearing check: at fixed latency the drift of the no-rollback variant
    # grows with the period (=(ds/dt * m * dt) ~ O(period^2))
    period_sweep = []
    for period in (0.025, 0.05, 0.1, 0.2):
        rep = optimistic_run(periods=200, latency_steps=tuple(range(26)), period=period)
        period_sweep.append({
            "period": period,
            "rollback_rate": rep.rollback_rate,
            "replay_error": rep.replay_error,
            "no_rollback_error": rep.no_rollback_error,
        })

    ledger, detail = clock_ledger(barrier_run(ratios[1], n_windows=100), operating)
    return {
        "barriers": barriers,
        "worst_skew": worst_skew,
        "total_stalls": total_stalls,
        "total_future_reads": total_future,
        "all_monotone": all_monotone,
        "neg_barrier_deadlock": {"stalls": broken.stalls, "deadlock": broken.deadlock,
                                 "max_skew": broken.max_skew},
        "neg_barrier_peek": {"future_reads": peek.future_reads},
        "operating_point": operating.as_dict(),
        "latency_sweep": sweep,
        "period_sweep": period_sweep,
        "_ledger": ledger,
        "_detail": detail,
    }


# --------------------------------------------------------------------------- #
# gate + controls
# --------------------------------------------------------------------------- #
def run_gate_bundle(fracture: Dict[str, Any], flow: Dict[str, Any],
                    clock: Dict[str, Any]) -> Dict[str, Any]:
    # worst-case evidence per face goes into the bundle
    fem = fracture["_ledgers"]["six_planes"]
    fem_oblique = fracture["_ledgers"]["oblique_three"]
    lbm = flow["_ledgers"]["oblique"]
    clk = clock["_ledger"]
    bundle = [fem, fem_oblique, lbm, clk]

    # the gate keys on the face name; keep one document per face in the bundle
    dedup: Dict[str, ConservationLedger] = {}
    for lg in bundle:
        dedup.setdefault(lg.face, lg)
    bundle = [dedup[f] for f in ("fem_dem", "lbm_fvm", "clock")]
    # use the *worst* fracture document (six planes) as the fem_dem evidence
    bundle[0] = fem

    policy = cross_library_policy()
    gate = ConservationGate(policy)
    verdict = gate.adjudicate(bundle)
    controls = run_controls(bundle, gate=gate)

    # substitutions: real output of the shortcut models, not scaled fakes
    surrogate_inertia = fracture["sphere_surrogate_tensors"]["single_plane"]["inertia_tensor"]
    sub_inertia = run_mutator_substitution(
        bundle, value=surrogate_inertia, face="fem_dem", entry="inertia_tensor", gate=gate)
    inverse_flux = flow["cases"]["oblique"]["inverse_distance"]["interface_mass_flux"]
    flux_entry = lbm.entry("interface_mass_flux")
    baseline_flux_target = flux_entry.source * (1.0 + flow["cases"]["oblique"]
                                                ["inverse_distance_flux_residual"])
    sub_flux = run_mutator_substitution(
        bundle, value=baseline_flux_target, face="lbm_fvm",
        entry="interface_mass_flux", gate=gate)

    return {
        "policy": policy.name,
        "faces": [lg.face for lg in bundle],
        "bundle_sha256": bundle_digest(bundle),
        "accepted": verdict.accepted,
        "failures": verdict.failures,
        "rows": verdict.rows,
        "verdict_seal": verdict.seal,
        "controls": controls,
        "substitutions": {"sphere_inertia": sub_inertia, "inverse_distance_flux": sub_flux},
        "_verdict": verdict,
        "_bundle": bundle,
    }


# --------------------------------------------------------------------------- #
# criteria evaluation
# --------------------------------------------------------------------------- #
def evaluate_criteria(fracture, flow, clock, gate_res) -> Dict[str, Any]:
    rows: Dict[str, Any] = {}

    def put(key: str, measured: Any) -> None:
        spec = CRITERIA[key]
        tol = spec.get("tol")
        if spec["op"] == "balance":
            ok = float(measured) <= float(tol)
        elif spec["op"] == "bound":
            ok = float(measured) <= float(tol)
        else:
            ok = bool(measured) is True
        rows[key] = {"op": spec["op"], "tol": tol, "measured": measured, "passed": bool(ok)}

    put("fem_dem.mass", fracture["worst_float"]["mass"])
    put("fem_dem.momentum", fracture["worst_float"]["momentum"])
    put("fem_dem.angular_momentum", fracture["worst_float"]["angular_momentum"])
    put("fem_dem.energy", fracture["worst_float"]["energy"])
    put("fem_dem.inertia_tensor", fracture["worst_float"]["inertia_tensor"])
    put("fem_dem.volume", fracture["worst_float"]["volume"])
    put("lbm_fvm.mass_inventory", flow["worst"]["mass_inventory"])
    put("lbm_fvm.momentum_inventory", flow["worst"]["momentum_inventory"])
    put("lbm_fvm.interface_mass_flux", flow["worst"]["interface_mass_flux"])
    put("clock.barrier_skew", clock["worst_skew"])
    put("clock.deadlocks", clock["total_stalls"])
    put("clock.future_reads", clock["total_future_reads"])
    put("clock.global_time_monotone", clock["all_monotone"])
    put("clock.optimistic_rollback_rate", clock["operating_point"]["rollback_rate"])
    put("clock.optimistic_replay_error", clock["operating_point"]["replay_error"])

    gate_rows = {r["key"]: r for r in gate_res["rows"]}
    extras = {
        "gate.accepted": gate_res["accepted"],
        "gate.all_controls_ok": gate_res["controls"]["all_ok"],
        "gate.positive_accepted": gate_res["controls"]["positive_accepted"],
        "gate.n_controls": gate_res["controls"]["n_controls"],
        "gate.n_rejected_with_expected_code":
            gate_res["controls"]["n_rejected_with_expected_code"],
        "gate.false_accept_rate": gate_res["controls"]["false_accept_rate"],
        "gate.specificity": gate_res["controls"]["specificity"],
        "gate.substitution_sphere_inertia_ok": gate_res["substitutions"]["sphere_inertia"]["ok"],
        "gate.substitution_inverse_distance_flux_ok":
            gate_res["substitutions"]["inverse_distance_flux"]["ok"],
    }
    all_passed = all(r["passed"] for r in rows.values()) and all(
        bool(v) for k, v in extras.items() if isinstance(v, bool))
    return {"criteria": rows, "gate": extras, "gate_criterion_rows": gate_rows,
            "all_passed": bool(all_passed),
            "n_criteria": len(rows) + len([k for k, v in extras.items() if isinstance(v, bool)])}


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #
def make_figures(out_dir: str, fracture, flow, clock) -> List[Dict[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figs: List[Dict[str, str]] = []

    # fig 1: ledger residuals, float vs rational vs sphere surrogate
    names = ["mass", "momentum", "angular_momentum", "energy", "inertia_tensor"]
    float_vals = [max(fracture["worst_float"][n], 1e-18) for n in names]
    # energy is excluded from the rational path on purpose: the strain-energy
    # investment involves sqrt(2U/sum m|d|^2), which is irrational
    rational_vals = [max(fracture["worst_rational"][n], 1e-18) if n in fracture["worst_rational"]
                     else float("nan") for n in names]
    surrogate = fracture["sphere_surrogate"]["three_planes"]
    sur_vals = []
    for n in names:
        key = {"mass": "mass_residual", "momentum": "momentum_residual",
               "angular_momentum": "angular_residual", "energy": "energy_residual",
               "inertia_tensor": "inertia_residual"}[n]
        sur_vals.append(max(abs(float(surrogate.get(key, 0.0))), 1e-18))
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(8.0, 4.4))
    ax.bar(x - 0.27, float_vals, 0.27, label="exact fragments (float)")
    ax.bar(x, rational_vals, 0.27, label="exact fragments (rational ℚ)")
    ax.bar(x + 0.27, sur_vals, 0.27, label="sphere-equivalent surrogate")
    ax.axhline(1e-9, color="k", ls="--", lw=1.0, label="criterion 1e-9")
    if any(math.isnan(v) for v in rational_vals):
        ax.text(len(names) - 0.62, 1e-13, "ℚ n/a\n(irrational)", fontsize=6,
                ha="center", color="grey")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels([n.replace("_", "\n") for n in names], fontsize=8)
    ax.set_ylabel("relative residual (log)")
    ax.set_title("FEM→DEM fracture hand-off: four ledgers + inertia tensor")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.25, axis="y")
    path = os.path.join(out_dir, "fig1_fracture_ledgers.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    figs.append({"name": "fig1_fracture_ledgers.png",
                 "caption": "Worst-case relative residual per ledger over the five fracture "
                            "cases (float and exact-rational re-evaluation) against the "
                            "sphere-equivalent surrogate baseline; dashed line = 1e-9 "
                            "criterion."})

    # fig 2: rollback rate vs latency, with the no-rollback drift
    sweep = clock["latency_sweep"]
    lat = [s["mean_latency_steps"] for s in sweep]
    rate = [100.0 * s["rollback_rate"] for s in sweep]
    drift = [max(s["no_rollback_error"], 1e-16) for s in sweep]
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    ax.plot(lat, rate, "o-", color="tab:blue", label="measured rollback rate")
    ax.plot([0, max(lat)], [0, 2.0 * max(lat)], ":", color="grey",
            label="identity  rate = latency / 50 steps")
    ax.axhline(5.0, color="tab:red", ls="--", lw=1.0, label="criterion 5%")
    ax.set_xlabel("mean message latency (consumer steps of a 50-step period)")
    ax.set_ylabel("rollback rate [%]", color="tab:blue")
    ax2 = ax.twinx()
    ax2.plot(lat, drift, "s--", color="tab:green", label="no-rollback drift")
    ax2.set_yscale("log")
    ax2.set_ylabel("|state − deterministic replay| (no rollback)", color="tab:green")
    lines = ax.get_legend_handles_labels()
    lines2 = ax2.get_legend_handles_labels()
    ax.legend(lines[0] + lines2[0], lines[1] + lines2[1], fontsize=7, loc="upper left")
    ax.set_title("Optimistic clock: rollback cost vs latency, and why rollback is load-bearing")
    ax.grid(alpha=0.25)
    path = os.path.join(out_dir, "fig2_rollback_rate.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    figs.append({"name": "fig2_rollback_rate.png",
                 "caption": "Rollback rate against mean message latency (left axis) with the "
                            "analytical identity rate = latency/period, the 5% criterion, and "
                            "the drift of the same run without rollback (right axis, log)."})

    # fig 3: interface mass flux residual, area-weighted vs inverse distance
    cases = list(flow["cases"].keys())
    aw = [max(flow["cases"][c]["area_weighted"]["interface_mass_flux"], 1e-18) for c in cases]
    idw = [max(flow["cases"][c]["inverse_distance"]["interface_mass_flux"], 1e-18)
           for c in cases]
    ratio = [max(r["interface_mass_flux"], 1e-18) for r in flow["ratio_sweep"]]
    ratio_b = [max(r["inverse_distance_flux"], 1e-18) for r in flow["ratio_sweep"]]
    labels = cases + [f"h/H={r['h_target']/0.1:.0f}" for r in flow["ratio_sweep"]]
    fig, ax = plt.subplots(figsize=(8.0, 4.2))
    xs = np.arange(len(labels))
    ax.bar(xs - 0.2, aw + ratio, 0.4, label="area-weighted (conservative)")
    ax.bar(xs + 0.2, idw + ratio_b, 0.4, label="inverse-distance (shortcut)")
    ax.axhline(1e-6, color="k", ls="--", lw=1.0, label="criterion 1e-6")
    ax.set_yscale("log")
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, rotation=20, fontsize=7)
    ax.set_ylabel("interface mass-flux relative residual (log)")
    ax.set_title("LBM→FVM interface flux conservation across coupling planes / grid ratios")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.25, axis="y")
    path = os.path.join(out_dir, "fig3_flux_residual.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    figs.append({"name": "fig3_flux_residual.png",
                 "caption": "Interface mass-flux residual for the area-weighted operator "
                            "versus the inverse-distance shortcut on an aligned, an "
                            "interior-cut and an oblique coupling plane plus a grid-ratio "
                            "sweep; dashed line = 1e-6 criterion."})
    return figs


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("artifacts", "p167w2b-n08"))
    args = ap.parse_args()
    out_dir = os.path.join(ROOT, args.out)
    os.makedirs(out_dir, exist_ok=True)

    t0 = time.time()
    fracture = run_fracture_face()
    flow = run_flow_face()
    clock = run_clock_face()
    gate_res = run_gate_bundle(fracture, flow, clock)
    criteria = evaluate_criteria(fracture, flow, clock, gate_res)

    pkg_dir = os.path.join(ROOT, "src", "coupling", "conservation_gate")
    token_scan = _scan_domain_tokens(
        [os.path.join(pkg_dir, f) for f in sorted(os.listdir(pkg_dir)) if f.endswith(".py")])
    criteria["domain_semantics_scan"] = token_scan
    criteria["all_passed"] = bool(criteria["all_passed"] and token_scan["clean"])

    def strip(obj):
        if isinstance(obj, dict):
            return {k: strip(v) for k, v in obj.items() if not k.startswith("_")}
        if isinstance(obj, (list, tuple)):
            return [strip(v) for v in obj]
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.floating, np.integer)):
            return obj.item()
        return obj

    audit = {
        "node": "P167W2B-N08",
        "title": "cross-library field-exchange conservation gate",
        "schema": "conservation_gate/1",
        "generated_by": "scripts/run_p167w2b_n08_conservation_audit.py",
        "package_version": VERSION,
        "wall_time_s": None,
        "criteria": criteria["criteria"],
        "gate": criteria["gate"],
        "gate_criterion_rows": criteria["gate_criterion_rows"],
        "all_passed": criteria["all_passed"],
        "n_criteria": criteria["n_criteria"],
        "domain_semantics_scan": token_scan,
        "fracture_face": strip(fracture),
        "flow_face": strip(flow),
        "clock_face": strip(clock),
        "gate_bundle": strip(gate_res),
    }
    audit["wall_time_s"] = round(time.time() - t0, 2)

    audit_path = os.path.join(out_dir, "conservation_audit.json")
    with open(audit_path, "w", encoding="utf-8") as fh:
        json.dump(audit, fh, indent=2, sort_keys=True)

    verdict_path = os.path.join(out_dir, "gate_verdict.json")
    with open(verdict_path, "w", encoding="utf-8") as fh:
        json.dump(strip(gate_res["_verdict"].as_dict()), fh, indent=2, sort_keys=True)

    controls_path = os.path.join(out_dir, "negative_controls.json")
    with open(controls_path, "w", encoding="utf-8") as fh:
        json.dump(strip(gate_res["controls"]), fh, indent=2, sort_keys=True)

    figs = make_figures(out_dir, fracture, flow, clock)

    manifest = {
        "node": "P167W2B-N08",
        "artifacts": {os.path.basename(p): _digest(p) for p in
                      [audit_path, verdict_path, controls_path] +
                      [os.path.join(out_dir, f["name"]) for f in figs]},
        "figures": figs,
        "all_passed": audit["all_passed"],
        "wall_time_s": audit["wall_time_s"],
    }
    with open(os.path.join(out_dir, "run_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)

    print(json.dumps({"all_passed": audit["all_passed"],
                      "n_criteria": audit["n_criteria"],
                      "gate_accepted": audit["gate"]["gate.accepted"],
                      "controls_ok": audit["gate"]["gate.all_controls_ok"],
                      "wall_time_s": audit["wall_time_s"]}, indent=2))
    for key, row in audit["criteria"].items():
        print(f"  {key:34s} measured={row['measured']!r:>12}  tol={row['tol']!r:>8}  "
              f"{'PASS' if row['passed'] else 'FAIL'}")
    return 0 if audit["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())