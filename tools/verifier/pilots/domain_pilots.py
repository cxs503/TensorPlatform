#!/usr/bin/env python3
"""
Pilot: closed-form reference implementations for four ship domains.

Domains covered (all closed-form / exact identities — no empirical fits):
  * stability   : rectangular-barge KM, wall-sided GZ(φ), vanishing angle, roll period
  * propulsion  : actuator-disk momentum theory (ideal efficiency, induced velocity, ideal power)
  * green       : fuel→CO₂ stoichiometry (MARPOL Annex VI Reg.2 carbon fractions)
  * seakeeping  : encounter-frequency identity, heave natural period with added mass

Protocol (subprocess mode, same as resistance_calculator.py):
  stdin:  {"specification": {"formula": "<name>", ...}}
  stdout: {"result": {...}}

The verifier runs this candidate against held-out questions from the
question bank.  The expected values in the bank are computed by an
INDEPENDENT implementation in build_domain_questions.py; agreement to
machine precision is asserted in the test suite (cross-implementation
cross-check, zero hand-copied numbers).
"""
from __future__ import annotations

import json
import math
import sys

G_DEFAULT = 9.81
MOLAR_C = 12.011      # g/mol, IUPAC 2021
MOLAR_CO2 = 44.01     # g/mol
W_C_MGO = 0.863       # MARPOL Annex VI Reg.2, marine gas oil
W_C_LNG = 0.812       # MARPOL Annex VI Reg.2, LNG


# ── stability ──────────────────────────────────────────────────────────────

def barge_km(B: float, T: float) -> float:
    """Metacentric height KM = KB + BM = T/2 + B²/(12T).

    Exact for a rectangular waterplane (I_wp = L·B³/12, ∇ = L·B·T, L cancels).
    """
    if B <= 0 or T <= 0:
        raise ValueError("B and T must be positive")
    return T / 2.0 + B * B / (12.0 * T)


def wall_sided_gz(GM: float, B: float, phi_deg: float) -> float:
    """Wall-sided righting arm GZ(φ) = (GM + (B/2)·tanφ)·sin²φ.

    Exact for vertical sides (waterplane stays a rectangle of width B).
    """
    if B <= 0:
        raise ValueError("B must be positive")
    phi = math.radians(phi_deg)
    if abs(math.cos(phi)) < 1e-12:
        raise ValueError("phi too close to 90 deg")
    return (GM + (B / 2.0) * math.tan(phi)) * math.sin(phi) ** 2


def wall_sided_vanishing_angle(GM: float, B: float) -> float:
    """Angle of vanishing stability for a wall-sided vessel, in radians.

    GZ(φ)=0 (φ>0)  ⇔  GM + (B/2)·tanφ = 0  ⇔  tanφ_v = −2GM/B.
    Only defined for GM < 0 (initially unstable); otherwise no positive root.
    """
    if GM >= 0:
        raise ValueError("GM >= 0: no positive vanishing angle exists")
    if B <= 0:
        raise ValueError("B must be positive")
    return math.atan(-2.0 * GM / B)


def roll_period(I44: float, delta_kg: float, GM: float, g: float = G_DEFAULT) -> float:
    """Small-amplitude roll period T4 = 2π·√(I44/(Δ·g·GM))."""
    if GM <= 0 or delta_kg <= 0 or I44 <= 0:
        raise ValueError("I44, delta, GM must be positive")
    return 2.0 * math.pi * math.sqrt(I44 / (delta_kg * g * GM))


# ── propulsion (actuator-disk momentum theory, exact) ─────────────────────

def _disk_axial(C_T: float) -> float:
    """Axial induction factor a from C_T = a(1+a):  a = (√(1+4C_T)−1)/2."""
    if C_T < 0:
        raise ValueError("C_T must be >= 0")
    return (math.sqrt(1.0 + 4.0 * C_T) - 1.0) / 2.0


def disk_eta_ideal(C_T: float) -> float:
    """Ideal (loss-free) actuator-disk efficiency η = 1/(1+a) = 2/(1+√(1+4C_T)).

    Convention: C_T = T/(ρ·A·V₀²), momentum thrust T = ρ·A·V₀²·a(1+a).
    """
    return 1.0 / (1.0 + _disk_axial(C_T))


def disk_induced_ratio(C_T: float) -> float:
    """Induced-velocity ratio ΔV/V₀ = a = (√(1+4C_T)−1)/2 (momentum theory)."""
    return _disk_axial(C_T)


def disk_ideal_power(T_N: float, V0: float, C_T: float) -> float:
    """Ideal shaft power P = T·V₀/η_ideal = T·V₀·(1+a) [W]."""
    if T_N < 0 or V0 <= 0:
        raise ValueError("T_N >= 0, V0 > 0")
    return T_N * V0 * (1.0 + _disk_axial(C_T))


# ── green (stoichiometry, exact) ───────────────────────────────────────────

def co2_from_fuel(m_fuel_kg: float, w_C: float) -> float:
    """m_CO₂ = m_fuel · w_C · M(CO₂)/M(C)."""
    if m_fuel_kg < 0 or not (0.0 < w_C <= 1.0):
        raise ValueError("invalid inputs")
    return m_fuel_kg * w_C * MOLAR_CO2 / MOLAR_C


def co2_mgo(m_fuel_kg: float) -> float:
    """CO₂ from MGO (w_C = 0.863, MARPOL Annex VI Reg.2)."""
    return co2_from_fuel(m_fuel_kg, W_C_MGO)


def co2_lng(m_fuel_kg: float) -> float:
    """CO₂ from LNG (w_C = 0.812, MARPOL Annex VI Reg.2)."""
    return co2_from_fuel(m_fuel_kg, W_C_LNG)


# ── seakeeping (exact identities) ──────────────────────────────────────────

def encounter_frequency(omega: float, V: float, mu_deg: float, g: float = G_DEFAULT) -> float:
    """Encounter frequency ω_e = ω·(1 − V·cosμ·ω/g) = ω·(1 − Fr_w·cosμ)."""
    mu = math.radians(mu_deg)
    return omega * (1.0 - V * math.cos(mu) * omega / g)


def heave_period(m_kg: float, a33_ratio: float, rho: float, nabla_m3: float,
                  g: float = G_DEFAULT) -> float:
    """Heave natural period T33 = 2π·√((m + A33)/(ρ·∇·g)), A33 = a33_ratio·m."""
    if m_kg <= 0 or rho <= 0 or nabla_m3 <= 0:
        raise ValueError("invalid inputs")
    m_eff = m_kg * (1.0 + a33_ratio)
    return 2.0 * math.pi * math.sqrt(m_eff / (rho * nabla_m3 * g))


# ── dispatch ───────────────────────────────────────────────────────────────

def candidate(spec: dict) -> dict:
    """Dispatch on spec['formula']; returns the result dict."""
    f = spec["formula"]
    if f == "barge_km":
        return {"KM": barge_km(spec["B"], spec["T"])}
    if f == "wall_sided_gz":
        return {"GZ": wall_sided_gz(spec["GM"], spec["B"], spec["phi_deg"])}
    if f == "vanishing_angle":
        return {"phi_v_rad": wall_sided_vanishing_angle(spec["GM"], spec["B"])}
    if f == "roll_period":
        return {"T4": roll_period(spec["I44"], spec["delta_kg"], spec["GM"], spec.get("g", G_DEFAULT))}
    if f == "disk_eta_ideal":
        return {"eta_ideal": disk_eta_ideal(spec["C_T"])}
    if f == "disk_induced":
        return {"dv_ratio": disk_induced_ratio(spec["C_T"])}
    if f == "disk_ideal_power":
        return {"P_ideal_W": disk_ideal_power(spec["T_N"], spec["V0"], spec["C_T"])}
    if f == "co2_stoich":
        return {"co2_kg": co2_from_fuel(spec["m_fuel_kg"], spec["w_C"])}
    if f == "co2_mgo":
        return {"co2_kg": co2_mgo(spec["m_fuel_kg"])}
    if f == "co2_lng":
        return {"co2_kg": co2_lng(spec["m_fuel_kg"])}
    if f == "encounter_freq":
        return {"omega_e": encounter_frequency(spec["omega"], spec["V"], spec["mu_deg"], spec.get("g", G_DEFAULT))}
    if f == "heave_period":
        return {"T33": heave_period(spec["m_kg"], spec["a33_ratio"], spec["rho"], spec["nabla_m3"], spec.get("g", G_DEFAULT))}
    raise ValueError(f"unknown formula: {f}")


def main() -> None:
    spec = json.load(sys.stdin)
    result = candidate(spec["specification"])
    json.dump({"result": result}, sys.stdout)


if __name__ == "__main__":
    main()
