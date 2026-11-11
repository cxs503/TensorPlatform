"""Estimator unit tests: the verification rig's own arithmetic must be right."""

from __future__ import annotations

import math

import pytest

from tools.metrics_verify import stats


def test_percentile_matches_numpy_linear_interpolation():
    np = pytest.importorskip("numpy")
    data = [3.0, 1.0, 4.0, 1.5, 5.0, 9.0, 2.6, 5.3]
    for q in (0.0, 0.1, 0.5, 0.9, 0.99, 1.0):
        assert stats.percentile(data, q) == pytest.approx(
            float(np.percentile(data, q * 100)), rel=1e-12)


def test_percentile_empty_sample_is_none_not_zero():
    """A zero would silently pass a latency ceiling: refusal is the contract."""
    assert stats.percentile([], 0.99) is None


def test_percentile_rejects_out_of_range_q():
    with pytest.raises(stats.StatsError):
        stats.percentile([1.0, 2.0], 1.5)


def test_summary_matches_numpy_moments():
    np = pytest.importorskip("numpy")
    data = [0.1, 0.2, 0.25, 0.9, 1.4]
    summary = stats.summarize(data)
    assert summary["mean"] == pytest.approx(float(np.mean(data)), rel=1e-12)
    assert summary["var"] == pytest.approx(float(np.var(data)), rel=1e-12)
    assert summary["n"] == len(data)


def test_lognormal_quantile_reproduces_textbook_normal_quantiles():
    """With median=1, sigma=1 the lognormal quantile is ``exp(z_q)`` for the normal z."""
    assert stats.lognormal_quantile(1.0, 1.0, 0.5) == pytest.approx(1.0, rel=1e-12)
    assert stats.lognormal_quantile(1.0, 1.0, 0.975) == pytest.approx(math.exp(1.959964), rel=1e-6)
    assert stats.lognormal_quantile(1.0, 1.0, 0.99) == pytest.approx(math.exp(2.326348), rel=1e-6)
    assert stats.lognormal_quantile(2.0, 0.0, 0.99) == pytest.approx(2.0, rel=1e-12)


def test_relative_and_absolute_error_helpers():
    assert stats.rel_error(1.05, 1.0) == pytest.approx(0.05)
    assert stats.rel_error(None, 1.0) is None
    assert stats.abs_error(0.94, 0.90) == pytest.approx(0.04)
    assert stats.rel_error(0.0, 0.0) == 0.0


def test_seeded_rng_is_reproducible():
    a = [stats.seeded_rng(7).random() for _ in range(3)]
    assert a == [stats.seeded_rng(7).random() for _ in range(3)]
    assert math.isfinite(sum(a))