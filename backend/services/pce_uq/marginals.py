"""Marginal input laws and the isoprobabilistic transform to standard variables.

Only what the PCE needs: a sampling map (unit hypercube -> physical), an inverse
map into the standard variable of the matching orthonormal basis, and the
probability-weighted quadrature rule.  Families supported: ``uniform``,
``normal`` (truncated at ``+-k sigma`` so the physical domain stays finite) and
``lognormal`` (specified by its physical mean/CV).

Like :mod:`.basis` this module is domain free; the *use* of a given marginal for
a given physical quantity is injected by the case specification, never coded
here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Sequence, Tuple

import numpy as np
from scipy.stats import norm

MARGINAL_FAMILIES: Tuple[str, ...] = ("uniform", "normal", "lognormal")


@dataclass(frozen=True)
class Marginal:
    """One uncertain input.

    ``family``:
      * ``uniform``   -- ``lower``/``upper`` given directly;
      * ``normal``    -- ``mean``/``std`` (truncated at ``truncate_sigma`` sigma);
      * ``lognormal`` -- ``mean``/``cv`` in *physical* units (log-space moments
        derived internally).
    """

    name: str
    family: str
    lower: float = float("nan")
    upper: float = float("nan")
    mean: float = float("nan")
    std: float = float("nan")
    cv: float = float("nan")
    truncate_sigma: float = 5.0

    # -- construction helpers ------------------------------------------------
    @staticmethod
    def uniform(name: str, lower: float, upper: float) -> "Marginal":
        if not (float(upper) > float(lower)):
            raise ValueError(f"{name}: uniform upper must exceed lower")
        return Marginal(name=name, family="uniform", lower=float(lower), upper=float(upper))

    @staticmethod
    def normal(name: str, mean: float, std: float, truncate_sigma: float = 5.0) -> "Marginal":
        if not (float(std) > 0.0):
            raise ValueError(f"{name}: normal std must be positive")
        return Marginal(name=name, family="normal", mean=float(mean), std=float(std),
                        truncate_sigma=float(truncate_sigma))

    @staticmethod
    def lognormal(name: str, mean: float, cv: float, truncate_sigma: float = 5.0) -> "Marginal":
        if not (float(mean) > 0.0):
            raise ValueError(f"{name}: lognormal mean must be positive")
        if not (float(cv) > 0.0):
            raise ValueError(f"{name}: lognormal cv must be positive")
        return Marginal(name=name, family="lognormal", mean=float(mean), cv=float(cv),
                        truncate_sigma=float(truncate_sigma))

    @staticmethod
    def from_spec(spec: Dict[str, Any]) -> "Marginal":
        """Build from a case-specification dict (JSON friendly)."""
        fam = str(spec["family"]).lower()
        name = str(spec["name"])
        if fam == "uniform":
            return Marginal.uniform(name, spec["lower"], spec["upper"])
        if fam == "normal":
            return Marginal.normal(name, spec["mean"], spec["std"],
                                   spec.get("truncate_sigma", 5.0))
        if fam == "lognormal":
            return Marginal.lognormal(name, spec["mean"], spec["cv"],
                                      spec.get("truncate_sigma", 5.0))
        raise ValueError(f"{name}: unknown marginal family {fam!r}")

    # -- derived quantities --------------------------------------------------
    @property
    def basis_family(self) -> str:
        return "legendre" if self.family == "uniform" else "hermite"

    @property
    def _log_moments(self) -> Tuple[float, float]:
        sig2 = math.log1p(self.cv ** 2)
        return math.log(self.mean) - 0.5 * sig2, math.sqrt(sig2)

    def bounds(self) -> Tuple[float, float]:
        """Finite physical support used for sampling and reporting."""
        if self.family == "uniform":
            return self.lower, self.upper
        k = float(self.truncate_sigma)
        if self.family == "normal":
            return self.mean - k * self.std, self.mean + k * self.std
        mu, sg = self._log_moments
        return math.exp(mu - k * sg), math.exp(mu + k * sg)

    def moments(self) -> Tuple[float, float]:
        """(mean, std) of the *untruncated* law -- the reference moments."""
        if self.family == "uniform":
            half = 0.5 * (self.upper - self.lower)
            return 0.5 * (self.lower + self.upper), half / math.sqrt(3.0)
        if self.family == "normal":
            return self.mean, self.std
        return self.mean, self.mean * self.cv

    # -- maps ----------------------------------------------------------------
    def from_unit(self, u: np.ndarray) -> np.ndarray:
        """Map uniforms on ``[0, 1]`` (LHS coordinates) onto the physical law."""
        u = np.clip(np.asarray(u, dtype=float), 0.0, 1.0)
        if self.family == "uniform":
            return self.lower + u * (self.upper - self.lower)
        k = float(self.truncate_sigma)
        lo_cdf, hi_cdf = norm.cdf(-k), norm.cdf(k)
        p = lo_cdf + u * (hi_cdf - lo_cdf)
        z = norm.ppf(p)
        if self.family == "normal":
            return self.mean + self.std * z
        mu, sg = self._log_moments
        return np.exp(mu + sg * z)

    def to_standard(self, x: np.ndarray) -> np.ndarray:
        """Physical value -> standard variable of the matching orthonormal basis."""
        x = np.asarray(x, dtype=float)
        if self.family == "uniform":
            return 2.0 * (x - self.lower) / (self.upper - self.lower) - 1.0
        if self.family == "normal":
            return (x - self.mean) / self.std
        mu, sg = self._log_moments
        return (np.log(np.maximum(x, 1e-300)) - mu) / sg

    def as_dict(self) -> Dict[str, Any]:
        lo, hi = self.bounds()
        mu, sd = self.moments()
        return {
            "name": self.name,
            "family": self.family,
            "basis_family": self.basis_family,
            "bounds": [float(lo), float(hi)],
            "mean": float(mu),
            "std": float(sd),
            "cv": float(sd / mu) if mu else float("nan"),
        }


def marginals_from_spec(specs: Sequence[Dict[str, Any]]) -> Tuple[Marginal, ...]:
    out = tuple(Marginal.from_spec(s) for s in specs)
    if not out:
        raise ValueError("at least one uncertain input is required")
    names = [m.name for m in out]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate input names: {names}")
    return out


def to_physical(marginals: Sequence[Marginal], U: np.ndarray) -> np.ndarray:
    """``(n, d)`` unit-hypercube coordinates -> physical parameter matrix."""
    U = np.atleast_2d(np.asarray(U, dtype=float))
    if U.shape[1] != len(marginals):
        raise ValueError("unit sample matrix and marginal list disagree on dimension")
    return np.column_stack([m.from_unit(U[:, j]) for j, m in enumerate(marginals)])


def to_standard_matrix(marginals: Sequence[Marginal], X: np.ndarray) -> np.ndarray:
    X = np.atleast_2d(np.asarray(X, dtype=float))
    return np.column_stack([m.to_standard(X[:, j]) for j, m in enumerate(marginals)])

def from_standard(marginal: Marginal, z: np.ndarray) -> np.ndarray:
    """Standard variable -> physical value, using the *untruncated* law.

    Quadrature nodes are allowed to sit further out than the sampling truncation
    (Gauss-Hermite nodes reach +-5 sigma and beyond); they weight the orthonormal
    basis consistently, so the inverse map must not rescale the tails.
    """
    z = np.asarray(z, dtype=float)
    if marginal.family == "uniform":
        return marginal.lower + 0.5 * (z + 1.0) * (marginal.upper - marginal.lower)
    if marginal.family == "normal":
        return marginal.mean + marginal.std * z
    mu, sg = marginal._log_moments
    return np.exp(mu + sg * z)
