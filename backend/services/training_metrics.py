"""训练评估契约（切分 / 指标 / 退化判定）—— numpy-only 单一实现源。

背景（见 docs/reports/RAY_TRAINER_HOLDOUT_AUDIT.md）：
``backend/services/ray_trainer.py`` 的 sklearn 并行通道把**训练行**当验证集

    X_val_split = X_scaled[:min(100, len(X_scaled))]   # 旧实现（HEAD 57e72968）

而分片训练覆盖了全部行 —— 于是上报的 R²/accuracy 是样本内拟合优度，不是留出泛化
能力：纯噪声标签也能报出接近 1 的 R²。同一通道在单类留出集上还硬编码
``{"accuracy": 1.0, "f1": 1.0}``（没评估过任何预测就上报完美分）。

本模块把该通道的评估口径收成唯一、可复算、与 scikit-learn 定义逐位等价的三件事：

1. ``holdout_split``：**先**划分留出折，再做标准化/分片；
2. ``r2`` / ``rmse`` / ``mae`` / ``accuracy`` / ``f1_score``：闭式实现，与
   ``sklearn.metrics`` 同名函数在定义域内逐位一致（有 sklearn 时由
   ``tests/test_ray_trainer_holdout_contract.py`` 做等价性交叉复核，无 sklearn 时跳过）；
3. ``evaluate_regression`` / ``evaluate_classification``：把「指标不可用」与
   「指标=1.0」显式分开 —— 退化留出集（单类、常量目标、样本不足）返回
   ``metrics_valid=False`` + ``status``，绝不返回伪造的完美分。

只依赖 numpy：本机（2026-11-01）镜像无 scikit-learn/ray，若评估逻辑绑死 sklearn
则该通道无法被独立复核；解耦后可用纯 numpy 复算每一分支。

约定：
  * ``status == "ok"`` 才可作为模型质量证据；
  * ``metrics`` 在不可用时为 ``None``（调用方应显式判空，不得当 0 用）；
  * 目标值一律按一维数组处理。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "DEFAULT_TEST_SIZE",
    "DEFAULT_SEED",
    "holdout_split",
    "r2",
    "rmse",
    "mae",
    "accuracy",
    "f1_score",
    "evaluate_regression",
    "evaluate_classification",
]

DEFAULT_TEST_SIZE = 0.2
DEFAULT_SEED = 42
MIN_EVAL_SAMPLES = 2


def holdout_split(
    n_samples: int,
    test_size: float = DEFAULT_TEST_SIZE,
    seed: int = DEFAULT_SEED,
    strategy: str = "random",
) -> Tuple[np.ndarray, np.ndarray]:
    """返回 (train_idx, val_idx)，两折互不相交。

    ``strategy="random"`` 复刻 ``sklearn.model_selection.train_test_split`` 的
    索引选择：``perm = RandomState(seed).permutation(n)``，前 ``n_test`` 个作留出折，
    ``n_test = ceil(test_size * n)``。
    ``strategy="chronological"`` 保留时间顺序：留出折是尾部 ``n_test`` 行（时序数据
    不得把未来行掺进训练）。
    """
    n = int(n_samples)
    if n < 2:
        raise ValueError(f"样本数不足以划分训练/留出折: n_samples={n}")
    if not 0.0 < float(test_size) < 1.0:
        raise ValueError(f"test_size 必须在 (0, 1) 之间: {test_size}")
    n_test = int(np.ceil(float(test_size) * n))
    n_test = min(max(n_test, 1), n - 1)

    if strategy == "chronological":
        train_idx = np.arange(0, n - n_test, dtype=int)
        val_idx = np.arange(n - n_test, n, dtype=int)
        return train_idx, val_idx
    if strategy != "random":
        raise ValueError(f"不支持的数据切分策略: {strategy}")

    perm = np.random.RandomState(int(seed)).permutation(n)
    val_idx = np.sort(perm[:n_test]).astype(int)
    train_idx = np.sort(perm[n_test:]).astype(int)
    return train_idx, val_idx


def _as_1d(values: Sequence[float]) -> np.ndarray:
    return np.asarray(values, dtype=float).reshape(-1)


def r2(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    """决定系数，语义与 ``sklearn.metrics.r2_score`` 一致。

    ``SS_tot == 0`` 时：残差为 0（完美常量预测）返回 1.0，否则 0.0（sklearn 同）。
    样本数 < 2 时返回 ``nan``（不可估计）。
    """
    yt = _as_1d(y_true)
    yp = _as_1d(y_pred)
    if yt.shape != yp.shape:
        raise ValueError(f"y_true/y_pred 长度不一致: {yt.shape} vs {yp.shape}")
    if yt.size < 2:
        return float("nan")
    ss_res = float(np.sum((yt - yp) ** 2))
    ss_tot = float(np.sum((yt - np.mean(yt)) ** 2))
    if ss_tot == 0.0:
        return 1.0 if ss_res == 0.0 else 0.0
    return 1.0 - ss_res / ss_tot


def rmse(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    yt = _as_1d(y_true)
    yp = _as_1d(y_pred)
    if yt.shape != yp.shape:
        raise ValueError(f"y_true/y_pred 长度不一致: {yt.shape} vs {yp.shape}")
    if yt.size == 0:
        return float("nan")
    return float(np.sqrt(np.mean((yt - yp) ** 2)))


def mae(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    yt = _as_1d(y_true)
    yp = _as_1d(y_pred)
    if yt.shape != yp.shape:
        raise ValueError(f"y_true/y_pred 长度不一致: {yt.shape} vs {yp.shape}")
    if yt.size == 0:
        return float("nan")
    return float(np.mean(np.abs(yt - yp)))


def accuracy(y_true: Sequence[Any], y_pred: Sequence[Any]) -> float:
    yt = np.asarray(y_true).reshape(-1)
    yp = np.asarray(y_pred).reshape(-1)
    if yt.shape != yp.shape:
        raise ValueError(f"y_true/y_pred 长度不一致: {yt.shape} vs {yp.shape}")
    if yt.size == 0:
        return float("nan")
    return float(np.mean(yt == yp))


def f1_score(
    y_true: Sequence[Any],
    y_pred: Sequence[Any],
    average: str = "weighted",
    zero_division: float = 0.0,
) -> float:
    """F1，语义与 ``sklearn.metrics.f1_score`` 的 ``binary``/``weighted`` 一致。"""
    yt = np.asarray(y_true).reshape(-1)
    yp = np.asarray(y_pred).reshape(-1)
    if yt.shape != yp.shape:
        raise ValueError(f"y_true/y_pred 长度不一致: {yt.shape} vs {yp.shape}")
    if yt.size == 0:
        return float("nan")
    classes = np.unique(np.concatenate([yt, yp]))
    per_class: Dict[Any, Tuple[float, float]] = {}
    for c in classes:
        tp = float(np.sum((yt == c) & (yp == c)))
        fp = float(np.sum((yt != c) & (yp == c)))
        fn = float(np.sum((yt == c) & (yp != c)))
        precision = tp / (tp + fp) if (tp + fp) > 0 else zero_division
        recall = tp / (tp + fn) if (tp + fn) > 0 else zero_division
        if precision + recall > 0:
            per_class[c] = (2.0 * precision * recall / (precision + recall), float(np.sum(yt == c)))
        else:
            per_class[c] = (zero_division, float(np.sum(yt == c)))

    if average == "binary":
        # sklearn 默认把 1 当正类；这里同样取 1（若无 1 则取 max(classes) 会偏离，
        # 故要求调用方显式避免在非二值目标上用 average="binary"）。
        target = 1 if 1 in per_class else max(classes)
        return float(per_class[target][0])
    if average != "weighted":
        raise ValueError(f"仅支持 average='weighted'/'binary'，收到 {average}")
    total_support = float(sum(s for _, s in per_class.values()))
    if total_support == 0:
        return float(zero_division)
    return float(sum(f * s for f, s in per_class.values()) / total_support)


def _base_result(task_type: str, n_eval: int) -> Dict[str, Any]:
    return {
        "task_type": task_type,
        "n_eval": int(n_eval),
        "metrics": None,
        "metrics_valid": False,
        "status": "unknown",
    }


def evaluate_regression(
    y_true: Sequence[float],
    y_pred: Sequence[float],
    min_samples: int = MIN_EVAL_SAMPLES,
) -> Dict[str, Any]:
    """留出折回归评估。退化情形显式不可用，而不是报一个好看的数字。"""
    yt = _as_1d(y_true)
    yp = _as_1d(y_pred)
    result = _base_result("regression", yt.size)
    if yt.size != yp.size:
        result["status"] = "shape_mismatch"
        return result
    if yt.size == 0:
        result["status"] = "empty_holdout"
        return result
    if yt.size < int(min_samples):
        result["status"] = "insufficient_samples"
        return result

    values = {"r2": r2(yt, yp), "rmse": rmse(yt, yp), "mae": mae(yt, yp)}
    if float(np.ptp(yt)) == 0.0:
        # 常量留出目标：R² 无信息（sklearn 会返回 1.0/0.0，容易被误读成完美/失败）
        result.update(
            metrics=values,
            metrics_valid=False,
            status="constant_target",
        )
        return result

    result.update(metrics=values, metrics_valid=True, status="ok")
    return result


def evaluate_classification(
    y_true: Sequence[Any],
    y_pred: Sequence[Any],
    min_samples: int = MIN_EVAL_SAMPLES,
) -> Dict[str, Any]:
    """留出折分类评估。单类留出折 → f1 不可定义，绝不伪造 1.0。"""
    yt = np.asarray(y_true).reshape(-1)
    yp = np.asarray(y_pred).reshape(-1)
    result = _base_result("classification", yt.size)
    if yt.size != yp.size:
        result["status"] = "shape_mismatch"
        return result
    if yt.size == 0:
        result["status"] = "empty_holdout"
        return result
    if yt.size < int(min_samples):
        result["status"] = "insufficient_samples"
        return result

    values: Dict[str, Optional[float]] = {"accuracy": accuracy(yt, yp), "f1": None}
    single_class = np.unique(yt).size < 2
    if single_class:
        # accuracy 仍是真实预测的结果（不伪造），但 F1/weighted 无定义 → 不可作证据
        result.update(metrics=values, metrics_valid=False, status="single_class_holdout")
        return result

    values["f1"] = f1_score(yt, yp, average="weighted")
    values["precision"] = _weighted_precision(yt, yp)
    values["recall"] = _weighted_recall(yt, yp)
    result.update(metrics=values, metrics_valid=True, status="ok")
    return result


def _weighted_pr(y_true: np.ndarray, y_pred: np.ndarray) -> Tuple[float, float]:
    classes = np.unique(np.concatenate([y_true, y_pred]))
    total_support = float(y_true.size)
    precision_sum = 0.0
    recall_sum = 0.0
    for c in classes:
        tp = float(np.sum((y_true == c) & (y_pred == c)))
        fp = float(np.sum((y_true != c) & (y_pred == c)))
        fn = float(np.sum((y_true == c) & (y_pred != c)))
        support = float(np.sum(y_true == c))
        precision_sum += (tp / (tp + fp) if (tp + fp) > 0 else 0.0) * support
        recall_sum += (tp / (tp + fn) if (tp + fn) > 0 else 0.0) * support
    if total_support == 0:
        return 0.0, 0.0
    return precision_sum / total_support, recall_sum / total_support


def _weighted_precision(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(_weighted_pr(y_true, y_pred)[0])


def _weighted_recall(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(_weighted_pr(y_true, y_pred)[1])