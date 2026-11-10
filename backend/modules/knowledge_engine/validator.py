"""Knowledge entry validator — JSON Schema validation for four-contract entries.

Validates entries against the knowledge_entry.v1.schema.json schema.
Provides detailed error messages for debugging.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Try to import jsonschema; fall back to basic validation if not available
try:
    import jsonschema
    from jsonschema import Draft7Validator, ValidationError
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False
    ValidationError = Exception


class SchemaValidationError(Exception):
    """Raised when a knowledge entry fails schema validation."""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or [message]


# Load schema
_SCHEMA_PATH = Path(__file__).parent.parent.parent.parent / "schemas" / "knowledge_entry.v1.schema.json"
_SCHEMA: dict | None = None


def _load_schema() -> dict:
    """Load the JSON schema."""
    global _SCHEMA
    if _SCHEMA is None:
        if _SCHEMA_PATH.exists():
            with open(_SCHEMA_PATH) as f:
                _SCHEMA = json.load(f)
        else:
            # Fallback minimal schema
            _SCHEMA = {
                "type": "object",
                "required": ["entry_id", "problem_statement", "methodology",
                            "evidence_chain", "conclusion", "provenance"],
                "properties": {
                    "entry_id": {"type": "string"},
                    "version": {"type": "string"},
                    "created_at": {"type": "string"},
                    "problem_statement": {"type": "object"},
                    "methodology": {"type": "object"},
                    "evidence_chain": {"type": "object"},
                    "conclusion": {"type": "object"},
                    "uncertainty": {"type": "object"},
                    "provenance": {"type": "object"},
                    "relations": {"type": "array"},
                }
            }
    return _SCHEMA


def validate_entry(entry_data: dict[str, Any], strict: bool = True) -> tuple[bool, list[str]]:
    """Validate a knowledge entry against the schema.

    Args:
        entry_data: Dictionary representation of the entry
        strict: If True, use full JSON Schema validation; if False, basic checks only

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    schema = _load_schema()

    # Basic required field checks
    required_fields = ["entry_id", "problem_statement", "methodology",
                       "evidence_chain", "conclusion", "provenance"]
    for field in required_fields:
        if field not in entry_data:
            errors.append(f"Missing required field: {field}")

    # Check entry_id format
    entry_id = entry_data.get("entry_id", "")
    if entry_id and not entry_id.startswith("ke_"):
        errors.append(f"entry_id must start with 'ke_', got: {entry_id}")

    # Check problem_statement structure
    ps = entry_data.get("problem_statement", {})
    if not isinstance(ps, dict):
        errors.append("problem_statement must be an object")
    elif "goal" not in ps:
        errors.append("problem_statement missing required field: goal")

    # Check methodology structure
    meth = entry_data.get("methodology", {})
    if not isinstance(meth, dict):
        errors.append("methodology must be an object")
    elif "approach" not in meth:
        errors.append("methodology missing required field: approach")

    # Check evidence_chain structure
    ev = entry_data.get("evidence_chain", {})
    if not isinstance(ev, dict):
        errors.append("evidence_chain must be an object")
    elif "artifacts" not in ev:
        errors.append("evidence_chain missing required field: artifacts")

    # Check conclusion structure
    conc = entry_data.get("conclusion", {})
    if not isinstance(conc, dict):
        errors.append("conclusion must be an object")
    elif "summary" not in conc:
        errors.append("conclusion missing required field: summary")

    # Check provenance structure
    prov = entry_data.get("provenance", {})
    if not isinstance(prov, dict):
        errors.append("provenance must be an object")
    else:
        if "source_type" not in prov:
            errors.append("provenance missing required field: source_type")
        if "source_id" not in prov:
            errors.append("provenance missing required field: source_id")

    # Full JSON Schema validation if available and strict
    if strict and HAS_JSONSCHEMA and not errors:
        validator = Draft7Validator(schema)
        schema_errors = list(validator.iter_errors(entry_data))
        for err in schema_errors:
            path = ".".join(str(p) for p in err.path) if err.path else "root"
            errors.append(f"{path}: {err.message}")

    return (len(errors) == 0, errors)


def validate_entry_or_raise(entry_data: dict[str, Any]) -> None:
    """Validate entry and raise SchemaValidationError if invalid."""
    is_valid, errors = validate_entry(entry_data)
    if not is_valid:
        raise SchemaValidationError(
            f"Knowledge entry validation failed with {len(errors)} error(s)",
            errors=errors
        )


def validate_entry_dict(entry: dict) -> dict:
    """Validate and return entry with validation status added.

    Returns the entry dict with '_validation' field added:
    {
        '_validation': {
            'valid': bool,
            'errors': list[str]
        }
    }
    """
    is_valid, errors = validate_entry(entry)
    entry_copy = dict(entry)
    entry_copy["_validation"] = {
        "valid": is_valid,
        "errors": errors,
    }
    return entry_copy
