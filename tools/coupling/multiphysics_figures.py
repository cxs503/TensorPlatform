#!/usr/bin/env python3
"""Figures for the multi-physics coupling verification framework (P250-N08).

Four figures, every number taken from the same artifact the acceptance gate
reads (no hand-copied values):

``fig1_interface_flux.png``       per-block solution + per-face flux from both
                                  sides and from the closed form
``fig2_conservation_table.png``   global budget table per conserved quantity +
                                  defect-injection curve against the threshold
``fig3_convergence.png``          coupling residual per iteration for three
                                  contrasts and three coupling schemes
``fig4_error_propagation.png``    measured sensitivity of five inputs against
                                  the closed form and the 1.0 threshold

Each PNG is written with a SHA-256 recorded alongside it so a report can bind the
picture to the run that produced it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

__all__ = ["render_figures", "sha256_of"]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _fig1(payload: Dict[str, Any], path: Path) -> None:
    fields = payload["fields"]
    case = fields[0]
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.6))

    ax = axes[0]
    offset = 0.0
    colours = plt.cm.viridis(np.linspace(0.1, 0.85, len(case["blocks"])))
    for block, colour in zip(case["blocks"], colours):
        x = offset + np.asarray(block["centers"])
        ax.plot(x, block["values"], "o", ms=3.2, color=colour, label="coupled (FV)")
        ax.plot(x, block["analytic"], "-", lw=1.1, color=colour, alpha=0.75)
        offset += block["length"]
    for face in case["interfaces"]:
        ax.axvline(face["position"], color="0.35", ls="--", lw=0.9)
        ax.text(
            face["position"],
            ax.get_ylim()[1],
            f"face {face['index']}",
            rotation=90,
            va="top",
            ha="right",
            fontsize=7,
            color="0.35",
        )
    ax.set_xlabel("position along the coupled chain")
    ax.set_ylabel("field value u")
    ax.set_title("(a) coupled field per block, dots FV vs line closed form")
    ax.legend(fontsize=7, loc="best")

    ax = axes[1]
    faces = payload["interface"]["faces"]
    idx = np.arange(len(faces))
    width = 0.27
    ax.bar(
        idx - width,
        [abs(f["flux_left_side"]) for f in faces],
        width,
        label="sending side |q|",
        color="tab:blue",
    )
    ax.bar(
        idx,
        [abs(f["flux_right_side"]) for f in faces],
        width,
        label="receiving side |q|",
        color="tab:orange",
    )
    ax.bar(
        idx + width,
        [abs(f["analytic_left_side"]) for f in faces],
        width,
        label="closed form |q|",
        color="0.6",
    )
    ax.set_xticks(idx)
    ax.set_xticklabels([f"face {f['face']}" for f in faces])
    ax.set_ylabel("flux magnitude across the face")
    ax.set_title(
        f"(b) flux per coupling face, max rel. imbalance "
        f"{payload['interface']['max_rel_error']:.2e} (< {payload['interface']['threshold']:.0%})"
    )
    ax.legend(fontsize=7)
    ax2 = ax.twinx()
    ax2.semilogy(idx, [max(f["rel_error"], 1e-18) for f in faces], "kv-", ms=4, lw=0.9)
    ax2.set_ylabel("relative imbalance (log)")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _fig2(payload: Dict[str, Any], path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.4), gridspec_kw={"width_ratios": [1.35, 1.0]})
    rows = payload["budget"]["quantities"]
    ax = axes[0]
    ax.axis("off")
    header = [
        "quantity",
        "flux in",
        "flux out",
        "vol. source",
        "face source",
        "budget residual",
        "rel. error",
        "status",
    ]
    cells: List[List[str]] = []
    for row in rows:
        cells.append(
            [
                row["quantity"],
                f"{row['flux_in']:+.6f}",
                f"{row['flux_out']:+.6f}",
                f"{row['source_volume']:+.4f}",
                f"{row['source_interface']:+.4f}",
                f"{row['exchanged_residual']:+.3e}",
                f"{row['exchanged_rel_error']:.2e}",
                "PASS" if row["passes"] else "FAIL",
            ]
        )
    table = ax.table(cellText=cells, colLabels=header, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(7.2)
    table.scale(1.0, 1.45)
    for key, cell in table.get_celld().items():
        if key[0] == 0:
            cell.set_facecolor("#dde6f2")
        elif cells[key[0] - 1][-1] == "PASS":
            cell.set_facecolor("#e6f4ea")
        else:
            cell.set_facecolor("#fde8e8")
    ax.set_title(
        "(a) global conservation ledger, threshold "
        f"{payload['budget']['threshold']:.0%}  (max rel. error "
        f"{payload['budget']['max_rel_error']:.2e})",
        fontsize=9,
    )

    ax = axes[1]
    defect = payload["defect"]
    scales = np.asarray(defect["scales"], dtype=float)
    ax.semilogy(
        scales,
        np.maximum(defect["interface_rel_error"], 1e-17),
        "o-",
        label="interface flux imbalance",
    )
    ax.semilogy(
        scales, np.maximum(defect["budget_rel_error"], 1e-17), "s-", label="global budget residual"
    )
    ax.axhline(
        payload["interface"]["threshold"],
        color="tab:red",
        ls="--",
        lw=1.0,
        label=f"A1 threshold {payload['interface']['threshold']:.0%}",
    )
    ax.axhline(
        payload["budget"]["threshold"],
        color="tab:green",
        ls=":",
        lw=1.0,
        label=f"A2 threshold {payload['budget']['threshold']:.0%}",
    )
    ax.set_xlabel("flux transfer scale injected into the exchange")
    ax.set_ylabel("relative error (log)")
    ax.set_title("(b) gate response to a non-conservative exchange")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _fig3(payload: Dict[str, Any], path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.4))
    conv = payload["convergence"]
    ax = axes[0]
    for name, entry in conv["cases"].items():
        series = np.asarray(entry["newton"], dtype=float)
        ax.semilogy(
            np.arange(series.size),
            series,
            "-",
            lw=1.3,
            label=f"{name}: rho={entry['metrics']['spectral_ratio']:.4f}, "
            f"p={entry['metrics']['order']:.4f}",
        )
    ax.set_xlabel("coupling iteration")
    ax.set_ylabel(r"$\|\Delta\phi\|/\|\phi\|$")
    ax.set_title(
        "(a) production scheme (measured face Jacobian, damping "
        f"{conv['damping']:g}): predicted rho = {abs(1 - conv['damping']):.2f}"
    )
    ax.legend(fontsize=7)
    ax.grid(True, which="major", alpha=0.3)

    ax = axes[1]
    for scheme, series in conv["scheme_study"].items():
        series = np.asarray(series, dtype=float)
        ax.semilogy(
            np.arange(series.size),
            series,
            "-",
            lw=1.2,
            label=f"{scheme} (rho={conv['scheme_metrics'][scheme]['spectral_ratio']:.3f})",
        )
    ax.set_xlabel("coupling iteration")
    ax.set_ylabel("flux imbalance (relative)")
    ax.set_title("(b) same case, three coupling schemes: the gate rejects the two slow ones")
    ax.legend(fontsize=7)
    ax.grid(True, which="major", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _fig4(payload: Dict[str, Any], path: Path) -> None:
    sens = payload["sensitivity"]
    rows = sens["parameters"]
    labels = [r["parameter"] for r in rows]
    measured = [r["abs_sensitivity"] for r in rows]
    analytic = [abs(r["analytic_sensitivity"]) for r in rows]
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    idx = np.arange(len(rows))
    width = 0.36
    ax.bar(idx - width / 2, measured, width, label="coupled solver (measured)", color="tab:blue")
    ax.bar(idx + width / 2, analytic, width, label="closed form (reference)", color="0.65")
    ax.axhline(
        sens["threshold"],
        color="tab:red",
        ls="--",
        lw=1.2,
        label=f"threshold |S| < {sens['threshold']:g}",
    )
    ax.set_xticks(idx)
    ax.set_xticklabels(labels, rotation=18, ha="right", fontsize=8)
    ax.set_ylabel(r"sensitivity coefficient $S=(\Delta y/y)/(\Delta p/p)$")
    ax.set_title(
        f"error propagation through the coupling, max |S| = {sens['max_abs_sensitivity']:.4f}"
    )
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def render_figures(payload: Dict[str, Any], outdir) -> List[Dict[str, Any]]:
    """Render the four figures and return their manifest rows."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    specs = [
        (
            "fig1_interface_flux.png",
            "Per-block coupled field against the closed form (a) and the flux across every coupling "
            "face as computed by the sending side, the receiving side and the closed form (b).",
            _fig1,
        ),
        (
            "fig2_conservation_table.png",
            "Global conservation ledger per conserved quantity with the A2 threshold (a) and the "
            "response of the A1/A2 gates to a deliberately non-conservative exchange (b).",
            _fig2,
        ),
        (
            "fig3_convergence.png",
            "Coupling residual per iteration for three coefficient contrasts (a) and for three "
            "coupling schemes on the same case (b).",
            _fig3,
        ),
        (
            "fig4_error_propagation.png",
            "Measured sensitivity of the coupled output to five inputs, against the closed-form value "
            "and the |S| < 1 requirement.",
            _fig4,
        ),
    ]
    manifest = []
    for name, caption, fn in specs:
        path = out / name
        fn(payload, path)
        manifest.append(
            {
                "name": name,
                "path": str(path),
                "caption": caption,
                "sha256": sha256_of(path),
                "bytes": int(path.stat().st_size),
            }
        )
    (out / "figures.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
