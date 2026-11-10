"""pyoptsparse adapter: SNOPT / IPOPT / SLSQP through one unified interface.

pyoptsparse is *not* available in the platform's loongarch64 interpreter (no
distribution on the local index; SNOPT is additionally commercial and IPOPT
needs a native build).  The adapter is therefore written against pyoptsparse's
real API and **probes** at import time:

* when ``pyoptsparse`` is importable, ``solve`` builds an
  :class:`pyoptsparse.Optimization` problem (``addVar`` / ``addObj`` / ``addCon``),
  hands it to ``OPT(algorithm)`` with an analytic or finite-difference
  sensitivity function, and maps ``sol.optInform`` onto the unified status;
* when it is not importable, the adapter raises :class:`AdapterUnavailable` and
  the toolchain records ``status = "unavailable"`` -- never a fabricated
  optimum.

The API path is exercised in the test-suite through a stub module injected into
``sys.modules`` (an interface-contract test, not evidence that SNOPT produces
numbers on this machine).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional

import numpy as np

from ..compiled import CompiledProblem
from ..contract import EngineProbe, HistoryPoint, SolveResult, SolveStatus
from .base import AdapterUnavailable, AlgorithmSpec, OptimizerAdapter, ProblemNotSupported

_SPECS = {
    "SNOPT": (True, True, "sparse SQP, commercial licence required"),
    "IPOPT": (True, True, "interior point, needs native build"),
    "SLSQP": (True, True, "dense SQP"),
    "NLPQLP": (True, True, "sequential quadratic programming"),
    "PSQP": (True, True, "sequential quadratic programming"),
}


class PyOptSparseAdapter(OptimizerAdapter):
    engine = "pyoptsparse"
    DEFAULT_ALGORITHM = "SNOPT"
    ALGORITHMS = tuple(
        AlgorithmSpec(name=name, engine_name=name, ineq=v[0], eq=v[1], gradient=True, notes=v[2])
        for name, v in _SPECS.items()
    )

    @classmethod
    def _load_library(cls):
        try:
            import pyoptsparse  # noqa: F401
        except Exception as exc:
            raise AdapterUnavailable(f"pyoptsparse not importable: {exc}") from exc
        return pyoptsparse

    @classmethod
    def probe(cls) -> EngineProbe:
        try:
            lib = cls._load_library()
        except AdapterUnavailable as exc:
            return EngineProbe(
                engine=cls.engine,
                available=False,
                algorithms=[],
                missing_algorithms=[s.name for s in cls.ALGORITHMS],
                version="unavailable",
                supports_equality=True,
                reason=str(exc),
            )
        # ask pyoptsparse which optimizers it was built with
        built: List[str] = []
        try:
            from pyoptsparse import OPT

            built = [o.upper() for o in getattr(OPT, "get_optimizers", lambda: [])()]
        except Exception:
            built = [s.name for s in cls.ALGORITHMS]
        missing = [s.name for s in cls.ALGORITHMS if s.name not in built]
        return EngineProbe(
            engine=cls.engine,
            available=bool(built),
            algorithms=built,
            missing_algorithms=missing,
            version=str(getattr(lib, "__version__", "unknown")),
            supports_equality=True,
            reason="" if built else "pyoptsparse present but compiled without optimizers",
        )

    # ------------------------------------------------------------------
    def solve(
        self,
        problem: CompiledProblem,
        algorithm: Optional[str] = None,
        options: Optional[Mapping[str, Any]] = None,
    ) -> SolveResult:
        self._load_library()
        from pyoptsparse import Optimization, OPT

        opts = dict(options or {})
        algo = self.algorithm(algorithm)
        self.check_supported(problem, algo)

        max_iter = int(opts.get("max_iter", 500))
        x0 = np.asarray(opts.get("x0", problem.x0()), dtype=float)
        names = problem.names
        history: List[HistoryPoint] = []
        n_eval = 0
        objective_name = "obj"

        def objfunc(xdict):
            """pyoptsparse expects objective *and* constraint values in one dict."""
            nonlocal n_eval
            n_eval += 1
            xv = np.array([float(xdict[n]) for n in names], dtype=float)
            history.append(
                HistoryPoint(
                    iteration=n_eval,
                    objective=float(problem.raw_objective(xv)),
                    constraint_violation=problem.max_violation(xv),
                    x=problem.values(xv),
                )
            )
            vals = {objective_name: problem.objective(xv)}
            for name, fn, _jac in problem.ineq:
                vals[name] = float(fn(problem.values(xv)))
            for name, fn, _jac in problem.eq:
                vals[name] = float(fn(problem.values(xv)))
            return vals

        def sens(xdict, funcs):
            xv = np.array([float(xdict[n]) for n in names], dtype=float)
            grad = problem.gradient(xv)
            out: Dict[str, Dict[str, np.ndarray]] = {objective_name: {n: np.array([grad[i]]) for i, n in enumerate(names)}}
            for name, fn, jac in problem.ineq:
                if jac is None:
                    continue
                g = np.asarray(jac(problem.values(xv)), dtype=float)
                out[name] = {n: np.array([g[i]]) for i, n in enumerate(names)}
            for name, fn, jac in problem.eq:
                if jac is None:
                    continue
                g = np.asarray(jac(problem.values(xv)), dtype=float)
                out[name] = {n: np.array([g[i]]) for i, n in enumerate(names)}
            return out

        opt_prob = Optimization(problem.spec.problem_id, objfunc)
        for i, var in enumerate(problem.spec.variables):
            opt_prob.addVar(var.name, "c", value=float(x0[i]), lower=float(var.lower), upper=float(var.upper))
        opt_prob.addObj(objective_name)
        for name, fn, jac in problem.ineq:
            opt_prob.addCon(name, "i", lower=0.0)
        for name, fn, jac in problem.eq:
            opt_prob.addCon(name, "e", lower=0.0, upper=0.0)

        pyopt_options = {"Major iterations limit": max_iter, "Minor iterations limit": 500, "Print file": ""}
        pyopt_options.update({k: v for k, v in opts.items() if isinstance(k, str) and " " in k})
        opt = OPT(algo.engine_name, options=pyopt_options)

        t0 = time.perf_counter()
        try:
            sol = opt(opt_prob, sens=sens)
            elapsed = time.perf_counter() - t0
        except Exception as exc:
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
                message=f"pyoptsparse raised {type(exc).__name__}: {exc}",
                options=opts,
            )

        x_star = sol.xStar
        if isinstance(x_star, dict):
            x_out = np.array([float(x_star[n]) for n in names], dtype=float)
        else:  # pragma: no cover - defensive
            x_out = np.asarray(x_star, dtype=float)

        inform = getattr(sol, "optInform", None)
        code = None
        if isinstance(inform, dict) and inform:
            raw = list(inform.values())[0]
            code = int(str(raw)[0]) if str(raw) else None
        elif isinstance(inform, (int, str)):
            code = int(str(inform)[0])
        if code == 1:
            status = SolveStatus.CONVERGED.value
        elif code is not None and code > 1:
            status = SolveStatus.MAX_ITERATIONS.value
        else:
            status = SolveStatus.CONVERGED.value if x_out is not None else SolveStatus.FAILED.value

        return self.finalize(
            problem,
            algorithm=algo.name,
            x=x_out,
            status=status,
            n_iter=len(history),
            n_eval=n_eval,
            elapsed_s=elapsed,
            history=history,
            message=f"pyoptsparse optInform={inform}",
            options=opts,
        )