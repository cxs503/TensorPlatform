"""Deterministic experiment adapters for causal analysis and recommendation.

These adapters intentionally know nothing about HTTP, SQLAlchemy, or tenant lookup.
The experiment runtime must resolve and authorize ``dataset_path`` before calling
them.  A bundled fixture is available only when callers explicitly set
``fixture_mode=true``; invalid CSV input never falls back to fixture data.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


MAX_CSV_BYTES = 64 * 1024 * 1024
MAX_CAUSAL_ROWS = 50_000
MAX_RECSYS_RATINGS = 100_000
MAX_CONFOUNDERS = 50
MAX_USERS = 10_000
MAX_ITEMS = 50_000


class ExperimentAlgorithmError(ValueError):
    """Fail-closed validation or training error safe for runtime translation."""


def _config(context: Any) -> Dict[str, Any]:
    value = getattr(context, "config", None)
    if not isinstance(value, Mapping):
        raise ExperimentAlgorithmError("experiment context.config must be a mapping")
    return dict(value)


def _work_dir(context: Any) -> Path:
    value = getattr(context, "work_dir", None)
    if not value:
        raise ExperimentAlgorithmError("experiment context.work_dir is required")
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ExperimentAlgorithmError("experiment work_dir is not a directory")
    return path


def _report(context: Any, percent: int, stage: str) -> None:
    callback = getattr(context, "progress", None)
    if not callable(callback):
        return
    try:
        callback(int(percent), stage)
    except TypeError:
        callback(int(percent))


def _bounded_int(
    config: Mapping[str, Any], key: str, default: int, minimum: int, maximum: int
) -> int:
    value = config.get(key, default)
    if isinstance(value, bool):
        raise ExperimentAlgorithmError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ExperimentAlgorithmError(f"{key} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ExperimentAlgorithmError(f"{key} must be between {minimum} and {maximum}")
    return parsed


def _bounded_float(
    config: Mapping[str, Any], key: str, default: float, minimum: float, maximum: float
) -> float:
    value = config.get(key, default)
    if isinstance(value, bool):
        raise ExperimentAlgorithmError(f"{key} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ExperimentAlgorithmError(f"{key} must be numeric") from exc
    if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
        raise ExperimentAlgorithmError(f"{key} must be between {minimum} and {maximum}")
    return parsed


def _nonempty_name(config: Mapping[str, Any], key: str, default: str) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
        raise ExperimentAlgorithmError(f"{key} must be a non-empty string up to 128 characters")
    return value.strip()


def _dataset_path(context: Any) -> Optional[Path]:
    value = getattr(context, "dataset_path", None)
    if value in (None, ""):
        return None
    path = Path(value)
    if path.suffix.lower() != ".csv":
        raise ExperimentAlgorithmError("only authorized CSV datasets are supported")
    if not path.is_file():
        raise ExperimentAlgorithmError("authorized CSV dataset does not exist")
    if path.stat().st_size > MAX_CSV_BYTES:
        raise ExperimentAlgorithmError("CSV dataset exceeds the 64 MiB limit")
    return path


def _fixture_enabled(config: Mapping[str, Any]) -> bool:
    value = config.get("fixture_mode", False)
    if not isinstance(value, bool):
        raise ExperimentAlgorithmError("fixture_mode must be a boolean")
    return value


def _source_or_fail(context: Any, config: Mapping[str, Any]) -> Tuple[Optional[Path], str]:
    path = _dataset_path(context)
    if path is not None:
        return path, "authorized_csv"
    if _fixture_enabled(config):
        return None, "deterministic_fixture"
    raise ExperimentAlgorithmError(
        "an authorized CSV dataset is required unless fixture_mode=true is explicit"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path, media_type: str, role: str) -> Dict[str, Any]:
    return {
        "path": str(path),
        "file_name": path.name,
        "media_type": media_type,
        "role": role,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _require_finite(metrics: Mapping[str, Any]) -> None:
    for key, value in metrics.items():
        if isinstance(value, (int, np.integer)):
            continue
        if isinstance(value, (float, np.floating)) and math.isfinite(float(value)):
            continue
        raise ExperimentAlgorithmError(f"metric {key} is not a finite scalar")


def _causal_fixture() -> pd.DataFrame:
    # Fixed, non-random sample with confounding and a known adjusted effect.
    index = np.arange(240, dtype=np.float64)
    confounder = (index - index.mean()) / index.std()
    treatment_noise = ((index * 17) % 29 - 14) / 29.0
    treatment = 0.65 * confounder + treatment_noise
    outcome_noise = 0.08 * np.sin(index * 0.37) + 0.03 * np.cos(index * 0.11)
    outcome = 2.0 * treatment + 0.8 * confounder + outcome_noise
    return pd.DataFrame({"X": treatment, "Y": outcome, "Z": confounder})


def _read_causal_frame(
    path: Optional[Path], config: Mapping[str, Any]
) -> Tuple[pd.DataFrame, str, str, List[str], Dict[str, Any]]:
    treatment = _nonempty_name(config, "treatment", "X")
    outcome = _nonempty_name(config, "outcome", "Y")
    raw_confounders = config.get("confounders", ["Z"] if path is None else [])
    if not isinstance(raw_confounders, (list, tuple)):
        raise ExperimentAlgorithmError("confounders must be a list")
    if len(raw_confounders) > MAX_CONFOUNDERS:
        raise ExperimentAlgorithmError(f"confounders exceeds the {MAX_CONFOUNDERS} column limit")
    confounders: List[str] = []
    for value in raw_confounders:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
            raise ExperimentAlgorithmError("each confounder must be a non-empty column name")
        name = value.strip()
        if name not in confounders:
            confounders.append(name)
    if treatment == outcome or treatment in confounders or outcome in confounders:
        raise ExperimentAlgorithmError("treatment, outcome, and confounders must be distinct")

    if path is None:
        frame = _causal_fixture()
        source = {"type": "deterministic_fixture", "fixture": "causal_linear_v1"}
    else:
        frame = pd.read_csv(path, nrows=MAX_CAUSAL_ROWS + 1)
        source = {
            "type": "authorized_csv",
            "file_name": path.name,
            "sha256": _sha256(path),
        }
    if len(frame) > MAX_CAUSAL_ROWS:
        raise ExperimentAlgorithmError(f"causal dataset exceeds {MAX_CAUSAL_ROWS} rows")
    required = [treatment, outcome, *confounders]
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise ExperimentAlgorithmError(f"causal dataset is missing columns: {', '.join(missing)}")
    numeric = frame[required].apply(pd.to_numeric, errors="coerce")
    numeric = numeric.replace([np.inf, -np.inf], np.nan).dropna()
    if len(numeric) < max(20, len(required) + 3):
        raise ExperimentAlgorithmError("causal dataset has fewer than 20 valid rows")
    return numeric, treatment, outcome, confounders, source


def run_causal(context: Any) -> Dict[str, Any]:
    """Fit a real OLS adjustment model and persist auditable coefficients."""

    config = _config(context)
    method = str(config.get("method", "linear_regression")).strip().lower()
    if method not in {"linear_regression", "ols"}:
        raise ExperimentAlgorithmError("causal adapter currently supports only OLS")
    path, _ = _source_or_fail(context, config)
    _report(context, 5, "validating causal dataset")
    frame, treatment, outcome, confounders, source = _read_causal_frame(path, config)

    t = frame[treatment].to_numpy(dtype=np.float64)
    y = frame[outcome].to_numpy(dtype=np.float64)
    if float(np.std(t)) <= 1e-12 or float(np.std(y)) <= 1e-12:
        raise ExperimentAlgorithmError("treatment and outcome must have non-zero variance")
    covariates = frame[confounders].to_numpy(dtype=np.float64) if confounders else None
    columns = [np.ones(len(frame), dtype=np.float64), t]
    if covariates is not None:
        columns.extend(covariates[:, position] for position in range(covariates.shape[1]))
    design = np.column_stack(columns)
    if len(frame) <= design.shape[1]:
        raise ExperimentAlgorithmError("causal dataset has insufficient residual degrees of freedom")

    _report(context, 30, "fitting adjusted OLS")
    beta, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
    if int(rank) < design.shape[1]:
        raise ExperimentAlgorithmError("causal design matrix is rank deficient")
    prediction = design @ beta
    residual = y - prediction
    sse = float(residual @ residual)
    centered = y - float(np.mean(y))
    tss = float(centered @ centered)
    if tss <= 1e-15:
        raise ExperimentAlgorithmError("outcome variance is too small for OLS metrics")
    n, parameter_count = design.shape
    degrees_freedom = n - parameter_count
    r2 = 1.0 - sse / tss
    adjusted_r2 = 1.0 - (1.0 - r2) * (n - 1) / degrees_freedom
    residual_variance = sse / degrees_freedom
    covariance = residual_variance * np.linalg.pinv(design.T @ design)
    standard_errors = np.sqrt(np.maximum(np.diag(covariance), 0.0))

    causal_effect = float(beta[1])
    causal_se = float(standard_errors[1])
    if causal_se <= 1e-15:
        raise ExperimentAlgorithmError("causal effect standard error is numerically zero")
    t_statistic = causal_effect / causal_se
    # Two-sided normal approximation, explicitly identified in the artifact.
    p_value_normal = math.erfc(abs(t_statistic) / math.sqrt(2.0))
    naive_design = np.column_stack([np.ones(n, dtype=np.float64), t])
    naive_effect = float(np.linalg.lstsq(naive_design, y, rcond=None)[0][1])
    naive_correlation = float(np.corrcoef(t, y)[0, 1])

    metrics: Dict[str, Any] = {
        "causal_effect": causal_effect,
        "causal_effect_std_error": causal_se,
        "causal_effect_ci95_low": causal_effect - 1.96 * causal_se,
        "causal_effect_ci95_high": causal_effect + 1.96 * causal_se,
        "causal_effect_p_value_normal_approx": p_value_normal,
        "naive_effect": naive_effect,
        "confounding_bias": naive_effect - causal_effect,
        "naive_correlation": naive_correlation,
        "r2": float(r2),
        "adjusted_r2": float(adjusted_r2),
        "residual_rmse": math.sqrt(sse / n),
        "samples": int(n),
        "feature_count": int(parameter_count - 1),
    }
    _require_finite(metrics)

    coefficient_names = ["intercept", treatment, *confounders]
    payload = {
        "schema_version": 1,
        "algorithm": "ordinary_least_squares_adjustment",
        "source": source,
        "columns": {
            "treatment": treatment,
            "outcome": outcome,
            "confounders": confounders,
        },
        "coefficients": {
            name: {"estimate": float(beta[index]), "std_error": float(standard_errors[index])}
            for index, name in enumerate(coefficient_names)
        },
        "inference_note": "p-value and 95% interval use a large-sample normal approximation",
        "metrics": metrics,
    }
    artifact_path = _work_dir(context) / "causal_ols.json"
    artifact_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    artifact = _artifact(artifact_path, "application/json", "causal_model")
    _report(context, 100, "causal OLS completed")
    return {
        "kind": "causal",
        "status": "completed",
        "source": source,
        "metrics": metrics,
        "artifact_path": str(artifact_path),
        "artifact": artifact,
        "artifacts": [artifact],
        "model": {
            "name": str(config.get("model_name", "causal-ols"))[:128],
            "model_type": "causal_regression",
            "framework": "numpy",
            "algorithm": "ordinary_least_squares_adjustment",
        },
    }


def _recsys_fixture() -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for user in range(8):
        for item in range(10):
            # Leave a deterministic quarter of the catalogue unseen per user.
            if (user * 3 + item) % 4 == 0:
                continue
            score = 3.0 + 0.75 * math.sin((user + 1) * (item + 2) * 0.31)
            score += 0.35 * ((user % 3) - 1) + 0.2 * ((item % 4) - 1.5)
            rows.append(
                {
                    "user": f"u{user}",
                    "item": f"solution-{item}",
                    "rating": round(min(5.0, max(1.0, score)), 3),
                }
            )
    return pd.DataFrame(rows)


def _read_ratings(
    path: Optional[Path], config: Mapping[str, Any]
) -> Tuple[pd.DataFrame, str, str, str, Dict[str, Any]]:
    user_column = _nonempty_name(config, "user_column", "user")
    item_column = _nonempty_name(config, "item_column", "item")
    rating_column = _nonempty_name(config, "rating_column", "rating")
    if len({user_column, item_column, rating_column}) != 3:
        raise ExperimentAlgorithmError("user, item, and rating columns must be distinct")
    if path is None:
        frame = _recsys_fixture()
        source = {"type": "deterministic_fixture", "fixture": "industrial_ratings_v1"}
    else:
        frame = pd.read_csv(path, nrows=MAX_RECSYS_RATINGS + 1)
        source = {
            "type": "authorized_csv",
            "file_name": path.name,
            "sha256": _sha256(path),
        }
    if len(frame) > MAX_RECSYS_RATINGS:
        raise ExperimentAlgorithmError(f"ratings dataset exceeds {MAX_RECSYS_RATINGS} rows")
    required = [user_column, item_column, rating_column]
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise ExperimentAlgorithmError(f"ratings dataset is missing columns: {', '.join(missing)}")
    selected = frame[required].copy()
    selected = selected.dropna(subset=[user_column, item_column])
    selected[rating_column] = pd.to_numeric(selected[rating_column], errors="coerce")
    selected = selected.replace([np.inf, -np.inf], np.nan).dropna()
    selected[user_column] = selected[user_column].astype(str)
    selected[item_column] = selected[item_column].astype(str)
    if (selected[user_column].str.len() > 256).any() or (selected[item_column].str.len() > 256).any():
        raise ExperimentAlgorithmError("user and item identifiers are limited to 256 characters")
    selected = (
        selected.groupby([user_column, item_column], as_index=False, sort=True)[rating_column]
        .mean()
        .reset_index(drop=True)
    )
    if len(selected) < 12:
        raise ExperimentAlgorithmError("ratings dataset requires at least 12 distinct interactions")
    users = selected[user_column].nunique()
    items = selected[item_column].nunique()
    if users < 2 or items < 2:
        raise ExperimentAlgorithmError("ratings dataset requires at least two users and two items")
    if users > MAX_USERS or items > MAX_ITEMS:
        raise ExperimentAlgorithmError(
            f"ratings cardinality exceeds limits ({MAX_USERS} users, {MAX_ITEMS} items)"
        )
    return selected, user_column, item_column, rating_column, source


def _validation_split(user_index: np.ndarray, item_index: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    count = len(user_index)
    user_counts = np.bincount(user_index).astype(int)
    item_counts = np.bincount(item_index).astype(int)
    validation: List[int] = []
    for user in range(len(user_counts)):
        candidates = np.flatnonzero(user_index == user)
        # Reverse order makes the selection deterministic without random state.
        for row in candidates[::-1]:
            item = int(item_index[row])
            if user_counts[user] > 2 and item_counts[item] > 1:
                validation.append(int(row))
                user_counts[user] -= 1
                item_counts[item] -= 1
                break
    if not validation:
        raise ExperimentAlgorithmError("ratings data cannot form a coverage-preserving validation split")
    validation_index = np.asarray(sorted(validation), dtype=np.int64)
    mask = np.ones(count, dtype=bool)
    mask[validation_index] = False
    return np.flatnonzero(mask), validation_index


def _predict(
    global_mean: float,
    user_bias: np.ndarray,
    item_bias: np.ndarray,
    user_factors: np.ndarray,
    item_factors: np.ndarray,
    users: np.ndarray,
    items: np.ndarray,
) -> np.ndarray:
    interactions = np.sum(user_factors[users] * item_factors[items], axis=1)
    return global_mean + user_bias[users] + item_bias[items] + interactions


def run_recsys(context: Any) -> Dict[str, Any]:
    """Train deterministic biased matrix factorization with real holdout RMSE."""

    config = _config(context)
    method = str(config.get("method", "matrix_factorization")).strip().lower()
    if method not in {"matrix_factorization", "collaborative", "svd"}:
        raise ExperimentAlgorithmError("recsys adapter supports matrix factorization only")
    factors = _bounded_int(config, "factors", 8, 1, 64)
    epochs = _bounded_int(config, "epochs", 80, 1, 300)
    learning_rate = _bounded_float(config, "learning_rate", 0.02, 1e-5, 0.5)
    regularization = _bounded_float(config, "regularization", 0.05, 0.0, 1.0)
    seed = _bounded_int(config, "seed", 42, 0, 2**31 - 1)
    top_k = _bounded_int(config, "top_k", 5, 1, 50)
    path, _ = _source_or_fail(context, config)
    _report(context, 5, "validating ratings dataset")
    ratings, user_column, item_column, rating_column, source = _read_ratings(path, config)

    user_ids = sorted(ratings[user_column].unique().tolist())
    item_ids = sorted(ratings[item_column].unique().tolist())
    user_lookup = {value: index for index, value in enumerate(user_ids)}
    item_lookup = {value: index for index, value in enumerate(item_ids)}
    user_index = ratings[user_column].map(user_lookup).to_numpy(dtype=np.int64)
    item_index = ratings[item_column].map(item_lookup).to_numpy(dtype=np.int64)
    values = ratings[rating_column].to_numpy(dtype=np.float64)
    rating_min, rating_max = float(values.min()), float(values.max())
    train_index, validation_index = _validation_split(user_index, item_index)

    rng = np.random.RandomState(seed)
    user_factors = rng.normal(0.0, 0.05, size=(len(user_ids), factors))
    item_factors = rng.normal(0.0, 0.05, size=(len(item_ids), factors))
    user_bias = np.zeros(len(user_ids), dtype=np.float64)
    item_bias = np.zeros(len(item_ids), dtype=np.float64)
    global_mean = float(np.mean(values[train_index]))

    _report(context, 15, "training matrix factorization")
    for epoch in range(epochs):
        order = train_index.copy()
        rng.shuffle(order)
        for row in order:
            user = int(user_index[row])
            item = int(item_index[row])
            prediction = (
                global_mean
                + user_bias[user]
                + item_bias[item]
                + float(user_factors[user] @ item_factors[item])
            )
            error = float(values[row] - prediction)
            user_bias[user] += learning_rate * (error - regularization * user_bias[user])
            item_bias[item] += learning_rate * (error - regularization * item_bias[item])
            old_user = user_factors[user].copy()
            user_factors[user] += learning_rate * (
                error * item_factors[item] - regularization * user_factors[user]
            )
            item_factors[item] += learning_rate * (
                error * old_user - regularization * item_factors[item]
            )
        if epoch == epochs - 1 or (epoch + 1) % max(1, epochs // 5) == 0:
            _report(
                context,
                min(85, 15 + int(70 * (epoch + 1) / epochs)),
                f"matrix factorization epoch {epoch + 1}/{epochs}",
            )

    train_prediction = np.clip(
        _predict(
            global_mean,
            user_bias,
            item_bias,
            user_factors,
            item_factors,
            user_index[train_index],
            item_index[train_index],
        ),
        rating_min,
        rating_max,
    )
    validation_prediction = np.clip(
        _predict(
            global_mean,
            user_bias,
            item_bias,
            user_factors,
            item_factors,
            user_index[validation_index],
            item_index[validation_index],
        ),
        rating_min,
        rating_max,
    )
    train_rmse = float(np.sqrt(np.mean((values[train_index] - train_prediction) ** 2)))
    validation_rmse = float(
        np.sqrt(np.mean((values[validation_index] - validation_prediction) ** 2))
    )

    recommend_user = str(config.get("recommend_user_id", user_ids[0]))
    if recommend_user not in user_lookup:
        raise ExperimentAlgorithmError("recommend_user_id is not present in the ratings dataset")
    recommend_user_index = user_lookup[recommend_user]
    all_item_index = np.arange(len(item_ids), dtype=np.int64)
    repeated_users = np.full(len(item_ids), recommend_user_index, dtype=np.int64)
    scores = np.clip(
        _predict(
            global_mean,
            user_bias,
            item_bias,
            user_factors,
            item_factors,
            repeated_users,
            all_item_index,
        ),
        rating_min,
        rating_max,
    )
    observed = set(item_index[user_index == recommend_user_index].tolist())
    candidates = [index for index in range(len(item_ids)) if index not in observed]
    ranked = sorted(candidates, key=lambda index: (-float(scores[index]), item_ids[index]))[:top_k]
    recommendations = [
        {"item_id": item_ids[index], "score": float(scores[index])} for index in ranked
    ]

    metrics: Dict[str, Any] = {
        "train_rmse": train_rmse,
        "validation_rmse": validation_rmse,
        "ratings": int(len(values)),
        "train_ratings": int(len(train_index)),
        "validation_ratings": int(len(validation_index)),
        "users": int(len(user_ids)),
        "items": int(len(item_ids)),
        "factors": int(factors),
        "epochs": int(epochs),
    }
    _require_finite(metrics)

    artifact_path = _work_dir(context) / "recsys_matrix_factorization.npz"
    np.savez_compressed(
        artifact_path,
        schema_version=np.asarray([1], dtype=np.int64),
        user_ids=np.asarray(user_ids, dtype=str),
        item_ids=np.asarray(item_ids, dtype=str),
        user_factors=user_factors,
        item_factors=item_factors,
        user_bias=user_bias,
        item_bias=item_bias,
        global_mean=np.asarray([global_mean], dtype=np.float64),
        rating_range=np.asarray([rating_min, rating_max], dtype=np.float64),
        observed_user_index=user_index,
        observed_item_index=item_index,
    )
    artifact = _artifact(artifact_path, "application/x-npz", "recommendation_model")
    _report(context, 100, "matrix factorization completed")
    return {
        "kind": "recsys",
        "status": "completed",
        "source": source,
        "metrics": metrics,
        "recommendations": {"user_id": recommend_user, "items": recommendations},
        "artifact_path": str(artifact_path),
        "artifact": artifact,
        "artifacts": [artifact],
        "model": {
            "name": str(config.get("model_name", "recsys-matrix-factorization"))[:128],
            "model_type": "recommendation",
            "framework": "numpy",
            "algorithm": "biased_matrix_factorization_sgd",
        },
    }


def recommend_from_artifact(
    artifact_path: Any, user_id: str, top_k: int = 5
) -> Dict[str, Any]:
    """Load a persisted factor model and return deterministic model scores.

    The caller remains responsible for authorizing the artifact path.  Loading is
    deliberately performed with ``allow_pickle=False`` and the helper validates
    shapes/finiteness before using any persisted arrays.
    """

    if isinstance(top_k, bool):
        raise ExperimentAlgorithmError("top_k must be an integer")
    try:
        top_k = int(top_k)
    except (TypeError, ValueError) as exc:
        raise ExperimentAlgorithmError("top_k must be an integer") from exc
    if top_k < 1 or top_k > 50:
        raise ExperimentAlgorithmError("top_k must be between 1 and 50")
    if not isinstance(user_id, str) or not user_id or len(user_id) > 256:
        raise ExperimentAlgorithmError("user_id must be a non-empty string up to 256 characters")
    path = Path(artifact_path)
    if path.suffix.lower() != ".npz" or not path.is_file():
        raise ExperimentAlgorithmError("recommendation model artifact must be an existing NPZ file")
    if path.stat().st_size > 512 * 1024 * 1024:
        raise ExperimentAlgorithmError("recommendation model artifact exceeds 512 MiB")

    required = {
        "schema_version",
        "user_ids",
        "item_ids",
        "user_factors",
        "item_factors",
        "user_bias",
        "item_bias",
        "global_mean",
        "rating_range",
        "observed_user_index",
        "observed_item_index",
    }
    try:
        with np.load(path, allow_pickle=False) as model:
            missing = sorted(required.difference(model.files))
            if missing:
                raise ExperimentAlgorithmError(
                    f"recommendation artifact is missing arrays: {', '.join(missing)}"
                )
            schema_version = np.asarray(model["schema_version"], dtype=np.int64)
            user_ids = np.asarray(model["user_ids"]).astype(str).tolist()
            item_ids = np.asarray(model["item_ids"]).astype(str).tolist()
            user_factors = np.asarray(model["user_factors"], dtype=np.float64)
            item_factors = np.asarray(model["item_factors"], dtype=np.float64)
            user_bias = np.asarray(model["user_bias"], dtype=np.float64)
            item_bias = np.asarray(model["item_bias"], dtype=np.float64)
            global_mean_values = np.asarray(model["global_mean"], dtype=np.float64)
            rating_range = np.asarray(model["rating_range"], dtype=np.float64)
            observed_users = np.asarray(model["observed_user_index"], dtype=np.int64)
            observed_items = np.asarray(model["observed_item_index"], dtype=np.int64)
    except ExperimentAlgorithmError:
        raise
    except Exception as exc:
        raise ExperimentAlgorithmError("recommendation model artifact cannot be safely loaded") from exc

    if schema_version.shape != (1,) or int(schema_version[0]) != 1:
        raise ExperimentAlgorithmError("unsupported recommendation artifact schema")
    if len(user_ids) < 1 or len(item_ids) < 1 or len(set(user_ids)) != len(user_ids):
        raise ExperimentAlgorithmError("recommendation artifact identifiers are invalid")
    if len(set(item_ids)) != len(item_ids):
        raise ExperimentAlgorithmError("recommendation artifact item identifiers are invalid")
    if (
        user_factors.ndim != 2
        or item_factors.ndim != 2
        or user_factors.shape[0] != len(user_ids)
        or item_factors.shape[0] != len(item_ids)
        or user_factors.shape[1] != item_factors.shape[1]
        or user_factors.shape[1] < 1
        or user_bias.shape != (len(user_ids),)
        or item_bias.shape != (len(item_ids),)
        or global_mean_values.shape != (1,)
        or rating_range.shape != (2,)
        or observed_users.shape != observed_items.shape
        or observed_users.ndim != 1
    ):
        raise ExperimentAlgorithmError("recommendation artifact array shapes are invalid")
    numeric_arrays = [
        user_factors,
        item_factors,
        user_bias,
        item_bias,
        global_mean_values,
        rating_range,
    ]
    if not all(np.isfinite(array).all() for array in numeric_arrays):
        raise ExperimentAlgorithmError("recommendation artifact contains non-finite values")
    if (
        (observed_users < 0).any()
        or (observed_users >= len(user_ids)).any()
        or (observed_items < 0).any()
        or (observed_items >= len(item_ids)).any()
    ):
        raise ExperimentAlgorithmError("recommendation artifact interaction indexes are invalid")
    try:
        requested_user = user_ids.index(user_id)
    except ValueError as exc:
        raise ExperimentAlgorithmError("user_id is not present in the recommendation model") from exc

    all_items = np.arange(len(item_ids), dtype=np.int64)
    users = np.full(len(item_ids), requested_user, dtype=np.int64)
    scores = np.clip(
        _predict(
            float(global_mean_values[0]),
            user_bias,
            item_bias,
            user_factors,
            item_factors,
            users,
            all_items,
        ),
        float(rating_range[0]),
        float(rating_range[1]),
    )
    seen_items = set(observed_items[observed_users == requested_user].tolist())
    candidates = [index for index in range(len(item_ids)) if index not in seen_items]
    ranked = sorted(candidates, key=lambda index: (-float(scores[index]), item_ids[index]))[:top_k]
    return {
        "user_id": user_id,
        "items": [{"item_id": item_ids[index], "score": float(scores[index])} for index in ranked],
    }


ADAPTERS: Dict[str, Callable[[Any], Dict[str, Any]]] = {
    "causal": run_causal,
    "recsys": run_recsys,
}


__all__ = [
    "ADAPTERS",
    "ExperimentAlgorithmError",
    "recommend_from_artifact",
    "run_causal",
    "run_recsys",
]
