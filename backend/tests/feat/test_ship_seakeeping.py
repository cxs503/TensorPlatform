"""Analytic-anchor tests for the seakeeping reference core (services/seakeeping.py).

Every assertion here is an *anchor* — a closed form or an invariance that holds
independently of the parameterised added-mass / damping coefficients. That property is
what makes the audit of the legacy service defensible: its failures (long-wave limit
violated, response insensitive to speed/heading, hard-coded amplitudes) do not depend on
which coefficient set is chosen.
"""
from __future__ import annotations

import math

import pytest

from services.seakeeping import (
    G,
    SeakeepingModel,
    _integrate,
    response_statistics,
    beam_factor,
    deep_water_wavelength,
    encounter_frequency,
    encounter_frequency_safe,
    ittc_m0_closed,
    ittc_m2_closed,
    spectrum,
    transfer,
    transfer_peak,
    uniform_grid,
    waterplane_length_factor,
    wave_number,
)

HULL = dict(L=150.0, B=22.0, T=8.0, Cb=0.68, Cwp=0.78, displacement_t=18400.0, KG=8.5, GM_T=1.5)


def model(**over) -> SeakeepingModel:
    params = dict(HULL)
    params.update(over)
    return SeakeepingModel(**params)


# ---------------------------------------------------------------------------
# 1. Wave kinematics: closed forms
# ---------------------------------------------------------------------------


def test_deep_water_dispersion_closed_form():
    for w in (0.2, 0.5, 1.0, 1.7):
        assert wave_number(w) == pytest.approx(w * w / G, rel=0, abs=0)
        assert deep_water_wavelength(w) == pytest.approx(2 * math.pi * G / (w * w), rel=1e-14)


def test_encounter_frequency_sign_and_limits():
    w, U = 0.8, 6.0
    k = w * w / G
    # head seas (180 deg) raise the encounter frequency
    assert encounter_frequency(w, U, 180.0) == pytest.approx(w + k * U, rel=1e-14)
    # following seas (0 deg) lower it
    assert encounter_frequency(w, U, 0.0) == pytest.approx(w - k * U, rel=1e-14)
    # beam seas leave it unchanged
    assert encounter_frequency(w, U, 90.0) == pytest.approx(w, rel=1e-14)
    # zero speed: identity for every heading
    for h in (0.0, 45.0, 180.0):
        assert encounter_frequency(w, 0.0, h) == pytest.approx(w, rel=1e-14)


def test_encounter_frequency_flags_invalid_following_sea_branch():
    # strong following sea: omega_e <= 0 -> flagged invalid instead of silently clamped
    we, ok = encounter_frequency_safe(0.8, 20.0, 0.0)
    assert we <= 0.0 and ok is False
    we, ok = encounter_frequency_safe(0.8, 6.0, 180.0)
    assert we > 0.0 and ok is True


def test_lengthwise_factor_series_matches_closed_form():
    # Gamma(u) = 3(sin u - u cos u)/u^3 ; series branch must agree at the switch-over
    for u in (2e-3, 5e-3, 1e-2):
        closed = 3.0 * (math.sin(u) - u * math.cos(u)) / u ** 3
        assert waterplane_length_factor(u) == pytest.approx(closed, rel=1e-9)
    assert waterplane_length_factor(0.0) == pytest.approx(1.0, abs=1e-12)


def test_beam_factor_long_wave_limit():
    assert beam_factor(1e-4, 22.0, 90.0) == pytest.approx(1.0, abs=1e-9)
    q = 0.5 * wave_number(0.8) * 22.0
    assert beam_factor(0.8, 22.0, 90.0) == pytest.approx(math.sin(q) / q, rel=1e-14)
    # head seas: no across-beam variation at all
    assert beam_factor(0.8, 22.0, 180.0) == pytest.approx(1.0, abs=1e-12)


# ---------------------------------------------------------------------------
# 2. SDOF transfer-function anchors (coefficient-free)
# ---------------------------------------------------------------------------


def test_transfer_peak_closed_form_is_reproduced():
    for zeta in (0.05, 0.10, 0.20, 0.30):
        r_peak, h_peak = transfer_peak(zeta)
        assert r_peak == pytest.approx(math.sqrt(1 - 2 * zeta ** 2), rel=1e-15)
        assert transfer(r_peak, zeta) == pytest.approx(h_peak, rel=1e-13)
        assert h_peak == pytest.approx(1.0 / (2 * zeta * math.sqrt(1 - zeta ** 2)), rel=1e-15)


def test_transfer_high_frequency_asymptote_is_minus_two():
    # |H(r)| ~ 1/r^2 : the ratio H(r)/H(2r) must tend to 4 (damping term is O(zeta/r))
    assert transfer(200.0, 0.1) / transfer(400.0, 0.1) == pytest.approx(4.0, rel=1e-4)
    assert transfer(400.0, 0.1) < 1e-5


# ---------------------------------------------------------------------------
# 3. Long-wave limits of the RAO (the anchor the legacy service violates)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "over",
    [
        dict(),
        dict(kappa33=0.3, zeta33=0.05),
        dict(kappa33=1.5, zeta33=0.45),
    ],
)
def test_long_wave_limits_are_coefficient_independent(over):
    m = model(**over)
    row = m.rao_at(0.02)  # k -> 0, so kL -> 0, kT -> 0
    assert row["heave_rao_m_per_m"] == pytest.approx(1.0, abs=2e-3)
    assert row["pitch_rao_rad_per_rad"] == pytest.approx(1.0, abs=2e-3)
    assert row["roll_rao_rad_per_rad"] == pytest.approx(1.0, abs=5e-3)


def test_heave_decays_after_resonance_and_stays_positive():
    m = model()
    omegas = [0.05 * i for i in range(2, 61)]
    curve = m.rao_curve(omegas)["heave_rao_m_per_m"]
    peak_i = max(range(len(curve)), key=lambda i: curve[i])
    tail = curve[peak_i:]
    assert all(tail[i] < tail[i - 1] + 1e-12 for i in range(1, len(tail))), "RAO must decay past resonance"
    assert all(v >= 0.0 for v in tail), "magnitude response must stay non-negative"
    assert curve[peak_i] > 0.5  # resonance is actually resolved, not clamped away


def test_natural_periods_are_physical_and_distinct():
    m = model()
    T = m.natural_periods()
    assert 5.0 < T["heave"] < 12.0
    assert 3.0 < T["pitch"] < 12.0
    assert 8.0 < T["roll"] < 25.0
    # pitch must not be an algebraic copy of heave (legacy service bug)
    assert abs(T["pitch"] - T["heave"]) > 0.2


# ---------------------------------------------------------------------------
# 4. Independent verification: time-domain ODE vs frequency-domain transfer
# ---------------------------------------------------------------------------


def test_time_domain_rk4_matches_frequency_domain_amplitude():
    """Integrate (M+A)x'' + Bx' + Cx = F0 cos(w_e t) and compare with |RAO(omega)|."""
    m = model()
    omega = 0.7  # well inside the wave band, off resonance
    row = m.rao_at(omega)

    M = m.mass + m.A33
    C = m.C33
    B = 2.0 * m.zeta33 * math.sqrt(C * M)
    F0 = C * row["gamma_beam"] * row["smith"]
    we = row["omega_e"]

    # x'' = (F0 cos(we t) - B x' - C x) / M
    def f(t, y):
        x, v = y
        return v, (F0 * math.cos(we * t) - B * v - C * x) / M

    dt = 0.002
    n_settle = int(600.0 / dt)          # ~80 periods, transient decayed
    n_measure = int(2.0 * math.pi / we / dt) * 3

    x, v, t = 0.0, 0.0, 0.0
    for _ in range(n_settle):
        k1 = f(t, (x, v))
        k2 = f(t + dt / 2, (x + dt * k1[0] / 2, v + dt * k1[1] / 2))
        k3 = f(t + dt / 2, (x + dt * k2[0] / 2, v + dt * k2[1] / 2))
        k4 = f(t + dt, (x + dt * k3[0], v + dt * k3[1]))
        x += dt * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]) / 6
        v += dt * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]) / 6
        t += dt

    xs = []
    for _ in range(n_measure):
        k1 = f(t, (x, v))
        k2 = f(t + dt / 2, (x + dt * k1[0] / 2, v + dt * k1[1] / 2))
        k3 = f(t + dt / 2, (x + dt * k2[0] / 2, v + dt * k2[1] / 2))
        k4 = f(t + dt, (x + dt * k3[0], v + dt * k3[1]))
        x += dt * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]) / 6
        v += dt * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]) / 6
        t += dt
        xs.append(x)

    amplitude = 0.5 * (max(xs) - min(xs))
    assert amplitude == pytest.approx(row["heave_rao_m_per_m"], rel=2e-3)


# ---------------------------------------------------------------------------
# 5. Froude scaling invariance
# ---------------------------------------------------------------------------


def test_froude_scaling_invariance():
    """lambda-scaled hull at lambda^0.5-scaled speed must give the same non-dimensional RAO."""
    lam = 2.0
    base = model()
    scaled = SeakeepingModel(
        L=HULL["L"] * lam,
        B=HULL["B"] * lam,
        T=HULL["T"] * lam,
        Cb=HULL["Cb"],
        Cwp=HULL["Cwp"],
        displacement_t=HULL["displacement_t"] * lam ** 3,
        KG=HULL["KG"] * lam,
        GM_T=HULL["GM_T"] * lam,
    )
    omega = 0.6
    speed = 5.0
    b = base.rao_at(omega, heading_deg=180.0, speed=speed)
    s = scaled.rao_at(omega / math.sqrt(lam), heading_deg=180.0, speed=speed * math.sqrt(lam))
    assert s["heave_rao_m_per_m"] == pytest.approx(b["heave_rao_m_per_m"], rel=1e-10)
    assert s["pitch_rao_rad_per_rad"] == pytest.approx(b["pitch_rao_rad_per_rad"], rel=1e-10)
    assert s["omega_e"] / b["omega_e"] == pytest.approx(1.0 / math.sqrt(lam), rel=1e-10)


# ---------------------------------------------------------------------------
# 6. Anti-MOCK: the response must react to speed and heading
# ---------------------------------------------------------------------------


def test_response_reacts_to_speed_and_heading():
    m = model()
    omega = 0.7
    a = m.rao_at(omega, heading_deg=180.0, speed=0.0)["heave_rao_m_per_m"]
    b = m.rao_at(omega, heading_deg=180.0, speed=10.0)["heave_rao_m_per_m"]
    c = m.rao_at(omega, heading_deg=90.0, speed=0.0)["heave_rao_m_per_m"]
    assert abs(b / a - 1.0) > 1e-3, "heave RAO must respond to speed (encounter frequency)"
    assert abs(c / a - 1.0) > 1e-2, "heave RAO must respond to heading (across-beam factor)"
    # pitch and heave are different physics: curves must not be identical
    row = m.rao_at(omega)
    assert abs(row["pitch_rao_rad_per_rad"] - row["heave_rao_m_per_m"]) > 1e-3


# ---------------------------------------------------------------------------
# 7. Spectra: closed-form moment identities
# ---------------------------------------------------------------------------


def test_ittc_moment_closed_forms():
    A, B = 173.0 * 9.0 / 8.0 ** 4, 691.0 / 8.0 ** 4
    assert ittc_m0_closed(A, B) == pytest.approx(A / (4 * B), rel=1e-14)
    assert ittc_m2_closed(A, B) == pytest.approx(A * math.sqrt(math.pi) / (4 * math.sqrt(B)), rel=1e-14)


@pytest.mark.parametrize("kind", ["pm", "jonswap", "ittc"])
def test_spectrum_recovers_target_hs(kind):
    omegas = uniform_grid(0.02, 6.0, 1201)
    spec = spectrum(kind, Hs=3.0, Tp=8.0, omega=omegas)
    assert spec["Hs_recovered"] == pytest.approx(3.0, rel=1e-6)
    assert spec["m0"] == pytest.approx(9.0 / 16.0, rel=1e-6)
    assert all(s >= 0.0 for s in spec["S"])
    assert math.isfinite(spec["Tz_s"]) and 3.0 < spec["Tz_s"] < 12.0


def test_pm_moment_matches_closed_form():
    """m0 = alpha*g^2/(4*1.25*wp^4) for S = alpha g^2 w^-5 exp(-1.25 (wp/w)^4)."""
    Tp = 8.0
    omegas = uniform_grid(0.02, 8.0, 4001)
    spec = spectrum("pm", Hs=2.5, Tp=Tp, omega=omegas)
    wp = 2 * math.pi / Tp
    m0_closed = spec["alpha_scale"] * G ** 2 / (4 * 1.25 * wp ** 4)
    assert spec["m0"] == pytest.approx(m0_closed, rel=2e-4)


# ---------------------------------------------------------------------------
# 8. Geometry gate (Cb / L / B / T must close before any RAO is produced)
# ---------------------------------------------------------------------------


def test_geometry_gate_rejects_inconsistent_displacement():
    with pytest.raises(ValueError, match="geometry inconsistent"):
        model(displacement_t=25000.0)


@pytest.mark.parametrize(
    "over,match",
    [
        (dict(Cb=0.05), "Cb"),
        (dict(T=40.0), "draft"),
        (dict(GM_T=0.0), "positive"),
    ],
)
def test_geometry_gate_rejects_absurd_hulls(over, match):
    with pytest.raises(ValueError, match=match):
        model(**over)


# ---------------------------------------------------------------------------
# 9. Endpoint contract
# ---------------------------------------------------------------------------


def test_endpoints_contract():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from fastapi import FastAPI

    from api.domain.ship_seakeeping import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = fastapi_testclient.TestClient(app)

    ok = client.post(
        "/api/v1/calculations/seakeeping_rao",
        json={"hull": dict(HULL), "heading_deg": 180.0, "speed_ms": 5.0, "n_points": 21},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert len(body["curves"]["heave_rao_m_per_m"]) == 21
    assert body["hydrostatics"]["geometry_residual_pct"] < 5.0
    assert set(body["natural_periods_s"]) == {"heave", "pitch", "roll"}

    bad = client.post(
        "/api/v1/calculations/seakeeping_rao",
        json={"hull": dict(HULL, displacement_t=25000.0)},
    )
    assert bad.status_code == 400
    assert "geometry inconsistent" in bad.json()["detail"]

    spec = client.post("/api/v1/calculations/seakeeping_spectrum", json={"kind": "jonswap"})
    assert spec.status_code == 200 and spec.json()["Hs_error_pct"] < 1e-3

    resp = client.post("/api/v1/calculations/seakeeping_response", json={"Hs": 3.0, "Tp": 8.0})
    assert resp.status_code == 200
    assert resp.json()["statistics"]["significant_heave"] > 0.0

# ---------------------------------------------------------------------------
# 10. Spectral response statistics: dimension-carrying anchors
#
# response_statistics() must take the spectral moments over the response *per unit
# wave amplitude* (m/m, deg/m). The slope-normalised *_rad_per_rad RAOs are
# dimensionless: multiplying them by S(omega) [m^2 s] returns sigma_zeta re-labelled
# as an angle, i.e. the pitch / roll spread is inflated by 1/k (~500x at lambda~200 m).
# ---------------------------------------------------------------------------


def _band_limited_long_wave_spectrum(omega_min=0.05, omega_max=0.12, hs=2.0, tp=25.0, n=400):
    """Spectrum confined to long waves (lambda = 2 pi g / w^2 = 680..3900 m >> L)."""
    omegas = uniform_grid(omega_min, omega_max, n)
    base = spectrum("pm", Hs=hs, Tp=tp, omega=omegas)
    S = [s if omega_min <= w <= omega_max else 0.0 for s, w in zip(base["S"], omegas)]
    spec = dict(base)
    spec["S"] = S
    return spec


def test_sdof_white_noise_integral_matches_closed_form():
    """int_0^inf |H(r)|^2 dr = pi/(4 zeta): exact anchor for the quadrature itself."""
    r = uniform_grid(1e-4, 400.0, 200001)
    for zeta in (0.05, 0.1, 0.2, 0.45):
        val = _integrate([transfer(x, zeta) ** 2 for x in r], r)
        assert val == pytest.approx(math.pi / (4.0 * zeta), rel=5e-4)


def test_long_wave_heave_sigma_follows_the_free_surface():
    """omega -> 0 means heave RAO -> 1, so sigma_heave -> sigma_zeta = Hs_band/4."""
    m = model()
    spec = _band_limited_long_wave_spectrum()
    stats = response_statistics(m, spec, heading_deg=180.0, speed=0.0)
    sigma_zeta = math.sqrt(_integrate(spec["S"], spec["omega"]))
    assert stats["sigma_heave"] == pytest.approx(sigma_zeta, rel=1e-2)
    # significant amplitude = 2 sigma (mean of the highest third), not Hs = 4 sigma
    assert stats["significant_heave"] == pytest.approx(2.0 * sigma_zeta, rel=1e-2)


def test_long_wave_pitch_sigma_follows_the_wave_slope():
    """omega -> 0 means pitch -> wave slope, i.e. sigma_pitch[deg] -> deg(sqrt(int k^2 S))."""
    m = model()
    spec = _band_limited_long_wave_spectrum()
    stats = response_statistics(m, spec, heading_deg=180.0, speed=0.0)
    slope_var = _integrate(
        [(wave_number(w) ** 2) * s for s, w in zip(spec["S"], spec["omega"])], spec["omega"]
    )
    assert stats["sigma_pitch"] == pytest.approx(math.degrees(math.sqrt(slope_var)), rel=2e-2)


def test_angle_statistics_are_per_amplitude_not_slope_normalised():
    """Regression guard for the slope-normalised-RAO defect (factor 1/k inflation)."""
    m = model()
    spec = _band_limited_long_wave_spectrum()
    stats = response_statistics(m, spec, heading_deg=180.0, speed=0.0)
    curve = m.rao_curve(spec["omega"], heading_deg=180.0, speed=0.0)
    expected = math.sqrt(
        _integrate([r * r * s for r, s in zip(curve["pitch_rao_deg_per_m"], spec["S"])], spec["omega"])
    )
    assert stats["sigma_pitch"] == pytest.approx(expected, rel=1e-12)
    # the old (dimensionally invalid) value was sigma_zeta in radians converted to deg,
    # i.e. ~0.5 rad -> ~28.6 deg for this sea state: at least two orders of magnitude off.
    sigma_zeta = math.sqrt(_integrate(spec["S"], spec["omega"]))
    buggy = math.degrees(sigma_zeta)
    assert buggy / stats["sigma_pitch"] > 100.0


def test_response_statistics_scale_linearly_with_hs():
    """Linear theory: sigma scales with Hs (quadrature-error bounded)."""
    m = model()
    omegas = uniform_grid(0.02, 6.0, 1201)
    a = response_statistics(m, spectrum("jonswap", Hs=2.0, Tp=8.0, omega=omegas))
    b = response_statistics(m, spectrum("jonswap", Hs=4.0, Tp=8.0, omega=omegas))
    for key in ("sigma_heave", "sigma_pitch", "sigma_roll", "sigma_vertical_acc_m_s2"):
        assert b[key] == pytest.approx(2.0 * a[key], rel=2e-3), key


def test_angle_statistics_stay_sub_degree_for_a_benign_sea():
    """Hs=3 m / Tp=8 s head seas: pitch and roll spread must be O(0.1 deg), not O(10 deg)."""
    m = model()
    stats = response_statistics(m, spectrum("jonswap", Hs=3.0, Tp=8.0, omega=uniform_grid(0.02, 6.0, 1201)))
    assert 0.01 < stats["sigma_pitch"] < 2.0
    assert 0.01 < stats["sigma_roll"] < 2.0
    assert stats["significant_pitch"] == pytest.approx(2.0 * stats["sigma_pitch"], rel=1e-12)
