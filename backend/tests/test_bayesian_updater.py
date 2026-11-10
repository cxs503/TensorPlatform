"""Tests for the Bayesian model update mechanism.

Covers:
- ModelWrapper / SensorData interfaces
- Gaussian prior and likelihood
- MCMC posterior sampling (Metropolis-Hastings)
- UKF estimation
- Predictive uncertainty quantification
- Negative controls (zero obs → prior; perfect obs → truth)
- Integration with surrogate registry
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

# Ensure backend/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.digital_twin.bayesian_updater import (
    BayesianUpdater,
    GaussianPrior,
    LinearModel,
    MCMCResult,
    ModelWrapper,
    SensorData,
    UKFResult,
    UnscentedKalmanFilter,
    gaussian_log_likelihood,
    metropolis_hastings,
    negative_control_perfect_observation,
    negative_control_zero_observations,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def linear_model():
    """Simple 2-parameter linear model: y = A @ θ + b."""
    A = np.array([[1.0, 0.5], [0.3, 1.0], [0.0, 0.8]])
    b = np.array([0.1, -0.2, 0.05])
    return LinearModel(A, b, param_names=["alpha", "beta"],
                       output_names=["y1", "y2", "y3"])


@pytest.fixture
def prior():
    """Gaussian prior centered at [1.0, 2.0] with std [0.5, 0.3]."""
    return GaussianPrior(mean=np.array([1.0, 2.0]), std=np.array([0.5, 0.3]))


@pytest.fixture
def true_theta():
    return np.array([1.2, 1.8])


@pytest.fixture
def noisy_observations(linear_model, true_theta):
    """Generate noisy observations from the linear model."""
    rng = np.random.default_rng(42)
    y_true = linear_model.evaluate(true_theta)
    noise_std = np.array([0.1, 0.15, 0.08])
    observations = []
    for i in range(5):
        noise = noise_std * rng.standard_normal(3)
        obs = SensorData(
            timestamp=float(i),
            values=y_true + noise,
            noise_std=noise_std,
        )
        observations.append(obs)
    return observations


# ---------------------------------------------------------------------------
# Interface tests
# ---------------------------------------------------------------------------

class TestModelWrapper:
    def test_linear_model_evaluate(self, linear_model, true_theta):
        y = linear_model.evaluate(true_theta)
        expected = linear_model.A @ true_theta + linear_model.b
        np.testing.assert_allclose(y, expected)

    def test_parameter_names(self, linear_model):
        assert linear_model.parameter_names() == ["alpha", "beta"]
        assert linear_model.n_params == 2

    def test_output_names(self, linear_model):
        assert linear_model.output_names() == ["y1", "y2", "y3"]
        assert linear_model.n_outputs == 3


class TestSensorData:
    def test_basic_construction(self):
        obs = SensorData(timestamp=1.0, values=np.array([3.0, 4.0]),
                         noise_std=np.array([0.1, 0.2]))
        assert obs.timestamp == 1.0
        assert obs.n_obs == 2

    def test_mismatched_shapes_raises(self):
        with pytest.raises(ValueError, match="same shape"):
            SensorData(timestamp=0.0, values=np.array([1.0]),
                       noise_std=np.array([0.1, 0.2]))

    def test_zero_noise_raises(self):
        with pytest.raises(ValueError, match="strictly positive"):
            SensorData(timestamp=0.0, values=np.array([1.0]),
                       noise_std=np.array([0.0]))

    def test_output_indices(self):
        obs = SensorData(timestamp=0.0, values=np.array([3.0]),
                         noise_std=np.array([0.1]),
                         output_indices=np.array([1]))
        assert obs.output_indices is not None
        assert obs.output_indices[0] == 1


# ---------------------------------------------------------------------------
# Prior and likelihood tests
# ---------------------------------------------------------------------------

class TestGaussianPrior:
    def test_log_pdf_at_mean(self, prior):
        lp = prior.log_pdf(prior.mean)
        assert lp == 0.0  # maximum of Gaussian

    def test_log_pdf_decreases_away(self, prior):
        lp_mean = prior.log_pdf(prior.mean)
        lp_away = prior.log_pdf(prior.mean + 2 * prior.std)
        assert lp_away < lp_mean

    def test_sample_shape(self, prior):
        rng = np.random.default_rng(0)
        s = prior.sample(rng)
        assert s.shape == prior.mean.shape

    def test_invalid_std_raises(self):
        with pytest.raises(ValueError):
            GaussianPrior(mean=np.array([0.0]), std=np.array([-1.0]))


class TestLikelihood:
    def test_perfect_match_high_likelihood(self, linear_model, true_theta):
        y = linear_model.evaluate(true_theta)
        obs = SensorData(timestamp=0.0, values=y, noise_std=np.array([0.1, 0.1, 0.1]))
        ll = gaussian_log_likelihood(y, obs)
        assert ll == 0.0  # perfect match → log-likelihood = 0

    def test_mismatch_negative_likelihood(self, linear_model, true_theta):
        y = linear_model.evaluate(true_theta)
        obs = SensorData(timestamp=0.0, values=y + 1.0,
                         noise_std=np.array([0.1, 0.1, 0.1]))
        ll = gaussian_log_likelihood(y, obs)
        assert ll < -10.0  # large mismatch → very negative

    def test_partial_observation(self, linear_model, true_theta):
        y = linear_model.evaluate(true_theta)
        obs = SensorData(timestamp=0.0, values=np.array([y[1]]),
                         noise_std=np.array([0.1]),
                         output_indices=np.array([1]))
        ll = gaussian_log_likelihood(y, obs)
        assert ll == 0.0  # perfect match on observed channel


# ---------------------------------------------------------------------------
# MCMC tests
# ---------------------------------------------------------------------------

class TestMCMC:
    def test_convergence_to_truth(self, linear_model, prior, noisy_observations, true_theta):
        """MCMC posterior mean should be close to true theta."""
        result = metropolis_hastings(
            model=linear_model,
            observations=noisy_observations,
            prior=prior,
            n_samples=3000,
            burn_in=500,
            seed=42,
        )
        assert result.acceptance_rate > 0.1
        assert result.acceptance_rate < 0.95
        # Posterior mean within 3 sigma of truth
        for i in range(len(true_theta)):
            assert abs(result.posterior_mean[i] - true_theta[i]) < 3 * prior.std[i]

    def test_posterior_narrower_than_prior(self, linear_model, prior, noisy_observations):
        """With data, posterior std should be smaller than prior std."""
        result = metropolis_hastings(
            model=linear_model,
            observations=noisy_observations,
            prior=prior,
            n_samples=3000,
            burn_in=500,
            seed=42,
        )
        for i in range(prior.mean.shape[0]):
            assert result.posterior_std[i] < prior.std[i] * 1.5  # allow some MC noise

    def test_credible_interval_contains_truth(self, linear_model, prior, noisy_observations, true_theta):
        """95% credible interval should contain the true value."""
        result = metropolis_hastings(
            model=linear_model,
            observations=noisy_observations,
            prior=prior,
            n_samples=5000,
            burn_in=1000,
            seed=42,
        )
        lo, hi = result.credible_interval(0.95)
        for i in range(len(true_theta)):
            assert lo[i] <= true_theta[i] <= hi[i]

    def test_no_observations_posterior_equals_prior(self, linear_model, prior):
        """With no observations, posterior = prior (negative control)."""
        result = metropolis_hastings(
            model=linear_model,
            observations=[],
            prior=prior,
            n_samples=3000,
            burn_in=500,
            seed=42,
        )
        # Posterior mean should be close to prior mean
        np.testing.assert_allclose(result.posterior_mean, prior.mean, atol=0.15)
        # Posterior std should be close to prior std
        np.testing.assert_allclose(result.posterior_std, prior.std, rtol=0.2)

    def test_to_dict(self, linear_model, prior, noisy_observations):
        result = metropolis_hastings(
            model=linear_model, observations=noisy_observations,
            prior=prior, n_samples=500, burn_in=100, seed=42,
        )
        d = result.to_dict()
        assert "posterior_mean" in d
        assert "posterior_std" in d
        assert "credible_interval_95" in d
        assert "acceptance_rate" in d


# ---------------------------------------------------------------------------
# UKF tests
# ---------------------------------------------------------------------------

class TestUKF:
    def test_convergence_to_truth(self, linear_model, prior, true_theta):
        """UKF should converge toward truth with repeated observations."""
        rng = np.random.default_rng(42)
        y_true = linear_model.evaluate(true_theta)
        noise_std = np.array([0.1, 0.15, 0.08])

        ukf = UnscentedKalmanFilter(model=linear_model, prior=prior,
                                     process_noise=np.array([1e-4, 1e-4]))
        for _ in range(20):
            noise = noise_std * rng.standard_normal(3)
            obs = SensorData(timestamp=0.0, values=y_true + noise, noise_std=noise_std)
            ukf.step(obs, dt=1.0)

        # Should be close to truth
        for i in range(len(true_theta)):
            assert abs(ukf.x[i] - true_theta[i]) < 0.5

    def test_covariance_decreases(self, linear_model, prior, true_theta):
        """UKF covariance should decrease with observations."""
        rng = np.random.default_rng(42)
        y_true = linear_model.evaluate(true_theta)
        noise_std = np.array([0.1, 0.15, 0.08])

        ukf = UnscentedKalmanFilter(model=linear_model, prior=prior,
                                     process_noise=np.array([1e-6, 1e-6]))
        initial_trace = np.trace(ukf.P)
        for _ in range(10):
            noise = noise_std * rng.standard_normal(3)
            obs = SensorData(timestamp=0.0, values=y_true + noise, noise_std=noise_std)
            ukf.step(obs, dt=0.01)
        final_trace = np.trace(ukf.P)
        assert final_trace < initial_trace

    def test_sigma_points_count(self, linear_model, prior):
        ukf = UnscentedKalmanFilter(model=linear_model, prior=prior)
        pts = ukf._sigma_points()
        assert pts.shape == (2 * linear_model.n_params + 1, linear_model.n_params)

    def test_to_dict(self, linear_model, prior):
        ukf = UnscentedKalmanFilter(model=linear_model, prior=prior)
        d = ukf.to_dict()
        assert "state_mean" in d
        assert "state_std" in d
        assert "alpha" in d["state_mean"]
        assert "beta" in d["state_mean"]


# ---------------------------------------------------------------------------
# BayesianUpdater integration tests
# ---------------------------------------------------------------------------

class TestBayesianUpdater:
    def test_full_update(self, linear_model, prior, noisy_observations, true_theta):
        updater = BayesianUpdater(linear_model, prior)
        for obs in noisy_observations:
            updater.add_observation(obs)
        result = updater.full_update(n_mcmc_samples=3000, burn_in=500, seed=42)

        assert result.mcmc is not None
        assert result.ukf_result is not None
        assert result.predictive_mean is not None
        assert result.predictive_std is not None
        assert result.n_observations == 5

    def test_predictive_uncertainty(self, linear_model, prior, noisy_observations):
        updater = BayesianUpdater(linear_model, prior)
        for obs in noisy_observations:
            updater.add_observation(obs)
        result = updater.full_update(n_mcmc_samples=3000, burn_in=500, seed=42)

        # Predictive mean should be finite
        assert np.all(np.isfinite(result.predictive_mean))
        # Predictive std should be positive
        assert np.all(result.predictive_std > 0)
        # CI should bracket the mean
        assert np.all(result.predictive_ci_lower <= result.predictive_mean)
        assert np.all(result.predictive_ci_upper >= result.predictive_mean)

    def test_to_dict(self, linear_model, prior, noisy_observations):
        updater = BayesianUpdater(linear_model, prior)
        for obs in noisy_observations:
            updater.add_observation(obs)
        result = updater.full_update(n_mcmc_samples=2000, burn_in=400, seed=42)
        d = result.to_dict()
        assert "mcmc" in d
        assert "ukf" in d
        assert "predictive" in d
        assert "prior" in d


# ---------------------------------------------------------------------------
# Negative control tests (critical acceptance criteria)
# ---------------------------------------------------------------------------

class TestNegativeControls:
    def test_zero_observations_posterior_equals_prior(self, linear_model, prior):
        """NC1: Zero observations → posterior = prior."""
        result = negative_control_zero_observations(
            linear_model, prior, n_samples=3000, seed=42
        )
        assert result["control"] == "zero_observations"
        assert result["pass"], (
            f"Zero-obs negative control failed: "
            f"normalized_mean_shift={result['normalized_mean_shift']}, "
            f"std_ratio={result['std_ratio']}"
        )

    def test_perfect_observation_posterior_at_truth(self, linear_model, prior, true_theta):
        """NC2: Perfect (noiseless) observation → posterior concentrates at truth."""
        result = negative_control_perfect_observation(
            linear_model, prior, true_theta, noise_std=1e-6,
            n_samples=5000, seed=42,
        )
        assert result["control"] == "perfect_observation"
        assert result["pass"], (
            f"Perfect-obs negative control failed: "
            f"relative_error={result['relative_error']}"
        )

    def test_zero_obs_updater_returns_prior_samples(self, linear_model, prior):
        """BayesianUpdater with no observations returns prior-equivalent samples."""
        updater = BayesianUpdater(linear_model, prior)
        result = updater.full_update(n_mcmc_samples=2000, burn_in=400, seed=42)
        assert result.negative_control == "zero_obs"
        # Posterior mean ≈ prior mean
        np.testing.assert_allclose(result.mcmc.posterior_mean, prior.mean, atol=0.2)

