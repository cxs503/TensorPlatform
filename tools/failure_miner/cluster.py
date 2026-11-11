"""Clustering and deduplication of failure signatures.

Groups classified missions by (signature_type, error_code, file_fingerprint,
reason_hash) and computes frequency × impact_score for ranking.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Sequence

from .signature_miner import (
    FailedMissionView,
    FailureSignature,
    compute_reason_hash,
    extract_error_code,
    extract_file_fingerprint,
    extract_failure_reason,
)


def cluster_signatures(
    classified: list[tuple[FailedMissionView, str]],
) -> list[FailureSignature]:
    """Cluster classified missions into failure signatures.

    Groups by (signature_type, error_code, file_fingerprint, reason_hash).
    Computes frequency and impact_score for each cluster.

    Args:
        classified: list of (mission, signature_type) from classifier

    Returns:
        List of FailureSignature sorted by impact_score descending.
    """
    groups: dict[str, list[FailedMissionView]] = defaultdict(list)

    for mission, sig_type in classified:
        error_code = extract_error_code(mission)
        file_fp = extract_file_fingerprint(mission)
        reason = extract_failure_reason(mission)
        reason_hash = compute_reason_hash(reason)

        key = f"{sig_type}|{error_code}|{file_fp}|{reason_hash}"
        groups[key].append(mission)

    signatures = []
    for key, missions in groups.items():
        parts = key.split("|")
        sig_type = parts[0]
        error_code = parts[1]
        file_fp = parts[2]
        reason_hash = parts[3]

        frequency = len(missions)
        # Impact score: frequency × unique_orgs × has_lifecycle_evidence
        unique_orgs = len({m.organization_id for m in missions if m.organization_id})
        has_lifecycle = sum(1 for m in missions if m.lifecycle_events)
        impact_score = frequency * max(1, unique_orgs) * (1 + 0.1 * has_lifecycle)

        # SQL trace for provenance
        mission_ids = [m.mission_id for m in missions]
        evidence_sql = (
            f"SELECT id, status, fail_stage, result FROM tensor_dev_missions "
            f"WHERE id IN ({','.join(str(mid) for mid in mission_ids[:10])})"
        )

        sig = FailureSignature(
            signature_type=sig_type,
            error_code=error_code,
            file_fingerprint=file_fp,
            reason_hash=reason_hash,
            mission_ids=mission_ids,
            evidence_sql=evidence_sql,
            frequency=frequency,
            impact_score=impact_score,
        )
        signatures.append(sig)

    # Sort by impact_score descending
    signatures.sort(key=lambda s: s.impact_score, reverse=True)
    return signatures


def deduplicate_against_existing(
    candidates: list[FailureSignature],
    existing_traps: list[dict],
    similarity_threshold: float = 0.8,
) -> tuple[list[FailureSignature], list[dict]]:
    """Mark candidates that are too similar to existing KNOWN_TRAPS.

    Args:
        candidates: new failure signatures
        existing_traps: existing KNOWN_TRAPS entries (list of dicts with 'text' key)
        similarity_threshold: Jaccard similarity threshold for marking as existing

    Returns:
        (new_candidates, marked_existing) tuples
    """
    new_candidates = []
    marked_existing = []

    for sig in candidates:
        # Build text representation for similarity check
        sig_text = f"{sig.signature_type} {sig.error_code} {sig.file_fingerprint}"
        sig_words = set(sig_text.lower().split())

        is_duplicate = False
        for trap in existing_traps:
            trap_text = trap.get("text", trap.get("rule_text", ""))
            trap_words = set(trap_text.lower().split())

            if not trap_words or not sig_words:
                continue

            # Jaccard similarity
            intersection = sig_words & trap_words
            union = sig_words | trap_words
            similarity = len(intersection) / len(union) if union else 0

            if similarity >= similarity_threshold:
                is_duplicate = True
                marked_existing.append({
                    "signature_hash": sig.signature_hash,
                    "existing_trap": trap_text[:120],
                    "similarity": round(similarity, 4),
                })
                break

        if not is_duplicate:
            new_candidates.append(sig)

    return new_candidates, marked_existing


def compute_similarity(text1: str, text2: str) -> float:
    """Compute Jaccard similarity between two texts."""
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union) if union else 0.0
