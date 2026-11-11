"""Figures for the multi-fidelity maturity record (three required plots).

Every number plotted comes from the study record; nothing is recomputed here
except the geometry of the ticks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from src.multifidelity.clock_manager import GlobalClockManager, Strategy  # noqa: E402

from .study import CHANNELS, CLOCKS, CONSUMER, HORIZON, OFFSETS, PRODUCER  # noqa: E402

BLUE, ORANGE, GREEN, RED, GREY = "#1f4e79", "#e07b39", "#2e7d32", "#b3261e", "#6b6b6b"


def _manager(strategy: Strategy) -> GlobalClockManager:
    manager = GlobalClockManager(strategy)
    for name, dt in CLOCKS.items():
        manager.register(name, dt)
    return manager


def fig_clock_sync(clock: Dict, exchange: Dict, path: Path) -> Path:
    fig, axes = plt.subplots(2, 1, figsize=(10.5, 7.2),
                             gridspec_kw={"height_ratios": [1.15, 1.0]})
    gcd = _manager(Strategy.GCD).build(HORIZON)
    lcm = _manager(Strategy.LCM).build(HORIZON)

    ax = axes[0]
    window = 0.05
    names = list(CLOCKS)
    for row, name in enumerate(names):
        dt = CLOCKS[name]
        n = int(round(window / dt)) + 1
        t = np.arange(n) * dt
        ax.vlines(t, row - 0.30, row + 0.30, color=BLUE, lw=1.2)
    grid_nodes = gcd.grid()
    near = grid_nodes[grid_nodes <= window]
    ax.vlines(near, -0.85, -0.55, color=GREEN, lw=1.0)
    macro = lcm.grid()
    ax.vlines(macro[macro <= window], 2.55, 2.85, color=ORANGE, lw=1.6)
    ax.set_yticks(list(range(len(names))) + [-0.7, 2.7])
    ax.set_yticklabels(
        [f"{n}  dt=1/{int(round(1/CLOCKS[n]))}" for n in names]
        + [f"global grid (gcd)\ndt_global={float(gcd.dt_global):.3e}",
           f"macro nodes (lcm)\ndt_global={float(lcm.dt_global):.3e}"])
    ax.set_xlim(0, window)
    ax.set_xlabel("time (dimensionless)")
    ax.set_title(f"(a) three participant lattices, the gcd grid and the lcm macro nodes "
                 f"(horizon {HORIZON})", loc="left", fontsize=10)
    ax.grid(axis="x", alpha=0.25)

    ax = axes[1]
    frame_edges = exchange["synced"]["frame_edges"]
    required = np.asarray(exchange["synced"]["frame_required"])
    booked_sync = np.asarray(exchange["synced"]["frame_booked"])
    free = exchange["free_running"]["frame_reference"]
    booked_free = np.asarray(free["booked"])
    centres = 0.5 * (np.asarray(frame_edges[:-1]) + np.asarray(frame_edges[1:]))
    ax.plot(centres, required, "o-", color=GREY, ms=3, lw=1.0, label="required (exact frame integral)")
    ax.plot(centres, booked_sync, "s-", color=GREEN, ms=3, lw=1.0,
            label="booked, barrier + nested sub-sampling")
    ax.plot(centres, booked_free, "^-", color=RED, ms=3, lw=1.0,
            label=f"booked, no shared clock (hold, offset {free['offset']:.2f} frame)")
    ax.set_xlabel("time (dimensionless)")
    ax.set_ylabel(f"{CHANNELS[0].name}")
    ax.set_title(f"(b) one channel's per-frame amounts: consumer {CONSUMER}, producer {PRODUCER}",
                 loc="left", fontsize=10)
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_conservation(exchange: Dict, clock: Dict, path: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4))
    syn = exchange["synced"]["summary"]
    hold = [exchange["free_running"]["by_mode"]["hold"][f"{o:.3f}"]["summary"]["max_frame_l1_defect"]
            for o in OFFSETS]
    extrap = [exchange["free_running"]["by_mode"]["extrapolate"][f"{o:.3f}"]["summary"]["max_frame_l1_defect"]
              for o in OFFSETS]
    net = [exchange["free_running"]["by_mode"]["hold"][f"{o:.3f}"]["summary"]["max_net_defect"]
           for o in OFFSETS]

    ax = axes[0]
    ax.semilogy(OFFSETS, hold, "o-", color=RED, label="frame L1 defect, hold")
    ax.semilogy(OFFSETS, extrap, "s--", color=ORANGE, label="frame L1 defect, extrapolate")
    ax.semilogy(OFFSETS, net, "^:", color=BLUE, label="net defect, hold")
    ax.axhline(syn["max_frame_l1_defect"], color=GREEN, lw=1.4,
               label=f"barrier protocol, worst channel ({syn['max_frame_l1_defect']:.2e})")
    ax.axhline(0.01, color=GREY, lw=0.9, ls="--")
    ax.axhline(0.05, color=GREY, lw=0.9, ls="-.")
    ax.text(0.02, 0.0115, "acceptance 1%", fontsize=7, color=GREY)
    ax.text(0.02, 0.057, "negative-control 5%", fontsize=7, color=GREY)
    ax.set_xlabel("consumer frame phase relative to producer publications (frame fraction)")
    ax.set_ylabel("relative defect")
    ax.set_title("(a) conservation defect vs consumer phase", loc="left", fontsize=10)
    ax.legend(fontsize=7.5)
    ax.grid(alpha=0.25, which="both")

    ax = axes[1]
    labels = list(exchange["synced"]["channels"])
    sync_vals = [exchange["synced"]["channels"][c]["frame_l1_defect"] for c in labels]
    hold_vals = [exchange["free_running"]["by_mode"]["hold"][f"{0.5:.3f}"]["channels"][c]["frame_l1_defect"]
                 for c in labels]
    x = np.arange(len(labels))
    ax.bar(x - 0.2, sync_vals, width=0.38, color=GREEN, label="barrier protocol")
    ax.bar(x + 0.2, hold_vals, width=0.38, color=RED, label="no shared clock (hold)")
    ax.set_yscale("log")
    ax.axhline(0.01, color=GREY, ls="--", lw=0.9)
    ax.set_xticks(x)
    ax.set_xticklabels([c.replace("_", "\n") for c in labels], fontsize=8)
    ax.set_ylabel("frame L1 defect")
    ax.set_title("(b) per conserved channel", loc="left", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25, which="both")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_distill_pareto(distill: Dict, path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8.6, 5.2))
    sweep = distill["sweep"]
    alphas = sorted({p["alpha"] for p in sweep})
    cmap = plt.get_cmap("viridis")
    for i, alpha in enumerate(alphas):
        pts = [p for p in sweep if p["alpha"] == alpha]
        ax.scatter([p["cost_us"] for p in pts], [p["val_accuracy_pct"] for p in pts],
                   s=34 + 6 * np.log2(max(1, max(p["width"] for p in pts))),
                   color=cmap(i / max(1, len(alphas) - 1)), label=f"alpha={alpha:.2f}",
                   edgecolor="white", linewidth=0.5, zorder=3)
    front = sorted(distill["pareto_points"], key=lambda p: p["cost_us"])
    ax.plot([p["cost_us"] for p in front], [p["val_accuracy_pct"] for p in front],
            "-", color=GREY, lw=1.2, zorder=2, label="Pareto front")

    teacher = distill["teacher"]
    ax.scatter([teacher["cost_us"]], [teacher["val_accuracy"]], marker="*", s=260,
               color=RED, zorder=4, label="teacher (grid-refined ensemble)")
    base = distill["ladder"]["closed_form_baseline"]
    ax.scatter([base["cost_us"]], [base["val_accuracy_pct"]], marker="v", s=90,
               color=ORANGE, zorder=4, label="closed-form low-fidelity fit")
    nod = distill["ladder"]["distillation_disabled"]
    ax.scatter([nod["cost_us"]], [nod["val_accuracy_pct"]], marker="X", s=110,
               color=BLUE, zorder=4, label="distillation disabled (alpha=1)")
    kd = distill["ladder"]["distillation_enabled"]
    ax.annotate("", xy=(kd["cost_us"], kd["val_accuracy_pct"]), xytext=(nod["cost_us"], nod["val_accuracy_pct"]),
                arrowprops=dict(arrowstyle="->", color=BLUE, lw=1.2))
    ax.annotate(f"+{kd['val_accuracy_pct'] - nod['val_accuracy_pct']:.1f} points\n"
                f"(error {nod['val_rel_rmse']/kd['val_rel_rmse']:.1f}x lower)",
                xy=(kd["cost_us"] * 1.06, 0.5 * (kd["val_accuracy_pct"] + nod["val_accuracy_pct"])),
                fontsize=8, color=BLUE)
    ax.set_xscale("log")
    ax.set_xlabel("inference cost per query (microseconds, log scale)")
    ax.set_ylabel("validation accuracy 100*(1-rel. RMSE) [%]")
    ax.set_title("accuracy / efficiency trade-off of the distilled student", loc="left", fontsize=10)
    ax.legend(fontsize=8, ncol=2, loc="lower right")
    ax.grid(alpha=0.25, which="both")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def all_figures(record: Dict, directory: Path) -> List[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    return [
        fig_clock_sync(record["clock"], record["exchange"],
                       directory / "fig1_clock_sync_timeline.png"),
        fig_conservation(record["exchange"], record["clock"],
                         directory / "fig2_conservation_error.png"),
        fig_distill_pareto(record["distill"], directory / "fig3_distillation_pareto.png"),
    ]