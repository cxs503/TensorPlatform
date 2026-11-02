"""Gates for the literature verification of the shipped CMA-ES kernel.

Run: ``python -m pytest tests/test_cmaes_literature_verification.py -q``
The numeric thresholds are the acceptance gates documented in
``docs/cmaes_literature_verification.md``.
"""
from __future__ import annotations

import numpy as np
import pytest

from optimizer.core.cmaes import CMAES
from optimizer.verification.cmaes_reference import BENCHMARKS, hansen2016_params
from optimizer.verification import cmaes_literature_verification as V


@pytest.mark.parametrize("dim", [2, 5, 10, 20])
def test_strategy_parameters_match_hansen2016(dim):
    """C1: closed-form parameters of Hansen (2016) eqs. (49)-(55), machine precision."""
    res = V.check_strategy_parameters(dims=(dim,))
    assert res["pass"], res
    assert res["worst_rel_err"] < 1e-12


def test_sampling_distribution_matches_declared_covariance():
    """C2: samples are N(mean, sigma^2 C); empirical covariance within 3% (Frobenius)."""
    res = V.check_sampling_fidelity()
    assert res["pass"], res
    assert res["rel_fro_err"] < 0.03


@pytest.mark.parametrize("name,dim,target", [
    ("sphere", 2, 1e-12),
    ("sphere", 5, 1e-10),
    ("ellipsoid1e6", 2, 1e-10),
    ("rosenbrock", 2, 1e-10),
])
def test_reaches_analytic_optimum_within_reference_efficiency(name, dim, target):
    """C3: hits the analytic optimum, at most 3x the evaluations of the independent reference."""
    res = V.check_convergence(cases=[(name, dim, target, 40000)])
    row = res["rows"][0]
    assert row["shipped_reached"], row
    assert row["ratio_shipped_over_reference"] <= 3.0, row


def test_rotation_invariance():
    """C4: Hansen (2016) sec. 3.3 -- invariance to rotation of the search space."""
    res = V.check_rotation_invariance()
    assert res["pass"], res


def test_reproducible_under_fixed_seed():
    """C5: identical seed -> bit-identical trajectory."""
    res = V.check_reproducibility()
    assert res["pass"], res


def test_covariance_stays_positive_semidefinite():
    """C6: C is a covariance matrix; min eigen-value must not go negative."""
    res = V.check_covariance_psd()
    assert res["pass"], res


def test_indefinite_covariance_is_repaired_not_isotropised():
    """C7: regression guard for the silent isotropic fallback that used to discard C."""
    res = V.check_psd_repair_guard()
    assert res["pass"], res


def test_samples_and_mean_respect_box():
    """C8: box constraints honoured for samples and for the distribution mean."""
    res = V.check_bounds_respected()
    assert res["pass"], res


def test_rank_one_correction_uses_pre_update_covariance():
    """C9: the (1-hsig) rank-one correction must scale C_old (Hansen 2016 eq. 47).

    Deterministic construction: a strongly anisotropic C plus a fitness that
    pulls the mean far away makes the path-length test fail (hsig = False), so
    the correction term is active. The post-update trace of C is then compared
    with eq. (47) evaluated *by hand* from the pre-update state.
    """
    n, lam = 4, 8
    k = CMAES(n, np.tile([-100.0, 100.0], (n, 1)), sigma0=1.0, seed=0, pop_size=lam)
    k.C = np.diag([10.0, 0.1, 1.0, 1.0])
    pop = k.sample_population()
    fit = np.array([float(np.sum((x - 6.0) ** 2)) for x in pop])

    mean_old, C_old, pc_old, sigma_old = k.mean.copy(), k.C.copy(), k.pc.copy(), k.sigma
    sel = pop[np.argsort(fit)[: k.mu]]
    mean_new = np.dot(k.weights, sel)
    y = (mean_new - mean_old) / sigma_old

    k.update(pop, fit)

    # hsig as recomputed independently for this generation
    ps = (1 - k.cs) * np.zeros(n) + np.sqrt(k.cs * (2 - k.cs) * k.mueff) * y
    hsig = (np.linalg.norm(ps) / np.sqrt(1 - (1 - k.cs) ** 2)
            < (1.4 + 2 / (n + 1)) * k.chiN)
    assert not hsig, "test construction must exercise the hsig=False branch"

    pc_new = (1 - k.cc) * pc_old + hsig * np.sqrt(k.cc * (2 - k.cc) * k.mueff) * y
    ys = (sel - mean_old) / sigma_old
    rank_mu = sum(k.weights[i] * np.outer(ys[i], ys[i]) for i in range(k.mu))
    C_ref = ((1 - k.c1 - k.cmu) * C_old
             + k.c1 * (np.outer(pc_new, pc_new) + (1 - hsig) * k.cc * (2 - k.cc) * C_old)
             + k.cmu * rank_mu)
    C_ref = (C_ref + C_ref.T) / 2

    np.testing.assert_allclose(k.C, C_ref, rtol=1e-12, atol=1e-14)
