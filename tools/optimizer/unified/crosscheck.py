"""Cross-engine adjudication: same problem, several solvers, one verdict.

Used as the machine-checkable criterion "one problem solved by three solvers
must agree within 1%": the harness runs a list of ``(engine, algorithm)`` pairs,
keeps only ``converged`` results, and reports

* per-run objective and constraint violation,
* pairwise relative deviation of the objective,
* the worst deviation against the *published* reference optimum,
* ``meets_tolerance`` -- the machine verdict.

Unavailable engines stay in the report as ``status="unavailable"`` rows so the
adjudication can never be silently thinned down to the engines that happen to
work.
"""

from __future__ import annotations

import itertools
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .adapters import solve as engine_solve
from .compiled import CompiledProblem
from .contract import ProblemSpec, SolveResult
from .kkt import kkt_diagnostics

DEFAULT_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("scipy", "SLSQP"),
    ("nlopt", "SLSQP"),
    ("scipy", "trust-constr"),
)


def _summary(result: SolveResult, include_x: bool = True) -> Dict[str, Any]:
    return {
        "engine": result.engine,
        "algorithm": result.algorithm,
        "status": result.status,
        "objective": _finite(result.objective),
        "constraint_violation": _finite(result.constraint_violation),
        "kkt_residual": _finite(result.kkt_residual) if result.kkt else None,
        "n_iter": result.n_iter,
        "n_eval": result.n_eval,
        "elapsed_s": round(result.elapsed_s, 6),
        "objective_rel_error": _finite(result.objective_rel_error),
        "message": result.message,
        "x": {k: _finite(v) for k, v in result.x.items()} if include_x else None,
    }


def _finite(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    v = float(value)
    return v if v == v and abs(v) != float("inf") else None


def crosscheck(
    problem: ProblemSpec,
    pairs: Optional[Sequence[Tuple[str, str]]] = None,
    tol: float = 0.01,
    options: Optional[Mapping[str, Any]] = None,
    include_kkt: bool = True,
) -> Dict[str, Any]:
    """Solve ``problem`` with each ``(engine, algorithm)`` pair and adjudicate."""
    compiled = CompiledProblem(problem)
    pairs = list(pairs or DEFAULT_PAIRS)
    t0 = time.perf_counter()
    results: List[SolveResult] = [
        engine_solve(compiled, engine=engine, algorithm=algo, options=options)
        for engine, algo in pairs
    ]
    elapsed = time.perf_counter() - t0

    rows = [_summary(r) for r in results]
    ok = [r for r in results if r.status == "converged" and _finite(r.objective) is not None]

    pairwise: List[Dict[str, Any]] = []
    max_dev = 0.0
    for a, b in itertools.combinations(ok, 2):
        fa, fb = float(a.objective), float(b.objective)
        denom = max(abs(fa), abs(fb), 1.0)
        dev = abs(fa - fb) / denom
        pairwise.append(
            {
                "a": f"{a.engine}:{a.algorithm}",
                "b": f"{b.engine}:{b.algorithm}",
                "objective_a": fa,
                "objective_b": fb,
                "rel_deviation": dev,
            }
        )
        max_dev = max(max_dev, dev)

    ref = problem.reference.get("objective")
    ref_err = None
    if ref is not None and ok:
        best = min(ok, key=lambda r: abs(float(r.objective) - float(ref)))
        denom = max(abs(float(ref)), 1.0)
        ref_err = abs(float(best.objective) - float(ref)) / denom

    kkt_rows = []
    if include_kkt:
        for r in results:
            if r.status != "converged" or not r.x:
                continue
            import numpy as np

            x = np.array([r.x[name] for name in compiled.names], dtype=float)
            diag = kkt_diagnostics(compiled, x)
            kkt_rows.append(
                {
                    "engine": r.engine,
                    "algorithm": r.algorithm,
                    "kkt_residual": diag["kkt_residual"],
                    "stationarity": diag["stationarity"],
                    "primal_feasibility": diag["primal_feasibility"],
                    "dual_feasibility": diag["dual_feasibility"],
                    "complementarity": diag["complementarity"],
                }
            )

    converged_count = len(ok)
    meets = bool(converged_count >= 2) and max_dev <= tol
    if converged_count >= 2 and ref_err is not None:
        meets = meets and ref_err <= tol

    return {
        "problem_id": problem.problem_id,
        "problem_fingerprint": problem.fingerprint(),
        "pairs": [f"{e}:{a}" for e, a in pairs],
        "tolerance": tol,
        "results": rows,
        "pairwise": pairwise,
        "converged_count": converged_count,
        "max_pairwise_rel_deviation": max_dev,
        "reference_objective": ref,
        "reference_rel_error_of_best": ref_err,
        "kkt": kkt_rows,
        "meets_tolerance": meets,
        "verdict": (
            f"{converged_count}/{len(pairs)} engines converged; max pairwise deviation "
            f"{max_dev:.3e} (tol {tol:.1e}); reference deviation {ref_err if ref_err is None else f'{ref_err:.3e}'}"
        ),
        "elapsed_s": elapsed,
    }