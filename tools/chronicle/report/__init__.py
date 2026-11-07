"""Chronicle report line v2 (IN18-T3): ten-section journal framework, figure
pipeline and an anti-fabrication provenance gate.

Modules
-------
snapshot    immutable SQLite evidence snapshot + SHA-256 anchor (``report.sqlite``)
provenance  ``{{prov:<id>}}`` marker protocol and record store
skeleton    ten-section journal skeleton generator and structural validator
figures     saturation / coverage-heatmap / funnel figures (PNG + PDF), hashed
verify      gate: re-execute recorded SQL, recompute hashes, FAIL on mismatch
demo        end-to-end build of the first issue (snapshot -> figures -> paper)
"""

from .provenance import ProvenanceStore, render_number  # noqa: F401
from .snapshot import Snapshot, build_snapshot  # noqa: F401
from .verify import verify_report  # noqa: F401

__all__ = [
    "ProvenanceStore",
    "Snapshot",
    "build_snapshot",
    "render_number",
    "verify_report",
]