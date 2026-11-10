"""Geometry / mesh validation pipeline (mission p372w2-n04, campaign geometry-validation).

Domain-neutral engine
---------------------
This package implements the *mechanism* of a pre-solve validation gate:

1. ``geometry_extract``  -- read a surface description (STL / OBJ / optional IGES
   tessellator), check watertightness, and derive integral geometric quantities
   (displacement volume below a waterline, bounding-box extents, waterplane area,
   block / waterplane coefficients, longitudinal centre of volume).
2. ``mesh_quality``      -- read a volume mesh (legacy-VTK ASCII or an OpenFOAM
   ``polyMesh`` directory, or a solver log) and evaluate per-cell volume,
   equiangle skewness, aspect ratio and first-cell wall distance y+.
3. ``interceptor``       -- compare derived values with declared values and with
   *applicability envelopes* that are **data**, loaded from a profile file.
4. ``pipeline``          -- batch orchestration with per-item error recovery,
   verdicts, machine-readable reason codes, and a hash-chained audit log.

Design rule (platform charter): no domain vocabulary or domain thresholds live in
the code.  Every threshold, tolerance and applicability envelope is injected from
the profile (see ``config/geometry_validation/profiles/``) so the same engine can
gate any geometry domain.

Public entry points
-------------------
``load_profile``, ``validate_item``, ``validate_batch``, ``AuditLog``,
``verify_audit_chain``.
"""
from __future__ import annotations

from .profile import ValidationProfile, load_profile, ProfileError
from .reason_codes import (
    REASON_CODES,
    GeometryValidationError,
    Finding,
    make_finding,
)
from .pipeline import validate_item, validate_batch, REPORT_SCHEMA_VERSION, TOOL_VERSION
from .audit import AuditLog, verify_audit_chain

__all__ = [
    "ValidationProfile",
    "load_profile",
    "ProfileError",
    "REASON_CODES",
    "GeometryValidationError",
    "Finding",
    "make_finding",
    "validate_item",
    "validate_batch",
    "REPORT_SCHEMA_VERSION",
    "TOOL_VERSION",
    "AuditLog",
    "verify_audit_chain",
]