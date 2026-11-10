"""Auto-updating capability ledger: append-only events + materialised state.

The ledger is the *output* of the grading loop and the only place where an
evidence level exists.  Three invariants are enforced in code rather than by
review:

1. **No upgrade without a passing chain.**  ``REAL`` is written only when the
   chain validates, its verdict is ``PASS`` and its decided level is ``REAL``.
2. **No silent downgrade.**  A failing chain on a subject that was ``REAL`` writes
   a ``regression`` event carrying ``level_before`` and a reason; the entry keeps
   the regression in ``regressions`` so the drop is queryable afterwards.
3. **Idempotence.**  The same chain (same ``chain_digest``) applied twice produces
   one event and no state change, so re-running the pipeline cannot inflate the
   upgrade count.

State is a JSON document; events are a JSONL append-only log.  Both are written
atomically (temp file + rename) so a crashed run cannot leave a half-written
ledger.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .grading import validate_chain

SCHEMA = "capability-ledger-autograde/1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class CapabilityLedger:
    """Content-addressed auto-graded capability ledger."""

    def __init__(self, state_path: Path, events_path: Optional[Path] = None,
                 verify_report_file: bool = True):
        self.verify_report_file = verify_report_file
        self.state_path = Path(state_path)
        self.events_path = Path(events_path) if events_path else self.state_path.with_name(
            self.state_path.stem + "_events.jsonl")
        self.state: Dict[str, Any] = {"schema": SCHEMA, "entries": {}, "applied_chains": [],
                                      "counters": {"created": 0, "updated": 0, "noop": 0,
                                                   "rejected": 0, "regression": 0}}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))

    # -- persistence -------------------------------------------------------
    def _flush(self) -> None:
        _atomic_write_json(self.state_path, self.state)

    def _event(self, event: Mapping[str, Any]) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    # -- query -------------------------------------------------------------
    def entry(self, subject_id: str) -> Optional[Dict[str, Any]]:
        return self.state["entries"].get(subject_id)

    def entries(self) -> Dict[str, Any]:
        return self.state["entries"]

    def level_of(self, subject_id: str) -> Optional[str]:
        e = self.entry(subject_id)
        return e["level"] if e else None

    def summary(self) -> Dict[str, Any]:
        by_level: Dict[str, int] = {}
        for entry in self.state["entries"].values():
            by_level[entry["level"]] = by_level.get(entry["level"], 0) + 1
        return {
            "schema": SCHEMA,
            "n_entries": len(self.state["entries"]),
            "by_level": by_level,
            "real_ratio": (by_level.get("REAL", 0) / len(self.state["entries"]))
            if self.state["entries"] else 0.0,
            "counters": dict(self.state["counters"]),
            "applied_chains": len(self.state["applied_chains"]),
        }

    # -- mutation ----------------------------------------------------------
    def apply(self, chain: Mapping[str, Any], mission_id: Optional[int] = None) -> Dict[str, Any]:
        """Apply one evidence chain.  Returns the decision record."""
        subject = chain.get("subject_id", "<unknown>")
        chain_digest = chain.get("chain_digest")
        validation = validate_chain(chain, verify_report_file=self.verify_report_file)

        if not validation["valid"]:
            self.state["counters"]["rejected"] += 1
            decision = {
                "subject_id": subject, "chain_digest": chain_digest, "action": "rejected",
                "level": self.level_of(subject), "reason": "chain_invalid",
                "problems": validation["problems"], "timestamp": _now(),
            }
            self._event({**decision, "kind": "ledger_event"})
            self._flush()
            return decision

        if chain_digest in self.state["applied_chains"]:
            self.state["counters"]["noop"] += 1
            decision = {"subject_id": subject, "chain_digest": chain_digest, "action": "noop",
                        "level": self.level_of(subject), "reason": "chain already applied",
                        "timestamp": _now()}
            self._event({**decision, "kind": "ledger_event"})
            self._flush()
            return decision

        entry = self.state["entries"].get(subject)
        created = entry is None
        if created:
            entry = {
                "subject_id": subject,
                "level": "WITHHELD",
                "evidence": [],
                "history": [],
                "regressions": [],
                "first_seen": _now(),
            }
            self.state["entries"][subject] = entry

        level_before = entry["level"]
        decided = chain["decided_level"]
        action = "created" if created else "updated"

        if decided == "REAL":
            entry["level"] = "REAL"
            entry["observed_level"] = None
            entry["last_pass_chain"] = chain_digest
        else:
            entry["level"] = "WITHHELD"
            entry["observed_level"] = chain.get("observed_level")
            if level_before == "REAL":
                entry["regressions"].append({
                    "timestamp": _now(), "chain_digest": chain_digest,
                    "level_before": "REAL", "level_after": "WITHHELD",
                    "reason": chain.get("reason", ""),
                })
                self.state["counters"]["regression"] += 1
                action = "regressed"

        entry["reason"] = chain.get("reason", "")
        entry["last_chain_digest"] = chain_digest
        entry["updated_at"] = _now()
        entry["evidence"].append({
            "chain_digest": chain_digest,
            "verdict": chain["verdict"],
            "decided_level": decided,
            "test_report_sha256": chain["test_report"]["sha256"],
            "produced_at": chain.get("produced_at"),
            "mission_id": mission_id if mission_id is not None else chain.get("mission_id"),
        })
        entry["history"].append({"timestamp": _now(), "level_before": level_before,
                                 "level_after": entry["level"], "chain_digest": chain_digest})
        self.state["applied_chains"].append(chain_digest)
        self.state["counters"]["created" if created else "updated"] += 1

        decision = {
            "subject_id": subject,
            "chain_digest": chain_digest,
            "action": action,
            "level_before": level_before,
            "level": entry["level"],
            "reason": chain.get("reason", ""),
            "timestamp": _now(),
        }
        self._event({**decision, "kind": "ledger_event"})
        self._flush()
        return decision