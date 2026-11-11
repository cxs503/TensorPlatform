"""Response checks: schema conformance, status conformance, numeric precision.

The numeric side is deliberately stronger than "is the field a number":

* **finiteness**  -- no NaN/Inf leaking through JSON;
* **spec-declared precision** -- ``multipleOf`` / ``example`` honoured;
* **declarative invariants** -- a data file supplies algebraic identities over
  response fields (``lhs`` vs ``rhs`` expression pairs) and the harness checks
  ``|lhs-rhs| <= max(abs_tol, rel_tol*|rhs|)``;
* **sensitivity** (in ``runner``) -- a response that does not react to its own
  inputs is flagged, which is how constant/MOCK numerics are caught.

Expressions are evaluated by a *whitelisted AST walker* (no ``eval`` of
arbitrary code, no attribute access, no imports), so an invariant file can be
supplied by an untrusted caller safely.
"""
from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_FUNCS = {
    "abs": abs,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "pow": pow,
}

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load,
    ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
    ast.USub, ast.UAdd, ast.Tuple, ast.Compare, ast.Lt, ast.LtE, ast.Gt,
    ast.GtE, ast.Eq, ast.NotEq,
)


class InvariantError(Exception):
    """Raised when an invariant expression cannot be evaluated."""


def safe_eval(expression: str, context: Dict[str, Any]) -> float:
    """Evaluate an arithmetic/logical expression over ``context`` (whitelisted AST)."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise InvariantError("syntax error in %r: %s" % (expression, exc)) from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise InvariantError("disallowed node %s in %r" % (type(node).__name__, expression))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise InvariantError("disallowed call in %r" % expression)
    try:
        value = eval(compile(tree, "<invariant>", "eval"), {"__builtins__": {}},  # noqa: S307
                     {**_FUNCS, **context})
    except Exception as exc:  # noqa: BLE001
        raise InvariantError("evaluation failed for %r: %s" % (expression, exc)) from exc
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except Exception as exc:  # noqa: BLE001
        raise InvariantError("non-numeric result for %r" % expression) from exc


# ---------------------------------------------------------------------------
# JSON flattening
# ---------------------------------------------------------------------------


def flatten(value: Any, prefix: str = "", out: Optional[Dict[str, Any]] = None,
            _depth: int = 0) -> Dict[str, Any]:
    """Flatten nested JSON to dotted keys (``a.b`` / ``c[0].d``)."""
    if out is None:
        out = {}
    if _depth > 30:
        return out
    if isinstance(value, dict):
        for key, item in value.items():
            child = "%s.%s" % (prefix, key) if prefix else str(key)
            flatten(item, child, out, _depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            flatten(item, "%s[%d]" % (prefix, index), out, _depth + 1)
    else:
        if prefix:
            out[prefix] = value
    return out


def numeric_leaves(value: Any) -> Dict[str, float]:
    leaves = flatten(value)
    return {
        key: float(item)
        for key, item in leaves.items()
        if isinstance(item, (int, float)) and not isinstance(item, bool)
    }


# ---------------------------------------------------------------------------
# schema standardisation + validation
# ---------------------------------------------------------------------------


def standardise(schema: Any, _depth: int = 0) -> Any:
    """Normalise OpenAPI 3.0/3.1 quirks into plain JSON Schema for validation."""
    if _depth > 40 or not isinstance(schema, (dict, list)):
        return schema
    if isinstance(schema, list):
        return [standardise(item, _depth + 1) for item in schema]
    out: Dict[str, Any] = {}
    for key, value in schema.items():
        if key in ("nullable", "example", "examples", "discriminator", "xml", "externalDocs",
                   "deprecated", "readOnly", "writeOnly"):
            continue
        if key in ("properties", "patternProperties", "definitions", "$defs"):
            out[key] = {k: standardise(v, _depth + 1) for k, v in (value or {}).items()}
        elif key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            out[key] = [standardise(item, _depth + 1) for item in (value or [])]
        elif key in ("items", "additionalProperties", "not", "contains", "propertyNames"):
            out[key] = standardise(value, _depth + 1)
        elif key in ("exclusiveMinimum", "exclusiveMaximum") and isinstance(value, bool):
            continue  # OpenAPI 3.0 boolean form: handled through minimum/maximum
        else:
            out[key] = value
    if schema.get("nullable") is True:
        stype = out.get("type")
        if isinstance(stype, str):
            out["type"] = [stype, "null"]
        elif isinstance(stype, list) and "null" not in stype:
            out["type"] = stype + ["null"]
    return out


@dataclass
class SchemaResult:
    checked: bool
    valid: bool
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"checked": self.checked, "valid": self.valid, "errors": self.errors[:5],
                "error_count": len(self.errors)}


def validate_schema(instance: Any, schema: Optional[Dict[str, Any]]) -> SchemaResult:
    """Validate ``instance`` against ``schema``; an empty schema is 'not checked'."""
    if not isinstance(schema, dict) or not schema:
        return SchemaResult(checked=False, valid=True, errors=[])
    try:
        import jsonschema
    except Exception:  # noqa: BLE001 - degrade: cannot validate without the library
        return SchemaResult(checked=False, valid=True, errors=["jsonschema unavailable"])
    validator_cls = jsonschema.validators.validator_for(standardise(schema))
    try:
        validator = validator_cls(standardise(schema))
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    except Exception as exc:  # noqa: BLE001 - a broken schema must not crash the run
        return SchemaResult(checked=False, valid=True, errors=["schema unusable: %s" % exc])
    return SchemaResult(checked=True, valid=not errors,
                        errors=["%s: %s" % ("/".join(str(p) for p in e.path) or "<root>", e.message)
                                for e in errors])


# ---------------------------------------------------------------------------
# status conformance
# ---------------------------------------------------------------------------


def status_conforms(expected: Sequence[str], actual: Optional[int]) -> bool:
    """Does ``actual`` match the spec-derived expectation pattern list?"""
    if actual is None:
        return False
    for pattern in expected:
        pattern = str(pattern)
        if pattern.endswith("XX") and len(pattern) == 3:
            if str(actual)[:1] == pattern[0]:
                return True
        elif pattern.isdigit() and int(pattern) == int(actual):
            return True
        elif pattern == "default":
            continue
    return False


# ---------------------------------------------------------------------------
# numeric precision + declarative invariants
# ---------------------------------------------------------------------------


@dataclass
class InvariantResult:
    invariant_id: str
    endpoint: str
    field: str
    lhs: float
    rhs: float
    rel_error: float
    abs_error: float
    tolerance: Dict[str, float]
    passed: bool
    status: str = "CHECKED"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.invariant_id,
            "endpoint": self.endpoint,
            "field": self.field,
            "lhs": self.lhs,
            "rhs": self.rhs,
            "rel_error": self.rel_error,
            "abs_error": self.abs_error,
            "tolerance": self.tolerance,
            "passed": self.passed,
            "status": self.status,
        }


def _rel_error(lhs: float, rhs: float) -> float:
    denominator = max(abs(rhs), 1e-30)
    return abs(lhs - rhs) / denominator


def check_invariant(name: str, endpoint: str, body: Any, spec_check: Dict[str, Any],
                    context: Optional[Dict[str, Any]] = None) -> InvariantResult:
    """Evaluate one declarative invariant against a response body."""
    flat = flatten(body)
    scope: Dict[str, Any] = {}
    scope.update({k.replace(".", "__"): v for k, v in flat.items()
                  if isinstance(v, (int, float)) and not isinstance(v, bool)})
    scope.update({k: v for k, v in flat.items() if isinstance(v, (int, float))})
    scope.update(context or {})
    rel_tol = float(spec_check.get("rel_tol", 0.03))
    abs_tol = float(spec_check.get("abs_tol", 1e-6))
    # Quantization-aware comparison: when a response field is published with a
    # finite number of decimals, the identity cannot hold tighter than the
    # published resolution.  The declared quantization (data) is added to the
    # absolute tolerance instead of silently loosening rel_tol.
    quantization = spec_check.get("quantization") or {}
    abs_tol += float(quantization.get("lhs", 0.0)) + float(quantization.get("rhs", 0.0))
    field_name = spec_check.get("field") or spec_check.get("lhs", "")
    try:
        lhs = safe_eval(str(spec_check.get("lhs", "nan")), scope)
        rhs = safe_eval(str(spec_check.get("rhs", "nan")), scope)
    except InvariantError as exc:
        return InvariantResult(name, endpoint, str(field_name), math.nan, math.nan,
                               math.inf, math.inf, {"rel": rel_tol, "abs": abs_tol},
                               False, status="UNEVALUABLE: %s" % exc)
    abs_error = abs(lhs - rhs)
    rel_error = _rel_error(lhs, rhs)
    passed = abs_error <= max(abs_tol, rel_tol * max(abs(rhs), 1e-30))
    return InvariantResult(name, endpoint, str(field_name), lhs, rhs, rel_error, abs_error,
                           {"rel": rel_tol, "abs": abs_tol}, passed)


def spec_declared_precision(body: Any, schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Check spec-declared numeric precision hints (``multipleOf`` / ``example``)."""
    result: Dict[str, Any] = {"checked": 0, "violations": [], "examples_compared": 0}
    if not isinstance(schema, dict) or not isinstance(body, dict):
        return result
    props = schema.get("properties") or {}
    for name, value in body.items():
        prop = props.get(name)
        if not isinstance(prop, dict):
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            multiple = prop.get("multipleOf")
            if isinstance(multiple, (int, float)) and multiple:
                result["checked"] += 1
                quotient = float(value) / float(multiple)
                if abs(quotient - round(quotient)) > 1e-6:
                    result["violations"].append(
                        {"field": name, "rule": "multipleOf", "value": value,
                         "multipleOf": multiple})
            if "example" in prop and isinstance(prop["example"], (int, float)):
                result["examples_compared"] += 1
                rel = _rel_error(float(value), float(prop["example"]))
                if rel > 0.03 and abs(float(value) - float(prop["example"])) > 1e-6:
                    result["violations"].append(
                        {"field": name, "rule": "example", "value": value,
                         "example": prop["example"], "rel_error": rel})
    return result


def finiteness(body: Any) -> Dict[str, Any]:
    leaves = numeric_leaves(body)
    bad = [key for key, value in leaves.items() if not math.isfinite(value)]
    return {"numeric_fields": len(leaves), "non_finite": bad, "ok": not bad}


def numeric_diff(left: Any, right: Any) -> Dict[str, Any]:
    """Compare two numeric field maps (determinism probe)."""
    a, b = numeric_leaves(left), numeric_leaves(right)
    shared = sorted(set(a) & set(b))
    diffs = {key: abs(a[key] - b[key]) for key in shared}
    worst = max(diffs.values()) if diffs else 0.0
    return {
        "compared": len(shared),
        "max_abs_diff": worst,
        "differing": [k for k, v in diffs.items() if v > 1e-6][:10],
        "only_left": sorted(set(a) - set(b))[:10],
        "only_right": sorted(set(b) - set(a))[:10],
    }