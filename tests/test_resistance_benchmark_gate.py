"""Regression tests for the resistance benchmark gate and the reference dataset.

These tests exist because the platform's accuracy gate was silently dead:

  * ``benchmark_validate`` POSTed to the resistance endpoint with ``{"speed": ...}``
    while the model expects ``speed_ms``, so every case was computed at the 1.0 m/s
    default;
  * it then read ``response["ct"]`` — a key the endpoint never returned — so
    ``calc_ct`` was always 0.0 and every case "failed" with 100 % error.

Both defects are contract drift between two endpoints, invisible to unit tests of
either side.  They are now prevented structurally (one shared kernel, no payload keys)
and locked down here: every assertion below fails on the pre-fix code.

Run standalone (no database / FastAPI app required):

    python3 -m pytest tests/test_resistance_benchmark_gate.py -q
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
DATASET_PATH = BACKEND / "data" / "benchmark_reference.json"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.ship_resistance_core import (  # noqa: E402
    GRAVITY,
    check_geometry,
    compute_resistance_core,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_gate_module():
    """Import api.domain.ship_benchmark without executing api/__init__ (DB stack)."""
    for name, sub in (("api", "api"), ("api.domain", "api/domain")):
        if name not in sys.modules:
            pkg = types.ModuleType(name)
            pkg.__path__ = [str(BACKEND / sub)]
            sys.modules[name] = pkg
    path = BACKEND / "api" / "domain" / "ship_benchmark.py"
    spec = importlib.util.spec_from_file_location("api.domain.ship_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["api.domain.ship_benchmark"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate():
    return load_gate_module()


@pytest.fixture(scope="module")
def dataset():
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def audit_module():
    path = REPO_ROOT / "tools" / "audit_benchmark_reference.py"
    spec = importlib.util.spec_from_file_location("audit_benchmark_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. Kernel: definitional identities
# ---------------------------------------------------------------------------
class TestKernelIdentities:
    def test_total_coefficient_identity(self):
        """Ct must equal Rt / (0.5 rho S V^2) exactly — this is a definition."""
        out = compute_resistance_core(4.356, 0.508, 0.508, 0.508, 3.0)
        expected = out["total_resistance_n"] / (
            0.5 * 998.0 * out["wetted_surface_m2"] * 3.0 ** 2)
        assert out["total_resistance_coeff"] == pytest.approx(expected, rel=1e-12)

    def test_totals_decompose(self):
        out = compute_resistance_core(5.72, 0.759, 0.246, 0.508, 2.572)
        assert out["total_resistance_n"] == pytest.approx(
            out["frictional_resistance_n"] + out["residual_resistance_n"], rel=1e-12)
        assert out["total_resistance_coeff"] == pytest.approx(
            out["friction_coeff"] + out["residual_coeff"], rel=1e-12)

    def test_friction_floor_is_respected(self):
        """Eq. identity: a hull cannot have Ct below the ITTC-57 friction line."""
        for speed in (1.0, 3.0, 6.0):
            out = compute_resistance_core(4.356, 0.508, 0.508, 0.508, speed)
            assert out["total_resistance_coeff"] > out["friction_coeff"]

    def test_reynolds_and_froude_definitions(self):
        out = compute_resistance_core(4.356, 0.508, 0.508, 0.508, 3.0, 998.0, 1.004e-6)
        assert out["reynolds_number"] == pytest.approx(3.0 * 4.356 / 1.004e-6, rel=1e-12)
        assert out["froude_number"] == pytest.approx(3.0 / math.sqrt(GRAVITY * 4.356), rel=1e-12)

    def test_resistance_grows_with_speed(self):
        cts = [compute_resistance_core(4.356, 0.508, 0.508, 0.508, v)["total_resistance_n"]
               for v in (3.0, 4.0, 5.0, 6.0)]
        assert cts == sorted(cts)

    @pytest.mark.parametrize("cb", [0.0, 0.1, 1.2, -0.3])
    def test_out_of_range_cb_rejected(self, cb):
        with pytest.raises(ValueError):
            compute_resistance_core(4.356, 0.508, 0.508, cb, 3.0)

    def test_check_geometry_flags_impossible_froude(self):
        diag = check_geometry(5.72, 0.759, 0.246, 0.508, speed_ms=10.289)
        assert diag["ok"] is False and diag["froude_number_ok"] is False

    def test_check_geometry_accepts_benchmark_condition(self):
        diag = check_geometry(4.356, 0.508, 0.508, 0.508, speed_ms=3.0)
        assert diag["ok"] is True


# ---------------------------------------------------------------------------
# 2. Reference dataset: definitional consistency
# ---------------------------------------------------------------------------
class TestReferenceDataset:
    def test_dataset_passes_its_own_audit(self, dataset, audit_module):
        """No scored case may contain a physically impossible value."""
        audit = audit_module.audit_dataset(dataset)
        errors = [f for f in audit["findings"]
                  if f["severity"] == "ERROR"
                  and not any(c["case_id"] == f["case_id"] and c["status"] == "EXCLUDED"
                              for c in audit["cases"])]
        assert errors == [], errors

    def test_froude_column_matches_definition(self, dataset):
        """The Fn column was off by 5.6x for DTMB-5415 (it used a 181 m hull)."""
        for case in dataset["cases"]:
            geo = case["geometry"]
            L = geo["length_m"]
            fn = case["speed_ms"] / math.sqrt(GRAVITY * L)
            assert case["froude_number_computed"] == pytest.approx(fn, rel=5e-3), case["case_id"]

    def test_reference_ct_above_friction_floor(self, dataset):
        """Every experimental Ct must exceed Cf(Re): R_T > R_F always."""
        for case in dataset["cases"]:
            geo = case["geometry"]
            L, V = geo["length_m"], case["speed_ms"]
            nu = case.get("kinematic_viscosity", 1.004e-6)
            Re = V * L / nu
            Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
            assert case["experimental_ct"] > Cf, case["case_id"]

    def test_resistance_matches_its_own_ct(self, dataset):
        """Where a reference wetted surface exists, R_T = Ct * 0.5 rho S V^2."""
        checked = 0
        for case in dataset["cases"]:
            S = case["geometry"].get("wetted_surface_m2")
            rt = case.get("experimental_rt_n")
            if not S or rt is None:
                continue
            rho = case.get("water_density", 998.2)
            expected = case["experimental_ct"] * 0.5 * rho * S * case["speed_ms"] ** 2
            assert rt == pytest.approx(expected, rel=1e-3), case["case_id"]
            checked += 1
        assert checked >= 4, "SUBOFF cases should carry a verifiable R_T"

    def test_impossible_resistance_values_are_withheld_not_invented(self, dataset):
        """Cases without a reference wetted surface must not carry a fabricated R_T."""
        for case in dataset["cases"]:
            if case.get("experimental_rt_n") is None:
                assert "rt_provenance" in case, case["case_id"]
                assert "withheld" in case["rt_provenance"], case["case_id"]

    def test_unphysical_cases_are_excluded_with_a_reason(self, dataset):
        for case in dataset["cases"]:
            if case.get("excluded"):
                assert case["exclusion_reason"], case["case_id"]

    def test_retained_records_still_cover_all_families(self, dataset):
        counts = {}
        for case in dataset["cases"]:
            if not case.get("excluded"):
                counts[case["ship_type"]] = counts.get(case["ship_type"], 0) + 1
        assert all(counts.get(t, 0) >= 1 for t in ("SUBOFF", "DTMB-5415", "KVLCC2")), counts


# ---------------------------------------------------------------------------
# 3. Geometry presets must not contradict themselves
# ---------------------------------------------------------------------------
class TestGeometryPresets:
    def test_displacement_is_derived_from_cb(self, gate):
        for ship_type, geo in gate.SHIP_GEOMETRY.items():
            derived = geo["cb"] * geo["length"] * geo["beam"] * geo["draft"]
            assert geo["displacement_m3"] == pytest.approx(derived, rel=1e-6), ship_type
            assert geo["displacement"] == pytest.approx(derived, rel=1e-6), ship_type

    def test_presets_are_physically_plausible(self, gate):
        for ship_type, geo in gate.SHIP_GEOMETRY.items():
            diag = check_geometry(geo["length"], geo["beam"], geo["draft"], geo["cb"])
            assert diag["ok"] is True, ship_type


# ---------------------------------------------------------------------------
# 4. The gate itself — the two contract bugs
# ---------------------------------------------------------------------------
class TestBenchmarkGate:
    @pytest.mark.parametrize("ship_type", ["SUBOFF", "DTMB-5415", "KVLCC2"])
    def test_calc_ct_is_not_silently_zero(self, gate, ship_type):
        """Pre-fix: response had no `ct` key -> calc_ct == 0.0 -> 100 % error."""
        resp = gate.benchmark_validate(
            gate.BenchmarkValidateRequest(ship_type=ship_type, benchmark_id="all"))
        assert resp.results, ship_type
        for r in resp.results:
            assert r.calc_ct > 1e-4, (ship_type, r.case_id, r.calc_ct)
            assert r.error_pct < 99.0, (ship_type, r.case_id, r.error_pct)

    def test_speed_is_taken_from_the_case(self, gate, dataset):
        """Pre-fix: payload key `speed` was ignored -> everything ran at 1.0 m/s."""
        resp = gate.benchmark_validate(
            gate.BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all"))
        speeds = {r.case_id: r.speed_ms for r in resp.results}
        for case in dataset["cases"]:
            if case["case_id"] in speeds:
                assert speeds[case["case_id"]] == case["speed_ms"]

    def test_calc_ct_reproduced_by_independent_kernel_call(self, gate, dataset):
        """The gate must report exactly what the shared kernel computes."""
        cases = {c["case_id"]: c for c in dataset["cases"]}
        for ship_type in ("SUBOFF", "KVLCC2"):
            resp = gate.benchmark_validate(
                gate.BenchmarkValidateRequest(ship_type=ship_type, benchmark_id="all"))
            for r in resp.results:
                geo = gate._case_geometry(ship_type, cases[r.case_id])
                core = compute_resistance_core(
                    geo["length"], geo["beam"], geo["draft"], geo["cb"], r.speed_ms)
                # the gate rounds coefficients to 6 dp before reporting
                assert r.calc_ct == pytest.approx(core["total_resistance_coeff"], abs=5e-7)

    def test_excluded_cases_are_reported_not_scored(self, gate):
        resp = gate.benchmark_validate(
            gate.BenchmarkValidateRequest(ship_type="DTMB-5415", benchmark_id="all"))
        excluded = {c["case_id"] for c in resp.summary["excluded_cases"]}
        scored = {r.case_id for r in resp.results}
        assert excluded, "DTMB-5415 has Fn>1 cases that must be excluded"
        assert not (excluded & scored)
        assert resp.summary["scored_cases"] == len(scored)

    def test_kvlcc2_honest_fail_is_reported(self, gate):
        """The kernel is calibrated for slender bodies; full-form hulls must fail loudly."""
        resp = gate.benchmark_validate(
            gate.BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="all"))
        assert resp.summary["passed"] == 0
        assert resp.summary["algorithm_upgrade_recommended"] is True

    def test_unknown_case_returns_404(self, gate):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            gate.benchmark_validate(
                gate.BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="nope"))
        assert exc.value.status_code == 404