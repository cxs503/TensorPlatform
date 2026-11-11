"""Spec-driven contract test-case generation.

For every declared operation (path x method) the generator emits:

* ``valid``               -- a spec-conformant request (schema ``example`` >
                              ``default`` > ``enum[0]`` > type-synthetic value);
* ``invalid_missing_required`` -- one required request field removed (expect 4xx);
* ``invalid_wrong_type``  -- one request field replaced by a wrong JSON type
                              (expect 4xx);
* ``invalid_enum``        -- one enumerable field replaced by an out-of-enum value
                              (expect 4xx), when the spec declares an enum.

``matrix_case`` turns an operation into the 5 status-code scenarios required by
acceptance criterion (c).  All values are synthetic: the module knows nothing
about any application domain, and callers inject realistic payloads through a
plain data file (``overrides``).
"""
from __future__ import annotations

import copy
import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import spec as spec_mod

#: statuses a well-behaved HTTP framework returns for a rejected request.
CLIENT_ERROR_CODES: Tuple[int, ...] = (400, 401, 403, 404, 405, 409, 415, 422)

MATRIX_SCENARIOS: Tuple[str, ...] = (
    "S1_valid",
    "S2_missing_required",
    "S3_wrong_type",
    "S4_unknown_path",
    "S5_wrong_method",
)

_SENTINEL_ABSENT = object()


@dataclass
class Case:
    case_id: str
    operation: str
    kind: str
    method: str
    path: str
    query: Dict[str, Any] = field(default_factory=dict)
    body: Any = None
    has_body: bool = False
    expected: Dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "operation": self.operation,
            "kind": self.kind,
            "method": self.method,
            "path": self.path,
            "expected": self.expected,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# synthetic value synthesis
# ---------------------------------------------------------------------------


def _numeric(schema: Dict[str, Any], integer: bool) -> Any:
    lo = schema.get("minimum", schema.get("exclusiveMinimum"))
    hi = schema.get("maximum", schema.get("exclusiveMaximum"))
    value: float = 1.0
    if isinstance(lo, (int, float)):
        value = float(lo) + (0.0 if "exclusiveMinimum" not in schema else 1.0)
        if value == 0.0:
            value = 1.0
    if isinstance(hi, (int, float)) and value > float(hi):
        value = (float(lo) + float(hi)) / 2.0 if isinstance(lo, (int, float)) else float(hi) - 1.0
    multiple = schema.get("multipleOf")
    if isinstance(multiple, (int, float)) and multiple:
        value = round(value / float(multiple)) * float(multiple)
        if value == 0.0:
            value = float(multiple)
    return int(round(value)) if integer else float(value)


def synthesize(schema: Any, rng: random.Random, depth: int = 0) -> Any:
    """Build one synthetic JSON value conforming to ``schema`` (best effort)."""
    if depth > 10 or not isinstance(schema, dict):
        return None
    if "example" in schema:
        return copy.deepcopy(schema["example"])
    if "examples" in schema and isinstance(schema["examples"], list) and schema["examples"]:
        return copy.deepcopy(schema["examples"][0])
    if "const" in schema:
        return copy.deepcopy(schema["const"])
    if schema.get("enum"):
        return copy.deepcopy(schema["enum"][0])
    for combinator in ("anyOf", "oneOf"):
        branches = [b for b in schema.get(combinator) or [] if isinstance(b, dict)]
        non_null = [b for b in branches if b.get("type") != "null"]
        if non_null:
            return synthesize(non_null[0], rng, depth + 1)
    if "allOf" in schema:
        merged: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for branch in schema.get("allOf") or []:
            if not isinstance(branch, dict):
                continue
            merged["properties"].update(branch.get("properties") or {})
            merged["required"].extend(branch.get("required") or [])
            if branch.get("type"):
                merged["type"] = branch["type"]
        return synthesize(merged, rng, depth + 1)

    stype = schema.get("type")
    if isinstance(stype, list):
        stype = next((t for t in stype if t != "null"), stype[0] if stype else None)

    if stype == "object" or (stype is None and isinstance(schema.get("properties"), dict)):
        props = schema.get("properties") or {}
        required = list(schema.get("required") or [])
        out: Dict[str, Any] = {}
        keys = required or list(props)[:8]
        for name in keys:
            prop_schema = props.get(name) or {}
            value = synthesize(prop_schema, rng, depth + 1)
            out[name] = value
        return out
    if stype == "array":
        item_schema = schema.get("items") or {}
        count = schema.get("minItems") or 1
        return [synthesize(item_schema, rng, depth + 1) for _ in range(int(min(count, 3)))]
    if stype == "integer":
        return _numeric(schema, integer=True)
    if stype == "number":
        return _numeric(schema, integer=False)
    if stype == "boolean":
        return False
    if stype == "string":
        fmt = schema.get("format")
        if fmt == "uuid":
            return str(uuid.UUID(int=0))
        if fmt == "date-time":
            return "2000-01-01T00:00:00Z"
        if fmt == "date":
            return "2000-01-01"
        if schema.get("pattern"):
            return "x"
        min_len = int(schema.get("minLength") or 1)
        return "x" * max(min_len, 1)
    return None


def _path_values(spec: Dict[str, Any], op: spec_mod.Operation, overrides: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Fill path template parameters; returns (rendered path, values used)."""
    from urllib.parse import quote

    path = op.path
    used: Dict[str, Any] = {}
    for param in spec_mod.path_parameters(spec, op):
        name = param.get("name")
        if not name:
            continue
        schema = param.get("schema") or {}
        value = overrides.get("path", {}).get(name, _SENTINEL_ABSENT)
        if value is _SENTINEL_ABSENT:
            value = synthesize(schema, random.Random(0))
            if value is None:
                value = 1 if schema.get("type") == "integer" else "x"
        used[name] = value
        path = path.replace("{%s}" % name, quote(str(value), safe=""))
    return path, used


def _query_values(spec: Dict[str, Any], op: spec_mod.Operation, overrides: Dict[str, Any],
                  required_only: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for param in spec_mod.query_parameters(spec, op, required_only=required_only):
        name = param.get("name")
        if not name:
            continue
        if name in (overrides.get("query") or {}):
            out[name] = overrides["query"][name]
            continue
        out[name] = synthesize(param.get("schema") or {}, random.Random(0))
    return out


def _wrong_type(value: Any, schema: Dict[str, Any]) -> Any:
    stype = schema.get("type")
    if stype in ("number", "integer"):
        return "not-a-number"
    if stype == "string":
        return 123456789
    if stype == "boolean":
        return "not-a-boolean"
    if stype == "array":
        return {"not": "an array"}
    if stype == "object":
        return "not-an-object"
    return "not-a-number"


# ---------------------------------------------------------------------------
# case builders
# ---------------------------------------------------------------------------


def valid_case(spec: Dict[str, Any], op: spec_mod.Operation, overrides: Optional[Dict[str, Any]] = None,
               case_id: Optional[str] = None) -> Case:
    overrides = overrides or {}
    if overrides.get("path"):
        rendered = op.path
        for name, value in overrides["path"].items():
            rendered = rendered.replace("{%s}" % name, str(value))
        path = rendered
    else:
        path, _ = _path_values(spec, op, overrides)
    query = _query_values(spec, op, overrides)
    body_schema = spec_mod.request_body_schema(spec, op)
    body = None
    has_body = False
    if body_schema is not None:
        if "body" in overrides:
            body = copy.deepcopy(overrides["body"])
        else:
            body = synthesize(body_schema, random.Random(0))
        has_body = True
    expected_codes = spec_mod.declared_success_codes(spec, op) or ["2XX"]
    expected = {"status": expected_codes, "success": True}
    return Case(
        case_id=case_id or ("valid::%s" % op.key),
        operation=op.key,
        kind="valid",
        method=op.method,
        path=path,
        query=query,
        body=body,
        has_body=has_body,
        expected=expected,
    )


def invalid_cases(spec: Dict[str, Any], op: spec_mod.Operation,
                  overrides: Optional[Dict[str, Any]] = None) -> List[Case]:
    """Emit invalid-request variants for one operation (may be empty)."""
    overrides = overrides or {}
    cases: List[Case] = []
    base = valid_case(spec, op, overrides, case_id="base::%s" % op.key)

    # (1) missing required field, body first then required query parameters
    body_schema = spec_mod.request_body_schema(spec, op)
    if body_schema is not None and isinstance(base.body, dict):
        required = [r for r in (body_schema.get("required") or []) if r in base.body]
        if required:
            payload = copy.deepcopy(base.body)
            dropped = required[0]
            payload.pop(dropped, None)
            cases.append(Case(
                case_id="missing_required::%s" % op.key,
                operation=op.key, kind="invalid_missing_required",
                method=op.method, path=base.path, query=base.query,
                body=payload, has_body=True,
                expected={"status": ["4XX"], "success": False},
                note="dropped body field %r" % dropped,
            ))
    required_query = [p for p in spec_mod.query_parameters(spec, op, required_only=True)]
    if required_query:
        name = required_query[0].get("name")
        if name:
            query = {k: v for k, v in base.query.items() if k != name}
            cases.append(Case(
                case_id="missing_required::%s::query" % op.key,
                operation=op.key, kind="invalid_missing_required",
                method=op.method, path=base.path, query=query,
                body=base.body, has_body=base.has_body,
                expected={"status": ["4XX"], "success": False},
                note="dropped required query parameter %r" % name,
            ))

    # (2) wrong JSON type
    if body_schema is not None and isinstance(base.body, dict):
        props = body_schema.get("properties") or {}
        required = [r for r in (body_schema.get("required") or []) if r in base.body]
        target = required[0] if required else (list(base.body)[0] if base.body else None)
        if target is not None:
            payload = copy.deepcopy(base.body)
            payload[target] = _wrong_type(payload[target], props.get(target) or {})
            cases.append(Case(
                case_id="wrong_type::%s" % op.key,
                operation=op.key, kind="invalid_wrong_type",
                method=op.method, path=base.path, query=base.query,
                body=payload, has_body=True,
                expected={"status": ["4XX"], "success": False},
                note="wrong type for body field %r" % target,
            ))

    # (3) out-of-enum value
    if body_schema is not None and isinstance(base.body, dict):
        props = body_schema.get("properties") or {}
        for name, prop in props.items():
            if name in base.body and isinstance(prop, dict) and prop.get("enum"):
                payload = copy.deepcopy(base.body)
                payload[name] = "__not_in_enum__"
                cases.append(Case(
                    case_id="invalid_enum::%s" % op.key,
                    operation=op.key, kind="invalid_enum",
                    method=op.method, path=base.path, query=base.query,
                    body=payload, has_body=True,
                    expected={"status": ["4XX"], "success": False},
                    note="out-of-enum value for body field %r" % name,
                ))
                break
    return cases


def matrix_case(spec: Dict[str, Any], op: spec_mod.Operation, scenario: str,
                overrides: Optional[Dict[str, Any]] = None) -> Optional[Case]:
    """Build one of the five status-code scenarios for an operation."""
    overrides = overrides or {}
    valid = valid_case(spec, op, overrides, case_id="%s::%s" % (scenario, op.key))
    if scenario == "S1_valid":
        return valid
    if scenario == "S2_missing_required":
        variants = [c for c in invalid_cases(spec, op, overrides)
                    if c.kind == "invalid_missing_required"]
        if not variants:
            return None
        case = variants[0]
        case.case_id = "S2_missing_required::%s" % op.key
        return case
    if scenario == "S3_wrong_type":
        variants = [c for c in invalid_cases(spec, op, overrides) if c.kind == "invalid_wrong_type"]
        if not variants:
            return None
        case = variants[0]
        case.case_id = "S3_wrong_type::%s" % op.key
        return case
    if scenario == "S4_unknown_path":
        return Case(
            case_id="S4_unknown_path::%s" % op.key,
            operation=op.key, kind="matrix_unknown_path",
            method=op.method, path=valid.path.rstrip("/") + "/__contract_probe__",
            query={k: v for k, v in valid.query.items() if False},  # drop query noise
            body=valid.body, has_body=valid.has_body,
            expected={"status": ["404"], "success": False},
            note="path does not exist in the spec",
        )
    if scenario == "S5_wrong_method":
        other = "delete" if op.method != "delete" else "get"
        return Case(
            case_id="S5_wrong_method::%s" % op.key,
            operation=op.key, kind="matrix_wrong_method",
            method=other, path=valid.path, query={}, body=None, has_body=False,
            expected={"status": ["404", "405"], "success": False},
            note="method %s not declared for this path" % other.upper(),
        )
    raise ValueError("unknown matrix scenario: %s" % scenario)


def generate_all(spec: Dict[str, Any], overrides: Optional[Dict[str, Any]] = None,
                 include_invalid: bool = True) -> List[Case]:
    """Generate the full case list (one valid case per operation + invalid variants)."""
    overrides = overrides or {}
    cases: List[Case] = []
    for op in spec_mod.enumerate_operations(spec):
        op_over = overrides.get(op.path) or overrides.get(op.key) or {}
        cases.append(valid_case(spec, op, op_over))
        if include_invalid:
            cases.extend(invalid_cases(spec, op, op_over))
    return cases