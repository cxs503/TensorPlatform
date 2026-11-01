"""m1699 — guards for the hull-girder vertical vibration core.

Each test pins one analytic anchor of ``backend/services/ship_vibration.py`` so
that a future edit cannot silently degrade the physics.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from services import ship_vibration as sv  # noqa: E402


def test_free_free_wavenumbers_match_published_table():
    lam = sv.free_free_wavenumbers(5)
    for computed, ref in zip(lam, sv.FREE_FREE_LAMBDA_TABLE):
        assert abs(computed - ref) / ref < 1e-12


def test_characteristic_equation_residual():
    # the residual is amplified by cosh(lam), so compare against that scale
    for lam in sv.free_free_wavenumbers(6):
        assert abs(math.cos(lam) * math.cosh(lam) - 1.0) < 1e-9 * math.cosh(lam)


def test_free_free_frequencies_match_hermite_beam_fem():
    """Independent check: cubic Hermite beam FEM on 120 elements."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "verify" / "vibration"))
    from audit_hull_vibration import beam_fem_frequencies

    L, EI, mu = 200.0, 1.0e14, 1.5e6
    analytic = sv.free_free_beam_frequencies(L, EI, mu, 3)
    fem = beam_fem_frequencies(L, EI, mu, n_elem=120, n_modes=3)
    assert np.allclose(analytic, fem, rtol=1e-4)


def test_frequency_scale_law():
    f = sv.free_free_beam_frequencies(100.0, 1e13, 1e6, 1)
    f2 = sv.free_free_beam_frequencies(200.0, 1e13, 1e6, 1)
    assert abs(f2[0] / f[0] - 0.25) < 1e-12


def test_exact_added_mass_closed_forms():
    assert sv.circle_added_mass_exact(2.0, 1000.0) == pytest.approx(1000.0 * math.pi * 4.0)
    # vertical motion: only the half-breadth matters, draught is irrelevant
    assert sv.ellipse_added_mass_exact(5.0, 1000.0) == pytest.approx(
        sv.flat_plate_added_mass_exact(5.0, 1000.0))
    # the T -> 0 limit is the flat plate, i.e. fullness does not reduce it
    assert sv.ellipse_added_mass_exact(20.0, 1025.0) == pytest.approx(1288052.99, rel=1e-8)


def test_panel_solver_reproduces_exact_circle():
    a = 10.0
    res = sv.section_added_mass_2d(sv.ellipse_radius_fn(a, a), n_panels=256)
    exact = sv.circle_added_mass_exact(a)
    assert res["bc_residual"] < 1e-9
    assert res["consistency_rel"] < 1e-9
    assert abs(res["m_a"] - exact) / exact < 0.01


def test_panel_solver_refuses_unvalidated_aspect_ratio():
    with pytest.raises(ValueError, match="aspect ratio"):
        sv.section_added_mass_2d(sv.ellipse_radius_fn(20.0, 10.0), n_panels=64)


def test_section_aspect_ratio():
    assert sv.section_aspect_ratio(sv.ellipse_radius_fn(10.0, 10.0)) == pytest.approx(1.0)
    assert sv.section_aspect_ratio(sv.ellipse_radius_fn(20.0, 10.0)) == pytest.approx(2.0)


def test_superellipse_equals_ellipse_at_p2():
    r1 = sv.superellipse_radius_fn(15.0, 8.0, 2.0)
    r2 = sv.ellipse_radius_fn(15.0, 8.0)
    theta = np.linspace(0, 2 * math.pi, 37)
    assert np.allclose(r1(theta), r2(theta), rtol=1e-12)


def test_hull_girder_frequencies_and_added_mass_ratio():
    res = sv.hull_girder_vertical_frequencies(320.0, 2.2e14, 1.0e6, 1.0e6, 3)
    assert len(res["frequencies_hz"]) == 3
    assert res["added_mass_ratio"] == pytest.approx(1.0)
    assert res["effective_mass_per_length_kg_m"] == pytest.approx(2.0e6)
    # frequencies must be strictly increasing with mode number
    assert res["frequencies_hz"][0] < res["frequencies_hz"][1] < res["frequencies_hz"][2]
    # doubling the added mass lowers the first frequency by exactly 1/sqrt(2)
    low = sv.hull_girder_vertical_frequencies(320.0, 2.2e14, 1.0e6, 3.0e6, 1)
    assert low["frequencies_hz"][0] / res["frequencies_hz"][0] == pytest.approx(
        1.0 / math.sqrt(2.0))


def test_blade_rate_and_margin():
    bpf = sv.blade_rate_frequency(100.0, 5)
    assert bpf["blade_rate_hz"] == pytest.approx(5.0 * 100.0 / 60.0)
    assert bpf["shaft_frequency_hz"] == pytest.approx(100.0 / 60.0)
    m = sv.resonance_margin(1.0, 1.25)
    assert m["margin"] == pytest.approx(0.25)
    assert m["relative_separation"] == pytest.approx(0.25)
    assert m["ratio"] == pytest.approx(1.25)


@pytest.mark.parametrize(
    "call",
    [
        lambda: sv.free_free_beam_frequencies(0.0, 1e14, 1e6, 1),
        lambda: sv.free_free_beam_frequencies(100.0, -1.0, 1e6, 1),
        lambda: sv.free_free_wavenumbers(0),
        lambda: sv.ellipse_radius_fn(-1.0, 5.0),
        lambda: sv.superellipse_radius_fn(5.0, 5.0, 1.5),
        lambda: sv.blade_rate_frequency(0.0, 5),
        lambda: sv.resonance_margin(0.0, 1.0),
        lambda: sv.hull_girder_vertical_frequencies(100.0, 1e14, 1e6, -1.0, 1),
    ],
)
def test_invalid_inputs_raise(call):
    with pytest.raises(ValueError):
        call()
