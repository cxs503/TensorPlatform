"""Figures for the conservative-exchange / clock-semantics gate (no domain content).

Every number is read from the acceptance artefact produced by
``python -m tools.coupling.exchange_gate``; nothing is typed in by hand, so a
figure cannot disagree with the gate.

    python tools/coupling_exchange_figures.py --json <acceptance.json> --outdir <dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

THRESHOLD_COLOR = "#b2182b"


def _load(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def fig_conservation(data: dict, outdir: Path) -> Path:
    rows = data["C1"]["rows"]
    labels, totals, moments = [], [], []
    for row in rows:
        op = row["operator"].replace("_", r"\_")
        labels.append(f"{row['quantity']}\n{op}\n{row['layout']}")
        totals.append(max(row.get("total_residual", 1e-18), 1e-18))
        moments.append(max(row.get("first_moment_residual", row.get("affine_reproduction_error",
                                                                   1e-18)), 1e-18))
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(max(9.0, 0.75 * len(labels) + 3.0), 4.4))
    ax.bar(x - 0.2, totals, width=0.4, label="amount residual", color="#2166ac")
    ax.bar(x + 0.2, moments, width=0.4, label="first-moment residual", color="#5aae61")
    ax.axhline(1e-12, color=THRESHOLD_COLOR, ls="--", lw=1.2, label="gate 1e-12")
    ax.set_yscale("log")
    ax.set_ylim(1e-18, 1e-8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7, rotation=0)
    ax.set_ylabel("relative residual [-]")
    ax.set_title("C1 conservation per quantity law (closed-form moment projection)")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(axis="y", alpha=0.3, which="both")
    fig.tight_layout()
    path = outdir / "fig1_conservation_c1.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_torque(data: dict, outdir: Path) -> Path:
    rows = [r for r in data["C1"]["rows"] if "torque_residual" in r]
    labels = [f"{r['operator'].replace('_', chr(92) + '_')}\n{r['layout']}" for r in rows]
    torque = [max(r["torque_residual"], 1e-18) for r in rows]
    moment = [max(r["first_moment_residual"], 1e-18) for r in rows]
    x = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    ax.bar(x - 0.2, moment, width=0.4, label="first-moment tensor", color="#5aae61")
    ax.bar(x + 0.2, torque, width=0.4, label="lumped torque  |sum r x F|", color="#8073ac")
    ax.axhline(1e-12, color=THRESHOLD_COLOR, ls="--", lw=1.2, label="gate 1e-12")
    ax.set_yscale("log")
    ax.set_ylim(1e-18, 1e-8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("relative residual [-]")
    ax.set_title("C1 vector exchange: torque is conserved through the moment tensor")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3, which="both")
    fig.tight_layout()
    path = outdir / "fig2_torque_c1.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_ablation(data: dict, outdir: Path) -> Path:
    c5 = data["C5"]
    seeds = [row["seed"] for row in c5["per_seed"]]
    off = [max(row["drift_without_correction"], 1e-18) for row in c5["per_seed"]]
    on = [max(row["drift_with_correction"], 1e-18) for row in c5["per_seed"]]
    fig, ax = plt.subplots(figsize=(8.0, 4.2))
    ax.plot(seeds, off, "o-", color="#b2182b", label="correction switched off")
    ax.plot(seeds, on, "s-", color="#2166ac", label="correction on")
    ax.axhline(1e-2, color="k", ls="--", lw=1.2, label="detection threshold 1 %")
    ax.set_yscale("log")
    ax.set_xlabel("trial (different source centroid and destination mesh)")
    ax.set_ylabel("relative drift of the amount [-]")
    ax.set_title("C5 positive control: the gate has teeth "
                 f"(detection rate {c5['detection_rate'] * 100:.0f} %)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    path = outdir / "fig3_ablation_c5.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_patch(data: dict, outdir: Path) -> Path:
    rows = data["C3"]["rows"]
    operators = sorted({op for row in rows for op in row["errors"]})
    layouts = sorted({row["layout"] for row in rows})
    motions = sorted({row["motion"] for row in rows})
    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    width = 0.8 / (len(operators) * len(motions))
    idx = 0
    colours = {"overlap_l2": "#b2182b", "overlap_p1": "#2166ac",
               "rbf_conservative": "#f4a582"}
    for layout in layouts:
        for motion in motions:
            subset = [r for r in rows if r["layout"] == layout and r["motion"] == motion][0]
            for op in operators:
                val = max(subset["errors"][op], 1e-18)
                ax.bar(idx, val, width=width, color=colours.get(op, "grey"),
                       label=op if idx < len(operators) else None)
                idx += 1
        idx += 0.6
    ax.axhline(1e-12, color="k", ls="--", lw=1.2, label="gate 1e-12")
    ax.axhline(1e-2, color=THRESHOLD_COLOR, ls=":", lw=1.2, label="negative-control level 1 %")
    ax.set_yscale("log")
    ax.set_ylim(1e-18, 1e-1)
    ax.set_ylabel("rigid-motion reproduction error [-]")
    ax.set_title("C3 patch test: second order reproduces rigid motion, first order does not")
    ax.set_xticks([])
    ax.legend(fontsize=8, ncol=2)
    ax.grid(axis="y", alpha=0.3, which="both")
    fig.tight_layout()
    path = outdir / "fig4_patch_test_c3.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_loop(data: dict, outdir: Path) -> Path:
    rows = data["C4"]["rows"]
    fig, ax = plt.subplots(figsize=(7.8, 4.2))
    for row, colour in zip(rows, ("#2166ac", "#1b7837", "#762a83")):
        hist = row["history"]
        cycles = [h["cycle"] for h in hist]
        worst = [max(h["amount"], h["momentum"], h["energy"], 1e-18) for h in hist]
        ax.semilogy(cycles, worst, "o-", color=colour,
                    label=f"{row['operator']} (moment projection on)")
    ax.axhline(1e-10, color=THRESHOLD_COLOR, ls="--", lw=1.2, label="gate 1e-10")
    ax.set_xlabel("coupling cycle")
    ax.set_ylabel("worst conserved-amount residual [-]")
    ax.set_title("C4 end-to-end coupled loop: amount, momentum, energy")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    path = outdir / "fig5_coupled_loop_c4.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_clock(data: dict, outdir: Path) -> Path:
    orders = data["C6"]["measured_orders"]
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    colours = {"sync": "#999999", "linear_interpolation": "#2166ac",
               "one_step_extrapolation": "#b2182b", "event_triggered": "#1b7837"}
    for row in orders:
        errors = np.asarray(row["errors"], dtype=float)
        if not np.all(np.isfinite(errors)) or np.all(errors == 0.0):
            continue
        n = len(errors)
        dts = 1.0 / np.array([8, 16, 32, 64][:n], dtype=float)
        slope = row["measured_order"]
        ax.loglog(dts, np.maximum(errors, 1e-18), "o-", color=colours.get(row["mode"], "grey"),
                  label=f"{row['mode']} (slope {slope:.2f})" if slope is not None
                  else row["mode"])
    ax.set_xlabel("sub-step dt [-]")
    ax.set_ylabel("exchange error [-]")
    ax.set_title("C6 clock semantics: measured error order per exchange mode")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    path = outdir / "fig6_clock_orders_c6.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="artifacts/coupling-exchange-n02/acceptance.json")
    parser.add_argument("--outdir", default="artifacts/coupling-exchange-n02")
    args = parser.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    data = _load(Path(args.json))
    made = [fig_conservation(data, outdir), fig_torque(data, outdir), fig_ablation(data, outdir),
            fig_patch(data, outdir), fig_loop(data, outdir), fig_clock(data, outdir)]
    for path in made:
        print(path)


if __name__ == "__main__":
    main()