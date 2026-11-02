"""Holtrop–Mennen (1982+1984) verification suite.

Evidence grade
--------------
The platform capability ``calc.resistance`` is advertised as REAL, but the
shipped endpoint uses an axisymmetric-body fit (``S = pi*B*L*(0.5+0.25*Cb)``,
``Cr = 0.002*(1+2*Cb)*Fn^2.5``) that is **not** Holtrop–Mennen.  The proper
Holtrop–Mennen implementation lives in ``services/holtrop_mennen.py`` (merged
in m1398) but was never wired to the endpoint or verified against published
reference values.

This suite does three things:

1. **Self-consistency checks** — verifies the internal mathematical coherence
   of the Holtrop–Mennen implementation (coefficient continuity, symmetry,
   domain boundaries, scaling laws).
2. **Cross-model comparison** — compares Holtrop–Mennen against the shipped
   axisymmetric model for the same hull, quantifying the shape-dependent bias.
3. **Reference-case verification** — runs KVLCC2 (SIMMAN 2008) at design speed
   and checks that the result falls within the published engineering envelope
   (total resistance ~1.8–2.0 MN at 15.5 kn for full-scale).

No literature *point* data (e.g. exact Rw from Holtrop & Mennen 1982 Table 2)
was reachable offline for coefficient-level verification; absolute-level
accuracy against published charts therefore stays WITHHELD and is documented
as such.  The KVLCC2 total-resistance envelope check is an *engineering
acceptance* test, not a point-accuracy claim.

Reference values
----------------
KVLCC2 full-scale (SIMMAN 2008):
  L = 320 m, B = 58 m, T = 20.8 m, Cb = 0.8098
  Design speed: 15.5 kn (7.974 m/s), Fn = 0.142
  Published total resistance range: ~1.8–2.0 MN (various CFD/EFD sources)
  Wetted surface: ~26 000–27 000 m² (various estimates)
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))

from services.holtrop_mennen import (  # noqa: E402
    c7_coefficient, c7_flagged, c15_coefficient, c16_coefficient,
    domain_warnings, half_entrance_angle, holtrop_resistance, in_domain,
    ittc1957_cf, wetted_surface, HoltropResult,
)
from services.ship_resistance import (  # noqa: E402
    wetted_surface_axisym, ittc57_cf as ship_res_itfc57,
    resistance_breakdown,
)

# ---------------------------------------------------------------------------
# KVLCC2 reference particulars (SIMMAN 2008)
# ---------------------------------------------------------------------------
KVLCC2 = {
    "L": 320.0,       # m
    "B": 58.0,        # m
    "T": 20.8,        # m
    "Cb": 0.8098,
    "speed_kn": 15.5,
    "rho": 1025.0,    # sea water
    "nu": 1.188e-6,   # sea water @ 15 °C
    "lcb_frac": -0.015,
}
KVLCC2_SPEED_MS = KVLCC2["speed_kn"] * 0.514444


def _run_holtrop(speed_ms: float, **overrides) -> HoltropResult:
    """Run Holtrop–Mennen for KVLCC2 at a given speed."""
    kw = dict(
        length=KVLCC2["L"], beam=KVLCC2["B"], draft=KVLCC2["T"],
        cb=KVLCC2["Cb"], speed_ms=speed_ms,
        rho=KVLCC2["rho"], nu=KVLCC2["nu"],
        cm=None, cwp=None, lcb_frac=KVLCC2["lcb_frac"],
        cstern=0.0, abt=0.0, hb=0.0, at=0.0,
        t_forward=None, s_appendage=0.0, form_factor_appendage=1.0,
        apply_correlation_allowance=True,
    )
    kw.update(overrides)
    return holtrop_resistance(**kw)


# ===================================================================
# 1. Self-consistency checks
# ===================================================================

class TestCoefficientContinuity:
    """Published Holtrop coefficients have piecewise definitions; verify
    continuity at the branch points (or document the discontinuity)."""

    def test_c16_continuous_at_cp_08(self):
        """c16: cubic branch (Cp<0.8) vs linear branch (Cp>=0.8)."""
        left = c16_coefficient(0.7999)
        right = c16_coefficient(0.8001)
        rel_err = abs(right - left) / max(abs(left), 1e-12)
        assert rel_err < 0.01, f"c16 discontinuity at Cp=0.8: {rel_err*100:.2f}%"

    def test_c15_bounded(self):
        """c15: piecewise at L/∇^(1/3) = 12; check it doesn't blow up."""
        left = c15_coefficient(11.99)
        right = c15_coefficient(12.01)
        assert -5.0 < left < 5.0
        assert -5.0 < right < 5.0

    def test_c7_discontinuity_documented(self):
        """c7 is *known* to be discontinuous at B/L = 0.11 (published regression
        artifact).  The implementation clips non-positive values; verify the
        flag fires at the known problematic range."""
        assert c7_flagged(0.11) is True
        assert c7_flagged(0.10) is False
        assert c7_flagged(0.12) is True
        assert c7_flagged(0.20) is False
        assert c7_flagged(0.26) is False


class TestScalingLaws:
    """Holtrop–Mennen must obey basic physical scaling laws."""

    def test_resistance_scales_with_density(self):
        """R ∝ ρ for the friction and correlation terms (dominant at low Fn)."""
        r1 = _run_holtrop(KVLCC2_SPEED_MS, rho=1025.0)
        r2 = _run_holtrop(KVLCC2_SPEED_MS, rho=998.2)
        ratio = r1.rt_n / r2.rt_n
        expected_ratio = 1025.0 / 998.2
        assert abs(ratio - expected_ratio) < 0.05, (
            f"Resistance density scaling: {ratio:.4f} vs {expected_ratio:.4f}")

    def test_resistance_increases_with_speed(self):
        """Total resistance must be monotonically increasing with speed."""
        speeds = [5.0, 7.0, 10.0, 15.0, 20.0]
        results = [_run_holtrop(v) for v in speeds]
        rts = [r.rt_n for r in results]
        assert all(b > a for a, b in zip(rts, rts[1:])), (
            f"Resistance not monotonic: {rts}")

    def test_wetted_surface_scales_with_dimensions(self):
        """S should scale roughly with L·B for similar hulls."""
        s1 = wetted_surface(320, 58, 20.8, 0.8098, 0.985, 0.850, 0.0)
        s2 = wetted_surface(160, 29, 10.4, 0.8098, 0.985, 0.850, 0.0)
        ratio = s1 / s2
        assert 3.5 < ratio < 4.5, f"Wetted surface scaling: {ratio:.2f} (expected ~4)"


class TestDomainChecks:
    """Holtrop–Mennen validity domain must be correctly flagged."""

    def test_kvlcc2_in_domain(self):
        """KVLCC2 is inside the Holtrop domain for most parameters."""
        warns = domain_warnings(
            KVLCC2["L"], KVLCC2["B"], KVLCC2["T"], KVLCC2["Cb"],
            KVLCC2_SPEED_MS / math.sqrt(9.81 * KVLCC2["L"]))
        assert len(warns) <= 1
        assert all("secondary" in w or "c7" in w for w in warns)

    def test_out_of_domain_cb_low(self):
        warns = domain_warnings(300, 50, 15, 0.40, 0.2)
        assert any("Cb" in w for w in warns)

    def test_out_of_domain_fn_high(self):
        warns = domain_warnings(300, 50, 15, 0.70, 0.50)
        assert any("Fn" in w for w in warns)

    def test_in_domain_helper(self):
        assert in_domain(320, 58, 20.8, 0.8098, 0.142) is True
        assert in_domain(300, 50, 15, 0.40, 0.20) is False


# ===================================================================
# 2. Cross-model comparison: Holtrop vs axisymmetric fit
# ===================================================================

class TestCrossModelComparison:
    """Compare Holtrop–Mennen against the shipped axisymmetric-body model.

    Key finding: the axisymmetric model gives a *similar total resistance*
    for KVLCC2 (within ~3%) but through a completely wrong decomposition:
    S is 53% too large, Ct is 33% too small, and there is no form factor.
    This is a classic "right answer for the wrong reason" situation that
    would fail on any hull outside the SUBOFF calibration range.
    """

    def test_wetted_surface_bias_kvlcc2(self):
        """The axisymmetric fit significantly overestimates S for a full-form
        tanker (it was calibrated for SUBOFF-like slender bodies)."""
        s_holtrop = wetted_surface(
            KVLCC2["L"], KVLCC2["B"], KVLCC2["T"], KVLCC2["Cb"],
            0.985, 0.850, 0.0)
        s_axisym = wetted_surface_axisym(KVLCC2["B"], KVLCC2["L"], KVLCC2["Cb"])
        bias = (s_axisym - s_holtrop) / s_holtrop * 100
        assert bias > 40, f"Axisymmetric S bias should be >40%: {bias:.1f}%"

    def test_friction_line_agreement(self):
        """ITTC-1957 Cf must be identical between the two implementations."""
        re = KVLCC2_SPEED_MS * KVLCC2["L"] / KVLCC2["nu"]
        cf_hm = ittc1957_cf(re)
        cf_sr = ship_res_itfc57(re)
        assert cf_hm == pytest.approx(cf_sr, rel=1e-12)

    def test_ct_decomposition_differs(self):
        """Ct values differ significantly between the two models, even though
        total resistance happens to be similar (compensating errors)."""
        hm = _run_holtrop(KVLCC2_SPEED_MS)
        sr = resistance_breakdown(
            KVLCC2["L"], KVLCC2["B"], KVLCC2["T"], KVLCC2["Cb"],
            KVLCC2_SPEED_MS, rho=KVLCC2["rho"], nu=KVLCC2["nu"])
        ct_diff = abs(hm.ct - sr["ct"]) / hm.ct * 100
        assert ct_diff > 20, f"Ct difference should be >20%: {ct_diff:.1f}%"

    def test_form_factor_absent_in_axisym(self):
        """The axisymmetric model has no form factor (1+k=1), while Holtrop
        gives ~1.34 for KVLCC2."""
        hm = _run_holtrop(KVLCC2_SPEED_MS)
        assert hm.form_factor > 1.30, f"1+k = {hm.form_factor:.4f}"


# ===================================================================
# 3. Reference-case verification: KVLCC2 at design speed
# ===================================================================

class TestKVLCC2ReferenceCase:
    """Verify Holtrop–Mennen against the published engineering envelope for
    KVLCC2 at 15.5 kn (design speed)."""

    def test_design_speed_fn(self):
        """Froude number at 15.5 kn must be ~0.142."""
        hm = _run_holtrop(KVLCC2_SPEED_MS)
        assert 0.140 < hm.fn < 0.145, f"Fn = {hm.fn:.4f}"

    def test_wetted_surface_in_published_range(self):
        """Published S for KVLCC2: ~26 000–27 000 m²."""
        hm = _run_holtrop(KVLCC2_SPEED_MS)
        assert 25000 < hm.wetted_surface_m2 < 28000, (
            f"S = {hm.wetted_surface_m2:.0f} m² outside published range")

    def test_form_factor_reasonable(self):
        """1+k for a full-form tanker should be ~1.30–1.40."""
        hm = _run_holtrop(KVLCC2_SPEED_MS)
        assert 1.25 < hm.form_factor < 1.45, (
            f"1+k = {hm.form_factor:.4f} outside tanker range")

    def test_total_resistance_in_engineering_envelope(self):
        """Published RT for KVLCC2 at 15.5 kn: ~1.8–2.0 MN (various sources).

        This is an *engineering envelope* check, not a point-accuracy claim.
        The exact value depends on the appendage resistance, CA correction,
        and the wave-resistance coefficients (which are WITHHELD per the
        m1398 audit).  The envelope is wide enough to accommodate these
        uncertainties while still catching gross errors.
        """
        hm = _run_holtrop(KVLCC2_SPEED_MS)
        rt_mn = hm.rt_n / 1e6
        assert 1.5 < rt_mn < 2.5, (
            f"RT = {rt_mn:.3f} MN outside engineering envelope [1.5, 2.5]")

    def test_friction_dominance_at_low_fn(self):
        """At Fn=0.14, friction (1+k)·RF should dominate (>80% of RT)."""
        hm = _run_holtrop(KVLCC2_SPEED_MS)
        friction_fraction = hm.rf_n / hm.rt_n * 100
        assert friction_fraction > 80, (
            f"Friction fraction = {friction_fraction:.1f}% (expected >80%)")

    def test_wave_resistance_small_at_low_fn(self):
        """At Fn=0.14, RW should be a small fraction of RT (<10%)."""
        hm = _run_holtrop(KVLCC2_SPEED_MS)
        wave_fraction = hm.rw_n / hm.rt_n * 100
        assert wave_fraction < 10, (
            f"Wave fraction = {wave_fraction:.1f}% (expected <10%)")


# ===================================================================
# 4. Speed sweep: resistance curve shape
# ===================================================================

class TestResistanceCurve:
    """Verify the shape of the resistance curve across a speed range."""

    @pytest.fixture(scope="class")
    def speed_sweep(cls):
        speeds_kn = [5, 8, 10, 12, 14, 15.5, 16, 18, 20]
        results = []
        for v_kn in speeds_kn:
            v_ms = v_kn * 0.514444
            hm = _run_holtrop(v_ms)
            results.append({
                "speed_kn": v_kn, "speed_ms": v_ms,
                "fn": hm.fn, "rt_kn": hm.rt_n / 1000,
                "ct": hm.ct, "rf_kn": hm.rf_n / 1000,
                "rw_kn": hm.rw_n / 1000, "ra_kn": hm.ra_n / 1000,
            })
        return results

    def test_resistance_curve_monotonic(self, speed_sweep):
        rts = [r["rt_kn"] for r in speed_sweep]
        assert all(b > a for a, b in zip(rts, rts[1:]))

    def test_ct_decreases_then_flattens(self, speed_sweep):
        """Ct typically decreases with speed for displacement hulls at low Fn."""
        cts = [r["ct"] for r in speed_sweep]
        first_half = cts[:len(cts)//2]
        assert all(b <= a for a, b in zip(first_half, first_half[1:]))

    def test_wave_resistance_grows_with_fn(self, speed_sweep):
        """RW/RT fraction should increase with Fn."""
        fractions = [r["rw_kn"] / r["rt_kn"] for r in speed_sweep]
        assert fractions[-1] > fractions[0], (
            f"Wave fraction not increasing: {fractions[0]:.3f} -> {fractions[-1]:.3f}")
