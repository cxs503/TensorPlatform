"""Adapter contract: one interface, many optimisation engines.

Every engine adapter implements

    probe()                      -> EngineProbe     (what is usable *here*)
    solve(problem, algorithm,...)-> SolveResult     (standardised output)

and declares, per algorithm, whether it can handle inequality and/or equality
constraints.  Two failure modes are **explicit outcomes**, never silent
substitutions:

``AdapterUnavailable``
    the third-party library is not importable in this interpreter;
``ProblemNotSupported``
    the engine/algorithm combination cannot represent the problem (e.g. a
    derivative-free bound-only method asked to satisfy constraints).

The shared :meth:`OptimizerAdapter.finalize` computes constraint violation and
the engine-independent KKT residual so all engines are compared on the same
yardstick.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from ..compiled import CompiledProblem
from ..contract import (
    EngineProbe,
    ProblemSpec,
    SolveResult,
    SolveStatus,
    environment_provenance,
)
from ..kkt import kkt_diagnostics

__all__ = [
    "OptimizerAdapter",
    "AdapterUnavailable",
    "ProblemNotSupported",
    "AlgorithmSpec",
]


class AdapterUnavailable(RuntimeError):
    """Third-party engine (or the requested algorithm) is not usable here."""


class ProblemNotSupported(RuntimeError):
    """The engine/algorithm cannot represent this problem class."""


class AlgorithmSpec:
    """Declarative capability record for one algorithm of one engine."""

    __slots__ = ("name", "engine_name", "ineq", "eq", "gradient", "global_", "notes")

    def __init__(
        self,
        name: str,
        engine_name: Optional[str] = None,
        ineq: bool = False,
        eq: bool = False,
        gradient: bool = True,
        global_: bool = False,
        notes: str = "",
    ):
        self.name = name                  # canonical name used by the toolchain
        self.engine_name = engine_name or name
        self.ineq = ineq
        self.eq = eq
        self.gradient = gradient
        self.global_ = global_
        self.notes = notes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "algorithm": self.name,
            "native_name": self.engine_name,
            "supports_inequality": self.ineq,
            "supports_equality": self.eq,
            "uses_gradient": self.gradient,
            "global_search": self.global_,
            "notes": self.notes,
        }


class OptimizerAdapter(ABC):
    """Base class for engine adapters."""

    engine: str = "abstract"
    ALGORITHMS: Tuple[AlgorithmSpec, ...] = ()
    DEFAULT_ALGORITHM: str = ""

    # -- capability ---------------------------------------------------------
    @classmethod
    def algorithm(cls, name: Optional[str]) -> AlgorithmSpec:
        target = (name or cls.DEFAULT_ALGORITHM).upper()
        for spec in cls.ALGORITHMS:
            if spec.name.upper() == target:
                return spec
        raise AdapterUnavailable(
            f"engine {cls.engine!r} has no algorithm {name!r}; available: "
            f"{[s.name for s in cls.ALGORITHMS]}"
        )

    @classmethod
    def available(cls) -> bool:
        try:
            cls._load_library()
            return True
        except AdapterUnavailable:
            return False

    @classmethod
    def probe(cls) -> EngineProbe:  # pragma: no cover - overridden
        return EngineProbe(engine=cls.engine, available=cls.available())

    # -- hooks --------------------------------------------------------------
    @classmethod
    def _load_library(cls) -> Any:  # pragma: no cover - overridden
        raise AdapterUnavailable(f"{cls.engine} adapter does not implement _load_library")

    @abstractmethod
    def solve(
        self,
        problem: CompiledProblem,
        algorithm: Optional[str] = None,
        options: Optional[Mapping[str, Any]] = None,
    ) -> SolveResult:
        ...

    # -- shared plumbing ----------------------------------------------------
    def check_supported(self, problem: CompiledProblem, algo: AlgorithmSpec) -> None:
        if problem.ineq and not algo.ineq:
            raise ProblemNotSupported(
                f"{self.engine}:{algo.name} cannot handle the {len(problem.ineq)} "
                f"inequality constraint(s) of problem {problem.spec.problem_id}"
            )
        if problem.eq and not algo.eq:
            raise ProblemNotSupported(
                f"{self.engine}:{algo.name} cannot handle the {len(problem.eq)} "
                f"equality constraint(s) of problem {problem.spec.problem_id}"
            )
        if algo.gradient and not problem.has_analytic_gradient:
            # derivative-based methods get a finite-difference gradient; this is
            # recorded in the result message, not hidden.
            pass

    def finalize(
        self,
        problem: CompiledProblem,
        algorithm: str,
        x: Sequence[float],
        status: str,
        n_iter: int,
        n_eval: int,
        elapsed_s: float,
        history: Optional[List] = None,
        message: str = "",
        options: Optional[Mapping[str, Any]] = None,
        multipliers: Optional[np.ndarray] = None,
    ) -> SolveResult:
        x = np.asarray(x, dtype=float)
        violation_map = problem.violations(x)
        kkt = kkt_diagnostics(problem, x, multipliers_ineq=multipliers)
        return SolveResult(
            problem_id=problem.spec.problem_id,
            engine=self.engine,
            algorithm=algorithm,
            status=status,
            success=status == SolveStatus.CONVERGED.value,
            x=problem.values(x),
            objective=problem.raw_objective(x),
            constraint_violation=float(max(violation_map.values()) if violation_map else 0.0),
            n_iter=int(n_iter),
            n_eval=int(n_eval),
            elapsed_s=float(elapsed_s),
            history=list(history or []),
            kkt=kkt,
            message=message,
            problem_fingerprint=problem.spec.fingerprint(),
            reference_objective=_ref(problem.spec),
            options=dict(options or {}),
            provenance=environment_provenance(),
        )


def _ref(spec: ProblemSpec) -> Optional[float]:
    ref = spec.reference.get("objective")
    return None if ref is None else float(ref)


def status_from_bool(success: bool, hit_limit: bool = False) -> str:
    if success:
        return SolveStatus.CONVERGED.value
    return SolveStatus.MAX_ITERATIONS.value if hit_limit else SolveStatus.FAILED.value


def timer() -> float:
    return time.perf_counter()