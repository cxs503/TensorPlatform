"""Cross-mapping failure modes and the ``failure_faces.json`` v2 contract.

A conservation gate that only returns a boolean is not auditable: when a map
loses mass the operator has to say *which cells failed and in which mode*.  This
module defines the cross-mapping extension of the platform's ``failure_faces``
artifact.

``failure_faces/v1`` (produced by the FEM side) describes *fracture* faces:
vertices, normal, area, centroid.  v2 keeps those fields untouched and adds a
per-face ``error_mode`` plus a ``cross_map`` block, so a coupling report can
carry dialogue between solvers:

.. code-block:: json

    {
      "schema": "failure_faces/v2",
      "error_modes": ["uncovered_destination", "nonnegativity_violation"],
      "faces": [{"face_id": 3, "error_mode": "uncovered_destination", ...}],
      "cross_map": {"matrix_digest": "...", "n_faults": 2, "tolerances": {...}}
    }

Reading is backward compatible: a v1 document loads through the same entry point
and its faces come back with ``error_mode = null``.

The mode vocabulary is domain-neutral — it names *what went wrong
numerically*, never what the field means.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as _dc_field
from enum import Enum
from typing import Dict, List, Optional, Sequence

import numpy as np

from .conservative import MapAudit, MapLaw

__all__ = [
    "CrossMapErrorMode",
    "CrossMapFault",
    "FaultTolerances",
    "detect_faults",
    "write_failure_faces",
    "load_failure_faces",
    "validate_failure_faces",
    "FAILURE_FACES_SCHEMA_V2",
]


FAILURE_FACES_SCHEMA_V2 = "failure_faces/v2"


class CrossMapErrorMode(str, Enum):
    """How a field map can be wrong (>= 3 modes are required by the gate)."""

    UNCOVERED_DESTINATION = "uncovered_destination"
    """Destination cells received no source measure: mass is silently dropped."""

    KIND_LAW_MISMATCH = "kind_law_mismatch"
    """The declared quantity kind and the applied conservation law disagree."""

    NONNEGATIVITY_VIOLATION = "nonnegativity_violation"
    """A non-negative quantity became negative (interpolation overshoot)."""

    MOMENT_DRIFT = "moment_drift"
    """Total conserved but the first moment moved: a biased operator."""

    OVERSHOOT = "overshoot"
    """Destination extrema exceed the source range: not a convex redistribution."""

    DISJOINT_MESHES = "disjoint_meshes"
    """The two discretisations do not intersect at all."""

    DEGENERATE_OVERLAP = "degenerate_overlap"
    """Partially covered destination: the operator is not a redistribution."""

    TOTAL_MISMATCH = "total_mismatch"
    """The conserved total changed beyond tolerance."""


@dataclass(frozen=True)
class FaultTolerances:
    """Per-mode tolerances (nothing is hard-coded downstream of this dataclass)."""

    total_rel: float = 5e-3
    moment_rel: float = 1e-2
    coverage: float = 1e-9
    nonnegativity: float = 1e-12
    overshoot: float = 1e-9


@dataclass
class CrossMapFault:
    """One failure record, shaped to be dropped into ``failure_faces[*]``."""

    mode: CrossMapErrorMode
    face_id: Optional[int] = None
    side: str = "dst"
    value: float = 0.0
    tolerance: float = 0.0
    detail: str = ""
    metrics: Dict[str, float] = _dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "face_id": self.face_id,
            "error_mode": self.mode.value,
            "side": self.side,
            "value": float(self.value),
            "tolerance": float(self.tolerance),
            "detail": self.detail,
            "metrics": {k: float(v) for k, v in self.metrics.items()},
        }


def detect_faults(
    audit: MapAudit,
    covered: Optional[np.ndarray] = None,
    *,
    moment_rel_error: Optional[float] = None,
    declared_kind: Optional[str] = None,
    expected_law: Optional[str] = None,
    tolerances: Optional[FaultTolerances] = None,
    max_cell_faults: int = 8,
) -> List[CrossMapFault]:
    """Turn a mapping audit into an explicit list of failure records.

    ``covered`` is the per-destination-cell covered fraction
    (:func:`tools.field_mapper.geometry.coverage_fraction`); when omitted the
    coverage-based modes are reported from the audit summary only.
    """
    tol = tolerances or FaultTolerances()
    faults: List[CrossMapFault] = []

    if audit.total_rel_error > tol.total_rel:
        faults.append(
            CrossMapFault(
                mode=CrossMapErrorMode.TOTAL_MISMATCH,
                value=audit.total_rel_error,
                tolerance=tol.total_rel,
                detail=f"conserved total changed by {audit.total_rel_error:.3e} (law={audit.law})",
                metrics={"total_src": audit.total_src, "total_dst": audit.total_dst},
            )
        )

    if declared_kind is not None and expected_law is not None:
        if audit.law != str(expected_law):
            faults.append(
                CrossMapFault(
                    mode=CrossMapErrorMode.KIND_LAW_MISMATCH,
                    value=float(audit.law != str(expected_law)),
                    tolerance=0.0,
                    detail=f"quantity declared {declared_kind!r} but law {audit.law!r} applied",
                )
            )
    if declared_kind is not None and audit.law == MapLaw.SCALAR.value and declared_kind in (
        MapLaw.DENSITY.value,
        MapLaw.EXTENSIVE.value,
    ):
        faults.append(
            CrossMapFault(
                mode=CrossMapErrorMode.KIND_LAW_MISMATCH,
                value=1.0,
                tolerance=0.0,
                detail=f"declared {declared_kind!r} is conserved, but the scalar law was applied",
            )
        )

    if audit.min_dst < -tol.nonnegativity and audit.min_src >= 0.0:
        faults.append(
            CrossMapFault(
                mode=CrossMapErrorMode.NONNEGATIVITY_VIOLATION,
                value=audit.min_dst,
                tolerance=tol.nonnegativity,
                detail="non-negative source produced a negative destination value",
            )
        )

    if audit.overshoot_ratio > 1.0 + tol.overshoot:
        faults.append(
            CrossMapFault(
                mode=CrossMapErrorMode.OVERSHOOT,
                value=audit.overshoot_ratio,
                tolerance=1.0 + tol.overshoot,
                detail="destination extrema exceed the source range (non-convex operator)",
            )
        )

    if moment_rel_error is not None and moment_rel_error > tol.moment_rel:
        faults.append(
            CrossMapFault(
                mode=CrossMapErrorMode.MOMENT_DRIFT,
                value=float(moment_rel_error),
                tolerance=tol.moment_rel,
                detail="first moment moved while the total was preserved",
            )
        )

    if covered is not None:
        covered = np.asarray(covered, dtype=float).reshape(-1)
        if covered.size and float(covered.max()) <= tol.coverage:
            faults.append(
                CrossMapFault(
                    mode=CrossMapErrorMode.DISJOINT_MESHES,
                    value=0.0,
                    tolerance=tol.coverage,
                    detail="no destination cell intersects any source cell",
                )
            )
        empty = np.flatnonzero(covered <= tol.coverage)[:max_cell_faults]
        for j in empty:
            faults.append(
                CrossMapFault(
                    mode=CrossMapErrorMode.UNCOVERED_DESTINATION,
                    face_id=int(j),
                    value=float(covered[j]),
                    tolerance=tol.coverage,
                    detail="destination cell receives no source measure",
                )
            )
        partial = np.flatnonzero((covered > tol.coverage) & (covered < 1.0 - tol.coverage))[:max_cell_faults]
        for j in partial:
            faults.append(
                CrossMapFault(
                    mode=CrossMapErrorMode.DEGENERATE_OVERLAP,
                    face_id=int(j),
                    value=float(covered[j]),
                    tolerance=1.0 - tol.coverage,
                    detail="destination cell is only partially covered",
                )
            )
    return faults


# --------------------------------------------------------------------------- #
# failure_faces.json v2 (v1-compatible reader)
# --------------------------------------------------------------------------- #
def write_failure_faces(
    path: str,
    faults: Sequence[CrossMapFault],
    *,
    faces: Optional[Sequence[dict]] = None,
    cross_map: Optional[dict] = None,
    generated_by: Optional[dict] = None,
) -> str:
    """Write a ``failure_faces/v2`` artifact (cross-mapping aware).

    ``faces`` may carry the v1 fracture-face fields (vertices/normal/area); each
    face is annotated with the ``error_mode`` of its fault when one exists.
    """
    by_face: Dict[int, CrossMapFault] = {
        int(f.face_id): f for f in faults if f.face_id is not None
    }
    payload_faces: List[dict] = []
    for idx, face in enumerate(faces or []):
        record = dict(face)
        record.setdefault("face_id", idx)
        fault = by_face.get(int(record["face_id"]))
        record["error_mode"] = fault.mode.value if fault else None
        payload_faces.append(record)
    for fault in faults:
        if fault.face_id is None:
            continue
        if any(int(f.get("face_id", -1)) == int(fault.face_id) for f in payload_faces):
            continue
        payload_faces.append(
            {
                "face_id": int(fault.face_id),
                "error_mode": fault.mode.value,
                "detail": fault.detail,
                "value": float(fault.value),
                "tolerance": float(fault.tolerance),
            }
        )
    modes = sorted({f.mode.value for f in faults})
    payload = {
        "schema": FAILURE_FACES_SCHEMA_V2,
        "generated_by": dict(generated_by or {}),
        "error_modes": modes,
        "n_faults": len(faults),
        "faults": [f.to_dict() for f in faults],
        "faces": payload_faces,
        "cross_map": dict(cross_map or {}),
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


def load_failure_faces(path: str):
    """Load a v1 **or** v2 ``failure_faces.json`` and summarise what it says.

    Returns ``(records, summary)``; ``records`` is the face list with the v2
    ``error_mode`` field defaulted to ``None`` for v1 inputs.
    """
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    schema = str(payload.get("schema", "failure_faces/v1")) if isinstance(payload, dict) else "failure_faces/v1"
    raw = payload.get("faces", []) if isinstance(payload, dict) else list(payload)
    records: List[dict] = []
    for idx, face in enumerate(raw or []):
        record = dict(face)
        record.setdefault("face_id", idx)
        record.setdefault("error_mode", None)
        records.append(record)
    face_modes = {r["error_mode"] for r in records if r.get("error_mode")}
    fault_modes = set()
    if isinstance(payload, dict):
        fault_modes = {f.get("error_mode") for f in payload.get("faults", []) if f.get("error_mode")}
    modes = sorted(face_modes | fault_modes)
    declared = payload.get("error_modes", []) if isinstance(payload, dict) else []
    summary = {
        "path": path,
        "schema": schema,
        "schema_version": 2 if schema == FAILURE_FACES_SCHEMA_V2 else 1,
        "n_faces": len(records),
        "error_modes_present": modes,
        "error_modes_declared": list(declared),
        "n_faults": int(payload.get("n_faults", len(payload.get("faults", [])) if isinstance(payload, dict) else 0)),
        "cross_map": payload.get("cross_map", {}) if isinstance(payload, dict) else {},
        "consistent": sorted(set(modes)) == sorted(set(declared)),
    }
    return records, summary


def validate_failure_faces(payload: dict) -> Dict[str, object]:
    """Structural validation of a v2 document (no external schema dependency).

    Checked: schema tag, ``error_modes`` non-empty and drawn from the vocabulary,
    every face's ``error_mode`` either ``None`` or a declared mode, and the
    ``cross_map`` block carrying the operator digest.
    """
    problems: List[str] = []
    if payload.get("schema") != FAILURE_FACES_SCHEMA_V2:
        problems.append(f"schema must be {FAILURE_FACES_SCHEMA_V2!r}")
    vocabulary = {m.value for m in CrossMapErrorMode}
    modes = payload.get("error_modes", [])
    if not modes:
        problems.append("error_modes must be a non-empty list")
    unknown = [m for m in modes if m not in vocabulary]
    if unknown:
        problems.append(f"unknown error modes: {unknown}")
    for idx, face in enumerate(payload.get("faces", [])):
        mode = face.get("error_mode")
        if mode is not None and mode not in vocabulary:
            problems.append(f"face {idx}: unknown error_mode {mode!r}")
        if mode is not None and mode not in modes:
            problems.append(f"face {idx}: error_mode {mode!r} not declared in error_modes")
    cross = payload.get("cross_map") or {}
    if not cross.get("matrix_digest"):
        problems.append("cross_map.matrix_digest is required")
    return {
        "ok": not problems,
        "problems": problems,
        "n_faces": len(payload.get("faces", [])),
        "n_modes": len(modes),
    }