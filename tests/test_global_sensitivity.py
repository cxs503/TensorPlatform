"""Verification suite for the variance-based GSA (Sobol') capability.

What this suite proves
----------------------
1. **Sampling design** -- Saltelli pick-freeze structure, determinism, bounds,
   evaluation cost ``(d+2)N``, input validation.
2. **Estimators against analytic indices** -- Ishigami & Homma (1990) and the
   Sobol' (1993) g-function, both scored against closed-form values, including
   the case that separates the sampling route from the spectral route
   (``x3`` in Ishigami: ``S1 = 0`` but ``S_T = 0.2439``).
3. **Cross-implementation check of the already-merged PCE path** --
   ``backend/services/ice_resistance_uq.py::{build_pce, sobol_indices}`` is
   scored against the closed-form decomposition of a Gaussian polynomial model
   (exactness check of merged code, not of new code).
4. **Machine-checked invariants** -- ``S_T >= S1``, ``sum S1 <= 1``, additive
   models give ``S1 == S_T``, constant responses give zero indices without NaNs.
5. **Tool wiring** -- ``tools/gsa_benchmark.py`` regenerates the artifact JSON
   and the three report figures.

References
----------
Saltelli et al. (2010) CPC 181, 259-270; Jansen (1999) CPC 117, 35-43;
Ishigami & Homma (1990) ISUMA; Sobol' (1993) Math. Model. Comput. Exp. 1.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.services.global_sensitivity import (  # noqa: E402
    SaltelliSamples,
    ishigami,
    ishigami_reference,
    saltelli_sample,
    sobol_convergence,
    sobol_g,
    sobol_g_reference,
    sobol_indices,
)

# ---------------------------------------------------------------------------
# 1. Sampling design
# ---------------------------------------------------------------------------


def test_sample_shapes_and_pick_freeze_structure():
    design = saltelli_sample([[0.0, 1.0], [-2.0, 3.0], [10.0, 20.0]], n_base=32, seed=1)
    assert isinstance(design, SaltelliSamples)
    assert design.dim == 3
    assert design.n_base == 32
    assert design.A.shape == (32, 3) and design.B.shape == (32, 3)
    assert design.AB.shape == (3, 32, 3)
    for i in range(design.dim):
        # column i of AB[i] comes from B, every other column from A
        np.testing.assert_array_equal(design.AB[i][:, i], design.B[:, i])
        for j in range(design.dim):
            if j != i:
                np.testing.assert_array_equal(design.AB[i][:, j], design.A[:, j])


def test_sample_respects_bounds_and_is_deterministic():
    bounds = [[0.0, 1.0], [-5.0, -1.0], [100.0, 101.0]]
    d1 = saltelli_sample(bounds, n_base=64, seed=7)
    d2 = saltelli_sample(bounds, n_base=64, seed=7)
    d3 = saltelli_sample(bounds, n_base=64, seed=8)
    np.testing.assert_array_equal(d1.A, d2.A)  # same seed -> bitwise identical
    np.testing.assert_array_equal(d1.B, d2.B)
    assert not np.allclose(d1.A, d3.A)
    for col, (lo, hi) in enumerate(bounds):
        assert d1.A[:, col].min() >= lo and d1.A[:, col].max() <= hi
        assert d1.B[:, col].min() >= lo and d1.B[:, col].max() <= hi


def test_sample_cost_is_d_plus_2_times_n():
    design = saltelli_sample([[0.0, 1.0]] * 4, n_base=100, seed=0)
    assert design.n_evaluations == (4 + 2) * 100


def test_sample_skip_changes_stream_but_keeps_ranges():
    a = saltelli_sample([[0.0, 1.0]] * 2, n_base=16, seed=3, skip=0)
    b = saltelli_sample([[0.0, 1.0]] * 2, n_base=16, seed=3, skip=16)
    assert not np.allclose(a.A, b.A)
    assert a.A.shape == b.A.shape == (16, 2)


@pytest.mark.parametrize(
    "bounds",
    [
        [[1.0, 0.0]],  # upper <= lower
        [[0.0, 1.0, 2.0]],  # not (d, 2)
        [[0.0, float("inf")]],  # non-finite
        [],  # empty
    ],
)
def test_invalid_bounds_raise(bounds):
    with pytest.raises(ValueError):
        saltelli_sample(bounds, n_base=8, seed=0)


def test_invalid_sample_sizes_raise():
    with pytest.raises(ValueError):
        saltelli_sample([[0.0, 1.0]], n_base=1, seed=0)
    with pytest.raises(ValueError):
        saltelli_sample([[0.0, 1.0]], n_base=8, seed=0, skip=-1)


# ---------------------------------------------------------------------------
# 2. Estimators against analytic indices
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ishigami_result():
    ref = ishigami_reference()
    return sobol_indices(ishigami, ref["bounds"], n_base=1 << 16, seed=11, names=ref["names"])


def test_ishigami_first_order_matches_analytic(ishigami_result):
    ref = ishigami_reference()
    err = np.abs(np.asarray(ishigami_result.first_order) - ref["first_order"])
    assert err.max() < 0.01, f"max |dS1| = {err.max():.4f}"


def test_ishigami_total_order_matches_analytic(ishigami_result):
    ref = ishigami_reference()
    err = np.abs(np.asarray(ishigami_result.total_order) - ref["total_order"])
    assert err.max() < 0.01, f"max |dST| = {err.max():.4f}"


def test_ishigami_dummy_input_has_zero_main_effect_but_nonzero_total(ishigami_result):
    """x3 only enters through an interaction: S1 = 0, S_T = 0.2439.

    This is the property a first-order-only method cannot see, and it is the
    reason the total-order estimator is part of the capability.
    """
    S1 = ishigami_result.as_dict()["first_order"]
    ST = ishigami_result.as_dict()["total_order"]
    assert abs(S1["x3"]) < 0.02
    assert ST["x3"] > 0.20


def test_ishigami_variance_estimate_matches_analytic(ishigami_result):
    ref = ishigami_reference()
    assert abs(ishigami_result.variance - ref["variance"]) / ref["variance"] < 0.01


def test_g_function_indices_match_analytic():
    ref = sobol_g_reference()
    res = sobol_indices(sobol_g, ref["bounds"], n_base=1 << 15, seed=13, names=ref["names"])
    dS1 = np.abs(np.asarray(res.first_order) - ref["first_order"])
    dST = np.abs(np.asarray(res.total_order) - ref["total_order"])
    assert dS1.max() < 0.012, f"max |dS1| = {dS1.max():.4f}"
    assert dST.max() < 0.012, f"max |dST| = {dST.max():.4f}"
    assert abs(res.variance - ref["variance"]) / ref["variance"] < 0.02


def test_g_function_ordering_is_preserved():
    """a = [0, 0.5, 3, 9, 99] must come out ranked x1 > x2 > x3 > x4 > x5."""
    ref = sobol_g_reference()
    res = sobol_indices(sobol_g, ref["bounds"], n_base=1 << 13, seed=5)
    order = np.argsort(-np.asarray(res.total_order))
    assert list(order) == [0, 1, 2, 3, 4]


def test_additive_model_first_order_equals_total_order():
    bounds = [[0.0, 1.0]] * 3

    def additive(x):
        return 2.0 * x[..., 0] + 5.0 * x[..., 1] - 1.0 * x[..., 2]

    res = sobol_indices(additive, bounds, n_base=1 << 14, seed=3)
    S1, ST = np.asarray(res.first_order), np.asarray(res.total_order)
    np.testing.assert_allclose(S1, ST, atol=0.03)
    # analytic: variances 4 : 25 : 1 over 30
    np.testing.assert_allclose(S1, np.array([4.0, 25.0, 1.0]) / 30.0, atol=0.03)
    assert abs(S1.sum() - 1.0) < 0.02


def test_constant_response_gives_zero_indices_without_nan():
    res = sobol_indices(lambda x: np.full(x.shape[0], 7.0) if np.ndim(x) > 1 else 7.0,
                        [[0.0, 1.0]] * 2, n_base=64, seed=0)
    assert res.variance == 0.0
    assert np.all(np.isfinite(res.first_order)) and np.all(res.first_order == 0.0)
    assert np.all(np.isfinite(res.total_order)) and np.all(res.total_order == 0.0)


def test_scalar_signature_callable_is_supported():
    """A callable that only accepts scalars is evaluated row by row."""
    bounds = [[0.0, 1.0]] * 2

    def scalar_only(a, b):
        return float(a) + float(b)  # fails on arrays -> row-loop fallback

    res = sobol_indices(scalar_only, bounds, n_base=1 << 14, seed=2)
    np.testing.assert_allclose(res.first_order, [0.5, 0.5], atol=0.03)
    assert res.n_evaluations == 4 * (1 << 14)


def test_indices_are_bitwise_deterministic():
    ref = sobol_g_reference()
    a = sobol_indices(sobol_g, ref["bounds"], n_base=1 << 10, seed=99)
    b = sobol_indices(sobol_g, ref["bounds"], n_base=1 << 10, seed=99)
    np.testing.assert_array_equal(a.first_order, b.first_order)
    np.testing.assert_array_equal(a.total_order, b.total_order)
    assert a.n_evaluations == b.n_evaluations


def test_names_and_confidence_validation():
    with pytest.raises(ValueError):
        sobol_indices(sobol_g, [[0.0, 1.0]] * 5, n_base=16, seed=0, names=["a", "b"])
    with pytest.raises(ValueError):
        sobol_indices(sobol_g, [[0.0, 1.0]] * 5, n_base=16, seed=0, confidence=1.5)
    with pytest.raises(ValueError):
        sobol_indices(sobol_g, [[0.0, 1.0]] * 5, n_base=16, seed=0, n_boot=-3)


def test_bootstrap_half_widths_shrink_with_sample_size():
    ref = sobol_g_reference()
    small = sobol_indices(sobol_g, ref["bounds"], n_base=1 << 9, seed=21, n_boot=80)
    large = sobol_indices(sobol_g, ref["bounds"], n_base=1 << 13, seed=21, n_boot=80)
    assert small.first_order_conf is not None and large.first_order_conf is not None
    assert np.mean(small.total_order_conf) > np.mean(large.total_order_conf)


def test_bootstrap_interval_covers_analytic_value():
    """Analytic S1 must lie inside the 95 % bootstrap band for the influential inputs."""
    ref = sobol_g_reference()
    res = sobol_indices(sobol_g, ref["bounds"], n_base=1 << 13, seed=31, n_boot=200, confidence=0.95)
    S1 = np.asarray(res.first_order)
    conf = np.asarray(res.first_order_conf)
    target = np.asarray(ref["first_order"])
    influential = target > 0.01
    assert np.all(np.abs(S1 - target)[influential] <= 1.5 * conf[influential])


def test_invariants_flag_structure_and_tolerance():
    ref = ishigami_reference()
    res = sobol_indices(ishigami, ref["bounds"], n_base=1 << 13, seed=17)
    inv = res.invariants()
    assert inv["total_ge_first"] and inv["first_order_non_negative"]
    assert inv["sum_first_order_le_one"]
    assert inv["interaction_share"] > 0.4  # Ishigami is strongly non-additive
    # a deliberately broken decomposition must be caught
    broken = type(res)(
        first_order=np.array([0.9, 0.0, 0.0]),
        total_order=np.array([0.1, 0.0, 0.0]),
        names=res.names,
        variance=1.0,
        mean=0.0,
        n_base=res.n_base,
        n_evaluations=res.n_evaluations,
        seed=res.seed,
    )
    assert not broken.invariants(tol=0.0)["total_ge_first"]


def test_error_reporting_against_reference():
    ref = sobol_g_reference()
    res = sobol_indices(sobol_g, ref["bounds"], n_base=1 << 12, seed=41)
    err = res.error_vs(ref)
    assert err["max_abs_first_order"] < 0.03
    assert err["variance_rel_error"] < 0.05
    assert len(err["rel_total_order"]) == 5


def test_reference_generators_are_self_consistent():
    ish = ishigami_reference()
    assert ish["variance"] == pytest.approx(13.8445)
    assert ish["first_order"].sum() < 1.0 < ish["total_order"].sum()
    g = sobol_g_reference()
    terms = 1.0 + 1.0 / (3.0 * (1.0 + np.asarray(g["a"])) ** 2)
    assert g["variance"] == pytest.approx(float(np.prod(terms) - 1.0))
    assert np.all(np.asarray(g["total_order"]) >= np.asarray(g["first_order"]) - 1e-12)
    assert g["first_order"].sum() <= 1.0


def test_g_function_rejects_dim_mismatch():
    with pytest.raises(ValueError):
        sobol_g(np.zeros((4, 3)))


# ---------------------------------------------------------------------------
# 3. Cross-implementation check of the merged PCE spectral path
# ---------------------------------------------------------------------------


def gaussian_polynomial(x1, x2, x3):
    u1, u2, u3 = x1 - 1.0, x2 - 1.0, x3 - 1.0
    return 1.0 + 2.0 * u1 + 0.5 * u2 + 0.3 * u1 * u2 + 0.8 * (u3 ** 2 - 1.0)


GPOLY_S1 = np.array([4.0, 0.25, 1.28]) / 5.62
GPOLY_ST = np.array([4.09, 0.34, 1.28]) / 5.62


def test_merged_pce_path_matches_analytic_indices():
    """Verification debt on *merged* code: the PCE -> Sobol' path was never
    scored against closed-form indices.  It is exact for polynomial responses."""
    from backend.services.ice_resistance_uq import (
        UncertainInput,
        build_pce,
        pce_statistics,
        sobol_indices as pce_sobol,
    )

    ui = [UncertainInput(f"x{i + 1}", 1.0, 1.0) for i in range(3)]
    pce = build_pce(gaussian_polynomial, ui, max_level=4, max_poly_order=2)
    stats = pce_statistics(pce)
    idx = pce_sobol(pce)
    S1 = np.array([idx["first_order"][f"x{i + 1}"] for i in range(3)])
    ST = np.array([idx["total_order"][f"x{i + 1}"] for i in range(3)])
    np.testing.assert_allclose(S1, GPOLY_S1, atol=1e-9)
    np.testing.assert_allclose(ST, GPOLY_ST, atol=1e-9)
    assert stats["variance"] == pytest.approx(5.62, rel=1e-12)


def test_sampling_route_orders_inputs_like_the_pce_route():
    """Independent-route sanity: ranking and the interaction gap must agree.

    The sampling route uses a uniform box while the PCE route carries the exact
    Gaussian measure, so index *values* are not comparable across the two
    measures -- but the ranking of ``S_T`` and the fact that input 1 and 2
    interact (``S_T > S1`` there) must survive the change of measure.
    """
    bounds = [[-3.0, 5.0]] * 3
    res = sobol_indices(gaussian_polynomial, bounds, n_base=1 << 14, seed=51)
    S1, ST = np.asarray(res.first_order), np.asarray(res.total_order)
    assert np.all(ST + 0.02 >= S1)
    assert ST[0] > ST[2] > ST[1]
    assert S1[0] > 0.45
    # the only interaction in the model is x1*x2 -> x3 must be purely main effect
    assert abs(ST[2] - S1[2]) < 0.02


def test_both_first_order_estimators_available_and_close_on_benign_models():
    ref = sobol_g_reference()
    res = sobol_indices(sobol_g, ref["bounds"], n_base=1 << 13, seed=71)
    assert res.first_order_jansen is not None and res.first_order_saltelli2010 is not None
    np.testing.assert_allclose(res.first_order_jansen, res.first_order_saltelli2010, atol=0.05)


def test_estimator_choice_is_selectable_and_validated():
    ref = sobol_g_reference()
    a = sobol_indices(sobol_g, ref["bounds"], n_base=1 << 10, seed=72,
                      first_order_estimator="jansen")
    np.testing.assert_allclose(a.first_order, a.first_order_jansen, atol=1e-12)
    with pytest.raises(ValueError):
        sobol_indices(sobol_g, ref["bounds"], n_base=64, seed=0, first_order_estimator="morris")


def test_heavy_tailed_response_needs_the_jansen_first_order_estimator():
    """Lindqvist ice resistance is heavy-tailed: the Saltelli (2010) product
    first-order estimator overshoots the total index, the Jansen form does not.

    This is a documented, reproducible selection rule, not a numerical accident.
    """
    from backend.services.ice_resistance_uq import SHIP_TRIAL_DATA, lindqvist_ice_resistance

    tr = SHIP_TRIAL_DATA[sorted(SHIP_TRIAL_DATA)[0]]
    ship = tr["params"]
    nominal = [
        float(tr["nominal_ice_thickness_m"]),
        float(tr["nominal_ice_strength_MPa"]),
        float(tr["nominal_friction_coeff"]),
        float(tr["nominal_speed_ms"]),
    ]
    bounds = [[v * 0.8, v * 1.2] for v in nominal]

    def resistance(h, sigma, mu, v):
        return lindqvist_ice_resistance(ship, h, sigma, mu, v)

    names = ["h_ice", "sigma_c", "mu", "V"]
    spreads, jan_ok = [], True
    for seed in (81, 20261102):
        res = sobol_indices(resistance, bounds, n_base=1 << 15, seed=seed, names=names)
        ST = np.asarray(res.total_order)
        S1_salt = np.asarray(res.first_order_saltelli2010)
        S1_jan = np.asarray(res.first_order_jansen)
        # Jansen keeps the physical ordering S1 <= S_T and the sum rule, for both seeds
        jan_ok &= bool(np.all(S1_jan <= ST + 0.03) and S1_jan.sum() <= 1.02)
        spreads.append(float(np.max(np.abs(S1_salt - S1_jan))))
    assert jan_ok, "Jansen first-order estimator must respect S1 <= S_T on a heavy-tailed response"
    # estimator choice is material here, not cosmetic
    assert max(spreads) > 0.02


# ---------------------------------------------------------------------------
# 4. Convergence behaviour
# ---------------------------------------------------------------------------


def test_convergence_errors_decrease():
    ref = sobol_g_reference()
    trace = sobol_convergence(sobol_g, ref["bounds"], n_bases=(256, 65536), seed=61)
    e_small = trace[0].error_vs(ref)["max_abs_total_order"]
    e_large = trace[1].error_vs(ref)["max_abs_total_order"]
    assert e_large < e_small
    assert e_large < 0.01


def test_convergence_costs_follow_the_formula():
    ref = ishigami_reference()
    trace = sobol_convergence(ishigami, ref["bounds"], n_bases=(64, 256), seed=62)
    assert [r.n_evaluations for r in trace] == [5 * 64, 5 * 256]


# ---------------------------------------------------------------------------
# 5. Tool wiring / artifacts
# ---------------------------------------------------------------------------


def test_benchmark_tool_regenerates_artifacts(tmp_path):
    cmd = [
        sys.executable,
        os.path.join(ROOT, "tools", "gsa_benchmark.py"),
        "--outdir",
        str(tmp_path),
        "--skip-application",
    ]
    env = dict(os.environ, MPLBACKEND="Agg")
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stderr[-3000:]
    payload = json.loads((tmp_path / "gsa_sobol_benchmark.json").read_text())
    for key in ("analytic_cases", "pce_crosscheck", "figures", "environment"):
        assert key in payload
    assert len(payload["analytic_cases"]) == 2
    for case in payload["analytic_cases"]:
        assert case["pass_1pct"] is True
        assert case["errors"]["max_abs_total_order"] < 0.01
    assert max(payload["pce_crosscheck"]["pce_error_first_order"]) < 1e-9
    for fig in payload["figures"].values():
        assert (tmp_path / fig).stat().st_size > 5000