"""Calibrated section closure for the blade-element propeller solver.

Why this module exists
----------------------
``backend/services/propeller_open_water.py`` was certified (mission 1393) on
*structural* and *analytic* criteria only: open-water efficiency identity,
actuator-disc bound, monotonicity, Froude-scaling invariance.  Its absolute
level against a published reference had never been measured, and the
verification document therefore carried ``WITHHELD`` on absolute accuracy
(``docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md`` §5.1/§6).

The repo already ships an independent, provenance-tracked transcription of the
Oosterveld & van Oossanen (1975) Wageningen B-series regression
(``tools/verify/propeller_open_water/reference_bseries.py``, 39 ``KT`` terms /
47 ``KQ`` terms).  Benchmarking the BEM against it (mission 1668,
``tools/verify/propeller_open_water/bem_vs_bseries.py``) shows the certified
model is systematically off: ``KT`` up to +37 % at high blade-area ratio /
pitch, ``KQ`` down to -30 % at low blade-area ratio, and a zero-thrust advance
ratio ``J0 ~= 0.99 P/D`` against ``J0 ~= 1.20 P/D`` in the reference.

The dominant missing physics is *section camber*: the certified closure is a
symmetric thin-airfoil law ``Cl = 2 pi sin(alpha)``, so the blade only stops
producing thrust at ``phi = beta`` (``J0 = P/D``).  Real B-series sections use a
NACA ``a = 0.8`` mean line, which remains loaded at zero incidence and pushes
``J0`` well above ``P/D``.  Secondary effect: a blade cascade has a lower lift
slope than an isolated section, and the deficit grows with solidity, which is
why the error is largest at ``Ae/A0 = 0.70``.

This module adds those two effects as an *explicit, documented, three-parameter*
mutation of the certified section model, calibrated once against the B-series
polynomial on a training grid and validated on a disjoint hold-out grid:

    Cl = lift_scale(sigma) * 2 pi * sin(alpha + a0),   lift_scale = 1/(1 + k_sigma*sigma)
    Cd = drag_scale * (CD0 + alpha^2)

with ``sigma`` the local solidity at 0.7R, ``sigma = Z*c(0.7R) / (2 pi 0.7R)``.

The certified default path is untouched: ``SectionModel()`` with unit parameters
reproduces ``blade_element_section`` exactly, and ``open_water_point`` without a
``section`` argument is bit-identical to the pre-calibration code.

Fitted constants (provenance: ``outputs/propeller_open_water/calibration_fit.json``,
produced by ``python3 tools/verify/propeller_open_water/bem_vs_bseries.py --fit``;
training grid Z=4, P/D in {0.8, 1.0, 1.2}, Ae/A0 in {0.40, 0.55, 0.70};
hold-out grid Z in {5, 6}, P/D in {0.9, 1.1}, Ae/A0 in {0.475, 0.625}).
"""

from __future__ import annotations

import math
from typing import Dict, Optional

from backend.services.propeller_open_water import (
    PropellerGeometry,
    SectionModel,
    open_water_point,
)

__all__ = [
    "CALIBRATION",
    "solidity_07",
    "calibrated_section",
    "open_water_point_calibrated",
]

#: Fitted to the B-series polynomial; see module docstring for provenance.
CALIBRATION: Dict[str, float] = {
    "zero_lift_rad": 0.028,   # camber zero-lift angle [rad] (NACA a=0.8 mean line)
    "k_solidity": 0.70,       # cascade lift-slope deficit coefficient
    "drag_scale": 1.375,      # section drag multiplier (incl. cascade/roughness)
}

#: Fit provenance: 0.0126 objective (mean squared relative error on KT and KQ),
#: fitted on the Z=4 training grid only; hold-out (Z=5,6) errors are reported in
#: docs/verifier/BEM_VS_BSERIES_BENCHMARK.md and never used for tuning.


def solidity_07(geom: PropellerGeometry) -> float:
    """Local solidity at 0.7R: ``Z * c(0.7R) / (2 pi 0.7R)``."""
    r_07 = 0.7 * geom.radius_m
    return geom.blade_count * geom.chord_m(0.7) / (2.0 * math.pi * r_07)


def calibrated_section(
    geom: PropellerGeometry,
    zero_lift_rad: Optional[float] = None,
    k_solidity: Optional[float] = None,
    drag_scale: Optional[float] = None,
) -> SectionModel:
    """Section closure with camber + cascade lift-slope deficit at this geometry."""
    a0 = CALIBRATION["zero_lift_rad"] if zero_lift_rad is None else zero_lift_rad
    k_s = CALIBRATION["k_solidity"] if k_solidity is None else k_solidity
    ds = CALIBRATION["drag_scale"] if drag_scale is None else drag_scale
    sigma = solidity_07(geom)
    lift_scale = 1.0 / (1.0 + k_s * sigma)
    return SectionModel(lift_scale=lift_scale, zero_lift_rad=a0, drag_scale=ds)


def open_water_point_calibrated(
    geom: PropellerGeometry,
    J: float,
    **kwargs,
) -> Dict[str, float]:
    """``open_water_point`` evaluated with the calibrated section closure."""
    kwargs.pop("section", None)
    return open_water_point(geom, J, section=calibrated_section(geom), **kwargs)

def zero_thrust_advance_ratio_calibrated(
    geom: PropellerGeometry,
    hi: float = 3.0,
    tol: float = 1e-5,
) -> float:
    """``J0`` (KT = 0) for the calibrated closure, by bisection on KT(J)."""
    section = calibrated_section(geom)
    lo = 1e-6
    if open_water_point(geom, lo, section=section)["KT"] <= 0.0:
        return 0.0
    if open_water_point(geom, hi, section=section)["KT"] > 0.0:
        return hi
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if open_water_point(geom, mid, section=section)["KT"] > 0.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)
