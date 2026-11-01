"""Independent ship-performance verification suite (TensorPlatform, m1536).

Provides published-method reference implementations and audit harnesses used to
re-verify already-merged platform capabilities against literature/analytic baselines.
"""

from .holtrop1984 import (  # noqa: F401
    AFRAMAX,
    BENCHMARK_HULLS,
    HullGeometry,
    KCS,
    KVLCC2,
    ResistanceResult,
    cf_ittc1957,
    cf_schoenherr,
    eedi_reference,
    form_factor_1pk,
    holtrop1984_resistance,
    wetted_surface,
    wetted_surface_denny_mumford,
)

__all__ = [
    "AFRAMAX", "BENCHMARK_HULLS", "HullGeometry", "KCS", "KVLCC2",
    "ResistanceResult", "cf_ittc1957", "cf_schoenherr", "eedi_reference",
    "form_factor_1pk", "holtrop1984_resistance", "wetted_surface",
    "wetted_surface_denny_mumford",
]
