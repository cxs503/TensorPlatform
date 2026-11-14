"""Canonical, immutable acceptance-criteria snapshots for TensorPlatform missions."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping


class AcceptanceCriteriaError(ValueError):
    """Acceptance criteria are malformed or their frozen fingerprint drifted."""


def canonicalize_acceptance_criteria(value: Any) -> list[dict[str, Any]]:
    """Validate criteria and assign deterministic IDs without changing their meaning.

    Empty criteria remain legal for legacy/simple engineering work, but their
    absence is explicit and downstream evaluation must remain inconclusive.
    When present, every criterion must be machine-checkable.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise AcceptanceCriteriaError("acceptance_criteria must be an array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value, start=1):
        if isinstance(raw, str):
            item = {
                "statement": raw,
                "requires_code": True,
                "requires_test": True,
            }
        elif isinstance(raw, Mapping):
            item = dict(raw)
        else:
            raise AcceptanceCriteriaError(f"criterion {index} must be an object or non-empty string")

        metric = item.get("metric")
        target = item.get("target")
        tolerance = item.get("tolerance")
        statement = item.get("statement")
        if metric is not None:
            if not isinstance(metric, str) or not metric.strip():
                raise AcceptanceCriteriaError(f"criterion {index} metric must be a non-empty string")
            if target is None or isinstance(target, bool) or not isinstance(target, (str, int, float)):
                raise AcceptanceCriteriaError(f"criterion {index} target must be a string or finite number")
            if isinstance(target, float) and not math.isfinite(target):
                raise AcceptanceCriteriaError(f"criterion {index} target must be finite")
            if isinstance(target, str) and not target.strip():
                raise AcceptanceCriteriaError(f"criterion {index} target must not be blank")
            if tolerance is None or isinstance(tolerance, bool) or not isinstance(tolerance, (str, int, float)):
                raise AcceptanceCriteriaError(f"criterion {index} tolerance must be a string or finite number")
            if isinstance(tolerance, float) and not math.isfinite(tolerance):
                raise AcceptanceCriteriaError(f"criterion {index} tolerance must be finite")
            if isinstance(tolerance, str) and not tolerance.strip():
                raise AcceptanceCriteriaError(f"criterion {index} tolerance must not be blank")
            item["metric"] = metric.strip()
        elif not isinstance(statement, str) or not statement.strip():
            raise AcceptanceCriteriaError(
                f"criterion {index} requires metric/target/tolerance or a non-empty statement"
            )
        else:
            item["statement"] = statement.strip()

        for flag in ("required", "requires_code", "requires_test"):
            if flag in item and not isinstance(item[flag], bool):
                raise AcceptanceCriteriaError(f"criterion {index} {flag} must be boolean")
        for refs_field in ("evidence_refs", "test_ids"):
            if refs_field in item and (
                not isinstance(item[refs_field], list)
                or any(not isinstance(ref, str) or not ref.strip() for ref in item[refs_field])
            ):
                raise AcceptanceCriteriaError(f"criterion {index} {refs_field} must be an array of non-empty strings")
        criterion_id = item.get("criterion_id", item.get("id"))
        if criterion_id is None:
            criterion_id = f"C{index:03d}"
        if not isinstance(criterion_id, str) or not criterion_id.strip():
            raise AcceptanceCriteriaError(f"criterion {index} has an invalid criterion_id")
        criterion_id = criterion_id.strip()
        if criterion_id in seen:
            raise AcceptanceCriteriaError(f"duplicate criterion_id: {criterion_id}")
        seen.add(criterion_id)
        item["criterion_id"] = criterion_id
        item.pop("id", None)
        if metric is not None:
            item["metric"] = metric.strip()
        normalized.append(item)
    return normalized


def acceptance_criteria_sha256(criteria: Any) -> str:
    """Return a stable digest of a canonical criteria list."""
    canonical = canonicalize_acceptance_criteria(criteria)
    try:
        payload = json.dumps(
            canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AcceptanceCriteriaError(f"criteria must contain JSON-safe values: {exc}") from exc
    return hashlib.sha256(payload).hexdigest()


def verify_frozen_acceptance_criteria(criteria: Any, expected_sha256: str | None) -> bool:
    """Verify the stored snapshot against its immutable fingerprint."""
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        return False
    try:
        return acceptance_criteria_sha256(criteria) == expected_sha256.lower()
    except (AcceptanceCriteriaError, TypeError, ValueError):
        return False
