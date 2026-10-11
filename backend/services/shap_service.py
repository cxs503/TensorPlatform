"""
SHAP 模型解释性服务
生成特征重要性、依赖图、瀑布图数据
"""
import json
import pickle
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

import shap
import matplotlib
matplotlib.use("Agg")


class SHAPExplainer:
    """SHAP 模型解释器"""

    EXPLANATIONS_DIR = "/app/uploads/shap_explanations"

    def __init__(self):
        Path(self.EXPLANATIONS_DIR).mkdir(parents=True, exist_ok=True)

    def explain_model(self, model_path: str, X_sample: np.ndarray,
                      feature_names: list = None, max_display: int = 15):
        """计算 SHAP 值并返回特征重要性"""
        if not Path(model_path).exists():
            return {"error": f"Model not found: {model_path}"}

        with open(model_path, "rb") as f:
            model = pickle.load(f)

        X_sample = np.asarray(X_sample)
        if len(X_sample) > 100:
            X_sample = X_sample[:100]  # Limit for performance

        try:
            # Tree-based models
            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(X_sample)

            if isinstance(shap_values, list):
                # Classification: multi-class output
                shap_values = shap_values[0]

            # Mean absolute SHAP values per feature
            mean_shap = np.abs(shap_values).mean(axis=0).tolist()

            # Build feature importance
            features = []
            for i, val in enumerate(mean_shap):
                name = feature_names[i] if feature_names and i < len(feature_names) else f"feature_{i}"
                features.append({
                    "feature": name,
                    "importance": round(float(val), 6),
                    "rank": i + 1,
                })

            features.sort(key=lambda x: x["importance"], reverse=True)
            for idx, f in enumerate(features):
                f["rank"] = idx + 1

            # Save
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            result = {
                "model_path": model_path,
                "n_samples": len(X_sample),
                "n_features": len(features),
                "top_features": features[:max_display],
                "all_features": features,
                "timestamp": timestamp,
            }
            json_path = Path(self.EXPLANATIONS_DIR) / f"shap_{timestamp}.json"
            json_path.write_text(json.dumps(result, ensure_ascii=False))

            return result

        except Exception as e:
            # Fallback to KernelExplainer for non-tree models
            try:
                background = X_sample[: min(20, len(X_sample))]
                explainer = shap.KernelExplainer(model.predict, background)
                shap_values = explainer.shap_values(X_sample[:10])

                return {
                    "method": "kernel",
                    "model_path": model_path,
                    "n_samples": len(X_sample),
                    "warning": f"Used KernelExplainer (slow). TreeExplainer error: {str(e)[:100]}",
                }
            except Exception as e2:
                return {"error": f"SHAP failed: TreeExplainer: {str(e)[:100]}, KernelExplainer: {str(e2)[:100]}"}

    def explain_prediction(self, model_path: str, single_input: list,
                           feature_names: list = None):
        """解释单次预测"""
        with open(model_path, "rb") as f:
            model = pickle.load(f)

        X = np.array(single_input).reshape(1, -1)
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X)
        expected_value = explainer.expected_value

        if isinstance(shap_values, list):
            shap_values = shap_values[0]
            expected_value = expected_value[0] if isinstance(expected_value, list) else expected_value

        contributions = []
        for i, val in enumerate(shap_values[0]):
            name = feature_names[i] if feature_names and i < len(feature_names) else f"feature_{i}"
            contributions.append({
                "feature": name,
                "shap_value": round(float(val), 6),
                "direction": "positive" if val > 0 else "negative",
            })

        contributions.sort(key=lambda x: abs(x["shap_value"]), reverse=True)

        return {
            "base_value": round(float(expected_value), 4),
            "prediction": round(float(model.predict(X)[0]), 4),
            "top_contributions": contributions[:10],
        }
