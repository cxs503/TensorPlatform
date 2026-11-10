"""Correctness of the feasibility-prior calibrator (the new stage of this package).

The calibrator must be *falsifiable*, so every test here compares against a known
truth, not against itself:

1. On data generated from a logistic link, the fitted slope must recover the true
   slope, and ECE must be small;
2. a constant predictor scores worse (higher Brier) than the fitted prior -- the
   base-rate reference is real, not decorative;
3. AUROC must match a hand-computed value on a separable example;
4. a reversed score (higher = worse) must be flagged ``monotone=False`` rather
   than silently reinterpreted;
5. the cutoff inversion must round-trip through the forward probability;
6. degenerate inputs (single class, constant scores, length mismatch) must raise.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tools.toolchain_integration.prior import (
    CalibrationError,
    calibrate,
    fit_logistic,
)


def _logistic_sample(n=20000, a_true=2.0, b_true=-0.5, seed=5):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    p = 1.0 / (1.0 + np.exp(-(b_true + a_true * x)))
    y = (rng.random(n) < p).astype(float)
    return x, y


def _reference_auroc(a: float, b: float, n: int = 1201) -> float:
    """Independent reference: quadrature of P(X_1 > X_0 | Y_1=1, Y_0=0).

    Derived from the generative model itself (not from the module under test):
        AUC = ∫∫_{x1>x0} p(x1)(1-p(x0)) φ(x1)φ(x0) dx1 dx0 / (P1 P0)
    """
    x = np.linspace(-9.0, 9.0, n)
    phi = np.exp(-0.5 * x ** 2) / math.sqrt(2.0 * math.pi)
    px = 1.0 / (1.0 + np.exp(-(b + a * x)))
    w = phi
    pos = float(np.sum(px * w))
    neg = float(np.sum((1.0 - px) * w))
    # upper triangle sum: x1 > x0
    weighted_pos = px * w
    weighted_neg = (1.0 - px) * w
    cum_neg = np.concatenate([[0.0], np.cumsum(weighted_neg)[:-1]])
    num = float(np.sum(weighted_pos * cum_neg))
    return num / (pos * neg)


def test_slope_recovery_and_calibration_quality():
    x, y = _logistic_sample()
    # (b, a) are on *standardised* x, i.e. slope_std = a_true * std(x) ~ a_true
    rep = calibrate(x, y, n_bins=10)
    assert rep.n == 20000
    assert rep.monotone is True
    assert rep.slope == pytest.approx(2.0, rel=0.10)
    assert rep.ece < 0.02
    assert rep.brier < rep.brier_reference
    reference = _reference_auroc(2.0, -0.5)
    assert rep.auroc == pytest.approx(reference, abs=0.02), (rep.auroc, reference)


def test_constant_predictor_is_worse():
    x, y = _logistic_sample(n=4000)
    rep = calibrate(x, y)
    assert rep.brier_reference > rep.brier                     # skill > 0
    assert rep.brier_skill > 0.1


def test_auroc_matches_hand_computation():
    scores = np.array([0.1, 0.2, 0.3, 0.4])
    labels = np.array([0.0, 0.0, 1.0, 1.0])
    rep = calibrate(scores, labels, n_bins=2)
    assert rep.auroc == pytest.approx(1.0)                     # perfectly separable

    scores2 = np.array([0.4, 0.3, 0.2, 0.1])
    rep2 = calibrate(scores2, labels, n_bins=2)
    assert rep2.auroc == pytest.approx(0.0)
    assert rep2.monotone is False                              # direction is reported


def test_cutoff_inversion_roundtrip():
    x, y = _logistic_sample(n=8000)
    rep = calibrate(x, y)
    for target in (0.2, 0.5, 0.8):
        cut = rep.cutoff(target)
        assert rep.probability(cut) == pytest.approx(target, abs=1e-9)


def test_bins_partition_the_sample():
    x, y = _logistic_sample(n=1000)
    rep = calibrate(x, y, n_bins=8)
    assert len(rep.bins) == 8
    assert sum(b["count"] for b in rep.bins) == 1000
    assert all(0.0 <= b["predicted"] <= 1.0 for b in rep.bins)
    # monotone predictions across equal-count bins (logistic is monotone)
    predicted = [b["predicted"] for b in rep.bins]
    assert predicted == sorted(predicted)


def test_degenerate_inputs_raise():
    with pytest.raises(CalibrationError):
        calibrate([0.1, 0.2, 0.3, 0.4], [1.0, 1.0, 1.0, 1.0])      # single class
    with pytest.raises(CalibrationError):
        calibrate([0.5, 0.5, 0.5, 0.5], [0.0, 1.0, 0.0, 1.0])      # constant score
    with pytest.raises(CalibrationError):
        calibrate([0.1, 0.2, 0.3], [0.0, 1.0, 0.0])                # too few points
    with pytest.raises(CalibrationError):
        calibrate([0.1, 0.2, 0.3, 0.4], [0.0, 1.0, 0.0])           # length mismatch
    with pytest.raises(CalibrationError):
        calibrate([0.1, 0.2, 0.3, 0.4], [0.0, 2.0, 0.0, 1.0])      # non 0/1 labels
    with pytest.raises(CalibrationError):
        calibrate([0.1, 0.2, 0.3, 0.4], [0.0, 1.0, 0.0, 1.0], n_bins=1)


def test_fit_logistic_is_deterministic():
    x, y = _logistic_sample(n=2000)
    a = fit_logistic(x, y)
    b = fit_logistic(x, y)
    assert a == b


def test_cutoff_requires_valid_target():
    x, y = _logistic_sample(n=2000)
    rep = calibrate(x, y)
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(CalibrationError):
            rep.cutoff(bad)