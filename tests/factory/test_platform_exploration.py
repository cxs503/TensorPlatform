import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("platform_evidence", ROOT / "tools/factory/platform_evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def test_source_reader_limits_scope_and_redacts_credentials(tmp_path):
    source = tmp_path / "backend/services/data_quality.py"
    source.parent.mkdir(parents=True)
    source.write_text('def check_data():\n    password = "do-not-send"\n    return 42\n')
    result = evidence.read_source(tmp_path, "backend/services/data_quality.py", 1, 3)
    assert "do-not-send" not in result["content"]
    assert "return 42" in result["content"]
    assert len(result["sha256"]) == 64
    with pytest.raises(ValueError):
        evidence.read_source(tmp_path, ".env")
    with pytest.raises(ValueError):
        evidence.read_source(tmp_path, "backend/services/data_quality.py", 1, 500)
    escape = source.parent / "outside.py"
    escape.symlink_to(ROOT / "backend/factory_app.py")
    with pytest.raises(ValueError):
        evidence.read_source(tmp_path, "backend/services/outside.py")


def test_report_rejects_invented_or_unobserved_citations():
    items = [{"id": "E1", "kind": "source"}]
    finding = {"title": "Source observation", "description": "Not a runtime test", "verification": "source-only", "evidence_ids": ["E1"]}
    report = {"summary": "Review", "findings": [dict(finding) for _ in range(3)], "recommended_first_workflow": "Read-only audit", "next_steps": [], "limitations": []}
    evidence.validate_report(report, items)
    report["findings"][0]["evidence_ids"] = ["invented"]
    with pytest.raises(ValueError):
        evidence.validate_report(report, items)
    report["findings"][0].update(evidence_ids=["E1"], verification="http-observed")
    with pytest.raises(ValueError):
        evidence.validate_report(report, items)


def test_targeted_claim_preserves_other_tasks_and_tenant_scope(factory):
    client, headers = factory["client"], factory["headers"]

    def create():
        response = client.post("/api/v1/tensor/missions", headers=headers(2), json={
            "goal": "Read-only exploration of the selected platform", "repo": "factory-project", "runner": "native", "model": "test-model"})
        assert response.status_code == 200
        return response.json()["id"]

    first, second = create(), create()
    body = {"agent_id": "explorer", "runner": "native", "model": "test-model", "mission_id": second}
    assert not client.post("/api/v1/tensor/missions/claim", headers=headers(6), json=body).json()["claimed"]
    claim = client.post("/api/v1/tensor/missions/claim", headers=headers(3), json=body)
    assert claim.status_code == 200
    assert claim.json()["mission"]["id"] == second
    assert client.get(f"/api/v1/tensor/missions/{first}", headers=headers(2)).json()["status"] == "queued"
