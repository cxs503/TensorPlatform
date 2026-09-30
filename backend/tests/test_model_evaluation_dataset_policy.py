import io
import sys
import tempfile
import unittest
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from fastapi import HTTPException


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from api.model_evaluation import (  # noqa: E402
    EvaluationRequest,
    _EvaluationArtifact,
    _configured_feature_columns,
    _load_training_context,
    _load_serialized_model,
    _normalize_evaluation_task_type,
    _predict_for_evaluation,
    _preprocess,
    evaluate_model,
)


class ModelEvaluationDatasetPolicyTests(unittest.TestCase):
    def test_model_version_features_survive_empty_training_job_features(self):
        class Row:
            def __init__(self, value):
                self.value = value

            def fetchone(self):
                return (self.value,)

        class Db:
            def __init__(self):
                self.values = [
                    {"feature_columns": []},
                    {"feature_columns": ["feature_a", "feature_b"]},
                ]

            def execute(self, *_args, **_kwargs):
                return Row(self.values.pop(0))

        context = _load_training_context(Db(), 1)
        self.assertEqual(
            _configured_feature_columns(context), ["feature_a", "feature_b"],
        )

    def test_time_series_alias_is_canonical(self):
        self.assertEqual(
            _normalize_evaluation_task_type("time_series"),
            "time_series_forecasting",
        )

    def test_time_series_evaluation_preserves_chronological_order(self):
        frame = pd.DataFrame({
            "timestamp": ["2026-01-03", "2026-01-01", "2026-01-02"],
            "temperature": [30.0, 10.0, 20.0],
            "target": [3.0, 1.0, 2.0],
        })

        X, y, features = _preprocess(
            frame, "target", None, None, "time_series_forecasting",
        )

        self.assertEqual(features, ["temperature"])
        self.assertEqual(X[:, 0].tolist(), [10.0, 20.0, 30.0])
        self.assertEqual(y.tolist(), [1.0, 2.0, 3.0])

    def test_cross_validation_on_independent_test_dataset_is_rejected(self):
        request = EvaluationRequest(
            model_id=1,
            dataset_id=2,
            task_type="regression",
            cross_validation=True,
        )

        with self.assertRaises(HTTPException) as raised:
            evaluate_model(request, db=None)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("全量推理", str(raised.exception.detail))

    def test_custom_main_predictor_is_restored_and_dataframe_output_is_adapted(self):
        source = """
import pandas as pd

class CustomFramePredictor:
    def predict(self, frame):
        if '时间' not in frame.columns:
            raise ValueError('missing 时间')
        return pd.DataFrame({
            '机理预测': frame['feature'] * 0.5,
            'FeO最终预测': frame['feature'] * 2.0,
        })
"""
        namespace = {"__name__": "__main__"}
        exec(source, namespace, namespace)
        predictor_class = namespace["CustomFramePredictor"]
        main_module = sys.modules["__main__"]
        setattr(main_module, "CustomFramePredictor", predictor_class)
        buffer = io.BytesIO()
        try:
            joblib.dump(predictor_class(), buffer)
        finally:
            delattr(main_module, "CustomFramePredictor")

        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot.py"
            snapshot.write_text(source, encoding="utf-8")
            buffer.seek(0)
            predictor, restored = _load_serialized_model(buffer, str(snapshot))

        artifact = _EvaluationArtifact(
            predictor=predictor,
            training_config={"source_time_column": "特征时间"},
            prefers_dataframe=restored,
        )
        frame = pd.DataFrame({
            "特征时间": ["2026-01-01", "2026-01-02"],
            "feature": [1.0, 2.0],
            "生产过程238㎡生产过程FeO": [2.0, 4.0],
        })
        predictions = _predict_for_evaluation(
            artifact,
            np.asarray([[1.0], [2.0]]),
            frame,
            "生产过程238㎡生产过程FeO",
        )

        self.assertTrue(restored)
        self.assertEqual(predictions.tolist(), [2.0, 4.0])


if __name__ == "__main__":
    unittest.main()
