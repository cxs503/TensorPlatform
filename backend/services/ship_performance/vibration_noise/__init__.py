"""Vibration & noise domain of the ship overall-performance platform.

This package fills the last empty domain of the platform's subject matrix
(阻力 / 推进 / 耐波 / 操纵 / 稳性 / 结构 / 振动噪声 / 绿色航行 / 冰区航行).

Evidence status of the sub-modules
----------------------------------
=================  ==========================================================
module             evidence / anchor
=================  ==========================================================
``excitation``     REAL — definitional kinematics (blade rate, shaft order,
                   Strouhal, cavitation number); no fitted constants.
``structural``     REAL — SDOF transmissibility closed form + FD / Rayleigh-Ritz
                   eigen-solvers verified against exact Euler-Bernoulli and
                   Kirchhoff-plate frequencies.
``acoustics``      REAL — exact monopole/dipole radiation, mass-law TL, SI level
                   bookkeeping.
``criteria``       WITHHELD — limit tables are contractual/licensed and absent
                   from this repository; only fail-closed machinery is shipped.
=================  ==========================================================

Verification: ``backend/tests/test_m1707_vibration_noise_verification.py`` and
``tools/benchmarks/vibration_noise_anchors.py`` (artifacts under
``artifacts/m1707/``).  Report: ``docs/reports/m1707_vibration_noise_verification.md``.
"""

from __future__ import annotations

from .acoustics import (  # noqa: F401
    limp_mass_transmission_loss_db,
    monopole_pressure_amplitude,
    monopole_radiated_power,
    monopole_radiation_resistance,
    dipole_radiated_power,
    pulsating_sphere_radiation_efficiency,
    sound_pressure_level_db,
    two_monopole_power_numeric,
)
from .criteria import (  # noqa: F401
    WITHHELD_REASON,
    LimitProfile,
    band_energy_sum,
    evaluate_velocity,
    withheld_limit_profiles,
)
from .excitation import (  # noqa: F401
    blade_rate_hz,
    blade_rate_harmonics,
    cavitation_number,
    propeller_tip_speed,
    shaft_order_hz,
    strouhal_frequency,
)
from .structural import (  # noqa: F401
    cantilever_frequencies,
    cantilever_rayleigh_ritz,
    half_power_bandwidth_ratio,
    isolation_insertion_loss_db,
    sdof_compliance,
    sdof_magnification_peak,
    sdof_magnification_peak_ratio,
    sdof_transmissibility,
    sdof_transmissibility_peak,
    sdof_transmissibility_peak_ratio,
    ssss_beam_exact_frequencies,
    ssss_beam_fd_frequencies,
    ssss_plate_fd_modes,
    ssss_plate_frequencies,
)

DOMAIN_EVIDENCE = {
    "excitation": "REAL",
    "structural": "REAL",
    "acoustics": "REAL",
    "criteria": "WITHHELD",
}

__all__ = [
    "DOMAIN_EVIDENCE",
    "WITHHELD_REASON",
    "LimitProfile",
    "band_energy_sum",
    "blade_rate_hz",
    "blade_rate_harmonics",
    "cantilever_frequencies",
    "cantilever_rayleigh_ritz",
    "cavitation_number",
    "dipole_radiated_power",
    "evaluate_velocity",
    "half_power_bandwidth_ratio",
    "isolation_insertion_loss_db",
    "limp_mass_transmission_loss_db",
    "monopole_pressure_amplitude",
    "monopole_radiated_power",
    "monopole_radiation_resistance",
    "propeller_tip_speed",
    "pulsating_sphere_radiation_efficiency",
    "sdof_compliance",
    "sdof_magnification_peak",
    "sdof_magnification_peak_ratio",
    "sdof_transmissibility",
    "sdof_transmissibility_peak",
    "sdof_transmissibility_peak_ratio",
    "shaft_order_hz",
    "sound_pressure_level_db",
    "ssss_beam_exact_frequencies",
    "ssss_beam_fd_frequencies",
    "ssss_plate_fd_modes",
    "ssss_plate_frequencies",
    "strouhal_frequency",
    "two_monopole_power_numeric",
    "withheld_limit_profiles",
]