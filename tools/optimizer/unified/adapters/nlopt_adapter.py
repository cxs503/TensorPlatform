"""NLopt adapter: derivative-free and gradient-based local/global algorithms.

Canonical algorithm name -> NLopt algorithm constant:

===========  =======================  =====  ====  ======  ======
canonical    nlopt constant           ineq   eq    grad    global
===========  =======================  =====  ====  ======  ======
COBYLA       LN_COBYLA                yes    yes*  no      no
SLSQP        LD_SLSQP                 yes    yes   yes     no
MMA          LD_MMA                   yes    no    yes     no
LBFGS        LD_LBFGS                 no     no    yes     no
BOBYQA       LN_BOBYQA                no     no    no      no
NELDERMEAD   LN_NELDERMEAD            no     no    no      no
SBPLX        LN_SBPLX                 no     no    no      no
DIRECT       GN_DIRECT_L              no     no    no      yes
ISRES        GN_ISRES                 yes    yes*  no      yes
AUGLAG       AUGLAG + LD_SLSQP local  yes    yes   yes     no
===========  =======================  =====  ====  ======  ======

Sign convention trap: NLopt wants inequality constraints as ``g(x) <= 0``,
the opposite of SciPy's ``c(x) >= 0``.  The adapter therefore *negates* both the
value and the gradient of each canonical ``g >= 0`` constraint; getting this
wrong "converges" to grossly infeasible points (verified during development).

``SBOA`` is requested by the toolchain interface but **absent** from the
installed NLopt build (2.11 exposes no ``LN_SBOA``); the probe reports it under
``missing_algorithms`` instead of pretending it exists.

\\* equality support depends on the NLopt build; if NLopt rejects the
constraint the adapter returns status ``unsupported`` with NLopt's message.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional

import numpy as np

from ..compiled import CompiledProblem, fd_gradient
from ..contract import EngineProbe, HistoryPoint, SolveResult, SolveStatus
from .base import AdapterUnavailable, AlgorithmSpec, OptimizerAdapter, ProblemNotSupported

# canonical name -> (nlopt attribute, ineq, eq, gradient, global, notes)
_SPECS = {
    "COBYLA": ("LN_COBYLA", True, True, False, False, "linear approximation trust region"),
    "SLSQP": ("LD_SLSQP", True, True, True, False, "sequential quadratic programming"),
    "MMA": ("LD_MMA", True, False, True, False, "method of moving asymptotes"),
    "LBFGS": ("LD_LBFGS", False, False, True, False, "limited-memory BFGS"),
    "BOBYQA": ("LN_BOBYQA", False, False, False, False, "quadratic local model"),
    "NELDERMEAD": ("LN_NELDERMEAD", False, False, False, False, "simplex"),
    "SBPLX": ("LN_SBPLX", False, False, False, False, "subplex"),
    "DIRECT": ("GN_DIRECT_L", False, False, False, True, "deterministic global"),
    "ISRES": ("GN_ISRES", True, True, False, True, "stochastic global, evolution strategy"),
    "AUGLAG": ("AUGLAG", True, True, True, False, "augmented Lagrangian + local SLSQP"),
}

#: algorithms the toolchain interface advertises but that this NLopt build lacks
_REQUESTED_BUT_MISSING = ("SBOA",)


def _nlopt():
    try:
        import nlopt  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on interpreter
        raise AdapterUnavailable(f"nlopt not importable: {exc}") from exc
    return nlopt


class NLoptAdapter(OptimizerAdapter):
    engine = "nlopt"
    DEFAULT_ALGORITHM = "COBYLA"
    ALGORITHMS = tuple(
        AlgorithmSpec(
            name=name,
            engine_name=res[0],
            ineq=res[1],
            eq=res[2],
            gradient=res[3],
            global_=res[4],
            notes=res[5],
        )
        for name, res in _SPECS.items()
    )

    @classmethod
    def _load_library(cls):
        nlopt = _nlopt()
        missing = [n for n in _SPECS.values() if not hasattr(nlopt, n[0])]
        return nlopt

    @classmethod
    def probe(cls) -> EngineProbe:
        try:
            nlopt = _nlopt()
        except AdapterUnavailable as exc:
            return EngineProbe(engine=cls.engine, available=False, reason=str(exc))
        available = [name for name, res in _SPECS.items() if hasattr(nlopt, res[0])]
        missing = list(_REQUESTED_BUT_MISSING)
        return EngineProbe(
            engine=cls.engine,
            available=bool(available),
            algorithms=available,
            missing_algorithms=missing,
            version=str(getattr(nlopt, "__version__", "unknown")),
            supports_equality=True,
            reason=(
                "SBOA requested by the toolchain interface but not present in this "
                "NLopt build (no LN_SBOA symbol)"
                if missing
                else ""
            ),
        )

    # ------------------------------------------------------------------
    def solve(
        self,
        problem: CompiledProblem,
        algorithm: Optional[str] = None,
        options: Optional[Mapping[str, Any]] = None,
    ) -> SolveResult:
        nlopt = _nlopt()
        opts = dict(options or {})
        algo = self.algorithm(algorithm)
        self.check_supported(problem, algo)
        if not hasattr(nlopt, algo.engine_name):
            raise AdapterUnavailable(
                f"nlopt build {getattr(nlopt, '__version__', '?')} lacks {algo.engine_name}"
            )

        max_eval = int(opts.get("max_eval", opts.get("max_iter", 2000)))
        ftol = float(opts.get("ftol", 1e-10))
        xtol = float(opts.get("xtol", 1e-10))
        x0 = np.asarray(opts.get("x0", problem.x0()), dtype=float)

        opt = nlopt.opt(getattr(nlopt, algo.engine_name), problem.n)
        opt.set_lower_bounds(problem.lower.tolist())
        opt.set_upper_bounds(problem.upper.tolist())
        opt.set_maxeval(max_eval)
        opt.set_ftol_rel(ftol)
        opt.set_ftol_abs(ftol)
        opt.set_xtol_rel(xtol)

        history: List[HistoryPoint] = []
        n_eval = 0

        def objective(x, grad):
            nonlocal n_eval
            n_eval += 1
            xv = np.asarray(x, dtype=float)
            value = problem.objective(xv)
            history.append(
                HistoryPoint(
                    iteration=n_eval,
                    objective=float(problem.raw_objective(xv)),
                    constraint_violation=problem.max_violation(xv),
                    x=problem.values(xv),
                )
            )
            if grad.size > 0:
                grad[:] = problem.gradient(xv)
            return float(value)

        opt.set_min_objective(objective)

        # --- constraints ---------------------------------------------------
        # NLopt's convention is g(x) <= 0, the canonical form here is g >= 0, and
        # gradient-based NLopt algorithms require constraint gradients: build one
        # callback that handles the sign flip and falls back to finite differences
        # when the problem ships no analytic Jacobian.
        def _constraint_cb(fn, jac, sign):
            def callback(x, grad):
                xv = np.asarray(x, dtype=float)
                vals = problem.values(xv)
                value = sign * float(fn(vals))
                if grad.size > 0:
                    if jac is not None:
                        g = np.asarray(jac(vals), dtype=float)
                    else:
                        g = fd_gradient(lambda z: float(fn(problem.values(z))), xv)
                    grad[:] = sign * g
                return value

            return callback

        for name, fn, jac in problem.ineq:
            try:
                opt.add_inequality_constraint(_constraint_cb(fn, jac, -1.0), 1e-10)
            except Exception as exc:
                return self.finalize(
                    problem,
                    algorithm=algo.name,
                    x=x0,
                    status=SolveStatus.UNSUPPORTED.value,
                    n_iter=0,
                    n_eval=0,
                    elapsed_s=0.0,
                    history=[],
                    message=f"nlopt rejected inequality constraint {name}: {exc}",
                    options=opts,
                )
        for name, fn, jac in problem.eq:
            try:
                opt.add_equality_constraint(_constraint_cb(fn, jac, 1.0), 1e-10)
            except Exception as exc:
                return self.finalize(
                    problem,
                    algorithm=algo.name,
                    x=x0,
                    status=SolveStatus.UNSUPPORTED.value,
                    n_iter=0,
                    n_eval=0,
                    elapsed_s=0.0,
                    history=[],
                    message=f"nlopt rejected equality constraint {name}: {exc}",
                    options=opts,
                )

        if algo.name == "AUGLAG":
            local = nlopt.opt(nlopt.LD_SLSQP, problem.n)
            local.set_ftol_rel(ftol)
            local.set_xtol_rel(xtol)
            opt.set_local_optimizer(local)

        t0 = time.perf_counter()
        try:
            x_out = np.asarray(opt.optimize(x0.tolist()), dtype=float)
            elapsed = time.perf_counter() - t0
        except nlopt.RoundoffLimited as exc:
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
                message=f"nlopt RoundoffLimited: {exc}",
                options=opts,
            )
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
                message=f"nlopt raised {type(exc).__name__}: {exc}",
                options=opts,
            )

        code = int(opt.last_optimize_result())
        message = _result_name(nlopt, code)
        if code in (nlopt.SUCCESS, nlopt.STOPVAL_REACHED, nlopt.FTOL_REACHED, nlopt.XTOL_REACHED):
            status = SolveStatus.CONVERGED.value
        elif code in (nlopt.MAXEVAL_REACHED, nlopt.MAXTIME_REACHED):
            status = SolveStatus.MAX_ITERATIONS.value
        else:
            status = SolveStatus.FAILED.value

        return self.finalize(
            problem,
            algorithm=algo.name,
            x=x_out,
            status=status,
            n_iter=int(getattr(opt, "get_numevals", lambda: len(history))()),
            n_eval=n_eval,
            elapsed_s=elapsed,
            history=history,
            message=message,
            options=opts,
        )


def _result_name(nlopt, code: int) -> str:
    names = {
        nlopt.FAILURE: "FAILURE",
        nlopt.INVALID_ARGS: "INVALID_ARGS",
        nlopt.OUT_OF_MEMORY: "OUT_OF_MEMORY",
        nlopt.ROUNDOFF_LIMITED: "ROUNDOFF_LIMITED",
        nlopt.FORCED_STOP: "FORCED_STOP",
        nlopt.SUCCESS: "SUCCESS",
        nlopt.STOPVAL_REACHED: "STOPVAL_REACHED",
        nlopt.FTOL_REACHED: "FTOL_REACHED",
        nlopt.XTOL_REACHED: "XTOL_REACHED",
        nlopt.MAXEVAL_REACHED: "MAXEVAL_REACHED",
        nlopt.MAXTIME_REACHED: "MAXTIME_REACHED",
    }
    return f"nlopt_result={code} ({names.get(code, 'unknown')})"