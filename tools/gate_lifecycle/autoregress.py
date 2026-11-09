"""Self-regression optimisation (C4): gap analysis -> proposal pool -> next wave.

The loop closes without a human:

1. ``gap_analysis`` inspects the finished wave (node results + ledger) and
   derives *gaps*: failed nodes, withheld nodes, blocked nodes, capabilities
   that never reached the ledger, and criteria that could not be adjudicated.
   Each gap carries the node ids that produced it (traceability).
2. ``proposals_from_gaps`` turns each gap into at most one proposal.  Before
   injecting, every proposal is deduped against (a) the existing proposal pool
   and (b) the repo-level fingerprint list, because a duplicate proposal is
   worse than no proposal (it burns a slot and pollutes the pool).
3. ``inject`` appends the surviving proposals to the pool (append-only JSONL or
   a pluggable sink) and returns the injection report.
4. ``seed_next_wave`` builds the next wave plan: one node per adopted proposal,
   with dependencies taken from the proposal's ``after`` hints (deduped,
   dangling hints dropped so the seeded plan is always a valid DAG).

Everything is deterministic; proposal ids are content digests, so a second run
over the same wave yields the same ids and injects nothing new (idempotent).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .model import NodeResult, Plan, canonical_json, digest
from .verdict import failure_accounting

POOL_SCHEMA = "gate-lifecycle-proposal-pool/v1"


# ---------------------------------------------------------------------------
# 1. gap analysis
# ---------------------------------------------------------------------------
def fingerprint(text: str) -> str:
    return digest({"text": " ".join((text or "").lower().split())})[:16]


def gap_analysis(plan: Plan, results: Sequence[NodeResult],
                 ledger_records: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    registered = {str(r.get("capability")) for r in ledger_records}
    gaps: list[dict[str, Any]] = []

    def add(kind: str, severity: str, node_id: str, capability: str, detail: str,
            suggestion: str) -> None:
        gaps.append({
            "kind": kind,
            "severity": severity,
            "node_id": node_id,
            "capability": capability,
            "detail": detail,
            "suggestion": suggestion,
            "fingerprint": fingerprint(f"{kind}:{capability}:{suggestion}"),
        })

    for r in results:
        cap = r.capability or r.node_id
        if r.status == "failed":
            violated = [c.name for c in r.criteria if c.state == "FAIL"]
            add("criterion_violation", "high", r.node_id, cap,
                "required criteria violated: " + ", ".join(violated),
                f"re-enter {cap} with the violated criterion as the acceptance line")
        elif r.status == "withheld":
            missing = [c.metric for c in r.criteria if c.state == "WITHHELD"]
            add("missing_evidence", "medium", r.node_id, cap,
                "metrics unavailable: " + (", ".join(missing) or "none declared"),
                f"instrument {cap}: emit the missing metrics so the gate can adjudicate")
        elif r.status == "blocked":
            add("blocked_dependency", "medium", r.node_id, cap,
                "node never executed because a dependency did not pass",
                f"unblock {cap} by resolving its upstream gaps first")
        elif r.status == "honest_fail":
            add("negative_result", "low", r.node_id, cap,
                "honest FAIL: hypothesis falsified, moved to the S-N track",
                f"record {cap} as negative knowledge and pick an orthogonal angle")
        if r.verdict == "PASS" and cap not in registered:
            add("unregistered_capability", "high", r.node_id, cap,
                "node passed but no capability ledger entry",
                f"register {cap} in the capability ledger")

    counts: dict[str, int] = {}
    for g in gaps:
        counts[g["kind"]] = counts.get(g["kind"], 0) + 1
    return {
        "plan_id": plan.plan_id,
        "gaps": sorted(gaps, key=lambda g: (g["kind"], g["node_id"], g["fingerprint"])),
        "counts": dict(sorted(counts.items())),
        "failure_accounting": failure_accounting(results),
    }


# ---------------------------------------------------------------------------
# 2. proposal generation
# ---------------------------------------------------------------------------
@dataclass
class ProposalSink:
    """Append-only JSONL proposal pool."""

    path: str | Path

    def read(self) -> list[dict[str, Any]]:
        p = Path(self.path)
        if not p.exists():
            return []
        out = []
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def append(self, proposals: Sequence[Mapping[str, Any]]) -> int:
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        existing = self.read()
        with p.open("w", encoding="utf-8") as fh:
            for rec in existing + [dict(x) for x in proposals]:
                fh.write(canonical_json(rec) + "\n")
        return len(proposals)


def proposals_from_gaps(gaps: Mapping[str, Any],
                        existing_pool: Sequence[Mapping[str, Any]] = (),
                        known_fingerprints: Iterable[str] = ()) -> dict[str, Any]:
    """One proposal per distinct gap fingerprint; drop duplicates."""
    known = {str(x) for x in known_fingerprints}
    for rec in existing_pool:
        if rec.get("fingerprint"):
            known.add(str(rec["fingerprint"]))
        if rec.get("text"):
            known.add(fingerprint(str(rec["text"])))

    proposals: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for gap in gaps.get("gaps", []):
        fp = str(gap["fingerprint"])
        if fp in known or fp in seen:
            duplicates.append({"fingerprint": fp, "reason": "already in pool or repo"})
            continue
        seen.add(fp)
        text = f"{gap['suggestion']} [gap:{gap['kind']}]"
        pid = "prop-" + digest({"fp": fp, "text": text})[:12]
        proposals.append({
            "schema": POOL_SCHEMA,
            "proposal_id": pid,
            "fingerprint": fp,
            "gap_kind": gap["kind"],
            "severity": gap["severity"],
            "capability": gap["capability"],
            "source_nodes": [gap["node_id"]],
            "text": text,
            "after": [],
            "origin_plan": gaps.get("plan_id"),
            "status": "pending",
        })
    order = {"high": 0, "medium": 1, "low": 2}
    proposals.sort(key=lambda p: (order.get(str(p["severity"]), 9), p["proposal_id"]))
    return {
        "proposals": proposals,
        "duplicates": duplicates,
        "generated": len(proposals),
        "deduped": len(duplicates),
    }


def inject(result: Mapping[str, Any], sink: ProposalSink) -> dict[str, Any]:
    """Inject generated proposals into the pool; return an audit record."""
    proposals = list(result["proposals"])
    written = sink.append(proposals) if proposals else 0
    return {
        "written": written,
        "deduped": result["deduped"],
        "proposal_ids": [p["proposal_id"] for p in proposals],
        "pool_path": str(sink.path),
        "pool_size": len(sink.read()),
    }


# ---------------------------------------------------------------------------
# 3. next-wave seeding
# ---------------------------------------------------------------------------
def seed_next_wave(pool: Sequence[Mapping[str, Any]], *, plan_id: str,
                   max_nodes: int = 10, duration: float = 1.0,
                   capabilities_per_node: int = 1) -> dict[str, Any]:
    """Turn pending proposals into a dispatchable next-wave plan."""
    pending = [p for p in pool if p.get("status", "pending") == "pending"]
    pending.sort(key=lambda p: ({"high": 0, "medium": 1, "low": 2}.get(
        str(p.get("severity")), 9), str(p.get("proposal_id"))))
    selected = pending[:max_nodes]
    nodes: list[dict[str, Any]] = []
    for idx, prop in enumerate(selected):
        nid = f"N{idx + 1:02d}"
        nodes.append({
            "id": nid,
            "deps": [],
            "duration": duration,
            "capability": str(prop.get("capability") or prop["proposal_id"]),
            "category": "capability_extension",
            "meta": {
                "proposal_id": prop["proposal_id"],
                "gap_kind": prop.get("gap_kind"),
                "text": prop.get("text"),
                "origin_plan": prop.get("origin_plan"),
            },
            "criteria": [],
        })
    # 'after' hints reference capabilities; wire them when both ends exist
    by_cap = {n["capability"]: n["id"] for n in nodes}
    for node, prop in zip(nodes, selected):
        deps = []
        for hint in prop.get("after", []) or []:
            target = by_cap.get(str(hint))
            if target and target != node["id"] and target not in deps:
                deps.append(target)
        node["deps"] = deps
    plan = {"plan_id": plan_id, "nodes": nodes,
            "meta": {"seeded_from": [p["proposal_id"] for p in selected],
                     "capabilities_per_node": capabilities_per_node}}
    # validate by construction (raises on cycle / unknown dep)
    Plan.from_dict(plan)
    return plan


def close_wave(pool: Sequence[Mapping[str, Any]], injected_ids: Sequence[str],
               *, adopted: bool = True) -> list[dict[str, Any]]:
    """Mark injected proposals as adopted (kept for audit, not re-seeded)."""
    out = []
    ids = set(injected_ids)
    for rec in pool:
        rec = dict(rec)
        if rec.get("proposal_id") in ids and adopted:
            rec["status"] = "adopted"
        out.append(rec)
    return out


def rewrite_pool(path: str | Path, records: Sequence[Mapping[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(canonical_json(rec) + "\n")
    os.replace(tmp, p)