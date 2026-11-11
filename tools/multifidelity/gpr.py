"""Gaussian-process regression on an anisotropic squared-exponential kernel.

This is the single-fidelity baseline of the framework ("spend the whole budget on the
expensive level and krig").  It is also the residual model used by the autoregressive
co-kriging stack.

Numerics
--------
* inputs are standardised per dimension, targets are standardised to zero mean /
  unit variance, so all hyper-parameters are dimensionless and comparable;
* kernel ``k(a, b) = sigma_f^2 exp(-0.5 * sum_i ((a_i - b_i) / l_i)^2)``;
* hyper-parameters ``(log l_i, log sigma_f)`` are fitted by maximising the log
  marginal likelihood with L-BFGS-B (a few random restarts, seeded);
* the nugget is a fixed small fraction of the target variance (``nugget``); it is the
  jitter that keeps the Cholesky factorisation stable for clustered designs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np

try:  # scipy's triangular solve is ~1000x faster than np.linalg.solve on small
    from scipy.linalg import solve_triangular as _solve_triangular  # dense BLAS path
except Exception:  # pragma: no cover - numpy-only fallback
    _solve_triangular = None

__all__ = ["GPR"]

_LOG_2PI = float(np.log(2.0 * np.pi))


def _tri_solve(L: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Solve ``L L^T x = b`` with ``L`` lower-triangular (never forms the inverse)."""
    if _solve_triangular is not None:
        y = _solve_triangular(L, b, lower=True, check_finite=False)
        return _solve_triangular(L.T, y, lower=False, check_finite=False)
    return np.linalg.solve(L.T, np.linalg.solve(L, b))


def _sqdist(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Pairwise squared Euclidean distance in *scaled* units (A: n×d, B: m×d)."""
    return (
        np.sum(A * A, axis=1)[:, None]
        + np.sum(B * B, axis=1)[None, :]
        - 2.0 * A @ B.T
    )


@dataclass
class GPR:
    """Small, dependency-light GP regressor (numpy + scipy.optimize only)."""

    nugget: float = 1e-8
    optimize: bool = True
    restarts: int = 1
    max_iter: int = 250
    seed: int = 0
    lengthscale: Optional[np.ndarray] = None
    sigma_f_init: Optional[float] = None
    trend_order: int = 1  # 0 = constant mean, 1 = affine (linear) mean function
    hyper_max_points: int = 120  # hyper-parameters are fitted on at most this many points

    # fitted state -------------------------------------------------------
    X_: Optional[np.ndarray] = None
    y_: Optional[np.ndarray] = None
    _x_mean: Optional[np.ndarray] = None
    _x_std: Optional[np.ndarray] = None
    _y_mean: float = 0.0
    _y_std: float = 1.0
    _alpha: Optional[np.ndarray] = None
    _chol: Optional[np.ndarray] = None
    _loglik: float = float("nan")
    _sigma_f: float = 1.0
    _beta: Optional[np.ndarray] = None
    _yz: Optional[np.ndarray] = None

    def _as_scaled(self, X: np.ndarray) -> np.ndarray:
        return (np.atleast_2d(X) - self._x_mean) / self._x_std

    def _design(self, Xz: np.ndarray) -> np.ndarray:
        """Trend design matrix in standardised coordinates."""
        cols = [np.ones((Xz.shape[0], 1))]
        if self.trend_order >= 1:
            cols.append(Xz)
        return np.hstack(cols)

    # ------------------------------------------------------------------ kernel
    def _kernel(self, A: np.ndarray, B: np.ndarray, lengthscale: np.ndarray,
                sigma_f: float) -> np.ndarray:
        d = _sqdist(A / lengthscale, B / lengthscale)
        return sigma_f**2 * np.exp(-0.5 * np.maximum(d, 0.0))

    def _neg_log_marginal(self, theta: np.ndarray) -> float:
        d = self.X_.shape[1]
        theta = np.clip(np.asarray(theta, dtype=float), -12.0, 12.0)
        lengthscale = np.exp(theta[:d])
        sigma_f = float(np.exp(theta[d]))
        K = self._kernel(self.X_, self.X_, lengthscale, sigma_f)
        K[np.diag_indices_from(K)] += self._jitter
        try:
            L = np.linalg.cholesky(K)
        except np.linalg.LinAlgError:
            return 1e25
        alpha = _tri_solve(L, self._yz)
        nll = 0.5 * float(self._yz @ alpha) + float(np.sum(np.log(np.diag(L))))
        nll += 0.5 * len(self._yz) * _LOG_2PI
        return nll if np.isfinite(nll) else 1e25

    # ------------------------------------------------------------------- fit
    def fit(self, X: np.ndarray, y: np.ndarray) -> "GPR":
        X = np.atleast_2d(np.asarray(X, dtype=float))
        y = np.asarray(y, dtype=float).reshape(-1)
        if X.shape[0] != y.shape[0]:
            raise ValueError("X and y length mismatch")
        if X.shape[0] < 2:
            raise ValueError("need at least 2 training points")
        self.X_ = X
        self._x_mean = X.mean(axis=0)
        std = X.std(axis=0)
        self._x_std = np.where(std > 0, std, 1.0)
        self.y_ = y
        d = X.shape[1]
        Xz = (X - self._x_mean) / self._x_std
        # polynomial drift: a stationary GP cannot represent a linear trend over a long
        # length scale (it either explodes or over-fits with a short one), so the trend is
        # removed by generalised least squares first and added back on prediction.
        H = self._design(Xz)
        self._beta = np.linalg.lstsq(H, y, rcond=None)[0] if H.shape[1] else np.zeros(1)
        y_res = y - H @ self._beta
        self._y_mean = float(y_res.mean())
        ys = float(y_res.std())
        # a (near) constant target keeps a tiny scale: defaulting to 1.0 would report
        # residual variances in absolute problem units and make a variance-driven
        # acquisition chase a level whose residual carries no signal.
        self._y_std = ys if ys > 1e-12 else 1e-12
        yz = (y_res - self._y_mean) / self._y_std
        self._yz = yz
        self._jitter = max(float(self.nugget), 1e-12 * max(1.0, float(np.var(yz))))
        theta = np.concatenate([np.zeros(d), [0.0]])
        if self.lengthscale is not None:
            theta[:d] = np.log(np.atleast_1d(np.asarray(self.lengthscale, dtype=float)))
        if self.sigma_f_init is not None:
            theta[d] = float(np.log(max(float(self.sigma_f_init), 1e-6)))

        best = (self._neg_log_marginal(theta), theta)
        n_hyper = X.shape[0]
        if n_hyper > int(self.hyper_max_points):
            # fit the hyper-parameters on a space-filling subsample: at large n the
            # marginal likelihood degenerates towards "interpolate every wiggle" (tiny
            # length scales), which predicts badly between samples; the subsample keeps
            # the scale estimate global, the final Cholesky uses every point.
            order = np.argsort(np.argsort((X - self._x_mean) @ np.ones(X.shape[1])))
            sel = np.unique(np.linspace(0, n_hyper - 1, int(self.hyper_max_points)).astype(int))
            X_hyper, yz_hyper = Xz[sel], yz[sel]
        else:
            X_hyper, yz_hyper = Xz, yz
        X_full, yz_full, yz = self.X_, self._yz, self._yz
        if self.optimize and X_hyper.shape[0] >= 3:
            from scipy.optimize import minimize

            self.X_, self._yz = X_hyper, yz_hyper

            # 1) deterministic isotropic scan: robust, and immune to the flat-likelihood
            #    pathologies that make gradient optimisers stop at arbitrary points;
            # 2) Nelder–Mead refinement of the full (ARD lengthscales, sigma_f) vector.
            for log_ls in np.linspace(np.log(0.02), np.log(20.0), 16):
                for log_sf in (0.0, float(np.log(2.0))):
                    th = np.concatenate([np.full(d, log_ls), [log_sf]])
                    if self.lengthscale is not None:
                        th[:d] = theta[:d]
                    f = self._neg_log_marginal(th)
                    if f < best[0]:
                        best = (f, th)
            span = np.log(20.0) - np.log(0.02)
            res = minimize(
                self._neg_log_marginal, best[1], method="Nelder-Mead",
                options={"maxiter": int(self.max_iter), "xatol": 1e-3,
                         "fatol": 1e-5, "adaptive": True},
            )
            if np.isfinite(res.fun) and res.fun < best[0]:
                th = np.clip(np.asarray(res.x, dtype=float),
                             np.log(0.02), np.log(20.0))
                if self.lengthscale is not None:
                    th[:d] = theta[:d]
                f = self._neg_log_marginal(th)
                if f < best[0]:
                    best = (f, th)
        self.X_, self._yz = X_full, yz

        self._loglik = -best[0]
        theta = best[1]
        self.lengthscale_ = np.exp(theta[:d])
        self._sigma_f = float(np.exp(theta[d]))
        K = self._kernel(Xz, Xz, self.lengthscale_, self._sigma_f)
        K[np.diag_indices_from(K)] += self._jitter
        self._chol = np.linalg.cholesky(K)
        self._alpha = _tri_solve(self._chol, self._yz)
        return self

    # --------------------------------------------------------------- predict
    def _check_fitted(self) -> None:
        if self._alpha is None:
            raise RuntimeError("GPR instance is not fitted yet")

    def predict(self, Xs: np.ndarray, return_std: bool = True) -> Tuple[np.ndarray, np.ndarray]:
        self._check_fitted()
        Xs = np.atleast_2d(np.asarray(Xs, dtype=float))
        Xz = (Xs - self._x_mean) / self._x_std
        Ks = self._kernel(Xz, (self.X_ - self._x_mean) / self._x_std,
                          self.lengthscale_, self._sigma_f)
        mean = Ks @ self._alpha
        mean = mean * self._y_std + self._y_mean + self._design(Xz) @ self._beta
        if not return_std:
            return mean, np.zeros_like(mean)
        v = (_solve_triangular(self._chol, Ks.T, lower=True, check_finite=False)
             if _solve_triangular is not None else np.linalg.solve(self._chol, Ks.T))
        var = self._sigma_f**2 - np.sum(v * v, axis=0)
        var = np.maximum(var, 1e-300) * self._y_std**2
        return mean, np.sqrt(var)

    # ------------------------------------------------------- kernel vectors
    def kernel_vector(self, x: np.ndarray) -> np.ndarray:
        """Kernel between a single point and the training set (standardised units)."""
        self._check_fitted()
        x = np.asarray(x, dtype=float).reshape(1, -1)
        xz = (x - self._x_mean) / self._x_std
        Xz = (self.X_ - self._x_mean) / self._x_std
        return self._kernel(xz, Xz, self.lengthscale_, self._sigma_f).reshape(-1)

    def variance_reduction(self, x: np.ndarray, pool: np.ndarray) -> float:
        """Expected variance drop at ``pool`` if ``x`` is added to the design.

        Uses the usual kriging identity
        ``Delta var(x_p) = cov(x_p, x)^2 / (var(x) + jitter)`` summed over the pool,
        returned in *target* units squared.  The quantity is the standard
        variance-based acquisition used by the adaptive allocator.
        """
        self._check_fitted()
        pool = np.atleast_2d(np.asarray(pool, dtype=float))
        Pz = (pool - self._x_mean) / self._x_std
        Xz = (self.X_ - self._x_mean) / self._x_std
        kp = self._kernel(Pz, Xz, self.lengthscale_, self._sigma_f)      # (m, n)
        kx = self.kernel_vector(x)                                      # (n,)
        # full Cholesky solve for K^-1 kx (both triangular factors!)
        v = _tri_solve(self._chol, kx)
        kv = kp @ v
        denom = self._sigma_f**2 + self._jitter
        red = float(np.sum(kv**2)) / denom
        return red * self._y_std**2

    def variance_reduction_many(self, Xcand: np.ndarray, pool: np.ndarray) -> np.ndarray:
        """Vectorised :meth:`variance_reduction` over many candidate points.

        ``Delta var_p = sum_p cov(x_p, x)^2 / var(x)`` for every candidate at once; the
        ``K^-1 K(pool, train)^T`` factorisation is shared, so the cost is one solve of size
        ``n x m`` plus one matrix product — the acquisition of the adaptive allocator.
        """
        self._check_fitted()
        Xcand = np.atleast_2d(np.asarray(Xcand, dtype=float))
        pool = np.atleast_2d(np.asarray(pool, dtype=float))
        Xtan = (self.X_ - self._x_mean) / self._x_std
        Pz = (pool - self._x_mean) / self._x_std
        Cz = (Xcand - self._x_mean) / self._x_std
        k_pool = self._kernel(Pz, Xtan, self.lengthscale_, self._sigma_f)      # m x n
        k_cand = self._kernel(Cz, Xtan, self.lengthscale_, self._sigma_f)      # c x n
        V = (_solve_triangular(self._chol, k_pool.T, lower=True, check_finite=False)
             if _solve_triangular is not None else np.linalg.solve(self._chol, k_pool.T))
        V = (_solve_triangular(self._chol.T, V, lower=False, check_finite=False)
             if _solve_triangular is not None else np.linalg.solve(self._chol.T, V))
        kv = k_cand @ V                                                        # c x m
        denom = self._sigma_f**2 + self._jitter
        return np.sum(kv * kv, axis=1) / denom * self._y_std**2

    # ------------------------------------------------------------- reporting
    def hyperparameters(self) -> dict:
        return {
            "lengthscale": [float(v) for v in np.atleast_1d(self.lengthscale_)],
            "sigma_f": float(self._sigma_f),
            "nugget": float(self._jitter),
            "log_marginal_likelihood": float(self._loglik),
            "n_train": int(len(self.y_)) if self.y_ is not None else 0,
        }


def _as_columns(X: np.ndarray, d: Optional[int] = None) -> np.ndarray:
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    if d is not None and X.shape[1] != d:
        raise ValueError(f"expected {d} columns, got {X.shape[1]}")
    return X