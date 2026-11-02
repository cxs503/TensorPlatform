"""Evidence generator for the ice-UQ quadrature-aliasing fix (task m1892).

Produces, from the live code path:

  artifacts/m1892/evidence.json                      machine-readable table set
  docs/verification/figures_m1892/fig1_aliasing_map.png
  docs/verification/figures_m1892/fig2_sobol_before_after.png

Run:  PYTHONPATH=backend python3 tools/validation/verify_ice_uq_aliasing_m1892.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "tools", "validation"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from services import ice_resistance_uq as UQ  # noqa: E402
import ice_uq_reference_m1882 as REF  # noqa: E402

ART = os.path.join(ROOT, "artifacts", "m1892")
FIG = os.path.join(ROOT, "docs", "verification", "figures_m1892")
os.makedirs(ART, exist_ok=True)
os.makedirs(FIG, exist_ok=True)


# --------------------------------------------------------------------- helpers
def cub_inputs():
    return [
        UQ.UncertainInput("xi0", nominal=1.0, std_relative=1.0),
        UQ.UncertainInput("xi1", nominal=1.0, std_relative=1.0),
    ]


def cubic(x0, x1):
    return (x0 - 1.0) ** 3


def pce_eval_vector(model, inputs):
    """Adapter so the PCE construction can be reused as a vector model."""
    def call(z):
        z = np.atleast_2d(z)
        out = np.empty(len(z))
        for i, row in enumerate(z):
            x = np.array([inp.mean + inp.std * row[d] for d, inp in enumerate(inputs)])
            out[i] = model(*x)
        return out
    return call


# --------------------------------------------------------------------- A: aliasing map
LEVELS = [1, 2, 3, 4, 5, 6]


def aliasing_map():
    rows = []
    for level in LEVELS:
        pce = UQ.build_pce(
            cubic, cub_inputs(), max_level=level, max_poly_order=3,
            quadrature_policy="allow_aliasing",
        )
        stats = UQ.pce_statistics(pce)
        sobol = UQ.sobol_indices(pce)
        rows.append({
            "level": level,
            "n_nodes": int(pce["n_evaluations"]),
            "exactness_degree": 2 * level - 1,
            "required_degree": 2 * pce["max_poly_order"],
            "aliasing_free": bool(pce["aliasing_free"]),
            "variance": float(stats["variance"]),
            "variance_rel_err": float(abs(stats["variance"] - 15.0) / 15.0),
            "ST_inert": float(sobol["total_order"]["xi1"]),
        })
    return rows


# --------------------------------------------------------------------- B: monomial exactness
def monomial_table():
    rows = []
    for order in (1, 2, 3, 4):
        def model(x0, x1, order=order):
            return (x0 - 1.0) ** order

        pce = UQ.build_pce(model, cub_inputs(), max_poly_order=order)
        stats = UQ.pce_statistics(pce)
        sobol = UQ.sobol_indices(pce)
        mean_a = REF.normal_moment(order)
        var_a = REF.normal_moment(2 * order) - mean_a ** 2
        rows.append({
            "order": order,
            "level_used": int(pce["max_level"]),
            "n_nodes": int(pce["n_evaluations"]),
            "mean": float(stats["mean"]), "mean_analytic": mean_a,
            "mean_rel_err": float(abs(stats["mean"] - mean_a) / max(1e-30, abs(mean_a)))
            if mean_a else float(abs(stats["mean"])),
            "variance": float(stats["variance"]), "variance_analytic": var_a,
            "variance_rel_err": float(abs(stats["variance"] - var_a) / var_a),
            "ST_inert": float(sobol["total_order"]["xi1"]),
            "ST_sum": float(sum(sobol["total_order"].values())),
        })
    return rows


# --------------------------------------------------------------------- C: real ships
def real_ship_checks(n_base=4096):
    out = {}
    for ship_name, trial in UQ.SHIP_TRIAL_DATA.items():
        ship = trial["params"]
        inputs = [
            UQ.UncertainInput("ice_strength_MPa", trial["nominal_ice_strength_MPa"], 0.20),
            UQ.UncertainInput("ice_thickness_m", trial["nominal_ice_thickness_m"], 0.15),
            UQ.UncertainInput("friction_coeff", trial["nominal_friction_coeff"], 0.25),
            UQ.UncertainInput("ship_speed_ms", trial["nominal_speed_ms"], 0.10),
        ]
        scalar = lambda s, h, m, v: UQ.lindqvist_ice_resistance(ship, h, s, m, v)  # noqa: E731

        # fixed pipeline (defaults) and legacy aliased configuration
        fixed = UQ.run_ice_resistance_uq(ship_name, n_mc_reference=100000, n_mc_validation=10000)
        legacy_pce = UQ.build_pce(
            scalar, inputs, max_level=3, max_poly_order=3,
            quadrature_policy="allow_aliasing",
        )
        legacy_sobol = UQ.sobol_indices(legacy_pce)
        legacy_stats = UQ.pce_statistics(legacy_pce)

        # independent Saltelli/Jansen reference on the declared truncated support
        specs = list(UQ.ICE_INPUT_TRUNCATION)
        lo = np.array([s.lower for s in specs])
        hi = np.array([s.upper for s in specs])
        means = np.array([i.mean for i in inputs])
        stds = np.array([i.std for i in inputs])

        def transform(z):
            return np.clip(means + stds * z, lo, hi)

        def vec(z):
            x = transform(np.atleast_2d(z))
            return np.array([scalar(*row) for row in x])

        mc = REF.legendre_saltelli_indices(vec, dim=4, n_base=n_base)

        sobol_fixed = [fixed.sobol_total_order[inp.name] for inp in inputs]
        sobol_legacy = [legacy_sobol["total_order"][inp.name] for inp in inputs]
        out[ship_name] = {
            "level_used": fixed.max_level,
            "n_model_evaluations": fixed.n_model_evaluations,
            "aliasing_free": fixed.quadrature_aliasing_free,
            "input_names": [inp.name for inp in inputs],
            "ST_fixed": sobol_fixed,
            "ST_legacy_level3": sobol_legacy,
            "ST_legacy_minus_fixed": [a - b for a, b in zip(sobol_legacy, sobol_fixed)],
            "ST_saltelli_mc": [float(v) for v in mc["total_order"]],
            "ST_fixed_vs_mc_max_abs": float(
                np.max(np.abs(np.array(sobol_fixed) - np.array(mc["total_order"])))
            ),
            "ST_legacy_vs_mc_max_abs": float(
                np.max(np.abs(np.array(sobol_legacy) - np.array(mc["total_order"])))
            ),
            "ST_fixed_sum": float(sum(sobol_fixed)),
            "mean_kN": fixed.mean_resistance_kN,
            "mean_legacy_kN": float(legacy_stats["mean"]),
            "std_kN": fixed.std_resistance_kN,
            "std_legacy_kN": float(np.sqrt(max(0.0, legacy_stats["variance"]))),
            "mc_mean_kN": fixed.mc_mean_kN,
            "mc_std_kN": fixed.mc_std_kN,
            "mean_rel_err_vs_mc": fixed.mean_error,
            "std_rel_err_vs_mc": fixed.std_error,
            "ci_coverage": fixed.ci_coverage,
            "truncation": fixed.distribution_truncation,
        }
    return out


# --------------------------------------------------------------------- D: truncation
def truncation_checks():
    # legacy hidden clip vs declared rejection sampling, measured on the ice model
    trial = UQ.SHIP_TRIAL_DATA["MV_Arctic"]
    ship = trial["params"]
    inputs = [
        UQ.UncertainInput("ice_strength_MPa", trial["nominal_ice_strength_MPa"], 0.20),
        UQ.UncertainInput("ice_thickness_m", trial["nominal_ice_thickness_m"], 0.15),
        UQ.UncertainInput("friction_coeff", trial["nominal_friction_coeff"], 0.25),
        UQ.UncertainInput("ship_speed_ms", trial["nominal_speed_ms"], 0.10),
    ]
    model = lambda s, h, m, v: UQ.lindqvist_ice_resistance(ship, h, s, m, v)  # noqa: E731
    specs = list(UQ.ICE_INPUT_TRUNCATION)

    reject = UQ.monte_carlo_reference(model, inputs, n_samples=100000, seed=5,
                                      truncation=specs, truncation_mode="reject")
    clip = UQ.monte_carlo_reference(model, inputs, n_samples=100000, seed=5,
                                    truncation=specs, truncation_mode="clip")
    plain = UQ.monte_carlo_reference(model, inputs, n_samples=100000, seed=5)

    return {
        "declared_masses": UQ.pce_numerics_status()["declared_truncation"],
        "max_mass_outside": float(reject["truncation"]["max_mass_outside"]),
        "reject": {"mean": reject["mean"], "std": reject["std"],
                   "n_redrawn": reject["truncation"]["n_redrawn"]},
        "clip": {"mean": clip["mean"], "std": clip["std"],
                 "mode": clip["truncation"]["truncation_mode"],
                 "per_input_fraction": [r["realized_outside_fraction"]
                                        for r in clip["truncation"]["per_input"]]},
        "untruncated": {"mean": plain["mean"], "std": plain["std"]},
        "clip_mean_bias_rel": float((clip["mean"] - reject["mean"]) / reject["mean"]),
        "clip_std_bias_rel": float((clip["std"] - reject["std"]) / reject["std"]),
        "untruncated_mean_bias_rel": float((plain["mean"] - reject["mean"]) / reject["mean"]),
        "untruncated_std_bias_rel": float((plain["std"] - reject["std"]) / reject["std"]),
    }


# --------------------------------------------------------------------- figures
def make_figures(amap, ships):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))

    levels = [r["level"] for r in amap]
    axes[0].plot(levels, [r["variance"] for r in amap], "o-", color="#c62828",
                 label="PCE variance (raw levels, order 3)")
    axes[0].axhline(15.0, color="black", linestyle="--", label="analytic Var = 15")
    for r in amap:
        axes[0].annotate(f"{r['variance']:.2f}", (r["level"], r["variance"]),
                         textcoords="offset points", xytext=(0, 7), fontsize=8,
                         ha="center")
    axes[0].axvline(4, color="green", linestyle=":", linewidth=1.5,
                    label="required level $p+1$ = 4")
    axes[0].set_xlabel("Smolyak level $L$")
    axes[0].set_ylabel("variance of $y=\\xi_0^3$")
    axes[0].set_title("Aliased vs converged variance (4-D rule, $p=3$)")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.3)

    axes[1].plot(levels, [r["ST_inert"] for r in amap], "s-", color="#1565c0",
                 label="$S_T(\\xi_1)$, $\\xi_1$ inert")
    axes[1].axhline(0.0, color="black", linestyle="--", label="analytic $S_T=0$")
    axes[1].axvline(4, color="green", linestyle=":", linewidth=1.5,
                    label="required level = 4")
    for r in amap:
        if r["ST_inert"] > 1e-3:
            axes[1].annotate(f"{r['ST_inert']:.4f}", (r["level"], r["ST_inert"]),
                             textcoords="offset points", xytext=(0, 7), fontsize=8,
                             ha="center")
    axes[1].set_xlabel("Smolyak level $L$")
    axes[1].set_ylabel("total-order Sobol index of the inert input")
    axes[1].set_title("Spurious sensitivity of an inert input")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.3)

    fig.suptitle("m1892 / m1882: quadrature aliasing of the ice-UQ PCE", fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig1_aliasing_map.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)

    names = list(ships)
    inputs = ships[names[0]]["input_names"]
    x = np.arange(len(inputs))
    width = 0.2
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    for ax, name in zip(axes, names[:2]):
        res = ships[name]
        ax.bar(x - 1.5 * width, res["ST_legacy_level3"], width, color="#c62828",
               label="PCE level 3 (legacy, aliased)")
        ax.bar(x - 0.5 * width, res["ST_fixed"], width, color="#2e7d32",
               label=f"PCE level {res['level_used']} (fixed)")
        ax.bar(x + 0.5 * width, res["ST_saltelli_mc"], width, color="#1565c0",
               label="Saltelli/Jansen MC (independent)")
        ax.set_xticks(x)
        ax.set_xticklabels([n.replace("_", "\n") for n in inputs], fontsize=8)
        ax.set_ylabel("total-order Sobol index $S_T$")
        ax.set_title(f"{name}: max|fixed-MC| = {res['ST_fixed_vs_mc_max_abs']:.3f}, "
                     f"max|legacy-MC| = {res['ST_legacy_vs_mc_max_abs']:.3f}",
                     fontsize=10)
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3, axis="y")
    fig.suptitle("Total-order Sobol indices: aliased vs fixed PCE vs independent MC",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig2_sobol_before_after.png"), dpi=150,
                bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------- main
def main():
    amap = aliasing_map()
    mono = monomial_table()
    ships = real_ship_checks()
    trunc = truncation_checks()
    status = UQ.pce_numerics_status()

    grid4d = {}
    for level in LEVELS:
        nodes, weights = UQ.smolyak_sparse_grid(level, 4)
        grid4d[str(level)] = {"n_nodes": int(len(weights)),
                              "weight_sum": float(np.sum(weights))}

    evidence = {
        "mission": "m1892",
        "production_grid_4d": grid4d,
        "defect": "PCE Smolyak projection aliases when max_level < max_poly_order+1",
        "aliasing_map": amap,
        "monomial_exactness": mono,
        "real_ships": ships,
        "truncation": trunc,
        "numerics_status": status,
        "verdict": {
            "aliasing_fixed": status["verdict_aliasing_fixed"],
            "guarded_variance_rel_err": status["guarded"]["variance_rel_err"],
            "guarded_ST_inert": status["guarded"]["S_T_inert"],
            "legacy_variance": status["aliased_reference"]["variance"],
            "legacy_ST_inert": status["aliased_reference"]["S_T_inert"],
            "truncation_declared": True,
            "max_truncation_mass": status["max_truncation_mass"],
        },
    }
    with open(os.path.join(ART, "evidence.json"), "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, indent=1, default=float)

    make_figures(amap, ships)

    # console summary (numbers are read from the artefacts in the report, not typed)
    print(json.dumps(evidence["verdict"], indent=1))
    for row in amap:
        print(f"L={row['level']} var={row['variance']:.6f} "
              f"ST_inert={row['ST_inert']:.6f} exact={row['exactness_degree']}")
    for name, res in ships.items():
        print(name, "lvl", res["level_used"],
              "|fixed-MC|", round(res["ST_fixed_vs_mc_max_abs"], 4),
              "|legacy-MC|", round(res["ST_legacy_vs_mc_max_abs"], 4),
              "mean_err", round(res["mean_rel_err_vs_mc"], 6),
              "std_err", round(res["std_rel_err_vs_mc"], 6),
              "cov", round(res["ci_coverage"], 4))
    print("truncation:", json.dumps({k: trunc[k] for k in
                                     ("max_mass_outside", "clip_mean_bias_rel",
                                      "clip_std_bias_rel")}, indent=1))
    print("artefacts:", ART, FIG)


if __name__ == "__main__":
    main()