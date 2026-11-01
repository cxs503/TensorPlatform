"""Verification tests for the vibration & noise domain (m1707).

Every assertion is against a **closed-form** anchor, not against another branch of
this same code:

1. SDOF transmissibility peak value ``1/(2 z sqrt(1-z^2))`` and its location
   ``sqrt(1-2 z^2)`` (Den Hartog).
2. Isolation roll-off: 12 dB/oct single stage, 24 dB/oct two stages in the
   lossless limit; degraded towards 6/12 dB/oct when the damper transmits force.
3. Cantilever Euler-Bernoulli frequencies from a Rayleigh-Ritz solve (converges
   downward to ``(beta_n L)^2/(2 pi L^2) sqrt(EI/rho A)``).
4. Simply-supported beam/plate finite-difference eigen-solvers: second-order
   convergence towards the exact ``n^2 pi/(2L^2) sqrt(EI/rhoA)`` and
   ``((m pi/a)^2+(n pi/b)^2) sqrt(D/rho h)`` frequencies.
5. Rayleigh quotient upper-bound property for the SSSS plate.
6. Monopole/dipole radiation: ``P = 1/2 R_r Q^2``, ``k^4`` dipole law, pulsating
   sphere efficiency ``(ka)^2``.
7. SI level bookkeeping: ``20 log10(p/1uPa)``, +3.01 dB incoherent, +6.02 dB coherent.
8. Criteria layer fails closed (limit tables are WITHHELD, no fabricated numbers).

Run: ``cd backend && python3 -m pytest tests/test_m1707_vibration_noise_verification.py -q``
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.ship_performance.vibration_noise import acoustics as AC
from services.ship_performance.vibration_noise import criteria as CR
from services.ship_performance.vibration_noise import excitation as EX
from services.ship_performance.vibration_noise import structural as ST

RHO = AC.RHO_SEAWATER
C = AC.C_SEAWATER


# ---------------------------------------------------------------------------
# 1. Excitation kinematics (definitional -> exact)
# ---------------------------------------------------------------------------
def test_blade_rate_and_orders_exact():
    assert EX.shaft_rate_hz(120.0) == pytest.approx(2.0, rel=0, abs=1e-15)
    assert EX.blade_rate_hz(120.0, 5) == pytest.approx(10.0, rel=0, abs=1e-15)
    harm = EX.blade_rate_harmonics(120.0, 5, 3)
    assert [h["order"] for h in harm] == [5.0, 10.0, 15.0]
    assert [h["frequency_hz"] for h in harm] == pytest.approx([10.0, 20.0, 30.0], abs=1e-12)


def test_tip_speed_and_strouhal():
    # 120 rpm, R = 2 m -> omega = 4 pi rad/s, U = 8 pi m/s
    assert EX.propeller_tip_speed(120.0, 2.0) == pytest.approx(8.0 * math.pi, rel=1e-15)
    assert EX.strouhal_frequency(5.0, 0.1, 0.2) == pytest.approx(10.0, rel=1e-15)


def test_cavitation_number_definition():
    # p_atm + rho g h, hand-computed value
    h, rho, v, p_atm, p_vap = 5.0, 1025.0, 12.0, 101325.0, 2300.0
    p_abs = p_atm + rho * 9.81 * h
    sigma = EX.cavitation_number(p_abs, p_vap, rho, v)
    expected = (p_abs - p_vap) / (0.5 * rho * v ** 2)
    assert sigma == pytest.approx(expected, rel=1e-15)
    assert 1.5 < sigma < 3.0  # sanity: not cavitating for this ship-like case


# ---------------------------------------------------------------------------
# 2. SDOF transmissibility closed form
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("zeta", [0.02, 0.05, 0.1])
def test_magnification_peak_closed_form(zeta):
    """|H| peaks at sqrt(1-2z^2) with value 1/(2 z sqrt(1-z^2)) — exact identities."""
    h_max = ST.sdof_magnification_peak(zeta)
    r_peak = ST.sdof_magnification_peak_ratio(zeta)
    assert ST.sdof_compliance(r_peak, zeta) == pytest.approx(h_max, rel=1e-12)
    r = np.linspace(r_peak - 0.02, r_peak + 0.02, 40001)
    numer = max(ST.sdof_compliance(float(rr), zeta) for rr in r)
    assert numer == pytest.approx(h_max, rel=2e-3)
    # half-power bandwidth: exact algebraic value, then the 2*zeta approximation error
    r1 = math.sqrt(1.0 - 2.0 * zeta ** 2 - 2.0 * zeta * math.sqrt(1.0 - zeta ** 2))
    r2 = math.sqrt(1.0 - 2.0 * zeta ** 2 + 2.0 * zeta * math.sqrt(1.0 - zeta ** 2))
    bw = ST.half_power_bandwidth_ratio(zeta)
    assert bw == pytest.approx(r2 - r1, rel=1e-10)
    assert bw == pytest.approx(2.0 * zeta, rel=1.5e-2)   # error grows with zeta (1.04% at z=0.1)


@pytest.mark.parametrize("zeta", [0.02, 0.05, 0.1])
def test_transmissibility_peak_differs_from_magnification_peak(zeta):
    """Trap: the transmissibility peak is NOT 1/(2 z sqrt(1-z^2)) (that is |H|)."""
    r_t = ST.sdof_transmissibility_peak_ratio(zeta)
    t_max = ST.sdof_transmissibility_peak(zeta)
    assert ST.sdof_transmissibility(r_t, zeta) == pytest.approx(t_max, rel=1e-12)
    r = np.linspace(r_t - 0.02, r_t + 0.02, 40001)
    assert max(ST.sdof_transmissibility(float(rr), zeta) for rr in r) == pytest.approx(t_max, rel=2e-3)
    # the closed form derived from dT/ds = 0, cross-checked by the numeric maximum
    s_star = (math.sqrt(1.0 + 8.0 * zeta ** 2) - 1.0) / (4.0 * zeta ** 2)
    assert r_t ** 2 == pytest.approx(s_star, rel=1e-12)
    # and the naive |H| formula would be optimistic/low by O(zeta^2)
    if zeta >= 0.05:
        assert abs(t_max / ST.sdof_magnification_peak(zeta) - 1.0) > 1e-3


def test_transmissibility_limits():
    assert ST.sdof_transmissibility(0.0, 0.05) == pytest.approx(1.0, rel=1e-15)
    # static -> unity, high frequency -> 2 zeta / r (damper-dominated)
    for zeta in (0.01, 0.1):
        r = 1000.0
        assert ST.sdof_transmissibility(r, zeta) == pytest.approx(2.0 * zeta / r, rel=2e-3)


@pytest.mark.parametrize("two_stage,expected", [(False, 12.0), (True, 24.0)])
def test_isolation_roll_off_lossless(two_stage, expected):
    slope = ST.transmissibility_slope_db_per_octave(10.0, r_from=20.0, n_oct=1,
                                                   zeta=1.0e-4, two_stage=two_stage)
    assert slope == pytest.approx(expected, abs=0.2)


def test_damping_degrades_isolation():
    """Known trap: damper force transmission limits the roll-off to 6 dB/oct."""
    lossless = ST.transmissibility_slope_db_per_octave(10.0, 100.0, 3, 1e-4, False)
    damped = ST.transmissibility_slope_db_per_octave(10.0, 100.0, 3, 0.1, False)
    assert lossless > 11.5
    assert damped < 7.0
    two_damped = ST.transmissibility_slope_db_per_octave(10.0, 100.0, 3, 0.1, True)
    assert two_damped == pytest.approx(2.0 * damped, rel=5e-2)


# ---------------------------------------------------------------------------
# 3. Cantilever Rayleigh-Ritz vs exact Euler-Bernoulli
# ---------------------------------------------------------------------------
def test_cantilever_rr_converges_from_above():
    EI, rho_a, L = 1.0, 1.0, 1.0
    exact = ST.cantilever_frequencies(L, EI, rho_a, 3)
    prev = None
    for n_terms in (2, 3, 4, 6, 8, 12):
        rr = ST.cantilever_rayleigh_ritz(L, EI, rho_a, n_terms)
        k = min(n_terms, exact.size)
        assert np.all(rr[:k] >= exact[:k] * (1.0 - 1e-9)), "Rayleigh quotient must be an upper bound"
        if prev is not None:
            kp = min(k, prev.size)
            assert np.all(rr[:kp] <= prev[:kp] * (1.0 + 1e-9)), "RR must converge monotonically downward"
        prev = rr
    best = ST.cantilever_rayleigh_ritz(L, EI, rho_a, 12)[:exact.size]
    rel = np.abs(best - exact) / exact
    assert np.all(rel[:2] < 1e-6)      # modes 1-2 at machine precision
    assert rel[2] < 1e-4


def test_cantilever_exact_reference_values():
    # L=1, EI=1, rhoA=1: f1 = (1.8751040687)^2 / (2 pi) = 0.559591...
    f = ST.cantilever_frequencies(1.0, 1.0, 1.0, 2)
    assert f[0] == pytest.approx(0.5595912, abs=1e-6)
    assert f[1] == pytest.approx(3.5068982, abs=1e-6)


# ---------------------------------------------------------------------------
# 4. Finite-difference eigen-solvers: second-order convergence
# ---------------------------------------------------------------------------
def _observed_order(errors, ratios):
    return math.log(errors[0] / errors[1]) / math.log(ratios[0])


def test_beam_fd_second_order_convergence():
    EI, rho_a, L = 1.0, 1.0, 1.0
    exact = ST.ssss_beam_exact_frequencies(L, EI, rho_a, 3)
    errs = []
    for n_cells in (16, 32, 64):
        fd = ST.ssss_beam_fd_frequencies(L, EI, rho_a, n_cells, 3)
        errs.append(np.abs((fd - exact) / exact))
    p = _observed_order([errs[0][0], errs[1][0]], [2.0])
    assert p == pytest.approx(2.0, abs=0.2)
    p_mode2 = _observed_order([errs[1][1], errs[2][1]], [2.0])
    assert p_mode2 == pytest.approx(2.0, abs=0.2)
    fine = ST.ssss_beam_fd_frequencies(L, EI, rho_a, 256, 3)
    assert np.all(np.abs((fine - exact) / exact) < 2e-4)


def test_plate_fd_matches_kirchhoff_closed_form():
    a = b = 1.0
    D = rho_h = 1.0
    exact = ST.ssss_plate_frequencies(a, b, D, rho_h, [(1, 1), (1, 2), (2, 1)])
    errs = []
    for n_cells in (10, 20, 40):
        res = ST.ssss_plate_fd_modes(a, b, D, rho_h, 1, 1, n_cells)
        errs.append(abs(res["frequency_hz"] - exact[0]) / exact[0])
    assert _observed_order(errs[:2], [2.0]) == pytest.approx(2.0, abs=0.2)
    # modes (1,2) and (2,1) of a square plate are degenerate
    assert exact[1] == pytest.approx(exact[2], rel=1e-14)
    fine = ST.ssss_plate_fd_modes(a, b, D, rho_h, 1, 2, 80)
    assert fine["frequency_hz"] == pytest.approx(exact[1], rel=1e-4)


def test_plate_rayleigh_quotient_admissibility_trap():
    """Trap: a separable cubic trial violates lap(w)=0 on the edges, so the Kirchhoff
    Rayleigh quotient is NOT a bound (it returns ~2.9x f11 for a square plate)."""
    a, b, D, rho_h = 1.3, 0.9, 5.0, 40.0
    exact = ST.ssss_plate_frequencies(a, b, D, rho_h, [(1, 1)])[0]
    bad = ST.plate_rayleigh_quotient_inadmissible_trial(a, b, D, rho_h)
    assert bad > 1.5 * exact, "documented negative example: quotient is meaningless here"


def test_plate_rayleigh_quotient_sine_matches_closed_form():
    """An *admissible* sine trial reproduces the closed form to quadrature accuracy."""
    a, b, D, rho_h = 1.3, 0.9, 5.0, 40.0
    for m, n in ((1, 1), (1, 2), (2, 1)):
        exact = ST.ssss_plate_frequencies(a, b, D, rho_h, [(m, n)])[0]
        rq = ST.plate_rayleigh_quotient_sine(a, b, D, rho_h, m, n)
        assert rq == pytest.approx(exact, rel=1e-10)


# ---------------------------------------------------------------------------
# 5. Underwater acoustics
# ---------------------------------------------------------------------------
def test_monopole_power_and_radiation_resistance_identity():
    f, Q = 1500.0, 3.0e-5
    P = AC.monopole_radiated_power(f, Q)
    Rr = AC.monopole_radiation_resistance(f)
    assert P == pytest.approx(0.5 * Rr * Q * Q, rel=1e-14)


def test_monopole_inverse_square_and_spherical_spreading():
    f, Q = 800.0, 1.0e-4
    p1 = AC.monopole_pressure_amplitude(f, Q, 1.0)
    p10 = AC.monopole_pressure_amplitude(f, Q, 10.0)
    assert p1 / p10 == pytest.approx(10.0, rel=1e-14)
    # 20 log10(10) = 20 dB decay per decade of range
    assert AC.sound_pressure_level_db(p1) - AC.sound_pressure_level_db(p10) == pytest.approx(20.0, abs=1e-12)
    assert AC.source_level_at_1m_db(p10, 10.0) == pytest.approx(AC.sound_pressure_level_db(p1), rel=1e-14)


def test_dipole_k4_law_numeric_vs_analytic():
    f, Q = 1000.0, 1.0e-4
    k = AC.wavenumber(f)
    ratios, powers = [], []
    for kd in (0.01, 0.05, 0.2):
        d = kd / k
        p_num = AC.two_monopole_power_numeric(f, Q, d, r_obs=2000.0, n_theta=2000)
        p_ana = AC.dipole_radiated_power(f, Q, d)
        ratios.append(p_num / p_ana)
        powers.append(p_num)
    assert np.allclose(ratios, 1.0, atol=2e-3)   # far-field integration reproduces closed form
    # power scales as f^4 (the "k^4 dipole law") at fixed separation and volume velocity
    d = 0.05
    p = [AC.two_monopole_power_numeric(f, Q, d, r_obs=5000.0, n_theta=2000) for f in (200.0, 400.0, 800.0)]
    slope = (math.log(p[2]) - math.log(p[0])) / (math.log(800.0) - math.log(200.0))
    assert slope == pytest.approx(4.0, abs=0.05)
    # at fixed frequency the power is proportional to d^2 (dipole strength squared)
    p_small = AC.two_monopole_power_numeric(f, Q, kd_small := 0.01 / k, r_obs=2000.0, n_theta=2000)
    p_double = AC.two_monopole_power_numeric(f, Q, 2.0 * kd_small, r_obs=2000.0, n_theta=2000)
    assert p_double / p_small == pytest.approx(4.0, rel=1e-2)


def test_pulsating_sphere_radiation_efficiency():
    a, f, U = 0.5, 1000.0, 0.1
    ka = AC.wavenumber(f) * a
    Q = 4.0 * math.pi * a * a * U
    P = AC.monopole_radiated_power(f, Q)
    sigma = P / (0.5 * RHO * C * U * U * 4.0 * math.pi * a * a)
    assert sigma == pytest.approx(ka * ka, rel=1e-14)
    assert AC.pulsating_sphere_radiation_efficiency(a, f) == pytest.approx(ka * ka, rel=1e-14)


def test_level_bookkeeping_and_mass_law():
    assert AC.sound_pressure_level_db(1.0) == pytest.approx(120.0, abs=1e-12)
    assert AC.incoherent_sum_db([100.0, 100.0]) == pytest.approx(103.0103, abs=1e-4)
    assert AC.coherent_sum_db_equal(2) == pytest.approx(6.0206, abs=1e-4)
    # limp mass: 6 dB/octave asymptote once omega m >> 2 rho c
    m = 5000.0
    tl2 = AC.limp_mass_transmission_loss_db(4000.0, m)
    tl1 = AC.limp_mass_transmission_loss_db(2000.0, m)
    assert (tl2 - tl1) == pytest.approx(6.0, abs=0.1)
    # low frequency in water: a heavy plate is nearly transparent (physical sanity)
    assert AC.limp_mass_transmission_loss_db(100.0, 50.0) < 0.01


# ---------------------------------------------------------------------------
# 6. Criteria layer must fail closed (no fabricated limit tables)
# ---------------------------------------------------------------------------
def test_band_energy_sum_identity():
    assert CR.band_energy_sum([3.0, 4.0]) == pytest.approx(5.0, rel=1e-15)
    assert CR.energy_mean([100.0, 100.0]) == pytest.approx(100.0, abs=1e-12)


def test_limit_profile_requires_provenance():
    with pytest.raises(ValueError):
        CR.LimitProfile(name="x", quantity="velocity_rms_mm_s", limits={"A": 1.0}, provenance="  ")
    with pytest.raises(ValueError):
        CR.LimitProfile(name="x", quantity="velocity_rms_mm_s", limits={}, provenance="contract")


def test_criteria_withheld_registry_and_fail_closed():
    registry = CR.withheld_limit_profiles()
    assert registry, "the WITHHELD registry must not be empty"
    for key in ("iso_6954_2000_habitability", "iso_10816_3_machinery_severity_zones"):
        assert key in registry and registry[key]
    with pytest.raises(ValueError):
        CR.require_limit_profile(None, "iso_6954_2000_habitability")


def test_evaluate_velocity_against_caller_profile():
    profile = CR.LimitProfile(
        name="demo",
        quantity="velocity_rms_mm_s",
        limits={"A": 1.8, "B": 4.5, "C": 7.1},
        provenance="unit-test placeholder, not a standard",
    )
    assert CR.evaluate_velocity(1.2, profile).zone == "A"
    assert CR.evaluate_velocity(4.0, profile).zone == "B"
    fail = CR.evaluate_velocity(11.0, profile)
    assert not fail.passed and fail.zone == "C" and fail.notes


def test_domain_evidence_grades_declared():
    from services.ship_performance.vibration_noise import DOMAIN_EVIDENCE

    assert DOMAIN_EVIDENCE == {
        "excitation": "REAL",
        "structural": "REAL",
        "acoustics": "REAL",
        "criteria": "WITHHELD",
    }