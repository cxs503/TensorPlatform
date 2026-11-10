"""Geometry of the conservative map: bounding-box search, exact overlap, clipping."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(ROOT))

from tools.field_mapper import (  # noqa: E402
    CellSet,
    bbox_candidate_pairs,
    box_overlap_entries,
    clip_convex_polygon,
    coverage_fraction,
    polygon_area,
    polygon_centroid,
    polygon_overlap_entries,
)
from tools.coupling.mesh import BoxMesh  # noqa: E402


def _unit_square(n: int):
    edges = np.linspace(0.0, 1.0, n + 1)
    lo = np.array([[x, y] for x in edges[:-1] for y in edges[:-1]])
    hi = np.array([[x, y] for x in edges[1:] for y in edges[1:]])
    return lo, hi


def test_bbox_candidates_are_a_superset_of_real_overlaps():
    """The sweep must never drop a real intersection (safety property of stage 1)."""
    src_lo, src_hi = _unit_square(4)
    dst_lo = src_lo + np.array([0.03, 0.07])
    dst_hi = src_hi + np.array([0.03, 0.07])
    i_idx, j_idx = bbox_candidate_pairs(src_lo, src_hi, dst_lo, dst_hi)
    candidates = set(zip(i_idx.tolist(), j_idx.tolist()))
    for i in range(src_lo.shape[0]):
        for j in range(dst_lo.shape[0]):
            lo = np.maximum(src_lo[i], dst_lo[j])
            hi = np.minimum(src_hi[i], dst_hi[j])
            if np.all(hi > lo):
                assert (i, j) in candidates
    assert len(candidates) >= 9  # shifted mesh overlaps at least 3x3 cells


def test_box_overlap_is_exact_and_partitions_the_source():
    src_lo, src_hi = _unit_square(3)
    dst_lo, dst_hi = _unit_square(7)
    entries = box_overlap_entries(src_lo, src_hi, dst_lo, dst_hi)
    # every destination cell is fully covered by the source partition
    covered = np.zeros(dst_lo.shape[0])
    np.add.at(covered, entries.dst_index, entries.measure)
    np.testing.assert_allclose(covered, np.prod(dst_hi - dst_lo, axis=1), rtol=1e-14, atol=0)
    assert abs(entries.total_measure - 1.0) < 1e-14
    assert entries.family == "box"
    assert entries.n_exact_pairs <= entries.n_bbox_pairs


def test_box_overlap_1d_2d_3d_share_the_measure_semantics():
    for dim in (1, 2, 3):
        lo = np.zeros((1, dim))
        hi = np.ones((1, dim))
        half_lo = np.full((1, dim), 0.5)
        entries = box_overlap_entries(lo, hi, half_lo, hi)
        assert entries.measure.size == 1
        assert abs(entries.measure[0] - 0.5**dim) < 1e-15


def test_polygon_clipping_recovers_exact_intersection_area():
    square = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    triangle = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    inter = clip_convex_polygon(square, triangle)
    assert abs(polygon_area(inter) - 0.5) < 1e-14
    # disjoint polygons clip to nothing
    far = triangle + np.array([5.0, 5.0])
    assert clip_convex_polygon(square, far).size == 0


def test_polygon_centroid_and_area_are_orientation_independent():
    square = np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]])
    reversed_square = square[::-1]
    assert abs(polygon_area(square) - 4.0) < 1e-14
    assert abs(polygon_area(reversed_square) - 4.0) < 1e-14
    np.testing.assert_allclose(polygon_centroid(reversed_square), [1.0, 1.0], atol=1e-14)


def test_polygon_overlap_entries_conserve_a_triangulated_square():
    """Two triangles and four quads are two different partitions of the same square."""
    c = 0.5
    polys = [
        np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]),
        np.array([[0.0, 0.0], [1.0, 1.0], [0.0, 1.0]]),
    ]
    quads = [
        np.array([[0.0, 0.0], [c, 0.0], [c, c], [0.0, c]]),
        np.array([[c, 0.0], [1.0, 0.0], [1.0, c], [c, c]]),
        np.array([[0.0, c], [c, c], [c, 1.0], [0.0, 1.0]]),
        np.array([[c, c], [1.0, c], [1.0, 1.0], [c, 1.0]]),
    ]
    src = CellSet.from_polygons(polys, name="triangles")
    dst = CellSet.from_polygons(quads, name="quads")
    entries = polygon_overlap_entries(src.polygons, dst.polygons)
    assert abs(entries.total_measure - 1.0) < 1e-14
    assert entries.family == "convex_polygon"
    covered = coverage_fraction(entries, dst.measure)
    np.testing.assert_allclose(covered, 1.0, atol=1e-14)


def test_coverage_flags_uncovered_and_double_covered_cells():
    src_lo = np.array([[0.0, 0.0]])
    src_hi = np.array([[1.0, 1.0]])
    dst_lo = np.array([[0.0, 0.0], [5.0, 5.0], [0.0, 0.0]])
    dst_hi = np.array([[1.0, 1.0], [6.0, 6.0], [1.0, 1.0]])
    entries = box_overlap_entries(src_lo, src_hi, dst_lo, dst_hi)
    covered = coverage_fraction(entries, np.prod(dst_hi - dst_lo, axis=1))
    assert covered[0] == pytest.approx(1.0)
    assert covered[1] == 0.0
    assert covered[2] == pytest.approx(1.0)


def test_box_mesh_interop_is_the_same_operator():
    """A BoxMesh and the equivalent CellSet must produce identical measures."""
    mesh = BoxMesh.uniform([0.0, 0.0], [1.0, 1.0], [5, 5], name="m5")
    cells = CellSet.from_box_mesh(mesh)
    assert cells.n_cells == mesh.n_cells
    np.testing.assert_allclose(cells.measure, mesh.volume)
    np.testing.assert_allclose(cells.centers, mesh.centers)