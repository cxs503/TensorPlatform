"""Ship ice-load inversion & structural-health-monitoring API.

``POST /api/v1/calculations/ice-load-inversion``

Takes a strain-gauge frame (either measured, or synthesised from a known ice
load vector for verification) and returns the inverted ice load distribution,
the L-curve diagnostics, the von Mises stress state, the safety factor with its
uncertainty, and the DNV acceptance classification.

The physics lives in :mod:`services.ice_shm`; this module only validates the
request and serialises the result.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.ice_shm import (
    DEFAULT_YIELD_PA,
    STEEL_YIELD_PA,
    BeamSection,
    IceBeltFrameModel,
    invert_ice_load,
    synthesize_strain,
)

router = APIRouter(prefix="/calculations", tags=["船舶冰载荷反演与结构健康监测"])

#: Default ice-belt frame instrumentation (24 strain gauges over a 5 m frame).
DEFAULT_N_GAUGES = 24
DEFAULT_LOAD_POSITIONS_M = [1.0, 2.0, 3.0, 4.0]


class SectionIn(BaseModel):
    """T-stiffener geometry of the ice-belt frame girder."""

    web_height_m: float = Field(default=0.45, gt=0, description="Web height (m)")
    web_thickness_m: float = Field(default=0.012, gt=0, description="Web thickness (m)")
    flange_width_m: float = Field(default=0.12, gt=0, description="Flange width (m)")
    flange_thickness_m: float = Field(default=0.018, gt=0, description="Flange thickness (m)")


class IceLoadInversionRequest(BaseModel):
    """Request body for the ice-load inversion endpoint."""

    # -- structure -------------------------------------------------------
    length_m: float = Field(default=5.0, gt=0, description="Frame span between supports (m)")
    n_elements: int = Field(default=20, ge=2, le=400, description="Number of beam elements")
    section: SectionIn = Field(default_factory=SectionIn)
    boundary: Literal["fixed_fixed", "cantilever"] = Field(
        default="fixed_fixed", description="Frame end conditions"
    )
    steel_grade: str = Field(default="NV_A36", description="DNV steel grade for the yield stress")
    yield_strength_pa: Optional[float] = Field(
        default=None, gt=0, description="Override the yield stress (Pa)"
    )

    # -- instrumentation -------------------------------------------------
    gauge_positions_m: Optional[List[float]] = Field(
        default=None,
        description=f"Gauge abscissae (m); default {DEFAULT_N_GAUGES} evenly spaced gauges",
    )
    load_positions_m: List[float] = Field(
        default_factory=lambda: list(DEFAULT_LOAD_POSITIONS_M),
        description="Candidate ice-load nodes (must coincide with mesh nodes)",
    )
    fibre_offset_m: Optional[float] = Field(
        default=None, description="Signed gauge distance from the neutral axis (m)"
    )

    # -- measurements ----------------------------------------------------
    strain_meas: Optional[List[float]] = Field(
        default=None,
        description="Measured strains; if omitted they are synthesised from loads_true_n",
    )
    strain_unit: Literal["strain", "microstrain"] = Field(
        default="microstrain", description="Unit of strain_meas and sigma_strain"
    )
    sigma_strain: Optional[List[float]] = Field(
        default=None, description="Per-gauge measurement standard deviation (same unit as strain)"
    )
    noise_rel: Optional[float] = Field(
        default=None,
        gt=0,
        le=1.0,
        description="Relative gauge noise used when sigma_strain is not supplied",
    )
    loads_true_n: Optional[List[float]] = Field(
        default=None,
        description="Ground-truth ice loads (N); used to synthesise data and to score the inversion",
    )

    # -- inversion options -----------------------------------------------
    lambda_selection: Literal["l_curve", "fixed"] = Field(
        default="l_curve", description="Tikhonov weight selection strategy"
    )
    lam: Optional[float] = Field(default=None, ge=0, description="Fixed lambda when selected")
    n_mc: int = Field(default=0, ge=0, le=5000, description="Monte-Carlo samples for SF scatter")
    seed: int = Field(default=0, description="Random seed for synthesis / Monte-Carlo")
    include_lcurve_points: bool = Field(
        default=True, description="Return the full L-curve scan arrays"
    )


class IceLoadInversionResponse(BaseModel):
    """Response of the ice-load inversion endpoint."""

    status: str
    structure: Dict[str, Any]
    influence: Dict[str, Any]
    inversion: Dict[str, Any]
    stress: Dict[str, Any]
    safety: Dict[str, Any]
    safety_factor: float
    sigma_sf_pa: Optional[float] = None
    computation_time_ms: float
    provenance: Dict[str, Any]


def _resolve_yield_strength(req: IceLoadInversionRequest) -> float:
    if req.yield_strength_pa is not None:
        return float(req.yield_strength_pa)
    grade = req.steel_grade.upper()
    if grade not in STEEL_YIELD_PA:
        raise HTTPException(
            status_code=422,
            detail=f"unknown steel grade {req.steel_grade!r}; known: {sorted(STEEL_YIELD_PA)}",
        )
    return STEEL_YIELD_PA[grade]


def _default_gauges(length_m: float, n: int = DEFAULT_N_GAUGES) -> List[float]:
    margin = 0.04 * length_m
    return [margin + (length_m - 2 * margin) * i / (n - 1) for i in range(n)]


@router.post("/ice-load-inversion", response_model=IceLoadInversionResponse)
def ice_load_inversion(req: IceLoadInversionRequest) -> IceLoadInversionResponse:
    """Invert an ice load distribution from strain data and assess the structure."""
    t0 = time.perf_counter()

    yield_pa = _resolve_yield_strength(req)
    section = BeamSection(
        web_height_m=req.section.web_height_m,
        web_thickness_m=req.section.web_thickness_m,
        flange_width_m=req.section.flange_width_m,
        flange_thickness_m=req.section.flange_thickness_m,
    )
    try:
        model = IceBeltFrameModel(
            length_m=req.length_m,
            n_elements=req.n_elements,
            section=section,
            boundary=req.boundary,
            fibre_offset_m=req.fibre_offset_m,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    gauges = req.gauge_positions_m if req.gauge_positions_m else _default_gauges(req.length_m)
    if len(set(gauges)) != len(gauges):
        raise HTTPException(status_code=422, detail="gauge_positions_m contains duplicates")
    if len(gauges) <= len(req.load_positions_m):
        raise HTTPException(
            status_code=422,
            detail="the inverse problem needs more gauges than load unknowns",
        )

    try:
        influence = model.build_influence_matrix(req.load_positions_m, gauges, req.fibre_offset_m)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    scale = 1e-6 if req.strain_unit == "microstrain" else 1.0
    loads_true = (
        np.asarray(req.loads_true_n, dtype=float) if req.loads_true_n is not None else None
    )
    if loads_true is not None and loads_true.size != influence.n_load:
        raise HTTPException(
            status_code=422,
            detail=f"loads_true_n must have {influence.n_load} entries",
        )

    sigma_strain: Optional[np.ndarray] = None
    if req.sigma_strain is not None:
        sigma_strain = np.asarray(req.sigma_strain, dtype=float) * scale
        if sigma_strain.size != influence.n_gauge:
            raise HTTPException(
                status_code=422,
                detail=f"sigma_strain must have {influence.n_gauge} entries",
            )
        if np.any(sigma_strain < 0.0):
            raise HTTPException(status_code=422, detail="sigma_strain must be non-negative")

    if req.strain_meas is not None:
        strain = np.asarray(req.strain_meas, dtype=float) * scale
        if strain.size != influence.n_gauge:
            raise HTTPException(
                status_code=422,
                detail=f"strain_meas must have {influence.n_gauge} entries, got {strain.size}",
            )
    elif loads_true is not None:
        noise_rel = 0.05 if req.noise_rel is None else float(req.noise_rel)
        strain, auto_sigma = synthesize_strain(
            influence, loads_true, noise_rel=noise_rel, seed=req.seed
        )
        if sigma_strain is None and noise_rel > 0.0:
            sigma_strain = auto_sigma
    else:
        raise HTTPException(
            status_code=422,
            detail="provide either strain_meas or loads_true_n (synthetic mode)",
        )

    if sigma_strain is None and req.noise_rel is not None:
        sigma_strain = float(req.noise_rel) * np.abs(strain)

    try:
        result = invert_ice_load(
            model,
            strain,
            req.load_positions_m,
            gauges,
            lam=req.lam,
            lambda_selection=req.lambda_selection,
            loads_true_n=loads_true,
            sigma_strain=sigma_strain,
            yield_strength_pa=yield_pa,
            n_mc=req.n_mc,
            seed=req.seed,
            influence=influence,
            fibre_offset_m=req.fibre_offset_m,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    payload = result.as_dict(include_lcurve_points=req.include_lcurve_points)
    elapsed_ms = (time.perf_counter() - t0) * 1e3

    return IceLoadInversionResponse(
        status="ok",
        structure={
            "model": model.info(),
            "yield_strength_pa": yield_pa,
            "steel_grade": req.steel_grade,
            "inertia_m4": section.inertia_m4,
            "section_modulus_m3": section.section_modulus_m3,
        },
        influence=payload["influence"],
        inversion={
            "loads_inverted_n": payload["loads_inverted_n"],
            "loads_true_n": payload["loads_true_n"],
            "total_ice_load_inverted_n": payload["total_ice_load_inverted_n"],
            "residual_norm": payload["residual_norm"],
            "residual_relative": payload["residual_relative"],
            "lambda": payload["lambda"],
            "lambda_selection": payload["lambda_selection"],
            "lcurve": payload["lcurve"],
            "sigma_load_n": payload["sigma_load_n"],
            "noise_level": payload["noise_level"],
            "load_uncertainty": payload["load_uncertainty"],
            "amplification_factor": payload["amplification_factor"],
            "load_relative_error": payload["load_relative_error"],
        },
        stress=payload["stress"],
        safety=payload["safety"],
        safety_factor=payload["safety_factor"],
        sigma_sf_pa=payload["stress"].get("sigma_sf_first_order"),
        computation_time_ms=elapsed_ms,
        provenance={
            "module": "services.ice_shm",
            "formulation": model.formulation,
            "regularisation": "tikhonov + l_curve",
            "safety_factor_criterion": payload["safety"]["criterion"],
            "timing_ms": payload["timing_ms"],
            "n_gauges": len(gauges),
            "n_loads": len(req.load_positions_m),
            "geometry_validated": model.geometry_validation["euler_bernoulli_valid"],
        },
    )


@router.get("/ice-load-inversion/info")
def ice_load_inversion_info() -> Dict[str, Any]:
    """Static description of the model / acceptance criteria (self-describing API)."""
    return {
        "endpoint": "POST /api/v1/calculations/ice-load-inversion",
        "physics": {
            "forward_model": "Hermite-cubic Euler-Bernoulli beam (TensorPhysics beam element)",
            "strain_relation": "eps = H F_ice with H = B K^-1 P",
            "inverse_model": "Tikhonov: argmin ||H F - eps||^2 + lam ||F||^2",
            "lambda_selection": "L-curve (maximum log-log curvature)",
            "stress": "sigma_vm = sqrt(sigma_x^2 + 3 tau^2), sigma_x = -E kappa y, tau = V / A_web",
            "safety_factor": "SF = sigma_yield / max(sigma_vm)",
        },
        "acceptance_bands": {
            "SAFE": "SF >= 1.5",
            "MARGINAL": "1.0 <= SF < 1.5",
            "FAIL": "SF < 1.0",
        },
        "steel_grades": STEEL_YIELD_PA,
        "default_yield_strength_pa": DEFAULT_YIELD_PA,
        "default_gauges": DEFAULT_N_GAUGES,
        "default_load_positions_m": DEFAULT_LOAD_POSITIONS_M,
        "uncertainty": "Cov(F) = A diag(sigma_eps^2) A^T, A = (H^T H + lam I)^-1 H^T",
    }