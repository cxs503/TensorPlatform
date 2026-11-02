"""
Truth Gates Aggregator — Pure functions for vote aggregation & reconciliation.

These functions are deterministic and side-effect-free so they can be
independently re-computed from the append-only vote ledger.

Design principle (owner 2026-10-31):
  "Two people who don't know each other's answers can hardly tell the same lie;
   the correct answer is unique — independent results must converge."

Verification debt closed in m2038 (audit: docs/audit/m2038_truth_gates_independence.md)
--------------------------------------------------------------------------------
The 2-of-3 rule was previously implemented as a bare ``tally["cheat"] >= 2``
comparison, which admits four escape paths that this module now blocks:

  D1 minority strike   — 2 cheat + 8 pass confirmed a strike (2/10 = 20 % < 2/3).
  D2 quorum bypass     — a 2-voter group with {cheat, cheat} confirmed a strike
                         although "2-of-3" presumes three independent voters.
  D3 order-dependent   — a tied tally ({cheat:2, pass:2}) took the verdict of
                         whichever key was inserted first, and reported
                         is_split=False. Same ballot set, two answers.
  D4 self-confirmation — the same voter_model voting twice was tallied twice
                         (the DB constraint hid this only on the API path; the
                         pure recompute path from an exported ledger was open).
  D5 correlated proof  — two "cheat" verdicts quoting the *same* evidence hash
                         are one source, not two independent reproductions.

Confirmed-cheat now requires: >= 3 distinct voters (quorum), >= 2 cheat votes,
cheat share >= 2/3, and no duplicate voters.  Evidence independence is reported
separately (``is_confirmed_cheat_independent``) because legacy ballots carry no
evidence hash at all; ``confirmation_blocks`` says exactly why a strike was
withheld.
"""
from __future__ import annotations

from typing import Any

# ─── Rule constants (single source of truth, imported by tests & audit) ───
VALID_VERDICTS = ("cheat", "pass", "partial", "fail")
QUORUM = 3                 # independent voters required before any strike
CHEAT_MIN = 2              # absolute number of cheat votes required
CHEAT_SHARE = 2.0 / 3.0    # 2-of-3 generalised to a supermajority
UNIQUE_PLURALITY = 2       # a verdict needs >= 2 votes to be a "majority"
# precedence when one voter somehow appears twice: keep the harshest verdict
_SEVERITY = {"cheat": 3, "fail": 2, "partial": 1, "pass": 0}

_EPS = 1e-9


def _norm_verdict(raw: Any) -> str | None:
    v = str(raw or "").strip().lower()
    return v if v in VALID_VERDICTS else None


def _norm_hash(raw: Any) -> str:
    """Normalise an evidence hash: strip, lower-case, drop 'sha256:' prefix."""
    h = str(raw or "").strip().lower()
    if h.startswith("sha256:"):
        h = h[len("sha256:"):]
    return "".join(h.split())


def _distinct_voters(votes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Collapse duplicate voters.  Deterministic: harshest verdict wins.

    Returns (kept_votes, duplicate_voter_ids, invalid_verdict_payloads).
    """
    kept: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    invalid: list[str] = []
    for v in votes:
        verdict = _norm_verdict(v.get("verdict"))
        voter = str(v.get("voter_model") or "").strip() or "<anonymous>"
        if verdict is None:
            # an unparseable ballot must not count as an independent opinion
            invalid.append(str(v.get("verdict")))
            continue
        previous = kept.get(voter)
        if previous is None:
            kept[voter] = {"voter_model": voter, "verdict": verdict,
                           "confidence": float(v.get("confidence", 0.5)),
                           "evidence_hash": _norm_hash(v.get("evidence_hash"))}
        else:
            duplicates.append(voter)
            if _SEVERITY[verdict] > _SEVERITY[previous["verdict"]]:
                previous["verdict"] = verdict
                previous["evidence_hash"] = _norm_hash(v.get("evidence_hash")) or previous["evidence_hash"]
            previous["confidence"] = max(previous["confidence"], float(v.get("confidence", 0.5)))
    # deterministic ordering (dict insertion order is caller-dependent)
    ordered = [kept[k] for k in sorted(kept)]
    return ordered, sorted(set(duplicates)), invalid


def aggregate_votes(votes: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Independence-aware vote aggregation for cheat detection.

    Rules
    -----
      * One ballot per voter (duplicates collapse to the harshest verdict).
      * Unparseable verdicts are *excluded* and reported, never tallied.
      * A verdict is a "majority" only if it is the unique leader with >= 2 votes;
        a tie is a split verdict (order-independent, deterministic).
      * A cheat strike is confirmed only with quorum (>= 3 distinct voters),
        >= 2 cheat votes and a cheat share >= 2/3.
      * Evidence independence (>= 2 distinct non-blank evidence hashes among the
        cheating voters) is reported separately as
        ``is_confirmed_cheat_independent``.

    Args:
        votes: list of dicts with keys {voter_model, verdict, confidence, evidence_hash}

    Returns:
        Aggregation dict (see ``_empty_result`` for the schema).
    """
    if not votes:
        return _empty_result()

    kept, duplicates, invalid = _distinct_voters(votes)
    total_voters = len(kept)

    tally: dict[str, int] = {v: 0 for v in VALID_VERDICTS}
    for v in kept:
        tally[v["verdict"]] += 1
    tally = {k: c for k, c in tally.items() if c > 0}

    if total_voters == 0:
        result = _empty_result()
        result["total_votes"] = len(votes)
        result["invalid_verdicts"] = invalid
        result["confirmation_blocks"] = ["no_valid_ballot"]
        return result

    avg_confidence = sum(v["confidence"] for v in kept) / total_voters

    # unique plurality (deterministic tie handling)
    max_count = max(tally.values())
    leaders = sorted(k for k, c in tally.items() if c == max_count)
    tied = len(leaders) > 1
    majority_verdict = None if (tied or max_count < UNIQUE_PLURALITY) else leaders[0]

    cheat_votes = tally.get("cheat", 0)
    cheat_share = cheat_votes / total_voters

    # evidence independence among the cheating voters
    cheat_hashes = sorted({v["evidence_hash"] for v in kept
                           if v["verdict"] == "cheat" and v["evidence_hash"]})
    hash_missing = any(v["verdict"] == "cheat" and not v["evidence_hash"] for v in kept)
    correlated = cheat_votes >= 2 and len(cheat_hashes) <= 1 and not hash_missing
    evidence_independent = cheat_votes >= 2 and len(cheat_hashes) >= 2

    blocks: list[str] = []
    if total_voters < QUORUM:
        blocks.append("quorum_not_met")
    if cheat_votes < CHEAT_MIN:
        blocks.append("cheat_votes_below_min")
    if cheat_votes and cheat_share + _EPS < CHEAT_SHARE:
        blocks.append("cheat_share_below_2_3")

    is_confirmed_cheat = not blocks

    independent_blocks = list(blocks)
    if is_confirmed_cheat:
        if correlated:
            independent_blocks.append("correlated_evidence_hash")
        elif hash_missing:
            independent_blocks.append("evidence_hash_missing")
        elif not evidence_independent:
            independent_blocks.append("evidence_not_independent")

    return {
        "total_votes": len(votes),
        "total_voters": total_voters,
        "tally": tally,
        "majority_verdict": majority_verdict,
        "is_split": majority_verdict is None and not (total_voters == 1 and max_count == 1),
        "is_confirmed_cheat": is_confirmed_cheat,
        "is_confirmed_cheat_independent": is_confirmed_cheat and not independent_blocks,
        "confidence": round(avg_confidence, 4),
        "cheat_share": round(cheat_share, 4),
        "quorum": QUORUM,
        "duplicate_voters": duplicates,
        "invalid_verdicts": invalid,
        "evidence": {
            "distinct_hashes": len(cheat_hashes),
            "missing": hash_missing,
            "correlated": correlated,
            "independent": evidence_independent,
        },
        "confirmation_blocks": blocks,
        "independence_blocks": independent_blocks,
    }


def _empty_result() -> dict[str, Any]:
    return {
        "total_votes": 0,
        "total_voters": 0,
        "tally": {},
        "majority_verdict": None,
        "is_confirmed_cheat": False,
        "is_confirmed_cheat_independent": False,
        "is_split": False,
        "confidence": 0.0,
        "cheat_share": 0.0,
        "quorum": QUORUM,
        "duplicate_voters": [],
        "invalid_verdicts": [],
        "evidence": {"distinct_hashes": 0, "missing": False,
                     "correlated": False, "independent": False},
        "confirmation_blocks": [],
        "independence_blocks": [],
    }


def reconcile_usage(
    self_reported: dict[str, Any],
    metered: dict[str, Any],
    threshold_pct: float = 20.0,
) -> dict[str, Any]:
    """
    Compare self-reported usage vs platform-metered usage.

    Args:
        self_reported: {"tool_calls": N, "tokens": N, "wall_seconds": N}
        metered: {"tool_calls": N, "tokens_used": N, "wall_seconds": N}
        threshold_pct: deviation threshold (default 20%)

    Returns:
        {
            "deviations": {
                "tool_calls": {"self_reported": N, "metered": N, "deviation_pct": X},
                ...
            },
            "max_deviation_pct": float,
            "is_anomaly": bool,  # max_deviation > threshold
            "anomaly_fields": [list of fields exceeding threshold],
        }
    """
    fields = ["tool_calls", "tokens", "wall_seconds"]
    # Map self_reported keys to canonical names
    key_map = {
        "tool_calls": ("tool_calls", "tool_calls"),
        "tokens": ("tokens", "tokens_used"),
        "wall_seconds": ("wall_seconds", "wall_seconds"),
    }

    deviations = {}
    anomaly_fields = []
    max_dev = 0.0

    for canonical, (self_key, metered_key) in key_map.items():
        self_val = float(self_reported.get(self_key, 0))
        met_val = float(metered.get(metered_key, 0))

        if met_val == 0 and self_val == 0:
            dev_pct = 0.0
        elif met_val == 0:
            dev_pct = 100.0 if self_val > 0 else 0.0
        else:
            dev_pct = abs(self_val - met_val) / met_val * 100.0

        deviations[canonical] = {
            "self_reported": self_val,
            "metered": met_val,
            "deviation_pct": round(dev_pct, 2),
        }

        if dev_pct > max_dev:
            max_dev = dev_pct
        if dev_pct > threshold_pct:
            anomaly_fields.append(canonical)

    return {
        "deviations": deviations,
        "max_deviation_pct": round(max_dev, 2),
        "threshold_pct": threshold_pct,
        "is_anomaly": max_dev > threshold_pct,
        "anomaly_fields": anomaly_fields,
    }


def reconcile_hash(self_reported_hash: str, stored_hash: str) -> dict[str, Any]:
    """
    Compare self-reported file hash vs platform artifact storage hash.

    A blank hash on either side can never confirm agreement: an agent that
    simply omits the evidence hash must not pass the check (m2038, defect D6 —
    previously ``"" == ""`` reported ``match=True, is_anomaly=False``).
    """
    a = _norm_hash(self_reported_hash)
    b = _norm_hash(stored_hash)
    if not a or not b:
        return {
            "self_reported_hash": self_reported_hash,
            "stored_hash": stored_hash,
            "match": False,
            "is_anomaly": True,
            "reason": "missing_hash",
        }
    match = a == b
    return {
        "self_reported_hash": self_reported_hash,
        "stored_hash": stored_hash,
        "match": match,
        "is_anomaly": not match,
        "reason": "match" if match else "hash_mismatch",
    }