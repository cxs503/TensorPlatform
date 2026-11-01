"""Independent reference implementations used to author verification questions.

The verifier question bank must never source its expected values from the code
under test; these modules provide the independent side of the comparison.
"""
from . import holtrop_mennen_1982, integrity  # noqa: F401

__all__ = ["holtrop_mennen_1982", "integrity"]
