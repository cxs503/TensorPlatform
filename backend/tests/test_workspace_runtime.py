import json
import sys

import pytest

from services import workspace_runtime
from services.workspace_runtime import (
    WorkspaceRuntimeError,
    analyze_run_output,
    inspect_workspace_inputs,
    list_runtimes,
    run_workspace_snapshot,
    safe_relative_path,
)


@pytest.fixture(autouse=True)
def use_current_python_as_managed_runtime(monkeypatch):
    runtime = {
        "id": 1002,
        "name": "test managed Python",
        "python_path": sys.executable,
        "actual_python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "available": True,
        "mode": "managed_python",
        "base_environment_id": 3,
        "dependency_set_id": 1,
        "runtime_digest": "sha256:test",
        "compute_type": "cpu",
        "cuda_version": None,
    }
    monkeypatch.setattr(workspace_runtime, "get_runtime_build", lambda environment_id: runtime)
    return runtime


def test_safe_relative_path_rejects_traversal_and_absolute_paths():
    assert safe_relative_path("project/train.py").as_posix() == "project/train.py"
    with pytest.raises(WorkspaceRuntimeError):
        safe_relative_path("../secret.py")
    with pytest.raises(WorkspaceRuntimeError):
        safe_relative_path("/app/secret.py")


def test_environment_list_comes_from_built_managed_runtimes(monkeypatch):
    managed = [
        {"id": 1001, "name": "Python 3.8", "available": True, "actual_python_version": "3.8.20"},
        {"id": 1002, "name": "Python 3.11", "available": True, "actual_python_version": "3.11.15"},
    ]
    monkeypatch.setattr(workspace_runtime, "list_runtime_builds", lambda: managed)

    runtimes = list_runtimes()

    assert runtimes == managed


def test_run_uses_unsaved_snapshot_without_overwriting_source_or_leaking_secrets(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "main.py"
    source.write_text("print('saved')\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "must-not-leak")

    response = run_workspace_snapshot(
        workspace=workspace,
        entry_path="main.py",
        files={"main.py": "import os\nprint('edited', os.getenv('DATABASE_URL', 'hidden'))\n"},
        code="import os\nprint('edited', os.getenv('DATABASE_URL', 'hidden'))\n",
        environment_id=1002,
    )

    assert response["status"] == "success"
    assert response["output"].strip() == "edited hidden"
    assert source.read_text(encoding="utf-8") == "print('saved')\n"
    assert not list(workspace.glob("tmp*.py"))


def test_run_can_import_numpy_with_sandbox_process_limits(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    response = run_workspace_snapshot(
        workspace=workspace,
        entry_path="main.py",
        files={"main.py": "import numpy as np\nprint(np.array([1, 2, 3]).sum())\n"},
        code="import numpy as np\nprint(np.array([1, 2, 3]).sum())\n",
        environment_id=1002,
    )
    assert response["status"] == "success"
    assert response["output"].strip() == "6"


def test_preflight_requires_dataset_for_external_input_contract(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code = "import os\nprint(os.getenv('ODF_DATASET_PATH'), os.getenv('ODF_HYPERPARAMS_PATH'))\n"

    result = inspect_workspace_inputs(workspace, "main.py", {"main.py": code}, code)

    assert result["needs_inputs"] is True
    assert result["dataset"]["specified"] is True
    assert result["dataset"]["required"] is True
    assert result["parameters"]["required"] is False


def test_preflight_accepts_existing_literal_files(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "data.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    code = "import pandas as pd, json\npd.read_csv('data.csv')\njson.load(open('params.json'))\n"

    result = inspect_workspace_inputs(workspace, "main.py", {"main.py": code}, code)

    assert result["needs_inputs"] is False
    assert result["dataset"]["found"] is True


def test_preflight_requires_dataset_when_code_declares_no_inputs(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code = "print('utility script')\n"

    result = inspect_workspace_inputs(workspace, "main.py", {"main.py": code}, code)

    assert result["needs_inputs"] is True
    assert result["dataset"]["specified"] is False


def test_run_injects_one_time_dataset_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code = """import argparse, os
parser = argparse.ArgumentParser()
parser.add_argument('--data')
args = parser.parse_args()
print(os.path.basename(args.data), open(args.data).read().strip())
print(os.getenv('ODF_DATASET_PATH') == args.data)
"""

    response = run_workspace_snapshot(
        workspace=workspace,
        entry_path="main.py",
        files={"main.py": code},
        code=code,
        environment_id=1002,
        input_files={
            "dataset": {"filename": "input.csv", "content": b"x,y\\n1,2\\n"},
        },
    )

    assert response["status"] == "success"
    assert "dataset_input.csv" in response["output"]
    assert response["output"].strip().endswith("True")
    assert response["inputs"]["dataset"]["size_bytes"] > 0


def test_run_injects_optional_hyperparameter_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code = """import argparse, json, os
parser = argparse.ArgumentParser()
parser.add_argument('--params')
args = parser.parse_args()
print(os.getenv('ODF_HYPERPARAMS_PATH') == args.params)
print(json.load(open(args.params))['epochs'])
"""

    response = run_workspace_snapshot(
        workspace=workspace,
        entry_path="main.py",
        files={"main.py": code},
        code=code,
        environment_id=1002,
        input_files={
            "hyperparameters": {"filename": "params.json", "content": b'{"epochs": 12}'},
        },
    )

    assert response["status"] == "success"
    assert response["output"].strip().splitlines() == ["True", "12"]
    assert response["inputs"]["hyperparameters"]["size_bytes"] > 0


def test_run_uses_run_scoped_persistent_model_output(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output_dir = tmp_path / "run-output"
    output_dir.mkdir()
    code = '''import os
with open(os.environ["ODF_MODEL_OUT_PATH"], "wb") as handle:
    handle.write(b"this-run-only")
print(os.environ["ODF_MODEL_OUT_PATH"])
'''

    response = run_workspace_snapshot(
        workspace=workspace,
        entry_path="main.py",
        files={"main.py": code},
        code=code,
        environment_id=1002,
        stdout_dir=str(output_dir),
    )

    assert response["status"] == "success"
    assert (output_dir / "model.pkl").read_bytes() == b"this-run-only"
    assert str(output_dir / "model.pkl") in response["output"]


def test_run_analysis_extracts_metrics_and_epoch_series():
    output = """Epoch [1/3] - Loss: 0.91
Epoch [2/3] - Loss: 0.42
Epoch [3/3] - Loss: 0.18
R² (决定系数): 0.8765
RMSE (均方根误差): 12.34
MAE (平均绝对误差): 8.9
"""

    result = analyze_run_output(
        output, "", duration_ms=1234.567, returncode=0, status="success"
    )

    assert result["duration_ms"] == 1234.57
    assert result["metrics"] == {
        "loss": 0.18, "r2": 0.8765, "rmse": 12.34, "mae": 8.9,
    }
    assert [point["epoch"] for point in result["series"]] == [1, 2, 3]


def test_run_reads_structured_prediction_result(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code = '''
import json, os
with open(os.environ["ODF_RESULT_PATH"], "w", encoding="utf-8") as handle:
    json.dump({
        "task_type": "regression",
        "metrics": {"r2": 0.95},
        "y_true": [1, 2, 3, 4],
        "y_pred": [1.1, 1.8, 3.2, 3.9],
        "feature_importance": {"temperature": 0.7, "pressure": 0.3},
    }, handle)
print("done")
'''

    response = run_workspace_snapshot(
        workspace=workspace,
        entry_path="main.py",
        files={"main.py": code},
        code=code,
        environment_id=1002,
    )

    analysis = response["analysis"]
    assert analysis["result_contract"] == "foundation_result_v1"
    assert analysis["metrics"]["r2"] == 0.95
    assert len(analysis["visualization"]["prediction_trend"]) == 4
    assert analysis["visualization"]["feature_importance"]["labels"] == ["temperature", "pressure"]
