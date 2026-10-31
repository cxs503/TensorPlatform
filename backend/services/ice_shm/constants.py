"""Shared constants for the ice-load inversion / SHM package.

Material data follow DNV *Rules for classification: Ships* (minimum specified
yield stress of normal and high strength hull structural steel); the
safety-factor bands follow the mission acceptance criterion
(SF >= 1.5 SAFE, 1.0 <= SF < 1.5 MARGINAL, SF < 1.0 FAIL).
"""
from __future__ import annotations

from typing import Dict

#: Steel Young's modulus (DNV rules for classification: ships, Pt.3 Ch.1).
DEFAULT_YOUNG_MODULUS_PA: float = 206.9e9

#: Minimum specified yield stress for DNV normal / high strength hull grades.
STEEL_YIELD_PA: Dict[str, float] = {
    "NV_A": 235.0e6,
    "NV_B": 235.0e6,
    "NV_D": 235.0e6,
    "NV_E": 235.0e6,
    "NV_A36": 355.0e6,
    "NV_D36": 355.0e6,
    "NV_E36": 355.0e6,
    "NV_F36": 355.0e6,
    "NV_A40": 390.0e6,
    "NV_D40": 390.0e6,
    "NV_E40": 390.0e6,
    "NV_F40": 390.0e6,
}

DEFAULT_YIELD_PA: float = STEEL_YIELD_PA["NV_A36"]

#: Safety-factor acceptance bands.
SF_SAFE_THRESHOLD: float = 1.5
SF_FAIL_THRESHOLD: float = 1.0

#: Euler-Bernoulli validity: shear deformation negligible for slender girders.
MIN_SLENDERNESS_L_OVER_H: float = 10.0

__all__ = [
    "DEFAULT_YOUNG_MODULUS_PA",
    "STEEL_YIELD_PA",
    "DEFAULT_YIELD_PA",
    "SF_SAFE_THRESHOLD",
    "SF_FAIL_THRESHOLD",
    "MIN_SLENDERNESS_L_OVER_H",
]