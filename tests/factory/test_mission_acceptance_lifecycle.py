"""End-to-end API contract tests for immutable mission acceptance criteria."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def test_criteria_survive_create_claim_start_complete_and_cannot_be_mutated(factory, monkeypatch):
    from api import tensor_missions

    monkeypatch.setattr(tensor_missions, "get_factory_profile", lambda: None)
    monkeypatch.setattr(tensor_missions, "validate_projects", lambda repo, co_repos=None: None)
    monkeypatch.setattr(tensor_missions, "validate_base_ref", lambda base_ref: None)
    monkeypatch.setattr(tensor_missions, "_repo_resolvable", lambda repo: True)

    criteria = [{
        "criterion_id": "relative-error",
        "metric": "relative_error",
        "target": "<0.03",
        "tolerance": "0.001",
    }]
    client = factory["client"]
    created = client.post(
        "/tensor/missions",
        headers=factory["headers"](2),
        json={"goal": "Implement a verifiable numerical feature", "repo": "TensorPlatform",
              "acceptance_criteria": criteria},
    )
    assert created.status_code == 200, created.text
    mission_id = created.json()["id"]

    detail = client.get(f"/tensor/missions/{mission_id}", headers=factory["headers"](1))
    assert detail.status_code == 200, detail.text
    assert detail.json()["acceptance_criteria"] == criteria
    digest = detail.json()["acceptance_criteria_sha256"]
    assert len(digest) == 64
    assert detail.json()["acceptance_criteria_freeze_source"] == "mission_created"

    claimed = client.post(
        "/tensor/missions/claim",
        headers=factory["headers"](3),
        json={"agent_id": "acceptance-contract-test", "runner": "openhands"},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["claimed"] is True
    mission = claimed.json()["mission"]
    assert mission["id"] == mission_id
    assert mission["acceptance_criteria"] == criteria
    assert mission["acceptance_criteria_sha256"] == digest

    started = client.post(
        f"/tensor/missions/{mission_id}/start",
        headers=factory["headers"](3),
        json={"claim_token": mission["claim_token"]},
    )
    assert started.status_code == 200, started.text
    assert started.json()["acceptance_criteria_sha256"] == digest
    assert started.json()["acceptance_criteria_status"] == "declared"

    completed = client.post(
        f"/tensor/missions/{mission_id}/complete",
        headers=factory["headers"](3),
        json={
            "claim_token": mission["claim_token"],
            "status": "succeeded",
            "acceptance": True,
            "branch": "feat/acceptance-contract-test",
            "head_commit": "a" * 40,
            "summary": {
                "headline": "Contract persistence integration test",
                "quality_gate": {"passed": True, "artifacts": []},
            },
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "awaiting_acceptance"

    after = client.get(f"/tensor/missions/{mission_id}", headers=factory["headers"](1))
    evidence = after.json()["approval"]["evidence"]
    assert evidence["acceptance_criteria"] == criteria
    assert evidence["acceptance_criteria_sha256"] == digest
    assert evidence["acceptance_criteria_integrity"] == "verified"
    assert evidence["acceptance_criteria_status"] == "declared"

    with pytest.raises(DBAPIError):
        with factory["engine"].begin() as db:
            db.execute(
                text("UPDATE tensor_dev_missions SET acceptance_criteria = CAST(:criteria AS jsonb) WHERE id=:mid"),
                {"criteria": '[{"metric":"relative_error","target":"<0.30","tolerance":"0.001","criterion_id":"relative-error"}]',
                 "mid": mission_id},
            )
