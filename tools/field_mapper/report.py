"""Report/manifest plumbing for field mapping (domain-neutral, no thresholds).

Two helpers:

* :func:`conservation_errors` —— turn a set of (name, source total, destination
  total) triples into relative errors.  Used by benchmarks so that the same
  arithmetic produces the mass, momentum and energy numbers instead of three
  hand-written formulas.
* :func:`map_manifest_block` —— build the ``cross_map`` / ``coupling_clock``
  shaped block that a ``tensorphysics-run-manifest-v1`` document can carry
  (the contract only *requires* the run-level fields; the coupling block is an
  optional, additionally-validated object).
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Sequence, Tuple

import numpy as np

from .conservative import MapAudit

__all__ = ["conservation_errors", "map_manifest_block"]


def conservation_errors(
    triples: Iterable[Tuple[str, float, float]],
) -> Dict[str, float]:
    """``{name: |dst - src| / max(|src|, tiny)}`` from ``(name, src, dst)`` triples."""
    out: Dict[str, float] = {}
    for name, src, dst in triples:
        denom = max(abs(float(src)), np.finfo(float).tiny)
        out[f"{name}_rel_error"] = abs(float(dst) - float(src)) / denom
        out[f"{name}_src_total"] = float(src)
        out[f"{name}_dst_total"] = float(dst)
    return out


def map_manifest_block(
    audit: MapAudit,
    *,
    src_mesh: str,
    dst_mesh: str,
    artifacts: Sequence[str] = (),
    tolerances: Mapping[str, float] | None = None,
) -> Dict[str, object]:
    """Assemble the map-facing part of a run manifest."""
    return {
        "operator": audit.operator,
        "law": audit.law,
        "correction": audit.correction,
        "src_mesh": src_mesh,
        "dst_mesh": dst_mesh,
        "matrix_digest": audit.matrix_digest,
        "total_rel_error": audit.total_rel_error,
        "l2_rel_error": audit.l2_rel_error,
        "covered_fraction_min": audit.covered_fraction_min,
        "tolerances": dict(tolerances or {}),
        "artifacts": list(artifacts),
    }