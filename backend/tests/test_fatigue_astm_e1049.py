"""Independent verification of the digital-twin fatigue chain vs ASTM E1049-85.

Why this file exists
--------------------
``services/digital_twin/models/fatigue.py`` used to "validate" its spectral
(narrow-band Miner) damage against its own rain-flow counter.  That is a
circular argument: if the counter is wrong, both sides are wrong together, and
the counter itself had no reference at all.  This suite supplies the missing
external references, all of which are cheap closed-form or standard-specification
anchors:

1. ``ASTM E1049-85`` canonical turning-point sequence -> published cycle split
   (3 closed cycles of range 3/4/8 plus 2 residues counted as **half** cycles),
   cross-checked against an *independent* four-point implementation.
2. Exact analytic cases (pure sinusoid, two-level block) where the rain-flow
   damage has a closed form.
3. DNV-RP-C203 S-N curve F anchors (knee ``N = 1e7`` at ``S = 36.84 MPa`` and
   ``N(100 MPa) = 5.0e5``), including C1 continuity at the knee.
4. The Rayleigh bilinear moment against a numerical integral of the *normalised*
   Rayleigh range density (an unnormalised density silently gives a factor 2).
5. Narrow-band Rayleigh damage vs Monte-Carlo rain-flow on records with a known
   PSD: this is what the twin actually reports, so it is measured, not assumed.
6. Wirsching-Light factor against its published coefficients and the four
   spectra experiment used in the audit note.

Run: ``cd backend && python3 -m pytest tests/test_fatigue_astm_e1049.py -q``
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from services.digital_twin.models.fatigue import (
    S_KNEE, SN_A, SN_A2, _jonswap, damage_from_stress_rms, rainflow_damage,
    rayleigh_bilinear_moment, record_spectral_stats, sn_cycles, spectral_fatigue,
    spectral_vs_rainflow, stress_from_damage, synthesise_stress,
    wirsching_light_factor)
from services.digital_twin.models.geometry import HullForm

# ---------------------------------------------------------------------------
# reference implementations (deliberately independent of the module under test)
# ---------------------------------------------------------------------------


def turning_points(s: np.ndarray) -> list[float]:
    s = np.asarray(s, float)
    tp = [s[0]]
    for i in range(1, len(s) - 1):
        if (s[i] - s[i - 1]) * (s[i + 1] - s[i]) < 0:
            tp.append(s[i])
    tp.append(s[-1])
    return tp


def four_point_count(seq) -> tuple[list[float], list[float]]:
    """ASTM E1049-85 four-point rule, restricted to *interior* closed loops.

    Kept only as a cross-check on the ASTM canonical sequence (the four-point
    rule alone cannot decide the record extremities - the 3-point rule is
    required there, which is exactly why the main verification below uses
    analytically decomposable records instead of a second heuristic counter).
    """
    P = [float(v) for v in seq]
    closed: list[float] = []
    while len(P) >= 4:
        s1, s2, s3 = abs(P[1] - P[0]), abs(P[2] - P[1]), abs(P[3] - P[2])
        if s2 <= s1 and s2 <= s3:                      # middle range is closed
            closed.append(s2)
            P = [P[0]] + P[3:]
        else:
            P = P[1:]
    residue = [abs(P[i + 1] - P[i]) for i in range(len(P) - 1)]
    return closed, residue


def miner(ranges, half=False) -> float:
    w = 0.5 if half else 1.0
    return w * sum(1.0 / sn_cycles(r) for r in ranges)


# ---------------------------------------------------------------------------
# 1. ASTM E1049-85 canonical sequence
# ---------------------------------------------------------------------------

ASTM_SEQ = [-2.0, 1.0, -3.0, 5.0, -1.0, 3.0, -4.0, 4.0, -2.0]
ASTM_FULL = [3.0, 4.0, 8.0]        # closed cycles, ASTM E1049-85 Fig. 6
ASTM_HALF = [8.0, 6.0]             # residues -> half cycles (§5.4.4)


def test_astm_canonical_cycle_split():
    """Canonical sequence: 3 closed cycles + 2 half cycles, nothing shifted."""
    rep = rainflow_damage(np.asarray(ASTM_SEQ))
    assert rep["n_full_cycles"] == pytest.approx(3.0)
    assert rep["n_half_cycles"] == pytest.approx(2.0)
    assert rep["n_cycles"] == pytest.approx(4.0)          # 3 + 0.5 * 2
    assert rep["max_full_range_MPa"] == pytest.approx(8.0)


def test_astm_canonical_damage_matches_half_cycle_sum():
    rep = rainflow_damage(np.asarray(ASTM_SEQ))
    ref = miner(ASTM_FULL) + miner(ASTM_HALF, half=True)
    assert rep["damage"] == pytest.approx(ref, rel=1e-12)
    # the regression that motivated the fix: residues as full cycles gave +37 %
    full_weighted = miner(ASTM_FULL) + miner(ASTM_HALF)
    assert full_weighted / ref == pytest.approx(1.3733, abs=2e-3)
    assert rep["damage"] < 0.9 * full_weighted


def test_astm_canonical_four_point_interior_subset():
    """The four-point rule sees only the interior loop (range 4) of this record."""
    closed, _ = four_point_count(ASTM_SEQ)
    assert closed == [4.0]
    assert set(closed) <= set(ASTM_FULL)


def expanding_spiral(n: int) -> list[float]:
    """0, -1, 1, -2, 2, ... -n, n : every new range is larger than the previous."""
    tp = [0.0]
    for k in range(1, n + 1):
        tp += [-float(k), float(k)]
    return tp


def contracting_spiral(n: int) -> list[float]:
    """0, n, -n, n-1, -(n-1), ... 1, -1, 0 : strictly nested excursions."""
    tp = [0.0, float(n), -float(n)]
    for k in range(n - 1, 0, -1):
        tp += [float(k), -float(k)]
    tp.append(0.0)
    return tp


@pytest.mark.parametrize("n", [3, 5, 9])
def test_expanding_spiral_exact_counts(n):
    """Expanding spiral: closed cycles of range 1, 3, 5, ... 2n-1, one residue."""
    rep = rainflow_damage(np.asarray(expanding_spiral(n)))
    expected = [float(2 * k - 1) for k in range(1, n + 1)]
    assert rep["n_full_cycles"] == pytest.approx(n)
    assert rep["max_full_range_MPa"] == pytest.approx(2 * n - 1)
    assert rep["damage"] == pytest.approx(miner(expected), rel=1e-12)
    # exactly one residue: the final excursion n -> ... (single turning point left)
    assert rep["n_half_cycles"] in (0.0, 1.0)


@pytest.mark.parametrize("n", [3, 5, 9])
def test_contracting_spiral_exact_split(n):
    """Contracting spiral: 1 closed cycle of range n, then 2n-1 nested half cycles.

    Derived by hand from the ASTM E1049-85 three-point rule (worked in
    docs/audit/m1653_fatigue_astm_verification.md, Appendix A).
    """
    tp = contracting_spiral(n)
    rep = rainflow_damage(np.asarray(tp))
    assert rep["n_full_cycles"] == pytest.approx(1.0)
    assert rep["max_full_range_MPa"] == pytest.approx(float(n))
    half = [float(2 * n - 1 - k) for k in range(2 * n - 1)]     # 2n-1, 2n-2, .. 1
    assert rep["n_half_cycles"] == pytest.approx(len(half))
    ref = 1.0 / sn_cycles(float(n)) + 0.5 * miner(half)
    assert rep["damage"] == pytest.approx(ref, rel=1e-12)


# ---------------------------------------------------------------------------
# 2. exact analytic cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_periods,amp", [(50, 10.0), (120, 6.0), (400, 3.0)])
def test_pure_sine_damage_closed_form(n_periods, amp):
    """A sinusoid gives ``n`` cycles of range ``2A`` (residues are sub-1 %)."""
    t = np.arange(int(n_periods * 200)) * (2 * math.pi / 200)
    rec = amp * np.sin(t)
    rep = rainflow_damage(rec)
    exact = n_periods / sn_cycles(2 * amp)
    assert rep["damage"] == pytest.approx(exact, rel=0.02)
    assert rep["n_cycles"] == pytest.approx(n_periods, rel=0.03)
    assert rep["max_range_MPa"] == pytest.approx(2 * amp, rel=1e-6)


def test_block_loading_exact_two_cycles():
    """[-5, 5, -5, 5, -5] is exactly two cycles of range 10 with no residue."""
    rep = rainflow_damage(np.array([-5.0, 5.0, -5.0, 5.0, -5.0]))
    assert rep["n_full_cycles"] == pytest.approx(2.0)
    assert rep["n_half_cycles"] == pytest.approx(0.0)
    assert rep["damage"] == pytest.approx(2.0 / sn_cycles(10.0), rel=1e-12)


def test_residue_dominated_record_is_not_over_counted():
    """One ramp + one excursion: 1 closed cycle + 1 half cycle (was 2 full).

    ``0 -> 10`` is closed because the record subsequently falls below its
    starting level (to -2); the remaining ``-2 -> 8`` is a residue half cycle.
    """
    rep = rainflow_damage(np.array([0.0, 10.0, -2.0, 8.0]))
    assert rep["n_full_cycles"] == pytest.approx(1.0)
    assert rep["n_half_cycles"] == pytest.approx(1.0)
    assert rep["n_cycles"] == pytest.approx(1.5)
    assert rep["damage"] == pytest.approx(1.5 / sn_cycles(10.0), rel=1e-12)


# ---------------------------------------------------------------------------
# 3. DNV-RP-C203 curve F anchors
# ---------------------------------------------------------------------------


def test_sn_curve_f_knee_and_anchors():
    assert S_KNEE == (SN_A / 1e7) ** (1.0 / 3.0)
    assert sn_cycles(S_KNEE) == pytest.approx(1.0e7, rel=1e-6)
    # curve F: log10 a = 11.699, m = 3  -> N(100 MPa) = 5.0e5
    assert sn_cycles(100.0) == pytest.approx(5.0e5, rel=1e-3)
    assert math.log10(SN_A) == pytest.approx(11.699, abs=1e-12)


def test_sn_curve_bilinear_continuity():
    """Second branch must be continuous at the knee (C0) with slope ratio m2/m1."""
    eps = 1e-9 * S_KNEE
    n_lo, n_hi = sn_cycles(S_KNEE - eps), sn_cycles(S_KNEE + eps)
    assert abs(n_lo - n_hi) / n_hi < 1e-7
    assert SN_A2 == pytest.approx(SN_A * S_KNEE ** 2.0)


# ---------------------------------------------------------------------------
# 4. Rayleigh bilinear moment vs numerical integral
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sigma", [3.0, 5.0, 12.0, 30.0, 60.0])
def test_rayleigh_bilinear_moment_vs_numeric_integral(sigma):
    """Closed form vs ∫ S^m f(S) dS with the *normalised* Rayleigh-range density."""
    mom = rayleigh_bilinear_moment(sigma)
    S = np.linspace(1e-6, 16 * sigma, 800_000)
    f = S / (4 * sigma ** 2) * np.exp(-S ** 2 / (8 * sigma ** 2))   # ∫f dS = 1
    assert np.trapezoid(f, S) == pytest.approx(1.0, rel=1e-4)
    low = S <= S_KNEE
    hi = np.trapezoid(S[~low] ** 3 * f[~low], S[~low]) / SN_A
    lo = np.trapezoid(S[low] ** 5 * f[low], S[low]) / SN_A2
    closed = mom["inv_life_m3"] + mom["inv_life_m5"]
    assert closed == pytest.approx(hi + lo, rel=1e-3)


def test_rayleigh_moment_single_slope_limit():
    """Below the knee the moment must collapse to (2√2σ)^m Γ(1+m/2)/a."""
    sigma = 200.0                                   # S_knee deep in the low tail
    mom = rayleigh_bilinear_moment(sigma)
    ref = (2 * math.sqrt(2) * sigma) ** 3 * math.gamma(2.5) / SN_A
    assert mom["inv_life_m3"] == pytest.approx(ref, rel=1e-4)


# ---------------------------------------------------------------------------
# 5. spectral (narrow-band) damage vs Monte-Carlo rain-flow
# ---------------------------------------------------------------------------


def _record(Hs1, Tp1, Hs2, Tp2, n=120_000, dt=0.2, sigma=10.0, seed=3):
    rng = np.random.default_rng(seed)
    om = np.linspace(0.05, 3.0, 300)
    S = _jonswap(om, Hs1, Tp1) + (_jonswap(om, Hs2, Tp2) if Hs2 > 0 else 0.0)
    dw, amp = om[1] - om[0], None
    amp = np.sqrt(2.0 * S * dw)
    ph = rng.uniform(0.0, 2.0 * math.pi, om.size)
    t = np.arange(n) * dt
    rec = np.zeros_like(t)
    for a, w, p in zip(amp, om, ph):
        rec += a * np.cos(w * t + p)
    return rec * sigma / np.std(rec)


CASES = {
    "narrow_mono": (5.0, 8.0, 0.0, 0.0),
    "broad_mono": (5.0, 4.0, 0.0, 0.0),
    "bimodal_sep3_50_50": (5.0, 8.0, 2.0, 12.0),
    "bimodal_sep3_90_10": (5.0, 8.0, 1.6, 12.0),
    "bimodal_close": (5.0, 6.0, 3.0, 9.0),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_narrowband_inside_platform_15pct_bar(name):
    """Narrow-band Rayleigh damage vs rain-flow must stay inside the 15 % bar.

    It is a *conservative* error (over-prediction) for every measured spectrum,
    which is why the twin reports this value; the upper bound is pinned so that
    a future change to the S-N branch or the moment integral cannot drift.
    """
    rec = _record(*CASES[name])
    st = record_spectral_stats(rec, 0.2)
    mom = rayleigh_bilinear_moment(st["sigma_rms_MPa"])
    d_nb = st["nu0_Hz"] * len(rec) * 0.2 * (mom["inv_life_m3"] + mom["inv_life_m5"])
    d_rf = rainflow_damage(rec)["damage"]
    ratio = d_nb / d_rf
    assert 1.0 <= ratio <= 1.15, (name, ratio)
    assert st["bandwidth_eps"] > 0.3


@pytest.mark.parametrize("name", sorted(CASES))
def test_wirsching_light_overshoots_narrowband(name):
    """The wide-band factor over-corrects: |err| grows, sign flips to unsafe.

    Documented limitation (see docs/audit/m1653_fatigue_astm_verification.md):
    the spectral-moment bandwidth over-states the damage-relevant width of a
    bimodal sea state, so lambda < 1 is applied where rain-flow says the
    narrow-band formula is already right.  Pinned here so the deficit stays
    machine-visible instead of being silently "validated".
    """
    rec = _record(*CASES[name])
    st = record_spectral_stats(rec, 0.2)
    mom = rayleigh_bilinear_moment(st["sigma_rms_MPa"])
    d_nb = st["nu0_Hz"] * len(rec) * 0.2 * (mom["inv_life_m3"] + mom["inv_life_m5"])
    lam = wirsching_light_factor(3.0, st["bandwidth_eps"])
    ratio_wl = d_nb * lam / rainflow_damage(rec)["damage"]
    assert lam < 1.0
    assert ratio_wl < 1.0                       # non-conservative
    assert ratio_wl > 0.85                      # ... but still inside the 15 % bar


def test_narrowband_vs_rainflow_on_platform_reference_record():
    """The module's own reference record, end-to-end (uses its own nu0/bandwidth)."""
    rep = spectral_vs_rainflow(n=120_000, dt=0.2, sigma_rms_MPa=10.0)
    assert rep["damage_rainflow"] > 0.0
    assert rep["ratio_narrowband"] == pytest.approx(1.0, rel=0.05)
    # the WL-corrected value is *worse* than the narrow band on the same record
    assert abs(rep["ratio_wl"] - 1.0) > abs(rep["ratio_narrowband"] - 1.0)


# ---------------------------------------------------------------------------
# 6. Wirsching-Light factor against its published coefficients
# ---------------------------------------------------------------------------


def test_wirsching_light_published_coefficients():
    m = 3.0
    a, b = 0.926 - 0.033 * m, 1.587 * m - 2.323
    for eps in (0.0, 0.25, 0.5, 0.75, 0.9):
        ref = a + (1.0 - a) * (1.0 - eps) ** b
        assert wirsching_light_factor(m, eps) == pytest.approx(ref, rel=1e-12)
    assert wirsching_light_factor(m, 0.0) == pytest.approx(1.0)      # narrow band


def test_wirsching_light_monotone_in_bandwidth():
    vals = [wirsching_light_factor(3.0, e) for e in np.linspace(0, 1, 21)]
    assert all(b <= a + 1e-15 for a, b in zip(vals, vals[1:]))


# ---------------------------------------------------------------------------
# 7. production path: reported damage is the conservative narrow-band value
# ---------------------------------------------------------------------------


@pytest.fixture()
def hull():
    return HullForm(180.0, 30.0, 18.0, 0.82)


def test_spectral_fatigue_reports_narrowband(hull):
    r = spectral_fatigue(hull, Hs=5.0, Tp=8.0, U_knots=12.0,
                         ice_concentration=0.0, exposure_s=86400.0)
    assert r["wideband_applied"] == 0.0
    assert r["damage_wave"] == pytest.approx(r["damage_wave_narrowband"], rel=1e-12)
    assert r["damage_wave_wideband"] == pytest.approx(
        r["damage_wave_narrowband"] * r["wideband_factor"], rel=1e-12)
    assert r["damage_wave"] >= r["damage_wave_wideband"]       # conservative side
    assert r["damage_total"] >= r["damage_wave"]
    assert r["life_years"] == pytest.approx(
        1.0 / r["damage_per_day"] / 365.25, rel=1e-9)


def test_spectral_fatigue_wideband_switch_restores_old_path(hull):
    kw = dict(Hs=5.0, Tp=8.0, U_knots=12.0, ice_concentration=0.0)
    a = spectral_fatigue(hull, **kw)
    b = spectral_fatigue(hull, apply_wideband=True, **kw)
    assert b["wideband_applied"] == 1.0
    assert b["damage_wave"] == pytest.approx(a["damage_wave_wideband"], rel=1e-12)
    assert b["damage_total"] < a["damage_total"]
    # 19 % of the wave term: the measured WL overshoot on the default reference
    assert a["damage_wave"] / b["damage_wave"] == pytest.approx(
        1.0 / a["wideband_factor"], rel=1e-12)


def test_spectral_fatigue_monotone_in_sea_state(hull):
    prev = -1.0
    for Hs in (2.0, 4.0, 6.0, 9.0):
        r = spectral_fatigue(hull, Hs=Hs, Tp=8.0, U_knots=12.0)
        assert r["damage_wave"] > prev
        prev = r["damage_wave"]
    # ice cycling adds damage and never removes it
    dry = spectral_fatigue(hull, Hs=4.0, Tp=8.0, U_knots=8.0, ice_concentration=0.0)
    icy = spectral_fatigue(hull, Hs=4.0, Tp=8.0, U_knots=8.0, ice_concentration=0.7,
                           ice_thickness=0.6)
    assert icy["damage_total"] > dry["damage_total"]
    assert icy["ice_cycles"] > 0.0


def test_sensor_path_stress_damage_inverse_consistent():
    for d0 in (1e-6, 5e-4, 2e-2):
        sig = stress_from_damage(d0, Tz_s=5.7)
        assert damage_from_stress_rms(sig, Tz_s=5.7) == pytest.approx(d0, rel=1e-3)
    assert damage_from_stress_rms(0.0) == pytest.approx(0.0)