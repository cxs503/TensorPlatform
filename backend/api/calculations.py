"""
Ship performance calculations API.

Provides resistance and stability calculation endpoints with timing instrumentation,
plus a timing baseline endpoint for latency statistics.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.ship_resistance_core import compute_resistance_core
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
    water_density: float = Field(default=998.0, gt=0, description="Water density (kg/m³)")
    kinematic_viscosity: float = Field(default=1.004e-6, gt=0, description="Kinematic viscosity (m²/s)")


class ResistanceResponse(BaseModel):
    """Resistance calculation response."""
    ship_type: str
    speed_ms: float
    total_resistance_n: float
    frictional_resistance_n: float
    residual_resistance_n: float
    reynolds_number: float
    froude_number: float
    # Explicit non-dimensional output. Consumers (e.g. the benchmark gate) need the
    # total resistance coefficient; deriving it from Rt on the caller side requires
    # re-guessing the wetted surface, which is how the gate silently drifted before.
    wetted_surface_m2: float = 0.0
    total_resistance_coeff: float = 0.0
    computation_time_ms: float


class StabilityRequest(BaseModel):
    """Stability calculation request."""
    ship_type: str = Field(default="generic", description="Hull type identifier")
    displacement_t: float = Field(default=1000.0, gt=0, description="Displacement (tonnes)")
    gm_m: float = Field(default=1.0, gt=0, description="Metacentric height (m)")
    kb_m: float = Field(default=2.0, gt=0, description="KB (m)")
    bm_m: float = Field(default=3.0, gt=0, description="BM (m)")
    heel_angle_deg: float = Field(default=10.0, description="Heel angle (degrees)")


class StabilityResponse(BaseModel):
    """Stability calculation response."""
    ship_type: str
    heel_angle_deg: float
    righting_arm_m: float
    righting_moment_knm: float
    gm_effective_m: float
    computation_time_ms: float


# ---------------------------------------------------------------------------
# Resistance calculation (Holtrop-Mennen simplified for SUBOFF)
# ---------------------------------------------------------------------------

@router.post("/resistance", response_model=ResistanceResponse)
@timed_calc("resistance")
async def calc_resistance(req: ResistanceRequest):
    """
    Compute total resistance for a given hull at specified speed.

    Uses a simplified Holtrop-Mennen formulation calibrated for the
    SUBOFF axisymmetric body (DARPA SUBOFF model).
    """
    t0 = time.perf_counter()

    try:
        core = compute_resistance_core(
            length=req.length,
            beam=req.beam,
            draft=req.draft,
            cb=req.cb,
            speed_ms=req.speed_ms,
            water_density=req.water_density,
            kinematic_viscosity=req.kinematic_viscosity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return ResistanceResponse(
        ship_type=req.ship_type,
        speed_ms=req.speed_ms,
        total_resistance_n=round(core["total_resistance_n"], 4),
        frictional_resistance_n=round(core["frictional_resistance_n"], 4),
        residual_resistance_n=round(core["residual_resistance_n"], 4),
        reynolds_number=round(core["reynolds_number"], 2),
        froude_number=round(core["froude_number"], 6),
        wetted_surface_m2=round(core["wetted_surface_m2"], 6),
        total_resistance_coeff=round(core["total_resistance_coeff"], 6),
        computation_time_ms=round(elapsed_ms, 3),
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
