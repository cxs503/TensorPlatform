"""Regression tests for the benchmark-reference dataset audit (v1.0 -> v2.0).

The dataset ``backend/data/benchmark_reference.json`` is the yardstick of
``POST /calculations/benchmark_validate`` (PASS iff error <= 15 %).  These tests
lock the internal-consistency contract of that yardstick, so the specific defects
found in v1.0 cannot silently come back:

* DTMB-5415 ``froude_number`` column was ~5.6x too small (Fr identity violated);
* SUBOFF ``experimental_rt_n`` contradicted Ct*0.5*rho*V^2*S by 87-90 %;
* ``cb`` contradicted ``displacement``/L*B*T for DTMB-5415 and KVLCC2.

They also lock the endpoint behaviour: rows that cannot serve as a yardstick are
excluded from the statistics in strict mode instead of producing false PASS/FAIL.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from api.domain.ship_benchmark import (  # noqa: E402
    SHIP_GEOMETRY,
    BenchmarkValidateRequest,
    benchmark_validate,
    froude_number,
    validate_geometry_preset,
)
from tools.benchmark_dataset_audit import (  # noqa: E402
    G,
    NU,
    RHO_SEA,
    audit_case,
    audit_dataset,
    ittc57_cf,
)

DATA_DIR = BACKEND_DIR / "data"
V1 = DATA_DIR / "benchmark_reference_v1.0.json"
V2 = DATA_DIR / "benchmark_reference.json"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _cases(path: Path):
    return json.loads(path.read_text())["cases"]


def _checks(report, check: str, severity: str = None):
    out = []
    for case in report["cases"]:
        for f in case["findings"]:
            if f["check"] == check and (severity is None or f["severity"] == severity):
                out.append((case["case_id"], f))
    return out


@pytest.fixture(scope="module")
def report_v1():
    return audit_dataset(V1)


@pytest.fixture(scope="module")
def report_v2():
    return audit_dataset(V2)


# ---------------------------------------------------------------------------
# 1. the audit really detects the v1.0 defects (otherwise the tool is useless)
# ---------------------------------------------------------------------------

class TestAuditDetectsV1Defects:
    def test_every_v1_row_has_an_error(self, report_v1):
        assert report_v1["summary"]["n_cases_with_errors"] == 12

    def test_froude_identity_violation_dtmb(self, report_v1):
        hits = _checks(report_v1, "froude_identity", "ERROR")
        assert {c for c, _ in hits} == {
            "DTMB5415-01", "DTMB5415-02", "DTMB5415-03", "DTMB5415-04"
        }
        # ratio of the filed to the derived value is ~5.6 for all four rows
        for _, f in hits:
            assert 80.0 < f["rel_error_pct"] < 85.0
            assert f["filed"] / f["expected"] == pytest.approx(0.061 / 0.3434, rel=0.02)

    def test_towing_force_contradiction_suboff(self, report_v1):
        hits = _checks(report_v1, "rt_closure", "ERROR")
        assert {c for c, _ in hits} == {"SUBOFF-01", "SUBOFF-02", "SUBOFF-03", "SUBOFF-04"}
        for _, f in hits:
            assert f["rel_error_pct"] > 80.0
            # filed value is far *below* the friction-consistent value
            assert f["filed"] < f["expected"]

    def test_cb_closure_contradiction(self, report_v1):
        hits = _checks(report_v1, "cb_closure", "ERROR")
        assert len(hits) == 8
        types = {c.split("-")[0] for c, _ in hits}
        assert types == {"DTMB5415", "KVLCC2"}

    def test_implied_density_is_physically_impossible(self, report_v1):
        """The contradiction is not a matter of a slightly different rho."""
        derived = {c["case_id"]: c["derived"] for c in report_v1["cases"]}
        for case_id in ("DTMB5415-01", "KVLCC2-01"):
            rho = derived[case_id]["cb_implied_density_kg_m3"]
            assert rho < 1000.0, f"{case_id}: implied rho {rho} is a valid water density"


# ---------------------------------------------------------------------------
# 2. v2.0 fixed the closable defects and left the rest honestly flagged
# ---------------------------------------------------------------------------

class TestDatasetV2Consistency:
    def test_no_froude_or_rt_errors_left(self, report_v2):
        assert _checks(report_v2, "froude_identity", "ERROR") == []
        assert _checks(report_v2, "rt_closure", "ERROR") == []

    def test_unresolved_cb_contradiction_still_declared(self, report_v2):
        hit_cases = {c for c, _ in _checks(report_v2, "cb_closure", "ERROR")}
        assert len(hit_cases) == 8  # cannot be resolved without an external source
        flagged = {c["case_id"] for c in _cases(V2) if c["reference_status"] == "inconsistent_unresolved"}
        assert hit_cases == flagged

    def test_status_counts(self, report_v2):
        assert report_v2["summary"]["status_counts"] == {
            "verified": 1, "out_of_validity_range": 3, "inconsistent_unresolved": 8
        }

    def test_froude_column_consistent_with_geometry(self):
        for case in _cases(V2):
            length = case["geometry"]["length_m"]
            fr = case["speed_ms"] / math.sqrt(G * length)
            assert case["froude_number"] == pytest.approx(fr, rel=0.001), case["case_id"]
            assert case["froude_number_source"].startswith("derived")

    def test_rt_closed_where_wetted_surface_known(self):
        for case in _cases(V2):
            if case["rt_n_source"].startswith("derived"):
                ct = case["experimental_ct"]
                v = case["speed_ms"]
                s = case["geometry"]["wetted_surface_m2"]
                assert case["rt_n"] == pytest.approx(ct * 0.5 * RHO_SEA * v ** 2 * s, rel=0.01)
                assert "rt_n_as_filed" in case  # provenance of the replaced value

    def test_ct_never_below_friction_floor(self):
        for case in _cases(V2):
            re = case["speed_ms"] * case["geometry"]["length_m"] / NU
            cf = ittc57_cf(re)
            assert case["experimental_ct"] > cf, case["case_id"]

    def test_migration_is_reproducible(self):
        """v2.0 must be a pure function of v1.0 - no hand-typed numbers."""
        from tools.migrate_benchmark_dataset_v2 import migrate

        regenerated = migrate(json.loads(V1.read_text()))
        assert regenerated == json.loads(V2.read_text())

    def test_v1_snapshot_kept_for_traceability(self):
        assert V1.exists()
        assert json.loads(V1.read_text())["version"] == "1.0"


# ---------------------------------------------------------------------------
# 3. geometry presets must be validated before a Cb-driven method eats them
# ---------------------------------------------------------------------------

class TestGeometryPresets:
    def test_kvlcc2_cb_inconsistent_with_displacement(self):
        report = validate_geometry_preset()
        entry = report["KVLCC2"]
        assert entry["applicable"] is True
        assert entry["validated"] is False
        assert entry["cb_filed"] == 0.8098
        assert entry["cb_from_geometry"] == pytest.approx(0.7758, abs=1e-3)
        assert entry["cb_consistency_pct"] > 3.0

    def test_dtmb_cb_inconsistent_with_displacement(self):
        entry = validate_geometry_preset()["DTMB-5415"]
        assert entry["validated"] is False
        assert entry["cb_from_geometry"] == pytest.approx(0.426, abs=1e-3)

    def test_suboff_reported_not_applicable(self):
        entry = validate_geometry_preset()["SUBOFF"]
        assert entry["applicable"] is False
        assert entry["validated"] is False
        assert "body of revolution" in entry["reason"]

    def test_preset_report_covers_all_hulls(self):
        assert set(validate_geometry_preset()) == set(SHIP_GEOMETRY)


# ---------------------------------------------------------------------------
# 4. endpoint: excluded rows must not decide PASS/FAIL
# ---------------------------------------------------------------------------

def _mock_api(ct: float):
    def _fake(ship_type, speed_ms, geometry):
        return {"ct": ct, "method": "mock"}
    return _fake


class TestEndpointStrictValidity:
    def test_lenient_mode_keeps_all_cases(self):
        with patch("api.domain.ship_benchmark._call_resistance_api", _mock_api(0.0038)):
            resp = benchmark_validate(BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all"))
        assert resp.summary["total_cases"] == 4
        assert resp.summary["strict_validity"] is False
        assert resp.summary["n_excluded"] == 0

    def test_strict_mode_excludes_out_of_range_rows(self):
        with patch("api.domain.ship_benchmark._call_resistance_api", _mock_api(0.0038)):
            resp = benchmark_validate(BenchmarkValidateRequest(
                ship_type="SUBOFF", benchmark_id="all", strict_validity=True))
        assert resp.summary["total_cases"] == 1
        assert [r.case_id for r in resp.results] == ["SUBOFF-01"]
        assert resp.summary["n_excluded"] == 3
        assert {e["reference_status"] for e in resp.summary["excluded_cases"]} == {"out_of_validity_range"}

    def test_strict_mode_reports_froude_from_geometry(self):
        with patch("api.domain.ship_benchmark._call_resistance_api", _mock_api(0.0038)):
            resp = benchmark_validate(BenchmarkValidateRequest(
                ship_type="SUBOFF", benchmark_id="all", strict_validity=True))
        assert resp.results[0].froude_number == pytest.approx(0.459, abs=1e-3)

    def test_strict_mode_all_excluded_gives_guidance(self):
        with patch("api.domain.ship_benchmark._call_resistance_api", _mock_api(0.0038)):
            resp = benchmark_validate(BenchmarkValidateRequest(
                ship_type="KVLCC2", benchmark_id="all", strict_validity=True))
        assert resp.summary["total_cases"] == 0
        assert resp.summary["n_excluded"] == 4
        assert "strict_validity" in resp.summary["recommendation"]

    def test_summary_carries_dataset_version_and_geometry_check(self):
        with patch("api.domain.ship_benchmark._call_resistance_api", _mock_api(0.0038)):
            resp = benchmark_validate(BenchmarkValidateRequest(
                ship_type="KVLCC2", benchmark_id="KVLCC2-01"))
        assert resp.summary["data_version"] == "2.0"
        assert resp.summary["geometry_validation"]["validated"] is False
        assert resp.results[0].reference_status == "inconsistent_unresolved"

    def test_froude_helper(self):
        assert froude_number(3.0, 4.356) == pytest.approx(0.4589, abs=1e-3)
        assert froude_number(0.0, 4.356) == 0.0


# ---------------------------------------------------------------------------
# 5. the auditor itself: sanity of the pure functions
# ---------------------------------------------------------------------------

class TestAuditPrimitives:
    def test_ittc57_tabulated_anchors(self):
        """ITTC-57 anchors: Cf(1e6)=4.687e-3, Cf(1e8)=2.083e-3 (guards the formula shape)."""
        assert ittc57_cf(1.0e6) == pytest.approx(4.6875e-3, rel=1e-3)
        assert ittc57_cf(1.0e8) == pytest.approx(2.0833e-3, rel=1e-3)

    def test_audit_case_flags_broken_row(self):
        broken = {
            "case_id": "T-1", "ship_type": "TEST", "speed_ms": 5.0, "froude_number": 0.1,
            "experimental_ct": 0.001, "experimental_rt_n": 1.0,
            "geometry": {"length_m": 10.0, "wetted_surface_m2": 20.0,
                         "beam_m": 2.0, "draft_m": 1.0, "displacement_t": 30.0, "cb": 0.9},
        }
        audit = audit_case(broken)
        checks = {f.check for f in audit.findings}
        assert {"froude_identity", "rt_closure", "cb_closure", "friction_floor"} <= checks
        assert audit.status == "inconsistent_unresolved"

    def test_audit_case_accepts_consistent_row(self):
        v = 2.0
        s = 10.0
        ct = 0.004
        good = {
            "case_id": "T-2", "ship_type": "TEST", "speed_ms": v,
            "froude_number": v / math.sqrt(G * 8.0), "experimental_ct": ct,
            "experimental_rt_n": ct * 0.5 * RHO_SEA * v ** 2 * s,
            "geometry": {"length_m": 8.0, "wetted_surface_m2": s,
                         "beam_m": 1.5, "draft_m": 0.6, "displacement_t": 7.0,
                         "cb": 7.0 * 1000 / (RHO_SEA * 8.0 * 1.5 * 0.6)},
        }
        audit = audit_case(good)
        assert audit.findings == [], [f.as_dict() for f in audit.findings]
        assert audit.status == "verified"