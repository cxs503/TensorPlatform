"""Canonical built-in algorithm implementations shared by AutoML and the sandbox."""
from __future__ import annotations

import inspect
from typing import Any, Callable


BUILTIN_SPECS: dict[str, dict[str, Any]] = {
    "random_forest": {"name": "Random Forest", "framework": "Scikit-learn", "tasks": ["classification", "regression"], "description": "随机森林集成算法"},
    "gradient_boosting": {"name": "Gradient Boosting", "framework": "Scikit-learn", "tasks": ["classification", "regression"], "description": "经典梯度提升算法"},
    "lightgbm": {"name": "LightGBM", "framework": "LightGBM", "tasks": ["classification", "regression"], "description": "轻量梯度提升算法"},
    "xgboost": {"name": "XGBoost", "framework": "XGBoost", "tasks": ["classification", "regression"], "description": "高精度梯度提升算法"},
    "logistic_regression": {"name": "Logistic Regression", "framework": "Scikit-learn", "tasks": ["classification"], "description": "逻辑回归分类算法"},
    "linear_regression": {"name": "Linear Regression", "framework": "Scikit-learn", "tasks": ["regression"], "description": "线性回归算法"},
    "ridge": {"name": "Ridge Regression", "framework": "Scikit-learn", "tasks": ["regression"], "description": "带 L2 正则化的线性回归"},
    "lasso": {"name": "Lasso Regression", "framework": "Scikit-learn", "tasks": ["regression"], "description": "带 L1 正则化的线性回归"},
    "svm": {"name": "Support Vector Machine", "framework": "Scikit-learn", "tasks": ["classification", "regression"], "description": "支持向量机算法"},
    "neural_network": {"name": "MLP", "framework": "Scikit-learn", "tasks": ["classification", "regression"], "description": "多层感知机（浅层全连接神经网络）"},
}


def build_random_forest(task_type: str = "regression", **params):
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    defaults = {"n_estimators": 80, "random_state": 42}
    defaults.update(params)
    estimator = RandomForestClassifier if task_type == "classification" else RandomForestRegressor
    return estimator(**defaults)


def build_gradient_boosting(task_type: str = "regression", **params):
    from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
    defaults = {"random_state": 42}
    defaults.update(params)
    estimator = GradientBoostingClassifier if task_type == "classification" else GradientBoostingRegressor
    return estimator(**defaults)


def build_lightgbm(task_type: str = "regression", **params):
    try:
        from lightgbm import LGBMClassifier, LGBMRegressor
        defaults = {"n_estimators": 80, "random_state": 42, "verbose": -1}
        defaults.update(params)
        estimator = LGBMClassifier if task_type == "classification" else LGBMRegressor
        return estimator(**defaults)
    except ImportError:
        from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
        defaults = {"random_state": 42}
        defaults.update(params)
        estimator = GradientBoostingClassifier if task_type == "classification" else GradientBoostingRegressor
        return estimator(**defaults)


def build_xgboost(task_type: str = "regression", **params):
    try:
        from xgboost import XGBClassifier, XGBRegressor
        defaults = {"n_estimators": 80, "random_state": 42}
        if task_type == "classification":
            defaults["eval_metric"] = "logloss"
        defaults.update(params)
        estimator = XGBClassifier if task_type == "classification" else XGBRegressor
        return estimator(**defaults)
    except ImportError:
        from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
        defaults = {"random_state": 42}
        defaults.update(params)
        estimator = GradientBoostingClassifier if task_type == "classification" else GradientBoostingRegressor
        return estimator(**defaults)


def build_logistic_regression(task_type: str = "classification", **params):
    from sklearn.linear_model import LogisticRegression
    defaults = {"max_iter": 500, "random_state": 42}
    defaults.update(params)
    return LogisticRegression(**defaults)


def build_linear_regression(task_type: str = "regression", **params):
    from sklearn.linear_model import LinearRegression
    return LinearRegression(**params)


def build_ridge(task_type: str = "regression", **params):
    from sklearn.linear_model import Ridge
    defaults = {"alpha": 1.0}
    defaults.update(params)
    return Ridge(**defaults)


def build_lasso(task_type: str = "regression", **params):
    from sklearn.linear_model import Lasso
    defaults = {"alpha": 0.1, "random_state": 42}
    defaults.update(params)
    return Lasso(**defaults)


def build_svm(task_type: str = "regression", **params):
    from sklearn.svm import SVC, SVR
    defaults = {"random_state": 42} if task_type == "classification" else {}
    defaults.update(params)
    estimator = SVC if task_type == "classification" else SVR
    return estimator(**defaults)


def build_neural_network(task_type: str = "regression", **params):
    from sklearn.neural_network import MLPClassifier, MLPRegressor
    defaults = {"random_state": 42, "max_iter": 300}
    defaults.update(params)
    estimator = MLPClassifier if task_type == "classification" else MLPRegressor
    return estimator(**defaults)


BUILDERS: dict[str, Callable[..., Any]] = {
    "random_forest": build_random_forest,
    "gradient_boosting": build_gradient_boosting,
    "lightgbm": build_lightgbm,
    "xgboost": build_xgboost,
    "logistic_regression": build_logistic_regression,
    "linear_regression": build_linear_regression,
    "ridge": build_ridge,
    "lasso": build_lasso,
    "svm": build_svm,
    "neural_network": build_neural_network,
}


# 各内置算法的可调超参规格：前端调参面板渲染源 + 后端参数校验白名单（双用途）。
# 字段：type=int/float/str/bool；default 为 None 表示“不限/留空”；min/max/options 可选。
PARAM_SPECS: dict[str, list[dict[str, Any]]] = {
    "random_forest": [
        {"name": "n_estimators", "type": "int", "default": 80, "min": 10, "max": 1000, "label": "树数量"},
        {"name": "max_depth", "type": "int", "default": None, "min": 1, "max": 50, "label": "最大深度（空=不限）"},
        {"name": "min_samples_split", "type": "int", "default": 2, "min": 2, "label": "最小分裂样本数"},
        {"name": "min_samples_leaf", "type": "int", "default": 1, "min": 1, "label": "叶子最小样本数"},
        {"name": "max_features", "type": "str", "default": None, "options": ["sqrt", "log2", "auto"], "label": "最大特征数"},
    ],
    "xgboost": [
        {"name": "n_estimators", "type": "int", "default": 80, "min": 10, "max": 1000},
        {"name": "max_depth", "type": "int", "default": 6, "min": 1, "max": 30},
        {"name": "learning_rate", "type": "float", "default": 0.3, "min": 0.01, "max": 1.0},
        {"name": "subsample", "type": "float", "default": 1.0, "min": 0.5, "max": 1.0},
        {"name": "colsample_bytree", "type": "float", "default": 1.0, "min": 0.5, "max": 1.0},
    ],
    "lightgbm": [
        {"name": "n_estimators", "type": "int", "default": 80, "min": 10, "max": 1000},
        {"name": "num_leaves", "type": "int", "default": 31, "min": 2, "max": 512},
        {"name": "learning_rate", "type": "float", "default": 0.1, "min": 0.01, "max": 1.0},
        {"name": "max_depth", "type": "int", "default": -1, "min": -1, "max": 50},
        {"name": "min_child_samples", "type": "int", "default": 20, "min": 1},
    ],
    "gradient_boosting": [
        {"name": "n_estimators", "type": "int", "default": 100, "min": 10, "max": 1000},
        {"name": "max_depth", "type": "int", "default": 3, "min": 1, "max": 30},
        {"name": "learning_rate", "type": "float", "default": 0.1, "min": 0.01, "max": 1.0},
        {"name": "subsample", "type": "float", "default": 1.0, "min": 0.5, "max": 1.0},
        {"name": "min_samples_split", "type": "int", "default": 2, "min": 2},
    ],
    "logistic_regression": [
        {"name": "C", "type": "float", "default": 1.0, "min": 0.0001, "label": "正则强度倒数"},
        {"name": "max_iter", "type": "int", "default": 500, "min": 100, "max": 5000},
        {"name": "penalty", "type": "str", "default": "l2", "options": ["l1", "l2", "elasticnet", "none"]},
        {"name": "solver", "type": "str", "default": "lbfgs", "options": ["lbfgs", "liblinear", "saga", "newton-cg"]},
    ],
    "linear_regression": [
        {"name": "fit_intercept", "type": "bool", "default": True, "label": "是否计算截距"},
    ],
    "ridge": [
        {"name": "alpha", "type": "float", "default": 1.0, "min": 0.0001, "label": "L2 正则强度"},
        {"name": "fit_intercept", "type": "bool", "default": True},
        {"name": "solver", "type": "str", "default": "auto", "options": ["auto", "svd", "cholesky", "lsqr", "sag"]},
    ],
    "lasso": [
        {"name": "alpha", "type": "float", "default": 0.1, "min": 0.0001, "label": "L1 正则强度"},
        {"name": "max_iter", "type": "int", "default": 1000, "min": 1},
        {"name": "fit_intercept", "type": "bool", "default": True},
        {"name": "selection", "type": "str", "default": "cyclic", "options": ["cyclic", "random"]},
    ],
    "svm": [
        {"name": "C", "type": "float", "default": 1.0, "min": 0.0001, "label": "正则强度倒数"},
        {"name": "kernel", "type": "str", "default": "rbf", "options": ["linear", "poly", "rbf", "sigmoid"]},
        {"name": "gamma", "type": "str", "default": "scale", "options": ["scale", "auto"]},
        {"name": "degree", "type": "int", "default": 3, "min": 2, "max": 5, "label": "仅 poly 有效"},
    ],
    "neural_network": [
        {"name": "hidden_layer_sizes", "type": "str", "default": "100", "label": "隐藏层（逗号分隔，如 64,32）"},
        {"name": "activation", "type": "str", "default": "relu", "options": ["relu", "tanh", "logistic", "identity"]},
        {"name": "alpha", "type": "float", "default": 0.0001, "min": 0.000001, "label": "L2 正则强度"},
        {"name": "max_iter", "type": "int", "default": 300, "min": 100, "max": 2000},
        {"name": "learning_rate_init", "type": "float", "default": 0.001, "min": 0.0001, "max": 1.0},
    ],
}


def build_estimator(algorithm_key: str, task_type: str, **params):
    if task_type == "time_series" and algorithm_key != "lightgbm":
        raise ValueError(f"内置算法 {algorithm_key} 不支持任务类型 {task_type}")
    task_type = "regression" if task_type == "time_series" else task_type
    spec = BUILTIN_SPECS.get(algorithm_key)
    builder = BUILDERS.get(algorithm_key)
    if not spec or not builder or task_type not in spec["tasks"]:
        raise ValueError(f"内置算法 {algorithm_key} 不支持任务类型 {task_type}")
    return builder(task_type=task_type, **params)


def build_model_factory(task_type: str) -> dict[str, Callable[..., Any]]:
    if task_type == "time_series":
        return {"lightgbm": lambda p=None: build_estimator("lightgbm", "time_series", **(p or {}))}
    task_type = "regression" if task_type == "time_series" else task_type
    return {
        key: (lambda selected=key, p=None: build_estimator(selected, task_type, **(p or {})))
        for key, spec in BUILTIN_SPECS.items()
        if task_type in spec["tasks"]
    }


def validate_algorithm_params(algorithm_key: str, task_type: str, params: dict | None) -> dict:
    """校验并归一化用户传入的算法超参，返回可直接 ** 展开给 build_xxx 的干净 dict。

    - 未知 key 报错（让用户立刻发现拼写错误，而非静默丢弃）
    - 类型转换：int/float/str/bool
    - 数值范围（min/max）、枚举（options）校验
    - hidden_layer_sizes "64,32" → (64, 32)
    - None / 空串值剔除（留空 = 用算法默认）
    """
    if not params:
        return {}
    spec = BUILTIN_SPECS.get(algorithm_key)
    if not spec:
        raise ValueError(f"未知的内置算法：{algorithm_key}")
    norm_task = "regression" if task_type == "time_series" else task_type
    if norm_task not in spec["tasks"]:
        raise ValueError(f"算法 {algorithm_key} 不支持任务类型 {task_type}")
    field_map = {f["name"]: f for f in PARAM_SPECS.get(algorithm_key, [])}
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        field = field_map.get(key)
        if not field:
            allowed = ", ".join(field_map) or "（无）"
            raise ValueError(f"算法 {algorithm_key} 不支持参数「{key}」，支持：{allowed}")
        if value is None or value == "":
            continue
        ftype = field["type"]
        try:
            if ftype == "int":
                value = int(value)
            elif ftype == "float":
                value = float(value)
            elif ftype == "bool":
                value = value if isinstance(value, bool) else str(value).lower() in ("true", "1", "yes")
            elif ftype == "str":
                value = str(value)
        except (TypeError, ValueError):
            raise ValueError(f"参数「{key}」需要 {ftype} 类型")
        if ftype in ("int", "float"):
            if "min" in field and value < field["min"]:
                raise ValueError(f"参数「{key}」不能小于 {field['min']}")
            if "max" in field and value > field["max"]:
                raise ValueError(f"参数「{key}」不能大于 {field['max']}")
        if ftype == "str" and "options" in field and value not in field["options"]:
            raise ValueError(f"参数「{key}」必须是 {field['options']} 之一")
        if key == "hidden_layer_sizes" and isinstance(value, str):
            try:
                value = tuple(int(x.strip()) for x in value.split(",") if x.strip())
            except ValueError:
                raise ValueError("hidden_layer_sizes 必须是逗号分隔的整数（如 64,32）")
        cleaned[key] = value
    return cleaned


def builtin_source(algorithm_key: str) -> str:
    spec = BUILTIN_SPECS.get(algorithm_key)
    builder = BUILDERS.get(algorithm_key)
    if not spec or not builder:
        raise KeyError(algorithm_key)
    function_source = inspect.getsource(builder)
    return (
        f'"""数字化底座内置算法：{spec["name"]}\n'
        "此文件是内置模板的工作区副本；可以修改并另存，但不能覆盖平台内置源码。\n"
        '"""\n\n'
        f"{function_source}\n\n"
        f"build_model = {builder.__name__}\n\n"
        'if __name__ == "__main__":\n'
        '    print(build_model(task_type="regression"))\n'
    )


def supported_models() -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {"classification": [], "regression": [], "time_series": []}
    for key, spec in BUILTIN_SPECS.items():
        for task in spec["tasks"]:
            result[task].append({"id": key, "name": spec["name"], "type": spec["framework"]})
    result["time_series"] = [item for item in result["regression"] if item["id"] == "lightgbm"]
    return result
