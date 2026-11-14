from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.mission_acceptance import (
    AcceptanceCriteriaError,
    acceptance_criteria_sha256,
    canonicalize_acceptance_criteria,
    verify_frozen_acceptance_criteria,
)


def test_canonical_ids_and_digest_are_deterministic():
    criteria = [
        {"metric": "relative_error", "target": "<0.03", "tolerance": "0.001"},
        {"criterion_id": "mass-balance", "metric": "mass", "target": 1.0, "tolerance": 1e-8},
    ]
    normalized = canonicalize_acceptance_criteria(criteria)
    assert [item["criterion_id"] for item in normalized] == ["C001", "mass-balance"]
    assert acceptance_criteria_sha256(criteria) == acceptance_criteria_sha256(normalized)


@pytest.mark.parametrize("bad", [
    "not-an-array",
    ["not-an-object"],
    [{"metric": "", "target": 1, "tolerance": 0}],
    [{"metric": "m", "target": None, "tolerance": 0}],
    [{"metric": "m", "target": 1, "tolerance": ""}],
    [
        {"criterion_id": "same", "metric": "a", "target": 1, "tolerance": 0},
        {"criterion_id": "same", "metric": "b", "target": 2, "tolerance": 0},
    ],
])
def test_rejects_malformed_criteria(bad):
    with pytest.raises(AcceptanceCriteriaError):
        canonicalize_acceptance_criteria(bad)


def test_empty_criteria_are_explicitly_supported_for_legacy_work():
    assert canonicalize_acceptance_criteria(None) == []
    assert acceptance_criteria_sha256([]) == hashlib.sha256(b"[]").hexdigest()


def test_fingerprint_detects_post_freeze_mutation():
    criteria = [{"metric": "error", "target": "<0.01", "tolerance": "0.001"}]
    digest = acceptance_criteria_sha256(criteria)
    assert verify_frozen_acceptance_criteria(criteria, digest)
    changed = [{"metric": "error", "target": "<0.10", "tolerance": "0.001"}]
    assert not verify_frozen_acceptance_criteria(changed, digest)


def test_digest_is_independent_of_object_key_order():
    a = [{"metric": "error", "target": "<0.01", "tolerance": "0.001"}]
    b = [{"tolerance": "0.001", "target": "<0.01", "metric": "error"}]
    assert acceptance_criteria_sha256(a) == acceptance_criteria_sha256(b)
