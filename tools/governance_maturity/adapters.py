"""Adapter layer for upstream artifact shapes (explicit contract boundary).

Everything the governance suite needs from *other* modules/repos enters through
this module.  If an upstream shape drifts, only the adapter changes -- the
registry, mutation scanner, gate-power instrument and write-back gate stay put.

Two adapter families:

* platform artifacts (``metric_row``, ``criterion``, ``mission_report``) --
  dictionaries produced by platform modules;
* engine verification reports (``verification_result``) -- JSON documents
  produced by solver repos.  The shape is described by a *profile* document so
  that no engine-specific field name is hard-coded here.

All adapters return either a registry envelope (:func:`to_envelope`) or raise
:class:`AdapterError` with a machine-readable code.  Failure to *adapt* is
distinct from failure to *validate*: the former means "upstream broke the
transport contract", the latter means "payload violates the semantic schema".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifact_registry import (
    ArtifactRegistry,
    file_digest,
    make_envelope,
)


class AdapterError(ValueError):
    """Raised when a raw upstream object cannot be mapped to a schema payload."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _require(obj: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in obj:
        raise AdapterError("missing_source_field", f"{where} lacks {key!r}")
    return obj[key]


def load_json(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def digest_of(path: str | Path) -> str:
    return file_digest(path)


def to_envelope(
    registry: ArtifactRegistry,
    artifact_type: str,
    payload: Mapping[str, Any],
    producer: str,
    source_digest: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Wrap an adapted payload into a registry envelope."""
    return make_envelope(
        registry,
        artifact_type=artifact_type,
        payload=payload,
        producer=producer,
        source_digest=source_digest,
        created_at=created_at,
    )


# --------------------------------------------------------------------------- #
# platform adapters
# --------------------------------------------------------------------------- #
def adapt_metric_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map a metric ledger row to the ``metric_row`` payload.

    Source contract (keys may be nested one level under ``row['fields']``):
    ``metric``, ``value``, ``unit``, ``window``, ``evidence_count``.
    """
    src = row.get("fields", row) if isinstance(row, Mapping) else row
    if not isinstance(src, Mapping):
        raise AdapterError("source_not_object", "metric row must be an object")
    payload = {
        "metric": str(_require(src, "metric", "metric row")),
        "value": float(_require(src, "value", "metric row")),
        "unit": str(_require(src, "unit", "metric row")),
        "window": str(_require(src, "window", "metric row")),
        "evidence_count": int(_require(src, "evidence_count", "metric row")),
    }
    if "value__unit" in src:
        payload["value__unit"] = str(src["value__unit"])
    return payload


def adapt_criterion(criterion: Mapping[str, Any]) -> dict[str, Any]:
    """Map a harvested criterion to the ``criterion`` payload."""
    payload = {
        "criterion_id": str(_require(criterion, "criterion_id", "criterion")),
        "comparator": str(_require(criterion, "comparator", "criterion")),
        "threshold": float(_require(criterion, "threshold", "criterion")),
        "source_type": str(_require(criterion, "source_type", "criterion")),
        "source_ref": str(_require(criterion, "source_ref", "criterion")),
        "decidable": bool(criterion.get("decidable", True)),
    }
    if "probe" in criterion:
        payload["probe"] = str(criterion["probe"])
    return payload


def adapt_mission_report(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Map a mission report document to the ``mission_report`` payload."""
    checks = doc.get("checks")
    return {
        "mission_id": int(_require(doc, "mission_id", "mission report")),
        "title": str(_require(doc, "title", "mission report")),
        "verdict": str(_require(doc, "verdict", "mission report")),
        "section_count": int(len(doc.get("sections", []))),
        "criteria_count": int(len(doc.get("criteria", []))) if isinstance(doc.get("criteria"), Sequence) else 0,
        "checks_passed": int(checks.get("passed", 0)) if isinstance(checks, Mapping) else 0,
    }


# --------------------------------------------------------------------------- #
# engine report adapters (profile-driven)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EngineProfile:
    """Description of an engine verification report, supplied as data."""

    name: str
    case_id_keys: tuple[str, ...]
    anchor_list_keys: tuple[str, ...] = ()
    anchor_name_key: str = "anchor"
    anchor_passed_key: str = "passed"

    @staticmethod
    def from_dict(doc: Mapping[str, Any]) -> "EngineProfile":
        return EngineProfile(
            name=str(doc["name"]),
            case_id_keys=tuple(doc.get("case_id_keys", ("case_id",))),
            anchor_list_keys=tuple(doc.get("anchor_list_keys", ())),
            anchor_name_key=str(doc.get("anchor_name_key", "anchor")),
            anchor_passed_key=str(doc.get("anchor_passed_key", "passed")),
        )


def _first_key(doc: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in doc:
            return doc[key]
    return None


def adapt_engine_report(doc: Mapping[str, Any], profile: EngineProfile) -> dict[str, Any]:
    """Map an engine verification report to the ``verification_result`` payload.

    The report is reduced to: case id, whether *all* declared anchors passed,
    how many anchors were declared, and the failing anchor names.  The caller
    keeps the raw document digest as provenance, so the reduction is auditable.
    """
    case_id = _first_key(doc, profile.case_id_keys)
    if case_id is None:
        raise AdapterError("missing_source_field", f"engine report lacks any of {profile.case_id_keys!r}")
    anchors: list[Mapping[str, Any]] = []
    for key in profile.anchor_list_keys:
        value = doc.get(key)
        if isinstance(value, list):
            anchors = [a for a in value if isinstance(a, Mapping)]
            break
    if not anchors:
        raise AdapterError("missing_source_field", f"engine report lacks anchor list {profile.anchor_list_keys!r}")
    failures = [
        str(a.get(profile.anchor_name_key, f"anchor_{i}"))
        for i, a in enumerate(anchors)
        if not bool(a.get(profile.anchor_passed_key, False))
    ]
    return {
        "case_id": str(case_id),
        "anchor_count": len(anchors),
        "passed": len(failures) == 0,
        "failure_count": len(failures),
        "failing_anchors": failures[:20],
    }


def profile_from_registry_doc(path: str | Path) -> dict[str, EngineProfile]:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return {p["name"]: EngineProfile.from_dict(p) for p in doc.get("profiles", [])}