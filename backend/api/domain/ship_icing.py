"""Ship superstructure icing API (node W3-N09).

Endpoints
---------
``GET  /ship/icing/capability``   — model description, evidence level, criteria
``POST /ship/icing/heat_balance`` — single-panel Mákiranta/Lozowski balance
``POST /ship/icing/spray``        — empirical spray water flux
``POST /ship/icing/accumulate``   — full accretion + GM-loss + Polar Code run
``POST /ship/icing/benchmark``    — calibration-point comparison vs the analytic
                                    reference (relative error, reproducibility)

The physics lives in ``services.ship_performance.icing``; this module is a thin
request/response layer.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.ship_performance import icing as ice
from services.ship_performance.icing.reference import makiranta_reference_icing_rate

router = APIRouter(prefix="/ship/icing", tags=["船舶总体性能-上层建筑积冰"])

EVIDENCE_LEVEL = "REAL"   # heat balance solved per panel, no hard-coded result


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class MetoceanRequest(BaseModel):
    t_air_c: float = Field(default=-10.0, ge=-60.0, le=10.0)
    wind_speed: float = Field(default=10.0, ge=0.0, le=60.0)
    t_water_c: float = Field(default=1.0, ge=-3.0, le=20.0)
    salinity: float = Field(default=33.0, ge=0.0, le=40.0)
    rel_humidity: float = Field(default=0.85, ge=0.0, le=1.0)
    cloud_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    solar_irradiance: float = Field(default=0.0, ge=0.0, le=1200.0)
    view_factor_to_sky: float = Field(default=0.5, ge=0.0, le=1.0)
    wind_dir: Tuple[float, float, float] = (0.0, -1.0, 0.0)
    ship_speed: Optional[float] = Field(
        default=None, ge=0.0, le=25.0,
        description="vessel speed m/s along +x; None -> preset service speed")

    def to_metocean(self) -> ice.Metocean:
        return ice.Metocean(
            t_air_c=self.t_air_c,
            wind_speed=self.wind_speed,
            t_water_c=self.t_water_c,
            salinity=self.salinity,
            rel_humidity=self.rel_humidity,
            cloud_fraction=self.cloud_fraction,
            solar_irradiance=self.solar_irradiance,
            wind_dir=self.wind_dir,
            ship_speed=self.ship_speed,
            view_factor_to_sky=self.view_factor_to_sky,
        )


class HeatBalanceRequest(MetoceanRequest):
    char_length: float = Field(default=4.5, gt=0.0, le=30.0,
                              description="characteristic panel length (m)")
    spray_flux: Optional[float] = Field(
        default=None, ge=0.0,
        description="impinging water flux kg m^-2 s^-1 (default: spray model)")


class AccumulateRequest(MetoceanRequest):
    vessel: str = Field(default="polar_fishing_vessel_28m")
    duration_h: float = Field(default=24.0, gt=0.0, le=720.0)
    dt_s: float = Field(default=600.0, gt=0.0, le=3600.0)
    panel_size: Optional[float] = Field(default=None, gt=0.05, le=5.0)
    kg_m: Optional[float] = Field(default=None, gt=0.1, description="override light KG")
    include_panels: bool = Field(default=False, description="include per-panel table")


class BenchmarkRequest(BaseModel):
    t_air_c: float = Field(default=-10.0)
    wind_speed: float = Field(default=10.0)
    t_water_c: float = Field(default=1.0)
    char_length: float = Field(default=5.25)
    salinity: float = Field(default=33.0)
    rel_humidity: float = Field(default=0.85)
    spray_flux: Optional[float] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/capability")
async def capability() -> dict:
    """Model description + acceptance-criterion framing (audit friendly)."""
    return {
        "capability": "ship.icing.superstructure",
        "evidence_level": EVIDENCE_LEVEL,
        "regimes": ["dry", "wet", "dry_nospray"],
        "heat_flux_terms": [
            "forced+free convection (flat plate, film properties)",
            "evaporation (liquid film) / sublimation (dry ice) via Lewis analogy",
            "long-wave radiation, sky+sea view factors",
            "solar short-wave (optional)",
            "spray sensible heat",
            "freezing latent heat",
            "ice-layer conduction resistance",
        ],
        "stability": [
            "ice mass/centroid integration over the panel mesh",
            "KG/GM recomputed with sinkage and transverse CG shift",
            "GZ from exact section integration (wall-sided + deck immersion)",
            "heeling moment and equilibrium heel from asymmetric ice",
            "IS Code 2008 A.2.2 criteria with icing; Polar Code I-A 6.3 allowance",
        ],
        "acceptance_criteria": {
            "dry_static_heat_balance_residual_w_m2": 1e-6,
            "icing_rate_vs_analytic_reference_rel_error": 0.15,
            "gm_loss_vs_layered_superposition_rel_error": 0.03,
            "pytest_min_passed": 15,
        },
        "limitations": [
            "wall-sided rectangular hull section (barge idealisation) for GZ",
            "spray sub-model W_ref = 2.0e-4 kg/m^3 is the single calibration knob",
            "no ice shedding / melting / thermal inertia of the steel structure",
        ],
    }


def _windward_normal(wind_dir: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """Outward normal of a surface blowing *into* the wind (unit-ish)."""
    mag = sum(c * c for c in wind_dir) ** 0.5 or 1.0
    return tuple(-c / mag for c in wind_dir)  # type: ignore[return-value]


@router.post("/heat_balance")
async def heat_balance(req: HeatBalanceRequest) -> dict:
    met = req.to_metocean()
    air, water = met.air(), met.water()
    rel_speed = met.relative_wind_speed
    rel_dir = met.relative_wind_dir
    coeffs = ice.transfer_coefficients(air, rel_speed, req.char_length)
    flux = req.spray_flux
    if flux is None:
        flux = ice.spray_mass_flux(rel_speed, "windward",
                                   _windward_normal(rel_dir), rel_dir)
    bal = ice.panel_heat_balance(air, water, coeffs, flux,
                                 view_factor_to_sky=req.view_factor_to_sky)
    out = bal.as_dict()
    out["relative_wind_speed_ms"] = rel_speed
    out["h_conv_w_m2k"] = coeffs.h_conv
    out["h_mass_m_s"] = coeffs.h_mass
    out["reynolds"] = coeffs.reynolds
    out["prandtl"] = coeffs.prandtl
    out["schmidt"] = coeffs.schmidt
    out["freezing_point_c"] = water.t_freezing - 273.15
    out["ice_thickness_rate_mm_per_h"] = 3.6e6 * bal.ice_mass_flux / ice.RHO_ICE
    return out


@router.post("/spray")
async def spray(req: MetoceanRequest) -> dict:
    hs = ice.significant_wave_height(req.wind_speed)
    return {
        "wind_speed_ms": req.wind_speed,
        "significant_wave_height_m": hs,
        "effective_spray_content_kg_m3": ice.effective_spray_content(req.wind_speed),
        "flux_kg_m2s": {
            zone: ice.spray_mass_flux(req.wind_speed, zone, normal, req.wind_dir)
            for zone, normal in {
                "windward": _windward_normal(req.wind_dir),
                "side": (1.0, 0.0, 0.0),
                "leeward": tuple(-c for c in _windward_normal(req.wind_dir)),
                "horizontal_up": (0.0, 0.0, 1.0),
            }.items()
        },
        "calibration": ice.CALIBRATION,
    }


@router.post("/accumulate")
async def accumulate(req: AccumulateRequest) -> dict:
    factory = ice.PRESETS.get(req.vessel)
    if factory is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown vessel preset '{req.vessel}'; available: {sorted(ice.PRESETS)}",
        )
    vessel = factory()
    if req.panel_size is not None:
        vessel = ice.VesselSpec(
            name=vessel.name, hull=vessel.hull,
            kg_light=vessel.kg_light if req.kg_m is None else req.kg_m,
            blocks=vessel.blocks, deck_height=vessel.deck_height,
            deck_area=vessel.deck_area, panel_size=req.panel_size,
        )
    elif req.kg_m is not None:
        vessel = ice.VesselSpec(
            name=vessel.name, hull=vessel.hull, kg_light=req.kg_m,
            blocks=vessel.blocks, deck_height=vessel.deck_height,
            deck_area=vessel.deck_area, panel_size=vessel.panel_size,
        )
    result = ice.run_icing_case(
        vessel, req.to_metocean(),
        duration_h=req.duration_h, dt_s=req.dt_s,
    )
    payload = result.to_dict()
    if not req.include_panels:
        payload.pop("panels", None)
    return payload


@router.post("/benchmark")
async def benchmark(req: BenchmarkRequest) -> dict:
    """Calibration-point comparison of the panel model vs the analytic reference."""
    met = ice.Metocean(t_air_c=req.t_air_c, wind_speed=req.wind_speed,
                       t_water_c=req.t_water_c, salinity=req.salinity,
                       rel_humidity=req.rel_humidity)
    air, water = met.air(), met.water()
    rel_speed = met.relative_wind_speed
    coeffs = ice.transfer_coefficients(air, rel_speed, req.char_length)
    flux = req.spray_flux
    if flux is None:
        flux = ice.spray_mass_flux(rel_speed, "windward",
                                   _windward_normal(met.relative_wind_dir),
                                   met.relative_wind_dir)
    bal = ice.panel_heat_balance(air, water, coeffs, flux)
    model_rate = 3.6e6 * bal.ice_mass_flux / 917.0
    ref = makiranta_reference_icing_rate(
        req.t_air_c, req.wind_speed, t_water_c=req.t_water_c,
        salinity=req.salinity, rel_humidity=req.rel_humidity,
        char_length=req.char_length, spray_flux=flux,
    )
    ref_rate = ref.ice_thickness_rate * 3.6e6
    rel_err = abs(model_rate - ref_rate) / ref_rate if ref_rate else None
    return {
        "model": {**bal.as_dict(), "ice_thickness_rate_mm_per_h": model_rate},
        "reference": ref.as_dict(),
        "relative_error": rel_err,
        "tolerance": 0.15,
        "pass": (rel_err is not None and rel_err < 0.15),
        "reference_note": (
            "Analytic closed-form Mákiranta-type reference (independent closures); "
            "literature-level 15% band, not a <3% validation."
        ),
    }