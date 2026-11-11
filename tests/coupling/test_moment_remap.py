"""Moment-preserving remap: 0/1/2 order moments, boundedness, round trip.

Acceptance mapping (node IN387-PLAT01):
  C2 (first moment + centroid drift), C3 (second moment + chained-cycle drift),
  C4 (boundedness), C5 (round-trip consistency), C7 (negative control).
"""
import json

import numpy as np
import pytest

from coupling.mesh import structured_mesh_2d
from coupling.moment_remap import (
    MomentRemap,
    MomentRemapError,
    overlap_geometry_2d,
    perturbed_mesh_2d,
    polygon_centroid,
    polygon_monomial_moments,
    rectilinear_mesh_2d,
)


# ---------------------------------------------------------------------------
# Fixtures / fields
# ---------------------------------------------------------------------------

def _source_mesh():
    """8x8 cells on [0,1]^2 with stretched (non-uniform) spacing."""
    return rectilinear_mesh_2d(np.linspace(0.0, 1.0, 9) ** 1.15, np.linspace(0.0, 1.0, 9))


def _target_mesh():
    """8x8 perturbed (non-orthogonal, off-grid-aligned) cells on the same box."""
    return perturbed_mesh_2d(8, 8, jitter=0.5, seed=3)


def _linear_field(mesh):
    return 1.0 + 0.5 * mesh.cells[:, 0] - 0.25 * mesh.cells[:, 1]


def _smooth_field(mesh, sigma=0.18, floor=0.1):
    d2 = np.sum((mesh.cells - np.array([0.42, 0.58])) ** 2, axis=1)
    return floor + np.exp(-d2 / (2.0 * sigma**2))


def _front_field(mesh):
    return np.where(mesh.cells[:, 0] < 0.5, 1.0, 0.0)


def _invariants(mesh, field):
    m0 = float(np.dot(mesh.volumes, field))
    cm = np.dot(mesh.volumes * field, mesh.cells) / m0
    d = mesh.cells - cm
    inertia = np.einsum("i,ij,ik->jk", mesh.volumes * field, d, d)
    return m0, cm, inertia


# ---------------------------------------------------------------------------
# Geometry primitives
# ---------------------------------------------------------------------------

def test_polygon_moments_are_exact_for_a_unit_square():
    square = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    m = polygon_monomial_moments(square, 3)
    assert m[(0, 0)] == pytest.approx(1.0, abs=1e-15)
    assert m[(1, 0)] == pytest.approx(0.5, abs=1e-15)
    assert m[(2, 0)] == pytest.approx(1.0 / 3.0, abs=1e-15)
    assert m[(1, 1)] == pytest.approx(0.25, abs=1e-15)
    assert m[(3, 0)] == pytest.approx(0.25, abs=1e-15)
    assert m[(2, 1)] == pytest.approx(1.0 / 6.0, abs=1e-15)


def test_overlap_geometry_partitions_both_meshes():
    src, tgt = _source_mesh(), _target_mesh()
    geom = overlap_geometry_2d(src, tgt)
    assert geom.coverage_error < 1e-12
    assert np.allclose(geom.C.sum(axis=0), src.volumes, rtol=1e-13, atol=1e-16)
    assert np.allclose(geom.C.sum(axis=1), tgt.volumes, rtol=1e-13, atol=1e-16)
    # first moments of the overlaps must sum to zero about the source centroids
    assert np.allclose(geom.M1.sum(axis=0), 0.0, atol=1e-15)


def test_polygon_centroid_matches_a_rectangle():
    quad = np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0]])
    assert np.allclose(polygon_centroid(quad), [1.0, 0.5], atol=1e-15)


# ---------------------------------------------------------------------------
# C1/C2 -- 0th and 1st moment
# ---------------------------------------------------------------------------

def test_mass_is_conserved_to_machine_precision():
    src, tgt = _source_mesh(), _target_mesh()
    f = _smooth_field(src)
    for enforce in (True, False):
        res = MomentRemap(src, tgt, enforce_moments=enforce).apply(f)
        assert res.mass_rel_error < 1e-12
        assert res.m0_rel_error < 1e-12
        assert np.isclose(res.mass_target, np.dot(src.volumes, f), rtol=1e-13)


def test_first_and_second_moments_are_exact():
    src, tgt = _source_mesh(), _target_mesh()
    for field_fn in (_linear_field, _smooth_field):
        res = MomentRemap(src, tgt).apply(field_fn(src))
        assert res.m1_rel_error <= 1e-6
        assert res.m2_rel_error <= 1e-6
        assert res.centroid_drift_cells <= 1e-3
        assert res.inertia_rel_error <= 1e-6
        assert res.constraints_feasible is True
        assert res.bound_violations == 0


def test_gradient_reconstruction_is_exact_on_off_grid_centroids():
    """With the correction disabled the operator is the exact cell-average map, so a
    linear field is reproduced at the perturbed target centroids to round-off."""
    src, tgt = _source_mesh(), _target_mesh()
    res = MomentRemap(src, tgt, enforce_moments=False).apply(_linear_field(src))
    exact = _linear_field(tgt)
    assert np.max(np.abs(res.field - exact)) < 1e-12
    assert res.limited_fraction == 0.0  # the limiter never damages a linear field


def test_moment_correction_shrinks_under_refinement():
    """The array-quadrature defect that the correction removes is O(h^2)."""
    corr = []
    for n in (4, 8, 16):
        src = rectilinear_mesh_2d(np.linspace(0.0, 1.0, n + 1), np.linspace(0.0, 1.0, n + 1))
        tgt = perturbed_mesh_2d(n, n, jitter=0.5, seed=n)
        corr.append(MomentRemap(src, tgt).apply(_smooth_field(src)).correction_rel)
    order = np.polyfit(np.log([1 / 4, 1 / 8, 1 / 16]), np.log(corr), 1)[0]
    assert order > 1.5
    assert corr[-1] < corr[0]


# ---------------------------------------------------------------------------
# C7 -- negative control: no moment constraint => the criteria must fail
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field_fn", [_linear_field, _smooth_field])
def test_without_moment_constraint_the_criteria_fail(field_fn):
    src, tgt = _source_mesh(), _target_mesh()
    on = MomentRemap(src, tgt, enforce_moments=True).apply(field_fn(src))
    off = MomentRemap(src, tgt, enforce_moments=False).apply(field_fn(src))
    assert on.m1_rel_error <= 1e-6 and on.m2_rel_error <= 1e-6
    assert off.m1_rel_error > 1e-6 or off.m2_rel_error > 1e-6
    assert off.m1_rel_error > 100.0 * max(on.m1_rel_error, 1e-15)


def test_donor_cell_baseline_has_a_large_first_moment_error():
    src, tgt = _source_mesh(), _target_mesh()
    donor = MomentRemap(
        src, tgt, reconstruction="donor", enforce_moments=False
    ).apply(_smooth_field(src))
    assert donor.m1_rel_error > 1e-3
    assert donor.mass_rel_error < 1e-12  # mass is conservative even for donor cell


# ---------------------------------------------------------------------------
# C4 -- boundedness
# ---------------------------------------------------------------------------

def test_boundedness_holds_for_a_smooth_field():
    src, tgt = _source_mesh(), _target_mesh()
    f = _smooth_field(src)
    res = MomentRemap(src, tgt).apply(f)
    assert res.bound_violations == 0
    assert res.field.min() >= f.min() - 1e-14
    assert res.field.max() <= f.max() + 1e-14


def test_boundedness_and_mass_survive_a_front_touching_the_bounds():
    """A front saturates the bounds: high-order moments degrade, mass never does."""
    src, tgt = _source_mesh(), _target_mesh()
    res = MomentRemap(src, tgt).apply(_front_field(src))
    assert res.bound_violations == 0
    assert res.field.min() >= -1e-14
    assert res.field.max() <= 1.0 + 1e-14
    assert res.mass_rel_error < 1e-12          # mass is never sacrificed
    assert res.constraints_feasible is False   # documented, not hidden
    assert res.mass_repaired is True


def test_unbounded_mode_recovers_the_high_order_moments():
    src, tgt = _source_mesh(), _target_mesh()
    res = MomentRemap(src, tgt, bounds=(None, None)).apply(_front_field(src))
    assert res.field.min() < 0.0          # unbounded: the repair overshoots
    assert res.m1_rel_error <= 1e-6
    assert res.m2_rel_error <= 1e-6


# ---------------------------------------------------------------------------
# C5 -- round trip and chained cycles
# ---------------------------------------------------------------------------

def test_roundtrip_on_the_same_mesh_is_the_identity():
    src = _source_mesh()
    f = _smooth_field(src)
    fwd = MomentRemap(src, src).apply(f)
    back = MomentRemap(src, src).apply(fwd.field)
    assert np.max(np.abs(back.field - f)) <= 1e-12
    assert np.array_equal(fwd.field, f) or np.max(np.abs(fwd.field - f)) <= 1e-14


def test_roundtrip_preserves_the_invariants_to_machine_precision():
    """A->B->A: the field itself is lossy (a projection cannot be inverted), but the
    conserved invariants (mass, centroid, inertia) come back exactly."""
    src, tgt = _source_mesh(), _target_mesh()
    fwd, bwd = MomentRemap(src, tgt), MomentRemap(tgt, src)
    for field_fn in (_linear_field, _smooth_field):
        f = field_fn(src)
        g = bwd.apply(fwd.apply(f).field).field
        m0a, ca, ia = _invariants(src, f)
        m0b, cb, ib = _invariants(src, g)
        h = np.mean(np.sqrt(src.volumes))
        assert abs(m0b / m0a - 1.0) <= 1e-12
        assert np.linalg.norm(cb - ca) / h <= 1e-12
        assert np.linalg.norm(ib - ia) / np.linalg.norm(ia) <= 1e-12


def test_hundred_chained_cycles_drift_stays_below_1e_4():
    src, tgt = _source_mesh(), _target_mesh()
    fwd, bwd = MomentRemap(src, tgt), MomentRemap(tgt, src)
    f0 = _smooth_field(src)
    m0a, ca, ia = _invariants(src, f0)
    cur = f0.copy()
    for _ in range(100):
        cur = bwd.apply(fwd.apply(cur).field).field
    m0b, cb, ib = _invariants(src, cur)
    h = np.mean(np.sqrt(src.volumes))
    assert abs(m0b / m0a - 1.0) <= 1e-4
    assert np.linalg.norm(cb - ca) / h <= 1e-4
    assert np.linalg.norm(ib - ia) / np.linalg.norm(ia) <= 1e-4


def test_chained_field_distortion_saturates_instead_of_diverging():
    """Repeated exchange accumulates O(h^2) distortion up to a bounded cycle fixed
    point (reported, not hidden): 100 and 200 cycles differ by <1%."""
    src, tgt = _source_mesh(), _target_mesh()
    fwd, bwd = MomentRemap(src, tgt), MomentRemap(tgt, src)
    f0 = _smooth_field(src)
    amp = np.max(np.abs(f0))

    def drift(n):
        cur = f0.copy()
        for _ in range(n):
            cur = bwd.apply(fwd.apply(cur).field).field
        return float(np.max(np.abs(cur - f0)) / amp)

    d100, d200 = drift(100), drift(200)
    assert d100 <= 0.5
    assert abs(d200 - d100) <= 0.01 * max(d100, 1e-12)


# ---------------------------------------------------------------------------
# fail-closed paths and audits
# ---------------------------------------------------------------------------

def test_meshes_covering_different_domains_are_rejected():
    src = _source_mesh()
    tgt = structured_mesh_2d(8, 8, x0=0.0, x1=1.5)
    with pytest.raises(MomentRemapError):
        MomentRemap(src, tgt)


def test_3d_is_rejected_instead_of_degraded():
    from coupling.mesh import structured_mesh_3d

    with pytest.raises(MomentRemapError):
        MomentRemap(structured_mesh_3d(2, 2, 2), structured_mesh_3d(3, 3, 3))


def test_wrong_field_shape_is_rejected():
    src, tgt = _source_mesh(), _target_mesh()
    with pytest.raises(MomentRemapError):
        MomentRemap(src, tgt).apply(np.zeros(src.n_cells + 1))


def test_audit_record_is_json_friendly():
    src, tgt = _source_mesh(), _target_mesh()
    d = MomentRemap(src, tgt).apply(_smooth_field(src)).to_dict()
    for key in ("mass_rel_error", "m1_rel_error", "m2_rel_error",
                "centroid_drift_cells", "inertia_rel_error", "bound_violations",
                "moments_enforced", "constraints_feasible", "coverage_error",
                "continuous_m1_rel_error", "moment_reference"):
        assert key in d
    json.dumps(d)  # must be serialisable for the evidence ledger


def test_both_moment_conventions_are_exact_and_reported():
    src, tgt = _source_mesh(), _target_mesh()
    f = _smooth_field(src)
    arr = MomentRemap(src, tgt, moment_reference="array").apply(f)
    con = MomentRemap(src, tgt, moment_reference="continuous").apply(f)
    assert arr.m1_rel_error <= 1e-6 and arr.m2_rel_error <= 1e-6
    assert con.continuous_m1_rel_error <= 1e-6 and con.continuous_m2_rel_error <= 1e-6
    # each convention leaves the *other* metric at the O(h^2) quadrature level
    assert arr.continuous_m1_rel_error > 1e-6
    assert con.array_m1_rel_error > 1e-6
    assert arr.array_m1_rel_error <= 1e-6