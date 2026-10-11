import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.pipeline_node_executor import (  # noqa: E402
    AuthorizedDataset,
    AuthorizedModel,
    PipelineNodeExecutionError,
    PipelineNodeExecutor,
    VerifiedResourceAccess,
)
from services.real_training_service import (  # noqa: E402
    MAX_BATCH_SIZE,
    MAX_TRAINING_ROWS,
    PyTorchTrainer,
    TORCH_AVAILABLE,
    validate_training_config,
    validate_training_data,
)


class TrainingSecurityLimitTests(unittest.TestCase):
    def test_rejects_unknown_or_unbounded_training_parameters(self):
        with self.assertRaisesRegex(ValueError, "未允许参数"):
            validate_training_config({"model_type": "random_forest", "task_type": "regression", "callbacks": "evil"})
        with self.assertRaisesRegex(ValueError, "batch_size"):
            validate_training_config({"model_type": "random_forest", "task_type": "regression", "batch_size": MAX_BATCH_SIZE + 1})

    def test_rejects_invalid_data_shape_and_non_finite_values(self):
        with self.assertRaisesRegex(ValueError, "形状"):
            validate_training_data({"X": [[1.0, 2.0]], "y": [1]})
        with self.assertRaisesRegex(ValueError, "非有限"):
            validate_training_data({"X": [[1.0, float("nan")]] * 5, "y": [1] * 5})
        self.assertGreater(MAX_TRAINING_ROWS, 10)

    @unittest.skipUnless(TORCH_AVAILABLE, "PyTorch unavailable")
    def test_source_contract_mlp_has_no_unrelated_tree_parameters(self):
        config = validate_training_config({
            "name": "algorithm-11",
            "model_type": "mlp",
            "task_type": "regression",
            "epochs": 10,
            "batch_size": 32,
            "learning_rate": 0.001,
            "hidden_layers": [64, 32],
            "dropout": 0.0,
            "batch_norm": True,
            "normalize": True,
            "normalize_target": True,
            "hyperparameter_source": "default",
            "source_execution_mode": "source_contract_adapter",
            "source_entry_function": "train_mlp_model",
        })

        self.assertNotIn("n_estimators", config)
        self.assertNotIn("max_depth", config)
        model = PyTorchTrainer.create_model(4, 1, config)
        self.assertEqual(
            [layer.__class__.__name__ for layer in model],
            ["Linear", "ReLU", "BatchNorm1d", "Linear", "ReLU", "BatchNorm1d", "Linear"],
        )

    def test_evaluation_rejects_untrusted_or_tampered_joblib(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "data.csv"
            frame = pd.DataFrame({"x": range(10), "target": range(10)})
            frame.to_csv(csv_path, index=False)
            buffer = io.BytesIO()
            joblib.dump(LinearRegression().fit(frame[["x"]], frame["target"]), buffer)
            raw = buffer.getvalue()
            dataset = AuthorizedDataset(1, 1, str(csv_path), row_count=10)

            def execute(model):
                access = VerifiedResourceAccess(1, datasets={1: dataset}, models={2: model})
                return PipelineNodeExecutor(access, dataset_roots=[root], model_roots=[root]).execute(
                    "model_evaluate", {"dataset_id": 1, "model_id": 2, "target_column": "target", "feature_columns": ["x"]}
                )

            with self.assertRaisesRegex(PipelineNodeExecutionError, "受信任"):
                execute(AuthorizedModel(2, 1, model_blob=raw, model_type="regression"))
            with self.assertRaisesRegex(PipelineNodeExecutionError, "SHA-256 校验失败"):
                execute(AuthorizedModel(2, 1, model_blob=raw + b"x", model_type="regression", artifact_source="real_training", artifact_sha256=hashlib.sha256(raw).hexdigest()))
            result = execute(AuthorizedModel(2, 1, model_blob=raw, model_type="regression", artifact_source="real_training", artifact_sha256=hashlib.sha256(raw).hexdigest()))
            self.assertIn("R2", result["metrics"])


if __name__ == "__main__":
    unittest.main()
