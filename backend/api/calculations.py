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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import AliasChoices, BaseModel, Field

from services.propulsion import actuator_disk, self_propulsion_point
from services.stability_reference import (
    exact_gz_box,
    wall_sided_limit_deg,
    wall_sided_gz,
)
from services.ship_resistance import ct_from_rt
from services.ship_timing import get_collector, timed_calc
from services.ship_hydrostatics import (
    GeometryError,
    HullOffsets,
    compute_hydrostatics,
    wigley_offsets,
)

router = APIRouter(prefix="/calculations", tags=["船舶总体性能计算"])

#: 阻力模型的来源标签，随响应下发，便于对标链记录"哪个模型给的数"。
RESISTANCE_METHOD = "ITTC-1957 friction + empirical residual (axisymmetric-body fit)"

# Conservative upper heel limit of the wall-sided identity when neither the hull
# geometry (beam/draft/depth) nor an explicit caller limit is supplied.
DEFAULT_WALL_SIDED_LIMIT_DEG = 25.0


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
    meridian_profile: Optional[List[List[float]]] = Field(
        default=None,
        description=(
            "Body-of-revolution meridian [[x, r], ...] with strictly increasing x (m) and "
            "r >= 0 (m). When given, the wetted surface is integrated exactly from the "
            "revolved polyline (frustum sum) and the Cb integrated from the geometry is "
            "gated against `cb` (m1683)."
        ),
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
    wetted_surface_method: str = ""
    form_factor: float = 1.0
    # m1388 收编：下游对标链按 C_T = R_T/(0.5 rho S V^2) 复核，端点直接给出
    # 系数与模型来源，避免消费侧自己猜字段/自己反推。
    total_resistance_coefficient: float = 0.0
    method: str = "ITTC-1957 friction + empirical residual (axisymmetric-body fit)"
    computation_time_ms: float


class StabilityRequest(BaseModel):
    """Stability calculation request.

    ``beam_m`` / ``draft_m`` / ``depth_m`` are optional.  When beam and draft are
    supplied the endpoint *derives* the exact heel limit of the wall-sided
    identity and returns an independently integrated box-barge arm for
    cross-checking; otherwise the conservative
    ``DEFAULT_WALL_SIDED_LIMIT_DEG`` (or an explicit ``wall_sided_limit_deg``)
    is used.  The same fields are also accepted under the short names posted by
    ``services.ship_proxy`` (``beam`` / ``draft`` / ``depth`` / ``displacement``).
    """
    ship_type: str = Field(default="generic", description="Hull type identifier")
    displacement_t: float = Field(
        default=1000.0, gt=0, description="Displacement (tonnes)",
        validation_alias=AliasChoices("displacement_t", "displacement"),
    )
    gm_m: float = Field(default=1.0, gt=0, description="Metacentric height (m)")
    kb_m: float = Field(default=2.0, gt=0, description="KB (m)")
    bm_m: float = Field(default=3.0, gt=0, description="BM (m)")
    heel_angle_deg: float = Field(
        default=10.0,
        ge=0.0,
        le=90.0,
        description="Heel angle (degrees, 0 <= phi <= 90; heel magnitude). phi = 90 deg is "
                    "the tan() singularity of the wall-sided form and is rejected with HTTP 400.",
    )
    wall_sided_limit_deg: Optional[float] = Field(
        default=None,
        gt=0.0,
        lt=90.0,
        description="Explicit upper validity limit override (deg). When omitted it is "
                    "derived from beam/draft/depth, else DEFAULT_WALL_SIDED_LIMIT_DEG.",
    )
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
    """Stability calculation response.

    Two generations of validity reporting are exposed side by side so that both
    consumer sets keep working: ``formula_valid`` / ``wall_sided_limit_deg``
    (inline, m1384) and ``wall_sided_valid`` / ``heel_validity_limit_deg`` /
    ``gz_box_exact_m`` (service-backed, m1386).  ``wall_sided_valid`` and
    ``formula_valid`` carry the same verdict by construction.
    """
    ship_type: str
    heel_angle_deg: float
    righting_arm_m: float
    righting_moment_knm: float
    gm_effective_m: float
    formula_valid: bool = True
    gz_basis: str = "wall_sided"
    wall_sided_limit_deg: float = DEFAULT_WALL_SIDED_LIMIT_DEG
    validity_note: str = ""
    computation_time_ms: float
    wall_sided_valid: bool = True
    heel_validity_limit_deg: Optional[float] = DEFAULT_WALL_SIDED_LIMIT_DEG
    gz_box_exact_m: Optional[float] = None
    validity_limit_source: str = "default"



class HydrostaticsRequest(BaseModel):
    """Hydrostatics request — from an offset table or the built-in Wigley hull."""
    hull: str = Field(
        default="wigley",
        description='Hull description source: "wigley" (built-in test hull) or "table" (offset table supplied)',
    )
    length: float = Field(default=6.0, gt=0, description="Length between perpendiculars (m)")
    beam: float = Field(default=0.6, gt=0, description="Beam at design waterline (m), wigley only")
    draft: float = Field(default=0.375, gt=0, description="Design draft (m), wigley only")
    n_stations: int = Field(default=81, ge=3, le=2001, description="Wigley station count")
    n_waterlines: int = Field(default=41, ge=3, le=2001, description="Wigley waterline count")
    stations: Optional[List[float]] = Field(default=None, description="Station x from AP (m), hull=table")
    waterlines: Optional[List[float]] = Field(default=None, description="Waterline z from keel (m), first must be 0")
    half_breadths: Optional[List[List[float]]] = Field(
        default=None, description="half_breadths[station][waterline] (m), hull=table"
    )
    kg: Optional[float] = Field(default=None, gt=0, description="VCG above keel (m); enables GM")
    rho: float = Field(default=1025.0, gt=0, description="Water density (kg/m³)")
    cb_reference: Optional[float] = Field(
        default=None, gt=0, le=1, description="Design Cb for the geometry gate; mismatch beyond tolerance -> 400"
    )
    cb_tolerance: float = Field(default=0.01, gt=0, le=0.5, description="Relative Cb tolerance for the gate")


class HydrostaticsResponse(BaseModel):
    """Hydrostatic quantities integrated from the hull form."""
    hull: str
    volume_m3: float
    displacement_t: float
    cb: float
    cm: float
    cp: float
    cw: float
    waterplane_area_m2: float
    lcb_m: float
    lcf_m: float
    kb_m: float
    bm_t_m: float
    bm_l_m: float
    km_t_m: float
    gm_m: Optional[float]
    wetted_surface_m2: float
    integration: str
    n_stations: int
    n_waterlines: int
    notes: List[str]
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

    # Wetted surface area: supplied geometry > geometry-validated input > empirical fit.
    # m1683: the empirical fit carries -18.5 % bias on the only data-anchored case
    # (SUBOFF, m1666); a meridian table lets the endpoint integrate S instead of
    # regressing it, and gates the geometry against the declared Cb first.
    S_method = ""
    if req.meridian_profile:
        from services.wetted_surface import GeometryError, revolution_wetted_surface

        try:
            geo = revolution_wetted_surface(req.meridian_profile, cb_reference=Cb)
        except GeometryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        S = geo.wetted_surface_m2
        S_source = "geometry_integrated"
        S_method = geo.method
    elif req.wetted_surface_m2 is not None:
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
        wetted_surface_method=S_method,
        form_factor=req.form_factor,
        total_resistance_coefficient=round(ct_from_rt(Rt, rho, S, V), 8),
        method=RESISTANCE_METHOD,
        computation_time_ms=round(elapsed_ms, 3),
    )


# ---------------------------------------------------------------------------
# Stability calculation (simplified GZ curve)
# ---------------------------------------------------------------------------

@router.post("/stability", response_model=StabilityResponse)
@timed_calc("stability")
async def calc_stability(req: StabilityRequest):
    """Compute righting arm and moment at a given heel angle.

    The shipped closed form is the classic wall-sided identity

        GZ(phi) = GM*sin(phi) + 0.5*BM*tan(phi)**2*sin(phi)

    which is *exact* only while the immersed section stays a parallel-sided
    trapezoid -- i.e. below the first of bilge emergence (``tan(phi) = 2T/B``)
    and deck-edge immersion (``tan(phi) = 2(D-T)/B``).

    Adjudicated behaviour (m1564, one endpoint, one verdict):

    * ``wall_sided_valid`` (= legacy ``formula_valid``) says whether the closed
      form may be trusted at this heel. The limit is derived from the geometry
      when beam/draft are supplied (``heel_validity_limit_deg``,
      ``validity_limit_source="analytic"``), otherwise from an explicit
      ``wall_sided_limit_deg`` (source ``"request"``) or the conservative
      ``DEFAULT_WALL_SIDED_LIMIT_DEG`` (source ``"default"``).
    * Inside the domain the arm is the wall-sided value itself
      (``gz_basis="wall_sided"``), which the reference integration reproduces to
      machine precision.
    * Outside the domain the arm is *never* the unbounded wall-sided ray: with
      geometry known the independently integrated constant-displacement box
      solution is returned (``gz_basis="hydrostatics_box_exact"``) and mirrored
      in ``gz_box_exact_m``; without geometry the bounded linear estimate
      ``GM*sin(phi)`` is returned (``gz_basis="linear_fallback"``).
    * ``|phi| >= 90 deg`` is the tan() singularity and is rejected with HTTP 400.
    """
    t0 = time.perf_counter()

    phi_deg = req.heel_angle_deg
    if not (-90.0 < phi_deg < 90.0):
        raise HTTPException(
            status_code=400,
            detail=(
                f"heel_angle_deg={phi_deg} out of range (-90, 90): tan(phi)^2 is "
                "unbounded (wall-sided singularity); supply a GZ curve from "
                "hydrostatics instead"
            ),
        )

    phi = math.radians(phi_deg)
    GM = req.gm_m
    BM = req.bm_m
    disp = req.displacement_t * 1000  # tonnes -> kg
    sin_phi = math.sin(phi)

    # -- validity limit: analytic when the geometry is known -----------------
    has_geometry = req.beam_m is not None and req.draft_m is not None
    if has_geometry:
        try:
            limit = wall_sided_limit_deg(req.beam_m, req.draft_m, req.depth_m)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        limit_source = "analytic"
        limit_text = f"analytic limit atan(2*min(T, D-T)/B) = {limit:.2f} deg"
    elif req.wall_sided_limit_deg is not None:
        limit = req.wall_sided_limit_deg
        limit_source = "request"
        limit_text = f"caller supplied limit = {limit:.2f} deg"
    else:
        limit = DEFAULT_WALL_SIDED_LIMIT_DEG
        limit_source = "default"
        limit_text = (
            f"beam/draft not supplied; conservative default limit "
            f"{DEFAULT_WALL_SIDED_LIMIT_DEG:.0f} deg assumed"
        )

    valid = abs(phi_deg) <= limit + 1e-9

    # -- independent box-barge cross-check (constant displacement) -----------
    gz_exact: Optional[float] = None
    if has_geometry:
        depth = req.depth_m if req.depth_m is not None else 1.5 * req.draft_m
        kg = req.kb_m + req.bm_m - req.gm_m  # KG implied by the supplied hydrostatics
        if depth > req.draft_m and 0.0 < kg < depth:
            gz_exact = exact_gz_box(req.beam_m, req.draft_m, kg, phi, depth)["gz_m"]

    # -- the arm that is actually shipped ------------------------------------
    gz_wall = wall_sided_gz(GM, BM, phi)
    if valid:
        GZ = gz_wall
        gz_basis = "wall_sided"
        note = f"within wall-sided domain ({limit_text})"
    elif gz_exact is not None:
        GZ = gz_exact
        gz_basis = "hydrostatics_box_exact"
        note = (
            f"OUTSIDE wall-sided domain ({limit_text}): cubic term is unbounded here, "
            f"so the independently integrated box-barge arm is returned "
            f"(wall-sided ray would give {gz_wall:.3f} m, "
            f"{(gz_wall - gz_exact) / abs(gz_exact) * 100.0:+.1f}% off); "
            "do not use the closed form for IS-Code areas or GZmax"
        )
    else:
        GZ = GM * sin_phi
        gz_basis = "linear_fallback"
        note = (
            f"OUTSIDE wall-sided domain ({limit_text}): cubic term dropped, bounded "
            "linear fallback GZ=GM*sin(phi); supply beam/draft for the integrated "
            "box-barge arm, or a hydrostatics-derived GZ curve for a real hull"
        )

    # Righting moment
    g = 9.81
    RM = disp * g * GZ / 1000  # kN*m

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return StabilityResponse(
        ship_type=req.ship_type,
        heel_angle_deg=req.heel_angle_deg,
        righting_arm_m=round(GZ, 6),
        righting_moment_knm=round(RM, 4),
        gm_effective_m=round(GM, 4),
        formula_valid=valid,
        gz_basis=gz_basis,
        wall_sided_limit_deg=round(limit, 3),
        validity_note=note,
        computation_time_ms=round(elapsed_ms, 3),
        wall_sided_valid=valid,
        heel_validity_limit_deg=round(limit, 3),
        gz_box_exact_m=None if gz_exact is None else round(gz_exact, 6),
        validity_limit_source=limit_source,
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
# Hydrostatics from the hull form (geometry -> Cb/KB/BM -> GM)
# ---------------------------------------------------------------------------

@router.post("/hydrostatics", response_model=HydrostaticsResponse)
@timed_calc("hydrostatics")
async def calc_hydrostatics(req: HydrostaticsRequest):
    """
    Integrate hydrostatics from the hull form.

    Fills the geometry side of the 稳性 chain: the ``/stability`` endpoint takes
    ``GM``/``BM`` as given parameters, whereas this endpoint derives
    ``∇, Cb, Cm, Cp, Cw, Aw, LCB, LCF, KB, BM_T, BM_L`` from stations x
    waterlines half-breadths and returns ``GM = KB + BM_T - KG`` when ``kg``
    is supplied.  ``cb_reference`` activates the platform's geometry gate
    (Cb from the offsets must match the design value within ``cb_tolerance``).
    """
    t0 = time.perf_counter()

    hull = (req.hull or "wigley").lower()
    if hull == "wigley":
        offsets = wigley_offsets(
            length=req.length,
            beam=req.beam,
            draft=req.draft,
            n_stations=req.n_stations,
            n_waterlines=req.n_waterlines,
        )
    elif hull == "table":
        if not req.stations or not req.waterlines or not req.half_breadths:
            raise HTTPException(
                status_code=400,
                detail="hull='table' requires stations, waterlines and half_breadths",
            )
        offsets = HullOffsets(
            length=req.length,
            stations=req.stations,
            waterlines=req.waterlines,
            half_breadths=req.half_breadths,
        )
    else:
        raise HTTPException(status_code=400, detail=f"unknown hull source '{req.hull}'")

    try:
        result = compute_hydrostatics(
            offsets,
            kg=req.kg,
            rho=req.rho,
            cb_reference=req.cb_reference,
            cb_tolerance=req.cb_tolerance,
        )
    except GeometryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    elapsed_ms = (time.perf_counter() - t0) * 1000
    payload = {
        "hull": hull,
        "volume_m3": round(result.volume_m3, 6),
        "displacement_t": round(result.displacement_t, 6),
        "cb": round(result.cb, 6),
        "cm": round(result.cm, 6),
        "cp": round(result.cp, 6),
        "cw": round(result.cw, 6),
        "waterplane_area_m2": round(result.waterplane_area_m2, 6),
        "lcb_m": round(result.lcb_m, 6),
        "lcf_m": round(result.lcf_m, 6),
        "kb_m": round(result.kb_m, 6),
        "bm_t_m": round(result.bm_t_m, 6),
        "bm_l_m": round(result.bm_l_m, 6),
        "km_t_m": round(result.km_t_m, 6),
        "gm_m": None if result.gm_m is None else round(result.gm_m, 6),
        "wetted_surface_m2": round(result.wetted_surface_m2, 6),
        "integration": result.integration,
        "n_stations": result.n_stations,
        "n_waterlines": result.n_waterlines,
        "notes": result.notes,
        "computation_time_ms": round(elapsed_ms, 3),
    }
    return HydrostaticsResponse(**payload)


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