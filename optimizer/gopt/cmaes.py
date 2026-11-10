"""G-OPT CMA-ES: covariance-matrix-adaptation evolution strategy (mature kernel).

Derivative-free, non-convex, badly-scaled and *noisy* objective functions.  This
module intentionally replaces the earlier minimal kernel
(``optimizer/core/cmaes.py``) for new work; the legacy kernel is left untouched
so its existing callers keep their bit-identical behaviour.

What "mature" means here (each item is exercised by a test in
``tests/test_gopt_cmaes.py`` against a closed-form optimum, never against
itself):

* ``ask``/``tell`` split (external/parallel evaluation possible);
* eigen-decomposition sampling, so a temporarily non-positive-definite ``C`` is
  sampled correctly instead of aborting (Hansen 2016, sec. 3);
* the *correct* ``h_sigma`` normalisation ``1-(1-c_s)^(2(g+1))`` using the
  generation counter ``g`` (the legacy kernel uses ``evaluation_count/lambda``
  which is off by one generation and violates the E||N(0,I)|| normalisation);
* CSA step-size control with the standard ``damps``;
* box constraints by clip *and* by mean projection, with the clipped fraction
  reported (a large fraction is a documented symptom, not silent bias);
* noisy objectives via ``n_resample`` re-evaluations per candidate;
* IPOP / BIPOP restarts (Auger & Hansen 2005) with an evaluation budget;
* the full Hansen termination-criterion set (TolFun, TolX, TolUpSigma,
  ConditionCov, NoEffectCoord, NoEffectAxis) with a machine-readable reason;
* bit-exact reproducibility for a fixed seed (tested).

Reference: Hansen, N. (2016) "The CMA Evolution Strategy: A Tutorial",
arXiv:1604.00772; Auger & Hansen (2005) "A restart CMA evolution strategy with
increasing population size", CEC 2005.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

__all__ = ["CMAESResult", "CMAESOptimizer", "minimize_cmaes"]


@dataclass
class CMAESResult:
    x: np.ndarray
    fun: float
    n_eval: int
    generations: int
    restarts: int
    converged: bool
    termination: str
    history: List[Dict[str, float]] = field(default_factory=list)
    diagnostics: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "x": np.asarray(self.x, float).tolist(),
            "fun": float(self.fun),
            "n_eval": int(self.n_eval),
            "generations": int(self.generations),
            "restarts": int(self.restarts),
            "converged": bool(self.converged),
            "termination": self.termination,
            "history": self.history,
            "diagnostics": self.diagnostics,
        }


class _RunState:
    """One CMA-ES run (a restart owns one of these)."""

    def __init__(self, n: int, mean: np.ndarray, sigma: float, popsize: int,
                 bounds: Optional[np.ndarray], rng: np.random.Generator):
        self.n = n
        self.mean = np.array(mean, float)
        self.sigma = float(sigma)
        self.lam = int(popsize)
        self.mu = self.lam // 2
        self.bounds = bounds
        self.rng = rng

        w = np.log(self.mu + 0.5) - np.log(np.arange(1, self.mu + 1))
        self.weights = w / w.sum()
        self.mueff = 1.0 / float(np.sum(self.weights ** 2))

        self.cc = (4 + self.mueff / n) / (n + 4 + 2 * self.mueff / n)
        self.cs = (self.mueff + 2) / (n + self.mueff + 5)
        self.c1 = 2 / ((n + 1.3) ** 2 + self.mueff)
        self.cmu = min(
            1 - self.c1,
            2 * (self.mueff - 2 + 1 / self.mueff) / ((n + 2) ** 2 + self.mueff),
        )
        self.damps = 1 + 2 * max(0.0, np.sqrt((self.mueff - 1) / (n + 1)) - 1) + self.cs
        self.chiN = np.sqrt(n) * (1 - 1 / (4 * n) + 1 / (21 * n * n))

        self.C = np.eye(n)
        self.pc = np.zeros(n)
        self.ps = np.zeros(n)
        self.g = 0                     # generation counter (h_sigma!)
        self.psd_repairs = 0
        self.clipped_fraction_history: List[float] = []
        self.condition_history: List[float] = []

    # -- distribution handling ------------------------------------------------
    def _chol_like(self) -> np.ndarray:
        """Return A with A A^T = C via eigen-decomposition (robust to non-SPD C)."""
        C = (self.C + self.C.T) / 2
        w, V = np.linalg.eigh(C)
        if w.min() <= 0:
            floor = max(1e-30, 1e-14 * float(np.max(w)))
            w = np.maximum(w, floor)
            C = (V * w) @ V.T
            self.C = (C + C.T) / 2
            self.psd_repairs += 1
        w = np.maximum(w, 1e-30)
        return V * np.sqrt(w)

    def ask(self) -> np.ndarray:
        A = self._chol_like()
        z = self.rng.standard_normal((self.lam, self.n))
        y = z @ A.T
        x = self.mean + self.sigma * y
        if self.bounds is not None:
            lo, hi = self.bounds[:, 0], self.bounds[:, 1]
            clipped = (x < lo) | (x > hi)
            self.clipped_fraction_history.append(float(clipped.mean()))
            x = np.clip(x, lo, hi)
        return x

    def tell(self, population: np.ndarray, fitness: Sequence[float]) -> None:
        fitness = np.asarray(fitness, float)
        idx = np.argsort(fitness)
        sel = population[idx[: self.mu]]
        old_mean = self.mean.copy()

        self.mean = self.weights @ sel
        if self.bounds is not None:
            self.mean = np.clip(self.mean, self.bounds[:, 0], self.bounds[:, 1])

        y_w = (self.mean - old_mean) / self.sigma
        self.ps = (1 - self.cs) * self.ps + np.sqrt(
            self.cs * (2 - self.cs) * self.mueff
        ) * y_w

        hsig_denom = np.sqrt(1 - (1 - self.cs) ** (2 * (self.g + 1)))
        hsig = (np.linalg.norm(self.ps) / hsig_denom) / self.chiN < (
            1.4 + 2 / (self.n + 1)
        )

        self.pc = (1 - self.cc) * self.pc
        if hsig:
            self.pc += np.sqrt(self.cc * (2 - self.cc) * self.mueff) * y_w

        C_old = self.C.copy()
        artmp = (sel - old_mean) / self.sigma
        rank_mu = (artmp.T * self.weights) @ artmp
        self.C = (
            (1 - self.c1 - self.cmu) * C_old
            + self.c1 * (np.outer(self.pc, self.pc) + (1 - hsig) * self.cc * (2 - self.cc) * C_old)
            + self.cmu * rank_mu
        )
        self.C = (self.C + self.C.T) / 2

        self.sigma *= float(np.exp((self.cs / self.damps) * (np.linalg.norm(self.ps) / self.chiN - 1)))
        self.sigma = float(np.clip(self.sigma, 1e-300, 1e20))
        self.g += 1
        self.condition_history.append(float(np.max(np.diag(self.C)) / max(np.min(np.diag(self.C)), 1e-300)))

    # -- termination criteria (Hansen 2016, sec. 5) ---------------------------
    def condition_number(self) -> float:
        w = np.linalg.eigvalsh((self.C + self.C.T) / 2)
        return float(np.max(w) / max(np.min(w), 1e-300))

    def check_termination(self, gen_best: List[float], gen_spread: List[float],
                          tolfun: float, tolx: float) -> Optional[str]:
        n, lam = self.n, self.lam
        # TolFun (Hansen 2016 sec. 5): the best values of the recent generations
        # are within tolfun *and* the current population itself is flat, i.e. the
        # distribution no longer discriminates candidates.
        window = 10 + int(np.ceil(30 * n / lam))
        if len(gen_best) > window and gen_spread:
            recent = gen_best[-window:]
            spread = gen_spread[-window:]
            if (max(recent) - min(recent)) < tolfun and max(spread) < 1e-10 * max(
                1.0, abs(min(recent))
            ):
                return "TolFun: objective range below tolerance"
        # TolX
        if np.all(self.sigma * np.abs(self.pc) < tolx) and np.all(
            self.sigma * np.sqrt(np.diag(self.C)) < tolx
        ):
            return "TolX: step size below tolerance in all coordinates"
        # TolUpSigma
        if self.sigma > 1e20:
            return "TolUpSigma: step size diverged"
        # ConditionCov
        if self.condition_number() > 1e14:
            return "ConditionCov: condition number of C exceeds 1e14"
        # NoEffectCoord
        if np.any(self.mean == self.mean + 0.2 * self.sigma * np.sqrt(np.diag(self.C))):
            return "NoEffectCoord: one coordinate cannot be changed any more"
        # NoEffectAxis
        i = int(self.g % n)
        e = np.zeros(n)
        e[i] = 1.0
        if np.allclose(
            self.mean, self.mean + 0.1 * self.sigma * np.sqrt(self.C[i, i]) * e, rtol=0, atol=0
        ):
            return "NoEffectAxis: the distribution axis has no effect"
        return None


class CMAESOptimizer:
    """Bounded CMA-ES with optional IPOP/BIPOP restarts and noise resampling."""

    def __init__(
        self,
        dim: int,
        bounds: Optional[np.ndarray] = None,
        x0: Optional[Sequence[float]] = None,
        sigma0: Optional[float] = None,
        popsize: Optional[int] = None,
        seed: int = 0,
        n_resample: int = 1,
        max_evals: int = 4000,
        restarts: int = 0,
        strategy: str = "ipop",
        tolfun: float = 1e-12,
        tolx: float = 1e-12,
        verbose: bool = False,
    ) -> None:
        self.n = int(dim)
        self.bounds = None if bounds is None else np.asarray(bounds, float).reshape(self.n, 2)
        if self.bounds is not None and np.any(self.bounds[:, 0] >= self.bounds[:, 1]):
            raise ValueError("bounds must satisfy lower < upper in every coordinate")
        self.x0 = None if x0 is None else np.asarray(x0, float).reshape(self.n)
        self.sigma0 = sigma0
        self.popsize = popsize or (4 + int(np.floor(3 * np.log(self.n))))
        self.seed = int(seed)
        self.n_resample = max(1, int(n_resample))
        self.max_evals = int(max_evals)
        self.restarts = int(restarts)
        self.strategy = strategy
        if strategy not in ("none", "ipop", "bipop"):
            raise ValueError(f"unknown restart strategy {strategy!r}")
        self.tolfun = float(tolfun)
        self.tolx = float(tolx)
        self.verbose = verbose

    # -- helpers -------------------------------------------------------------
    def _mean_and_sigma(self) -> (np.ndarray, float):
        if self.x0 is not None:
            mean = self.x0.copy()
        elif self.bounds is not None:
            mean = 0.5 * (self.bounds[:, 0] + self.bounds[:, 1])
        else:
            mean = np.zeros(self.n)
        if self.sigma0 is not None:
            sigma = float(self.sigma0)
        elif self.bounds is not None:
            sigma = float(0.25 * np.mean(self.bounds[:, 1] - self.bounds[:, 0]))
        else:
            sigma = 0.3 * max(1.0, float(np.max(np.abs(mean))))
        return mean, sigma

    def optimize(self, objective: Callable[[np.ndarray], float]) -> CMAESResult:
        rng = np.random.default_rng(self.seed)
        mean0, sigma0 = self._mean_and_sigma()

        best_x: Optional[np.ndarray] = None
        best_f = np.inf
        n_eval = 0
        total_gens = 0
        history: List[Dict[str, float]] = []
        all_run_log: List[Dict[str, object]] = []
        termination = "MaxEvals"
        converged = False

        lambda_base = self.popsize
        for run in range(self.restarts + 1):
            if n_eval >= self.max_evals:
                break
            if self.strategy == "bipop" and run > 0:
                if run % 2 == 1:  # large run
                    lam = int(lambda_base * 2 ** ((run + 1) // 2))
                    sigma = sigma0
                else:            # small run
                    lam = max(4, int(lambda_base / (2 ** ((run // 2)))))
                    sigma = sigma0 * 0.5 ** (run // 2)
            elif self.strategy == "ipop" and run > 0:
                lam = int(lambda_base * 2 ** run)
                sigma = sigma0
            else:
                lam = lambda_base
                sigma = sigma0

            state = _RunState(self.n, mean0, sigma, lam, self.bounds, rng)
            gen_best_hist: List[float] = []
            gen_spread_hist: List[float] = []
            gen = 0
            reason = None
            while True:
                if n_eval >= self.max_evals:
                    reason = "MaxEvals"
                    break
                pop = state.ask()
                fits = []
                for cand in pop:
                    if self.n_resample == 1:
                        f = float(objective(cand))
                        n_eval += 1
                    else:
                        vals = [float(objective(cand)) for _ in range(self.n_resample)]
                        n_eval += self.n_resample
                        f = float(np.mean(vals))
                    fits.append(f)
                fits_arr = np.asarray(fits)
                gbest = int(np.argmin(fits_arr))
                if fits_arr[gbest] < best_f:
                    best_f = float(fits_arr[gbest])
                    best_x = pop[gbest].copy()

                history.append(
                    {
                        "run": run,
                        "generation": gen,
                        "n_eval": n_eval,
                        "gen_best": float(fits_arr[gbest]),
                        "best_so_far": float(best_f),
                        "sigma": float(state.sigma),
                        "condition": state.condition_number(),
                        "lambda": int(state.lam),
                    }
                )
                gen_best_hist.append(float(fits_arr[gbest]))
                gen_spread_hist.append(float(np.max(fits_arr) - np.min(fits_arr)))
                state.tell(pop, fits_arr)
                gen += 1
                total_gens += 1
                if self.verbose and gen % 20 == 0:
                    print(f"[gopt/cmaes] run {run} gen {gen} evals {n_eval} best {best_f:.6e} sigma {state.sigma:.3e}")
                if len(gen_best_hist) > 3 and reason is None:
                    reason = state.check_termination(
                        gen_best_hist, gen_spread_hist, self.tolfun, self.tolx
                    )
                    if reason is not None:
                        break
            all_run_log.append(
                {
                    "run": run,
                    "lambda": int(state.lam),
                    "sigma_final": float(state.sigma),
                    "psd_repairs": int(state.psd_repairs),
                    "generations": int(state.g),
                    "termination": reason or "MaxEvals",
                    "mean_clipped_fraction": (
                        float(np.mean(state.clipped_fraction_history))
                        if state.clipped_fraction_history
                        else 0.0
                    ),
                }
            )
            if self.verbose:
                print(f"[gopt/cmaes] run {run} ended: {reason} (evals {n_eval}, best {best_f:.6e})")
            termination = reason or "MaxEvals"
            converged = bool(reason is not None and not reason.startswith("TolUpSigma"))
            # A restart budget means "one run per restart": a TolFun stop is the
            # normal end of a run, not the end of the search (Auger & Hansen 2005).
            if n_eval >= self.max_evals or run >= self.restarts:
                break

        return CMAESResult(
            x=best_x if best_x is not None else mean0,
            fun=float(best_f),
            n_eval=int(n_eval),
            generations=int(total_gens),
            restarts=int(len(all_run_log) - 1),
            converged=bool(converged),
            termination=termination,
            history=history,
            diagnostics={
                "runs": all_run_log,
                "strategy": self.strategy,
                "n_resample": self.n_resample,
                "popsize_base": int(self.popsize),
                "seed": self.seed,
            },
        )


def minimize_cmaes(
    fun: Callable[[np.ndarray], float],
    dim: int,
    bounds: Optional[np.ndarray] = None,
    seed: int = 0,
    max_evals: int = 4000,
    popsize: Optional[int] = None,
    restarts: int = 0,
    n_resample: int = 1,
) -> CMAESResult:
    """Convenience wrapper mirroring ``scipy.optimize.minimize``'s spirit."""
    opt = CMAESOptimizer(
        dim=dim,
        bounds=bounds,
        seed=seed,
        max_evals=max_evals,
        popsize=popsize,
        restarts=restarts,
        n_resample=n_resample,
    )
    return opt.optimize(fun)