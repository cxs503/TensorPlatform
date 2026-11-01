"""Underwater acoustics: monopole / dipole radiation and level bookkeeping.

All relations below are exact far-field results (or exact algebraic identities),
so they can be verified to machine precision against their closed forms:

* monopole pressure amplitude ``|p| = rho c k Q / (4 pi r)``
* monopole radiated power ``P = rho c k^2 Q^2 / (8 pi)``  (=> radiation
  resistance ``R_r = rho c k^2 / (4 pi)``, from ``P = 1/2 R_r Q^2``)
* dipole (two closely spaced opposite monopoles, separation ``d``)
  ``P = rho c k^4 Q^2 d^2 / (24 pi)``  -> the classical ``k^4`` (dipole) law
* pulsating sphere of radius ``a`` with prescribed surface velocity ``U``:
  ``sigma_rad = P / (1/2 rho c U^2 * 4 pi a^2) = (k a)^2``
* limp (mass-law) wall, normal incidence: ``TL = 10 log10(1 + (omega m / (2 rho c))^2)``
* SI levels re 1 uPa / 1 m, and incoherent / coherent dB addition

References
----------
* Kinsler, L.E. et al., *Fundamentals of Acoustics*, 4th ed., ch. 7-8.
* Blake, W.K., *Mechanics of Flow-Induced Sound and Vibration*, vol. 1, ch. 4.
* Ross, D., *Mechanics of Underwater Noise*, ch. 8 (level conventions).
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

P_REF_PA = 1.0e-6          # 1 uPa, underwater acoustics reference pressure
RHO_SEAWATER = 1025.0      # kg/m^3
C_SEAWATER = 1500.0        # m/s


def wavenumber(freq_hz: float, c: float = C_SEAWATER) -> float:
    """Acoustic wavenumber ``k = 2 pi f / c``."""
    return 2.0 * math.pi * freq_hz / c


def monopole_pressure_amplitude(freq_hz: float, q_amp: float, r: float,
                                rho: float = RHO_SEAWATER, c: float = C_SEAWATER) -> float:
    """Far-field pressure amplitude of a monopole with volume-velocity amplitude ``q_amp`` [m^3/s]."""
    if r <= 0.0:
        raise ValueError("r must be positive")
    k = wavenumber(freq_hz, c)
    return rho * c * k * q_amp / (4.0 * math.pi * r)


def monopole_radiated_power(freq_hz: float, q_amp: float,
                            rho: float = RHO_SEAWATER, c: float = C_SEAWATER) -> float:
    """Radiated acoustic power of a monopole [W]: ``P = rho c k^2 Q^2 / (8 pi)``."""
    k = wavenumber(freq_hz, c)
    return rho * c * k * k * q_amp * q_amp / (8.0 * math.pi)


def monopole_radiation_resistance(freq_hz: float,
                                  rho: float = RHO_SEAWATER, c: float = C_SEAWATER) -> float:
    """Radiation resistance referred to volume velocity [kg/(m^4 s)]: ``R_r = rho c k^2/(4 pi)``.

    Defined by ``P = 0.5 * R_r * Q^2`` with ``Q`` the *amplitude* of volume velocity.
    """
    k = wavenumber(freq_hz, c)
    return rho * c * k * k / (4.0 * math.pi)


def dipole_radiated_power(freq_hz: float, q_amp: float, separation: float,
                          rho: float = RHO_SEAWATER, c: float = C_SEAWATER) -> float:
    """Radiated power of a compact dipole (separation ``d`` << wavelength) [W].

    ``P = rho c k^4 Q^2 d^2 / (24 pi)``.
    """
    k = wavenumber(freq_hz, c)
    return rho * c * k ** 4 * q_amp * q_amp * separation * separation / (24.0 * math.pi)


def pulsating_sphere_radiation_efficiency(radius: float, freq_hz: float,
                                          c: float = C_SEAWATER) -> float:
    """Radiation efficiency of a pulsating sphere with prescribed surface velocity.

    ``sigma = P / (1/2 rho c U^2 * 4 pi a^2) = (k a)^2``, the classical
    small-source result (the sphere is then acoustically compact and a poor
    radiator at low ``ka``).
    """
    ka = wavenumber(freq_hz, c) * radius
    return ka * ka


def sound_pressure_level_db(p_rms_pa: float, p_ref: float = P_REF_PA) -> float:
    """SI level ``20 log10(p_rms / 1 uPa)``."""
    if p_rms_pa <= 0.0:
        raise ValueError("p_rms_pa must be positive")
    return 20.0 * math.log10(p_rms_pa / p_ref)


def source_level_at_1m_db(p_rms_at_r_pa: float, r: float, p_ref: float = P_REF_PA) -> float:
    """Back-propagate a spherical spreading measurement to the 1 m reference."""
    if r <= 0.0:
        raise ValueError("r must be positive")
    return sound_pressure_level_db(p_rms_at_r_pa * r, p_ref)


def incoherent_sum_db(levels_db: Sequence[float]) -> float:
    """Energy (incoherent) addition of uncorrelated sources."""
    p2 = sum(10.0 ** (lvl / 10.0) for lvl in levels_db)
    return 10.0 * math.log10(p2)


def coherent_sum_db_equal(n: int) -> float:
    """Coherent addition of ``n`` equal in-phase pressures: ``+20 log10 n`` dB."""
    if n < 1:
        raise ValueError("n must be >= 1")
    return 20.0 * math.log10(n)


def limp_mass_transmission_loss_db(freq_hz: float, areal_density: float,
                                   rho: float = RHO_SEAWATER, c: float = C_SEAWATER) -> float:
    """Normal-incidence transmission loss of a limp mass [dB], exact.

    ``TL = 10 log10(1 + (omega m / (2 rho c))^2)``; the ``+6 dB/octave``
    mass law is its high-frequency asymptote.
    """
    if areal_density <= 0.0:
        raise ValueError("areal_density must be positive")
    omega = 2.0 * math.pi * freq_hz
    return 10.0 * math.log10(1.0 + (omega * areal_density / (2.0 * rho * c)) ** 2)


def two_monopole_pressure_field(freq_hz: float, q_amp: float, separation: float,
                                r_obs: float, n_theta: int = 360,
                                c: float = C_SEAWATER) -> dict:
    """Far-field pattern of two *opposite* monopoles (+Q at -d/2, -Q at +d/2).

    Used for the numerical cross-check of the ``k^4`` dipole law: the squared
    pressure is integrated over a sphere in the far field (``kr >> 1``).
    """
    k = wavenumber(freq_hz, c)
    d = separation
    theta = np.linspace(0.0, math.pi, n_theta)
    # path difference between the two sources at field angle theta
    phase = k * d * np.cos(theta)
    p_dir = np.abs(1.0 - np.exp(1j * phase))  # |1 - e^{i phi}|
    base = RHO_SEAWATER * c * k * q_amp / (4.0 * math.pi * r_obs)
    p_amp = base * p_dir
    return {"theta": theta, "p_amp": p_amp, "base": base, "k": k}


def two_monopole_power_numeric(freq_hz: float, q_amp: float, separation: float,
                               r_obs: float, n_theta: int = 720,
                               rho: float = RHO_SEAWATER, c: float = C_SEAWATER) -> float:
    """Power of the opposite-monopole pair from far-field intensity integration.

    ``P = int I dA`` with ``I = p_amp^2 / (2 rho c)`` (far field) and
    ``dA = 2 pi r^2 sin(theta) dtheta``.
    """
    fld = two_monopole_pressure_field(freq_hz, q_amp, separation, r_obs, n_theta, c)
    theta = fld["theta"]
    p = fld["p_amp"]
    integrand = p * p / (2.0 * rho * c) * 2.0 * math.pi * r_obs ** 2 * np.sin(theta)
    return float(np.trapezoid(integrand, theta))


__all__ = [
    "P_REF_PA",
    "RHO_SEAWATER",
    "C_SEAWATER",
    "wavenumber",
    "monopole_pressure_amplitude",
    "monopole_radiated_power",
    "monopole_radiation_resistance",
    "dipole_radiated_power",
    "pulsating_sphere_radiation_efficiency",
    "sound_pressure_level_db",
    "source_level_at_1m_db",
    "incoherent_sum_db",
    "coherent_sum_db_equal",
    "limp_mass_transmission_loss_db",
    "two_monopole_pressure_field",
    "two_monopole_power_numeric",
]