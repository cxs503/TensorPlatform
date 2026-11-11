"""OpenAPI document loading, reference resolution and operation enumeration.

Design constraints (TensorPlatform iron rule):
    This module -- like every other module in ``tools/api_contract`` -- must stay
    **domain agnostic**.  No endpoint literal, host name, port or domain noun is
    baked in; everything concrete (spec source, base URL, request overrides,
    numeric invariants) is injected as data at run time.

Degradation contract (negative control, criterion f):
    * source missing / unreadable / unreachable  -> ``SpecLoad(status="WITHHELD")``
    * source present but not a usable OpenAPI doc -> ``SpecLoad(status="ERROR")``
    * otherwise                                   -> ``SpecLoad(status="OK")``

Nothing in this module raises on bad input: degraded states are *values*, so a
caller can record them and keep going (a contract run must never crash because
the thing it audits is absent).
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

HTTP_METHODS: Tuple[str, ...] = ("get", "post", "put", "patch", "delete")

STATUS_OK = "OK"
STATUS_WITHHELD = "WITHHELD"
STATUS_ERROR = "ERROR"


@dataclass
class SpecLoad:
    """Result of loading an OpenAPI document."""

    status: str
    spec: Optional[Dict[str, Any]] = None
    reason: str = ""
    source: str = ""
    sha256: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK and isinstance(self.spec, dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "source": self.source,
            "sha256": self.sha256,
            "path_count": len(self.spec.get("paths", {})) if self.ok else 0,
        }


def _sha256(raw: bytes) -> str:
    import hashlib

    return hashlib.sha256(raw).hexdigest()


def load_spec(source: str, timeout: float = 30.0) -> SpecLoad:
    """Load an OpenAPI document from a filesystem path or an http(s) URL."""
    if not source:
        return SpecLoad(STATUS_WITHHELD, reason="empty spec source", source="")
    raw: bytes
    try:
        if re.match(r"^https?://", source):
            with urllib.request.urlopen(source, timeout=timeout) as fh:
                raw = fh.read()
        else:
            if not os.path.exists(source):
                return SpecLoad(
                    STATUS_WITHHELD, reason="spec source not found: %s" % source, source=source
                )
            with open(source, "rb") as fh:
                raw = fh.read()
    except Exception as exc:  # noqa: BLE001 - degradation is a value, never a crash
        return SpecLoad(
            STATUS_WITHHELD, reason="spec source unreadable: %s: %s" % (type(exc).__name__, exc),
            source=source,
        )

    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return SpecLoad(STATUS_ERROR, reason="malformed JSON: %s" % exc, source=source,
                        sha256=_sha256(raw))

    if not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict):
        return SpecLoad(STATUS_ERROR, reason="not an OpenAPI document (missing 'paths' object)",
                        source=source, sha256=_sha256(raw))

    return SpecLoad(STATUS_OK, spec=doc, source=source, sha256=_sha256(raw))


# ---------------------------------------------------------------------------
# $ref resolution
# ---------------------------------------------------------------------------

_JSON_POINTER_ESCAPE = {"~1": "/", "~0": "~"}


def _unescape(token: str) -> str:
    for esc, char in _JSON_POINTER_ESCAPE.items():
        token = token.replace(esc, char)
    return token


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """Resolve a local JSON pointer (``#/a/b/0``).  Returns ``None`` if absent."""
    if not pointer.startswith("#"):
        return None
    node = doc
    for token in pointer[1:].split("/"):
        if token == "":
            continue
        token = _unescape(token)
        if isinstance(node, dict):
            node = node.get(token)
        elif isinstance(node, list):
            try:
                node = node[int(token)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if node is None:
            return None
    return node


def deref(spec: Dict[str, Any], schema: Any, _depth: int = 0, _seen: Optional[set] = None) -> Any:
    """Fully inline ``$ref`` chains so downstream validators see plain schemas.

    Cyclic references are cut (replaced by ``{}``) which keeps the schema
    permissive instead of looping forever.
    """
    if _seen is None:
        _seen = set()
    if _depth > 24 or not isinstance(schema, (dict, list)):
        return schema
    if isinstance(schema, list):
        return [deref(spec, item, _depth + 1, _seen) for item in schema]
    ref = schema.get("$ref")
    if isinstance(ref, str):
        if ref in _seen:
            return {}
        target = resolve_pointer(spec, ref)
        if target is None:
            return {}
        merged = {k: v for k, v in schema.items() if k != "$ref"}
        out = deref(spec, target, _depth + 1, _seen | {ref})
        if merged and isinstance(out, dict):
            out = {**out, **merged}
        return out
    return {key: deref(spec, value, _depth + 1, _seen) for key, value in schema.items()}


# ---------------------------------------------------------------------------
# Operation enumeration
# ---------------------------------------------------------------------------


@dataclass
class Operation:
    path: str
    method: str
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return "%s %s" % (self.method.upper(), self.path)

    def as_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "method": self.method}


def enumerate_operations(spec: Dict[str, Any]) -> List[Operation]:
    ops: List[Operation] = []
    for path, item in sorted((spec.get("paths") or {}).items()):
        if not isinstance(item, dict):
            continue
        for method in HTTP_METHODS:
            op = item.get(method)
            if isinstance(op, dict):
                ops.append(Operation(path=path, method=method, raw=op))
    return ops


def operation_dict(spec: Dict[str, Any], op: Operation) -> Dict[str, Any]:
    """Return the operation object, looking it up when ``op.raw`` is empty.

    ``Operation`` instances are frequently built from just ``(path, method)``
    (reporting, matrix rows, diff helpers); resolving lazily keeps that ergonomic
    without duplicating the static ``spec`` reference.
    """
    if isinstance(op.raw, dict) and op.raw:
        return op.raw
    item = (spec.get("paths") or {}).get(op.path) or {}
    resolved = item.get(op.method) if isinstance(item, dict) else None
    return resolved if isinstance(resolved, dict) else {}


def get_operation(spec: Dict[str, Any], path: str, method: str) -> Operation:
    """Build an Operation with its raw document node attached."""
    op = Operation(path=path, method=method.lower())
    op.raw = operation_dict(spec, op)
    return op


def _merge_parameters(spec: Dict[str, Any], path_item: Dict[str, Any], op: Dict[str, Any]) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for params in (path_item.get("parameters") or [], op.get("parameters") or []):
        for param in params or []:
            param = deref(spec, param)
            if isinstance(param, dict) and param.get("name"):
                merged[(param["name"], param.get("in", ""))] = param
    return list(merged.values())


def operation_parameters(spec: Dict[str, Any], op: Operation) -> List[Dict[str, Any]]:
    item = (spec.get("paths") or {}).get(op.path) or {}
    return _merge_parameters(spec, item, operation_dict(spec, op))


def path_parameters(spec: Dict[str, Any], op: Operation) -> List[Dict[str, Any]]:
    return [p for p in operation_parameters(spec, op) if p.get("in") == "path"]


def query_parameters(spec: Dict[str, Any], op: Operation, required_only: bool = False) -> List[Dict[str, Any]]:
    params = [p for p in operation_parameters(spec, op) if p.get("in") == "query"]
    if required_only:
        params = [p for p in params if p.get("required")]
    return params


def request_body_schema(spec: Dict[str, Any], op: Operation) -> Optional[Dict[str, Any]]:
    body = operation_dict(spec, op).get("requestBody")
    if not isinstance(body, dict):
        return None
    body = deref(spec, body)
    content = body.get("content") or {}
    for ctype in ("application/json", "application/*+json", "*/*"):
        media = content.get(ctype)
        if isinstance(media, dict) and media.get("schema"):
            return deref(spec, media["schema"])
    for media in content.values():  # first declared media type as fallback
        if isinstance(media, dict) and media.get("schema"):
            return deref(spec, media["schema"])
    return None


def request_body_required(spec: Dict[str, Any], op: Operation) -> bool:
    body = operation_dict(spec, op).get("requestBody")
    if not isinstance(body, dict):
        return False
    return bool(deref(spec, body).get("required"))


def response_schema(spec: Dict[str, Any], op: Operation, status: str = "200") -> Optional[Dict[str, Any]]:
    responses = operation_dict(spec, op).get("responses") or {}
    candidates = [status, status[:1] + "XX", "default"]
    for code in candidates:
        entry = responses.get(code)
        if not isinstance(entry, dict):
            continue
        entry = deref(spec, entry)
        content = entry.get("content") or {}
        for media in content.values():
            if isinstance(media, dict) and media.get("schema"):
                return deref(spec, media["schema"])
        return None
    return None


def declared_success_codes(spec: Dict[str, Any], op: Operation) -> List[str]:
    codes = [
        str(code)
        for code in (operation_dict(spec, op).get("responses") or {})
        if str(code)[:1] == "2" or str(code) == "default"
    ]
    return sorted(set(codes))


def declared_error_codes(spec: Dict[str, Any], op: Operation) -> List[str]:
    codes = [
        str(code)
        for code in (operation_dict(spec, op).get("responses") or {})
        if str(code)[:1] in {"4", "5"}
    ]
    return sorted(set(codes))


def spec_fingerprint(spec: Dict[str, Any]) -> Dict[str, Any]:
    ops = enumerate_operations(spec)
    return {
        "openapi": spec.get("openapi") or spec.get("swagger"),
        "paths": len(spec.get("paths") or {}),
        "operations": len(ops),
        "components": len(((spec.get("components") or {}).get("schemas") or {})),
    }


def iter_operations(spec: Dict[str, Any]) -> Iterator[Operation]:
    return iter(enumerate_operations(spec))