"""Figures and markdown summary for the scalability ledger.

Figures are written as PNG and their sha256 is recorded, so the report can quote a
verifiable artifact instead of a hand-waved picture.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from tools.scaling.fitting import amdahl_speedup  # noqa: E402

__all__ = ["make_figures", "summary_markdown", "sha256_of"]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _series(data: Dict[str, Any], key: str):
    ns = sorted(int(n) for n in data[key])
    return np.asarray(ns, dtype=float), np.asarray([data[key][str(n)] for n in ns], dtype=float)


def make_figures(ledger: Dict[str, Any], figdir: Path) -> List[Dict[str, str]]:
    figdir = Path(figdir)
    figdir.mkdir(parents=True, exist_ok=True)
    k = ledger["kernels"]
    out: List[Dict[str, str]] = []

    def register(path: Path, caption: str) -> None:
        out.append({"path": str(path), "caption": caption, "sha256": sha256_of(path)})

    # ---- F1: strong scaling speedup with Amdahl fits ----------------------
    fig, ax = plt.subplots(figsize=(7.2, 5.0), dpi=140)
    strong = {lab: d for lab, d in k.items() if d["mode"] == "strong"}
    colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#8c564b", "#17becf"]
    n_max = max((max(d["core_counts"]) for d in strong.values()), default=32)
    ideal_n = np.logspace(0, np.log2(n_max) / np.log2(2), 200)
    ax.plot(ideal_n, ideal_n, "k--", lw=1.0, label="ideal linear")
    for i, (lab, d) in enumerate(strong.items()):
        ns, sp = _series(d, "speedup")
        ax.plot(ns, sp, "o-", color=colors[i % len(colors)], lw=1.6, ms=6, label=lab)
        fit = d.get("amdahl") or {}
        if fit.get("s") is not None:
            ax.plot(
                ideal_n,
                amdahl_speedup(ideal_n, fit["s"]),
                ":",
                color=colors[i % len(colors)],
                lw=1.2,
                alpha=0.9,
            )
    ax.set_xscale("log", base=2)
    ax.set_xlabel("cores n")
    ax.set_ylabel("speedup S(n) = T(1)/T(n)")
    ax.set_title("Strong scaling: measured (markers) vs Amdahl fit (dotted)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=7, loc="upper left")
    p = figdir / "f1_strong_scaling_speedup.png"
    fig.tight_layout()
    fig.savefig(p)
    plt.close(fig)
    register(p, "Strong-scaling speedup for every kernel family with the fitted Amdahl curve.")

    # ---- F2: efficiency curves -------------------------------------------
    fig, ax = plt.subplots(figsize=(7.2, 5.0), dpi=140)
    for i, (lab, d) in enumerate(strong.items()):
        ns, eff = _series(d, "efficiency")
        ax.plot(ns, eff, "o-", color=colors[i % len(colors)], lw=1.6, ms=6, label=lab)
    ax.axhline(0.70, color="grey", ls="--", lw=1.0)
    ax.axhline(0.50, color="grey", ls=":", lw=1.0)
    ax.text(n_max * 0.9, 0.71, "C2 gate: E(8)>=0.70", ha="right", fontsize=7, color="grey")
    ax.text(n_max * 0.9, 0.51, "C2 gate: E(16)>=0.50", ha="right", fontsize=7, color="grey")
    ax.set_xscale("log", base=2)
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("cores n")
    ax.set_ylabel("parallel efficiency E(n) = S(n)/n")
    ax.set_title("Strong-scaling efficiency and the acceptance gates")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=7, loc="lower left")
    p = figdir / "f2_strong_efficiency.png"
    fig.tight_layout()
    fig.savefig(p)
    plt.close(fig)
    register(p, "Strong-scaling efficiency; grey lines are the C2 acceptance gates.")

    # ---- F3: weak scaling (stencil + halo) -------------------------------
    weak = {lab: d for lab, d in k.items() if d["mode"] == "weak"}
    if weak:
        fig, ax = plt.subplots(figsize=(7.2, 5.0), dpi=140)
        for i, (lab, d) in enumerate(weak.items()):
            ns, eff = _series(d, "efficiency")
            ax.plot(ns, eff, "s-", color=colors[i % len(colors)], lw=1.6, ms=6, label=lab)
            g = d.get("gustafson") or {}
            if g.get("s_gustafson") is not None:
                nfit = np.logspace(0, np.log2(max(ns)) / np.log2(2), 200)
                ax.plot(
                    nfit,
                    1.0 / (g["s_gustafson"] + (1.0 - g["s_gustafson"]) / nfit),
                    ":",
                    color=colors[i % len(colors)],
                    lw=1.2,
                )
        ax.axhline(0.85, color="grey", ls="--", lw=1.0)
        w_max = max(max(d["core_counts"]) for d in weak.values())
        ax.text(w_max * 0.9, 0.86, "C3 gate: E(8)>=0.85",
                ha="right", fontsize=7, color="grey")
        ax.set_xscale("log", base=2)
        ax.set_ylim(0, 1.1)
        ax.set_xlabel("cores n (work per core held constant)")
        ax.set_ylabel("weak efficiency E_w(n) = T(1)/T(n)")
        ax.set_title("Weak scaling: stencil (bandwidth) vs halo ring (latency)")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(fontsize=7, loc="lower left")
        p = figdir / "f3_weak_scaling.png"
        fig.tight_layout()
        fig.savefig(p)
        plt.close(fig)
        register(p, "Weak-scaling efficiency with work per core held constant.")

    # ---- F4: injected-s recovery + pseudo-parallel negative control ------
    fams = [d for d in k.values() if d["kernel"] in ("serial_frac", "pseudo_par")]
    if fams:
        fig, ax = plt.subplots(figsize=(7.2, 5.0), dpi=140)
        for i, d in enumerate(fams):
            ns, sp = _series(d, "speedup")
            ax.plot(ns, sp, "o-", color=colors[i % len(colors)], lw=1.6, ms=6,
                    label=f"{d['label']} (measured)")
            fit = d.get("amdahl") or {}
            if fit.get("s") is not None:
                nfit = np.logspace(0, np.log2(max(ns)) / np.log2(2), 200)
                ax.plot(nfit, amdahl_speedup(nfit, fit["s"]), ":", color=colors[i % len(colors)],
                        lw=1.2,
                        label=f"fit s={fit['s']:.3f} (R2={fit.get('r2', float('nan')):.4f})")
        ax.plot(ideal_n, ideal_n, "k--", lw=1.0, label="ideal linear")
        ax.set_xscale("log", base=2)
        ax.set_xlabel("cores n")
        ax.set_ylabel("speedup")
        ax.set_title("Amdahl recovery of injected serial fractions + pseudo-parallel control")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(fontsize=7, loc="upper left")
        p = figdir / "f4_amdahl_recovery_and_pseudo_control.png"
        fig.tight_layout()
        fig.savefig(p)
        plt.close(fig)
        register(p, "Injected serial-fraction families and the pseudo-parallel negative control.")

    return out


def summary_markdown(ledger: Dict[str, Any], acceptance: Dict[str, Any]) -> str:
    env = ledger["environment"]
    lines: List[str] = []
    lines.append("# Parallel scalability ledger (strong / weak scaling, Amdahl, pseudo-parallel control)\n")
    lines.append(
        f"Host `{env.get('hostname')}` — {env.get('cpu_model', env.get('machine'))} — "
        f"os.cpu_count={env.get('cpu_count_os')} — {env.get('platform')}"
    )
    lines.append(
        f"Protocol: {ledger['protocol']['reps']} timed repetitions + 1 warm-up, randomised core order, "
        f"median statistic, integer checksum reduced mod 2^64.\n"
    )
    lines.append("| kernel | mode | E(1) | E(2) | E(4) | E(8) | E(16) | E(32) | s_Amdahl | R² |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for lab, d in ledger["kernels"].items():
        eff = d["efficiency"]
        fit = d.get("amdahl") or {}
        row = [lab, d["mode"]]
        for n in ("1", "2", "4", "8", "16", "32"):
            v = eff.get(n)
            row.append("-" if v is None else f"{v:.3f}")
        row.append("-" if fit.get("s") is None else f"{fit['s']:.4f}")
        row.append("-" if fit.get("r2") is None else f"{fit['r2']:.4f}")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("## Acceptance verdicts\n")
    lines.append("| id | criterion | verdict | key numbers |")
    lines.append("|---|---|---|---|")
    for cid, c in acceptance["criteria"].items():
        keys = []
        for key in ("E8", "E16", "s_hat", "ci_low"):
            if c.get(key) is not None:
                keys.append(f"{key}={c[key]:.3f}" if isinstance(c[key], float) else f"{key}={c[key]}")
        if "detail" in c and cid == "C4":
            for lab, dd in c["detail"].items():
                keys.append(
                    f"{lab}: s_hat={dd['s_hat']:.4f} truth={dd['s_injected_measured']:.4f} "
                    f"err={dd['abs_error_pp']:.2f}pp R2={dd['r2']:.4f}"
                )
        lines.append(f"| {cid} | {c['criterion']} | {'PASS' if c['pass'] else 'FAIL'} | {'; '.join(keys)} |")
    lines.append("")
    lines.append(f"**Overall: {acceptance['n_pass']}/{acceptance['n_criteria']} criteria pass.**\n")
    lines.append("## Core quota recommendation\n")
    lines.append("| kernel | recommended max cores | marginal throughput gain per step |")
    lines.append("|---|---|---|")
    for lab, q in acceptance["criteria"]["C6"]["quota"].items():
        gains = ", ".join(f"{k}->{v:.2f}" for k, v in q["marginal_throughput_gain"].items())
        lines.append(f"| {lab} | {q['recommended_max_cores']} | {gains} |")
    lines.append("")
    return "\n".join(lines)