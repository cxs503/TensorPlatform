"""Tests for the m1684 hull-girder vibration kernel (``ship_vibration``).

Layering follows the platform's verification-debt convention: the anchors are
*reference-free judges that must hold exactly* — exact characteristic roots,
monotonicity bracket, similitude invariance, analytic SDOF algebra — while the
FEM is graded against the closed form of the uniform beam with the tolerance the
certified mesh holds in ``examples/verify_ship_vibration.py``.

No service, no network, no heavy compute: everything here runs in well under a
second, so the file is safe on a saturated box.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.ship_vibration import (  # noqa: E402
    analytic_bracket,
    assess_hull_vibration,
    blade_rate_frequency,
    effective_mass_per_length,
    free_free_beam_eigenvalue,
    hull_girder_modal,
    nodal_points,
    resonance_separation,
    sdof_amplification,
    uniform_beam_frequency,
    uniform_beam_mode_shape,
    velocity_limit_check,
)

E = 2.06e11
L = 200.0
I0 = 8.0
M0 = 3.0e4


# ---------------------------------------------------------------------------
# J1 — exact characteristic roots
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "mode,expected",
    [
        (1, 4.730040744862704),
        (2, 7.853204624095838),
        (3, 10.995607838001671),
        (4, 14.137165491257464),
        (5, 17.278759657399481),
    ],
)
def test_free_free_roots_match_reference(mode, expected):
    assert free_free_beam_eigenvalue(mode) == pytest.approx(expected, rel=0, abs=1e-9)


def test_free_free_roots_satisfy_characteristic_equation():
    """cos(x) cosh(x) = 1 to machine precision.

    The naive residual is amplified by cosh(x) ~ 1.6e7 at mode 5 (catastrophic
    cancellation), so the root error is measured the Newton way: residual divided
    by the derivative gives the distance to the true root in radians.
    """
    for mode in range(1, 13):
        x = free_free_beam_eigenvalue(mode)
        f = math.cos(x) * math.cosh(x) - 1.0
        df = math.cos(x) * math.sinh(x) - math.sin(x) * math.cosh(x)
        assert abs(f / df) < 1e-12
        # asymptotic branch: x_n -> (2n+1) pi / 2, within half a radian of it
        assert abs((2 * mode + 1) * math.pi / 2 - x) < 0.5


def test_root_rejects_bad_mode():
    with pytest.raises(ValueError):
        free_free_beam_eigenvalue(0)


def test_closed_form_scales_with_length_to_the_minus_two():
    f1 = uniform_beam_frequency(E, I0, M0, L)
    f2 = uniform_beam_frequency(E, I0, M0, 2 * L)
    assert f1 / f2 == pytest.approx(4.0, rel=1e-12)


# ---------------------------------------------------------------------------
# J2 — FEM vs closed form
# ---------------------------------------------------------------------------

def test_fem_uniform_beam_matches_closed_form():
    exact1 = uniform_beam_frequency(E, I0, M0, L, 1)
    exact2 = uniform_beam_frequency(E, I0, M0, L, 2)
    modal = hull_girder_modal(E, np.full(81, I0), np.full(81, M0), L, 2)
    assert modal["frequencies"][0] == pytest.approx(exact1, rel=1e-6)
    assert modal["frequencies"][1] == pytest.approx(exact2, rel=1e-6)


def test_fem_converges_monotonically_from_above():
    """Consistent-mass Hermite elements approach the closed form fourth order."""
    exact = uniform_beam_frequency(E, I0, M0, L, 1)
    errors = []
    for n_el in (5, 10, 20, 40):
        f = hull_girder_modal(E, np.full(n_el + 1, I0), np.full(n_el + 1, M0), L, 1)["frequencies"][0]
        errors.append(abs(f - exact) / exact)
    assert all(a > b for a, b in zip(errors, errors[1:])), errors
    # last refinement must beat 4th-order-ish expectations (>= 3.5 observed)
    assert math.log(errors[-2] / errors[-1], 2) > 3.5


def test_fem_rigid_body_modes_are_zero():
    modal = hull_girder_modal(E, np.full(41, I0), np.full(41, M0), L, 2)
    assert np.max(np.abs(modal["rigid_body_frequencies"])) < 1e-3


def test_fem_rejects_inconsistent_input():
    with pytest.raises(ValueError):
        hull_girder_modal(E, np.full(10, I0), np.full(9, M0), L)
    with pytest.raises(ValueError):
        hull_girder_modal(E, np.array([1.0, 2.0]), np.array([1.0, 1.0]), L)


# ---------------------------------------------------------------------------
# J3 — mode shape geometry
# ---------------------------------------------------------------------------

def test_mode1_nodal_lines_match_exact_locations():
    modal = hull_girder_modal(E, np.full(81, I0), np.full(81, M0), L, 1)
    nodes = nodal_points(modal["stations"], modal["mode_shapes"][0])
    assert len(nodes) == 2
    assert nodes[0] / L == pytest.approx(0.22416, abs=2e-3)
    assert nodes[1] / L == pytest.approx(0.77584, abs=2e-3)


def test_mode_shape_matches_exact_shape_at_stations():
    xs = np.linspace(0.0, L, 81)
    modal = hull_girder_modal(E, np.full(81, I0), np.full(81, M0), L, 1)
    num = modal["mode_shapes"][0]
    exact = uniform_beam_mode_shape(xs, L, 1)
    if num[40] * exact[40] < 0:
        num = -num
    assert np.sqrt(np.mean((num - exact) ** 2)) < 1e-3


def test_exact_mode_shape_has_extremes_at_the_ends():
    xs = np.linspace(0.0, L, 401)
    phi = uniform_beam_mode_shape(xs, L, 1)
    assert abs(phi[0]) == pytest.approx(1.0, abs=1e-12)
    assert phi[0] * phi[-1] > 0  # free-free mode 1: both ends move in phase


# ---------------------------------------------------------------------------
# J4 — monotonicity bracket for non-uniform girders
# ---------------------------------------------------------------------------

def test_nonuniform_solution_inside_rigorous_bracket():
    x = np.linspace(0.0, 1.0, 41)
    I = 120.0 * (0.4 + 0.6 * np.exp(-((x - 0.5) / 0.25) ** 2))
    m = np.full(x.size, 4.0e5)
    lo, hi = analytic_bracket(E, I, m, L)
    f1 = hull_girder_modal(E, I, m, L, 1)["frequencies"][0]
    assert lo < f1 < hi


def test_bracket_endpoints_are_the_uniform_solutions():
    I = np.linspace(50.0, 150.0, 21)
    m = np.full(21, 1.0e5)
    lo, hi = analytic_bracket(E, I, m, L)
    assert lo == pytest.approx(uniform_beam_frequency(E, 50.0, 1.0e5, L), rel=1e-12)
    assert hi == pytest.approx(uniform_beam_frequency(E, 150.0, 1.0e5, L), rel=1e-12)


def test_bracket_rejects_nonpositive_sections():
    with pytest.raises(ValueError):
        analytic_bracket(E, np.array([1.0, 0.0, 1.0]), np.ones(3), L)


def test_frequency_monotone_in_stiffness():
    x = np.linspace(0.0, 1.0, 31)
    base = 100.0 * (0.4 + 0.6 * np.exp(-((x - 0.5) / 0.25) ** 2))
    m = np.full(x.size, 4.0e5)
    f_a = hull_girder_modal(E, base, m, L, 1)["frequencies"][0]
    f_b = hull_girder_modal(E, base * 4.0, m, L, 1)["frequencies"][0]
    assert f_b / f_a == pytest.approx(2.0, rel=1e-6)


# ---------------------------------------------------------------------------
# J5 — similitude invariance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lam", [0.5, 2.0, 3.0])
def test_similitude_scales_frequency_by_inverse_length(lam):
    x = np.linspace(0.0, 1.0, 41)
    I = 120.0 * (0.4 + 0.6 * np.exp(-((x - 0.5) / 0.25) ** 2))
    m = np.full(x.size, 4.0e5)
    f_ref = hull_girder_modal(E, I, m, L, 1)["frequencies"][0]
    f_sc = hull_girder_modal(E, I * lam ** 4, m * lam ** 2, L * lam, 1)["frequencies"][0]
    assert f_sc / f_ref == pytest.approx(1.0 / lam, rel=1e-6)


# ---------------------------------------------------------------------------
# Mass distribution helper
# ---------------------------------------------------------------------------

def test_effective_mass_uniform_and_added_mass_monotone():
    m = effective_mass_per_length(80000.0, L, 11, added_mass_factor=0.25)
    assert m.shape == (11,)
    assert np.allclose(m, 80000.0 * 1000.0 / L * 1.25)
    m0 = effective_mass_per_length(80000.0, L, 11, added_mass_factor=0.0)
    assert np.allclose(m0, 80000.0 * 1000.0 / L)


def test_effective_mass_distribution_is_normalised_to_the_same_total():
    dist = np.array([1.0, 1.2, 1.4, 1.2, 1.0])
    m = effective_mass_per_length(1000.0, 50.0, 5, added_mass_factor=0.0, distribution=dist)
    assert m.sum() * (50.0 / 5) == pytest.approx(1000.0 * 1000.0, rel=1e-12)


def test_effective_mass_rejects_bad_distribution():
    with pytest.raises(ValueError):
        effective_mass_per_length(1000.0, 50.0, 5, distribution=np.ones(4))


# ---------------------------------------------------------------------------
# Excitation / criteria algebra
# ---------------------------------------------------------------------------

def test_blade_rate_is_definitional():
    assert blade_rate_frequency(120.0, 5) == pytest.approx(10.0, rel=0)
    assert blade_rate_frequency(90.0, 4) == pytest.approx(6.0, rel=0)
    with pytest.raises(ValueError):
        blade_rate_frequency(0.0, 4)


def test_resonance_separation_verdicts_and_boundary():
    # exactly on the 15 % lower boundary -> still acceptable
    c = resonance_separation(0.85 * 6.0, 6.0)
    assert c["verdict"] == "OK_BELOW" and c["margin"] == pytest.approx(0.0, abs=1e-12)
    c = resonance_separation(1.10 * 6.0, 6.0)
    assert c["verdict"] == "OK_ABOVE" and c["margin"] == pytest.approx(0.0, abs=1e-12)
    c = resonance_separation(6.0, 6.0)
    assert c["verdict"] == "COINCIDENCE" and c["margin"] < 0


def test_sdof_amplification_exact_anchors():
    assert sdof_amplification(0.0, 1.0, 0.02) == pytest.approx(1.0, rel=1e-12)
    assert sdof_amplification(1.0, 1.0, 0.02) == pytest.approx(1.0 / (2 * 0.02), rel=1e-12)
    # far above resonance -> attenuation below unity
    assert sdof_amplification(5.0, 1.0, 0.02) < 0.05
    with pytest.raises(ValueError):
        sdof_amplification(1.0, 1.0, 0.0)


def test_velocity_limit_check_arithmetic():
    ok = velocity_limit_check(3.0, 4.0)
    assert ok["verdict"] == "PASS" and ok["utilisation"] == pytest.approx(0.75)
    bad = velocity_limit_check(5.0, 4.0)
    assert bad["verdict"] == "EXCEED" and bad["margin_mm_s"] == pytest.approx(-1.0)
    with pytest.raises(ValueError):
        velocity_limit_check(1.0, 0.0)


# ---------------------------------------------------------------------------
# End-to-end screening
# ---------------------------------------------------------------------------

def test_assess_hull_vibration_end_to_end():
    x = np.linspace(0.0, 1.0, 46)
    I = 260.0 * (0.35 + 0.65 * np.exp(-((x - 0.5) / 0.22) ** 2))
    res = assess_hull_vibration(E, I, 80000.0, 225.0, 90.0, 4)
    assert len(res["frequencies"]) == 6
    assert all(math.isfinite(f) and f > 0 for f in res["frequencies"])
    # frequencies ascending
    assert all(a < b for a, b in zip(res["frequencies"], res["frequencies"][1:]))
    # 2-node mode must sit inside the rigorous bracket of the section field
    lo, hi = analytic_bracket(E, I, np.full(I.size, 80000.0 * 1000.0 / 225.0), 225.0, 1)
    assert lo < res["f_2node_vertical"] < hi
    # nodal lines inside the hull
    assert all(0.0 < n < 225.0 for n in res["nodal_points"])
    assert res["f_blade_rate"] == pytest.approx(6.0)
    # every mode x harmonic pair was screened and margins are finite
    assert len(res["resonance_checks"]) == 6 * 4
    assert all(math.isfinite(c["margin"]) for c in res["resonance_checks"])
    assert res["n_coincident"] == sum(
        1 for c in res["resonance_checks"] if c["verdict"] == "COINCIDENCE"
    )
    assert res["resonant_amplification"] > 1.0


def test_assess_hull_vibration_stiffer_girder_raises_frequency():
    x = np.linspace(0.0, 1.0, 31)
    I = 200.0 * (0.4 + 0.6 * np.exp(-((x - 0.5) / 0.25) ** 2))
    soft = assess_hull_vibration(E, I, 50000.0, 180.0, 100.0, 4)
    stiff = assess_hull_vibration(E, I * 2.0, 50000.0, 180.0, 100.0, 4)
    assert stiff["f_2node_vertical"] > soft["f_2node_vertical"]
    assert stiff["f_2node_vertical"] / soft["f_2node_vertical"] == pytest.approx(
        math.sqrt(2.0), rel=1e-6
    )


def test_nodal_points_helper():
    x = np.linspace(0.0, 1.0, 101)
    assert nodal_points(x, np.sin(2 * math.pi * x)) == pytest.approx([0.0, 0.5], abs=1e-12)