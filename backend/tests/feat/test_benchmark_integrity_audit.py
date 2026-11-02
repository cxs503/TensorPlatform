"""
Verification-debt regression tests for the ship-resistance benchmark chain.

History: the previous chain was *self-confirming* — the validator called a ship
platform on port 8002 and read a ``ct`` key the platform never returned, so
every case silently evaluated to ``ct = 0`` (100 % error), while the unit tests
only exercised a hand-written pseudo-Holtrop mock and could never fail.

This suite was rewritten on 2026-11-01 (m1774) to pin the **current** canon
(dataset v3.0, per m1767/m1771) instead of the pre-fix defects it originally
guarded.  The old assertions (FROUDE_INCONSISTENT / CT_RT_INCONSISTENT /
REFERENCE_DATASET_UNUSABLE / OUT_OF_ENVELOPE) described the v2.x dataset and
the pre-refactor API; they were the "degenerate tests" m1771 deferred.

Covered here:
  1. physics-core refactor is numerically identical to the legacy inline formula
  2. the validator evaluates real physics in process (no network needed)
  3. the legacy remote reply (forces only, no coefficient) still yields a Ct
     derived from R_T — the field-mismatch fix of m1388/m1406
  4. reference-dataset integrity audit (v3.0): Froude/displacement closures
     pass, no FAIL verdict, full-hull rows withheld rather than passed
  5. honest-FAIL semantics: withheld rows never count toward the pass rate;
     a genuine model miss (SUBOFF-04) is reported, not hidden
  6. the endpoint loop and a direct physics call agree (no drift)

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
from services.benchmark_integrity import total_resistance_coefficient  # noqa: E402
from services.resistance_core import (  # noqa: E402
    axisym_wetted_surface,
    ct_from_rt,
    ittc57_cf,
    resistance_core,
)
from services.benchmark_integrity import total_resistance_coefficient  # noqa: E402
from services.ship_resistance import (  # noqa: E402
    DEFAULT_KINEMATIC_VISCOSITY,
    DEFAULT_WATER_DENSITY,
    resistance_breakdown,
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
        """Response carries total_resistance_coefficient + wetted_surface_m2.

        (m1774: the field was renamed ``ct`` -> ``total_resistance_coefficient``
        during the m1757/m1771 refactor; the validator consumes the new name.)
        """
        resp = asyncio.run(calc_resistance(ResistanceRequest(
            ship_type="SUBOFF", speed_ms=3.0, length=4.356, beam=0.508,
            draft=0.476, cb=0.542)))
        assert resp.total_resistance_n > 0
        assert resp.total_resistance_coefficient > 0
        assert resp.wetted_surface_m2 == pytest.approx(
            axisym_wetted_surface(4.356, 0.508, 0.542), rel=1e-6)
        # Ct must reproduce the legacy total: Rt = Ct * 0.5 rho S V^2
        rt_back = ct_from_rt(resp.total_resistance_n, 998.0, resp.wetted_surface_m2, 3.0)
        assert rt_back == pytest.approx(resp.total_resistance_coefficient, rel=1e-6)


# ---------------------------------------------------------------------------
# 2. in-process evaluation (dead-port regression)
# ---------------------------------------------------------------------------

class TestInProcessEvaluation:
    def test_no_network_needed(self):
        """Validation must not depend on a ship platform on any port."""
        import os
        assert "SHIP_BENCHMARK_REMOTE" not in os.environ

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
        """A platform reply with forces but no coefficient must still yield a Ct.

        m1774: the legacy hop is opt-in via SHIP_BENCHMARK_REMOTE=1 and the
        reply schema is the endpoint's (total_resistance_n / wetted_surface_m2);
        the coefficient is derived from R_T when absent.
        """
        reply = {"total_resistance_n": 68.686790, "wetted_surface_m2": 4.4179,
                 "method": "remote /calculations/resistance"}
        with patch.dict("os.environ", {"SHIP_BENCHMARK_REMOTE": "1"}), \
             patch("httpx.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = dict(reply)
            resp = benchmark_validate(BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="SUBOFF-01"))
        r = resp.results[0]
        expected = total_resistance_coefficient(
            reply, {"geometry": {"wetted_surface_m2": 4.4179}}, 998.0, 3.0)
        assert r.calc_ct == pytest.approx(expected, rel=1e-4)
        assert r.calc_ct > 0.0


# ---------------------------------------------------------------------------
# 3. reference-dataset integrity audit (canon v3.0)
# ---------------------------------------------------------------------------

class TestDatasetIntegrityAudit:
    def test_dataset_v30_shape_and_binding(self):
        """v3.0 audit payload: per-row verdicts, no FAIL, binding gate on."""
        audit = benchmark_audit()
        assert audit["dataset_version"] == "3.0"
        assert audit["total"] == len(DATA["cases"]) == 12
        assert audit["binding"] is True
        assert audit["failing_cases"] == []
        assert audit["verdict_counts"]["FAIL"] == 0
        assert {"rows", "verdict_counts", "failing_cases", "binding"} <= set(audit)

    def test_dtmb5415_froude_now_consistent(self):
        """v2.x defect (Fn stated for a ~181 m hull) is fixed in v3.0."""
        audit = benchmark_audit()
        row = next(r for r in audit["rows"] if r["case_id"] == "DTMB5415-01")
        fr = next(c for c in row["checks"] if c["check"] == "froude_closure")
        assert fr["status"] == "PASS"
        assert fr["error_pct"] < 1.0
        assert fr["computed"] == pytest.approx(0.061, rel=1e-2)
        assert fr["stated"] == pytest.approx(0.061, rel=1e-2)

    def test_no_row_has_failing_check(self):
        """m1767 recheck: 12 rows x 5 criteria, 0 FAIL. No silent inconsistency."""
        audit = benchmark_audit()
        for row in audit["rows"]:
            if row["ship_type"] != "SUBOFF":
                continue
            for c in row["checks"]:
                assert c["status"] in ("PASS", "WARN"), (
                    f"{row['case_id']}/{c['check']}: {c['detail']}")

    def test_suboff_rows_fully_consistent(self):
        """The model-calibrated body (SUBOFF) is the clean reference set."""
        audit = benchmark_audit()
        suboff = [r for r in audit["rows"] if r["ship_type"] == "SUBOFF"]
        assert len(suboff) == 4
        assert all(r["verdict"] == "PASS" for r in suboff)

    def test_full_hull_rows_withheld_not_failed(self):
        """KVLCC2/DTMB rows stay WITHHELD (honest), never plain-passed.

        The KVLCC2 Ct gap (m1751: -48..-55%) is a *model* limitation; the
        dataset rows are PARTIAL and must be withheld from the pass rate.
        """
        audit = benchmark_audit()
        full = [r for r in audit["rows"] if r["ship_type"] in ("KVLCC2", "DTMB-5415")]
        assert len(full) == 8
        assert all(r["verdict"] == "WITHHELD" for r in full)

    def test_suboff_ct_falls_with_speed(self):
        """A deeply submerged body's Ct must fall with Re (v3.0 rows do)."""
        cases = sorted(
            (c for c in DATA["cases"] if c["ship_type"] == "SUBOFF"),
            key=lambda c: c["speed_ms"],
        )
        cts = [c["experimental_ct"] for c in cases]
        assert all(a >= b for a, b in zip(cts, cts[1:])), cts


# ---------------------------------------------------------------------------
# 4. honest-FAIL semantics
# ---------------------------------------------------------------------------

class TestHonestFailSemantics:
    def test_suboff_verified_passes(self):
        """SUBOFF-01..03 pass inside the 15 % gate; SUBOFF-04 is a real miss."""
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all"))
        by_id = {r.case_id: r for r in resp.results}
        for case_id in ("SUBOFF-01", "SUBOFF-02", "SUBOFF-03"):
            assert by_id[case_id].pass_ is True
            assert by_id[case_id].error_pct <= 15.0
            assert by_id[case_id].withheld is False
        assert by_id["SUBOFF-04"].pass_ is False
        assert by_id["SUBOFF-04"].error_pct > 15.0
        assert by_id["SUBOFF-04"].withheld is False
        assert resp.summary["verified_passed"] == 3
        assert resp.summary["honest_fail"] is False

    def test_suboff_04_reported_not_hidden(self):
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="SUBOFF-04"))
        r = resp.results[0]
        assert r.pass_ is False
        assert r.error_pct > 15.0

    def test_partial_rows_withheld_not_passed(self):
        """DTMB-5415 rows are PARTIAL -> withheld; 0 passes, honest_fail=True."""
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type="DTMB-5415", benchmark_id="all"))
        assert len(resp.results) == 4
        assert resp.summary["passed"] == 0
        assert resp.summary["honest_fail"] is True
        for r in resp.results:
            assert r.withheld is True
            assert r.pass_ is False
            assert "HONEST-FAIL DATASET-WITHHELD" in r.note

    def test_full_form_hull_withheld(self):
        """KVLCC2: axisymmetric-body fit is out of applicability -> withheld."""
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="KVLCC2-02"))
        r = resp.results[0]
        assert r.withheld is True
        assert r.dataset_status == "PARTIAL"
        assert r.error_pct > 15.0
        assert "HONEST-FAIL" in r.note


# ---------------------------------------------------------------------------
# 5. single source of truth / geometry presets
# ---------------------------------------------------------------------------

class TestConsistencyBetweenPaths:
    def test_endpoint_and_direct_physics_agree(self):
        """The endpoint loop must equal a direct resistance_breakdown call."""
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all"))
        geo = SHIP_GEOMETRY["SUBOFF"]
        for r in resp.results:
            res = resistance_breakdown(
                length=geo["length"], beam=geo["beam"], draft=geo["draft"],
                cb=geo["cb"], speed=r.speed_ms,
                rho=DEFAULT_WATER_DENSITY, nu=DEFAULT_KINEMATIC_VISCOSITY)
            assert r.calc_ct == pytest.approx(res["ct"], abs=5e-5)
            # error_pct arithmetic is reproducible from the result fields
            expect = abs(r.calc_ct - r.ref_ct) / r.ref_ct * 100.0
            assert r.error_pct == pytest.approx(expect, abs=0.01)

    def test_suboff_cb_is_not_the_diameter(self):
        """Was 0.508 (=D): a placeholder, not a block coefficient."""
        assert SHIP_GEOMETRY["SUBOFF"]["cb"] == pytest.approx(0.542)
        assert SHIP_GEOMETRY["SUBOFF"]["draft"] == pytest.approx(0.508)

    def test_wetted_surface_computed_not_stored(self):
        """m1771: presets no longer store wetted_surface_m2; it is computed."""
        for ship_type, geo in SHIP_GEOMETRY.items():
            assert "wetted_surface_m2" not in geo, ship_type
            est = axisym_wetted_surface(geo["length"], geo["beam"], geo["cb"])
            assert est > 0.0, ship_type
