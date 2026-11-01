"""
Ship performance calculations API.

Provides resistance and stability calculation endpoints with timing instrumentation,
plus a timing baseline endpoint for latency statistics.

Mission m1388: the resistance physics moved to :mod:`services.ship_resistance`
(pure, importable, single source of truth) so the benchmark validation chain can
call it in-process instead of over an HTTP hop whose schema never matched.
Numerical output of ``POST /calculations/resistance`` is unchanged.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.ship_resistance import (
    DEFAULT_KINEMATIC_VISCOSITY,
    DEFAULT_WATER_DENSITY,
    resistance_breakdown,
)
from services.ship_timing import get_collector, timed_calc

router = APIRouter(prefix="/calculations", tags=["船舶总体性能计算"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ResistanceRequest(BaseModel):
    """Resistance calculation request."""
    ship_type: str = Field(default="SUBOFF", description="Hull type identifier")
    speed_ms: float = Field(default=1.0, gt=0, description="Speed in m/s")
    length: float = Field(default=4.356, gt=0, description="Length overall (m)")
    beam: float = Field(default=0.508, gt=0, description="Beam (m)")
    draft: float = Field(default=0.476, gt=0, description="Draft (m)")
    cb: float = Field(default=0.542, gt=0, le=1, description="Block coefficient")
    water_density: float = Field(default=DEFAULT_WATER_DENSITY, gt=0, description="Water density (kg/m³)")
    kinematic_viscosity: float = Field(default=DEFAULT_KINEMATIC_VISCOSITY, gt=0, description="Kinematic viscosity (m²/s)")


class ResistanceResponse(BaseModel):
    ship_type: str
    speed_ms: float
    total_resistance_n: float
    frictional_resistance_n: float
    residual_resistance_n: float
    reynolds_number: float
    froude_number: float
    computation_time_ms: float
    # m1388: explicit provenance so consumers can see which model produced the
    # numbers (the benchmark chain previously assumed a ``method`` field that
    # did not exist and silently received nothing).
    method: str = "ITTC-1957 friction + empirical residual (axisymmetric-body fit)"
    wetted_surface_m2: Optional[float] = None
    total_resistance_coefficient: Optional[float] = None


class StabilityRequest(BaseModel):
    """Stability calculation request."""
    ship_type: str = Field(default="generic", description="Hull type identifier")
    displacement_t: float = Field(default=1000.0, gt=0, description="Displacement (tonnes)")
    gm_m: float = Field(default=1.0, gt=0, description="Metacentric height (m)")
    kb_m: float = Field(default=2.0, gt=0, description="KB (m)")
    bm_m: float = Field(default=3.0, gt=0, description="BM (m)")
    heel_angle_deg: float = Field(default=10.0, description="Heel angle (degrees)")


class StabilityResponse(BaseModel):
    ship_type: str
    heel_angle_deg: float
    righting_arm_m: float
    righting_moment_knm: float
    gm_effective_m: float
    computation_time_ms: float


# ---------------------------------------------------------------------------
# Resistance calculation (physics in services.ship_resistance)
# ---------------------------------------------------------------------------

@router.post("/resistance", response_model=ResistanceResponse)
@timed_calc("resistance")
async def calc_resistance(req: ResistanceRequest):
    """
    Compute total resistance for a given hull at specified speed.

    Model: ITTC-1957 friction line + empirical residual-resistance fit
    calibrated on SUBOFF-like axisymmetric bodies (see
    ``services/ship_resistance.py`` for the equations and applicability note).
    """
    t0 = time.perf_counter()

    L, B, T, Cb = req.length, req.beam, req.draft, req.cb
    V, rho, nu = req.speed_ms, req.water_density, req.kinematic_viscosity

    # Geometric sanity check
    if not (0.3 < Cb < 1.0):
        raise HTTPException(status_code=400, detail=f"Cb={Cb} out of physical range [0.3, 1.0]")
    if L <= 0 or B <= 0 or T <= 0:
        raise HTTPException(status_code=400, detail="Dimensions must be positive")

    res = resistance_breakdown(length=L, beam=B, draft=T, cb=Cb, speed=V, rho=rho, nu=nu)

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return ResistanceResponse(
        ship_type=req.ship_type,
        speed_ms=V,
        total_resistance_n=round(res["rt_n"], 4),
        frictional_resistance_n=round(res["rf_n"], 4),
        residual_resistance_n=round(res["rr_n"], 4),
        reynolds_number=round(res["reynolds"], 2),
        froude_number=round(res["froude"], 6),
        computation_time_ms=round(elapsed_ms, 3),
        wetted_surface_m2=round(res["wetted_surface_m2"], 6),
        total_resistance_coefficient=round(res["ct"], 8),
    )


# ---------------------------------------------------------------------------
# Stability calculation (simplified GZ curve)
# ---------------------------------------------------------------------------

@router.post("/stability", response_model=StabilityResponse)
@timed_calc("stability")
async def calc_stability(req: StabilityRequest):
    """
    Compute righting arm and moment at a given heel angle.

    Uses the wall-sided formula: GZ = GM·sin(φ) + ½·BM·tan²(φ)·sin(φ)
    """
    t0 = time.perf_counter()

    phi = math.radians(req.heel_angle_deg)
    GM = req.gm_m
    BM = req.bm_m
    disp = req.displacement_t * 1000  # tonnes → kg

    # Wall-sided formula
    GZ = GM * math.sin(phi) + 0.5 * BM * (math.tan(phi) ** 2) * math.sin(phi)

    # Righting moment
    g = 9.81
    RM = disp * g * GZ / 1000  # kN·m

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return StabilityResponse(
        ship_type=req.ship_type,
        heel_angle_deg=req.heel_angle_deg,
        righting_arm_m=round(GZ, 6),
        righting_moment_knm=round(RM, 4),
        gm_effective_m=round(GM, 4),
        computation_time_ms=round(elapsed_ms, 3),
    )


# ---------------------------------------------------------------------------
# Timing baseline endpoint
# ---------------------------------------------------------------------------

@router.get("/timing_baseline")
async def get_timing_baseline():
    """
    Return p50/p90/p99 wall-clock latency statistics per endpoint.

    Response format:
    {
      "timing_records": [...],
      "baseline": {
        "resistance": {"count": N, "p50": ..., "p90": ..., "p99": ...},
        "stability":  {"count": M, "p50": ..., "p90": ..., "p99": ...}
      },
      "total_records": K
    }
    """
    collector = get_collector()
    records = collector.get_records()
    baseline = collector.get_baseline()

    return {
        "timing_records": records,
        "baseline": baseline,
        "total_records": len(records),
    }