"""
Tests for Ship API Proxy Orchestration Layer (M10).
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from services.ship_proxy import (
    ShipProxy,
    ShipProxyError,
    record_timing,
    get_timing_stats,
    timed_call,
    benchmark_validate,
    BENCHMARK_REFERENCE,
)


class TestShipProxy:
    """Test ShipProxy class."""

    def test_proxy_init(self):
        proxy = ShipProxy()
        assert proxy.base_url == "http://localhost:8002/api/v1"
        assert proxy.timeout == 30.0
        assert proxy.max_retries == 3

    def test_proxy_custom_init(self):
        proxy = ShipProxy(base_url="http://test:9000", timeout=10, max_retries=5)
        assert proxy.base_url == "http://test:9000"
        assert proxy.timeout == 10
        assert proxy.max_retries == 5

    @pytest.mark.asyncio
    async def test_resistance_call(self):
        """Test resistance proxy call with mocked httpx."""
        proxy = ShipProxy()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "rt": 259.1,
                "method": "ITTC-57摩擦阻力 + Holtrop剩余阻力",
            }
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("services.ship_proxy.httpx.AsyncClient", return_value=mock_client):
            result = await proxy.resistance({
                "length": 100, "beam": 15, "draft": 5,
                "cb": 0.6, "displacement": 5000, "speed": 12
            })

        assert result["rt"] == 259.1

    @pytest.mark.asyncio
    async def test_stability_call(self):
        """Test stability proxy call with mocked httpx."""
        proxy = ShipProxy()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "gm": 3.062,
                "method": "静水力学理论 + IMO衡准",
            }
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("services.ship_proxy.httpx.AsyncClient", return_value=mock_client):
            result = await proxy.stability({
                "length": 100, "beam": 15, "draft": 5,
                "cb": 0.6, "displacement": 5000, "kg": 6
            })

        assert result["gm"] == 3.062

    @pytest.mark.asyncio
    async def test_proxy_error_handling(self):
        """Test proxy error handling on non-200 response."""
        proxy = ShipProxy(max_retries=1)
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("services.ship_proxy.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ShipProxyError) as exc_info:
                await proxy.resistance({"length": 100})

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_proxy_timeout(self):
        """Test proxy timeout handling."""
        proxy = ShipProxy(max_retries=1, timeout=0.001)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        import httpx
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutError("timeout"))

        with patch("services.ship_proxy.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ShipProxyError) as exc_info:
                await proxy.resistance({"length": 100})

        assert exc_info.value.status_code == 504


class TestTiming:
    """Test timing functionality."""

    def test_record_and_get_timing(self):
        record_timing(10.5)
        record_timing(20.3)
        record_timing(15.0)

        stats = get_timing_stats()
        assert stats["count"] >= 3
        assert stats["p50"] > 0
        assert stats["p90"] > 0
        assert stats["p99"] > 0

    def test_empty_timing(self):
        # Clear timing records by getting stats on empty
        # (In practice, _timing_records is module-level)
        stats = get_timing_stats()
        assert "p50" in stats
        assert "p90" in stats
        assert "p99" in stats


class TestBenchmarkValidate:
    """Test benchmark validation."""

    def test_benchmark_reference_exists(self):
        assert "resistance" in BENCHMARK_REFERENCE
        assert "stability" in BENCHMARK_REFERENCE
        assert "kvlcc2_full" in BENCHMARK_REFERENCE["resistance"]
        assert "suboff_model" in BENCHMARK_REFERENCE["resistance"]

    def test_suboff_honest_fail_not_a_gate(self):
        """SUBOFF is honest-FAIL: it must be excluded from the gate, not given a
        300% tolerance (a threshold that can never fail is not a gate, PB-005)."""
        suboff = BENCHMARK_REFERENCE["resistance"]["suboff_model"]
        assert suboff["tolerance_pct"] <= 15.0
        assert suboff["gate_enabled"] is False
        assert "honest-FAIL" in suboff.get("note", "")

    @pytest.mark.asyncio
    async def test_benchmark_unknown_case(self):
        """Unknown case should return error."""
        result = await benchmark_validate("resistance", "unknown_case")
        assert "error" in result
        assert result["pass"] is False


class TestBatchSweep:
    """Test batch sweep functionality."""

    @pytest.mark.asyncio
    async def test_batch_sweep_param_matrix(self):
        """Test that batch_sweep generates correct Cartesian product."""
        proxy = ShipProxy()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"rt": 100}}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("services.ship_proxy.httpx.AsyncClient", return_value=mock_client):
            results = await proxy.batch_sweep(
                param_matrix={"speed": [10, 12], "cb": [0.5, 0.6]},
                calc_type="resistance",
                concurrent=True,
            )

        # 2 x 2 = 4 combinations
        assert len(results) == 4
        for r in results:
            assert "params" in r
            assert "result" in r
            assert "error" in r
