"""Verification tests for the resistance / stability calculators (mission m1384).

Run with:  python3 -m pytest tools/verif/test_ship_calcs_verification.py -q
(parked outside backend/tests because that tree's conftest imports the full
FastAPI app, which needs cryptography/psycopg2 -- unavailable in the sandbox.)

These tests turn the closed-form and self-consistency checks of
``tools/verif/verify_ship_calcs.py`` into regression guards:

* ITTC-1957 friction line == closed form (analytic).
* Wetted-surface override wins over the biased legacy estimator, and the size of
  that bias is pinned against closed-form shapes.
* GZ stays finite / bounded and is flagged outside the wall-sided validity
  range; the 90 deg tan() singularity is rejected.
* The benchmark reference dataset is audited for R = 0.5*rho*S*V^2*Ct
  self-consistency, so inconsistent "reference" rows cannot silently become
  pass criteria again.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO / "backend"
sys.path.insert(0, str(BACKEND_DIR))

RHO = 998.0
NU = 1.004e-6


def _load_calc_module():
    """Load backend/api/calculations.py without triggering api/__init__ (needs DB)."""
    spec = importlib.util.spec_from_file_location(
        "tp_calc_verif_test", BACKEND_DIR / "api" / "calculations.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tp_calc_verif_test"] = mod  # pydantic needs it for annotations
    spec.loader.exec_module(mod)
    return mod


calc = _load_calc_module()


def _run(coro):
    import asyncio

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# geometry: closed-form ground truth
# ---------------------------------------------------------------------------

class TestWettedSurface:
    def test_cylinder_bias_is_pinned(self):
        """Legacy estimator under-predicts an open cylinder by ~30 %."""
        L, D = 4.356, 0.508
        s_true = math.pi * D * L
        s_emp = calc._empirical_wetted_surface(L, D, math.pi / 4)
        err = s_emp / s_true - 1.0
        assert err == pytest.approx(-0.3036, abs=5e-4)
        assert abs(err) > 0.15  # documented, but far outside the 15 % bar

    def test_spheroid_bias_is_pinned(self):
        L, B = 4.356, 0.508
        a, b = L / 2.0, B / 2.0
        e = math.sqrt(1.0 - (b * b) / (a * a))
        s_true = 2 * math.pi * b * b + 2 * math.pi * a * b * math.asin(e) / e
        s_emp = calc._empirical_wetted_surface(L, B, math.pi / 6)
        assert s_emp / s_true - 1.0 == pytest.approx(-0.2017, abs=5e-4)

    def test_override_is_used_and_reported(self):
        req = calc.ResistanceRequest(speed_ms=3.0, wetted_surface_m2=5.42)
        resp = _run(calc.calc_resistance(req))
        assert resp.wetted_surface_m2 == pytest.approx(5.42)
        assert resp.wetted_surface_source == "input_validated"

    def test_default_path_is_backward_compatible(self):
        req = calc.ResistanceRequest(speed_ms=3.0)
        resp = _run(calc.calc_resistance(req))
        assert resp.wetted_surface_source == "empirical_formula"
        # value produced by the pre-m1384 implementation, kept stable
        assert resp.wetted_surface_m2 == pytest.approx(4.4180, abs=1e-3)
        assert resp.total_resistance_n == pytest.approx(68.6868, abs=1e-3)
        assert resp.form_resistance_n == 0.0


class TestIttc1957:
    @pytest.mark.parametrize("re_exp", [6, 7, 8, 9])
    def test_cf_matches_closed_form(self, re_exp):
        Re = 10.0 ** re_exp
        L = 100.0
        V = Re * NU / L
        req = calc.ResistanceRequest(
            speed_ms=V, length=L, beam=10.0, draft=5.0, cb=0.6,
            water_density=RHO, kinematic_viscosity=NU, wetted_surface_m2=1e5,
        )
        resp = _run(calc.calc_resistance(req))
        cf = 0.075 / (math.log10(Re) - 2.0) ** 2
        assert resp.friction_coefficient_cf == pytest.approx(cf, rel=1e-6)
        assert resp.reynolds_number == pytest.approx(Re, rel=1e-4)

    def test_form_factor_scales_only_the_viscous_part(self):
        base = _run(calc.calc_resistance(calc.ResistanceRequest(speed_ms=3.0)))
        k = _run(calc.calc_resistance(
            calc.ResistanceRequest(speed_ms=3.0, form_factor=1.15)))
        assert k.form_resistance_n == pytest.approx(0.15 * k.frictional_resistance_n, rel=1e-4)
        assert k.residual_resistance_n == pytest.approx(base.residual_resistance_n, rel=1e-6)
        assert k.total_resistance_n > base.total_resistance_n

    def test_cb_out_of_range_rejected(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _run(calc.calc_resistance(calc.ResistanceRequest(speed_ms=3.0, cb=0.2)))


# ---------------------------------------------------------------------------
# stability: wall-sided validity domain
# ---------------------------------------------------------------------------

class TestStabilityValidity:
    def test_wall_sided_closed_form_in_range(self):
        for phi_deg in (5, 10, 15, 20, 25):
            resp = _run(calc.calc_stability(calc.StabilityRequest(
                heel_angle_deg=phi_deg, gm_m=1.0, bm_m=3.0)))
            phi = math.radians(phi_deg)
            gz = math.sin(phi) * (1.0 + 0.5 * 3.0 * math.tan(phi) ** 2)
            assert resp.righting_arm_m == pytest.approx(gz, abs=1e-6)
            assert resp.formula_valid is True
            assert resp.gz_basis == "wall_sided"

    def test_90_deg_singularity_rejected(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _run(calc.calc_stability(calc.StabilityRequest(heel_angle_deg=90.0)))
        assert exc.value.status_code == 400

    def test_just_below_90_is_bounded(self):
        """89.999 deg is finite via the linear fallback, not a 1e16 m blow-up."""
        resp = _run(calc.calc_stability(calc.StabilityRequest(heel_angle_deg=89.999)))
        assert math.isfinite(resp.righting_arm_m)
        assert resp.righting_arm_m <= 1.0 + 1e-6
        assert resp.gz_basis == "linear_fallback"

    def test_beyond_limit_falls_back_and_is_flagged(self):
        resp = _run(calc.calc_stability(calc.StabilityRequest(
            heel_angle_deg=60.0, gm_m=1.0, bm_m=3.0)))
        assert resp.formula_valid is False
        assert resp.gz_basis == "linear_fallback"
        assert resp.righting_arm_m == pytest.approx(math.sin(math.radians(60)), abs=1e-6)

    def test_all_arms_finite_and_bounded(self):
        for phi in range(0, 90):
            resp = _run(calc.calc_stability(calc.StabilityRequest(
                heel_angle_deg=float(phi), gm_m=1.0, bm_m=3.0)))
            assert math.isfinite(resp.righting_arm_m)
            assert abs(resp.righting_arm_m) < 10.0, f"phi={phi}: {resp.righting_arm_m}"

    def test_negative_heel_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            calc.StabilityRequest(heel_angle_deg=-5.0)


# ---------------------------------------------------------------------------
# benchmark reference dataset audit
# ---------------------------------------------------------------------------

class TestBenchmarkDatasetConsistency:
    def test_reference_rows_satisfy_r_equals_ct(self):
        path = BACKEND_DIR / "data" / "benchmark_reference.json"
        data = json.loads(path.read_text())
        bad = []
        for case in data["cases"]:
            geom = case.get("geometry", {}) or {}
            S = geom.get("wetted_surface_m2")
            V = case.get("speed_ms")
            ct = case.get("experimental_ct")
            rt = case.get("experimental_rt_n")
            if not (S and V and ct and rt):
                continue
            implied = 0.5 * RHO * S * V * V * ct
            if abs(rt - implied) / implied > 0.05:
                bad.append((case["case_id"], rt, round(implied, 2)))
        # Known debt: the four SUBOFF rows are internally inconsistent (Rt is
        # ~7x lower than their own Ct implies) and must not be used as criteria.
        assert {b[0] for b in bad} == {"SUBOFF-01", "SUBOFF-02", "SUBOFF-03", "SUBOFF-04"}