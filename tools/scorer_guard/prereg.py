"""Scorer version lock (pre-registration) and drift detection (IN25-T5).

The lock binds, in one hashable record: the scorer specification, the anchor
ledger head, the red-team case suite and the acceptance thresholds.  A tournament
may only be run with a lock whose fingerprints verify; any post-hoc edit to a
threshold, an anchor or the case suite is reported as ``*_DRIFT`` and the scorer
refuses to run (see :func:`tools.scorer_guard.score.score_submission`).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import anchors as A
from . import score as S

LOCK_ID = "ship_perf_tournament_scorer_lock"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def case_suite_hash(case_ids: Sequence[str]) -> str:
    return _sha(A.canonical_json(sorted(case_ids)))


def build_lock(
    registry: Mapping[str, Any],
    case_ids: Sequence[str],
    *,
    created: Optional[str] = None,
    acceptance: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the pre-registration lock for a tournament run."""
    head = registry["ledger"]["head"] if "ledger" in registry else A.chain_head(registry["anchors"])
    spec = S.scorer_spec()
    acceptance = dict(
        acceptance
        or {
            "must_intercept_all_attacks": True,
            "mutation_score_semantic_min": 1.0,
            "anchor_recompute_rel_dev_max": 1e-12,
            "score_limits": {"exact": 100.0, "half_band": 50.0, "outside_band": 0.0},
        }
    )
    lock: Dict[str, Any] = {
        "lock_id": LOCK_ID,
        "created": created or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scorer": spec,
        "scorer_spec_sha256": _sha(A.canonical_json(spec)),
        "anchor_registry_id": registry.get("registry_id"),
        "anchor_registry_version": registry.get("version"),
        "anchor_head": head,
        "anchor_chain": list(registry.get("ledger", {}).get("chain", [])),
        "case_suite_sha256": case_suite_hash(case_ids),
        "n_cases": len(list(case_ids)),
        "acceptance": acceptance,
        "fingerprint": S.scorer_fingerprint(head),
    }
    return lock


def verify_lock(
    lock: Mapping[str, Any],
    registry: Mapping[str, Any],
    case_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Re-derive every locked quantity; report drift instead of silently scoring."""
    drift: List[str] = []
    spec_now = S.scorer_spec()
    if _sha(A.canonical_json(spec_now)) != lock.get("scorer_spec_sha256"):
        drift.append("SCORER_SPEC_DRIFT")
    head_now = A.chain_head(registry["anchors"])
    if head_now != lock.get("anchor_head"):
        drift.append("ANCHOR_HEAD_DRIFT")
    if case_ids is not None and case_suite_hash(case_ids) != lock.get("case_suite_sha256"):
        drift.append("CASE_SUITE_DRIFT")
    fp_now = S.scorer_fingerprint(head_now)
    if fp_now != lock.get("fingerprint"):
        drift.append("FINGERPRINT_DRIFT")
    ledger = A.verify_ledger(registry)
    if not ledger["ok"]:
        drift.append("LEDGER_INTEGRITY_DRIFT")
    return {
        "ok": not drift,
        "drift": drift,
        "fingerprint_now": fp_now,
        "anchor_head_now": head_now,
        "spec_sha256_now": _sha(A.canonical_json(spec_now)),
        "expected_fingerprint": lock.get("fingerprint"),
    }