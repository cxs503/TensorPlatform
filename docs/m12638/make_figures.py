"""Figures for the multi-fidelity campaign (all numbers read back from the evidence JSON).

Outputs ``docs/m12638/figures/*.png``.  Every plotted quantity is either read from
``evidence/multifidelity_evidence.json`` (written by ``run_multifidelity.py``) or
recomputed here from the same modules — nothing is typed by hand.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import fidelity_levels as fl  # noqa: E402
import michell_generic as mg  # noqa: E402

EV = HERE / "evidence" / "multifidelity_evidence.json"
FIG = HERE / "figures"
FIG.mkdir(exist_ok=True)


def fig1_pareto(ev):
    items = ev["case_b_pareto"]["items"]
    costs = [it["cost"] for it in items]
    errs = [max(it["error"], 1e-9) for it in items]
    labels = ["L0 empirical\n(friction only)", "L1 potential\n(screening)",
              "L2 potential\n(verified)"]
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    ax.plot(costs, errs, "o-", color="C0", lw=1.6, ms=8)
    for c, e, lab in zip(costs, errs, labels):
        ax.annotate(lab, (c, e), textcoords="offset points", xytext=(8, 6), fontsize=8)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("cost per evaluation [µs, measured median]")
    ax.set_ylabel("error vs verified level (nRMSE)")
    ax.grid(True, which="both", alpha=0.3)
    ax.set_title("Cost–accuracy Pareto front of the fidelity ladder "
                 f"({ev['case_b_pareto']['n_points']} levels, all non-dominated)")
    fig.tight_layout()
    fig.savefig(FIG / "fig1_level_pareto.png", dpi=160)
    plt.close(fig)


def fig2_forrester(ev):
    c = ev["case_a_curve"]
    x = np.asarray(c["x"])
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.plot(x, c["truth"], "k-", lw=2.0, label="high-fidelity truth")
    ax.plot(x, c["low_fidelity_model"], "--", color="C7", lw=1.0, label="low-fidelity model")
    ax.plot(x, c["single_fidelity"], ":", color="C3", lw=1.6,
            label=f"single fidelity, 10 HF only (nRMSE "
                  f"{ev['case_a_forrester']['single_fidelity_high_only']['nrmse']*100:.1f}%)")
    ax.plot(x, c["co_kriging"], "-", color="C0", lw=1.6,
            label=f"co-kriging, 10 HF + 50 LF (nRMSE "
                  f"{ev['case_a_forrester']['co_kriging']['nrmse']*100:.2f}%)")
    ax.set_xlabel("x")
    ax.set_ylabel("f(x)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    ax.set_title("Analytic benchmark: autoregressive co-kriging vs single-fidelity kriging")
    fig.tight_layout()
    fig.savefig(FIG / "fig2_forrester.png", dpi=160)
    plt.close(fig)


def fig3_wigley(ev):
    c = ev["case_d_wigley"]["curve"]
    fn = np.asarray(c["fn"])
    ref = np.asarray(c["reference"])
    fig, ax = plt.subplots(1, 2, figsize=(10.0, 4.0))
    ax[0].plot(fn, ref * 1e3, "k-", lw=2, label="verified reference (L2)")
    ax[0].plot(fn, np.asarray(c["fused"]) * 1e3, "o", ms=4, color="C0",
               label="multi-fidelity fusion")
    ax[0].plot(fn, np.asarray(c["l1"]) * 1e3, "--", lw=1, color="C2", label="L1 screening")
    ax[0].plot(fn, np.asarray(c["l0"]) * 1e3, ":", lw=1.2, color="C3", label="L0 empirical")
    ax[0].set_xlabel("Froude number  Fn")
    ax[0].set_ylabel("total resistance coefficient $C_T\\times10^3$")
    ax[0].set_title("Wigley hull (L/B=10, B/T=1.6): fidelity ladder vs Fn")
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=0.3)
    err = np.abs(np.asarray(c["fused"]) - ref) / np.abs(ref) * 100
    idx_hf = ev["case_d_wigley"]["hf_indices"]
    ax[1].semilogy(fn, np.maximum(err, 1e-6), "o-", ms=4, color="C0")
    ax[1].semilogy(fn[idx_hf], np.maximum(err[idx_hf], 1e-6), "r*", ms=12,
                   label="high-fidelity samples")
    ax[1].axhline(10.0, color="r", ls="--", lw=1, label="10% criterion")
    ax[1].set_xlabel("Froude number  Fn")
    ax[1].set_ylabel("fusion error [%]")
    ax[1].set_title("Fusion error on the held-out Froude points")
    ax[1].legend(fontsize=8)
    ax[1].grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(FIG / "fig3_wigley_sweep.png", dpi=160)
    plt.close(fig)


def fig4_campaign(ev):
    c = ev["case_c_adaptive"]
    fig, ax = plt.subplots(1, 2, figsize=(10.0, 4.0))
    names = ["single fidelity\n(100 × L2)", "adaptive\nallocation",
             "planned\nallocation"]
    errs = [c["single_fidelity"]["nrmse"], c["multi_fidelity"]["nrmse"],
            c["planned_allocation"]["nrmse"]]
    colors = ["C7", "C0", "C2"]
    bars = ax[0].bar(names, np.asarray(errs) * 100, color=colors)
    for b, e in zip(bars, errs):
        ax[0].text(b.get_x() + b.get_width() / 2, b.get_height() * 1.02,
                   f"{e*100:.1f}%", ha="center", fontsize=9)
    ax[0].axhline(c["single_fidelity"]["nrmse"] * 100 * 0.8, color="r", ls="--",
                  lw=1, label="80% criterion line")
    ax[0].set_ylabel("prediction error on 160 held-out designs [nRMSE %]")
    ax[0].set_title("6-D hull-form campaign at equal budget")
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=0.3, axis="y")

    diag = c.get("single_level_diagnostic", {})
    labels = ["L0 only\n(n=%d)" % diag.get("L0_empirical", {}).get("n", 0),
              "L1 only\n(n=%d)" % diag.get("L1_potential_coarse", {}).get("n", 0),
              "L2 only\n(n=%d)" % diag.get("L2_potential_converged", {}).get("n", 0)]
    vals = [diag.get(k, {}).get("nrmse", np.nan) * 100 for k in
            ("L0_empirical", "L1_potential_coarse", "L2_potential_converged")]
    ax[1].bar(labels, vals, color=["C3", "C2", "C0"])
    ax[1].set_ylabel("nRMSE [%]  (single-level GP, same design)")
    ax[1].set_title("Where the information sits: single-level diagnostics")
    ax[1].grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(FIG / "fig4_campaign.png", dpi=160)
    plt.close(fig)


def fig5_solver_validation(ev):
    """Generic thin-ship solver vs the repository's closed-form reference."""
    sys.path.insert(0, str(ROOT / "docs" / "verification_debt" / "wigley_michell"))
    import michell_wigley as ref  # noqa: E402

    hull = mg.HullFamily(L=100.0, B=10.0, T=6.25, m=1.0, n=2.0, a=0.0)
    wig = ref.Wigley(L=100.0, B=10.0, T=6.25)
    # the theta-truncation rule used by both routes needs 2 k0 T < 60, i.e. U > 1.43 m/s
    # for this hull; slower speeds give a vanishing integral and are excluded
    speeds = np.linspace(1.6, 6.0, 12)
    rel = []
    for u in speeds:
        mine = mg.michell_rw_spectral(hull, float(u), n_theta=96)
        theirs = ref.rw_theta_integral(wig, float(u), n_theta=600)
        rel.append(abs(mine - theirs) / abs(theirs) if theirs else np.nan)
    fig, ax = plt.subplots(1, 2, figsize=(10.0, 4.0))
    ax[0].semilogy(speeds, np.maximum(rel, 1e-18), "o-")
    ax[0].axhline(1e-10, color="r", ls="--", lw=1, label="1e-10 agreement")
    ax[0].set_xlabel("ship speed U [m/s]  (L = 100 m)")
    ax[0].set_ylabel("|Rw(generic) − Rw(closed form)| / Rw")
    ax[0].set_title("Generic thin-ship solver validated against the closed-form Wigley reference")
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=0.3, which="both")

    ns = [4, 6, 8, 12, 16, 24, 32, 48, 64, 96]
    truth = mg.michell_rw_spectral(hull, 3.5, n_theta=512)
    conv = [abs(mg.michell_rw_spectral(hull, 3.5, n_theta=n) - truth) / truth for n in ns]
    ax[1].loglog(ns, np.maximum(conv, 1e-16), "s-")
    ax[1].set_xlabel("theta quadrature nodes")
    ax[1].set_ylabel("relative error vs 512-node value")
    ax[1].set_title("Signature convergence (U = 3.5 m/s)")
    ax[1].grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(FIG / "fig5_solver_validation.png", dpi=160)
    plt.close(fig)


def main() -> int:
    ev = json.loads(EV.read_text(encoding="utf-8"))
    fig1_pareto(ev)
    fig2_forrester(ev)
    fig3_wigley(ev)
    fig4_campaign(ev)
    fig5_solver_validation(ev)
    for p in sorted(FIG.glob("*.png")):
        print(p.name, p.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())