"""Bayesian model update mechanism for the digital twin.

Implements the full Bayesian inference chain:
    sensor data → likelihood → Bayesian correction → posterior distribution
    → prediction uncertainty quantification

Key components
--------------
ModelWrapper : abstract interface (parameters θ → outputs y)
SensorData   : abstract interface (timestamp + observation + noise std)
BayesianUpdater : orchestrates prior → likelihood → posterior sampling
    - MCMC via Metropolis-Hastings (posterior samples)
    - UKF as Gaussian approximation alternative
    - Predictive uncertainty: posterior params → predictive distribution

Design principles
-----------------
* Zero domain semantics: all physics enters via ModelWrapper injection.
* Negative controls: zero observations → prior unchanged;
  perfect observations → posterior concentrates at likelihood peak.
* The EKF in state_estimator.py provides online point estimates;
  this module provides full posterior distributions for offline/batch analysis.

References
----------
- Gelman et al., "Bayesian Data Analysis", 3rd ed., CRC 2013.
- Julier & Uhlmann, "Unscented filtering and nonlinear estimation", PIEEE 2004.
- Brooks et al., "Handbook of Markov Chain Monte Carlo", CRC 2011.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Abstract interfaces (zero domain semantics)
# ---------------------------------------------------------------------------

class ModelWrapper(ABC):
    """Abstract parameterized model: input parameters θ → output y.

    The model is domain-agnostic. Concrete implementations wrap
    surrogate registries, physics solvers, or analytical functions.
    """

    @abstractmethod
    def parameter_names(self) -> List[str]:
        """Names of the uncertain parameters θ."""

    @abstractmethod
    def output_names(self) -> List[str]:
        """Names of the model outputs y."""

    @abstractmethod
    def evaluate(self, theta: np.ndarray) -> np.ndarray:
        """Evaluate model at parameter vector θ, return output vector y."""

    @property
    def n_params(self) -> int:
        return len(self.parameter_names())

    @property
    def n_outputs(self) -> int:
        return len(self.output_names())


class SensorData:
    """Abstract sensor observation: timestamp + value + noise standard deviation.

    Attributes
    ----------
    timestamp : float
        Observation time (seconds or step index).
    values : np.ndarray
        Observed values (must align with model outputs or a subset).
    noise_std : np.ndarray
        Standard deviation of observation noise for each channel.
    output_indices : np.ndarray or None
        Which model output indices these observations correspond to.
        None means all outputs are observed.
    """

    def __init__(
        self,
        timestamp: float,
        values: np.ndarray,
        noise_std: np.ndarray,
        output_indices: Optional[np.ndarray] = None,
    ):
        self.timestamp = float(timestamp)
        self.values = np.asarray(values, dtype=float)
        self.noise_std = np.asarray(noise_std, dtype=float)
        if self.values.shape != self.noise_std.shape:
            raise ValueError("values and noise_std must have the same shape")
        if np.any(self.noise_std <= 0):
            raise ValueError("noise_std must be strictly positive")
        self.output_indices = (
            np.asarray(output_indices, dtype=int) if output_indices is not None else None
        )

    @property
    def n_obs(self) -> int:
        return len(self.values)


# ---------------------------------------------------------------------------
# Concrete ModelWrapper implementations
# ---------------------------------------------------------------------------

class LinearModel(ModelWrapper):
    """y = A @ θ + b (for testing and verification)."""

    def __init__(self, A: np.ndarray, b: Optional[np.ndarray] = None,
                 param_names: Optional[List[str]] = None,
                 output_names: Optional[List[str]] = None):
        self.A = np.asarray(A, dtype=float)
        self.b = np.asarray(b, dtype=float) if b is not None else np.zeros(self.A.shape[0])
        self._param_names = param_names or [f"theta_{i}" for i in range(self.A.shape[1])]
        self._output_names = output_names or [f"y_{i}" for i in range(self.A.shape[0])]

    def parameter_names(self) -> List[str]:
        return self._param_names

    def output_names(self) -> List[str]:
        return self._output_names

    def evaluate(self, theta: np.ndarray) -> np.ndarray:
        return self.A @ theta + self.b


class SurrogateModelWrapper(ModelWrapper):
    """Wraps the digital twin surrogate registry as a ModelWrapper.

    Maps uncertain parameters (resistance_bias, damage_scale, gm_offset)
    to surrogate outputs (R_total, damage_per_day, GM).
    """

    def __init__(self, registry, hull, base_inputs: Dict[str, float]):
        """
        Parameters
        ----------
        registry : SurrogateRegistry
            The surrogate model registry.
        hull : HullForm
            Hull geometry for physics models.
        base_inputs : dict
            Fixed inputs for the surrogates (V, ice_concentration, etc.).
        """
        self.registry = registry
        self.hull = hull
        self.base_inputs = base_inputs
        self._param_names = ["resistance_bias", "damage_scale", "gm_offset"]
        self._output_names = ["R_total_kN", "damage_per_day", "GM_m"]

    def parameter_names(self) -> List[str]:
        return self._param_names

    def output_names(self) -> List[str]:
        return self._output_names

    def evaluate(self, theta: np.ndarray) -> np.ndarray:
        r_bias, d_scale, gm_offset = theta
        # Resistance
        res = self.registry.safe_predict("resistance", self.base_inputs)["outputs"]
        R_total = float(res["R_total_kN"]) * r_bias
        # Fatigue / damage
        dmg = self.registry.safe_predict("fatigue", self.base_inputs)["outputs"]
        damage = float(dmg["damage_per_day"]) * d_scale
        # GM (offset from nominal)
        gm = 1.5 + gm_offset  # nominal GM ~ 1.5 m
        return np.array([R_total, damage, gm])


# ---------------------------------------------------------------------------
# Prior distributions
# ---------------------------------------------------------------------------

@dataclass
class GaussianPrior:
    """Independent Gaussian prior on each parameter."""
    mean: np.ndarray
    std: np.ndarray

    def __post_init__(self):
        self.mean = np.asarray(self.mean, dtype=float)
        self.std = np.asarray(self.std, dtype=float)
        if np.any(self.std <= 0):
            raise ValueError("prior std must be strictly positive")

    def log_pdf(self, theta: np.ndarray) -> float:
        """Log probability density (up to normalizing constant)."""
        z = (theta - self.mean) / self.std
        return -0.5 * np.sum(z ** 2)

    def sample(self, rng: np.random.Generator) -> np.ndarray:
        return self.mean + self.std * rng.standard_normal(len(self.mean))


# ---------------------------------------------------------------------------
# Likelihood
# ---------------------------------------------------------------------------

def gaussian_log_likelihood(
    model_output: np.ndarray,
    observation: SensorData,
) -> float:
    """Gaussian likelihood: log p(z | θ) = -0.5 Σ ((z_i - h_i(θ)) / σ_i)².

    Parameters
    ----------
    model_output : np.ndarray
        Model prediction y = M(θ).
    observation : SensorData
        Sensor observation with values and noise_std.

    Returns
    -------
    float : log-likelihood value.
    """
    if observation.output_indices is not None:
        h = model_output[observation.output_indices]
    else:
        h = model_output[: observation.n_obs]
    residual = observation.values - h
    return -0.5 * np.sum((residual / observation.noise_std) ** 2)


# ---------------------------------------------------------------------------
# MCMC: Metropolis-Hastings sampler
# ---------------------------------------------------------------------------

@dataclass
class MCMCResult:
    """Result of MCMC sampling."""
    samples: np.ndarray          # (n_samples, n_params)
    log_posterior: np.ndarray    # (n_samples,)
    acceptance_rate: float
    n_samples: int
    burn_in: int

    @property
    def posterior_mean(self) -> np.ndarray:
        return np.mean(self.samples, axis=0)

    @property
    def posterior_std(self) -> np.ndarray:
        return np.std(self.samples, axis=0)

    def credible_interval(self, level: float = 0.95) -> Tuple[np.ndarray, np.ndarray]:
        """Equal-tailed credible interval."""
        alpha = (1.0 - level) / 2.0
        lo = np.quantile(self.samples, alpha, axis=0)
        hi = np.quantile(self.samples, 1.0 - alpha, axis=0)
        return lo, hi

    def to_dict(self) -> Dict[str, Any]:
        lo, hi = self.credible_interval()
        return {
            "posterior_mean": self.posterior_mean.tolist(),
            "posterior_std": self.posterior_std.tolist(),
            "credible_interval_95": {
                "lower": lo.tolist(),
                "upper": hi.tolist(),
            },
            "acceptance_rate": self.acceptance_rate,
            "n_samples": self.n_samples,
            "burn_in": self.burn_in,
        }


def metropolis_hastings(
    model: ModelWrapper,
    observations: List[SensorData],
    prior: GaussianPrior,
    n_samples: int = 5000,
    burn_in: int = 1000,
    proposal_std: Optional[np.ndarray] = None,
    seed: int = 42,
) -> MCMCResult:
    """Metropolis-Hastings MCMC for posterior sampling.

    Parameters
    ----------
    model : ModelWrapper
        Parameterized forward model.
    observations : list of SensorData
        All sensor observations (likelihood is product over observations).
    prior : GaussianPrior
        Prior distribution on parameters.
    n_samples : int
        Total number of MCMC iterations (including burn-in).
    burn_in : int
        Number of initial samples to discard.
    proposal_std : np.ndarray or None
        Standard deviation of Gaussian proposal. Defaults to prior.std / 5.
    seed : int
        Random seed.

    Returns
    -------
    MCMCResult with posterior samples and diagnostics.
    """
    rng = np.random.default_rng(seed)
    n_params = model.n_params

    if proposal_std is None:
        proposal_std = prior.std / 5.0
    proposal_std = np.asarray(proposal_std, dtype=float)

    def log_posterior(theta: np.ndarray) -> float:
        lp = prior.log_pdf(theta)
        if not math.isfinite(lp):
            return -np.inf
        try:
            y = model.evaluate(theta)
        except Exception:
            return -np.inf
        ll = 0.0
        for obs in observations:
            ll += gaussian_log_likelihood(y, obs)
        return lp + ll

    # Initialize from prior
    theta_current = prior.sample(rng)
    lp_current = log_posterior(theta_current)

    samples = np.zeros((n_samples, n_params))
    log_posts = np.zeros(n_samples)
    accepted = 0

    for i in range(n_samples):
        theta_proposal = theta_current + proposal_std * rng.standard_normal(n_params)
        lp_proposal = log_posterior(theta_proposal)

        log_alpha = lp_proposal - lp_current
        if math.isfinite(log_alpha) and (log_alpha >= 0 or rng.uniform() < math.exp(log_alpha)):
            theta_current = theta_proposal
            lp_current = lp_proposal
            accepted += 1

        samples[i] = theta_current
        log_posts[i] = lp_current

    acceptance_rate = accepted / n_samples
    return MCMCResult(
        samples=samples[burn_in:],
        log_posterior=log_posts[burn_in:],
        acceptance_rate=acceptance_rate,
        n_samples=n_samples - burn_in,
        burn_in=burn_in,
    )


# ---------------------------------------------------------------------------
# UKF: Unscented Kalman Filter (Gaussian approximation)
# ---------------------------------------------------------------------------

@dataclass
class UKFResult:
    """Result of UKF estimation at one time step."""
    state_mean: np.ndarray
    state_cov: np.ndarray
    sigma_points: np.ndarray
    predicted_obs: np.ndarray
    innovation: Optional[np.ndarray] = None
    kalman_gain: Optional[np.ndarray] = None

    @property
    def state_std(self) -> np.ndarray:
        return np.sqrt(np.diag(self.state_cov))

    def to_dict(self, param_names: Optional[List[str]] = None) -> Dict[str, Any]:
        names = param_names or [f"theta_{i}" for i in range(len(self.state_mean))]
        return {
            "state_mean": {n: float(v) for n, v in zip(names, self.state_mean)},
            "state_std": {n: float(v) for n, v in zip(names, self.state_std)},
        }


class UnscentedKalmanFilter:
    """Unscented Kalman Filter for nonlinear Bayesian estimation.

    Implements the standard UKF with scaled unscented transform.
    State vector = model parameters θ.

    Parameters
    ----------
    model : ModelWrapper
        Forward model mapping θ → y.
    prior : GaussianPrior
        Initial belief (mean, covariance = diag(std²)).
    process_noise : np.ndarray
        Process noise standard deviation per parameter.
    alpha : float
        UKF spread parameter (default 1e-3).
    beta : float
        UKF prior knowledge parameter (default 2.0 for Gaussian).
    kappa : float
        Secondary scaling parameter (default 0.0).
    """

    def __init__(
        self,
        model: ModelWrapper,
        prior: GaussianPrior,
        process_noise: Optional[np.ndarray] = None,
        alpha: float = 1e-3,
        beta: float = 2.0,
        kappa: float = 0.0,
    ):
        self.model = model
        self.n = model.n_params
        self.x = prior.mean.copy()
        self.P = np.diag(prior.std ** 2)
        self.process_noise = (
            np.asarray(process_noise, dtype=float)
            if process_noise is not None
            else np.ones(self.n) * 1e-4
        )
        # UKF parameters
        self.alpha = alpha
        self.beta = beta
        self.kappa = kappa
        self.lam = alpha ** 2 * (self.n + kappa) - self.n
        # Weights
        self._compute_weights()

    def _compute_weights(self) -> None:
        n = self.n
        lam = self.lam
        # Mean weights
        self.Wm = np.zeros(2 * n + 1)
        self.Wm[0] = lam / (n + lam)
        self.Wm[1:] = 1.0 / (2.0 * (n + lam))
        # Covariance weights
        self.Wc = np.zeros(2 * n + 1)
        self.Wc[0] = lam / (n + lam) + (1.0 - self.alpha ** 2 + self.beta)
        self.Wc[1:] = 1.0 / (2.0 * (n + lam))

    def _sigma_points(self) -> np.ndarray:
        """Generate 2n+1 sigma points."""
        n = self.n
        pts = np.zeros((2 * n + 1, n))
        pts[0] = self.x
        try:
            sqrtP = np.linalg.cholesky((n + self.lam) * self.P)
        except np.linalg.LinAlgError:
            # Fallback: add small regularization
            sqrtP = np.linalg.cholesky((n + self.lam) * self.P + 1e-8 * np.eye(n))
        for i in range(n):
            pts[i + 1] = self.x + sqrtP[i]
            pts[n + i + 1] = self.x - sqrtP[i]
        return pts

    def predict(self, dt: float = 1.0) -> UKFResult:
        """Predict step: propagate state and covariance."""
        self.P = self.P + np.diag(self.process_noise ** 2) * dt
        return UKFResult(state_mean=self.x.copy(), state_cov=self.P.copy(),
                         sigma_points=self._sigma_points(), predicted_obs=np.array([]))

    def update(self, observation: SensorData) -> UKFResult:
        """Update step: incorporate observation via unscented transform."""
        sigma_pts = self._sigma_points()
        n_sigma = len(sigma_pts)

        # Transform sigma points through measurement model
        y_sigma = np.zeros((n_sigma, observation.n_obs))
        for i, sp in enumerate(sigma_pts):
            y_full = self.model.evaluate(sp)
            if observation.output_indices is not None:
                y_sigma[i] = y_full[observation.output_indices]
            else:
                y_sigma[i] = y_full[: observation.n_obs]

        # Predicted observation mean
        y_pred = np.sum(self.Wm[:, None] * y_sigma, axis=0)

        # Innovation covariance
        Pyy = np.zeros((observation.n_obs, observation.n_obs))
        for i in range(n_sigma):
            dy = y_sigma[i] - y_pred
            Pyy += self.Wc[i] * np.outer(dy, dy)
        R = np.diag(observation.noise_std ** 2)
        Pyy = Pyy + R

        # Cross-covariance
        Pxy = np.zeros((self.n, observation.n_obs))
        for i in range(n_sigma):
            dx = sigma_pts[i] - self.x
            dy = y_sigma[i] - y_pred
            Pxy += self.Wc[i] * np.outer(dx, dy)

        # Kalman gain
        try:
            K = Pxy @ np.linalg.inv(Pyy)
        except np.linalg.LinAlgError:
            return UKFResult(state_mean=self.x.copy(), state_cov=self.P.copy(),
                             sigma_points=sigma_pts, predicted_obs=y_pred)

        # Innovation
        innovation = observation.values - y_pred

        # Update state and covariance
        self.x = self.x + K @ innovation
        self.P = self.P - K @ Pyy @ K.T
        self.P = 0.5 * (self.P + self.P.T)  # symmetrize
        # Ensure positive semi-definite
        eigvals = np.linalg.eigvalsh(self.P)
        if np.min(eigvals) < 0:
            self.P += (-np.min(eigvals) + 1e-10) * np.eye(self.n)
        self.P = 0.5 * (self.P + self.P.T)  # symmetrize

        return UKFResult(
            state_mean=self.x.copy(),
            state_cov=self.P.copy(),
            sigma_points=sigma_pts,
            predicted_obs=y_pred,
            innovation=innovation,
            kalman_gain=K,
        )

    def step(self, observation: SensorData, dt: float = 1.0) -> UKFResult:
        """Combined predict + update step."""
        self.predict(dt)
        return self.update(observation)

    def to_dict(self) -> Dict[str, Any]:
        names = self.model.parameter_names()
        return {
            "state_mean": {n: float(v) for n, v in zip(names, self.x)},
            "state_std": {n: float(math.sqrt(max(p, 0.0))) for n, p in zip(names, np.diag(self.P))},
        }


# ---------------------------------------------------------------------------
# Bayesian Updater: orchestrator
# ---------------------------------------------------------------------------

@dataclass
class BayesianUpdateResult:
    """Complete result of Bayesian model update."""
    # Posterior from MCMC
    mcmc: Optional[MCMCResult] = None
    # Posterior from UKF (Gaussian approximation)
    ukf_result: Optional[UKFResult] = None
    # Predictive uncertainty
    predictive_mean: Optional[np.ndarray] = None
    predictive_std: Optional[np.ndarray] = None
    predictive_ci_lower: Optional[np.ndarray] = None
    predictive_ci_upper: Optional[np.ndarray] = None
    # Diagnostics
    prior_mean: Optional[np.ndarray] = None
    prior_std: Optional[np.ndarray] = None
    n_observations: int = 0
    negative_control: str = "none"  # "zero_obs" | "perfect_obs" | "none"

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "n_observations": self.n_observations,
            "negative_control": self.negative_control,
        }
        if self.mcmc is not None:
            d["mcmc"] = self.mcmc.to_dict()
        if self.ukf_result is not None:
            d["ukf"] = self.ukf_result.to_dict()
        if self.predictive_mean is not None:
            d["predictive"] = {
                "mean": self.predictive_mean.tolist(),
                "std": self.predictive_std.tolist() if self.predictive_std is not None else None,
                "ci_95_lower": self.predictive_ci_lower.tolist() if self.predictive_ci_lower is not None else None,
                "ci_95_upper": self.predictive_ci_upper.tolist() if self.predictive_ci_upper is not None else None,
            }
        if self.prior_mean is not None:
            d["prior"] = {
                "mean": self.prior_mean.tolist(),
                "std": self.prior_std.tolist(),
            }
        return d


class BayesianUpdater:
    """Orchestrates the full Bayesian update pipeline.

    Workflow:
    1. Specify prior on model parameters
    2. Collect sensor observations
    3. Compute likelihood
    4. Sample posterior via MCMC (or approximate via UKF)
    5. Propagate posterior through model for predictive uncertainty

    Parameters
    ----------
    model : ModelWrapper
        The parameterized forward model.
    prior : GaussianPrior
        Prior distribution on parameters.
    """

    def __init__(self, model: ModelWrapper, prior: GaussianPrior):
        self.model = model
        self.prior = prior
        self.observations: List[SensorData] = []
        self.ukf: Optional[UnscentedKalmanFilter] = None

    def add_observation(self, obs: SensorData) -> None:
        """Add a sensor observation to the dataset."""
        self.observations.append(obs)

    def clear_observations(self) -> None:
        """Clear all observations (for negative control testing)."""
        self.observations.clear()

    def compute_log_likelihood(self, theta: np.ndarray) -> float:
        """Compute total log-likelihood at parameter θ."""
        ll = 0.0
        y = self.model.evaluate(theta)
        for obs in self.observations:
            ll += gaussian_log_likelihood(y, obs)
        return ll

    def compute_log_posterior(self, theta: np.ndarray) -> float:
        """Compute unnormalized log-posterior at parameter θ."""
        return self.prior.log_pdf(theta) + self.compute_log_likelihood(theta)

    def sample_posterior(
        self,
        n_samples: int = 5000,
        burn_in: int = 1000,
        proposal_std: Optional[np.ndarray] = None,
        seed: int = 42,
    ) -> MCMCResult:
        """Sample posterior distribution via MCMC."""
        return metropolis_hastings(
            model=self.model,
            observations=self.observations,
            prior=self.prior,
            n_samples=n_samples,
            burn_in=burn_in,
            proposal_std=proposal_std,
            seed=seed,
        )

    def run_ukf(
        self,
        process_noise: Optional[np.ndarray] = None,
    ) -> UnscentedKalmanFilter:
        """Run UKF sequentially through all observations."""
        ukf = UnscentedKalmanFilter(
            model=self.model,
            prior=self.prior,
            process_noise=process_noise,
        )
        for i, obs in enumerate(self.observations):
            dt = 1.0
            if i > 0:
                dt = obs.timestamp - self.observations[i - 1].timestamp
            ukf.step(obs, dt=max(dt, 0.01))
        self.ukf = ukf
        return ukf

    def predictive_distribution(
        self,
        posterior_samples: np.ndarray,
        n_mc: int = 100,
        seed: int = 123,
    ) -> Dict[str, np.ndarray]:
        """Propagate posterior parameter uncertainty through the model.

        For each posterior sample, evaluate the model and compute
        predictive statistics (mean, std, credible intervals).

        Parameters
        ----------
        posterior_samples : np.ndarray
            (n_samples, n_params) array of posterior parameter samples.
        n_mc : int
            Number of posterior samples to use (subsample if too many).
        seed : int
            Random seed for subsampling.

        Returns
        -------
        dict with keys: mean, std, ci_lower, ci_upper (each shape (n_outputs,))
        """
        rng = np.random.default_rng(seed)
        n_total = len(posterior_samples)
        if n_total > n_mc:
            idx = rng.choice(n_total, n_mc, replace=False)
            samples = posterior_samples[idx]
        else:
            samples = posterior_samples

        n_out = self.model.n_outputs
        predictions = np.zeros((len(samples), n_out))
        for i, theta in enumerate(samples):
            predictions[i] = self.model.evaluate(theta)

        return {
            "mean": np.mean(predictions, axis=0),
            "std": np.std(predictions, axis=0),
            "ci_lower": np.quantile(predictions, 0.025, axis=0),
            "ci_upper": np.quantile(predictions, 0.975, axis=0),
            "predictions": predictions,
        }

    def full_update(
        self,
        n_mcmc_samples: int = 5000,
        burn_in: int = 1000,
        seed: int = 42,
    ) -> BayesianUpdateResult:
        """Execute the full Bayesian update pipeline.

        Returns BayesianUpdateResult with MCMC posterior, UKF approximation,
        and predictive uncertainty.
        """
        result = BayesianUpdateResult(
            prior_mean=self.prior.mean.copy(),
            prior_std=self.prior.std.copy(),
            n_observations=len(self.observations),
        )

        if not self.observations:
            # Negative control: zero observations → posterior = prior
            result.negative_control = "zero_obs"
            rng = np.random.default_rng(seed)
            prior_samples = np.array([self.prior.sample(rng) for _ in range(n_mcmc_samples - burn_in)])
            result.mcmc = MCMCResult(
                samples=prior_samples,
                log_posterior=np.array([self.prior.log_pdf(s) for s in prior_samples]),
                acceptance_rate=1.0,
                n_samples=n_mcmc_samples - burn_in,
                burn_in=burn_in,
            )
            # Run UKF with no updates
            self.ukf = UnscentedKalmanFilter(self.model, self.prior)
            result.ukf_result = UKFResult(
                state_mean=self.ukf.x.copy(),
                state_cov=self.ukf.P.copy(),
                sigma_points=self.ukf._sigma_points(),
                predicted_obs=np.array([]),
            )
            # Predictive from prior
            pred = self.predictive_distribution(prior_samples, seed=seed + 1)
            result.predictive_mean = pred["mean"]
            result.predictive_std = pred["std"]
            result.predictive_ci_lower = pred["ci_lower"]
            result.predictive_ci_upper = pred["ci_upper"]
            return result

        # MCMC posterior
        mcmc_result = self.sample_posterior(
            n_samples=n_mcmc_samples, burn_in=burn_in, seed=seed
        )
        result.mcmc = mcmc_result

        # UKF approximation
        ukf = self.run_ukf()
        result.ukf_result = UKFResult(
            state_mean=ukf.x.copy(),
            state_cov=ukf.P.copy(),
            sigma_points=ukf._sigma_points(),
            predicted_obs=np.array([]),
        )

        # Predictive uncertainty from MCMC posterior
        pred = self.predictive_distribution(mcmc_result.samples, seed=seed + 1)
        result.predictive_mean = pred["mean"]
        result.predictive_std = pred["std"]
        result.predictive_ci_lower = pred["ci_lower"]
        result.predictive_ci_upper = pred["ci_upper"]

        return result


# ---------------------------------------------------------------------------
# Negative control helpers
# ---------------------------------------------------------------------------

def negative_control_zero_observations(
    model: ModelWrapper,
    prior: GaussianPrior,
    n_samples: int = 2000,
    seed: int = 42,
) -> Dict[str, Any]:
    """Negative control: with zero observations, posterior must equal prior.

    Returns dict comparing prior and posterior statistics.
    """
    updater = BayesianUpdater(model, prior)
    result = updater.full_update(n_mcmc_samples=n_samples, burn_in=n_samples // 5, seed=seed)

    prior_mean = prior.mean
    prior_std = prior.std
    post_mean = result.mcmc.posterior_mean
    post_std = result.mcmc.posterior_std

    # Posterior should match prior within sampling noise
    mean_shift = np.abs(post_mean - prior_mean) / prior_std  # normalized shift
    std_ratio = post_std / prior_std

    return {
        "control": "zero_observations",
        "prior_mean": prior_mean.tolist(),
        "prior_std": prior_std.tolist(),
        "posterior_mean": post_mean.tolist(),
        "posterior_std": post_std.tolist(),
        "normalized_mean_shift": mean_shift.tolist(),
        "std_ratio": std_ratio.tolist(),
        "pass": bool(np.all(mean_shift < 0.3) and np.all(std_ratio > 0.7) and np.all(std_ratio < 1.3)),
    }


def negative_control_perfect_observation(
    model: ModelWrapper,
    prior: GaussianPrior,
    true_theta: np.ndarray,
    noise_std: float = 1e-6,
    n_samples: int = 3000,
    seed: int = 42,
) -> Dict[str, Any]:
    """Negative control: with perfect (noiseless) observations, posterior
    should concentrate at the true parameter value.

    Returns dict comparing posterior to truth.
    """
    # Generate perfect observation
    y_true = model.evaluate(true_theta)
    obs = SensorData(
        timestamp=0.0,
        values=y_true,
        noise_std=np.full_like(y_true, noise_std),
    )

    updater = BayesianUpdater(model, prior)
    updater.add_observation(obs)
    result = updater.full_update(n_mcmc_samples=n_samples, burn_in=n_samples // 3, seed=seed)

    post_mean = result.mcmc.posterior_mean
    post_std = result.mcmc.posterior_std

    # Posterior should concentrate near truth
    error = np.abs(post_mean - true_theta)
    relative_error = error / (np.abs(true_theta) + 1e-10)

    return {
        "control": "perfect_observation",
        "true_theta": true_theta.tolist(),
        "posterior_mean": post_mean.tolist(),
        "posterior_std": post_std.tolist(),
        "absolute_error": error.tolist(),
        "relative_error": relative_error.tolist(),
        "pass": bool(np.all(relative_error < 0.1)),  # within 10% of truth
    }
