"""Negative findings extraction and duplicate detection.

Zero domain semantics: all extraction patterns and field names are configurable.
"""

from .extractor import NegativeFindingsExtractor, NegativeFinding
from .fingerprint import SimHash, FingerprintGate

__all__ = [
    "NegativeFindingsExtractor",
    "NegativeFinding",
    "SimHash",
    "FingerprintGate",
]
