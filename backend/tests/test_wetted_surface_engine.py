"""m1637 — wetted-surface estimator: closed-form and literature regression guards.

Run with::

    python3 -m pytest backend/tests/test_wetted_surface_engine.py -q

The references are re-derived here from closed form (cylinder, spheroids,
sphere, capsule) and from recorded literature values (KCS / KVLCC2 /
DTMB-5415), so the assertions fail if the estimator drifts away from geometry
rather than from a frozen number.  The acceptance bar is the platform standard:
|error| <= 15 % is unacceptable -> here the in-domain worst case must stay
below that, and the ship band with Cb <= 0.90 is held to 5 %.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import wetted_surface as ws  # noqa: E402

L_SUBOFF, D_SUBOFF = 4.356, 0.508


def s_cylinder_closed(d: float, length: float) -> float:
    return math.pi * d * length + 0.5 * math.pi * d * d


def s_spheroid(length: float, d: float) -> float:
    a, b = length / 2.0, d / 2.0
    e = math.sqrt(1.0 - (b * b) / (a * a))
    return 2.0 * math.pi * b * b + 2.0 * math.pi * a * b * math.asin(e) / e


def s_capsule(d: float, length: float) -> float:
    r = d / 2.0
    return 2.0 * math.pi * r * (length - 2.0 * r) + 4.0 * math.pi * r * r


def err(value: float, ref: float) -> float:
    return value / ref - 1.0


# ---------------------------------------------------------------------------
# body-of-revolution branch — exact references
# ---------------------------------------------------------------------------

class TestRevolutionBranch:
    def test_closed_cylinder_is_exact(self):
        cb = math.pi / 4  # V = pi D^2 L / 4 over the L*D*D box
        s = ws.estimate_wetted_surface(L_SUBOFF, D_SUBOFF, D_SUBOFF, cb)
        assert s.model == ws.MODEL_CAPPED_CYLINDER
        assert abs(err(s.area_m2, s_cylinder_closed(D_SUBOFF, L_SUBOFF))) < 5e-3

    @pytest.mark.parametrize("beam", [0.508, 0.3048])
    def test_spheroid_is_exact_for_two_fineness_ratios(self, beam):
        s = ws.estimate_wetted_surface(L_SUBOFF, beam, beam, math.pi / 6)
        assert abs(err(s.area_m2, s_spheroid(L_SUBOFF, beam))) < 5e-3

    def test_capsule_with_parallel_midbody_within_6pct(self):
        """SUBOFF-like hull: cylinder + hemispherical caps."""
        r = D_SUBOFF / 2.0
        volume = math.pi * r * r * (L_SUBOFF - 2 * r) + 4.0 / 3.0 * math.pi * r ** 3
        cb = volume / (L_SUBOFF * D_SUBOFF * D_SUBOFF)
        s = ws.estimate_wetted_surface(L_SUBOFF, D_SUBOFF, D_SUBOFF, cb)
        assert s.model == ws.MODEL_CAPPED_CYLINDER
        assert abs(err(s.area_m2, s_capsule(D_SUBOFF, L_SUBOFF))) < 0.06

    def test_sphere_degenerate_case_is_bounded(self):
        s = ws.estimate_wetted_surface(1.0, 1.0, 1.0, math.pi / 6)
        assert abs(err(s.area_m2, math.pi)) < 5e-3

    def test_blunter_than_a_sphere_is_bounded(self):
        """No NaN/overflow when the volume cannot fit the requested length."""
        s = ws.estimate_wetted_surface(1.0, 1.0, 1.0, 1.0)
        assert math.isfinite(s.area_m2) and s.area_m2 > 0.0


# ---------------------------------------------------------------------------
# ship branch — literature values
# ---------------------------------------------------------------------------

SHIP_CASES = {
    # name: (L, B, T, Cb, S_w literature m^2, tolerance)
    "KCS": (230.0, 32.2, 10.8, 0.6505, 9530.0, 0.05),
    "KVLCC2": (320.0, 58.0, 20.8, 0.8098, 27194.0, 0.05),
    "DTMB5415": (5.72, 0.768, 0.248, 0.5065, 4.72, 0.05),
}


class TestShipBranch:
    @pytest.mark.parametrize("name", sorted(SHIP_CASES))
    def test_literature_wetted_surface_within_5pct(self, name):
        L, B, T, cb, s_lit, tol = SHIP_CASES[name]
        s = ws.estimate_wetted_surface(L, B, T, cb)
        assert s.model == ws.MODEL_DENNY_MUMFORD
        assert abs(err(s.area_m2, s_lit)) < tol

    def test_box_barge_within_15pct(self):
        s_ref = 100.0 * 20.0 + 2.0 * 100.0 * 8.0 + 2.0 * 20.0 * 8.0
        s = ws.estimate_wetted_surface(100.0, 20.0, 8.0, 1.0)
        assert s.model == ws.MODEL_DENNY_MUMFORD
        assert abs(err(s.area_m2, s_ref)) < 0.15

    def test_wigley_is_routed_to_the_ship_branch(self):
        """Fine model hull (B/T = 1.6) must not fall into the bare-body branch."""
        s = ws.estimate_wetted_surface(6.0, 0.6, 0.375, 0.4444)
        assert s.model == ws.MODEL_DENNY_MUMFORD

    def test_body_of_revolution_uses_bare_body_branch(self):
        s = ws.estimate_wetted_surface(L_SUBOFF, D_SUBOFF, D_SUBOFF, 0.52)
        assert s.model == ws.MODEL_CAPPED_CYLINDER


# ---------------------------------------------------------------------------
# the debt being paid: legacy bias vs repaired bias
# ---------------------------------------------------------------------------

class TestLegacyBiasIsPaid:
    def test_legacy_formula_fails_the_15pct_bar_where_the_new_one_passes(self):
        cases = [
            ("cylinder", L_SUBOFF, D_SUBOFF, D_SUBOFF, math.pi / 4,
             s_cylinder_closed(D_SUBOFF, L_SUBOFF)),
            ("spheroid", L_SUBOFF, D_SUBOFF, D_SUBOFF, math.pi / 6,
             s_spheroid(L_SUBOFF, D_SUBOFF)),
            ("KVLCC2", 320.0, 58.0, 20.8, 0.8098, 27194.0),
            ("KCS", 230.0, 32.2, 10.8, 0.6505, 9530.0),
        ]
        for name, L, B, T, cb, ref in cases:
            leg = ws.legacy_axisymmetric(L, B, cb)
            new = ws.estimate_wetted_surface(L, B, T, cb).area_m2
            assert abs(err(leg, ref)) > 0.15, f"{name}: legacy bias not reproduced"
            assert abs(err(new, ref)) < 0.05, f"{name}: repaired error {err(new, ref):+.1%}"

    def test_legacy_still_reachable_bit_exactly(self):
        L, B, cb = 4.356, 0.508, 0.542
        s = ws.estimate_wetted_surface(L, B, 0.476, cb, model="legacy")
        assert s.model == ws.MODEL_LEGACY
        assert s.area_m2 == pytest.approx(math.pi * B * L * (0.5 + 0.25 * cb), rel=1e-15)


# ---------------------------------------------------------------------------
# contract / error handling
# ---------------------------------------------------------------------------

class TestContract:
    def test_unknown_model_is_rejected(self):
        with pytest.raises(ws.WettedSurfaceError):
            ws.estimate_wetted_surface(10.0, 2.0, 1.0, 0.6, model="holtrop_magic")

    def test_auto_requires_a_draft(self):
        with pytest.raises(ws.WettedSurfaceError):
            ws.estimate_wetted_surface(10.0, 2.0, None, 0.6)

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_non_positive_dimensions_rejected(self, bad):
        with pytest.raises(ws.WettedSurfaceError):
            ws.denny_mumford(100.0, bad, 5.0, 0.7)

    def test_branch_reason_is_populated(self):
        s = ws.estimate_wetted_surface(320.0, 58.0, 20.8, 0.8098)
        assert "ship form" in s.branch_reason
        assert s.length == 320.0 and s.cb == pytest.approx(0.8098)