#!/usr/bin/env python3
"""Report number-level provenance sidecar (P251-W2-N04).

Why this exists
---------------
The chronicle line (``tools/chronicle/provenance/tokens.py``) already proves
that every digit in a manuscript is *extracted* and that each extracted token
is bound to an adjacent ``{{prov:pid}}`` record.  It cannot prove the other
direction: whether the record itself is re-computable.  A record is only a
claim until it carries (a) a fingerprint of the query that produced it and
(b) a timestamp.  This module defines that record -- the **sidecar**.

Contract
--------
A report template contains no result numbers at all, only placeholders::

    coverage reached {{prov:metric-a}} % of pixel rows {{prov:metric-b}}.

The sidecar holds one record per key::

    {"key": "metric-a", "kind": "number", "value": 85.71, "decimals": 1,
     "query_hash": "sha256:...", "computed_at": "2026-11-10T09:00:00Z",
     "source": {"type": "sql", "sql": "SELECT ...", "params": [], "db": "s.sqlite"}}

    {"key": "fig-001", "kind": "artifact", "artifact_sha256": "<64 hex>",
     "computed_at": "...", "source": {"type": "artifact", "path": "figs/a.png"}}

* ``query_hash`` -- SHA-256 of the canonicalised ``(sql, params)`` pair.  It
  pins the *identity of the query*; the verifier recomputes it from
  ``source`` so a record whose SQL was re-pointed after fingerprinting is
  detectable even when the stored value still looks plausible.
* ``artifact_sha256`` -- SHA-256 of the artifact bytes, for figures/tables.
* ``computed_at`` -- when the value was produced (only ordering evidence; it
  is not a signature).

Declared limits (honest scope)
------------------------------
* A sidecar is tamper *evidence*, not tamper *proof*: no signing key lives in
  the worktree, so a determined forger who rewrites value, query_hash **and**
  computed_at consistently and also re-runs the query to match is not caught
  by this module.  What it does catch is every *inconsistent* edit -- the
  common failure mode (a number changed in the text or in the record but not
  in the data).
* ``query_hash`` is a fingerprint, not a proof of correctness: it says "this
  value came from this query", never "this query is right".
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = "report-prov-sidecar/1"
PLACEHOLDER_FORMAT = "{{prov:%s}}"
PLACEHOLDER_RE = re.compile(r"\{\{prov:([A-Za-z0-9_.:-]+)\}\}")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

NUMBER_KIND = "number"
ARTIFACT_KIND = "artifact"
KINDS = (NUMBER_KIND, ARTIFACT_KIND)


# ---------------------------------------------------------------------------
# hashing helpers
# ---------------------------------------------------------------------------
def canonical_query(sql: str, params: Sequence[Any] = ()) -> str:
    """Canonical serialisation of a query identity: whitespace-collapsed SQL
    (trailing ``;`` dropped) plus the JSON encoding of its parameters.  Two
    queries differing only in formatting hash identically; a changed literal
    or a changed parameter list does not."""
    norm = " ".join(str(sql).split()).rstrip(";")
    return json.dumps(
        {"sql": norm, "params": list(params)},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def query_hash(sql: str, params: Sequence[Any] = ()) -> str:
    """Fingerprint of a query identity, ``sha256:<hex>``."""
    return "sha256:" + hashlib.sha256(canonical_query(sql, params).encode("utf-8")).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_ok(value: str) -> bool:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------
@dataclass
class NumberRecord:
    key: str
    value: float
    query_hash: str
    source: dict
    computed_at: str
    kind: str = NUMBER_KIND
    decimals: int | None = None
    unit: str = ""
    label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ArtifactRecord:
    key: str
    artifact_sha256: str
    source: dict
    computed_at: str
    kind: str = ARTIFACT_KIND
    label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def render_number_text(rec: NumberRecord) -> str:
    """The one sanctioned rendering of a recorded number: the sidecar decides
    the decimals, so the same record always renders byte-identically."""
    value = float(rec.value)
    if rec.decimals is None:
        return f"{value:g}"
    return f"{value:.{int(rec.decimals)}f}"


def render_artifact_text(rec: ArtifactRecord) -> str:
    return rec.artifact_sha256


def render_record_text(rec: NumberRecord | ArtifactRecord) -> str:
    return render_number_text(rec) if rec.kind == NUMBER_KIND else render_artifact_text(rec)


def rounding_budget(rec: NumberRecord) -> float:
    """Half a unit in the last printed digit: a correctly rounded display is
    inside budget, a changed digit is not."""
    if rec.decimals is None:
        return max(abs(float(rec.value)) * 1e-9, 1e-12)
    return 0.5 * 10 ** (-int(rec.decimals)) + 1e-12


# ---------------------------------------------------------------------------
# sidecar
# ---------------------------------------------------------------------------
class Sidecar:
    """A set of provenance records keyed by placeholder key."""

    def __init__(self, records: Iterable[NumberRecord | ArtifactRecord] = ()) -> None:
        self.records: dict[str, NumberRecord | ArtifactRecord] = {}
        for rec in records:
            self.records[rec.key] = rec

    # -- creation ----------------------------------------------------------
    def add_number(
        self,
        key: str,
        value: float,
        sql: str,
        params: Sequence[Any] = (),
        decimals: int | None = None,
        db: str = "snapshot.sqlite",
        computed_at: str | None = None,
        unit: str = "",
        label: str = "",
    ) -> NumberRecord:
        if key in self.records:
            raise ValueError(f"duplicate sidecar key: {key}")
        rec = NumberRecord(
            key=key,
            value=float(value),
            query_hash=query_hash(sql, params),
            source={"type": "sql", "sql": " ".join(str(sql).split()).rstrip(";"),
                    "params": list(params), "db": db},
            computed_at=computed_at or utc_now(),
            decimals=None if decimals is None else int(decimals),
            unit=unit,
            label=label,
        )
        self.records[key] = rec
        return rec

    def add_artifact(
        self,
        key: str,
        path: str | Path,
        artifact_root: str | Path | None = None,
        computed_at: str | None = None,
        label: str = "",
    ) -> ArtifactRecord:
        if key in self.records:
            raise ValueError(f"duplicate sidecar key: {key}")
        p = Path(path)
        full = p if p.is_absolute() or artifact_root is None else Path(artifact_root) / p
        if not full.exists():
            raise FileNotFoundError(f"artifact for {key} not found: {full}")
        rec = ArtifactRecord(
            key=key,
            artifact_sha256=sha256_file(full),
            source={"type": "artifact", "path": str(p)},
            computed_at=computed_at or utc_now(),
            label=label,
        )
        self.records[key] = rec
        return rec

    # -- access ------------------------------------------------------------
    def get(self, key: str) -> NumberRecord | ArtifactRecord | None:
        return self.records.get(key)

    def __contains__(self, key: object) -> bool:
        return key in self.records

    def __len__(self) -> int:
        return len(self.records)

    def number_records(self) -> list[NumberRecord]:
        return [r for r in self.records.values() if r.kind == NUMBER_KIND]

    def artifact_records(self) -> list[ArtifactRecord]:
        return [r for r in self.records.values() if r.kind == ARTIFACT_KIND]

    def missing_keys(self, keys: Iterable[str]) -> list[str]:
        return [k for k in dict.fromkeys(keys) if k not in self.records]

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "placeholder_format": PLACEHOLDER_FORMAT,
            "record_count": len(self.records),
            "records": [r.to_dict() for r in self.records.values()],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Sidecar":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        records: list[NumberRecord | ArtifactRecord] = []
        for raw in payload.get("records", []):
            kind = raw.get("kind", NUMBER_KIND)
            if kind == NUMBER_KIND:
                records.append(NumberRecord(
                    key=raw["key"], value=float(raw["value"]), query_hash=raw["query_hash"],
                    source=dict(raw["source"]), computed_at=raw["computed_at"], decimals=raw.get("decimals"),
                    unit=raw.get("unit", ""), label=raw.get("label", ""),
                ))
            elif kind == ARTIFACT_KIND:
                records.append(ArtifactRecord(
                    key=raw["key"], artifact_sha256=raw["artifact_sha256"],
                    source=dict(raw["source"]), computed_at=raw["computed_at"],
                    label=raw.get("label", ""),
                ))
            else:
                raise ValueError(f"unknown record kind {kind!r} for key {raw.get('key')!r}")
        return cls(records)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def validate_sidecar(sidecar: Sidecar, strict_hash: bool = True) -> list[dict]:
    """Schema + internal-consistency findings.  ``strict_hash`` recomputes
    ``query_hash`` from ``source`` (the negative control for a re-pointed
    record); pass False to validate the shape only."""
    out: list[dict] = []
    for rec in sidecar.records.values():
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", rec.key or ""):
            out.append({"code": "SIDECAR_SCHEMA_INVALID", "key": rec.key,
                        "detail": f"key {rec.key!r} is not a legal placeholder key"})
        if not _iso_ok(rec.computed_at):
            out.append({"code": "SIDECAR_SCHEMA_INVALID", "key": rec.key,
                        "detail": f"computed_at {rec.computed_at!r} is not ISO-8601"})
        src = rec.source or {}
        stype = src.get("type")
        if stype not in ("sql", "artifact"):
            out.append({"code": "SIDECAR_SCHEMA_INVALID", "key": rec.key,
                        "detail": f"source.type {stype!r} must be 'sql' or 'artifact'"})
            continue
        if rec.kind == NUMBER_KIND:
            assert isinstance(rec, NumberRecord)
            if not isinstance(src.get("sql"), str) or not src["sql"].strip():
                out.append({"code": "SIDECAR_SCHEMA_INVALID", "key": rec.key,
                            "detail": "number record has no source.sql"})
            if not isinstance(src.get("params", []), list):
                out.append({"code": "SIDECAR_SCHEMA_INVALID", "key": rec.key,
                            "detail": "source.params must be a list"})
            if rec.decimals is not None and int(rec.decimals) < 0:
                out.append({"code": "SIDECAR_SCHEMA_INVALID", "key": rec.key,
                            "detail": "decimals must be >= 0"})
            if strict_hash and isinstance(src.get("sql"), str):
                expect = query_hash(src["sql"], src.get("params") or [])
                if rec.query_hash != expect:
                    out.append({
                        "code": "QUERY_HASH_MISMATCH", "key": rec.key,
                        "detail": f"recorded {str(rec.query_hash)[:19]}... != recomputed "
                                  f"{expect[:19]}... from source (query re-pointed after fingerprinting)",
                    })
        else:
            assert isinstance(rec, ArtifactRecord)
            if not HEX64_RE.match(str(rec.artifact_sha256)):
                out.append({"code": "SIDECAR_SCHEMA_INVALID", "key": rec.key,
                            "detail": "artifact_sha256 must be 64 lowercase hex chars"})
            if not isinstance(src.get("path"), str) or not src["path"]:
                out.append({"code": "SIDECAR_SCHEMA_INVALID", "key": rec.key,
                            "detail": "artifact record has no source.path"})
    return out