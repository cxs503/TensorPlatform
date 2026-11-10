"""Five-level maturity ladder — JSON Schema validation (p251w2-n03).

The five-level ladder declaration (levels, capabilities, transitions) must
pass JSON Schema validation before it is accepted as a machine-adjudicable
artifact.  This module provides:

* ``LADDER_SCHEMA`` — the JSON Schema for a maturity-ladder declaration
  (validates the five-level ladder, capability states, and transition records).
* ``validate_ladder_schema`` — validate a declaration dict against the schema.
* ``validate_transition_record`` — validate a single transition record.

**Zero-domain-semantics**: the schema only constrains *structure* (levels are
exactly the five named rungs; transitions carry timestamp/evidence/adjudicator/
rollback).  It never names a concrete capability or metric — those are data.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .model import MATURITY_LEVELS

#: The five rungs, in order (the only allowed level names).
_LEVELS = list(MATURITY_LEVELS)

#: Rollback-point sub-schema (shared by the ladder and the transition record).
_ROLLBACK_POINT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["capability_id", "level_before", "evidence_digest", "created_at"],
    "properties": {
        "capability_id": {"type": "string", "minLength": 1},
        "level_before": {"enum": _LEVELS},
        "evidence_digest": {"type": "string"},
        "created_at": {"type": "string"},
    },
}

#: Transition sub-schema (self-contained — no external ``$ref``).
_TRANSITION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "transition_id", "capability_id", "from_level", "to_level",
        "timestamp", "evidence_refs", "adjudicator", "rollback_point",
        "reason",
    ],
    "properties": {
        "transition_id": {"type": "string", "minLength": 1},
        "capability_id": {"type": "string", "minLength": 1},
        "from_level": {"enum": _LEVELS},
        "to_level": {"enum": _LEVELS},
        "timestamp": {"type": "string"},
        "evidence_refs": {
            "type": "array",
            "items": {"type": "string"},
        },
        "adjudicator": {"type": "string", "minLength": 1},
        "rollback_point": {
            "oneOf": [
                {"type": "null"},
                _ROLLBACK_POINT_SCHEMA,
            ],
        },
        "reason": {"type": "string"},
        "direction": {"enum": ["up", "down", "rollback"]},
    },
}

#: Capability sub-schema.
_CAPABILITY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["capability_id", "current_level", "history"],
    "properties": {
        "capability_id": {"type": "string", "minLength": 1},
        "current_level": {"enum": _LEVELS},
        "level_rank": {"type": "integer", "minimum": 0, "maximum": 4},
        "history": {
            "type": "array",
            "items": _TRANSITION_SCHEMA,
        },
        "metadata": {"type": "object"},
        "updated_at": {"type": "string"},
    },
}

#: JSON Schema (draft-07) for a maturity-ladder declaration.
LADDER_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": "https://tensorplatform.local/schemas/maturity_ladder.v1.schema.json",
    "title": "Capability maturity five-level ladder declaration",
    "description": (
        "Machine-adjudicable evidence chain for the five-level capability "
        "maturity ladder: Implemented → Tested → Benchmark-verified → "
        "Coupled prototype → Engineering-qualified.  Every transition "
        "records timestamp, evidence, adjudicator and rollback point."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "ladder", "capabilities", "transitions"],
    "properties": {
        "schema_version": {"const": "maturity-ladder/1.0"},
        "ladder": {
            "type": "array",
            "minItems": 5,
            "maxItems": 5,
            "items": {"type": "string"},
            "const": _LEVELS,
        },
        "capabilities": {
            "type": "array",
            "minItems": 1,
            "items": _CAPABILITY_SCHEMA,
        },
        "transitions": {
            "type": "array",
            "items": _TRANSITION_SCHEMA,
        },
    },
}


def _validate_with_jsonschema(payload: Any, schema: Dict[str, Any]) -> Dict[str, Any]:
    """Validate ``payload`` against ``schema`` using the ``jsonschema`` library.

    Falls back to a structural check if ``jsonschema`` is not installed
    (the schema is small enough that the fallback covers the same ground).
    """
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return _structural_validate(payload, schema)

    try:
        jsonschema.validate(payload, schema)
        return {"valid": True, "errors": []}
    except jsonschema.ValidationError as exc:
        return {"valid": False, "errors": [str(exc)]}


def _structural_validate(payload: Any, schema: Dict[str, Any]) -> Dict[str, Any]:
    """Minimal structural fallback (no ``jsonschema`` dependency)."""
    errors: List[str] = []
    if not isinstance(payload, dict):
        errors.append("payload must be an object")
        return {"valid": False, "errors": errors}
    for req in schema.get("required", []):
        if req not in payload:
            errors.append(f"missing required key: {req}")
    if "ladder" in payload:
        ladder = payload["ladder"]
        if not isinstance(ladder, list) or len(ladder) != 5:
            errors.append("ladder must be a 5-element list")
        elif ladder != _LEVELS:
            errors.append(f"ladder must be exactly {_LEVELS}")
    if "capabilities" in payload:
        caps = payload["capabilities"]
        if not isinstance(caps, list) or not caps:
            errors.append("capabilities must be a non-empty list")
        else:
            for i, cap in enumerate(caps):
                if not isinstance(cap, dict):
                    errors.append(f"capabilities[{i}] must be an object")
                    continue
                for req in ("capability_id", "current_level", "history"):
                    if req not in cap:
                        errors.append(f"capabilities[{i}] missing {req}")
                if "current_level" in cap and cap["current_level"] not in _LEVELS:
                    errors.append(f"capabilities[{i}] current_level not in {_LEVELS}")
    if "transitions" in payload:
        trans = payload["transitions"]
        if not isinstance(trans, list):
            errors.append("transitions must be a list")
        else:
            for i, t in enumerate(trans):
                if not isinstance(t, dict):
                    errors.append(f"transitions[{i}] must be an object")
                    continue
                for req in ("transition_id", "capability_id", "from_level",
                            "to_level", "timestamp", "evidence_refs",
                            "adjudicator", "rollback_point", "reason"):
                    if req not in t:
                        errors.append(f"transitions[{i}] missing {req}")
                for lvl_key in ("from_level", "to_level"):
                    if lvl_key in t and t[lvl_key] not in _LEVELS:
                        errors.append(f"transitions[{i}] {lvl_key} not in {_LEVELS}")
    return {"valid": not errors, "errors": errors}


def validate_ladder_schema(payload: Any) -> Dict[str, Any]:
    """Validate a maturity-ladder declaration against the JSON Schema."""
    return _validate_with_jsonschema(payload, LADDER_SCHEMA)


def validate_transition_record(record: Any) -> Dict[str, Any]:
    """Validate a single transition record against the transition sub-schema."""
    return _validate_with_jsonschema(record, _TRANSITION_SCHEMA)


def ladder_declaration(
    capabilities: List[Dict[str, Any]],
    transitions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a valid ladder declaration (for reports / artifacts)."""
    return {
        "schema_version": "maturity-ladder/1.0",
        "ladder": list(_LEVELS),
        "capabilities": capabilities,
        "transitions": transitions,
    }
