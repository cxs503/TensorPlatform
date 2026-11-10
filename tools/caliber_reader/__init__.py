"""Caliber reader: machine interpretation of a benchmark table's measurement basis.

Domain-neutral by construction: the engine implements the *mechanism* (closure
test, hypothesis search, fail-closed verdict, audited decision gate); every
physical number enters through injected configuration (``lines.json``,
``reader_config.json``) and through the table artifact itself.
"""
from .lines import LineSet, LineResult, LineSpec, LineError, load_lineset
from .reader import (interpret, invert_pair, attribute_deviation, parse_table, Row,
                     ERROR_DECISION_ALLOWED, EXIT_CODES)
from .gate import judge_error, rejection_rate, AuditLog

__all__ = [
    "LineSet", "LineResult", "LineSpec", "LineError", "load_lineset",
    "interpret", "invert_pair", "attribute_deviation", "parse_table", "Row",
    "ERROR_DECISION_ALLOWED", "EXIT_CODES",
    "judge_error", "rejection_rate", "AuditLog",
]

__version__ = "1.0.0"