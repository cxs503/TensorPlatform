"""Ledger integration: a level edit must pass the gate before it is written.

``apply_changes`` is the only sanctioned write path for capability levels:

1. read the current ledger, build ``{capability_id: level}``;
2. merge the requested edits and run :func:`tools.evidence_regression.gate.evaluate_changes`;
3. **RED ⇒ nothing is written** (``GateBlocked``), the refusal is appended to the audit trail;
4. otherwise the new ledger is written and the verdict (with the probe digest that backed it)
   is appended to the audit trail.

The audit trail is JSONL so every accepted downgrade can be traced back to the exact probe
run that justified it (and an acknowledgement can never outlive its evidence: the digest has
to match).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .gate import GateResult, evaluate_changes
from .model import sha256_obj
from .registry import ProbeRegistry

DEFAULT_AUDIT = "artifacts/evidence_regression/ledger_audit.jsonl"


class GateBlocked(RuntimeError):
    """The requested level edit was refused by the regression gate."""

    def __init__(self, result: GateResult, audit_record: Dict[str, Any]):
        self.result = result
        self.audit_record = audit_record
        codes = ", ".join(sorted({f.code for f in result.red_findings})) or "RED"
        super().__init__(f"level edit blocked by evidence regression gate: {codes}")


def read_ledger(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def levels_of(ledger: Mapping[str, Any], field_name: str = "claimed_level") -> Dict[str, str]:
    caps = ledger.get("capabilities") if isinstance(ledger, Mapping) else None
    if not isinstance(caps, list):
        raise ValueError("ledger has no 'capabilities' list")
    out: Dict[str, str] = {}
    for cap in caps:
        if not isinstance(cap, dict):
            continue
        cid = cap.get("id") or cap.get("capability_id")
        level = cap.get(field_name)
        if cid and level:
            out[str(cid)] = str(level).upper()
    return out


def _apply(ledger: Dict[str, Any], changes: Mapping[str, str], field_name: str) -> Dict[str, Any]:
    updated = json.loads(json.dumps(ledger))  # deep copy, keeps unknown fields intact
    caps = updated.get("capabilities") or []
    seen = set()
    for cap in caps:
        if not isinstance(cap, dict):
            continue
        cid = cap.get("id") or cap.get("capability_id")
        if cid in changes:
            previous = cap.get(field_name)
            cap[field_name] = str(changes[cid]).upper()
            cap.setdefault("level_change_log", []).append({
                "from": previous, "to": str(changes[cid]).upper(),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            })
            seen.add(cid)
    for cid, level in changes.items():
        if cid not in seen:
            caps.append({"id": cid, field_name: str(level).upper(),
                         "level_change_log": [{"from": None, "to": str(level).upper(),
                                               "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}]})
    updated["capabilities"] = caps
    return updated


def _append_audit(path: str | Path, record: Dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def apply_changes(ledger_path: str | Path, changes: Mapping[str, str], registry: ProbeRegistry,
                  out_path: Optional[str | Path] = None, audit_path: str | Path = DEFAULT_AUDIT,
                  ack: Any = None, field_name: str = "claimed_level",
                  actor: str = "evidence-regression-gate") -> Dict[str, Any]:
    """Gate then (only on OK/ACK) write. Raises :class:`GateBlocked` on RED."""
    ledger = read_ledger(ledger_path)
    before = levels_of(ledger, field_name)
    after = dict(before)
    after.update({str(k): str(v).upper() for k, v in changes.items()})
    started = time.perf_counter()
    result = evaluate_changes(registry, before, after, ack=ack)
    elapsed = (time.perf_counter() - started) * 1000.0
    record: Dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "actor": actor,
        "ledger": str(ledger_path),
        "changes": dict(changes),
        "verdict": result.verdict,
        "red_codes": sorted({f.code for f in result.red_findings}),
        "elapsed_ms": round(elapsed, 3),
        "gate_digest": result.digest(),
        "written": result.verdict != "RED",
    }
    if result.verdict == "RED":
        _append_audit(audit_path, record)
        raise GateBlocked(result, record)
    target_path = Path(out_path) if out_path else Path(ledger_path)
    _apply_payload = _apply(ledger, changes, field_name)
    _apply_payload["last_gated_edit"] = {
        "verdict": result.verdict,
        "gate_digest": result.digest(),
        "changes": dict(changes),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    target_path.write_text(json.dumps(_apply_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    record["out_path"] = str(target_path)
    _append_audit(audit_path, record)
    return {"record": record, "gate": result.to_dict(), "out_path": str(target_path),
            "ledger_digest": sha256_obj(_apply_payload)}


def blocked_summary(exc: GateBlocked) -> Dict[str, Any]:
    """Small helper for CLI/CI consumers."""
    return {
        "verdict": exc.result.verdict,
        "red_codes": sorted({f.code for f in exc.result.red_findings}),
        "capabilities": sorted({f.capability_id for f in exc.result.red_findings}),
        "audit_record": exc.audit_record,
    }


__all__ = ["apply_changes", "read_ledger", "levels_of", "GateBlocked", "blocked_summary", "DEFAULT_AUDIT"]