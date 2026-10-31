"""
Ship batch sweep domain module.

Provides POST /calculations/batch_sweep — accepts a JSON array of cases,
each containing ship_type + params, and calls the ship resistance API
for each case with concurrency control.
"""

import asyncio
import logging
import os
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calculations", tags=["船舶批量计算"])

# Ship performance platform base URL (default port 8002)
SHIP_API_BASE = os.environ.get("SHIP_API_BASE", "http://localhost:8002")
RESISTANCE_ENDPOINT = f"{SHIP_API_BASE}/api/v1/calculations/resistance"

# Timeout for the entire batch (seconds).  If exceeded we degrade to serial.
BATCH_TIMEOUT_SECONDS = int(os.environ.get("BATCH_TIMEOUT_SECONDS", "5000"))


# ── Request / Response models ──────────────────────────────────────

class BatchCase(BaseModel):
    """A single case in the batch sweep."""
    ship_type: str = Field(..., description="Ship type, e.g. SUBOFF, DTMB")
    params: dict[str, Any] = Field(..., description="Parameters for resistance calculation")


class BatchSweepRequest(BaseModel):
    """Request body for POST /calculations/batch_sweep."""
    cases: list[BatchCase] = Field(..., description="Array of cases to compute")
    max_concurrent: int = Field(default=5, ge=1, le=100, description="Max concurrent requests")


class BatchCaseResult(BaseModel):
    """Result for a single case."""
    index: int
    ship_type: str
    status: str  # "ok" | "error"
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    elapsed_ms: float = 0.0


class BatchSweepResponse(BaseModel):
    """Response for POST /calculations/batch_sweep."""
    total: int
    success: int
    failed: int
    elapsed_ms: float
    concurrency_mode: str  # "concurrent" | "serial" | "degraded_serial"
    results: list[BatchCaseResult]


# ── Core logic ─────────────────────────────────────────────────────

async def _call_resistance(
    client: httpx.AsyncClient,
    index: int,
    case: BatchCase,
    semaphore: asyncio.Semaphore,
) -> BatchCaseResult:
    """Call the resistance API for a single case with semaphore-based concurrency control."""
    payload = {"ship_type": case.ship_type, **case.params}
    t0 = time.monotonic()
    async with semaphore:
        try:
            resp = await client.post(RESISTANCE_ENDPOINT, json=payload, timeout=60.0)
            elapsed = (time.monotonic() - t0) * 1000
            if resp.status_code == 200:
                return BatchCaseResult(
                    index=index,
                    ship_type=case.ship_type,
                    status="ok",
                    data=resp.json(),
                    elapsed_ms=round(elapsed, 2),
                )
            else:
                return BatchCaseResult(
                    index=index,
                    ship_type=case.ship_type,
                    status="error",
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    elapsed_ms=round(elapsed, 2),
                )
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return BatchCaseResult(
                index=index,
                ship_type=case.ship_type,
                status="error",
                error=str(exc)[:200],
                elapsed_ms=round(elapsed, 2),
            )


async def _run_batch(
    cases: list[BatchCase],
    max_concurrent: int,
) -> BatchSweepResponse:
    """Execute the batch sweep with concurrency control and timeout degradation."""
    t_start = time.monotonic()
    semaphore = asyncio.Semaphore(max_concurrent)
    concurrency_mode = "concurrent" if max_concurrent > 1 else "serial"

    async with httpx.AsyncClient() as client:
        try:
            results: list[BatchCaseResult] = await asyncio.wait_for(
                asyncio.gather(
                    *[_call_resistance(client, i, c, semaphore) for i, c in enumerate(cases)]
                ),
                timeout=BATCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Batch sweep timed out after %ds — degrading to serial execution",
                BATCH_TIMEOUT_SECONDS,
            )
            concurrency_mode = "degraded_serial"
            # Re-run serially (semaphore=1)
            serial_sem = asyncio.Semaphore(1)
            results = []
            for i, c in enumerate(cases):
                r = await _call_resistance(client, i, c, serial_sem)
                results.append(r)

    elapsed = (time.monotonic() - t_start) * 1000
    success = sum(1 for r in results if r.status == "ok")
    failed = sum(1 for r in results if r.status == "error")

    return BatchSweepResponse(
        total=len(results),
        success=success,
        failed=failed,
        elapsed_ms=round(elapsed, 2),
        concurrency_mode=concurrency_mode,
        results=results,
    )


# ── Endpoint ───────────────────────────────────────────────────────

@router.post("/batch_sweep", response_model=BatchSweepResponse)
async def batch_sweep(req: BatchSweepRequest):
    """
    Batch sweep: submit multiple resistance calculation cases at once.

    Accepts a JSON body with:
    - cases: array of {ship_type, params}
    - max_concurrent: max parallel requests (default 5)

    Returns results array with per-case status.
    If the batch exceeds the timeout (5000s), it degrades to serial.
    """
    if not req.cases:
        raise HTTPException(status_code=400, detail="cases array must not be empty")

    response = await _run_batch(req.cases, req.max_concurrent)
    return response
