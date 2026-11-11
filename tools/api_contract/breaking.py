"""Spec-to-spec comparison and incompatible-change detection.

The detector answers one question: *if the implementation keeps behaving like
the old spec, will existing clients break?*  Five change families are required by
acceptance criterion (d), and each one has (i) a detection rule here and (ii) a
mutation injector used to prove the rule actually fires:

===========================  =============================================
change code                  meaning
===========================  =============================================
``NEW_REQUIRED_FIELD``       a previously optional request field/parameter is
                             now mandatory (old clients omit it -> 4xx)
``FIELD_REMOVAL``            a response field disappeared / a request field the
                             server used to accept is gone
``STATUS_CODE_CHANGE``       declared success status code set changed
``ERROR_FORMAT_CHANGE``      error payload contract changed (schema dropped or
                             retyped, ``detail``-style field removed)
``PRECISION_DEGRADATION``    declared numeric granularity got coarser
                             (``multipleOf`` added/increased or removed hint)
===========================  =============================================

Extra codes (``OPERATION_REMOVED``, ``TYPE_CHANGE``, ``PARAM_REMOVED``,
``NEW_OPERATION`` ...) are reported for completeness; only *breaking* codes fail
the gate.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import spec as spec_mod

BREAKING_CODES = frozenset({
    "NEW_REQUIRED_FIELD",
    "FIELD_REMOVAL",
    "STATUS_CODE_CHANGE",
    "ERROR_FORMAT_CHANGE",
    "PRECISION_DEGRADATION",
    "OPERATION_REMOVED",
    "TYPE_CHANGE",
    "PARAM_REMOVED",
    "RESPONSE_NEW_REQUIRED",
    "ENUM_NARROWED",
})

MUTATION_KINDS: Tuple[str, ...] = (
    "new_required_field",
    "field_removal",
    "status_code_change",
    "error_format_change",
    "precision_degradation",
)

MUTATION_EXPECTED_CODE = {
    "new_required_field": "NEW_REQUIRED_FIELD",
    "field_removal": "FIELD_REMOVAL",
    "status_code_change": "STATUS_CODE_CHANGE",
    "error_format_change": "ERROR_FORMAT_CHANGE",
    "precision_degradation": "PRECISION_DEGRADATION",
}


# ---------------------------------------------------------------------------
# helpers over a raw (not dereferenced) spec
# ---------------------------------------------------------------------------


def _op_spec(spec: Dict[str, Any], path: str, method: str) -> Optional[Dict[str, Any]]:
    item = (spec.get("paths") or {}).get(path)
    if not isinstance(item, dict):
        return None
    op = item.get(method)
    return op if isinstance(op, dict) else None


def _body_schema_raw(spec: Dict[str, Any], op: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    body = op.get("requestBody")
    if not isinstance(body, dict):
        return None
    body = spec_mod.deref(spec, body)
    content = body.get("content") or {}
    for media in content.values():
        if isinstance(media, dict) and media.get("schema"):
            schema = spec_mod.deref(spec, media["schema"])
            return schema if isinstance(schema, dict) else None
    return None


def _required(schema: Optional[Dict[str, Any]]) -> set:
    if not isinstance(schema, dict):
        return set()
    return set(schema.get("required") or [])


def _props(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _resolve_schema(spec: Dict[str, Any], schema: Any) -> Dict[str, Any]:
    resolved = spec_mod.deref(spec, schema) if isinstance(schema, (dict, list)) else schema
    return resolved if isinstance(resolved, dict) else {}


def _response_schema_raw(spec: Dict[str, Any], op: Dict[str, Any], code: str) -> Optional[Dict[str, Any]]:
    entry = (op.get("responses") or {}).get(code)
    if not isinstance(entry, dict):
        entry = (op.get("responses") or {}).get(str(code))
    if not isinstance(entry, dict):
        return None
    entry = spec_mod.deref(spec, entry)
    for media in (entry.get("content") or {}).values():
        if isinstance(media, dict) and media.get("schema"):
            return _resolve_schema(spec, media["schema"])
    return None


def _live_node(spec: Dict[str, Any], node: Any) -> Optional[Dict[str, Any]]:
    """Return the *live* schema node (following ``$ref`` without copying).

    Mutation injection must edit the document, not a dereferenced copy, so this
    helper is deliberately separate from :func:`spec_mod.deref`.
    """
    for _ in range(10):
        if not isinstance(node, dict):
            return None
        ref = node.get("$ref")
        if not isinstance(ref, str):
            return node
        node = spec_mod.resolve_pointer(spec, ref)
    return None


def _live_body_schema(spec: Dict[str, Any], op: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    body = op.get("requestBody")
    if not isinstance(body, dict):
        return None
    body = _live_node(spec, body) or body
    for media in (body.get("content") or {}).values():
        if isinstance(media, dict) and media.get("schema"):
            return _live_node(spec, media["schema"])
    return None


def _live_response_schema(spec: Dict[str, Any], op: Dict[str, Any], code: str) -> Optional[Dict[str, Any]]:
    entry = (op.get("responses") or {}).get(code)
    if not isinstance(entry, dict):
        return None
    entry = _live_node(spec, entry) or entry
    for media in (entry.get("content") or {}).values():
        if isinstance(media, dict) and media.get("schema"):
            return _live_node(spec, media["schema"])
    return None


def _precision_hint(schema: Dict[str, Any]) -> Optional[float]:
    """Return declared granularity: ``multipleOf`` (fallback: x-precision digits)."""
    multiple = schema.get("multipleOf")
    if isinstance(multiple, (int, float)) and multiple:
        return float(multiple)
    digits = schema.get("x-precision")
    if isinstance(digits, (int, float)):
        return float(10) ** (-int(digits))
    return None


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def _change(code: str, path: str, method: str, detail: str) -> Dict[str, Any]:
    return {
        "code": code,
        "path": path,
        "method": method,
        "breaking": code in BREAKING_CODES,
        "detail": detail,
    }


def diff_specs(base: Dict[str, Any], head: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Compare two OpenAPI documents; returns a list of classified changes."""
    changes: List[Dict[str, Any]] = []
    base_ops = {(op.path, op.method): op for op in spec_mod.enumerate_operations(base)}
    head_ops = {(op.path, op.method): op for op in spec_mod.enumerate_operations(head)}

    for key in sorted(base_ops):
        path, method = key
        if key not in head_ops:
            changes.append(_change("OPERATION_REMOVED", path, method,
                                   "operation present in base spec is gone"))
            continue
        base_op, head_op = spec_mod.operation_dict(base, base_ops[key]), spec_mod.operation_dict(head, head_ops[key])
        changes.extend(_diff_operation(base, head, path, method, base_op, head_op))
    for key in sorted(set(head_ops) - set(base_ops)):
        changes.append(_change("NEW_OPERATION", key[0], key[1], "operation added (non-breaking)"))
    return changes


def _diff_operation(base: Dict[str, Any], head: Dict[str, Any], path: str, method: str,
                    base_op: Dict[str, Any], head_op: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    # --- request contract -------------------------------------------------
    base_body = _body_schema_raw(base, base_op)
    head_body = _body_schema_raw(head, head_op)
    added_required = _required(head_body) - _required(base_body)
    if added_required:
        out.append(_change("NEW_REQUIRED_FIELD", path, method,
                           "newly required body fields: %s" % sorted(added_required)))
    removed_body = set(_props(base_body)) - set(_props(head_body))
    if removed_body:
        out.append(_change("FIELD_REMOVAL", path, method,
                           "request fields removed from schema: %s" % sorted(removed_body)))
    for name in sorted(set(_props(base_body)) & set(_props(head_body))):
        b, h = _props(base_body)[name], _props(head_body)[name]
        if isinstance(b, dict) and isinstance(h, dict) and b.get("type") != h.get("type"):
            out.append(_change("TYPE_CHANGE", path, method,
                               "request field %r type %r -> %r" % (name, b.get("type"), h.get("type"))))
        if isinstance(b, dict) and isinstance(h, dict) and b.get("enum") and h.get("enum"):
            if not set(h["enum"]).issubset(set(b["enum"])):
                out.append(_change("ENUM_NARROWED", path, method,
                                   "request field %r enum narrowed" % name))

    # --- parameters -------------------------------------------------------
    def _params(doc: Dict[str, Any], op: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
        item = (doc.get("paths") or {}).get(path) or {}
        merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for group in (item.get("parameters") or [], op.get("parameters") or []):
            for prm in group or []:
                prm = spec_mod.deref(doc, prm)
                if isinstance(prm, dict) and prm.get("name"):
                    merged[(prm["name"], prm.get("in", ""))] = prm
        return merged

    base_params, head_params = _params(base, base_op), _params(head, head_op)
    newly_required = {
        key for key, prm in head_params.items()
        if prm.get("required") and not (base_params.get(key) or {}).get("required")
    }
    if newly_required:
        out.append(_change("NEW_REQUIRED_FIELD", path, method,
                           "newly required parameters: %s" % sorted(n[0] for n in newly_required)))
    for key in sorted(set(base_params) - set(head_params)):
        out.append(_change("PARAM_REMOVED", path, method,
                           "parameter %r removed" % (key[0],)))

    # --- status codes -----------------------------------------------------
    base_codes = set(spec_mod.declared_success_codes(base, spec_mod.Operation(path, method)))
    head_codes = set(spec_mod.declared_success_codes(head, spec_mod.Operation(path, method)))
    if base_codes and base_codes != head_codes:
        out.append(_change("STATUS_CODE_CHANGE", path, method,
                           "success codes %s -> %s" % (sorted(base_codes), sorted(head_codes))))

    # --- response payload -------------------------------------------------
    resp_props_base: Dict[str, Any] = {}
    resp_props_head: Dict[str, Any] = {}
    for code in sorted(base_codes & head_codes):
        b_schema = _response_schema_raw(base, base_op, code) or {}
        h_schema = _response_schema_raw(head, head_op, code) or {}
        b_props, h_props = _props(b_schema), _props(h_schema)
        resp_props_base.update(b_props)
        resp_props_head.update(h_props)
        gone = set(b_props) - set(h_props)
        if gone:
            out.append(_change("FIELD_REMOVAL", path, method,
                               "response fields removed (%s): %s" % (code, sorted(gone))))
        for name in sorted(set(b_props) & set(h_props)):
            b, h = b_props[name], h_props[name]
            if not isinstance(b, dict) or not isinstance(h, dict):
                continue
            if b.get("type") != h.get("type"):
                out.append(_change("TYPE_CHANGE", path, method,
                                   "response field %r type %r -> %r" % (name, b.get("type"), h.get("type"))))
            b_hint, h_hint = _precision_hint(b), _precision_hint(h)
            if b_hint is not None and h_hint is not None and h_hint > b_hint * (1 + 1e-12):
                out.append(_change("PRECISION_DEGRADATION", path, method,
                                   "field %r multipleOf/x-precision %g -> %g" % (name, b_hint, h_hint)))
            elif b_hint is None and h_hint is not None:
                out.append(_change("PRECISION_DEGRADATION", path, method,
                                   "field %r gained coarse granularity %g" % (name, h_hint)))
        new_required = _required(h_schema) - _required(b_schema)
        if new_required:
            out.append(_change("RESPONSE_NEW_REQUIRED", path, method,
                               "response %s newly requires %s" % (code, sorted(new_required))))

    # --- error payload contract ------------------------------------------
    base_errors = set(spec_mod.declared_error_codes(base, spec_mod.Operation(path, method)))
    head_errors = set(spec_mod.declared_error_codes(head, spec_mod.Operation(path, method)))
    for code in sorted(base_errors & head_errors):
        b_schema = _response_schema_raw(base, base_op, code)
        h_schema = _response_schema_raw(head, head_op, code)
        b_props, h_props = _props(b_schema), _props(h_schema)
        if b_props and not h_props:
            out.append(_change("ERROR_FORMAT_CHANGE", path, method,
                               "error %s payload schema dropped" % code))
        else:
            gone = set(b_props) - set(h_props)
            retyped = [
                name for name in set(b_props) & set(h_props)
                if isinstance(b_props[name], dict) and isinstance(h_props[name], dict)
                and b_props[name].get("type") != h_props[name].get("type")
            ]
            if gone or retyped:
                out.append(_change("ERROR_FORMAT_CHANGE", path, method,
                                   "error %s payload changed (removed=%s retyped=%s)"
                                   % (code, sorted(gone), sorted(retyped))))

    # --- error payload for a *new* successful code mapping ---------------
    if base_errors - head_errors:
        out.append(_change("ERROR_FORMAT_CHANGE", path, method,
                           "declared error codes removed: %s" % sorted(base_errors - head_errors)))
    return out


def summarise(changes: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    breaking = [c for c in changes if c.get("breaking")]
    by_code: Dict[str, int] = {}
    for change in changes:
        by_code[change["code"]] = by_code.get(change["code"], 0) + 1
    return {
        "total": len(changes),
        "breaking": len(breaking),
        "non_breaking": len(changes) - len(breaking),
        "by_code": by_code,
        "breaking_codes": sorted({c["code"] for c in breaking}),
    }


# ---------------------------------------------------------------------------
# mutation injection (proves the detector fires)
# ---------------------------------------------------------------------------


def inject_mutation(spec: Dict[str, Any], kind: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return ``(mutated_spec, info)`` for one of ``MUTATION_KINDS``."""
    if kind not in MUTATION_KINDS:
        raise ValueError("unknown mutation kind %r (expected one of %s)" % (kind, MUTATION_KINDS))
    mutated = copy.deepcopy(spec)
    ops = spec_mod.enumerate_operations(mutated)

    def with_body(op: spec_mod.Operation) -> Optional[Dict[str, Any]]:
        schema = _live_body_schema(mutated, op.raw)
        return schema if isinstance(schema, dict) and _props(schema) else None

    if kind == "new_required_field":
        for op in ops:
            schema = with_body(op)
            if not schema:
                continue
            props = _props(schema)
            optional = [name for name in props if name not in _required(schema)]
            if optional:
                name = optional[0]
            else:
                # every declared field is already required: the *stricter*
                # mutation is a brand-new mandatory field, which is exactly the
                # incompatible change we want to prove is detectable.
                name = "__contract_probe_new_required__"
                props[name] = {"type": "string"}
            schema.setdefault("required", []).append(name)
            return mutated, {"kind": kind, "path": op.path, "method": op.method,
                             "field": name, "expected_code": MUTATION_EXPECTED_CODE[kind]}

    if kind == "field_removal":
        for op in ops:
            for code in spec_mod.declared_success_codes(mutated, op):
                schema = _live_response_schema(mutated, op.raw, code)
                props = _props(schema)
                if not props:
                    continue
                name = sorted(props)[0]
                props.pop(name)
                return mutated, {"kind": kind, "path": op.path, "method": op.method,
                                 "code": code, "field": name,
                                 "expected_code": MUTATION_EXPECTED_CODE[kind]}

    if kind == "status_code_change":
        for op in ops:
            responses = op.raw.get("responses") or {}
            success = [code for code in responses if str(code)[:1] == "2"]
            if not success:
                continue
            old = str(success[0])
            target = "201" if old != "201" else "200"
            responses[target] = responses.pop(success[0])
            return mutated, {"kind": kind, "path": op.path, "method": op.method,
                             "from": old, "to": target,
                             "expected_code": MUTATION_EXPECTED_CODE[kind]}

    if kind == "error_format_change":
        for op in ops:
            for code in spec_mod.declared_error_codes(mutated, op):
                schema = _live_response_schema(mutated, op.raw, code)
                props = _props(schema)
                if not props:
                    continue
                name = sorted(props)[0]
                props[name] = {"type": "string"}  # object -> string retype
                return mutated, {"kind": kind, "path": op.path, "method": op.method,
                                 "code": code, "field": name,
                                 "expected_code": MUTATION_EXPECTED_CODE[kind]}

    if kind == "precision_degradation":
        for op in ops:
            for code in spec_mod.declared_success_codes(mutated, op):
                schema = _live_response_schema(mutated, op.raw, code)
                for name, prop in sorted(_props(schema).items()):
                    if isinstance(prop, dict) and prop.get("type") in ("number", "integer"):
                        prop["multipleOf"] = 1.0 if prop.get("type") == "integer" else 0.5
                        return mutated, {"kind": kind, "path": op.path, "method": op.method,
                                         "code": code, "field": name, "multipleOf": prop["multipleOf"],
                                         "expected_code": MUTATION_EXPECTED_CODE[kind]}
    raise RuntimeError("no mutation target found for kind %r" % kind)


def verify_mutations(spec: Dict[str, Any], kinds: Sequence[str] = MUTATION_KINDS) -> Dict[str, Any]:
    """Inject each mutation and check that the detector reports its code."""
    rows = []
    for kind in kinds:
        mutated, info = inject_mutation(spec, kind)
        changes = diff_specs(spec, mutated)
        codes = {c["code"] for c in changes}
        expected = info["expected_code"]
        rows.append({
            "kind": kind,
            "expected_code": expected,
            "detected_codes": sorted(codes),
            "detected": expected in codes,
            "mutation": {k: v for k, v in info.items() if k != "expected_code"},
        })
    return {
        "injected": len(rows),
        "detected": sum(1 for row in rows if row["detected"]),
        "detection_rate": (sum(1 for row in rows if row["detected"]) / len(rows)) if rows else 0.0,
        "rows": rows,
    }