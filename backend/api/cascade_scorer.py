"""
Multi-Fidelity Cascade Scorer API
=================================

Endpoints
---------
* ``POST /cascade/scorer/run``    — run a cascade scoring session
* ``GET  /cascade/scorer/config`` — get default configuration
* ``POST /cascade/scorer/config`` — validate a custom configuration
* ``GET  /cascade/scorer/info``   — capability info

All endpoints are domain-agnostic.  Scoring functions are injected by the
caller via the request body (parameter keys + direction) or by registering
domain-specific callables out-of-band.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.cascade_scorer import (
    Candidate,
    CascadeConfig,
    CascadeScorer,
    make_noise_scorer,
    make_synthetic_scorer,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cascade", tags=["多保真级联打分器"])


# ── request / response models ──────────────────────────────────────────


class ScorerSpec(BaseModel):
    """Specification for a synthetic scorer built from a parameter key."""

    param_key: str = Field(..., description="Parameter name to score on")
    direction: str = Field(
        "min", description="'min' (lower=better) or 'max' (higher=better)"
    )
    noise_std: float = Field(
        0.0, description="Gaussian noise std (surrogate uncertainty)"
    )
    seed: int = Field(42, description="RNG seed for noise")


class GateResidualsInput(BaseModel):
    """Physics-consistency residuals for a candidate at a strictness level."""

    mass: float = Field(0.0, description="Mass conservation relative residual")
    momentum: float = Field(
        0.0, description="Momentum conservation relative residual"
    )
    energy: float = Field(
        0.0, description="Energy conservation relative residual"
    )


class CandidateWithGates(BaseModel):
    """Candidate with pre-computed physics-consistency residuals."""

    id: str
    params: Dict[str, float]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    gate_basic: GateResidualsInput = Field(default_factory=GateResidualsInput)
    gate_full: GateResidualsInput = Field(default_factory=GateResidualsInput)
    gate_strict: GateResidualsInput = Field(default_factory=GateResidualsInput)


class CascadeRunRequest(BaseModel):
    """Request body for ``POST /cascade/scorer/run``."""

    candidates: List[CandidateWithGates] = Field(
        ..., description="Candidates to score"
    )
    low_scorer: ScorerSpec = Field(..., description="Low-fidelity scorer spec")
    medium_scorer: ScorerSpec = Field(
        ..., description="Medium-fidelity scorer spec"
    )
    high_scorer: ScorerSpec = Field(
        ..., description="High-fidelity scorer spec"
    )
    config: Optional[Dict[str, Any]] = Field(
        None, description="Override default cascade config"
    )


class StageResultOutput(BaseModel):
    level: str
    n_input: int
    n_passed: int
    n_eliminated: int
    n_gate_failed: int
    n_budget_capped: int = 0
    cost_spent: float
    elapsed_s: float


class CascadeRunResponse(BaseModel):
    """Response for a cascade run."""

    run_id: str
    stages: List[StageResultOutput]
    total_cost: float
    total_elapsed_s: float
    budget: float
    budget_remaining: float
    n_candidates: int
    n_final: int
    elimination_rate_low_medium: float
    winners: List[Dict[str, Any]] = Field(default_factory=list)
    config: Dict[str, Any]


class ConfigResponse(BaseModel):
    """Configuration response."""

    config: Dict[str, Any]
    description: str


# ── endpoints ──────────────────────────────────────────────────────────


@router.get("/scorer/info", response_model=Dict[str, Any])
async def scorer_info():
    """Return capability information."""
    return {
        "name": "multi-fidelity-cascade-scorer",
        "version": "1.0.0",
        "description": (
            "Low→Medium→High fidelity three-level filtering with "
            "physics-consistency gates and budget-adaptive sampling"
        ),
        "stages": [
            {
                "level": "LOW",
                "description": "Analytical/empirical formulas, quick screening",
                "default_cost": 1.0,
            },
            {
                "level": "MEDIUM",
                "description": "Reduced-order/surrogate models, preliminary ranking",
                "default_cost": 10.0,
            },
            {
                "level": "HIGH",
                "description": "Full-order CFD/experiment, final arbitration",
                "default_cost": 1000.0,
            },
        ],
        "features": [
            "physics_consistency_gate (mass/momentum/energy)",
            "budget_adaptive_sampling",
            "budget_capped_evaluations",
            "configurable_thresholds",
        ],
        "target_elimination_low_medium": ">=80%",
    }


@router.get("/scorer/config", response_model=ConfigResponse)
async def get_default_config():
    """Return the default cascade configuration."""
    cfg = CascadeConfig()
    return ConfigResponse(
        config=cfg.to_dict(),
        description=(
            "Default configuration: low_pass_rate=0.50, "
            "medium_pass_rate=0.40, budget_adaptive=True"
        ),
    )


@router.post("/scorer/config", response_model=ConfigResponse)
async def validate_config(config: Dict[str, Any]):
    """Validate a custom configuration and return normalised values."""
    try:
        cfg = CascadeConfig.from_dict(config)
        return ConfigResponse(
            config=cfg.to_dict(),
            description="Configuration validated successfully",
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid configuration: {exc}",
        )


@router.post("/scorer/run", response_model=CascadeRunResponse)
async def run_cascade(request: CascadeRunRequest):
    """Run a multi-fidelity cascade scoring session.

    The request provides candidates with pre-computed physics-consistency
    residuals and scorer specifications (parameter key + direction + noise).
    The cascade filters candidates through low → medium → high fidelity
    stages, applying physics gates and budget-adaptive sampling.
    """
    if not request.candidates:
        raise HTTPException(
            status_code=422, detail="At least one candidate is required"
        )

    # Build config
    cfg = (
        CascadeConfig.from_dict(request.config)
        if request.config
        else CascadeConfig()
    )

    # Build candidates
    candidates: List[Candidate] = []
    for ci in request.candidates:
        c = Candidate(id=ci.id, params=ci.params, metadata=ci.metadata)
        # pre-store gate residuals
        c.gate_residuals["basic"] = {
            "mass": ci.gate_basic.mass,
            "momentum": ci.gate_basic.momentum,
            "energy": ci.gate_basic.energy,
        }
        c.gate_residuals["full"] = {
            "mass": ci.gate_full.mass,
            "momentum": ci.gate_full.momentum,
            "energy": ci.gate_full.energy,
        }
        c.gate_residuals["strict"] = {
            "mass": ci.gate_strict.mass,
            "momentum": ci.gate_strict.momentum,
            "energy": ci.gate_strict.energy,
        }
        candidates.append(c)

    # Build scorers from specs
    def _build_scorer(spec: ScorerSpec):
        base = make_synthetic_scorer(spec.param_key, spec.direction)
        if spec.noise_std > 0:
            return make_noise_scorer(base, spec.noise_std, spec.seed)
        return base

    low_fn = _build_scorer(request.low_scorer)
    med_fn = _build_scorer(request.medium_scorer)
    high_fn = _build_scorer(request.high_scorer)

    # Residual function: use pre-stored residuals
    def residual_fn(c: Candidate, strictness: str) -> Dict[str, float]:
        return c.gate_residuals.get(
            strictness, {"mass": 0.0, "momentum": 0.0, "energy": 0.0}
        )

    # Run cascade
    scorer = CascadeScorer(
        config=cfg,
        low_scorer=low_fn,
        medium_scorer=med_fn,
        high_scorer=high_fn,
        residual_fn=residual_fn,
    )
    result = scorer.run(candidates)

    # Build winners list
    winners = []
    for c in result.stages[-1].survivors:
        winners.append(
            {
                "id": c.id,
                "params": c.params,
                "low_score": c.low_score,
                "medium_score": c.medium_score,
                "high_score": c.high_score,
                "best_score": c.best_score,
            }
        )

    return CascadeRunResponse(
        run_id=str(uuid.uuid4()),
        stages=[
            StageResultOutput(
                level=s.level.name,
                n_input=s.n_input,
                n_passed=s.n_passed,
                n_eliminated=s.n_eliminated,
                n_gate_failed=s.n_gate_failed,
                n_budget_capped=s.n_budget_capped,
                cost_spent=round(s.cost_spent, 2),
                elapsed_s=round(s.elapsed_s, 4),
            )
            for s in result.stages
        ],
        total_cost=round(result.total_cost, 2),
        total_elapsed_s=round(result.total_elapsed_s, 4),
        budget=result.budget,
        budget_remaining=round(result.budget_remaining, 2),
        n_candidates=result.n_candidates,
        n_final=result.n_final,
        elimination_rate_low_medium=round(
            result.elimination_rate_low_medium, 4
        ),
        winners=winners,
        config=cfg.to_dict(),
    )
