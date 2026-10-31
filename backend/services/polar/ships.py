"""Multi-ship-type geometry presets and geometry validation for polar voyages.

The presets are representative mid-size commercial hulls plus an escort
icebreaker.  They are *presets* (documented, editable engineering data) and not
the output of a particular vessel's lines plan; the polar models only need the
principal particulars plus a bow angle and a friction coefficient.

Geometry validation follows the platform rule "geometries must be validated on
Cb / L / B before any performance number is reported":

* 0.50 <= Cb <= 0.90            (displacement hulls; 0.50-0.70 for icebreakers)
* 4.0  <= L/B <= 10.0
* 2.0  <= B/T <= 6.0
* block coefficient consistency: |Cb - nabla/(L B T)| <= 2 % of Cb
* deadweight must be smaller than the displacement implied by Cb L B T
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .constants import RHO_WATER

#: Physical / engineering validity windows used by :func:`validate_geometry`.
GEOMETRY_LIMITS = {
    "cb": (0.50, 0.90),
    "l_over_b": (4.0, 10.0),
    "b_over_t": (2.0, 6.0),
    "bow_half_angle_deg": (8.0, 45.0),
    "cb_consistency_rel": 0.02,
}


@dataclass(frozen=True)
class ShipGeometry:
    """Principal particulars of a ship used by the polar performance models.

    Attributes
    ----------
    key            short identifier used by the API (stable, lowercase)
    category       IMO CII ship-type category (see constants.CII_REFERENCE_LINES)
    length_m       length between perpendiculars [m]
    beam_m         moulded beam [m]
    draft_m        scantling / actual draft [m]
    cb             block coefficient based on L B T
    dwt_t          deadweight [t] (CII transport-work capacity)
    design_speed_kn design speed in open water [kn]
    brake_power_kw installed main-engine brake power [kW]
    sfoc_g_kwh     specific fuel oil consumption [g/kWh]
    eta_propulsive total propulsive efficiency (open water, hull, rotative, shaft)
    bow_half_angle_deg  mean waterline entrance half angle at the ice contact zone
    ice_class      ice class label (documentation only, the physics is set by h_max)
    h_max_m        maximum level-ice thickness the ship may break (operability limit)
    mu_hull_ice    ice-hull friction coefficient in water [-]
    """

    key: str
    name: str
    category: str
    length_m: float
    beam_m: float
    draft_m: float
    cb: float
    dwt_t: float
    design_speed_kn: float
    brake_power_kw: float
    sfoc_g_kwh: float
    eta_propulsive: float
    bow_half_angle_deg: float = 25.0
    ice_class: str = "1A"
    h_max_m: float = 1.0
    mu_hull_ice: float = 0.10
    fuel_type: str = "heavy_fuel_oil"
    tags: List[str] = field(default_factory=list)

    # -- derived quantities --------------------------------------------------
    @property
    def displacement_m3(self) -> float:
        """Volume displacement nabla = Cb * L * B * T [m3]."""
        return self.cb * self.length_m * self.beam_m * self.draft_m

    @property
    def displacement_t(self) -> float:
        """Mass displacement in tonnes (sea water)."""
        return self.displacement_m3 * RHO_WATER / 1000.0

    @property
    def l_over_b(self) -> float:
        return self.length_m / self.beam_m

    @property
    def b_over_t(self) -> float:
        return self.beam_m / self.draft_m

    @property
    def design_speed_ms(self) -> float:
        from .constants import KNOT

        return self.design_speed_kn * KNOT

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category,
            "length_m": self.length_m,
            "beam_m": self.beam_m,
            "draft_m": self.draft_m,
            "cb": self.cb,
            "dwt_t": self.dwt_t,
            "design_speed_kn": self.design_speed_kn,
            "brake_power_kw": self.brake_power_kw,
            "sfoc_g_kwh": self.sfoc_g_kwh,
            "eta_propulsive": self.eta_propulsive,
            "bow_half_angle_deg": self.bow_half_angle_deg,
            "ice_class": self.ice_class,
            "h_max_m": self.h_max_m,
            "mu_hull_ice": self.mu_hull_ice,
            "fuel_type": self.fuel_type,
            "derived": {
                "displacement_m3": round(self.displacement_m3, 3),
                "displacement_t": round(self.displacement_t, 2),
                "l_over_b": round(self.l_over_b, 4),
                "b_over_t": round(self.b_over_t, 4),
            },
        }


# ---------------------------------------------------------------------------
# Preset fleet
# ---------------------------------------------------------------------------
_PRESETS: Dict[str, ShipGeometry] = {
    "handysize_bulker": ShipGeometry(
        key="handysize_bulker",
        name="Handysize bulk carrier (35k DWT, 1A)",
        category="bulk_carrier",
        length_m=180.0,
        beam_m=28.4,
        draft_m=10.5,
        cb=0.83,
        dwt_t=35000.0,
        design_speed_kn=14.0,
        brake_power_kw=7500.0,
        sfoc_g_kwh=178.0,
        eta_propulsive=0.68,
        bow_half_angle_deg=26.0,
        ice_class="1A",
        h_max_m=1.0,
        tags=["bulk", "arctic"],
    ),
    "pc6_bulker": ShipGeometry(
        key="pc6_bulker",
        name="Polar Class 6 ice-class bulker (55k DWT)",
        category="bulk_carrier",
        length_m=200.0,
        beam_m=30.0,
        draft_m=11.0,
        cb=0.82,
        dwt_t=55000.0,
        design_speed_kn=14.5,
        brake_power_kw=13000.0,
        sfoc_g_kwh=175.0,
        eta_propulsive=0.66,
        bow_half_angle_deg=20.0,
        ice_class="PC6",
        h_max_m=1.4,
        tags=["bulk", "polar", "independent"],
    ),
    "aframax_tanker": ShipGeometry(
        key="aframax_tanker",
        name="Aframax tanker (110k DWT)",
        category="tanker",
        length_m=249.0,
        beam_m=44.0,
        draft_m=14.9,
        cb=0.83,
        dwt_t=110000.0,
        design_speed_kn=14.5,
        brake_power_kw=15500.0,
        sfoc_g_kwh=176.0,
        eta_propulsive=0.70,
        bow_half_angle_deg=28.0,
        ice_class="1A",
        h_max_m=0.9,
        fuel_type="heavy_fuel_oil",
        tags=["tanker"],
    ),
    "container_8000teu": ShipGeometry(
        key="container_8000teu",
        name="Container ship (8000 TEU, 95k DWT)",
        category="container_ship",
        length_m=300.0,
        beam_m=40.0,
        draft_m=13.5,
        cb=0.62,
        dwt_t=95000.0,
        design_speed_kn=22.0,
        brake_power_kw=45000.0,
        sfoc_g_kwh=172.0,
        eta_propulsive=0.72,
        bow_half_angle_deg=18.0,
        ice_class="1B",
        h_max_m=0.7,
        tags=["container", "hull-form"],
    ),
    "icebreaker_escort": ShipGeometry(
        key="icebreaker_escort",
        name="Polar escort icebreaker (25 MW)",
        category="tanker",  # CII category only used if CII is requested for it
        length_m=120.0,
        beam_m=26.0,
        draft_m=8.5,
        cb=0.60,
        dwt_t=8000.0,
        design_speed_kn=16.0,
        brake_power_kw=25000.0,
        sfoc_g_kwh=190.0,
        eta_propulsive=0.62,
        bow_half_angle_deg=22.0,
        ice_class="Polar-5",
        h_max_m=2.0,
        fuel_type="light_fuel_oil",
        tags=["escort", "icebreaker"],
    ),
}


def list_ships() -> List[Dict[str, Any]]:
    """Return the preset catalogue as JSON-serialisable dicts."""
    return [s.as_dict() for s in _PRESETS.values()]


def get_ship(key: str) -> ShipGeometry:
    """Look up a preset ship by key (case-insensitive)."""
    k = (key or "").strip().lower()
    if k not in _PRESETS:
        raise KeyError(
            f"unknown ship_type '{key}'; available: {sorted(_PRESETS)}"
        )
    return _PRESETS[k]


def ship_from_params(params: Dict[str, Any]) -> ShipGeometry:
    """Build a custom :class:`ShipGeometry` from an API payload.

    Missing optional fields fall back to the handysize bulker, so a caller can
    override only the principal particulars.
    """
    base = _PRESETS["handysize_bulker"]
    merged = base.as_dict()
    merged.pop("derived", None)
    for k, v in (params or {}).items():
        if k in merged and v is not None:
            merged[k] = v
    merged.setdefault("key", "custom")
    merged.setdefault("name", "custom hull")
    kwargs = {}
    for name, f in ShipGeometry.__dataclass_fields__.items():
        if name in merged:
            kwargs[name] = merged[name]
        elif f.default is not dataclasses.MISSING:
            kwargs[name] = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            kwargs[name] = f.default_factory()  # type: ignore[misc]
    return ShipGeometry(**kwargs)


def validate_geometry(ship: ShipGeometry) -> Dict[str, Any]:
    """Validate a hull's principal particulars.

    Returns a dict ``{ok: bool, checks: [...], failures: [...]}``.  The check is
    machine-readable so the API can answer 400 on an inconsistent hull instead
    of silently reporting performance numbers for an impossible ship.
    """
    checks: List[Dict[str, Any]] = []

    def _check(name: str, value: float, lo: float, hi: float) -> None:
        ok = lo <= value <= hi
        checks.append(
            {"name": name, "value": round(float(value), 6), "range": [lo, hi], "ok": ok}
        )

    _check("cb", ship.cb, *GEOMETRY_LIMITS["cb"])
    _check("l_over_b", ship.l_over_b, *GEOMETRY_LIMITS["l_over_b"])
    _check("b_over_t", ship.b_over_t, *GEOMETRY_LIMITS["b_over_t"])
    _check("bow_half_angle_deg", ship.bow_half_angle_deg, *GEOMETRY_LIMITS["bow_half_angle_deg"])
    _check("mu_hull_ice", ship.mu_hull_ice, 0.02, 0.40)
    _check("eta_propulsive", ship.eta_propulsive, 0.40, 0.85)

    # negative dimensions are impossible
    for name in ("length_m", "beam_m", "draft_m", "dwt_t", "brake_power_kw"):
        checks.append(
            {
                "name": f"{name}>0",
                "value": round(float(getattr(ship, name)), 6),
                "range": [0.0, "inf"],
                "ok": getattr(ship, name) > 0,
            }
        )

    # Cb consistency: cb must equal nabla/(L B T) for the reported displacement.
    nabla = ship.displacement_m3
    cb_from_volume = nabla / (ship.length_m * ship.beam_m * ship.draft_m)
    rel = abs(cb_from_volume - ship.cb) / max(ship.cb, 1e-9)
    checks.append(
        {
            "name": "cb_consistency",
            "value": round(rel, 9),
            "range": [0.0, GEOMETRY_LIMITS["cb_consistency_rel"]],
            "ok": rel <= GEOMETRY_LIMITS["cb_consistency_rel"],
        }
    )

    # deadweight must be below the displacement mass
    disp_t = ship.displacement_t
    checks.append(
        {
            "name": "dwt_below_displacement",
            "value": round(ship.dwt_t / disp_t, 6),
            "range": [0.0, 1.0],
            "ok": ship.dwt_t <= disp_t,
        }
    )

    failures = [c for c in checks if not c["ok"]]
    return {
        "ok": not failures,
        "ship": ship.key,
        "derived": {
            "displacement_m3": round(ship.displacement_m3, 3),
            "displacement_t": round(disp_t, 2),
            "l_over_b": round(ship.l_over_b, 4),
            "b_over_t": round(ship.b_over_t, 4),
        },
        "checks": checks,
        "failures": failures,
    }