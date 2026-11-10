"""Evidence Chain Integrity — auto-verify report six elements.

Checks:
  1. Number traceability (every numeric claim has SQL/artifact source)
  2. Figure sha256 (every referenced figure has a hash in provenance)
  3. Criteria coverage (all acceptance criteria are addressed)
  4. Six-element completeness (intro/method/results/figures/notes/error)
  5. Honest-FAIL declaration (if results are negative, they're labeled)
  6. Audit trail (timestamps, versions, commit refs present)

Design: standalone, zero domain semantics, stdlib only.
"""
from .checker import (
    EvidenceReport,
    VerificationResult,
    verify_report,
    verify_six_elements,
    verify_number_traceability,
    verify_figure_provenance,
    verify_criteria_coverage,
)

__all__ = [
    "EvidenceReport",
    "VerificationResult",
    "verify_report",
    "verify_six_elements",
    "verify_number_traceability",
    "verify_figure_provenance",
    "verify_criteria_coverage",
]
__version__ = "1.0.0"
