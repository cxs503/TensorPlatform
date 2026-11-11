#!/usr/bin/env python3
"""HTTP surface of the metrics-tool verification chain (3 verification endpoints).

Design rules
------------
* every endpoint is **pure**: it synthesises its own ground truth from the request
  parameters and returns both the truth and what the tool recovered, so a caller
  can re-derive the verdict without trusting this service;
* malformed input is rejected by pydantic with HTTP 422 -- never clamped, because
  a silently clamped parameter would turn an acceptance run into a passing one;
* no database, no filesystem writes: the service cannot influence the platform
  state it is supposed to measure.

Routes
------
``POST /v1/metrics_verify/pulse``      -- heartbeat latency/loss/freshness bundle
``POST /v1/metrics_verify/chronicle``  -- event-stream integrity bundle
``POST /v1/metrics_verify/portrait``   -- seat profile consistency + drift bundle
``GET  /v1/metrics_verify/spec``       -- the acceptance thresholds (read-only)
``GET  /health``                       -- liveness
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from . import chronicle_integrity, portrait_consistency, pulse_latency
from .sim import FleetConfig

SPEC: Dict[str, Any] = {
    "pulse_latency_mean_max_s": 1.0,
    "pulse_latency_p99_max_s": 5.0,
    "pulse_loss_detection_min": 0.95,
    "chronicle_events": 10000,
    "chronicle_completeness_min": 1.0,
    "chronicle_order_correct_min": 1.0,
    "chronicle_disorder_detection_min": 0.99,
    "portrait_error_max": 0.05,
    "portrait_drift_latency_max_events": 10,
}

app = FastAPI(
    title="TensorPlatform metrics verification",
    version="1.0.0",
    description="Self-contained verification chain for the pulse / chronicle / portrait metrics tools.",
)


# --------------------------------------------------------------------------- #
# request models
# --------------------------------------------------------------------------- #
class PulseRequest(BaseModel):
    n_agents: int = Field(20, ge=1, le=500, description="number of simulated agents")
    n_heartbeats: int = Field(200, ge=10, le=5000, description="heartbeats per agent")
    seed: int = Field(20261111, ge=0, le=2**32 - 1)
    median_latency_s: float = Field(0.08, gt=0.0, le=60.0)
    sigma: float = Field(0.9, ge=0.0, le=3.0)
    stall_rate: float = Field(0.0, ge=0.0, lt=1.0)
    loss_rate: float = Field(0.05, ge=0.0, lt=1.0)
    enabled_fraction: float = Field(1.0, gt=0.0, le=1.0)
    mean_max_s: float = Field(SPEC["pulse_latency_mean_max_s"], gt=0.0, le=600.0)
    p99_max_s: float = Field(SPEC["pulse_latency_p99_max_s"], gt=0.0, le=3600.0)

    @model_validator(mode="after")
    def _mean_below_p99(self) -> "PulseRequest":
        if self.mean_max_s >= self.p99_max_s:
            raise ValueError("mean_max_s must be smaller than p99_max_s")
        if self.stall_rate > 0 and self.loss_rate >= 1.0:  # pragma: no cover - guarded by Field
            raise ValueError("loss_rate must stay below 1")
        return self

    def fleet_config(self) -> FleetConfig:
        return FleetConfig(
            n_agents=self.n_agents,
            n_heartbeats=self.n_heartbeats,
            seed=self.seed,
            median_latency_s=self.median_latency_s,
            sigma=self.sigma,
            stall_rate=self.stall_rate,
            loss_rate=self.loss_rate,
            enabled_fraction=self.enabled_fraction,
        )


class ChronicleRequest(BaseModel):
    n_events: int = Field(10000, ge=10, le=100000)
    sessions: int = Field(5, ge=1, le=64)
    seed: int = Field(20261111, ge=0, le=2**32 - 1)
    disorder_rate: float = Field(0.01, ge=0.0, le=0.5)
    disorder_detect_min: float = Field(SPEC["chronicle_disorder_detection_min"], gt=0.0, lt=1.0)


class PortraitRequest(BaseModel):
    n_agents: int = Field(8, ge=3, le=64, description="worker_portrait needs >=3 active workers")
    missions_per_agent: int = Field(200, ge=10, le=2000)
    seed: int = Field(20261111, ge=0, le=2**32 - 1)
    tolerance: float = Field(SPEC["portrait_error_max"], gt=0.0, lt=1.0)
    injected_status_flips: int = Field(0, ge=0, le=100000)
    drift: bool = True


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #
def _guarded(fn):
    try:
        return fn()
    except ValueError as exc:  # domain validation -> 422, never a 500 or a clamp
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/metrics_verify/pulse")
def verify_pulse(req: PulseRequest) -> Dict[str, Any]:
    """Heartbeat latency distribution, loss detection and freshness cross-check."""
    def run() -> Dict[str, Any]:
        return pulse_latency.run_pulse_verify(
            req.fleet_config(), mean_max_s=req.mean_max_s, p99_max_s=req.p99_max_s,
            loss_detect_min=SPEC["pulse_loss_detection_min"])
    result = _guarded(run)
    result["criteria"] = {
        "pulse_disorder": None,
        "latency_mean_below_ceiling": result["ceiling"]["mean_pass"],
        "latency_p99_below_ceiling": result["ceiling"]["p99_pass"],
        "loss_detection_above_threshold": result["loss"]["pass"],
        "freshness_crosscheck": result["core"]["pass"],
    }
    return result


@app.post("/v1/metrics_verify/chronicle")
def verify_chronicle(req: ChronicleRequest) -> Dict[str, Any]:
    """Event-stream completeness / order / monotonicity verification."""
    def run() -> Dict[str, Any]:
        return chronicle_integrity.run_chronicle_verify(
            n_events=req.n_events, sessions=req.sessions, seed=req.seed,
            disorder_rate=req.disorder_rate,
            disorder_detect_min=req.disorder_detect_min)
    result = _guarded(run)
    result["criteria"] = {
        "honest_stream_integrity": result["honest"]["pass"],
        "disorder_detection": result["disorder"]["pass"],
        "drop_detection": result["drop_control"]["pass"],
        "duplicate_detection": result["duplicate_control"]["pass"],
    }
    return result


@app.post("/v1/metrics_verify/portrait")
def verify_portrait(req: PortraitRequest) -> Dict[str, Any]:
    """Seat-profile consistency (success rate / mean wall / domain mix) + drift latency."""
    def run() -> Dict[str, Any]:
        return portrait_consistency.run_portrait_verify(
            n_agents=req.n_agents, missions_per_agent=req.missions_per_agent,
            seed=req.seed, tolerance=req.tolerance,
            injected_status_flips=req.injected_status_flips, drift=req.drift)
    result = _guarded(run)
    result["criteria"] = {
        "profile_consistency": result["consistency"]["pass"],
        "corruption_detected": result["corruption_control"]["detected"],
        "drift_latency": (result.get("drift") or {}).get("pass"),
    }
    return result


@app.get("/v1/metrics_verify/spec")
def get_spec() -> Dict[str, Any]:
    """Acceptance thresholds this service grades against (single source of truth)."""
    return {"spec": SPEC, "source": "tools/metrics_verify/api.py", "version": app.version}


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "metrics_verify", "endpoints": 3}