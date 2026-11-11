"""Figures for the coupled-loop maturity node (P350-W2-N09).

Four diagnostics, each of which is also a machine criterion:

``fig1_residual_history.png``
    relative residual of the same coupled system swept plain and with Aitken
    relaxation, against the analytic ``rho^k`` decay of the plain iteration.
``fig2_refinement_order.png``
    log-log interface-transfer residual against mesh width for the three
    operators plus the mesh-independent-noise negative control.
``fig3_contraction_and_divergence.png``
    measured contraction estimate against the known spectral radius, and the
    iteration at which divergence is declared for ``rho > 1``.
``fig4_sensitivity_consistency.png``
    the two derivative paths (composed / re-resolved) against the analytic value
    for the clean design and for two corrupted designs, with the 2 % band.

Run: ``python -m tools.coupling.maturity_figures`` (writes into
``artifacts/p350w2_n09/``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .conservation_check import (  # noqa: E402
    SegmentMesh,
    cell_averages,
    noisy_operator,
    refinement_order,
    relative_residual,
    transfer,
)
from .sensitivity import (  # noqa: E402
    CoupledSystem,
    linear_two_discipline_system,
    scale_declared_partials,
    sensitivity_consistency_gate,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "p350w2_n09"
LEVELS = (24, 48, 96, 192, 384)


def _profile(x: np.ndarray) -> np.ndarray:
    return np.sin(2.0 * np.pi * x) + 0.5 * x ** 3


def _study(operator=None, transformer=None) -> Dict[str, float]:
    h: List[float] = []
    residuals: List[float] = []
    for n in LEVELS:
        src = SegmentMesh.uniform(0.0, 1.0, n)
        dst = SegmentMesh.uniform(0.0, 1.0, int(round(1.5 * n)) + 1)
        h.append(float(dst.widths.mean()))
        values = cell_averages(_profile, src)
        if transformer is not None:
            mapped = transformer(values, src, dst)
        else:
            mapped = transfer(values, src, dst, operator)
        residuals.append(relative_residual(cell_averages(_profile, dst), mapped))
    record = refinement_order(h, residuals)
    return {"h": h, "residuals": residuals, "observed_order": record["observed_order"]}


def fig_residual_history(path: Path) -> dict:
    fixture = linear_two_discipline_system(0.85)
    system = fixture.to_system()
    _, plain = system.solve([1.0], relaxation="none")
    _, aitken = system.solve([1.0], relaxation="aitken")
    k = np.arange(1, len(plain.residual_series) + 1)
    theory = plain.residual_series[0] * fixture.spectral_radius ** (k - 1)

    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.semilogy(k, plain.residual_series, "o-", ms=3, lw=1.4, label="plain sweep")
    ax.semilogy(
        np.arange(1, len(aitken.residual_series) + 1),
        aitken.residual_series,
        "s-",
        ms=3,
        lw=1.4,
        label="Aitken relaxation",
    )
    ax.semilogy(
        k, theory, "k--", lw=1.0, label=rf"analytic $\rho^k$, $\rho={fixture.spectral_radius:.2f}$"
    )
    ax.axhline(1e-12, color="grey", ls=":", lw=1.0)
    ax.set_xlabel("iteration")
    ax.set_ylabel("relative residual")
    ax.set_title("Coupled fixed point: measured decay vs analytic contraction")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {
        "iterations_plain": int(plain.iterations),
        "iterations_aitken": int(aitken.iterations),
        "rho_hat_plain": float(plain.rho_hat),
        "spectral_radius": fixture.spectral_radius,
    }


def fig_refinement_order(path: Path) -> dict:
    curves: Dict[str, float] = {}
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    for operator in ("donor_cell", "linear_reconstruction", "quadratic_projection"):
        study = _study(operator=operator)
        curves[operator] = study["observed_order"]
        ax.loglog(
            study["h"], study["residuals"], "o-", ms=4, lw=1.5,
            label=f"{operator} (order {study['observed_order']:.2f})",
        )
    control = _study(transformer=noisy_operator(amplitude=1e-2, seed=11))
    curves["noisy_control"] = control["observed_order"]
    ax.loglog(
        control["h"], control["residuals"], "x--", color="crimson", ms=6, lw=1.2,
        label=f"mesh-independent noise (order {control['observed_order']:.2f})",
    )
    ax.set_xlabel("destination cell width h")
    ax.set_ylabel("relative transfer residual")
    ax.set_title("Interface operator refinement orders")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"observed_orders": curves}


def fig_contraction_and_divergence(path: Path) -> dict:
    rhos = np.array([0.2, 0.35, 0.5, 0.7, 0.85, 0.95])
    measured: List[float] = []
    for rho in rhos:
        _, cert = linear_two_discipline_system(float(rho)).to_system().solve([1.0])
        measured.append(float(cert.rho_hat))

    divergent = np.array([1.05, 1.2, 1.5, 2.0])
    flagged: List[int] = []
    for rho in divergent:
        _, cert = linear_two_discipline_system(float(rho)).to_system().solve([1.0], max_iterations=500)
        flagged.append(int(cert.iterations) if cert.status == "DIVERGED" else -1)

    fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.2))
    ax = axes[0]
    ax.plot(rhos, rhos, "k--", lw=1.0, label="exact identity")
    ax.plot(rhos, measured, "o-", ms=5, lw=1.4, label=r"measured $\hat\rho$")
    ax.set_xlabel("analytic spectral radius")
    ax.set_ylabel("measured contraction estimate")
    ax.set_title("Contraction estimate vs spectral radius")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[1]
    ax.bar([f"{r:g}" for r in divergent], flagged, color="indianred")
    ax.axhline(10, color="k", ls="--", lw=1.0, label="10-iteration acceptance cap")
    ax.set_xlabel(r"spectral radius $\rho > 1$")
    ax.set_ylabel("iteration where DIVERGED is declared")
    ax.set_title("Early divergence detection")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {
        "rho_input": rhos.tolist(),
        "rho_measured": measured,
        "max_relative_deviation": float(np.max(np.abs(np.array(measured) - rhos) / rhos)),
        "divergence_iterations": {f"{r:g}": f for r, f in zip(divergent, flagged)},
    }


def fig_sensitivity(path: Path) -> dict:
    fixture = linear_two_discipline_system(0.5)
    clean = sensitivity_consistency_gate(fixture.to_system(), [1.0], analytic=fixture.analytic_sensitivity)
    base = fixture.to_system()
    corrupted_coupling = CoupledSystem(
        disciplines=[base.disciplines[0], scale_declared_partials(base.disciplines[1], state_scale=1.5)],
        output=base.output,
    )
    corrupted_param = CoupledSystem(
        disciplines=[base.disciplines[0], scale_declared_partials(base.disciplines[1], param_scale=1.25)],
        output=base.output,
    )
    bad_state = sensitivity_consistency_gate(corrupted_coupling, [1.0])
    bad_param = sensitivity_consistency_gate(corrupted_param, [1.0])

    labels = ["clean design", "coupling Jacobian x1.5", "parameter derivative x1.25"]
    composed = [clean["composed"], bad_state["composed"], bad_param["composed"]]
    resolved = [clean["total"], bad_state["total"], bad_param["total"]]
    analytic = [clean["analytic"]] * 3

    x = np.arange(len(labels))
    width = 0.26
    fig, ax = plt.subplots(figsize=(7.6, 4.6))
    ax.bar(x - width, composed, width, label="composed (frozen per-discipline partials)")
    ax.bar(x, resolved, width, label="re-resolved (ground truth)")
    ax.bar(x + width, analytic, width, label="analytic")
    for xi, value in zip(x, composed):
        ax.annotate(f"{value:.3f}", (xi - width, value), ha="center", va="bottom", fontsize=7)
    ax.axhline(fixture.analytic_sensitivity * 1.02, color="crimson", ls="--", lw=1.0)
    ax.axhline(
        fixture.analytic_sensitivity * 0.98,
        color="crimson",
        ls="--",
        lw=1.0,
        label="±2 % band around the analytic value",
    )
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("dF/dp")
    ax.set_title("Sensitivity consistency gate: clean design and broken designs")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {
        "clean_relative_error": clean["relative_error"],
        "corrupted_coupling_relative_error": bad_state["relative_error"],
        "corrupted_param_relative_error": bad_param["relative_error"],
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {
        "fig1_residual_history": fig_residual_history(OUT / "fig1_residual_history.png"),
        "fig2_refinement_order": fig_refinement_order(OUT / "fig2_refinement_order.png"),
        "fig3_contraction_and_divergence": fig_contraction_and_divergence(
            OUT / "fig3_contraction_and_divergence.png"
        ),
        "fig4_sensitivity_consistency": fig_sensitivity(OUT / "fig4_sensitivity_consistency.png"),
    }
    (OUT / "figures.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())