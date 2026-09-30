import pandas as pd
import pytest

from services.data_cleaning_service import (
    CleaningRuleError,
    DEFAULT_CLEANING_DEFINITION,
    apply_definition,
    parse_rule_file,
)


def test_parse_declarative_python_without_execution():
    source = b'CLEANING_RULES = {"operations": [{"type": "drop_missing", "columns": ["target"]}]}'
    definition, stored = parse_rule_file("rule.py", source)
    assert definition["version"] == 1
    assert definition["operations"][0]["type"] == "drop_missing"
    assert "CLEANING_RULES" in stored


def test_reject_executable_python_rule():
    with pytest.raises(CleaningRuleError, match="不允许"):
        parse_rule_file(
            "unsafe.py",
            b'import os\nCLEANING_RULES = {"operations": [{"type": "drop_missing"}]}',
        )


def test_guided_operations_are_applied_in_order():
    frame = pd.DataFrame(
        {
            "temperature": [100, None, 700, 100],
            "pressure": [" 1.0 ", "2.0", "3.0", " 1.0 "],
            "status": ["ok", "ok", "bad", "ok"],
        }
    )
    definition = {
        "operations": [
            {
                "type": "fill_missing",
                "columns": ["temperature"],
                "method": "median",
            },
            {"type": "clip", "column": "temperature", "min": 0, "max": 500},
            {"type": "trim", "columns": ["pressure"], "mode": "both"},
            {
                "type": "filter_rows",
                "column": "status",
                "operator": "eq",
                "value": "ok",
                "keep_matching": True,
            },
            {
                "type": "drop_duplicates",
                "columns": ["temperature", "pressure", "status"],
                "keep": "first",
            },
        ]
    }
    cleaned, summary = apply_definition(frame, definition)
    assert len(cleaned) == 2
    assert cleaned["temperature"].tolist() == [100.0, 100.0]
    assert cleaned["pressure"].tolist() == ["1.0", "2.0"]
    assert summary["rows_before"] == 4
    assert summary["rows_after"] == 2
    assert summary["operations_executed"] == 5


def test_unknown_column_reports_operation_number():
    frame = pd.DataFrame({"a": [1, 2]})
    with pytest.raises(CleaningRuleError, match="第 1 步"):
        apply_definition(
            frame,
            {"operations": [{"type": "drop_columns", "columns": ["missing"]}]},
        )


def test_default_cleaning_removes_empty_columns_and_sparse_rows():
    frame = pd.DataFrame(
        {"a": [1, None], "b": [2, None], "empty": [None, None]}
    )
    cleaned, summary = apply_definition(frame, DEFAULT_CLEANING_DEFINITION)
    assert cleaned.columns.tolist() == ["a", "b"]
    assert len(cleaned) == 1
    assert summary["columns_removed"] == 1
    assert summary["rows_removed"] == 1
