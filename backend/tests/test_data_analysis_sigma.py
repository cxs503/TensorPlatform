import numpy as np
import pandas as pd

from services.data_analysis_service import analyze_frame


def test_three_sigma_and_variable_roles_are_reported():
    values = np.r_[np.zeros(100), 20.0]
    frame = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(values), freq="min"),
        "feature_a": values,
        "feature_b": np.arange(len(values), dtype=float),
        "target": np.arange(len(values), dtype=float) * 2,
        "batch": ["A"] * len(values),
    })

    result = analyze_frame(
        frame,
        source_name="test",
        analyses=["anomaly"],
        numeric_columns=["feature_a", "feature_b", "target"],
        time_column="timestamp",
        target_column="target",
        algorithm_params={"anomaly": {"sigma_multiplier": 3}},
    )

    roles = result["variable_roles"]
    assert roles["dependent"] == "target"
    assert roles["independent"] == ["feature_a", "feature_b"]
    assert roles["time"] == "timestamp"
    assert roles["excluded"] == ["batch"]

    three_sigma = result["analyses"]["anomaly"]["three_sigma"]
    feature = next(item for item in three_sigma["columns"] if item["column"] == "feature_a")
    assert three_sigma["sigma_multiplier"] == 3
    assert feature["count"] == 1
    assert feature["upper"] < 20
