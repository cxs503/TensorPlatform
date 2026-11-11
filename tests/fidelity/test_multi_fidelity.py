"""pytest suite for the fidelity reliability layer (``tools/fidelity``).

Domain neutrality: every fixture here is a synthetic analytic function of a generic
"quantity"; there is no domain vocabulary and no domain data in this file (the C5
criterion is checked mechanically by ``test_module_has_no_domain_vocabulary``).

Coverage map
------------
TrustDomain              box construction, normalised outside distance, inflation,
                         width floor, dimension validation
conformal_quantile       finite-sample index, saturation reporting, input validation
TierModel                bias / quantile ordering, credible-interval coverage (C2),
                         shrunk-interval negative control (C2)
FidelityChain            propagated bound is conservative and finite (C1), the
                         under-stated-sensitivity falsification case
ExtrapolationLaw         p >= 1 constraint, envelope over the study, three-distance
                         power-law verification (C4)
FidelitySwitchGate       overshoot trigger rate and in-domain false-alarm rate (C3),
                         tolerance rule, credible-domain disjointness guard
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from fidelity import (  # noqa: E402
    CalibrationSet,
    ChainLink,
    ExtrapolationLaw,
    FidelityChain,
    FidelitySwitchGate,
    TierModel,
    TrustDomain,
    calibrate_tier,
    check_conservativeness,
    conformal_quantile,
    fit_extrapolation_law,
    interval_coverage,
)


# --------------------------------------------------------------------------- fixtures
X0 = 0.5
DOMAIN_LO, DOMAIN_HI = 0.35, 0.65
WIDTH = DOMAIN_HI - DOMAIN_LO


def truth(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float).ravel()
    return np.exp(0.8 * x) + 0.3 * np.sin(4.0 * x)


def _derivatives(x: np.ndarray, order: int = 6) -> list:
    """Analytic derivatives of ``truth`` up to ``order`` (about any point)."""
    x = np.asarray(x, dtype=float).ravel()
    shots = [
        np.exp(0.8 * x) + 0.3 * np.sin(4.0 * x),
        0.8 * np.exp(0.8 * x) + 1.2 * np.cos(4.0 * x),
        0.64 * np.exp(0.8 * x) - 4.8 * np.sin(4.0 * x),
        0.512 * np.exp(0.8 * x) - 19.2 * np.cos(4.0 * x),
        0.4096 * np.exp(0.8 * x) + 76.8 * np.sin(4.0 * x),
        0.32768 * np.exp(0.8 * x) + 307.2 * np.cos(4.0 * x),
        -0.262144 * np.exp(0.8 * x) - 1228.8 * np.sin(4.0 * x),
    ]
    return shots[: order + 1]


def taylor_tier(order: int):
    """Truncated expansion of ``truth`` about ``X0``: order 1 = crude, 5 = accurate."""

    def evaluate(X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        x = X.reshape(-1) if X.ndim > 1 else X
        out = np.zeros(x.size)
        for k, coeff in enumerate(_derivatives(np.array([X0]), order)):
            out += coeff[0] / math.factorial(k) * (x - X0) ** k
        return out

    return evaluate


def _grid(lo: float, hi: float, n: int) -> np.ndarray:
    return np.linspace(lo, hi, n).reshape(-1, 1)


def build_calibration(n: int = 121) -> CalibrationSet:
    X = _grid(DOMAIN_LO, DOMAIN_HI, n)
    ref = truth(X)
    return CalibrationSet(
        X=X,
        reference=ref,
        tier_values={0: taylor_tier(1)(X), 1: taylor_tier(3)(X), 2: taylor_tier(5)(X)},
    )


def build_study(order: int, distances=(0.1, 0.2, 0.4)) -> tuple:
    """Extrapolation study: errors of the tier at ``d`` domain widths beyond the box."""
    fn = taylor_tier(order)
    xs = np.array([DOMAIN_HI + d * WIDTH for d in distances]).reshape(-1, 1)
    return np.array(distances, dtype=float), np.abs(fn(xs) - truth(xs))


def build_chain(n: int = 121, *, study_rank0: bool = True) -> FidelityChain:
    cal = build_calibration(n)
    tiers = {}
    for rank, order in enumerate((1, 3, 5)):
        study = build_study(order) if (study_rank0 or rank > 0) else None
        tiers[rank] = calibrate_tier(rank, f"tier{rank}", taylor_tier(order), cal,
                                     study=study)
    return FidelityChain(tiers, links={0: ChainLink(1 / 3, 1.0),
                                       1: ChainLink(1 / 3, 1.0),
                                       2: ChainLink(1 / 3, 1.0)})


@pytest.fixture(scope="module")
def chain() -> FidelityChain:
    return build_chain()


# --------------------------------------------------------------------------- domain
def test_trust_domain_distance_semantics():
    dom = TrustDomain([0.0, 0.0], [1.0, 2.0])
    X = np.array([[0.5, 1.0], [1.0, 2.0], [1.2, 1.0], [0.5, 2.4], [-0.25, 3.0]])
    d = dom.distance(X)
    assert d[0] == 0.0 and d[1] == 0.0
    # 0.2 of the first axis width (1.0) outside
    assert d[2] == pytest.approx(0.2)
    # 0.4 of the second axis width (2.0) outside
    assert d[3] == pytest.approx(0.2)
    # worst overshoot over axes: 0.25 (axis 1) vs 0.5 (axis 2)
    assert d[4] == pytest.approx(0.5)


def test_trust_domain_requires_consistent_bounds_and_dimension():
    with pytest.raises(ValueError):
        TrustDomain([0.0, 1.0], [1.0])
    with pytest.raises(ValueError):
        TrustDomain([1.0], [0.0])
    dom = TrustDomain([0.0, 0.0], [1.0, 1.0])
    with pytest.raises(ValueError):
        dom.distance(np.zeros((3, 3)))


def test_trust_domain_scaling_and_degenerate_axis():
    dom = TrustDomain([0.0], [1.0]).scaled(0.5)   # 50 % wider on each side
    assert dom.lo[0] == pytest.approx(-0.25) and dom.hi[0] == pytest.approx(1.25)
    flat = TrustDomain([2.0, 0.0], [2.0, 1.0])    # zero width on axis 0
    assert np.all(np.isfinite(flat.width()))
    assert flat.distance(np.array([[2.0, 0.5]]))[0] == 0.0
    assert flat.distance(np.array([[2.5, 0.5]]))[0] > 1e6


def test_trust_domain_from_points_with_margin_matches_scaled():
    X = np.array([[0.0], [1.0]])
    a = TrustDomain.from_points(X, margin=0.2)
    b = TrustDomain.from_points(X).scaled(0.2)
    assert np.allclose(a.lo, b.lo) and np.allclose(a.hi, b.hi)


# --------------------------------------------------------------------------- conformal
def test_conformal_quantile_finite_sample_index_and_saturation():
    scores = np.arange(1, 21, dtype=float)          # 1..20
    q, saturated = conformal_quantile(scores, 0.90)
    assert saturated is False and q == pytest.approx(19.0)   # ceil(21*0.9) = 19 -> s[18]
    q, saturated = conformal_quantile(scores, 0.95)
    assert saturated is False and q == pytest.approx(20.0)   # ceil(21*0.95) = 20
    q, saturated = conformal_quantile(scores[:10], 0.99)     # ceil(11*0.99) = 11 > 10
    assert saturated is True and q == pytest.approx(10.0)
    inf_q, _ = conformal_quantile(scores[:10], 0.99, fallback_max=False)
    assert not np.isfinite(inf_q)


def test_conformal_quantile_rejects_invalid_input():
    with pytest.raises(ValueError):
        conformal_quantile([], 0.9)
    with pytest.raises(ValueError):
        conformal_quantile([1.0, 2.0], 1.5)
    with pytest.raises(ValueError):
        conformal_quantile([-1.0, 2.0], 0.9)


def test_calibration_set_validates_shapes_and_ranks():
    with pytest.raises(ValueError):
        CalibrationSet(np.zeros((2, 1)), np.zeros(3), {0: np.zeros(2)})
    with pytest.raises(ValueError):
        CalibrationSet(np.zeros((2, 1)), np.zeros(2), {})
    with pytest.raises(ValueError):
        CalibrationSet(np.zeros((2, 1)), np.zeros(2), {-1: np.zeros(2)})


# --------------------------------------------------------------------------- tiers
def test_tier_bias_and_quantile_ordering(chain):
    for rank in chain.ranks:
        tier = chain.tier(rank)
        assert tier.q_report <= tier.q_guard + 1e-12
        assert tier.lipschitz >= 0.0
        assert tier.n_calibration == 121


def test_higher_tier_is_more_accurate(chain):
    X = _grid(DOMAIN_LO, DOMAIN_HI, 81)
    errs = [float(np.max(np.abs(chain.predict(r, X) - truth(X)))) for r in chain.ranks]
    assert errs[0] > errs[1] > errs[2]


def test_credible_interval_coverage_in_domain(chain):
    """C2 positive control: 95 % interval must cover the truth on >= 90 % of points."""
    X = _grid(DOMAIN_LO, DOMAIN_HI, 601)[17:]
    rep = interval_coverage(chain, 0, X, truth(X))
    assert rep["coverage"] >= 0.90
    assert rep["mean_halfwidth"] < 1.0


def test_shrunk_interval_fails_coverage(chain):
    """C2 negative control: a deliberately halved interval must fall below 80 %."""
    X = _grid(DOMAIN_LO, DOMAIN_HI, 601)[17:]
    rep = interval_coverage(chain, 0, X, truth(X), halfwidth_scale=0.5)
    assert rep["coverage"] < 0.80
    assert rep["mean_halfwidth"] < 0.5


def test_interval_rejects_nonpositive_scale(chain):
    with pytest.raises(ValueError):
        chain.credible_interval(0, np.zeros((2, 1)), halfwidth_scale=0.0)


# --------------------------------------------------------------------------- chain / C1
def test_composed_bound_is_conservative_and_finite(chain):
    """C1: propagated bound >= realised error on every point, and not absurdly loose."""
    X = _grid(DOMAIN_LO, DOMAIN_HI, 401)[7:]
    pred = chain.composed_prediction(X)
    bound = chain.composed_bound(X, mode="guard")
    audit = check_conservativeness(bound, pred - truth(X))
    assert np.all(np.isfinite(bound))
    assert audit["violations"] == 0
    assert audit["ratio_p95"] < 200.0


def test_propagation_carries_downstream_sensitivity():
    """The bound must scale with the injected Lipschitz constant of the functional."""
    cal = build_calibration()
    tiers = {r: calibrate_tier(r, f"t{r}", taylor_tier(o), cal)
             for r, o in enumerate((1, 3, 5))}
    X = _grid(DOMAIN_LO, DOMAIN_HI, 201)
    base = FidelityChain(tiers, links={r: ChainLink(1.0, 1.0) for r in tiers})
    amplified = FidelityChain(tiers, links={r: ChainLink(1.0, 4.0) for r in tiers})
    assert np.allclose(amplified.composed_bound(X),
                       4.0 * base.composed_bound(X), rtol=1e-12)


def test_understated_sensitivity_is_detectable(chain):
    """Falsification: with sensitivity L = 0.05 the guard must report violations."""
    cal = build_calibration()
    tiers = {r: calibrate_tier(r, f"t{r}", taylor_tier(o), cal)
             for r, o in enumerate((1, 3, 5))}
    weak = FidelityChain(tiers, links={0: ChainLink(1.0, 0.05)})
    X = _grid(DOMAIN_LO, DOMAIN_HI, 201)
    pred = weak.composed_prediction(X, ranks=[0])
    bound = weak.composed_bound(X, ranks=[0])
    audit = check_conservativeness(bound, pred - truth(X))
    assert audit["violations"] > 0
    assert audit["violation_rate"] > 0.5


def test_chain_requires_contiguous_ranks(chain):
    tiers = {r: chain.tier(r) for r in (0, 2)}
    with pytest.raises(ValueError):
        FidelityChain(tiers)
    with pytest.raises(KeyError):
        chain.tier(7)


def test_composed_prediction_matches_manual_sum(chain):
    X = _grid(DOMAIN_LO, DOMAIN_HI, 11)
    manual = sum(chain.link(r).weight * (chain.predict(r, X) - chain.tier(r).bias)
                 for r in chain.ranks)
    assert np.allclose(chain.composed_prediction(X), manual, rtol=1e-12)


def test_tier_model_can_be_constructed_directly():
    dom = TrustDomain([0.0], [1.0])
    tier = TierModel(rank=0, name="manual", evaluate=lambda X: np.zeros(
        np.asarray(X).reshape(-1).size), bias=0.0, q_report=0.1, q_guard=0.2,
        report_level=0.95, alarm_level=0.99, saturated=(False, False), lipschitz=0.0,
        domain=dom, calibration_points=np.zeros((1, 1)), residuals=np.zeros(1),
        law=ExtrapolationLaw(C=0.0, p=1.0), n_calibration=1)
    assert np.allclose(tier.bound(np.zeros((3, 1)), mode="report"), 0.1)


# --------------------------------------------------------------------------- C4
def test_extrapolation_law_enforces_power_law_minimum():
    d = np.array([0.1, 0.2, 0.4])
    e = np.array([0.4, 0.2, 0.1])      # decaying errors: slope < 0
    law = fit_extrapolation_law(d, e)
    assert law.p >= 1.0
    assert law.C > 0.0


def test_extrapolation_law_envelope_covers_study_and_is_monotone():
    d = np.array([0.1, 0.2, 0.4])
    e = np.array([1.0e-3, 2.5e-3, 6.0e-3])
    law = fit_extrapolation_law(d, e, safety=1.5)
    assert law.p >= 1.0
    assert np.all(law.eps(d) >= e)
    grid = np.linspace(0.05, 0.8, 50)
    assert np.all(np.diff(law.eps(grid)) >= 0)


def test_three_distance_power_law_verification(chain):
    """C4: at three extrapolation distances the fitted law covers the realised error."""
    distances = np.array([0.1, 0.2, 0.4])
    tier = chain.tier(0)
    law = tier.law
    assert law.source == "study" and law.p >= 1.0
    xs = (DOMAIN_HI + distances * WIDTH).reshape(-1, 1)
    actual = np.abs(chain.predict(0, xs) - truth(xs))
    assert np.all(law.eps(distances) >= actual)
    # the risk term is inert inside the calibration box
    assert np.allclose(tier.extrapolation_risk(_grid(DOMAIN_LO, DOMAIN_HI, 7)), 0.0)


def test_extrapolation_law_validation():
    with pytest.raises(ValueError):
        ExtrapolationLaw(C=-1.0, p=1.0)
    with pytest.raises(ValueError):
        ExtrapolationLaw(C=1.0, p=0.5)
    with pytest.raises(ValueError):
        fit_extrapolation_law([0.0], [1.0])


# --------------------------------------------------------------------------- C3 gate
def test_gate_triggers_on_twenty_percent_overshoot(chain):
    gate = chain.gate()
    xs = (DOMAIN_HI + 0.2 * WIDTH + 0.05 * WIDTH * np.arange(10)).reshape(-1, 1)
    decisions = gate.advice(xs)
    assert all(d.escalate for d in decisions)
    assert all("domain_exceeded" in d.reasons for d in decisions)
    assert all(d.target_rank == chain.top_rank for d in decisions)


def test_gate_false_alarm_rate_inside_domain(chain):
    gate = chain.gate()
    rng = np.random.default_rng(20260101)
    X = DOMAIN_LO + (DOMAIN_HI - DOMAIN_LO) * rng.random((500, 1))
    rate = gate.escalation_rate(X)
    assert rate <= 0.05


def test_gate_keeps_coarse_tier_where_it_is_admissible(chain):
    gate = chain.gate()
    X = _grid(DOMAIN_LO + 0.01, DOMAIN_HI - 0.01, 20)
    decisions = gate.advice(X)
    assert sum(not d.escalate for d in decisions) >= 19
    assert all(d.target_rank == 0 for d in decisions if not d.escalate)


def test_gate_tolerance_rule_is_opt_in(chain):
    X = _grid(0.45, 0.55, 5)
    loose = chain.gate(rel_tol=None).advice(X)
    strict = chain.gate(rel_tol=1e-6).advice(X)
    assert not any(d.escalate for d in loose)
    assert all(d.escalate for d in strict)
    assert all("tolerance_exceeded" in d.reasons for d in strict)


def aliased_tier(amplitude: float, period: float):
    """Model error with structure finer than the calibration grid (grid aliasing)."""

    def evaluate(X: np.ndarray) -> np.ndarray:
        x = np.asarray(X, dtype=float).reshape(-1)
        return truth(x) + amplitude * np.sin(2 * np.pi * (x - DOMAIN_LO) / period)

    return evaluate


def test_gate_credible_domain_guard_fires_on_aliased_error():
    """The guard must fire without an out-of-domain point.

    The coarse calibration grid is placed at integer multiples of half the error
    period, so the sampled residuals are (numerically) zero while the realised error
    between nodes is the full amplitude.  Two tiers disagreeing beyond their alarm
    quantiles is enough for ``fidelity_insufficient``.
    """
    n = 9
    period = 2.0 * WIDTH / (n - 1)          # half-period equals the node spacing
    X = _grid(DOMAIN_LO, DOMAIN_HI, n)
    cal = CalibrationSet(X, truth(X), {0: aliased_tier(0.05, period)(X),
                                       1: truth(X)})
    tiers = {0: calibrate_tier(0, "aliased", aliased_tier(0.05, period), cal),
             1: calibrate_tier(1, "reference", truth, cal)}
    chain = FidelityChain(tiers)
    assert chain.tier(0).q_guard < 1e-9        # the grid cannot see the error
    Xt = _grid(DOMAIN_LO, DOMAIN_HI, 61)
    flags = [d.fidelity_insufficient for d in FidelitySwitchGate(chain).advice(Xt)]
    assert any(flags)


def test_gate_flags_points_beyond_the_extrapolation_study(chain):
    """Beyond the studied distance the risk law itself is unvalidated -> escalate."""
    gate = chain.gate()
    study_max = chain.tier(0).law.study_max_distance
    far = np.array([[DOMAIN_HI + 1.2 * study_max * WIDTH]])
    near = np.array([[DOMAIN_HI + 0.5 * study_max * WIDTH]])
    far_decision = gate.advice(far)[0]
    near_decision = gate.advice(near)[0]
    assert "extrapolation_study_exceeded" in far_decision.reasons
    assert "extrapolation_study_exceeded" not in near_decision.reasons
    assert far_decision.escalate and near_decision.escalate   # both outside the box


def test_gate_rejects_invalid_settings(chain):
    with pytest.raises(ValueError):
        chain.gate(max_distance=-0.1)
    with pytest.raises(ValueError):
        chain.gate(rel_tol=0.0)


# --------------------------------------------------------------------------- hypercube
def truth_2d(X: np.ndarray) -> np.ndarray:
    X = np.atleast_2d(np.asarray(X, dtype=float))
    return np.exp(0.5 * X[:, 0]) * np.cos(1.5 * X[:, 1]) + 0.2 * X[:, 0] * X[:, 1]


def reduced_2d(a: float, b: float):
    """A reduced model: one coefficient slightly off -> smooth discrepancy field."""

    def evaluate(X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=float))
        return np.exp(a * X[:, 0]) * np.cos(b * X[:, 1]) + 0.2 * X[:, 0] * X[:, 1]

    return evaluate


def test_two_dimensional_chain_coverage_and_bound():
    rng = np.random.default_rng(7)
    Xc = rng.random((225, 2))
    cal = CalibrationSet(Xc, truth_2d(Xc), {
        0: reduced_2d(0.40, 1.30)(Xc),
        1: reduced_2d(0.46, 1.42)(Xc),
        2: truth_2d(Xc),
    })
    tiers = {0: calibrate_tier(0, "a", reduced_2d(0.40, 1.30), cal),
             1: calibrate_tier(1, "b", reduced_2d(0.46, 1.42), cal),
             2: calibrate_tier(2, "c", truth_2d, cal)}
    chain = FidelityChain(tiers)
    Xt = rng.random((300, 2))
    rep = interval_coverage(chain, 0, Xt, truth_2d(Xt))
    assert rep["coverage"] >= 0.90
    audit = check_conservativeness(chain.tier_bound(0, Xt),
                                   chain.predict(0, Xt) - truth_2d(Xt))
    assert audit["violations"] == 0
    assert chain.gate().escalation_rate(Xt) <= 0.05


# --------------------------------------------------------------------------- C5
def test_module_has_no_domain_vocabulary():
    """C5: the mechanism carries no domain vocabulary (shared lexicon scan)."""
    from evidence_domain_scan import load_lexicon, scan_paths

    result = scan_paths(["tools/fidelity", "tests/fidelity"], lexicon=load_lexicon(),
                        root=ROOT)
    assert result["scanned_files"] >= 3
    assert result["hits"] == []


def test_repeated_calls_are_deterministic(chain):
    X = np.linspace(0.4, 1.0, 25).reshape(-1, 1)
    first = chain.composed_bound(X)
    second = chain.composed_bound(X)
    assert np.array_equal(first, second)
    assert [d.as_dict() for d in chain.gate().advice(X)] == \
           [d.as_dict() for d in chain.gate().advice(X)]
