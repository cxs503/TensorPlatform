import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi import HTTPException
from sklearn.ensemble import RandomForestRegressor

from api.model_explain import SHAPExplainReq, _load_data, explain_with_shap
from services.model_explain_service import ModelExplainer


class WrappedRegressor:
    """Small predictor that represents a preprocessing/model wrapper."""

    def predict(self, values):
        array = np.asarray(values, dtype=float)
        return 2.0 * array[:, 0] - 0.5 * array[:, 1]


class FailedExplainer:
    SHAP_AVAILABLE = True

    def explain_with_shap(self, model, values, feature_names):
        return {
            "method": "SHAP",
            "error": "unsupported model",
            "message": "SHAP分析失败：unsupported model",
        }


class DatasetDB:
    def __init__(self, file_path, columns_info):
        self.row = (file_path, columns_info)

    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    def execute(self, *_args, **_kwargs):
        return self.Result(self.row)


class ModelExplainShapTest(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(42)
        self.values = rng.normal(size=(12, 2))
        self.names = ["gas_consumption", "oxygen_flow"]

    def test_non_tree_wrapper_uses_model_agnostic_shap(self):
        result = ModelExplainer().explain_with_shap(
            WrappedRegressor(), self.values.tolist(), self.names
        )

        self.assertNotIn("error", result)
        self.assertEqual(result["explainer_type"], "PermutationExplainer")
        self.assertEqual(len(result["feature_importance"]), 2)
        self.assertEqual(
            {item["feature"] for item in result["feature_importance"]},
            set(self.names),
        )

    def test_bare_tree_model_keeps_tree_explainer(self):
        target = 2.0 * self.values[:, 0] - self.values[:, 1]
        model = RandomForestRegressor(n_estimators=5, random_state=42).fit(
            self.values, target
        )

        result = ModelExplainer().explain_with_shap(
            model, self.values.tolist(), self.names
        )

        self.assertNotIn("error", result)
        self.assertEqual(result["explainer_type"], "TreeExplainer")
        self.assertEqual(len(result["feature_importance"]), 2)

    def test_permutation_importance_supports_predict_only_model(self):
        target = 2.0 * self.values[:, 0] - 0.5 * self.values[:, 1]
        result = ModelExplainer().get_permutation_importance(
            WrappedRegressor(),
            self.values.tolist(),
            target.tolist(),
            self.names,
            n_repeats=3,
            task_type="regression",
        )

        self.assertEqual(result["method"], "permutation_importance")
        self.assertEqual(result["metric"], "mse")
        self.assertEqual(len(result["results"]), 2)

    def test_data_loader_uses_saved_features_and_ignores_derived_column(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "engineered.csv"
            pd.DataFrame({
                "timestamp": ["2026-01-01", "2026-01-02"],
                "gas_consumption": [100.0, 110.0],
                "oxygen_flow": [450.0, 451.0],
                "temperature": [1200.0, 1210.0],
                "特征1": [1.0, 2.0],
            }).to_csv(path, index=False)
            db = DatasetDB(str(path), [
                {"name": "temperature", "dtype": "float64", "derived": False},
                {"name": "特征1", "dtype": "float64", "derived": True},
            ])

            X, y, names, target_column = _load_data(
                db,
                165,
                None,
                expected_feature_names=self.names,
                expected_feature_count=2,
            )

        self.assertEqual(names, self.names)
        self.assertEqual(target_column, "temperature")
        self.assertEqual(X.shape, (2, 2))
        self.assertEqual(y.tolist(), [1200.0, 1210.0])

    @patch("api.model_explain.get_model_explainer", return_value=FailedExplainer())
    @patch("api.model_explain._load_data")
    @patch("api.model_explain._load_model", return_value=WrappedRegressor())
    def test_api_raises_422_when_service_returns_error(
        self, _load_model, load_data, _get_explainer
    ):
        load_data.return_value = (
            self.values,
            np.zeros(len(self.values)),
            self.names,
            "temperature",
        )

        with self.assertRaises(HTTPException) as raised:
            explain_with_shap(
                SHAPExplainReq(model_id=349, dataset_id=161), db=object()
            )

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("unsupported model", raised.exception.detail["message"])


if __name__ == "__main__":
    unittest.main()
