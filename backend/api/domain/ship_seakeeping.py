"""Seakeeping (耐波性) API — anchored reference implementation.

POST /api/v1/calculations/seakeeping_rao        RAO curves + hydrostatics + natural periods
POST /api/v1/calculations/seakeeping_spectrum   JONSWAP / PM / ITTC spectrum + moments
POST /api/v1/calculations/seakeeping_response   RAO x spectrum -> sigma / significant amplitudes

Evidence grade: REAL model, WITHHELD experimental accuracy (see services.seakeeping.ACCURACY_NOTE).
The geometry gate is enforced *before* any response is returned: nabla must close on
Cb*L*B*T within tolerance, otherwise the request is rejected (platform rule "Cb/L/B 先验证").
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.seakeeping import (
    ACCURACY_NOTE,
    SeakeepingModel,
    spectrum,
    uniform_grid,
    response_statistics,
)

router = APIRouter(prefix="/calculations", tags=["船舶总体性能计算"])


class HullCondition(BaseModel):
    """Hull + loading condition (geometry is validated before any physics runs)."""

    L: float = Field(150.0, gt=0, description="Length between perpendiculars (m)")
    B: float = Field(22.0, gt=0, description="Breadth moulded (m)")
    T: float = Field(8.0, gt=0, description="Draft (m)")
    Cb: float = Field(0.68, gt=0, le=1.0, description="Block coefficient")
    Cwp: float = Field(0.78, gt=0, le=1.0, description="Waterplane coefficient")
    displacement_t: float = Field(18400.0, gt=0, description="Displacement (t) — must close on Cb*L*B*T*rho")
    KG: float = Field(8.5, gt=0, description="Centre of gravity height (m)")
    GM_T: float = Field(1.5, gt=0, description="Transverse metacentric height (m)")
    rho: float = Field(1025.0, gt=0, description="Water density (kg/m3)")
    KB: Optional[float] = Field(None, gt=0, description="Buoyancy centre height (m); default 0.55*T")
    k_yy: float = Field(0.25, gt=0, description="Pitch radius of gyration / L")
    k_xx: float = Field(0.35, gt=0, description="Roll radius of gyration / B")
    kappa33: float = Field(0.80, gt=0, description="Heave added mass / ship mass")
    kappa55: float = Field(0.25, gt=0, description="Pitch added inertia / I55")
    kappa44: float = Field(0.35, gt=0, description="Roll added inertia / I44")
    zeta33: float = Field(0.20, gt=0, lt=1, description="Heave damping ratio")
    zeta55: float = Field(0.12, gt=0, lt=1, description="Pitch damping ratio")
    zeta44: float = Field(0.10, gt=0, lt=1, description="Roll damping ratio")


class RAORequest(BaseModel):
    hull: HullCondition = Field(default_factory=HullCondition)
    heading_deg: float = Field(180.0, description="0 = following, 180 = head seas")
    speed_ms: float = Field(0.0, ge=0, description="Ship speed (m/s)")
    omega_min: float = Field(0.1, gt=0)
    omega_max: float = Field(2.0, gt=0)
    n_points: int = Field(61, ge=2, le=2001)


class SpectrumRequest(BaseModel):
    kind: str = Field("jonswap", description="pm | jonswap | ittc")
    Hs: float = Field(3.0, gt=0, description="Significant wave height (m)")
    Tp: float = Field(8.0, gt=0, description="Peak period (s); for ITTC this is T1")
    gamma: float = Field(3.3, gt=0, description="JONSWAP peak enhancement")
    omega_min: float = Field(0.05, gt=0)
    omega_max: float = Field(2.5, gt=0)
    n_points: int = Field(201, ge=3, le=4001)


class ResponseRequest(BaseModel):
    hull: HullCondition = Field(default_factory=HullCondition)
    kind: str = Field("jonswap")
    Hs: float = Field(3.0, gt=0)
    Tp: float = Field(8.0, gt=0)
    gamma: float = Field(3.3, gt=0)
    heading_deg: float = Field(180.0)
    speed_ms: float = Field(0.0, ge=0)
    omega_min: float = Field(0.05, gt=0)
    omega_max: float = Field(2.5, gt=0)
    n_points: int = Field(201, ge=3, le=4001)
    mask_invalid: bool = Field(
        False,
        description="exclude the following-sea band with omega_e <= 0 from the response moments",
    )


def _model(hull: HullCondition) -> SeakeepingModel:
    try:
        return SeakeepingModel(**hull.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/seakeeping_rao")
def seakeeping_rao(req: RAORequest) -> Dict[str, object]:
    """Frequency-domain RAO curves for heave / pitch / roll.

    All three curves depend on heading and speed through the encounter frequency and the
    lengthwise phasing factor — an invariance test verifies this explicitly
    (the legacy /seakeeping/rao endpoint returns identical curves for 180 deg/0 kn and
    90 deg/25 kn; see docs/audits/seakeeping_verification_m1433.md).
    """
    model = _model(req.hull)
    omegas = uniform_grid(req.omega_min, req.omega_max, req.n_points)
    curve = model.rao_curve(omegas, heading_deg=req.heading_deg, speed=req.speed_ms)
    return {
        "hydrostatics": model.hydrostatics(),
        "natural_periods_s": model.natural_periods(),
        "natural_frequencies_rad_s": {
            "heave": model.omega3,
            "pitch": model.omega5,
            "roll": model.omega4,
        },
        "heading_deg": req.heading_deg,
        "speed_ms": req.speed_ms,
        "curves": curve,
        "peaks": model.peaks(omegas, heading_deg=req.heading_deg, speed=req.speed_ms),
        "accuracy_note": ACCURACY_NOTE,
    }


@router.post("/seakeeping_spectrum")
def seakeeping_spectrum(req: SpectrumRequest) -> Dict[str, object]:
    """Wave spectrum scaled so that 4*sqrt(m0) == Hs by construction."""
    omegas = uniform_grid(req.omega_min, req.omega_max, req.n_points)
    try:
        spec = spectrum(req.kind, req.Hs, req.Tp, omegas, gamma=req.gamma)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "kind": spec["kind"],
        "omega": spec["omega"],
        "S": spec["S"],
        "m0": spec["m0"],
        "m1": spec["m1"],
        "m2": spec["m2"],
        "Hs_target": spec["Hs_target"],
        "Hs_recovered": spec["Hs_recovered"],
        "Hs_error_pct": 100.0 * (spec["Hs_recovered"] / spec["Hs_target"] - 1.0),
        "Tz_s": spec["Tz_s"],
        "Tm01_s": spec["Tm01_s"],
        "alpha_scale": spec["alpha_scale"],
        "note": "m0/m2 by trapezoid quadrature; Ts/Tp resolution limits accuracy for narrow spectra",
    }


@router.post("/seakeeping_response")
def seakeeping_response(req: ResponseRequest) -> Dict[str, object]:
    """Response statistics: sigma = sqrt(int RAO^2 S dw), significant amplitude = 2 sigma."""
    model = _model(req.hull)
    omegas = uniform_grid(req.omega_min, req.omega_max, req.n_points)
    try:
        spec = spectrum(req.kind, req.Hs, req.Tp, omegas, gamma=req.gamma)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stats = response_statistics(
        model, spec, heading_deg=req.heading_deg, speed=req.speed_ms,
        mask_invalid=req.mask_invalid,
    )
    return {
        "sea_state": {"kind": req.kind, "Hs_m": req.Hs, "Tp_s": req.Tp, "gamma": req.gamma},
        "heading_deg": req.heading_deg,
        "speed_ms": req.speed_ms,
        "hydrostatics": model.hydrostatics(),
        "natural_periods_s": model.natural_periods(),
        "statistics": stats,
        "units": {
            "sigma_heave": "m",
            "significant_heave": "m",
            "sigma_pitch": "deg",
            "significant_pitch": "deg",
            "sigma_roll": "deg",
            "significant_roll": "deg",
            "sigma_vertical_acc": "m/s^2",
        },
        "accuracy_note": ACCURACY_NOTE,
    }