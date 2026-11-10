"""Ledger management: adjudication with sha256 seal and per-item judgment reasons.

The ledger answers four questions for a candidate ``title + goal``:

1. **Which neighbours exist** — the nearest ``k`` ledger entries with dual-channel
   similarity breakdown (BM25 normalised + char 3-gram Jaccard).
2. **How close** — the composite score.
3. **Which residual Δ is required** — a near neighbour blocks unless the candidate
   declares something the neighbour does not already have, in one of three channels:
   ``new_quantity``, ``new_criterion``, ``new_truth``.
4. **Whether it is satisfied** — declared identifier counts only if absent from
   the neighbour's declarations *and* absent from the neighbour's own text.

Fail-closed: no Δ → block, Δ repeating neighbour → block, fresh Δ → allow.
"""
from __future__ import annotations

import json
import os
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.whitespace_radar_core.evidence import canonical_bytes, seal, sha256_hex  # noqa: E402
from tools.whitespace_radar_core.index import Bm25Index  # noqa: E402

from .core import SeedConfig, DualSim, dual_similarity, char_3gram_jaccard

SCHEMA = "seeding-dedup/v1"
VERSION = "ag29w1-n10-v1"

#: the three shapes of novelty accepted as residual Δ
DELTA_CHANNELS: Tuple[str, ...] = ("new_quantity", "new_criterion", "new_truth")

ALLOW = "allow"
BLOCK = "block"

CODES = (
    "NEW_TOPIC",
    "RESIDUAL_DELTA",
    "NO_RESIDUAL_DELTA",
    "INSUFFICIENT_INPUT",
    "LEDGER_INVALID",
)


def normalize_id(text: str) -> str:
    """Identifier normalisation: NFKC, casefold, collapse whitespace."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass(frozen=True)
class Node:
    """One ledger entry: a history node or an in-flight node."""

    node_id: str
    title: str
    goal: str
    status: str = "done"  # done | inflight
    deltas: Mapping[str, Sequence[str]] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.goal}"

    def declared(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for channel in DELTA_CHANNELS:
            raw = self.deltas.get(channel) or ()
            if isinstance(raw, str):
                raw = [raw]
            ids = [normalize_id(str(v)) for v in raw if str(v).strip()]
            out[channel] = sorted(set(ids))
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "title": self.title,
            "status": self.status,
            "deltas": self.declared(),
        }


@dataclass(frozen=True)
class Candidate:
    title: str
    goal: str
    deltas: Mapping[str, Sequence[str]] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.goal}"

    def declared(self) -> Dict[str, List[str]]:
        return Node("", self.title, self.goal, deltas=self.deltas).declared()


@dataclass(frozen=True)
class LedgerEntry:
    """One entry in the adjudication ledger (serialisable)."""

    node_id: str
    title: str
    goal: str
    status: str = "done"
    deltas: Mapping[str, Sequence[str]] = field(default_factory=dict)

    def to_node(self) -> Node:
        return Node(self.node_id, self.title, self.goal, self.status, self.deltas)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "title": self.title,
            "goal": self.goal,
            "status": self.status,
            "deltas": dict(self.deltas),
        }


def _as_node(entry: Any) -> Node:
    if isinstance(entry, Node):
        return entry
    if isinstance(entry, LedgerEntry):
        return entry.to_node()
    if not isinstance(entry, Mapping):
        raise ValueError(f"ledger entry must be a mapping or Node, got {type(entry).__name__}")
    title = entry.get("title")
    goal = entry.get("goal")
    node_id = entry.get("node_id") or entry.get("id")
    if not isinstance(title, str) or not isinstance(goal, str) or not node_id:
        raise ValueError(f"ledger entry needs node_id/title/goal: {sorted(entry)}")
    deltas = entry.get("deltas") or {}
    if not isinstance(deltas, Mapping):
        raise ValueError("deltas must be a mapping of channel -> list")
    return Node(str(node_id), title, goal, str(entry.get("status") or "done"), deltas)


def load_ledger(path: str) -> List[Node]:
    """Load a ledger export (``{"schema": ..., "nodes": [...]}`` or a bare list)."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        payload = json.load(fh)
    entries = payload.get("nodes") if isinstance(payload, Mapping) else payload
    if not isinstance(entries, list):
        raise ValueError("ledger must be a list of nodes")
    return [_as_node(e) for e in entries]


def ledger_seal(nodes: Sequence[Node]) -> str:
    """Content digest of the ledger the decision was taken against."""
    payload = [
        {"node_id": n.node_id, "title": n.title, "goal": n.goal, "status": n.status,
         "deltas": n.declared()}
        for n in sorted(nodes, key=lambda x: x.node_id)
    ]
    return sha256_hex(canonical_bytes(payload))


def _occupied(node: Node) -> Dict[str, List[str]]:
    """Identifiers the neighbour already holds, per channel."""
    return node.declared()


def _residual(
    candidate: Candidate, neighbour: Node
) -> Tuple[Dict[str, Any], bool]:
    """Per-channel residual Δ between a candidate and its nearest neighbour."""
    taken = _occupied(neighbour)
    declared = candidate.declared()
    neighbour_text = normalize_id(neighbour.text)
    channels: Dict[str, Any] = {}
    satisfied = False
    for channel in DELTA_CHANNELS:
        items = declared.get(channel) or []
        fresh = [i for i in items if i not in set(taken.get(channel) or []) and i not in neighbour_text]
        repeated = [i for i in items if i not in fresh]
        channels[channel] = {
            "declared": items,
            "neighbour_occupied": taken.get(channel) or [],
            "residual": fresh,
            "repeated": repeated,
            "satisfied": bool(fresh),
        }
        satisfied = satisfied or bool(fresh)
    return channels, satisfied


def _term_set(text: str) -> set:
    """Simple term set for min_terms check (CJK bigrams + latin tokens)."""
    import re
    norm = text.lower()
    terms = set()
    for m in re.finditer(r"[\u4e00-\u9fff]+", norm):
        run = m.group(0)
        for i in range(len(run) - 1):
            terms.add(run[i:i + 2])
    for m in re.finditer(r"[a-z][a-z0-9_./+-]{1,}", norm):
        terms.add(m.group(0))
    return terms


def adjudicate(
    candidate: Any,
    ledger: Sequence[Any],
    index: Bm25Index,
    *,
    cfg: Optional[SeedConfig] = None,
    k: Optional[int] = None,
    theta: Optional[float] = None,
) -> Dict[str, Any]:
    """Adjudicate one seeding candidate against the near-duplicate ledger.

    Uses dual-channel similarity (BM25 + char 3-gram Jaccard).
    Returns a sealed envelope with sha256 and per-item judgment reasons.
    """
    cfg = cfg or SeedConfig()
    th = cfg.theta if theta is None else float(theta)
    kk = cfg.k if k is None else int(k)

    try:
        nodes = [_as_node(e) for e in ledger]
    except ValueError as exc:
        return seal(
            {
                "version": VERSION,
                "decision": BLOCK,
                "code": "LEDGER_INVALID",
                "reasons": [str(exc)],
                "candidate": {"title": getattr(candidate, "title", ""),
                              "goal": getattr(candidate, "goal", "")},
            },
            schema=SCHEMA,
        )

    cand = candidate if isinstance(candidate, Candidate) else Candidate(
        str((candidate or {}).get("title") or ""),
        str((candidate or {}).get("goal") or ""),
        (candidate or {}).get("deltas") or {},
    )

    base = {
        "version": VERSION,
        "candidate": {"title": cand.title, "goal": cand.goal, "deltas": cand.declared()},
        "ledger": {
            "n_nodes": len(nodes),
            "n_done": sum(1 for n in nodes if n.status == "done"),
            "n_inflight": sum(1 for n in nodes if n.status != "done"),
            "seal": ledger_seal(nodes),
        },
        "policy": {
            "theta": round(th, 6),
            "channels": list(DELTA_CHANNELS),
            "min_title_chars": cfg.min_title_chars,
            "min_goal_chars": cfg.min_goal_chars,
            "min_terms": cfg.min_terms,
            "seed_config": cfg.to_dict(),
            "fail_closed": True,
        },
    }

    # ---- fail-closed input checks -------------------------------------
    reasons: List[str] = []
    if len(cand.title.strip()) < cfg.min_title_chars:
        reasons.append(f"title {len(cand.title.strip())} chars < {cfg.min_title_chars}")
    if len(cand.goal.strip()) < cfg.min_goal_chars:
        reasons.append(f"goal {len(cand.goal.strip())} chars < {cfg.min_goal_chars}")
    if len(_term_set(cand.text)) < cfg.min_terms:
        reasons.append(f"terms {len(_term_set(cand.text))} < {cfg.min_terms}")
    if reasons:
        return seal({**base, "decision": BLOCK, "code": "INSUFFICIENT_INPUT",
                     "reasons": reasons, "neighbours": []}, schema=SCHEMA)

    # ---- neighbours with dual-channel similarity ----------------------
    scored: List[Dict[str, Any]] = []
    for node in nodes:
        sim = dual_similarity(index, cand.text, node.node_id, node.text, cfg)
        scored.append(
            {
                "node_id": node.node_id,
                "status": node.status,
                "score": sim.score,
                "sim": sim.to_dict(),
                "title": node.title,
                "goal": node.goal,
            }
        )
    scored.sort(key=lambda r: (-r["score"], r["node_id"]))
    neighbours = scored[: max(1, kk)]
    nearest_id = neighbours[0]["node_id"] if neighbours else ""
    nearest_score = neighbours[0]["score"] if neighbours else 0.0

    if not neighbours or nearest_score < th:
        return seal(
            {
                **base,
                "decision": ALLOW,
                "code": "NEW_TOPIC",
                "reasons": [
                    f"nearest ledger entry {nearest_id or '(none)'} scores "
                    f"{nearest_score:.3f} < theta {th:.3f}: no near duplicate"
                ],
                "neighbours": neighbours,
                "required_residual": None,
            },
            schema=SCHEMA,
        )

    nearest_node = next(n for n in nodes if n.node_id == nearest_id)
    channels, satisfied = _residual(cand, nearest_node)
    required = {
        "nearest": nearest_id,
        "nearest_status": nearest_node.status,
        "nearest_score": nearest_score,
        "theta": round(th, 6),
        "channels": channels,
        "instruction": (
            "declare at least one identifier in one of the channels that the nearest "
            "neighbour does not already hold and does not state in its own text"
        ),
        "missing_channels": [c for c in DELTA_CHANNELS if not channels[c]["satisfied"]],
    }

    # Per-item judgment reasons
    judgment_reasons = []
    if satisfied:
        for channel in DELTA_CHANNELS:
            ch = channels[channel]
            if ch["residual"]:
                judgment_reasons.append(
                    f"channel {channel}: residual {ch['residual']} "
                    f"(neighbour occupied {ch['neighbour_occupied']})"
                )
    else:
        for channel in DELTA_CHANNELS:
            ch = channels[channel]
            if ch["repeated"]:
                judgment_reasons.append(
                    f"channel {channel}: all declared items repeated "
                    f"(neighbour occupied {ch['neighbour_occupied']})"
                )
            elif not ch["declared"]:
                judgment_reasons.append(f"channel {channel}: no items declared")

    if satisfied:
        return seal(
            {
                **base,
                "decision": ALLOW,
                "code": "RESIDUAL_DELTA",
                "reasons": [
                    f"near duplicate {nearest_id} (score {nearest_score:.3f} >= theta {th:.3f}) "
                    "but the candidate declares residual delta"
                ] + judgment_reasons,
                "neighbours": neighbours,
                "required_residual": required,
                "judgment_reasons": judgment_reasons,
            },
            schema=SCHEMA,
        )
    return seal(
        {
            **base,
            "decision": BLOCK,
            "code": "NO_RESIDUAL_DELTA",
            "reasons": [
                f"near duplicate {nearest_id} (score {nearest_score:.3f} >= theta {th:.3f}) "
                "and the candidate declares no residual delta the neighbour lacks",
                "fail-closed: a re-worded duplicate must be merged, not seeded",
            ] + judgment_reasons,
            "neighbours": neighbours,
            "required_residual": required,
            "judgment_reasons": judgment_reasons,
        },
        schema=SCHEMA,
    )
