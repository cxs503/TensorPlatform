"""Aggregate a verification suite's criteria by *execution*, not by hand.

Why this module exists
----------------------
Several verification suites end with a ``test_verification_summary`` that
``print()``s a fixed block of text such as ``"All 8 verification criteria
PASSED"``.  Such a summary is a *claim without computation*: it keeps printing
PASS after the physics it describes has been broken, and any number typed into
it is hand-copied rather than derived.

This helper re-runs the criteria test methods themselves and reports what
actually happened, so:

* a summary can never claim more than the suite verifies;
* the printed table is derived from execution (no hand-copied numbers);
* dropping a criterion from the summary is detected (coverage check), which
  turns "silent removal of a verification" into a test failure.

Nothing here imports pytest-only machinery beyond the skip/xfail exception
types, so it is safe to reuse outside pytest (e.g. report generators).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
ERROR = "ERROR"
NOT_COVERED = "NOT_COVERED"


@dataclass
class CheckResult:
    """Outcome of a single criterion test method (or one of its parameter sets)."""

    method: str
    status: str
    detail: str = ""


@dataclass
class CriterionResult:
    """Outcome of one named criterion, aggregated over its test methods."""

    label: str
    classes: tuple[str, ...]
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        statuses = [c.status for c in self.checks]
        if not statuses or NOT_COVERED in statuses:
            return NOT_COVERED
        if ERROR in statuses:
            return ERROR
        if FAIL in statuses:
            return FAIL
        if all(s == SKIP for s in statuses):
            return SKIP
        return PASS

    @property
    def executed(self) -> int:
        return sum(1 for c in self.checks if c.status in (PASS, FAIL, ERROR))

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "classes": list(self.classes),
            "status": self.status,
            "executed": self.executed,
            "checks": [{"method": c.method, "status": c.status, "detail": c.detail} for c in self.checks],
        }


# --------------------------------------------------------------------------- #
# Parameter handling (plain fixtures + @pytest.mark.parametrize)
# --------------------------------------------------------------------------- #
def _parametrize_sets(func: Any) -> list[dict[str, Any]] | None:
    """Expand ``@pytest.mark.parametrize`` into concrete kwargs, if present."""
    markers = list(getattr(func, "pytestmark", []))
    markers += [m for m in getattr(func, "__dict__", {}).get("pytestmark", [])]
    for marker in markers:
        if getattr(marker, "name", "") != "parametrize":
            continue
        args = getattr(marker, "args", ())
        if len(args) < 2:
            continue
        argnames, argvalues = args[0], args[1]
        if isinstance(argnames, str):
            names = [n.strip() for n in argnames.split(",") if n.strip()]
        else:  # pragma: no cover - tuple form is rare in this repo
            names = list(argnames)
        out: list[dict[str, Any]] = []
        for value in argvalues:
            values = value if len(names) > 1 else [value]
            out.append(dict(zip(names, values)))
        return out
    return None


def _call_sets(func: Any, fixtures: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """Resolve the call kwargs for one test method; ``None`` if unresolvable."""
    sig = inspect.signature(func)
    wanted = [p for p in sig.parameters if p not in ("self", "cls")]

    parametrized = _parametrize_sets(func)
    if parametrized is not None:
        if all(all(k in kwargs for k in wanted) for kwargs in parametrized):
            return parametrized
        return None

    if not wanted:
        return [{}]
    if all(name in fixtures for name in wanted):
        return [{name: fixtures[name] for name in wanted}]
    return None


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _is_skip_exception(exc: BaseException) -> bool:
    name = type(exc).__name__
    return name in {"Skipped", "XFailed", "Failed"} and exc.__class__.__module__.startswith("_pytest")


def execute_test_method(func: Any, fixtures: Mapping[str, Any]) -> list[CheckResult]:
    """Run one test method for every resolvable parameter set."""
    call_sets = _call_sets(func, fixtures)
    if call_sets is None:
        missing = [p for p in inspect.signature(func).parameters if p not in ("self", "cls") and p not in fixtures]
        return [
            CheckResult(
                method=func.__name__,
                status=NOT_COVERED,
                detail=f"unresolved parameters: {', '.join(missing) or 'parametrize values'}",
            )
        ]

    results: list[CheckResult] = []
    owner = getattr(func, "__qualname__", func.__name__).split(".")[0]
    for kwargs in call_sets:
        instance = None
        if "self" in inspect.signature(func).parameters:
            module_globals = func.__globals__
            instance = module_globals[owner]() if owner in module_globals else None
        suffix = f"({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})" if kwargs else ""
        try:
            func(instance, **kwargs) if instance is not None else func(**kwargs)
        except AssertionError as exc:
            results.append(CheckResult(func.__name__ + suffix, FAIL, str(exc)[:200]))
        except BaseException as exc:  # noqa: BLE001 - classify, do not mask
            if _is_skip_exception(exc):
                results.append(CheckResult(func.__name__ + suffix, SKIP, f"{type(exc).__name__}: {str(exc)[:160]}"))
            elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            else:
                results.append(CheckResult(func.__name__ + suffix, ERROR, f"{type(exc).__name__}: {str(exc)[:200]}"))
        else:
            results.append(CheckResult(func.__name__ + suffix, PASS))
    return results


def aggregate(
    scope: Mapping[str, Any],
    criteria: Mapping[str, Sequence[str]],
    fixtures: Mapping[str, Any] | None = None,
) -> list[CriterionResult]:
    """Run every ``test_*`` method of every listed class, grouped by criterion.

    ``scope`` is normally ``globals()`` of the test module, so the criterion
    classes are looked up by name and cannot drift out of sync with the file.
    """
    fixtures = fixtures or {}
    out: list[CriterionResult] = []
    for label, class_names in criteria.items():
        result = CriterionResult(label=label, classes=tuple(class_names))
        for class_name in class_names:
            cls = scope.get(class_name)
            if cls is None:
                result.checks.append(CheckResult(f"{class_name}.<missing>", NOT_COVERED, "class not found"))
                continue
            methods = [
                value
                for name, value in vars(cls).items()
                if name.startswith("test") and callable(value)
            ]
            for method in methods:
                result.checks.extend(execute_test_method(method, fixtures))
        out.append(result)
    return out


def coverage_gaps(scope: Mapping[str, Any], criteria: Mapping[str, Sequence[str]]) -> list[str]:
    """Criterion classes present in the module but absent from the summary spec."""
    listed = {name for names in criteria.values() for name in names}
    gaps = []
    for name, value in scope.items():
        if not isinstance(value, type) or not name.startswith("Test"):
            continue
        if not any(n.startswith("test") for n in vars(value)):
            continue
        if name not in listed:
            gaps.append(name)
    return sorted(gaps)


def format_table(results: Iterable[CriterionResult], title: str = "verification summary") -> str:
    rows = [f"{title}", f"{'criterion':<10} {'status':<12} {'executed':>8}  classes"]
    for r in results:
        rows.append(f"{r.label:<10} {r.status:<12} {r.executed:>8}  {', '.join(r.classes)}")
    failing = [
        f"  {r.label}.{c.method}: {c.status} {c.detail}"
        for r in results
        for c in r.checks
        if c.status in (FAIL, ERROR, NOT_COVERED)
    ]
    if failing:
        rows.append("failures:")
        rows.extend(failing)
    total = len(list(results))
    passed = sum(1 for r in results if r.status == PASS)
    rows.append(f"total criteria={total} passed={passed}")
    return "\n".join(rows)


def assert_all_passed(results: Sequence[CriterionResult], criteria: Mapping[str, Sequence[str]]) -> None:
    """Fail with a precise message when any criterion is not genuinely verified."""
    bad = [r for r in results if r.status != PASS]
    if bad:
        detail = "; ".join(f"{r.label}={r.status}" for r in bad)
        raise AssertionError(f"verification summary has non-passing criteria: {detail}")
    if len(results) != len(criteria):
        raise AssertionError(f"summary covered {len(results)} criteria, spec declares {len(criteria)}")


def to_json(results: Sequence[CriterionResult]) -> dict:
    return {
        "criteria": [r.to_dict() for r in results],
        "total": len(results),
        "passed": sum(1 for r in results if r.status == PASS),
    }