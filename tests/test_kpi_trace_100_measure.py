"""KPI-TRACE-100 计量实现的单元测试（纯算术口径回归）。

不连库：只验证口径实现（可溯判定/比率/时间戳解析），库侧读数由
artifacts/kpi_trace_100/ 的三条算路交叉验证覆盖。
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "kpi_trace_100_measure.py"


def _load():
    spec = importlib.util.spec_from_file_location("kpi_trace_100_measure", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


M = _load()


def test_traceable_flag_any_channel():
    # 主账 report_id 通道
    assert M.traceable_flag({"link_report_id": "t", "link_report_row": "f"}) is True
    # 报告表通道
    assert M.traceable_flag({"link_report_id": "f", "link_report_row": "t"}) is True
    # 双通道皆无
    assert M.traceable_flag({"link_report_id": "f", "link_report_row": "f"}) is False
    # NULL 视同无
    assert M.traceable_flag({"link_report_id": "", "link_report_row": ""}) is False


def test_core_metrics_matches_definition():
    rows = [
        {"traceable": True}, {"traceable": True}, {"traceable": False}, {"traceable": False},
    ]
    m = M.core_metrics(rows)
    assert m == {"terminal": 4, "traceable": 2, "pct": 50.0}


def test_core_metrics_rounding_and_empty():
    rows = [{"traceable": True}] * 7677 + [{"traceable": False}] * (14085 - 7677)
    m = M.core_metrics(rows)
    assert m["terminal"] == 14085
    assert m["traceable"] == 7677
    assert m["pct"] == 54.5  # 54.5041... -> 2dp
    assert M.core_metrics([]) == {"terminal": 0, "traceable": 0, "pct": None}


def test_parse_ts_handles_psql_copy_formats():
    # psql COPY 实际格式：微秒位数不定 + 无冒号时区
    a = M.parse_ts("2026-10-31 00:34:05.83694+00")
    assert a == datetime(2026, 10, 31, 0, 34, 5, 836940, tzinfo=timezone.utc)
    b = M.parse_ts("2026-11-11 19:18:00+00")
    assert b == datetime(2026, 11, 11, 19, 18, 0, tzinfo=timezone.utc)
    assert M.parse_ts("") is None
    assert M.parse_ts("not-a-timestamp") is None


def test_bool_tristate_nulls_are_false():
    assert M.tf("t") is True
    assert M.tf("f") is False
    assert M.tf("") is False  # NULL（COPY 空串）