"""L2 / RBF remap operator tests (conservation + accuracy + fail-closed)."""
import numpy as np
import pytest

from coupling.mesh import Mesh, MeshError, structured_mesh_2d, structured_mesh_3d
from coupling.remap import L2Remap, RBFRemap, RemapError


def _gaussian_2d(cells, sigma=0.12, center=(0.5, 0.5)):
    d2 = np.sum((cells - np.array(center)) ** 2, axis=1)
    return np.exp(-d2 / (2 * sigma**2))


# ---------------------------------------------------------------------------
# L2 remap, 2D exact path
# ---------------------------------------------------------------------------

def test_l2_2d_linear_field_is_exact():
    src = structured_mesh_2d(2, 2)
    tgt = structured_mesh_2d(8, 8)
    f = src.cells[:, 0] + 2.0 * src.cells[:, 1]
    res = L2Remap(src, tgt).apply(f)
    assert res.conservation_error < 1e-12
    # piecewise-constant source: each target cell takes the average of the
    # source cell averages over the overlap -> for a linear field that is
    # the source cell value for interior target cells
    for j in range(tgt.n_cells):
        i = int(np.argmin(np.linalg.norm(src.cells - tgt.cells[j], axis=1)))
        assert abs(res.field[j] - f[i]) < 1e-12


def test_l2_2d_gaussian_conservation_and_convergence():
    # refine source AND target together: error vs the true Gaussian must
    # shrink (first-order L2); conservation must stay at machine precision
    errs = []
    for n in (2, 4, 8):
        src = structured_mesh_2d(n, n)
        tgt = structured_mesh_2d(2 * n, 2 * n)
        f = _gaussian_2d(src.cells)
        res = L2Remap(src, tgt).apply(f)
        assert res.conservation_error < 1e-12, f"conservation broken at n={n}"
        errs.append(np.sqrt(np.mean((res.field - _gaussian_2d(tgt.cells)) ** 2)))
    assert errs[-1] < 0.5 * errs[0], errs


def test_l2_2d_identity_mesh():
    m = structured_mesh_2d(5, 5)
    f = _gaussian_2d(m.cells)
    res = L2Remap(m, m).apply(f)
    assert np.max(np.abs(res.field - f)) < 1e-12
    assert res.conservation_error < 1e-14


def test_l2_3d_conservation_and_accuracy():
    src = structured_mesh_3d(4, 4, 4)
    tgt = structured_mesh_3d(8, 8, 8)
    f = np.exp(-np.sum((src.cells - 0.5) ** 2, axis=1) / (2 * 0.2**2))
    res = L2Remap(src, tgt, n_grid=64, domain=((0, 0, 0), (1, 1, 1))).apply(f)
    assert res.conservation_error < 1e-9
    exact = np.exp(-np.sum((tgt.cells - 0.5) ** 2, axis=1) / (2 * 0.2**2))
    assert np.sqrt(np.mean((res.field - exact) ** 2)) < 1e-1


def test_l2_3d_linear_field():
    src = structured_mesh_3d(2, 2, 2)
    tgt = structured_mesh_3d(4, 4, 4)
    f = src.cells[:, 0] - src.cells[:, 1] + 3.0 * src.cells[:, 2]
    res = L2Remap(src, tgt, n_grid=32, domain=((0, 0, 0), (1, 1, 1))).apply(f)
    assert res.conservation_error < 1e-9
    # interior target cells take the containing source cell's value
    for j in range(tgt.n_cells):
        i = int(np.argmin(np.linalg.norm(src.cells - tgt.cells[j], axis=1)))
        assert abs(res.field[j] - f[i]) < 1e-9


# ---------------------------------------------------------------------------
# RBF remap
# ---------------------------------------------------------------------------

def test_rbf_2d_smooth_accuracy_beats_l2():
    src = structured_mesh_2d(8, 8)
    tgt = structured_mesh_2d(16, 16)
    f = _gaussian_2d(src.cells, sigma=0.2)
    exact = _gaussian_2d(tgt.cells, sigma=0.2)
    l2 = L2Remap(src, tgt).apply(f)
    rbf = RBFRemap(src, tgt, correct_mass=True).apply(f)
    e_l2 = np.sqrt(np.mean((l2.field - exact) ** 2))
    e_rbf = np.sqrt(np.mean((rbf.field - exact) ** 2))
    assert e_rbf < e_l2
    assert rbf.conservation_error < 1e-10  # mass-corrected: machine precision


def test_rbf_uncorrected_not_required_to_conserve():
    src = structured_mesh_2d(6, 6)
    tgt = structured_mesh_2d(12, 12)
    f = _gaussian_2d(src.cells, sigma=0.15)
    res = RBFRemap(src, tgt, correct_mass=False).apply(f)
    # no conservation guarantee without correction -- just check finiteness
    assert np.all(np.isfinite(res.field))


# ---------------------------------------------------------------------------
# Fail-closed behavior
# ---------------------------------------------------------------------------

def test_nonconvex_2d_cell_rejected():
    # L-shaped (non-convex) loop
    poly = np.array([[0, 0], [1, 0], [1, 0.5], [0.5, 0.5], [0.5, 1], [0, 1]], float)
    cells = np.array([[0.5, 0.5]])
    with pytest.raises(MeshError):
        Mesh(dim=2, cells=cells, volumes=np.array([0.75]), polygons=[poly])


def test_clockwise_2d_cell_rejected():
    poly = np.array([[0, 0], [0, 1], [1, 1], [1, 0]], float)  # clockwise
    with pytest.raises(MeshError):
        Mesh(dim=2, cells=np.array([[0.5, 0.5]]), volumes=np.array([1.0]), polygons=[poly])


def test_dim_mismatch_rejected():
    with pytest.raises(RemapError):
        L2Remap(structured_mesh_2d(2, 2), structured_mesh_3d(2, 2, 2))


def test_field_shape_mismatch_rejected():
    m = structured_mesh_2d(3, 3)
    op = L2Remap(m, m)
    with pytest.raises(RemapError):
        op.apply(np.ones(5))


def test_nonfinite_field_rejected():
    m = structured_mesh_2d(3, 3)
    f = _gaussian_2d(m.cells)
    f[0] = np.nan
    with pytest.raises(RemapError):
        L2Remap(m, m).apply(f)


def test_disjoint_meshes_rejected():
    # target mesh floating outside the source domain -> leak -> fail-closed
    src = structured_mesh_2d(2, 2, 0, 1, 0, 1)
    tgt = structured_mesh_2d(2, 2, 5, 6, 5, 6)
    with pytest.raises(RemapError):
        L2Remap(src, tgt)
