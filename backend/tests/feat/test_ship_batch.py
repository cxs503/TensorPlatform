"""
Tests for POST /api/v1/calculations/batch_sweep endpoint.

Verifies:
1. Batch submission of 50 cases returns 50 results
2. Result count matches input count
3. Concurrency control (max_concurrent) is respected
4. Error handling for invalid cases
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import httpx

# Ensure the backend directory is on the path
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from api.domain.ship_batch import (
    BatchCase,
    BatchSweepRequest,
    BatchSweepResponse,
    _run_batch,
    _call_resistance,
    router,
)


# ── Helpers ────────────────────────────────────────────────────────

def _make_cases(n: int, ship_type: str = "SUBOFF") -> list[BatchCase]:
    """Generate n test cases with varying speed."""
    cases = []
    for i in range(n):
        speed = 0.5 + i * 0.1
        cases.append(BatchCase(
            ship_type=ship_type,
            params={
                "length": 4.26,
                "beam": 0.508,
                "draft": 0.508,
                "displacement": 0.1,
                "speed": round(speed, 2),
                "cb": 0.66,
            },
        ))
    return cases


class MockResponse:
    """Mock httpx response."""
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {"rt": 0.01, "ct": 0.008}
        self.text = text

    def json(self):
        return self._json


class MockAsyncClient:
    """Mock httpx.AsyncClient that tracks concurrency."""
    def __init__(self, fail_indices=None, delay=0.01):
        self.fail_indices = fail_indices or set()
        self.delay = delay
        self.call_count = 0
        self.max_concurrent_observed = 0
        self._current_concurrent = 0
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url, json=None, timeout=None):
        async with self._lock:
            self._current_concurrent += 1
            if self._current_concurrent > self.max_concurrent_observed:
                self.max_concurrent_observed = self._current_concurrent
            idx = self.call_count
            self.call_count += 1

        await asyncio.sleep(self.delay)

        async with self._lock:
            self._current_concurrent -= 1

        if idx in self.fail_indices:
            return MockResponse(status_code=500, text="Internal Server Error")
        return MockResponse(status_code=200, json_data={
            "rt": 0.01 + idx * 0.001,
            "ct": 0.008,
            "reynolds_number": 1e6,
            "froude_number": 0.1,
        })


# ── Tests ──────────────────────────────────────────────────────────

class TestBatchSweepUnit:
    """Unit tests for batch sweep logic."""

    @pytest.mark.asyncio
    async def test_50_cases_return_50_results(self):
        """Submit 50 cases and verify 50 results are returned."""
        cases = _make_cases(50)
        mock_client = MockAsyncClient()

        with patch("api.domain.ship_batch.httpx.AsyncClient", return_value=mock_client):
            response = await _run_batch(cases, max_concurrent=5)

        assert response.total == 50
        assert len(response.results) == 50
        assert response.success == 50
        assert response.failed == 0

    @pytest.mark.asyncio
    async def test_result_count_matches_input(self):
        """Verify result array length matches input cases length for various sizes."""
        for n in [1, 5, 10, 25, 50]:
            cases = _make_cases(n)
            mock_client = MockAsyncClient()

            with patch("api.domain.ship_batch.httpx.AsyncClient", return_value=mock_client):
                response = await _run_batch(cases, max_concurrent=3)

            assert response.total == n, f"Expected {n} results, got {response.total}"
            assert len(response.results) == n

    @pytest.mark.asyncio
    async def test_concurrency_control_respected(self):
        """Verify that max_concurrent limits the number of parallel requests."""
        cases = _make_cases(20)
        mock_client = MockAsyncClient(delay=0.05)

        with patch("api.domain.ship_batch.httpx.AsyncClient", return_value=mock_client):
            response = await _run_batch(cases, max_concurrent=3)

        # The semaphore should limit concurrency to 3
        assert mock_client.max_concurrent_observed <= 3, (
            f"Observed {mock_client.max_concurrent_observed} concurrent, expected <= 3"
        )
        assert response.total == 20

    @pytest.mark.asyncio
    async def test_concurrency_default_5(self):
        """Verify default max_concurrent=5 works correctly."""
        cases = _make_cases(15)
        mock_client = MockAsyncClient(delay=0.02)

        with patch("api.domain.ship_batch.httpx.AsyncClient", return_value=mock_client):
            response = await _run_batch(cases, max_concurrent=5)

        assert mock_client.max_concurrent_observed <= 5
        assert response.total == 15

    @pytest.mark.asyncio
    async def test_serial_mode(self):
        """Verify max_concurrent=1 runs serially."""
        cases = _make_cases(10)
        mock_client = MockAsyncClient(delay=0.02)

        with patch("api.domain.ship_batch.httpx.AsyncClient", return_value=mock_client):
            response = await _run_batch(cases, max_concurrent=1)

        assert mock_client.max_concurrent_observed <= 1
        assert response.concurrency_mode == "serial"
        assert response.total == 10

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Verify that failed cases are reported as errors without crashing."""
        cases = _make_cases(10)
        mock_client = MockAsyncClient(fail_indices={2, 5, 7})

        with patch("api.domain.ship_batch.httpx.AsyncClient", return_value=mock_client):
            response = await _run_batch(cases, max_concurrent=3)

        assert response.total == 10
        assert response.success == 7
        assert response.failed == 3

        # Check error results have error messages
        error_results = [r for r in response.results if r.status == "error"]
        assert len(error_results) == 3
        for er in error_results:
            assert er.error is not None
            assert len(er.error) > 0

    @pytest.mark.asyncio
    async def test_elapsed_time_tracked(self):
        """Verify each result has elapsed_ms tracked."""
        cases = _make_cases(5)
        mock_client = MockAsyncClient(delay=0.01)

        with patch("api.domain.ship_batch.httpx.AsyncClient", return_value=mock_client):
            response = await _run_batch(cases, max_concurrent=2)

        for r in response.results:
            assert r.elapsed_ms >= 0
        assert response.elapsed_ms > 0


class TestBatchSweepEndpoint:
    """Integration-style tests for the FastAPI endpoint."""

    @pytest.mark.asyncio
    async def test_request_model_validation(self):
        """Test that the request model validates correctly."""
        req = BatchSweepRequest(
            cases=_make_cases(5),
            max_concurrent=3,
        )
        assert len(req.cases) == 5
        assert req.max_concurrent == 3

    @pytest.mark.asyncio
    async def test_request_model_default_concurrency(self):
        """Test default max_concurrent is 5."""
        req = BatchSweepRequest(cases=_make_cases(1))
        assert req.max_concurrent == 5

    @pytest.mark.asyncio
    async def test_response_model_structure(self):
        """Test response model has all required fields."""
        resp = BatchSweepResponse(
            total=10,
            success=8,
            failed=2,
            elapsed_ms=100.0,
            concurrency_mode="concurrent",
            results=[],
        )
        assert resp.total == 10
        assert resp.success == 8
        assert resp.failed == 2
        assert resp.concurrency_mode == "concurrent"
