"""Behavioural contract for an external geometry module (loaded by path).

The module under test is an input to the mutation scanner: its revision is
pinned by sha256 in the mutation ledger.  The test suite is deliberately
domain-blind -- it only talks about polygons, areas, distances and validation
errors -- so the platform layer carries no domain semantics.

Inputs are synthetic (unit square, regular polygons), never production cases.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import math
import os
import pathlib

import numpy as np
import pytest

_MODULE_ENV = os.environ.get("GM_TARGET_MODULE")
if not _MODULE_ENV:  # contract suites only run inside a mutation sandbox
    pytest.skip("GM_TARGET_MODULE not set", allow_module_level=True)
MODULE_PATH = pathlib.Path(_MODULE_ENV).resolve()
_SPEC = importlib.util.spec_from_file_location("gm_target_under_test", MODULE_PATH)
MOD = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
import sys as _sys
_sys.modules["gm_target_under_test"] = MOD  # dataclasses need the module registered
_SPEC.loader.exec_module(MOD)


def square(side: float = 2.0):
    return MOD.PanelMesh(np.array([[0.0, 0.0], [side, 0.0], [side, side], [0.0, side]]))


# --------------------------------------------------------------------------- #
# construction and validation
# --------------------------------------------------------------------------- #
def test_unit_square_invariants():
    mesh = square(2.0)
    assert mesh.panel_count == 4
    assert np.allclose(mesh.lengths, [2.0, 2.0, 2.0, 2.0], rtol=1e-12)
    assert mesh.perimeter == pytest.approx(8.0, rel=1e-12)
    assert mesh.area == pytest.approx(4.0, rel=1e-12)
    assert np.allclose(mesh.centers, [[1.0, 0.0], [2.0, 1.0], [1.0, 2.0], [0.0, 1.0]], rtol=1e-12)
    assert np.allclose(mesh.ends, np.roll(mesh.vertices, -1, axis=0), rtol=0, atol=0)


def test_normals_point_outward():
    mesh = square(2.0)
    outward = mesh.centers - np.array([1.0, 1.0])
    dots = np.sum(mesh.normals * outward, axis=1)
    assert np.all(dots > 0.0)
    assert np.allclose(np.linalg.norm(mesh.normals, axis=1), 1.0, rtol=1e-12)


@pytest.mark.parametrize("bad", [0, 1, 2, -3, 3.5, True, "4", float("nan"), float("inf")])
def test_panel_count_validation_rejects(bad):
    with pytest.raises(ValueError):
        MOD.regular_polygon(bad)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf"), True])
def test_radius_validation_rejects(bad):
    with pytest.raises(ValueError):
        MOD.regular_polygon(8, radius=bad)


def test_panel_count_two_is_rejected_even_though_polygon_has_three_vertices():
    with pytest.raises(ValueError):
        MOD.regular_polygon(2)


def test_clockwise_polygon_rejected():
    with pytest.raises(ValueError):
        MOD.PanelMesh(np.array([[0.0, 0.0], [0.0, 2.0], [2.0, 2.0], [2.0, 0.0]]))


def test_collinear_polygon_rejected():
    with pytest.raises(ValueError):
        MOD.PanelMesh(np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]))


def test_zero_length_panel_rejected():
    with pytest.raises(ValueError):
        MOD.PanelMesh(np.array([[0.0, 0.0], [0.0, 0.0], [1.0, 1.0]]))


def test_non_finite_vertices_rejected():
    with pytest.raises(ValueError):
        MOD.PanelMesh(np.array([[0.0, 0.0], [1.0, float("nan")], [1.0, 1.0]]))


def test_wrong_shape_rejected():
    with pytest.raises(ValueError):
        MOD.PanelMesh(np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]))


def test_mesh_is_immutable():
    mesh = square()
    with pytest.raises(dataclasses.FrozenInstanceError):
        mesh.vertices = np.zeros((3, 2))  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# generators
# --------------------------------------------------------------------------- #
def test_regular_polygon_area_and_perimeter():
    n, r = 6, 1.0
    mesh = MOD.regular_polygon(n, radius=r)
    expected_area = 0.5 * n * r * r * math.sin(2.0 * math.pi / n)
    expected_perimeter = n * 2.0 * r * math.sin(math.pi / n)
    assert mesh.area == pytest.approx(expected_area, rel=1e-12)
    assert mesh.perimeter == pytest.approx(expected_perimeter, rel=1e-12)


def test_regular_polygon_converges_to_circle():
    mesh = MOD.regular_polygon(64, radius=1.0)
    assert mesh.area == pytest.approx(math.pi, abs=2e-2)
    assert mesh.perimeter == pytest.approx(2.0 * math.pi, abs=2e-2)


def test_ellipse_mesh_semi_axes_and_area():
    n, a, b = 8, 2.0, 1.0
    mesh = MOD.ellipse_mesh(n, semi_major=a, semi_minor=b)
    assert mesh.vertices[:, 0].max() == pytest.approx(a, rel=1e-12)
    assert mesh.vertices[:, 1].min() == pytest.approx(-b, rel=1e-12)
    expected = 0.5 * n * a * b * math.sin(2.0 * math.pi / n)
    assert mesh.area == pytest.approx(expected, rel=1e-12)


# --------------------------------------------------------------------------- #
# point queries
# --------------------------------------------------------------------------- #
def test_contains_point_half_open_rule():
    mesh = square(2.0)
    points = np.array(
        [
            [1.0, 1.0],   # strictly inside
            [3.0, 1.0],   # right of the square
            [-1.0, 1.0],  # left of the square
            [1.0, 3.0],   # above the square
        ]
    )
    inside = mesh.contains_point(points)
    assert inside.tolist() == [True, False, False, False]
    # boundary points are classified by the half-open ray rule, so only the
    # clearance query is contractual for them
    boundary = np.array([[1.0, 0.0], [0.0, 1.0], [2.0, 1.0], [1.0, 2.0]])
    assert np.allclose(mesh.boundary_clearance(boundary), 0.0, atol=1e-12)


def test_contains_point_rejects_non_finite():
    mesh = square()
    with pytest.raises(ValueError):
        mesh.contains_point(np.array([[float("inf"), 0.0]]))


def test_boundary_clearance_values():
    mesh = square(2.0)
    points = np.array([[1.0, 1.0], [0.0, 1.0], [3.0, 3.0]])
    clearance = mesh.boundary_clearance(points)
    assert clearance[0] == pytest.approx(1.0, rel=1e-12)
    assert clearance[1] == pytest.approx(0.0, abs=1e-12)
    assert clearance[2] == pytest.approx(math.sqrt(2.0), rel=1e-12)
    assert np.all(clearance >= 0.0)

# --------------------------------------------------------------------------- #
# exact boundaries (added after the mutation ledger exposed boundary-blindness)
# --------------------------------------------------------------------------- #
def test_minimum_vertex_count_boundary():
    # exactly three vertices are valid; two or fewer are not
    mesh = MOD.PanelMesh(np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]))
    assert mesh.panel_count == 3
    assert mesh.area == pytest.approx(0.5, rel=1e-12)
    with pytest.raises(ValueError):
        MOD.PanelMesh(np.array([[0.0, 0.0], [1.0, 0.0]]))


def test_zero_length_panel_boundary():
    # a panel of length exactly 1e-14 is rejected (fail-closed equality)
    with pytest.raises(ValueError):
        MOD.PanelMesh(np.array([[0.0, 0.0], [1e-14, 0.0], [0.0, 1.0]]))
    # slightly longer is accepted
    mesh = MOD.PanelMesh(np.array([[0.0, 0.0], [1e-9, 0.0], [0.0, 1.0]]))
    assert mesh.panel_count == 3


def test_degenerate_area_boundary():
    # twice-the-area exactly 1e-14 is rejected
    with pytest.raises(ValueError):
        MOD.PanelMesh(np.array([[0.0, 0.0], [1e-7, 0.0], [0.0, 1e-7]]))
