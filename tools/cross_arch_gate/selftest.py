"""Mutation harness: measure the gate's own detection and false-positive rates.

The mission's third and fourth criteria are statements about the *judge*, not
about the machines — "an injected out-of-band difference is always flagged, and
nothing else ever is".  Those can only be measured by controlled mutation: take
a known-good arm, inject a defect of a known class, and check what the gate says.

Seven mutation families are used, chosen so that each one maps onto exactly one
expected label:

============================  ====================================================
mutation                      expected label
============================  ====================================================
``clean``                     whatever the measured arm actually is (bit-identical
                              for a same-config rerun, within band otherwise)
``perturb_1e-9``              OVER_TOLERANCE  (10x above the tolerance band)
``perturb_1e-6``              OVER_TOLERANCE  (gross, catch obvious misses)
``perturb_1e-13``             WITHIN_TOLERANCE (below the band: a *false positive*
                              would be recorded here)
``nudge_4ulp``                WITHIN_TOLERANCE (inside the ULP cap)
``inject_nan``                NAN with ``ok = False`` (single-sided NaN)
``flip_int``                  OVER_TOLERANCE (integers admit no tolerance)
``drop_field``                OVER_TOLERANCE with ``detail = missing_field``
``zero_abs_shift``            OVER_TOLERANCE — an exact-zero field has no relative
                              scale, so it must be shifted in absolute units
============================  ====================================================
"""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from . import classify, fixtures, gate, numerics


# --------------------------------------------------------------------------- #
# record mutations (operate on encoded records, never on live floats)
# --------------------------------------------------------------------------- #
def _fp_names(rec: Mapping[str, Any], kind: str = fixtures.FP) -> List[str]:
    return sorted(n for n, row in rec["fields"].items() if row["kind"] == kind)


def _int_names(rec: Mapping[str, Any]) -> List[str]:
    return sorted(n for n, row in rec["fields"].items() if row["kind"] == fixtures.INT)


def _clone(rec: Mapping[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(dict(rec))
    return out


def _seal(rec: Dict[str, Any]) -> Dict[str, Any]:
    from .record import field_digest
    rec["digest"] = field_digest(rec["fields"])
    rec["field_count"] = len(rec["fields"])
    return rec


def mutate_scale(rec: Mapping[str, Any], factor: float, names: Iterable[str]) -> Dict[str, Any]:
    out = _clone(rec)
    for name in names:
        value = numerics.decode_hex(out["fields"][name]["hex"])
        new = value * factor
        out["fields"][name] = {"kind": out["fields"][name]["kind"],
                               "hex": numerics.hex_token(new),
                               "bits": format(numerics.bits(new), "016x")}
    return _seal(out)


def mutate_nudge(rec: Mapping[str, Any], steps: int, names: Iterable[str]) -> Dict[str, Any]:
    out = _clone(rec)
    for name in names:
        value = numerics.decode_hex(out["fields"][name]["hex"])
        for _ in range(abs(steps)):
            value = math.nextafter(value, math.inf if steps > 0 else -math.inf)
        out["fields"][name] = {"kind": out["fields"][name]["kind"],
                               "hex": numerics.hex_token(value),
                               "bits": format(numerics.bits(value), "016x")}
    return _seal(out)


def mutate_nan(rec: Mapping[str, Any], names: Iterable[str]) -> Dict[str, Any]:
    out = _clone(rec)
    for name in names:
        out["fields"][name] = {"kind": out["fields"][name]["kind"],
                               "hex": float("nan").hex(),
                               "bits": format(numerics.bits(float("nan")), "016x")}
    return _seal(out)


def mutate_shift(rec: Mapping[str, Any], delta: float, names: Iterable[str]) -> Dict[str, Any]:
    """Absolute shift — the only meaningful perturbation for an exact-zero field.

    Measured trap (first pipeline run): scaling an exact ``0.0`` by ``1 + 1e-9``
    returns ``0.0``, so a relative mutation silently does nothing and shows up as
    a missed detection.  A zero-valued field has no relative scale, so it must be
    injected with an absolute offset instead.
    """
    out = _clone(rec)
    for name in names:
        value = numerics.decode_hex(out["fields"][name]["hex"]) + delta
        out["fields"][name] = {"kind": out["fields"][name]["kind"],
                               "hex": numerics.hex_token(value),
                               "bits": format(numerics.bits(value), "016x")}
    return _seal(out)


def mutate_int(rec: Mapping[str, Any], names: Iterable[str]) -> Dict[str, Any]:
    out = _clone(rec)
    for name in names:
        out["fields"][name] = {"kind": fixtures.INT,
                               "int": int(out["fields"][name]["int"]) + 1}
    return _seal(out)


def mutate_drop(rec: Mapping[str, Any], names: Iterable[str]) -> Dict[str, Any]:
    out = _clone(rec)
    for name in names:
        out["fields"].pop(name, None)
    return _seal(out)


# --------------------------------------------------------------------------- #
# the matrix
# --------------------------------------------------------------------------- #
#: Mutation families, in report order.  Exposed as data so the report can cite the
#: count instead of hard-coding prose that silently drifts when a family is added.
MUTATION_FAMILIES: tuple = (
    ("perturb_1e-9", classify.OVER_TOLERANCE),
    ("perturb_1e-6", classify.OVER_TOLERANCE),
    ("perturb_1e-13", classify.WITHIN_TOLERANCE),
    ("nudge_4ulp", classify.WITHIN_TOLERANCE),
    ("inject_nan", classify.NAN),
    ("flip_int", classify.OVER_TOLERANCE),
    ("drop_field", classify.OVER_TOLERANCE),
    ("zero_abs_shift", classify.OVER_TOLERANCE),
)


def _injections(rec: Mapping[str, Any], width: int) -> List[Dict[str, Any]]:
    fp = _fp_names(rec)
    zeros = [n for n in fp if numerics.decode_hex(rec["fields"][n]["hex"]) == 0.0]
    nonzero = [n for n in fp if n not in set(zeros)]
    pick_fp = nonzero[::max(1, len(nonzero) // width)][:width]
    pick_zero = zeros[: max(1, width // 2)]
    ints = _int_names(rec)
    pick_int = ints[::max(1, len(ints) // max(1, width // 2))][:max(1, width // 2)]
    return [
        {"id": "clean", "expected": None, "record": _clone(rec),
         "injected": []},
        {"id": "perturb_1e-9", "expected": classify.OVER_TOLERANCE,
         "record": mutate_scale(rec, 1.0 + 1e-9, pick_fp), "injected": pick_fp},
        {"id": "perturb_1e-6", "expected": classify.OVER_TOLERANCE,
         "record": mutate_scale(rec, 1.0 + 1e-6, pick_fp), "injected": pick_fp},
        {"id": "perturb_1e-13", "expected": classify.WITHIN_TOLERANCE,
         "record": mutate_scale(rec, 1.0 + 1e-13, pick_fp), "injected": pick_fp},
        {"id": "nudge_4ulp", "expected": classify.WITHIN_TOLERANCE,
         "record": mutate_nudge(rec, 4, pick_fp), "injected": pick_fp},
        {"id": "inject_nan", "expected": classify.NAN,
         "record": mutate_nan(rec, pick_fp), "injected": pick_fp},
        {"id": "flip_int", "expected": classify.OVER_TOLERANCE,
         "record": mutate_int(rec, pick_int), "injected": pick_int},
        {"id": "drop_field", "expected": classify.OVER_TOLERANCE,
         "record": mutate_drop(rec, pick_fp[: max(1, width // 2)]),
         "injected": pick_fp[: max(1, width // 2)]},
        {"id": "zero_abs_shift", "expected": classify.OVER_TOLERANCE,
         "record": mutate_shift(rec, 1e-3, pick_zero), "injected": pick_zero},
    ]


def run_matrix(ref: Mapping[str, Any], candidates: Mapping[str, Mapping[str, Any]],
               policy: Mapping[str, Any], width: int = 6) -> Dict[str, Any]:
    """Run every mutation family against every candidate arm."""
    cases: List[Dict[str, Any]] = []
    for arm_name, cand in candidates.items():
        for injection in _injections(cand, width):
            report = gate.compare(ref, injection["record"], policy)
            rows = {r["name"]: r for r in report["rows"]}
            injected = list(injection["injected"])
            expected = injection["expected"]

            if expected == classify.OVER_TOLERANCE:
                flagged = [n for n in injected if rows.get(n, {}).get("class") == classify.OVER_TOLERANCE]
                detected, total = len(flagged), len(injected)
            elif expected == classify.NAN:
                flagged = [n for n in injected
                           if rows.get(n, {}).get("class") == classify.NAN and not rows[n].get("ok")]
                detected, total = len(flagged), len(injected)
            else:
                flagged, detected, total = [], 0, 0

            benign = [n for n, r in rows.items() if n not in set(injected)]
            false_pos = [n for n in benign if rows[n]["class"] == classify.OVER_TOLERANCE]
            false_nan = [n for n in benign if rows[n]["class"] == classify.NAN and not rows[n].get("ok")]

            cases.append({
                "arm": arm_name,
                "mutation": injection["id"],
                "expected_class": expected,
                "injected_count": int(total),
                "detected_count": int(detected),
                "flagged_fields": flagged,
                "benign_count": len(benign),
                "false_positive_fields": false_pos,
                "false_nan_fields": false_nan,
                "verdict": report["verdict"],
                "class_counts": report["class_counts"],
            })
    return {"cases": cases, "metrics": metrics(cases)}


def metrics(cases: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    det = sum(c["detected_count"] for c in cases)
    det_total = sum(c["injected_count"] for c in cases)
    nan_cases = [c for c in cases if c["expected_class"] == classify.NAN]
    nan_det = sum(c["detected_count"] for c in nan_cases)
    nan_total = sum(c["injected_count"] for c in nan_cases)
    benign_total = sum(c["benign_count"] for c in cases)
    false_pos = sum(len(c["false_positive_fields"]) for c in cases) + \
        sum(len(c["false_nan_fields"]) for c in cases)
    return {
        "over_tolerance_detection_rate": det / det_total if det_total else 1.0,
        "detected_fields": det,
        "injected_fields": det_total,
        "nan_detection_rate": nan_det / nan_total if nan_total else 1.0,
        "nan_detected_fields": nan_det,
        "nan_injected_fields": nan_total,
        "false_positive_rate": false_pos / benign_total if benign_total else 0.0,
        "false_positive_fields": false_pos,
        "benign_fields": benign_total,
        "cases": len(cases),
    }