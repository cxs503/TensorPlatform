"""Manoeuvring (操纵性) calculation endpoint -- MMG 3-DOF standard manoeuvres.

Evidence grade: the model structure and the numerical machinery are REAL
(verified against closed-form solutions in ``tests/feat/test_manoeuvring.py``);
the absolute predictive accuracy is WITHHELD because the hull-force model is a
Newtonian cross-flow model without a sourced/CFD-derived derivative set.  The
response therefore carries an explicit ``accuracy_grade`` and ``caveats`` block
instead of presenting unqualified IMO verdicts.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from methods.manoeuvring import (
    IMO_LIMITS, HullParameters, MMGModel, ModelParameters,
    evaluate_imo, nomoto_indices, run_constant_rudder, steady_state,
    turning_circle_indicators, zigzag,
)

router = APIRouter(tags=["船舶操纵性"])

ACCURACY_GRADE = "WITHHELD"
CAVEATS = [
    "hull forces are a Newtonian cross-flow model: no potential lift term "
    "(Y'_v, N'_v of a real hull are absent), so turning ability is "
    "model-dependent (see docs/verification/MANOEUVRING_MMG_V1.md sensitivity table)",
    "rudder inflow neglects propeller-race acceleration (constant-thrust "
    "assumption), which under-estimates rudder normal force",
    "IMO Res. MSC.137(76) verdicts are ADVISORY for this configuration and must "
    "not be used as a compliance statement until a sourced derivative set is wired in",
]


class ManoeuvringRequest(BaseModel):
    """Principal particulars + standard manoeuvre selection."""

    length: float = Field(..., gt=0, description="L, length between perpendiculars (m)")
    beam: float = Field(..., gt=0, description="B, moulded beam (m)")
    draft: float = Field(..., gt=0, description="T, mean draft (m)")
    cb: float = Field(..., gt=0, le=1, description="block coefficient")
    design_speed: float = Field(..., gt=0, description="U0, approach speed (m/s)")
    displacement_t: Optional[float] = Field(
        default=None, gt=0,
        description="displacement (t); if given it is cross-checked against rho*Cb*L*B*T (3%)")
    water_density: float = Field(default=1025.0, gt=0)
    manoeuvre: str = Field(default="turning_circle",
                           description="turning_circle | zigzag | steady_state")
    rudder_deg: float = Field(default=35.0, gt=0, le=45.0)
    heading_deg: float = Field(default=10.0, gt=0, le=30.0, description="zigzag heading amplitude")
    dt: float = Field(default=0.5, gt=0, le=1.0, description="time step (s)")
    duration: float = Field(default=1800.0, gt=0, le=3600.0)


class ManoeuvringResponse(BaseModel):
    scenario: Dict[str, Any]
    manoeuvre: str
    indicators: Dict[str, float]
    zigzag_overshoots_deg: List[float] = []
    nomoto: Dict[str, float]
    imo: Dict[str, Any]
    accuracy_grade: str
    caveats: List[str]


@router.get("/calculations/manoeuvring/limits")
def manoeuvring_limits() -> Dict[str, Any]:
    """IMO criteria limits and the declared modelling constants."""
    p = ModelParameters()
    return {
        "imo_resolution": "MSC.137(76)",
        "imo_limits": IMO_LIMITS,
        "imo_min_length_m": 100.0,
        "model_parameters": p.__dict__,
        "accuracy_grade": ACCURACY_GRADE,
        "caveats": CAVEATS,
    }


@router.post("/calculations/manoeuvring", response_model=ManoeuvringResponse)
def calculate_manoeuvring(req: ManoeuvringRequest) -> ManoeuvringResponse:
    """Run a standard IMO manoeuvre with the MMG 3-DOF model."""
    try:
        hull = HullParameters(
            length=req.length, beam=req.beam, draft=req.draft, cb=req.cb,
            design_speed=req.design_speed, displacement_t=req.displacement_t,
            water_density=req.water_density,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    model = MMGModel(hull)
    delta = math.radians(req.rudder_deg)
    nomoto = nomoto_indices(model)
    indicators: Dict[str, float] = {}
    overshoots: List[float] = []

    try:
        if req.manoeuvre == "steady_state":
            u, v, r = steady_state(model, delta)
            indicators = {
                "u_steady": u, "v_steady": v, "r_steady": r,
                "steady_radius_over_L": (u / r / hull.length) if r > 1e-12 else float("inf"),
                "drift_angle_deg": math.degrees(math.atan2(-v, u)),
            }
        elif req.manoeuvre == "turning_circle":
            trace = run_constant_rudder(model, delta, req.duration, req.dt)
            indicators = turning_circle_indicators(model, trace)
        elif req.manoeuvre == "zigzag":
            _, overshoots = zigzag(model, req.rudder_deg, req.heading_deg,
                                  dt=req.dt, max_time=req.duration)
            indicators = {"overshoots_deg": overshoots}
        else:
            raise HTTPException(status_code=400,
                                detail=f"unknown manoeuvre '{req.manoeuvre}'")
    except ArithmeticError as exc:
        raise HTTPException(status_code=422, detail=f"steady-state solve failed: {exc}") from exc

    zigzag_10 = overshoots if req.manoeuvre == "zigzag" and req.rudder_deg <= 10.5 else []
    zigzag_20 = overshoots if req.manoeuvre == "zigzag" and req.rudder_deg > 10.5 else []
    imo = evaluate_imo(
        hull.length,
        turning=indicators if req.manoeuvre == "turning_circle" else None,
        zigzag_10=zigzag_10, zigzag_20=zigzag_20,
    )
    return ManoeuvringResponse(
        scenario={
            "L": hull.length, "B": hull.beam, "T": hull.draft, "Cb": hull.cb,
            "U0": hull.design_speed, "displacement_t": req.displacement_t,
            "displacement_from_geometry_t": round(hull.mass / 1000.0, 1),
            "froude": round(hull.froude, 4), "rudder_deg": req.rudder_deg,
            "dt_s": req.dt, "duration_s": req.duration,
        },
        manoeuvre=req.manoeuvre,
        indicators={k: (round(v, 6) if isinstance(v, float) else v) for k, v in indicators.items()},
        zigzag_overshoots_deg=[round(o, 2) for o in overshoots],
        nomoto={k: round(v, 8) for k, v in nomoto.items()},
        imo=imo,
        accuracy_grade=ACCURACY_GRADE,
        caveats=CAVEATS,
    )
