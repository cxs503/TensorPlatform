"""
Polar voyage performance package: ice resistance, brash-ice channel, escort
spacing optimisation, CII/EEDI energy efficiency and ice-field route search.

Module map
----------
constants.py   physical constants + IMO (MEPC.353(78)/354(78)/364(79)/203(62)) tables
ships.py       multi-ship-type geometry presets + geometry validation (Cb/L/B)
ice_field.py   2-D ice-field discretisation (h, sigma, current, wind) + scenario generator
resistance.py  Holtrop-Mennen open water + first-principles level-ice resistance
channel.py     brash-ice channel resistance (calibrated) vs Riska-type reference
convoy.py      escort spacing s -> convoy energy E(s), interior minimum
cii.py         CII (attained/required/rating) and EEDI/EEXI linkage
route.py       A*/Dijkstra energy-optimal path + joint route-speed schedule
optimizer.py   joint polar voyage optimisation (route x speed x ice strategy)

All routines are pure-Python/numpy, deterministic and free of hidden state so
that they can be unit-tested and replayed (no random numbers at call time).
"""

from __future__ import annotations

__all__ = [
    "MODULE_ID",
    "MODULE_VERSION",
]

MODULE_ID = "tp-polar-voyage"
MODULE_VERSION = "1.0.0"

#: Evidence level of the physics implemented in this package.
#: REAL  = closed-form / first-principles algorithms, no hard-coded outputs.
#: The empirical coefficients are documented engineering calibrations, they are
#: NOT fitted to a specific full-scale trial (see docs/polar/POLAR_VOYAGE_CII_ESCORT.md).
EVIDENCE_LEVEL = "REAL"