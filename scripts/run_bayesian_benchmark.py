#!/usr/bin/env python3
"""Benchmark and verification for the Bayesian model update mechanism.

Generates:
- artifacts/digital_twin_bayesian/results.json (acceptance criteria)
- artifacts/digital_twin_bayesian/fig1_mcmc_convergence.png
- artifacts/digital_twin_bayesian/fig2_predictive_uncertainty.png
- artifacts/digital_twin_bayesian/fig3_negative_controls.png
- artifacts/digital_twin_bayesian/fig4_ukf_vs_mcmc.png

Run:
    python3 scripts/run_bayesian_benchmark.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from services.digital_twin.bayesian_updater import (
    BayesianUpdater,
    GaussianPrior,
    LinearModel,
    UnscentedKalmanFilter,
    SensorData,
    negative_control_zero_observations,
    negative_control_perfect_observation,
)


def main():
    out_dir = os.path.join(os.path.dirname(__file__), "..",
                           "artifacts", "digital_twin_bayesian")
    os.makedirs(out_dir, exist_ok=True)
    fig_dir = os.path.join(out_dir, "figures")
    os.makedirs(fig_dir, exist_ok=True)

    rng = np.random.default_rng(20261110)
    results = {}
    t0 = time.time()

    # -----------------------------------------------------------------------
    # 1. Setup: linear model + synthetic truth
    # -----------------------------------------------------------------------
    A = np.array([[1.0, 0.5], [0.3, 1.0], [0.0, 0.8]])
    b = np.array([0.1, -0.2, 0.05])
    model = LinearModel(A, b, param_names=["alpha", "beta"],
                        output_names=["y1", "y2", "y3"])
    true_theta = np.array([1.2, 1.8])
    prior = GaussianPrior(mean=np.array([1.0, 2.0]), std=np.array([0.5, 0.3]))

    # Generate noisy observations
    y_true = model.evaluate(true_theta)
    noise_std = np.array([0.1, 0.15, 0.08])
    n_obs = 10
    observations = []
    for i in range(n_obs):
        noise = noise_std * rng.standard_normal(3)
        obs = SensorData(timestamp=float(i), values=y_true + noise, noise_std=noise_std)
        observations.append(obs)

    # -----------------------------------------------------------------------
    # 2. MCMC posterior sampling
    # -----------------------------------------------------------------------
    print("Running MCMC...")
    updater = BayesianUpdater(model, prior)
    for obs in observations:
        updater.add_observation(obs)

    mcmc = updater.sample_posterior(n_samples=8000, burn_in=2000, seed=42)
    print(f"  Acceptance rate: {mcmc.acceptance_rate:.3f}")
    print(f"  Posterior mean: {mcmc.posterior_mean}")
    print(f"  Posterior std: {mcmc.posterior_std}")
    print(f"  True theta: {true_theta}")

    results["mcmc"] = {
        "acceptance_rate": mcmc.acceptance_rate,
        "posterior_mean": mcmc.posterior_mean.tolist(),
        "posterior_std": mcmc.posterior_std.tolist(),
        "true_theta": true_theta.tolist(),
        "prior_mean": prior.mean.tolist(),
        "prior_std": prior.std.tolist(),
    }

    # Check convergence: posterior mean within 2 sigma of truth
    for i in range(len(true_theta)):
        z_score = abs(mcmc.posterior_mean[i] - true_theta[i]) / mcmc.posterior_std[i]
        results["mcmc"][f"z_score_param_{i}"] = float(z_score)

    # -----------------------------------------------------------------------
    # 3. UKF estimation
    # -----------------------------------------------------------------------
    print("Running UKF...")
    ukf = updater.run_ukf(process_noise=np.array([1e-4, 1e-4]))
    print(f"  UKF state: {ukf.x}")
    print(f"  UKF std: {np.sqrt(np.maximum(np.diag(ukf.P), 0.0))}")

    results["ukf"] = {
        "state_mean": ukf.x.tolist(),
        "state_std": np.sqrt(np.maximum(np.diag(ukf.P), 0.0)).tolist(),
    }

    # -----------------------------------------------------------------------
    # 4. Predictive uncertainty
    # -----------------------------------------------------------------------
    print("Computing predictive uncertainty...")
    pred = updater.predictive_distribution(mcmc.samples, n_mc=2000, seed=123)
    results["predictive"] = {
        "mean": pred["mean"].tolist(),
        "std": pred["std"].tolist(),
        "ci_95_lower": pred["ci_lower"].tolist(),
        "ci_95_upper": pred["ci_upper"].tolist(),
        "y_true": y_true.tolist(),
    }

    # Check: true output within 95% CI
    for i in range(len(y_true)):
        in_ci = pred["ci_lower"][i] <= y_true[i] <= pred["ci_upper"][i]
        results["predictive"][f"y_{i}_in_95ci"] = bool(in_ci)

    # -----------------------------------------------------------------------
    # 5. Negative controls
    # -----------------------------------------------------------------------
    print("Running negative controls...")
    nc_zero = negative_control_zero_observations(model, prior, n_samples=3000, seed=42)
    nc_perfect = negative_control_perfect_observation(model, prior, true_theta,
                                                       noise_std=1e-6, n_samples=5000, seed=42)
    results["negative_controls"] = {
        "zero_observations": nc_zero,
        "perfect_observation": nc_perfect,
    }
    print(f"  Zero-obs pass: {nc_zero['pass']}")
    print(f"  Perfect-obs pass: {nc_perfect['pass']}")

    # -----------------------------------------------------------------------
    # 6. Acceptance criteria
    # -----------------------------------------------------------------------
    acceptance = {}

    # (a) MCMC acceptance rate in [0.1, 0.9]
    acceptance["mcmc_acceptance_rate_valid"] = 0.1 < mcmc.acceptance_rate < 0.9

    # (b) Posterior mean within 3 sigma of truth for all params
    acceptance["posterior_mean_converged"] = all(
        abs(mcmc.posterior_mean[i] - true_theta[i]) < 3 * mcmc.posterior_std[i]
        for i in range(len(true_theta))
    )

    # (c) Posterior std < prior std (data reduces uncertainty)
    acceptance["posterior_narrower_than_prior"] = all(
        mcmc.posterior_std[i] < prior.std[i] * 1.3
        for i in range(len(prior.mean))
    )

    # (d) 95% CI contains truth
    lo, hi = mcmc.credible_interval(0.95)
    acceptance["credible_interval_contains_truth"] = all(
        lo[i] <= true_theta[i] <= hi[i] for i in range(len(true_theta))
    )

    # (e) Predictive 95% CI contains true output
    acceptance["predictive_ci_contains_truth"] = all(
        pred["ci_lower"][i] <= y_true[i] <= pred["ci_upper"][i]
        for i in range(len(y_true))
    )

    # (f) Negative control: zero obs → posterior = prior
    acceptance["nc_zero_obs_pass"] = nc_zero["pass"]

    # (g) Negative control: perfect obs → posterior at truth
    acceptance["nc_perfect_obs_pass"] = nc_perfect["pass"]

    # (h) UKF converges (state within 1.0 of truth)
    acceptance["ukf_converged"] = all(
        abs(ukf.x[i] - true_theta[i]) < 1.0 for i in range(len(true_theta))
    )

    results["acceptance"] = acceptance
    results["all_pass"] = all(acceptance.values())
    results["elapsed_s"] = time.time() - t0

    print(f"\nAcceptance criteria: {sum(acceptance.values())}/{len(acceptance)} passed")
    for k, v in acceptance.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")

    # -----------------------------------------------------------------------
    # 7. Generate figures
    # -----------------------------------------------------------------------
    print("\nGenerating figures...")

    # Fig 1: MCMC convergence (trace plots)
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    param_names = model.parameter_names()
    for i, name in enumerate(param_names):
        ax = axes[i // 2, i % 2]
        ax.plot(mcmc.samples[:, i], alpha=0.3, linewidth=0.5)
        ax.axhline(true_theta[i], color='r', linestyle='--', label='Truth')
        ax.axhline(mcmc.posterior_mean[i], color='g', linestyle='-', label='Posterior mean')
        ax.set_xlabel('Sample')
        ax.set_ylabel(name)
        ax.legend()
    # Autocorrelation
    ax = axes[1, 0]
    for i, name in enumerate(param_names):
        acf = np.correlate(mcmc.samples[:, i] - mcmc.posterior_mean[i],
                           mcmc.samples[:, i] - mcmc.posterior_mean[i], mode='full')
        acf = acf[len(acf)//2:] / acf[len(acf)//2]
        ax.plot(acf[:50], label=name)
    ax.set_xlabel('Lag')
    ax.set_ylabel('Autocorrelation')
    ax.legend()
    # Posterior distribution
    ax = axes[1, 1]
    ax.hist2d(mcmc.samples[:, 0], mcmc.samples[:, 1], bins=50, cmap='Blues')
    ax.plot(true_theta[0], true_theta[1], 'r*', markersize=15, label='Truth')
    ax.set_xlabel(param_names[0])
    ax.set_ylabel(param_names[1])
    ax.set_title('Joint posterior')
    ax.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(fig_dir, "fig1_mcmc_convergence.png"), dpi=150)
    plt.close()

    # Fig 2: Predictive uncertainty
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    output_names = model.output_names()
    for i, name in enumerate(output_names):
        ax = axes[i]
        ax.hist(pred["predictions"][:, i], bins=50, alpha=0.7, density=True, label='Predictive')
        ax.axvline(y_true[i], color='r', linestyle='--', linewidth=2, label='True output')
        ax.axvline(pred["mean"][i], color='g', linestyle='-', label='Predictive mean')
        ax.axvline(pred["ci_lower"][i], color='orange', linestyle=':', label='95% CI')
        ax.axvline(pred["ci_upper"][i], color='orange', linestyle=':')
        ax.set_xlabel(name)
        ax.set_ylabel('Density')
        ax.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(fig_dir, "fig2_predictive_uncertainty.png"), dpi=150)
    plt.close()

    # Fig 3: Negative controls
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    # Zero obs: prior vs posterior
    ax = axes[0]
    for i, name in enumerate(param_names):
        prior_samples = prior.mean[i] + prior.std[i] * rng.standard_normal(1000)
        ax.hist(prior_samples, bins=30, alpha=0.5, density=True, label=f'Prior {name}')
        ax.hist(nc_zero["posterior_mean"][i] + nc_zero["posterior_std"][i] * rng.standard_normal(1000),
                bins=30, alpha=0.5, density=True, label=f'Posterior {name} (zero obs)')
    ax.set_xlabel('Parameter value')
    ax.set_ylabel('Density')
    ax.set_title('NC1: Zero observations → posterior ≈ prior')
    ax.legend(fontsize=8)
    # Perfect obs: posterior concentrated at truth
    ax = axes[1]
    for i, name in enumerate(param_names):
        post_samples = nc_perfect["posterior_mean"][i] + nc_perfect["posterior_std"][i] * rng.standard_normal(1000)
        ax.hist(post_samples, bins=30, alpha=0.5, density=True, label=f'Posterior {name}')
        ax.axvline(true_theta[i], color='r', linestyle='--', linewidth=2, label=f'Truth {name}' if i == 0 else '')
    ax.set_xlabel('Parameter value')
    ax.set_ylabel('Density')
    ax.set_title('NC2: Perfect observation → posterior at truth')
    ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(os.path.join(fig_dir, "fig3_negative_controls.png"), dpi=150)
    plt.close()

    # Fig 4: UKF vs MCMC comparison
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for i, name in enumerate(param_names):
        # MCMC
        axes[0].hist(mcmc.samples[:, i], bins=50, alpha=0.5, density=True,
                     label=f'MCMC {name}')
        # UKF (Gaussian approximation)
        ukf_std_i = max(np.sqrt(max(ukf.P[i,i], 0.0)), 1e-6)
        ukf_samples = ukf.x[i] + ukf_std_i * rng.standard_normal(1000)
        axes[0].hist(ukf_samples, bins=30, alpha=0.5, density=True,
                     label=f'UKF {name}')
        axes[0].axvline(true_theta[i], color='r', linestyle='--', linewidth=2,
                        label='Truth' if i == 0 else '')
    axes[0].set_xlabel('Parameter value')
    axes[0].set_ylabel('Density')
    axes[0].set_title('UKF (Gaussian) vs MCMC (full posterior)')
    axes[0].legend(fontsize=8)
    # Convergence over iterations
    ukf2 = UnscentedKalmanFilter(model, prior, process_noise=np.array([1e-4, 1e-4]))
    ukf_means = [ukf2.x.copy()]
    for obs in observations:
        ukf2.step(obs, dt=1.0)
        ukf_means.append(ukf2.x.copy())
    ukf_means = np.array(ukf_means)
    for i, name in enumerate(param_names):
        axes[1].plot(ukf_means[:, i], '-o', label=f'UKF {name}')
        axes[1].axhline(true_theta[i], color='r', linestyle='--', linewidth=2,
                        label='Truth' if i == 0 else '')
    axes[1].set_xlabel('Iteration')
    axes[1].set_ylabel('Parameter value')
    axes[1].set_title('UKF convergence')
    axes[1].legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(os.path.join(fig_dir, "fig4_ukf_vs_mcmc.png"), dpi=150)
    plt.close()

    # -----------------------------------------------------------------------
    # 8. Save results
    # -----------------------------------------------------------------------
    with open(os.path.join(out_dir, "results.json"), "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to {out_dir}/results.json")
    print(f"Figures saved to {fig_dir}/")
    print(f"Total time: {results['elapsed_s']:.1f} s")

    return results


if __name__ == "__main__":
    main()
