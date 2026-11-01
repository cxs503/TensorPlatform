"""Verification tests for the propulsion module (推进).

All references are analytic (no empirical dataset is fabricated):

* Lanchester-Glauert ideal efficiency ``eta_i = 2 / (1 + sqrt(1 + C_T))``
  -- explicit anchor ``C_T = 3 -> eta_i = 2/3``.
* Momentum balance ``T = 2 rho A v_a (V + v_a)``.
* Pressure jump ``dP = T/A = 2 rho v_a (V + v_a)``.
* Energy flux ``T (V + v_a) = 0.5 rho A (V + v_a) [(V + 2 v_a)^2 - V^2]``.
* Kelvin/definition identites ``eta_O = J K_T / (2 pi K_Q)`` and
  ``eta_D = eta_O eta_R (1 - t) / (1 - w)``.
* IMO MEPC.308(73) EEDI closed-form arithmetic.
"""
import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from services.propulsion import (  # noqa: E402
    DEFAULT_RHO,
    TWO_PI,
    ActuatorDiskResult,
    attained_eedi,
    actuator_disk,
    disk_area,
    open_water,
    propeller_curve,
    self_propulsion_point,
    thrust_coefficient,
)

REL_TOL = 1e-12

# A representative merchant-ship propulsor used across the tests: D = 6 m,
# service speed 14 kn, wake fraction / thrust deduction are *inputs* (typical
# single-screw values), never claimed as calibrated correlations here.
D_PROP = 6.0
V_SHIP = 14.0 * 0.514444444          # 14 kn -> m/s
RHO = DEFAULT_RHO
# K_T(J) = 0.50 - 0.35 J - 0.25 J^2  (zero thrust at J = 0.878)
# K_Q(J) = 0.050 - 0.018 J - 0.020 J^2
# The two curves are mutually consistent: eta_O(J) peaks at 0.597 near J = 0.60
# and stays below the actuator-disk bound for the same thrust loading.
KT_COEFFS = [0.50, -0.35, -0.25]
KQ_COEFFS = [0.050, -0.018, -0.020]
W = 0.28
T_DED = 0.18
ETA_R = 1.00


@pytest.fixture()
def curves():
    return propeller_curve(KT_COEFFS, KQ_COEFFS)


# ---------------------------------------------------------------------------
# actuator disk
# ---------------------------------------------------------------------------

class TestActuatorDiskAnalytic:
    """Verify the momentum solution against the Lanchester-Glauert closed form."""

    @pytest.mark.parametrize("speed_ms, diameter_m, thrust_n", [
        (3.0, 2.0, 25_000.0),
        (6.0, 4.0, 120_000.0),
        (7.2, 6.0, 350_000.0),
        (12.0, 1.0, 4_500.0),
        (0.5, 0.3, 120.0),
    ])
    def test_eta_matches_analytic_closed_form(self, speed_ms, diameter_m, thrust_n):
        r = actuator_disk(speed_ms, thrust_n, diameter_m)
        assert r.ideal_efficiency == pytest.approx(r.ideal_efficiency_analytic,
                                                  rel=REL_TOL)
        expected = 2.0 / (1.0 + math.sqrt(1.0 + r.thrust_coefficient))
        assert r.ideal_efficiency == pytest.approx(expected, rel=REL_TOL)

    def test_explicit_anchor_ct_3_gives_two_thirds(self):
        """C_T = 3 must give eta_i = 2/(1+sqrt(4)) = 2/3 exactly."""
        V, D = 5.0, 2.0
        area = disk_area(D)
        thrust = 3.0 * 0.5 * RHO * area * V ** 2
        r = actuator_disk(V, thrust, D)
        assert r.thrust_coefficient == pytest.approx(3.0, rel=1e-12)
        assert r.ideal_efficiency == pytest.approx(2.0 / 3.0, rel=1e-12)
        # induced velocity ratio x = (sqrt(1+3)-1)/2 = 0.5
        assert r.advance_ratio == pytest.approx(0.5, rel=1e-12)
        assert r.induced_velocity_ms == pytest.approx(0.5 * V, rel=1e-12)

    @pytest.mark.parametrize("speed_ms, diameter_m, thrust_n", [
        (3.0, 2.0, 25_000.0),
        (7.2, 6.0, 350_000.0),
    ])
    def test_two_solution_routes_agree(self, speed_ms, diameter_m, thrust_n):
        r = actuator_disk(speed_ms, thrust_n, diameter_m)
        assert r.induced_velocity_ms == pytest.approx(
            r.induced_velocity_analytic_ms, rel=REL_TOL)

    @pytest.mark.parametrize("speed_ms, diameter_m, thrust_n", [
        (3.0, 2.0, 25_000.0),
        (7.2, 6.0, 350_000.0),
        (12.0, 1.0, 4_500.0),
    ])
    def test_momentum_pressure_and_energy_identities(self, speed_ms, diameter_m,
                                                     thrust_n):
        r = actuator_disk(speed_ms, thrust_n, diameter_m)
        # (1) momentum balance reproduces the prescribed thrust
        assert abs(r.momentum_residual_n) <= 1e-9 * max(thrust_n, 1.0)
        # (2) pressure jump = T/A and = 2 rho v_a (V + v_a)
        assert r.pressure_jump_pa == pytest.approx(thrust_n / r.disk_area_m2,
                                                  rel=REL_TOL)
        assert r.pressure_jump_pa == pytest.approx(
            2.0 * RHO * r.induced_velocity_ms * (speed_ms + r.induced_velocity_ms),
            rel=REL_TOL)
        # (3) energy flux from the far-wake velocity increment equals T (V + v_a)
        assert r.energy_flux_w == pytest.approx(r.ideal_power_w, rel=1e-10)
        # (4) effective power definition
        assert r.effective_power_w == pytest.approx(thrust_n * speed_ms, rel=REL_TOL)

    def test_zero_thrust_limit_is_loss_free(self):
        r = actuator_disk(5.0, 0.0, 2.0)
        assert r.thrust_coefficient == 0.0
        assert r.induced_velocity_ms == pytest.approx(0.0, abs=1e-15)
        assert r.ideal_efficiency == pytest.approx(1.0, rel=1e-15)
        assert r.ideal_power_w == pytest.approx(0.0, abs=1e-12)

    def test_ideal_efficiency_bounds_and_monotonicity(self):
        speeds, diameter = 6.0, 5.0
        previous = None
        for ct in (0.05, 0.25, 1.0, 2.0, 5.0, 20.0, 100.0):
            thrust = ct * 0.5 * RHO * disk_area(diameter) * speeds ** 2
            eta = actuator_disk(speeds, thrust, diameter).ideal_efficiency
            assert 0.0 < eta < 1.0
            if previous is not None:
                assert eta < previous  # heavier loading -> lower ideal efficiency
            previous = eta

    def test_high_ct_asymptote(self):
        """eta_i -> 2/sqrt(C_T) as C_T -> inf (leading-order truncation O(1/sqrt(C_T)))."""
        V, D = 6.0, 5.0
        ct = 1.0e6
        thrust = ct * 0.5 * RHO * disk_area(D) * V ** 2
        eta = actuator_disk(V, thrust, D).ideal_efficiency
        assert eta * math.sqrt(ct) == pytest.approx(2.0, rel=1e-3)
        # exact closed form must still be reproduced at this loading
        assert eta == pytest.approx(2.0 / (1.0 + math.sqrt(1.0 + ct)), rel=1e-12)

    def test_thrust_coefficient_helper_matches_internal(self):
        V, D, thrust = 6.0, 5.0, 200_000.0
        assert thrust_coefficient(thrust, D, V) == pytest.approx(
            actuator_disk(V, thrust, D).thrust_coefficient, rel=1e-14)

    @pytest.mark.parametrize("kwargs", [
        dict(speed_ms=0.0, thrust_n=1000.0, diameter_m=2.0),
        dict(speed_ms=5.0, thrust_n=-1.0, diameter_m=2.0),
        dict(speed_ms=5.0, thrust_n=1000.0, diameter_m=0.0),
        dict(speed_ms=5.0, thrust_n=1000.0, diameter_m=2.0, rho=0.0),
    ])
    def test_invalid_inputs_rejected(self, kwargs):
        with pytest.raises(ValueError):
            actuator_disk(**kwargs)

    def test_result_is_immutable_dataclass(self):
        r = actuator_disk(5.0, 1000.0, 2.0)
        assert isinstance(r, ActuatorDiskResult)
        with pytest.raises(Exception):
            r.thrust_coefficient = 1.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# open water
# ---------------------------------------------------------------------------

class TestOpenWater:

    def test_efficiency_identity(self, curves):
        kt_fn, kq_fn = curves
        r = open_water(n_rps=2.5, advance_speed_ms=0.7 * V_SHIP,
                       diameter_m=D_PROP, kt_fn=kt_fn, kq_fn=kq_fn)
        assert r.open_water_efficiency == pytest.approx(
            r.open_water_efficiency_identity, rel=1e-12)
        assert r.open_water_efficiency == pytest.approx(
            r.advance_ratio * r.thrust_coefficient
            / (TWO_PI * r.torque_coefficient), rel=1e-12)

    def test_force_scaling_with_diameter_and_rate(self, curves):
        """T ~ rho n^2 D^4 and Q ~ rho n^2 D^5 at fixed J."""
        kt_fn, kq_fn = curves
        n1, d1 = 2.0, D_PROP
        j_target = 0.6
        r1 = open_water(n1, j_target * n1 * d1, d1, kt_fn, kq_fn)
        r2 = open_water(2.0 * n1, j_target * 2.0 * n1 * d1, d1, kt_fn, kq_fn)
        assert r2.thrust_n == pytest.approx(4.0 * r1.thrust_n, rel=1e-12)
        assert r2.torque_nm == pytest.approx(4.0 * r1.torque_nm, rel=1e-12)
        r3 = open_water(n1, j_target * n1 * 2 * d1, 2 * d1, kt_fn, kq_fn)
        assert r3.thrust_n == pytest.approx(16.0 * r1.thrust_n, rel=1e-12)
        assert r3.torque_nm == pytest.approx(32.0 * r1.torque_nm, rel=1e-12)

    def test_zero_advance_has_zero_efficiency(self, curves):
        kt_fn, kq_fn = curves
        r = open_water(3.0, 0.0, D_PROP, kt_fn, kq_fn)
        assert r.advance_ratio == 0.0
        assert r.open_water_efficiency == 0.0
        assert r.delivered_power_w > 0.0

    def test_no_thrust_condition_gives_zero_thrust(self, curves):
        """At the K_T zero crossing the shaft still absorbs torque, eta_O = 0."""
        kt_fn, kq_fn = curves
        # root of 0.55 - 0.42 J - 0.28 J^2
        j0 = (-0.35 + math.sqrt(0.35 ** 2 + 4 * 0.25 * 0.50)) / (2 * 0.25)
        r = open_water(2.0, j0 * 2.0 * D_PROP, D_PROP, kt_fn, kq_fn)
        thrust_scale = RHO * 2.0 ** 2 * D_PROP ** 4
        assert abs(r.thrust_n) < 1e-12 * thrust_scale
        assert r.torque_coefficient > 0.0
        assert abs(r.open_water_efficiency) < 1e-12

    def test_efficiency_peak_between_zero_and_zero_thrust(self, curves):
        kt_fn, kq_fn = curves
        etas = []
        for j in [0.05 * i for i in range(1, 18)]:
            r = open_water(2.0, j * 2.0 * D_PROP, D_PROP, kt_fn, kq_fn)
            etas.append(r.open_water_efficiency)
        assert max(etas) < 1.0
        assert max(etas) > 0.3
        # unimodal: rises then falls
        peak = etas.index(max(etas))
        assert 0 < peak < len(etas) - 1
        assert all(etas[i] < etas[i + 1] for i in range(peak))
        assert all(etas[i] > etas[i + 1] for i in range(peak, len(etas) - 1))

    @pytest.mark.parametrize("kwargs", [
        dict(n_rps=0.0, advance_speed_ms=3.0, diameter_m=6.0),
        dict(n_rps=2.0, advance_speed_ms=-1.0, diameter_m=6.0),
        dict(n_rps=2.0, advance_speed_ms=3.0, diameter_m=-6.0),
    ])
    def test_invalid_inputs_rejected(self, kwargs, curves):
        kt_fn, kq_fn = curves
        with pytest.raises(ValueError):
            open_water(kt_fn=kt_fn, kq_fn=kq_fn, **kwargs)


# ---------------------------------------------------------------------------
# self-propulsion point
# ---------------------------------------------------------------------------

class TestSelfPropulsion:

    def _case(self, curves, resistance_n=180_000.0):
        kt_fn, kq_fn = curves
        return self_propulsion_point(
            resistance_n=resistance_n, ship_speed_ms=V_SHIP,
            diameter_m=D_PROP, kt_fn=kt_fn, kq_fn=kq_fn,
            wake_fraction=W, thrust_deduction=T_DED,
            relative_rotative_efficiency=ETA_R)

    def test_force_balance_is_solved(self, curves):
        r = self._case(curves)
        assert r.required_thrust_n == pytest.approx(
            180_000.0 / (1.0 - T_DED), rel=1e-14)
        assert abs(r.thrust_balance_residual_n) <= 1e-9 * r.required_thrust_n
        assert r.advance_ratio == pytest.approx(
            V_SHIP * (1.0 - W) / (r.revolutions_per_s * D_PROP), rel=1e-14)
        assert 0 < r.revolutions_per_s < 60.0

    def test_quasi_propulsive_identity(self, curves):
        r = self._case(curves)
        assert r.quasi_propulsive_efficiency == pytest.approx(
            r.quasi_propulsive_efficiency_identity, rel=1e-10)
        assert r.quasi_propulsive_efficiency == pytest.approx(
            r.effective_power_w / r.delivered_power_w, rel=1e-12)
        assert 0.0 < r.quasi_propulsive_efficiency < 1.0

    def test_relative_rotative_efficiency_scales_eta_d(self, curves):
        kt_fn, kq_fn = curves
        kwargs = dict(resistance_n=180_000.0, ship_speed_ms=V_SHIP,
                      diameter_m=D_PROP, kt_fn=kt_fn, kq_fn=kq_fn,
                      wake_fraction=W, thrust_deduction=T_DED)
        r_full = self_propulsion_point(relative_rotative_efficiency=1.0, **kwargs)
        r_half = self_propulsion_point(relative_rotative_efficiency=0.98, **kwargs)
        assert (r_half.quasi_propulsive_efficiency
                == pytest.approx(0.98 * r_full.quasi_propulsive_efficiency,
                                 rel=1e-12))
        assert (r_half.delivered_power_w
                > r_full.delivered_power_w)  # more shaft power for same EHP

    def test_revolutions_increase_with_resistance(self, curves):
        rates = [self._case(curves, res).revolutions_per_s
                 for res in (60_000.0, 120_000.0, 240_000.0, 480_000.0)]
        assert all(b > a for a, b in zip(rates, rates[1:]))
        # n ~ sqrt(T) but K_T rises as J falls under heavier loading, so the
        # 8x thrust step needs less than sqrt(8) = 2.83 in shaft rate.
        ratio = rates[-1] / rates[0]
        assert 1.2 < ratio < math.sqrt(8.0)
        # delivered power grows monotonically and always exceeds the towed
        # (effective) power; it grows *slower* than P_E here because the light
        # loading case sits at high J where eta_O is poor.
        cases = [self._case(curves, res) for res in (60_000.0, 120_000.0)]
        assert cases[1].delivered_power_w > cases[0].delivered_power_w
        for c in cases:
            assert c.delivered_power_w > c.effective_power_w
        assert (cases[1].delivered_power_w / cases[0].delivered_power_w
                < cases[1].effective_power_w / cases[0].effective_power_w)

    def test_open_water_efficiency_below_ideal_bound(self, curves):
        """A real open-water point cannot beat the actuator-disk ideal."""
        r = self._case(curves)
        v_a = V_SHIP * (1.0 - W)
        ct = thrust_coefficient(r.thrust_n, D_PROP, v_a, RHO)
        eta_ideal = actuator_disk(v_a, r.thrust_n, D_PROP, RHO).ideal_efficiency
        assert ct > 0.0
        assert r.open_water_efficiency < eta_ideal

    def test_no_hull_corrections_reduces_to_open_water(self, curves):
        """With w = t = 0 and eta_R = 1: eta_D == eta_O and V_a == V."""
        r = self_propulsion_point(
            resistance_n=90_000.0, ship_speed_ms=V_SHIP, diameter_m=D_PROP,
            kt_fn=curves[0], kq_fn=curves[1], wake_fraction=0.0,
            thrust_deduction=0.0, relative_rotative_efficiency=1.0)
        assert r.required_thrust_n == pytest.approx(90_000.0, rel=1e-14)
        assert r.quasi_propulsive_efficiency == pytest.approx(
            r.open_water_efficiency, rel=1e-12)

    @pytest.mark.parametrize("kwargs", [
        dict(wake_fraction=1.0),
        dict(wake_fraction=-0.1),
        dict(thrust_deduction=1.0),
        dict(relative_rotative_efficiency=0.0),
        dict(resistance_n=-5.0),
        dict(ship_speed_ms=0.0),
    ])
    def test_invalid_inputs_rejected(self, curves, kwargs):
        base = dict(resistance_n=180_000.0, ship_speed_ms=V_SHIP,
                    diameter_m=D_PROP, kt_fn=curves[0], kq_fn=curves[1],
                    wake_fraction=W, thrust_deduction=T_DED)
        base.update(kwargs)
        with pytest.raises(ValueError):
            self_propulsion_point(**base)

    def test_unreachable_thrust_raises(self):
        """A curve that cannot reach the thrust below n_max_hard must raise."""
        with pytest.raises(ValueError):
            self_propulsion_point(
                resistance_n=5.0e9, ship_speed_ms=V_SHIP, diameter_m=D_PROP,
                kt_fn=lambda j: 0.02, kq_fn=lambda j: 0.01,
                wake_fraction=W, thrust_deduction=T_DED)

    def test_negative_thrust_curve_raises(self):
        """An all-negative K_T curve can never balance resistance."""
        with pytest.raises(ValueError):
            self_propulsion_point(
                resistance_n=180_000.0, ship_speed_ms=V_SHIP, diameter_m=D_PROP,
                kt_fn=lambda j: -0.1, kq_fn=lambda j: 0.01,
                wake_fraction=W, thrust_deduction=T_DED)


# ---------------------------------------------------------------------------
# executor-agnostic consistency with the platform resistance endpoint
# ---------------------------------------------------------------------------

class TestIntegrationWithResistance:
    """The self-propulsion solver must consume the platform resistance output."""

    def test_consumes_resistance_api_shape(self, curves):
        # mirrors backend.api.calculations.calc_resistance output fields
        resistance_payload = {
            "total_resistance_n": 168_420.5,
            "frictional_resistance_n": 120_000.0,
            "residual_resistance_n": 48_420.5,
        }
        r = self_propulsion_point(
            resistance_n=resistance_payload["total_resistance_n"],
            ship_speed_ms=V_SHIP, diameter_m=D_PROP,
            kt_fn=curves[0], kq_fn=curves[1],
            wake_fraction=W, thrust_deduction=T_DED)
        assert r.effective_power_w == pytest.approx(
            resistance_payload["total_resistance_n"] * V_SHIP, rel=1e-14)
        assert r.delivered_power_w > r.effective_power_w


# ---------------------------------------------------------------------------
# green shipping: attained EEDI
# ---------------------------------------------------------------------------

class TestAttainedEedi:

    def test_hand_computed_reference(self):
        """10000 kW * 170 g/kWh * 3.114 over (50000 t * 14 kn) -> 7.562571.. g/(t nm)."""
        r = attained_eedi(capacity_t=50_000.0, v_ref_kn=14.0,
                          p_me_kw=10_000.0, sfc_me_g_kwh=170.0, cf_me=3.114)
        expected = (10_000.0 * 170.0 * 3.114) / (50_000.0 * 14.0)
        assert r.eedi_g_co2_per_t_nm == pytest.approx(expected, rel=1e-12)
        assert r.eedi_g_co2_per_t_nm == pytest.approx(7.562571428571429, rel=1e-12)

    def test_unit_chain_is_tonne_mile(self):
        r = attained_eedi(capacity_t=100_000.0, v_ref_kn=12.0,
                          p_me_kw=5_000.0, sfc_me_g_kwh=160.0, cf_me=3.0)
        assert r.numerator_g_per_h == pytest.approx(5_000 * 160 * 3.0, rel=1e-14)
        assert r.denominator_t_nm_per_h == pytest.approx(100_000 * 12.0, rel=1e-14)

    def test_correction_factors_enter_denominator(self):
        base = attained_eedi(capacity_t=50_000.0, v_ref_kn=14.0,
                             p_me_kw=10_000.0, sfc_me_g_kwh=170.0, cf_me=3.114)
        corrected = attained_eedi(capacity_t=50_000.0, v_ref_kn=14.0,
                                  p_me_kw=10_000.0, sfc_me_g_kwh=170.0,
                                  cf_me=3.114, f_c=1.05, f_l=1.02)
        assert corrected.eedi_g_co2_per_t_nm < base.eedi_g_co2_per_t_nm
        assert (base.eedi_g_co2_per_t_nm / corrected.eedi_g_co2_per_t_nm
                == pytest.approx(1.05 * 1.02, rel=1e-12))

    def test_auxiliary_and_innovative_terms(self):
        r = attained_eedi(capacity_t=50_000.0, v_ref_kn=14.0,
                          p_me_kw=10_000.0, sfc_me_g_kwh=170.0, cf_me=3.114,
                          p_ae_kw=800.0, sfc_ae_g_kwh=180.0, cf_ae=3.114,
                          p_eff_kw=500.0, sfc_eff_g_kwh=170.0)
        assert r.main_engine_term_g_per_h == pytest.approx(
            10_000.0 * 170.0 * 3.114, rel=1e-12)
        assert r.auxiliary_engine_term_g_per_h == pytest.approx(
            800.0 * 180.0 * 3.114, rel=1e-12)
        assert r.innovative_term_g_per_h == pytest.approx(
            500.0 * 170.0 * 3.114, rel=1e-12)
        assert r.numerator_g_per_h == pytest.approx(
            r.main_engine_term_g_per_h + r.auxiliary_engine_term_g_per_h
            - r.innovative_term_g_per_h, rel=1e-12)

    @pytest.mark.parametrize("kwargs", [
        dict(capacity_t=0.0, v_ref_kn=14.0, p_me_kw=1000.0),
        dict(capacity_t=50_000.0, v_ref_kn=0.0, p_me_kw=1000.0),
        dict(capacity_t=50_000.0, v_ref_kn=14.0, p_me_kw=-1.0),
        dict(capacity_t=50_000.0, v_ref_kn=14.0, p_me_kw=1000.0, cf_me=0.0),
        dict(capacity_t=50_000.0, v_ref_kn=14.0, p_me_kw=1000.0, f_i=0.0),
    ])
    def test_invalid_inputs_rejected(self, kwargs):
        base = dict(capacity_t=50_000.0, v_ref_kn=14.0, p_me_kw=1000.0,
                    sfc_me_g_kwh=170.0, cf_me=3.114)
        base.update(kwargs)
        with pytest.raises(ValueError):
            attained_eedi(**base)


# ---------------------------------------------------------------------------
# curve container
# ---------------------------------------------------------------------------

class TestPropellerCurve:

    def test_polynomial_evaluation(self):
        kt_fn, kq_fn = propeller_curve([0.5, -0.3], [0.05, -0.02])
        assert kt_fn(0.0) == pytest.approx(0.5)
        assert kt_fn(1.0) == pytest.approx(0.2)
        assert kt_fn(0.5) == pytest.approx(0.35)
        assert kq_fn(0.5) == pytest.approx(0.04)

    def test_input_lists_not_mutated(self):
        kt = [0.5, -0.3]
        propeller_curve(kt, [0.05])
        assert kt == [0.5, -0.3]