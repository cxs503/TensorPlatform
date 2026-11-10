"""Injection bench: does the metamorphic gate actually catch a forged number?

Three forgery classes are injected into the **candidate** (second) implementation
of a target capability:

* ``scale_1p05`` — the reported value is multiplied by 1.05 (a plausible
  "unit-conversion / efficiency correction" forgery);
* ``offset``     — a constant is added, sized as a fixed fraction of the
  reference magnitude at the base probe;
* ``sign_flip``  — the reported value is negated (a classic transcription bug
  dressed up as a result).

The gate detects an injection when **at least one** applicable relation fails.
The same gate is run, unchanged, on the untouched control population; any alarm
there is a false positive and is reported as such (the gate is not allowed to be
"detect-everything").
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from . import metamorphic
from .model import (
    HandleSpec,
    HandleVerdict,
    InjectionType,
    RelationResult,
    RelationStatus,
)

Oracle = metamorphic.Oracle

OFFSET_FRACTION = 0.05  # offset injection = 5% of the reference magnitude


def inject(candidate: Oracle, kind: InjectionType, reference_value: float = 1.0) -> Oracle:
    """Return a candidate implementation carrying one forgery class."""
    if kind is InjectionType.SCALE_1P05:
        return lambda p: 1.05 * candidate(p)
    if kind is InjectionType.OFFSET:
        span = abs(reference_value) if reference_value != 0.0 else 1.0
        delta = OFFSET_FRACTION * span
        return lambda p: candidate(p) + delta
    if kind is InjectionType.SIGN_FLIP:
        return lambda p: -candidate(p)
    raise KeyError(kind)


def gate(handle: HandleSpec, candidate: Oracle, reference: Oracle | None = None
         ) -> HandleVerdict:
    """Evaluate every declared relation of a handle on a candidate implementation."""
    results: List[RelationResult] = []
    for spec in handle.relations:
        results.append(metamorphic.evaluate(spec, handle, candidate, reference))
    return HandleVerdict(handle_id=handle.handle_id,
                         handle_class=handle.handle_class,
                         results=tuple(results))


def run_bench(specs: Sequence[HandleSpec],
              injections: Sequence[InjectionType] = tuple(InjectionType),
              handle_factory=None) -> Dict[str, Any]:
    """Run injection + false-positive calibration over a handle population."""
    from . import handles as H  # local import keeps the module graph acyclic

    factory = handle_factory or H.build_oracles
    rows: List[Dict[str, Any]] = []
    controls: List[Dict[str, Any]] = []
    clean: List[Dict[str, Any]] = []

    for spec in specs:
        reference, second = factory(spec)
        base_value = reference(spec.probes["base"])

        # 1) clean state: honest second implementation must not be flagged
        verdict = gate(spec, second, reference)
        clean.append({
            "handle_id": spec.handle_id,
            "handle_class": spec.handle_class,
            "flagged": verdict.flagged,
            "n_applicable": len(verdict.applicable),
            "failed_kinds": [r.kind.value for r in verdict.failed],
            "max_residual_ratio": _max_ratio(verdict),
        })

        if spec.handle_class == "control":
            controls.append(clean[-1])
            continue

        # 2) injected state: each forgery class must be detected
        for kind in injections:
            forged = inject(second, kind, base_value)
            v = gate(spec, forged, reference)
            detecting = [r.kind.value for r in v.failed]
            rows.append({
                "handle_id": spec.handle_id,
                "ledger_proxy_for": spec.ledger_proxy_for,
                "injection": kind.value,
                "detected": bool(detecting),
                "detecting_kinds": detecting,
                "max_residual_ratio": _max_ratio(v),
                "relation_results": [{"kind": r.kind.value, "status": r.status.value,
                                      "residual": r.residual, "tolerance": r.tolerance}
                                     for r in v.applicable],
            })

    summary = _summarise(rows, controls, clean, injections)
    return {
        "clean": clean,
        "controls": controls,
        "injections": rows,
        "summary": summary,
    }


def _max_ratio(verdict: HandleVerdict) -> float:
    """Largest residual/tolerance ratio over the applicable relations."""
    ratios = []
    for r in verdict.applicable:
        if r.tolerance > 0:
            ratios.append(r.residual / r.tolerance)
    return max(ratios) if ratios else 0.0


def _summarise(rows: List[Dict[str, Any]], controls: List[Dict[str, Any]],
               clean: List[Dict[str, Any]], injections: Sequence[InjectionType]
               ) -> Dict[str, Any]:
    per_type: Dict[str, Dict[str, Any]] = {}
    for kind in injections:
        subset = [r for r in rows if r["injection"] == kind.value]
        hits = sum(1 for r in subset if r["detected"])
        per_type[kind.value] = {
            "handles": len(subset),
            "detected": hits,
            "rate": (hits / len(subset)) if subset else 0.0,
            "detecting_kinds": sorted({k for r in subset for k in r["detecting_kinds"]}),
        }
    total = len(rows)
    hits = sum(1 for r in rows if r["detected"])
    false_alarms = [c["handle_id"] for c in controls if c["flagged"]]
    clean_alarms = [c["handle_id"] for c in clean if c["flagged"]]
    return {
        "per_injection": per_type,
        "injection_total": total,
        "injection_detected": hits,
        "injection_rate": (hits / total) if total else 0.0,
        "controls": len(controls),
        "false_alarms": false_alarms,
        "false_alarm_count": len(false_alarms),
        "false_alarm_rate": (len(false_alarms) / len(controls)) if controls else 0.0,
        "clean_alarms_all_handles": clean_alarms,
        "clean_alarm_count": len(clean_alarms),
    }


def detection_matrix(rows: Sequence[Mapping[str, Any]],
                     kinds: Sequence[str]) -> Dict[str, Dict[str, int]]:
    """relation kind x injection type -> number of injections it caught."""
    matrix = {k: {kind: 0 for kind in kinds} for k in kinds}
    for row in rows:
        for k in row["detecting_kinds"]:
            matrix.setdefault(k, {kk: 0 for kk in kinds})
            matrix[k][row["injection"]] += 1
    return matrix


def relation_coverage(clean_rows: Sequence[Mapping[str, Any]],
                      verdicts: Sequence[HandleVerdict]) -> Dict[str, int]:
    """Count how many times each relation kind was applicable and passed."""
    stats: Dict[str, Dict[str, int]] = {}
    for v in verdicts:
        for r in v.results:
            if r.status is RelationStatus.NOT_APPLICABLE:
                continue
            slot = stats.setdefault(r.kind.value, {"applicable": 0, "passed": 0})
            slot["applicable"] += 1
            if r.status is RelationStatus.PASS:
                slot["passed"] += 1
    return stats