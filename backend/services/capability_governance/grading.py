"""Probe grading + evidence chain: the mechanical half of the auto-grading loop.

Given a probe result (:mod:`services.capability_governance.probes`) and the
frozen endpoint spec, this module produces

1. a **criteria verdict table** — every acceptance criterion with its measured
   value, threshold and pass flag (no prose, no hand-written numbers);
2. an **evidence chain** — the verdict plus the sha256 of the test report it came
   from, the sha256 of the probe routine source, the acceptance criteria, the
   measured values, a verdict word, a timestamp and the mission id;
3. a **decided level** — ``REAL`` only when *every* criterion passes, otherwise
   ``WITHHELD``; a failing *responsiveness* criterion is additionally reported as
   an observed ``MOCK`` fingerprint, because an output that does not move with
   its input is the signature of a hardcoded value, not of an unproven algorithm.

The chain is content-addressed: ``chain_digest`` is the sha256 of the canonical
JSON of the chain without that field, so editing any measurement invalidates it
and :func:`validate_chain` will say so.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

SCHEMA = "capability-evidence-chain/1.0"
REQUIRED_FIELDS: Sequence[str] = (
    "schema",
    "subject_id",
    "mission_id",
    "test_report",
    "acceptance_criteria",
    "measured_values",
    "verdict",
    "decided_level",
    "produced_at",
    "chain_digest",
)
LEVELS = ("REAL", "WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED")


# ---------------------------------------------------------------------------
# digests
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


# ---------------------------------------------------------------------------
# criteria
# ---------------------------------------------------------------------------


def _rel_error_pct(measured: float, reference: float) -> float:
    if reference == 0.0:
        return 0.0 if measured == 0.0 else math.inf
    return abs(measured - reference) / abs(reference) * 100.0


def _criterion(name: str, op: str, threshold: float, actual: float) -> Dict[str, Any]:
    if op == "<=":
        passed = actual <= threshold
    elif op == ">=":
        passed = actual >= threshold
    elif op == "==":
        passed = actual == threshold
    else:  # pragma: no cover - programming error
        raise ValueError(f"unknown operator '{op}'")
    return {"name": name, "op": op, "threshold": threshold, "actual": actual, "passed": bool(passed)}


def convergence_order(errors: Sequence[float], grid: Sequence[int]) -> Optional[float]:
    """Empirical order from the two finest *non-zero* error levels."""
    pairs = [(e, g) for e, g in zip(errors, grid) if e > 0]
    if len(pairs) < 2:
        return None
    (e_fine, g_fine), (e_coarse, g_coarse) = pairs[-1], pairs[-2]
    ratio_h = g_fine / g_coarse
    if ratio_h <= 1.0 or e_fine <= 0.0:
        return None
    return math.log(e_coarse / e_fine) / math.log(ratio_h)


def grade_probe(result: Mapping[str, Any], spec: Mapping[str, Any],
                routine_source_sha256: Optional[str] = None,
                spec_digest: Optional[str] = None) -> Dict[str, Any]:
    """Evaluate every acceptance criterion for one frozen endpoint."""
    measured = [float(m) for m in result["measured"]]
    reference = [float(r) for r in result["reference"]]
    repeat_equal = [bool(x) for x in result.get("repeat_equal", [True] * len(measured))]

    errors = [_rel_error_pct(m, r) for m, r in zip(measured, reference)]
    finest_error = errors[-1]

    # 1) determinism: same input twice -> same number
    det_actual = 1.0 if all(repeat_equal) else 0.0
    det = _criterion("deterministic_repeat", ">=", 1.0, det_actual)

    # 2) responsiveness: the output must move when the input moves
    scale = max(abs(v) for v in measured) or 1.0
    spread = (max(measured) - min(measured)) / scale
    resp = _criterion("input_response_spread", ">=", float(spec.get("min_spread", 1e-6)), spread)

    # 3) numeric agreement at the finest grid point
    num = _criterion("reference_error_pct_finest", "<=", float(spec.get("tol_pct", 1.0)), finest_error)

    # 4) convergence: error must fall at the declared rate when the grid refines
    order = convergence_order(errors, [int(g) for g in result["grid"]])
    min_order = spec.get("min_order")
    if min_order is None or order is None:
        order_c = {"name": "empirical_order", "op": ">=", "threshold": float(min_order or 0.0),
                   "actual": order, "passed": True, "note": "not applicable (saturated/sparse)"}
    else:
        order_c = _criterion("empirical_order", ">=", float(min_order), order)

    # 5) provenance: routine source + spec must be content-addressed
    prov_ok = _is_sha256(routine_source_sha256) and _is_sha256(spec_digest)
    prov = {"name": "provenance_sha256", "op": ">=", "threshold": 1.0,
            "actual": 1.0 if prov_ok else 0.0, "passed": bool(prov_ok)}

    criteria = [det, resp, num, order_c, prov]
    all_passed = all(c["passed"] for c in criteria)

    if all_passed:
        decided = "REAL"
        verdict = "PASS"
        reason = "all criteria satisfied"
    elif not det["passed"] or not resp["passed"]:
        decided = "WITHHELD"
        verdict = "FAIL"
        reason = ("observed MOCK fingerprint: output does not respond to its input / "
                  "does not reproduce itself") if not resp["passed"] else "non-deterministic output"
    else:
        decided = "WITHHELD"
        verdict = "FAIL"
        reason = "measured value outside declared tolerance" if not num["passed"] else \
                 "convergence order below declared minimum" if not order_c["passed"] else \
                 "provenance missing"

    return {
        "schema": "capability-probe-verdict/1.0",
        "subject_id": result["endpoint_id"],
        "routine": result["routine"],
        "defect": result["defect"],
        "grid": list(result["grid"]),
        "measured": measured,
        "reference": reference,
        "errors_pct": errors,
        "criteria": criteria,
        "verdict": verdict,
        "decided_level": decided,
        "observed_level": "MOCK" if (not resp["passed"] or not det["passed"]) else None,
        "reason": reason,
        "all_passed": all_passed,
    }


def build_chain(verdict: Mapping[str, Any], *, test_report_path: Path, mission_id: int,
                routine_source_sha256: str, spec_digest: str,
                produced_at: Optional[str] = None) -> Dict[str, Any]:
    """Assemble (and content-address) the evidence chain for one endpoint."""
    produced = produced_at or datetime.now(timezone.utc).isoformat()
    body: Dict[str, Any] = {
        "schema": SCHEMA,
        "subject_id": verdict["subject_id"],
        "mission_id": mission_id,
        "test_report": {
            "path": str(test_report_path),
            "sha256": sha256_file(Path(test_report_path)),
        },
        "acceptance_criteria": list(verdict["criteria"]),
        "measured_values": {
            "grid": list(verdict["grid"]),
            "measured": list(verdict["measured"]),
            "reference": list(verdict["reference"]),
            "errors_pct": list(verdict["errors_pct"]),
        },
        "verdict": verdict["verdict"],
        "decided_level": verdict["decided_level"],
        "observed_level": verdict.get("observed_level"),
        "reason": verdict["reason"],
        "provenance": {
            "probe_routine_sha256": routine_source_sha256,
            "endpoint_spec_sha256": spec_digest,
            "routine": verdict["routine"],
            "defect_mode": verdict["defect"],
        },
        "produced_at": produced,
    }
    body["chain_digest"] = digest_of(body)
    return body


def validate_chain(chain: Mapping[str, Any],
                   verify_report_file: bool = True) -> Dict[str, Any]:
    """Structural + content validation.  Never mutates and never repairs.

    When ``verify_report_file`` is set the chain must point at a test report that
    exists *and* hashes to the declared value: a sha256 field is a claim, and the
    claim is only worth something if it is checked against the artefact.
    """
    problems: List[str] = []
    for field in REQUIRED_FIELDS:
        if field not in chain:
            problems.append(f"missing field '{field}'")
    if problems:
        return {"valid": False, "problems": problems, "digest_recomputed": None}

    report = chain["test_report"] or {}
    if not _is_sha256(report.get("sha256")):
        problems.append("test_report.sha256 is not a sha256 digest")
    elif verify_report_file:
        report_path = Path(report.get("path") or "")
        if not report_path.is_file():
            problems.append(f"test report not found: {report_path}")
        elif sha256_file(report_path) != report["sha256"]:
            problems.append("test_report.sha256 does not match the file on disk")
    if not chain["acceptance_criteria"]:
        problems.append("acceptance_criteria is empty")
    for crit in chain["acceptance_criteria"]:
        for key in ("name", "op", "threshold", "actual", "passed"):
            if key not in crit:
                problems.append(f"criterion missing '{key}'")
                break
    if not chain["measured_values"]:
        problems.append("measured_values is empty")
    if chain["verdict"] not in ("PASS", "FAIL"):
        problems.append(f"invalid verdict '{chain['verdict']}'")
    all_passed = all(c.get("passed") for c in chain["acceptance_criteria"]) if chain["acceptance_criteria"] else False
    if chain["verdict"] == "PASS" and not all_passed:
        problems.append("verdict PASS but a criterion failed")
    if chain["decided_level"] == "REAL" and not all_passed:
        problems.append("decided REAL but a criterion failed")
    if chain["decided_level"] not in LEVELS:
        problems.append(f"invalid decided_level '{chain['decided_level']}'")
    prov = chain.get("provenance") or {}
    if not _is_sha256(prov.get("probe_routine_sha256")):
        problems.append("provenance.probe_routine_sha256 missing/not sha256")
    if not _is_sha256(prov.get("endpoint_spec_sha256")):
        problems.append("provenance.endpoint_spec_sha256 missing/not sha256")

    body = {k: v for k, v in chain.items() if k != "chain_digest"}
    recomputed = digest_of(body)
    if recomputed != chain.get("chain_digest"):
        problems.append("chain_digest does not match the chain content")

    return {"valid": not problems, "problems": problems, "digest_recomputed": recomputed}