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

    L = req.length
    B = req.beam
    T = req.draft
    Cb = req.cb
    V = req.speed_ms
    rho = req.water_density
    nu = req.kinematic_viscosity

    # Geometric sanity check
    if not (0.3 < Cb < 1.0):
        raise HTTPException(status_code=400, detail=f"Cb={Cb} out of physical range [0.3, 1.0]")
    if L <= 0 or B <= 0 or T <= 0:
        raise HTTPException(status_code=400, detail="Dimensions must be positive")

    # Wetted surface area (approximation for axisymmetric body)
    S = math.pi * B * L * (0.5 + 0.25 * Cb)

    # Reynolds number
    Re = V * L / nu

    # ITTC-1957 friction line
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2

    # Frictional resistance
    Rf = 0.5 * rho * S * Cf * V ** 2

    # Residual resistance (simplified: function of Fn and Cb)
    g = 9.81
    Fn = V / math.sqrt(g * L)

    # Empirical residual resistance coefficient for SUBOFF-like bodies
    # Based on curve-fitting to experimental data
    Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = 0.5 * rho * S * Cr * V ** 2

    Rt = Rf + Rr

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return ResistanceResponse(
        ship_type=req.ship_type,
        speed_ms=V,
        total_resistance_n=round(Rt, 4),
        frictional_resistance_n=round(Rf, 4),
        residual_resistance_n=round(Rr, 4),
        reynolds_number=round(Re, 2),
        froude_number=round(Fn, 6),
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
