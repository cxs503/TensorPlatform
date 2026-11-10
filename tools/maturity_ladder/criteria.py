"""Five-level maturity transition criteria — data-driven, machine-checkable (p251w2-n03).

Each level transition is a **set of checks** that must all pass for the
capability to move up one rung.  A check is a simple predicate:

    ``metric_name`` ``op`` ``threshold``

e.g. ``unit_test_pass_rate >= 0.90`` or ``benchmark_error < 0.15``.

The criteria are **data** (a JSON-serialisable spec), never code branches.
The default registry below is the platform's pre-declared five-rung ladder;
a deployment may inject its own registry (with different tolerances) via
``TransitionCriteria`` — the engine only evaluates what it is given.

**Zero-domain-semantics**: metric names are free-form strings; the engine
never interprets them.  A ``synth.*`` capability in the tests exercises the
same machinery as a real one.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .model import MATURITY_LEVELS, LEVEL_RANK, level_rank

#: Comparison operators (pure math, no domain meaning).
OPS = {
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "==": operator.eq,
    "!=": operator.ne,
}


def _numeric(value: Any) -> Optional[float]:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class TransitionCheck:
    """One machine-checkable predicate for a level transition.

    ``name`` is the metric key in an :class:`~tools.maturity_ladder.model.EvidenceBundle`
    ``metrics`` dict; ``op`` is a comparison operator; ``value`` is the threshold.
    """

    name: str
    op: str
    value: Any
    description: str = ""

    def evaluate(self, metrics: Mapping[str, Any]) -> Dict[str, Any]:
        """Evaluate this check against a metric dict.  Returns a machine-readable record."""
        op_fn = OPS.get(self.op)
        record: Dict[str, Any] = {
            "check": self.name,
            "op": self.op,
            "threshold": self.value,
            "ok": False,
            "observed": None,
        }
        if op_fn is None:
            record["error"] = f"unknown operator {self.op!r}"
            return record
        if self.name not in metrics:
            record["error"] = f"metric {self.name!r} missing from evidence"
            return record
        observed = metrics[self.name]
        record["observed"] = observed
        # Boolean thresholds only with ==
        if isinstance(self.value, bool):
            if self.op != "==":
                record["error"] = "boolean threshold only allowed with =="
                return record
            record["ok"] = bool(observed) == self.value
            return record
        # Numeric comparison
        num = _numeric(observed)
        if num is None:
            record["error"] = f"metric {self.name!r} is not numeric"
            return record
        record["ok"] = bool(op_fn(num, float(self.value)))
        return record

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "op": self.op,
            "value": self.value,
            "description": self.description,
        }


@dataclass(frozen=True)
class TransitionCriteria:
    """The set of checks that must **all** pass for a level transition.

    ``from_level`` / ``to_level`` name the two rungs; ``checks`` is the
    conjunction (all must pass).  ``tolerance`` is the configurable error
    bound used by error-based checks (benchmark / conservation) — it is
    **pre-declared** and cannot be adjusted after observing results.
    """

    from_level: str
    to_level: str
    checks: Tuple[TransitionCheck, ...]
    tolerance: float = 0.15
    version: str = "maturity-ladder-criteria/1.0"

    def __post_init__(self) -> None:
        if self.from_level not in LEVEL_RANK:
            raise ValueError(f"未知 from_level {self.from_level!r}")
        if self.to_level not in LEVEL_RANK:
            raise ValueError(f"未知 to_level {self.to_level!r}")
        if level_rank(self.to_level) <= level_rank(self.from_level):
            raise ValueError(
                f"to_level {self.to_level!r} 必须高于 from_level {self.from_level!r}"
            )

    def evaluate(self, metrics: Mapping[str, Any]) -> Dict[str, Any]:
        """Evaluate all checks against a metric dict.  All must pass."""
        results = [c.evaluate(metrics) for c in self.checks]
        passed = all(r["ok"] for r in results)
        failures = [r for r in results if not r["ok"]]
        return {
            "from_level": self.from_level,
            "to_level": self.to_level,
            "tolerance": self.tolerance,
            "passed": passed,
            "checks": results,
            "failures": failures,
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "from_level": self.from_level,
            "to_level": self.to_level,
            "tolerance": self.tolerance,
            "version": self.version,
            "checks": [c.as_dict() for c in self.checks],
        }


# ── Default five-rung ladder (pre-declared, platform standard) ─────────────
def _check(name: str, op: str, value: Any, desc: str = "") -> TransitionCheck:
    return TransitionCheck(name=name, op=op, value=value, description=desc)


#: L1→L2: unit test pass rate ≥ 90% + code coverage ≥ 80%
L1_TO_L2 = TransitionCriteria(
    from_level="Implemented",
    to_level="Tested",
    checks=(
        _check("unit_test_pass_rate", ">=", 0.90, "单元测试通过率 ≥ 90%"),
        _check("code_coverage", ">=", 0.80, "代码覆盖率 ≥ 80%"),
    ),
)

#: L2→L3: benchmark error < tolerance + grid/time-step convergence verified
L2_TO_L3 = TransitionCriteria(
    from_level="Tested",
    to_level="Benchmark-verified",
    checks=(
        _check("benchmark_error", "<", 0.15, "基准案例误差 < 容忍度"),
        _check("convergence_verified", "==", True, "网格/时间步收敛性验证通过"),
    ),
)

#: L3→L4: cross-solver coupling test passed + conservation error < tolerance
L3_TO_L4 = TransitionCriteria(
    from_level="Benchmark-verified",
    to_level="Coupled prototype",
    checks=(
        _check("coupling_test_passed", "==", True, "跨求解器耦合测试通过"),
        _check("conservation_error", "<", 0.15, "守恒量误差 < 容忍度"),
    ),
)

#: L4→L5: independent review + real engineering case + uncertainty quantification
L4_TO_L5 = TransitionCriteria(
    from_level="Coupled prototype",
    to_level="Engineering-qualified",
    checks=(
        _check("independent_review_passed", "==", True, "独立复核通过"),
        _check("engineering_case_validated", "==", True, "实际工程案例验证通过"),
        _check("uncertainty_quantified", "==", True, "不确定度量化完成"),
    ),
)

#: Default registry: the five-rung ladder in order.
DEFAULT_CRITERIA: Dict[str, TransitionCriteria] = {
    "L1_TO_L2": L1_TO_L2,
    "L2_TO_L3": L2_TO_L3,
    "L3_TO_L4": L3_TO_L4,
    "L4_TO_L5": L4_TO_L5,
}


def transition_checks(registry: Optional[Mapping[str, TransitionCriteria]] = None) -> Dict[str, Any]:
    """Return the transition criteria as a JSON-serialisable dict (for reports)."""
    reg = registry or DEFAULT_CRITERIA
    return {k: v.as_dict() for k, v in reg.items()}


def evaluate_transition(
    criteria: TransitionCriteria,
    metrics: Mapping[str, Any],
) -> Dict[str, Any]:
    """Evaluate one transition criteria against a metric dict (pure function)."""
    return criteria.evaluate(metrics)
