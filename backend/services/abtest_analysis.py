"""Pure statistical helpers for persisted A/B deployment experiments."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np


HIGHER_IS_BETTER = {"accuracy", "f1", "r2"}
LOWER_IS_BETTER = {"mae"}
SUPPORTED_METRICS = HIGHER_IS_BETTER | LOWER_IS_BETTER


def stable_variant(experiment_id: str, subject_id: str, traffic_split_a: float) -> str:
    """Assign the same subject to the same variant for the life of an experiment."""
    digest = hashlib.sha256(f"{experiment_id}:{subject_id}".encode("utf-8")).hexdigest()
    bucket = int(digest[:12], 16) % 10_000
    return "A" if bucket < int(float(traffic_split_a) * 100) else "B"


def observation_metric(metric: str, prediction: Any, actual: Any) -> float:
    if metric not in SUPPORTED_METRICS:
        raise ValueError(f"unsupported metric: {metric}")
    if metric in {"accuracy", "f1"}:
        return 1.0 if prediction == actual else 0.0
    try:
        return abs(float(prediction) - float(actual))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{metric} requires numeric prediction and actual values") from exc


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _metric_value(metric: str, predictions: list[Any], actuals: list[Any]) -> float | None:
    if not predictions or len(predictions) != len(actuals):
        return None
    if metric == "accuracy":
        return float(np.mean([p == a for p, a in zip(predictions, actuals)]))
    if metric == "f1":
        try:
            from sklearn.metrics import f1_score

            return float(f1_score(actuals, predictions, average="weighted", zero_division=0))
        except Exception:
            return float(np.mean([p == a for p, a in zip(predictions, actuals)]))
    try:
        predicted = np.asarray(predictions, dtype=float)
        actual = np.asarray(actuals, dtype=float)
    except (TypeError, ValueError):
        return None
    if metric == "mae":
        return float(np.mean(np.abs(predicted - actual)))
    if metric == "r2":
        if len(actual) < 2:
            return None
        total = float(np.sum((actual - np.mean(actual)) ** 2))
        if total <= 0:
            return None
        return float(1 - np.sum((actual - predicted) ** 2) / total)
    return None


def _welch_test(values_a: list[float], values_b: list[float]) -> tuple[float | None, float | None]:
    if len(values_a) < 2 or len(values_b) < 2:
        return None, None
    if float(np.std(values_a)) == 0 and float(np.std(values_b)) == 0:
        return None, None
    try:
        from scipy import stats

        statistic, p_value = stats.ttest_ind(values_a, values_b, equal_var=False)
        statistic = _finite_float(statistic)
        p_value = _finite_float(p_value)
        return statistic, p_value
    except Exception:
        return None, None


def _effect_size(values_a: list[float], values_b: list[float]) -> float | None:
    if len(values_a) < 2 or len(values_b) < 2:
        return None
    combined = np.asarray(values_a + values_b, dtype=float)
    std = float(np.std(combined))
    if not math.isfinite(std) or std <= 0:
        return 0.0
    return float((np.mean(values_b) - np.mean(values_a)) / std)


def _summary(
    metric: str,
    metric_a: float | None,
    metric_b: float | None,
    test_values_a: list[float],
    test_values_b: list[float],
    *,
    source: str,
) -> dict:
    statistic, p_value = _welch_test(test_values_a, test_values_b)
    significant = p_value is not None and p_value < 0.05
    best_variant = None
    if significant and metric_a is not None and metric_b is not None:
        if metric in LOWER_IS_BETTER:
            best_variant = "A" if metric_a < metric_b else "B"
        else:
            best_variant = "A" if metric_a > metric_b else "B"

    difference = None
    improvement_pct = None
    if metric_a is not None and metric_b is not None:
        difference = metric_b - metric_a
        if abs(metric_a) > 1e-12:
            numerator = metric_a - metric_b if metric in LOWER_IS_BETTER else metric_b - metric_a
            improvement_pct = numerator / abs(metric_a) * 100

    result = {
        "source": source,
        "metric": metric,
        "metric_direction": "lower" if metric in LOWER_IS_BETTER else "higher",
        "mean_a": round(metric_a, 6) if metric_a is not None else None,
        "mean_b": round(metric_b, 6) if metric_b is not None else None,
        "std_a": round(float(np.std(test_values_a)), 6) if test_values_a else None,
        "std_b": round(float(np.std(test_values_b)), 6) if test_values_b else None,
        "n_a": len(test_values_a),
        "n_b": len(test_values_b),
        "t_statistic": round(statistic, 6) if statistic is not None else None,
        "p_value": round(p_value, 6) if p_value is not None else None,
        "significant": bool(significant),
        "effect_size": None,
        "best_variant": best_variant,
        "difference": round(difference, 6) if difference is not None else None,
        "improvement_pct": round(improvement_pct, 2) if improvement_pct is not None else None,
    }
    effect = _effect_size(test_values_a, test_values_b)
    result["effect_size"] = round(effect, 6) if effect is not None else None
    if len(test_values_a) < 2 or len(test_values_b) < 2:
        result["error"] = "每个模型至少需要2条已回填真实值的结果"
    elif p_value is None:
        result["error"] = "样本方差不足，暂时无法进行显著性检验"
    return result


def analyze_observations(metric: str, rows: list[dict]) -> dict:
    grouped = {"A": [], "B": []}
    for row in rows:
        variant = str(row.get("variant") or "").upper()
        if variant in grouped and row.get("actual") is not None:
            grouped[variant].append(row)

    predictions_a = [row.get("prediction") for row in grouped["A"]]
    predictions_b = [row.get("prediction") for row in grouped["B"]]
    actuals_a = [row.get("actual") for row in grouped["A"]]
    actuals_b = [row.get("actual") for row in grouped["B"]]
    metric_a = _metric_value(metric, predictions_a, actuals_a)
    metric_b = _metric_value(metric, predictions_b, actuals_b)

    if metric in {"accuracy", "f1"}:
        test_a = [1.0 if p == a else 0.0 for p, a in zip(predictions_a, actuals_a)]
        test_b = [1.0 if p == a else 0.0 for p, a in zip(predictions_b, actuals_b)]
        basis = "逐请求是否预测正确"
    else:
        test_a = [abs(float(p) - float(a)) for p, a in zip(predictions_a, actuals_a)]
        test_b = [abs(float(p) - float(a)) for p, a in zip(predictions_b, actuals_b)]
        basis = "逐请求绝对误差"

    result = _summary(metric, metric_a, metric_b, test_a, test_b, source="online_feedback")
    result["significance_basis"] = basis
    return result


def analyze_manual_values(metric: str, values_a: list[float], values_b: list[float]) -> dict:
    clean_a = [value for value in (_finite_float(v) for v in values_a) if value is not None]
    clean_b = [value for value in (_finite_float(v) for v in values_b) if value is not None]
    metric_a = float(np.mean(clean_a)) if clean_a else None
    metric_b = float(np.mean(clean_b)) if clean_b else None
    return _summary(metric, metric_a, metric_b, clean_a, clean_b, source="legacy_manual")


def calculate_sample_size(
    metric: str,
    baseline: float = 0.5,
    min_effect: float = 0.05,
    power: float = 0.8,
    alpha: float = 0.05,
) -> dict:
    if metric not in SUPPORTED_METRICS:
        raise ValueError("不支持的指标")
    if not 0 < power < 1 or not 0 < alpha < 1:
        raise ValueError("power和alpha必须在0到1之间")
    if min_effect <= 0:
        raise ValueError("最小效应必须大于0")
    from scipy import stats

    z_alpha = float(stats.norm.ppf(1 - alpha / 2))
    z_beta = float(stats.norm.ppf(power))
    if metric in {"accuracy", "f1"}:
        if not 0 < baseline < 1:
            raise ValueError("比例指标的基线必须在0到1之间")
        target = baseline + min_effect
        if target >= 1:
            raise ValueError("基线与最小提升之和必须小于1")
        numerator = (z_alpha + z_beta) ** 2 * (
            baseline * (1 - baseline) + target * (1 - target)
        )
        per_variant = int(math.ceil(numerator / (min_effect**2)))
        method = "双样本比例近似"
    else:
        per_variant = int(math.ceil(2 * ((z_alpha + z_beta) / min_effect) ** 2))
        method = "标准化效应量（Cohen's d）"
    return {
        "required_per_variant": per_variant,
        "total": per_variant * 2,
        "metric": metric,
        "baseline": baseline if metric in {"accuracy", "f1"} else None,
        "min_effect": min_effect,
        "power": power,
        "alpha": alpha,
        "method": method,
    }
