"""Cost model: expected compute cost of an order from dispatch-time features.

``true_cost`` in the fixture is a log-linear function of deliverable size, scale
bucket and tier; here that functional form is *re-estimated* by ordinary least
squares on the **historical wall-times of previously dispatched orders only**
(the past is observable, the future is not -- no leakage).  Residual spread is
reported as ``r2`` / ``sigma`` so a reviewer can see how much of the EVOI
denominator is noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

__all__ = ["CostModel", "COST_FEATURES"]

#: columns of the design matrix used for the (log) cost regression
COST_FEATURES: tuple[str, ...] = ("log_target_words", "scale_ord", "tier_ord")


@dataclass
class CostModel:
    coef: np.ndarray | None = None
    r2: float = float("nan")
    sigma: float = float("nan")
    n_fit: int = 0

    def fit(self, X: np.ndarray, cost: np.ndarray, names: Sequence[str]) -> "CostModel":
        """Fit ``log cost`` on the named columns of ``X``."""
        cols = [list(names).index(c) for c in COST_FEATURES]
        A = np.hstack([np.ones((len(X), 1)), np.asarray(X, dtype=float)[:, cols]])
        y = np.log(np.clip(np.asarray(cost, dtype=float), 1e-9, None))
        self.coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        resid = y - A @ self.coef
        ss_res = float(resid @ resid)
        ss_tot = float(((y - y.mean()) ** 2).sum())
        self.r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
        dof = max(1, len(y) - A.shape[1])
        self.sigma = float(np.sqrt(ss_res / dof))
        self.n_fit = int(len(y))
        return self

    def predict(self, X: np.ndarray, names: Sequence[str]) -> np.ndarray:
        if self.coef is None:
            raise RuntimeError("CostModel.fit must be called before predict")
        cols = [list(names).index(c) for c in COST_FEATURES]
        A = np.hstack([np.ones((len(X), 1)), np.asarray(X, dtype=float)[:, cols]])
        # log-normal expectation (E[cost] = exp(mu + sigma^2/2)) -- using the
        # median would systematically under-price heavy-tailed runs.
        return np.exp(A @ self.coef + 0.5 * self.sigma**2)

    def report(self) -> dict[str, Any]:
        return {
            "features": list(COST_FEATURES),
            "coef": [float(c) for c in (self.coef if self.coef is not None else [])],
            "r2_log_cost": self.r2,
            "sigma_log_cost": self.sigma,
            "n_fit": self.n_fit,
        }