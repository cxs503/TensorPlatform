
"""Canonical, immutable acceptance-criteria snapshots for TensorPlatform missions."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping


class AcceptanceCriteriaError(ValueError):
    """Acceptance criteria are malformed or their frozen fingerprint drifted."""


def canonicalize_acceptance_criteria(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AcceptanceCriteriaError("acceptance_criteria must be an array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, Mapping):
            raise AcceptanceCriteriaError(f"criterion {index} must be an object")
        item = dict(raw)
        metric, target, tolerance = item.get("metric"), item.get("target"), item.get("tolerance")
        if not isinstance(metric, str) or not metric.strip():
            raise AcceptanceCriteriaError(f"criterion {index} requires a non-empty metric")
        for field, entry in (("target", target), ("tolerance", tolerance)):
            if entry is None or isinstance(entry, bool) or not isinstance(entry, (str, int, float)):
                raise AcceptanceCriteriaError(f"criterion {index} {field} must be a string or finite number")
            if isinstance(entry, float) and not math.isfinite(entry):
                raise AcceptanceCriteriaError(f"criterion {index} {field} must be finite")
            if isinstance(entry, str) and not entry.strip():
                raise AcceptanceCriteriaError(f"criterion {index} {field} must not be blank")
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
        item["metric"] = metric.strip()
        normalized.append(item)
    return normalized


def acceptance_criteria_sha256(criteria: Any) -> str:
    canonical = canonicalize_acceptance_criteria(criteria)
    try:
        payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AcceptanceCriteriaError(f"criteria must contain JSON-safe values: {exc}") from exc
    return hashlib.sha256(payload).hexdigest()


def verify_frozen_acceptance_criteria(criteria: Any, expected_sha256: str | None) -> bool:
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        return False
    try:
        return acceptance_criteria_sha256(criteria) == expected_sha256.lower()
    except (AcceptanceCriteriaError, TypeError, ValueError):
        return False
