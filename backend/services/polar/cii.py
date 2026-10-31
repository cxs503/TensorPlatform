"""CII / EEDI / EEXI energy-efficiency calculations (IMO framework).

Implemented exactly as specified by

* MEPC.353(78) G1  -- CII reference lines,
* MEPC.354(78) G4  -- CII rating boundaries d1..d4,
* MEPC.364(79)     -- carbon factors C_F and the EEXI calculation method,
* MEPC.203(62)     -- EEDI reference lines and phase reduction factors.

Two independent code paths compute the attained CII:

``attained_cii_from_fuel``   mass path: fuel mass [t] -> CO2 [g] -> CII
``attained_cii_from_energy`` energy path: brake energy [MJ] -> fuel [g] -> CO2 -> CII

They are kept separate on purpose (different unit chains, written out
explicitly) so that acceptance criterion (d) can compare them and catch unit /
carbon-factor mistakes.  Whether the *absolute* CII level of a real ship matches
a verifier's calculation cannot be checked offline (no fleet dataset here), so
criterion (d) is reported as: two-path consistency plus exact agreement with the
guideline anchor values (Z schedule and rating-boundary multipliers), with the
absolute validation marked WITHHELD.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .constants import (
    CARBON_FACTORS,
    CII_RATING_BOUNDARIES,
    CII_REDUCTION_FACTOR,
    CII_REFERENCE_LINES,
    EEDI_PHASE_REDUCTION,
    EEDI_REFERENCE_LINES,
    NM_M,
)

#: MJ per kWh
MJ_PER_KWH = 3.6


def _cf(fuel_type: str) -> float:
    if fuel_type not in CARBON_FACTORS:
        raise ValueError(
            f"unknown fuel type '{fuel_type}'; known: {sorted(CARBON_FACTORS)}"
        )
    return CARBON_FACTORS[fuel_type]


def co2_from_fuel(fuel_t: float, fuel_type: str) -> float:
    """CO2 mass [t] from fuel mass [t]."""
    return fuel_t * _cf(fuel_type)


def cii_reference_value(category: str, capacity: float) -> float:
    """CII reference line value [g CO2 / (capacity * nm)]."""
    if category not in CII_REFERENCE_LINES:
        raise ValueError(
            f"no CII reference line for category '{category}'; "
            f"known: {sorted(CII_REFERENCE_LINES)}"
        )
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    line = CII_REFERENCE_LINES[category]
    return line["a"] * capacity ** line["c"]


def required_cii(category: str, capacity: float, year: int) -> Dict[str, float]:
    """Required annual operational CII for a given year [g/(capacity nm)]."""
    z = CII_REDUCTION_FACTOR.get(int(year))
    if z is None:
        raise ValueError(
            f"no CII reduction factor for year {year}; "
            f"known: {sorted(CII_REDUCTION_FACTOR)}"
        )
    ref = cii_reference_value(category, capacity)
    return {
        "year": int(year),
        "reduction_factor_pct": z,
        "cii_reference": ref,
        "required_cii": ref * (1.0 - z / 100.0),
    }


def attained_cii_from_fuel(
    fuel_t: float,
    fuel_type: str,
    capacity: float,
    distance_nm: float,
) -> float:
    """Attained CII [g CO2 / (capacity * nm)] -- mass path."""
    if fuel_t < 0:
        raise ValueError("fuel must be non-negative")
    if capacity <= 0 or distance_nm <= 0:
        raise ValueError("capacity and distance must be positive")
    co2_g = fuel_t * 1.0e6 * _cf(fuel_type)     # t -> g
    return co2_g / (capacity * distance_nm)


def attained_cii_from_energy(
    brake_energy_mj: float,
    sfoc_g_kwh: float,
    fuel_type: str,
    capacity: float,
    distance_nm: float,
) -> float:
    """Attained CII [g CO2 / (capacity * nm)] -- energy path.

    brake energy [MJ] -> brake work [kWh] -> fuel [g] via SFOC -> CO2 [g].
    """
    if brake_energy_mj < 0:
        raise ValueError("energy must be non-negative")
    if capacity <= 0 or distance_nm <= 0:
        raise ValueError("capacity and distance must be positive")
    brake_kwh = brake_energy_mj / MJ_PER_KWH
    fuel_g = brake_kwh * sfoc_g_kwh
    co2_g = fuel_g * _cf(fuel_type)
    return co2_g / (capacity * distance_nm)


def cii_rating(attained: float, required: float) -> Dict[str, Any]:
    """CII rating A-E from the guideline boundary multipliers."""
    if required <= 0:
        raise ValueError("required CII must be positive")
    ratio = attained / required
    b = CII_RATING_BOUNDARIES
    if ratio <= b["d1"]:
        grade = "A"
    elif ratio <= b["d2"]:
        grade = "B"
    elif ratio <= b["d3"]:
        grade = "C"
    elif ratio <= b["d4"]:
        grade = "D"
    else:
        grade = "E"
    return {
        "rating": grade,
        "attained_over_required": round(ratio, 6),
        "boundaries": dict(b),
        "compliant": grade in {"A", "B", "C"},
    }


# ---------------------------------------------------------------------------
# EEDI / EEXI linkage
# ---------------------------------------------------------------------------

def eedi_reference_value(category: str, dwt: float) -> float:
    if category not in EEDI_REFERENCE_LINES:
        raise ValueError(f"no EEDI reference line for '{category}'")
    line = EEDI_REFERENCE_LINES[category]
    return line["a"] * dwt ** (-line["c"])


def required_eedi(category: str, dwt: float, phase: int = 2) -> Dict[str, float]:
    if phase not in EEDI_PHASE_REDUCTION:
        raise ValueError(f"unknown EEDI phase {phase}")
    ref = eedi_reference_value(category, dwt)
    red = EEDI_PHASE_REDUCTION[phase]
    return {
        "phase": phase,
        "reduction_factor_pct": red,
        "eedi_reference": ref,
        "required_eedi": ref * (1.0 - red / 100.0),
    }


def attained_eedi(
    p_me_kw: float,
    sfoc_g_kwh: float,
    fuel_type: str,
    dwt: float,
    v_ref_kn: float,
    p_ae_kw: float = 0.0,
    sfoc_ae_g_kwh: Optional[float] = None,
) -> float:
    """Attained EEDI [g CO2 / (t nm)] for a single-fuel ship.

    EEDI = ( sum(P_ME) C_F SFOC_ME + P_AE C_F SFOC_AE ) / (capacity * V_ref)
    (f_j, f_i, f_c = 1: no ice-class or capacity correction applied, documented).
    """
    if dwt <= 0 or v_ref_kn <= 0:
        raise ValueError("dwt and v_ref must be positive")
    cf = _cf(fuel_type)
    sfoc_ae = sfoc_g_kwh if sfoc_ae_g_kwh is None else sfoc_ae_g_kwh
    co2_g_h = p_me_kw * sfoc_g_kwh * cf + p_ae_kw * sfoc_ae * cf
    return co2_g_h / (dwt * v_ref_kn)


def attained_eexi(
    p_me_kw: float,
    sfoc_g_kwh: float,
    fuel_type: str,
    dwt: float,
    v_ref_kn: float,
    p_ae_kw: float = 0.0,
    sfoc_ae_g_kwh: Optional[float] = None,
) -> float:
    """Attained EEXI [g CO2 / (t nm)] (MEPC.364(79) simplified form).

    The 75 % main-engine load factor of the guideline is applied to P_ME; the
    capacity/ice-class correction factors f_i, f_c are taken as 1.
    """
    if dwt <= 0 or v_ref_kn <= 0:
        raise ValueError("dwt and v_ref must be positive")
    cf = _cf(fuel_type)
    sfoc_ae = sfoc_g_kwh if sfoc_ae_g_kwh is None else sfoc_ae_g_kwh
    co2_g_h = 0.75 * p_me_kw * sfoc_g_kwh * cf + p_ae_kw * sfoc_ae * cf
    return co2_g_h / (dwt * v_ref_kn)


@dataclass
class VoyageEfficiency:
    """Aggregated voyage energy-efficiency result."""

    fuel_t: float
    fuel_type: str
    distance_nm: float
    capacity: float
    category: str
    brake_energy_mj: float
    sfoc_g_kwh: float

    def attained_cii(self) -> float:
        return attained_cii_from_fuel(
            self.fuel_t, self.fuel_type, self.capacity, self.distance_nm
        )

    def attained_cii_energy_path(self) -> float:
        return attained_cii_from_energy(
            self.brake_energy_mj, self.sfoc_g_kwh, self.fuel_type,
            self.capacity, self.distance_nm,
        )

    def as_dict(self, year: int = 2026) -> Dict[str, Any]:
        att = self.attained_cii()
        att_energy = self.attained_cii_energy_path()
        req = required_cii(self.category, self.capacity, year)
        rating = cii_rating(att, req["required_cii"])
        rel = abs(att - att_energy) / att if att > 0 else 0.0
        return {
            "fuel_t": round(self.fuel_t, 4),
            "fuel_type": self.fuel_type,
            "co2_t": round(co2_from_fuel(self.fuel_t, self.fuel_type), 4),
            "distance_nm": round(self.distance_nm, 2),
            "capacity": self.capacity,
            "category": self.category,
            "attained_cii": round(att, 6),
            "attained_cii_energy_path": round(att_energy, 6),
            "path_rel_diff_pct": round(100.0 * rel, 9),
            "year": year,
            "cii_reference": round(req["cii_reference"], 6),
            "required_cii": round(req["required_cii"], 6),
            "reduction_factor_pct": req["reduction_factor_pct"],
            **rating,
        }


def verify_cii_ship_types(ship_keys: List[str], year: int = 2026) -> Dict[str, Any]:
    """Criterion (d): CII vs the IMO guideline formulation, 3 ship types.

    For each ship type a representative laden polar voyage at design speed is
    built from the ship's own power/SFOC figures; the attained CII is then
    computed through the mass path and through the energy path, and the required
    CII / rating through the guideline tables.  Also checks the guideline anchor
    values (reduction-factor schedule 5/7/9/11 % and rating boundaries
    0.86/0.94/1.06/1.18).
    """
    from .ships import get_ship

    rows: List[Dict[str, Any]] = []
    for key in ship_keys:
        ship = get_ship(key)
        # representative voyage: 3000 nm at design speed, 85 % MCR, 5 % aux
        distance_nm = 3000.0
        speed_kn = ship.design_speed_kn
        hours = distance_nm / speed_kn
        p_me = 0.85 * ship.brake_power_kw
        p_ae = 0.05 * ship.brake_power_kw
        fuel_t = (p_me + p_ae) * ship.sfoc_g_kwh * hours / 1.0e6
        energy_mj = (p_me + p_ae) * hours * MJ_PER_KWH

        voyage = VoyageEfficiency(
            fuel_t=fuel_t,
            fuel_type=ship.fuel_type,
            distance_nm=distance_nm,
            capacity=ship.dwt_t,
            category=ship.category,
            brake_energy_mj=energy_mj,
            sfoc_g_kwh=ship.sfoc_g_kwh,
        )
        row = voyage.as_dict(year=year)
        row["ship"] = ship.key
        row["ship_name"] = ship.name
        rows.append(row)

    max_path_diff = max(abs(r["path_rel_diff_pct"]) for r in rows)

    # guideline anchor checks (exact expected values from MEPC.353(78)/354(78))
    anchor_checks = []
    expected_z = {2023: 5.0, 2024: 7.0, 2025: 9.0, 2026: 11.0}
    for y, z in expected_z.items():
        got = required_cii("bulk_carrier", 50000.0, y)["reduction_factor_pct"]
        anchor_checks.append(
            {
                "check": f"Z({y})",
                "expected": z,
                "actual": got,
                "rel_error_pct": round(abs(got - z) / z * 100.0, 9),
            }
        )
    for name, expected in (("d1", 0.86), ("d2", 0.94), ("d3", 1.06), ("d4", 1.18)):
        got = CII_RATING_BOUNDARIES[name]
        anchor_checks.append(
            {
                "check": f"rating_boundary_{name}",
                "expected": expected,
                "actual": got,
                "rel_error_pct": round(abs(got - expected) / expected * 100.0, 9),
            }
        )
    max_anchor_err = max(c["rel_error_pct"] for c in anchor_checks)

    return {
        "criterion": "d",
        "description": "CII vs IMO guideline formulation for 3 ship types "
        "(mass path vs energy path) + guideline anchor values",
        "year": year,
        "rows": rows,
        "max_two_path_rel_diff_pct": round(max_path_diff, 9),
        "guideline_anchor_checks": anchor_checks,
        "max_anchor_rel_error_pct": round(max_anchor_err, 9),
        "threshold_pct": 5.0,
        "pass": max_path_diff < 5.0 and max_anchor_err < 5.0,
        "limitation": "absolute CII of a real ship cannot be validated offline "
        "(no fleet/trial dataset); WITHHELD for absolute validation.",
    }