"""Acceptance locks for the PCE quadrature-aliasing defect of the ice UQ module.

Task m1882 (independent numerical review) located the defect: with the shipped
default ``max_level=3`` / ``max_poly_order=3`` the Smolyak rule integrates total
degree ``2L-1 = 5`` only, while resolving ``<psi_a, psi_b>`` for an order-3 basis
needs degree 6.  On the pure cubic response ``y = xi_0^3`` with an inert second
input ``xi_1`` the projection therefore reported

    S_T(xi_1) = 0.1818 (analytic 0)   and   Var = 11 (analytic 15)

i.e. 18.2 % of the total-order sensitivity was handed to an input with *zero*
influence, and that configuration was what ``/ice-uq/uq`` and ``/ice-uq/uq/all``
served by default.  Task m1892 fixes it at the source and locks it here:

  L1  counter-example: default entry point -> S_T(inert) <= 1e-6, Var = 15;
  L2  the guard: ``max_level < max_poly_order + 1`` is raised (default) or
      rejected with ``PCEQuadratureAliasingError`` (``policy='error'``) and the
      raw aliased numbers stay reproducible only via ``'allow_aliasing'``;
  L3  pipeline defaults: ``run_all_ships_uq`` / ``run_ice_resistance_uq`` are
      aliasing-free and report the level actually used;
  L4  the support of the sampled law is *declared at the entry point*
      (``TruncationSpec`` / ``ICE_INPUT_TRUNCATION``) instead of being clipped
      against four hard-coded, index-keyed bounds inside ``monte_carlo_reference``.

Run: python3 -m pytest tests/test_m1892_pce_aliasing.py -q
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (os.path.join(ROOT, "backend"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services import ice_resistance_uq as UQ  # noqa: E402


# --------------------------------------------------------------------- helpers
def _cubic_inputs():
    # nominal 1.0 with std_relative 1.0 -> standard coordinate xi = x - 1
    return [UQ.UncertainInput("xi0", nominal=1.0, std_relative=1.0),
            UQ.UncertainInput("xi1", nominal=1.0, std_relative=1.0)]


def _cubic(x0, x1):
    return (x0 - 1.0) ** 3  # xi0^3; xi1 provably has no influence


def _double_factorial_steps(p):
    """E[xi^p] for xi ~ N(0,1) and even p, 0 for odd p."""
    if p % 2:
        return 0.0
    value = 1.0
    for k in range(p - 1, 0, -2):
        value *= k
    return value


# --------------------------------------------------------------------- L1
def test_l1_cubic_counter_example_default_entry_point():
    """m1882 counter-example: inert input must get <= 1e-6 total-order index."""
    pce = UQ.build_pce(_cubic, _cubic_inputs())  # production defaults
    sobol = UQ.sobol_indices(pce)
    stats = UQ.pce_statistics(pce)

    assert pce["aliasing_free"] is True
    assert pce["max_level"] >= pce["max_poly_order"] + 1
    assert sobol["total_order"]["xi1"] <= 1e-6, sobol["total_order"]
    assert sobol["first_order"]["xi1"] <= 1e-6, sobol["first_order"]
    assert stats["variance"] == pytest.approx(15.0, abs=1e-9)
    assert stats["mean"] == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("order", [1, 2, 3, 4])
def test_l1_monomial_response_variance_matches_analytic(order):
    """Var(xi^p) = (2p-1)!! - ((p-1)!!)^2 must be exact for p <= max order."""
    inputs = _cubic_inputs()

    def model(x0, x1):
        return (x0 - 1.0) ** order

    pce = UQ.build_pce(model, inputs, max_poly_order=order)
    stats = UQ.pce_statistics(pce)
    sobol = UQ.sobol_indices(pce)

    mean_analytic = _double_factorial_steps(order)
    var_analytic = _double_factorial_steps(2 * order) - mean_analytic ** 2
    assert stats["mean"] == pytest.approx(mean_analytic, abs=1e-9)
    assert stats["variance"] == pytest.approx(var_analytic, rel=1e-10, abs=1e-10)
    assert sobol["total_order"]["xi1"] <= 1e-6
    assert sum(sobol["total_order"].values()) == pytest.approx(1.0, abs=1e-10)


# --------------------------------------------------------------------- L2
def test_l2_required_level_is_poly_order_plus_one():
    for p in range(0, 6):
        required = UQ.required_quadrature_level(p)
        assert required == p + 1
        assert 2 * required - 1 >= 2 * p           # Gram matrix resolved
        assert 2 * (required - 1) - 1 < 2 * p      # ... and it is the minimum


def test_l2_auto_raise_reports_the_level_it_used():
    pce = UQ.build_pce(_cubic, _cubic_inputs(), max_level=3, max_poly_order=3)
    assert pce["max_level_requested"] == 3
    assert pce["max_level"] == 4
    assert pce["quadrature_raised"] is True
    assert pce["quadrature_exactness_degree"] == 7
    assert pce["required_exactness_degree"] == 6


def test_l2_error_policy_rejects_the_aliasing_regime():
    with pytest.raises(UQ.PCEQuadratureAliasingError) as exc:
        UQ.build_pce(_cubic, _cubic_inputs(), max_level=3, max_poly_order=3,
                     quadrature_policy="error")
    assert "max_level" in str(exc.value) and "4" in str(exc.value)


def test_l2_unknown_policy_is_rejected():
    with pytest.raises(ValueError):
        UQ.build_pce(_cubic, _cubic_inputs(), max_level=2, max_poly_order=3,
                     quadrature_policy="whatever")


def test_l2_aliasing_evidence_stays_reproducible_but_opt_in():
    """The m1882 numbers must stay reproducible -- through an explicit bypass."""
    raw = UQ.build_pce(_cubic, _cubic_inputs(), max_level=3, max_poly_order=3,
                       quadrature_policy="allow_aliasing")
    assert raw["aliasing_free"] is False
    assert UQ.pce_statistics(raw)["variance"] == pytest.approx(11.0, abs=1e-6)
    assert UQ.sobol_indices(raw)["total_order"]["xi1"] == pytest.approx(0.181818, abs=1e-6)


# --------------------------------------------------------------------- L3
@pytest.fixture(scope="module")
def all_ships():
    return UQ.run_all_ships_uq()


def test_l3_run_all_ships_defaults_are_aliasing_free(all_ships):
    for name, result in all_ships.items():
        assert result.quadrature_aliasing_free is True, name
        assert result.max_level is not None and result.max_poly_order is not None
        assert result.max_level >= result.max_poly_order + 1, name
        assert result.max_level == UQ.PCE_DEFAULT_MAX_LEVEL
        assert result.max_poly_order == UQ.PCE_DEFAULT_MAX_POLY_ORDER
        # 4-D Smolyak grid at the aliasing-free level
        assert result.n_model_evaluations == 165, (name, result.n_model_evaluations)


def test_l3_run_uq_statistics_still_track_the_mc_reference(all_ships):
    """The fix must not degrade the statistics the endpoints publish."""
    for name, result in all_ships.items():
        assert result.mean_error is not None and result.mean_error < 0.05, name
        assert result.std_error is not None and result.std_error < 0.10, name
        assert result.ci_coverage is not None and result.ci_coverage > 0.90, name
        assert result.sobol_sum_first_order <= 1.0 + 1e-6
        assert result.sobol_sum_total_order >= 1.0 - 1e-6


def test_l3_legacy_default_is_repaired_not_honoured():
    """An explicit legacy (3, 3) request - what the API model still sends - is safe."""
    result = UQ.run_ice_resistance_uq(
        "MV_Arctic", max_level=3, max_poly_order=3,
        n_mc_validation=500, n_mc_reference=5000,
    )
    assert result.quadrature_aliasing_free is True
    assert result.max_level == 4


# --------------------------------------------------------------------- L4
def test_l4_declared_truncation_is_reported_by_the_pipeline():
    result = UQ.run_ice_resistance_uq(
        "MV_Arctic", n_mc_validation=500, n_mc_reference=20000,
    )
    trunc = result.distribution_truncation
    assert trunc is not None and trunc["truncated_distribution"] is True
    assert trunc["truncation_mode"] == "reject"          # no probability atoms
    assert trunc["max_mass_outside"] < 1e-3              # declared mass is small
    assert [row["input"] for row in trunc["per_input"]] == [
        "ice_strength_MPa", "ice_thickness_m", "friction_coeff", "ship_speed_ms"
    ]
    for row in trunc["per_input"]:
        assert row["rationale"], row
        assert 0.0 <= row["mass_outside"] < 1e-2, row


def test_l4_bounds_are_declared_at_the_entry_point():
    names = [spec.input_name for spec in UQ.ICE_INPUT_TRUNCATION]
    assert names == ["ice_strength_MPa", "ice_thickness_m", "friction_coeff", "ship_speed_ms"]
    friction = UQ.ICE_INPUT_TRUNCATION[2]
    assert (friction.lower, friction.upper) == (0.02, 0.5)
    assert all(spec.is_bounded() for spec in UQ.ICE_INPUT_TRUNCATION)


def test_l4_monte_carlo_reference_does_not_clip_behind_the_callers_back():
    """Without a declared support the sampler must be a plain Gaussian.

    The legacy code clipped ``d == 0`` at 0.3, which biases E[x] for a
    N(1, 1) input by +0.14 and shrinks the std by ~7 %.
    """
    inputs = [UQ.UncertainInput("x", nominal=1.0, std_relative=1.0)]
    mc = UQ.monte_carlo_reference(lambda x: x, inputs, n_samples=20000, seed=7)
    assert mc["truncation"]["truncated_distribution"] is False
    assert mc["truncation"]["truncation_mode"] == "none"
    assert mc["mean"] == pytest.approx(1.0, abs=0.05)
    assert mc["std"] == pytest.approx(1.0, abs=0.05)


def test_l4_reject_mode_honours_declared_support_without_atoms():
    inputs = [UQ.UncertainInput("x", nominal=1.0, std_relative=1.0)]
    spec = [UQ.TruncationSpec("x", lower=0.5, upper=math.inf, rationale="test")]
    mc = UQ.monte_carlo_reference(
        lambda x: x, inputs, n_samples=20000, seed=11, truncation=spec,
    )
    diag = mc["truncation"]
    assert diag["truncated_distribution"] is True
    assert diag["truncation_mode"] == "reject"
    assert diag["n_redrawn"] > 0
    # exact truncated normal: E[X | X > 0.5] = mu + sigma*phi(a)/(1-Phi(a)), a = -0.5
    a = (0.5 - 1.0) / 1.0
    ratio = math.exp(-0.5 * a * a) / math.sqrt(2 * math.pi) / (1.0 - 0.5 * (1 + math.erf(a / math.sqrt(2))))
    assert mc["mean"] == pytest.approx(1.0 + ratio, abs=0.02)
    assert mc["mean"] > 1.0  # clipped-at-bound atoms would give a lower mean


def test_l4_clip_mode_is_explicit_and_quantified():
    inputs = [UQ.UncertainInput("x", nominal=1.0, std_relative=1.0)]
    spec = [UQ.TruncationSpec("x", lower=0.5, upper=math.inf, rationale="test")]
    mc = UQ.monte_carlo_reference(
        lambda x: x, inputs, n_samples=20000, seed=13,
        truncation=spec, truncation_mode="clip",
    )
    diag = mc["truncation"]
    assert diag["truncation_mode"] == "clip"
    assert diag["per_input"][0]["realized_outside_fraction"] == pytest.approx(0.31, abs=0.02)
    assert diag["per_input"][0]["mass_outside"] == pytest.approx(0.3085, abs=1e-3)


def test_l4_mismatched_truncation_declaration_is_rejected():
    inputs = _cubic_inputs()
    with pytest.raises(ValueError):
        UQ.monte_carlo_reference(
            lambda x0, x1: x0, inputs, n_samples=100,
            truncation=[UQ.TruncationSpec("nope", 0.0, 1.0)],
        )
    with pytest.raises(ValueError):
        UQ.monte_carlo_reference(
            lambda x0, x1: x0, inputs, n_samples=100,
            truncation=[UQ.TruncationSpec("xi0", 0.0, 1.0)],
        )


# --------------------------------------------------------------------- status surface
def test_status_surface_reports_the_fix():
    status = UQ.pce_numerics_status()
    assert status["verdict_aliasing_fixed"] is True
    assert status["guarded"]["aliasing_free"] is True
    assert status["guarded"]["S_T_inert"] <= 1e-6
    assert status["guarded"]["variance"] == pytest.approx(15.0, abs=1e-9)
    assert status["guarded"]["variance_rel_err"] < 1e-12
    assert status["aliased_reference"]["variance"] == pytest.approx(11.0, abs=1e-6)
    assert status["aliased_reference"]["S_T_inert"] == pytest.approx(0.181818, abs=1e-6)
    assert status["max_truncation_mass"] < 1e-3
    assert UQ.PCE_NUMERICS_STATUS["evidence_grade"] == "REAL"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

# --------------------------------------------------------------------- endpoint wiring
def test_api_endpoint_reads_reference_aliases_and_serves_fixed_numbers():
    """``POST /ice-uq/uq`` must build its response from the UQ result.

    The endpoint reads ``result.reference_mean_kN`` / ``result.reference_std_kN``,
    which did not exist on ``IceResistanceUQResult`` -- every call raised
    AttributeError (HTTP 500) before the aliases were added in m1892.  The call
    below also checks that the served Sobol indices are the aliasing-free ones.
    """
    pytest.importorskip("fastapi")
    import asyncio
    import importlib.util

    path = os.path.join(ROOT, "backend", "api", "domain", "ice_uq.py")
    spec = importlib.util.spec_from_file_location("ice_uq_api_m1892", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ice_uq_api_m1892"] = module  # needed for pydantic annotation rebuild
    spec.loader.exec_module(module)

    request = module.UQRequest(ship_name="MV_Arctic", n_mc_validation=300)
    response = asyncio.run(module.run_uq_analysis(request))

    assert response.ship_name == "MV_Arctic"
    assert response.reference_mean_kN is not None
    assert response.reference_std_kN is not None
    assert response.variance_kN2 > 0.0
    assert abs(sum(response.sobol_total_order.values()) - 1.0) < 0.05
    assert response.n_model_evaluations == 165  # level 4 -> aliasing-free grid
