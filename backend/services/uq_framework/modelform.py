"""Model-form uncertainty: multi-model spread and Bayesian model averaging.

The gap this file closes
------------------------
Input propagation answers "given this model, how uncertain is the answer?".
Chao/Kinematic-style practice on this platform has repeatedly shown the honest
answer to "how uncertain is the answer?" needs a *second* term: the models are
wrong, and different defensible models disagree.  Before this module the
platform had **no** estimator for that term, so every reported band was an
input/numeric band silently presented as total uncertainty.

Two estimators are supplied, both domain-free.

1. **Multi-model spread (structural/ensemble spread).**  Evaluate every model on
   one common sample design, take
   ``sigma_between^2 = (1/M) sum_m (mean_m - mean_ens)^2`` (or the weighted
   version), and report it relative to the ensemble mean.  This is the model-
   form contribution that no amount of input refinement can remove.

2. **Bayesian model averaging (BMA).**  With per-model Gaussian prediction
   error ``sigma_m`` (supplied, or estimated from residuals on a validation
   set), the posterior weight is

       w_m  proportional to  prior_m * sigma_m^{-n_m} * exp(-SSE_m / (2 sigma_m^2))

   and the BMA predictive variance follows the law of total variance

       Var_BMA = sum_m w_m ( sigma_m^2 + (mean_m - mean_ens)^2 ),

   i.e. *within-model* predictive scatter plus *between-model* disagreement.
   The decomposition is reported explicitly
   (``model_form_share = between / total``), because that ratio, not the total,
   is the quantity that says whether more physics or more data is needed.

3. **Bias correction.**  When the ensemble is systematically off, a per-model
   mean residual on the validation set is subtracted (``bias_corrected``).
   Reported separately from the raw ensemble: a bias correction that fixes the
   deviation is evidence about the truth reference, not about the models, and
   must not be confused with model-form spread.

Scoring.  If a truth reference is supplied (``truth`` callable evaluated on the
same design, or a scalar), the ensemble mean deviation
``|mean_ens - truth| / |truth|`` is reported -- the only falsifiable statement
available, and the one the acceptance criteria score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

import numpy as np

from .montecarlo import call_in_unit_box
from .marginals import InputDistribution

__all__ = [
    "ModelFormResult",
    "bma_weights",
    "compare_models",
    "run_model_form",
]


# --------------------------------------------------------------------------- #
# weighting
# --------------------------------------------------------------------------- #
def bma_weights(
    residuals: Mapping[str, np.ndarray],
    sigma: Optional[Mapping[str, float]] = None,
    priors: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """Posterior model weights and per-model likelihoods.

    ``residuals[m]`` are model-minus-data residuals on the *same* validation
    points.  If ``sigma`` is omitted it is estimated as the root-mean-square
    residual of that model (a conservative, self-consistent choice: a model
    that fits the data badly gets a wide ``sigma`` and its evidence is not
    artificially inflated).
    """
    names = list(residuals.keys())
    if not names:
        raise ValueError("residuals must be non-empty")
    r = {m: np.asarray(residuals[m], dtype=float).ravel() for m in names}
    n = min(v.size for v in r.values())
    if n < 2:
        raise ValueError("need at least 2 validation points")
    r = {m: v[:n] for m, v in r.items()}
    sig = {}
    for m in names:
        if sigma is not None and m in sigma:
            s = float(sigma[m])
            if not (s > 0):
                raise ValueError(f"{m}: sigma must be > 0")
            sig[m] = s
        else:
            s = float(np.sqrt(np.mean(r[m] ** 2)))
            sig[m] = s if s > 0 else 1e-12
    pri = {m: float(priors.get(m, 1.0)) if priors else 1.0 for m in names}
    for m, val in pri.items():
        if val < 0.0 or not np.isfinite(val):
            raise ValueError(f"{m}: prior weight must be finite and >= 0")
    loglik = {m: -0.5 * n * np.log(2.0 * np.pi * sig[m] ** 2)
              - 0.5 * float(np.sum(r[m] ** 2)) / sig[m] ** 2 for m in names}
    # a zero prior is an exclusion, not a -inf log-likelihood: handle it before
    # the log so no RuntimeWarning is emitted and the weight is exactly 0
    active = [m for m in names if pri[m] > 0.0]
    if not active:
        raise ValueError("all prior weights are zero")
    log_post = {m: float(np.log(pri[m])) + loglik[m] for m in active}
    mx = max(log_post.values())
    denom = sum(np.exp(log_post[m] - mx) for m in active)
    w = {m: (float(np.exp(log_post[m] - mx) / denom) if m in active else 0.0) for m in names}
    return {"weights": w, "sigma": sig, "log_likelihood": loglik, "n_validation": int(n),
            "excluded_by_prior": [m for m in names if m not in active]}


# --------------------------------------------------------------------------- #
# container
# --------------------------------------------------------------------------- #
@dataclass
class ModelFormResult:
    """Model-form (structural) uncertainty of an ensemble of models."""

    model_names: Sequence[str]
    weights: Dict[str, float]
    weight_source: str
    model_means: Dict[str, float]
    model_stds: Dict[str, float]
    ensemble_mean: float
    ensemble_std: float
    between_model_std: float
    model_form_rel_spread: float
    within_model_var: float
    model_form_share: float
    bias_corrected_means: Dict[str, float] = field(default_factory=dict)
    truth: Optional[float] = None
    truth_std: Optional[float] = None
    deviation_vs_truth: Optional[float] = None
    deviation_vs_truth_bc: Optional[float] = None
    best_single: Optional[str] = None
    best_single_deviation: Optional[float] = None
    n_samples: int = 0
    n_evals: int = 0
    notes: Sequence[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "method": "multi_model_bma",
            "model_names": list(self.model_names),
            "weights": dict(self.weights),
            "weight_source": self.weight_source,
            "model_means": dict(self.model_means),
            "model_stds": dict(self.model_stds),
            "ensemble_mean": float(self.ensemble_mean),
            "ensemble_std": float(self.ensemble_std),
            "between_model_std": float(self.between_model_std),
            "model_form_rel_spread": float(self.model_form_rel_spread),
            "within_model_var": float(self.within_model_var),
            "model_form_share": float(self.model_form_share),
            "bias_corrected_means": dict(self.bias_corrected_means),
            "truth": None if self.truth is None else float(self.truth),
            "truth_std": None if self.truth_std is None else float(self.truth_std),
            "deviation_vs_truth": (None if self.deviation_vs_truth is None
                                   else float(self.deviation_vs_truth)),
            "deviation_vs_truth_bias_corrected": (
                None if self.deviation_vs_truth_bc is None else float(self.deviation_vs_truth_bc)),
            "best_single": self.best_single,
            "best_single_deviation": (None if self.best_single_deviation is None
                                      else float(self.best_single_deviation)),
            "n_samples": int(self.n_samples),
            "n_evals": int(self.n_evals),
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def compare_models(
    models: Mapping[str, Callable[..., Any]],
    dists: Sequence[InputDistribution],
    n: int = 1000,
    seed: int = 0,
    weights: Optional[Mapping[str, float]] = None,
    validation: Optional[Mapping[str, Any]] = None,
    truth: Optional[Any] = None,
    call_style: str = "kwargs",
) -> ModelFormResult:
    """Evaluate a model ensemble on one design and quantify model-form uncertainty.

    Parameters
    ----------
    models      : ``{name: func}`` -- every model answers the same question.
    weights     : ``None`` (equal weights), ``"bma"`` (posterior weights from
                  ``validation``), or an explicit ``{name: w}`` mapping.
    validation  : ``{"truth": array, "models": {name: array}}`` -- per-model
                  predictions, *not* residuals, so the residual sign convention
                  is fixed here.  ``sigma`` / ``priors`` keys are optional.
    truth       : callable evaluated on the same design, or a scalar reference
                  mean, used only for the deviation score.
    """
    names = list(models.keys())
    if not names:
        raise ValueError("models must be non-empty")
    d = len(dists)
    rng = np.random.default_rng(seed)
    from .pce import latin_hypercube

    U = latin_hypercube(n, d, seed) if n >= 10 else rng.random((int(n), d))
    ys: Dict[str, np.ndarray] = {}
    evals = 0
    for m in names:
        ys[m] = call_in_unit_box(models[m], dists, U, call_style=call_style)
        evals += int(n)

    means = {m: float(np.mean(ys[m])) for m in names}
    stds = {m: float(np.std(ys[m], ddof=1)) for m in names}

    notes = []
    weight_source = "equal"
    if isinstance(weights, str) and weights.lower() == "bma":
        if not validation:
            raise ValueError("weights='bma' requires a validation mapping")
        missing = [m for m in names if m not in (validation.get("models") or {})]
        if missing:
            raise ValueError(
                "validation['models'] must cover every model for BMA; missing "
                f"{missing}.  A model without validation data has no evidence, and "
                "silently falling back to a prior would make the weights unearned.")
        v_truth = np.asarray(validation["truth"], dtype=float).ravel()
        resid = {m: np.asarray(validation["models"][m], dtype=float).ravel() - v_truth
                 for m in names}
        bw = bma_weights(resid, sigma=validation.get("sigma"),
                         priors=validation.get("priors"))
        w = bw["weights"]
        weight_source = f"bma[n={bw['n_validation']}]"
        notes.append("sigma estimated from RMS residual where not supplied")
    elif isinstance(weights, Mapping):
        w = {m: float(weights[m]) for m in names}
        tot = sum(w.values())
        if not (tot > 0):
            raise ValueError("weights must sum to a positive number")
        w = {m: v / tot for m, v in w.items()}
        weight_source = "user"
    else:
        w = {m: 1.0 / len(names) for m in names}

    ens_mean = float(sum(w[m] * means[m] for m in names))
    between_var = float(sum(w[m] * (means[m] - ens_mean) ** 2 for m in names))
    within_var = float(sum(w[m] * stds[m] ** 2 for m in names))
    total_var = between_var + within_var
    ens_std = float(np.sqrt(total_var))
    mf_share = float(between_var / total_var) if total_var > 0 else 0.0
    rel_spread = float(np.sqrt(between_var) / abs(ens_mean)) if ens_mean else float("inf")

    # bias correction from the validation set (per-model mean residual)
    bc: Dict[str, float] = {}
    if validation:
        v_truth = np.asarray(validation["truth"], dtype=float).ravel()
        for m in names:
            pred = np.asarray(validation["models"][m], dtype=float).ravel()
            nmin = min(pred.size, v_truth.size)
            bc[m] = float(means[m] + np.mean(v_truth[:nmin] - pred[:nmin]))
        notes.append("bias_corrected means shift each model by its mean validation residual")
    ens_mean_bc = float(sum(w[m] * bc[m] for m in names)) if bc else None

    truth_mean = None
    truth_std = None
    if truth is not None:
        if callable(truth):
            yt = call_in_unit_box(truth, dists, U, call_style=call_style)
            truth_mean = float(np.mean(yt))
            truth_std = float(np.std(yt, ddof=1))
            evals += int(n)
        else:
            truth_mean = float(truth)
    dev = None if truth_mean is None or truth_mean == 0 else abs(ens_mean - truth_mean) / abs(truth_mean)
    dev_bc = (None if (truth_mean in (None, 0) or ens_mean_bc is None)
              else abs(ens_mean_bc - truth_mean) / abs(truth_mean))
    if truth_mean is not None:
        singles = {m: abs(means[m] - truth_mean) / abs(truth_mean) for m in names
                   if truth_mean != 0}
        best = min(singles, key=singles.get) if singles else None
        best_dev = singles[best] if best else None
    else:
        best, best_dev = None, None

    return ModelFormResult(
        model_names=names,
        weights=w,
        weight_source=weight_source,
        model_means=means,
        model_stds=stds,
        ensemble_mean=ens_mean,
        ensemble_std=ens_std,
        between_model_std=float(np.sqrt(between_var)),
        model_form_rel_spread=rel_spread,
        within_model_var=within_var,
        model_form_share=mf_share,
        bias_corrected_means=bc,
        truth=truth_mean,
        truth_std=truth_std,
        deviation_vs_truth=dev,
        deviation_vs_truth_bc=dev_bc,
        best_single=best,
        best_single_deviation=best_dev,
        n_samples=int(U.shape[0]),
        n_evals=int(evals),
        notes=notes,
    )


def run_model_form(
    models: Mapping[str, Callable[..., Any]],
    dists: Sequence[InputDistribution],
    spec: Optional[Mapping[str, Any]] = None,
) -> ModelFormResult:
    """Thin spec-driven wrapper used by :func:`backend.services.uq_framework.runner.run_uq`."""
    spec = dict(spec or {})
    return compare_models(
        models,
        dists,
        n=int(spec.get("n_samples", 1000)),
        seed=int(spec.get("seed", 0)),
        weights=spec.get("weights", "equal"),
        validation=spec.get("validation"),
        truth=spec.get("truth"),
        call_style=str(spec.get("call_style", "kwargs")),
    )