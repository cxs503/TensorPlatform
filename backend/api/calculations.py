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
from pydantic import AliasChoices, BaseModel, Field

from services.ship_timing import get_collector, timed_calc
from services.stability_reference import (
    box_parameters,
    exact_gz_box,
    wall_sided_limit_deg,
)

router = APIRouter(prefix="/calculations", tags=["船舶总体性能计算"])

# Conservative upper bound of the wall-sided identity when the hull geometry
# (beam / draft / depth) is not supplied, so the exact limit cannot be derived.
# Typical deck-edge immersion angles of slender hulls are 15-25 deg.
DEFAULT_WALL_SIDED_LIMIT_DEG = 25.0


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
    """Stability calculation request.

    ``beam_m`` / ``draft_m`` / ``depth_m`` are optional.  When beam and draft are
    given, the endpoint derives the exact heel limit of the wall-sided identity
    and returns an independent box-barge cross-check arm; otherwise a
    conservative 25 deg limit is assumed.
    """
    ship_type: str = Field(default="generic", description="Hull type identifier")
    displacement_t: float = Field(
        default=1000.0, gt=0, description="Displacement (tonnes)",
        validation_alias=AliasChoices("displacement_t", "displacement"),
    )
    gm_m: float = Field(default=1.0, gt=0, description="Metacentric height (m)")
    kb_m: float = Field(default=2.0, gt=0, description="KB (m)")
    bm_m: float = Field(default=3.0, gt=0, description="BM (m)")
    heel_angle_deg: float = Field(default=10.0, description="Heel angle (degrees)")
    beam_m: Optional[float] = Field(
        default=None, gt=0, description="Beam (m); enables the analytic validity limit",
        validation_alias=AliasChoices("beam_m", "beam"),
    )
    draft_m: Optional[float] = Field(
        default=None, gt=0, description="Draft (m); enables the analytic validity limit",
        validation_alias=AliasChoices("draft_m", "draft"),
    )
    depth_m: Optional[float] = Field(
        default=None, gt=0, description="Moulded depth (m); defaults to 1.5 x draft",
        validation_alias=AliasChoices("depth_m", "depth"),
    )


class StabilityResponse(BaseModel):
    """Stability calculation response."""
    ship_type: str
    heel_angle_deg: float
    righting_arm_m: float
    righting_moment_knm: float
    gm_effective_m: float
    computation_time_ms: float
    # Validity of the wall-sided identity at the requested heel angle.
    wall_sided_valid: bool = True
    heel_validity_limit_deg: Optional[float] = None
    gz_box_exact_m: Optional[float] = None
    validity_note: str = ""


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

    That identity is exact only while the immersed section stays a
    parallel-sided trapezoid (no deck-edge immersion, no bilge emergence).
    Beyond that limit ``tan²(φ)`` makes the arm grow without bound, so the
    response carries an explicit validity verdict (``wall_sided_valid``), the
    angle limit itself when beam/draft are supplied, and an independently
    integrated box-barge arm for cross-checking.
    """
    t0 = time.perf_counter()

    phi_deg = req.heel_angle_deg
    # |phi| = 90 deg is a mathematical singularity of tan(phi)**2 and every
    # finite righting arm has vanished long before it.
    if not (-90.0 < phi_deg < 90.0):
        raise HTTPException(
            status_code=400,
            detail=f"heel_angle_deg={phi_deg} out of range (-90, 90): tan(phi)^2 is unbounded",
        )

    phi = math.radians(phi_deg)
    GM = req.gm_m
    BM = req.bm_m
    disp = req.displacement_t * 1000  # tonnes → kg

    # Wall-sided formula
    GZ = GM * math.sin(phi) + 0.5 * BM * (math.tan(phi) ** 2) * math.sin(phi)

    # Righting moment
    g = 9.81
    RM = disp * g * GZ / 1000  # kN·m

    # -- validity verdict ---------------------------------------------------
    heel_limit: Optional[float] = None
    gz_exact: Optional[float] = None
    if req.beam_m is not None and req.draft_m is not None:
        try:
            heel_limit = wall_sided_limit_deg(req.beam_m, req.draft_m, req.depth_m)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        note_prefix = f"analytic limit atan(2*min(T, D-T)/B) = {heel_limit:.2f} deg"
    else:
        heel_limit = DEFAULT_WALL_SIDED_LIMIT_DEG
        note_prefix = (
            "beam/draft not supplied; conservative default limit "
            f"{DEFAULT_WALL_SIDED_LIMIT_DEG:.0f} deg assumed"
        )

    valid = abs(phi_deg) <= heel_limit + 1e-9
    if valid:
        note = f"within wall-sided domain ({note_prefix})"
    else:
        note = (
            f"OUTSIDE wall-sided domain ({note_prefix}); GZ grows without bound, "
            "do not use for IS-Code areas or GZmax"
        )

    # Independent box-barge cross-check (constant-displacement exact solution).
    if req.beam_m is not None and req.draft_m is not None:
        depth = req.depth_m if req.depth_m is not None else 1.5 * req.draft_m
        kg = req.kb_m + req.bm_m - req.gm_m  # inferred from the supplied hydrostatics
        if depth > req.draft_m and 0.0 < kg < depth:
            gz_exact = round(
                exact_gz_box(req.beam_m, req.draft_m, kg, phi, depth)["gz_m"], 6
            )
            if gz_exact is not None and not valid:
                note += f"; exact box-barge arm at this heel = {gz_exact:.3f} m"
        else:
            note += "; box cross-check skipped (needs 0 < KG < depth, depth > draft)"

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return StabilityResponse(
        ship_type=req.ship_type,
        heel_angle_deg=req.heel_angle_deg,
        righting_arm_m=round(GZ, 6),
        righting_moment_knm=round(RM, 4),
        gm_effective_m=round(GM, 4),
        computation_time_ms=round(elapsed_ms, 3),
        wall_sided_valid=valid,
        heel_validity_limit_deg=round(heel_limit, 3),
        gz_box_exact_m=gz_exact,
        validity_note=note,
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