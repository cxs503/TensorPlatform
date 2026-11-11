"""Synthetic application layer for the invariant audit (no mechanism imports)."""
from __future__ import annotations

from .synthetic_models import (
    boost_response_heuristic,
    displacement_exact,
    postprocess_dimensions_formula_bug,
    postprocess_dimensions_ok,
    postprocess_dimensions_unit_bug,
    response_diagonal,
    transport_grid,
)

__all__ = [
    "boost_response_heuristic",
    "displacement_exact",
    "postprocess_dimensions_formula_bug",
    "postprocess_dimensions_ok",
    "postprocess_dimensions_unit_bug",
    "response_diagonal",
    "transport_grid",
]