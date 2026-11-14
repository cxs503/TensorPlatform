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
    [None],
    ["   "],
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


def test_mission_api_persists_and_carries_frozen_criteria_end_to_end():
    api = (ROOT / "backend" / "api" / "tensor_missions.py").read_text(encoding="utf-8")
    assert "CAST(:criteria AS jsonb), :criteria_sha256, now(), 'mission_created'" in api
    assert 'criteria = row["acceptance_criteria"] or []' in api
    assert '"acceptance_criteria": criteria' in api
    assert "verify_frozen_acceptance_criteria(criteria, digest)" in api
    assert '"acceptance_criteria": criteria' in api
    assert '"acceptance_criteria_integrity": criteria_integrity' in api


def test_non_finite_criterion_values_are_rejected():
    with pytest.raises(AcceptanceCriteriaError):
        acceptance_criteria_sha256([{
            "metric": "error", "target": float("nan"), "tolerance": 0.01,
        }])


def test_qualitative_criteria_strings_are_canonicalized_for_campaign_tasks():
    criteria = canonicalize_acceptance_criteria([
        "Deliver a reproducible implementation with a regression test."
    ])
    assert criteria == [{
        "statement": "Deliver a reproducible implementation with a regression test.",
        "requires_code": True,
        "requires_test": True,
        "criterion_id": "C001",
    }]
    assert len(acceptance_criteria_sha256(criteria)) == 64


def test_campaign_dispatch_persists_task_criteria_at_creation():
    strategy_api = (ROOT / "backend" / "api" / "factory_strategy.py").read_text(encoding="utf-8")
    assert '"requires_code": task.execution_mode == "python-package"' in strategy_api
    assert "canonicalize_acceptance_criteria([" in strategy_api
    assert "acceptance_criteria_freeze_source" in strategy_api
    assert "'mission_created'" in strategy_api
