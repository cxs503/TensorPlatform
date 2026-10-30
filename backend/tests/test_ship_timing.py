"""Unit tests for ship_timing module."""
import sys
import time
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from services.ship_timing import (
    TimingCollector,
    _percentile,
    _extract_ship_type,
    timed_calc,
    get_collector,
    reset_counters,
)


class TestPercentile:
    def test_single_value(self):
        assert _percentile([5.0], 50) == 5.0

    def test_two_values(self):
        assert _percentile([1.0, 3.0], 50) == 2.0

    def test_empty(self):
        assert _percentile([], 50) == 0.0

    def test_p99_ge_p90_ge_p50(self):
        values = sorted([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
        p50 = _percentile(values, 50)
        p90 = _percentile(values, 90)
        p99 = _percentile(values, 99)
        assert p99 >= p90 >= p50


class TestExtractShipType:
    def test_from_kwargs_object(self):
        class Req:
            ship_type = "SUBOFF"
        assert _extract_ship_type((), {"req": Req()}, "unknown") == "SUBOFF"

    def test_from_args_object(self):
        class Req:
            ship_type = "KVLCC2"
        assert _extract_ship_type((Req(),), {}, "unknown") == "KVLCC2"

    def test_from_kwargs_dict(self):
        assert _extract_ship_type((), {"data": {"ship_type": "DTMB"}}, "unknown") == "DTMB"

    def test_default(self):
        assert _extract_ship_type((), {}, "fallback") == "fallback"


class TestTimingCollector:
    def test_record_and_get(self):
        tc = TimingCollector()
        tc.record("resistance", "SUBOFF", 1.5, 1.2)
        records = tc.get_records()
        assert len(records) == 1
        assert records[0]["endpoint"] == "resistance"
        assert records[0]["ship_type"] == "SUBOFF"
        assert records[0]["wall_clock_ms"] == 1.5

    def test_baseline_percentiles(self):
        tc = TimingCollector()
        for i in range(100):
            tc.record("resistance", "SUBOFF", float(i + 1))
        bl = tc.get_baseline()
        assert bl["resistance"]["count"] == 100
        assert bl["resistance"]["p50"] > 0
        assert bl["resistance"]["p99"] >= bl["resistance"]["p90"] >= bl["resistance"]["p50"]

    def test_clear(self):
        tc = TimingCollector()
        tc.record("resistance", "SUBOFF", 1.0)
        tc.clear()
        assert len(tc.get_records()) == 0

    def test_multiple_endpoints(self):
        tc = TimingCollector()
        tc.record("resistance", "SUBOFF", 1.0)
        tc.record("stability", "KVLCC2", 2.0)
        bl = tc.get_baseline()
        assert "resistance" in bl
        assert "stability" in bl


class TestTimedCalcDecorator:
    def test_sync_function(self):
        reset_counters()
        tc = TimingCollector()
        # Monkey-patch the global collector
        import services.ship_timing as st
        orig = st._collector
        st._collector = tc

        @timed_calc("test_ep")
        def slow_func():
            time.sleep(0.01)
            return 42

        result = slow_func()
        assert result == 42
        records = tc.get_records()
        assert len(records) == 1
        assert records[0]["endpoint"] == "test_ep"
        assert records[0]["wall_clock_ms"] > 0

        st._collector = orig

    @pytest.mark.asyncio
    async def test_async_function(self):
        import asyncio
        reset_counters()
        tc = TimingCollector()
        import services.ship_timing as st
        orig = st._collector
        st._collector = tc

        @timed_calc("async_ep")
        async def async_func():
            await asyncio.sleep(0.01)
            return "done"

        result = await async_func()
        assert result == "done"
        records = tc.get_records()
        assert len(records) == 1
        assert records[0]["endpoint"] == "async_ep"

        st._collector = orig

    def test_sampling(self):
        reset_counters()
        tc = TimingCollector()
        import services.ship_timing as st
        orig = st._collector
        st._collector = tc

        @timed_calc("sampled", sample_interval=5)
        def func():
            return 1

        for _ in range(10):
            func()
        # Should have recorded 2 out of 10 (every 5th)
        assert len(tc.get_records()) == 2

        st._collector = orig
