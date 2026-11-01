"""Absolute-level verification of ``propeller.open_water`` against the
Wageningen B-series polynomial (mission 1668).

This suite closes the ``WITHHELD`` that
``docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md`` §5.1/§6 carried for
absolute accuracy.  It asserts four things:

1. **Sensitivity** — the audit can still see the defect: the certified
   (uncalibrated) BEM deviates from the B-series reference far beyond the 15 %
   acceptance bar, so a green run below cannot be an artefact of a blind test.
2. **Level** — the calibrated closure reproduces the B-series ``KT``, ``KQ`` and
   ``eta0`` within the declared bands, on the **hold-out** grid (Z=5,6,
   P/D in {0.9,1.1}, Ae/A0 in {0.475,0.625}) that took no part in the fit.
3. **No regression of the certified path** — ``open_water_point`` without a
   ``section`` argument is bit-identical to ``DEFAULT_SECTION``, i.e. the
   calibration hook is purely additive.
4. **Invariants survive the calibration** — ``eta0 <= eta_ideal``, monotone
   ``KT(J)``/``KQ(J)`` decay, single interior efficiency maximum, Froude-scaling
   invariance.  A fitted model that broke these would be useless regardless of
   its fit quality.

Reference: ``tools/verify/propeller_open_water/reference_bseries.py``
(Oosterveld & van Oossanen 1975, 39 ``KT`` / 47 ``KQ`` terms).
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.services import propeller_bseries_calibration as cal  # noqa: E402
from backend.services.propeller_open_water import (  # noqa: E402
    DEFAULT_SECTION,
    PropellerGeometry,
    open_water_point,
)
from tools.verify.propeller_open_water import reference_bseries as ref  # noqa: E402

HOLDOUT = [(5, 0.9, 0.475), (5, 1.1, 0.625), (6, 0.9, 0.625), (6, 1.1, 0.475)]
TRAIN = [(4, 0.8, 0.40), (4, 1.0, 0.55), (4, 1.2, 0.70)]
J_SWEEP = (0.2, 0.4, 0.6, 0.8)


def geom(z, p_d, ae, sections=40):
    return PropellerGeometry(diameter_m=5.0, pitch_ratio=p_d, blade_area_ratio=ae,
                             blade_count=z, hub_ratio=0.18, sections=sections)


def _errors(grid, mode):
    out = {"KT": [], "KQ": [], "eta0": []}
    for z, p_d, ae in grid:
        g = geom(z, p_d, ae)
        for J in J_SWEEP:
            r = ref.open_water_coefficients(J, p_d, ae, z)
            if r["KT"] <= 0.02:
                continue
            m = (open_water_point(g, J) if mode == "certified"
                 else cal.open_water_point_calibrated(g, J))
            out["KT"].append(100.0 * (m["KT"] - r["KT"]) / r["KT"])
            out["KQ"].append(100.0 * (m["KQ"] - r["KQ"]) / r["KQ"])
            out["eta0"].append(100.0 * (m["eta0"] - r["eta_0"]) / r["eta_0"])
    return out


def first_zero_crossing(g, j_hi=2.0, steps=200):
    """First J where KT changes sign, by fine sweep + bisection."""
    lo, prev = 1e-6, open_water_point(g, 1e-6)["KT"]
    for k in range(1, steps + 1):
        J = j_hi * k / steps
        kt = open_water_point(g, J)["KT"]
        if kt <= 0.0 < prev:
            hi = J
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                if open_water_point(g, mid)["KT"] > 0.0:
                    lo = mid
                else:
                    hi = mid
            return 0.5 * (lo + hi)
        lo, prev = J, kt
    return j_hi


def _mean_abs(vals):
    return sum(abs(v) for v in vals) / len(vals)


class TestSensitivity:
    """The certified model must be *seen* to fail - otherwise the test is blind."""

    def test_certified_kt_exceeds_acceptance_bar(self):
        e = _errors(TRAIN, "certified")["KT"]
        assert _mean_abs(e) > 15.0, f"mean |dKT| = {_mean_abs(e):.1f}% - test has lost sensitivity"

    def test_certified_zero_thrust_ratio_too_low(self):
        """The symmetric section loses thrust at J ~= P/D instead of ~1.2 P/D.

        ``J0`` is taken as the *first* zero crossing of ``KT(J)``: the certified
        closure is not monotone in J (see ``test_certified_kt_not_monotone``),
        so ``solve_zero_thrust_advance_ratio`` - which brackets on [0, 3] - can
        return the second crossing instead.
        """
        for z, p_d, ae in TRAIN:
            g = geom(z, p_d, ae)
            j0_bem = first_zero_crossing(g)
            j0_ref = ref.zero_thrust_J(p_d, ae, z)
            assert j0_bem < 0.95 * j0_ref, f"Z={z} P/D={p_d} Ae/A0={ae}: {j0_bem:.3f} vs {j0_ref:.3f}"

    def test_certified_kt_decays_too_fast(self):
        """Documented defect: thrust collapses early once J exceeds ~0.99 P/D."""
        g = geom(4, 0.8, 0.40)
        kt = [open_water_point(g, J)["KT"] for J in (0.4, 0.7, 0.9)]
        assert kt[0] > kt[1] > kt[2], "KT must decay monotonically"
        assert kt[2] < 0.0, "certified BEM already produces drag at J=0.9 (ref. KT=+0.024)"


class TestCalibratedLevel:
    """Hold-out accuracy bands: the numbers the mission claims."""

    @pytest.mark.parametrize("key,band", [("KT", 6.0), ("KQ", 10.0), ("eta0", 8.0)])
    def test_holdout_mean_error(self, key, band):
        e = _errors(HOLDOUT, "calibrated")[key]
        assert _mean_abs(e) < band, f"hold-out mean |d{key}| = {_mean_abs(e):.2f}% > {band}%"

    def test_holdout_efficiency_within_acceptance_bar(self):
        """Every hold-out efficiency point inside the 15 % acceptance bar."""
        e = _errors(HOLDOUT, "calibrated")["eta0"]
        assert max(abs(v) for v in e) < 15.0

    def test_calibration_improves_every_quantity(self):
        for key in ("KT", "KQ", "eta0"):
            base = _mean_abs(_errors(HOLDOUT, "certified")[key])
            tuned = _mean_abs(_errors(HOLDOUT, "calibrated")[key])
            assert tuned < 0.65 * base, f"{key}: {tuned:.1f}% vs certified {base:.1f}%"

    def test_zero_thrust_ratio_recovered(self):
        for z, p_d, ae in HOLDOUT:
            g = geom(z, p_d, ae)
            j0 = cal.zero_thrust_advance_ratio_calibrated(g)
            j0_ref = ref.zero_thrust_J(p_d, ae, z)
            assert abs(j0 - j0_ref) / j0_ref < 0.05


class TestCertifiedPathUntouched:
    """The hook is additive: default behaviour must not move."""

    def test_default_section_is_identity(self):
        g = geom(4, 1.0, 0.55)
        for J in (0.1, 0.5, 0.9):
            a = open_water_point(g, J)
            b = open_water_point(g, J, section=DEFAULT_SECTION)
            c = open_water_point(g, J, axial_induction=False)
            assert a["KT"] == b["KT"] and a["KQ"] == b["KQ"]
            assert c["KT"] != a["KT"]  # the switch still does something

    def test_unit_section_reproduces_certified_numbers(self):
        g = geom(5, 0.9, 0.625)
        sec = cal.calibrated_section(g, zero_lift_rad=0.0, k_solidity=0.0, drag_scale=1.0)
        a = open_water_point(g, 0.5)
        b = open_water_point(g, 0.5, section=sec)
        assert a["KT"] == pytest.approx(b["KT"], rel=1e-12)
        assert a["KQ"] == pytest.approx(b["KQ"], rel=1e-12)


class TestCalibratedInvariants:
    """Physics rails that the fitted closure must not break."""

    @pytest.mark.parametrize("z,p_d,ae", HOLDOUT)
    def test_efficiency_below_actuator_disc_bound(self, z, p_d, ae):
        g = geom(z, p_d, ae)
        for J in (0.2, 0.4, 0.6, 0.8):
            out = cal.open_water_point_calibrated(g, J)
            if out["KT"] > 0.0:
                assert out["eta0"] <= out["eta_ideal"] + 1e-9

    @pytest.mark.parametrize("z,p_d,ae", HOLDOUT)
    def test_monotone_decay_and_single_efficiency_peak(self, z, p_d, ae):
        g = geom(z, p_d, ae)
        Js = [0.02 * k for k in range(1, 101)]
        pts = [cal.open_water_point_calibrated(g, J) for J in Js]
        kt = [p["KT"] for p in pts]
        kq = [p["KQ"] for p in pts]
        assert all(b <= a + 1e-9 for a, b in zip(kt, kt[1:])), "KT not monotone decreasing"
        assert all(b <= a + 1e-9 for a, b in zip(kq, kq[1:])), "KQ not monotone decreasing"
        etas = [p["eta0"] for p in pts if p["KT"] > 0.0]
        peak = max(range(len(etas)), key=etas.__getitem__)
        assert 0 < peak < len(etas) - 1, "efficiency maximum not interior"

    def test_froude_scaling_invariance(self):
        """Non-dimensional coefficients are invariant under geometric scaling."""
        lam = 2.5
        g1 = geom(4, 1.0, 0.55)
        g2 = PropellerGeometry(diameter_m=5.0 * lam, pitch_ratio=1.0, blade_area_ratio=0.55,
                               blade_count=4, hub_ratio=0.18, sections=40)
        for J in (0.3, 0.6, 0.9):
            a = cal.open_water_point_calibrated(g1, J)
            b = cal.open_water_point_calibrated(g2, J)
            assert a["KT"] == pytest.approx(b["KT"], rel=1e-9)
            assert a["KQ"] == pytest.approx(b["KQ"], rel=1e-9)

    def test_declared_blade_area_honoured(self):
        for z, p_d, ae in HOLDOUT:
            g = geom(z, p_d, ae)
            assert g.blade_area_m2 == pytest.approx(ae * 3.141592653589793 * g.radius_m ** 2, rel=1e-12)


class TestSolidityModel:
    def test_solidity_increases_with_blade_area_ratio(self):
        lo = cal.solidity_07(geom(4, 1.0, 0.40))
        hi = cal.solidity_07(geom(4, 1.0, 0.70))
        assert hi > lo > 0.0

    def test_lift_deficit_grows_with_solidity(self):
        lo = cal.calibrated_section(geom(4, 1.0, 0.40)).lift_scale
        hi = cal.calibrated_section(geom(4, 1.0, 0.70)).lift_scale
        assert 0.0 < hi < lo < 1.0