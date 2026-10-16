# -*- coding: utf-8 -*-
"""Pure-function tests for services.feature_engineering_service.

No DB / no running server. All inputs are pandas DataFrames built in-test.
"""
import numpy as np
import pandas as pd
import pytest

from services.feature_engineering_service import (
    build_recommendations,
    eval_expression,
    rolling_transform,
    run_transformations,
)


# ---------------------------------------------------------------- eval_expression
def test_eval_expression_basic_arithmetic():
    df = pd.DataFrame({"煤粉": [10.0, 20.0], "焦粉": [2.0, 4.0]})
    out = eval_expression("({煤粉}+{焦粉})", df)
    assert list(out) == [12.0, 24.0]


def test_eval_expression_division_and_constants():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [2.0, 4.0, 6.0]})
    out = eval_expression("({a}+1)/({b})", df)
    # expected = (a+1)/b = [1.0, 0.75, 0.666...]
    np.testing.assert_allclose(list(out), [1.0, 0.75, 2.0 / 3.0])


def test_eval_expression_unknown_column_raises():
    df = pd.DataFrame({"a": [1.0]})
    with pytest.raises(ValueError, match="不存在的列"):
        eval_expression("({missing})", df)


def test_eval_expression_rejects_non_numeric_source_instead_of_silently_filling():
    df = pd.DataFrame({"furnace": ["BF_001", "BF_002"]})
    with pytest.raises(ValueError, match="非数值内容"):
        eval_expression("{furnace}+1", df)


def test_eval_expression_rejects_function_calls():
    """Security: arbitrary code execution must be blocked (no function calls)."""
    df = pd.DataFrame({"a": [1.0]})
    with pytest.raises((ValueError, SyntaxError)):
        # __import__ / attribute access style must not parse+eval
        eval_expression("__import__('os')", df)


# --------------------------------------------------------------- rolling_transform
def test_rolling_transform_mean_window():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    out = rolling_transform(s, window=2, agg="mean")
    # first element has no full window -> mean of [1] = 1 (min_periods=1)
    assert out.iloc[0] == pytest.approx(1.0)
    assert out.iloc[1] == pytest.approx(1.5)
    assert out.iloc[3] == pytest.approx(3.5)


def test_rolling_transform_diff_is_first_difference():
    s = pd.Series([10.0, 13.0, 18.0])
    out = rolling_transform(s, window=3, agg="diff")
    np.testing.assert_allclose(out.tolist(), [np.nan, 3.0, 5.0])


def test_rolling_transform_unknown_agg_raises():
    s = pd.Series([1.0, 2.0])
    with pytest.raises(ValueError, match="不支持的聚合类型"):
        rolling_transform(s, window=2, agg="median")


def test_rolling_transform_fill_value_replaces_nan():
    s = pd.Series([1.0, 2.0, 3.0])
    out = rolling_transform(s, window=2, agg="diff", fill_value=-1.0)
    # first diff is NaN -> replaced by -1.0
    assert out.iloc[0] == -1.0
    assert out.iloc[1] == pytest.approx(1.0)


# ----------------------------------------------------------- build_recommendations
def test_recommendations_do_not_generate_industry_formulas():
    df = pd.DataFrame({
        name: [1.0, 2.0]
        for name in ["煤粉", "焦粉", "铁料1", "铁料2", "冷返矿", "环冷罩内温度"]
    })
    result = build_recommendations(df)
    assert result["recommendations"]
    assert all(item["transform"]["type"] != "expression" for item in result["recommendations"])


def test_recommendations_keep_generic_sensor_features():
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=3, freq="h"),
        "temperature": [10.0, 20.0, 30.0],
    })
    result = build_recommendations(df)
    types = {item["transform"]["type"] for item in result["recommendations"]}
    assert {"time_component", "lag", "rolling", "normalize"} <= types


# ---------------------------------------------------------- run_transformations
def test_run_transformations_chains_expression_and_rolling():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "b": [2.0, 4.0, 6.0, 8.0]})
    transformations = [
        {"type": "expression", "name": "sum_ab", "expression": "({a}+{b})"},
        {"type": "rolling", "name": "roll_sum", "source_column": "a",
         "window": 2, "agg": "sum", "fill_value": 0},
    ]
    out = run_transformations(df, transformations)
    # original columns preserved + 2 derived
    assert set(["a", "b", "sum_ab", "roll_sum"]).issubset(out.columns)
    np.testing.assert_allclose(out["sum_ab"].tolist(), [3.0, 6.0, 9.0, 12.0])
    # rolling sum w=2 min_periods=1 -> [1, 3, 5, 7]
    np.testing.assert_allclose(out["roll_sum"].tolist(), [1.0, 3.0, 5.0, 7.0])


def test_run_transformations_rejects_duplicate_column_name():
    df = pd.DataFrame({"a": [1.0]})
    with pytest.raises(ValueError, match="冲突"):
        run_transformations(
            df, [{"type": "expression", "name": "a", "expression": "({a}+1)"}]
        )


def test_run_transformations_rejects_missing_or_invalid_time_column():
    df = pd.DataFrame({"time": ["2026-01-01", "not-a-time"], "a": [1.0, 2.0]})
    transform = [{"type": "rolling", "name": "avg", "source_column": "a", "agg": "mean"}]
    with pytest.raises(ValueError, match="时间列不存在"):
        run_transformations(df, transform, "missing")
    with pytest.raises(ValueError, match="无法解析"):
        run_transformations(df, transform, "time")


def test_run_transformations_rejects_infinity_unless_fill_value_is_configured():
    df = pd.DataFrame({"a": [1.0, 2.0]})
    transform = [{"type": "expression", "name": "ratio", "expression": "{a}/0"}]
    with pytest.raises(ValueError, match="无穷大值"):
        run_transformations(df, transform)

    transform[0]["fill_value"] = 0
    out = run_transformations(df, transform)
    assert out["ratio"].tolist() == [0.0, 0.0]


def test_run_transformations_keeps_partial_warmup_nan_without_crashing():
    df = pd.DataFrame({"a": [1.0, 2.0, 4.0]})
    out = run_transformations(
        df,
        [{"type": "rolling", "name": "delta", "source_column": "a", "agg": "diff"}],
    )
    assert pd.isna(out["delta"].iloc[0])
    assert out["delta"].iloc[1:].tolist() == [1.0, 2.0]
