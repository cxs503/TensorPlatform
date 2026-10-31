"""Polar voyage performance API (ice resistance, escort spacing, CII, routing).

Endpoints (all under ``/api/v1/polar``)
--------------------------------------
GET  /capabilities                module metadata, model description, evidence level
GET  /ships                       preset hull catalogue (multi-ship-type support)
POST /ships/validate              Cb / L/B / B/T geometry validation
POST /ice-field/summary           validate an uploaded ice field, return statistics
GET  /ice-field/scenario          reproducible synthetic scenario field
POST /resistance/level-ice        level-ice resistance breakdown (TP-POLAR-1)
POST /resistance/level-ice/grid   grid-integrated resistance along a track
POST /resistance/channel          brash-ice channel resistance (brash/Riska pair)
POST /convoy/spacing              energy-optimal escort spacing E(s)
POST /cii                         attained / required CII, rating, EEDI / EEXI
POST /route/optimize              joint route + speed optimisation
POST /voyage/optimize             full voyage report (route + speed + CII + convoy)
GET  /acceptance                  acceptance criteria (a)-(e) of node W3-N11

Errors are reported with HTTP 400 (inconsistent input / infeasible voyage) and
404 (unknown ship type), never with a silently wrong number.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.polar import EVIDENCE_LEVEL, MODULE_ID, MODULE_VERSION
from services.polar import cii as cii_mod
from services.polar import resistance as ice_mod
from services.polar.channel import (
    channel_resistance,
    channel_resistance_reference,
    verify_channel_concentrations,
)
from services.polar.convoy import ConvoyConfig, convoy_energy_curve, optimize_spacing
from services.polar.constants import KNOT
from services.polar.ice_field import IceField
from services.polar.optimizer import (
    DEFAULT_DEADLINE_H,
    DEFAULT_ROUTE_END_KM,
    DEFAULT_ROUTE_SHIP,
    DEFAULT_ROUTE_START_KM,
    optimize_voyage,
    verify_route_improvement,
)
from services.polar.route import plan_voyage
from services.polar.ships import (
    get_ship,
    list_ships,
    ship_from_params,
    validate_geometry,
)

router = APIRouter(prefix="/polar", tags=["极地航线与能效"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ShipSpec(BaseModel):
    """Either a preset key or a full custom hull definition."""

    ship_type: Optional[str] = Field(default=None, description="preset key, e.g. pc6_bulker")
    params: Optional[Dict[str, Any]] = Field(
        default=None, description="custom principal particulars (overrides the preset)"
    )


class GeometryRequest(BaseModel):
    ship_type: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


class LevelIceRequest(BaseModel):
    ship_type: str = Field(default="pc6_bulker")
    params: Optional[Dict[str, Any]] = None
    speed_kn: float = Field(default=6.0, gt=0, le=25)
    h_m: float = Field(default=0.6, ge=0, le=6.0)
    sigma_mpa: float = Field(default=1.3, gt=0, le=8.0)
    channel_factor: float = Field(default=1.0, gt=0, le=1.0)
    current_along_ms: float = Field(default=0.0, ge=-3, le=3)
    wind_along_ms: float = Field(default=0.0, ge=-40, le=40)


class IceFieldRequest(BaseModel):
    field: Dict[str, Any] = Field(..., description="ice-field payload (h, sigma, currents, wind)")


class GridResistanceRequest(BaseModel):
    ship_type: str = "pc6_bulker"
    params: Optional[Dict[str, Any]] = None
    speed_kn: float = Field(default=6.0, gt=0, le=25)
    channel_factor: float = Field(default=1.0, gt=0, le=1.0)
    field: Dict[str, Any]
    track_km: List[List[float]] = Field(
        ..., description="track polyline in km: [[x0, y0], [x1, y1], ...]", min_length=2
    )
    n_stations: int = Field(default=81, ge=3, le=401)


class ChannelRequest(BaseModel):
    ship_type: str = "handysize_bulker"
    params: Optional[Dict[str, Any]] = None
    speed_kn: float = Field(default=6.0, gt=0, le=25)
    h_b_m: float = Field(default=0.8, ge=0, le=3.0)
    concentration: float = Field(default=0.45, ge=0, le=1.0)
    include_reference: bool = True


class ConvoyRequest(BaseModel):
    escort_type: str = "icebreaker_escort"
    follower_type: str = "handysize_bulker"
    followers: int = Field(default=2, ge=1, le=10)
    h_level_m: float = Field(default=0.9, gt=0, le=3.0)
    h_brash_m: float = Field(default=0.8, ge=0, le=3.0)
    concentration: float = Field(default=0.45, ge=0, le=1.0)
    speed_kn: float = Field(default=5.0, gt=0, le=20)
    s_min_m: float = Field(default=100.0, gt=0)
    s_max_m: float = Field(default=4000.0, gt=0)


class CiiRequest(BaseModel):
    ship_type: str = "handysize_bulker"
    params: Optional[Dict[str, Any]] = None
    fuel_t: float = Field(..., ge=0)
    distance_nm: float = Field(..., gt=0)
    year: int = Field(default=2026)
    brake_energy_mj: Optional[float] = Field(default=None, ge=0)


class RouteRequest(BaseModel):
    ship_type: str = DEFAULT_ROUTE_SHIP
    params: Optional[Dict[str, Any]] = None
    start_km: List[float] = Field(default=list(DEFAULT_ROUTE_START_KM), min_length=2, max_length=2)
    end_km: List[float] = Field(default=list(DEFAULT_ROUTE_END_KM), min_length=2, max_length=2)
    deadline_h: Optional[float] = Field(default=DEFAULT_DEADLINE_H, gt=0)
    stride: int = Field(default=3, ge=1, le=6)
    field: Optional[Dict[str, Any]] = None
    scenario: str = "nsr_corridor"


class VoyageRequest(RouteRequest):
    year: int = 2026
    escort_type: Optional[str] = "icebreaker_escort"
    include_experience_baseline: bool = True


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _resolve_ship(spec_ship_type: Optional[str], params: Optional[Dict[str, Any]]):
    if params:
        ship = ship_from_params(params)
        check = validate_geometry(ship)
        if not check["ok"]:
            raise HTTPException(status_code=400, detail={"message": "invalid ship geometry",
                                                        "failures": check["failures"]})
        return ship
    try:
        return get_ship(spec_ship_type or "handysize_bulker")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _field_from(payload: Optional[Dict[str, Any]], scenario: str = "nsr_corridor") -> IceField:
    if payload:
        try:
            return IceField.from_dict(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid ice field: {exc}") from exc
    try:
        return IceField.generate_scenario(scenario)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# metadata / ships
# ---------------------------------------------------------------------------

@router.get("/capabilities")
async def capabilities() -> Dict[str, Any]:
    """Module metadata and the physics it implements."""
    return {
        "module": MODULE_ID,
        "version": MODULE_VERSION,
        "evidence_level": EVIDENCE_LEVEL,
        "models": {
            "open_water": "Holtrop-Mennen (1984), simplified (no bulb/transom)",
            "level_ice": "TP-POLAR-1: crushing + bending (elastic foundation) + submersion",
            "channel": "brash-mixture buoyancy/friction vs calibrated Riska-type reference",
            "convoy": "Stefan refreezing + wake proximity penalty, E(s) interior minimum",
            "cii": "IMO MEPC.353(78) / 354(78) / 364(79), EEDI MEPC.203(62)",
            "route": "A*/Dijkstra on the ice grid + Lagrangian deadline speed allocation",
        },
        "criteria": ["a ice resistance", "b channel resistance", "c escort spacing",
                     "d CII", "e route optimisation", "f pytest"],
        "endpoints": [
            "GET /polar/capabilities", "GET /polar/ships", "POST /polar/ships/validate",
            "POST /polar/ice-field/summary", "GET /polar/ice-field/scenario",
            "POST /polar/resistance/level-ice", "POST /polar/resistance/level-ice/grid",
            "POST /polar/resistance/channel", "POST /polar/convoy/spacing",
            "POST /polar/cii", "POST /polar/route/optimize", "POST /polar/voyage/optimize",
            "GET /polar/acceptance",
        ],
    }


@router.get("/ships")
async def ships() -> Dict[str, Any]:
    return {"count": len(list_ships()), "ships": list_ships()}


@router.post("/ships/validate")
async def ships_validate(req: GeometryRequest) -> Dict[str, Any]:
    ship = _resolve_ship(req.ship_type, req.params)
    return validate_geometry(ship)


# ---------------------------------------------------------------------------
# ice field
# ---------------------------------------------------------------------------

@router.get("/ice-field/scenario")
async def ice_field_scenario(kind: str = "nsr_corridor", include_grid: bool = False) -> Dict[str, Any]:
    try:
        field = IceField.generate_scenario(kind)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return field.to_dict(include_grid=include_grid)


@router.post("/ice-field/summary")
async def ice_field_summary(req: IceFieldRequest) -> Dict[str, Any]:
    try:
        field = IceField.from_dict(req.field)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid ice field: {exc}") from exc
    return {"valid": True, "summary": field.summary()}


# ---------------------------------------------------------------------------
# resistance
# ---------------------------------------------------------------------------

@router.post("/resistance/level-ice")
async def level_ice(req: LevelIceRequest) -> Dict[str, Any]:
    ship = _resolve_ship(req.ship_type, req.params)
    v_ms = req.speed_kn * KNOT
    brk = ice_mod.level_ice_resistance(
        ship,
        v_ms,
        req.h_m,
        req.sigma_mpa,
        channel_factor=req.channel_factor,
        current_along_ms=req.current_along_ms,
        wind_along_ms=req.wind_along_ms,
    )
    ref = ice_mod.holtrop_ice_corrected_reference(ship, v_ms, req.h_m, req.sigma_mpa)
    out = brk.as_dict()
    out.update(
        {
            "ship": ship.key,
            "speed_kn": req.speed_kn,
            "h_m": req.h_m,
            "sigma_mpa": req.sigma_mpa,
            "channel_factor": req.channel_factor,
            "reference": {
                "total_n": round(ref["reference_total_n"], 2),
                "ice_n": round(ref["reference_ice_n"], 2),
            },
            "wetted_surface_m2": round(ice_mod.wetted_surface_m2(ship), 3),
        }
    )
    return out


@router.post("/resistance/level-ice/grid")
async def level_ice_grid(req: GridResistanceRequest) -> Dict[str, Any]:
    ship = _resolve_ship(req.ship_type, req.params)
    field = _field_from(req.field)
    track = [tuple(pt) for pt in req.track_km]
    try:
        res = ice_mod.track_ice_resistance(
            field, ship, req.speed_kn * KNOT, track, channel_factor=req.channel_factor
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    res["ship"] = ship.key
    res["field"] = {"name": field.name, "source": field.source}
    return res


@router.post("/resistance/channel")
async def channel(req: ChannelRequest) -> Dict[str, Any]:
    ship = _resolve_ship(req.ship_type, req.params)
    v_ms = req.speed_kn * KNOT
    try:
        prod = channel_resistance(ship, v_ms, req.h_b_m, req.concentration)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out: Dict[str, Any] = {
        "ship": ship.key,
        "speed_kn": req.speed_kn,
        "h_b_m": req.h_b_m,
        "concentration": req.concentration,
        "total_n": round(prod["total_n"], 2),
        "open_water_n": round(prod["open_water_n"], 2),
        "brash_n": round(prod["brash_n"], 2),
    }
    if req.include_reference:
        # calibrate on the documented anchor concentration, then evaluate
        from services.polar.channel import calibrate_k

        k_cal = calibrate_k(ship, v_ms, req.h_b_m)
        ref = channel_resistance_reference(ship, v_ms, req.h_b_m, req.concentration, k_cal=k_cal)
        out["reference"] = {
            "k_cal": round(k_cal, 6),
            "total_n": round(ref["total_n"], 2),
            "brash_n": round(ref["brash_n"], 2),
            "rel_error_pct": round(100.0 * (prod["total_n"] - ref["total_n"]) / ref["total_n"], 4),
        }
    return out


# ---------------------------------------------------------------------------
# convoy
# ---------------------------------------------------------------------------

@router.post("/convoy/spacing")
async def convoy_spacing(req: ConvoyRequest) -> Dict[str, Any]:
    escort = _resolve_ship(req.escort_type, None)
    follower = _resolve_ship(req.follower_type, None)
    cfg = ConvoyConfig(
        escort=escort,
        follower=follower,
        h_level_m=req.h_level_m,
        h_brash_m=req.h_brash_m,
        concentration=req.concentration,
        speed_kn=req.speed_kn,
        followers=req.followers,
    )
    res = optimize_spacing(cfg, s_min_m=req.s_min_m, s_max_m=req.s_max_m)
    return res


@router.post("/convoy/curve")
async def convoy_curve(req: ConvoyRequest) -> Dict[str, Any]:
    escort = _resolve_ship(req.escort_type, None)
    follower = _resolve_ship(req.follower_type, None)
    cfg = ConvoyConfig(
        escort=escort,
        follower=follower,
        h_level_m=req.h_level_m,
        h_brash_m=req.h_brash_m,
        concentration=req.concentration,
        speed_kn=req.speed_kn,
        followers=req.followers,
    )
    return {"config": cfg.as_dict(), "curve": convoy_energy_curve(cfg, req.s_min_m, req.s_max_m)}


# ---------------------------------------------------------------------------
# CII / EEDI
# ---------------------------------------------------------------------------

@router.post("/cii")
async def cii(req: CiiRequest) -> Dict[str, Any]:
    ship = _resolve_ship(req.ship_type, req.params)
    try:
        attained = cii_mod.attained_cii_from_fuel(
            req.fuel_t, ship.fuel_type, ship.dwt_t, req.distance_nm
        )
        required = cii_mod.required_cii(ship.category, ship.dwt_t, req.year)
        rating = cii_mod.cii_rating(attained, required["required_cii"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    out: Dict[str, Any] = {
        "ship": ship.key,
        "category": ship.category,
        "capacity_dwt": ship.dwt_t,
        "fuel_t": req.fuel_t,
        "fuel_type": ship.fuel_type,
        "co2_t": round(cii_mod.co2_from_fuel(req.fuel_t, ship.fuel_type), 4),
        "distance_nm": req.distance_nm,
        "attained_cii": round(attained, 6),
        **required,
        **rating,
    }
    if req.brake_energy_mj is not None:
        alt = cii_mod.attained_cii_from_energy(
            req.brake_energy_mj, ship.sfoc_g_kwh, ship.fuel_type, ship.dwt_t, req.distance_nm
        )
        out["attained_cii_energy_path"] = round(alt, 6)
        out["path_rel_diff_pct"] = round(100.0 * abs(alt - attained) / attained, 9)
    out["eedi"] = {
        "attained": round(
            cii_mod.attained_eedi(
                ship.brake_power_kw, ship.sfoc_g_kwh, ship.fuel_type,
                ship.dwt_t, ship.design_speed_kn, p_ae_kw=0.05 * ship.brake_power_kw,
            ),
            6,
        ),
        "required_phase2": round(
            cii_mod.required_eedi(ship.category, ship.dwt_t, phase=2)["required_eedi"], 6
        ),
    }
    return out


# ---------------------------------------------------------------------------
# route / voyage
# ---------------------------------------------------------------------------

@router.post("/route/optimize")
async def route_optimize(req: RouteRequest) -> Dict[str, Any]:
    ship = _resolve_ship(req.ship_type, req.params)
    field = _field_from(req.field, req.scenario)
    try:
        plan = plan_voyage(
            field, ship, tuple(req.start_km), tuple(req.end_km),
            deadline_h=req.deadline_h, stride=req.stride,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    plan["points_km"] = [[round(x, 3), round(y, 3)] for x, y in plan["points_km"]]
    return {
        "ship": ship.key,
        "field": {"name": field.name, "source": field.source, "grid": field.summary()["grid"]},
        "start_km": req.start_km,
        "end_km": req.end_km,
        "deadline_h": req.deadline_h,
        **plan,
    }


@router.post("/voyage/optimize")
async def voyage_optimize(req: VoyageRequest) -> Dict[str, Any]:
    ship = _resolve_ship(req.ship_type, req.params)
    field = _field_from(req.field, req.scenario)
    convoy = None
    if req.escort_type:
        try:
            convoy = ConvoyConfig(
                escort=get_ship(req.escort_type),
                follower=ship,
                h_level_m=0.9,
                h_brash_m=0.8,
                concentration=0.45,
                speed_kn=5.0,
                followers=2,
            )
        except KeyError:
            convoy = None
    try:
        return optimize_voyage(
            field, ship, tuple(req.start_km), tuple(req.end_km),
            deadline_h=req.deadline_h, stride=req.stride, year=req.year,
            convoy=convoy,
            include_experience_baseline=req.include_experience_baseline,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# acceptance
# ---------------------------------------------------------------------------

@router.get("/acceptance")
async def acceptance(ship_type: str = "handysize_bulker", year: int = 2026) -> Dict[str, Any]:
    """Run the machine-checkable acceptance criteria of node W3-N11.

    This endpoint executes the full verification (ice resistance over 5 ice
    conditions, channel resistance, escort spacing, CII for three ship types and
    the route comparison); it takes ~30 s and is intended for CI / audit, not for
    interactive use.
    """
    from services.polar.optimizer import full_acceptance_report

    try:
        report = full_acceptance_report(ship_key=ship_type, year=year)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return report


@router.get("/acceptance/route")
async def acceptance_route() -> Dict[str, Any]:
    """Criterion (e) on its own (faster than the full acceptance run)."""
    try:
        return verify_route_improvement()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/acceptance/channel")
async def acceptance_channel(ship_type: str = "handysize_bulker") -> Dict[str, Any]:
    """Criterion (b) on its own."""
    try:
        return verify_channel_concentrations(get_ship(ship_type))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc