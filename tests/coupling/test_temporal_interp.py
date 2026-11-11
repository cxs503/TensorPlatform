"""Temporal interpolation operator: identity, consistency, order, energy, bounds.

Acceptance mapping (node IN387-PLAT01):
  C1 (isomorphic identity), C6 (energy budget under heterogeneous time steps),
  C2-order (first-order convergence on coupled trajectories).
"""
import numpy as np
import pytest

from coupling.temporal_interp import (
    EnergyAudit,
    TemporalInterpError,
    TemporalInterpolator,
    TemporalSeries,
    convergence_study,
    lagrange_weights,
)


def _series(method_times, values, weights=None):
    return TemporalSeries(
        times=np.asarray(method_times, dtype=float),
        snapshots=np.asarray(values, dtype=float).reshape(len(method_times), -1),
        weights=weights,
    )


def _oscillating_series(n=257, t_end=1.0, n_cells=1):
    t = np.linspace(0.0, t_end, n)
    f = np.cos(2 * np.pi * t / t_end)[:, None] * np.ones((1, n_cells))
    return TemporalSeries(times=t, snapshots=f, weights=np.full(n_cells, 1.0 / n_cells))


# ---------------------------------------------------------------------------
# C1 -- identity on a shared clock
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["nearest", "linear", "cubic"])
def test_node_request_is_bit_exact_identity(method):
    s = _oscillating_series()
    interp = TemporalInterpolator(s, method=method)
    k = 37
    res = interp.interpolate(float(s.times[k]))
    assert res.at_node is True
    assert np.array_equal(res.field, s.snapshots[k])  # bit-exact, error identically 0
    assert abs(res.field[0] - s.snapshots[k, 0]) <= 1e-12


def test_uniform_field_is_preserved_exactly():
    t = np.linspace(0.0, 1.0, 33)
    s = TemporalSeries(times=t, snapshots=np.full((t.size, 5), 3.25))
    for method in ("nearest", "linear", "cubic"):
        out = TemporalInterpolator(s, method=method).interpolate(0.4137).field
        assert np.allclose(out, 3.25, rtol=0, atol=1e-15)


# ---------------------------------------------------------------------------
# C7-style consistency: partition of unity / exactness on polynomials
# ---------------------------------------------------------------------------

def test_lagrange_weights_are_a_partition_of_unity():
    nodes = np.array([0.0, 0.3, 0.55, 1.4])
    w = lagrange_weights(nodes, 0.71)
    assert abs(w.sum() - 1.0) < 1e-15
    # cardinality at the nodes
    for k, xk in enumerate(nodes):
        e = np.zeros(len(nodes))
        e[k] = 1.0
        assert np.allclose(lagrange_weights(nodes, xk), e, atol=1e-14)


def test_linear_operator_reproduces_linear_in_time_exactly():
    t = np.array([0.0, 0.05, 0.17, 0.4, 0.62, 1.0])  # deliberately non-uniform
    f = 2.0 - 1.5 * t
    s = _series(t, f)
    res = TemporalInterpolator(s, method="linear").interpolate(0.2345)
    assert abs(res.field[0] - (2.0 - 1.5 * 0.2345)) < 1e-14
    assert res.weight_sum_error < 1e-15


def test_cubic_operator_reproduces_cubic_in_time_exactly():
    t = np.linspace(0.0, 1.0, 17) ** 1.3
    f = 1.0 - 2.0 * t + 0.5 * t**2 - 0.25 * t**3
    s = _series(t, f)
    res = TemporalInterpolator(s, method="cubic").interpolate(0.4873)
    exact = 1.0 - 2.0 * 0.4873 + 0.5 * 0.4873**2 - 0.25 * 0.4873**3
    assert abs(res.field[0] - exact) < 1e-12
    assert len(res.stencil) == 4


def test_nearest_is_a_zero_order_hold():
    t = np.linspace(0.0, 1.0, 11)
    f = np.arange(t.size, dtype=float)
    interp = TemporalInterpolator(_series(t, f), method="nearest")
    res = interp.interpolate(0.14)  # nearest sample is 0.1
    assert res.field[0] == f[1]
    assert res.stencil == (1,)


# ---------------------------------------------------------------------------
# fail-closed behaviour
# ---------------------------------------------------------------------------

def test_extrapolation_fails_closed_by_default():
    s = _oscillating_series()
    with pytest.raises(TemporalInterpError):
        TemporalInterpolator(s, method="linear").interpolate(1.2)


def test_extrapolation_hold_clamps_to_the_endpoint():
    s = _oscillating_series()
    res = TemporalInterpolator(s, method="linear", extrapolation="hold").interpolate(1.5)
    assert np.isclose(res.field[0], s.snapshots[-1, 0])


def test_bad_series_and_method_are_rejected():
    with pytest.raises(TemporalInterpError):
        TemporalSeries(times=[0.0, 1.0, 0.5], snapshots=np.zeros((3, 1)))
    with pytest.raises(TemporalInterpError):
        TemporalInterpolator(_oscillating_series(), method="quintic")


# ---------------------------------------------------------------------------
# Convergence orders
# ---------------------------------------------------------------------------

def test_smooth_data_orders_are_interpolation_intrinsic():
    lin = convergence_study("smooth", method="linear")
    cub = convergence_study("smooth", method="cubic")
    near = convergence_study("smooth", method="nearest")
    assert abs(lin["order"] - 2.0) < 0.2
    assert abs(cub["order"] - 4.0) < 0.4
    assert abs(near["order"] - 1.0) < 0.2


def test_coupled_trajectory_order_is_one_for_linear():
    """First-order-in-time solver data: the exchange is first order (spec 1 +/- 0.2)."""
    st = convergence_study("euler", method="linear")
    assert abs(st["order"] - 1.0) <= 0.2


def test_cubic_beats_linear_on_smooth_data():
    lin = convergence_study("smooth", method="linear")["err"][-1]
    cub = convergence_study("smooth", method="cubic")["err"][-1]
    assert cub < 1e-2 * lin


def test_piecewise_linear_data_is_reproduced_exactly():
    st = convergence_study("piecewise_linear", method="linear")
    assert st["zero_error"] is True
    assert max(st["err"]) < 1e-13


# ---------------------------------------------------------------------------
# C6 -- energy under heterogeneous time steps
# ---------------------------------------------------------------------------

def test_energy_conservation_on_heterogeneous_clocks():
    """Fine clock dt_f, coarse clock requests at every 16th sample, offset phase."""
    t_end, ratio = 1.0, 16
    n_fine = 1024 + 1
    t_fine = np.linspace(0.0, t_end, n_fine)
    dt_f = t_fine[1] - t_fine[0]
    vols = np.array([0.5, 0.5])
    snap = np.cos(2 * np.pi * t_fine / t_end)[:, None] * np.ones((1, 2))
    series = TemporalSeries(times=t_fine, snapshots=snap, weights=vols)
    interp = TemporalInterpolator(series, method="linear")

    worst = 0.0
    for j in range(1, 40):
        tq = (ratio * j + 0.37) * dt_f
        if tq >= t_end:
            break
        ref = np.cos(2 * np.pi * tq / t_end) * np.ones(2)
        audit = interp.energy_audit(tq, ref)
        assert isinstance(audit, EnergyAudit)
        worst = max(worst, audit.energy_rel_error)
        # the mass defect here is the *interpolation* error of the reference
        # signal (a uniform field carries its global integral in every cell), it
        # is second order in dt_f and far below the energy budget
        assert audit.mass_rel_error <= 0.01
    assert worst <= 0.01


def test_conserved_uniform_snapshot_mass_is_preserved_by_interpolation():
    """Partition of unity => the global integral of every snapshot is reproduced."""
    t = np.linspace(0.0, 1.0, 129)
    n_cells = 8
    vols = np.linspace(0.5, 1.5, n_cells)
    rng = np.random.default_rng(7)
    snap = rng.random((t.size, n_cells))
    snap *= (2.0 / (snap @ vols))[:, None].repeat(n_cells, axis=1)  # mass = 2 everywhere
    series = TemporalSeries(times=t, snapshots=snap, weights=vols)
    for method in ("linear", "cubic"):
        interp = TemporalInterpolator(series, method=method)
        for tq in (0.1234, 0.5007, 0.8771):
            audit = interp.energy_audit(tq, snap[0])
            assert abs(series.mass(interp.interpolate(tq).field) - 2.0) < 1e-12


def test_bounds_limiter_reports_clamped_fraction():
    # cubic through (0,0),(1,1),(2,0),(3,0) dips to -0.3125 at t = 2.5
    s = _series([0.0, 1.0, 2.0, 3.0], [0.0, 1.0, 0.0, 0.0])
    raw = TemporalInterpolator(s, method="cubic").interpolate(2.5)
    assert raw.field[0] < 0.0
    res = TemporalInterpolator(s, method="cubic", bounds=(0.0, None)).interpolate(2.5)
    assert res.field[0] == 0.0
    assert 0.0 < res.clamped_fraction <= 1.0


def test_result_audit_is_json_friendly():
    s = _oscillating_series()
    d = TemporalInterpolator(s, method="cubic").interpolate(0.4321).to_dict()
    assert set(d) >= {"time", "method", "stencil", "weights", "at_node",
                      "weight_sum_error", "clamped_fraction"}