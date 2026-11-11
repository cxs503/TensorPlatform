"""Space-time exchange: composed temporal interpolation + moment-preserving remap.

Acceptance mapping (node IN387-PLAT01):
  C1 (isomorphic identity), C2/C3 (moments survive the composition),
  C5 (round-trip invariants), C6 (energy budget across heterogeneous clocks).
"""
import json

import numpy as np
import pytest

from coupling.mesh import structured_mesh_2d
from coupling.moment_remap import perturbed_mesh_2d, rectilinear_mesh_2d
from coupling.spacetime_exchange import (
    SpacetimeExchangeError,
    SpacetimeExchanger,
    composition_roundtrip,
)
from coupling.temporal_interp import TemporalInterpError


def _meshes():
    src = rectilinear_mesh_2d(np.linspace(0.0, 1.0, 9), np.linspace(0.0, 1.0, 9))
    tgt = perturbed_mesh_2d(8, 8, jitter=0.5, seed=11)
    return src, tgt


def _history(n_t=257, t_end=1.0, sigma=0.18):
    """Snapshots of a translating-diffusing bump: smooth in space and in time."""
    t = np.linspace(0.0, t_end, n_t)
    src, _ = _meshes()
    x, y = src.cells[:, 0], src.cells[:, 1]
    out = np.empty((n_t, src.n_cells))
    for k, tk in enumerate(t):
        xc = 0.3 + 0.4 * tk
        yc = 0.5 + 0.1 * np.sin(2 * np.pi * tk)
        out[k] = 0.1 + np.exp(-((x - xc) ** 2 + (y - yc) ** 2) / (2 * sigma**2))
    return t, out


def _reference_field(tq, sigma=0.18):
    """Field at time ``tq`` on the target mesh (analytic, no discretisation)."""
    _, tgt = _meshes()
    xc = 0.3 + 0.4 * tq
    yc = 0.5 + 0.1 * np.sin(2 * np.pi * tq)
    return 0.1 + np.exp(
        -((tgt.cells[:, 0] - xc) ** 2 + (tgt.cells[:, 1] - yc) ** 2) / (2 * sigma**2)
    )


# ---------------------------------------------------------------------------
# C1 -- identity when both clocks and meshes agree
# ---------------------------------------------------------------------------

def test_identity_when_target_time_is_a_sample():
    src, tgt = _meshes()
    t, hist = _history(n_t=33)
    # same mesh on both sides and a request that lands exactly on a sample
    ex = SpacetimeExchanger(src, src, t, time_method="cubic", enforce_moments=False)
    k = 11
    res = ex.exchange(hist, float(t[k]))
    assert res.time.at_node is True
    assert np.array_equal(res.field, hist[k])
    assert np.max(np.abs(res.field - hist[k])) <= 1e-12


def test_identity_survives_the_composition_when_meshes_match():
    src, _ = _meshes()
    t, hist = _history(n_t=65)
    ex = SpacetimeExchanger(src, src, t, time_method="linear")
    tq = float(t[20] + 0.37 * (t[1] - t[0]))
    res = ex.exchange(hist, tq)
    # temporal interpolation is not the identity between samples, but the spatial
    # operator is, so the result equals the pure temporal interpolant
    from coupling.temporal_interp import TemporalInterpolator, TemporalSeries

    direct = TemporalInterpolator(
        TemporalSeries(times=t, snapshots=hist, weights=src.volumes), method="linear"
    ).interpolate(tq).field
    assert np.max(np.abs(res.field - direct)) <= 1e-12


# ---------------------------------------------------------------------------
# C2/C3 -- moments survive the composition
# ---------------------------------------------------------------------------

def test_space_time_exchange_conserves_mass_and_moments():
    src, tgt = _meshes()
    t, hist = _history(n_t=129)
    ex = SpacetimeExchanger(src, tgt, t, time_method="cubic")
    tq = float(t[40] + 0.43 * (t[1] - t[0]))
    res = ex.exchange(hist, tq)
    assert res.spatial_mass_rel_error <= 1e-12
    assert res.time_mass_rel_error <= 1e-12
    assert res.remap.m1_rel_error <= 1e-6
    assert res.remap.m2_rel_error <= 1e-6
    assert res.remap.centroid_drift_cells <= 1e-3
    assert res.remap.bound_violations == 0


def test_space_time_exchange_delivers_the_moment_invariant():
    """The target array carries the mass/centroid/inertia of the interpolated
    source array (the invariant a coupled solver actually transports)."""
    src, tgt = _meshes()
    t, hist = _history(n_t=65)
    ex = SpacetimeExchanger(src, tgt, t)
    tq = float(t[17] + 0.21 * (t[1] - t[0]))
    res = ex.exchange(hist, tq)
    interp = res.time.field
    m0 = np.dot(src.volumes, interp)
    cm = np.dot(src.volumes * interp, src.cells) / m0
    d = src.cells - cm
    inertia = np.einsum("i,ij,ik->jk", src.volumes * interp, d, d)

    m0t = np.dot(tgt.volumes, res.field)
    cmt = np.dot(tgt.volumes * res.field, tgt.cells) / m0t
    dt = tgt.cells - cmt
    inertia_t = np.einsum("i,ij,ik->jk", tgt.volumes * res.field, dt, dt)

    h = np.mean(np.sqrt(tgt.volumes))
    assert abs(m0t / m0 - 1.0) <= 1e-12
    assert np.linalg.norm(cmt - cm) / h <= 1e-3
    assert np.linalg.norm(inertia_t - inertia) / np.linalg.norm(inertia) <= 1e-6


# ---------------------------------------------------------------------------
# C6 -- energy budget across heterogeneous clocks
# ---------------------------------------------------------------------------

def test_energy_budget_across_heterogeneous_clocks():
    """Fine clock 1024 samples/T, coarse requests every 16th sample with a phase
    offset; a spatially uniform oscillating field isolates the temporal operator."""
    t_end, ratio = 1.0, 16
    n_fine = 1024 + 1
    t = np.linspace(0.0, t_end, n_fine)
    dt_f = t[1] - t[0]
    src, tgt = _meshes()
    osc = np.cos(2 * np.pi * t / t_end)
    hist = np.repeat(osc[:, None], src.n_cells, axis=1)
    ex = SpacetimeExchanger(src, tgt, t, time_method="linear")
    worst = 0.0
    for j in range(1, 30):
        tq = (ratio * j + 0.37) * dt_f
        if tq >= t_end:
            break
        res = ex.exchange(hist, tq, reference_field=_reference_field_uniform(tq, tgt))
        assert res.energy_rel_error is not None
        worst = max(worst, res.energy_rel_error)
    assert worst <= 0.01


def _reference_field_uniform(tq, tgt):
    return np.cos(2 * np.pi * tq) * np.ones(tgt.n_cells)


def test_cubic_is_much_more_accurate_than_linear_in_time():
    src, tgt = _meshes()
    t, hist = _history(n_t=33)
    tq = float(t[13] + 0.5 * (t[1] - t[0]))
    ref = _reference_field(tq)
    lin = SpacetimeExchanger(src, tgt, t, time_method="linear").exchange(hist, tq, reference_field=ref)
    cub = SpacetimeExchanger(src, tgt, t, time_method="cubic").exchange(hist, tq, reference_field=ref)
    err_lin = np.max(np.abs(lin.field - ref))
    err_cub = np.max(np.abs(cub.field - ref))
    assert err_cub < err_lin


# ---------------------------------------------------------------------------
# C5 -- composition round trip
# ---------------------------------------------------------------------------

def test_composition_roundtrip_preserves_the_invariants():
    src, tgt = _meshes()
    f = 0.1 + np.exp(
        -((src.cells[:, 0] - 0.4) ** 2 + (src.cells[:, 1] - 0.55) ** 2) / (2 * 0.18**2)
    )
    rt = composition_roundtrip(src, tgt, f)
    assert rt["mass_rel_error"] <= 1e-12
    assert rt["rel_error"] <= 0.5   # field-level information loss (reported)
    assert rt["forward"]["m1_rel_error"] <= 1e-6


# ---------------------------------------------------------------------------
# fail-closed paths
# ---------------------------------------------------------------------------

def test_request_outside_the_sample_window_fails_closed():
    src, tgt = _meshes()
    t, hist = _history(n_t=33)
    ex = SpacetimeExchanger(src, tgt, t)
    with pytest.raises(TemporalInterpError):
        ex.exchange(hist, 1.5)


def test_snapshot_history_shape_is_validated():
    src, tgt = _meshes()
    t, hist = _history(n_t=33)
    ex = SpacetimeExchanger(src, tgt, t)
    with pytest.raises(SpacetimeExchangeError):
        ex.exchange(hist[:-2], 0.5)
    with pytest.raises(SpacetimeExchangeError):
        ex.exchange(hist[:, :-1], 0.5)


def test_audit_record_is_json_friendly():
    src, tgt = _meshes()
    t, hist = _history(n_t=33)
    tq = float(t[10] + 0.31 * (t[1] - t[0]))
    res = SpacetimeExchanger(src, tgt, t).exchange(hist, tq, reference_field=_reference_field(tq))
    d = res.to_dict()
    for key in ("target_time", "time", "remap", "mass_at_target",
                "time_mass_rel_error", "spatial_mass_rel_error", "energy_rel_error"):
        assert key in d
    json.dumps(d)