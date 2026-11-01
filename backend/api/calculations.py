"""
Ship performance calculations API.

Provides resistance and stability calculation endpoints with timing instrumentation,
plus a timing baseline endpoint for latency statistics.

Verification status (m1384, see docs/reports/m1384_resistance_stability_verification.md):
  * Friction line Cf is the exact ITTC-1957 closed form (verified analytically).
  * The legacy wetted-surface estimate ``S = pi*B*L*(0.5+0.25*Cb)`` carries a
    documented shape-dependent bias (measured -30.4% on a closed-form cylinder,
    -8.7% on a prolate spheroid).  Callers that *know* the geometry (preferred,
    per the platform rule "geometry must be validated first") should pass
    ``wetted_surface_m2``; the response records which source was used in
    ``wetted_surface_source``.
  * The residual-resistance coefficient ``Cr`` is an empirical curve fit without
    a published provenance; it is exposed as-is but must not be quoted as a
    literature-validated model.
  * The GZ wall-sided formula is only valid below deck-edge immersion.  Beyond
    ``wall_sided_limit_deg`` the cubic term is dropped (``gz_basis =
    "linear_fallback"``, ``formula_valid=False``) so the returned arm stays
    bounded by GM instead of exploding (pre-fix GZ(89 deg) = 4925 m,
    GZ(90 deg) = 4.0e32 m). The 90 deg singularity (tan -> inf) is rejected with
    HTTP 400.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.propulsion import actuator_disk, self_propulsion_point
from services.ship_timing import get_collector, timed_calc

router = APIRouter(prefix="/calculations", tags=["船舶总体性能计算"])


def _propeller_curves(kt_coefficients, kq_coefficients):
    """Local import wrapper so the module stays free of services cycles."""
    from services.propulsion import propeller_curve
    return propeller_curve(kt_coefficients, kq_coefficients)


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
    wetted_surface_m2: Optional[float] = Field(
        default=None,
        gt=0,
        description="Geometry-validated wetted surface area (m²). Overrides the empirical estimate.",
    )
    form_factor: float = Field(
        default=1.0,
        ge=1.0,
        le=1.5,
        description="(1+k) viscous form factor from ITTC-1957; 1.0 = bare flat-plate friction only.",
    )


class ResistanceResponse(BaseModel):
    """Resistance calculation response."""
    ship_type: str
    speed_ms: float
    total_resistance_n: float
    frictional_resistance_n: float
    form_resistance_n: float = 0.0
    residual_resistance_n: float
    reynolds_number: float
    friction_coefficient_cf: float = 0.0
    froude_number: float
    wetted_surface_m2: float = 0.0
    wetted_surface_source: str = "empirical_formula"
    form_factor: float = 1.0
    computation_time_ms: float


class StabilityRequest(BaseModel):
    """Stability calculation request."""
    ship_type: str = Field(default="generic", description="Hull type identifier")
    displacement_t: float = Field(default=1000.0, gt=0, description="Displacement (tonnes)")
    gm_m: float = Field(default=1.0, gt=0, description="Metacentric height (m)")
    kb_m: float = Field(default=2.0, gt=0, description="KB (m)")
    bm_m: float = Field(default=3.0, gt=0, description="BM (m)")
    heel_angle_deg: float = Field(
        default=10.0,
        ge=0.0,
        le=90.0,
        description="Heel angle (degrees). 90 deg is the tan() singularity and is rejected.",
    )
    wall_sided_limit_deg: float = Field(
        default=25.0,
        gt=0.0,
        lt=90.0,
        description="Deck-edge immersion angle: upper validity limit of the wall-sided formula.",
    )


class StabilityResponse(BaseModel):
    """Stability calculation response."""
    ship_type: str
    heel_angle_deg: float
    righting_arm_m: float
    righting_moment_knm: float
    gm_effective_m: float
    formula_valid: bool = True
    gz_basis: str = "wall_sided"
    wall_sided_limit_deg: float = 25.0
    validity_note: str = ""
    computation_time_ms: float


# ---------------------------------------------------------------------------
# Resistance calculation (Holtrop-Mennen simplified for SUBOFF)
# ---------------------------------------------------------------------------

def _empirical_wetted_surface(length: float, beam: float, cb: float) -> float:
    """Legacy wetted-surface estimate for an axisymmetric body.

    Kept for backward compatibility. Measured bias against closed-form shapes
    (``tools/verif/verify_ship_calcs.py``):
        cylinder (Cb = pi/4)        : -30.4 %
        prolate spheroid (b/a=0.25) : -8.7 %
    Prefer passing ``wetted_surface_m2`` when the geometry has been validated.
    """
    return math.pi * beam * length * (0.5 + 0.25 * cb)


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

    # Wetted surface area: geometry-validated input wins over the empirical fit
    if req.wetted_surface_m2 is not None:
        S = float(req.wetted_surface_m2)
        S_source = "input_validated"
    else:
        S = _empirical_wetted_surface(L, B, Cb)
        S_source = "empirical_formula"

    # Reynolds number
    Re = V * L / nu

    # ITTC-1957 friction line
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2

    # Frictional resistance (bare flat plate, ITTC-1957)
    Rf = 0.5 * rho * S * Cf * V ** 2

    # Viscous form resistance from the (1+k) form factor
    k = req.form_factor - 1.0
    Rform = k * Rf

    # Residual resistance (simplified: function of Fn and Cb)
    g = 9.81
    Fn = V / math.sqrt(g * L)

    # Empirical residual resistance coefficient for SUBOFF-like bodies.
    # NOTE: curve fit, no published provenance -- see module docstring.
    Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = 0.5 * rho * S * Cr * V ** 2

    Rt = Rf + Rform + Rr

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return ResistanceResponse(
        ship_type=req.ship_type,
        speed_ms=V,
        total_resistance_n=round(Rt, 4),
        frictional_resistance_n=round(Rf, 4),
        form_resistance_n=round(Rform, 4),
        residual_resistance_n=round(Rr, 4),
        reynolds_number=round(Re, 2),
        friction_coefficient_cf=round(Cf, 10),
        froude_number=round(Fn, 6),
        wetted_surface_m2=round(S, 6),
        wetted_surface_source=S_source,
        form_factor=req.form_factor,
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

    Validity: exact only for a wall-sided hull section, up to deck-edge
    immersion (``wall_sided_limit_deg``). Beyond that limit the cubic term
    over-predicts GZ without bound (the section has already lost its wall-sided
    buoyancy wedge), so it is dropped: the response falls back to the linear
    estimate ``GZ = GM*sin(phi)`` with ``gz_basis="linear_fallback"`` and
    ``formula_valid=False``. That fallback is conservative, not a substitute for
    a hydrostatics GZ curve. φ = 90 deg is the tan() singularity (GZ -> inf)
    and is rejected.
    """
    t0 = time.perf_counter()

    if req.heel_angle_deg >= 90.0 - 1e-9:
        raise HTTPException(
            status_code=400,
            detail=(
                "heel_angle_deg=90 deg is the tan() singularity of the wall-sided "
                "formula (GZ -> inf); supply a GZ curve from hydrostatics instead"
            ),
        )

    phi = math.radians(req.heel_angle_deg)
    GM = req.gm_m
    BM = req.bm_m
    disp = req.displacement_t * 1000  # tonnes → kg

    sin_phi = math.sin(phi)
    valid = req.heel_angle_deg <= req.wall_sided_limit_deg
    if valid:
        # Wall-sided formula
        GZ = GM * sin_phi + 0.5 * BM * (math.tan(phi) ** 2) * sin_phi
        gz_basis = "wall_sided"
        note = "within wall-sided validity range (phi <= deck-edge immersion)"
    else:
        # Cubic term is unbounded above deck-edge immersion -> bounded fallback
        GZ = GM * sin_phi
        gz_basis = "linear_fallback"
        note = (
            "phi exceeds wall_sided_limit_deg=%.1f deg: wall-sided cubic term dropped, "
            "linear fallback GZ=GM*sin(phi); use a hydrostatics-derived GZ curve"
            % req.wall_sided_limit_deg
        )

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
        formula_valid=valid,
        gz_basis=gz_basis,
        wall_sided_limit_deg=req.wall_sided_limit_deg,
        validity_note=note,
        computation_time_ms=round(elapsed_ms, 3),
    )


# ---------------------------------------------------------------------------
# Propulsion: actuator disk / momentum theory and self-propulsion point
# ---------------------------------------------------------------------------

class ActuatorDiskRequest(BaseModel):
    """Actuator-disk momentum theory request."""
    speed_ms: float = Field(default=7.2, gt=0, description="Speed of advance (m/s)")
    thrust_n: float = Field(default=350000.0, ge=0, description="Required thrust (N)")
    diameter_m: float = Field(default=6.0, gt=0, description="Propeller diameter (m)")
    water_density: float = Field(default=1025.0, gt=0, description="Water density (kg/m³)")


class ActuatorDiskResponse(BaseModel):
    disk_area_m2: float
    thrust_coefficient: float
    induced_velocity_ms: float
    induced_velocity_analytic_ms: float
    ideal_efficiency: float
    ideal_efficiency_analytic: float
    momentum_residual_n: float
    pressure_jump_pa: float
    ideal_power_w: float
    effective_power_w: float
    computation_time_ms: float


class SelfPropulsionRequest(BaseModel):
    """Self-propulsion point request (hull/propeller equilibrium)."""
    resistance_n: float = Field(default=180000.0, ge=0, description="Towed resistance (N)")
    speed_ms: float = Field(default=7.2, gt=0, description="Ship speed (m/s)")
    diameter_m: float = Field(default=6.0, gt=0, description="Propeller diameter (m)")
    wake_fraction: float = Field(default=0.28, ge=0, lt=1, description="Taylor wake fraction w")
    thrust_deduction: float = Field(default=0.18, ge=0, lt=1, description="Thrust deduction t")
    relative_rotative_efficiency: float = Field(
        default=1.0, gt=0, le=1.5, description="Relative rotative efficiency η_R")
    kt_coefficients: list[float] = Field(
        default=[0.50, -0.35, -0.25],
        description="K_T(J) coefficients in ascending power (caller supplied curve)")
    kq_coefficients: list[float] = Field(
        default=[0.050, -0.018, -0.020],
        description="K_Q(J) coefficients in ascending power (caller supplied curve)")
    water_density: float = Field(default=1025.0, gt=0, description="Water density (kg/m³)")


class SelfPropulsionResponse(BaseModel):
    revolutions_per_s: float
    advance_ratio: float
    thrust_coefficient: float
    torque_coefficient: float
    open_water_efficiency: float
    required_thrust_n: float
    thrust_n: float
    torque_nm: float
    effective_power_w: float
    delivered_power_w: float
    quasi_propulsive_efficiency: float
    quasi_propulsive_efficiency_identity: float
    thrust_balance_residual_n: float
    iterations: int
    computation_time_ms: float


def _propulsion_error(exc: ValueError, endpoint: str) -> HTTPException:
    return HTTPException(status_code=400, detail=f"{endpoint}: {exc}")


@router.post("/propulsion/actuator_disk", response_model=ActuatorDiskResponse)
@timed_calc("propulsion_actuator_disk")
async def calc_actuator_disk(req: ActuatorDiskRequest):
    """Ideal (actuator-disk) propulsor performance for a prescribed thrust.

    Reference: Lanchester-Glauert momentum theory, eta_i = 2/(1+sqrt(1+C_T)).
    """
    t0 = time.perf_counter()
    try:
        res = actuator_disk(req.speed_ms, req.thrust_n, req.diameter_m,
                            req.water_density)
    except ValueError as exc:  # pragma: no cover - pydantic guards most cases
        raise _propulsion_error(exc, "actuator_disk")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return ActuatorDiskResponse(
        disk_area_m2=round(res.disk_area_m2, 6),
        thrust_coefficient=round(res.thrust_coefficient, 8),
        induced_velocity_ms=round(res.induced_velocity_ms, 8),
        induced_velocity_analytic_ms=round(res.induced_velocity_analytic_ms, 8),
        ideal_efficiency=round(res.ideal_efficiency, 10),
        ideal_efficiency_analytic=round(res.ideal_efficiency_analytic, 10),
        momentum_residual_n=res.momentum_residual_n,
        pressure_jump_pa=round(res.pressure_jump_pa, 4),
        ideal_power_w=round(res.ideal_power_w, 4),
        effective_power_w=round(res.effective_power_w, 4),
        computation_time_ms=round(elapsed_ms, 3),
    )


@router.post("/propulsion/self_propulsion", response_model=SelfPropulsionResponse)
@timed_calc("propulsion_self_propulsion")
async def calc_self_propulsion(req: SelfPropulsionRequest):
    """Hull/propeller equilibrium point for a prescribed towed resistance.

    Solves the shaft rate that satisfies T(1-t) = R with J = V(1-w)/(nD), then
    reports P_D, P_E and the quasi-propulsive coefficient
    eta_D = eta_O eta_R (1-t)/(1-w).
    """
    t0 = time.perf_counter()
    kt_fn, kq_fn = _propeller_curves(req.kt_coefficients, req.kq_coefficients)
    try:
        res = self_propulsion_point(
            resistance_n=req.resistance_n,
            ship_speed_ms=req.speed_ms,
            diameter_m=req.diameter_m,
            kt_fn=kt_fn,
            kq_fn=kq_fn,
            wake_fraction=req.wake_fraction,
            thrust_deduction=req.thrust_deduction,
            relative_rotative_efficiency=req.relative_rotative_efficiency,
            rho=req.water_density,
        )
    except ValueError as exc:
        raise _propulsion_error(exc, "self_propulsion")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return SelfPropulsionResponse(
        revolutions_per_s=round(res.revolutions_per_s, 8),
        advance_ratio=round(res.advance_ratio, 8),
        thrust_coefficient=round(res.thrust_coefficient, 8),
        torque_coefficient=round(res.torque_coefficient, 8),
        open_water_efficiency=round(res.open_water_efficiency, 10),
        required_thrust_n=round(res.required_thrust_n, 4),
        thrust_n=round(res.thrust_n, 4),
        torque_nm=round(res.torque_nm, 4),
        effective_power_w=round(res.effective_power_w, 4),
        delivered_power_w=round(res.delivered_power_w, 4),
        quasi_propulsive_efficiency=round(res.quasi_propulsive_efficiency, 10),
        quasi_propulsive_efficiency_identity=round(
            res.quasi_propulsive_efficiency_identity, 10),
        thrust_balance_residual_n=res.thrust_balance_residual_n,
        iterations=res.iterations,
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