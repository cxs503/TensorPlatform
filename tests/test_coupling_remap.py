"""Tests for the conservative remapping operators (tools/coupling/remap.py).

Acceptance mapping (node IN383-PLAT01):
  C1 sum conservation residual ≤ 1e-12
  C2 positivity / no overshoot
  C3 patch test error ≤ 1e-12
  C5 conservation correction switched off must drift ≥ 1 % (negative control)
  C7 idempotent + traceable (digest stable, re-run bit-identical short of bit rot)
Fixtures are synthetic box meshes; no domain vocabulary is involved.
"""

from __future__ import annotations

import numpy as np
import pytest

from tools.coupling import (
    BoxMesh,
    Field,
    QuantityKind,
    RemapDomainError,
    RemapError,
    RemapOperator,
    RemapSpec,
    bounded_mass_fix,
    canonical_digest,
    check,
    measure,
    overlap_matrix,
    remap,
)

DENSITY = QuantityKind.DENSITY


def coarse_mesh(n=6, dim=2) -> BoxMesh:
    return BoxMesh.uniform([0.0] * dim, [1.0] * dim, [n] * dim, name="coarse")


def shifted_mesh(n=6, dim=2) -> BoxMesh:
    """Same domain / same cell count, half-cell offset, non-uniform corner cells."""
    h = 1.0 / n
    edges = np.concatenate(([0.0], 0.5 * h + h * np.arange(n - 1), [1.0]))
    shape = tuple([n] * dim)
    lo = np.empty(shape + (dim,))
    hi = np.empty(shape + (dim,))
    for k in range(dim):
        view = [-1 if j == k else 1 for j in range(dim)]
        lo[..., k] = np.broadcast_to(edges[:-1].reshape(view), shape)
        hi[..., k] = np.broadcast_to(edges[1:].reshape(view), shape)
    return BoxMesh(lo=lo.reshape(-1, dim), hi=hi.reshape(-1, dim), name="shifted")


def blob(mesh: BoxMesh, centre=0.4, width=0.2, amplitude=1.0) -> Field:
    d2 = ((mesh.centers - centre) ** 2).sum(axis=1)
    return Field(amplitude * np.exp(-d2 / (2 * width * width)), mesh, DENSITY, name="blob")


def linear_field(mesh: BoxMesh, coeffs=(1.0, 0.5, -0.25)) -> Field:
    """Cell averages of a linear function (exact at cell centres)."""
    x = mesh.centers
    values = np.full(mesh.n_cells, coeffs[0])
    for k in range(mesh.dim):
        values = values + coeffs[k + 1] * x[:, k]
    return Field(values, mesh, DENSITY, name="linear")


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def test_overlap_matrix_is_partition_of_unity():
    for dst in (coarse_mesh(8).refined(2), shifted_mesh(7)):
        src = coarse_mesh(8)
        w = overlap_matrix(src, dst)
        np.testing.assert_allclose(w.sum(axis=0), dst.volume, rtol=1e-14, atol=1e-16)
        np.testing.assert_allclose(w.sum(axis=1), src.volume, rtol=1e-14, atol=1e-16)


def test_overlap_matrix_rejects_dimension_mismatch():
    with pytest.raises(RemapError):
        overlap_matrix(coarse_mesh(4, dim=2), coarse_mesh(4, dim=3))


def test_refined_mesh_is_strictly_nested():
    c = coarse_mesh(4)
    f = c.refined(2)
    assert f.n_cells == 4 * c.n_cells
    assert f.total_volume == pytest.approx(c.total_volume, rel=1e-15)
    hits = overlap_matrix(c, f)
    # every fine cell sits inside exactly one coarse cell
    assert np.all((hits > 0).sum(axis=0) == 1)


# ---------------------------------------------------------------------------
# C1: total conservation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("layout", ["nested", "shifted"])
def test_overlap_projection_conserves_total(layout):
    src = coarse_mesh(6)
    dst = src.refined(2) if layout == "nested" else shifted_mesh(9)
    field = blob(src)
    result = remap(field, src, dst, "density")
    assert result.report.raw_rel_residual <= 1e-14
    assert result.report.correction_factor == 1.0  # nothing to correct: intrinsically conservative
    assert abs(result.field.total - field.total) <= 1e-14 * abs(field.total)


def test_extensive_quantity_conserves_without_volume_weighting():
    src = coarse_mesh(5)
    dst = src.refined(3)
    values = np.arange(src.n_cells, dtype=float) + 1.0
    field = Field(values, src, QuantityKind.EXTENSIVE, name="ext")
    result = remap(field, src, dst, "extensive")
    assert result.field.total == pytest.approx(field.total, rel=1e-14)


# ---------------------------------------------------------------------------
# C3: patch tests
# ---------------------------------------------------------------------------

def test_patch_test_constant_field_is_reproduced_exactly():
    src = coarse_mesh(5)
    for dst in (src.refined(2), shifted_mesh(7)):
        const = Field(np.full(src.n_cells, 2.75), src, DENSITY, name="const")
        out = remap(const, src, dst, "density").field
        assert np.max(np.abs(out.values - 2.75)) / 2.75 <= 1e-12


def test_patch_test_nested_refinement_reproduces_source_values():
    src = coarse_mesh(5)
    dst = src.refined(2)
    field = linear_field(src)
    out = remap(field, src, dst, "density").field
    # each fine cell is fully inside one coarse cell => piecewise-constant value kept
    w = overlap_matrix(src, dst)
    owner = np.argmax(w, axis=0)
    err = np.max(np.abs(out.values - field.values[owner]))
    assert err <= 1e-12 * np.max(np.abs(field.values))


def test_patch_test_linear_cell_integral_is_exact():
    """Assembly check: per destination cell the projected integral equals the
    exact integral of the piecewise-constant source restricted to that cell."""
    src = coarse_mesh(5)
    dst = shifted_mesh(7)
    field = linear_field(src)
    out = remap(field, src, dst, "density").field
    w = overlap_matrix(src, dst)
    exact = w.T @ field.values
    got = out.values * dst.volume
    scale = np.maximum(np.abs(exact), 1e-300)
    assert np.max(np.abs(got - exact) / scale) <= 1e-12


def test_roundtrip_on_nested_meshes_is_identity():
    coarse = coarse_mesh(6)
    fine = coarse.refined(2)
    field = blob(coarse)
    fwd = remap(field, coarse, fine, "density")
    back = remap(fwd.field, fine, coarse, "density")
    err = np.max(np.abs(back.field.values - field.values)) / np.max(np.abs(field.values))
    assert err <= 1e-12


def test_first_moment_is_conserved_on_nested_meshes():
    src = coarse_mesh(6)
    dst = src.refined(2)
    field = blob(src, centre=0.65, width=0.15)
    out = remap(field, src, dst, "density").field
    gate = check(field, out, moment_tol=1e-12)
    assert gate.passed, gate.failures


# ---------------------------------------------------------------------------
# C2: positivity and no overshoot
# ---------------------------------------------------------------------------

def test_rbf_positivity_and_no_overshoot_are_enforced():
    src = coarse_mesh(6)
    dst = shifted_mesh(9)
    # steep two-level field: an unconstrained interpolant overshoots / undershoots
    values = np.where(src.centers[:, 0] < 0.5, 1.0, 0.0)
    field = Field(values, src, DENSITY, name="step")
    raw = remap(field, src, dst, "density",
                spec=RemapSpec(operator=RemapOperator.RBF_CONSERVATIVE, positivity=False))
    assert raw.field.values.min() < 0.0 or raw.field.values.max() > 1.0
    fixed = remap(field, src, dst, "density",
                  spec=RemapSpec(operator=RemapOperator.RBF_CONSERVATIVE,
                                 positivity=True, enforce_bounds=True))
    assert fixed.field.values.min() >= -1e-15
    assert fixed.field.values.max() <= 1.0 + 1e-12
    assert abs(fixed.field.total - field.total) <= 1e-12 * abs(field.total)


def test_bounded_mass_fix_respects_bounds_and_hits_target():
    rng = np.random.default_rng(0)
    values = rng.uniform(0.0, 1.0, 32)
    volume = rng.uniform(0.1, 1.0, 32)
    target = float((values * volume).sum()) * 1.05
    fixed, _, _ = bounded_mass_fix(values, volume, target, lo=0.0, hi=1.5)
    assert float((fixed * volume).sum()) == pytest.approx(target, rel=1e-14)
    assert fixed.min() >= 0.0 and fixed.max() <= 1.5


# ---------------------------------------------------------------------------
# C5: the correction must be doing real work (negative control)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dst_kind", ["coarse", "shifted"])
def test_conservation_correction_switched_off_drifts_measurably(dst_kind):
    src = coarse_mesh(6).refined(2)
    dst = coarse_mesh(6) if dst_kind == "coarse" else shifted_mesh(7)
    field = blob(src)
    off = remap(field, src, dst, "density",
                spec=RemapSpec(operator=RemapOperator.NEAREST_SCALED,
                               apply_conservation_correction=False))
    on = remap(field, src, dst, "density",
               spec=RemapSpec(operator=RemapOperator.NEAREST_SCALED,
                              apply_conservation_correction=True))
    assert off.report.raw_rel_residual >= 1e-2      # ≥ 1 % drift without the correction
    assert on.report.sum_dst == pytest.approx(field.total, rel=1e-14)


def test_overlap_operator_does_not_need_the_correction():
    """Positive control: the intrinsically conservative operator is unaffected."""
    src = coarse_mesh(6).refined(2)
    dst = shifted_mesh(7)
    field = blob(src)
    off = remap(field, src, dst, "density",
                spec=RemapSpec(operator=RemapOperator.OVERLAP_L2,
                               apply_conservation_correction=False))
    assert off.report.raw_rel_residual <= 1e-14


def test_rbf_drift_without_correction_is_at_least_a_tenth_of_a_percent():
    src = coarse_mesh(6)
    dst = src.refined(2)
    field = blob(src)
    off = remap(field, src, dst, "density",
                spec=RemapSpec(operator=RemapOperator.RBF_CONSERVATIVE,
                               apply_conservation_correction=False))
    assert off.report.raw_rel_residual >= 1e-3
    on = remap(field, src, dst, "density",
               spec=RemapSpec(operator=RemapOperator.RBF_CONSERVATIVE,
                              apply_conservation_correction=True))
    assert on.report.rel_residual <= 1e-14


# ---------------------------------------------------------------------------
# Fail-closed behaviour
# ---------------------------------------------------------------------------

def test_partially_covered_destination_fails_closed():
    src = coarse_mesh(4)
    dst = BoxMesh.uniform([0.5, 0.0], [1.5, 1.0], [6, 4], name="overhang")
    field = blob(src)
    with pytest.raises(RemapDomainError):
        remap(field, src, dst, "density")


def test_fill_policy_makes_partial_coverage_explicit():
    src = coarse_mesh(4)
    dst = BoxMesh.uniform([0.5, 0.0], [1.5, 1.0], [6, 4], name="straddling")
    field = blob(src)
    out = remap(field, src, dst, "density",
                spec=RemapSpec(outside_policy="fill", apply_conservation_correction=False))
    assert out.report.covered_fraction < 1.0
    assert out.report.uncovered_dst_cells > 0
    assert out.report.sum_dst < field.total  # half the source domain is not represented


def test_quantity_kind_mismatch_fails_closed():
    src = coarse_mesh(4)
    field = blob(src)
    with pytest.raises(RemapError):
        remap(field, src, src.refined(2), "extensive")


def test_unknown_operator_and_kernel_are_rejected():
    with pytest.raises(RemapError):
        RemapSpec(operator="magic_projection")
    with pytest.raises(RemapError):
        RemapSpec(rbf_kernel="bessel")
    with pytest.raises(RemapError):
        RemapSpec(outside_policy="silently_zero")


# ---------------------------------------------------------------------------
# C7: idempotence and traceability
# ---------------------------------------------------------------------------

def test_remap_is_idempotent_and_digest_traceable():
    src = coarse_mesh(5)
    dst = src.refined(2)
    field = blob(src)
    spec = RemapSpec(operator=RemapOperator.RBF_CONSERVATIVE, positivity=True)
    first = remap(field, src, dst, "density", spec=spec)
    second = remap(field, src, dst, "density", spec=spec)
    assert np.array_equal(first.field.values, second.field.values)
    assert first.report.remap_digest == second.report.remap_digest
    assert canonical_digest(["a", 1.5]) == canonical_digest(["a", 1.5])


def test_report_records_every_input_digest():
    src = coarse_mesh(4)
    dst = src.refined(2)
    field = blob(src)
    report = remap(field, src, dst, "density").report
    assert len(report.spec_digest) == 64
    assert len(report.src_digest) == 64
    assert len(report.dst_digest) == 64
    assert report.src_digest == field.digest()
    assert report.to_dict()["raw_rel_residual"] <= 1e-14
    assert report.n_src_cells == src.n_cells and report.n_dst_cells == dst.n_cells


def test_interface_accepts_positional_form_and_unpacks():
    src = coarse_mesh(4)
    dst = src.refined(2)
    field = blob(src)
    out_field, report = remap(field, src, dst, "density")
    assert out_field.mesh is dst and report.operator == "overlap_l2"


def test_measure_reports_sum_and_moment_residuals():
    src = coarse_mesh(4)
    dst = src.refined(2)
    field = blob(src)
    out = remap(field, src, dst, "density").field
    metrics = measure(field, out)
    assert metrics.sum_rel_residual <= 1e-14
    assert metrics.overshoot_ratio <= 1.0 + 1e-12
    assert metrics.nonneg_violation == 0.0
    assert metrics.to_dict()["sum_src"] == pytest.approx(field.total, rel=1e-15)