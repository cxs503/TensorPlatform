"""Reproducible evidence envelope for the whitespace radar (schema ``radar/v1``).

Why a custom envelope instead of json.dump(payload)
--------------------------------------------------
The acceptance criterion is "same input, rerun -> byte identical evidence file,
identical sha256".  That requires removing every source of nondeterminism:

* **no wall clock** — the ``as_of`` field is derived from the *content digest of
  the pool* (``pool_digest``), not from ``datetime.now()``;
* **sorted keys + fixed separators + fixed indent**, UTF-8, ``ensure_ascii=False``;
* **floats rounded to 6 decimals** at construction time (see ``round_floats``);
* **lists ordered by explicit keys** (doc ids, term strings), never by dict
  insertion order inherited from a filesystem scan.

The digest is computed over the *payload without the digest field*, so it is a
self-verifying envelope: ``rederive_digest(doc) == doc['digest']['sha256']``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

SCHEMA = "radar/v1"
SUPPORTED_SCHEMAS = (SCHEMA, "radar-gate/v1", "radar-query/v1", "radar-bench/v1", "radar-pool/v1", "radar-modules/v1", "radar-campaign/v1")
FLOAT_NDIGITS = 6


def canonical_bytes(obj: Any) -> bytes:
    """Deterministic JSON encoding used for every hashable payload."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _round(obj: Any, nd: int = FLOAT_NDIGITS) -> Any:
    if isinstance(obj, float):
        # ``-0.0`` and ``0.0`` must not produce two different digests.
        return round(obj, nd) + 0.0
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round(v, nd) for v in obj]
    return obj


def round_floats(obj: Any, nd: int = FLOAT_NDIGITS) -> Any:
    """Public alias: canonical numeric normalisation of an evidence payload."""
    return _round(obj, nd)


def seal(payload: Dict[str, Any], *, schema: str = SCHEMA) -> Dict[str, Any]:
    """Attach schema version + content digest to ``payload`` (returns new dict).

    The input mapping is not mutated; the returned envelope is JSON-serialisable
    and byte-stable.
    """
    if schema not in SUPPORTED_SCHEMAS:
        raise ValueError(f"unsupported schema {schema!r}")
    body = _round(dict(payload))
    body.pop("digest", None)
    body["schema"] = schema
    digest = sha256_hex(canonical_bytes(body))
    out = dict(body)
    out["digest"] = {
        "alg": "sha256",
        "scope": "canonical_json_without_digest_field",
        "sha256": digest,
    }
    return out


def dumps(envelope: Dict[str, Any]) -> str:
    """Stable textual form written by the CLI (indent=2 + trailing newline)."""
    return json.dumps(envelope, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def verify(envelope: Dict[str, Any]) -> bool:
    """Recompute the digest of an envelope produced by :func:`seal`."""
    got = (envelope.get("digest") or {}).get("sha256")
    body = {k: v for k, v in envelope.items() if k != "digest"}
    return got == sha256_hex(canonical_bytes(_round(body)))


def check_compatible(envelope: Dict[str, Any]) -> None:
    """Schema compatibility gate (used by the arbitration de-dup bridge).

    Forward compatibility rule: a consumer of ``radar/v1`` must refuse unknown
    *major* versions but may accept extra keys inside a known version.  This is
    the contract tested by ``tests/test_whitespace_radar.py``.
    """
    sch = envelope.get("schema")
    if sch not in SUPPORTED_SCHEMAS:
        raise ValueError(
            f"incompatible schema {sch!r}; this build speaks {SUPPORTED_SCHEMAS}"
        )