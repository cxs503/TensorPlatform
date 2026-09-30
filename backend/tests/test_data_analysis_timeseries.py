import numpy as np
import pandas as pd

from services.data_analysis_service import analyze_frame


def test_time_quality_mutual_information_and_drift_are_real_computations():
    rng = np.random.default_rng(42)
    full_time = pd.date_range("2026-01-01", periods=160, freq="h")
    kept = np.delete(np.arange(len(full_time)), [20, 21, 90])
    time = full_time[kept]
    nonlinear_feature = np.linspace(-3, 3, len(time))
    target = nonlinear_feature ** 2 + rng.normal(0, 0.03, len(time))
    drifting_feature = np.r_[
        rng.normal(0, 0.1, len(time) // 2),
        rng.normal(4, 0.1, len(time) - len(time) // 2),
    ]
    frame = pd.DataFrame({
        "timestamp": time,
        "target_time": time + pd.Timedelta(hours=3),
        "nonlinear_feature": nonlinear_feature,
        "drifting_feature": drifting_feature,
        "target": target,
    }).sample(frac=1, random_state=7).reset_index(drop=True)

    result = analyze_frame(
        frame,
        source_name="time-series-test",
        analyses=["time_quality", "mutual_information", "time_drift", "lag"],
        time_column="timestamp",
        target_column="target",
        algorithm_params={"lag": {"max_lag": 6, "interpolation_limit": 2}},
    )["analyses"]

    quality = result["time_quality"]
    assert quality["backward_jumps"] > 0
    assert quality["sample_interval_seconds"] == 3600
    assert quality["missing_time_points"] == 3
    assert quality["time_offsets"][0]["median_seconds"] == 10800

    mutual = result["mutual_information"]
    nonlinear = next(item for item in mutual["items"] if item["column"] == "nonlinear_feature")
    assert nonlinear["mutual_information"] > 0.5
    assert abs(nonlinear["pearson"]) < 0.1

    drift = result["time_drift"]
    drifting = next(item for item in drift["items"] if item["column"] == "drifting_feature")
    assert drifting["level"] == "significant"
    assert drifting["psi"] >= 0.25

    lag = result["lag"]
    assert lag["sample_interval_seconds"] == 3600
    assert lag["resampled"] is True
    assert lag["items"][0]["correlations"][1]["lag_label"] == "1小时"
