#!/usr/bin/env python3
"""Case execution + error table (domain-neutral).

The runner resolves the two dotted callables of a case, calls them with the
*same* opaque ``inputs`` payload, and compares the declared quantities.  A
failure inside the code under test never aborts the run: it degrades to
``Verdict.ERROR`` with the exception type/message recorded, because a
verification harness that dies on the first broken solver is useless as an
audit instrument.

Outputs
-------
``run_report`` is a JSON-serialisable dict (sorted keys, no wall clock unless
``stamp=True``) so that two runs on the same environment are byte-identical —
a prerequisite for the sha256 evidence chain in :mod:`evidence`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .model import (
    SCHEMA_VERSION,
    CaseResult,
    CaseSpec,
    Metric,
    Verdict,
    _jsonable,
)
from .registry import CaseRegistry, resolve_callable


@dataclass(frozen=True)
class RunReport:
    """Aggregated outcome of running a registry."""

    results: Tuple[CaseResult, ...]
    domains: Mapping[str, int] = field(default_factory=dict)
    generated_at: Optional[str] = None

    @property
    def counts(self) -> Dict[str, int]:
        counts = {Verdict.PASS.value: 0, Verdict.FAIL.value: 0, Verdict.ERROR.value: 0}
        for result in self.results:
            counts[result.verdict.value] += 1
        counts["total"] = len(self.results)
        counts["quantities"] = sum(len(r.metrics) for r in self.results)
        return counts

    @property
    def ok(self) -> bool:
        return all(r.verdict is Verdict.PASS for r in self.results) and bool(self.results)

    def by_id(self, case_id: str) -> CaseResult:
        for result in self.results:
            if result.case_id == case_id:
                return result
        raise KeyError(case_id)

    def error_table(self) -> List[Dict[str, Any]]:
        """Flat ``case/quantity`` rows — the machine-readable error table."""
        rows: List[Dict[str, Any]] = []
        for result in self.results:
            for metric in result.metrics:
                rows.append({
                    "case_id": result.case_id,
                    "quantity": metric.name,
                    "actual": metric.to_dict()["actual"],
                    "expected": metric.to_dict()["expected"],
                    "abs_error": metric.to_dict()["abs_error"],
                    "rel_error": metric.to_dict()["rel_error"],
                    "rtol": float(metric.tolerance.rtol),
                    "atol": float(metric.tolerance.atol),
                    "verdict": metric.verdict.value,
                })
        return rows

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "counts": self.counts,
            "ok": self.ok,
            "domains": {k: int(v) for k, v in sorted(self.domains.items())},
            "cases": [r.to_dict() for r in self.results],
            "error_table": self.error_table(),
        }
        if self.generated_at:
            payload["generated_at"] = self.generated_at
        return payload


def run_case(spec: CaseSpec,
             resolver: Callable[[str], Callable[..., Any]] = resolve_callable) -> CaseResult:
    """Execute one case and compare every declared quantity."""
    try:
        implementation = resolver(spec.implementation)
        reference = resolver(spec.reference)
    except Exception as exc:  # resolution failure -> honest ERROR, never a silent skip
        return CaseResult(case_id=spec.case_id, verdict=Verdict.ERROR,
                          error=str(exc), error_type=type(exc).__name__)

    inputs = dict(spec.inputs)
    try:
        outputs = implementation(**inputs)
    except Exception as exc:
        return CaseResult(case_id=spec.case_id, verdict=Verdict.ERROR,
                          error=f"implementation raised: {exc}", error_type=type(exc).__name__)
    try:
        expected = reference(**inputs)
    except Exception as exc:
        return CaseResult(case_id=spec.case_id, verdict=Verdict.ERROR,
                          outputs=_as_mapping(outputs),
                          error=f"reference raised: {exc}", error_type=type(exc).__name__)

    if not isinstance(outputs, Mapping):
        return CaseResult(case_id=spec.case_id, verdict=Verdict.ERROR,
                          error=f"implementation must return a mapping, got {type(outputs).__name__}",
                          error_type="TypeError")
    if not isinstance(expected, Mapping):
        return CaseResult(case_id=spec.case_id, verdict=Verdict.ERROR,
                          outputs=_as_mapping(outputs),
                          error=f"reference must return a mapping, got {type(expected).__name__}",
                          error_type="TypeError")

    metrics: List[Metric] = []
    for name, tolerance in spec.quantities:
        if name not in outputs:
            return CaseResult(case_id=spec.case_id, verdict=Verdict.ERROR,
                              outputs=_as_mapping(outputs), expected=_as_mapping(expected),
                              error=f"implementation output lacks quantity {name!r}", error_type="KeyError")
        if name not in expected:
            return CaseResult(case_id=spec.case_id, verdict=Verdict.ERROR,
                              outputs=_as_mapping(outputs), expected=_as_mapping(expected),
                              error=f"reference output lacks quantity {name!r}", error_type="KeyError")
        try:
            metrics.append(Metric.compare(name, outputs[name], expected[name], tolerance))
        except (TypeError, ValueError) as exc:
            return CaseResult(case_id=spec.case_id, verdict=Verdict.ERROR,
                              outputs=_as_mapping(outputs), expected=_as_mapping(expected),
                              error=str(exc), error_type=type(exc).__name__)

    verdict = Verdict.PASS if all(m.verdict is Verdict.PASS for m in metrics) else Verdict.FAIL
    return CaseResult(case_id=spec.case_id, verdict=verdict,
                      metrics=tuple(metrics),
                      outputs=_as_mapping(outputs), expected=_as_mapping(expected))


def _as_mapping(payload: Any) -> Dict[str, Any]:
    return dict(payload) if isinstance(payload, Mapping) else {"_value": payload}


def run_registry(registry: CaseRegistry, *,
                 resolver: Callable[[str], Callable[..., Any]] = resolve_callable,
                 stamp: bool = False) -> RunReport:
    """Run every registered case in ``case_id`` order (deterministic)."""
    results = tuple(run_case(spec, resolver=resolver) for spec in registry)
    return RunReport(
        results=results,
        domains=registry.domains(),
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if stamp else None,
    )


def error_table_markdown(report: RunReport) -> str:
    """Human-readable mirror of :meth:`RunReport.error_table` (all numbers from it)."""
    counts = report.counts
    lines = [
        "# Verification harness — error table",
        "",
        f"cases: {counts['total']} | PASS {counts[Verdict.PASS.value]} | "
        f"FAIL {counts[Verdict.FAIL.value]} | ERROR {counts[Verdict.ERROR.value]} | "
        f"quantities {counts['quantities']}",
        "",
        "Pass rule: `|actual - expected| <= atol + rtol * |expected|`. "
        "`rel_error = |actual - expected| / |expected|` (blank when `expected == 0`).",
        "",
        "| case | quantity | actual | expected | abs_error | rel_error | rtol | atol | verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report.error_table():
        rel = "—" if row["rel_error"] is None else f"{row['rel_error']:.3e}"
        lines.append(
            "| {case_id} | {quantity} | {actual!r} | {expected!r} | {abs_error:.3e} | {rel} | "
            "{rtol:.1e} | {atol:.1e} | {verdict} |".format(rel=rel, **{k: row[k] for k in
                                                                      ("case_id", "quantity", "actual",
                                                                       "expected", "abs_error",
                                                                       "rtol", "atol", "verdict")})
        )
    for result in report.results:
        if result.verdict is Verdict.ERROR:
            lines.append(f"\n* `{result.case_id}` ERROR ({result.error_type}): {result.error}")
    lines.append("")
    return "\n".join(lines)


def write_run_report(report: RunReport, out_dir: Path | str) -> Dict[str, Path]:
    """Write ``report.json`` + ``error_table.md`` (deterministic bytes)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.json"
    table_path = out_dir / "error_table.md"
    report_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    table_path.write_text(error_table_markdown(report), encoding="utf-8")
    return {"report": report_path, "error_table": table_path}