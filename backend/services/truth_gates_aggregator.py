"""
Truth Gates Aggregator — Pure functions for vote aggregation & reconciliation.

These functions are deterministic and side-effect-free so they can be
independently re-computed from the append-only vote ledger.

Design principle (owner 2026-10-31):
  "Two people who don't know each other's answers can hardly tell the same lie;
   the correct answer is unique — independent results must converge."
"""
from __future__ import annotations
from typing import Any


def aggregate_votes(votes: list[dict[str, Any]]) -> dict[str, Any]:
    """
    2-of-3 majority voting for cheat detection.

    Rules:
      - Only 'cheat' verdicts require 2-of-3 majority to confirm strike.
      - pass / partial / fail maintain single-vote + spot audit.
      - If no verdict reaches >=2 votes, it's a "split verdict" event.

    Args:
        votes: list of dicts with keys {voter_model, verdict, confidence, ...}

    Returns:
        {
            "vote_group_id": str,
            "total_voters": int,
            "tally": {"cheat": N, "pass": N, ...},
            "majority_verdict": str | None,
            "is_confirmed_cheat": bool,
            "is_split": bool,
            "confidence": float,
        }
    """
    if not votes:
        return {
            "total_voters": 0,
            "tally": {},
            "majority_verdict": None,
            "is_confirmed_cheat": False,
            "is_split": False,
            "confidence": 0.0,
        }

    # Tally votes
    tally: dict[str, int] = {}
    confidence_sum = 0.0
    for v in votes:
        verdict = v.get("verdict", "unknown")
        tally[verdict] = tally.get(verdict, 0) + 1
        confidence_sum += float(v.get("confidence", 0.5))

    total = len(votes)
    avg_confidence = confidence_sum / total if total > 0 else 0.0

    # Find majority (>=2 out of 3)
    majority_verdict = None
    max_count = 0
    for verdict, count in tally.items():
        if count > max_count:
            max_count = count
            majority_verdict = verdict

    # 2-of-3 rule: only 'cheat' needs >=2 to confirm
    is_confirmed_cheat = tally.get("cheat", 0) >= 2

    # Split verdict: no verdict has >=2 votes
    is_split = max_count < 2

    return {
        "total_voters": total,
        "tally": tally,
        "majority_verdict": majority_verdict if max_count >= 2 else None,
        "is_confirmed_cheat": is_confirmed_cheat,
        "is_split": is_split,
        "confidence": round(avg_confidence, 4),
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
        metered: {"tool_calls": N, "tokens": N, "wall_seconds": N}
        threshold_pct: deviation threshold (default 20%)

    Returns:
        {
            "deviations": {
                "tool_calls": {"self": N, "metered": N, "deviation_pct": X},
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
    """
    match = self_reported_hash.lower() == stored_hash.lower()
    return {
        "self_reported_hash": self_reported_hash,
        "stored_hash": stored_hash,
        "match": match,
        "is_anomaly": not match,
    }
