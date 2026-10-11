import subprocess
import sys

import numpy as np
import pytest

from api.deployment import _load_sandbox_model_subprocess
from api.workspace import _normalize_training_name, _save_model_to_registry
from services.sandbox_model_artifact import (
    SandboxModelArtifactError,
    inspect_sandbox_artifact,
    runtime_source,
)


SOURCE = '''import numpy as np
class CustomPredictor:
    def __init__(self):
        self.n_features_in_ = 2
        self.feature_columns = ["temperature", "pressure"]
    def predict(self, values):
        return np.asarray(values)[:, 0]

if __name__ == "__main__":
    raise RuntimeError("training code must not run while restoring the artifact")
'''


def test_artifact_is_restored_with_its_source_and_runtime(tmp_path):
    artifact = tmp_path / "model.pkl"
    creator = tmp_path / "create.py"
    creator.write_text(
        runtime_source(SOURCE) + f'\nimport joblib\njoblib.dump(CustomPredictor(), {str(artifact)!r})\n',
        encoding="utf-8",
    )
    subprocess.run([sys.executable, str(creator)], check=True)

    metadata = inspect_sandbox_artifact(artifact, SOURCE, sys.executable)

    assert metadata == {
        "model_type": "CustomPredictor",
        "n_features_in": 2,
        "feature_columns": ["temperature", "pressure"],
    }
    assert "training code must not run" not in runtime_source(SOURCE)

    wrapper = _load_sandbox_model_subprocess(
        123, artifact.read_bytes(), SOURCE, None, sys.executable,
        n_features_in=2, feature_columns=["temperature", "pressure"],
    )
    prediction = wrapper.predict([[8.5, 2.1]])
    assert prediction.tolist() == [8.5]
    assert wrapper.n_features_in_ == 2


def test_dataframe_predictor_receives_named_features_and_synthetic_time(tmp_path):
    source = '''import numpy as np
import pandas as pd
class FramePredictor:
    def __init__(self):
        self.n_features_in_ = 2
        self.feature_columns = ["temperature", "pressure"]
    def predict(self, df_new):
        timestamps = pd.to_datetime(df_new["时间"])
        intermediate = df_new["temperature"].to_numpy()
        return pd.DataFrame({
            "时间": timestamps,
            "中间预测": intermediate,
            "最终预测": intermediate + timestamps.notna().astype(float),
        })
'''
    artifact = tmp_path / "frame_model.pkl"
    creator = tmp_path / "create_frame.py"
    creator.write_text(
        runtime_source(source) + f'\nimport joblib\njoblib.dump(FramePredictor(), {str(artifact)!r})\n',
        encoding="utf-8",
    )
    subprocess.run([sys.executable, str(creator)], check=True)

    wrapper = _load_sandbox_model_subprocess(
        124, artifact.read_bytes(), source, None, sys.executable,
        n_features_in=2, feature_columns=["temperature", "pressure"],
    )

    prediction = wrapper.predict([[8.5, 2.1]])

    assert prediction.tolist() == [9.5]


def test_registry_rejects_missing_run_artifact_instead_of_using_shared_tmp(tmp_path):
    with pytest.raises(SandboxModelArtifactError, match="没有生成模型制品"):
        _save_model_to_registry(
            "run-1", str(tmp_path), {"analysis": {}},
            {"organization_id": 1}, "train.py", SOURCE, environment_id=1002,
        )


def test_training_name_is_editable_with_a_safe_filename_default():
    assert _normalize_training_name(" 工艺参数优化优化训练 ", "projects/train.py") == "工艺参数优化优化训练"
    assert _normalize_training_name("", "projects/train.py") == "train-训练"

    with pytest.raises(Exception, match="不能超过128"):
        _normalize_training_name("x" * 129, "train.py")
