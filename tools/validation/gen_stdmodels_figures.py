#!/usr/bin/env python3
"""Figures for the ``stdmodels.lookup`` audit (m1876) — generated from the audit JSON.

Nothing in the figures is typed by hand: every number is read from
``artifacts/stdmodels_audit/audit.json`` (produced by
``tools/validation/verify_stdmodels_lookup.py``).

Outputs (into the audit directory):
    fig_r1_anchors.png   — R1 literature-anchor relative errors vs the 3 % / 15 % gates
    fig_r3_density.png   — R3 implied mass density per registry entry vs the physical band

Usage: python3 tools/validation/gen_stdmodels_figures.py
"""

from __future__ import annotations

import argparse
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_IN = os.path.join(REPO, "artifacts", "stdmodels_audit", "audit.json")


def _check(report: dict, cid: str) -> dict:
    return next(c for c in report["checks"] if c["id"] == cid)


def plot_anchors(report: dict, path: str) -> str:
    r1 = _check(report, "R1")
    fields = [f for f in r1["fields"] if f["rel_error"] is not None]
    labels = [f"{f['case']}\n{f['field']}" for f in fields]
    errs = [100.0 * f["rel_error"] for f in fields]

    fig, ax = plt.subplots(figsize=(max(7.0, 0.45 * len(labels) + 3), 4.4))
    colors = ["#2e7d32" if f["verdict"] == "PASS" else "#c62828" for f in fields]
    bars = ax.bar(range(len(errs)), errs, color=colors, width=0.7)
    ax.axhline(3.0, color="#1565c0", ls="--", lw=1.2, label="pass gate 3 %")
    ax.axhline(-3.0, color="#1565c0", ls="--", lw=1.2)
    ax.axhline(15.0, color="#6a1b9a", ls=":", lw=1.2, label="reject gate 15 %")
    ax.axhline(-15.0, color="#6a1b9a", ls=":", lw=1.2)
    ax.axhline(0.0, color="k", lw=0.6)
    for bar, err in zip(bars, errs):
        ax.annotate(f"{err:+.2f}%", (bar.get_x() + bar.get_width() / 2, err),
                    textcoords="offset points",
                    xytext=(0, 4 if err >= 0 else -12), ha="center", fontsize=7)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("relative error vs published value [%]")
    ax.set_title(f"R1 literature anchors — worst |err| = "
                 f"{100 * r1['worst_abs_rel_error']:.2f} % "
                 f"({r1['n_pass']}/{r1['n_fields']} fields PASS)", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_density(report: dict, path: str) -> str:
    r3 = _check(report, "R3")
    rows = r3["warnings"] + r3["violations"]
    rows = sorted(rows, key=lambda r: r["implied_density"])
    names = [r["name"] for r in rows]
    implied = [r["implied_density"] for r in rows]
    expect = [r["expected_density"] for r in rows]
    hard = {r["name"] for r in r3["violations"]}

    fig, ax = plt.subplots(figsize=(max(6.5, 0.32 * len(names) + 3), 4.4))
    xs = range(len(names))
    colors = ["#c62828" if n in hard else "#ef6c00" for n in names]
    ax.scatter(xs, implied, c=colors, s=28, zorder=3,
               label="registry (red = >15 %, orange = 5–15 %)")
    ax.scatter(xs, expect, marker="_", c="#1565c0", s=180, zorder=2,
               label="physical density (1.025 / 1.000 t·m⁻³)")
    for x, y, e in zip(xs, implied, expect):
        ax.vlines(x, min(y, e), max(y, e), color="0.7", lw=0.8, zorder=1)
    ax.set_yscale("log")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(names, rotation=70, ha="right", fontsize=6.5)
    ax.set_ylabel("implied mass density  displacement /(Lpp·B·T·Cb)  [t·m⁻³]")
    ax.set_title(f"R3 displacement/density consistency — {r3['n_violations']} hard "
                 f"violations of {r3['n_checked']} checked (worst "
                 f"{100 * r3['max_abs_rel_error']:.0f} %)", fontsize=10)
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(axis="y", alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in", dest="infile", default=DEFAULT_IN)
    ap.add_argument("--outdir", default=os.path.dirname(DEFAULT_IN))
    args = ap.parse_args(argv)

    with open(args.infile, encoding="utf-8") as fh:
        report = json.load(fh)
    os.makedirs(args.outdir, exist_ok=True)
    made = [
        plot_anchors(report, os.path.join(args.outdir, "fig_r1_anchors.png")),
        plot_density(report, os.path.join(args.outdir, "fig_r3_density.png")),
    ]
    print(json.dumps({"figures": made}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())