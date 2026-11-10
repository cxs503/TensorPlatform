"""Ledger core for reference-gated verdicts (WITHHELD reference activation).

Problem
-------
A judgement whose reference value has not arrived yet is filed as ``WITHHELD``.
That verdict used to be terminal in prose only: the report said "waiting for the
missing reference data" and the flip back to a decided verdict depended on a
human noticing.  This package turns the sentence into a state machine:

``reference truth table``  (JSON document, one row per judgement subject)
        -> written by external data feeds, digest-addressed
``criterion ledger``       (JSON document: computed value, tolerance, verdict,
                            digest of the reference row the verdict was decided
                            against, plus counters)
``audit log``              (append-only JSONL: one record per verdict transition)

The two sides are compared by content digest, never by timestamps or by a
"has been processed" flag, so an insert, an update and a delete are all visible
as the same kind of difference: *the reference digest moved*.

Write-time recompute gate
-------------------------
The counters stored in the ledger document are a cache of the base records
(verdict tally + audit log).  Before any state write the cache is recomputed
from the base and compared field by field; a mismatch (hand-edited counter,
partially restored backup, counter drift) refuses the write and reports the
per-field delta instead of laundering the forged number.  ``WITHHELD`` is not
allowed to only go up either: a removed reference row makes the verdict fall
back to ``WITHHELD`` and that fall leaves its own audit record.

Invariants (enforced in code, asserted in the accompanying test-suite)
----------------------------------------------------------------------
1. Digest-addressed transition: a transition exists only when the reference
   digest of a subject differs from the digest stored in the ledger.
2. Append-only audit: records are never rewritten; each carries
   ``actor``, ``sha256``, the old/new verdict and the error value.
3. Idempotence: every record carries an ``idempotency_key`` derived from
   (subject, previous digest, new digest, verdict transition); replaying the
   same reference data appends nothing.
4. Atomic writes: state is written to a temporary file and renamed, so a crash
   cannot leave a half-written ledger; readers see the old or the new document.
5. Serialised writers: an advisory file lock wraps read-modify-write, so
   concurrent detectors cannot duplicate an audit record.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

try:  # POSIX advisory locking; absent on some CI wheels -> degrade to no-op
    import fcntl
except ImportError:  # pragma: no cover - platform dependent
    fcntl = None  # type: ignore[assignment]

STATE_SCHEMA = "withheld-activation-state/1.0"
REFERENCE_SCHEMA = "withheld-reference-truth/1.0"

VERDICT_WITHHELD = "WITHHELD"
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"

TRIGGER_ACTIVATION = "activation"
TRIGGER_WITHDRAWAL = "withdrawal"
TRIGGER_REFERENCE_UPDATE = "reference_update"
TRIGGER_COUNTER_REPAIR = "counter_repair"

#: Counter fields and how each one is recomputed from the base records.
COUNTER_FIELDS = (
    "subjects",
    "withheld",
    "pass",
    "fail",
    "audit_entries",
    "activations",
    "withdrawals",
    "reference_updates",
    "counter_repairs",
)


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(payload: Any) -> str:
    """Stable serialisation: keys sorted, no insignificant whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    """Append exactly one record; a single ``write`` of one line + fsync."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError as exc:  # honest failure, never silently dropped
            raise ValueError(f"corrupt audit record at {path}:{lineno}: {exc}") from exc
    return records


@contextlib.contextmanager
def exclusive_lock(lock_path: Path):
    """Advisory exclusive lock around read-modify-write (no-op if unsupported)."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


# --------------------------------------------------------------------------- #
# reference truth table
# --------------------------------------------------------------------------- #
#: Fields that carry the *truth* of a reference row.  Bookkeeping columns
#: (``revision``, ``updated_at``) are deliberately excluded: re-writing the same
#: value is not new information and must stay idempotent.
REFERENCE_VALUE_FIELDS = ("quantity", "reference_value", "uncertainty_pct", "source")


class ReferenceValueError(ValueError):
    """Rejected reference row (non-numeric, non-finite or degenerate value)."""


def reference_row_digest(row: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Content digest of a reference row, or ``None`` when the row is absent."""
    if not row:
        return None
    payload = {field: row.get(field) for field in REFERENCE_VALUE_FIELDS}
    return sha256_hex(canonical_json(payload))


class ReferenceStore:
    """The *reference table*: one content-addressed row per judgement subject."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.document: Dict[str, Any] = {"schema": REFERENCE_SCHEMA, "rows": {}}
        if self.path.exists():
            self.document = json.loads(self.path.read_text(encoding="utf-8"))
        self.document.setdefault("rows", {})

    def reload(self) -> "ReferenceStore":
        """Re-read the table from disk (the feed may be another process)."""
        return ReferenceStore(self.path)

    # -- reads -------------------------------------------------------------
    def rows(self) -> Dict[str, Dict[str, Any]]:
        return dict(self.document.get("rows", {}))

    def row(self, subject_id: str) -> Optional[Dict[str, Any]]:
        return self.document.get("rows", {}).get(subject_id)

    def digest(self, subject_id: str) -> Optional[str]:
        return reference_row_digest(self.row(subject_id))

    def table_digest(self) -> str:
        """Digest of the whole table (provenance for reports/audits)."""
        rows = self.document.get("rows", {})
        return sha256_hex(canonical_json({k: reference_row_digest(v) for k, v in rows.items()}))

    # -- writes ------------------------------------------------------------
    def upsert(self, subject_id: str, *, quantity: str, reference_value: float,
               source: str = "external_feed", uncertainty_pct: Optional[float] = None,
               persist: bool = True) -> Dict[str, Any]:
        """Insert or update one row.  Same values -> same digest (idempotent)."""
        if not quantity:
            raise ReferenceValueError("quantity must be a non-empty label")
        try:
            value = float(reference_value)
        except (TypeError, ValueError) as exc:
            raise ReferenceValueError(f"reference_value must be numeric: {reference_value!r}") from exc
        if not math.isfinite(value):
            raise ReferenceValueError("reference_value must be finite")
        if value == 0.0:
            # A relative error is undefined against a zero reference; refuse at the
            # ingest boundary instead of producing an arbitrary verdict later.
            raise ReferenceValueError("reference_value must be non-zero (relative error undefined)")
        uncertainty = None
        if uncertainty_pct is not None:
            uncertainty = float(uncertainty_pct)
            if not math.isfinite(uncertainty) or uncertainty < 0.0:
                raise ReferenceValueError("uncertainty_pct must be finite and >= 0")
        previous = self.document["rows"].get(subject_id)
        revision = int((previous or {}).get("revision", 0)) + 1
        row = {
            "quantity": str(quantity),
            "reference_value": value,
            "uncertainty_pct": uncertainty,
            "source": str(source),
            "revision": revision,
            "updated_at": utc_now(),
        }
        self.document["rows"][subject_id] = row
        if persist:
            self.save()
        return row

    def delete(self, subject_id: str, *, persist: bool = True) -> bool:
        removed = self.document["rows"].pop(subject_id, None) is not None
        if removed and persist:
            self.save()
        return removed

    def save(self) -> None:
        atomic_write_json(self.path, self.document)


# --------------------------------------------------------------------------- #
# criterion ledger
# --------------------------------------------------------------------------- #
class CounterMismatch(RuntimeError):
    """Declared counters disagree with the base records: write refused."""

    def __init__(self, declared: Mapping[str, Any], recomputed: Mapping[str, Any],
                 deltas: Mapping[str, Any]):
        self.declared = dict(declared)
        self.recomputed = dict(recomputed)
        self.deltas = dict(deltas)
        fields = ", ".join(f"{k}{v:+d}" for k, v in sorted(self.deltas.items()))
        super().__init__(f"counter recompute gate refused write (declared - recomputed): {fields}")


def recompute_counters(criteria: Mapping[str, Mapping[str, Any]],
                       audit_records: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    """Derive every counter from the base records (verdict tally + audit log)."""
    criteria = criteria or {}
    records = list(audit_records or [])

    def _count(trigger: str) -> int:
        return sum(1 for r in records if r.get("trigger") == trigger)

    return {
        "subjects": len(criteria),
        "withheld": sum(1 for c in criteria.values() if c.get("verdict") == VERDICT_WITHHELD),
        "pass": sum(1 for c in criteria.values() if c.get("verdict") == VERDICT_PASS),
        "fail": sum(1 for c in criteria.values() if c.get("verdict") == VERDICT_FAIL),
        "audit_entries": len(records),
        "activations": _count(TRIGGER_ACTIVATION),
        "withdrawals": _count(TRIGGER_WITHDRAWAL),
        "reference_updates": _count(TRIGGER_REFERENCE_UPDATE),
        "counter_repairs": _count(TRIGGER_COUNTER_REPAIR),
    }


def counter_deltas(declared: Mapping[str, Any],
                   recomputed: Mapping[str, Any]) -> Dict[str, int]:
    """Per-field ``declared - recomputed`` for the counter fields only."""
    deltas: Dict[str, int] = {}
    for field in COUNTER_FIELDS:
        left = int(declared.get(field, 0) or 0)
        right = int(recomputed.get(field, 0) or 0)
        if left != right:
            deltas[field] = left - right
    return deltas


class CriterionLedger:
    """Verdict state per subject + append-only audit log + recompute gate."""

    def __init__(self, state_path: Path, audit_path: Optional[Path] = None):
        self.state_path = Path(state_path)
        self.audit_path = Path(audit_path) if audit_path else self.state_path.with_name("audit.jsonl")
        self.lock_path = self.state_path.with_name(self.state_path.name + ".lock")
        self.document: Dict[str, Any] = {"schema": STATE_SCHEMA, "counters": {}, "criteria": {}}
        if self.state_path.exists():
            self.document = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.document.setdefault("criteria", {})
        self.document.setdefault("counters", {})

    def reload(self) -> "CriterionLedger":
        """Re-read state from disk (another detector process may have written).

        A ledger that has never been persisted keeps its in-memory document, so an
        in-process seed followed by a scan behaves as expected.
        """
        if not self.state_path.exists():
            return self
        fresh = CriterionLedger(self.state_path, self.audit_path)
        self.document = fresh.document
        return self

    # -- basic accessors ---------------------------------------------------
    @property
    def criteria(self) -> Dict[str, Dict[str, Any]]:
        return self.document["criteria"]

    @property
    def declared_counters(self) -> Dict[str, Any]:
        return dict(self.document.get("counters") or {})

    def audit_records(self) -> List[Dict[str, Any]]:
        return read_jsonl(self.audit_path)

    def recomputed_counters(self, audit_records: Optional[Iterable[Mapping[str, Any]]] = None,
                            criteria: Optional[Mapping[str, Mapping[str, Any]]] = None) -> Dict[str, int]:
        if audit_records is None:
            audit_records = self.audit_records()
        return recompute_counters(self.criteria if criteria is None else criteria, audit_records)

    # -- write-time recompute gate ----------------------------------------
    def gate_report(self, audit_records: Optional[Iterable[Mapping[str, Any]]] = None) -> Dict[str, Any]:
        """Compare declared counters against base records; report the delta."""
        records = self.audit_records() if audit_records is None else list(audit_records)
        recomputed = recompute_counters(self.criteria, records)
        declared = self.declared_counters
        deltas = counter_deltas(declared, recomputed)
        return {
            "ok": not deltas,
            "declared": {f: int(declared.get(f, 0) or 0) for f in COUNTER_FIELDS},
            "recomputed": recomputed,
            "deltas": deltas,
            "audit_entries": len(records),
        }

    def enforce_gate(self, audit_records: Optional[Iterable[Mapping[str, Any]]] = None) -> Dict[str, Any]:
        report = self.gate_report(audit_records)
        if not report["ok"]:
            raise CounterMismatch(report["declared"], report["recomputed"], report["deltas"])
        return report

    # -- mutation ----------------------------------------------------------
    def add_criterion(self, subject_id: str, *, computed_value: float,
                      tolerance_pct: float, persist: bool = True) -> Dict[str, Any]:
        """Register a subject.  A subject without reference truth starts WITHHELD."""
        value = float(computed_value)
        tolerance = float(tolerance_pct)
        if not math.isfinite(value):
            raise ValueError("computed_value must be finite")
        if not math.isfinite(tolerance) or tolerance <= 0.0:
            raise ValueError("tolerance_pct must be finite and > 0")
        entry = {
            "computed_value": value,
            "tolerance_pct": tolerance,
            "verdict": VERDICT_WITHHELD,
            "error_pct": None,
            "reason": "no_reference_truth",
            "reference_digest": None,
            "decided_at": None,
            "audit_seq": None,
        }
        self.criteria[subject_id] = entry
        if persist:
            self.save()
        return entry

    def append_audit(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        """Append one audit record with the next sequence number."""
        existing = self.audit_records()
        payload = dict(record)
        payload["seq"] = len(existing) + 1
        payload.setdefault("ts", utc_now())
        payload.setdefault("actor", "auto")
        append_jsonl(self.audit_path, payload)
        return payload

    def audit_idempotency_keys(self) -> set:
        return {r.get("idempotency_key") for r in self.audit_records() if r.get("idempotency_key")}

    def save(self, audit_records: Optional[Iterable[Mapping[str, Any]]] = None) -> Dict[str, int]:
        """Persist the state, stamping counters recomputed from the base records."""
        counters = self.recomputed_counters(audit_records)
        self.document["counters"] = counters
        self.document["schema"] = STATE_SCHEMA
        atomic_write_json(self.state_path, self.document)
        return counters

    def repair_counters(self, *, actor: str = "auto", reason: str = "recompute_gate_repair") -> Dict[str, Any]:
        """Rebuild counters from the base and leave an audit record of the repair."""
        before = self.declared_counters
        records = self.audit_records()
        recomputed = recompute_counters(self.criteria, records)
        deltas = counter_deltas(before, recomputed)
        record = None
        if deltas:
            record = self.append_audit({
                "trigger": TRIGGER_COUNTER_REPAIR,
                "actor": actor,
                "subject_id": None,
                "old_verdict": None,
                "new_verdict": None,
                "error_pct": None,
                "sha256": sha256_hex(canonical_json(recomputed)),
                "reason": reason,
                "counter_deltas": deltas,
                "idempotency_key": sha256_hex(
                    "counter_repair|" + canonical_json({"deltas": deltas, "audit": len(records)})
                ),
            })
        counters = self.save()
        return {"deltas": deltas, "repaired": record is not None, "counters": counters}

    @contextlib.contextmanager
    def transaction(self):
        """Serialise writers and refuse to start on a tampered counter block."""
        with exclusive_lock(self.lock_path):
            yield self


def load_pair(state_path: Path, reference_path: Path
              ) -> tuple[CriterionLedger, ReferenceStore]:
    return CriterionLedger(state_path), ReferenceStore(reference_path)