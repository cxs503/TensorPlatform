"""Rule engine: probe numbers → evidence level.

The rules are *ordered* and each one is a named, machine-checkable predicate over
fields recorded in :class:`~tools.mock_detector.model.ProbeReport`.  Nothing is
inferred from endpoint names, payload field names, summaries or documentation —
that is what "zero domain semantics" means here, and it is also what makes the
detector applicable to a domain it has never seen.

Level semantics used by this detector
-------------------------------------
``NOT_SUPPORTED``  the route does not exist (404/405/410/501)
``STUB``           2xx but no numeric output at all: nothing is computed
``MOCK``           numeric output exists, but it is hardcoded (invariant to every
                   swept input), random (non-deterministic), stateful (order
                   dependent), or quantized to a couple of buckets
``WITHHELD``       **no evidence** — unreachable, payload rejected, or no
                   sweepable numeric input.  Never a pass.
``REAL``           structurally consistent with a real computation: deterministic,
                   idempotent, order-independent, and responding smoothly to its
                   numeric inputs.  Reported at ``level_scope="STRUCTURAL"`` with
                   ``accuracy_verified=False``: this level says "the numbers are
                   computed from the inputs", *not* "the numbers are correct".

Rule order matters: the first matching rule wins, and every fired rule is
recorded in ``reason_codes`` so a reviewer can see exactly which predicate
decided the level.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .model import ProbeReport

#: rule table, evaluated top-down
RULE_TABLE: List[Dict[str, Any]] = [
    {"code": "ROUTE_ABSENT", "level": "NOT_SUPPORTED",
     "desc": "HTTP 404/405/410/501: the route does not exist"},
    {"code": "UNREACHABLE", "level": "WITHHELD",
     "desc": "transport error / timeout / non-JSON body / 5xx: no evidence"},
    {"code": "INPUT_REJECTED", "level": "WITHHELD",
     "desc": "HTTP 400/422: no valid payload could be constructed, so nothing was measured"},
    {"code": "NO_NUMERIC_OUTPUT", "level": "STUB",
     "desc": "2xx with zero numeric fields: the endpoint returns no computed quantity"},
    {"code": "NONDETERMINISTIC", "level": "MOCK",
     "desc": "identical request twice gave different numeric vectors beyond tolerance"},
    {"code": "NON_IDEMPOTENT", "level": "MOCK",
     "desc": "repeating the identical request changed the numeric answer"},
    {"code": "ORDER_DEPENDENT_STATE", "level": "MOCK",
     "desc": "answer changed after an unrelated request was interleaved (hidden state)"},
    {"code": "INPUT_INVARIANT", "level": "MOCK",
     "desc": "no numeric output moved on any of >=2 swept input axes: hardcoded output"},
    {"code": "GLOBAL_RESPONSE_ONLY", "level": "WITHHELD",
     "desc": "single-axis sweeps did not move the output but scaling every input at once did: "
             "the true driver is not among the swept fields — undecidable, not hardcoded"},
    {"code": "QUANTIZED_OUTPUT", "level": "MOCK",
     "desc": "<=2 distinct output vectors over >=8 probe samples: a few frozen buckets"},
    {"code": "NO_SWEEPABLE_INPUT", "level": "WITHHELD",
     "desc": "no numeric input could be swept: response shape is unmeasurable"},
    {"code": "STRUCTURAL_PASS", "level": "REAL",
     "desc": "deterministic (directly, or through stable repeats) + idempotent + "
             "order-independent + responds to numeric input"},
]

#: informational flags — recorded, never decide a level
FLAG_TABLE: Dict[str, str] = {
    "VOLATILE_METADATA": "whole-payload hash moved while the numeric vector was stable "
                         "(request id / timestamp in the body — harmless)",
    "PARTIALLY_INVARIANT": ">70% of output fields were constant along every swept axis",
    "NON_MONOTONE_RESPONSE": "at least one output field reversed direction along a sweep "
                             "(legitimate for non-monotone physics, e.g. a hump)",
    "STEPPED_RESPONSE": "max/mean adjacent step ratio > 8: response looks bucketed or piecewise",
    "SINGLE_AXIS_ONLY": "only one input axis could be swept",
    "AXES_REJECTED": "some sweep points were rejected by the endpoint (validity constraints)",
    "BUDGET_EXHAUSTED_IN_SWEEP": "request budget cut the sweep short — coverage is partial",
    "DETERMINISM_SECOND_ATTEMPT": "the determinism partner call failed once and was retried",
    "DETERMINISM_INFERRED_FROM_REPEAT": "direct pair comparison had no shared numeric field; "
                                        "reproducibility was established by the immediate repeats",
    "PROBE_DEADLINE": "per-target wall-clock deadline hit: coverage is partial",
    "GLOBAL_PERTURBATION_SKIPPED": "no numeric input could be jointly scaled",
    "GLOBAL_PERTURBATION_NO_NUMERICS": "joint-perturbation call returned no numeric field",
    "ISOLATION_PROBE_SKIPPED": "no perturbable numeric input, order-independence untested",
    "NO_NUMERIC_OUTPUT": "2xx but the body contains no numeric field at all",
    "INPUT_REJECTED": "the platform refused every candidate payload (400/422)",
    "UNREACHABLE": "no HTTP answer: connection refused / timeout / non-JSON / 5xx",
}

MIN_AXES_FOR_INVARIANCE = 2
MIN_SAMPLES_FOR_CARDINALITY = 8
MAX_DISTINCT_VECTORS_FOR_QUANTIZED = 2
PARTIAL_INVARIANCE_THRESHOLD = 0.70
STEP_RATIO_THRESHOLD = 8.0
INVARIANCE_TOL_FOR_GLOBAL = 1e-9
#: rules whose firing is only meaningful when numeric probes actually ran
MEASURED_RULES = ("NONDETERMINISTIC", "NON_IDEMPOTENT", "ORDER_DEPENDENT_STATE",
                  "INPUT_INVARIANT", "QUANTIZED_OUTPUT", "GLOBAL_RESPONSE_ONLY")


def _absent(report: ProbeReport) -> bool:
    return report.status in (404, 405, 410, 501)


def _unreachable(report: ProbeReport) -> bool:
    if report.error_kind in ("TRANSPORT", "TIMEOUT", "PARSE"):
        return True
    return bool(report.status and 500 <= report.status < 600)


def _rejected(report: ProbeReport) -> bool:
    return bool(report.status and 400 <= report.status < 500
                and not _absent(report) and not report.http_ok)


def _no_numeric(report: ProbeReport) -> bool:
    return bool(report.http_ok) and report.n_samples == 0 and report.det_ok is None


def _nondeterministic(report: ProbeReport) -> bool:
    return report.det_ok is False


def _non_idempotent(report: ProbeReport) -> bool:
    return report.idem_ok is False


def _order_dependent(report: ProbeReport) -> bool:
    return report.idem_isolation_ok is False


def _input_invariant(report: ProbeReport) -> bool:
    """No single axis moves the output AND a joint scaling does not either."""
    if report.axes_probed < MIN_AXES_FOR_INVARIANCE or report.axes_respond != 0:
        return False
    return not (report.global_scale_worst_rel is not None
                and report.global_scale_worst_rel > INVARIANCE_TOL_FOR_GLOBAL)


def _global_response_only(report: ProbeReport) -> bool:
    return (report.axes_probed >= MIN_AXES_FOR_INVARIANCE
            and report.axes_respond == 0
            and report.global_scale_worst_rel is not None
            and report.global_scale_worst_rel > INVARIANCE_TOL_FOR_GLOBAL)


def _quantized(report: ProbeReport) -> bool:
    return (report.n_samples >= MIN_SAMPLES_FOR_CARDINALITY
            and 0 < report.n_distinct_vectors <= MAX_DISTINCT_VECTORS_FOR_QUANTIZED)


def _structural_pass(report: ProbeReport) -> bool:
    """Determinism directly, or (flag it) through a stable immediate repeat."""
    return report.det_ok is True or (report.det_ok is None and report.idem_ok is True)


def _no_sweepable(report: ProbeReport) -> bool:
    return report.axes_probed == 0 and report.axes_respond == 0


PREDICATES = {
    "ROUTE_ABSENT": _absent,
    "UNREACHABLE": _unreachable,
    "INPUT_REJECTED": _rejected,
    "NO_NUMERIC_OUTPUT": _no_numeric,
    "NONDETERMINISTIC": _nondeterministic,
    "NON_IDEMPOTENT": _non_idempotent,
    "ORDER_DEPENDENT_STATE": _order_dependent,
    "INPUT_INVARIANT": _input_invariant,
    "QUANTIZED_OUTPUT": _quantized,
    "GLOBAL_RESPONSE_ONLY": _global_response_only,
    "NO_SWEEPABLE_INPUT": _no_sweepable,
    "STRUCTURAL_PASS": _structural_pass,
}


def classify(report: ProbeReport) -> ProbeReport:
    """Assign ``level`` + ``reason_codes`` + informational ``flags`` in place."""
    codes: List[str] = []
    fired = False
    for rule in RULE_TABLE:
        pred = PREDICATES.get(rule["code"])
        if pred is None or not pred(report):
            continue
        if not fired:
            # the first matching rule fixes the level ...
            report.level = rule["level"]
            report.fired_rule = rule["code"]
            codes.append(rule["code"])
            fired = True
        elif (rule["code"] in MEASURED_RULES and report.det_ok is not None
              or rule["code"] == "NO_SWEEPABLE_INPUT" and report.det_ok is not None):
            # ... but every other predicate that also held is recorded as
            # corroborating evidence (a stateful counter is both non-deterministic
            # and non-idempotent; keeping both lets a reviewer see why).
            codes.append(rule["code"])
    if not fired:                                # pragma: no cover - defensive
        codes.append("UNDECIDED")
        report.level = "WITHHELD"

    # informational flags (never change the level)
    if report.volatile_metadata:
        report.flags.append("VOLATILE_METADATA")
    if report.det_ok is None and report.idem_ok is True:
        report.flags.append("DETERMINISM_INFERRED_FROM_REPEAT")
    if (report.constant_field_fraction is not None
            and report.constant_field_fraction >= PARTIAL_INVARIANCE_THRESHOLD
            and report.axes_probed > 0):
        report.flags.append("PARTIALLY_INVARIANT")
    shapes = [a.get("shape_counts", {}) for a in report.axes if a.get("status") == "ok"]
    if any(s.get("non_monotone") for s in shapes):
        report.flags.append("NON_MONOTONE_RESPONSE")
    if any(float(a.get("max_jump_ratio") or 1.0) > STEP_RATIO_THRESHOLD
           for a in report.axes if a.get("status") == "ok"):
        report.flags.append("STEPPED_RESPONSE")
    if report.axes_probed == 1:
        report.flags.append("SINGLE_AXIS_ONLY")
    if report.axes_rejected:
        report.flags.append("AXES_REJECTED")

    report.reason_codes = sorted(set(codes))
    report.level_scope = "STRUCTURAL"
    report.accuracy_verified = False
    report.flags = sorted(set(report.flags))
    report.detail = _detail(report)
    return report


def _detail(report: ProbeReport) -> str:
    bits = []
    if report.det_worst_rel is not None:
        bits.append(f"det_rel={report.det_worst_rel:.3g}")
    if report.idem_worst_rel is not None:
        bits.append(f"idem_rel={report.idem_worst_rel:.3g}")
    if report.idem_isolation_worst_rel is not None:
        bits.append(f"iso_rel={report.idem_isolation_worst_rel:.3g}")
    if report.axes_probed:
        bits.append(f"axes={report.axes_probed}probed/{report.axes_respond}respond/"
                    f"{report.axes_invariant}invariant")
    if report.n_samples:
        bits.append(f"vectors={report.n_distinct_vectors}/{report.n_samples}")
    if report.n_output_fields:
        bits.append(f"fields={report.n_output_fields}")
    return " ".join(bits)


def rule_table_json() -> List[Dict[str, Any]]:
    """Machine-readable rule table for the report/provenance."""
    return [dict(r) for r in RULE_TABLE]