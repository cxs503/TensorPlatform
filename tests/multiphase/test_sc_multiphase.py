"""Unit tests for the IN59-P1 multiphase kernel (``src/multiphase``).

Every test here is a *machine-checkable* statement about the kernel, not a
tolerance on a physical QoI: exact conservation laws, symmetry of the force
stencil, moment consistency of the equilibrium, bitwise reproducibility.
They run in a few seconds (small grids, short runs).

    pytest tests/multiphase -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from multiphase import (  # noqa: E402
    CS2,
    ShanChen,
    advect_markers,
    droplet_field,
    eos_pressure,
    potential_flow_impact_ratio,
    pseudopotential,
    slab_field,
)
from multiphase.sc_multiphase import CX, CY, W, equilibrium  # noqa: E402


# --------------------------------------------------------------------- basics
def test_equilibrium_moments():
    rng = np.random.default_rng(0)
    rho = 1.0 + 0.1 * rng.random((8, 9))
    ux = 0.01 * (rng.random((8, 9)) - 0.5)
    uy = 0.01 * (rng.random((8, 9)) - 0.5)
    f = equilibrium(rho, ux, uy)
    assert np.allclose(f.sum(axis=0), rho, rtol=0, atol=1e-14)
    assert np.allclose((f * CX[:, None, None]).sum(axis=0), rho * ux, rtol=0, atol=1e-14)
    assert np.allclose((f * CY[:, None, None]).sum(axis=0), rho * uy, rtol=0, atol=1e-14)


def test_weights_and_lattice_symmetry():
    assert np.isclose(W.sum(), 1.0)
    # the discrete velocity set is symmetric and the second moment is cs^2 I
    assert np.allclose((W * CX * CX).sum(), CS2)
    assert np.allclose((W * CY * CY).sum(), CS2)
    assert np.allclose((W * CX * CY).sum(), 0.0)


def test_pseudopotential_bounds_and_eos():
    rho = np.linspace(1e-3, 6.0, 100)
    psi = pseudopotential(rho, 1.0)
    assert np.all(psi > 0) and np.all(psi < 1.0)
    assert np.all(np.diff(psi) > 0)
    # G < 0 must create a spinodal (non-monotonic EOS) at rho = 1
    p = eos_pressure(rho, -5.0, 1.0)
    grad = np.gradient(p, rho)
    assert grad.min() < 0.0, "no van-der-Waals loop: the interaction would be repulsive"


def test_uniform_fluid_stays_at_rest():
    """A uniform quiescent state is an exact solution; any drift is a bug."""
    for scheme in ("shifted", "guo"):
        sim = ShanChen(16, 12, G=-5.0, tau=1.0, scheme=scheme)
        sim.step(40)
        ux, uy = sim.u_raw
        assert np.abs(ux).max() < 1e-12
        assert np.abs(uy).max() < 1e-12
        assert np.isclose(sim.rho.mean(), 1.0, rtol=0, atol=1e-14)


def test_force_stencil_points_toward_the_dense_phase():
    """Regression test for the roll-sign bug: ``psi(x + c)``, not ``psi(x - c)``.

    A repulsive-looking stencil makes the uniform state unconditionally stable and
    silently kills every multiphase result downstream (this is exactly the bug that
    was found the hard way).  Robust statement, seam-independent: the interaction
    force must be aligned with the density gradient, ``F . grad(rho) > 0``.
    """
    ny, nx = 32, 16
    y = np.arange(ny) + 0.5
    prof = 0.5 * (1.0 - np.tanh((y - 16.0) / 3.0))     # dense for y < 16
    rho = 0.3 + 1.4 * prof[:, None] * np.ones((1, nx))
    sim = ShanChen(nx, ny, G=-5.0, tau=1.0, scheme="guo", rho_init=rho)
    grad = np.gradient(rho.mean(axis=1))
    fy = sim.force[1].mean(axis=1)
    mask = np.abs(grad) > 0.05
    assert mask.sum() >= 2
    assert float((fy[mask] * grad[mask]).sum()) > 0.0, (
        "attraction must pull toward the denser phase: F.grad(rho) > 0, got "
        f"{(fy[mask] * grad[mask]).sum():.4f}"
    )


def test_mass_is_conserved_exactly():
    rho, ux0, uy0 = droplet_field(nx=48, ny=48, R=10.0, rho_liquid=1.7, rho_gas=0.21)
    sim = ShanChen(48, 48, G=-5.0, tau=1.0, scheme="guo", g_ramp=100,
                   rho_init=rho, ux_init=ux0, uy_init=uy0)
    m0 = sim.rho.sum()
    sim.step(80)
    assert abs(sim.rho.sum() - m0) / m0 < 1e-12


def test_total_momentum_is_conserved_in_a_periodic_box():
    """The SC force is pairwise on a periodic lattice -> sum(F) = 0 exactly."""
    for scheme in ("shifted", "guo"):
        rho, ux0, uy0 = droplet_field(nx=40, ny=40, R=8.0, rho_liquid=1.7,
                                      rho_gas=0.21, uy=-0.02)
        sim = ShanChen(40, 40, G=-5.0, tau=1.0, scheme=scheme, g_ramp=50,
                       rho_init=rho, ux_init=ux0, uy_init=uy0)
        px0 = float((sim.rho * sim.u_raw[0]).sum())
        py0 = float((sim.rho * sim.u_raw[1]).sum())
        sim.step(60)
        px1 = float((sim.rho * sim.u_raw[0]).sum())
        py1 = float((sim.rho * sim.u_raw[1]).sum())
        scale = sim.rho.sum() * np.sqrt(CS2)
        assert abs(px1 - px0) / scale < 1e-10
        assert abs(py1 - py0) / scale < 1e-10


def test_bitwise_reproducibility():
    rho, ux0, uy0 = droplet_field(nx=32, ny=32, R=7.0, rho_liquid=1.7, rho_gas=0.21)
    out = []
    for _ in range(2):
        sim = ShanChen(32, 32, G=-5.0, tau=1.0, scheme="guo", g_ramp=60,
                       rho_init=rho, ux_init=ux0, uy_init=uy0)
        sim.step(60)
        out.append(sim.f.copy())
    assert np.array_equal(out[0], out[1])


def test_schemes_are_actually_different():
    """A controlled comparison needs two *different* algorithms, not two names."""
    rho, ux0, uy0 = droplet_field(nx=32, ny=32, R=7.0, rho_liquid=1.7, rho_gas=0.21)
    a = ShanChen(32, 32, G=-5.0, tau=1.0, scheme="shifted", g_ramp=60,
                 rho_init=rho, ux_init=ux0, uy_init=uy0)
    b = ShanChen(32, 32, G=-5.0, tau=1.0, scheme="guo", g_ramp=60,
                 rho_init=rho, ux_init=ux0, uy_init=uy0)
    a.step(60)
    b.step(60)
    assert not np.allclose(a.f, b.f)
    assert a.max_spurious_speed() != b.max_spurious_speed()


# ---------------------------------------------------------------- diagnostics
def test_marker_advection_is_exact_for_a_uniform_flow():
    ny, nx = 16, 16
    ux = np.full((ny, nx), 0.03)
    uy = np.full((ny, nx), -0.02)
    px = np.array([1.5, 7.25, 15.5])
    py = np.array([2.5, 9.75, 14.5])
    npx, npy = advect_markers(px, py, ux, uy, dt=1.0)
    assert np.allclose(npx, (px + 0.03) % nx, atol=1e-12)
    assert np.allclose(npy, (py - 0.02) % ny, atol=1e-12)


def test_added_mass_impact_ratio():
    # equal densities, C_A = 1 (2D cylinder, potential flow) -> u1/u0 = 1/2
    assert np.isclose(potential_flow_impact_ratio(1.7, 1.7), 0.5)
    # no ambient fluid -> no added mass -> velocity is unchanged
    assert np.isclose(potential_flow_impact_ratio(1.7, 0.0), 1.0)
    # a light droplet in a dense pool is stopped harder
    assert potential_flow_impact_ratio(0.85, 1.7) < 0.5


def test_slab_and_droplet_fields_have_the_requested_bulk_values():
    rho, ux, uy = slab_field(nx=16, ny=32, rho_liquid=1.7, rho_gas=0.21,
                             y_interface=16.0)
    assert rho[:4].mean() > 1.6 and rho[-4:].mean() < 0.3
    rho2, _, uy2 = droplet_field(nx=48, ny=48, R=10.0, rho_liquid=1.7,
                                 rho_gas=0.21, uy=-0.05)
    assert rho2[24, 24] > 1.6 and rho2[1, 1] < 0.3
    assert uy2[24, 24] < -0.04 and abs(uy2[1, 1]) < 1e-12


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError):
        ShanChen(8, 8, tau=0.5)
    with pytest.raises(ValueError):
        ShanChen(8, 8, scheme="lattice-boltzmann-but-magic")
    with pytest.raises(ValueError):
        ShanChen(8, 8, rho_init=np.ones((4, 4)))


def test_blob_radius_matches_the_initialised_droplet():
    rho, ux0, uy0 = droplet_field(nx=64, ny=64, R=12.0, rho_liquid=1.7, rho_gas=0.21)
    sim = ShanChen(64, 64, G=-5.0, tau=1.0, scheme="guo", rho_init=rho,
                   ux_init=ux0, uy_init=uy0)
    assert abs(sim.blob_radius(0.5 * (1.7 + 0.21)) - 12.0) < 1.0