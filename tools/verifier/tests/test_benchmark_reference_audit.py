"""Regression tests for the benchmark reference dataset audit (tp-ver-8).

The resistance accuracy gate (``POST /calculations/benchmark_validate``) issues a
15%-error verdict against ``backend/data/benchmark_reference.json``.  These tests
pin the properties of that dataset that the verdict depends on:

* the dataset passes its own consistency audit with no *undeclared* failure
  (``tools/verifier/audit_benchmark_reference.py --strict``);
* every published total resistance satisfies ``Rt = 0.5*rho*V^2*S*Ct`` exactly,
  and where that identity cannot be evaluated the value is withheld (``null``)
  rather than fabricated;
* unusable cases are declared (``data_status != "ok"``) and the gate reports them
  as ``status="withheld"`` instead of scoring them;
* the audit actually has teeth - replaying the v1.0 values must raise FAIL.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.verifier.audit_benchmark_reference import (  # noqa: E402
    _load_presets,
    audit_case,
    audit_dataset,
)

DATASET_PATH = REPO_ROOT / "backend/data/benchmark_reference.json"
PRESETS_MODULE = REPO_ROOT / "backend/api/domain/ship_benchmark.py"


@pytest.fixture(scope="module")
def dataset():
    return json.loads(DATASET_PATH.read_text())


@pytest.fixture(scope="module")
def report(dataset):
    presets = _load_presets(PRESETS_MODULE)
    assert presets, "could not parse SHIP_GEOMETRY from ship_benchmark.py"
    return audit_dataset(dataset, presets)


class TestDatasetAudit:
    def test_no_undeclared_failures(self, report):
        assert report["undeclared_fail_cases"] == 0, [
            (c["case_id"], [k for k in c["checks"] if k["status"] == "FAIL"])
            for c in report["cases"] if c["verdict"] == "FAIL"
        ]

    def test_presets_sane(self, report):
        assert report["preset_fail_count"] == 0, [p for p in report["presets"] if p["status"] == "FAIL"]

    def test_strict_pass(self, report):
        assert report["strict_pass"] is True

    def test_verdict_split(self, report):
        counts = report["case_verdict_counts"]
        assert counts.get("FAIL", 0) == 0
        # the four DTMB-5415 cases must be declared withheld, not scored
        withheld = [c["case_id"] for c in report["cases"] if c["verdict"] == "WITHHELD"]
        assert len(withheld) == 4, withheld
        assert all(cid.startswith("DTMB5415") for cid in withheld)
        assert counts.get("PASS", 0) == report["n_cases"] - len(withheld)


class TestReferenceValues:
    def test_density_declared(self, dataset):
        for case in dataset["cases"]:
            assert case.get("reference_density_kg_m3") in (1000.0, 1025.0), case["case_id"]

    def test_total_resistance_identity(self, dataset):
        """Rt must equal 0.5*rho*V^2*S*Ct to within 0.5% wherever it is published."""
        checked = 0
        for case in dataset["cases"]:
            rt = case.get("experimental_rt_n")
            if rt is None:
                continue
            geo = case["geometry"]
            rho = case["reference_density_kg_m3"]
            expected = 0.5 * rho * case["speed_ms"] ** 2 * geo["wetted_surface_m2"] * case["experimental_ct"]
            assert abs(rt - expected) / expected < 0.005, (case["case_id"], rt, expected)
            checked += 1
        assert checked >= 4, "expected at least the SUBOFF cases to carry a derived Rt"

    def test_rt_never_fabricated(self, dataset):
        """No Rt without a reconstructable wetted surface."""
        for case in dataset["cases"]:
            has_s = "wetted_surface_m2" in case["geometry"]
            if not has_s:
                assert case.get("experimental_rt_n") is None, case["case_id"]
                assert case.get("rt_status") == "unavailable_no_wetted_surface", case["case_id"]
            else:
                assert case.get("rt_status") == "derived_from_ct", case["case_id"]

    def test_froude_number_consistent(self, dataset):
        for case in dataset["cases"]:
            L = case["geometry"]["length_m"]
            fn = case["speed_ms"] / math.sqrt(9.81 * L)
            assert abs(case["froude_number"] - fn) / fn < 0.005, case["case_id"]

    def test_withheld_cases_declare_reason(self, dataset):
        for case in dataset["cases"]:
            if case.get("data_status", "ok") != "ok":
                assert case.get("data_status_reason"), case["case_id"]


class TestAuditHasTeeth:
    """Replaying the v1.0 numbers must be flagged - otherwise the audit is a no-op."""

    LEGACY_CASE = {
        "case_id": "DTMB5415-01",
        "ship_type": "DTMB-5415",
        "speed_kn": 5.0,
        "speed_ms": 2.572,
        "froude_number": 0.061,
        "experimental_ct": 0.0045,
        "experimental_rt_n": 8.2,
        "geometry": {
            "length_m": 5.72,
            "beam_m": 0.759,
            "draft_m": 0.246,
            "displacement_kg": 466.3,
            "cb": 0.508,
        },
    }

    def test_legacy_case_fails(self):
        result = audit_case(self.LEGACY_CASE)
        assert result["verdict"] == "FAIL"
        failing = {c["check"] for c in result["checks"] if c["status"] == "FAIL"}
        assert "froude_consistency" in failing
        assert "implied_wetted_area" in failing

    def test_legacy_ct_rt_mismatch_is_quantified(self):
        case = dict(self.LEGACY_CASE)
        s = 5.42  # give it a wetted surface so the identity is directly checkable
        case["geometry"] = dict(case["geometry"], wetted_surface_m2=s)
        result = audit_case(case)
        check = next(c for c in result["checks"] if c["check"] == "ct_rt_consistency")
        assert check["status"] == "FAIL"
        expected = 0.5 * 1025.0 * 2.572 ** 2 * s * 0.0045
        assert check["rel_err"] > 0.5, (check["rel_err"], expected)


class TestGateWithholds:
    """The endpoint must not issue a verdict for declared-withheld cases."""

    @pytest.fixture(scope="class")
    @classmethod
    def module(cls):
        # ship_benchmark.py uses only fastapi/pydantic and no ``api.*`` imports,
        # so load it by path: importing it as a package would drag in the whole
        # backend app (cryptography, DB drivers, ...) which unit tests must not
        # require.
        if not PRESETS_MODULE.exists():
            pytest.skip("ship_benchmark module not available")
        name = "ship_benchmark_under_test"
        spec = importlib.util.spec_from_file_location(name, PRESETS_MODULE)
        mod = importlib.util.module_from_spec(spec)
        # pydantic resolves the postponed annotations of this module by name,
        # so it has to be registered before execution
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_withheld_cases_are_not_scored(self, module, monkeypatch):
        monkeypatch.setattr(
            module, "_call_resistance_api",
            lambda ship_type, speed_ms, geometry: {"ct": 0.0035, "method": "test-double"},
        )
        req = module.BenchmarkValidateRequest(ship_type="DTMB-5415", benchmark_id="all")
        resp = module.benchmark_validate(req)
        assert resp.summary["scored_cases"] == 0
        assert resp.summary["withheld_cases"] == 4
        assert resp.summary["pass_rate"] == 0.0
        assert all(r.status == "withheld" for r in resp.results)
        assert all(r.note.startswith("WITHHELD:") for r in resp.results)

    def test_scored_cases_still_scored(self, module, monkeypatch):
        monkeypatch.setattr(
            module, "_call_resistance_api",
            lambda ship_type, speed_ms, geometry: {"ct": 0.0038, "method": "test-double"},
        )
        req = module.BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all")
        resp = module.benchmark_validate(req)
        assert resp.summary["scored_cases"] == 4
        assert resp.summary["withheld_cases"] == 0
        assert all(r.status == "scored" for r in resp.results)

    def test_suboff_preset_not_degenerate(self, module):
        preset = module.SHIP_GEOMETRY["SUBOFF"]
        assert preset["displacement"] != preset["beam"]
        assert preset["displacement"] != preset["cb"]
        assert preset["displacement_unit"] == "kg"
        assert 0.3 < preset["cb"] < 0.9