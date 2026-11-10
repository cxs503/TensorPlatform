"""DoE framework HTTP API — platform-level, zero domain semantics.

Endpoints:
  POST /api/v1/doe/generate   — generate an experiment design
  GET  /api/v1/doe/types      — list available design types
  POST /api/v1/doe/efficiency — compute D-efficiency of a given design
  POST /api/v1/budget/decide  — make a budget adaptation decision
  POST /api/v1/budget/simulate — run budget adaptation simulation
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.budget_adaptation import (
    BudgetAdaptationEngine,
    BudgetDecision,
    TelemetrySnapshot,
    run_budget_simulation,
)
from services.doe_framework import (
    DESIGN_TYPES,
    DoEResult,
    LinearConstraint,
    ParameterSpec,
    generate_design,
)

router = APIRouter(prefix="/doe", tags=["doe"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ParameterSpecInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    low: float
    high: float
    discrete_values: Optional[List[float]] = None


class LinearConstraintInput(BaseModel):
    coefficients: Dict[str, float]
    rhs: float


class DoEGenerateRequest(BaseModel):
    design_type: str = Field(..., description="One of: full_factorial, fractional_factorial, central_composite, box_behnken, d_optimal")
    parameters: List[ParameterSpecInput] = Field(..., min_length=1, max_length=20)
    constraints: Optional[List[LinearConstraintInput]] = None
    n_levels: int = Field(2, ge=2, le=5, description="For full_factorial")
    resolution: int = Field(3, ge=2, le=5, description="For fractional_factorial")
    alpha: Optional[float] = Field(None, description="For central_composite")
    n_center: int = Field(3, ge=1, description="For box_behnken")
    n_runs: Optional[int] = Field(None, ge=4, description="For d_optimal")
    max_iter: int = Field(200, ge=10, description="For d_optimal")
    seed: int = Field(42, description="Random seed for reproducibility")


class DoEGenerateResponse(BaseModel):
    design_name: str
    n_runs: int
    n_parameters: int
    parameter_names: List[str]
    d_efficiency: float
    run_order: List[int]
    points_coded: List[List[float]]
    points_physical: List[List[float]]
    metadata: Dict[str, Any]


class DoEEfficiencyRequest(BaseModel):
    points: List[List[float]]
    parameter_names: List[str]


class DoEEfficiencyResponse(BaseModel):
    d_efficiency: float
    n_runs: int
    n_parameters: int


class TelemetrySnapshotInput(BaseModel):
    task_id: str
    timestamp: float
    elapsed_ratio: float = Field(..., ge=0, le=1)
    convergence_rate: float
    intermediate_quality: float = Field(..., ge=0, le=1)
    resource_consumption: float = Field(..., ge=0, le=1)
    iteration_count: int = Field(..., ge=0)


class BudgetDecideRequest(BaseModel):
    task_id: str
    snapshots: List[TelemetrySnapshotInput] = Field(..., min_length=1)
    remaining_budget: float = Field(1.0, gt=0)


class BudgetDecideResponse(BaseModel):
    task_id: str
    action: str
    confidence: float
    adjustment_ratio: float
    reason: str
    telemetry_used: int
    timestamp: str


class BudgetSimulateRequest(BaseModel):
    n_tasks: int = Field(10, ge=4, le=100)
    seed: int = Field(42)


class BudgetSimulateResponse(BaseModel):
    n_tasks: int
    accuracy_up: float
    accuracy_down: float
    accuracy_terminate: float
    resource_savings: float
    static_budget_total: float
    adaptive_budget_total: float
    decisions: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/types")
def list_design_types() -> Dict[str, List[str]]:
    """List available DoE design types."""
    return {"design_types": list(DESIGN_TYPES.keys())}


@router.post("/generate", response_model=DoEGenerateResponse)
def generate_doe(req: DoEGenerateRequest):
    """Generate an experiment design."""
    try:
        specs = [
            ParameterSpec(
                name=p.name,
                low=p.low,
                high=p.high,
                discrete_values=p.discrete_values,
            )
            for p in req.parameters
        ]
        constraints = None
        if req.constraints:
            constraints = [
                LinearConstraint(coefficients=c.coefficients, rhs=c.rhs)
                for c in req.constraints
            ]
        kwargs = {}
        if req.design_type == "full_factorial":
            kwargs["n_levels"] = req.n_levels
        elif req.design_type == "fractional_factorial":
            kwargs["resolution"] = req.resolution
        elif req.design_type == "central_composite" and req.alpha is not None:
            kwargs["alpha"] = req.alpha
        elif req.design_type == "box_behnken":
            kwargs["n_center"] = req.n_center
        elif req.design_type == "d_optimal":
            if req.n_runs is None:
                raise HTTPException(400, "n_runs is required for d_optimal design")
            kwargs["n_runs"] = req.n_runs
            kwargs["max_iter"] = req.max_iter
            kwargs["seed"] = req.seed

        result = generate_design(req.design_type, specs, constraints=constraints, **kwargs)

        return DoEGenerateResponse(
            design_name=result.design_name,
            n_runs=len(result.points),
            n_parameters=len(result.parameter_names),
            parameter_names=result.parameter_names,
            d_efficiency=result.d_efficiency,
            run_order=result.run_order,
            points_coded=result.points.tolist(),
            points_physical=result.points_physical.tolist(),
            metadata=result.metadata,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Internal error: {exc}")


@router.post("/efficiency", response_model=DoEEfficiencyResponse)
def compute_efficiency(req: DoEEfficiencyRequest):
    """Compute D-efficiency of a given design."""
    try:
        from services.doe_framework import _d_efficiency, full_factorial
        points = np.array(req.points, dtype=float)
        # Build synthetic specs for reference
        specs = [
            ParameterSpec(name=name, low=-1.0, high=1.0)
            for name in req.parameter_names
        ]
        ff = full_factorial(specs, n_levels=2)
        d_eff = _d_efficiency(points, ff.points)
        return DoEEfficiencyResponse(
            d_efficiency=d_eff,
            n_runs=len(points),
            n_parameters=len(req.parameter_names),
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# Budget adaptation endpoints
# ---------------------------------------------------------------------------

budget_router = APIRouter(prefix="/budget", tags=["budget"])


@budget_router.post("/decide", response_model=BudgetDecideResponse)
def budget_decide(req: BudgetDecideRequest):
    """Make a budget adaptation decision based on telemetry."""
    try:
        engine = BudgetAdaptationEngine()
        snapshots = [
            TelemetrySnapshot(
                task_id=s.task_id,
                timestamp=s.timestamp,
                elapsed_ratio=s.elapsed_ratio,
                convergence_rate=s.convergence_rate,
                intermediate_quality=s.intermediate_quality,
                resource_consumption=s.resource_consumption,
                iteration_count=s.iteration_count,
            )
            for s in req.snapshots
        ]
        decision = engine.decide(req.task_id, snapshots, req.remaining_budget)
        return BudgetDecideResponse(
            task_id=decision.task_id,
            action=decision.action,
            confidence=decision.confidence,
            adjustment_ratio=decision.adjustment_ratio,
            reason=decision.reason,
            telemetry_used=decision.telemetry_used,
            timestamp=decision.timestamp,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))


@budget_router.post("/simulate", response_model=BudgetSimulateResponse)
def budget_simulate(req: BudgetSimulateRequest):
    """Run budget adaptation simulation and return aggregate metrics."""
    try:
        result = run_budget_simulation(n_tasks=req.n_tasks, seed=req.seed)
        return BudgetSimulateResponse(
            n_tasks=result["n_tasks"],
            accuracy_up=result["accuracy_up"],
            accuracy_down=result["accuracy_down"],
            accuracy_terminate=result["accuracy_terminate"],
            resource_savings=result["resource_savings"],
            static_budget_total=result["static_budget_total"],
            adaptive_budget_total=result["adaptive_budget_total"],
            decisions=result["decisions"],
        )
    except Exception as exc:
        raise HTTPException(500, str(exc))


__all__ = ["router", "budget_router"]
