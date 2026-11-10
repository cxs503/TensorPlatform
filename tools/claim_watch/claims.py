#!/usr/bin/env python3
"""Claim records: schema validation, URIs and the read-only resolver.

The ledger is a list of records described by ``schemas/claim.json``. This module
owns the three things the checker is not allowed to improvise:

* **validation** — a malformed record must be reported as INVALID, never silently
  graded (a claim whose ``artifact_sha256`` is missing is not a weak claim, it is
  not a claim at all);
* **URI grammar** — ``<repo>:<path>``, ``file:///abs`` and worktree-relative
  paths resolve through a ``RepoResolver`` built from an explicit root map, so a
  batch never guesses which checkout a path belongs to;
* **read-only access** — every artifact read goes through ``read_bytes`` which
  opens the file ``rb`` and never writes, creates or locks anything.

Zero domain vocabulary and no imports of the audited code: claims are data.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = "claim/1.0"
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "schemas", "claim.json")

_SHA = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED = ("schema_version", "claim_id", "value", "artifact_uri", "artifact_sha256",
             "verified_at", "evidence_grade")
_ALLOWED = set(_REQUIRED) | {"unit", "env_fingerprint", "depends_on", "pointer", "producer", "notes"}
_GRADES = ("REAL", "MOCK", "STUB", "WITHHELD")
_FP_REQUIRED = ("arch", "platform", "byteorder", "pointer_bits", "python", "digest")


class ClaimError(ValueError):
    """Raised when a record cannot be treated as a claim at all."""


# --------------------------------------------------------------------------- #
# hashing / reading (read-only by construction)
# --------------------------------------------------------------------------- #
def sha256_file(path: str) -> Optional[str]:
    """sha256 of a file's bytes, or None when unreadable/absent. Never writes."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, ValueError):
        return None


def read_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# URI resolution
# --------------------------------------------------------------------------- #
def split_uri(uri: str) -> Tuple[Optional[str], str]:
    """Split ``<repo>:<path>`` into (repo, path); anything else -> (None, uri)."""
    if not uri:
        return None, uri
    if uri.startswith("file://"):
        return None, uri[len("file://"):]
    head, sep, tail = uri.partition(":")
    # A single-letter head is a Windows drive letter, not a repo token.
    if sep and tail and len(head) > 1 and "/" not in head and "\\" not in head:
        return head, tail
    return None, uri


@dataclass
class RepoResolver:
    """Maps claim URIs to filesystem paths under an explicit root map.

    ``roots`` keys are repo tokens used in URIs; the value is the checkout root.
    A URI without a repo token is tried against every root in insertion order and
    then as given (absolute paths win). Nothing outside ``roots`` is ever probed
    recursively: resolution is a bounded lookup, not a search.
    """

    roots: Dict[str, str] = field(default_factory=dict)
    allow_absolute: bool = True

    def __post_init__(self) -> None:
        self.roots = {k: os.path.abspath(v) for k, v in dict(self.roots).items()}

    def repo_of(self, uri: str) -> Optional[str]:
        repo, _ = split_uri(uri)
        return repo

    def resolve(self, uri: str) -> Optional[str]:
        """Absolute path or None. Pure lookup; never creates anything."""
        if not uri:
            return None
        repo, tail = split_uri(uri)
        if repo:
            root = self.roots.get(repo)
            if root is None:
                return None
            cand = os.path.normpath(os.path.join(root, tail))
            return cand if os.path.exists(cand) else None
        if tail.startswith("/"):
            if not self.allow_absolute:
                return None
            return tail if os.path.exists(tail) else None
        for root in self.roots.values():
            cand = os.path.normpath(os.path.join(root, tail))
            if os.path.exists(cand):
                return cand
        return None

    def qualifying_root(self, uri: str) -> Optional[str]:
        """Name of the root that made ``uri`` resolvable (for audits)."""
        repo, tail = split_uri(uri)
        if repo:
            return repo if self.resolve(uri) else None
        if tail.startswith("/"):
            return "abs" if os.path.exists(tail) else None
        for name, root in self.roots.items():
            if os.path.exists(os.path.normpath(os.path.join(root, tail))):
                return name
        return None


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _validate_structurally(claim: Any) -> List[str]:
    """Dependency-free validation used when jsonschema is unavailable."""
    errs: List[str] = []
    if not isinstance(claim, dict):
        return ["claim must be an object"]
    missing = [k for k in _REQUIRED if k not in claim]
    if missing:
        errs.append("missing required: " + ",".join(missing))
    extra = sorted(set(claim) - _ALLOWED)
    if extra:
        errs.append("unexpected keys: " + ",".join(extra))
    if claim.get("schema_version") not in (SCHEMA_VERSION, None):
        errs.append(f"schema_version must be {SCHEMA_VERSION}")
    if "artifact_sha256" in claim and not _SHA.match(str(claim["artifact_sha256"] or "")):
        errs.append("artifact_sha256 must be 64 lowercase hex chars")
    if claim.get("evidence_grade") not in _GRADES:
        errs.append("evidence_grade must be one of " + ",".join(_GRADES))
    if not isinstance(claim.get("value"), (int, float, str)) or isinstance(claim.get("value"), bool):
        errs.append("value must be a number or a string")
    fp = claim.get("env_fingerprint")
    if fp is not None:
        if not isinstance(fp, dict):
            errs.append("env_fingerprint must be an object or null")
        else:
            for k in _FP_REQUIRED:
                if k not in fp:
                    errs.append(f"env_fingerprint missing {k}")
    deps = claim.get("depends_on") or []
    if not isinstance(deps, list):
        errs.append("depends_on must be an array")
    else:
        for i, dep in enumerate(deps):
            if not isinstance(dep, dict):
                errs.append(f"depends_on[{i}] must be an object")
                continue
            if "uri" not in dep or "kind" not in dep:
                errs.append(f"depends_on[{i}] needs uri and kind")
            if dep.get("kind") not in ("artifact", "declaration", None):
                errs.append(f"depends_on[{i}].kind must be artifact|declaration")
            if dep.get("sha256") is not None and not _SHA.match(str(dep.get("sha256"))):
                errs.append(f"depends_on[{i}].sha256 must be null or 64 hex chars")
    if not str(claim.get("claim_id", "")).strip():
        errs.append("claim_id must be non-empty")
    if not str(claim.get("artifact_uri", "")).strip():
        errs.append("artifact_uri must be non-empty")
    return errs


_SCHEMA_CACHE: Dict[str, Any] = {}


def load_schema(path: Optional[str] = None) -> Optional[dict]:
    """Load the claim schema once per (path, mtime, size).

    Returning a stable object matters: the compiled-validator cache is keyed by
    object identity, and re-reading the file on every one of 835 claims would both
    re-parse it and defeat that cache.
    """
    p = os.path.abspath(path or SCHEMA_PATH)
    try:
        st = os.stat(p)
    except OSError:
        return None
    key = f"{p}:{st.st_mtime_ns}:{st.st_size}"
    cached = _SCHEMA_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        with open(p, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
    except (OSError, ValueError):
        return None
    _SCHEMA_CACHE.clear()
    _SCHEMA_CACHE[key] = schema
    return schema


_VALIDATOR_CACHE: Dict[Any, Any] = {}


def _inline_refs(node: Any, defs: Mapping[str, Any], depth: int = 0) -> Any:
    """Replace local ``#/$defs/<name>`` references with the definition itself.

    jsonschema resolves references through the ``referencing`` registry on every
    validation call, which dominated the batch profile. All references in
    ``schemas/claim.json`` are local and acyclic, so inlining once gives the same
    accept/reject behaviour at a fraction of the cost. A non-local reference is
    left untouched (and still resolved by jsonschema, i.e. never silently dropped).
    """
    if depth > 8:
        return node
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref[len("#/$defs/"):]
            target = defs.get(name)
            if isinstance(target, dict):
                merged = {k: v for k, v in node.items() if k != "$ref"}
                resolved = _inline_refs(target, defs, depth + 1)
                if merged and isinstance(resolved, dict):
                    resolved = {**resolved, **merged}
                return resolved
        return {k: _inline_refs(v, defs, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(v, defs, depth + 1) for v in node]
    return node


def _compiled_validator(schema: dict) -> Any:
    """Compiled jsonschema validator, built once per schema object.

    ``jsonschema.validate`` re-checks the *schema* on every call; over an 835-claim
    batch that is 70 s of pure meta-validation. Compiling once and iterating errors
    keeps the same semantics at ~1/50 the cost. The cache is keyed by object identity
    plus the schema's ``$id``, and callers must not mutate a schema in place (they
    do not: schemas are read from disk or built once per test).
    """
    key = (id(schema), schema.get("$id") if isinstance(schema, dict) else None)
    validator = _VALIDATOR_CACHE.get(key)
    if validator is None:
        import jsonschema  # type: ignore
        cls = jsonschema.validators.validator_for(schema)
        cls.check_schema(schema)
        inlined = _inline_refs(schema, schema.get("$defs") or {})
        validator = cls(inlined)
        _VALIDATOR_CACHE[key] = validator
    return validator


def validate_claim(claim: Any, schema: Optional[dict] = None) -> List[str]:
    """Return a list of schema violations (empty list == valid)."""
    errs = _validate_structurally(claim)
    if errs:
        return errs
    schema = schema if schema is not None else load_schema()
    if not schema:
        return errs
    try:
        validator = _compiled_validator(schema)
        for error in validator.iter_errors(claim):
            errs.append(str(error.message)[:400])
            if len(errs) >= 8:
                break
    except Exception as exc:  # schema itself broken / jsonschema unavailable
        errs.append(str(exc)[:400])
    return errs


# --------------------------------------------------------------------------- #
# ledger IO
# --------------------------------------------------------------------------- #
def make_claim(
    claim_id: str,
    value: Any,
    artifact_uri: str,
    artifact_sha256: str,
    verified_at: str,
    evidence_grade: str = "REAL",
    unit: Optional[str] = None,
    env_fingerprint: Optional[Mapping[str, Any]] = None,
    depends_on: Optional[Sequence[Mapping[str, Any]]] = None,
    pointer: Optional[str] = None,
    producer: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a schema-valid claim. Ordering is fixed so ledgers diff cleanly."""
    claim: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "claim_id": claim_id,
        "value": value,
        "unit": unit,
        "artifact_uri": artifact_uri,
        "artifact_sha256": artifact_sha256,
        "env_fingerprint": dict(env_fingerprint) if env_fingerprint is not None else None,
        "depends_on": [dict(d) for d in (depends_on or [])],
        "verified_at": verified_at,
        "evidence_grade": evidence_grade,
        "pointer": pointer,
        "producer": producer,
        "notes": notes,
    }
    return claim


def load_ledger(path: str) -> List[Dict[str, Any]]:
    """Read a ledger: either a JSON list or {"claims": [...]}."""
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if isinstance(doc, dict):
        doc = doc.get("claims", [])
    if not isinstance(doc, list):
        raise ClaimError(f"ledger {path} is not a list of claims")
    return doc


def dump_ledger(claims: Iterable[Mapping[str, Any]], path: str) -> int:
    """Write a ledger (the only write this module performs; caller-chosen path)."""
    items = [dict(c) for c in claims]
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"schema_version": "claim-ledger/1.0", "n_claims": len(items), "claims": items},
                  fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    return len(items)