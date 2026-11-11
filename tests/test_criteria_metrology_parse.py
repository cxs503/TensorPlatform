"""四元组模型 + 抽取器测试（零领域语义：变量名为合成符号）。"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import pytest

from tools.criteria_metrology.model import (
    Criterion,
    Interval,
    Metric,
    normalize_criterion,
    relative_error,
)
from tools.criteria_metrology.parse import (
    analyze_numeric_cell,
    numeric_confidence,
    parse_markdown_criteria,
    parse_tolerance,
    tokenize_numbers,
    tolerance_from_header,
)


# --------------------------------------------------------------------------- #
# 模型：安全数学
# --------------------------------------------------------------------------- #


def test_interval_normalizes_bounds():
    iv = Interval(0.6, 0.4)
    assert (iv.lo, iv.hi) == (0.4, 0.6)
    assert iv.width == pytest.approx(0.2)
    assert iv.mid == pytest.approx(0.5)


def test_interval_rejects_nan():
    with pytest.raises(ValueError):
        Interval(float("nan"), 1.0)


def test_relative_error_zero_reference_returns_none_not_inf():
    assert relative_error(1.0, 0.0) is None
    assert relative_error(0.0, 0.0) is None


def test_relative_error_floor_makes_it_bounded():
    val = relative_error(1.0, 1e-300, floor=1.0)
    assert val is not None and math.isfinite(val)
    assert val == pytest.approx(1.0)


def test_relative_error_propagates_none():
    assert relative_error(None, 1.0) is None
    assert relative_error(1.0, None) is None
    assert relative_error(float("nan"), 1.0) is None


def test_normalize_criterion_is_idempotent_and_marks_derived_scale():
    c = Criterion(criterion_id="t", y=2.0, y_ref=0.0, tau=0.03)
    c1 = normalize_criterion(c)
    c2 = normalize_criterion(c1)
    assert c1.scale == pytest.approx(2.0)
    assert c1.scale_source.startswith("derived:")
    assert c2.scale == c1.scale
    assert c2.scale_source == c1.scale_source


def test_criterion_roundtrip_dict():
    c = Criterion(
        criterion_id="t",
        y=1.0,
        y_ref=None,
        tau=0.05,
        metric=Metric.RELATIVE,
        reference_interval=Interval(0.9, 1.1),
        source={"path": "x.md", "line": 3},
    )
    back = Criterion.from_dict(c.as_dict())
    assert back.reference_interval == c.reference_interval
    assert back.metric is Metric.RELATIVE
    assert back.source["line"] == 3


# --------------------------------------------------------------------------- #
# 抽取器：数值结构
# --------------------------------------------------------------------------- #


def test_exponent_is_single_token_not_interval():
    toks = tokenize_numbers("tolerance <1e-10 and 2.881e-15")
    assert [t.value for t in toks] == [1e-10, 2.881e-15]


def test_interval_detected_only_for_bare_dash():
    assert analyze_numeric_cell("0.04-0.06")["relation"] == "interval"
    assert analyze_numeric_cell("0.04 ~ 0.06")["relation"] == "interval"
    # 百分数之间的减号是减法，不是区间
    assert analyze_numeric_cell("20.0%-0.5%")["relation"] != "interval"
    # 成对写法
    assert analyze_numeric_cell("3.8324 vs 3.8317")["relation"] == "pair"


def test_dates_are_masked_before_tokenizing():
    toks = tokenize_numbers("2026-11-07T01:10:00+00:00")
    assert toks == []


def test_tolerance_parsing_variants():
    assert parse_tolerance("<2%")[0] == pytest.approx(0.02)
    assert parse_tolerance("≤3%")[0] == pytest.approx(0.03)
    tau, comp, metric = parse_tolerance("≥15 pass")
    assert tau == pytest.approx(15.0) and comp == ">=" and metric is Metric.ONE_SIDED
    assert parse_tolerance("<1e-10")[0] == pytest.approx(1e-10)


def test_tolerance_from_verdict_header():
    tau, comp = tolerance_from_header("是否 <3%")
    assert tau == pytest.approx(0.03) and comp == "<="
    assert tolerance_from_header("是否 < 3 %")[0] == pytest.approx(0.03)
    assert tolerance_from_header("说明") == (None, None)


def test_numeric_confidence_rejects_prose_cells():
    assert numeric_confidence("0.0175")["is_numeric"]
    assert not numeric_confidence("任务判据：不少于二十个候选、分级不少于八、缺口不少于六")["is_numeric"]


# --------------------------------------------------------------------------- #
# 抽取器：整表
# --------------------------------------------------------------------------- #

TABLE = """
| 关键量 | 基准 | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| q1 | 1.000 | 1.020 | 2.0% | 是 |
| q2 | 0.04 ~ 0.06 | 0.0175 | 56.3% | 否 |
| q3 | 0.0 | 4.2e-09 | — | 是 |
| q4 | 任务判据：不少于二十个 | 见工件 | 0 | 是 |
"""


def test_parse_markdown_table_records_and_rejects():
    rows, rejected = parse_markdown_criteria(TABLE, source={"path": "x.md"})
    assert len(rows) == 3
    assert all(r.tau == pytest.approx(0.03) for r in rows)
    by_id = {r.quantity: r for r in rows}
    assert by_id["q1"].y == pytest.approx(1.02)
    assert by_id["q1"].y_ref == pytest.approx(1.0)
    # 区间基准被识别成 Interval（T3 的结构证据）
    assert by_id["q2"].interval is not None
    assert by_id["q2"].interval.lo == pytest.approx(0.04)
    # 零基准行：y 由误差反推不够（误差列是 "—"），故 y=None 且保留 y_ref=0
    assert by_id["q3"].y_ref == 0.0
    # 散文基准单元格不取数 ⇒ 该行被拒收，且给出原因
    assert any(x["reason"] in ("no_measurement", "no_numeric_reference") for x in rejected)


def test_records_carry_provenance_and_fingerprint():
    rows, _ = parse_markdown_criteria(TABLE, source={"path": "x.md"})
    r = rows[0]
    assert r.source["path"] == "x.md"
    assert r.source["line"] > 0
    assert r.source["row"].startswith("|")
    assert "table_header" in r.source


def test_derived_flag_when_only_error_given():
    text = """
| 量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| q9 | 2.0 | 12.5% | 否 |
"""
    rows, _ = parse_markdown_criteria(text, source={"path": "y.md"})
    assert len(rows) == 1
    rec = rows[0]
    assert rec.derived_y is True
    assert rec.reported_error == pytest.approx(0.125)
    assert rec.y == pytest.approx(2.0 * 1.125)


def test_parser_never_raises_on_garbage():
    rows, rejected = parse_markdown_criteria("| a | b |\n|---|---|\n|\n", source={})
    assert rows == []
    assert isinstance(rejected, list)