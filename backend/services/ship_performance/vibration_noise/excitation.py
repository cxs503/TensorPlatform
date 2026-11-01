"""Vibration & noise excitation kinematics (propeller / shaft / flow).

Scope note
----------
This module implements only **kinematical and definitional** quantities, i.e.
relations that follow from the definition of rotational frequency, blade count,
Strouhal number and cavitation number.  No empirical curve-fit constant is
introduced here, so every number produced is exact given its inputs
(evidence grade REAL for the kinematics; it does *not* claim predictions of
pressure-pulse amplitude, which require empirical/literature constants and are
deliberately NOT implemented — see ``docs/reports/m1707_vibration_noise_verification.md``).

References
----------
* Carlton, J.S., *Marine Propellers and Propulsion*, 3rd ed., ch. 12
  (blade rate / shaft-order excitation).
* ITTC 7.5-02-03-01.4 (cavitation number definition).
* Blake, W.K., *Mechanics of Flow-Induced Sound and Vibration*, vol. 1, ch. 1
  (Strouhal frequency of separated flow).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence


def shaft_rate_hz(shaft_rpm: float) -> float:
    """Revolutions per second of the propeller shaft."""
    if shaft_rpm < 0.0:
        raise ValueError("shaft_rpm must be non-negative")
    return shaft_rpm / 60.0


def blade_rate_hz(shaft_rpm: float, n_blades: int) -> float:
    """Blade passing frequency ``f_BPF = n * Z`` in Hz."""
    if n_blades < 1:
        raise ValueError("n_blades must be >= 1")
    return shaft_rate_hz(shaft_rpm) * n_blades


def shaft_order_hz(shaft_rpm: float, order: float) -> float:
    """Frequency of a given shaft *order* (order 1 = once per revolution)."""
    if order < 0.0:
        raise ValueError("order must be non-negative")
    return shaft_rate_hz(shaft_rpm) * order


def blade_rate_harmonics(shaft_rpm: float, n_blades: int, n_harmonics: int) -> List[Dict[str, float]]:
    """First ``n_harmonics`` blade-rate harmonics as (order, frequency) pairs.

    The k-th blade-rate harmonic sits at shaft order ``k * Z``.
    """
    if n_harmonics < 1:
        raise ValueError("n_harmonics must be >= 1")
    out = []
    for k in range(1, n_harmonics + 1):
        order = k * n_blades
        out.append({"harmonic": k, "order": float(order), "frequency_hz": shaft_order_hz(shaft_rpm, order)})
    return out


def order_track(rpm_range: Sequence[float], order: float) -> List[Dict[str, float]]:
    """Map an engine-order track over an RPM ramp (order analysis / Campbell data)."""
    track = []
    for rpm in rpm_range:
        track.append({"rpm": float(rpm), "frequency_hz": shaft_order_hz(float(rpm), order)})
    return track


def propeller_tip_speed(shaft_rpm: float, radius_m: float) -> float:
    """Tip speed ``U = omega * R`` in m/s (cavitation/erosion screening)."""
    if radius_m <= 0.0:
        raise ValueError("radius_m must be positive")
    return 2.0 * math.pi * shaft_rate_hz(shaft_rpm) * radius_m


def cavitation_number(p_abs_pa: float, p_vapour_pa: float, rho: float, v_ref_ms: float) -> float:
    """Cavitation number ``sigma = (p_abs - p_v) / (0.5 * rho * V^2)``.

    ``p_abs_pa`` must be the *absolute* static pressure at the reference point
    (e.g. shaft-centre immersion ``p_atm + rho g h``), ``v_ref_ms`` the reference
    velocity at that point.
    """
    if v_ref_ms <= 0.0:
        raise ValueError("v_ref_ms must be positive")
    return (p_abs_pa - p_vapour_pa) / (0.5 * rho * v_ref_ms ** 2)


def strouhal_frequency(velocity_ms: float, length_m: float, strouhal: float = 0.2) -> float:
    """Shedding frequency ``f = St * U / D`` (flow-induced noise screening)."""
    if length_m <= 0.0:
        raise ValueError("length_m must be positive")
    return strouhal * velocity_ms / length_m


@dataclass(frozen=True)
class WakeHarmonic:
    """One harmonic of the circumferential wake field seen by a blade."""

    order: int  # shaft order
    frequency_hz: float


def wake_harmonics(shaft_rpm: float, n_blades: int, n_harmonics: int) -> List[WakeHarmonic]:
    """Shaft orders that excite the hull through a Z-bladed propeller.

    A Z-bladed propeller rotating in a fixed non-uniform wake produces hull
    excitation at blade-rate multiples ``k*Z``; these are enumerated per-rev
    orders with their frequencies at the given RPM.
    """
    return [WakeHarmonic(order=h["order"], frequency_hz=h["frequency_hz"])
            for h in blade_rate_harmonics(shaft_rpm, n_blades, n_harmonics)]


__all__ = [
    "shaft_rate_hz",
    "blade_rate_hz",
    "shaft_order_hz",
    "blade_rate_harmonics",
    "order_track",
    "propeller_tip_speed",
    "cavitation_number",
    "strouhal_frequency",
    "WakeHarmonic",
    "wake_harmonics",
]