"""Domain-neutral data contract for the unified optimizer toolchain.

Three objects make up the contract:

``ProblemSpec``
    Serialisable description of an optimisation problem (variables, objective,
    constraints, optional analytic gradients, reference optimum).  It is the
    payload of ``schemas/optimizer_problem.v1.schema.json``.

``SolveResult``
    Standardised output of *any* engine: optimal point, objective, convergence
    status, iteration history, constraint violation, KKT residual.  Payload of
    ``schemas/optimizer_result.v1.schema.json``.

``EngineProbe``
    What an adapter can actually do in *this* interpreter (library present?
    version? which algorithms?), so a missing third-party solver is recorded as
    ``unavailable`` instead of silently faking a number.

Nothing in this module knows what the optimised quantity physically means: the
contract is deliberately free of domain vocabulary.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence

SCHEMA_PROBLEM = "optimizer/problem/v1"
SCHEMA_RESULT = "optimizer/result/v1"

#: Acceptable statuses of a solve.  ``unavailable`` / ``unsupported`` are
#: first-class outcomes: an absent library or an algorithm that cannot handle
#: the constraint mix must be reported, never papered over.
STATUSES = (
    "converged",
    "max_iterations",
    "infeasible",
    "unsupported",
    "unavailable",
    "failed",
)


class SolveStatus(str, Enum):
    CONVERGED = "converged"
    MAX_ITERATIONS = "max_iterations"
    INFEASIBLE = "infeasible"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class ContractError(ValueError):
    """Malformed problem specification."""


# ---------------------------------------------------------------------------
# problem specification
# ---------------------------------------------------------------------------


@dataclass
class VariableSpec:
    name: str
    lower: float
    upper: float
    x0: Optional[float] = None

    def validate(self) -> None:
        if not self.name or not self.name.isidentifier():
            raise ContractError(f"variable name {self.name!r} is not a valid identifier")
        if not (math.isfinite(self.lower) and math.isfinite(self.upper)):
            raise ContractError(f"variable {self.name}: bounds must be finite")
        if self.lower > self.upper:
            raise ContractError(f"variable {self.name}: lower > upper")
        if self.x0 is not None and not (self.lower <= self.x0 <= self.upper):
            raise ContractError(f"variable {self.name}: x0 outside bounds")

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name, "lower": self.lower, "upper": self.upper}
        if self.x0 is not None:
            d["x0"] = self.x0
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "VariableSpec":
        return cls(
            name=str(d["name"]),
            lower=float(d["lower"]),
            upper=float(d["upper"]),
            x0=None if d.get("x0") is None else float(d["x0"]),
        )


@dataclass
class ConstraintSpec:
    """One constraint, normalised internally to ``g(x) >= 0`` or ``h(x) == 0``.

    DSL form: ``expr`` compared to ``threshold`` with ``sense`` in
    ``{"le", "ge", "eq"}``.  Internally a ``le`` constraint becomes
    ``threshold - expr >= 0`` so that every engine receives the same canonical
    ``g >= 0`` convention.
    """

    name: str
    expr: str
    kind: str = "ineq"          # "ineq" | "eq"
    sense: str = "le"           # "le" | "ge" (inequality only)
    threshold: float = 0.0
    jac: Optional[List[str]] = None

    def validate(self) -> None:
        if self.kind not in ("ineq", "eq"):
            raise ContractError(f"constraint {self.name}: kind must be 'ineq' or 'eq'")
        if self.kind == "ineq" and self.sense not in ("le", "ge"):
            raise ContractError(f"constraint {self.name}: sense must be 'le' or 'ge'")
        if self.kind == "eq" and self.sense not in ("eq", "le", "ge"):
            raise ContractError(f"constraint {self.name}: equality sense must be 'eq'")
        if not isinstance(self.expr, str) or not self.expr.strip():
            raise ContractError(f"constraint {self.name}: empty expression")
        if self.jac is not None and not isinstance(self.jac, list):
            raise ContractError(f"constraint {self.name}: jac must be a list of expressions")

    @property
    def canonical_sense(self) -> str:
        """``ge`` (inequality ≥ 0) or ``eq`` (equality == 0)."""
        if self.kind == "eq":
            return "eq"
        return "ge"

    def to_dict(self, include_reference: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "expr": self.expr,
            "threshold": self.threshold,
        }
        if self.kind == "ineq":
            d["sense"] = self.sense
        if self.jac is not None:
            d["jac"] = list(self.jac)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ConstraintSpec":
        return cls(
            name=str(d["name"]),
            expr=str(d["expr"]),
            kind=str(d.get("kind", "ineq")),
            sense=str(d.get("sense", "le")),
            threshold=float(d.get("threshold", 0.0)),
            jac=None if d.get("jac") is None else [str(e) for e in d["jac"]],
        )


@dataclass
class ObjectiveSpec:
    expr: str
    sense: str = "minimize"     # "minimize" | "maximize"
    gradient: Optional[List[str]] = None

    def validate(self) -> None:
        if self.sense not in ("minimize", "maximize"):
            raise ContractError("objective.sense must be 'minimize' or 'maximize'")
        if not isinstance(self.expr, str) or not self.expr.strip():
            raise ContractError("objective.expr must be a non-empty string")
        if self.gradient is not None and len(self.gradient) == 0:
            raise ContractError("objective.gradient must be a non-empty list when given")

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"expr": self.expr, "sense": self.sense}
        if self.gradient is not None:
            d["gradient"] = list(self.gradient)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ObjectiveSpec":
        return cls(
            expr=str(d["expr"]),
            sense=str(d.get("sense", "minimize")),
            gradient=None if d.get("gradient") is None else [str(e) for e in d["gradient"]],
        )


@dataclass
class ProblemSpec:
    problem_id: str
    variables: List[VariableSpec]
    objective: ObjectiveSpec
    constraints: List[ConstraintSpec] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA_PROBLEM

    # -- validation ---------------------------------------------------------
    def validate(self) -> "ProblemSpec":
        if self.schema != SCHEMA_PROBLEM:
            raise ContractError(f"problem schema must be {SCHEMA_PROBLEM!r}, got {self.schema!r}")
        if not self.problem_id:
            raise ContractError("problem_id must not be empty")
        if not self.variables:
            raise ContractError("problem needs at least one variable")
        seen = set()
        for v in self.variables:
            v.validate()
            if v.name in seen:
                raise ContractError(f"duplicate variable {v.name!r}")
            seen.add(v.name)
        self.objective.validate()
        names = [v.name for v in self.variables]
        if self.objective.gradient is not None and len(self.objective.gradient) != len(names):
            raise ContractError("objective.gradient length must equal number of variables")
        cseen = set()
        for c in self.constraints:
            c.validate()
            if c.name in cseen:
                raise ContractError(f"duplicate constraint {c.name!r}")
            cseen.add(c.name)
            if c.jac is not None and len(c.jac) != len(names):
                raise ContractError(f"constraint {c.name}: jac length must equal number of variables")
        return self

    # -- serialisation ------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "schema": self.schema,
            "problem_id": self.problem_id,
            "variables": [v.to_dict() for v in self.variables],
            "objective": self.objective.to_dict(),
            "constraints": [c.to_dict() for c in self.constraints],
        }
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ProblemSpec":
        return cls(
            problem_id=str(d["problem_id"]),
            variables=[VariableSpec.from_dict(v) for v in d["variables"]],
            objective=ObjectiveSpec.from_dict(d["objective"]),
            constraints=[ConstraintSpec.from_dict(c) for c in d.get("constraints", [])],
            metadata=dict(d.get("metadata", {})),
            schema=str(d.get("schema", SCHEMA_PROBLEM)),
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    @property
    def variable_names(self) -> List[str]:
        return [v.name for v in self.variables]

    @property
    def n_variables(self) -> int:
        return len(self.variables)

    def fingerprint(self) -> str:
        """Stable content hash of the problem definition (evidence linking)."""
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    @property
    def reference(self) -> Dict[str, Any]:
        return dict(self.metadata.get("reference", {}))


# ---------------------------------------------------------------------------
# standardised result
# ---------------------------------------------------------------------------


@dataclass
class HistoryPoint:
    iteration: int
    objective: float
    constraint_violation: float
    x: Optional[Dict[str, float]] = None

    def to_dict(self, include_x: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "iteration": int(self.iteration),
            "objective": float(self.objective),
            "constraint_violation": float(self.constraint_violation),
        }
        if include_x and self.x is not None:
            d["x"] = {k: float(v) for k, v in self.x.items()}
        return d


def environment_provenance() -> Dict[str, Any]:
    """Interpreter / platform fingerprint attached to every result."""
    versions: Dict[str, str] = {}
    for mod in ("numpy", "scipy", "nlopt", "pyoptsparse", "jsonschema"):
        try:
            __import__(mod)
        except Exception:  # pragma: no cover - defensive
            continue
        try:
            from importlib.metadata import version as _pkg_version

            versions[mod] = str(_pkg_version(mod))
        except Exception:  # pragma: no cover - fallback for vendored builds
            versions[mod] = "unknown"
    return {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "library_versions": versions,
    }


def finite_or_none(value: Any) -> Optional[float]:
    """JSON-safe float: non-finite values become ``null`` (no number is claimed)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


@dataclass
class SolveResult:
    problem_id: str
    engine: str
    algorithm: str
    status: str
    success: bool
    x: Dict[str, float]
    objective: float
    constraint_violation: float
    n_iter: int
    n_eval: int
    elapsed_s: float
    history: List[HistoryPoint] = field(default_factory=list)
    kkt: Optional[Dict[str, float]] = None
    message: str = ""
    problem_fingerprint: str = ""
    reference_objective: Optional[float] = None
    options: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA_RESULT

    # -- derived ------------------------------------------------------------
    @property
    def objective_error(self) -> Optional[float]:
        if self.reference_objective is None:
            return None
        return float(self.objective - self.reference_objective)

    @property
    def objective_rel_error(self) -> Optional[float]:
        err = self.objective_error
        if err is None:
            return None
        # scale floor 1.0 keeps the metric finite and meaningful when the
        # reference optimum is zero (then relative error == absolute error)
        denom = max(abs(float(self.reference_objective)), 1.0)
        return float(err / denom)

    @property
    def kkt_residual(self) -> Optional[float]:
        if not self.kkt:
            return None
        return float(self.kkt.get("kkt_residual", float("nan")))

    # -- serialisation ------------------------------------------------------
    def to_dict(self, history_limit: Optional[int] = 200, include_x: bool = True) -> Dict[str, Any]:
        hist = self.history
        if history_limit is not None and len(hist) > history_limit:
            keep = hist[: history_limit // 2] + hist[-(history_limit // 2):]
        else:
            keep = hist
        d: Dict[str, Any] = {
            "schema": self.schema,
            "problem_id": self.problem_id,
            "problem_fingerprint": self.problem_fingerprint,
            "engine": self.engine,
            "algorithm": self.algorithm,
            "status": self.status,
            "success": bool(self.success),
            "x": {k: finite_or_none(v) for k, v in self.x.items()},
            "objective": finite_or_none(self.objective),
            "constraint_violation": finite_or_none(None if self.constraint_violation is None
                                                  else max(0.0, float(self.constraint_violation))),
            "n_iter": int(self.n_iter),
            "n_eval": int(self.n_eval),
            "elapsed_s": float(self.elapsed_s),
            "message": str(self.message),
            "history": [h.to_dict(include_x) for h in keep],
            "history_total": len(hist),
            "options": dict(self.options),
            "provenance": dict(self.provenance) or environment_provenance(),
        }
        if self.kkt is not None:
            d["kkt"] = {k: float(v) for k, v in self.kkt.items() if math.isfinite(float(v))}
        if self.reference_objective is not None:
            d["reference_objective"] = finite_or_none(self.reference_objective)
            err = self.objective_error
            rel = self.objective_rel_error
            d["objective_error"] = finite_or_none(err) if err is not None else None
            d["objective_rel_error"] = finite_or_none(rel) if rel is not None else None
        return d

    def to_json(self, indent: int = 2, history_limit: Optional[int] = 200) -> str:
        return json.dumps(self.to_dict(history_limit=history_limit), indent=indent)

    @classmethod
    def unavailable(
        cls,
        problem: ProblemSpec,
        engine: str,
        algorithm: str,
        reason: str,
    ) -> "SolveResult":
        """Result for an engine that is not installed -- explicit, never faked."""
        return cls(
            problem_id=problem.problem_id,
            engine=engine,
            algorithm=algorithm,
            status=SolveStatus.UNAVAILABLE.value,
            success=False,
            x={v.name: float("nan") for v in problem.variables},
            objective=float("nan"),
            constraint_violation=float("nan"),
            n_iter=0,
            n_eval=0,
            elapsed_s=0.0,
            message=reason,
            problem_fingerprint=problem.fingerprint(),
            reference_objective=_reference_value(problem),
            provenance=environment_provenance(),
        )


def _reference_value(problem: ProblemSpec) -> Optional[float]:
    ref = problem.reference.get("objective")
    return None if ref is None else float(ref)


# ---------------------------------------------------------------------------
# engine probe
# ---------------------------------------------------------------------------


@dataclass
class EngineProbe:
    engine: str
    available: bool
    algorithms: List[str] = field(default_factory=list)
    missing_algorithms: List[str] = field(default_factory=list)
    version: str = "unavailable"
    supports_equality: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine": self.engine,
            "available": bool(self.available),
            "algorithms": list(self.algorithms),
            "missing_algorithms": list(self.missing_algorithms),
            "version": self.version,
            "supports_equality": bool(self.supports_equality),
            "reason": self.reason,
        }