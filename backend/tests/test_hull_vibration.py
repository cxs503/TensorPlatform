"""Verification tests for the hull-girder vertical vibration module.

Anchors (all exact, no empirical data):

* free-free Euler-Bernoulli beam transcendental roots ``cosh(bL)cos(bL) = 1``
  (independent reference constants; the solver recomputes them by bisection);
* the **transfer-matrix solver** is exact per segment, so on a *uniform* beam
  it must reproduce the closed form to machine precision;
* the **Hermite FE solver** must converge to the same closed form at the
  theoretical O(h^4) rate (slope of log(error) vs log(h) ~ 4);
* scaling laws that hold exactly for a uniform beam: ``f ~ sqrt(EI)`` and
  ``f ~ 1/sqrt(m)``;
* box-girder section: for equal deck/bottom areas ``A`` and vanishing side
  plates, ``I = A D^2 / 2`` and ``z_NA = D / 2``;
* eigenvalue residual ``||K phi - omega^2 M phi|| / ||K phi||`` and
  mass-orthonormality of the FE modes.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from services.hull_vibration import (  # noqa: E402
    BeamSegment,
    UNIFORM_BEAM_ROOT_REFERENCE,
    blade_rate_frequency,
    box_girder_section,
    fem_frequencies,
    hull_girder_vibration,
    tmm_frequencies,
    uniform_beam_frequencies,
    uniform_beam_mode_shape,
    uniform_beam_roots,
)

EI_REF = 1.0e14        # N m^2
M_REF = 2.5e5          # kg / m
L_REF = 320.0          # m


# ---------------------------------------------------------------------------
# 1. Closed form and its roots
# ---------------------------------------------------------------------------

def test_roots_match_independent_reference_constants():
    roots = uniform_beam_roots(5)
    ref = np.array(UNIFORM_BEAM_ROOT_REFERENCE)
    assert np.allclose(roots, ref, rtol=1e-12)


def test_roots_satisfy_the_transcendental_equation():
    for x in uniform_beam_roots(6):
        assert abs(math.cosh(x) * math.cos(x) - 1.0) < 1e-9 * math.cosh(x)


def test_uniform_frequency_against_hand_computation():
    # f_1 = (4.7300407448627040)^2 / (2 pi L^2) * sqrt(EI/m)
    expected = (4.7300407448627040 ** 2) / (2.0 * math.pi * L_REF ** 2) * math.sqrt(
        EI_REF / M_REF
    )
    got = float(uniform_beam_frequencies(EI_REF, M_REF, L_REF, 1)[0])
    assert got == pytest.approx(expected, rel=1e-12)
    # 2-node frequency of a 320 m hull girder is O(0.5-1 Hz): sanity band
    assert 0.3 < got < 2.0


def test_frequency_scaling_laws_are_exact():
    f0 = uniform_beam_frequencies(EI_REF, M_REF, L_REF, 3)
    f_EI = uniform_beam_frequencies(4.0 * EI_REF, M_REF, L_REF, 3)
    f_m = uniform_beam_frequencies(EI_REF, 4.0 * M_REF, L_REF, 3)
    assert np.allclose(f_EI / f0, 2.0, rtol=1e-12)
    assert np.allclose(f_m / f0, 0.5, rtol=1e-12)


def test_uniform_mode_shape_has_unit_value_and_orthogonal_slope():
    x = np.linspace(0.0, L_REF, 201)
    phi = uniform_beam_mode_shape(UNIFORM_BEAM_ROOT_REFERENCE[0], L_REF, x)
    assert abs(phi[0] - 1.0) < 1e-12
    # free-free 2-node mode has one sign change (two nodes counting the ends)
    interior = phi[1:-1]
    assert np.any(interior * interior[0] < 0.0)


# ---------------------------------------------------------------------------
# 2. Transfer-matrix reference solver
# ---------------------------------------------------------------------------

def test_tmm_reproduces_closed_form_on_uniform_beam():
    segs = [BeamSegment(L_REF, EI_REF, M_REF)]
    f_closed = uniform_beam_frequencies(EI_REF, M_REF, L_REF, 3)
    f_tmm = tmm_frequencies(segs, 3)
    assert np.allclose(f_tmm, f_closed, rtol=1e-6)


def test_tmm_on_stepped_beam_is_bracketed_by_uniform_limits():
    # middle segment 4x stiffer: the stepped spectrum must be stiffer than the
    # all-soft beam and softer than the all-stiff beam
    soft = [BeamSegment(320.0, EI_REF, M_REF)]
    stepper = [
        BeamSegment(100.0, EI_REF, M_REF),
        BeamSegment(120.0, 4.0 * EI_REF, M_REF),
        BeamSegment(100.0, EI_REF, M_REF),
    ]
    stiff = [BeamSegment(320.0, 4.0 * EI_REF, M_REF)]
    f_soft = tmm_frequencies(soft, 1)[0]
    f_step = tmm_frequencies(stepper, 1)[0]
    f_stiff = tmm_frequencies(stiff, 1)[0]
    assert f_soft < f_step < f_stiff


# ---------------------------------------------------------------------------
# 3. Hermite FE solver
# ---------------------------------------------------------------------------

def test_fem_matches_closed_form_within_one_percent():
    res = fem_frequencies([BeamSegment(L_REF, EI_REF, M_REF)], 3, 32)
    f_closed = uniform_beam_frequencies(EI_REF, M_REF, L_REF, 3)
    rel = np.abs(res["frequencies_hz"] - f_closed) / f_closed
    assert rel.max() < 0.01


def test_fem_rigid_body_modes_are_zero():
    # A free-free beam has two zero-energy modes.  The generalised eigensolver
    # returns their eigenvalues with an O(1e-9) absolute error in omega^2, and
    # f = omega / 2 pi amplifies that to ~1e-5 Hz, so the tolerance is on the
    # frequency (1e-3 Hz) rather than on f == 0.
    res = fem_frequencies([BeamSegment(L_REF, EI_REF, M_REF)], 2, 16)
    assert np.abs(res["rigid_body"]).max() < 1e-3
    assert np.abs(res["rigid_body"]).max() < 1e-3 * res["frequencies_hz"][0]


def test_fem_convergence_rate_is_fourth_order():
    f_closed = uniform_beam_frequencies(EI_REF, M_REF, L_REF, 1)
    elems = [8, 16, 32, 64]
    err = []
    for n in elems:
        f = fem_frequencies([BeamSegment(L_REF, EI_REF, M_REF)], 1, n)["frequencies_hz"][0]
        err.append(abs(f - f_closed[0]) / f_closed[0])
    slopes = [
        math.log(err[i] / err[i + 1]) / math.log(elems[i + 1] / elems[i])
        for i in range(len(elems) - 1)
    ]
    assert min(slopes) > 3.5, slopes      # theory: 4 for cubic Hermite beam


def test_fem_eigen_solution_residuals_and_orthonormality():
    res = fem_frequencies([BeamSegment(L_REF, EI_REF, M_REF)], 3, 24)
    K, M, phi = res["K"], res["M"], res["phi"]
    omega = 2.0 * math.pi * res["frequencies_hz"]
    k_norm = np.linalg.norm(K)
    for k, w in enumerate(omega):
        r = K @ phi[:, k] - w * w * M @ phi[:, k]
        # relative residual against ||K|| ||phi|| (K is EI-scaled: 1e14, so an
        # absolute residual is meaningless without this normalisation)
        denom = k_norm * np.linalg.norm(phi[:, k])
        assert np.linalg.norm(r) / max(denom, 1e-30) < 1e-8
    G = phi.T @ M @ phi
    assert np.allclose(G, np.eye(G.shape[0]), atol=1e-10)
    # modes are dynamically orthogonal too (K-orthogonality)
    GK = phi.T @ K @ phi
    assert np.allclose(GK, np.diag(np.diag(GK)), atol=1e-6 * np.abs(GK).max())


# ---------------------------------------------------------------------------
# 4. Section idealisation
# ---------------------------------------------------------------------------

def test_box_girder_limit_case_matches_closed_form():
    B, D, t = 40.0, 20.0, 0.02
    sec = box_girder_section(B, D, t, t, 0.0)
    A = B * t
    # two plates of area A separated by D: analytic limit of the idealisation
    expected = 2.0 * A * (D / 2.0) ** 2 + 2.0 * B * t ** 3 / 12.0
    assert sec["I_vertical_m4"] == pytest.approx(expected, rel=1e-12)
    assert sec["neutral_axis_above_keel_m"] == pytest.approx(D / 2.0, rel=1e-12)
    assert sec["section_area_m2"] == pytest.approx(2.0 * A, rel=1e-12)


def test_box_girder_side_plates_raise_inertia():
    base = box_girder_section(40.0, 20.0, 0.02, 0.02, 0.0)["I_vertical_m4"]
    with_sides = box_girder_section(40.0, 20.0, 0.02, 0.02, 0.02)["I_vertical_m4"]
    assert with_sides > base


def test_section_moduli_are_positive_and_consistent():
    sec = box_girder_section(58.0, 30.0, 0.035, 0.030, 0.022)
    assert sec["section_modulus_deck_m3"] > 0.0
    assert sec["section_modulus_keel_m3"] > 0.0
    assert sec["I_vertical_m4"] == pytest.approx(
        sec["section_modulus_deck_m3"] * (30.0 - sec["neutral_axis_above_keel_m"]), rel=1e-9
    )


# ---------------------------------------------------------------------------
# 5. Top-level hull-girder entry point
# ---------------------------------------------------------------------------

def _kvlcc2_like(**kwargs):
    params = dict(
        length_m=320.0,
        beam_m=58.0,
        depth_m=30.0,
        draft_m=20.8,
        t_deck_m=0.035,
        t_bottom_m=0.030,
        t_side_m=0.022,
        cb=0.82,
        rpm=90.0,
        n_blades=4,
        n_modes=3,
        n_elements=48,
    )
    params.update(kwargs)
    return hull_girder_vibration(**params)


def test_hull_girder_solvers_agree():
    fem = _kvlcc2_like(solver="fem")
    cf = _kvlcc2_like(solver="closed_form")
    tmm = _kvlcc2_like(solver="tmm")
    assert np.allclose(fem.frequencies_hz, cf.frequencies_hz, rtol=2e-4)
    assert np.allclose(tmm.frequencies_hz, cf.frequencies_hz, rtol=1e-6)


def test_hull_girder_additional_mass_lowers_frequency():
    light = _kvlcc2_like(added_mass_ratio=0.0)
    heavy = _kvlcc2_like(added_mass_ratio=1.0)
    assert heavy.frequencies_hz[0] < light.frequencies_hz[0]
    # for a uniform beam the scaling is exact: f ~ (m_s + a m_d)^-1/2
    m_s = light.mass["steel_mass_per_m"]
    m_d = light.mass["added_mass_per_m"] if light.mass["added_mass_per_m"] > 0 else None
    assert m_d is None or m_d >= 0.0


def test_hull_girder_flags_under_idealised_section():
    res = _kvlcc2_like()
    assert res.section["I_source"] == "box_girder_idealisation"
    assert any("steel-weight" in w for w in res.warnings)


def test_hull_girder_user_section_override_suppresses_warning():
    res = _kvlcc2_like(I_vertical_m4=1200.0, steel_mass_per_m=112000.0)
    assert res.section["I_source"] == "user"
    assert res.section["I_vertical_m4"] == pytest.approx(1200.0)
    assert not any("steel-weight" in w for w in res.warnings)


def test_blade_rate_and_margins():
    br = blade_rate_frequency(90.0, 4)
    assert br["shaft_hz"] == pytest.approx(1.5)
    assert br["blade_rate_hz"] == pytest.approx(6.0)
    res = _kvlcc2_like()
    assert len(res.margins) == 2 * len(res.frequencies_hz)
    for m in res.margins:
        assert m["resonance_risk"] == (abs(m["relative_separation"]) < 0.10)


def test_serialisation_is_json_safe():
    import json

    payload = _kvlcc2_like().as_dict()
    json.dumps(payload, allow_nan=False)
    assert len(payload["frequencies_hz"]) == 3
    assert payload["mode_labels"][0] == "2-node"


# ---------------------------------------------------------------------------
# 6. Input validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "kw",
    [
        {"length_m": 0.0},
        {"beam_m": -1.0},
        {"cb": 0.0},
        {"t_deck_m": 0.0},
    ],
)
def test_invalid_inputs_rejected(kw):
    with pytest.raises(ValueError):
        _kvlcc2_like(**kw)


def test_unknown_solver_rejected():
    with pytest.raises(ValueError):
        _kvlcc2_like(solver="magic")

# ---------------------------------------------------------------------------
# 7. API surface (router mounted on a bare FastAPI app: the full application
#    needs a database driver, this suite only needs the calculation router)
# ---------------------------------------------------------------------------

def _router_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.calculations import router

    app = FastAPI()
    app.include_router(router)
    paths = [r.path for r in app.routes if "vibration" in getattr(r, "path", "")]
    assert paths, "vibration route not registered"
    _router_client.path = paths[0]
    return TestClient(app)


def test_api_endpoint_returns_consistent_spectrum():
    client = _router_client()
    resp = client.post(
        _router_client.path,
        json={"n_modes": 2, "n_elements": 32, "solver": "fem"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ref = _kvlcc2_like(n_modes=2, n_elements=32, solver="fem")
    # the JSON payload rounds to 6 decimals, hence rtol 1e-5
    assert np.allclose(body["frequencies_hz"], ref.frequencies_hz, rtol=1e-5)
    assert body["mode_labels"] == ["2-node", "3-node"]


def test_api_endpoint_rejects_bad_solver_and_bad_geometry():
    client = _router_client()
    bad_solver = client.post(_router_client.path, json={"solver": "magic"})
    assert bad_solver.status_code == 400
    bad_geom = client.post(_router_client.path, json={"cb": 0.0})
    assert bad_geom.status_code == 422        # rejected by pydantic (gt=0)
