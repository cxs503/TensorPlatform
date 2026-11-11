"""Autoregressive (Kennedy–O'Hagan) multi-level co-kriging.

Model
-----
For a ladder with ranks ``0..K``::

    z_0(x) = delta_0(x)                                  delta_0 ~ GP
    z_k(x) = rho_k * z_{k-1}(x) + delta_k(x)             delta_k ~ GP,  k = 1..K

``rho_k`` are unknown scaling factors estimated from the data, and each ``delta_k`` is
an independent GP fitted to the *residual* of the k-th level after the lower levels
have been applied.  One can show this is the general Kennedy–O'Hagan construction for
nested designs (Forrester, Sóbester & Keane 2008, §8.2).

Fitting (recursive, level by level)
-----------------------------------
1. fit ``GP_0`` on all rank-0 data;
2. for ``k = 1..K``: predict the lower-level chain at ``X_k`` (the data of level k),
   estimate ``rho_k`` by least squares ``rho_k = <z_{k-1}, y_k> / <z_{k-1}, z_{k-1}>``,
   form the residual ``delta_k = y_k - rho_k z_{k-1}``, fit ``GP_delta_k`` on it.

Prediction
----------
``mean = rho_K * ... (rho_1 * m_0(x) + m_delta1(x)) ... + m_deltaK(x)``.  The variance
uses the standard *independence* approximation ``var = rho_k^2 var_{k-1} + var_delta_k``
(neglecting the cross-covariance between lower-level and residual processes), which is
conservative in the sense that it never under-states the residual uncertainty of the
top level when ``delta_k`` is positively correlated with the lower-level error.

Degenerate cases
----------------
* only rank-0 data present  -> the surrogate *is* a single-fidelity GP;
* a level with fewer than 2 data points is skipped (``rho`` stays at the previous one)
  so the allocator can add levels incrementally during a sequential campaign.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .gpr import GPR

__all__ = ["MultiFidelitySurrogate", "fit_co_kriging"]


@dataclass
class _ResidualModel:
    rank: int
    rho: float
    gp: Optional[GPR]


class MultiFidelitySurrogate:
    """Recursive AR(1) co-kriging over an arbitrary number of levels."""

    def __init__(
        self,
        nugget: float = 1e-8,
        optimize: bool = True,
        restarts: int = 2,
        seed: int = 0,
        min_points: int = 2,
        rho_mode: str = "profile",
        reuse_hyper: bool = False,
        profile_rho: bool = True,
        rho_candidates: Optional[Sequence[float]] = None,
        rho_fit_restarts: int = 0,
        rho_prior_weight: float = 10.0,
        hyper_cache: Dict[int, Tuple[np.ndarray, float]] | None = None,
    ):
        self.nugget = nugget
        self.profile_rho = profile_rho
        self.rho_candidates = tuple(rho_candidates) if rho_candidates is not None else None
        self.rho_fit_restarts = int(rho_fit_restarts)
        self.rho_prior_weight = float(rho_prior_weight)
        self.optimize = optimize
        self.restarts = restarts
        self.seed = seed
        self.min_points = min_points
        if rho_mode not in ("profile", "ls"):
            raise ValueError("rho_mode must be 'profile' or 'ls'")
        self.rho_mode = rho_mode
        self.reuse_hyper = bool(reuse_hyper)
        #: length-scale cache, so a sequential campaign can reuse the hyper-parameters
        #: fitted on an earlier (smaller) design instead of re-optimising every round
        self._hyper_cache: Dict[int, Tuple[np.ndarray, float]] = dict(hyper_cache or {})
        self.rho: Dict[int, float] = {}
        self.models: Dict[int, Optional[GPR]] = {}
        self.n_train: Dict[int, int] = {}
        self._ranks: List[int] = []
        self._top: int = 0
        self._fitted = False

    # ------------------------------------------------------------------ fitting
    def fit(self, data: Dict[int, Tuple[np.ndarray, np.ndarray]]) -> "MultiFidelitySurrogate":
        """Fit the recursive chain.

        ``data`` maps rank -> ``(X, y)``.  Ranks may be missing; the chain is built
        over the sorted set of available ranks.
        """
        clean: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
        for rank, (X, y) in data.items():
            X = np.atleast_2d(np.asarray(X, dtype=float))
            y = np.asarray(y, dtype=float).reshape(-1)
            if X.shape[0] != y.shape[0]:
                raise ValueError(f"rank {rank}: X/y length mismatch")
            if X.shape[0] >= self.min_points:
                clean[int(rank)] = (X, y)
        if not clean:
            raise ValueError("co-kriging needs at least one level with >= min_points data")

        self.models = {}
        self.rho = {}
        self._data = {r: (np.asarray(v[0], dtype=float), np.asarray(v[1], dtype=float))
                      for r, v in clean.items()}
        self.n_train = {r: int(v[0].shape[0]) for r, v in clean.items()}
        ranks = sorted(clean)
        self._ranks = []
        self._top = ranks[-1]
        prev_rank: Optional[int] = None
        for rank in ranks:
            X, y = clean[rank]
            if prev_rank is None:
                gp = self._make_gp(rank).fit(X, y)
                self._hyper_cache[rank] = (np.atleast_1d(gp.lengthscale_).copy(), gp._sigma_f)
                self.models[rank] = gp
                self.rho[rank] = 1.0
                prev_rank = rank
                self._ranks.append(rank)
                continue
            # lower-level chain evaluated at the current level's data points
            lower_mean, _ = self._predict_chain(prev_rank, X)
            paired = self._paired_lower_values(prev_rank, X)
            rho = self._estimate_rho(X, lower_mean, y, paired)
            residual = y - rho * lower_mean
            if np.allclose(residual, residual[0]):
                self.models[rank] = None
            else:
                gp_d = self._make_gp(rank).fit(X, residual)
                self.models[rank] = gp_d
                self._hyper_cache[rank] = (np.atleast_1d(gp_d.lengthscale_).copy(),
                                           gp_d._sigma_f)
            self.rho[rank] = rho
            prev_rank = rank
            self._ranks.append(rank)
        self._fitted = True
        self._ranks: List[int] = ranks
        self._top = ranks[-1]
        return self

    def _make_gp(self, rank: int) -> GPR:
        """GP configured for one rank, optionally warm-started from the previous fit."""
        if self.reuse_hyper and rank in self._hyper_cache:
            ls, sf = self._hyper_cache[rank]
            return GPR(nugget=self.nugget, optimize=False, lengthscale=ls,
                       sigma_f_init=sf, seed=self.seed)
        return GPR(nugget=self.nugget, optimize=self.optimize,
                   restarts=self.restarts, seed=self.seed)

    # ---------------------------------------------------------------- rho fitting
    def _rho_grid(self) -> Sequence[float]:
        if self.rho_candidates is not None:
            return self.rho_candidates
        return tuple(np.round(np.geomspace(0.05, 5.0, 13), 6))

    def _paired_lower_values(self, lower_rank: int, X: np.ndarray) -> Optional[np.ndarray]:
        """Measured lower-level values at exactly the same design points (nested design).

        When the campaign keeps a nested design (every coarse-level evaluation is also
        made at the fine-level points, which is cheap) the AR(1) scaling can be estimated
        from *paired measurements* instead of from the GP chain.  That removes the
        smoothing error of the lower GP from the estimate — which otherwise dominates when
        the lower surrogate is itself only moderately accurate.
        """
        if lower_rank not in self._data:
            return None
        Xl, yl = self._data[lower_rank]
        out = np.empty(len(X))
        tol = 1e-12 + 1e-9 * float(np.max(np.abs(Xl)))
        for i, x in enumerate(X):
            d = np.max(np.abs(Xl - x), axis=1)
            j = int(np.argmin(d))
            if d[j] > tol:
                return None
            out[i] = yl[j]
        return out

    def _estimate_rho(self, X: np.ndarray, lower_mean: np.ndarray, y: np.ndarray,
                      paired: Optional[np.ndarray] = None) -> float:
        """Estimate the AR(1) scaling factor.

        With ``profile_rho`` the estimate maximises the *profile* marginal likelihood of
        the residual process, i.e. for every candidate ``rho`` a GP is fitted to
        ``y - rho z_{k-1}`` and the log marginal likelihood is compared.  This is the
        standard Kennedy–O'Hagan practice and, unlike a plain least-squares projection
        of ``y`` on ``z_{k-1}``, it does **not** collapse when the levels differ by a
        smooth additive term (the residual then stays smooth, which is exactly what the
        ``delta`` GP is there to model).
        """
        if paired is not None and paired.size >= 3 and float(np.sum(paired**2)) > 0:
            # co-located estimator: no surrogate error between the two levels, but with a
            # small paired sample it is noisy — shrink it towards the natural prior rho=1
            # (the two levels describe the same quantity) with ``rho_prior_weight``
            # pseudo-observations.
            n_pair = float(paired.size)
            w = n_pair / (n_pair + self.rho_prior_weight)
            rho_pair = float(np.sum(paired * y) / np.sum(paired**2))
            if np.isfinite(rho_pair) and rho_pair > 0:
                return float(w * rho_pair + (1.0 - w) * 1.0)
        denom = float(np.sum(lower_mean**2))
        ls = float(np.sum(lower_mean * y) / denom) if denom > 0 else 1.0
        if not np.isfinite(ls) or ls <= 0:
            ls = 1.0
        if not self.profile_rho or self.rho_mode == "ls":
            return ls
        # stage 1 ------------------------------------------------------------
        # one full (hyper-parameter) fit at the least-squares scaling, which gives a
        # sensible correlation structure to scan ``rho`` against;
        resid0 = y - ls * lower_mean
        if float(np.std(resid0)) < 1e-14:
            return ls
        base = GPR(nugget=self.nugget, optimize=True, restarts=self.rho_fit_restarts,
                   max_iter=60, seed=self.seed).fit(X, resid0)
        ls_d = np.atleast_1d(base.lengthscale_)
        sf_d = float(base._sigma_f)

        # stage 2 ------------------------------------------------------------
        # profile scan with the correlation structure held fixed: only the scaling of
        # the lower level changes, so each candidate is one cheap Cholesky solve.
        best_rho, best_ll = ls, -np.inf
        candidates = list(self._rho_grid()) + [ls]
        for rho in candidates:
            if not np.isfinite(rho) or rho <= 0:
                continue
            resid = y - float(rho) * lower_mean
            if float(np.std(resid)) < 1e-14:
                return float(rho)
            try:
                gp = GPR(nugget=self.nugget, optimize=False, lengthscale=ls_d,
                         sigma_f_init=sf_d, seed=self.seed).fit(X, resid)
            except Exception:  # pragma: no cover - defensive
                continue
            ll = gp.hyperparameters()["log_marginal_likelihood"]
            if ll > best_ll:
                best_ll, best_rho = ll, float(rho)
        return best_rho

    # ---------------------------------------------------------------- prediction
    def _predict_chain(self, rank: int, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Prediction of the chain ending at ``rank`` (all lower levels included)."""
        ranks = [r for r in self._ranks + [rank] if r <= rank]
        ranks = sorted(set(ranks))
        mean, var = self.models[ranks[0]].predict(X)
        for r in ranks[1:]:
            gp = self.models.get(r)
            rho = self.rho.get(r, 1.0)
            if gp is not None:
                dm, dv = gp.predict(X)
            else:
                dm, dv = np.zeros_like(mean), np.zeros_like(var)
            mean = rho * mean + dm
            var = rho**2 * var + dv
        return mean, var

    def predict(self, X: np.ndarray, return_std: bool = True) -> Tuple[np.ndarray, np.ndarray]:
        if not self._fitted:
            raise RuntimeError("call fit() first")
        X = np.atleast_2d(np.asarray(X, dtype=float))
        mean, var = self._predict_chain(self._top, X)
        return mean, (np.sqrt(np.maximum(var, 0.0)) if return_std else np.zeros_like(var))

    # ---------------------------------------------------------------- reporting
    def summary(self) -> dict:
        return {
            "ranks": list(getattr(self, "_ranks", [])),
            "rho": {int(k): float(v) for k, v in self.rho.items()},
            "n_train": {int(k): int(v) for k, v in self.n_train.items()},
            "top_cost_level": int(getattr(self, "_top", 0)),
            "residual_gps": {
                int(k): (None if gp is None else gp.hyperparameters())
                for k, gp in self.models.items()
            },
        }

    # ------------------------------------------------- decision support (allocator)
    def level_residual_gp(self, rank: int) -> Optional[GPR]:
        """The residual GP that a new evaluation at ``rank`` would refine."""
        return self.models.get(int(rank))

    def propagate_factor(self, rank: int) -> float:
        """How strongly an improvement at ``rank`` propagates to the top level."""
        f = 1.0
        for r in self._ranks:
            if r > rank:
                f *= float(self.rho.get(r, 1.0)) ** 2
        return f

    def top_ranks(self) -> Sequence[int]:
        return tuple(getattr(self, "_ranks", ()))


def fit_co_kriging(
    data: Dict[int, Tuple[np.ndarray, np.ndarray]], **kwargs
) -> MultiFidelitySurrogate:
    """Convenience wrapper around :class:`MultiFidelitySurrogate`."""
    return MultiFidelitySurrogate(**kwargs).fit(data)