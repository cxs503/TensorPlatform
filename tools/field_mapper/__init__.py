"""Conservative field mapping for cross-solver coupling (domain-neutral).

Pipeline implemented here::

    geometry search  ->  exact overlap operator  ->  conservation correction  ->  audit

* :mod:`tools.field_mapper.geometry`     —— bounding-box sweep + exact overlap
  (analytic for axis-aligned boxes, Sutherland–Hodgman clipping for convex
  polygons) and the coverage audit.
* :mod:`tools.field_mapper.conservative` —— the operator, the declared
  conservation laws, the corrections, and the machine-checkable audit.
* :mod:`tools.field_mapper.errors`       —— cross-mapping failure modes and the
  ``failure_faces/v2`` artifact (v1-compatible reader).
* :mod:`tools.field_mapper.report`       —— relative-error arithmetic and the
  manifest block.

What the package guarantees
---------------------------
1. **Redistribution, not resampling** — with ``operator="overlap"`` and a fully
   covered destination, every conserved total (``Σ f·V`` for densities, ``Σ f``
   for extensive quantities) is preserved to round-off, positivity is preserved,
   and the operator is the identity on a coincident mesh.
2. **Nothing silent** — coverage, extrema, moment drift and the conservation
   residual are all reported, and a violated tolerance becomes a named failure
   record rather than a warning.
3. **No domain semantics, no baked thresholds** — cells are boxes or polygons,
   fields are arrays with a declared law; tolerances come from the caller.

The one thing the package deliberately does **not** claim is accuracy: a
conservative map can be first-order at best when the two discretisations do not
resolve the same scales.  Accuracy is audited separately against an analytic
reference (``MapAudit.l2_vs_reference``).
"""

from .conservative import (
    CellSet,
    ConservativeMap,
    Correction,
    MapAudit,
    MapError,
    MapLaw,
    conserved_total,
    l2_relative_error,
)
from .errors import (
    FAILURE_FACES_SCHEMA_V2,
    CrossMapErrorMode,
    CrossMapFault,
    FaultTolerances,
    detect_faults,
    load_failure_faces,
    validate_failure_faces,
    write_failure_faces,
)
from .geometry import (
    GeometryError,
    OverlapEntries,
    bbox_candidate_pairs,
    box_overlap_entries,
    clip_convex_polygon,
    coverage_fraction,
    overlap_entries,
    polygon_area,
    polygon_centroid,
    polygon_overlap_entries,
)
from .report import conservation_errors, map_manifest_block

__all__ = [
    "CellSet",
    "ConservativeMap",
    "Correction",
    "MapAudit",
    "MapError",
    "MapLaw",
    "conserved_total",
    "l2_relative_error",
    "CrossMapErrorMode",
    "CrossMapFault",
    "FaultTolerances",
    "FAILURE_FACES_SCHEMA_V2",
    "detect_faults",
    "write_failure_faces",
    "load_failure_faces",
    "validate_failure_faces",
    "GeometryError",
    "OverlapEntries",
    "bbox_candidate_pairs",
    "box_overlap_entries",
    "overlap_entries",
    "polygon_overlap_entries",
    "polygon_area",
    "polygon_centroid",
    "clip_convex_polygon",
    "coverage_fraction",
    "conservation_errors",
    "map_manifest_block",
]