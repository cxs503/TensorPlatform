"""Append only JSONL ledger for selector decisions (criterion C7).

Every decision (shadow *and* enforcing) writes one self contained record: the
changed files, the graph fingerprint, the config hash, the selected tests, the
selection hash, timings and the evidence counters.  ``Ledger.reproduce(run_id)``
recomputes the decision from the record alone, so any past row can be audited
end-to-end without re-running the pipeline.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .graph import ImportGraph
from .selector import ImpactSelector, Selection

SCHEMA = "tp.test_impact_selector.ledger/v1"


def _hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Ledger:
    path: Path | str

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    # -- write --------------------------------------------------------------
    def record(
        self,
        selection: Selection,
        plan: Dict[str, object],
        duration_ms: float,
        extra: Optional[Dict[str, object]] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, object]:
        row = {
            "schema": SCHEMA,
            "run_id": run_id or f"tis-{uuid.uuid4().hex[:12]}",
            "ts": round(time.time(), 6),
            "mode": selection.requested_mode,
            "executed": plan.get("executed"),
            "decision": selection.mode,
            "trigger": selection.trigger,
            "changed_files": selection.changed_files,
            "changed_hash": _hash(selection.changed_files),
            "selected": len(selection.tests),
            "full": selection.all_tests,
            "ratio": round(selection.ratio, 6),
            "selection_hash": selection.selection_hash,
            "tests": selection.tests if selection.mode == "subset" else [],
            "graph_fingerprint": selection.graph_fingerprint,
            "config_hash": selection.config_hash,
            "reasons": selection.reasons[:10],
            "selector_ms": round(duration_ms, 3),
            "evidence": {
                "impacted_modules": len(selection.impacted_modules),
                "safety": selection.extra.get("safety", []),
                "saved_ratio": plan.get("saved_ratio"),
            },
        }
        if extra:
            row["extra"] = extra
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        return row

    # -- read ---------------------------------------------------------------
    def rows(self) -> List[Dict[str, object]]:
        if not self.path.is_file():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def get(self, run_id: str) -> Optional[Dict[str, object]]:
        for row in self.rows():
            if row.get("run_id") == run_id:
                return row
        return None

    def stats(self) -> Dict[str, object]:
        rows = self.rows()
        n = len(rows)
        fail_closed = sum(1 for r in rows if r.get("decision") == "full")
        subset = [r for r in rows if r.get("decision") == "subset"]
        return {
            "schema": SCHEMA + ".stats/v1",
            "records": n,
            "shadow_records": sum(1 for r in rows if r.get("mode") == "shadow"),
            "enforcing_records": sum(1 for r in rows if r.get("mode") == "enforcing"),
            "fail_closed": fail_closed,
            "fail_closed_ratio": round(fail_closed / n, 6) if n else 0.0,
            "subset_decisions": len(subset),
            "mean_selected_ratio": round(sum(r["ratio"] for r in subset) / len(subset), 6) if subset else 0.0,
            "ledger_sha256": _hash([r.get("run_id") for r in rows]),
        }

    # -- audit --------------------------------------------------------------
    def reproduce(self, run_id: str, graph: ImportGraph, selector: ImpactSelector) -> Dict[str, object]:
        """Recompute one historical decision and compare hashes (C7)."""
        row = self.get(run_id)
        if row is None:
            return {"run_id": run_id, "found": False, "match": False}
        sel = selector.select(row["changed_files"], mode=row.get("mode"))
        return {
            "run_id": run_id,
            "found": True,
            "recorded": {
                "decision": row["decision"],
                "trigger": row["trigger"],
                "selected": row["selected"],
                "selection_hash": row["selection_hash"],
                "graph_fingerprint": row["graph_fingerprint"],
                "config_hash": row["config_hash"],
            },
            "recomputed": {
                "decision": sel.mode,
                "trigger": sel.trigger,
                "selected": len(sel.tests),
                "selection_hash": sel.selection_hash,
                "graph_fingerprint": sel.graph_fingerprint,
                "config_hash": sel.config_hash,
            },
            "match": sel.selection_hash == row["selection_hash"],
            "graph_unchanged": sel.graph_fingerprint == row["graph_fingerprint"],
            "config_unchanged": sel.config_hash == row["config_hash"],
        }