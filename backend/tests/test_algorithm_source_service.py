from models.core import AlgorithmModel
from services import algorithm_source_service as source_service
from fastapi import HTTPException
import pytest


class FakeDB:
    def commit(self):
        pass

    def refresh(self, model):
        pass


def test_python_artifact_import_and_overwrite_updates_real_file(tmp_path, monkeypatch):
    artifact_root = tmp_path / "artifacts"
    source_file = artifact_root / "1" / "train.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("print('original')\n", encoding="utf-8")
    monkeypatch.setattr(source_service, "ALGORITHM_ARTIFACT_DIR", artifact_root)
    model = AlgorithmModel(
        id=1,
        name="Python algorithm",
        algorithm="random_forest",
        framework="sklearn",
        metrics={
            "artifact_kind": "algorithm_source",
            "artifact_format": "python",
            "artifact_path": str(source_file),
            "source_filename": "train.py",
        },
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    imported = source_service.import_to_workspace(model, workspace)
    result = source_service.overwrite_algorithm_source(
        FakeDB(), model, workspace, imported["project_path"], "train.py",
        "print('edited')\n", imported["revision"],
    )

    assert imported["source_mode"] == "python"
    assert source_file.read_text(encoding="utf-8") == "print('edited')\n"
    assert result["has_original_source"] is True
    assert model.metrics["source_code"] == "print('edited')\n"


def test_inline_source_import_and_overwrite_stays_inline(tmp_path, monkeypatch):
    monkeypatch.setattr(source_service, "ALGORITHM_ARTIFACT_DIR", tmp_path / "artifacts")
    model = AlgorithmModel(
        id=8, name="Inline", algorithm="python", framework="Python",
        metrics={"code": "print('inline')\n"},
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    imported = source_service.import_to_workspace(model, workspace)
    result = source_service.overwrite_algorithm_source(
        FakeDB(), model, workspace, imported["project_path"], "main.py",
        "print('updated inline')\n", imported["revision"],
    )

    assert imported["source_mode"] == "inline"
    assert model.metrics["code"] == "print('updated inline')\n"
    assert result["source_mode"] == "inline"


def test_builtin_algorithm_stays_in_automl_and_cannot_enter_sandbox(tmp_path, monkeypatch):
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setattr(source_service, "ALGORITHM_ARTIFACT_DIR", artifact_root)
    model = AlgorithmModel(
        id=18, name="Random Forest", algorithm="random_forest", framework="sklearn",
        metrics={"asset_type": "builtin", "builtin_key": "random_forest"},
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    summary = source_service.algorithm_summary(model)

    assert summary["asset_type"] == "builtin"
    assert summary["sandbox_policy"] == "unavailable"
    with pytest.raises(HTTPException) as exc:
        source_service.import_to_workspace(model, workspace)
    assert exc.value.status_code == 400
    assert "内置算法" in exc.value.detail


def test_trainable_pkl_stays_in_store_and_cannot_enter_sandbox(tmp_path, monkeypatch):
    artifact_root = tmp_path / "artifacts"
    pkl_file = artifact_root / "25" / "model.pkl"
    pkl_file.parent.mkdir(parents=True)
    pkl_file.write_bytes(b"trusted-pkl-placeholder")
    monkeypatch.setattr(source_service, "ALGORITHM_ARTIFACT_DIR", artifact_root)
    model = AlgorithmModel(
        id=25, name="MLP fine tune", algorithm="mlp", framework="pytorch",
        metrics={
            "artifact_kind": "model_artifact", "artifact_format": "pkl",
            "artifact_path": str(pkl_file), "source_filename": "model.pkl",
            "artifact_trainable": True,
        },
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    summary = source_service.algorithm_summary(model)

    assert summary["asset_type"] == "finetune_model"
    assert summary["sandbox_policy"] == "unavailable"
    assert summary["downloadable"] is True
    with pytest.raises(HTTPException) as exc:
        source_service.import_to_workspace(model, workspace)
    assert exc.value.status_code == 400
    assert "算法商店" in exc.value.detail
    assert pkl_file.read_bytes() == b"trusted-pkl-placeholder"


def test_unsupported_historical_artifact_is_not_importable(tmp_path, monkeypatch):
    monkeypatch.setattr(source_service, "ALGORITHM_ARTIFACT_DIR", tmp_path / "artifacts")
    model = AlgorithmModel(id=30, name="Old ONNX", metrics={"artifact_format": "onnx"})
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(HTTPException) as exc:
        source_service.import_to_workspace(model, workspace)
    assert exc.value.status_code == 400


def test_docker_bundle_is_downloadable_but_not_editable():
    model = AlgorithmModel(
        id=44,
        name="Docker bundle",
        algorithm="docker_bundle",
        framework="docker",
        metrics={
            "artifact_kind": "deployment_bundle",
            "artifact_format": "docker_bundle",
            "source_filename": "model.tar.gz",
        },
    )

    summary = source_service.algorithm_summary(model)

    assert summary["asset_type"] == "deployment_bundle"
    assert summary["asset_type_label"] == "Docker部署包"
    assert summary["sandbox_policy"] == "unavailable"
    assert summary["downloadable"] is True


def test_python_training_contract_uses_source_declared_parameters():
    source = '''
import json
from sklearn.preprocessing import StandardScaler
import torch.nn as nn

TARGET = "temperature"
TIME_COL = "timestamp"
RAW_FEATURE_COLS = ["pressure", "speed"]

class Network(nn.Module):
    def __init__(self, input_dim, hidden_units=[64, 32]):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(input_dim, hidden_units[0]),
            nn.ReLU(),
            nn.BatchNorm1d(hidden_units[0]),
            nn.Linear(hidden_units[0], 1),
        )

def train_mlp_model(data, hyperparams_json='{"epochs": 10, "batch_size": 32, "learning_rate": 0.001}'):
    params = json.loads(hyperparams_json)
    hidden_units = params.get("hidden_units", [64, 32])
    scaler_x = StandardScaler()
    scaler_y = StandardScaler()
'''

    contract = source_service.extract_python_training_contract(source)

    assert contract["entry_function"] == "train_mlp_model"
    assert contract["declared_defaults"] == {
        "epochs": 10,
        "batch_size": 32,
        "learning_rate": 0.001,
        "hidden_units": [64, 32],
    }
    assert contract["defaults"] == {
        "epochs": 10,
        "batch_size": 32,
        "learning_rate": 0.001,
        "hidden_layers": [64, 32],
        "dropout": 0.0,
        "batch_norm": True,
        "normalize": True,
        "normalize_target": True,
    }
    assert set(contract["schema"]["properties"]) == {
        "epochs", "batch_size", "learning_rate", "hidden_units",
    }
    assert contract["execution_mode"] == "execute_user_script"
    assert contract["data_contract"] == {
        "mode": "full_dataset",
        "target_column": "temperature",
        "time_column": "timestamp",
        "feature_columns": ["pressure", "speed"],
        "detected": True,
    }
