"""
模型可解释性服务
提供SHAP、LIME、特征重要性分析
"""
from typing import Any, Dict, List, Optional

import numpy as np

try:
    import shap

    SHAP_AVAILABLE = True
except ImportError:
    SHAP_AVAILABLE = False
    print("SHAP not available, using mock")

try:
    from lime.lime_tabular import LimeTabularExplainer

    LIME_AVAILABLE = True
except ImportError:
    LIME_AVAILABLE = False
    print("LIME not available, using mock")


class ModelExplainer:
    """模型可解释性服务"""

    def __init__(self):
        self.SHAP_AVAILABLE = SHAP_AVAILABLE
        self.LIME_AVAILABLE = LIME_AVAILABLE

    def explain_with_shap(
        self,
        model,
        X: List[List[float]],
        feature_names: List[str] = None,
    ) -> Dict[str, Any]:
        """
        使用SHAP解释模型预测

        Args:
            model: 训练好的模型
            X: 输入特征
            feature_names: 特征名称
        """
        if not self.SHAP_AVAILABLE:
            # 不再返回随机假数据,明确告知不可用
            return {
                "method": "SHAP",
                "available": False,
                "error": "SHAP未安装",
                "message": "SHAP 库未安装,无法提供真实归因分析。",
            }

        try:
            from sklearn.pipeline import Pipeline as _SkPipe

            values = np.asarray(X, dtype=float)
            if values.ndim != 2 or not len(values) or not values.shape[1]:
                raise ValueError("SHAP输入必须是非空的二维特征数据")

            # Pipeline 和平台 PyTorchPredictor 都可能包含缩放器。直接抽出内部模型
            # 会绕过预处理并产生错误归因，因此只有裸树模型使用 TreeExplainer。
            module_name = type(model).__module__.lower()
            class_name = type(model).__name__.lower()
            is_pipeline = isinstance(model, _SkPipe)
            is_tree_model = not is_pipeline and (
                hasattr(model, "tree_")
                or hasattr(model, "estimators_")
                or hasattr(model, "get_booster")
                or hasattr(model, "booster_")
                or "xgboost" in module_name
                or "lightgbm" in module_name
                or "forest" in class_name
                or "tree" in class_name
            )

            output_index = None
            note = None
            if is_tree_model:
                explainer = shap.TreeExplainer(model)
                raw_shap_values = explainer.shap_values(values)
                base_values = explainer.expected_value
                explainer_type = "TreeExplainer"
            else:
                if hasattr(model, "predict_proba"):
                    predict_fn = model.predict_proba
                elif hasattr(model, "predict"):
                    predict_fn = model.predict
                else:
                    raise ValueError(
                        f"模型 {type(model).__name__} 没有 predict/predict_proba 接口"
                    )

                # 模型无关解释会完整经过模型封装中的缩放和预测逻辑，适用于
                # PyTorch MLP、sklearn MLP 以及带预处理的 Pipeline。
                rng = np.random.default_rng(42)
                background_size = min(len(values), 50)
                if len(values) > background_size:
                    background_idx = rng.choice(
                        len(values), size=background_size, replace=False
                    )
                    background = values[background_idx]
                else:
                    background = values
                explained_values = values[: min(len(values), 10)]
                masker = shap.maskers.Independent(
                    background, max_samples=background_size
                )
                explainer = shap.Explainer(
                    predict_fn, masker, algorithm="permutation"
                )
                explanation = explainer(
                    explained_values,
                    max_evals=max(2 * values.shape[1] + 1, 5),
                )
                raw_shap_values = explanation.values
                base_values = explanation.base_values
                explainer_type = type(explainer).__name__
                note = (
                    "该模型不是裸树模型，已使用模型无关的Permutation SHAP；"
                    "结果会完整经过模型的预处理和预测流程。"
                )

            # SHAP在不同版本/任务下可能返回列表或三维数组。二分类默认展示
            # 正类，多分类默认展示第一类，确保前端始终收到二维样本×特征数据。
            if isinstance(raw_shap_values, list):
                output_index = 1 if len(raw_shap_values) == 2 else 0
                shap_values = np.asarray(raw_shap_values[output_index], dtype=float)
            else:
                shap_values = np.asarray(raw_shap_values, dtype=float)
                if shap_values.ndim == 3:
                    output_index = 1 if shap_values.shape[-1] == 2 else 0
                    shap_values = shap_values[..., output_index]
            if shap_values.ndim == 1:
                shap_values = shap_values.reshape(1, -1)
            if shap_values.ndim != 2:
                raise ValueError(f"无法识别SHAP结果形状：{shap_values.shape}")

            feature_importance = np.abs(shap_values).mean(axis=0).tolist()
            if feature_names is None or len(feature_names) != len(feature_importance):
                feature_names = [
                    f"feature_{i}" for i in range(len(feature_importance))
                ]

            base_array = np.asarray(base_values)
            if output_index is not None and base_array.ndim > 1:
                base_array = base_array[..., output_index]
            elif (
                output_index is not None
                and base_array.ndim == 1
                and base_array.size <= 20
                and base_array.size > output_index
            ):
                base_array = np.asarray([base_array[output_index]])
            base_list = np.asarray(base_array, dtype=float).reshape(-1).tolist()

            result = {
                "method": "SHAP",
                "available": True,
                "explainer_type": explainer_type,
                "shap_values": shap_values[:10].tolist(),
                "feature_importance": [
                    {"feature": name, "importance": float(importance)}
                    for name, importance in sorted(
                        zip(feature_names, feature_importance),
                        key=lambda item: item[1],
                        reverse=True,
                    )
                ],
                "expected_value": base_list,
                "base_values": base_list,
            }
            if output_index is not None:
                result["output_index"] = output_index
            if note:
                result["note"] = note
            return result
        except Exception as e:
            return {
                "error": str(e),
                "message": f"SHAP分析失败：{e}",
                "available": True,
                "method": "SHAP",
            }

    def explain_with_lime(
        self,
        model,
        X: List[List[float]],
        feature_names: List[str] = None,
        num_features: int = 10,
    ) -> Dict[str, Any]:
        """
        使用LIME解释模型预测

        Args:
            model: 训练好的模型
            X: 输入特征
            feature_names: 特征名称
            num_features: 显示的特征数量
        """
        if not self.LIME_AVAILABLE:
            # 不再返回随机假数据,明确告知不可用
            return {
                "method": "LIME",
                "available": False,
                "error": "LIME未安装",
                "message": "LIME 库未安装,无法提供真实局部解释。",
            }

        try:
            if feature_names is None:
                feature_names = [f"feature_{i}" for i in range(len(X[0]))]

            # 从 Pipeline 提取底层模型 + 判断分类/回归
            from sklearn.pipeline import Pipeline as _SkPipe
            _inner = model
            if isinstance(model, _SkPipe):
                _inner = model.named_steps.get('model', model)
            _is_clf = hasattr(_inner, 'predict_proba')
            _predict_fn = model.predict_proba if _is_clf else (lambda x: model.predict(x).reshape(-1, 1))
            _mode = "classification" if _is_clf else "regression"

            # 创建LIME解释器
            explainer = LimeTabularExplainer(
                training_data=np.array(X),
                feature_names=feature_names,
                class_names=["class_0", "class_1"] if _is_clf else None,
                mode=_mode,
            )

            # 解释第一个样本
            exp = explainer.explain_instance(
                np.array(X[0]), _predict_fn, num_features=num_features
            )

            # 提取特征重要性
            explanation = exp.as_list()

            return {
                "method": "LIME",
                "explanation": [
                    {
                        "feature": name,
                        "weight": weight,
                        "direction": "positive" if weight > 0 else "negative",
                    }
                    for name, weight in explanation
                ],
                "local_prediction": exp.local_pred[0]
                if hasattr(exp, "local_pred")
                else None,
            }
        except Exception as e:
            return {"error": str(e), "method": "LIME"}

    def get_feature_importance(
        self,
        model,
        feature_names: List[str] = None,
    ) -> Dict[str, Any]:
        """
        获取特征重要性

        Args:
            model: 训练好的模型
            feature_names: 特征名称
        """
        importance = None

        # 尝试从模型中获取特征重要性
        if hasattr(model, "feature_importances_"):
            importance = model.feature_importances_
        elif hasattr(model, "coef_"):
            importance = np.abs(model.coef_)
            if len(importance.shape) > 1:
                importance = importance.mean(axis=0)

        if importance is None:
            # 模型未暴露 feature_importances_/coef_,返回明确标记的占位(非真实值)
            return {
                "method": "feature_importance",
                "available": False,
                "error": "模型无可用的特征重要性属性",
                "message": "该模型未提供 feature_importances_ 或 coef_ 属性,无法计算真实特征重要性。",
                "feature_importance": [],
                "total_features": len(feature_names) if feature_names else 0,
            }

        if feature_names is None:
            feature_names = [f"feature_{i}" for i in range(len(importance))]

        # 排序
        sorted_idx = np.argsort(importance)[::-1]

        return {
            "method": "feature_importance",
            "feature_importance": [
                {
                    "feature": feature_names[idx],
                    "importance": float(importance[idx]),
                    "rank": rank + 1,
                }
                for rank, idx in enumerate(sorted_idx)
            ],
            "total_features": len(feature_names),
        }

    def get_partial_dependence(
        self,
        model,
        X: List[List[float]],
        feature_idx: int,
        feature_name: str = None,
        num_points: int = 50,
    ) -> Dict[str, Any]:
        """
        计算部分依赖图数据

        Args:
            model: 模型
            X: 训练数据
            feature_idx: 特征索引
            feature_name: 特征名称
            num_points: 采样点数
        """
        X = np.array(X)

        # 获取特征范围
        feature_values = X[:, feature_idx]
        min_val = feature_values.min()
        max_val = feature_values.max()

        # 生成采样点
        points = np.linspace(min_val, max_val, num_points)

        # 计算每个点的平均预测值
        pd_values = []
        for point in points:
            X_copy = X.copy()
            X_copy[:, feature_idx] = point
            # 使用模型的预测概率
            if hasattr(model, "predict_proba"):
                preds = model.predict_proba(X_copy)
                if len(preds.shape) > 1:
                    preds = preds[:, 1]
                pd_values.append(float(preds.mean()))
            else:
                preds = model.predict(X_copy)
                pd_values.append(float(preds.mean()))

        return {
            "method": "partial_dependence",
            "feature": feature_name or f"feature_{feature_idx}",
            "feature_idx": feature_idx,
            "x_values": points.tolist(),
            "y_values": pd_values,
        }

    def get_permutation_importance(
        self,
        model,
        X: List[List[float]],
        y: List[float],
        feature_names: List[str] = None,
        n_repeats: int = 10,
        task_type: str = None,
    ) -> Dict[str, Any]:
        """
        计算排列重要性

        Args:
            model: 模型
            X: 特征数据
            y: 标签
            feature_names: 特征名称
            n_repeats: 重复次数
        """
        X = np.array(X)
        y = np.array(y)

        if feature_names is None:
            feature_names = [f"feature_{i}" for i in range(X.shape[1])]

        task_type = str(task_type or "").lower()
        is_classification = task_type in {"classification", "binary", "multiclass"}
        baseline_predictions = np.asarray(model.predict(X)).reshape(-1)
        if len(baseline_predictions) != len(y):
            raise ValueError(
                f"模型预测返回 {len(baseline_predictions)} 条，但目标列有 {len(y)} 条"
            )
        if is_classification:
            baseline_metric = float(np.mean(baseline_predictions == y))
            metric_name = "accuracy"
        else:
            try:
                y = y.astype(float)
                baseline_predictions = baseline_predictions.astype(float)
            except (TypeError, ValueError) as exc:
                raise ValueError("回归目标列必须是数值类型") from exc
            baseline_metric = float(np.mean((baseline_predictions - y) ** 2))
            metric_name = "mse"

        # 对每个特征计算重要性
        importance_results = []

        rng = np.random.default_rng(42)
        for feat_idx in range(X.shape[1]):
            metric_values = []
            for _ in range(n_repeats):
                X_permuted = X.copy()
                X_permuted[:, feat_idx] = rng.permutation(
                    X_permuted[:, feat_idx]
                )
                predictions = np.asarray(model.predict(X_permuted)).reshape(-1)
                if is_classification:
                    metric_value = float(np.mean(predictions == y))
                else:
                    metric_value = float(np.mean((predictions.astype(float) - y) ** 2))
                metric_values.append(metric_value)

            importance_results.append(
                {
                    "feature": feature_names[feat_idx],
                    "importance": float(
                        np.mean(metric_values) - baseline_metric
                        if not is_classification
                        else baseline_metric - np.mean(metric_values)
                    ),
                    "std": float(np.std(metric_values)),
                }
            )

        # 排序
        importance_results.sort(key=lambda x: x["importance"], reverse=True)

        return {
            "method": "permutation_importance",
            "metric": metric_name,
            "baseline_metric": baseline_metric,
            "results": importance_results,
        }


# 全局实例
_explainer: Optional[ModelExplainer] = None


def get_model_explainer() -> ModelExplainer:
    """获取模型解释器"""
    global _explainer
    if _explainer is None:
        _explainer = ModelExplainer()
    return _explainer
