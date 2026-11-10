#!/usr/bin/env python3
"""Figures for the domain-neutral verification harness (all numbers from artifacts).

Reads ``artifacts/verification_harness/study.json`` + ``bundle/report.json`` and
emits three panels — no number is typed by hand (程序化注入零手抄):

``fig_vh_error_vs_tolerance.png``  per-quantity relative error against the
                                   declared acceptance band (log scale)
``fig_vh_convergence.png``         composite-midpoint convergence on the
                                   synthetic parabolic form, measured vs
                                   theoretical order ``p = 2``
``fig_vh_tamper.png``              tamper-detection rates per tamper class

Usage: python3 generate_verification_harness_figures.py [--dir artifacts/verification_harness]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def _error_panel(study: dict, ax) -> None:
    rows = study["run"]["error_table"]
    labels, rel_errors, bands = [], [], []
    for row in rows:
        labels.append(f"{row['case_id'].replace('synth-', '')}\n{row['quantity']}")
        rel = row["rel_error"]
        # a zero residual cannot be plotted on a log axis: clamp at the double
        # epsilon floor and say so in the caption
        rel_errors.append(max(rel if rel is not None else 1e-16, 1e-16))
        bands.append(row["rtol"])
    positions = range(len(labels))
    ax.bar(positions, rel_errors, color="#2b6cb0", label="measured rel. error")
    ax.plot(list(positions), bands, linestyle="none", marker="_", markersize=18,
            color="#c53030", label="declared rtol")
    ax.set_yscale("log")
    ax.set_xticks(list(positions))
    ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("relative error (log)")
    ax.set_title("A. Every quantity vs its acceptance band")
    ax.grid(True, axis="y", which="both", alpha=0.3)
    ax.legend(fontsize=8)


def _convergence_panel(study: dict, ax) -> None:
    conv = study["convergence"]
    h = [row["h"] for row in conv["rows"]]
    err_volume = [row["rel_error_volume"] for row in conv["rows"]]
    err_water = [row["rel_error_waterplane"] for row in conv["rows"]]
    ax.loglog(h, err_volume, "o-", label="volume (quadrature)")
    ax.loglog(h, err_water, "s--", label="waterplane area (quadrature)")
    # reference slope p = 2 anchored on the finest volume error
    anchor = err_volume[-1] / (h[-1] ** 2)
    ax.loglog(h, [anchor * value ** 2 for value in h], "k:",
              label=f"slope p={conv['theoretical_order']:.0f} (theory)")
    ax.set_xlabel("h = L / nx  (grid spacing, m)")
    ax.set_ylabel("relative error vs closed form")
    ax.set_title("B. Observed order $p$ = "
                 f"{conv['observed_order_volume']:.3f} (volume)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)


def _tamper_panel(study: dict, ax) -> None:
    tamper = study["tamper"]
    names = sorted(tamper["classes"])
    values = [1.0 if tamper["classes"][name]["detected"] else 0.0 for name in names]
    names.append("random\nsingle-byte")
    values.append(tamper["byte_flip_rate"])
    positions = range(len(names))
    bars = ax.bar(positions, [value * 100.0 for value in values],
                  color=["#2f855a" if value == 1.0 else "#c53030" for value in values])
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2,
                f"{value * 100:.0f}%", ha="center", fontsize=8)
    ax.axhline(100.0, color="#c53030", linestyle="--", linewidth=1)
    ax.set_xticks(list(positions))
    ax.set_xticklabels(names, rotation=20, ha="right", fontsize=8)
    ax.set_ylim(0, 118)
    ax.set_ylabel("detection rate [%]")
    ax.set_title(f"C. Tamper detection {tamper['detected_total']}/{tamper['trials_total']}")
    ax.grid(True, axis="y", alpha=0.3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="artifacts/verification_harness")
    args = parser.parse_args()
    out = Path(args.dir)
    study = json.loads((out / "study.json").read_text(encoding="utf-8"))

    for name, panel in (("fig_vh_error_vs_tolerance.png", _error_panel),
                        ("fig_vh_convergence.png", _convergence_panel),
                        ("fig_vh_tamper.png", _tamper_panel)):
        figure, ax = plt.subplots(figsize=(7.0, 4.2))
        panel(study, ax)
        figure.tight_layout()
        figure.savefig(out / name, dpi=160)
        plt.close(figure)
        print(f"wrote {out / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())