"""Artifact Lineage DAG — build, query, and verify artifact dependency graphs.

Scans work/m*/artifacts/runs/docs directories, computes sha256, builds a
NetworkX DAG, supports forward/backward tracing and tamper detection.

Design: standalone module, zero domain semantics, stdlib + networkx only.
"""
from .dag_builder import (
    ArtifactNode,
    LineageDAG,
    build_lineage_dag,
    verify_integrity,
    detect_tampering,
)

__all__ = [
    "ArtifactNode",
    "LineageDAG",
    "build_lineage_dag",
    "verify_integrity",
    "detect_tampering",
]
__version__ = "1.0.0"
