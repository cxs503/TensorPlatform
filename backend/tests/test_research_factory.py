"""Integration tests for the persistent research runner."""
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

# Load this isolated router module without importing api/__init__.py, which
# eagerly imports the entire platform application and unrelated dependencies.
MODULE_PATH = Path(__file__).resolve().parents[1] / "api" / "research_factory.py"
MODULE_SPEC = importlib.util.spec_from_file_location("research_factory_under_test", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
rf = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(rf)


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

def test_asset_search_finds_persisted_run_and_reports_optional_provider(isolated_store, monkeypatch):
    with sqlite3.connect(str(rf.DB_PATH)) as db:
        db.execute(
            """INSERT INTO research_runs
            (id,title,goal,solver,backend,status,verification,created_at,parameters_json,result_json,error,artifact_dir)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("RF-TEST-SEARCH", "Icebreaker DEM benchmark", "Compare bonded particle fracture and resistance",
             "SpringChainBenchmark", "local-cpu", "completed", "not-validated",
             "2026-10-18T00:00:00+00:00", '{"particles":24}',
             '{"benchmark":"icebreaking-probe","artifacts":[]}', None, None)
        )
    monkeypatch.delenv("XERJ_BASE_URL", raising=False)
    monkeypatch.delenv("XERJ_INDEX", raising=False)
    result = rf.search_research_assets(q="bonded particle fracture", limit=10)
    assert result["total"] == 1
    assert result["items"][0]["id"] == "RF-TEST-SEARCH"
    assert result["items"][0]["source"] == "research_factory"
    assert result["sources"]["local_runs"]["status"] == "available"
    assert result["sources"]["xerj"]["status"] == "not_configured"


def test_asset_search_queries_configured_xerj_and_keeps_local_results(isolated_store, monkeypatch):
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self):
            return json.dumps({"result":{"total":1,"hits":[
                {"_id":"doc-1","_score":0.91,"_source":{"title":"LBM droplet benchmark","content":"Laplace pressure validation","path":"benchmarks/laplace.md"}}
            ]}}).encode("utf-8")
    def fake_urlopen(request, timeout):
        assert request.full_url == "http://127.0.0.1:8080/v1/indices/research/search"
        assert json.loads(request.data)["q"] == "Laplace pressure"
        assert request.headers.get("Authorization") == "ApiKey test-secret"
        assert timeout == 3.0
        return FakeResponse()
    monkeypatch.setenv("XERJ_BASE_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("XERJ_INDEX", "research")
    monkeypatch.setenv("XERJ_API_KEY", "test-secret")
    monkeypatch.setattr(rf, "urlopen", fake_urlopen)
    result = rf.search_research_assets(q="Laplace pressure", limit=5)
    assert result["sources"]["xerj"]["status"] == "available"
    assert result["items"][0]["source"] == "xerj"
    assert result["items"][0]["title"] == "LBM droplet benchmark"
