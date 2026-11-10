#!/usr/bin/env python3
"""Domain-neutral data model of the verification harness (``verification-harness/1.0``).

A *verification case* is nothing but

    an opaque ``case_id``
  + ``implementation``  (dotted path of the code under test)
  + ``reference``       (dotted path of the independent authority)
  + ``inputs``          (JSON payload handed verbatim to both callables)
  + ``quantities``      (names to compare, each with a tolerance)

Nothing in this module (nor in :mod:`registry`, :mod:`runner`, :mod:`evidence`)
knows what a hull, a route or a sea state is: the *domain* of a case is an
opaque configuration string that the harness never interprets.  All physical
meaning arrives through the injected case package (see
``tools/verification_harness/cases/*.json``), which is exactly the "zero domain
semantics" iron law of this repository.

Error convention
----------------
``abs_error = |actual - expected|``

``rel_error = |actual - expected| / |expected|`` and ``None`` when
``expected == 0`` (a relative error against zero is undefined; such a quantity
can only be judged through ``atol``).  The pass rule is the standard mixed
criterion::

    |actual - expected| <= atol + rtol * |expected|

Both numbers are surfaced in every artifact so an auditor can recompute the
error table by hand (acceptance criterion A2).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = "verification-harness/1.0"

# Number of significant digits kept when MyJSON-serialising measured floats.
# The full-precision value is what gets compared; the rounding only stabilises
# artifacts so that two runs on the same environment are byte-identical.
FLOAT_DIGITS = 12


class ReferenceKind(str, Enum):
    """Where the expected value comes from (never interpreted by the harness)."""

    ANALYTIC = "analytic"
    EXTERNAL_REFERENCE = "external_reference"
    BASELINE = "baseline"


class Verdict(str, Enum):
    """Outcome of a single quantity comparison or of a whole case."""

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"


def absolute_error(actual: float, expected: float) -> float:
    """``|actual - expected|`` (finite-checked)."""
    return abs(float(actual) - float(expected))


def relative_error(actual: float, expected: float) -> Optional[float]:
    """``|actual - expected| / |expected|``, or ``None`` when ``expected == 0``."""
    expected = float(expected)
    if expected == 0.0:
        return None
    return abs(float(actual) - expected) / abs(expected)


def observed_order(fine: Sequence[float], coarse: Sequence[float],
                   refinement: float) -> float:
    """Observed order of accuracy ``p`` from two error levels.

    ``p = log(e_coarse / e_fine) / log(refinement)`` with ``refinement`` the
    factor by which the grid was refined.  Domain-neutral helper used by
    convergence-style cases.
    """
    if len(fine) != len(coarse) or not fine:
        raise ValueError("fine/coarse error lists must be non-empty and equal length")
    if refinement <= 1.0:
        raise ValueError("refinement must be > 1")
    orders = []
    for fine_err, coarse_err in zip(fine, coarse):
        fine_err, coarse_err = abs(float(fine_err)), abs(float(coarse_err))
        if fine_err <= 0.0 or coarse_err <= 0.0:
            raise ValueError("error levels must be strictly positive")
        orders.append(math.log(coarse_err / fine_err) / math.log(refinement))
    return sum(orders) / len(orders)


@dataclass(frozen=True)
class Tolerance:
    """Mixed absolute/relative acceptance band."""

    rtol: float = 1e-6
    atol: float = 0.0

    def __post_init__(self) -> None:
        for name, value in (("rtol", self.rtol), ("atol", self.atol)):
            value = float(value)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"tolerance {name} must be finite and >= 0, got {value!r}")
        if float(self.rtol) >= 1.0:
            raise ValueError(f"rtol must be < 1 (a 100% band is not a verification), got {self.rtol!r}")

    def accepts(self, actual: float, expected: float) -> bool:
        return absolute_error(actual, expected) <= float(self.atol) + float(self.rtol) * abs(float(expected))

    def to_dict(self) -> Dict[str, float]:
        return {"rtol": float(self.rtol), "atol": float(self.atol)}


@dataclass(frozen=True)
class Metric:
    """One compared quantity."""

    name: str
    actual: float
    expected: float
    abs_error: float
    rel_error: Optional[float]
    tolerance: Tolerance
    verdict: Verdict

    @classmethod
    def compare(cls, name: str, actual: float, expected: float,
                tolerance: Tolerance) -> "Metric":
        actual_f, expected_f = float(actual), float(expected)
        if not (math.isfinite(actual_f) and math.isfinite(expected_f)):
            raise ValueError(f"quantity {name!r} is not finite: actual={actual_f!r} expected={expected_f!r}")
        verdict = Verdict.PASS if tolerance.accepts(actual_f, expected_f) else Verdict.FAIL
        return cls(
            name=name,
            actual=actual_f,
            expected=expected_f,
            abs_error=absolute_error(actual_f, expected_f),
            rel_error=relative_error(actual_f, expected_f),
            tolerance=tolerance,
            verdict=verdict,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "actual": _round(self.actual),
            "expected": _round(self.expected),
            "abs_error": _round(self.abs_error),
            "rel_error": None if self.rel_error is None else _round(self.rel_error),
            "tolerance": self.tolerance.to_dict(),
            "verdict": self.verdict.value,
        }


def _round(value: float) -> float:
    """Stable rounding for artifacts (12 significant digits, exact for 0.0)."""
    value = float(value)
    if value == 0.0 or not math.isfinite(value):
        return value
    return float(f"%.{FLOAT_DIGITS}g" % value)


@dataclass(frozen=True)
class CaseSpec:
    """A registered verification case (fully declarative, no domain knowledge)."""

    case_id: str
    implementation: str
    reference: str
    quantities: Tuple[Tuple[str, Tolerance], ...]
    domain: str = ""
    inputs: Mapping[str, Any] = field(default_factory=dict)
    reference_kind: ReferenceKind = ReferenceKind.ANALYTIC
    description: str = ""
    tags: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.case_id).strip():
            raise ValueError("case_id must be a non-empty string")
        if not str(self.implementation).strip():
            raise ValueError(f"case {self.case_id}: implementation path is empty")
        if not str(self.reference).strip():
            raise ValueError(f"case {self.case_id}: reference path is empty")
        if not self.quantities:
            raise ValueError(f"case {self.case_id}: at least one quantity is required")
        names = [name for name, _ in self.quantities]
        if len(set(names)) != len(names):
            raise ValueError(f"case {self.case_id}: duplicate quantity names {names!r}")

    @property
    def quantity_names(self) -> Tuple[str, ...]:
        return tuple(name for name, _ in self.quantities)

    def tolerance_for(self, name: str) -> Tolerance:
        for quantity, tolerance in self.quantities:
            if quantity == name:
                return tolerance
        raise KeyError(f"case {self.case_id}: unknown quantity {name!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "domain": self.domain,
            "implementation": self.implementation,
            "reference": self.reference,
            "reference_kind": self.reference_kind.value,
            "inputs": dict(self.inputs),
            "quantities": {name: tolerance.to_dict() for name, tolerance in self.quantities},
            "description": self.description,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class CaseResult:
    """Outcome of executing one case."""

    case_id: str
    verdict: Verdict
    metrics: Tuple[Metric, ...] = ()
    outputs: Mapping[str, Any] = field(default_factory=dict)
    expected: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    error_type: Optional[str] = None

    @property
    def worst_rel_error(self) -> Optional[float]:
        values = [m.rel_error for m in self.metrics if m.rel_error is not None]
        return max(values) if values else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "verdict": self.verdict.value,
            "metrics": [m.to_dict() for m in self.metrics],
            "outputs": _jsonable(self.outputs),
            "expected": _jsonable(self.expected),
            "error": self.error,
            "error_type": self.error_type,
        }


def _jsonable(value: Any) -> Any:
    """Best-effort JSON-safe projection of a callable's return payload."""
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return _round(float(value))
    return repr(value)