"""Model training: logistic regression (interpretable baseline) + gradient boosting.

Time-split validation: train on created_at < t_split, test on >= t_split.
Calibration: Platt scaling for LR, isotonic for GBT.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    brier_score_loss,
    roc_auc_score,
    accuracy_score,
)
from sklearn.preprocessing import StandardScaler


FEATURE_COLS = [
    "goal_word_count",
    "criteria_count",
    "parent_depth",
    "repo_historical_loss_rate",
    "concurrency_level",
    "is_mature_single",
    "budget_max_tool_calls",
    "budget_max_wall_seconds",
    "budget_max_tokens",
    "repo_encoded",
    "model_encoded",
]


def _time_split(df: pd.DataFrame, t_split):
    """Split into train/test by created_at < t_split."""
    train = df[df["created_at"] < t_split].copy()
    test = df[df["created_at"] >= t_split].copy()
    return train, test


def _prepare_xy(df: pd.DataFrame, feature_cols: list[str]):
    """Prepare X, y arrays, handling missing values."""
    X = df[feature_cols].copy()
    # Fill NaN with median
    for col in X.columns:
        if X[col].isna().any():
            X[col] = X[col].fillna(X[col].median())
    y = df["label_agent_lost"].values
    return X.values, y


def train_calibrators(
    df: pd.DataFrame,
    t_split,
    output_dir: str | Path = "artifacts/m9912",
) -> dict[str, Any]:
    """Train logistic regression and gradient boosting calibrators.

    Returns dict with trained models, metrics, and calibration info.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    train_df, test_df = _time_split(df, t_split)

    # Assert no temporal overlap
    assert train_df["created_at"].max() < test_df["created_at"].min(), (
        f"Temporal overlap detected: train max={train_df['created_at'].max()}, "
        f"test min={test_df['created_at'].min()}"
    )

    X_train, y_train = _prepare_xy(train_df, FEATURE_COLS)
    X_test, y_test = _prepare_xy(test_df, FEATURE_COLS)

    # Handle edge case: if test set is empty, use last 20% of data
    if len(y_test) == 0:
        split_idx = int(len(df) * 0.8)
        train_df = df.iloc[:split_idx]
        test_df = df.iloc[split_idx:]
        X_train, y_train = _prepare_xy(train_df, FEATURE_COLS)
        X_test, y_test = _prepare_xy(test_df, FEATURE_COLS)
        t_split = train_df["created_at"].max()

    # Scale features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    results = {
        "t_split": str(t_split),
        "n_train": len(y_train),
        "n_test": len(y_test),
        "base_rate": float(y_train.mean()),
        "feature_cols": FEATURE_COLS,
    }

    # ---- Logistic Regression (interpretable baseline) ----
    lr = LogisticRegression(
        C=1.0,
        max_iter=1000,
        class_weight="balanced",
        random_state=42,
        solver="lbfgs",
    )
    lr.fit(X_train_scaled, y_train)

    # Calibrated LR (Platt scaling)
    lr_cal = CalibratedClassifierCV(lr, cv=3, method="sigmoid")
    lr_cal.fit(X_train_scaled, y_train)

    lr_proba = lr_cal.predict_proba(X_test_scaled)[:, 1]
    lr_brier = brier_score_loss(y_test, lr_proba)
    lr_auc = roc_auc_score(y_test, lr_proba) if len(np.unique(y_test)) > 1 else 0.5

    results["logistic_regression"] = {
        "brier_score": float(lr_brier),
        "auc": float(lr_auc),
        "accuracy": float(accuracy_score(y_test, lr_cal.predict(X_test_scaled))),
        "coefficients": dict(zip(FEATURE_COLS, lr.coef_[0].tolist())),
        "intercept": float(lr.intercept_[0]),
    }

    # ---- Gradient Boosting (power model) ----
    gbt = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=3,
        learning_rate=0.1,
        min_samples_leaf=10,
        random_state=42,
    )
    gbt.fit(X_train_scaled, y_train)

    # Calibrated GBT (isotonic)
    gbt_cal = CalibratedClassifierCV(gbt, cv=3, method="isotonic")
    gbt_cal.fit(X_train_scaled, y_train)

    gbt_proba = gbt_cal.predict_proba(X_test_scaled)[:, 1]
    gbt_brier = brier_score_loss(y_test, gbt_proba)
    gbt_auc = roc_auc_score(y_test, gbt_proba) if len(np.unique(y_test)) > 1 else 0.5

    results["gradient_boosting"] = {
        "brier_score": float(gbt_brier),
        "auc": float(gbt_auc),
        "accuracy": float(accuracy_score(y_test, gbt_cal.predict(X_test_scaled))),
        "feature_importances": dict(zip(FEATURE_COLS, gbt.feature_importances_.tolist())),
    }

    # ---- Brier Skill Score vs base rate ----
    base_rate = y_train.mean()
    base_brier = base_rate * (1 - base_rate) + (1 - base_rate) * base_rate  # = 2*p*(1-p) for constant predictor
    # Actually base rate predictor: always predict base_rate
    base_brier = brier_score_loss(y_test, np.full_like(y_test, base_rate, dtype=float))

    lr_brier_skill = 1 - lr_brier / base_brier if base_brier > 0 else 0.0
    gbt_brier_skill = 1 - gbt_brier / base_brier if base_brier > 0 else 0.0

    results["brier_skill"] = {
        "base_brier": float(base_brier),
        "lr_brier_skill": float(lr_brier_skill),
        "gbt_brier_skill": float(gbt_brier_skill),
    }

    # ---- Single-feature baseline (repo frequency only) ----
    # Use only repo_encoded as feature
    X_train_repo = X_train_scaled[:, FEATURE_COLS.index("repo_encoded")].reshape(-1, 1)
    X_test_repo = X_test_scaled[:, FEATURE_COLS.index("repo_encoded")].reshape(-1, 1)

    lr_repo = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
    lr_repo.fit(X_train_repo, y_train)
    repo_proba = lr_repo.predict_proba(X_test_repo)[:, 1]
    repo_auc = roc_auc_score(y_test, repo_proba) if len(np.unique(y_test)) > 1 else 0.5

    results["repo_baseline"] = {
        "auc": float(repo_auc),
        "delta_auc_vs_lr": float(lr_auc - repo_auc),
        "delta_auc_vs_gbt": float(gbt_auc - repo_auc),
    }

    # ---- Label shuffle negative control ----
    y_shuffled = np.random.RandomState(42).permutation(y_train)
    lr_shuffled = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
    lr_shuffled.fit(X_train_scaled, y_shuffled)
    shuffled_proba = lr_shuffled.predict_proba(X_test_scaled)[:, 1]
    shuffled_brier = brier_score_loss(y_test, shuffled_proba)
    shuffled_brier_skill = 1 - shuffled_brier / base_brier if base_brier > 0 else 0.0

    results["label_shuffle_control"] = {
        "brier_score": float(shuffled_brier),
        "brier_skill": float(shuffled_brier_skill),
        "assertion_pass": shuffled_brier_skill <= 0,
    }

    # Save models
    model_dir = output_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=True)

    with open(model_dir / "lr_calibrated.pkl", "wb") as f:
        pickle.dump({"model": lr_cal, "scaler": scaler, "features": FEATURE_COLS}, f)

    with open(model_dir / "gbt_calibrated.pkl", "wb") as f:
        pickle.dump({"model": gbt_cal, "scaler": scaler, "features": FEATURE_COLS}, f)

    # Save results
    with open(output_dir / "training_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    return results


if __name__ == "__main__":
    import os
    from data.extract import extract_mission_dataset

    db_url = os.environ.get("DATABASE_URL", "sqlite:///backend/foundation_ai.db")
    data = extract_mission_dataset(db_url)
    if data["n_missions"] > 0:
        results = train_calibrators(data["df"], data["t_split"])
        print(json.dumps(results, indent=2, default=str))
    else:
        print("No data to train on")
