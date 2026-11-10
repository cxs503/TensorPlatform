"""Decision rule and counterfactual split benefit analysis.

Decision rule: P(loss) >= tau -> recommend splitting into <= 2 sub-tasks.
Counterfactual: for historical missions that were lost, estimate what P(loss)
would have been if split, and compute the fraction that would have dropped below tau.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# Default threshold
DEFAULT_TAU = 0.5


def decision_rule(
    p_loss: float,
    tau: float = DEFAULT_TAU,
    budget_uplift_factor: float = 1.5,
) -> dict[str, Any]:
    """Apply decision rule: if P(loss) >= tau, recommend split.

    Returns dict with recommendation and suggested budget adjustment.
    """
    should_split = p_loss >= tau

    recommendation = {
        "p_loss": float(p_loss),
        "tau": float(tau),
        "should_split": bool(should_split),
        "max_subtasks": 2 if should_split else 1,
        "budget_uplift_factor": float(budget_uplift_factor) if should_split else 1.0,
        "recommendation": "SPLIT" if should_split else "PROCEED",
    }

    return recommendation


def counterfactual_split_benefit(
    df: pd.DataFrame,
    t_split,
    lr_model: Any,
    gbt_model: Any,
    tau: float = DEFAULT_TAU,
    split_risk_reduction: float = 0.4,
    output_dir: str | Path = "artifacts/m9912",
) -> dict[str, Any]:
    """Historical replay: what if we had split high-risk missions?

    For each mission that was actually lost (agent_lost), compute:
    1. Original P(loss) at dispatch time
    2. Counterfactual P(loss) if split (reduced by split_risk_reduction factor)
    3. Whether counterfactual P(loss) < tau

    Returns the fraction of lost missions that would have been saved.

    Args:
        split_risk_reduction: assumed risk reduction factor from splitting.
            E.g., 0.4 means P_split = P_original * (1 - 0.4) = 0.6 * P_original.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from .train import FEATURE_COLS, _prepare_xy

    # Focus on missions that were actually lost
    lost_df = df[df["label_agent_lost"] == 1].copy()

    if len(lost_df) == 0:
        return {
            "n_lost_missions": 0,
            "tau": tau,
            "split_risk_reduction": split_risk_reduction,
            "would_be_saved": 0,
            "save_rate": 0.0,
            "details": [],
        }

    X_lost, _ = _prepare_xy(lost_df, FEATURE_COLS)
    lr_scaler = lr_model["scaler"]
    gbt_scaler = gbt_model["scaler"]
    lr_clf = lr_model["model"]
    gbt_clf = gbt_model["model"]

    X_lost_scaled = lr_scaler.transform(X_lost)

    lr_proba = lr_clf.predict_proba(X_lost_scaled)[:, 1]
    gbt_proba = gbt_clf.predict_proba(X_lost_scaled)[:, 1]

    # Use GBT as primary (better performance typically)
    primary_proba = gbt_proba

    # Counterfactual: if split, risk reduces by factor
    counterfactual_proba = primary_proba * (1 - split_risk_reduction)

    # Count how many would have been saved (P_cf < tau)
    would_be_saved = int((counterfactual_proba < tau).sum())
    save_rate = would_be_saved / len(lost_df) if len(lost_df) > 0 else 0.0

    # Detailed analysis
    details = []
    for idx, (_, row) in enumerate(lost_df.iterrows()):
        details.append({
            "mission_id": int(row["id"]),
            "repo": row["repo"],
            "original_p_loss": float(primary_proba[idx]),
            "counterfactual_p_loss": float(counterfactual_proba[idx]),
            "would_be_saved": bool(counterfactual_proba[idx] < tau),
            "created_at": str(row["created_at"]),
        })

    results = {
        "n_lost_missions": len(lost_df),
        "tau": tau,
        "split_risk_reduction": split_risk_reduction,
        "would_be_saved": would_be_saved,
        "save_rate": float(save_rate),
        "mean_original_p_loss": float(primary_proba.mean()),
        "mean_counterfactual_p_loss": float(counterfactual_proba.mean()),
        "details": details,
    }

    # Save results
    with open(output_dir / "counterfactual_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    return results


def generate_split_recommendations(
    df: pd.DataFrame,
    lr_model: Any,
    gbt_model: Any,
    tau: float = DEFAULT_TAU,
) -> pd.DataFrame:
    """Generate split recommendations for all missions in dataset.

    Returns DataFrame with original and recommended actions.
    """
    from .train import FEATURE_COLS, _prepare_xy

    X, _ = _prepare_xy(df, FEATURE_COLS)
    lr_scaler = lr_model["scaler"]
    gbt_scaler = gbt_model["scaler"]
    gbt_clf = gbt_model["model"]

    X_scaled = lr_scaler.transform(X)
    gbt_proba = gbt_clf.predict_proba(X_scaled)[:, 1]

    recommendations = []
    for idx, (_, row) in enumerate(df.iterrows()):
        rec = decision_rule(gbt_proba[idx], tau=tau)
        recommendations.append({
            "mission_id": int(row["id"]),
            "repo": row["repo"],
            "p_loss": float(gbt_proba[idx]),
            "should_split": rec["should_split"],
            "recommendation": rec["recommendation"],
            "max_subtasks": rec["max_subtasks"],
            "budget_uplift_factor": rec["budget_uplift_factor"],
        })

    return pd.DataFrame(recommendations)


if __name__ == "__main__":
    import os
    import pickle
    from ..data.extract import extract_mission_dataset
    from .train import train_calibrators

    db_url = os.environ.get("DATABASE_URL", "sqlite:///backend/foundation_ai.db")
    data = extract_mission_dataset(db_url)
    if data["n_missions"] > 0:
        train_calibrators(data["df"], data["t_split"])
        with open("artifacts/m9912/models/lr_calibrated.pkl", "rb") as f:
            lr_model = pickle.load(f)
        with open("artifacts/m9912/models/gbt_calibrated.pkl", "rb") as f:
            gbt_model = pickle.load(f)
        cf_results = counterfactual_split_benefit(
            data["df"], data["t_split"], lr_model, gbt_model
        )
        print(json.dumps(cf_results, indent=2, default=str))
    else:
        print("No data for counterfactual analysis")
