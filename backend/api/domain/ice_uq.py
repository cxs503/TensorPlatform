"""
Ice Resistance UQ API endpoint.

Provides REST API for full-scale ice resistance prediction with
Polynomial Chaos Expansion and uncertainty quantification.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.ice_resistance_uq import (
    SHIP_TRIAL_DATA,
    IceResistanceUQResult,
    run_ice_resistance_uq,
    run_all_ships_uq,
    lindqvist_ice_resistance,
    ShipIceParams,
)

router = APIRouter(prefix="/ice-uq", tags=["冰阻力不确定度量化"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class IceResistanceRequest(BaseModel):
    """Deterministic ice resistance calculation request."""
    ship_name: str = Field(default="MV_Arctic", description="Ship identifier")
    ice_thickness_m: float = Field(default=1.0, gt=0, description="Ice thickness (m)")
    ice_strength_MPa: float = Field(default=1.5, gt=0, description="Ice compressive strength (MPa)")
    friction_coeff: float = Field(default=0.15, gt=0, le=1, description="Ice-ship friction coefficient")
    ship_speed_ms: float = Field(default=3.0, gt=0, description="Ship speed (m/s)")


class IceResistanceResponse(BaseModel):
    """Deterministic ice resistance response."""
    ship_name: str
    ice_thickness_m: float
    ice_strength_MPa: float
    friction_coeff: float
    ship_speed_ms: float
    total_resistance_kN: float


class UQRequest(BaseModel):
    """UQ analysis request."""
    ship_name: str = Field(default="MV_Arctic", description="Ship identifier")
    max_level: int = Field(default=3, ge=1, le=6, description="Smolyak sparse grid level")
    max_poly_order: int = Field(default=3, ge=1, le=5, description="Maximum PCE polynomial order")
    n_mc_validation: int = Field(default=10000, ge=100, le=100000, description="MC samples for CI validation")


class UQResponse(BaseModel):
    """UQ analysis response."""
    ship_name: str
    mean_resistance_kN: float
    std_resistance_kN: float
    variance_kN2: float
    ci_lower_kN: float
    ci_upper_kN: float
    ci_confidence: float
    sobol_first_order: Dict[str, float]
    sobol_total_order: Dict[str, float]
    sobol_sum_first_order: float
    sobol_sum_total_order: float
    n_model_evaluations: int
    reference_mean_kN: Optional[float] = None
    reference_std_kN: Optional[float] = None
    mean_error: Optional[float] = None
    std_error: Optional[float] = None
    ci_coverage: Optional[float] = None


class AllShipsUQResponse(BaseModel):
    """UQ results for all ships."""
    results: Dict[str, Any]
    summary: Dict[str, Any]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/resistance", response_model=IceResistanceResponse)
async def calc_ice_resistance(req: IceResistanceRequest):
    """
    Compute deterministic level-ice resistance using Lindqvist (1989) formulation.
    """
    if req.ship_name not in SHIP_TRIAL_DATA:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown ship: {req.ship_name}. Available: {list(SHIP_TRIAL_DATA.keys())}"
        )

    ship = SHIP_TRIAL_DATA[req.ship_name]["params"]
    R = lindqvist_ice_resistance(
        ship, req.ice_thickness_m, req.ice_strength_MPa,
        req.friction_coeff, req.ship_speed_ms
    )

    return IceResistanceResponse(
        ship_name=req.ship_name,
        ice_thickness_m=req.ice_thickness_m,
        ice_strength_MPa=req.ice_strength_MPa,
        friction_coeff=req.friction_coeff,
        ship_speed_ms=req.ship_speed_ms,
        total_resistance_kN=round(R, 4),
    )


@router.post("/uq", response_model=UQResponse)
async def run_uq_analysis(req: UQRequest):
    """
    Run full UQ analysis for ice resistance using PCE with Smolyak quadrature.

    Returns mean, std, 95% CI, and Sobol sensitivity indices.
    """
    if req.ship_name not in SHIP_TRIAL_DATA:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown ship: {req.ship_name}. Available: {list(SHIP_TRIAL_DATA.keys())}"
        )

    result = run_ice_resistance_uq(
        ship_name=req.ship_name,
        max_level=req.max_level,
        max_poly_order=req.max_poly_order,
        n_mc_validation=req.n_mc_validation,
    )

    return UQResponse(
        ship_name=result.ship_name,
        mean_resistance_kN=round(result.mean_resistance_kN, 4),
        std_resistance_kN=round(result.std_resistance_kN, 4),
        variance_kN2=round(result.variance_kN2, 4),
        ci_lower_kN=round(result.ci_lower_kN, 4),
        ci_upper_kN=round(result.ci_upper_kN, 4),
        ci_confidence=result.ci_confidence,
        sobol_first_order={k: round(v, 6) for k, v in result.sobol_first_order.items()},
        sobol_total_order={k: round(v, 6) for k, v in result.sobol_total_order.items()},
        sobol_sum_first_order=round(result.sobol_sum_first_order, 6),
        sobol_sum_total_order=round(result.sobol_sum_total_order, 6),
        n_model_evaluations=result.n_model_evaluations,
        reference_mean_kN=result.reference_mean_kN,
        reference_std_kN=result.reference_std_kN,
        mean_error=round(result.mean_error, 6) if result.mean_error is not None else None,
        std_error=round(result.std_error, 6) if result.std_error is not None else None,
        ci_coverage=round(result.ci_coverage, 6) if result.ci_coverage is not None else None,
    )


@router.post("/uq/all", response_model=AllShipsUQResponse)
async def run_all_ships_uq_analysis():
    """
    Run UQ analysis for all ships in the trial database.

    Returns per-ship results and aggregate summary statistics.
    """
    results = run_all_ships_uq()

    # Build summary
    mean_errors = [r.mean_error for r in results.values() if r.mean_error is not None]
    std_errors = [r.std_error for r in results.values() if r.std_error is not None]
    ci_coverages = [r.ci_coverage for r in results.values() if r.ci_coverage is not None]

    summary = {
        "n_ships": len(results),
        "max_mean_error": max(mean_errors) if mean_errors else None,
        "max_std_error": max(std_errors) if std_errors else None,
        "min_ci_coverage": min(ci_coverages) if ci_coverages else None,
        "mean_error_pass": all(e < 0.15 for e in mean_errors) if mean_errors else False,
        "std_error_pass": all(e < 0.20 for e in std_errors) if std_errors else False,
        "ci_coverage_pass": all(c > 0.90 for c in ci_coverages) if ci_coverages else False,
    }

    # Serialize results
    results_dict = {}
    for name, r in results.items():
        results_dict[name] = {
            "mean_resistance_kN": round(r.mean_resistance_kN, 4),
            "std_resistance_kN": round(r.std_resistance_kN, 4),
            "ci_lower_kN": round(r.ci_lower_kN, 4),
            "ci_upper_kN": round(r.ci_upper_kN, 4),
            "sobol_first_order": {k: round(v, 6) for k, v in r.sobol_first_order.items()},
            "sobol_total_order": {k: round(v, 6) for k, v in r.sobol_total_order.items()},
            "n_model_evaluations": r.n_model_evaluations,
            "mean_error": round(r.mean_error, 6) if r.mean_error is not None else None,
            "std_error": round(r.std_error, 6) if r.std_error is not None else None,
            "ci_coverage": round(r.ci_coverage, 6) if r.ci_coverage is not None else None,
        }

    return AllShipsUQResponse(results=results_dict, summary=summary)


@router.get("/ships")
async def list_available_ships():
    """List available ships with their parameters and nominal conditions."""
    result = {}
    for name, data in SHIP_TRIAL_DATA.items():
        p = data["params"]
        result[name] = {
            "length_m": p.length_m,
            "beam_m": p.beam_m,
            "draft_m": p.draft_m,
            "cb": p.cb,
            "bow_angle_deg": p.bow_angle_deg,
            "displacement_t": p.displacement_t,
            "nominal_ice_thickness_m": data["nominal_ice_thickness_m"],
            "nominal_ice_strength_MPa": data["nominal_ice_strength_MPa"],
            "nominal_friction_coeff": data["nominal_friction_coeff"],
            "nominal_speed_ms": data["nominal_speed_ms"],
            "reference_mean_resistance_kN": data["mean_resistance_kN"],
            "reference_std_resistance_kN": data["std_resistance_kN"],
        }
    return result
