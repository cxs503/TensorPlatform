"""Append-only, hash-chained upgrade audit trail.

Every level change of a capability writes exactly one record here — upgrades and
rollbacks alike — carrying the evidence snapshot *before* and *after*, the rule
that was applied, its digest, the resulting completeness, the operator
(``auto`` / a human id) and the reason.  Records are chained: each record stores
the digest of its predecessor, so editing or deleting a line in the middle of the
trail is detectable by :meth:`UpgradeAudit.verify` — an audit trail an author can
rewrite proves nothing.

Record shape (one JSON object per line)::

    {"audit_id": "au-<16 hex>",
     "seq": 1,
     "capability_slug": "synth.alpha",
     "direction": "upgrade" | "rollback" | "register" | "defect" | "hold",
     "from_level": "MOCK", "to_level": "WITHHELD",
     "rule_id": "r.mock->withheld", "rule_digest": "<64 hex>",
     "rules_digest": "<64 hex>",
     "evidence_snapshot": {"before": {...}, "after": {...}},
     "completeness": {"before": 0.5, "after": 1.0},
     "missing_before": ["min_anchors"],
     "reason": "...", "operator": "auto",
     "batch_id": "b-<12 hex>",
     "timestamp": "2026-...",
     "prev_digest": "...", "record_digest": "<64 hex>"}

``audit_id`` is derived from the record content (not a clock or a counter), so
re-running a scenario reproduces the same ids; ``seq`` and ``prev_digest`` carry
the ordering.  The trail is written with one ``write`` per record on an opened
append handle, which keeps a partially written run readable up to the last
complete line.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .model import canonical_json, digest

AUDIT_SCHEMA = "capability-upgrade-audit/1.0"
DIRECTIONS = ("upgrade", "rollback", "register", "defect", "hold")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_digest(record: Mapping[str, Any]) -> str:
    """Digest of a record with its own ``record_digest`` field removed."""
    body = {k: v for k, v in record.items() if k != "record_digest"}
    return digest(body)


class UpgradeAudit:
    """Hash-chained JSONL audit trail (in-memory when ``path`` is ``None``)."""

    def __init__(self, path: Optional[Path | str] = None):
        self.path = Path(path) if path is not None else None
        self._records: List[Dict[str, Any]] = []
        if self.path is not None and self.path.exists():
            self._records = self._read_all()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- persistence -------------------------------------------------------
    def _read_all(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        assert self.path is not None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
        return out

    @property
    def last_digest(self) -> str:
        return self._records[-1]["record_digest"] if self._records else ""

    # -- append ------------------------------------------------------------
    def append(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        """Append one record, filling seq / timestamp / ids / chain links."""
        direction = str(record.get("direction", "upgrade"))
        if direction not in DIRECTIONS:
            raise ValueError(f"unknown audit direction {direction!r}; allowed={DIRECTIONS}")
        body: Dict[str, Any] = dict(record)
        body.pop("record_digest", None)
        body.pop("audit_id", None)
        body["direction"] = direction
        body["seq"] = len(self._records) + 1
        body.setdefault("timestamp", _now())
        content = digest(body)
        body["audit_id"] = f"au-{content[:16]}"
        body["schema"] = AUDIT_SCHEMA
        body["prev_digest"] = self.last_digest
        body["record_digest"] = record_digest(body)
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(canonical_json(body) + "\n")
        self._records.append(body)
        return body

    # -- query -------------------------------------------------------------
    def records(self, capability_slug: Optional[str] = None,
                direction: Optional[str] = None) -> List[Dict[str, Any]]:
        out = self._records
        if capability_slug is not None:
            out = [r for r in out if r.get("capability_slug") == capability_slug]
        if direction is not None:
            out = [r for r in out if r.get("direction") == direction]
        return list(out)

    def transitions(self, capability_slug: Optional[str] = None) -> List[Dict[str, Any]]:
        """Records that actually moved a level (upgrade or rollback)."""
        return [r for r in self.records(capability_slug)
                if r.get("direction") in ("upgrade", "rollback")
                and r.get("from_level") != r.get("to_level")]

    def __len__(self) -> int:
        return len(self._records)

    # -- integrity ---------------------------------------------------------
    def verify(self) -> Dict[str, Any]:
        """Recompute every digest and the chain; report the first broken index."""
        problems: List[Dict[str, Any]] = []
        prev = ""
        for idx, rec in enumerate(self._records):
            if rec.get("seq") != idx + 1:
                problems.append({"index": idx, "problem": "seq_mismatch",
                                 "expected": idx + 1, "observed": rec.get("seq")})
            if rec.get("prev_digest") != prev:
                problems.append({"index": idx, "problem": "prev_digest_mismatch"})
            if record_digest(rec) != rec.get("record_digest"):
                problems.append({"index": idx, "problem": "record_digest_mismatch"})
            prev = str(rec.get("record_digest", ""))
        return {
            "valid": not problems,
            "n_records": len(self._records),
            "first_problem_index": problems[0]["index"] if problems else None,
            "problems": problems[:10],
            "head_digest": self.last_digest,
        }


def write_snapshot(path: Path | str, payload: Mapping[str, Any]) -> Path:
    """Atomically write a JSON snapshot (state or summary) next to the trail."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


__all__ = ["AUDIT_SCHEMA", "DIRECTIONS", "UpgradeAudit", "record_digest", "write_snapshot"]