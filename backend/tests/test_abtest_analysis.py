from services.abtest_analysis import (
    analyze_manual_values,
    analyze_observations,
    calculate_sample_size,
    observation_metric,
    stable_variant,
)


def test_stable_variant_is_repeatable():
    first = stable_variant("exp_1", "furnace-7", 50)
    assert first in {"A", "B"}
    assert stable_variant("exp_1", "furnace-7", 50) == first


def test_mae_chooses_lower_value():
    result = analyze_manual_values("mae", [1.0, 1.1, 0.9], [2.0, 2.1, 1.9])
    assert result["significant"] is True
    assert result["best_variant"] == "A"
    assert result["metric_direction"] == "lower"


def test_constant_samples_do_not_emit_nan():
    result = analyze_manual_values("accuracy", [1, 1, 1], [1, 1, 1])
    assert result["p_value"] is None
    assert result["significant"] is False
    assert result["error"]


def test_online_feedback_calculates_mae_from_prediction_and_actual():
    rows = [
        {"variant": "A", "prediction": 10, "actual": 12},
        {"variant": "A", "prediction": 11, "actual": 12},
        {"variant": "B", "prediction": 12, "actual": 12},
        {"variant": "B", "prediction": 12.5, "actual": 12},
    ]
    result = analyze_observations("mae", rows)
    assert result["mean_a"] == 1.5
    assert result["mean_b"] == 0.25


def test_observation_metric_uses_correct_direction_inputs():
    assert observation_metric("accuracy", 1, 1) == 1
    assert observation_metric("mae", 8, 10) == 2


def test_sample_size_rejects_impossible_proportion():
    try:
        calculate_sample_size("accuracy", baseline=0.99, min_effect=0.5)
    except ValueError as exc:
        assert "必须小于1" in str(exc)
    else:
        raise AssertionError("expected invalid proportion to be rejected")


def test_continuous_metric_sample_size_is_positive():
    result = calculate_sample_size("mae", min_effect=0.5)
    assert result["required_per_variant"] > 0
    assert result["total"] == result["required_per_variant"] * 2
