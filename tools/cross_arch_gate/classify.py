"""Field-level classification of cross-architecture differences.

Exactly four labels are emitted, matching the four physically distinct outcomes
of comparing the same output field measured on two architectures:

``BIT_IDENTICAL``    the two 64-bit patterns are equal.  For an integer field
                     this is the *only* acceptable outcome, because integer
                     arithmetic has no rounding freedom at all.
``WITHIN_TOLERANCE`` both values are finite, differ in the last bits, and lie
                     inside the declared tolerance band (relative tolerance AND
                     an ULP cap, so neither a huge magnitude nor a tiny one can
                     hide a real error).  Declared diagnostics use an absolute
                     tolerance instead — see the note in :mod:`.fixtures`.
``OVER_TOLERANCE``   outside the band: a real discrepancy.
``NAN``              at least one side is NaN.  Sub-classified, because "both
                     NaN with the same payload" is a *consistent* result while
                     "NaN on one side only" is a defect.

Structural problems (a field present on one architecture, absent on the other;
a kind that changed) are mapped to ``OVER_TOLERANCE`` with a ``detail`` code
rather than getting a fifth label: from the consumer's point of view a missing
field is the largest possible tolerance violation.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

from . import fixtures, numerics

BIT_IDENTICAL = "BIT_IDENTICAL"
WITHIN_TOLERANCE = "WITHIN_TOLERANCE"
OVER_TOLERANCE = "OVER_TOLERANCE"
NAN = "NAN"
LABELS = (BIT_IDENTICAL, WITHIN_TOLERANCE, OVER_TOLERANCE, NAN)


def classify_field(name: str, ref_row: Optional[Mapping[str, Any]],
                   cand_row: Optional[Mapping[str, Any]], policy: Mapping[str, Any]) -> Dict[str, Any]:
    """Classify one field; returns a flat row that is also the report row."""
    rel_tol = float(policy["fp64_rel_tol"])
    ulp_cap = int(policy["fp64_ulp_cap"])
    diag_abs = float(policy["diag_abs_tol"])

    row: Dict[str, Any] = {"name": name, "class": None, "detail": None,
                           "ok": False, "ulp": None, "rel": None, "abs": None}
    if ref_row is None or cand_row is None:
        row.update({"class": OVER_TOLERANCE, "detail": "missing_field",
                    "kind": (ref_row or cand_row or {}).get("kind")})
        return row

    kind = ref_row["kind"]
    row["kind"] = kind
    if kind != cand_row["kind"]:
        row.update({"class": OVER_TOLERANCE, "detail": "kind_mismatch"})
        return row

    if kind == fixtures.INT:
        left, right = int(ref_row["int"]), int(cand_row["int"])
        row["ref"], row["cand"] = left, right
        if left == right:
            row.update({"class": BIT_IDENTICAL, "detail": "int_equal", "ok": True})
        else:
            row.update({"class": OVER_TOLERANCE, "detail": "int_mismatch",
                        "abs": abs(left - right)})
        return row

    left = numerics.decode_hex(ref_row["hex"])
    right = numerics.decode_hex(cand_row["hex"])
    row["ref_hex"], row["cand_hex"] = ref_row["hex"], cand_row["hex"]

    if numerics.is_nan(left) or numerics.is_nan(right):
        both = numerics.is_nan(left) and numerics.is_nan(right)
        payload_equal = numerics.bits(left) == numerics.bits(right)
        row.update({
            "class": NAN,
            "detail": ("both_nan_same_bits" if payload_equal else
                       "both_nan_diff_payload" if both else "single_sided_nan"),
            "ok": both,
        })
        return row

    if numerics.bits(left) == numerics.bits(right):
        row.update({"class": BIT_IDENTICAL, "detail": "bits_equal", "ok": True,
                    "ulp": 0, "rel": 0.0})
        return row

    ulp = numerics.ulp_distance(left, right)
    rel = numerics.rel_error(left, right)
    row["ulp"], row["rel"] = ulp, rel
    if kind == fixtures.DIAG:
        # cancelling diagnostic: judged on the absolute scale of the O(1) terms
        absolute = abs(left - right)
        row["abs"] = absolute
        ok = math.isfinite(absolute) and absolute <= diag_abs
        row.update({"class": WITHIN_TOLERANCE if ok else OVER_TOLERANCE,
                    "detail": "diag_abs_band" if ok else "diag_abs_exceeded",
                    "ok": ok})
        return row

    ok = math.isfinite(rel) and rel <= rel_tol and ulp <= ulp_cap
    row.update({"class": WITHIN_TOLERANCE if ok else OVER_TOLERANCE,
                "detail": "fp_band" if ok else "fp_band_exceeded", "ok": ok})
    return row


def summarize(rows) -> Dict[str, Any]:
    counts = {label: 0 for label in LABELS}
    for row in rows:
        counts[row["class"]] = counts.get(row["class"], 0) + 1
    return counts