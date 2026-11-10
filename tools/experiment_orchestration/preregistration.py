"""Preregistration — JSON schema validation + sha256 freeze.

Before an experiment runs, its specification is:
  1. Validated against a JSON schema (if provided)
  2. Frozen with a SHA-256 fingerprint
  3. Recorded with metadata (timestamp, schema version)

This ensures experiment reproducibility: the same spec always produces
the same preregistration record.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any


def validate_experiment_spec(spec: dict[str, Any], schema_path: str) -> list[str]:
    """Validate an experiment spec against a JSON schema file.

    Uses a lightweight validator (no external deps). Checks:
      - Required fields present
      - Type constraints
      - Enum constraints
      - Minimum/maximum for numbers

    Returns:
        List of error strings (empty if valid).
    """
    if not os.path.exists(schema_path):
        return [f"Schema file not found: {schema_path}"]

    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        return [f"Failed to load schema: {e}"]

    errors = _validate_object(spec, schema, path="$")
    return errors


def _validate_object(obj: Any, schema: dict[str, Any], path: str) -> list[str]:
    """Recursively validate an object against a JSON schema."""
    errors: list[str] = []

    if not isinstance(obj, dict):
        if schema.get("type") == "object":
            errors.append(f"{path}: expected object, got {type(obj).__name__}")
        return errors

    # Check required fields
    required = schema.get("required", [])
    for field_name in required:
        if field_name not in obj:
            errors.append(f"{path}: missing required field '{field_name}'")

    # Check properties
    properties = schema.get("properties", {})
    for key, value in obj.items():
        if key in properties:
            prop_schema = properties[key]
            child_path = f"{path}.{key}"
            errors.extend(_validate_value(value, prop_schema, child_path))

    # Check additionalProperties
    if schema.get("additionalProperties") is False:
        allowed = set(properties.keys())
        for key in obj:
            if key not in allowed:
                errors.append(f"{path}: unexpected field '{key}'")

    return errors


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    """Validate a single value against a schema."""
    errors: list[str] = []

    # Type check
    expected_type = schema.get("type")
    if expected_type:
        if not _check_type(value, expected_type):
            errors.append(f"{path}: expected type '{expected_type}', got {type(value).__name__}")
            return errors  # skip further checks if type is wrong

    # Enum check
    enum_values = schema.get("enum")
    if enum_values is not None:
        if value not in enum_values:
            errors.append(f"{path}: value {value!r} not in enum {enum_values}")

    # Numeric constraints
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            errors.append(f"{path}: value {value} < minimum {minimum}")
        if maximum is not None and value > maximum:
            errors.append(f"{path}: value {value} > maximum {maximum}")

    # String constraints
    if isinstance(value, str):
        min_len = schema.get("minLength")
        max_len = schema.get("maxLength")
        if min_len is not None and len(value) < min_len:
            errors.append(f"{path}: string length {len(value)} < minLength {min_len}")
        if max_len is not None and len(value) > max_len:
            errors.append(f"{path}: string length {len(value)} > maxLength {max_len}")

    # Nested object
    if schema.get("type") == "object" and isinstance(value, dict):
        errors.extend(_validate_object(value, schema, path))

    # Array
    if schema.get("type") == "array" and isinstance(value, (list, tuple)):
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(value):
                errors.extend(_validate_value(item, items_schema, f"{path}[{i}]"))

    return errors


def _check_type(value: Any, expected: str) -> bool:
    """Check if a value matches the expected JSON schema type."""
    type_map = {
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "array": lambda v: isinstance(v, (list, tuple)),
        "object": lambda v: isinstance(v, dict),
        "null": lambda v: v is None,
    }
    checker = type_map.get(expected)
    if checker is None:
        return True  # unknown type, skip check
    return checker(value)


def freeze_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Freeze an experiment spec with SHA-256 fingerprint and metadata.

    Returns:
        {
            "spec": <original spec>,
            "spec_sha256": <hex digest>,
            "frozen_at": <ISO timestamp>,
        }
    """
    spec_json = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    sha256 = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()
    return {
        "spec": spec,
        "spec_sha256": sha256,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
    }


class Preregistration:
    """Manages experiment preregistration lifecycle."""

    def __init__(self, schema_path: str | None = None):
        self.schema_path = schema_path

    def register(self, experiment_spec: dict[str, Any]) -> dict[str, Any]:
        """Register an experiment spec: validate + freeze.

        Raises ValueError if validation fails.
        """
        if self.schema_path:
            errors = validate_experiment_spec(experiment_spec, self.schema_path)
            if errors:
                raise ValueError(f"Schema validation errors: {errors}")

        frozen = freeze_spec(experiment_spec)
        return {
            **frozen,
            "experiment_id": experiment_spec.get("experiment_id", "unknown"),
            "schema_validated": self.schema_path is not None,
            "preregistered_at": datetime.now(timezone.utc).isoformat(),
        }
