"""C4 acceptance: model-form (structural) uncertainty of a model ensemble.

The truth is known analytically in every case, so the ensemble mean deviation is
a genuine error, not a self-consistency check.  Three regimes are pinned:

``affine``        every model is the truth plus a scaling/offset error -- the
                  regime where the *spread* is the whole story;
``truncation``    one exact model, one missing the interaction, one with the
                  wrong term, one linearised -- the regime BMA must resolve, and
                  where equal weights are demonstrably worse;
``uniform_bias``  all models share the same bias, so the spread is tiny and the
                  deviation is not: an honest counter-example showing that
                  model-form spread cannot detect a *common* error (only bias
                  correction against data can, and it needs a reference the
                  ensemble does not contain).

The law of total variance identity ``sigma_ens^2 = within + between`` is also
asserted, because that decomposition is what the framework reports as
"model-form share".
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.services.uq_framework import (  # noqa: E402
    InputDistribution,
    UQConfig,
    bma_weights,
    compare_models,
    run_uq,
)
from backend.services.uq_framework.marginals import validate_distributions  # noqa: E402
from backend.services.uq_framework.pce import latin_hypercube  # noqa: E402
from backend.services.uq_framework.synthetic import model_ensemble  # noqa: E402

TOL = 0.10  # C4: |ensemble_mean - truth| / |truth| < 10 %

DISTS2 = [InputDistribution.normal("x0", 0.0, 1.0),
          InputDistribution.uniform("x1", -1.0, 1.0)]
DISTS3 = [InputDistribution.normal("x0", 0.0, 1.0),
          InputDistribution.uniform("x1", -1.0, 1.0),
          InputDistribution.triangular("x2", 0.0, 1.0, 4.0)]


def _validation(ens, n_points: int = 32, seed: int = 5, meas_error: float = 0.005):
    """Build a synthetic validation set (experiment = truth + small meas_error)."""
    dists = DISTS2 if len(ens["models"][next(iter(ens["models"]))].__code__.co_varnames) == 2 \
        else DISTS3
    U = latin_hypercube(n_points, len(dists), seed)
    X = np.column_stack([d.from_unit(U[:, j]) for j, d in enumerate(dists)])
    kw = {d.name: X[:, j] for j, d in enumerate(dists)}
    y_true = np.asarray(ens["truth"](**kw), dtype=float)
    rng = np.random.default_rng(seed + 1)
    y_meas = y_true + rng.normal(0.0, meas_error * np.abs(y_true).mean(), y_true.shape)
    models = {k: np.asarray(m(**kw), dtype=float) for k, m in ens["models"].items()}
    return dists, {"truth": y_meas, "models": models}


def _dists_for(ens):
    first = ens["models"][next(iter(ens["models"]))]
    return DISTS2 if len(first.__code__.co_varnames) == 2 else DISTS3


# --------------------------------------------------------------------------- #
# weighting
# --------------------------------------------------------------------------- #
def test_bma_weights_are_normalised_and_prefer_the_better_model():
    rng = np.random.default_rng(0)
    truth = np.zeros(50)
    resid = {"good": rng.normal(0, 0.01, 50), "bad": rng.normal(0, 0.20, 50)}
    out = bma_weights(resid)
    assert sum(out["weights"].values()) == pytest.approx(1.0, abs=1e-12)
    assert out["weights"]["good"] > 0.99
    assert out["sigma"]["bad"] > out["sigma"]["good"]


def test_bma_weights_respect_priors_and_sigma():
    resid = {"a": np.zeros(10), "b": np.full(10, 1e-3)}
    out = bma_weights(resid, sigma={"a": 1e-3, "b": 1e-3}, priors={"a": 1.0, "b": 0.0})
    assert out["weights"]["a"] == pytest.approx(1.0, abs=1e-12)
    assert out["weights"]["b"] == 0.0
    assert out["excluded_by_prior"] == ["b"]
    with pytest.raises(ValueError):
        bma_weights({"a": np.zeros(5)}, sigma={"a": -1.0})


# --------------------------------------------------------------------------- #
# C4 -- ensemble mean against the known truth
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", ["affine", "truncation", "uniform_bias"])
def test_ensemble_mean_is_within_ten_percent_of_the_truth(kind):
    ens = model_ensemble(kind, seed=0)
    dists = _dists_for(ens)
    _, validation = _validation(ens)
    res = compare_models(ens["models"], dists, n=1000, weights="bma",
                         validation=validation, truth=ens["truth"])
    assert res.deviation_vs_truth is not None
    assert res.deviation_vs_truth < TOL, res.as_dict()
    assert sum(res.weights.values()) == pytest.approx(1.0, abs=1e-12)


def test_bma_is_at_least_as_good_as_equal_weights_on_the_truncation_case():
    ens = model_ensemble("truncation", seed=0)
    dists = _dists_for(ens)
    _, validation = _validation(ens)
    eq = compare_models(ens["models"], dists, n=1000, weights="equal",
                        validation=validation, truth=ens["truth"])
    bma = compare_models(ens["models"], dists, n=1000, weights="bma",
                         validation=validation, truth=ens["truth"])
    assert bma.deviation_vs_truth <= eq.deviation_vs_truth + 1e-12
    assert bma.weights["exact"] > 0.5
    # the exact model must also be the best single model, so the improvement is
    # attributable to weighting rather than to luck
    assert bma.best_single == "exact"


def test_law_of_total_variance_decomposition():
    ens = model_ensemble("affine", seed=0)
    dists = _dists_for(ens)
    _, validation = _validation(ens)
    res = compare_models(ens["models"], dists, n=1000, weights="equal",
                         validation=validation, truth=ens["truth"])
    assert res.ensemble_std ** 2 == pytest.approx(
        res.within_model_var + res.between_model_std ** 2, rel=1e-10)
    assert 0.0 <= res.model_form_share <= 1.0


def test_common_bias_is_not_detectable_from_spread_alone():
    """Honest limitation: identical bias in every model leaves the spread at zero."""
    ens = model_ensemble("uniform_bias", seed=0)
    dists = _dists_for(ens)
    _, validation = _validation(ens)
    res = compare_models(ens["models"], dists, n=1000, weights="equal",
                         validation=validation)
    assert res.between_model_std > 0.0        # the *models* do differ...
    assert res.model_form_share < 0.01        # ...but the spread is a tiny share
    # with a truth reference the deviation is visible, and bias correction fixes it
    res2 = compare_models(ens["models"], dists, n=1000, weights="equal",
                          validation=validation, truth=ens["truth"])
    assert res2.deviation_vs_truth is not None
    assert res2.deviation_vs_truth_bc < res2.deviation_vs_truth


def test_bias_correction_reduces_the_deviation_on_the_affine_case():
    ens = model_ensemble("affine", seed=0)
    dists = _dists_for(ens)
    _, validation = _validation(ens)
    res = compare_models(ens["models"], dists, n=1000, weights="equal",
                         validation=validation, truth=ens["truth"])
    assert res.deviation_vs_truth_bc < res.deviation_vs_truth
    assert set(res.bias_corrected_means) == set(ens["models"])


def test_model_form_requires_a_validation_set_for_bma():
    ens = model_ensemble("affine", seed=0)
    dists = _dists_for(ens)
    with pytest.raises(ValueError):
        compare_models(ens["models"], dists, n=200, weights="bma", validation=None)


# --------------------------------------------------------------------------- #
# C4 through the framework entry point
# --------------------------------------------------------------------------- #
def test_run_uq_quantifies_model_form_and_scores_c4():
    ens = model_ensemble("truncation", seed=0)
    dists = _dists_for(ens)
    _, validation = _validation(ens)
    cfg = UQConfig("c4_e2e", dists, propagation_method="monte_carlo", n_mc=400, sobol=False,
                   model_form={"weights": "bma", "validation": validation,
                               "truth": ens["truth"], "n_samples": 400})
    res = run_uq(ens["models"], cfg)
    assert res.model_form is not None
    assert res.criteria["C4_model_form"]["status"] == "pass"
    assert res.model_form["deviation_vs_truth"] < TOL
    assert res.provenance["reference_model"] in ens["models"]
    # the model-form leg evaluated every model on the same design
    n_models = len(ens["models"])
    # every model plus the truth reference, all on the same 400-point design
    assert res.provenance["n_model_evaluations"]["model_form"] == 400 * (n_models + 1)

def test_bma_degenerates_to_selection_when_validation_error_is_negligible():
    """Documented limitation (honest negative result).

    With an almost noiseless validation set, BMA's evidence term is dominated by
    the *smallest* residual, so the posterior collapses onto one model -- i.e. it
    becomes model selection.  On an ensemble where every member is biased in a
    different direction, that is *worse* than plain averaging.  The framework
    therefore never assumes BMA is the better weighting; the two are reported
    side by side.
    """
    ens = model_ensemble("competing_biases", seed=0)
    dists = _dists_for(ens)
    _, validation = _validation(ens)
    bma = compare_models(ens["models"], dists, n=600, weights="bma",
                         validation=validation, truth=ens["truth"])
    eq = compare_models(ens["models"], dists, n=600, weights="equal",
                        validation=validation, truth=ens["truth"])
    assert max(bma.weights.values()) > 0.99            # selection, not averaging
    assert bma.deviation_vs_truth > eq.deviation_vs_truth
    assert eq.deviation_vs_truth < TOL                 # averaging is within tolerance
    assert eq.deviation_vs_truth <= eq.best_single_deviation + 1e-12
