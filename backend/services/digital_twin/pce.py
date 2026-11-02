"""Polynomial chaos expansion (PCE) surrogate engine.

Basis: tensor-product Legendre polynomials on inputs mapped to ``[-1, 1]``
(uniform / bounded design space).  Coefficients are obtained by ordinary least
squares on a space-filling sample set; prediction is a single basis evaluation
plus a dot product, i.e. O(P) with P the number of basis terms (~50), which is
the reason the surrogates meet the <100 ms latency requirement by orders of
magnitude.

The implementation is intentionally dependency-light (numpy only) and
deterministic given the seed.
"""
from __future__ import annotations

import itertools
from typing import Callable, Dict, List, Mapping, Sequence, Tuple

import numpy as np


def _legendre_all(x: np.ndarray, degree: int) -> np.ndarray:
    """Return (n, degree+1) array of Legendre polynomials P_0..P_degree at x."""
    n = x.size
    out = np.empty((n, degree + 1), dtype=float)
    out[:, 0] = 1.0
    if degree >= 1:
        out[:, 1] = x
    for k in range(1, degree):
        out[:, k + 1] = ((2 * k + 1) * x * out[:, k] - k * out[:, k - 1]) / (k + 1)
    return out


class PolynomialChaos:
    """Least-squares PCE mapping R^d -> R^m."""

    def __init__(
        self,
        names: Sequence[str],
        lower: Sequence[float],
        upper: Sequence[float],
        degree: int = 3,
    ):
        if len(names) != len(lower) or len(names) != len(upper):
            raise ValueError("names / lower / upper length mismatch")
        self.names = list(names)
        self.lower = np.asarray(lower, dtype=float)
        self.upper = np.asarray(upper, dtype=float)
        if np.any(self.upper <= self.lower):
            raise ValueError("upper bounds must exceed lower bounds")
        self.degree = int(degree)
        self.indices: List[Tuple[int, ...]] = [
            idx for idx in itertools.product(range(self.degree + 1), repeat=len(names))
            if sum(idx) <= self.degree
        ]
        self.coefficients: Dict[str, np.ndarray] = {}
        self.output_names: List[str] = []
        self.fit_report: Dict[str, object] = {}

    # -- featurisation ------------------------------------------------------
    def _to_unit(self, X: np.ndarray) -> np.ndarray:
        return 2.0 * (X - self.lower) / (self.upper - self.lower) - 1.0

    def _design(self, X: np.ndarray) -> np.ndarray:
        Xi = self._to_unit(np.atleast_2d(X))
        polys = [_legendre_all(Xi[:, j], self.degree) for j in range(len(self.names))]
        cols = []
        for idx in self.indices:
            col = np.ones(Xi.shape[0])
            for j, k in enumerate(idx):
                col = col * polys[j][:, k]
            cols.append(col)
        return np.column_stack(cols)

    # -- fitting ------------------------------------------------------------
    def fit(
        self,
        func: Callable[[np.ndarray], np.ndarray],
        n_samples: int = 320,
        seed: int = 20261031,
        ridge: float = 1e-10,
        n_validation: int = 64,
        output_names: Sequence[str] | None = None,
    ) -> "PolynomialChaos":
        """Fit coefficients; ``func`` maps (n, d) -> (n, m)."""
        d = len(self.names)
        rng = np.random.default_rng(seed)
        X = self.lower + (self.upper - self.lower) * rng.random((n_samples, d))
        Y = np.atleast_2d(np.asarray(func(X), dtype=float))
        if Y.shape[0] != X.shape[0]:
            Y = Y.reshape(X.shape[0], -1)
        m = Y.shape[1]
        A = self._design(X)
        coef, *_ = np.linalg.lstsq(A, Y, rcond=ridge)
        self.coefficients = {f"y{i}": coef[:, i] for i in range(m)}

        # hold-out diagnostics
        Xv = self.lower + (self.upper - self.lower) * rng.random((n_validation, d))
        Yv = np.atleast_2d(np.asarray(func(Xv), dtype=float)).reshape(n_validation, m)
        Av = self._design(Xv)
        Yp = Av @ coef
        ss_res = np.sum((Yv - Yp) ** 2, axis=0)
        ss_tot = np.sum((Yv - Yv.mean(axis=0)) ** 2, axis=0)
        r2 = 1.0 - ss_res / np.maximum(ss_tot, 1e-30)
        rel = np.abs(Yp - Yv) / np.maximum(np.abs(Yv), 1e-12)
        if output_names is not None:
            self.output_names = list(output_names)
        else:
            self.output_names = [f"y{i}" for i in range(m)]
        self.fit_report = {
            "n_basis": len(self.indices),
            "degree": self.degree,
            "n_samples": int(n_samples),
            "outputs": self.output_names,
            "r2": {self.output_names[i]: round(float(r2[i]), 6) for i in range(m)},
            "max_rel_error_holdout": {
                self.output_names[i]: round(float(np.max(rel[:, i])), 6) for i in range(m)
            },
            "mean_rel_error_holdout": {
                self.output_names[i]: round(float(np.mean(rel[:, i])), 6) for i in range(m)
            },
        }
        return self

    # -- evaluation ---------------------------------------------------------
    def predict_matrix(self, X: np.ndarray) -> np.ndarray:
        A = self._design(X)
        cols = [A @ self.coefficients[f"y{i}"] for i in range(len(self.output_names))]
        return np.column_stack(cols)

    def predict(self, values: Mapping[str, float]) -> Dict[str, float]:
        x = np.array([[float(values[n]) for n in self.names]], dtype=float)
        y = self.predict_matrix(x)[0]
        return {name: float(y[i]) for i, name in enumerate(self.output_names)}

    def predict_std(
        self,
        values: Mapping[str, float],
        sigma_input: Sequence[float] | None = None,
        rel_step: float = 1e-6,
    ) -> Dict[str, float]:
        """First-order (delta-method) propagation of input std onto the outputs.

        ``sigma_input`` is the **absolute** standard deviation of each input in the
        same units as ``lower`` / ``upper`` (default: 1 % of the corresponding input
        range).  The result lives in the **fitted output space**: if the model was
        fitted on ``log10(y)`` the returned std is in decades and must be converted
        before it can be reported next to a physical mean -- see
        :meth:`PCESurrogate.predict_with_uncertainty`.

        The finite-difference step is scaled by the input range (``rel_step``) and
        clipped to the design box, so the estimate does not depend on the absolute
        magnitude of the input units and never extrapolates outside the surrogate
        domain.
        """
        base = self.predict(values)  # validates the input key set
        sig = (np.asarray(sigma_input, dtype=float) if sigma_input is not None
               else 0.01 * (self.upper - self.lower))
        if sig.size != len(self.names):
            raise ValueError("sigma_input length must match the number of inputs")
        grad = np.zeros((len(self.names), len(self.output_names)))
        for j, pname in enumerate(self.names):
            h = abs(rel_step) * (self.upper[j] - self.lower[j])
            if h <= 0.0:
                continue
            lo_v = min(max(values[pname] - h, self.lower[j]), self.upper[j])
            hi_v = min(max(values[pname] + h, self.lower[j]), self.upper[j])
            span = hi_v - lo_v
            if span <= 0.0:
                continue
            lo = dict(values); lo[pname] = lo_v
            hi = dict(values); hi[pname] = hi_v
            d_lo, d_hi = self.predict(lo), self.predict(hi)
            for i, name in enumerate(self.output_names):
                grad[j, i] = (d_hi[name] - d_lo[name]) / span
        var = (grad * sig[:, None]) ** 2
        return {name: float(np.sqrt(np.sum(var[:, i])))
                for i, name in enumerate(self.output_names)}

    def to_dict(self) -> Dict[str, object]:
        return {
            "inputs": self.names,
            "bounds": [[float(self.lower[j]), float(self.upper[j])]
                       for j in range(len(self.names))],
            "degree": self.degree,
            "n_basis": len(self.indices),
            "fit_report": self.fit_report,
        }