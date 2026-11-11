"""Target discovery: OpenAPI → list of probe targets, with no domain knowledge.

Everything the detector needs to know about an endpoint (what to POST, which
fields are numeric) is read out of the endpoint's own OpenAPI schema.  The code
below only understands JSON-Schema types; it has no idea what the fields mean,
which is exactly the property that lets the same detector run against any
service that publishes a schema.

Synthesis rules for a field without a schema ``default``:

* ``minimum``/``maximum`` present  → midpoint of the declared admissible range
  (clamped so that a positive-scaled sweep stays inside the range);
* ``enum``                        → first enum member;
* otherwise                       → deterministic pick from a generic numeric
  ladder, indexed by ``sha256(field name)`` — reproducible across runs, and
  deliberately unrelated to anything semantic.

Payloads that the service rejects (400/422) are **not** patched by guessing:
the scanner retries with the next ladder index a bounded number of times and, if
still rejected, the endpoint is reported ``WITHHELD`` (no evidence) rather than
scored.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from .model import Target

#: generic numeric ladder — values, not meanings
GENERIC_LADDER = (1.0, 2.0, 3.0, 5.0, 8.0, 10.0, 20.0, 100.0)

#: request paths that are skipped by default: they are asynchronous jobs, uploads,
#: websockets or state-mutating deletes, where a "same request twice" probe is
#: either meaningless or harmful.  These are HTTP/transport verbs, not domain
#: semantics.
DEFAULT_EXCLUDE = (
    r"simulat", r"train", r"upload", r"stream", r"websocket", r"batch",
    r"delete", r"restart", r"kill", r"shutdown", r"/\{.*\}", r"export",
    r"download", r"login", r"token",
)

MAX_PROPERTIES = 24


def _digest_index(name: str, modulo: int) -> int:
    """Deterministic index derived from a field name (reproducible, non-semantic)."""
    h = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return int(h[:8], 16) % max(modulo, 1)


def resolve_ref(spec: Dict[str, Any], schema: Any, depth: int = 0) -> Dict[str, Any]:
    """Follow a local ``$ref`` (``#/components/schemas/X``); stop on anything odd."""
    if depth > 6 or not isinstance(schema, dict):
        return schema if isinstance(schema, dict) else {}
    ref = schema.get("$ref")
    if not ref or not isinstance(ref, str) or not ref.startswith("#/"):
        return schema
    node: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            return {}
        node = node[part]
    return resolve_ref(spec, node, depth + 1)


def _value_for(name: str, prop: Dict[str, Any], attempt: int = 0) -> Any:
    """Synthesise a value for one schema property (structural rules only)."""
    prop = dict(prop or {})
    if "default" in prop and attempt == 0:
        return prop["default"]
    enum = prop.get("enum")
    if enum:
        return enum[min(attempt, len(enum) - 1)]
    typ = prop.get("type")
    if isinstance(typ, list):
        typ = next((t for t in typ if t != "null"), None)
    lo, hi = prop.get("minimum"), prop.get("maximum")
    exclusive_min = prop.get("exclusiveMinimum")
    if isinstance(exclusive_min, (int, float)) and lo is None:
        lo = exclusive_min
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi > lo:
        base = lo + (hi - lo) / 2.0
        if typ == "integer":
            return int(round(base))
        return round(base, 10)
    if isinstance(lo, (int, float)) and hi is None:
        base = max(float(lo), 1.0)
        if "exclusiveMinimum" in prop:
            base = float(lo) * 1.5 if float(lo) > 0 else 1.0
        return int(round(base)) if typ == "integer" else round(base, 10)
    ladder = GENERIC_LADDER
    if attempt:
        ladder = tuple(reversed(GENERIC_LADDER))
    idx = (_digest_index(name, len(ladder)) + attempt) % len(ladder)
    val = ladder[idx]
    if isinstance(hi, (int, float)) and val > hi:
        val = float(hi)
    if isinstance(lo, (int, float)) and val < lo:
        val = float(lo)
    return int(val) if typ == "integer" else float(val)


def _body_schema(item: Dict[str, Any], spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rb = item.get("requestBody") or {}
    content = rb.get("content") or {}
    for media in ("application/json", "application/*+json", "*/*"):
        if media in content:
            sch = resolve_ref(spec, content[media].get("schema") or {})
            if sch.get("type") == "object" or "properties" in sch:
                return sch
    return None


def cap_id_from(path: str, method: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z]+", ".", path).strip(".")
    slug = slug.replace("api.v1.", "")
    return f"{method.lower()}:{slug}"


def build_payload(schema: Dict[str, Any], attempt: int = 0,
                  max_properties: int = MAX_PROPERTIES) -> Dict[str, Any]:
    """Synthesise a JSON body from an object schema (scalars only, no nesting)."""
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    payload: Dict[str, Any] = {}
    for name in list(props)[:max_properties]:
        prop = resolve_ref({}, props[name]) if isinstance(props[name], dict) else {}
        typ = prop.get("type")
        if isinstance(typ, list):
            typ = next((t for t in typ if t != "null"), None)
        if typ in ("object", "array") or "properties" in prop or "items" in prop:
            if name in required:
                # nested required structure: skip the endpoint (cannot be built
                # without domain knowledge) — flagged upstream
                return {}
            continue
        if typ is None and "anyOf" in prop:
            inner = next((resolve_ref({}, s) for s in prop["anyOf"]
                          if isinstance(s, dict) and s.get("type") not in (None, "null")), {})
            typ = inner.get("type")
            prop = {**inner, **{k: v for k, v in prop.items() if k != "anyOf"}}
        if typ == "string":
            payload[name] = prop.get("default", "probe")
        elif typ in ("number", "integer"):
            payload[name] = _value_for(name, prop, attempt)
        elif typ == "boolean":
            payload[name] = bool(prop.get("default", True))
        elif name in required:
            return {}
    return payload


def build_targets(spec: Dict[str, Any], include: Optional[str] = None,
                  exclude: Optional[str] = None, max_targets: int = 200,
                  methods: tuple = ("post",), timeout_s: float = 20.0
                  ) -> List[Target]:
    """One :class:`Target` per matching POST endpoint that has a JSON body schema."""
    exclude_re = re.compile(exclude or "|".join(DEFAULT_EXCLUDE), re.I)
    include_re = re.compile(include, re.I) if include else None
    targets: List[Target] = []
    for path, item in sorted((spec.get("paths") or {}).items()):
        if not isinstance(item, dict) or not item:
            continue
        if exclude_re.search(path):
            continue
        if include_re and not include_re.search(path):
            continue
        for method in methods:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            schema = _body_schema(op, spec)
            if not schema:
                continue
            payload = build_payload(schema)
            if not payload:
                continue
            numeric = [k for k, v in payload.items()
                       if isinstance(v, (int, float)) and not isinstance(v, bool)]
            targets.append(Target(
                cap_id=cap_id_from(path, method),
                method=method.upper(),
                path=path,
                base_payload=payload,
                sweep_fields=numeric,
                timeout_s=timeout_s,
                note=(op.get("summary") or "")[:120],
            ))
            if len(targets) >= max_targets:
                return targets
    return targets


def alternate_payload(target: Target, attempt: int) -> Optional[Dict[str, Any]]:
    """A second/third candidate body (re-drawn ladder index) for rejected targets."""
    base = target.base_payload or {}
    out: Dict[str, Any] = {}
    for key, val in base.items():
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            out[key] = val
            continue
        ladder = tuple(reversed(GENERIC_LADDER))
        idx = (_digest_index(key, len(ladder)) + attempt) % len(ladder)
        new = ladder[idx]
        if isinstance(val, int):
            new = int(round(new))
        out[key] = new
    return out if out != base else None


def targets_from_audit_specs(timeout_s: float = 20.0) -> List[Target]:
    """Targets built from the *domain-owned* payload registry, if this repo has one.

    The detector itself never learns what the fields mean: the payloads and the
    list of sweepable fields are read out of the registry that the domain repo
    owns (``tools.capability_audit.adapters``).  This is the injection seam for
    endpoints whose request bodies cannot be synthesised from the schema alone.
    """
    try:
        from tools.capability_audit import adapters as cap_adapters
    except Exception:
        return []
    out: List[Target] = []
    for spec in getattr(cap_adapters, "SPECS", []) or []:
        endpoint = getattr(spec, "endpoint", None)
        payload = getattr(spec, "base_input", None)
        if not endpoint or not isinstance(payload, dict):
            continue
        numeric = [k for k, v in payload.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)]
        out.append(Target(
            cap_id=getattr(spec, "cap_id", endpoint),
            method="POST",
            path=endpoint if endpoint.startswith("/") else "/" + endpoint,
            base_payload=dict(payload),
            sweep_fields=numeric,
            timeout_s=timeout_s,
            note="payload injected from the domain capability-audit registry",
        ))
    return out


def load_spec_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def targets_from_config(path: str) -> List[Target]:
    """Explicit target list (config/artifact injection for domain-owned payloads)."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    raw = data.get("targets") if isinstance(data, dict) else data
    out = []
    for rec in raw or []:
        out.append(Target(
            cap_id=rec["cap_id"],
            method=rec.get("method", "POST"),
            path=rec["path"],
            base_payload=rec.get("base_payload") or {},
            sweep_fields=rec.get("sweep_fields"),
            timeout_s=float(rec.get("timeout_s", 20.0)),
            note=rec.get("note", ""),
        ))
    return out