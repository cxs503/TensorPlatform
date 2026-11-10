"""SciPy adapter: wraps :func:`scipy.optimize.minimize` behind the unified API.

Algorithms (canonical name -> scipy method):

===============  ==================  =======  =====  ========
canonical        scipy method        ineq     eq     grad
===============  ==================  =======  =====  ========
SLSQP            SLSQP               yes      yes    yes
L-BFGS-B         L-BFGS-B            no       no     yes
Nelder-Mead      Nelder-Mead         no       no     no
Powell           Powell              no       no     no
COBYLA           COBYLA              yes      no     no
trust-constr     trust-constr        yes      yes    yes
===============  ==================  =======  =====  ========

Constraint handling uses the canonical ``g(x) >= 0`` / ``h(x) == 0`` form
directly, which is also SciPy's convention.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np

from ..compiled import CompiledProblem
from ..contract import EngineProbe, HistoryPoint, SolveResult, SolveStatus
from .base import AlgorithmSpec, AdapterUnavailable, OptimizerAdapter, ProblemNotSupported

_METHODS: Dict[str, Dict[str, Any]] = {
    "SLSQP": dict(method="SLSQP", ineq=True, eq=True, gradient=True, notes="quasi-Newton SQP"),
    "L-BFGS-B": dict(method="L-BFGS-B", ineq=False, eq=False, gradient=True, notes="bounds only"),
    "NELDER-MEAD": dict(method="Nelder-Mead", ineq=False, eq=False, gradient=False, notes="simplex"),
    "POWELL": dict(method="Powell", ineq=False, eq=False, gradient=False, notes="direction set"),
    "COBYLA": dict(method="COBYLA", ineq=True, eq=False, gradient=False, notes="linear approximations"),
    "TRUST-CONSTR": dict(method="trust-constr", ineq=True, eq=True, gradient=True, notes="trust region"),
}


class ScipyAdapter(OptimizerAdapter):
    engine = "scipy"
    DEFAULT_ALGORITHM = "SLSQP"
    ALGORITHMS = tuple(
        AlgorithmSpec(
            name=name,
            engine_name=cfg["method"],
            ineq=cfg["ineq"],
            eq=cfg["eq"],
            gradient=cfg["gradient"],
            notes=cfg["notes"],
        )
        for name, cfg in _METHODS.items()
    )

    @classmethod
    def _load_library(cls):
        try:
            import scipy.optimize as sco  # noqa: F401
        except Exception as exc:  # pragma: no cover - scipy always present here
            raise AdapterUnavailable(f"scipy.optimize not importable: {exc}") from exc
        return sco

    @classmethod
    def probe(cls) -> EngineProbe:
        try:
            sco = cls._load_library()
        except AdapterUnavailable as exc:
            return EngineProbe(engine=cls.engine, available=False, reason=str(exc))
        import scipy

        return EngineProbe(
            engine=cls.engine,
            available=True,
            algorithms=[s.name for s in cls.ALGORITHMS],
            version=str(getattr(scipy, "__version__", "unknown")),
            supports_equality=True,
        )

    # ------------------------------------------------------------------
    def solve(
        self,
        problem: CompiledProblem,
        algorithm: Optional[str] = None,
        options: Optional[Mapping[str, Any]] = None,
    ) -> SolveResult:
        sco = self._load_library()
        opts = dict(options or {})
        algo = self.algorithm(algorithm)
        self.check_supported(problem, algo)
        method = str(algo.engine_name)

        max_iter = int(opts.get("max_iter", 500))
        ftol = float(opts.get("ftol", 1e-10))
        max_eval = int(opts.get("max_eval", 20000))
        x0 = np.asarray(opts.get("x0", problem.x0()), dtype=float)

        history: List[HistoryPoint] = []
        n_eval = 0

        def objective(x):
            nonlocal n_eval
            n_eval += 1
            return problem.objective(x)

        constraints = []
        for name, fn, jac in problem.ineq:
            constraints.append(
                {
                    "type": "ineq",
                    "fun": (lambda z, _fn=fn: np.asarray(_fn(problem.values(z)), dtype=float)),
                    **({"jac": (lambda z, _jac=jac: np.asarray(_jac(problem.values(z)), dtype=float))} if jac is not None else {}),
                }
            )
        for name, fn, jac in problem.eq:
            constraints.append(
                {
                    "type": "eq",
                    "fun": (lambda z, _fn=fn: np.asarray(_fn(problem.values(z)), dtype=float)),
                    **({"jac": (lambda z, _jac=jac: np.asarray(_jac(problem.values(z)), dtype=float))} if jac is not None else {}),
                }
            )

        def callback(xk, *args, **kwargs):
            xk = np.asarray(xk, dtype=float)
            history.append(
                HistoryPoint(
                    iteration=len(history) + 1,
                    objective=float(problem.sign * problem.objective(xk)),
                    constraint_violation=problem.max_violation(xk),
                    x=problem.values(xk),
                )
            )

        scipy_options: Dict[str, Any] = {"maxiter": max_iter}
        if method == "SLSQP":
            scipy_options["ftol"] = ftol
        elif method == "L-BFGS-B":
            scipy_options["ftol"] = ftol
            scipy_options["maxfun"] = max_eval
        elif method == "Nelder-Mead":
            scipy_options.update({"xatol": ftol, "fatol": ftol, "maxfev": max_eval})
        elif method == "Powell":
            scipy_options.update({"xtol": ftol, "ftol": ftol, "maxfev": max_eval})
        elif method == "COBYLA":
            scipy_options.update({"tol": max(ftol, 1e-12), "maxiter": max_iter})
        elif method == "trust-constr":
            scipy_options.update({"gtol": ftol, "xtol": ftol, "maxiter": max_iter})

        kwargs: Dict[str, Any] = dict(
            fun=objective,
            x0=x0,
            method=method,
            bounds=problem.bounds,
            callback=callback,
            options=scipy_options,
        )
        if algo.gradient:
            kwargs["jac"] = lambda z: problem.gradient(z)
        if method in ("SLSQP", "COBYLA", "trust-constr"):
            kwargs["constraints"] = constraints

        t0 = time.perf_counter()
        try:
            res = sco.minimize(**kwargs)
            elapsed = time.perf_counter() - t0
        except Exception as exc:  # engine-side failure is an explicit outcome
            elapsed = time.perf_counter() - t0
            return self.finalize(
                problem,
                algorithm=algo.name,
                x=x0,
                status=SolveStatus.FAILED.value,
                n_iter=len(history),
                n_eval=n_eval,
                elapsed_s=elapsed,
                history=history,
                message=f"scipy.minimize raised {type(exc).__name__}: {exc}",
                options=opts,
            )

        x_best = np.asarray(res.x, dtype=float)
        multipliers = None
        if method == "SLSQP":
            mult = getattr(res, "multipliers", None)
            if isinstance(mult, dict) and problem.ineq:
                eq_part = np.asarray(mult.get("eqlin", []), dtype=float)
                ineq_part = np.asarray(mult.get("ineqlin", []), dtype=float)
                if ineq_part.size == len(problem.ineq):
                    multipliers = ineq_part
        if res.success:
            status = SolveStatus.CONVERGED.value
        elif res.status in (1, 2, 3, 9) or "iteration" in str(res.message).lower():
            status = SolveStatus.MAX_ITERATIONS.value
        else:
            status = SolveStatus.FAILED.value

        return self.finalize(
            problem,
            algorithm=algo.name,
            x=x_best,
            status=status,
            n_iter=int(getattr(res, "nit", len(history))),
            n_eval=int(getattr(res, "nfev", n_eval)),
            elapsed_s=elapsed,
            history=history,
            message=str(getattr(res, "message", "")),
            options=opts,
            multipliers=multipliers,
        )