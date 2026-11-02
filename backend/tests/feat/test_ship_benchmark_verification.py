"""
Verification tests for the ship-resistance benchmark chain (mission m1609).

Scope: geometry verification (Cb / L / B first), the resistance-response
contract that the benchmark depends on, and the integrity of the reference
dataset.  Each test pins one defect that was found and fixed in this mission, so
a regression cannot silently re-open it.

These tests load the numeric modules by path instead of importing ``api`` as a
package: the web application pulls in optional dependencies (psycopg2,
cryptography) that are absent from the CI image, and the physics under test must
stay verifiable regardless (see docs/geometry_verification_m1609.md).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
DOMAIN = BACKEND / "api" / "domain"
DATA_PATH = BACKEND / "data" / "benchmark_reference.json"


def _load(name: str, path: Path):
    """Load a module by path under a fixed name (no sys.path mutation).

    ``sys.path.insert(0, api/domain)`` is deliberately avoided: the local
    ``platform.py`` would shadow the stdlib ``platform`` module.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


hg = _load("hull_geometry", DOMAIN / "hull_geometry.py")
rc = _load("resistance_core", DOMAIN / "resistance_core.py")
sb = _load("ship_benchmark", DOMAIN / "ship_benchmark.py")


@pytest.fixture(scope="module")
def dataset():
    return json.loads(DATA_PATH.read_text())


# ---------------------------------------------------------------------------
# 1. Geometry verification gate
# ---------------------------------------------------------------------------

class TestGeometryGate:
    @pytest.mark.parametrize("hull", list(hg.PUBLISHED_GEOMETRY))
    def test_gate_passes_for_published_hulls(self, hull):
        gate = hg.validate_hull_geometry(hull)
        assert gate["ok"] is True, gate["checks"]

    def test_kvlcc2_block_coefficient_is_geometry_consistent(self):
        """Cb must equal nabla / (L B T) from the same published table."""
        derived = hg.validate_hull_geometry("KVLCC2")["derived"]
        assert derived["cb_from_volume"] == pytest.approx(0.8098, abs=5e-4)
        assert derived["length_beam_ratio"] == pytest.approx(5.5172, abs=1e-3)
        assert derived["beam_draft_ratio"] == pytest.approx(2.7885, abs=1e-3)

    def test_dtmb5415_published_volume_replaces_legacy_466kg(self):
        """v1.0 stored 466.3 kg, implying Cb=0.437 against a published 0.506."""
        derived = hg.validate_hull_geometry("DTMB-5415")["derived"]
        assert derived["cb_from_volume"] == pytest.approx(0.5094, abs=1e-3)
        legacy_cb = 0.4663 / (5.72 * 0.760 * 0.248)
        assert legacy_cb < 0.45  # the preset this mission rejected

    def test_holtrop_surface_matches_published_kvlcc2(self):
        """Holtrop & Mennen (1982) vs published S: must agree within 3 %."""
        s = hg.holtrop_wetted_surface(320.0, 58.0, 20.8, 0.8098, 0.9980, 0.9038)
        assert abs(s - 27194.0) / 27194.0 < 0.03
        assert abs(s - 27194.0) / 27194.0 == pytest.approx(0.00468, abs=5e-4)

    def test_holtrop_surface_beats_legacy_placeholder(self):
        """The legacy placeholder was +50 % (KVLCC2) and +75 % (DTMB)."""
        legacy_kvlcc2 = math.pi * 58.0 * 320.0 * (0.5 + 0.25 * 0.8098)
        assert (legacy_kvlcc2 - 27194.0) / 27194.0 == pytest.approx(0.506, abs=5e-3)
        legacy_dtmb = math.pi * 0.760 * 5.72 * (0.5 + 0.25 * 0.508)
        assert (legacy_dtmb - 4.874) / 4.874 > 0.7

    def test_suhoff_declared_cb_violates_cauchy_schwarz_bound(self):
        """SUBOFF has no offsets; (L, D, S) alone bound Cb_cyl from below."""
        bounds = hg.axisymmetric_cb_bounds(4.356, 0.508, 5.42)
        assert bounds["cb_lower_bound"] == pytest.approx(0.6078, abs=5e-4)
        gate = hg.validate_hull_geometry("SUBOFF", {"form": "axisymmetric",
                                                    "length_m": 4.356,
                                                    "diameter_m": 0.508,
                                                    "s_ref_m2": 5.42,
                                                    "cb": 0.508})
        cb_check = [c for c in gate["checks"] if c["check"] == "cb_within_cauchy_schwarz_bracket"][0]
        assert cb_check["ok"] is False
        assert gate["ok"] is False

    def test_effective_fullness_is_admissible(self):
        cb_eff = hg.axisymmetric_cb_effective(4.356, 0.508, 5.42)
        assert cb_eff == pytest.approx(0.7796, abs=5e-4)
        assert cb_eff ** 2 <= cb_eff <= 1.0


# ---------------------------------------------------------------------------
# 2. Resistance solver contract (the defect that made the benchmark unusable)
# ---------------------------------------------------------------------------

class TestResistanceContract:
    def test_solution_carries_coefficients(self):
        sol = rc.solve_resistance("SUBOFF", 3.0)
        for key in ("ct", "cf", "cr", "wetted_surface_m2", "reynolds_number",
                    "froude_number", "validity", "geometry"):
            assert key in sol, f"missing {key} in resistance solution"
        assert sol["ct"] == pytest.approx(sol["cf"] + sol["cr"], rel=1e-12)

    def test_ct_is_independent_of_wetted_surface(self):
        """Ct = Cf + Cr, so the force-level S error never affected the coefficient.

        Checked on a monohull, where Cb comes from the published table and is not
        itself derived from S (for a body of revolution the effective fullness is
        S/(pi D L), so there S does feed back into Cr).
        """
        a = rc.solve_resistance("KVLCC2", 7.202, overrides={"wetted_surface_m2": 27194.0})
        b = rc.solve_resistance("KVLCC2", 7.202, overrides={"wetted_surface_m2": 40958.4})
        assert a["ct"] == pytest.approx(b["ct"], rel=1e-12)
        # ... but it does scale the force by the surface ratio
        assert a["total_resistance_n"] / b["total_resistance_n"] == pytest.approx(
            27194.0 / 40958.4, rel=1e-3)

    def test_endpoint_response_model_exposes_ct(self):
        """Static guard: the response model must declare the compared quantity."""
        tree = ast.parse((BACKEND / "api" / "calculations.py").read_text())
        fields = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "ResistanceResponse":
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        fields.add(stmt.target.id)
        assert {"ct", "cf", "cr"} <= fields, f"ResistanceResponse fields: {sorted(fields)}"

    def test_speed_alias_accepted(self):
        """The benchmark used to post 'speed'; the model only declared 'speed_ms'."""
        req = rc  # keep linters quiet about unused import in this scope
        calculations = (BACKEND / "api" / "calculations.py").read_text()
        assert 'AliasChoices("speed_ms", "speed"' in calculations
        assert req is rc

    def test_geometry_gate_refuses_tampered_hull(self, monkeypatch):
        tampered = dict(hg.PUBLISHED_GEOMETRY["KVLCC2"])
        tampered["volume_m3"] = 307000.0  # the rejected v1.0 value (-1.8 % volume)
        tampered["volume_m3"] = 250000.0  # 20 % off: must be caught
        monkeypatch.setitem(hg.PUBLISHED_GEOMETRY, "TAMPERED", tampered)
        with pytest.raises(rc.GeometryError):
            rc.solve_resistance("TAMPERED", 7.0)

    def test_out_of_envelope_is_reported_not_hidden(self):
        inside = rc.solve_resistance("SUBOFF", 3.0)["validity"]
        outside = rc.solve_resistance("SUBOFF", 5.0)["validity"]
        merchant = rc.solve_resistance("KVLCC2", 7.202)["validity"]
        assert inside["in_envelope"] is True
        assert outside["in_envelope"] is False
        assert any("Fr=" in r for r in outside["reasons"])
        assert merchant["in_envelope"] is False
        assert any("axisymmetric" in r for r in merchant["reasons"])


# ---------------------------------------------------------------------------
# 3. Reference dataset integrity
# ---------------------------------------------------------------------------

class TestReferenceDataset:
    def test_case_count_and_required_fields(self, dataset):
        assert len(dataset["cases"]) == 12
        for case in dataset["cases"]:
            for key in ("case_id", "ship_type", "speed_ms", "experimental_ct", "source_citation"):
                assert key in case

    def test_force_matches_coefficient(self, dataset):
        """v1.0 hand-typed forces disagreed with Ct by 3-8x on some cases."""
        for case in dataset["cases"]:
            geo = case["geometry"]
            expected = (0.5 * 998.0 * geo["wetted_surface_m2"]
                        * case["experimental_ct"] * case["speed_ms"] ** 2)
            assert case["experimental_rt_n"] == pytest.approx(expected, rel=0.01), case["case_id"]

    def test_legacy_forces_are_preserved_not_destroyed(self, dataset):
        for case in dataset["cases"]:
            assert "reported_rt_n_legacy" in case
        legacy = {c["case_id"]: c["reported_rt_n_legacy"] for c in dataset["cases"]}
        assert legacy["SUBOFF-01"] == 12.5            # 7.4x too small
        assert legacy["KVLCC2-01"] == 550000.0        # 3.7x too small

    def test_froude_number_consistent(self, dataset):
        for case in dataset["cases"]:
            geo = case["geometry"]
            fr = case["speed_ms"] / math.sqrt(9.81 * geo["length_m"])
            assert case["froude_number"] == pytest.approx(fr, rel=0.01), case["case_id"]

    def test_geometry_blocks_cite_a_source(self, dataset):
        for case in dataset["cases"]:
            assert case["geometry"]["citation"]
            assert case["geometry"]["wetted_surface_m2"] > 0

    def test_model_scale_length_with_full_scale_speed_is_flagged(self, dataset):
        """DTMB-5415: 5.72 m model run at 20-30 kn -> Fr 1.37-2.06, unphysical."""
        flagged = {c["case_id"] for c in dataset["cases"] if c.get("provenance_flag")}
        assert flagged == {"DTMB5415-02", "DTMB5415-03", "DTMB5415-04"}
        case = [c for c in dataset["cases"] if c["case_id"] == "DTMB5415-04"][0]
        assert case["froude_number"] > 2.0
        assert any("displacement regime" in f for f in case["provenance_flag"])

    def test_dataset_records_its_own_verification_basis(self, dataset):
        assert dataset["verification"]["rho_ref_kg_m3"] == 998.0
        assert "rt = 0.5 * rho_ref * S_published * ct * V^2" in \
               dataset["verification"]["derived_rt_formula"]


# ---------------------------------------------------------------------------
# 4. Accuracy against the reference data (acceptance guard)
# ---------------------------------------------------------------------------

class TestAccuracy:
    def _err(self, hull, speed, ref_ct):
        sol = rc.solve_resistance(hull, speed)
        return abs(sol["ct"] - ref_ct) / ref_ct * 100.0, sol

    def test_certified_suboff_cases_within_15pct(self):
        err1, sol1 = self._err("SUBOFF", 3.0, 0.0038)
        err2, sol2 = self._err("SUBOFF", 4.0, 0.0040)
        assert sol1["validity"]["in_envelope"] and sol2["validity"]["in_envelope"]
        assert err1 == pytest.approx(5.33, abs=0.05)
        assert err2 == pytest.approx(5.78, abs=0.05)
        assert err1 <= 15.0 and err2 <= 15.0

    def test_merchant_hulls_are_documented_as_out_of_envelope(self):
        """Honest-FAIL: the residual fit has no form factor and no wave term."""
        for speed, ref in ((7.202, 0.0028), (7.717, 0.0030)):
            err, sol = self._err("KVLCC2", speed, ref)
            assert sol["validity"]["in_envelope"] is False
            assert err > 40.0  # under-prediction, reported not hidden


# ---------------------------------------------------------------------------
# 5. Benchmark endpoint behaviour (loaded outside the web application)
# ---------------------------------------------------------------------------

class TestBenchmarkEndpoint:
    def test_contract_violation_is_reported_when_ct_missing(self, monkeypatch):
        """A response without 'ct' used to yield a silent 100 % error for all cases."""
        monkeypatch.setattr(
            sb, "_call_resistance_api",
            lambda hull, speed, geo: {"method": "fake", "total_resistance_n": 1.0},
        )
        monkeypatch.setenv("BENCHMARK_ALLOW_LOCAL_FALLBACK", "0")
        resp = sb.benchmark_validate(
            sb.BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all")
        )
        assert resp.summary["contract_violations"] == [c["case_id"] for c in
                                                       json.loads(DATA_PATH.read_text())["cases"]
                                                       if c["ship_type"] == "SUBOFF"]
        assert all("contract violation" in r.note for r in resp.results)

    def test_local_fallback_reproduces_in_process_solution(self, monkeypatch):
        """When the HTTP service is down the benchmark still exercises the algorithm."""
        def _boom(*a, **k):
            raise RuntimeError("connection refused")
        monkeypatch.setattr("httpx.post", _boom)
        monkeypatch.setenv("BENCHMARK_ALLOW_LOCAL_FALLBACK", "1")
        data = sb._resistance("SUBOFF", 3.0, sb.SHIP_GEOMETRY["SUBOFF"])
        assert data[1] == "local_fallback"
        direct = rc.solve_resistance("SUBOFF", 3.0)
        assert data[0]["ct"] == pytest.approx(direct["ct"], rel=1e-9)

    def test_summary_reports_geometry_audit(self):
        resp = sb.benchmark_validate(
            sb.BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="all")
        )
        geo = resp.summary["geometry"]
        assert geo["gate_ok"] is True
        assert geo["preset"]["wetted_surface_m2"] == pytest.approx(27194.0, rel=1e-6)
        assert geo["preset"]["cb_source"] == "published_table"

    def test_suboff_honest_fail_note_retained(self):
        resp = sb.benchmark_validate(
            sb.BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all")
        )
        assert resp.summary["honest_fail"] is True
        flagged = [r for r in resp.results if "HONEST-FAIL" in r.note]
        assert len(flagged) >= 2  # Fr > 0.62 cases
        assert all(r.in_validity_envelope is False for r in flagged)