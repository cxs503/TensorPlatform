"""Capability ledger auto-registration (C3).

On a PASS verdict the pipeline must register a ``capability_delta`` record with
no human step.  Registration is append-only and **idempotent**: the dedupe key
is ``(node_id, result_digest)``, so re-running the same input never inflates the
ledger, while a genuinely different result appends a new revision (history is
kept, latest revision wins).

S-grade derivation is a declared, auditable rule set (S3 > S2 > S1):

S3  declares a *new* mechanism whose evidence includes a structural guarantee
    (``structural_guard`` list non-empty) **and** a baseline comparison
    (``baseline_delta`` present with a numeric delta);
S2  has a baseline comparison (``baseline_delta`` numeric) -- a measurable
    improvement over a prior state;
S1  everything else that passes (execution-level deliverable).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .model import NodeResult, PASS, canonical_json, digest

LEDGER_SCHEMA = "gate-lifecycle-capability-ledger/v1"


def grade_of(record: Mapping[str, Any]) -> str:
    ev = record.get("evidence") or {}
    structural = ev.get("structural_guard") or []
    delta = ev.get("baseline_delta")
    has_delta = isinstance(delta, (int, float)) and not isinstance(delta, bool)
    if structural and has_delta:
        return "S3"
    if has_delta:
        return "S2"
    return "S1"


def build_record(result: NodeResult, node_meta: Mapping[str, Any] | None = None,
                 run_id: str = "") -> dict[str, Any]:
    meta = dict(node_meta or {})
    evidence = dict(result.evidence)
    # evidence fields that come from the plan/node metadata (declared up front)
    for key in ("baseline_delta", "baseline_ref", "structural_guard", "artifact_paths"):
        if key in meta and key not in evidence:
            evidence[key] = meta[key]
    record = {
        "schema": LEDGER_SCHEMA,
        "run_id": run_id,
        "node_id": result.node_id,
        "capability": result.capability,
        "category": result.category,
        "verdict": result.verdict,
        "status": result.status,
        "evidence": evidence,
        "criteria": [c.to_dict() for c in result.criteria],
    }
    record["grade"] = grade_of(record)
    record["record_digest"] = digest({k: v for k, v in record.items()})
    return record


@dataclass
class LedgerStats:
    appended: int
    duplicates: int
    records: int
    by_grade: dict[str, int]
    by_category: dict[str, int]


class CapabilityLedger:
    """Append-only JSONL ledger with digest dedupe."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- io -------------------------------------------------------------
    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def _key(self, rec: Mapping[str, Any]) -> str:
        body = {k: v for k, v in rec.items() if k != "record_digest"}
        return digest({"node_id": rec.get("node_id"), "result": body})

    def register(self, records: Sequence[Mapping[str, Any]]) -> LedgerStats:
        existing = self.read()
        seen = {self._key(r) for r in existing}
        appended: list[dict[str, Any]] = []
        dupes = 0
        for rec in records:
            key = self._key(rec)
            if key in seen:
                dupes += 1
                continue
            seen.add(key)
            appended.append(dict(rec))
        if appended:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for rec in existing + appended:
                    fh.write(canonical_json(rec) + "\n")
            os.replace(tmp, self.path)
        all_recs = existing + appended
        by_grade: dict[str, int] = {}
        by_cat: dict[str, int] = {}
        for rec in all_recs:
            by_grade[str(rec.get("grade"))] = by_grade.get(str(rec.get("grade")), 0) + 1
            by_cat[str(rec.get("category"))] = by_cat.get(str(rec.get("category")), 0) + 1
        return LedgerStats(len(appended), dupes, len(all_recs),
                           dict(sorted(by_grade.items())), dict(sorted(by_cat.items())))

    def latest(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for rec in self.read():
            out[str(rec.get("capability"))] = rec
        return out


def registration_completeness(results: Iterable[NodeResult],
                              records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """C3 metric: every PASS node must have exactly one ledger record per run."""
    passed = [r for r in results if r.verdict == PASS]
    registered = {str(rec.get("node_id")) for rec in records if rec.get("verdict") == PASS}
    missing = [r.node_id for r in passed if r.node_id not in registered]
    extra = sorted(registered - {r.node_id for r in passed})
    total = len(passed)
    covered = total - len(missing)
    return {
        "passed_nodes": total,
        "registered": covered,
        "missing": missing,
        "unexpected": extra,
        "completeness": (covered / total) if total else 1.0,
    }