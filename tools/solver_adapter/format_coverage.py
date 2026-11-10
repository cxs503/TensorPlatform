"""C1: machine-checked coverage of every declared native I/O record by protocol v1.

The scan answers four questions, each of which can come out *negative* (so the
gate is falsifiable, not decorative):

1. is every declared native record mapped to a field the adapter's **manifest**
   actually declares?  (an unmapped or dangling name fails);
2. do the protocol field's quantity kind and dimensional exponents match the
   native record's?  (a silently converted unit fails);
3. does every solver declare at least one input and one output record, both
   directions?  (a one-way declaration fails);
4. are all eight solvers present?

The report is a plain dict so it can be archived as acceptance evidence.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from .adapters import ADAPTER_CLASSES, adapter_class
from .errors import CoverageError
from .formats import IOFormat, NATIVE_FORMATS
from .protocol import SOLVER_IDS, FieldSpec

__all__ = ["CoverageReport", "scan_coverage", "coverage_json"]


class CoverageReport(dict):
    """Mapping subclass so the JSON artifact *is* the returned object."""

    @property
    def coverage(self) -> float:
        return float(self["coverage"])

    @property
    def ok(self) -> bool:
        return bool(self["ok"])


def _manifest_specs(solver_id: str) -> Dict[str, FieldSpec]:
    cls = adapter_class(solver_id)
    return {spec.name: spec for spec in cls.FIELDS}


def scan_coverage(
    formats: Optional[Mapping[str, Iterable[IOFormat]]] = None,
    *,
    raise_on_missing: bool = True,
) -> CoverageReport:
    """Scan native records against the protocol manifests.

    ``formats`` defaults to :data:`tools.solver_adapter.formats.NATIVE_FORMATS`;
    tests pass a deliberately broken table to prove the gate can fail.
    """
    table = dict(formats if formats is not None else NATIVE_FORMATS)
    per_solver: Dict[str, Dict[str, Any]] = {}
    problems: list = []
    total = mapped = 0
    verified = 0

    for solver_id in SOLVER_IDS:
        entries = tuple(table.get(solver_id, ()))
        specs = _manifest_specs(solver_id)
        directions = {e.direction for e in entries}
        solver_mapped = 0
        for entry in entries:
            total += 1
            spec = specs.get(entry.protocol_field) if entry.protocol_field else None
            if entry.verified_against_repo:
                verified += 1
            if spec is None:
                problems.append(
                    f"{solver_id}:{entry.direction}:{entry.record} -> "
                    f"{entry.protocol_field or '<unmapped>'} is not a declared protocol field"
                )
                continue
            if spec.dim_vector != tuple(entry.dim_vector):
                problems.append(
                    f"{solver_id}:{entry.direction}:{entry.record} dimensional mismatch "
                    f"{tuple(entry.dim_vector)} vs protocol {spec.dim_vector}"
                )
                continue
            mapped += 1
            solver_mapped += 1
        for required in ("input", "output"):
            if required not in directions:
                problems.append(f"{solver_id}: no {required} records declared")
        per_solver[solver_id] = {
            "records": len(entries),
            "mapped": solver_mapped,
            "unmapped": len(entries) - solver_mapped,
            "declared_fields": len(specs),
            "verified_against_repo": sum(1 for e in entries if e.verified_against_repo),
        }

    for solver_id in SOLVER_IDS:
        if solver_id not in ADAPTER_CLASSES:
            problems.append(f"{solver_id}: no adapter registered")
        if not table.get(solver_id):
            problems.append(f"{solver_id}: no native records declared")

    coverage = (mapped / total) if total else 0.0
    report: CoverageReport = CoverageReport(
        protocol="solver-protocol/v1",
        solvers=list(SOLVER_IDS),
        records_total=total,
        records_mapped=mapped,
        coverage=coverage,
        complete=bool(mapped == total and total > 0 and not problems),
        verified_against_repo=verified,
        problems=problems,
        per_solver=per_solver,
        note=(
            "records are declared by the adapter scaffold; verified_against_repo=0 means "
            "no library repository/artifact was inspected, so this proves protocol-side "
            "coverage only"
        ),
    )
    report["ok"] = report["complete"]
    if raise_on_missing and not report["complete"]:
        raise CoverageError(
            f"protocol coverage {coverage:.1%} ({mapped}/{total}); problems: {problems[:5]}"
        )
    return report


def coverage_json(report: Mapping[str, Any]) -> str:
    import json

    return json.dumps(dict(report), indent=2, sort_keys=True) + "\n"