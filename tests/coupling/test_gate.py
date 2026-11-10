"""Twin consistency gate tests (1% acceptance bar, honest FAIL on breakage)."""
import numpy as np

from coupling.gate import TwinConsistencyGate
from coupling.mesh import structured_mesh_2d
from coupling.remap import L2Remap


def _case(with_velocity=True):
    src = structured_mesh_2d(4, 4)
    tgt = structured_mesh_2d(16, 16)
    f = np.exp(-np.sum((src.cells - 0.5) ** 2, axis=1) / (2 * 0.1**2))
    res = L2Remap(src, tgt).apply(f)
    # constant velocity: momentum/energy integrals reduce to the mass
    # integral, so a conservative exchange conserves all three exactly
    v_s = np.tile([1.0, 0.5], (src.n_cells, 1))
    v_t = np.tile([1.0, 0.5], (tgt.n_cells, 1))
    return src, f, tgt, res.field, v_s, v_t


def test_gate_passes_for_conservative_exchange():
    src, f, tgt, ft, v_s, v_t = _case()
    rep = TwinConsistencyGate(rel_tol=0.01).check(src, f, tgt, ft, v_s, v_t)
    assert rep.passed
    assert max(rep.errors.values()) < 1e-9  # L2 2D is exact to machine precision


def test_gate_fails_honestly_on_broken_exchange():
    src, f, tgt, ft, v_s, v_t = _case()
    ft_broken = ft * 1.05  # 5% mass leak
    rep = TwinConsistencyGate(rel_tol=0.01).check(src, f, tgt, ft_broken, v_s, v_t)
    assert not rep.passed
    assert rep.errors["mass"] > 0.04  # honest FAIL, not covered up


def test_gate_scalar_only():
    src, f, tgt, ft, _, _ = _case()
    rep = TwinConsistencyGate(rel_tol=0.01).check(src, f, tgt, ft)
    assert set(rep.errors) == {"mass"}
    assert rep.passed


def test_gate_rejects_bad_tol():
    import pytest
    with pytest.raises(ValueError):
        TwinConsistencyGate(rel_tol=0.0)
    with pytest.raises(ValueError):
        TwinConsistencyGate(rel_tol=1.5)
