"""pytest suite for the sample-driven PCE kernel (``tools/pce``).

Four groups of checks:

* **mathematics** -- analytic models with known coefficients, variance and
  ANOVA indices (Legendre/uniform and Hermite/normal), plus the closed-form
  Ishigami benchmark used in the report;
* **API contract** -- ``pce_fit`` returns what the platform promises
  (``coefficients`` / ``mean`` / ``variance`` / ``sobol_first`` / ``sobol_total``)
  and rejects malformed input instead of silently reinterpreting it;
* **diagnostics** -- prediction, leave-one-out error, quantile bands;
* **platform rules** -- the package stays free of domain vocabulary, and the
  generic case runner reproduces the whole UQ pipeline from an injected case.

Every fixture here is synthetic; no test knows about ships, hulls or solvers.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pytest

from tools.pce import (
    REQUIRED_KEYS,
    pce_band,
    pce_fit,
    pce_predict,
    physical_design,
    quadrature_design,
    resolve_marginals,
)
from tools.pce.case_runner import run_case

PI = math.pi
REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _uniform_specs(dim: int, low: float = -PI, high: float = PI):
    return [
        {"name": f"x{j + 1}", "family": "uniform", "lower": low, "upper": high}
        for j in range(dim)
    ]


def _normal_specs(dim: int):
    return [{"name": f"x{j + 1}", "family": "normal", "mean": 0.0, "std": 1.0} for j in range(dim)]


def _ishigami(X) -> np.ndarray:
    X = np.atleast_2d(np.asarray(X, dtype=float))
    return np.sin(X[:, 0]) + 7.0 * np.sin(X[:, 1]) ** 2 + 0.1 * X[:, 2] ** 4 * np.sin(X[:, 0])


ISHIGAMI_REFERENCE = {
    "variance": 13.8445,
    "first": np.array([0.3139, 0.4424, 0.0]),
    "total": np.array([0.5575, 0.4424, 0.2439]),
    "second_x1x3": 0.2439,  # Saltelli et al. (2008) Table 2.3
}


@pytest.fixture(scope="module")
def ishigami_fit():
    """One converged projection fit, shared by the Ishigami checks."""
    specs = _uniform_specs(3)
    grid = quadrature_design(specs, kind="tensor", n_per_dim=13)
    Y = _ishigami(grid["X"])
    return pce_fit(grid["X"], Y, 12, marginals=specs, weights=grid["weights"])


# --------------------------------------------------------------------------- #
# 1. API contract
# --------------------------------------------------------------------------- #
def test_api_contract_keys_and_shapes():
    specs = _uniform_specs(2, -1.0, 1.0)
    grid = quadrature_design(specs, kind="tensor", n_per_dim=4)
    Y = 1.0 + 2.0 * grid["X"][:, 0] - 0.5 * grid["X"][:, 1]
    fit = pce_fit(grid["X"], Y, 1, marginals=specs, weights=grid["weights"])

    for key in REQUIRED_KEYS:
        assert key in fit, f"missing required key {key!r}"
    assert isinstance(fit["coefficients"], np.ndarray)
    assert fit["sobol_first"].shape == (2,)
    assert fit["sobol_total"].shape == (2,)
    assert isinstance(fit["mean"], float) and isinstance(fit["variance"], float)
    assert fit["n_dim"] == 2 and fit["n_terms"] == fit["coefficients"].size
    assert fit["method"] == "projection"


def test_projection_recovers_exact_legendre_coefficients():
    """Uniform inputs: a bivariate polynomial is reproduced to machine precision."""
    specs = _uniform_specs(2, -1.0, 1.0)
    grid = quadrature_design(specs, kind="tensor", n_per_dim=4)
    x1, x2 = grid["X"][:, 0], grid["X"][:, 1]
    Y = 1.0 + 2.0 * x1 - 0.5 * x2 + 0.25 * x1 * x2
    fit = pce_fit(grid["X"], Y, 2, marginals=specs, weights=grid["weights"])

    # orthonormal Legendre: P0=1, P1=sqrt(3) x, P2=sqrt(5)(3x^2-1)/2
    expected = {(0, 0): 1.0, (1, 0): 2.0 / math.sqrt(3.0),
                (0, 1): -0.5 / math.sqrt(3.0), (1, 1): 0.25 / 3.0}
    idx = {tuple(a): c for a, c in zip(fit["multi_indices"], fit["coefficients"])}
    for alpha, value in expected.items():
        assert idx.get(alpha, 0.0) == pytest.approx(value, rel=0, abs=1e-12)
    variance = 4.0 / 3.0 + 0.25 / 3.0 + (0.25 / 3.0) ** 2
    assert fit["variance"] == pytest.approx(variance, rel=1e-12)
    assert fit["mean"] == pytest.approx(1.0, abs=1e-12)
    # Sobol' on the analytic ANOVA components
    assert fit["sobol_first"][0] == pytest.approx((4.0 / 3.0) / variance, rel=1e-12)
    assert fit["sobol_first"][1] == pytest.approx((0.25 / 3.0) / variance, rel=1e-11)
    # first + second order exhaust the variance of a bilinear response
    assert (fit["sobol_first"].sum() + fit["sobol_second"]["x1-x2"]) == pytest.approx(1.0, rel=1e-12)
    assert fit["sobol_second"]["x1-x2"] == pytest.approx((0.25 / 3.0) ** 2 / variance, rel=1e-11)


def test_regression_recovers_exact_hermite_polynomial():
    """Normal inputs: He_2 captures a quadratic term with unit variance bookkeeping."""
    specs = _normal_specs(2)
    mar = resolve_marginals(np.zeros((2, 2)), specs)
    X, _ = physical_design(400, mar, sampler="lhs", seed=4)
    a, b = 1.5, 0.75
    Y = 2.0 + a * X[:, 0] + b * (X[:, 1] ** 2 - 1.0) / math.sqrt(2.0)
    fit = pce_fit(X, Y, 2, marginals=specs)

    variance = a ** 2 + b ** 2
    assert fit["mean"] == pytest.approx(2.0, abs=5e-3)
    assert fit["variance"] == pytest.approx(variance, rel=5e-2)
    assert fit["sobol_first"][0] == pytest.approx(a ** 2 / variance, abs=2e-2)
    assert fit["sobol_first"][1] == pytest.approx(b ** 2 / variance, abs=2e-2)
    assert fit["sobol_total"][0] == pytest.approx(fit["sobol_first"][0], abs=2e-2)


def test_marginal_inference_matches_explicit_spec():
    """``marginals=None`` infers a uniform law from the sample range.

    The design is a full lattice that *includes* the endpoints, so the inferred
    range is the true one; with a Gauss rule the nodes never touch the
    boundary and re-inferring the law from the samples would silently change
    the measure (that is why quadrature designs must state their laws).
    """
    specs = _uniform_specs(2, -1.0, 1.0)
    axis = np.linspace(-1.0, 1.0, 20)
    X = np.array([[a, b] for a in axis for b in axis])
    Y = X[:, 0] ** 2 + 0.5 * X[:, 1]
    explicit = pce_fit(X, Y, 2, marginals=specs)
    inferred = pce_fit(X, Y, 2, marginals=None)
    assert np.allclose(explicit["sobol_first"], inferred["sobol_first"], atol=1e-6)
    assert explicit["variance"] == pytest.approx(inferred["variance"], rel=1e-6)
    # the analytic ANOVA of y = x1^2 + 0.5 x2 on U(-1,1)^2
    var = 4.0 / 45.0 + 0.25 / 3.0
    assert explicit["sobol_first"][0] == pytest.approx((4.0 / 45.0) / var, rel=1e-6)
    assert explicit["sobol_first"][1] == pytest.approx((0.25 / 3.0) / var, rel=1e-6)


def test_normal_shorthand_inference():
    specs = _normal_specs(2)
    mar = resolve_marginals(np.zeros((2, 2)), specs)
    X, _ = physical_design(600, mar, sampler="qmc", seed=8)
    Y = 1.0 + X[:, 0] + 0.25 * X[:, 1]
    fit = pce_fit(X, Y, 1, marginals="normal")
    assert [m["family"] for m in fit["marginals"]] == ["normal", "normal"]
    assert fit["marginals"][0]["mean"] == pytest.approx(float(np.mean(X[:, 0])), abs=1e-12)
    assert fit["variance"] == pytest.approx(1.0 + 0.0625, rel=5e-2)


def test_invalid_inputs_raise():
    X = np.zeros((10, 2))
    Y = np.zeros(10)
    with pytest.raises(ValueError):
        pce_fit(X, Y, -1)
    with pytest.raises(ValueError):
        pce_fit(X, np.zeros(9), 1)
    with pytest.raises(ValueError):  # 10 samples for 15 quartic terms -> under-determined
        pce_fit(X, Y, 4, marginals=_uniform_specs(2, -1.0, 1.0))
    bad = np.zeros((5, 2))
    bad[0, 0] = np.nan
    with pytest.raises(ValueError):
        pce_fit(bad, np.zeros(5), 1)


# --------------------------------------------------------------------------- #
# 2. Ishigami closed-form benchmark (the report's Sobol' table)
# --------------------------------------------------------------------------- #
def test_ishigami_variance_and_first_order_within_5pct(ishigami_fit):
    variance = ishigami_fit["variance"]
    assert variance == pytest.approx(ISHIGAMI_REFERENCE["variance"], rel=5e-3)
    for j in range(3):
        assert abs(ishigami_fit["sobol_first"][j] - ISHIGAMI_REFERENCE["first"][j]) < 0.05


def test_ishigami_total_order_within_5pct(ishigami_fit):
    for j in range(3):
        assert abs(ishigami_fit["sobol_total"][j] - ISHIGAMI_REFERENCE["total"][j]) < 0.05
    # x3 has zero first-order but a large total effect: the total-order test
    assert ishigami_fit["sobol_first"][2] == pytest.approx(0.0, abs=5e-3)
    assert ishigami_fit["sobol_total"][2] > 0.2


def test_ishigami_interaction_index(ishigami_fit):
    s13 = ishigami_fit["sobol_second"].get("x1-x3", float("nan"))
    assert s13 == pytest.approx(ISHIGAMI_REFERENCE["second_x1x3"], abs=0.05)


def test_additive_response_has_equal_total_and_first():
    specs = _uniform_specs(3, -1.0, 1.0)
    grid = quadrature_design(specs, kind="tensor", n_per_dim=5)
    Y = 3.0 - grid["X"][:, 0] + 0.25 * grid["X"][:, 1] + 0.05 * grid["X"][:, 2]
    fit = pce_fit(grid["X"], Y, 2, marginals=specs, weights=grid["weights"])
    assert np.allclose(fit["sobol_first"], fit["sobol_total"], atol=1e-12)
    assert fit["sobol_first"].sum() == pytest.approx(1.0, abs=1e-12)
    assert max(fit["sobol_second"].values()) < 1e-12  # no measurable interaction


# --------------------------------------------------------------------------- #
# 3. diagnostics: prediction, LOO, band, projection vs regression
# --------------------------------------------------------------------------- #
def test_pce_predict_reproduces_response():
    specs = _uniform_specs(3, -1.0, 1.0)
    grid = quadrature_design(specs, kind="tensor", n_per_dim=5)
    X = grid["X"]
    Y = 1.0 + 2.0 * X[:, 0] + 0.5 * X[:, 1] - 0.3 * X[:, 0] * X[:, 2] + 0.2 * X[:, 2] ** 2
    fit = pce_fit(X, Y, 3, marginals=specs, weights=grid["weights"])
    pred = pce_predict(fit, X)
    assert np.max(np.abs(pred - Y)) < 1e-10
    # extrapolation onto a fresh design still follows the surrogate polynomial
    fresh, _ = physical_design(50, resolve_marginals(np.zeros((2, 3)), specs), sampler="lhs", seed=2)
    assert np.all(np.isfinite(pce_predict(fit, fresh)))


def test_loo_error_discriminates_model_adequacy():
    specs = _uniform_specs(3, -PI, PI)
    mar = resolve_marginals(np.zeros((2, 3)), specs)
    X, _ = physical_design(600, mar, sampler="lhs", seed=6)
    Y = _ishigami(X)
    coarse = pce_fit(X, Y, 1, marginals=specs)
    fine = pce_fit(X, Y, 8, marginals=specs)
    assert coarse["loo_relative_error"] > 5.0 * fine["loo_relative_error"]
    assert fine["loo_relative_error"] < 0.1
    assert fine["r2"] > coarse["r2"]


def test_band_is_ordered_and_matches_surrogate_moments():
    specs = _normal_specs(2)
    mar = resolve_marginals(np.zeros((2, 2)), specs)
    X, _ = physical_design(500, mar, sampler="qmc", seed=3)
    Y = 1.0 + X[:, 0] + 0.2 * X[:, 1] ** 2
    fit = pce_fit(X, Y, 2, marginals=specs)
    band = pce_band(fit, probs=(0.025, 0.5, 0.975), n_surrogate=8192, seed=1)
    assert band["lower"] < band["median"] < band["upper"]
    assert band["width"] > 0.0
    # a 95% band of a roughly normal output is about 3.9 standard deviations
    assert band["width"] / (3.9 * fit["std"]) == pytest.approx(1.0, abs=0.15)


def test_projection_and_regression_agree_on_smooth_response():
    specs = _uniform_specs(3, -1.0, 1.0)
    grid = quadrature_design(specs, kind="tensor", n_per_dim=6)
    Xp = grid["X"]
    Yp = 1.0 + 2.0 * Xp[:, 0] + 0.5 * Xp[:, 1] - 0.3 * Xp[:, 0] * Xp[:, 2] + 0.2 * Xp[:, 2] ** 2
    proj = pce_fit(Xp, Yp, 3, marginals=specs, weights=grid["weights"])

    mar = resolve_marginals(np.zeros((2, 3)), specs)
    Xr, _ = physical_design(800, mar, sampler="lhs", seed=9)
    Yr = 1.0 + 2.0 * Xr[:, 0] + 0.5 * Xr[:, 1] - 0.3 * Xr[:, 0] * Xr[:, 2] + 0.2 * Xr[:, 2] ** 2
    reg = pce_fit(Xr, Yr, 3, marginals=specs)

    assert proj["mean"] == pytest.approx(reg["mean"], rel=1e-3)
    assert proj["std"] == pytest.approx(reg["std"], rel=1e-3)
    assert np.allclose(proj["sobol_first"], reg["sobol_first"], atol=1e-3)
    assert proj["n_samples"] == Xp.shape[0]


# --------------------------------------------------------------------------- #
# 4. platform rules: zero domain semantics + case-driven pipeline
# --------------------------------------------------------------------------- #
BANNED_VOCABULARY = (
    "ship", "hull", "vessel", "tanker", "propuls", "seakeeping", "holtrop",
    "resistance", "froude", "draft", "waterline", "bow", "stern", "rudder",
    "manoeuvr", "manoeuvr", "heave", "pitch roll", "knot", "sea state",
)


def test_pce_package_has_no_domain_semantics():
    """The kernel must stay a pure mathematics/UQ module (platform iron rule)."""
    offenders = []
    for path in sorted((REPO / "tools" / "pce").glob("*.py")):
        text = path.read_text().lower()
        for word in BANNED_VOCABULARY:
            if re.search(rf"\b{word}", text):
                offenders.append((path.name, word))
    assert offenders == [], f"domain vocabulary leaked into tools/pce: {offenders}"


def test_case_runner_end_to_end_on_injected_case(tmp_path):
    """The generic runner reproduces the full pipeline from a case file."""
    model_file = str(Path(__file__).resolve().parent / "p152w5_n07_synthetic_models.py")
    case = {
        "case_id": "pytest_synthetic_case",
        "title": "synthetic three-input case (test fixture)",
        "model": {
            "path": "p152w5_n07_synthetic_models:quadratic_response",
            "file": model_file,
            "call_style": "kwargs",
            "output": None,
        },
        "inputs": [
            {"name": "x1", "family": "normal", "mean": 0.0, "std": 1.0},
            {"name": "x2", "family": "normal", "mean": 0.0, "std": 1.0},
            {"name": "x3", "family": "normal", "mean": 0.0, "std": 1.0},
        ],
        "design": {"mode": "regression", "degree": 2, "n_samples": 48, "sampler": "lhs", "seed": 11},
        "band": {"probs": [0.025, 0.5, 0.975], "n_surrogate": 4096, "seed": 3},
        "mc": {"n": 10000, "sampler": "lhs", "seed": 20261110,
               "reference_n": 50000, "reference_sampler": "qmc", "reference_seed": 7},
        "thresholds": {"moment_rel_tol": 0.03, "min_eval_speedup": 100.0, "min_r2": 0.99},
    }
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case))
    from tools.pce.case_runner import load_case

    ledger = run_case(load_case(path), reps=1, with_sweep=False)

    assert ledger["case_id"] == "pytest_synthetic_case"
    assert ledger["pce"]["method"] == "regression"
    assert ledger["acceleration"]["speedup_model_evaluations"] > 100.0
    assert ledger["errors"]["vs_reference"]["rel_err_std"] < 0.03
    assert ledger["criteria"]["pce_r2"]["pass"] is True
    assert ledger["all_criteria_pass"] is True
    # every number the report would quote is in the ledger, nothing is implicit
    assert set(ledger["pce"]["sobol_first"]) == {"x1", "x2", "x3"}
    assert ledger["case_sha256"] == ledger["case_sha256"].lower()