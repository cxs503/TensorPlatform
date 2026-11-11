"""Equivalence machinery: duality, Monte-Carlo calibration, MDE behaviour."""
from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from tools.capability_evidence_chain import equivalence as eq


# --------------------------------------------------------------------------
# decision / duality
# --------------------------------------------------------------------------
def test_mean_tost_duality_interval_and_pvalue():
    x = [0.01, 0.02, 0.015, 0.012, 0.018, 0.011, 0.013, 0.016]
    res = eq.tost_mean_one_sample(x, margin=0.05, alpha=0.05)
    assert res.equivalent
    assert res.ci_low > -0.05 and res.ci_high < 0.05
    assert res.p_value < 0.05
    # the dual statement must be exactly equivalent to interval inclusion
    res2 = eq.tost_mean_one_sample(x, margin=0.05, alpha=0.05, compute_sensitivity=False)
    assert (res2.p_value < 0.05) == (res2.ci_low > -0.05 and res2.ci_high < 0.05)


def test_mean_tost_pvalue_matches_two_one_sided_t_tests():
    x = np.array([0.01, 0.05, 0.08, 0.02, 0.06, 0.03])
    res = eq.tost_mean_one_sample(x, margin=0.1, alpha=0.05, compute_sensitivity=False)
    expected = max(
        stats.ttest_1samp(x, 0.1, alternative="less").pvalue,
        stats.ttest_1samp(x, -0.1, alternative="greater").pvalue,
    )
    assert res.p_value == pytest.approx(expected, rel=1e-9, abs=1e-12)


def test_mean_tost_not_equivalent_when_mean_is_outside_margin():
    x = [0.20, 0.21, 0.19, 0.22]
    res = eq.tost_mean_one_sample(x, margin=0.05)
    assert not res.equivalent
    assert res.decision == "NOT_EQUIVALENT"


def test_mean_tost_withheld_without_spread():
    res = eq.tost_mean_one_sample([0.01], margin=0.05)
    assert not res.equivalent
    assert res.decision == "WITHHELD_NO_SPREAD"
    assert res.power is None and res.mde is None


def test_two_sample_tost_identical_streams_equivalent():
    rng = np.random.default_rng(1)
    a = rng.normal(0.02, 0.005, 40)
    b = rng.normal(0.02, 0.005, 40)
    res = eq.tost_mean_two_sample(a, b, margin=0.02, alpha=0.05)
    assert res.equivalent
    assert res.notes["n_a"] == 40 and res.notes["n_b"] == 40


def test_paired_tost_requires_equal_lengths():
    with pytest.raises(ValueError):
        eq.tost_paired([0.1, 0.2], [0.1], margin=0.5)
    res = eq.tost_paired([0.11, 0.12, 0.10], [0.10, 0.11, 0.10], margin=0.05)
    assert res.scenario == "mean_paired"
    assert res.equivalent


# --------------------------------------------------------------------------
# McNemar-free Monte-Carlo validation of the analytic power
# --------------------------------------------------------------------------
def _mc_power_mean(n, sd, mu, margin, alpha, sims, seed=7):
    rng = np.random.default_rng(seed)
    x = rng.normal(mu, sd, (sims, n))
    m, s = x.mean(1), x.std(1, ddof=1)
    se = s / np.sqrt(n)
    df = n - 1
    p = np.maximum(stats.t.sf((m + margin) / se, df), stats.t.sf((margin - m) / se, df))
    return float(np.mean(p < alpha))


def test_mean_power_matches_monte_carlo():
    for mu in (0.0, 0.05):
        ana = eq.power_mean_one_sample(8, 0.05, mu, 0.10, 0.05)
        mc = _mc_power_mean(8, 0.05, mu, 0.10, 0.05, sims=4000)
        se = np.sqrt(max(mc * (1 - mc), 1e-12) / 4000)
        assert abs(ana - mc) <= max(4 * se, 1e-3), (mu, ana, mc, se)


def test_variance_power_matches_monte_carlo():
    n1 = n2 = 80
    theta, alpha, vr = 2.0, 0.05, 1.3225
    ana = eq.power_variance_ratio(n1, n2, vr, theta, alpha)
    rng = np.random.default_rng(11)
    sims = 2000
    rej = 0
    for _ in range(sims // 200):
        a = rng.normal(0, np.sqrt(vr), (200, n1))
        b = rng.normal(0, 1.0, (200, n2))
        v1, v2 = a.var(1, ddof=1), b.var(1, ddof=1)
        p = np.maximum(
            stats.f.sf((v1 * theta) / v2, n1 - 1, n2 - 1),
            stats.f.cdf((v1 / theta) / v2, n1 - 1, n2 - 1),
        )
        rej += int(np.sum(p < alpha))
    mc = rej / sims
    se = np.sqrt(mc * (1 - mc) / sims)
    assert abs(ana - mc) <= max(4 * se, 0.02), (ana, mc, se)


def test_one_sample_power_is_monotone_in_deviation():
    prev = 1.0
    for dev in (0.0, 0.02, 0.04, 0.06, 0.08):
        cur = eq.power_mean_one_sample(20, 0.05, dev, 0.10, 0.05)
        assert cur <= prev + 1e-12
        prev = cur


# --------------------------------------------------------------------------
# variance scenario
# --------------------------------------------------------------------------
def test_variance_tost_interval_duality():
    rng = np.random.default_rng(3)
    a = rng.normal(0, 1.0, 60)
    b = rng.normal(0, 1.0, 60)
    res = eq.tost_variance_ratio(a, b, theta=3.0, alpha=0.05)
    assert (res.ci_low > 1 / 3.0 and res.ci_high < 3.0) == res.equivalent
    assert res.equivalent  # same spread, wide margin


def test_variance_tost_withheld_for_small_samples():
    res = eq.tost_variance_ratio([0.1], [0.2, 0.3], theta=2.0)
    assert res.decision == "WITHHELD_SMALL_SAMPLE"
    assert not res.equivalent


def test_variance_tost_rejects_when_spreads_differ_by_orders():
    rng = np.random.default_rng(5)
    a = rng.normal(0, 0.001, 200)
    b = rng.normal(0, 1.0, 200)
    res = eq.tost_variance_ratio(a, b, theta=2.0)
    assert not res.equivalent


# --------------------------------------------------------------------------
# distribution scenario
# --------------------------------------------------------------------------
def test_distribution_tost_equivalent_for_same_law():
    rng = np.random.default_rng(9)
    a = rng.normal(0, 1, 80)
    b = rng.normal(0, 1, 80)
    res = eq.tost_distribution_ks(a, b, margin=0.9, alpha=0.05, n_boot=400)
    assert res.equivalent
    assert res.ci_high < 0.9


def test_distribution_tost_rejects_for_separated_laws():
    rng = np.random.default_rng(9)
    a = np.zeros(50)
    b = np.full(50, 10.0)
    res = eq.tost_distribution_ks(a, b, margin=0.3, alpha=0.05, n_boot=200)
    assert not res.equivalent
    assert res.estimate == pytest.approx(1.0)


def test_distribution_tost_is_seeded_and_reproducible():
    rng = np.random.default_rng(2)
    a, b = rng.normal(0, 1, 40), rng.normal(0.1, 1, 40)
    r1 = eq.tost_distribution_ks(a, b, 0.3, n_boot=200, seed=1234)
    r2 = eq.tost_distribution_ks(a, b, 0.3, n_boot=200, seed=1234)
    assert r1.ci_high == r2.ci_high and r1.p_value == r2.p_value


# --------------------------------------------------------------------------
# MDE / sample-size sensitivity
# --------------------------------------------------------------------------
def test_mde_increases_with_sample_size():
    small = eq.mde_mean_one_sample(10, 0.05, 0.10, 0.05, 0.80)
    large = eq.mde_mean_one_sample(80, 0.05, 0.10, 0.05, 0.80)
    assert 0.0 <= small < large <= 0.10


def test_mde_zero_when_design_cannot_certify_at_all():
    # sd far larger than the margin: even mu = 0 is not certifiable
    assert eq.mde_mean_one_sample(3, 5.0, 0.01, 0.05, 0.80) == 0.0
    assert eq.mde_generic(lambda dev: 0.1, 0.05, 0.80) == 0.0


def test_n_required_mean_hits_power_target():
    n = eq.n_required_mean(sd=0.05, margin=0.05, alpha=0.05, power_target=0.80)
    assert eq.power_mean_one_sample(n, 0.05, 0.0, 0.05, 0.05) >= 0.80
    assert eq.power_mean_one_sample(n - 1, 0.05, 0.0, 0.05, 0.05) < 0.80


def test_tost_result_is_json_safe():
    import json

    x = [0.01, 0.02, 0.03]
    payload = eq.tost_mean_one_sample(x, 0.05).to_dict()
    json.dumps(payload)                      # must not raise
    nan_free = eq.tost_mean_one_sample([0.02], 0.05).to_dict()
    assert nan_free["p_value"] is None       # NaN is normalised away
    assert nan_free["n"] == 1.0

def test_mde_is_not_zero_for_small_margins():
    """Regression guard: a fixed 1e-4 bisection tolerance zeroed MDE for 1e-6 margins."""
    mde = eq.mde_mean_one_sample(200, 1e-14, 1e-6, 0.05, 0.80)
    assert mde > 0.9e-6, mde
    res = eq.tost_mean_one_sample([1e-13] * 50 + [2e-13] * 50, 1e-6, 0.05)
    assert res.equivalent and res.mde is not None and res.mde > 0.5e-6
