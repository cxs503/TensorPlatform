"""Joint polar voyage optimisation: route x speed x ice strategy x CII.

This module glues the physics pieces together into the production entry point

    optimize_voyage(field, ship, start, end, deadline, convoy=...)
        -> optimal track, per-leg speeds, fuel / brake energy, attained CII and
           rating for a chosen year, plus the energy-optimal escort spacing if a
           convoy configuration is supplied.

and provides the acceptance checks that need more than one module:

    verify_route_improvement(...)   criterion (e): optimal route vs the *best*
                                    member of an experience-route family
    full_acceptance_report(...)     criteria (a)-(f) in one JSON document
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import MODULE_ID, MODULE_VERSION
from .channel import verify_channel_concentrations
from .cii import VoyageEfficiency, required_cii, cii_rating, attained_cii_from_fuel
from .constants import KNOT, NM_M
from .convoy import ConvoyConfig, verify_escort_optimum
from .ice_field import IceField
from .resistance import verify_level_ice_conditions
from .route import (
    EXPERIENCE_MARGIN,
    RoutePlanner,
    experience_baseline,
    max_sustainable_speed_ms,
    plan_voyage,
)
from .ships import ShipGeometry, validate_geometry

#: Latitudes (km from the ice edge) scanned when building the experience-route
#: family: the way a master picks a band off the ice chart.
EXPERIENCE_BANDS_KM: Tuple[float, ...] = (20.0, 50.0, 80.0, 110.0, 140.0, 170.0)
#: Longitudinal inset used for the dogleg waypoints [km].
EXPERIENCE_INSET_KM = 60.0
#: Default demonstration voyage: corridor transit across the heavy-ice tongue of
#: the ``nsr_corridor`` scenario.
DEFAULT_ROUTE_START_KM: Tuple[float, float] = (15.0, 200.0)
DEFAULT_ROUTE_END_KM: Tuple[float, float] = (405.0, 100.0)
#: Schedule requirement (charter-party ETA) used by criterion (e) [h].
DEFAULT_DEADLINE_H = 30.0
#: Default hull for the route / CII demonstrations (Polar Class 6 bulker).
DEFAULT_ROUTE_SHIP = "pc6_bulker"


def voyage_efficiency_from_plan(
    ship: ShipGeometry,
    plan: Dict[str, Any],
    year: int = 2026,
) -> Dict[str, Any]:
    """CII / rating for a planned voyage."""
    distance_nm = plan["distance_km"] * 1000.0 / NM_M
    voyage = VoyageEfficiency(
        fuel_t=plan["fuel_kg"] / 1000.0,
        fuel_type=ship.fuel_type,
        distance_nm=distance_nm,
        capacity=ship.dwt_t,
        category=ship.category,
        brake_energy_mj=plan["brake_energy_mj"],
        sfoc_g_kwh=ship.sfoc_g_kwh,
    )
    return voyage.as_dict(year=year)


def _baseline_family(
    field: IceField,
    ship: ShipGeometry,
    start: Tuple[float, float],
    end: Tuple[float, float],
    modes: Sequence[str] = ("constant_worst_case", "station_wise"),
    deadline_h: Optional[float] = None,
    include_lead_aware: bool = True,
) -> List[Dict[str, Any]]:
    """All experience routes: direct track, latitude-band doglegs and -- when the
    field carries scenario leads -- a *lead-aware pilot* route through them."""
    track_options: List[Tuple[str, Optional[List[Tuple[float, float]]]]] = [
        ("direct", None)
    ]
    for band in EXPERIENCE_BANDS_KM:
        track_options.append(
            (
                f"band_{band:.0f}km",
                [
                    (start[0], start[1]),
                    (start[0] + EXPERIENCE_INSET_KM, band),
                    (end[0] - EXPERIENCE_INSET_KM, band),
                    (end[0], end[1]),
                ],
            )
        )
    if include_lead_aware and field.name == "nsr_corridor":
        # a pilot who has the ice chart flies the shore lead east to the junction,
        # then down the tongue-crossing lead, then to the destination
        # (only the lead points that lie on the way: east of the departure and
        #  north of the destination, i.e. no gratuitous overshoot)
        shore, crossing = IceField.scenario_lead_polylines("nsr_corridor")
        wps: List[Tuple[float, float]] = [tuple(start)]
        wps.extend(pt for pt in shore[1:] if start[0] < pt[0] <= 360.0)
        wps.extend(pt for pt in crossing[1:] if pt[1] >= min(start[1], end[1]))
        wps.append(tuple(end))
        track_options.append(("lead_aware_pilot", wps))

    out: List[Dict[str, Any]] = []
    for label, wps in track_options:
        for mode in modes:
            try:
                base = experience_baseline(
                    field, ship, start, end, mode=mode, waypoints=wps,
                    deadline_h=deadline_h,
                )
            except ValueError as exc:  # infeasible track (land / too thick ice)
                out.append({"label": label, "mode": mode, "feasible": False, "reason": str(exc)})
                continue
            out.append(
                {
                    "label": label,
                    "mode": mode,
                    "feasible": True,
                    "speed_scale": base["speed_scale"],
                    "fuel_t": base["fuel_kg"] / 1000.0,
                    "time_h": base["time_h"],
                    "distance_km": base["distance_km"],
                    "mean_speed_kn": base["mean_speed_kn"],
                    "mean_h_m": base["mean_h_m"],
                    "waypoints": base["waypoints"],
                    "points_km": base["points_km"],
                    "brake_energy_mj": base["brake_energy_mj"],
                }
            )
    return out


def best_experience_route(baselines: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    feasible = [b for b in baselines if b.get("feasible")]
    if not feasible:
        raise ValueError("no feasible experience-route baseline")
    return min(feasible, key=lambda b: b["fuel_t"])


def optimize_voyage(
    field: IceField,
    ship: ShipGeometry,
    start_km: Tuple[float, float],
    end_km: Tuple[float, float],
    deadline_h: Optional[float] = None,
    stride: int = 3,
    year: int = 2026,
    convoy: Optional[ConvoyConfig] = None,
    include_experience_baseline: bool = True,
) -> Dict[str, Any]:
    """Production entry point: joint route + speed optimisation of one voyage."""
    geom = validate_geometry(ship)
    if not geom["ok"]:
        raise ValueError(f"ship geometry failed validation: {geom['failures']}")

    plan = plan_voyage(field, ship, start_km, end_km, deadline_h=deadline_h, stride=stride)

    result: Dict[str, Any] = {
        "module": {"id": MODULE_ID, "version": MODULE_VERSION},
        "ship": ship.key,
        "ship_name": ship.name,
        "field": {"name": field.name, "source": field.source, "grid": field.summary()["grid"]},
        "deadline_h": deadline_h,
        "deadline_binding": plan["deadline_binding"],
        "route": {
            "distance_km": round(plan["distance_km"], 3),
            "distance_nm": round(plan["distance_km"] * 1000.0 / NM_M, 3),
            "time_h": round(plan["time_h"], 4),
            "mean_speed_kn": round(
                plan["distance_km"] * 1000.0 / (plan["time_h"] * 3600.0) / KNOT, 4
            ),
            "lambda_kg_per_h": plan["lambda_kg_per_h"],
            "waypoints_km": [[round(x, 2), round(y, 2)] for x, y in plan["points_km"]],
            "speeds_kn": plan.get("speed_profile_kn", []),
            "max_speed_kn": max(plan.get("speed_profile_kn") or [0.0]),
            "min_speed_kn": min(plan.get("speed_profile_kn") or [0.0]),
            "speed_by_ice": plan.get("speed_by_ice", []),
        },
        "energy": {
            "fuel_t": round(plan["fuel_kg"] / 1000.0, 4),
            "mean_h_m": round(plan.get("mean_h_m", 0.0), 4),
            "brake_energy_mj": round(plan["brake_energy_mj"], 2),
            "fuel_per_nm_kg": round(plan["fuel_kg"] / (plan["distance_km"] * 1000.0 / NM_M), 4),
        },
        "efficiency": voyage_efficiency_from_plan(ship, plan, year=year),
        "geometry_check": geom,
    }

    if convoy is not None:
        result["convoy"] = verify_escort_optimum(convoy)

    if include_experience_baseline:
        baselines = _baseline_family(field, ship, start_km, end_km)
        best = best_experience_route(baselines)
        result["experience_baselines"] = [
            {k: v for k, v in b.items() if k not in {"points_km", "waypoints"}}
            for b in baselines
        ]
        result["best_experience_route"] = {
            k: v for k, v in best.items() if k not in {"points_km", "waypoints"}
        }
        result["best_experience_route_points_km"] = [
            [round(x, 2), round(y, 2)] for x, y in best["points_km"]
        ]
        result["saving_vs_best_experience_pct"] = round(
            100.0 * (best["fuel_t"] - plan["fuel_kg"] / 1000.0) / best["fuel_t"], 3
        )
    return result


def verify_route_improvement(
    field: Optional[IceField] = None,
    ship: Optional[ShipGeometry] = None,
    start_km: Tuple[float, float] = DEFAULT_ROUTE_START_KM,
    end_km: Tuple[float, float] = DEFAULT_ROUTE_END_KM,
    deadline_h: float = DEFAULT_DEADLINE_H,
    stride: int = 3,
) -> Dict[str, Any]:
    """Criterion (e): energy-optimal route/speed vs the experience routes.

    All candidates -- the experience routes *and* the optimiser -- must meet the
    same ETA (``deadline_h``); the experience routes are uniformly speed-scaled
    onto that ETA so they get exactly the same schedule slack.  The baseline set
    contains the direct great circle, six latitude-band doglegs and a
    *lead-aware pilot* route through the scenario leads, each with two speed
    policies; the optimiser is compared against the **best** of them, so the
    saving cannot come from a strawman.
    """
    from .ice_field import IceField as _IceField
    from .ships import get_ship

    field = field or _IceField.generate_scenario()
    ship = ship or get_ship(DEFAULT_ROUTE_SHIP)

    baselines = _baseline_family(field, ship, start_km, end_km, deadline_h=deadline_h)
    best = best_experience_route(baselines)
    rule_based = best_experience_route(
        [b for b in baselines if b.get("feasible") and b["label"] != "lead_aware_pilot"]
    )

    plan = plan_voyage(field, ship, start_km, end_km, deadline_h=deadline_h, stride=stride)

    fuel_opt_t = plan["fuel_kg"] / 1000.0
    saving = 100.0 * (best["fuel_t"] - fuel_opt_t) / best["fuel_t"]
    saving_rule = 100.0 * (rule_based["fuel_t"] - fuel_opt_t) / rule_based["fuel_t"]

    return {
        "criterion": "e",
        "description": "optimal route+speed vs best experience route at equal ETA",
        "ship": ship.key,
        "field": {"name": field.name, "source": field.source},
        "start_km": list(start_km),
        "end_km": list(end_km),
        "deadline_h": deadline_h,
        "baselines": [
            {k: v for k, v in b.items() if k not in {"points_km", "waypoints"}}
            for b in baselines
        ],
        "best_baseline": {
            "label": best["label"],
            "mode": best["mode"],
            "fuel_t": round(best["fuel_t"], 4),
            "time_h": round(best["time_h"], 4),
            "distance_km": round(best["distance_km"], 3),
            "mean_speed_kn": round(best["mean_speed_kn"], 3),
        },
        "best_rule_based_baseline": {
            "label": rule_based["label"],
            "mode": rule_based["mode"],
            "fuel_t": round(rule_based["fuel_t"], 4),
            "time_h": round(rule_based["time_h"], 4),
            "distance_km": round(rule_based["distance_km"], 3),
        },
        "optimized": {
            "fuel_t": round(fuel_opt_t, 4),
            "time_h": round(plan["time_h"], 4),
            "distance_km": round(plan["distance_km"], 3),
            "mean_speed_kn": round(
                plan["distance_km"] * 1000.0 / (plan["time_h"] * 3600.0) / KNOT, 3
            ),
            "mean_h_m": round(plan["mean_h_m"], 4),
            "lambda_kg_per_h": plan["lambda_kg_per_h"],
            "speed_by_ice": plan["speed_by_ice"],
            "waypoints_km": [[round(x, 2), round(y, 2)] for x, y in plan["points_km"]],
        },
        "saving_vs_best_experience_pct": round(saving, 3),
        "saving_vs_best_rule_based_pct": round(saving_rule, 3),
        "threshold_pct": 5.0,
        "pass": bool(saving > 5.0),
    }


def full_acceptance_report(
    ship_key: str = "handysize_bulker",
    escort_key: str = "icebreaker_escort",
    follower_key: Optional[str] = None,
    year: int = 2026,
) -> Dict[str, Any]:
    """Criteria (a)-(e) of campaign node W3-N11 in one JSON document.

    Criterion (f) (pytest) is reported by the test runner, not here.
    """
    from .ships import get_ship

    ship = get_ship(ship_key)
    escort = get_ship(escort_key)
    follower = get_ship(follower_key or ship_key)

    convoy = ConvoyConfig(escort=escort, follower=follower, followers=2)
    criterion_a = verify_level_ice_conditions(ship)
    criterion_b = verify_channel_concentrations(ship)
    criterion_c = verify_escort_optimum(convoy)
    from .cii import verify_cii_ship_types

    criterion_d = verify_cii_ship_types(
        ["handysize_bulker", "aframax_tanker", "container_8000teu"], year=year
    )
    criterion_e = verify_route_improvement()

    return {
        "node": "W3-N11",
        "module": {"id": MODULE_ID, "version": MODULE_VERSION},
        "ship": ship.key,
        "criteria": {
            "a_ice_resistance": criterion_a,
            "b_channel_resistance": criterion_b,
            "c_escort_spacing": criterion_c,
            "d_cii": criterion_d,
            "e_route_optimisation": criterion_e,
        },
        "all_pass": bool(
            criterion_a["pass"]
            and criterion_b["pass"]
            and criterion_c["pass"]
            and criterion_d["pass"]
            and criterion_e["pass"]
        ),
    }