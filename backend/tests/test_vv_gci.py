"""Verification suite for the solution-verification (GCI) toolbox — mission 1700.

Rationale
---------
``backend/services/vv/`` answers *"how much of this number is discretisation
error?"*, a question the platform previously could not answer at all (grep for
``Richardson`` / ``GCI`` returned zero hits before this mission; campaign wave-9
coverage analysis lists ``numerical V&V / GCI`` as a zero-node domain).  A
verification tool that is itself unverified is worse than none, so this suite

1. ``TestEstimatorExactness`` — feeds the estimator sequences whose order is
   *known by construction* (``f(h) = f_exact + C h^p``) over integer and
   non-integer ``p``, uniform and unequal refinement ratios, monotone and
   oscillatory convergence.  The observed order must be recovered to machine
   precision and the Richardson value must equal ``f_exact`` exactly.
2. ``TestAnalyticBenchmarks`` — runs three genuinely discrete problems with
   closed-form solutions (central-difference convection-diffusion, 4th-order
   compact Numerov Poisson, 5-point 2D Poisson), asserting the observed order,
   and — the key falsifiable statement — that the reported GCI band actually
   covers the *true* discretisation error.
3. ``TestFalsification`` — negative controls.  A tool that always says "passed"
   is worthless: divergent, round-off-limited and non-asymptotic sequences must
   be rejected, and a GCI band that misses the true error must be reported as
   such.
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from services.vv import (  # noqa: E402
    DIVERGENCE,
    INDETERMINATE,
    MONOTONE,
    OSCILLATORY,
    Level,
    assess_convergence,
    gci_pair,
    observed_order,
    richardson_value,
)
from services.vv.cases import (  # noqa: E402
    CASES,
    convection_diffusion_1d_exact,
    poisson_2d_exact,
    power_law,
)


# ---------------------------------------------------------------------------
# 1. Estimator exactness on constructed sequences
# ---------------------------------------------------------------------------
class TestEstimatorExactness:
    @pytest.mark.parametrize("p", [0.5, 1.0, 1.7, 2.0, 3.0, 4.0])
    def test_recovers_constructed_order(self, p):
        """f(h) = 1 + 0.05 h^p, r = 2: observed order must equal p."""
        vals = [power_law(h, p=p, exact=1.0, c=0.05) for h in (1 / 32, 1 / 16, 1 / 8)]
        p_obs = observed_order(vals[0], vals[1], vals[2], 2.0, 2.0)
        assert p_obs == pytest.approx(p, rel=1e-8, abs=1e-10)

    def test_unequal_refinement_ratios(self):
        """r21 = 1.5, r32 = 2.0 must be handled without assuming uniform r."""
        h = [1 / 27, 1 / 18, 1 / 9]  # r21 = 1.5, r32 = 2.0
        vals = [power_law(x, p=2.0) for x in h]
        p_obs = observed_order(vals[0], vals[1], vals[2], h[1] / h[0], h[2] / h[1])
        assert p_obs == pytest.approx(2.0, rel=1e-8)

    def test_uniform_ratio_closed_form_consistency(self):
        """For uniform r the iterative form must match the closed form."""
        h = [1 / 40, 1 / 20, 1 / 10]
        vals = [power_law(x, p=3.0) for x in h]
        p_iter = observed_order(vals[0], vals[1], vals[2], 2.0, 2.0)
        d21, d32 = vals[1] - vals[0], vals[2] - vals[1]
        p_closed = math.log(abs(d32 / d21)) / math.log(2.0)
        assert p_iter == pytest.approx(p_closed, rel=1e-8)

    @pytest.mark.parametrize("p", [1.0, 2.0, 4.0])
    def test_richardson_value_is_exact_for_power_law(self, p):
        vals = [power_law(h, p=p, exact=2.5, c=-0.3) for h in (1 / 64, 1 / 32)]
        f_ext = richardson_value(vals[0], vals[1], 2.0, p)
        assert f_ext == pytest.approx(2.5, rel=1e-12)

    def test_gci_scales_as_expected(self):
        """GCI of the fine grid = Fs * |e1| / |f1| with e1 = C h^p."""
        p, c = 2.0, 0.05
        f1, f2 = power_law(1 / 32, p=p, c=c), power_law(1 / 16, p=p, c=c)
        out = gci_pair(f1, f2, 2.0, p, fs=1.25)
        e1 = c * (1 / 32) ** p
        assert out["error_abs"] == pytest.approx(e1, rel=1e-12)
        assert out["gci_relative"] == pytest.approx(1.25 * e1 / f1, rel=1e-12)

    def test_oscillatory_sequence_detected_with_correct_order(self):
        """Alternating-sign errors must be classified oscillatory, order intact."""
        p, c, exact = 2.0, 0.05, 1.0
        vals = [
            exact + c * (1 / 32) ** p,
            exact - c * (1 / 16) ** p,
            exact + c * (1 / 8) ** p,
        ]
        rep = assess_convergence(
            [Level(1 / 32, vals[0]), Level(1 / 16, vals[1]), Level(1 / 8, vals[2])],
            quantity="osc",
            exact=exact,
            expected_order=p,
        )
        assert rep.verdict == OSCILLATORY
        assert rep.observed_order == pytest.approx(p, rel=1e-8)
        assert rep.convergence_ratio is not None and -1.0 < rep.convergence_ratio < 0.0

    def test_asymptotic_indicator_is_unity_for_power_law(self):
        vals = [power_law(h, p=2.0) for h in (1 / 64, 1 / 32, 1 / 16)]
        rep = assess_convergence([Level(h, v) for h, v in zip((1 / 64, 1 / 32, 1 / 16), vals)])
        assert rep.asymptotic_ratio == pytest.approx(1.0, rel=1e-8)
        assert rep.asymptotic_ok is True

    def test_input_order_does_not_matter(self):
        vals = [power_law(h, p=2.0) for h in (1 / 64, 1 / 32, 1 / 16)]
        hs = (1 / 64, 1 / 32, 1 / 16)
        a = assess_convergence([Level(h, v) for h, v in zip(hs, vals)])
        b = assess_convergence([Level(h, v) for h, v in zip(hs[::-1], vals[::-1])])
        assert a.observed_order == pytest.approx(b.observed_order)
        assert a.gci_fine == pytest.approx(b.gci_fine)


# ---------------------------------------------------------------------------
# 2. Analytic benchmark problems (exact solutions available)
# ---------------------------------------------------------------------------
class TestAnalyticBenchmarks:
    @pytest.mark.parametrize("name", sorted(CASES))
    def test_observed_order_matches_theory(self, name):
        case = CASES[name]
        series = case.series()
        rep = assess_convergence(
            [Level(h, v) for h, v in series],
            quantity=case.quantity,
            exact=case.exact,
            expected_order=case.nominal_order,
        )
        assert rep.observed_order == pytest.approx(case.nominal_order, rel=0.05)
        assert rep.verdict == MONOTONE

    @pytest.mark.parametrize("name", sorted(CASES))
    def test_gci_band_covers_true_error(self, name):
        """The only falsifiable claim: |f_fine - f_exact| <= GCI_fine."""
        case = CASES[name]
        series = case.series()
        rep = assess_convergence(
            [Level(h, v) for h, v in series],
            quantity=case.quantity,
            exact=case.exact,
            expected_order=case.nominal_order,
        )
        assert rep.true_error_covered is True
        assert rep.true_error_ratio_to_gci is not None
        # asymptotic + Fs = 1.25 => true error is ~1/1.25 = 0.8 of the GCI band
        assert 0.55 <= rep.true_error_ratio_to_gci <= 1.0
        assert rep.passed is True

    @pytest.mark.parametrize("name", sorted(CASES))
    def test_extrapolation_beats_fine_grid(self, name):
        """Richardson extrapolation must reduce the error, not merely move it."""
        case = CASES[name]
        series = case.series()
        rep = assess_convergence(
            [Level(h, v) for h, v in series],
            quantity=case.quantity,
            exact=case.exact,
            expected_order=case.nominal_order,
        )
        err_fine = abs(rep.values[0] - case.exact)
        err_ext = abs(rep.extrapolated_value - case.exact)
        assert err_ext < err_fine
        assert err_ext / err_fine < 0.05  # O(h^p) error removed

    @pytest.mark.parametrize("n", [16, 32, 64])
    def test_poisson_2d_center_value_matches_discrete_eigenvalue(self, n):
        """Exact closed form of the *discrete* solution, not just its asymptote.

        For the eigenmode sin(pi x) sin(pi y) the 5-point operator has the exact
        eigenvalue lambda_h = 4(1 - cos(pi h)) / h^2, hence the discrete centre
        value is u_h = 2 pi^2 / lambda_h = (pi h)^2 / (2 (1 - cos(pi h)))
        = 1 + (pi h)^2/12 + O(h^4).
        """
        h, qoi = CASES["poisson_2d_5pt"].solve(n)
        discrete = (math.pi * h) ** 2 / (2.0 * (1.0 - math.cos(math.pi * h)))
        assert qoi == pytest.approx(discrete, rel=1e-12)
        assert qoi == pytest.approx(poisson_2d_exact(), abs=6e-3)
        # captured discretisation error is of the advertised order
        assert qoi - 1.0 == pytest.approx((math.pi * h) ** 2 / 12.0, rel=2e-2)

    def test_second_order_scaling_of_error(self):
        """Halving h must quarter the error (order 2): 4 +/- 5%."""
        c1, c2 = CASES["conv_diff_1d_p5"].series((40, 80))
        e1 = abs(c1[1] - convection_diffusion_1d_exact(0.5))   # h = 1/40
        e2 = abs(c2[1] - convection_diffusion_1d_exact(0.5))   # h = 1/80
        assert e2 / e1 == pytest.approx(0.25, rel=0.05)  # halving h quarters the error


# ---------------------------------------------------------------------------
# 3. Falsification / negative controls
# ---------------------------------------------------------------------------
class TestFalsification:
    def test_divergent_sequence_rejected(self):
        """Successive changes *growing* is divergence: must not be certified."""
        rep = assess_convergence(
            [Level(1 / 32, 1.0), Level(1 / 16, 1.5), Level(1 / 8, 1.6)], quantity="div"
        )
        assert rep.verdict == DIVERGENCE
        assert rep.passed is False
        assert any("divergent" in w for w in rep.warnings)

    def test_round_off_limited_sequence_rejected(self):
        rep = assess_convergence(
            [Level(1 / 32, 81.3), Level(1 / 16, 81.30000000000001), Level(1 / 8, 89.0)],
            quantity="toi",
        )
        assert rep.passed is False
        assert rep.verdict in (INDETERMINATE, DIVERGENCE)

    def test_non_asymptotic_order_flagged(self):
        """A sequence obeying a *mixed* order (h + h^2) is not an h^2 sequence."""
        def mixed(h):
            return 1.0 + 0.4 * h + 0.05 * h**2

        hs = (1 / 8, 1 / 4, 1 / 2)
        rep = assess_convergence(
            [Level(h, mixed(h)) for h in hs], quantity="mixed", expected_order=2.0
        )
        assert rep.passed is False
        assert any(w.startswith("observed order") for w in rep.warnings)

    def test_gci_that_misses_true_error_is_reported(self):
        """Second-order-looking data whose exact value is offset: band not a bound."""
        p, c, exact, offset = 2.0, 0.05, 1.0, 3.0e-3
        vals = [exact + offset + c * h**p for h in (1 / 32, 1 / 16, 1 / 8)]
        rep = assess_convergence(
            [Level(h, v) for h, v in zip((1 / 32, 1 / 16, 1 / 8), vals)],
            quantity="biased",
            exact=exact,
            expected_order=p,
        )
        assert rep.observed_order == pytest.approx(p, rel=1e-8)
        assert rep.true_error_covered is False
        assert rep.passed is False

    def test_degenerate_identical_solutions_raise(self):
        with pytest.raises(ValueError):
            observed_order(1.0, 1.0, 2.0, 2.0, 2.0)

    def test_single_level_rejected(self):
        with pytest.raises(ValueError):
            assess_convergence([Level(0.1, 1.0)])

    def test_bad_inputs_rejected(self):
        with pytest.raises(ValueError):
            Level(0.0, 1.0)
        with pytest.raises(ValueError):
            Level(1.0, float("nan"))
        with pytest.raises(ValueError):
            assess_convergence([Level(0.1, 1.0), Level(0.1, 2.0)])


# ---------------------------------------------------------------------------
# 4. Two-level fallback (Fs = 3) and API contract
# ---------------------------------------------------------------------------
class TestTwoLevelFallback:
    def test_two_levels_use_fs_three_and_nominal_order(self):
        vals = [power_law(1 / 32, p=2.0), power_law(1 / 16, p=2.0)]
        rep = assess_convergence(
            [Level(1 / 32, vals[0]), Level(1 / 16, vals[1])],
            quantity="two",
            exact=1.0,
            expected_order=2.0,
        )
        assert rep.factor_of_safety == 3.0
        assert rep.observed_order == pytest.approx(2.0)
        assert rep.asymptotic_ratio is None
        assert any("only two levels" in w for w in rep.warnings)

    def test_report_serialises(self):
        vals = [power_law(h, p=2.0) for h in (1 / 32, 1 / 16, 1 / 8)]
        rep = assess_convergence([Level(h, v) for h, v in zip((1 / 32, 1 / 16, 1 / 8), vals)])
        d = rep.to_dict()
        assert set(d) >= {
            "verdict",
            "observed_order",
            "gci_fine",
            "asymptotic_ratio",
            "true_error_covered",
            "passed",
        }
        assert isinstance(d["spacings"], list)
        assert "p_obs" in rep.summary()