"""
Mission m1388 — benchmark validation chain: live (mock-free) verification tests.

These tests reproduce the two defects found during the audit and pin the fixes:

1. The chain must produce *physical* numbers without any HTTP hop
   (previously: ``calc_ct = 0.0`` / ``error_pct = 999`` for every case because
   the remote call used a wrong schema and the endpoint never returned ``ct``).
2. The reference dataset must be gated: rows failing the Ct-Rt identity, the
   Froude identity or the friction floor are ``withheld`` and cannot yield a
   ``pass`` even when the numeric error happens to be small.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from api.calculations import ResistanceRequest, calc_resistance
from api.domain.ship_benchmark import (
    BenchmarkValidateRequest,
    _call_resistance_api,
    benchmark_validate,
    list_benchmark_cases,
    audit_benchmark_cases,
)
from services.benchmark_dataset_audit import (
    CONSISTENT,
    INCONSISTENT,
    PARTIAL,
    assess_case,
    load_and_assess,
)
from services.ship_resistance import (
    ct_from_rt,
    froude_number,
    ittc57_cf,
    resistance_breakdown,
    schoenherr_cf,
)


# ---------------------------------------------------------------------------
# Physics core
# ---------------------------------------------------------------------------

class TestResistanceCore:
    def test_endpoint_matches_core(self):
        """Endpoint output must equal the pure core (single source of truth)."""
        req = ResistanceRequest()  # SUBOFF defaults
        resp = asyncio.run(calc_resistance(req))
        core = resistance_breakdown(
            length=req.length, beam=req.beam, draft=req.draft, cb=req.cb,
            speed=req.speed_ms, rho=req.water_density, nu=req.kinematic_viscosity,
        )
        # endpoint rounds to 4 decimals (N) / 8 decimals (Ct)
        assert resp.total_resistance_n == pytest.approx(core["rt_n"], abs=1e-3)
        assert resp.frictional_resistance_n == pytest.approx(core["rf_n"], abs=1e-3)
        assert resp.total_resistance_coefficient == pytest.approx(core["ct"], abs=1e-8)

    def test_friction_line_cross_check_schoenherr(self):
        """ITTC-1957 vs Schoenherr: two independent correlations.

        They agree within 3% for Re >= 1e7 (the range relevant to the benchmark
        hulls) and are *documented* to diverge more at Re = 1e6, which is why the
        divergence is asserted as a bounded fact rather than forced to pass.
        """
        for re in (1e7, 1e8, 1e9):
            cf57, cfsch = ittc57_cf(re), schoenherr_cf(re)
            assert abs(cf57 - cfsch) / cfsch < 0.03, (re, cf57, cfsch)
        cf57, cfsch = ittc57_cf(1e6), schoenherr_cf(1e6)
        div = abs(cf57 - cfsch) / cfsch
        assert 0.03 < div < 0.12, (cf57, cfsch, div)

    def test_ct_rt_identity_roundtrip(self):
        res = resistance_breakdown(4.356, 0.508, 0.508, 0.542, 3.0)
        assert ct_from_rt(res["rt_n"], 998.0, res["wetted_surface_m2"], 3.0) == pytest.approx(res["ct"], rel=1e-12)

    def test_residual_zero_below_froude_floor(self):
        res = resistance_breakdown(320.0, 58.0, 20.8, 0.8098, 0.2)
        assert res["cr"] == 0.0

    def test_froude_number_definition(self):
        assert froude_number(3.0, 4.356) == pytest.approx(3.0 / (9.81 * 4.356) ** 0.5, rel=1e-12)


# ---------------------------------------------------------------------------
# Defect 1: chain must work without the HTTP hop
# ---------------------------------------------------------------------------

class TestChainLive:
    def test_in_process_resistance_returns_numbers(self):
        from api.domain.ship_benchmark import SHIP_GEOMETRY
        out = _call_resistance_api("KVLCC2", 7.717, SHIP_GEOMETRY["KVLCC2"])
        assert out is not None
        assert out["transport"] == "in_process"
        assert 0.0 < out["ct"] < 0.05
        assert out["rt_n"] > 0.0

    def test_benchmark_validate_no_mocks(self):
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="all"))
        assert len(resp.results) == 4
        for r in resp.results:
            assert r.calc_ct > 0.0, f"{r.case_id}: chain produced calc_ct=0 (transport defect回归)"
            assert r.error_pct < 999.0
            assert r.calc_rt_n and r.calc_rt_n > 0.0
        assert resp.summary["transport"] == "in_process"

    def test_no_bogus_cfd_recommendation_without_verified_rows(self):
        """KVLCC2 rows are PARTIAL -> withheld, so no accuracy verdict may fire."""
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="all"))
        assert resp.summary["verified_cases"] == 0
        assert resp.summary["pass_rate_verified"] is None
        assert resp.summary["algorithm_upgrade_recommended"] is False
        assert "NO VERIFIED REFERENCE ROWS" in resp.summary["recommendation"]


# ---------------------------------------------------------------------------
# Defect 2: dataset gate
# ---------------------------------------------------------------------------

class TestDatasetAudit:
    @pytest.fixture(scope="class")
    def audit(self):
        return audit_benchmark_cases()

    def test_suboff_ct_rt_identity_fails(self, audit):
        rows = {r["case_id"]: r for r in audit["cases"]}
        for i in range(1, 5):
            row = rows[f"SUBOFF-0{i}"]
            assert row["status"] == INCONSISTENT
            assert "I1_ct_rt_identity" in row["failed_checks"]

    def test_dtmb_froude_identity_fails(self, audit):
        rows = {r["case_id"]: r for r in audit["cases"]}
        for i in range(1, 5):
            row = rows[f"DTMB5415-0{i}"]
            assert row["status"] == INCONSISTENT
            assert "I2_froude_identity" in row["failed_checks"]

    def test_kvlcc2_partial_because_no_wetted_area(self, audit):
        rows = {r["case_id"]: r for r in audit["cases"]}
        for i in range(1, 5):
            row = rows[f"KVLCC2-0{i}"]
            assert row["status"] == PARTIAL
            assert "I1_ct_rt_identity" in row["checks_not_run"]

    def test_dataset_has_no_verified_rows(self, audit):
        assert audit["total_cases"] == 12
        assert audit["verified_cases"] == 0
        assert audit["withheld_cases"] == 12
        assert audit["verifiable_accuracy_claim"] is False

    def test_friction_floor_rejects_sub_friction_ct(self):
        bad = {
            "case_id": "SYNTH-01", "ship_type": "KVLCC2", "speed_ms": 7.717,
            "froude_number": froude_number(7.717, 320.0),
            "experimental_ct": 0.0005, "geometry": {"length_m": 320.0},
        }
        row = assess_case(bad)
        assert "I3_friction_floor" in row["failed_checks"]
        assert row["status"] == INCONSISTENT

    def test_synthetic_consistent_row_passes(self):
        """A row built from the identity itself must be graded CONSISTENT."""
        res = resistance_breakdown(4.356, 0.508, 0.508, 0.542, 3.0)
        good = {
            "case_id": "SYNTH-OK", "ship_type": "SUBOFF", "speed_ms": 3.0,
            "froude_number": froude_number(3.0, 4.356),
            "experimental_ct": res["ct"], "experimental_rt_n": res["rt_n"],
            "geometry": {"length_m": 4.356, "wetted_surface_m2": res["wetted_surface_m2"]},
        }
        row = assess_case(good)
        assert row["status"] == CONSISTENT
        assert row["withheld"] is False


# ---------------------------------------------------------------------------
# Gate semantics: small numeric error must not rescue an unusable reference row
# ---------------------------------------------------------------------------

class TestWithheldGate:
    def test_perfect_error_still_withheld_on_bad_row(self):
        from api.domain.ship_benchmark import _load_benchmark_data, SHIP_GEOMETRY
        cases = _load_benchmark_data()["cases"]
        case = next(c for c in cases if c["case_id"] == "SUBOFF-01")
        geo = SHIP_GEOMETRY["SUBOFF"]
        # Pretend the model reproduces the reference coefficient exactly.
        fake = {"ct": case["experimental_ct"], "rt_n": 1.0,
                "wetted_surface_m2": 5.42, "method": "fake", "transport": "fake"}
        with patch("api.domain.ship_benchmark._call_resistance_api", return_value=fake):
            resp = benchmark_validate(BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="SUBOFF-01"))
        r = resp.results[0]
        assert r.error_pct == 0.0
        assert r.withheld is True
        assert r.pass_ is False
        assert "DATASET-WITHHELD" in r.note
        assert resp.summary["verified_cases"] == 0


# ---------------------------------------------------------------------------
# Listing endpoint exposes the audit status
# ---------------------------------------------------------------------------

def test_case_listing_reports_dataset_status():
    out = list_benchmark_cases(ship_type="SUBOFF")
    assert out["total"] == 4
    assert set(out["dataset_status"].values()) == {"INCONSISTENT"}