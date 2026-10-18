"""Integration tests for the persistent research runner."""
import hashlib
import json
import sqlite3

import pytest

from api import research_factory as rf


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(rf, "DB_PATH", tmp_path / "research.sqlite3")
    monkeypatch.setattr(rf, "ARTIFACT_DIR", tmp_path / "artifacts")
    rf.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    rf.ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(rf.DB_PATH)) as db:
        db.execute("""CREATE TABLE research_runs (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, goal TEXT NOT NULL, solver TEXT NOT NULL,
          backend TEXT NOT NULL, status TEXT NOT NULL, verification TEXT NOT NULL, created_at TEXT NOT NULL,
          started_at TEXT, finished_at TEXT, parameters_json TEXT NOT NULL, result_json TEXT, error TEXT, artifact_dir TEXT)""")
    yield


def test_health_reports_real_runner_and_hpc_boundary():
    result = rf.health()
    assert result["status"] == "ready"
    assert result["runners"][0]["kind"] == "real-numerical-computation"
    assert result["hpc"]["connected"] is False


def test_create_execute_persist_and_download_artifacts(isolated_store):
    task = rf.create_task(rf.TaskCreate(
        title="test spring chain",
        goal="exercise numerical runner",
        parameters={"particles": 12, "steps": 300, "dt": 0.001},
    ))
    assert task["status"] == "queued"
    run = rf.run_task(task["id"])
    assert run["status"] == "completed"
    assert run["result"]["steps"] == 300
    assert run["result"]["sample_count"] > 1
    assert run["verification"] == "computed-not-scientifically-validated"
    assert rf.get_task(task["id"])["result"]["peak_displacement"] > 0
    root = rf.ARTIFACT_DIR / task["id"]
    assert (root / "timeseries.csv").is_file()
    manifest_path = root / "run-manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    assert manifest["run_id"] == task["id"]
    assert manifest["schema_version"] == 1
    assert manifest["status"] == "succeeded"
    assert manifest["validation_status"] == "not_run"
    assert manifest["validation"]["status"] == "not_run"
    assert manifest["outputs"][0]["role"] == "timeseries"
    csv_path = root / "timeseries.csv"
    csv_bytes = csv_path.read_bytes()
    csv_output = manifest["outputs"][0]
    assert csv_output["checksum_sha256"] == hashlib.sha256(csv_bytes).hexdigest()
    assert csv_output["size_bytes"] == len(csv_bytes)
    artifacts = {item["name"]: item for item in run["result"]["artifacts"]}
    assert artifacts["timeseries.csv"]["sha256"] == hashlib.sha256(csv_bytes).hexdigest()
    assert artifacts["timeseries.csv"]["size_bytes"] == len(csv_bytes)
    assert artifacts["run-manifest.json"]["sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert artifacts["run-manifest.json"]["size_bytes"] == len(manifest_bytes)
    assert len(manifest["configuration_sha256"]) == 64


def test_parameter_bounds_are_rejected(isolated_store):
    with pytest.raises(Exception) as exc:
        rf.create_task(rf.TaskCreate(title="invalid step", parameters={"dt": 0.5}))
    assert "dt" in str(exc.value)


def test_tensor_solver_is_not_misrepresented_as_available(isolated_store):
    with pytest.raises(Exception):
        rf.TaskCreate(title="fake dem run", solver="TensorDEM")
