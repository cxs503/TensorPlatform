"""Acceptance tests for ice-load inversion & structural health monitoring.

Covers the mission acceptance criteria:

* (a) noiseless inversion is exact (residual < 1e-9)
* (b) 5 % strain noise -> load uncertainty < 10 % (amplification < 2)
* (c) L-curve corner exists and the selected lambda is (near) error-optimal
* (d) DNV safety-factor bands (SF >= 1.5 SAFE, 1.0 <= SF < 1.5 MARGINAL, FAIL < 1.0)
* (e) a single inversion cycle takes < 0.1 s
* (f) the suite itself (>= 12 tests)

plus forward-model verification against Euler-Bernoulli closed-form solutions,
uncertainty-propagation cross-checks and the HTTP endpoint.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.ice_shm import (  # noqa: E402
    SF_FAIL_THRESHOLD,
    SF_SAFE_THRESHOLD,
    BeamSection,
    IceBeltFrameModel,
    StructuralHealthMonitor,
    classify_safety_factor,
    invert_ice_load,
    l_curve_scan,
    safety_factor_with_uncertainty,
    synthesize_strain,
    tikhonov_solve,
    validate_structural_geometry,
)

# ---------------------------------------------------------------------------
# Reference configuration (documented in docs/m838/report.md)
# ---------------------------------------------------------------------------

LENGTH_M = 5.0
N_ELEMENTS = 20
LOAD_POSITIONS = [1.0, 2.0, 3.0, 4.0]
GAUGES = [0.2 + 4.6 * i / 23 for i in range(24)]
LOADS_TRUE = np.linspace(0.75, 1.25, 4) * 55.0e3  # 220 kN total ice load
NOISE_REL = 0.05
SEED = 7


@pytest.fixture(scope="module")
def model() -> IceBeltFrameModel:
    return IceBeltFrameModel(length_m=LENGTH_M, n_elements=N_ELEMENTS)


@pytest.fixture(scope="module")
def influence(model: IceBeltFrameModel):
    return model.build_influence_matrix(LOAD_POSITIONS, GAUGES)


@pytest.fixture(scope="module")
def noisy_data(influence):
    return synthesize_strain(influence, LOADS_TRUE, noise_rel=NOISE_REL, seed=SEED)


# ---------------------------------------------------------------------------
# 1. Section / geometry validation
# ---------------------------------------------------------------------------


def test_section_properties_are_derived_from_geometry():
    s = BeamSection(web_height_m=0.45, web_thickness_m=0.012, flange_width_m=0.12,
                    flange_thickness_m=0.018)
    # hand calculation of the T-section about its own neutral axis
    a_w, a_f = 0.45 * 0.012, 0.12 * 0.018
    y_bar = (a_w * 0.225 + a_f * (0.45 + 0.009)) / (a_w + a_f)
    i_ref = (
        0.012 * 0.45 ** 3 / 12 + a_w * (0.225 - y_bar) ** 2
        + 0.12 * 0.018 ** 3 / 12 + a_f * (0.459 - y_bar) ** 2
    )
    assert s.neutral_axis_from_plating_m == pytest.approx(y_bar, rel=1e-12)
    assert s.inertia_m4 == pytest.approx(i_ref, rel=1e-12)
    assert s.inertia_m4 == pytest.approx(1.75664e-4, rel=1e-3)
    assert s.section_modulus_m3 == pytest.approx(s.inertia_m4 / s.extreme_fibre_offset_m)
    assert s.area_m2 == pytest.approx(a_w + a_f)
    with pytest.raises(ValueError):
        BeamSection(web_height_m=-0.1)
    with pytest.raises(ValueError):
        BeamSection(web_thickness_m=0.012, flange_width_m=0.001)


def test_geometry_validation_flags_euler_bernoulli_range():
    report = validate_structural_geometry(LENGTH_M, N_ELEMENTS, BeamSection())
    assert report["n_elements"] == N_ELEMENTS
    assert report["element_length_m"] == pytest.approx(LENGTH_M / N_ELEMENTS)
    assert report["slenderness_l_over_h"] > 10.0
    assert report["euler_bernoulli_valid"] is True
    with pytest.raises(ValueError):
        validate_structural_geometry(-1.0, N_ELEMENTS, BeamSection())
    with pytest.raises(ValueError):
        validate_structural_geometry(LENGTH_M, 1, BeamSection())
    with pytest.raises(ValueError):
        validate_structural_geometry(LENGTH_M, N_ELEMENTS, BeamSection(), boundary="free")


# ---------------------------------------------------------------------------
# 2. Forward model vs closed-form Euler-Bernoulli solutions
# ---------------------------------------------------------------------------


def test_forward_fixed_fixed_point_load_matches_analytic(model: IceBeltFrameModel):
    """Clamped-clamped centre load: delta = PL^3/(192EI), M = PL/8."""
    p = 100.0e3
    mid = model.n_nodes // 2
    f = np.zeros(model.n_dof)
    f[2 * mid] = p
    u = model.solve(f)
    ei = model.young_modulus_pa * model.section.inertia_m4

    delta_ref = p * model.length_m ** 3 / (192.0 * ei)
    assert abs(u[2 * mid]) == pytest.approx(delta_ref, rel=1e-10)

    profile = model.internal_forces(u)
    m_ref = p * model.length_m / 8.0
    assert np.max(np.abs(profile["moment_nm"])) == pytest.approx(m_ref, rel=1e-3)

    # bending stress at the extreme fibre must follow sigma = M c / I
    sigma_ref = m_ref * model.section.fibre_from_plating_m / model.section.inertia_m4
    assert np.max(np.abs(profile["sigma_x_plating_pa"])) == pytest.approx(sigma_ref, rel=1e-3)


def test_forward_cantilever_tip_load_matches_analytic():
    m = IceBeltFrameModel(length_m=3.0, n_elements=12, boundary="cantilever")
    p = 20.0e3
    f = np.zeros(m.n_dof)
    f[2 * (m.n_nodes - 1)] = p
    u = m.solve(f)
    ei = m.young_modulus_pa * m.section.inertia_m4
    delta_ref = p * m.length_m ** 3 / (3.0 * ei)
    assert abs(u[2 * (m.n_nodes - 1)]) == pytest.approx(delta_ref, rel=1e-10)
    profile = m.internal_forces(u)
    assert np.max(np.abs(profile["moment_nm"])) == pytest.approx(p * m.length_m, rel=1e-3)


def test_forward_uniform_load_matches_analytic(model: IceBeltFrameModel):
    """Clamped-clamped UDL: delta = wL^4/(384EI), M = wL^2/12 (consistent loads).

    With consistent nodal load vectors the Hermite element reproduces the nodal
    deflections of the closed-form solution exactly; the recovered section
    moment converges with mesh refinement (2nd order).
    """
    w = 40.0e3  # N/m
    ei = model.young_modulus_pa * model.section.inertia_m4
    delta_ref = w * LENGTH_M ** 4 / (384.0 * ei)
    m_ref = w * LENGTH_M ** 2 / 12.0

    moment_errors = []
    for n_el in (10, 20, 40):
        m = IceBeltFrameModel(length_m=LENGTH_M, n_elements=n_el)
        le, el = m.element_length_m, m.element_length_m
        f = np.zeros(m.n_dof)
        for e in range(n_el):
            dofs = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
            f[dofs] += np.array([w * le / 2, w * le ** 2 / 12, w * le / 2, -w * le ** 2 / 12])
        u = m.solve(f)
        # nodal deflection is exact
        assert abs(abs(u[2 * (m.n_nodes // 2)]) - delta_ref) / delta_ref < 1e-10
        # section moment at the element adjacent to the support, sampled inside
        # the element, converges from the linear (FE) to the quadratic (exact) field
        x = 0.5 * el
        moment_fe = ei * m.curvature_at(x, u)
        moment_ref = w * (LENGTH_M * x / 2.0 - x ** 2 / 2.0) - w * LENGTH_M ** 2 / 12.0
        moment_errors.append(abs(abs(moment_fe) - abs(moment_ref)) / m_ref)
        profile = m.internal_forces(u)
        assert np.max(np.abs(profile["moment_nm"])) == pytest.approx(m_ref, rel=1e-2)
    assert moment_errors[0] > moment_errors[1] > moment_errors[2], moment_errors
    assert moment_errors[2] < 1e-2


def test_strain_influence_matrix_matches_forward_solution(model, influence):
    """eps = H F must reproduce the direct FE strain at every gauge."""
    u = model.solve_candidates(LOADS_TRUE, influence.load_nodes)
    eps_direct = np.array(
        [model.strain_at(x, u) for x in influence.gauge_positions_m]
    )
    eps_matrix = influence.H @ LOADS_TRUE
    assert np.allclose(eps_matrix, eps_direct, rtol=1e-10, atol=0.0)
    # superposition / linearity
    assert np.allclose(influence.H @ (2.0 * LOADS_TRUE), 2.0 * eps_matrix, rtol=1e-12)
    assert influence.rank == influence.n_load


def test_stress_maps_are_consistent_with_displacement_recovery(model, influence):
    """The vectorised stress maps must agree with the displacement-based route."""
    direct = model.stress_state(LOADS_TRUE, influence.load_nodes)
    batch = model.stress_state_many([LOADS_TRUE], influence.load_nodes)
    assert batch["max_von_mises_pa"][0] == pytest.approx(direct["max_von_mises_pa"], rel=1e-12)
    assert batch["max_bending_moment_nm"][0] == pytest.approx(
        direct["max_bending_moment_nm"], rel=1e-12
    )


# ---------------------------------------------------------------------------
# 3. Inverse problem: criterion (a) noiseless exactness
# ---------------------------------------------------------------------------


def test_tikhonov_zero_lambda_equals_least_squares(influence, noisy_data):
    eps, _ = noisy_data
    assert np.allclose(
        tikhonov_solve(influence.H, eps, 0.0),
        np.linalg.lstsq(influence.H, eps, rcond=None)[0],
        rtol=0, atol=1e-18,
    )


def test_noiseless_inversion_is_exact(model, influence):
    """Criterion (a): residual < 1e-9 and F_inv == F_true."""
    eps, _ = synthesize_strain(influence, LOADS_TRUE, noise_rel=0.0)
    result = invert_ice_load(
        model, eps, LOAD_POSITIONS, GAUGES,
        lambda_selection="fixed", lam=0.0, loads_true_n=LOADS_TRUE, influence=influence,
    )
    assert result.residual_norm < 1e-9
    assert result.load_relative_error < 1e-9
    assert np.allclose(result.loads_inverted_n, LOADS_TRUE, rtol=1e-9, atol=1e-3)


def test_noiseless_inversion_with_lcurve_also_exact(model, influence):
    """Without noise the L-curve must not smear an exact solution (< 1 % error)."""
    eps, _ = synthesize_strain(influence, LOADS_TRUE, noise_rel=0.0)
    result = invert_ice_load(
        model, eps, LOAD_POSITIONS, GAUGES,
        lambda_selection="l_curve", loads_true_n=LOADS_TRUE, influence=influence,
    )
    assert result.diagnostics["noise_free_data"] is True
    assert result.lam == 0.0
    assert result.load_relative_error < 1e-9
    assert result.residual_norm < 1e-9


# ---------------------------------------------------------------------------
# 4. Inverse problem: criterion (b) noise amplification
# ---------------------------------------------------------------------------


def test_noise_amplification_below_two(model, influence, noisy_data):
    """Criterion (b): 5 % strain noise -> < 10 % load uncertainty (factor < 2)."""
    eps, sigma = noisy_data
    result = invert_ice_load(
        model, eps, LOAD_POSITIONS, GAUGES, lambda_selection="l_curve",
        loads_true_n=LOADS_TRUE, sigma_strain=sigma, influence=influence,
    )
    assert result.noise_level == pytest.approx(NOISE_REL, abs=0.005)
    assert result.load_uncertainty < 0.10
    assert result.amplification_factor < 2.0
    assert result.load_relative_error < 0.10


def test_noise_amplification_is_robust_across_seeds(model, influence):
    """The amplification bound must hold for every noise realisation, not one lucky seed."""
    factors = []
    for seed in range(8):
        eps, sigma = synthesize_strain(influence, LOADS_TRUE, noise_rel=NOISE_REL, seed=seed)
        res = invert_ice_load(
            model, eps, LOAD_POSITIONS, GAUGES, lambda_selection="l_curve",
            loads_true_n=LOADS_TRUE, sigma_strain=sigma, influence=influence,
        )
        factors.append(res.amplification_factor)
    assert max(factors) < 2.0
    assert np.mean(factors) == pytest.approx(1.6, abs=0.2)


# ---------------------------------------------------------------------------
# 5. Inverse problem: criterion (c) L-curve
# ---------------------------------------------------------------------------


def test_lcurve_is_l_shaped_with_error_optimal_corner(model, influence, noisy_data):
    """Criterion (c): interior curvature peak and near-minimal inversion error."""
    eps, _ = noisy_data
    scan = l_curve_scan(influence.H, eps, true_loads=LOADS_TRUE)
    lam = np.asarray(scan["lambda"])
    rho = np.asarray(scan["residual_norm"])
    eta = np.asarray(scan["solution_norm"])
    curvature = np.asarray(scan["curvature"])

    # L-shape: a real trade-off curve spanning decades in both norms
    assert np.log10(rho.max() / rho.min()) > 1.0
    assert np.log10(eta.max() / eta.min()) > 0.5
    # monotone branches (residual grows, solution shrinks with lambda)
    assert np.all(np.diff(rho) > -1e-18)
    assert np.all(np.diff(eta) <= 1e-18)

    # curvature corner strictly interior and pronounced
    idx = int(scan["index_opt"])
    assert 0 < idx < lam.size - 1
    assert abs(curvature[idx]) == pytest.approx(np.max(np.abs(curvature[1:-1])))
    assert abs(scan["corner_curvature"]) > 10.0

    # the selected lambda is (near) error-optimal
    ratio = scan["inversion_error_at_corner"] / scan["min_inversion_error"]
    assert ratio < 1.5
    assert scan["inversion_error_at_corner"] < 0.10


def test_lcurve_lambda_reduces_noise_amplification(model, influence, noisy_data):
    """Increasing lambda must monotonically reduce the propagated load uncertainty."""
    eps, sigma = noisy_data
    from services.ice_shm import load_covariance, relative_load_uncertainty

    amps = []
    for lam in np.logspace(-19, -16, 8):
        f = tikhonov_solve(influence.H, eps, lam)
        _, s_f = load_covariance(influence.H, lam, sigma)
        amps.append(relative_load_uncertainty(f, s_f))
    assert np.all(np.diff(amps) < 0.0)


# ---------------------------------------------------------------------------
# 6. Safety factor: criterion (d)
# ---------------------------------------------------------------------------


def test_safety_factor_bands_follow_dnv_criterion():
    """Criterion (d): SF >= 1.5 SAFE, 1.0 <= SF < 1.5 MARGINAL, SF < 1.0 FAIL."""
    assert classify_safety_factor(2.0)["level"] == "SAFE"
    assert classify_safety_factor(SF_SAFE_THRESHOLD)["level"] == "SAFE"
    assert classify_safety_factor(1.2)["level"] == "MARGINAL"
    assert classify_safety_factor(SF_FAIL_THRESHOLD)["level"] == "MARGINAL"
    assert classify_safety_factor(0.9)["level"] == "FAIL"
    assert classify_safety_factor(1.0)["verdict"] == "ATTENTION"
    assert classify_safety_factor(0.5)["verdict"] == "NOT_ACCEPTABLE"
    assert classify_safety_factor(2.0)["utilisation"] == pytest.approx(0.5)
    with pytest.raises(ValueError):
        classify_safety_factor(float("nan"))


def test_safety_factor_crosses_bands_with_ice_load(model, influence):
    """Increasing ice load must drive SF from SAFE through MARGINAL to FAIL."""
    levels = []
    for scale in (0.8, 1.2, 1.8):
        state = safety_factor_with_uncertainty(
            model, LOADS_TRUE * scale, influence.load_nodes
        )
        levels.append((state["safety_factor"], state["classification"]["level"]))
    assert levels[0][1] == "SAFE"
    assert levels[1][1] == "MARGINAL"
    assert levels[2][1] == "FAIL"
    assert levels[0][0] > levels[1][0] > levels[2][0]
    # reported stress must equal yield/SF
    state = safety_factor_with_uncertainty(model, LOADS_TRUE, influence.load_nodes)
    assert state["safety_factor"] == pytest.approx(
        state["yield_strength_pa"] / state["max_von_mises_pa"], rel=1e-12
    )


def test_uncertainty_first_order_matches_monte_carlo(model, influence, noisy_data):
    """First-order SF scatter must reproduce the Monte-Carlo scatter (within 30 %)."""
    eps, sigma = noisy_data
    result = invert_ice_load(
        model, eps, LOAD_POSITIONS, GAUGES, lambda_selection="l_curve",
        loads_true_n=LOADS_TRUE, sigma_strain=sigma, n_mc=400, seed=11, influence=influence,
    )
    mc = result.stress["monte_carlo"]
    sigma_sf = result.stress["sigma_sf_first_order"]
    assert sigma_sf > 0.0
    assert mc["sf_std"] > 0.0
    assert abs(sigma_sf - mc["sf_std"]) / mc["sf_std"] < 0.30
    assert mc["sf_p05"] < mc["sf_mean"] < mc["sf_p95"]


# ---------------------------------------------------------------------------
# 7. Efficiency: criterion (e)
# ---------------------------------------------------------------------------


def test_single_inversion_is_fast(model, influence, noisy_data):
    """Criterion (e): one inversion + assessment cycle < 0.1 s."""
    eps, sigma = noisy_data
    dumps = []
    for _ in range(3):
        t0 = time.perf_counter()
        invert_ice_load(
            model, eps, LOAD_POSITIONS, GAUGES, lambda_selection="l_curve",
            loads_true_n=LOADS_TRUE, sigma_strain=sigma, influence=influence,
        )
        dumps.append(time.perf_counter() - t0)
    assert min(dumps) < 0.1, dumps
    assert max(dumps) < 0.2, dumps


# ---------------------------------------------------------------------------
# 8. Real-time monitoring integration
# ---------------------------------------------------------------------------


def test_monitor_stream_detects_escalating_ice_load(model, influence):
    monitor = StructuralHealthMonitor(
        model, GAUGES, LOAD_POSITIONS, noise_rel=NOISE_REL, n_mc=0
    )
    scales = [0.8, 1.0, 1.2, 1.5, 1.8]
    frames = [
        synthesize_strain(influence, LOADS_TRUE * s, noise_rel=NOISE_REL, seed=100 + i)[0]
        for i, s in enumerate(scales)
    ]
    report = monitor.run_stream(frames)
    assert report["n_frames"] == len(scales)
    assert len(monitor.history) == len(scales)
    sfs = [r["safety_factor"] for r in report["records"]]
    assert sfs == sorted(sfs, reverse=True)  # monotonically degrading
    assert report["n_alarms"] >= 2
    assert report["n_failures"] >= 1
    assert report["max_cycle_ms"] < 100.0
    trend = monitor.trend()
    assert trend["degradation"] > 0.0

    frame = monitor.update(frames[0])
    assert 0.0 < frame["total_ice_load_n"] < np.sum(LOADS_TRUE) * 1.5
    assert frame["safety"]["level"] in ("SAFE", "MARGINAL", "FAIL")
    monitor.reset()
    assert monitor.history == []


# ---------------------------------------------------------------------------
# 9. Robust validation of hostile inputs
# ---------------------------------------------------------------------------


def test_invalid_inputs_are_rejected(model, influence, noisy_data):
    eps, _ = noisy_data
    with pytest.raises(ValueError):  # wrong gauge count
        invert_ice_load(model, eps[:-1], LOAD_POSITIONS, GAUGES, influence=influence)
    with pytest.raises(ValueError):  # underdetermined: 4 unknowns, 3 gauges
        invert_ice_load(model, eps[:3], LOAD_POSITIONS, GAUGES[:3])
    with pytest.raises(ValueError):  # negative regularisation weight
        invert_ice_load(model, eps, LOAD_POSITIONS, GAUGES, lambda_selection="fixed",
                        lam=-1.0, influence=influence)
    with pytest.raises(ValueError):  # unknown selection strategy
        invert_ice_load(model, eps, LOAD_POSITIONS, GAUGES,
                        lambda_selection="magic", influence=influence)
    with pytest.raises(ValueError):  # load position off the mesh nodes
        model.build_influence_matrix([1.03, 2.0, 3.0, 4.0], GAUGES)
    with pytest.raises(ValueError):  # gauge outside the girder
        model.build_influence_matrix(LOAD_POSITIONS, [-0.1, 1.0, 2.0])


# ---------------------------------------------------------------------------
# 10. HTTP endpoint
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def api_client():
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from api.ship_ice_inversion import router

    app = fastapi.FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def test_api_endpoint_synthetic_mode(api_client):
    resp = api_client.post(
        "/api/v1/calculations/ice-load-inversion",
        json={"loads_true_n": LOADS_TRUE.tolist(), "noise_rel": 0.05, "seed": SEED},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    inv = body["inversion"]
    assert len(inv["loads_inverted_n"]) == len(LOADS_TRUE)
    assert inv["amplification_factor"] < 2.0
    assert inv["load_uncertainty"] < 0.10
    assert body["safety_factor"] > 0.0
    assert body["safety"]["level"] in ("SAFE", "MARGINAL", "FAIL")
    assert body["provenance"]["formulation"] == "hermite_cubic_euler_bernoulli"
    assert len(inv["lcurve"]["lambda"]) >= 10


def test_api_accepts_microstrain_measurements(api_client):
    influence = IceBeltFrameModel(LENGTH_M, N_ELEMENTS).build_influence_matrix(
        LOAD_POSITIONS, GAUGES
    )
    eps, sigma = synthesize_strain(influence, LOADS_TRUE, noise_rel=NOISE_REL, seed=SEED)
    resp = api_client.post(
        "/api/v1/calculations/ice-load-inversion",
        json={
            "strain_meas": (eps * 1e6).tolist(),
            "sigma_strain": (sigma * 1e6).tolist(),
            "load_positions_m": LOAD_POSITIONS,
            "gauge_positions_m": GAUGES,
            "lambda_selection": "l_curve",
            "n_mc": 0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["inversion"]["lambda_selection"] == "l_curve"
    # microstrain round-trip must land on the same inversion as the SI path
    direct = invert_ice_load(
        IceBeltFrameModel(LENGTH_M, N_ELEMENTS), eps, LOAD_POSITIONS, GAUGES,
        sigma_strain=sigma,
    )
    assert np.allclose(
        body["inversion"]["loads_inverted_n"], direct.loads_inverted_n, rtol=1e-6
    )


def test_api_rejects_inconsistent_requests(api_client):
    bad = api_client.post(
        "/api/v1/calculations/ice-load-inversion", json={"strain_meas": [1.0, 2.0]}
    )
    assert bad.status_code == 422
    unknown_grade = api_client.post(
        "/api/v1/calculations/ice-load-inversion",
        json={"loads_true_n": LOADS_TRUE.tolist(), "steel_grade": "NV_Z99"},
    )
    assert unknown_grade.status_code == 422
    info = api_client.get("/api/v1/calculations/ice-load-inversion/info")
    assert info.status_code == 200
    assert info.json()["acceptance_bands"]["SAFE"] == "SF >= 1.5"