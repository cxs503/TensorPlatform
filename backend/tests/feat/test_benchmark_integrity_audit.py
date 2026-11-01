"""
Verification-debt regression tests for the ship-resistance benchmark chain.

These tests exist because the previous chain was *self-confirming*: the validator
called a ship platform on port 8002 and read a ``ct`` key that the platform never
returns, so every case silently evaluated to ``ct = 0`` (100 % error), while the
unit tests only exercised a hand-written pseudo-Holtrop mock
(``base_ct = 0.0025 + 0.003 * Fn``) and therefore could never fail.

Covered here:
  1. physics-core refactor is numerically identical to the legacy inline formula
  2. the validator evaluates real physics in process (no port 8002 needed)
  3. the dimensional-only remote reply is converted to Ct (field-mismatch fix)
  4. reference-dataset integrity audit: Froude inconsistency, Ct-vs-Rt
     inconsistency, validity-envelope flags
  5. honest-FAIL semantics: no case is counted as a verified pass outside the
     model's calibration envelope
  6. the two evaluation paths (endpoint loop and services.benchmark_audit) agree

Run with:
    cd backend && DATABASE_URL="sqlite:////tmp/tp_test.db" \
        python3 -m pytest --noconftest tests/feat/test_benchmark_integrity_audit.py -q
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from api.domain.ship_benchmark import (  # noqa: E402
    SHIP_GEOMETRY,
    BenchmarkValidateRequest,
    benchmark_audit,
    benchmark_validate,
)
from api.calculations import ResistanceRequest, calc_resistance  # noqa: E402
from services import benchmark_audit as audit_mod  # noqa: E402
from services.resistance_core import (  # noqa: E402
    axisym_wetted_surface,
    ct_from_rt,
    ittc57_cf,
    resistance_core,
)

DATA = json.loads((BACKEND_DIR / "data" / "benchmark_reference.json").read_text())


def _legacy_resistance(V, L, B, T, Cb, rho=998.0, nu=1.004e-6):
    """Inline copy of the pre-refactor formulation (golden reference)."""
    S = math.pi * B * L * (0.5 + 0.25 * Cb)
    Re = V * L / nu
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
    Rf = 0.5 * rho * S * Cf * V ** 2
    Fn = V / math.sqrt(9.81 * L)
    Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = 0.5 * rho * S * Cr * V ** 2
    return S, Rf, Rr, Rf + Rr


# ---------------------------------------------------------------------------
# 1. refactor regression
# ---------------------------------------------------------------------------

class TestPhysicsCoreRefactor:
    CASES = [
        (1.0, 4.356, 0.508, 0.476, 0.542),
        (3.0, 4.356, 0.508, 0.476, 0.542),
        (5.0, 4.356, 0.508, 0.508, 0.508),
        (7.202, 320.0, 58.0, 20.8, 0.8098),
        (2.572, 5.720, 0.759, 0.246, 0.508),
    ]

    def test_matches_legacy_formula(self):
        for V, L, B, T, Cb in self.CASES:
            S, Rf, Rr, Rt = _legacy_resistance(V, L, B, T, Cb)
            res = resistance_core(V, L, B, T, Cb)
            assert res["wetted_surface_m2"] == pytest.approx(S, rel=1e-12)
            assert res["frictional_resistance_n"] == pytest.approx(Rf, rel=1e-12)
            assert res["residual_resistance_n"] == pytest.approx(Rr, rel=1e-10)
            assert res["total_resistance_n"] == pytest.approx(Rt, rel=1e-10)

    def test_ct_is_independent_of_wetted_surface(self):
        """Ct = Cf + Cr: the area cancels. Documented separability of error sources."""
        a = resistance_core(3.0, 4.356, 0.508, 0.476, 0.542)
        b = resistance_core(3.0, 4.356, 0.508, 0.476, 0.542, wetted_surface=5.42)
        assert a["ct"] == pytest.approx(b["ct"], rel=1e-12)
        # ... while the dimensional force scales with the area
        assert b["total_resistance_n"] > a["total_resistance_n"]

    def test_explicit_wetted_surface_overrides_estimate(self):
        res = resistance_core(3.0, 4.356, 0.508, 0.476, 0.542, wetted_surface=5.42)
        assert res["wetted_surface_m2"] == 5.42
        assert res["ct"] == pytest.approx(res["cf"] + res["cr"], rel=1e-12)

    def test_friction_line_limits(self):
        assert ittc57_cf(1e9) < ittc57_cf(1e7) < ittc57_cf(1e6)
        assert ittc57_cf(1.0e7) == pytest.approx(0.075 / (7.0 - 2.0) ** 2, rel=1e-12)

    def test_invalid_inputs_rejected(self):
        with pytest.raises(ValueError):
            resistance_core(3.0, 4.356, 0.508, 0.476, 0.1)   # Cb out of range
        with pytest.raises(ValueError):
            resistance_core(0.0, 4.356, 0.508, 0.476, 0.542)   # zero speed

    def test_api_endpoint_exposes_ct_and_surface(self):
        """Response now carries ct and wetted_surface_m2 (needed by the validator)."""
        resp = asyncio.run(calc_resistance(ResistanceRequest(
            ship_type="SUBOFF", speed_ms=3.0, length=4.356, beam=0.508,
            draft=0.476, cb=0.542)))
        assert resp.total_resistance_n > 0
        assert resp.ct > 0
        assert resp.wetted_surface_m2 == pytest.approx(axisym_wetted_surface(4.356, 0.508, 0.542), rel=1e-6)
        # Ct must reproduce the legacy total: Rt = Ct * 0.5 rho S V^2
        rt_back = ct_from_rt(resp.total_resistance_n, 998.0, resp.wetted_surface_m2, 3.0)
        assert rt_back == pytest.approx(resp.ct, rel=1e-6)


# ---------------------------------------------------------------------------
# 2. in-process evaluation (dead-port regression)
# ---------------------------------------------------------------------------

class TestInProcessEvaluation:
    def test_no_network_needed(self):
        """Validation must not depend on a ship platform on port 8002."""
        import os
        assert "SHIP_PLATFORM_URL" not in os.environ

        def _boom(*a, **k):  # any HTTP attempt fails the test
            raise AssertionError("benchmark validation attempted a network call")

        with patch("httpx.post", side_effect=_boom):
            resp = benchmark_validate(BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="SUBOFF-01"))
        assert resp.results[0].calc_ct > 0

    def test_calc_ct_never_silently_zero(self):
        """Regression: the old code yielded ct=0.0 -> 100% error on every case."""
        for ship_type, case_id in [("SUBOFF", "SUBOFF-01"), ("KVLCC2", "KVLCC2-01")]:
            resp = benchmark_validate(BenchmarkValidateRequest(ship_type=ship_type, benchmark_id=case_id))
            r = resp.results[0]
            assert r.calc_ct > 0.0, f"{case_id}: calc_ct collapsed to zero"
            assert r.error_pct < 999.0

    def test_remote_dimension_only_reply_derives_ct(self):
        """A platform reply with forces but no 'ct' key must still produce a Ct."""
        reply = {"total_resistance_n": 68.686790, "wetted_surface_m2": 4.4179,
                 "method": "remote /calculations/resistance"}
        with patch.dict("os.environ", {"SHIP_PLATFORM_URL": "http://localhost:8002"}), \
             patch("httpx.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = dict(reply)
            resp = benchmark_validate(BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="SUBOFF-01"))
        r = resp.results[0]
        assert r.calc_ct == pytest.approx(ct_from_rt(68.686790, 998.0, 4.4179, 3.0), rel=1e-4)
        assert "derived" in r.method


# ---------------------------------------------------------------------------
# 3. reference-dataset integrity audit
# ---------------------------------------------------------------------------

class TestDatasetIntegrityAudit:
    def test_froude_inconsistency_detected_for_dtmb5415(self):
        """DTMB5415 rows state Fn computed for a ~181 m length, not the 5.72 m model."""
        rows = {r["case_id"]: r for r in audit_mod.audit_dataset(DATA, SHIP_GEOMETRY)["rows"]}
        row = rows["DTMB5415-01"]
        assert row["froude_error_pct"] > 400.0
        assert row["status"] == "INCONSISTENT"
        assert any("FROUDE_INCONSISTENT" in f for f in row["integrity_flags"])
        assert row["froude_number_recomputed"] == pytest.approx(2.572 / math.sqrt(9.81 * 5.72), rel=1e-3)

    def test_ct_rt_inconsistency_detected(self):
        """Every row's dimensional resistance disagrees with its own coefficient."""
        audit = audit_mod.audit_dataset(DATA, SHIP_GEOMETRY)
        for row in audit["rows"]:
            assert row["ct_rt_error_pct"] is not None
            assert row["ct_rt_error_pct"] > 15.0, f"{row['case_id']} unexpectedly consistent"
            assert any("CT_RT_INCONSISTENT" in f for f in row["integrity_flags"])

    def test_axisymmetric_surface_invalid_for_displacement_hulls(self):
        """The SUBOFF-calibrated area formula over-predicts merchant-hull wetted area."""
        for ship_type, published in audit_mod.LITERATURE_WETTED_SURFACE.items():
            geo = SHIP_GEOMETRY[ship_type]
            est = axisym_wetted_surface(geo["length"], geo["beam"], geo["cb"])
            if ship_type == "SUBOFF":
                continue
            over = (est - published["value_m2"]) / published["value_m2"]
            assert over > 0.15, f"{ship_type}: expected >15% over-prediction, got {over:.0%}"

    def test_dataset_verdict_is_unusable(self):
        """No reference row may be reported as plain OK (honest-FAIL, not hidden)."""
        audit = audit_mod.audit_dataset(DATA, SHIP_GEOMETRY)
        assert audit["verdict"] == "REFERENCE_DATASET_UNUSABLE"
        assert audit["status_counts"].get("OK", 0) == 0

    def test_suboff_ct_trend_flagged(self):
        """A deeply submerged body's Ct must fall with Re; the dataset rises 16%."""
        audit = audit_mod.audit_dataset(DATA, SHIP_GEOMETRY)
        assert audit["trend"]["SUBOFF"]["monotonic_increasing"] is True
        assert audit["trend"]["SUBOFF"]["physically_suspect"] is True

    def test_audit_endpoint_shape(self):
        payload = benchmark_audit()
        assert payload["case_count"] == len(DATA["cases"])
        assert {"rows", "status_counts", "trend", "verdict"} <= set(payload)


# ---------------------------------------------------------------------------
# 4. honest-FAIL semantics
# ---------------------------------------------------------------------------

class TestHonestFailSemantics:
    def test_no_verified_pass_outside_envelope(self):
        for ship_type in ("SUBOFF", "KVLCC2"):
            resp = benchmark_validate(BenchmarkValidateRequest(ship_type=ship_type, benchmark_id="all"))
            assert resp.summary["verified_passes_in_envelope"] == 0
            assert resp.summary["algorithm_upgrade_recommended"] is True
            for r in resp.results:
                assert r.envelope_flags, f"{r.case_id}: expected an envelope flag"

    def test_previously_passing_suboff_cases_flagged(self):
        """SUBOFF-01..03 land under 15% but stay unverified (out of envelope)."""
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all"))
        by_id = {r.case_id: r for r in resp.results}
        for case_id in ("SUBOFF-01", "SUBOFF-02", "SUBOFF-03"):
            assert by_id[case_id].error_pct < 15.0
            assert "OUT_OF_ENVELOPE" in by_id[case_id].note

    def test_suboff_04_honest_fail(self):
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="SUBOFF-04"))
        r = resp.results[0]
        assert r.pass_ is False
        assert "HONEST-FAIL" in r.note

    def test_inconsistent_rows_excluded_not_passed(self):
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type="DTMB-5415", benchmark_id="all"))
        assert resp.results == []
        assert len(resp.summary["excluded_cases"]) == 4
        assert all("FROUDE_INCONSISTENT" in e["reason"] for e in resp.summary["excluded_detail"])
        assert resp.summary["passed"] == 0

    def test_full_form_hull_has_envelope_flag(self):
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="KVLCC2-02"))
        r = resp.results[0]
        assert any("Cb=" in f and "form factor" in f for f in r.envelope_flags)
        assert r.error_pct > 15.0

    def test_reference_warning_note_for_ct_rt_mismatch(self):
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="KVLCC2-01"))
        assert "REFERENCE_WARNING" in resp.results[0].note


# ---------------------------------------------------------------------------
# 5. single source of truth / geometry presets
# ---------------------------------------------------------------------------

class TestConsistencyBetweenPaths:
    def test_endpoint_and_offline_validator_agree(self):
        """The endpoint loop and services.benchmark_audit must not drift."""
        offline = audit_mod.validate_cases(DATA, "SUBOFF", SHIP_GEOMETRY)
        online = benchmark_validate(BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all"))
        offline_by_id = {r["case_id"]: r for r in offline["results"]}
        for r in online.results:
            assert r.calc_ct == pytest.approx(offline_by_id[r.case_id]["calc_ct"], rel=1e-6)
            assert r.error_pct == pytest.approx(offline_by_id[r.case_id]["error_pct"], abs=0.01)

    def test_suboff_cb_is_not_the_diameter(self):
        """Was 0.508 (=D): a placeholder, not a block coefficient."""
        assert SHIP_GEOMETRY["SUBOFF"]["cb"] == pytest.approx(0.542)
        geo = SHIP_GEOMETRY["SUBOFF"]
        assert geo["wetted_surface_m2"] == pytest.approx(5.42)

    def test_all_presets_carry_verified_wetted_surface(self):
        for ship_type, geo in SHIP_GEOMETRY.items():
            assert geo.get("wetted_surface_m2", 0) > 0, ship_type
            assert ship_type in audit_mod.LITERATURE_WETTED_SURFACE