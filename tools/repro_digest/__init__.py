"""Cross-ISA floating-point reproducibility protocol (IN22-N08).

Public surface::

    from tools.repro_digest import ulp
    ulp.quant_digest(values)            # 12-significant-digit canonical digest
    ulp.ulp_distance(a, b)              # exact ULP distance
    ulp.compare_records(ref, cur)       # per-field report
    ulp.assert_reproducible(ref, cur, ulp_gate=k)

pytest fixture ``assert_reproducible`` lives in :mod:`tools.repro_digest.pytest_plugin`.
"""

from . import ulp  # noqa: F401

__all__ = ["ulp"]
__version__ = "1.0.0"