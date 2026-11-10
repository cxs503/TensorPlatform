"""Seeding-time near-duplicate ledger with a residual-Δ gate (fail-closed).

Why this exists
---------------
Proposals are seeded into campaign DAGs, and the expensive failure is not "a bad
idea" — it is *the same idea arriving again* under new wording: a re-ordered
goal, a synonym swap, a new parameter value.  History entries and **in-flight
nodes** are the two populations that can already occupy the idea, and an
in-flight node matters as much as a finished one (it is *current* work).

What the gate returns
---------------------
For a candidate ``title + goal`` the ledger answers four questions in one sealed
envelope (schema ``seeding-dedup/v1``):

1. **Which neighbours exist** — the nearest ``k`` ledger entries with the full
   similarity breakdown (lexical / skeleton / containment / 2-gram IDF cosine /
   char IDF cosine / LCS), ordered deterministically.
2. **How close** — the composite score, comparable across runs because the IDF
   model is rebuilt from the ledger, never from a wall clock or hash order.
3. **Which residual Δ is required** — a near neighbour blocks the candidate
   unless the candidate declares something the neighbour does **not** already
   have, in one of three channels: ``new_quantity`` (a physical/observable
   quantity that did not exist), ``new_criterion`` (an acceptance criterion),
   ``new_truth`` (a reference/ground-truth value).  The envelope spells out, per
   channel, which identifiers the neighbour already occupies, so "what would
   have to be new" is machine-readable rather than a reviewer's opinion.
4. **Whether it is satisfied** — a declared identifier counts as residual Δ only
   if it is absent from the neighbour's declarations *and* absent from the
   neighbour's own text.

Fail-closed rules (all of them refuse, none of them pass by default)
-------------------------------------------------------------------
* missing/short title or goal, too few terms            -> ``INSUFFICIENT_INPUT``
* a near neighbour and no usable Δ                      -> ``NO_RESIDUAL_DELTA``
* a Δ that merely repeats the neighbour's own Δ         -> ``NO_RESIDUAL_DELTA``
* unparseable ledger entry                              -> ``LEDGER_INVALID``

Only two outcomes allow the candidate: no neighbour above ``theta``
(``NEW_TOPIC``) or a near neighbour **plus** a residual Δ (``RESIDUAL_DELTA``).

Domain neutrality: the module knows nothing about the subject.  Titles, goals and
Δ identifiers are opaque strings supplied by the caller; the channels are the
three *shapes* of novelty the seeding gate must be able to distinguish, and the
weights/thresholds all live in :class:`~ops.taskplane.governance.proposal_dedup.DedupConfig`.
"""
from __future__ import annotations

import json
import os
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.whitespace_radar_core.evidence import canonical_bytes, seal, sha256_hex  # noqa: E402

from .proposal_dedup import (  # noqa: E402
    DedupConfig,
    IdfModel,
    build_idf,
    similarity,
    term_set,
)

SCHEMA = "seeding-dedup/v1"
VERSION = "p273-n06-v1"

#: the three *shapes* of novelty a seeding gate accepts as residual Δ
DELTA_CHANNELS: Tuple[str, ...] = ("new_quantity", "new_criterion", "new_truth")

#: fail-closed input floors
MIN_TITLE_CHARS = 4
MIN_GOAL_CHARS = 20
MIN_TERMS = 6


@dataclass(frozen=True)
class SeedingConfig:
    """Seeding-gate policy.

    ``theta`` is the near-duplicate threshold for the *title+goal* regime.  It is
    deliberately **not** :attr:`proposal_dedup.DedupConfig.theta_clone`: that
    value was calibrated on >= 60-character proposal summaries, while a seeding
    candidate is a short title plus one goal sentence, and the similarity
    features are IDF-weighted (short corpora inflate every score).  The shipped
    value below was selected on the ``dev`` split of
    ``data/seeding_dedup/pairs_100.json`` and sits inside a plateau — see
    ``artifacts/p273w3_n06/dedup_bench.json`` (``theta_sweep``) for the whole
    curve, holdout column included.
    """

    theta: float = 0.30            # near-duplicate threshold for title+goal
    k: int = 5                     # neighbours reported
    min_title_chars: int = MIN_TITLE_CHARS
    min_goal_chars: int = MIN_GOAL_CHARS
    min_terms: int = MIN_TERMS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "theta": self.theta,
            "k": self.k,
            "min_title_chars": self.min_title_chars,
            "min_goal_chars": self.min_goal_chars,
            "min_terms": self.min_terms,
        }

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


def _as_node(entry: Any) -> Node:
    if isinstance(entry, Node):
        return entry
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


def adjudicate(
    candidate: Any,
    ledger: Sequence[Any],
    *,
    cfg: Optional[DedupConfig] = None,
    seeding: Optional[SeedingConfig] = None,
    k: Optional[int] = None,
    theta: Optional[float] = None,
    idf: Optional[IdfModel] = None,
) -> Dict[str, Any]:
    """Adjudicate one seeding candidate against the near-duplicate ledger.

    ``candidate`` is ``{"title", "goal", "deltas"}`` (or a :class:`Candidate`);
    ``ledger`` is the history **and in-flight** nodes.  The returned envelope is
    sealed (``seeding-dedup/v1``) so a gate decision can never be re-litigated
    without a digest disagreement.
    """
    cfg = cfg or DedupConfig()
    sc = seeding or SeedingConfig()
    th = sc.theta if theta is None else float(theta)
    kk = sc.k if k is None else int(k)
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
            "min_title_chars": sc.min_title_chars,
            "min_goal_chars": sc.min_goal_chars,
            "min_terms": sc.min_terms,
            "seeding_config": sc.to_dict(),
            "fail_closed": True,
        },
    }

    # ---- fail-closed input checks -------------------------------------
    reasons: List[str] = []
    if len(cand.title.strip()) < sc.min_title_chars:
        reasons.append(f"title {len(cand.title.strip())} chars < {sc.min_title_chars}")
    if len(cand.goal.strip()) < sc.min_goal_chars:
        reasons.append(f"goal {len(cand.goal.strip())} chars < {sc.min_goal_chars}")
    if len(term_set(cand.text)) < sc.min_terms:
        reasons.append(f"terms {len(term_set(cand.text))} < {sc.min_terms}")
    if reasons:
        return seal({**base, "decision": BLOCK, "code": "INSUFFICIENT_INPUT",
                     "reasons": reasons, "neighbours": []}, schema=SCHEMA)

    # ---- neighbours ----------------------------------------------------
    model = idf if idf is not None else build_idf([cand.text] + [n.text for n in nodes])
    scored: List[Dict[str, Any]] = []
    for node in nodes:
        sim = similarity(cand.text, node.text, model, cfg)
        scored.append(
            {
                "node_id": node.node_id,
                "status": node.status,
                "score": round(sim.score, 6),
                "sim": sim.to_dict(),
                "title": node.title,
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
    if satisfied:
        return seal(
            {
                **base,
                "decision": ALLOW,
                "code": "RESIDUAL_DELTA",
                "reasons": [
                    f"near duplicate {nearest_id} (score {nearest_score:.3f} >= theta {th:.3f}) "
                    "but the candidate declares residual delta"
                ],
                "neighbours": neighbours,
                "required_residual": required,
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
            ],
            "neighbours": neighbours,
            "required_residual": required,
        },
        schema=SCHEMA,
    )


def _candidate_text(entry: Any) -> str:
    if isinstance(entry, (Candidate, Node)):
        return entry.text
    if isinstance(entry, Mapping):
        return f"{entry.get('title') or ''}\n{entry.get('goal') or ''}"
    raise ValueError(f"candidate must be a mapping or Candidate, got {type(entry).__name__}")


def near_duplicate_report(
    candidates: Iterable[Any],
    ledger: Sequence[Any],
    *,
    cfg: Optional[DedupConfig] = None,
    seeding: Optional[SeedingConfig] = None,
    k: Optional[int] = None,
    theta: Optional[float] = None,
) -> Dict[str, Any]:
    """Batch form: adjudicate many candidates against one ledger (deterministic)."""
    nodes = [_as_node(e) for e in ledger]
    model = build_idf([_candidate_text(c) for c in candidates] + [n.text for n in nodes])
    rows = [
        adjudicate(c, nodes, cfg=cfg, seeding=seeding, k=k, theta=theta, idf=model)
        for c in candidates
    ]
    return {
        "version": VERSION,
        "n_candidates": len(rows),
        "n_blocked": sum(1 for r in rows if r["decision"] == BLOCK),
        "n_allowed": sum(1 for r in rows if r["decision"] == ALLOW),
        "rows": rows,
    }