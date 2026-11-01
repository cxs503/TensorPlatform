"""Analytic-anchor tests for the ITTC-1978 model->full-scale extrapolation (m1654).

Every assertion here is an *anchor* — a published closed form, an exact similarity law,
or an algebraic identity — so the verification does not depend on the author's numbers:

1. ``Cf``        — ITTC-1957 closed form, cross-checked against (a) the pre-existing
   ``services.ship_resistance.ittc57_cf`` (bit-identical) and (b) the *independent*
   historical Schoenherr (1932) line, whose difference is the documented line-choice
   spread rather than a tautology.
2. ``dC_F``      — ITTC-1978 closed form ``[105 (ks/Lwl)^(1/3) - 0.64] e-3``: landmark
   values, monotonicity in ``L``, and its analytic zero crossing.
3. Similarity    — Froude speed law ``V_s = V_m sqrt(lambda)`` and Reynolds ratio
   ``Re_s/Re_m = lambda^1.5`` at equal viscosity, both exact.
4. Identities    — ``Cw`` invariance, the two-term correction decomposition
   ``C_Ts - C_Tm = (1+k)(C_Fs - C_Fm) + dC_F``, and the ``lambda^3`` limit that the
   extrapolation must collapse to when the Reynolds effect is switched off.
5. Guards        — non-physical input is rejected instead of returned.
"""
from __future__ import annotations

import math

import pytest

from services.full_scale_extrapolation import (
    DEFAULT_KS_M,
    decompose,
    correlation_allowance,
    correlation_allowance_zero_length,
    extrapolate,
    froude_speed,
    ittc1957_cf,
    speed_for_froude,
)
from services.ship_resistance import ittc57_cf as platform_ittc57_cf
from services.ship_resistance import schoenherr_cf

# DTRC SUBOFF model datum from the in-tree canon (backend/data/benchmark_reference.json,
# case SUBOFF-02): L=4.356 m, S=5.42 m^2, V=4.0 m/s, R_T=173.13 N, fresh water 20 C.
SUBOFF_MODEL = dict(
    model_length_m=4.356,
    model_speed_ms=4.0,
    model_wetted_surface_m2=5.42,
    model_resistance_n=173.13,
)
LAMBDA = 24.0
SUBOFF_SHIP_LENGTH = SUM = SUBOFF_MODEL["model_length_m"] * LAMBDA


def suboff(**over):
    kwargs = dict(SUBOFF_MODEL, ship_length_m=SUBOFF_SHIP_LENGTH)
    kwargs.update(over)
    return extrapolate(**kwargs)


# ---------------------------------------------------------------------------
# 1. ITTC-1957 friction line
# ---------------------------------------------------------------------------

class TestFrictionLine:
    def test_matches_platform_closed_form(self):
        """Same closed form as the already-merged resistance chain (bit-identical)."""
        for re in (1e6, 1e7, 1e8, 1e9, 3.7e9):
            assert ittc1957_cf(re) == platform_ittc57_cf(re)

    def test_published_landmark(self):
        """ITTC-1957 gives Cf = 1.531e-3 at Re = 1e9 (0.075/49 exactly)."""
        assert ittc1957_cf(1.0e9) == pytest.approx(0.075 / 49.0, rel=0, abs=0)
        assert ittc1957_cf(1.0e9) == pytest.approx(1.5306e-3, rel=1e-4)

    def test_monotone_decreasing(self):
        res = [1e6, 1e7, 1e8, 1e9, 1e10]
        cfs = [ittc1957_cf(r) for r in res]
        assert all(a > b for a, b in zip(cfs, cfs[1:]))

    def test_independent_schoenherr_line_agrees_within_spread(self):
        """Two distinct historical correlations differ by <2.5 % for Re >= 1e7.

        Measured spread (this test doubles as the record of it): +2.240 % at Re=1e7,
        +0.546 % at 1e8, -0.021 % at 1e9, -0.067 % at 3e9.  The two lines cross near
        Re = 5e8 — ITTC-1957 sits *above* Schoenherr (1932) at model Reynolds numbers
        and marginally below at full scale, so neither may be called "the correct one";
        the spread is the friction-line model risk budget.
        """
        for re in (1e7, 1e8, 1e9, 3.0e9):
            cf_it = ittc1957_cf(re)
            cf_sch = schoenherr_cf(re)
            assert abs(cf_it - cf_sch) / cf_sch < 0.025, (re, cf_it, cf_sch)
        # documented sign change across the crossover
        assert ittc1957_cf(1e7) > schoenherr_cf(1e7)
        assert ittc1957_cf(1e9) < schoenherr_cf(1e9)

    def test_singular_branch_rejected(self):
        with pytest.raises(ValueError):
            ittc1957_cf(100.0)
        with pytest.raises(ValueError):
            ittc1957_cf(0.0)


# ---------------------------------------------------------------------------
# 2. ITTC-1978 correlation allowance
# ---------------------------------------------------------------------------

class TestCorrelationAllowance:
    def test_landmark_150m_150um(self):
        """dC_F(150 m, ks=150 um) = [105*(1e-6)^(1/3) - 0.64]e-3 = 0.410e-3."""
        assert correlation_allowance(150.0) == pytest.approx(0.410e-3, rel=0, abs=1e-15)

    def test_matches_closed_form_symbolically(self):
        for length in (50.0, 120.0, 200.0, 320.0, 600.0):
            expected = (105.0 * (DEFAULT_KS_M / length) ** (1.0 / 3.0) - 0.64) * 1e-3
            assert correlation_allowance(length) == expected

    def test_monotone_decreasing_in_length(self):
        lengths = [50.0, 100.0, 150.0, 250.0, 400.0, 600.0]
        dcfs = [correlation_allowance(x) for x in lengths]
        assert all(a > b for a, b in zip(dcfs, dcfs[1:]))
        assert dcfs[0] > 0.5e-3 and dcfs[-1] < 0.1e-3

    def test_analytic_zero_crossing(self):
        """dC_F changes sign at L = ks/(0.64/105)^3 = 662.4 m (ks = 150 um)."""
        l0 = correlation_allowance_zero_length()
        assert l0 == pytest.approx(150e-6 / (0.64 / 105.0) ** 3)
        assert l0 == pytest.approx(662.4, abs=0.1)
        assert correlation_allowance(l0) == pytest.approx(0.0, abs=1e-15)
        assert correlation_allowance(l0 * 0.99) > 0.0 > correlation_allowance(l0 * 1.01)

    def test_negative_branch_is_flagged_not_clamped(self):
        res = extrapolate(
            model_length_m=4.356, model_speed_ms=4.0, model_wetted_surface_m2=5.42,
            model_resistance_n=173.13, ship_length_m=900.0,
        )
        assert res.delta_cf < 0.0
        assert any("negative" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# 3. Exact similarity laws
# ---------------------------------------------------------------------------

class TestSimilarityLaws:
    def test_froude_speed_law(self):
        res = suboff()
        assert res.speed_ship_ms == pytest.approx(4.0 * math.sqrt(LAMBDA), rel=1e-15)
        assert res.froude_number_ship == pytest.approx(res.froude_number_model, rel=1e-15)
        assert res.scale_ratio == pytest.approx(LAMBDA)

    def test_froude_helpers_are_inverse(self):
        for fn in (0.05, 0.15, 0.26, 0.4):
            assert froude_speed(speed_for_froude(fn, 200.0), 200.0) == pytest.approx(fn, rel=1e-14)

    def test_reynolds_ratio_lambda_pow_1p5(self):
        """Equal viscosity -> Re_s/Re_m = lambda^1.5 exactly."""
        res = extrapolate(
            model_length_m=4.356, model_speed_ms=4.0, model_wetted_surface_m2=5.42,
            model_resistance_n=173.13, ship_length_m=SUBOFF_SHIP_LENGTH,
            nu_ship=1.004e-6, rho_ship=998.2,
        )
        assert res.reynolds_ship / res.reynolds_model == pytest.approx(LAMBDA ** 1.5, rel=1e-14)

    def test_wetted_surface_froude_scaling(self):
        res = suboff()
        assert res.wetted_surface_source == "froude_scaling_lambda2"
        assert res.wetted_surface_ship_m2 == pytest.approx(5.42 * LAMBDA ** 2, rel=1e-15)

    def test_wetted_surface_override_is_labelled(self):
        res = suboff(ship_wetted_surface_m2=3000.0)
        assert res.wetted_surface_source == "input_validated"
        assert res.wetted_surface_ship_m2 == 3000.0


# ---------------------------------------------------------------------------
# 4. Algebraic identities of the 1978 procedure
# ---------------------------------------------------------------------------

class TestIdentities:
    def test_wave_coefficient_is_scale_invariant(self):
        """Cw recovered from the ship side equals Cw taken from the model side."""
        res = suboff(form_factor=1.15, ks=None)
        cw_ship_side = res.ct_ship - res.form_factor * res.cf_ship - res.delta_cf
        assert cw_ship_side == pytest.approx(res.cw, rel=0, abs=1e-16)

    def test_two_term_decomposition_closes(self):
        res = suboff(form_factor=1.1)
        split = decompose(res)
        assert split["closure_residual"] == pytest.approx(0.0, abs=1e-18)
        assert split["delta_ct_viscous"] + split["delta_ct_correlation"] == pytest.approx(
            res.ct_ship - res.ct_model, rel=0, abs=1e-16
        )

    def test_physics_sign_of_the_correction(self):
        """Friction drop (Re grows) minus roughness allowance: C_Ts < C_Tm for a
        100 m ship; the sign flips for an artificially long hull (dC_F -> 0)."""
        assert suboff().ct_ship < suboff().ct_model
        short = extrapolate(
            model_length_m=4.356, model_speed_ms=4.0, model_wetted_surface_m2=5.42,
            model_resistance_n=173.13, ship_length_m=4.356 * 2.0,
        )
        assert short.delta_cf > 0.5e-3  # small ship: allowance dominates

    def test_lambda_cubed_limit(self):
        """Switching the Reynolds effect off (nu_s = nu_m * lambda^1.5, ks=None) makes
        the extrapolation collapse to the pure Froude law R_Ts/R_Tm = lambda^3."""
        res = extrapolate(
            model_length_m=4.356, model_speed_ms=4.0, model_wetted_surface_m2=5.42,
            model_resistance_n=173.13, ship_length_m=SUBOFF_SHIP_LENGTH,
            ks=None, rho_ship=998.2, nu_ship=1.004e-6 * LAMBDA ** 1.5,
        )
        assert res.cf_ship == pytest.approx(res.cf_model, rel=1e-15)
        assert res.rt_ship_n / res.rt_model_n == pytest.approx(LAMBDA ** 3, rel=1e-14)

    def test_coefficient_and_resistance_inputs_agree(self):
        rt = suboff()
        ct_input = 173.13 / (0.5 * 998.2 * 5.42 * 4.0 ** 2)
        ct = suboff(model_resistance_n=None, model_total_coefficient=ct_input)
        assert ct.ct_model == pytest.approx(rt.ct_model, rel=1e-14)
        assert ct.rt_ship_n == pytest.approx(rt.rt_ship_n, rel=1e-14)
        assert ct.rt_model_n == pytest.approx(173.13, rel=1e-14)

    def test_effective_power_definition(self):
        res = suboff()
        assert res.effective_power_kw == pytest.approx(
            res.rt_ship_n * res.speed_ship_ms / 1000.0, rel=1e-15
        )

    def test_form_factor_and_roughness_sensitivity(self):
        """Sensitivity signs, including the counter-intuitive one for (1+k).

        At a *fixed measured* C_Tm, raising the assumed form factor removes more
        viscous resistance at model scale than it adds at full scale (C_Fm > C_Fs), so
        the extrapolated C_Ts *decreases*:
        ``d C_Ts / d(1+k) = C_Fs - C_Fm < 0``.  (1+k) is therefore not recoverable from
        a single-scale C_T, and the sensitivity is reported rather than assumed away.
        """
        base = suboff(form_factor=1.0).ct_ship
        higher_k = suboff(form_factor=1.2)
        assert higher_k.ct_ship < base
        # d C_Ts / d(1+k) = C_Fs - C_Fm, applied over delta(1+k) = 0.2
        assert higher_k.ct_ship - base == pytest.approx(
            0.2 * (higher_k.cf_ship - higher_k.cf_model), rel=0, abs=1e-15
        )
        # roughness allowance enters with the opposite (positive) sign
        assert suboff(ks=None).ct_ship < suboff().ct_ship
        assert suboff(ks=300e-6).ct_ship > suboff().ct_ship
        assert suboff(ks=300e-6).ct_ship - suboff().ct_ship == pytest.approx(
            suboff(ks=300e-6).delta_cf - suboff().delta_cf, rel=0, abs=1e-15
        )


# ---------------------------------------------------------------------------
# 5. Guards and warnings
# ---------------------------------------------------------------------------

class TestGuards:
    def test_requires_exactly_one_model_datum(self):
        with pytest.raises(ValueError):
            suboff(model_resistance_n=None)
        with pytest.raises(ValueError):
            suboff() if False else extrapolate(
                **{**SUBOFF_MODEL, "ship_length_m": SUBOFF_SHIP_LENGTH,
                   "model_total_coefficient": 0.004},
            )

    def test_rejects_non_positive_inputs(self):
        for bad in ("model_length_m", "model_speed_ms", "model_wetted_surface_m2", "ship_length_m"):
            with pytest.raises(ValueError):
                suboff(**{bad: -1.0})

    def test_rejects_form_factor_below_one(self):
        with pytest.raises(ValueError):
            suboff(form_factor=0.9)

    def test_rejects_viscous_baseline_violation(self):
        """C_Tm below (1+k)C_Fm is non-physical and must not be extrapolated."""
        with pytest.raises(ValueError):
            suboff(model_resistance_n=1.0)

    def test_froude_mismatch_warns(self):
        res = suboff(ship_speed_ms=5.0)
        assert any("Froude mismatch" in w for w in res.warnings)
        assert res.froude_number_ship != pytest.approx(res.froude_number_model)

    def test_froude_consistent_speed_does_not_warn(self):
        res = suboff(ship_speed_ms=4.0 * math.sqrt(LAMBDA))
        assert not any("Froude mismatch" in w for w in res.warnings)

    def test_low_reynolds_warns(self):
        res = extrapolate(
            model_length_m=0.5, model_speed_ms=0.1, model_wetted_surface_m2=0.2,
            model_resistance_n=1.2, ship_length_m=12.0,
        )
        assert res.reynolds_model < 1e5
        assert any("validated range" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# 6. HTTP endpoint wiring
# ---------------------------------------------------------------------------

class TestEndpoint:
    @pytest.fixture(scope="class")
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.calculations import router

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        return TestClient(app)

    def _payload(self, **over):
        body = dict(
            model_length_m=4.356,
            model_speed_ms=4.0,
            model_wetted_surface_m2=5.42,
            model_resistance_n=173.13,
            ship_length_m=SUBOFF_SHIP_LENGTH,
            form_factor=1.0,
        )
        body.update(over)
        return body

    def test_endpoint_matches_service(self, client):
        r = client.post("/api/v1/calculations/full_scale", json=self._payload())
        assert r.status_code == 200, r.text
        data = r.json()
        ref = suboff()
        assert data["ct_ship"] == pytest.approx(ref.ct_ship, rel=1e-12)
        assert data["rt_ship_n"] == pytest.approx(ref.rt_ship_n, rel=1e-12)
        assert data["delta_cf"] == pytest.approx(ref.delta_cf, rel=1e-12, abs=1e-18)
        assert data["delta_ct_closure_residual"] == pytest.approx(0.0, abs=1e-15)
        assert data["effective_power_kw"] > 0.0
        assert data["method"].startswith("ITTC-1978")

    def test_endpoint_rejects_missing_model_datum(self, client):
        payload = self._payload()
        payload.pop("model_resistance_n")
        r = client.post("/api/v1/calculations/full_scale", json=payload)
        assert r.status_code == 400

    def test_endpoint_rejects_negative_speed(self, client):
        r = client.post("/api/v1/calculations/full_scale", json=self._payload(model_speed_ms=-1.0))
        assert r.status_code == 422

    def test_endpoint_rejects_nonphysical_coefficient(self, client):
        r = client.post(
            "/api/v1/calculations/full_scale",
            json=self._payload(model_resistance_n=1.0),
        )
        assert r.status_code == 400