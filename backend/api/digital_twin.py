"""Versioned API for the ice-region multi-physics digital twin.

Endpoints (all under ``/api/v1/digital-twin/v1``)::

    GET  /health                     orchestrator + surrogate health, latencies
    GET  /surrogates                 registry contract (schemas, versions, fit quality)
    POST /predict                    one surrogate prediction  (sub-100 ms budget)
    POST /state/estimate             ingest a sensor frame -> posterior state
    POST /forecast                   performance envelope for 1 h / 6 h / 24 h
    GET  /version                    version triple + contract fingerprint

Error model: 400 for schema violations, 404 for unknown surrogate names,
503 when the twin is cold and cannot be built, 500 for unexpected failures.
Every response carries the version triple so that clients can pin behaviour.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.digital_twin import (API_VERSION, DT_VERSION, SCHEMA_VERSION,
                                   DigitalTwinError, InputValidationError,
                                   SurrogateNotFound, get_orchestrator)
from services.digital_twin.online_predictor import EnvironmentForecast, VoyagePlan

router = APIRouter(prefix="/digital-twin", tags=["冰区数字孪生"])


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------

class VesselParams(BaseModel):
    L: float = Field(default=180.0, gt=20, le=400)
    B: float = Field(default=32.0, gt=3, le=70)
    T: float = Field(default=11.5, gt=1, le=30)
    Cb: float = Field(default=0.80, gt=0.2, le=0.95)
    KG: float = Field(default=10.0, gt=0.5, le=30)


class PredictRequest(BaseModel):
    surrogate: str = Field(description="resistance | seakeeping | manoeuvring | stability | fatigue")
    inputs: Dict[str, float] = Field(description="surrogate input fields")
    vessel: VesselParams = Field(default_factory=VesselParams)


class SensorFrame(BaseModel):
    gm_meas_m: Optional[float] = None
    speed_ms: Optional[float] = None
    shaft_power_kW: Optional[float] = None
    strain_rms_MPa: Optional[float] = None
    ice_concentration: float = 0.0
    ice_thickness: float = 0.0
    Hs: float = 2.0
    Tp: float = 8.0
    peak_period_s: float = 8.0
    timestamp_s: Optional[float] = None


class EstimateRequest(BaseModel):
    frame: SensorFrame
    vessel: VesselParams = Field(default_factory=VesselParams)


class EnvironmentPayload(BaseModel):
    ice_concentration: Any = 0.0
    ice_thickness: Any = 0.0
    Hs: Any = 2.0
    Tp: Any = 8.0
    wind_ms: Any = 10.0


class ForecastRequest(BaseModel):
    horizon_h: float = Field(default=1.0, gt=0, le=48)
    speed_ms: float = Field(default=7.0, gt=0, le=15)
    heading_deg: float = Field(default=180.0, ge=0, le=360)
    environment: EnvironmentPayload = Field(default_factory=EnvironmentPayload)
    dt_s: float = Field(default=300.0, ge=30, le=3600)
    vessel: VesselParams = Field(default_factory=VesselParams)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _orchestrator(vessel: VesselParams):
    try:
        return get_orchestrator(vessel.L, vessel.B, vessel.T, vessel.Cb, vessel.KG)
    except DigitalTwinError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _versions() -> Dict[str, str]:
    return {"api_version": API_VERSION, "dt_version": DT_VERSION,
            "schema_version": SCHEMA_VERSION}


@router.get("/v1/version")
async def version() -> Dict[str, Any]:
    """Versioned contract triple (clients pin on this)."""
    return {"versions": _versions(), "status": "ok"}


@router.get("/v1/surrogates")
async def list_surrogates(
    L: float = Query(180.0), B: float = Query(32.0), T: float = Query(11.5),
    Cb: float = Query(0.80), KG: float = Query(10.0),
) -> Dict[str, Any]:
    """Registry contract: names, versions, input/output schemas, fit quality."""
    dt = _orchestrator(VesselParams(L=L, B=B, T=T, Cb=Cb, KG=KG))
    dt.warmup()
    return {"versions": dt.versions(), "surrogates": dt.registry.list(),
            "n_surrogates": len(dt.registry),
            "contract_fingerprint": dt.registry.contract_fingerprint()}


@router.get("/v1/health")
async def health(
    L: float = Query(180.0), B: float = Query(32.0), T: float = Query(11.5),
    Cb: float = Query(0.80), KG: float = Query(10.0),
) -> Dict[str, Any]:
    """Orchestrator health with latency percentiles and counters."""
    dt = _orchestrator(VesselParams(L=L, B=B, T=T, Cb=Cb, KG=KG))
    dt.warmup()
    return dt.health()


@router.post("/v1/predict")
async def predict(req: PredictRequest) -> Dict[str, Any]:
    """Predict with one surrogate (latency budget 100 ms, p95 asserted in tests)."""
    dt = _orchestrator(req.vessel)
    dt.warmup()
    try:
        result = dt.registry.safe_predict(req.surrogate, req.inputs)
    except SurrogateNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InputValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"versions": dt.versions(), **result}


@router.post("/v1/state/estimate")
async def estimate(req: EstimateRequest) -> Dict[str, Any]:
    """Fuse one sensor frame into the state estimate (EKF)."""
    dt = _orchestrator(req.vessel)
    dt.warmup()
    try:
        result = dt.ingest(req.frame.model_dump(), now_s=req.frame.timestamp_s)
    except InputValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"versions": dt.versions(), **result}


@router.post("/v1/forecast")
async def forecast(req: ForecastRequest) -> Dict[str, Any]:
    """Performance envelope (power / fuel / damage / GM margin) for the horizon."""
    dt = _orchestrator(req.vessel)
    dt.warmup()
    env = EnvironmentForecast(**req.environment.model_dump())
    plan = VoyagePlan(speed_ms=req.speed_ms, heading_deg=req.heading_deg,
                      horizon_h=req.horizon_h)
    try:
        result = dt.forecast(horizon_h=req.horizon_h, plan=plan, env=env, dt_s=req.dt_s)
    except InputValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result