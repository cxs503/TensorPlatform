"""Mission acceptance criteria lifecycle integration tests."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def test_criteria_survive_create_claim_start_complete_and_are_immutable(factory, monkeypatch):
    from api import tensor_missions

    monkeypatch.setattr(tensor_missions, "get_factory_profile", lambda: None)
    monkeypatch.setattr(tensor_missions, "validate_projects", lambda repo, co_repos=None: None)
    monkeypatch.setattr(tensor_missions, "validate_base_ref", lambda base_ref: None)
    monkeypatch.setattr(tensor_missions, "_repo_resolvable", lambda repo: True)

    criteria = [{"criterion_id": "relative-error", "metric": "relative_error",
                 "target": "<0.03", "tolerance": "0.001"}]
    client = factory["client"]
    created = client.post("/tensor/missions", headers=factory["headers"](2),
                          json={"goal": "Implement a verifiable numerical feature",
                                "repo": "TensorPlatform", "acceptance_criteria": criteria})
    assert created.status_code == 200, created.text
    mid = created.json()["id"]
    detail = client.get(f"/tensor/missions/{mid}", headers=factory["headers"](1))
    assert detail.status_code == 200, detail.text
    assert detail.json()["acceptance_criteria"] == criteria
    digest = detail.json()["acceptance_criteria_sha256"]
    assert len(digest) == 64
    assert detail.json()["acceptance_criteria_freeze_source"] == "mission_created"

    claimed = client.post("/tensor/missions/claim", headers=factory["headers"](3),
                          json={"agent_id": "acceptance-contract-test", "runner": "openhands"})
    assert claimed.status_code == 200, claimed.text
    mission = claimed.json()["mission"]
    assert mission["id"] == mid
    assert mission["acceptance_criteria"] == criteria
    assert mission["acceptance_criteria_sha256"] == digest

    started = client.post(f"/tensor/missions/{mid}/start",
                          headers=factory["headers"](3),
                          json={"claim_token": mission["claim_token"]})
    assert started.status_code == 200, started.text
    assert started.json()["acceptance_criteria_sha256"] == digest
    assert started.json()["acceptance_criteria_status"] == "declared"

    completed = client.post(
        f"/tensor/missions/{mid}/complete", headers=factory["headers"](3),
        json={"claim_token": mission["claim_token"], "status": "succeeded",
              "acceptance": True, "branch": "feat/acceptance-contract-test",
              "head_commit": "a" * 40,
              "summary": {"headline": "Contract lifecycle test",
                          "quality_gate": {"passed": True, "artifacts": []}}},
    )
    assert completed.status_code == 200, completed.text
    after = client.get(f"/tensor/missions/{mid}", headers=factory["headers"](1))
    evidence = after.json()["approval"]["evidence"]
    assert evidence["acceptance_criteria"] == criteria
    assert evidence["acceptance_criteria_sha256"] == digest
    assert evidence["acceptance_criteria_integrity"] == "verified"
    assert evidence["acceptance_criteria_status"] == "declared"

    with pytest.raises(DBAPIError):
        with factory["engine"].begin() as db:
            db.execute(text("UPDATE tensor_dev_missions SET acceptance_criteria = CAST(:v AS jsonb) WHERE id=:id"),
                       {"v": '[{"criterion_id":"relative-error","metric":"relative_error","target":"<0.30","tolerance":"0.001"}]',
                        "id": mid})
