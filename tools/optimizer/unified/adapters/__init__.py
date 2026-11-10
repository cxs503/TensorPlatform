"""Adapter registry and engine-agnostic dispatch.

``solve(problem_or_spec, engine, algorithm, options)`` is the single entry point
used by the CLI, the cross-check harness and any platform stage.  It guarantees
that a missing engine becomes a ``SolveResult`` with ``status="unavailable"``
(and a ``NaN`` objective that is never compared as a number) rather than an
exception leaking into a batch job.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Union

from ..compiled import CompiledProblem
from ..contract import EngineProbe, ProblemSpec, SolveResult, SolveStatus
from .base import AdapterUnavailable, AlgorithmSpec, OptimizerAdapter, ProblemNotSupported
from .nlopt_adapter import NLoptAdapter
from .pyoptsparse_adapter import PyOptSparseAdapter
from .scipy_adapter import ScipyAdapter

ENGINES = {
    ScipyAdapter.engine: ScipyAdapter,
    NLoptAdapter.engine: NLoptAdapter,
    PyOptSparseAdapter.engine: PyOptSparseAdapter,
}

#: algorithms the toolchain interface must be able to *name*, mapped to the
#: engines that provide them (used by the capability audit)
REQUESTED_ALGORITHMS = ("SLSQP", "L-BFGS-B", "Nelder-Mead", "COBYLA", "SBOA", "MMA", "SNOPT", "IPOPT")


def engine_class(engine: str) -> type:
    key = engine.strip().lower()
    if key not in ENGINES:
        raise KeyError(f"unknown engine {engine!r}; registered: {sorted(ENGINES)}")
    return ENGINES[key]


def get_adapter(engine: str) -> OptimizerAdapter:
    return engine_class(engine)()


def available_engines() -> List[str]:
    return [name for name, cls in ENGINES.items() if cls.available()]


def probe_all() -> List[EngineProbe]:
    return [cls.probe() for cls in ENGINES.values()]


def algorithm_matrix() -> List[Dict[str, Any]]:
    """Flat capability table: one row per (engine, algorithm)."""
    rows: List[Dict[str, Any]] = []
    for probe in probe_all():
        cls = ENGINES[probe.engine]
        for spec in cls.ALGORITHMS:
            rows.append(
                {
                    "engine": probe.engine,
                    "engine_available": probe.available,
                    "engine_version": probe.version,
                    **spec.to_dict(),
                    "available": probe.available and spec.name in probe.algorithms,
                }
            )
    return rows


def probe_requested_algorithms() -> Dict[str, List[str]]:
    """Which engines can serve each algorithm named by the toolchain interface."""
    table: Dict[str, List[str]] = {name: [] for name in REQUESTED_ALGORITHMS}
    for row in algorithm_matrix():
        for requested in REQUESTED_ALGORITHMS:
            if row["algorithm"].upper() == requested.upper() and row["available"]:
                table[requested].append(row["engine"])
    return table


def solve(
    problem: Union[ProblemSpec, CompiledProblem],
    engine: str,
    algorithm: Optional[str] = None,
    options: Optional[Mapping[str, Any]] = None,
) -> SolveResult:
    """Solve ``problem`` with ``engine``; unavailable/unsupported are outcomes."""
    compiled = problem if isinstance(problem, CompiledProblem) else CompiledProblem(problem)
    try:
        adapter = get_adapter(engine)
    except KeyError:
        return SolveResult.unavailable(compiled.spec, str(engine), str(algorithm), f"unknown engine {engine!r}")
    try:
        return adapter.solve(compiled, algorithm=algorithm, options=options)
    except AdapterUnavailable as exc:
        return SolveResult.unavailable(compiled.spec, adapter.engine, str(algorithm), str(exc))
    except ProblemNotSupported as exc:
        spec = compiled.spec
        return SolveResult(
            problem_id=spec.problem_id,
            engine=adapter.engine,
            algorithm=str(algorithm),
            status=SolveStatus.UNSUPPORTED.value,
            success=False,
            x={v.name: float("nan") for v in spec.variables},
            objective=float("nan"),
            constraint_violation=float("nan"),
            n_iter=0,
            n_eval=0,
            elapsed_s=0.0,
            message=str(exc),
            problem_fingerprint=spec.fingerprint(),
        )


__all__ = [
    "ENGINES",
    "OptimizerAdapter",
    "AdapterUnavailable",
    "ProblemNotSupported",
    "AlgorithmSpec",
    "ScipyAdapter",
    "NLoptAdapter",
    "PyOptSparseAdapter",
    "engine_class",
    "get_adapter",
    "available_engines",
    "probe_all",
    "probe_requested_algorithms",
    "algorithm_matrix",
    "solve",
]