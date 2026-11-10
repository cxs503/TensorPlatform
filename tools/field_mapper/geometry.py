"""Geometric search for cell-to-cell overlap measures (domain-neutral).

A conservative map between two meshes needs, for every pair of cells, the exact
measure of their intersection::

    S[i, j] = |Omega_i^src  ∩  Omega_j^dst|      (volume in 3-D, area in 2-D)

Only then is the operator a *pure redistribution* of the conserved total.  Two
representation families are supported, and both go through the same search path:

``box``
    Axis-aligned boxes (structured or block-structured meshes).  Intersection of
    two boxes is a box, so the measure is a product of one-dimensional overlap
    lengths — analytic and exact to round-off.
``convex_polygon``
    Convex polygonal cells in 2-D (unstructured meshes, cut cells).  The
    intersection is obtained by Sutherland–Hodgman clipping of the source polygon
    against the half-planes of the target polygon, then measured with the
    shoelace formula.

The search is a two-stage filter, which is what makes large grid ratios
affordable:

1. **bounding-box sweep** — destination cells are sorted along one axis; a
   per-source ``searchsorted`` leaves only the candidates whose extent along that
   axis actually overlaps, then one vectorised test rejects the rest of the
   bounding-box candidates;
2. **exact geometry** — boxes use the analytic overlap; polygons use clipping.

Nothing here knows what a field means: ``lo``/``hi``/polygon vertices are the
whole vocabulary.  Domain semantics enter at the call site.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "GeometryError",
    "OverlapEntries",
    "bbox_candidate_pairs",
    "box_overlap_entries",
    "polygon_area",
    "polygon_centroid",
    "clip_convex_polygon",
    "polygon_overlap_entries",
    "overlap_entries",
    "coverage_fraction",
]


class GeometryError(ValueError):
    """Raised when a geometric description is unusable."""


# --------------------------------------------------------------------------- #
# Shared container
# --------------------------------------------------------------------------- #
@dataclass
class OverlapEntries:
    """Sparse intersection measures between a source and a target discretisation.

    Attributes
    ----------
    src_index, dst_index
        Cell indices of the overlapping pairs (same length as ``measure``).
    measure
        Intersection volume/area (strictly positive; zero-measure pairs are not
        stored).
    n_src, n_dst
        Cell counts of the two sides.
    n_bbox_pairs
        How many pairs survived the bounding-box filter (candidate work performed).
    n_exact_pairs
        How many pairs were measured by the exact operator (``measure > 0``).
    family
        ``"box"`` or ``"convex_polygon"`` — which measured the entries.
    """

    src_index: np.ndarray
    dst_index: np.ndarray
    measure: np.ndarray
    n_src: int
    n_dst: int
    n_bbox_pairs: int
    n_exact_pairs: int
    family: str

    @property
    def total_measure(self) -> float:
        return float(self.measure.sum(axis=0)) if self.measure.size else 0.0

    def as_coo(self, shape: Optional[Tuple[int, int]] = None):
        """Return ``(matrix, shape)`` as a dense-or-sparse neutral pair.

        A dense array is returned deliberately: coupling interfaces in this
        platform are small enough that the dense operator is the audit-friendly
        representation, and it keeps the package free of a hard SciPy dependency.
        Large interfaces can build a CSR matrix from the triplets directly.
        """
        shape = shape or (self.n_dst, self.n_src)
        matrix = np.zeros(shape, dtype=float)
        if self.measure.size:
            np.add.at(matrix, (self.dst_index, self.src_index), self.measure)
        return matrix, shape

    def summary(self) -> Dict[str, float]:
        return {
            "family": self.family,
            "n_src": self.n_src,
            "n_dst": self.n_dst,
            "n_bbox_pairs": self.n_bbox_pairs,
            "n_exact_pairs": self.n_exact_pairs,
            "total_measure": self.total_measure,
            "candidate_efficiency": (
                self.n_exact_pairs / self.n_bbox_pairs if self.n_bbox_pairs else 0.0
            ),
        }


# --------------------------------------------------------------------------- #
# Stage 1 — bounding-box sweep
# --------------------------------------------------------------------------- #
def bbox_candidate_pairs(
    src_lo: np.ndarray,
    src_hi: np.ndarray,
    dst_lo: np.ndarray,
    dst_hi: np.ndarray,
    axis: int = 0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Bounding-box candidate pairs ``(i, j)`` whose boxes may intersect.

    The result is a *superset* of the true intersections: every real overlap is
    returned, together with some near misses that the exact stage rejects.  Being
    a superset is the safety property — the filter can be made tighter or looser
    without ever changing the map itself.
    """
    src_lo = np.atleast_2d(np.asarray(src_lo, dtype=float))
    src_hi = np.atleast_2d(np.asarray(src_hi, dtype=float))
    dst_lo = np.atleast_2d(np.asarray(dst_lo, dtype=float))
    dst_hi = np.atleast_2d(np.asarray(dst_hi, dtype=float))
    if src_lo.shape != src_hi.shape or dst_lo.shape != dst_hi.shape:
        raise GeometryError("lo/hi shape mismatch")
    if src_lo.shape[1] != dst_lo.shape[1]:
        raise GeometryError("source and destination must share a dimension")
    dim = src_lo.shape[1]
    if not (0 <= axis < dim):
        raise GeometryError(f"sweep axis {axis} out of range for dim={dim}")

    order = np.argsort(dst_lo[:, axis], kind="stable")
    swept_lo = dst_lo[order, axis]
    swept_hi = dst_hi[order, axis]

    src_out: list[np.ndarray] = []
    dst_out: list[np.ndarray] = []
    for i in range(src_lo.shape[0]):
        # destination cells start before the source ends ...
        stop = int(np.searchsorted(swept_lo, src_hi[i, axis], side="right"))
        if stop == 0:
            continue
        cand = order[:stop]
        # ... and end after the source starts.
        cand = cand[swept_hi[:stop] >= src_lo[i, axis]]
        if cand.size == 0:
            continue
        # remaining axes: reject on the bounding box as a whole
        ok = np.ones(cand.size, dtype=bool)
        for k in range(dim):
            if k == axis:
                continue
            ok &= dst_hi[cand, k] >= src_lo[i, k]
            ok &= dst_lo[cand, k] <= src_hi[i, k]
        cand = cand[ok]
        if cand.size:
            src_out.append(np.full(cand.size, i, dtype=np.int64))
            dst_out.append(cand.astype(np.int64))
    if not src_out:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    return np.concatenate(src_out), np.concatenate(dst_out)


# --------------------------------------------------------------------------- #
# Stage 2a — exact box overlap
# --------------------------------------------------------------------------- #
def box_overlap_entries(
    src_lo: np.ndarray,
    src_hi: np.ndarray,
    dst_lo: np.ndarray,
    dst_hi: np.ndarray,
) -> OverlapEntries:
    """Exact ``|box_i ∩ box_j|`` for axis-aligned boxes.

    1-D, 2-D and 3-D are handled by the same code: the overlap in each dimension
    is ``max(0, min(hi) - max(lo))`` and the measure is their product (in 1-D the
    "volume" is a length, which is exactly the measure a 1-D conservation law
    needs).
    """
    src_lo = np.atleast_2d(np.asarray(src_lo, dtype=float))
    src_hi = np.atleast_2d(np.asarray(src_hi, dtype=float))
    dst_lo = np.atleast_2d(np.asarray(dst_lo, dtype=float))
    dst_hi = np.atleast_2d(np.asarray(dst_hi, dtype=float))
    i_idx, j_idx = bbox_candidate_pairs(src_lo, src_hi, dst_lo, dst_hi)
    n_bbox = int(i_idx.size)
    if n_bbox == 0:
        return OverlapEntries(
            np.empty(0, dtype=np.int64),
            np.empty(0, dtype=np.int64),
            np.empty(0),
            src_lo.shape[0],
            dst_lo.shape[0],
            0,
            0,
            "box",
        )
    lo = np.maximum(src_lo[i_idx], dst_lo[j_idx])
    hi = np.minimum(src_hi[i_idx], dst_hi[j_idx])
    overlap = np.prod(np.clip(hi - lo, 0.0, None), axis=1)
    keep = overlap > 0.0
    return OverlapEntries(
        i_idx[keep],
        j_idx[keep],
        overlap[keep],
        src_lo.shape[0],
        dst_lo.shape[0],
        n_bbox,
        int(keep.sum()),
        "box",
    )


# --------------------------------------------------------------------------- #
# Stage 2b — convex polygon clipping
# --------------------------------------------------------------------------- #
def _as_polygon(poly, name: str = "polygon") -> np.ndarray:
    arr = np.asarray(poly, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise GeometryError(f"{name} must be an (n, 2) vertex loop, got {arr.shape}")
    if arr.shape[0] < 3:
        raise GeometryError(f"{name} needs at least three vertices")
    return arr


def _signed_area(poly: np.ndarray) -> float:
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def polygon_area(poly) -> float:
    """Area of a simple polygon (shoelace), independent of vertex orientation."""
    return abs(_signed_area(_as_polygon(poly)))


def polygon_centroid(poly) -> np.ndarray:
    """Area centroid of a simple polygon."""
    arr = _as_polygon(poly)
    x, y = arr[:, 0], arr[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cross = x * yn - xn * y
    area2 = float(cross.sum())
    if abs(area2) < 1e-300:
        raise GeometryError("degenerate polygon (zero area)")
    cx = float(((x + xn) * cross).sum()) / (3.0 * area2)
    cy = float(((y + yn) * cross).sum()) / (3.0 * area2)
    return np.array([cx, cy])


def _orientation(poly: np.ndarray) -> float:
    return _signed_area(poly)


def _cross2(a: np.ndarray, b: np.ndarray) -> float:
    """Scalar 2-D cross product ``a × b`` (``np.cross`` deprecated 2-D input)."""
    return float(a[0] * b[1] - a[1] * b[0])


def clip_convex_polygon(subject, clip) -> np.ndarray:
    """Sutherland–Hodgman clipping of a polygon by a **convex** polygon.

    Returns the intersection vertex loop (counter-clockwise, or an empty
    ``(0, 2)`` array when the intersection has zero area).  The clip polygon may
    be given with either orientation; it is normalised to counter-clockwise.
    """
    subj = _as_polygon(subject, "subject")
    clipper = _as_polygon(clip, "clip")
    if _orientation(clipper) < 0:
        clipper = clipper[::-1]

    output: list[np.ndarray] = [p for p in subj]
    n_clip = clipper.shape[0]
    for k in range(n_clip):
        if not output:
            break
        a = clipper[k]
        b = clipper[(k + 1) % n_clip]
        edge = b - a
        inside: list[np.ndarray] = []
        prev = output[-1]
        prev_in = _cross2(edge, prev - a) >= 0.0
        for cur in output:
            cur_in = _cross2(edge, cur - a) >= 0.0
            if cur_in:
                if not prev_in:
                    inside.append(_segment_intersection(prev, cur, a, edge))
                inside.append(cur)
            elif prev_in:
                inside.append(_segment_intersection(prev, cur, a, edge))
            prev, prev_in = cur, cur_in
        output = inside
    if len(output) < 3:
        return np.empty((0, 2), dtype=float)
    poly = np.asarray(output, dtype=float)
    if polygon_area(poly) <= 0.0:
        return np.empty((0, 2), dtype=float)
    return poly


def _segment_intersection(p0: np.ndarray, p1: np.ndarray, a: np.ndarray, edge: np.ndarray) -> np.ndarray:
    """Intersection of segment ``p0→p1`` with the infinite line ``a + t·edge``."""
    d = p1 - p0
    denom = _cross2(d, edge)
    if abs(denom) < 1e-300:  # parallel: neither point crossed, return the segment end
        return p1.copy()
    t = _cross2(a - p0, edge) / denom
    t = min(1.0, max(0.0, t))
    return p0 + t * d


def polygon_overlap_entries(src_polys: Sequence[np.ndarray], dst_polys: Sequence[np.ndarray]) -> OverlapEntries:
    """Exact overlap areas between two families of convex polygons."""
    src = [_as_polygon(p, "source polygon") for p in src_polys]
    dst = [_as_polygon(p, "destination polygon") for p in dst_polys]
    src_lo = np.array([p.min(axis=0) for p in src])
    src_hi = np.array([p.max(axis=0) for p in src])
    dst_lo = np.array([p.min(axis=0) for p in dst])
    dst_hi = np.array([p.max(axis=0) for p in dst])
    i_idx, j_idx = bbox_candidate_pairs(src_lo, src_hi, dst_lo, dst_hi)
    n_bbox = int(i_idx.size)
    si: list[int] = []
    dj: list[int] = []
    vals: list[float] = []
    for i, j in zip(i_idx, j_idx):
        inter = clip_convex_polygon(src[i], dst[j])
        if inter.size == 0:
            continue
        area = polygon_area(inter)
        if area <= 0.0:
            continue
        si.append(int(i))
        dj.append(int(j))
        vals.append(area)
    return OverlapEntries(
        np.asarray(si, dtype=np.int64),
        np.asarray(dj, dtype=np.int64),
        np.asarray(vals, dtype=float),
        len(src),
        len(dst),
        n_bbox,
        len(vals),
        "convex_polygon",
    )


# --------------------------------------------------------------------------- #
# Dispatcher + coverage audit
# --------------------------------------------------------------------------- #
def overlap_entries(
    src_cells: Sequence,
    dst_cells: Sequence,
    family: str = "box",
) -> OverlapEntries:
    """Overlap measures between two cell families.

    ``family="box"``        cells are ``(lo, hi)`` corner arrays
    ``family="polygon"``    cells are ``(n, 2)`` convex vertex loops
    """
    if family in ("box", "boxes", "axis-aligned"):
        src_lo, src_hi = _split_boxes(src_cells)
        dst_lo, dst_hi = _split_boxes(dst_cells)
        return box_overlap_entries(src_lo, src_hi, dst_lo, dst_hi)
    if family in ("polygon", "polygons", "convex_polygon"):
        return polygon_overlap_entries(src_cells, dst_cells)
    raise GeometryError(f"unknown cell family: {family!r}")


def _split_boxes(cells) -> Tuple[np.ndarray, np.ndarray]:
    if isinstance(cells, tuple) and len(cells) == 2:
        return np.atleast_2d(np.asarray(cells[0], float)), np.atleast_2d(np.asarray(cells[1], float))
    raise GeometryError("box cells must be given as a (lo, hi) pair")


def coverage_fraction(entries: OverlapEntries, dst_measure: np.ndarray) -> np.ndarray:
    """Per-destination-cell covered fraction ``Σ_i S_ij / |Omega_j|``.

    Values below 1 flag destination cells that receive only part of a
    conservative total — the ``uncovered_destination`` error mode.  Values above
    1 flag overlapping source cells (a source mesh that is not a partition).
    """
    dst_measure = np.asarray(dst_measure, dtype=float)
    covered = np.zeros(dst_measure.shape, dtype=float)
    if entries.measure.size:
        np.add.at(covered, entries.dst_index, entries.measure)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(dst_measure > 0.0, covered / dst_measure, 0.0)