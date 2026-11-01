"""Verification / numerical-V&V (GCI) toolkit for TensorPlatform.

Import surface::

    from src.verification import gci, observed_order, richardson_extrapolate
"""

from src.verification.gci import (  # noqa: F401
    FS_THREE_GRIDS,
    FS_TWO_GRIDS,
    GCIRegime,
    GCIResult,
    R_MIN_RECOMMENDED,
    gci,
    observed_order,
    refinement_ratio_ok,
    richardson_extrapolate,
)

__all__ = [
    "FS_THREE_GRIDS",
    "FS_TWO_GRIDS",
    "GCIRegime",
    "GCIResult",
    "R_MIN_RECOMMENDED",
    "gci",
    "observed_order",
    "refinement_ratio_ok",
    "richardson_extrapolate",
]