"""
Ice-Class Hull Form Multi-Objective Optimization API.

Provides REST endpoint for running NSGA-II optimization and retrieving
Pareto front results with visualization data.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/ice_optimization", tags=["冰级船型多目标优化"])


class IceOptimizationRequest(BaseModel):
    """Ice-class optimization request."""
    pop_size: int = Field(default=40, ge=10, le=200, description="Population size")
    n_generations: int = Field(default=50, ge=10, le=500, description="Number of generations")
    seed: int = Field(default=42, description="Random seed")
    use_surrogate: bool = Field(default=True, description="Enable surrogate model acceleration")


class IceOptimizationResponse(BaseModel):
    """Ice-class optimization response."""
    status: str
    n_pareto_solutions: int
    wall_time_s: float
    convergence: dict
    pareto_front: list
    metadata: dict


@router.post("/run", response_model=IceOptimizationResponse)
async def run_ice_optimization(req: IceOptimizationRequest):
    """
    Run ice-class hull form multi-objective optimization.

    NSGA-II driven Pareto front search balancing:
    - Ice resistance (R_ice) at h=1m, V=5kn
    - Open water resistance (R_ow) at V=15kn
    - Stability (GM) — maximized

    Design variables: L, B, T, Cb, α_bow, α_stem (6-dim)
    """
    from optimizer.ice_class.optimization import run_ice_class_optimization

    try:
        result = run_ice_class_optimization(
            pop_size=req.pop_size,
            n_generations=req.n_generations,
            seed=req.seed,
            use_surrogate=req.use_surrogate,
            verbose=False,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Optimization failed: {str(e)}")

    return IceOptimizationResponse(
        status="completed",
        n_pareto_solutions=result["pareto_front"]["n_solutions"],
        wall_time_s=result["metadata"]["wall_time_s"],
        convergence=result["convergence"],
        pareto_front=result["pareto_front"]["solutions"],
        metadata=result["metadata"],
    )


@router.get("/constraints")
async def get_optimization_constraints():
    """Return constraint bounds and IACS UR I requirements."""
    from optimizer.ice_class.constraints import CONSTRAINT_BOUNDS, L_B_RANGE, B_T_RANGE, GM_MIN_ICE
    return {
        "variable_bounds": {k: list(v) for k, v in CONSTRAINT_BOUNDS.items()},
        "L_B_range": list(L_B_RANGE),
        "B_T_range": list(B_T_RANGE),
        "GM_min_ice": GM_MIN_ICE,
        "IACS_UR_I": {
            "description": "IACS UR I Polar Class structural requirements",
            "min_shell_thickness_mm": "0.07 * L^(1/3) + 2.5",
            "frame_spacing_max_mm": 600,
            "min_GM_m": GM_MIN_ICE,
        },
    }


@router.get("/health")
async def health_check():
    """Health check for ice optimization module."""
    return {"status": "healthy", "module": "ice_class_optimization"}
