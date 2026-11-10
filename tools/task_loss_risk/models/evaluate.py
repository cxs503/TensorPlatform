"""Evaluation metrics: Brier skill, AUC, ECE, calibration curves.

ECE (Expected Calibration Error) measures the average gap between
predicted probability and actual frequency across probability bins.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score


def compute_ece(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> float:
    """Compute Expected Calibration Error.

    ECE = sum(|bin_i| / N * |acc_i - conf_i|)
    where acc_i = mean(y_true in bin_i), conf_i = mean(y_prob in bin_i).
    """
    bin_boundaries = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    n = len(y_true)

    bin_data = []
    for i in range(n_bins):
        lo, hi = bin_boundaries[i], bin_boundaries[i + 1]
        if i < n_bins - 1:
            mask = (y_prob >= lo) & (y_prob < hi)
        else:
            mask = (y_prob >= lo) & (y_prob <= hi)

        if mask.sum() == 0:
            bin_data.append({
                "bin": i,
                "lo": float(lo),
                "hi": float(hi),
                "count": 0,
                "accuracy": 0.0,
                "confidence": 0.0,
                "gap": 0.0,
            })
            continue

        acc = y_true[mask].mean()
        conf = y_prob[mask].mean()
        gap = abs(acc - conf)
        ece += (mask.sum() / n) * gap

        bin_data.append({
            "bin": i,
            "lo": float(lo),
            "hi": float(hi),
            "count": int(mask.sum()),
            "accuracy": float(acc),
            "confidence": float(conf),
            "gap": float(gap),
        })

    return float(ece), bin_data


def compute_brier_skill(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    base_rate: float,
) -> float:
    """Compute Brier Skill Score vs constant base-rate predictor.

    BSS = 1 - Brier_model / Brier_base
    Brier_base = base_rate * (1 - base_rate) + (1 - base_rate) * base_rate
               = 2 * base_rate * (1 - base_rate)  [for constant predictor]
    Actually: Brier for constant predictor p is: p^2*(1-p) + (1-p)^2*p = p*(1-p)
    Wait, let me recalculate:
    Brier = (1/N) * sum((y_i - p)^2)
    For constant p = base_rate:
    Brier_base = (1/N) * [n_pos*(1-p)^2 + n_neg*(0-p)^2]
               = (1/N) * [n_pos*(1-p)^2 + n_neg*p^2]
               = p*(1-p)^2 + (1-p)*p^2
               = p*(1-p)*[(1-p) + p]
               = p*(1-p)
    """
    brier_model = brier_score_loss(y_true, y_prob)
    brier_base = base_rate * (1 - base_rate)

    if brier_base <= 0:
        return 0.0

    return float(1 - brier_model / brier_base)


def evaluate_calibrators(
    df: pd.DataFrame,
    t_split,
    lr_model: Any,
    gbt_model: Any,
    output_dir: str | Path = "artifacts/m9912",
) -> dict[str, Any]:
    """Evaluate trained calibrators on test set.

    Returns comprehensive evaluation results.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    test_df = df[df["created_at"] >= t_split].copy()
    if len(test_df) == 0:
        split_idx = int(len(df) * 0.8)
        test_df = df.iloc[split_idx:].copy()
        t_split = df.iloc[split_idx - 1]["created_at"]

    from .train import FEATURE_COLS, _prepare_xy

    X_test, y_test = _prepare_xy(test_df, FEATURE_COLS)

    lr_scaler = lr_model["scaler"]
    gbt_scaler = gbt_model["scaler"]
    lr_clf = lr_model["model"]
    gbt_clf = gbt_model["model"]

    X_test_scaled = lr_scaler.transform(X_test)

    lr_proba = lr_clf.predict_proba(X_test_scaled)[:, 1]
    gbt_proba = gbt_clf.predict_proba(X_test_scaled)[:, 1]

    base_rate = y_test.mean()

    # Brier scores
    lr_brier = brier_score_loss(y_test, lr_proba)
    gbt_brier = brier_score_loss(y_test, gbt_proba)

    # Brier skill
    lr_bss = compute_brier_skill(y_test, lr_proba, base_rate)
    gbt_bss = compute_brier_skill(y_test, gbt_proba, base_rate)

    # AUC
    lr_auc = roc_auc_score(y_test, lr_proba) if len(np.unique(y_test)) > 1 else 0.5
    gbt_auc = roc_auc_score(y_test, gbt_proba) if len(np.unique(y_test)) > 1 else 0.5

    # ECE
    lr_ece, lr_bins = compute_ece(y_test, lr_proba)
    gbt_ece, gbt_bins = compute_ece(y_test, gbt_proba)

    results = {
        "n_test": len(y_test),
        "base_rate": float(base_rate),
        "logistic_regression": {
            "brier_score": float(lr_brier),
            "brier_skill": float(lr_bss),
            "auc": float(lr_auc),
            "ece": float(lr_ece),
            "calibration_bins": lr_bins,
        },
        "gradient_boosting": {
            "brier_score": float(gbt_brier),
            "brier_skill": float(gbt_bss),
            "auc": float(gbt_auc),
            "ece": float(gbt_ece),
            "calibration_bins": gbt_bins,
        },
    }

    # Save evaluation results
    with open(output_dir / "evaluation_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    return results


if __name__ == "__main__":
    import os
    import pickle
    from ..data.extract import extract_mission_dataset
    from .train import train_calibrators

    db_url = os.environ.get("DATABASE_URL", "sqlite:///backend/foundation_ai.db")
    data = extract_mission_dataset(db_url)
    if data["n_missions"] > 0:
        train_results = train_calibrators(data["df"], data["t_split"])
        # Load models for evaluation
        with open("artifacts/m9912/models/lr_calibrated.pkl", "rb") as f:
            lr_model = pickle.load(f)
        with open("artifacts/m9912/models/gbt_calibrated.pkl", "rb") as f:
            gbt_model = pickle.load(f)
        eval_results = evaluate_calibrators(data["df"], data["t_split"], lr_model, gbt_model)
        print(json.dumps(eval_results, indent=2, default=str))
    else:
        print("No data to evaluate on")
