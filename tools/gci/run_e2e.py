"""End-to-end ITTC GCI / U_V study: solve, converge, adjudicate, plot.

Run::

    python -m tools.gci.run_e2e            # writes artifacts/gci/*

Produces
--------
``artifacts/gci/gci_e2e.json``      machine-readable record of every case
``artifacts/gci/fig1_convergence.png``  three-grid convergence + Richardson limit
``artifacts/gci/fig2_richardson.png``   log-log error-vs-mesh Richardson plot
``artifacts/gci/fig3_u_v_gate.png``     U_V against the 15% experimental gate

The primary case is the in-repo analytic verification case
(``tools.gci.cases``: circular-section added mass, exact reference value 1.0),
run on three systematically refined grids.  The remaining rows are
gate-logic demonstrations and are *labelled as such* in the JSON
(``"kind": "synthetic"``) -- they exist to exercise the FAIL and WITHHELD
branches of the verdict, never to support a validation claim.
"""

from __future__ import annotations

import json
import math
import os
import platform
import subprocess
import sys
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from tools.gci.anchors import reproduction_table  # noqa: E402
from tools.gci.cases import ANALYTIC_CA, circle_added_mass, refinement_levels  # noqa: E402
from tools.gci.core import GATE_DEFAULT, gci, ittc_validation_state, verdict  # noqa: E402

OUT_DIR = os.path.join("artifacts", "gci")


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:  # pragma: no cover
        return "unknown"


def run_case(base_nr: int, base_ntheta: int, ratio: int = 2, levels: int = 3) -> Dict[str, object]:
    grids = refinement_levels(base_nr=base_nr, base_ntheta=base_ntheta, ratio=ratio, levels=levels)
    runs: List[Dict[str, object]] = [circle_added_mass(g["nr"], g["ntheta"]).to_dict() for g in grids]
    f1 = float(runs[-1]["ca"])  # finest
    f2 = float(runs[-2]["ca"])
    f3 = float(runs[-3]["ca"])
    res = gci(f1, f2, f3, float(ratio), float(ratio), fs=1.25)
    dev = abs(f1 - ANALYTIC_CA) / ANALYTIC_CA
    return {
        "grids": runs,
        "f1": f1,
        "f2": f2,
        "f3": f3,
        "reference": ANALYTIC_CA,
        "deviation": dev,
        "result": res,
        "verdict": verdict(res.u_v, dev, GATE_DEFAULT),
    }


def synthetic_case(
    case_id: str,
    reason: str,
    f1: float,
    f2: float,
    f3: float,
    reference: float,
    ratio: float = 2.0,
) -> Dict[str, object]:
    res = gci(f1, f2, f3, ratio, ratio, fs=1.25)
    dev = abs(f1 - reference) / abs(reference)
    return {
        "grids": [],
        "f1": f1,
        "f2": f2,
        "f3": f3,
        "reference": reference,
        "deviation": dev,
        "result": res,
        "verdict": verdict(res.u_v, dev, GATE_DEFAULT),
        "kind": "synthetic",
        "label": case_id,
        "reason": reason,
    }


def _plot_convergence(main: Dict[str, object], path: str) -> None:
    res = main["result"]
    grids = main["grids"]
    h = [1.0 / (g["nr"] / grids[0]["nr"]) for g in grids]  # h/h_coarse
    ca = [g["ca"] for g in grids]
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.semilogx(h, ca, "o-", label="FD solution $C_a^{num}$ (3 grids)", color="#1f77b4")
    ax.axhline(res.phi_ext, ls="--", color="#d62728",
               label=f"Richardson limit $\\phi_{{ext}}$ = {res.phi_ext:.7f}")
    ax.axhline(ANALYTIC_CA, ls=":", color="#2ca02c",
               label=f"exact $C_a$ = {ANALYTIC_CA:.1f}")
    for x, y, g in zip(h, ca, grids):
        ax.annotate(f"{g['nr']}x{g['ntheta']}", (x, y), textcoords="offset points",
                    xytext=(6, -12), fontsize=8)
    ax.set_xlabel("mesh spacing h / h$_{coarse}$  (log scale)")
    ax.set_ylabel("added-mass coefficient $C_a$")
    ax.set_title(f"ITTC 7.5-03-02-01 three-grid convergence\n"
                 f"$p_{{obs}}$ = {res.p_used:.4f},  $R_G$ = {res.R_G:.4f},  "
                 f"$GCI_{{21}}$ = {100 * res.gci21:.4f}%")
    ax.invert_xaxis()
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _plot_richardson(main: Dict[str, object], path: str) -> None:
    res = main["result"]
    grids = main["grids"]
    h = [grids[0]["ds"] / g["ds"] for g in grids]  # normalised mesh spacing
    ca = [g["ca"] for g in grids]
    err = [abs(c - res.phi_ext) for c in ca]
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    ax.loglog(h, err, "s", color="#1f77b4", label="|$C_a^{(i)}-\\phi_{ext}$|")
    # fitted line through the two finest grids: slope = p_obs
    slope = res.p_used
    x = [h[-1] * 0.8, h[0] * 1.25]
    c = err[-1] / (h[-1] ** -slope) if err[-1] > 0 else 1.0
    ax.loglog(x, [c * xx ** -slope for xx in x], "--", color="#d62728",
              label=f"slope $-p_{{obs}}$ = -{slope:.4f}")
    ref_c = err[0] / (h[0] ** -2.0) if err[0] > 0 else 1.0
    ax.loglog(x, [ref_c * xx ** -2.0 for xx in x], ":", color="#2ca02c",
              label="reference slope $p$ = -2")
    for xx, yy, g in zip(h, err, grids):
        ax.annotate(f"{g['nr']}x{g['ntheta']}", (xx, yy), textcoords="offset points",
                    xytext=(6, 6), fontsize=8)
    ax.set_xlabel("normalised mesh spacing h / h$_{coarse}$ (log)")
    ax.set_ylabel("estimate of the discretisation error (log)")
    ax.set_title("Richardson / grid-convergence plot")
    ax.legend(fontsize=8)
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _plot_gate(records: List[Dict[str, object]], path: str) -> None:
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    labels, uvs, devs, colors = [], [], [], []
    cmap = {"PASS": "#2ca02c", "FAIL": "#d62728", "WITHHELD": "#ff7f0e"}
    for rec in records:
        res = rec["result"]
        labels.append(rec.get("label") or f"base {rec['grids'][0]['nr']}x{rec['grids'][0]['ntheta']}")
        uvs.append(100.0 * res.u_v)
        devs.append(100.0 * float(rec["deviation"]))
        colors.append(cmap[str(rec["verdict"])])
    x = range(len(labels))
    ax.bar(x, uvs, color=colors, alpha=0.75, label="$U_V$ = $GCI_{21}$ [%]")
    ax.plot(list(x), devs, "k^", ms=8, label="experimental/benchmark deviation [%]")
    ax.axhline(100 * GATE_DEFAULT, ls="--", color="#9467bd",
               label=f"15% acceptance gate")
    ax.set_yscale("log")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=15, fontsize=8)
    ax.set_ylabel("percent (log scale)")
    ax.set_title("$U_V$ versus the 15% experimental gate "
                 "(colour = verdict: green PASS, red FAIL, orange WITHHELD)")
    ax.legend(fontsize=8)
    ax.grid(True, which="both", axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)

    main_case = run_case(40, 64)
    coarse_case = run_case(10, 16)
    # Gate-logic demonstrations (explicitly synthetic):
    fail_case = synthetic_case(
        "SYNTHETIC-FAIL",
        "biased solver: fine-grid QoI displaced by 20% while the triplet stays "
        "monotonically convergent, so U_V is tight and the deviation must be "
        "attributed to the solver",
        1.2,
        1.2 * (1.0 - 1e-3),
        1.2 * (1.0 - 1e-3) * (1.0 - 4e-3),
        1.0,
    )
    withheld_case = synthetic_case(
        "SYNTHETIC-WITHHELD",
        "oscillatory triplet (R_G < 0): the observed order is unusable, the "
        "limiting-order fallback gives a GCI band wider than the gate",
        1.0,
        1.30,
        1.20,
        1.0,
    )

    records = [
        dict(main_case, label="circle-section 40..160", kind="real"),
        dict(coarse_case, label="circle-section 10..40", kind="real"),
        fail_case,
        withheld_case,
    ]

    payload = {
        "meta": {
            "standard": "ITTC 7.5-03-01-01 / 7.5-03-02-01 (2017)",
            "gate": GATE_DEFAULT,
            "safety_factor": 1.25,
            "reference_value": ANALYTIC_CA,
            "python": platform.python_version(),
            "git_commit": _git_commit(),
            "case": "tools.gci.cases.circle_added_mass "
                    "(2D circular-section added mass, exact reference 1.0)",
        },
        "cases": [],
        "anchors": reproduction_table(),
    }
    for rec in records:
        res = rec["result"]
        payload["cases"].append(
            {
                "label": rec["label"],
                "kind": rec["kind"],
                "reason": rec.get("reason"),
                "grids": rec["grids"],
                "f1": rec["f1"],
                "f2": rec["f2"],
                "f3": rec["f3"],
                "reference": rec["reference"],
                "deviation": rec["deviation"],
                "r21": res.r21,
                "r32": res.r32,
                "p": res.p_used,
                "p_obs": res.p_obs,
                "p_source": res.p_source,
                "R_G": res.R_G,
                "GCI21": res.gci21,
                "GCI32": res.gci32,
                "U_V": res.u_v,
                "convergence": res.convergence,
                "phi_ext": res.phi_ext,
                "ittc_state": ittc_validation_state(res.u_v, float(rec["deviation"])),
                "verdict": rec["verdict"],
            }
        )

    with open(os.path.join(OUT_DIR, "gci_e2e.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, allow_nan=False)

    _plot_convergence(main_case, os.path.join(OUT_DIR, "fig1_convergence.png"))
    _plot_richardson(main_case, os.path.join(OUT_DIR, "fig2_richardson.png"))
    _plot_gate(records, os.path.join(OUT_DIR, "fig3_u_v_gate.png"))

    hdr = f"{'case':26s} {'p':>8s} {'R_G':>8s} {'GCI21%':>10s} {'U_V%':>10s} {'dev%':>10s} {'verdict':>9s}"
    print(hdr)
    print("-" * len(hdr))
    for rec in records:
        res = rec["result"]
        print(
            f"{rec['label']:26s} {res.p_used:8.4f} {res.R_G:8.4f} "
            f"{100 * res.gci21:10.4f} {100 * res.u_v:10.4f} "
            f"{100 * float(rec['deviation']):10.4f} {rec['verdict']:>9s}"
        )
    print(f"\nartifacts written to {OUT_DIR}/")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())