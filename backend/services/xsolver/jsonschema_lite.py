"""Dependency-free JSON-Schema (draft-07 subset) validator + schema/vocabulary loader.

The platform already ships ``jsonschema`` in some environments, but the
acceptance run must work in the bare sandbox as well, so the subset we actually
use (``type``, ``const``, ``enum``, ``required``, ``properties``,
``additionalProperties``, ``items``, ``minItems``, ``minProperties``,
``minLength``, ``pattern``, ``minimum``, ``maximum``, ``anyOf``, ``oneOf``) is
implemented here in ~120 lines.  Anything outside the subset raises instead of
being silently ignored -- a validator that ignores keywords would turn "schema
validated" into a slogan.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "xsolver"
CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "xsolver"

_SUPPORTED = {
    "$schema", "$id", "title", "description", "type", "const", "enum", "required",
    "properties", "additionalProperties", "items", "minItems", "minProperties",
    "minLength", "pattern", "minimum", "maximum", "anyOf", "oneOf", "allOf",
    "format", "default", "examples", "note", "notes", "$comment",
}

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_schema(name: str) -> Dict[str, Any]:
    """Load ``schemas/xsolver/<name>.json`` (``name`` without extension)."""
    return load_json(SCHEMA_DIR / f"{name}.json")


def load_config(name: str) -> Dict[str, Any]:
    """Load ``config/xsolver/<name>.json``."""
    return load_json(CONFIG_DIR / f"{name}.json")


def _check_type(value: Any, spec: Any) -> bool:
    names = spec if isinstance(spec, list) else [spec]
    for name in names:
        py = _TYPE_MAP.get(name)
        if py is None:
            continue
        if name in ("number", "integer") and isinstance(value, bool):
            continue
        if isinstance(value, py):
            return True
    return False


def _validate(value: Any, schema: Dict[str, Any], path: str, errors: List[str]) -> None:
    unknown = set(schema) - _SUPPORTED
    if unknown:
        raise ValueError(f"unsupported schema keywords at {path}: {sorted(unknown)}")

    if "type" in schema and not _check_type(value, schema["type"]):
        errors.append(f"{path}: expected type {schema['type']}, got {type(value).__name__}")
        return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: string shorter than {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > maximum {schema['maximum']}")
    if isinstance(value, dict):
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append(f"{path}: fewer than {schema['minProperties']} properties")
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, sub in value.items():
            child = f"{path}.{key}"
            if key in props:
                _validate(sub, props[key], child, errors)
            elif extra is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(extra, dict):
                _validate(sub, extra, child, errors)
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{i}]", errors)
    if "allOf" in schema:
        for i, sub in enumerate(schema["allOf"]):
            _validate(value, sub, f"{path}<allOf[{i}]>", errors)
    if "anyOf" in schema:
        sub_errors: List[List[str]] = []
        for sub in schema["anyOf"]:
            errs: List[str] = []
            _validate(value, sub, path, errs)
            sub_errors.append(errs)
        if all(sub_errors):
            errors.append(f"{path}: no anyOf branch matched ({sub_errors[0][:2]})")
    if "oneOf" in schema:
        matched = 0
        for sub in schema["oneOf"]:
            errs: List[str] = []
            _validate(value, sub, path, errs)
            if not errs:
                matched += 1
        if matched != 1:
            errors.append(f"{path}: oneOf matched {matched} branches")


def validate(instance: Any, schema: Dict[str, Any]) -> List[str]:
    """Return a list of human-readable errors (empty == valid)."""
    errors: List[str] = []
    _validate(instance, schema, "$", errors)
    return errors


def validate_or_raise(instance: Any, schema: Dict[str, Any], what: str = "document") -> None:
    errors = validate(instance, schema)
    if errors:
        raise ValueError(f"{what} violates schema: " + "; ".join(errors[:6]))


def iter_schema_files() -> Iterable[Path]:
    return sorted(SCHEMA_DIR.glob("*.json"))