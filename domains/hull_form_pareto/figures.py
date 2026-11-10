"""Figures for the hull-form Pareto study (matplotlib, Agg backend).

Every figure is generated from artefact JSON (no hand-copied numbers) and the
PNG is stored under ``docs/figures/hull_form_pareto`` with a caption recorded in
``figures.json`` (assertion / provenance chain for the report).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .hull import HOLD_ER_FRACTION, HOLD_FWD_MARGIN, build_hull, load_design_space  # noqa: E402
from .pareto import FIGURE_DIR, REPO_ROOT  # noqa: E402

plt.rcParams.update({"figure.dpi": 130, "font.size": 9, "axes.grid": True,
                     "grid.alpha": 0.3})


def _front_arrays(result: Dict[str, object]):
    front = result["front"]
    R = np.array([row["resistance_kn"] for row in front])
    GM = np.array([row["gm_m"] for row in front])
    VC = np.array([row["cargo_volume_m3"] for row in front])
    L = np.array([row["hull"]["length"] for row in front])
    B = np.array([row["hull"]["beam"] for row in front])
    disp = np.array([row["hull"]["displacement_t"] for row in front])
    return front, R, GM, VC, L, B, disp


def fig_pareto_3d(result: Dict[str, object], path: Path) -> str:
    front, R, GM, VC, L, B, disp = _front_arrays(result)
    fig = plt.figure(figsize=(11.5, 7.4))
    ax = fig.add_subplot(2, 2, 1, projection="3d")
    sc = ax.scatter(R, GM, VC / 1000.0, c=disp, cmap="viridis", s=18, depthshade=False)
    ax.set_xlabel("$R_T$ [kN]")
    ax.set_ylabel("$GM$ [m]")
    ax.set_zlabel("$V_{cargo}$ [$10^3$m$^3$]")
    ax.set_title("NSGA-II Pareto front (colour = displacement [t])")
    fig.colorbar(sc, ax=ax, shrink=0.6, label="displ. [t]")

    for k, (x, y, xl, yl, ttl) in enumerate((
            (R, GM, "$R_T$ [kN]", "$GM$ [m]", "resistance vs stability"),
            (R, VC / 1000.0, "$R_T$ [kN]", "$V_{cargo}$ [$10^3$m$^3$]",
             "resistance vs cargo capacity"),
            (GM, VC / 1000.0, "$GM$ [m]", "$V_{cargo}$ [$10^3$m$^3$]",
             "stability vs cargo capacity"))):
        axp = fig.add_subplot(2, 2, k + 2)
        s = axp.scatter(x, y, c=disp, cmap="viridis", s=16)
        axp.set_xlabel(xl)
        axp.set_ylabel(yl)
        axp.set_title(ttl, fontsize=9)
    fig.colorbar(s, ax=fig.axes[1:], shrink=0.6, label="displ. [t]")
    fig.subplots_adjust(hspace=0.35, wspace=0.35)
    fig.savefig(path)
    plt.close(fig)
    return "hull_form_pareto_pareto_front"


def fig_hypervolume(result: Dict[str, object], path: Path) -> str:
    hv_archive = result.get("archive_hypervolume_history") or []
    hv_pop = result.get("hypervolume_history") or []
    gens = result.get("history_generations") or list(range(len(hv_pop)))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 3.9))
    if hv_archive:
        g_arch = np.linspace(0, len(hv_pop) - 1 if hv_pop else len(hv_archive) - 1,
                             len(hv_archive))
        ax1.plot(g_arch, np.array(hv_archive) / max(hv_archive[0], 1e-12),
                 "o-", ms=3, lw=1.2, label="archive HV (monotone)")
    if hv_pop:
        ax1.plot(gens, np.array(hv_pop) / max(max(hv_pop), 1e-12), "-", lw=1.0,
                 alpha=0.7, label="population-front HV")
    ax1.set_xlabel("generation")
    ax1.set_ylabel("HV / HV$_{ref}$")
    ax1.set_title("Hypervolume convergence")
    ax1.legend(fontsize=8)
    conv = result["summary"]["hypervolume_convergence"]
    ax2.axis("off")
    txt = (
        f"generations: {result['summary']['n_generation']}\n"
        f"population: {result['summary']['n_population']}\n"
        f"archive size: {result['summary']['archive_size']}\n"
        f"HV final: {conv['hv_final']:.4e}\n"
        f"HV(gen {result['summary']['n_generation'] - conv['window']}): "
        f"{conv['hv_window_start']:.4e}\n"
        f"delta over last {conv['window']} gens: {conv['delta'] * 100:.2f} %\n"
        f"spacing (normalised): {result['summary']['spacing_normalised']:.4f}\n"
        f"Spearman(R,GM): {result['summary']['spearman_R_GM']:+.3f}\n"
        f"Spearman(R/Δ$^{{2/3}}$,GM): {result['summary']['spearman_specific_R_GM']:+.3f}"
    )
    ax2.text(0.02, 0.95, txt, va="top", family="monospace", fontsize=9)
    ax2.set_title("acceptance numbers (from artifacts)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return "hull_form_pareto_hypervolume"


def fig_resistance_breakdown(result: Dict[str, object], path: Path) -> str:
    front = result["front"]
    by_r = sorted(front, key=lambda r: r["resistance_kn"])
    picks = [("min $R_T$", by_r[0]), ("median $R_T$", by_r[len(by_r) // 2]),
             ("max $R_T$", by_r[-1])]
    by_v = sorted(front, key=lambda r: -r["cargo_volume_m3"])
    picks.append(("max $V_{cargo}$", by_v[0]))
    by_g = sorted(front, key=lambda r: -r["gm_m"])
    picks.append(("max GM", by_g[0]))

    parts = ["rf_n", "rw_n", "rbulb_n", "rtr_n", "ra_n"]
    labels = ["friction $R_F(1+k)$", "wave $R_W$", "bulb $R_B$",
              "transom $R_{TR}$", "correlation $R_A$"]
    colors = plt.cm.tab10(np.linspace(0, 1, len(parts)))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.0))
    x = np.arange(len(picks))
    bottom = np.zeros(len(picks))
    for p, lab, c in zip(parts, labels, colors):
        vals = np.array([pk[1]["resistance"][p] / 1000.0 for pk in picks])
        ax1.bar(x, vals, bottom=bottom, label=lab, color=c)
        bottom += vals
    ax1.set_xticks(x, [p[0] for p in picks], fontsize=8)
    ax1.set_ylabel("resistance component [kN]")
    ax1.set_title("Holtrop–Mennen breakdown of representative Pareto points")
    ax1.legend(fontsize=7)

    hulllabels = ["$L$ [m]", "$B$ [m]", "$T$ [m]", "$C_b$", "$GM$ [m]", "$V_{cargo}/10^3$ m$^3$"]
    keys = [("hull", "length"), ("hull", "beam"), ("hull", "draft"), ("hull", "cb")]
    table = []
    for _, row in picks:
        vals = [row[k[0]][k[1]] for k in keys]
        vals += [row["gm_m"], row["cargo_volume_m3"] / 1000.0]
        table.append(vals)
    table = np.array(table).T
    for j, (label, _row) in enumerate(picks):
        ax2.plot(range(len(hulllabels)), table[:, j], "o-", ms=3, label=label)
    ax2.set_xticks(range(len(hulllabels)), hulllabels, fontsize=8)
    ax2.set_ylabel("value")
    ax2.set_title("principal particulars of the same points")
    ax2.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return "hull_form_pareto_resistance_breakdown"


def fig_section_profiles(result: Dict[str, object], path: Path) -> str:
    space = load_design_space()
    front = result["front"]
    by_r = sorted(front, key=lambda r: r["resistance_kn"])
    picks = [("min $R_T$", by_r[0]), ("max $V_{cargo}$",
             sorted(front, key=lambda r: -r["cargo_volume_m3"])[0])]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 3.8))
    for name, row in picks:
        hull = build_hull(np.array(row["x"]), space)
        ax1.plot(hull.stations, hull.section_fullness, lw=1.6,
                 label=f"{name}: $C_p$={hull.cp:.3f}")
    ax1.axvspan(HOLD_ER_FRACTION, min(HOLD_ER_FRACTION + picks[0][1]["hull"]["hold_length_fraction"],
                                      1 - HOLD_FWD_MARGIN),
                alpha=0.10, color="grey", label="hold window (min-$R_T$ design)")
    ax1.set_xlabel("x / L (0 = AP)")
    ax1.set_ylabel("sectional fullness s(x)")
    ax1.set_title("parametric sectional-area distribution (∫s dx = $C_p$)")
    ax1.legend(fontsize=8)

    for name, row in picks:
        hull = build_hull(np.array(row["x"]), space)
        area = hull.section_fullness * hull.cm * hull.beam * (hull.depth - hull.double_bottom)
        ax2.plot(hull.stations, area, lw=1.6, label=name)
    ax2.set_xlabel("x / L")
    ax2.set_ylabel("hold section area [m$^2$]")
    ax2.set_title("cargo-hold section area (volume = integral of this curve)")
    ax2.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return "hull_form_pareto_section_profiles"


def fig_zdt1(zdt1: Dict[str, object], path: Path) -> str:
    from .pareto import zdt1_reference_front
    ref = zdt1_reference_front()
    front = np.array(zdt1["front"])
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 3.9))
    ax1.plot(ref[:, 0], ref[:, 1], "k-", lw=1.4, label="analytic ZDT1 front")
    ax1.plot(front[:, 0], front[:, 1], "o", ms=3.5, mfc="none", color="tab:red",
             label=f"NSGA-II ({len(front)} points)")
    ax1.set_xlabel("$f_1$")
    ax1.set_ylabel("$f_2$")
    ax1.set_title("ZDT1 (n=30): approximate vs analytic front")
    ax1.legend(fontsize=8)
    hv = np.array(zdt1["hypervolume_history"], dtype=float)
    ax2.plot(np.arange(len(hv)), hv, lw=1.3)
    ax2.set_xlabel("generation")
    ax2.set_ylabel("hypervolume")
    ax2.set_title(f"Hausdorff(relative) = {zdt1['hausdorff_relative'] * 100:.2f} %"
                  f"  (crit. < 5 %)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return "hull_form_pareto_zdt1"


def fig_negative_control(negative: Dict[str, object], result: Dict[str, object],
                         path: Path) -> str:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 3.9))
    ev = result["all_evaluated_objectives"]
    arr = np.array(ev)
    ax1.scatter(arr[:, 0], -arr[:, 1], s=4, alpha=0.15, label="all 3-objective evaluations")
    ax1.set_xlabel("$R_T$ [kN]")
    ax1.set_ylabel("$GM$ [m]")
    ga = negative["ga_best_design"]
    rnd = negative["random_best_design"]
    ax1.scatter([ga["resistance_kn"]], [ga["gm_m"]], marker="*", s=180,
                color="tab:red", label=f"single-obj GA best {ga['resistance_kn']:.1f} kN")
    if rnd:
        ax1.scatter([rnd["resistance_kn"]], [rnd["gm_m"]], marker="P", s=110,
                    color="tab:green",
                    label=f"random-search best {rnd['resistance_kn']:.1f} kN")
    ax1.legend(fontsize=7)
    ax1.set_title("negative control: 1 objective ⇒ global optimum")
    ax2.axis("off")
    ax2.text(0.02, 0.95,
             f"single-objective GA best R: {negative['ga_best_R_kn']:.2f} kN\n"
             f"random search best R ({negative['random_search_samples']} LHS): "
             f"{negative['random_search_best_R_kn']:.2f} kN\n"
             f"relative gap: {negative['relative_gap'] * 100:.3f} %  (crit. < 1 %)\n"
             f"GA best feasible: {negative['ga_best_feasible']}",
             va="top", family="monospace", fontsize=9)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return "hull_form_pareto_negative_control"


def make_all_figures(result: Dict[str, object], zdt1: Dict[str, object],
                     negative: Dict[str, object],
                     out_dir: Optional[Path] = None) -> Dict[str, Path]:
    out = Path(out_dir) if out_dir else FIGURE_DIR
    out.mkdir(parents=True, exist_ok=True)
    produced = {}
    for name, fn, args in (
            ("pareto_front", fig_pareto_3d, (result,)),
            ("hypervolume", fig_hypervolume, (result,)),
            ("resistance_breakdown", fig_resistance_breakdown, (result,)),
            ("section_profiles", fig_section_profiles, (result,)),
            ("zdt1", fig_zdt1, (zdt1,)),
            ("negative_control", fig_negative_control, (negative, result))):
        p = out / f"{name}.png"
        fn(*args, p)
        produced[name] = p
    (out / "figures.json").write_text(json.dumps(
        {k: str(v.relative_to(REPO_ROOT)) for k, v in produced.items()}, indent=2))
    return produced