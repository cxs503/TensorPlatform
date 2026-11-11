"""Listener over the ``audit_capabilities`` API payload.

The API reports ``{capability_id, level, criterion}`` rows.  The watcher turns
successive payloads into a *diff* (added / level_changed / removed) with the
digest of each snapshot attached, so an upgrade can cite the exact audit
observation that triggered it.  The watcher never writes levels itself — it
feeds the upgrader, which owns the decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .model import EvidenceError, canonical_json, normalise_level, sha256_obj, utc_now


@dataclass(frozen=True)
class AuditRow:
    capability_id: str
    level: str
    criterion: str = ""


@dataclass(frozen=True)
class AuditSnapshot:
    rows: Tuple[AuditRow, ...]
    generated_at: str = ""

    def __post_init__(self) -> None:
        ids = [row.capability_id for row in self.rows]
        if len(set(ids)) != len(ids):
            raise EvidenceError("audit snapshot contains duplicate capability_ids")

    @property
    def levels(self) -> Dict[str, str]:
        return {row.capability_id: row.level for row in self.rows}

    def body(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "rows": [
                {"capability_id": r.capability_id, "level": r.level, "criterion": r.criterion}
                for r in self.rows
            ],
        }

    @property
    def digest(self) -> str:
        return sha256_obj(self.body())

    def as_dict(self) -> Dict[str, Any]:
        payload = self.body()
        payload["digest"] = self.digest
        return payload

    @classmethod
    def from_api_payload(cls, payload: Any) -> "AuditSnapshot":
        """Accept the audit API payload shapes seen in the platform.

        ``{"capabilities": [...]}``, ``{"items": [...]}``, ``{"data": [...]}`` and
        a bare list are all accepted; each row needs ``capability_id`` (or
        ``id``/``slug``) and ``level`` (or ``evidence_level``).
        """
        if isinstance(payload, Mapping):
            generated_at = str(payload.get("generated_at") or payload.get("ts") or "")
            raw = None
            for key in ("capabilities", "items", "data", "rows"):
                if key in payload and isinstance(payload[key], Sequence):
                    raw = payload[key]
                    break
            if raw is None:
                raise EvidenceError("audit payload has no capability list")
        elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
            generated_at, raw = "", payload
        else:
            raise EvidenceError("audit payload must be a mapping or a sequence")

        rows: List[AuditRow] = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise EvidenceError("audit row must be a mapping")
            cap_id = item.get("capability_id") or item.get("id") or item.get("slug")
            level = item.get("level") or item.get("evidence_level")
            if not cap_id or not level:
                raise EvidenceError(f"audit row misses capability_id/level: {item!r}")
            rows.append(
                AuditRow(
                    capability_id=str(cap_id),
                    level=normalise_level(str(level)),
                    criterion=str(item.get("criterion") or item.get("note") or ""),
                )
            )
        return cls(rows=tuple(rows), generated_at=generated_at or utc_now())


class AuditWatcher:
    """Diff successive audit snapshots; keeps only the previous snapshot."""

    def __init__(self) -> None:
        self._last: Optional[AuditSnapshot] = None

    @property
    def last(self) -> Optional[AuditSnapshot]:
        return self._last

    def diff(self, snapshot: AuditSnapshot) -> List[Dict[str, Any]]:
        changes: List[Dict[str, Any]] = []
        prev = self._last.levels if self._last else {}
        cur = snapshot.levels
        for cap_id in sorted(set(prev) | set(cur)):
            before = prev.get(cap_id)
            after = cur.get(cap_id)
            if before == after:
                continue
            if before is None:
                kind = "added"
            elif after is None:
                kind = "removed"
            else:
                kind = "level_changed"
            changes.append({"capability_id": cap_id, "kind": kind, "before": before, "after": after})
        self._last = snapshot
        return changes

    def observe(self, snapshot: AuditSnapshot) -> Dict[str, Any]:
        changes = self.diff(snapshot)
        return {
            "snapshot_digest": snapshot.digest,
            "generated_at": snapshot.generated_at,
            "n_rows": len(snapshot.rows),
            "levels": snapshot.levels,
            "changes": changes,
        }

    def poll(self, fetch: Callable[[], Any]) -> Dict[str, Any]:
        """Poll the live audit API through an injected fetcher."""
        return self.observe(AuditSnapshot.from_api_payload(fetch()))