"""
API endpoint for Clock Synchronization & Subcycling Framework.

POST /api/v1/coupling/clock_sync_validate
  Run a clock synchronization validation scenario and return results.

POST /api/v1/coupling/clock_sync_simulate
  Run a custom coupling simulation with user-specified parameters.

GET  /api/v1/coupling/clock_sync_status
  Return framework status and acceptance criteria.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.services.coupling_clock_sync import (
    MasterClock,
    SolverDomain,
    SubSolver,
    SubcyclingController,
    create_ice_coupling_scenario,
    validate_clock_sync,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["耦合时钟同步"])


# ── Pydantic models ──────────────────────────────────────────────────────

class ClockSyncValidateRequest(BaseModel):
    """Request for the standard ice-coupling validation scenario."""
    dt_master: float = Field(1e-2, description="Master time step [s]")
    dt_ice: float = Field(1e-4, description="Ice solver time step [s]")
    dt_struct: float = Field(1e-3, description="Structure solver time step [s]")
    dt_hydro: float = Field(5e-3, description="Hydrodynamics solver time step [s]")
    n_steps: int = Field(100, description="Number of master steps to simulate")


class ClockSyncValidateResponse(BaseModel):
    """Response with validation results."""
    clock_sync_error: float = Field(..., description="Maximum clock sync error [s]")
    energy_conservation_ratio: float = Field(..., description="Energy conservation ratio")
    pass_sync: bool = Field(..., description="clock_sync_error < 1e-6 s")
    pass_energy: bool = Field(..., description="energy_conservation_ratio < 0.01")
    pass_all: bool = Field(..., description="All criteria pass")
    t_final: float = Field(..., description="Final simulation time [s]")
    n_master_steps: int = Field(..., description="Number of master steps executed")
    solver_summary: Dict[str, Any] = Field(default_factory=dict)


class ClockSyncStatusResponse(BaseModel):
    """Framework status and acceptance criteria."""
    module: str = "coupling_clock_sync"
    version: str = "1.0.0"
    acceptance_criteria: Dict[str, Any]
    supported_domains: List[str]
    status: str = "operational"


# ── Endpoints ────────────────────────────────────────────────────────────

@router.post("/coupling/clock_sync_validate")
def clock_sync_validate(req: ClockSyncValidateRequest) -> ClockSyncValidateResponse:
    """
    Run the standard ice-coupling clock synchronization validation.

    Acceptance criteria:
      - clock_sync_error < 1e-6 [s]
      - subcycling_energy_conservation < 0.01 [ratio]
    """
    result = validate_clock_sync(
        dt_master=req.dt_master,
        dt_ice=req.dt_ice,
        dt_struct=req.dt_struct,
        dt_hydro=req.dt_hydro,
        n_steps=req.n_steps,
    )

    details = result["details"]

    # Build solver summary
    solver_summary: Dict[str, Any] = {}
    for solver_name, sub_steps in details.get("sub_steps_taken", {}).items():
        solver_summary[solver_name] = {
            "sub_steps_per_master": sub_steps,
        }

    # Add energy info from history
    if details.get("energy_history"):
        e0 = details["energy_history"][0]
        ef = details["energy_history"][-1]
        for name in e0:
            if name not in solver_summary:
                solver_summary[name] = {}
            solver_summary[name]["energy_initial"] = e0[name]
            solver_summary[name]["energy_final"] = ef[name]

    return ClockSyncValidateResponse(
        clock_sync_error=result["clock_sync_error"],
        energy_conservation_ratio=result["energy_conservation_ratio"],
        pass_sync=result["pass_sync"],
        pass_energy=result["pass_energy"],
        pass_all=result["pass_all"],
        t_final=details["t_final"],
        n_master_steps=details["n_master_steps"],
        solver_summary=solver_summary,
    )


@router.get("/coupling/clock_sync_status")
def clock_sync_status() -> ClockSyncStatusResponse:
    """Return framework status and acceptance criteria."""
    return ClockSyncStatusResponse(
        acceptance_criteria={
            "clock_sync_error": {"threshold": 1e-6, "unit": "s", "operator": "<"},
            "subcycling_energy_conservation": {"threshold": 0.01, "unit": "ratio", "operator": "<"},
            "pytest_test_clock_sync": {"expected": "PASS", "type": "bool"},
        },
        supported_domains=[d.value for d in SolverDomain],
    )
