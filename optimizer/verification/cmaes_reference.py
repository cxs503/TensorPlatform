"""Independent reference for the CMA-ES kernel.

Two independent artifacts live here, both transcribed directly from the primary
literature -- *not* from ``optimizer/core/cmaes.py`` -- so that comparing them
against the shipped kernel is a genuine cross-implementation check:

1. ``hansen2016_params`` -- closed-form strategy parameters of Hansen (2016),
   "The CMA Evolution Strategy: A Tutorial", arXiv:1604.00772, eqs. (49)-(56).
   ``lambda`` and ``mu`` follow the tutorial default (eq. 5): 4+floor(3 ln N), mu=floor(lambda/2).
2. ``ReferenceCMAES`` -- plain (non-active) CMA-ES with the tutorial's
   eigen-decomposition sampling and the tutorial's own ``hsig`` definition, so
   the shipped kernel can be replayed step-by-step against an implementation
   that never saw the shipped code.

Benchmark functions are the standard ones of the evolutionary-computation
literature, each with a *known* analytic global minimum (0 for all of them),
which is what makes "error vs analytic optimum" a legitimate criterion.
"""
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# 1. Closed-form strategy parameters (Hansen 2016, eqs. 49-56)
# ---------------------------------------------------------------------------


def hansen2016_params(dim: int, pop_size: int | None = None) -> dict:
    """Return the tutorial's closed-form strategy parameters for dimension ``dim``."""
    lam = int(4 + np.floor(3 * np.log(dim))) if pop_size is None else int(pop_size)
    mu = lam // 2
    w = np.log(mu + 0.5) - np.log(np.arange(1, mu + 1))
    w = w / np.sum(w)
    mueff = 1.0 / np.sum(w ** 2)

    cc = (4 + mueff / dim) / (dim + 4 + 2 * mueff / dim)          # eq. (49)
    cs = (mueff + 2) / (dim + mueff + 5)                          # eq. (50)
    c1 = 2 / ((dim + 1.3) ** 2 + mueff)                           # eq. (51)
    cmu = min(1 - c1, 2 * (mueff - 2 + 1 / mueff) / ((dim + 2) ** 2 + mueff))  # eq. (52)/(53)
    damps = 1 + 2 * max(0.0, np.sqrt((mueff - 1) / (dim + 1)) - 1) + cs       # eq. (54)
    chiN = np.sqrt(dim) * (1 - 1 / (4 * dim) + 1 / (21 * dim ** 2))          # eq. (55)
    return {
        "lambda": lam,
        "mu": mu,
        "weights": w,
        "mueff": float(mueff),
        "cc": float(cc),
        "cs": float(cs),
        "c1": float(c1),
        "cmu": float(cmu),
        "damps": float(damps),
        "chiN": float(chiN),
    }


# ---------------------------------------------------------------------------
# 2. Reference CMA-ES (Hansen 2016, tutorial pseudo-code, non-active)
# ---------------------------------------------------------------------------


class ReferenceCMAES:
    """Tutorial-faithful, unbounded, non-active CMA-ES (eigen-decomposition form).

    Deliberately written in the tutorial's own parametrisation (B, D, invsqrtC)
    rather than the Cholesky parametrisation used by the shipped kernel, so the
    comparison in ``cmaes_literature_verification`` is not self-referential.
    """

    def __init__(self, dim: int, sigma0: float, x0=None, seed: int = 42,
                 pop_size: int | None = None):
        p = hansen2016_params(dim, pop_size)
        self.dim = dim
        self.lam = p["lambda"]
        self.mu = p["mu"]
        self.weights = p["weights"]
        self.mueff = p["mueff"]
        self.cc, self.cs, self.c1, self.cmu = p["cc"], p["cs"], p["c1"], p["cmu"]
        self.damps, self.chiN = p["damps"], p["chiN"]

        self.rng = np.random.RandomState(seed)
        self.mean = np.zeros(dim) if x0 is None else np.asarray(x0, float).copy()
        self.sigma = float(sigma0)
        self.pc = np.zeros(dim)
        self.ps = np.zeros(dim)
        self.C = np.eye(dim)
        self.B = np.eye(dim)
        self.D = np.ones(dim)
        self.invsqrtC = np.eye(dim)
        self.counteval = 0
        self._eigeneval = 0
        self.trace_C = [1.0]
        self.gen = 0

    # -- sampling -----------------------------------------------------------
    def sample(self):
        z = self.rng.randn(self.lam, self.dim)
        y = (z * self.D) @ self.B.T              # tutorial: y = B D z
        return self.mean + self.sigma * y

    # -- one generation -----------------------------------------------------
    def tell(self, pop: np.ndarray, fit: np.ndarray):
        idx = np.argsort(fit)
        sel = pop[idx[: self.mu]]
        old = self.mean.copy()
        self.mean = self.weights @ sel
        y = (self.mean - old) / self.sigma

        self.ps = (1 - self.cs) * self.ps + np.sqrt(self.cs * (2 - self.cs) * self.mueff) * (
            self.invsqrtC @ y)
        self.counteval += self.lam
        self.gen += 1
        hsig = (np.linalg.norm(self.ps)
                / np.sqrt(1 - (1 - self.cs) ** (2 * self.counteval / self.lam))
                < (1.4 + 2 / (self.dim + 1)) * self.chiN)
        self.hsig = bool(hsig)

        C_old = self.C
        self.pc = (1 - self.cc) * self.pc + (hsig * np.sqrt(self.cc * (2 - self.cc) * self.mueff) * y)

        rank_mu = np.zeros((self.dim, self.dim))
        ys = (sel - old) / self.sigma
        for i in range(self.mu):
            rank_mu += self.weights[i] * np.outer(ys[i], ys[i])
        self.C = ((1 - self.c1 - self.cmu) * C_old
                  + self.c1 * (np.outer(self.pc, self.pc) + (1 - hsig) * self.cc * (2 - self.cc) * C_old)
                  + self.cmu * rank_mu)
        self.C = (self.C + self.C.T) / 2

        self.sigma *= np.exp((self.cs / self.damps) * (np.linalg.norm(self.ps) / self.chiN - 1))

        if self.counteval - self._eigeneval > self.lam / (self.c1 + self.cmu) / self.dim / 10:
            self._eigeneval = self.counteval
            D2, B = np.linalg.eigh(self.C)
            D2 = np.maximum(D2, 1e-20)
            self.D = np.sqrt(D2)
            self.B = B
            self.invsqrtC = (B * (1 / self.D)) @ B.T
        self.trace_C.append(float(np.trace(self.C)))

    def run(self, f, max_evals: int, target: float = None):
        best_f, best_x, hist = np.inf, None, []
        while self.counteval < max_evals:
            pop = self.sample()
            fit = np.array([f(x) for x in pop])
            i = int(np.argmin(fit))
            if fit[i] < best_f:
                best_f, best_x = float(fit[i]), pop[i].copy()
            hist.append(best_f)
            self.tell(pop, fit)
            if target is not None and best_f < target:
                break
        return {"best_fitness": best_f, "best_params": best_x,
                "evaluations": self.counteval, "history": hist,
                "trace_C": self.trace_C}


# ---------------------------------------------------------------------------
# 3. Standard benchmark functions with analytic global minimum f* = 0
# ---------------------------------------------------------------------------


def rotation_matrix(dim: int, seed: int = 7) -> np.ndarray:
    """Deterministic random orthogonal matrix (QR of a fixed normal draw)."""
    rng = np.random.RandomState(seed)
    q, r = np.linalg.qr(rng.randn(dim, dim))
    return q * np.sign(np.diag(r))


BENCHMARKS = {
    # name: (callable factory, analytic optimum, note)
    "sphere": (lambda n: (lambda x: float(np.sum(x ** 2))), 0.0,
               "isotropic, separable; convergence rate benchmark of Hansen (2016) sec. 4"),
    "ellipsoid1e6": (lambda n: (lambda x: float(np.sum((1e6 ** (np.arange(n) / max(n - 1, 1))) * x ** 2))),
                     0.0, "condition number 1e6; tests covariance adaptation"),
    "cigar": (lambda n: (lambda x: float(x[0] ** 2 + 1e4 * np.sum(x[1:] ** 2))),
              0.0, "axis ratio 100; tests single-direction step-size control"),
    "rosenbrock": (lambda n: (lambda x: float(np.sum(100 * (x[1:] - x[:-1] ** 2) ** 2 + (1 - x[:-1]) ** 2))),
                   0.0, "non-separable, narrow curved valley"),
    "rastrigin": (lambda n: (lambda x: float(10 * n + np.sum(x ** 2 - 10 * np.cos(2 * np.pi * x)))),
                  0.0, "multimodal (global optimum at x=0)"),
    "ackley": (lambda n: (lambda x: float(-20 * np.exp(-0.2 * np.sqrt(np.mean(x ** 2)))
                                       - np.exp(np.mean(np.cos(2 * np.pi * x))) + 20 + np.e)),
               0.0, "multimodal with broad flat outer region"),
}


def rotated(f, dim: int, seed: int = 7):
    """Return ``g(x) = f(R x)`` -- CMA-ES is rotation invariant (Hansen 2016 sec. 3.3)."""
    R = rotation_matrix(dim, seed)
    return lambda x: f(R @ x)