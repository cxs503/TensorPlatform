"""Tests for the GCI / observed-order verification toolkit (src/ship_verification/gci.py).

Two layers:

* synthetic (manufactured) refinement sequences with a *known* order of accuracy -
  these prove the estimator itself is correct, so a failing audit result cannot be
  blamed on the tool;
* the two real references audited in ``gci_studies`` - the RK4 MMG time integration
  (order 4 expected) and the Gauss-Legendre Michell quadrature (spectral, i.e. must be
  converged to the round-off floor at production resolution).
"""

from __future__ import annotations

import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ship_verification.gci import (  # noqa: E402
    FS_TRIPLET,
    FS_TWO_LEVEL,
    gci_study,
    observed_order,
    richardson_extrapolate,
    study_from_function,
)

# ---------------------------------------------------------------------------
# 1. estimator correctness on manufactured sequences
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("p_true", [1.0, 2.0, 3.0, 4.0])
def test_observed_order_recovered(p_true):
    exact, c = 1.7, 0.3
    res = study_from_function(lambda h: exact + c * h ** p_true, [0.4, 0.2, 0.1])
    assert res.regime == "monotone"
    assert res.observed_order == pytest.approx(p_true, rel=1e-6)
    assert res.extrapolated == pytest.approx(exact, abs=1e-10)
    assert res.asymptotic_ratio == pytest.approx(1.0, abs=0.02)
    assert res.safety_factor == FS_TRIPLET
    assert res.converged is True
    # GCI must bracket the true error, i.e. exceed |f_fine - exact|
    true_err_pct = abs(res.values[0] - exact) / exact * 100.0
    assert res.gci_fine_pct > true_err_pct


def test_richardson_extrapolate_matches_closed_form():
    # f(h) = 1 + 2 h^2 -> f(0) = 1 exactly
    assert richardson_extrapolate(1.02, 1.08, 2.0, 2.0) == pytest.approx(1.0, abs=1e-12)


def test_unequal_refinement_ratios():
    res = study_from_function(lambda h: 2.0 + 5.0 * h ** 2, [0.3, 0.2, 0.05])
    assert res.steps == pytest.approx([0.05, 0.2, 0.3])
    assert res.refinement_ratios == pytest.approx([4.0, 1.5])
    assert res.observed_order == pytest.approx(2.0, rel=1e-6)
    assert res.converged is True


def test_oscillatory_sequence_flagged_not_trusted():
    # increments change sign: convergence is oscillatory -> no reliable order
    res = gci_study([0.4, 0.2, 0.1], [1.01, 0.998, 1.0004])
    assert res.regime == "oscillatory"
    assert res.oscillatory is True
    assert res.observed_order is None
    assert res.converged is False


def test_roundoff_floor_guard():
    res = gci_study([0.4, 0.2, 0.1], [2.5, 2.5, 2.5])
    assert res.roundoff_dominated is True
    assert res.regime == "roundoff"
    assert res.gci_fine_pct == 0.0
    assert res.converged is True  # converged below the double-precision floor


def test_diverging_sequence_not_converged():
    # error grows under refinement (h^+1 divergence)
    res = study_from_function(lambda h: 1.0 + 0.5 / h, [0.4, 0.2, 0.1])
    assert res.converged is False
    assert res.observed_order is None
    assert res.regime in {"diverging", "no_order_estimate"}


def test_two_level_assumed_order_uses_fs_three():
    # second-order data, but only two levels -> p must be supplied
    res = gci_study([0.2, 0.1], [1.2000, 1.2125], assumed_order=4.0)
    assert res.safety_factor == FS_TWO_LEVEL
    assert res.observed_order is None
    assert res.assumed_order == 4.0
    # finest level is h = 0.1 -> f = 1.2125; Fs = 3, r = 2, p = 4
    assert res.gci_fine_pct == pytest.approx(
        3.0 * abs((1.2125 - 1.2) / 1.2125) / (2.0 ** 4 - 1) * 100.0, rel=1e-12)
    assert res.converged is True


def test_tolerance_verdict_rejects_coarse_study():
    # p = 1 with a big step halving: GCI well above 5 %
    res = study_from_function(lambda h: 1.0 + 0.4 * h, [0.5, 0.25, 0.125], tolerance_pct=5.0)
    assert res.observed_order == pytest.approx(1.0, rel=1e-9)
    assert res.gci_fine_pct > 5.0
    assert res.converged is False


def test_observed_order_helper_signature():
    # f1 must be the FINEST level: h = 0.02, 0.04, 0.08 halves towards coarser
    p, regime, msgs = observed_order(1.0 + 0.3 * 0.02, 1.0 + 0.3 * 0.04, 1.0 + 0.3 * 0.08,
                                    2.0, 2.0)
    assert regime == "monotone"
    assert p == pytest.approx(1.0, rel=1e-6)  # linear in h here
    assert isinstance(msgs, list)


def test_result_is_json_serialisable():
    res = study_from_function(lambda h: 1.0 + h ** 2, [0.4, 0.2, 0.1])
    blob = json.dumps(res.to_dict())
    assert json.loads(blob)["observed_order"] == pytest.approx(2.0, rel=1e-6)


# ---------------------------------------------------------------------------
# 2. the real references
# ---------------------------------------------------------------------------
def test_mmg_rk4_recovers_fourth_order():
    """RK4 on the MMG system must show p ~ 4 and a negligible GCI."""
    from src.ship_verification.gci_studies import mmg_timestep_study

    study = mmg_timestep_study(steps=(0.16, 0.08, 0.04))
    res = study["studies"]["x_at_tau40_m"]["finest_triplet"]
    assert res["regime"] == "monotone"
    assert res["observed_order"] == pytest.approx(4.0, abs=0.6)
    assert res["asymptotic_ratio"] == pytest.approx(1.0, abs=0.05)
    assert res["gci_fine_pct"] < 0.01
    assert res["converged"] is True


def test_mmg_shipped_step_gci_is_negligible():
    """The production step dtau = 0.01 carries no meaningful discretisation error."""
    from src.ship_verification.gci_studies import mmg_timestep_study

    study = mmg_timestep_study(steps=(0.02, 0.01))
    res = study["studies"]["tactical_diameter_m"]["production_step"]
    assert res["gci_fine_pct"] < 1e-3          # per cent
    assert res["converged"] is True


def test_michell_reference_is_converged_at_production_resolution():
    """Gauss-Legendre theta quadrature is at the round-off floor by n_theta = 200."""
    from src.ship_verification.gci_studies import _import_michell

    mod = _import_michell()
    hull = mod.Wigley()
    U = 0.30 * math.sqrt(hull.g * hull.L)
    rw_200 = mod.rw_theta_integral(hull, U, n_theta=200)
    rw_400 = mod.rw_theta_integral(hull, U, n_theta=400)
    assert abs(rw_200 - rw_400) / rw_400 < 1e-10

    res = gci_study([1 / 100.0, 1 / 200.0, 1 / 400.0],
                    [mod.rw_theta_integral(hull, U, n_theta=100), rw_200, rw_400])
    assert res.regime == "roundoff"
    assert res.converged is True
    assert res.gci_fine_pct == 0.0


def test_michell_assumed_order_is_conservative():
    """The hard-coded p = 4 in michell_rw_converged is conservative (spectral decay)."""
    from src.ship_verification.gci_studies import michell_quadrature_study

    res = michell_quadrature_study(fn=0.30, n_sweep=(16, 32, 64), n_reference=400)
    assert res["effective_order"] > 4.0
    assert res["assumed_p_is_conservative"] is True
    assert res["richardson_check"]["correction_rel"] < 1e-12