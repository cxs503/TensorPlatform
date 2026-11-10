"""Tests for the cross-solver verification framework.

Covers:
- Input/output schema validation
- Conservation residual gate
- Auto mutual arbitration engine
- Traceability (SHA-256 digests)
- Schema coverage analysis
- Negative controls (missing fields, conservation_fail)
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures: synthetic verified cases
# ---------------------------------------------------------------------------

def _make_input_case(
    case_id: str = "cavity_re1000",
    geo_type: str = "cavity",
    dimensions: dict | None = None,
    solver_name: str = "TensorLBM",
    solver_version: str = "1.0.0",
    extra: dict | None = None,
) -> dict:
    """Create a minimal valid input case."""
    if dimensions is None:
        dimensions = {"domain_size": [1.0, 1.0]}
    data = {
        "schema_version": "1.0.0",
        "case_id": case_id,
        "geometry": {
            "type": geo_type,
            "dimensions": dimensions,
        },
        "physics": {
            "Re": 1000,
            "boundary_conditions": {
                "inlet": "lid_driven",
                "walls": "no_slip",
            },
        },
        "solver": {
            "name": solver_name,
            "version": solver_version,
        },
    }
    if extra:
        data.update(extra)
    return data


def _make_output_case(
    case_id: str = "cavity_re1000",
    solver_name: str = "TensorLBM",
    solver_version: str = "1.0.0",
    primary_quantities: list | None = None,
    conservation_residuals: list | None = None,
    validation_status: str | None = None,
    git_commit: str | None = None,
) -> dict:
    """Create a minimal valid output case."""
    if primary_quantities is None:
        primary_quantities = [
            {"name": "C_D", "value": 1.0, "unit": "dimensionless"},
        ]
    data = {
        "schema_version": "1.0.0",
        "case_id": case_id,
        "solver_signature": {
            "name": solver_name,
            "version": solver_version,
            "git_commit": git_commit,
        },
        "primary_quantities": primary_quantities,
        "validation_status": validation_status or "pass",
    }
    if conservation_residuals is not None:
        data["conservation_residuals"] = conservation_residuals
    return data


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------

class TestInputSchemaValidation:
    """Test input schema validation."""

    def test_valid_input_passes(self):
        from backend.physics.cross_solver import validate_input_schema
        data = _make_input_case()
        errors = validate_input_schema(data)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_missing_geometry_type_fails(self):
        from backend.physics.cross_solver import validate_input_schema
        data = _make_input_case()
        del data["geometry"]["type"]
        errors = validate_input_schema(data)
        assert any("geometry" in e and "type" in e for e in errors)

    def test_missing_dimensions_fails(self):
        from backend.physics.cross_solver import validate_input_schema
        data = _make_input_case()
        del data["geometry"]["dimensions"]
        errors = validate_input_schema(data)
        assert any("dimensions" in e for e in errors)

    def test_missing_solver_name_fails(self):
        from backend.physics.cross_solver import validate_input_schema
        data = _make_input_case()
        del data["solver"]["name"]
        errors = validate_input_schema(data)
        assert any("solver" in e and "name" in e for e in errors)

    def test_missing_solver_version_fails(self):
        from backend.physics.cross_solver import validate_input_schema
        data = _make_input_case()
        del data["solver"]["version"]
        errors = validate_input_schema(data)
        assert any("solver" in e and "version" in e for e in errors)

    def test_missing_boundary_conditions_fails(self):
        from backend.physics.cross_solver import validate_input_schema
        data = _make_input_case()
        del data["physics"]["boundary_conditions"]
        errors = validate_input_schema(data)
        assert any("boundary_conditions" in e for e in errors)

    def test_wigley_input_valid(self):
        from backend.physics.cross_solver import validate_input_schema
        data = {
            "schema_version": "1.0.0",
            "case_id": "wigley_iii_fn0.25",
            "geometry": {
                "type": "wigley_parabolic",
                "dimensions": {"L": 100.0, "B": 10.0, "T": 6.25},
                "cb": 0.4444,
            },
            "physics": {
                "Fr": 0.25,
                "Re": 5e7,
                "boundary_conditions": {
                    "free_surface": "linearized",
                    "inlet": "uniform_flow",
                    "outlet": "radiation",
                },
            },
            "solver": {
                "name": "TensorBEM",
                "version": "2.1.0",
                "git_commit": "abc1234",
            },
        }
        errors = validate_input_schema(data)
        assert errors == [], f"Unexpected errors: {errors}"


class TestOutputSchemaValidation:
    """Test output schema validation."""

    def test_valid_output_passes(self):
        from backend.physics.cross_solver import validate_output_schema
        data = _make_output_case()
        errors = validate_output_schema(data)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_empty_primary_quantities_fails(self):
        from backend.physics.cross_solver import validate_output_schema
        data = _make_output_case(primary_quantities=[])
        errors = validate_output_schema(data)
        assert any("primary_quantities" in e and "at least one" in e for e in errors)

    def test_missing_primary_quantities_fails(self):
        """Negative control: delete primary_quantities entirely."""
        from backend.physics.cross_solver import validate_output_schema
        data = _make_output_case()
        del data["primary_quantities"]
        errors = validate_output_schema(data)
        assert any("primary_quantities" in e for e in errors), \
            f"Expected error about missing primary_quantities, got: {errors}"

    def test_missing_quantity_name_fails(self):
        from backend.physics.cross_solver import validate_output_schema
        data = _make_output_case(primary_quantities=[{"value": 1.0, "unit": "N"}])
        errors = validate_output_schema(data)
        assert any("name" in e for e in errors)

    def test_conservation_residuals_valid(self):
        from backend.physics.cross_solver import validate_output_schema
        data = _make_output_case(conservation_residuals=[
            {"law": "mass", "residual": 1e-8, "tolerance": 1e-6},
            {"law": "momentum_x", "residual": 5e-5, "tolerance": 1e-4},
        ])
        errors = validate_output_schema(data)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_conservation_residuals_missing_law_fails(self):
        from backend.physics.cross_solver import validate_output_schema
        data = _make_output_case(conservation_residuals=[
            {"residual": 1e-8, "tolerance": 1e-6},
        ])
        errors = validate_output_schema(data)
        assert any("law" in e for e in errors)


# ---------------------------------------------------------------------------
# Traceability tests
# ---------------------------------------------------------------------------

class TestTraceability:
    """Test SHA-256 digest and traceability."""

    def test_digest_is_deterministic(self):
        from backend.physics.cross_solver import compute_digest
        data = {"a": 1, "b": [2, 3], "c": "test"}
        d1 = compute_digest(data)
        d2 = compute_digest(data)
        assert d1 == d2
        assert len(d1) == 64  # SHA-256 hex length

    def test_digest_changes_with_data(self):
        from backend.physics.cross_solver import compute_digest
        d1 = compute_digest({"a": 1})
        d2 = compute_digest({"a": 2})
        assert d1 != d2

    def test_digest_matches_manual_sha256(self):
        from backend.physics.cross_solver import compute_digest
        import json
        data = {"x": 42, "y": "hello"}
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert compute_digest(data) == expected

    def test_enrich_with_traceability(self):
        from backend.physics.cross_solver import enrich_with_traceability
        data = {"case_id": "test"}
        enriched = enrich_with_traceability(data, role="input")
        assert "traceability" in enriched
        assert "input_digest" in enriched["traceability"]
        assert "computed_at" in enriched["traceability"]
        assert len(enriched["traceability"]["input_digest"]) == 64

    def test_traceability_includes_solver_version_and_git_commit(self):
        from backend.physics.cross_solver import enrich_with_traceability
        data = {
            "case_id": "test",
            "solver_signature": {
                "name": "TensorLBM",
                "version": "1.0.0",
                "git_commit": "deadbeef",
            },
        }
        enriched = enrich_with_traceability(data, role="output")
        assert enriched["solver_signature"]["git_commit"] == "deadbeef"
        assert enriched["traceability"]["output_digest"]


# ---------------------------------------------------------------------------
# Conservation residual gate tests
# ---------------------------------------------------------------------------

class TestConservationGate:
    """Test conservation residual gate."""

    def test_mass_residual_passes(self):
        from backend.physics.cross_solver import check_conservation_residuals
        data = _make_output_case(conservation_residuals=[
            {"law": "mass", "residual": 1e-8, "tolerance": 1e-6},
        ])
        results = check_conservation_residuals(data)
        assert len(results) == 1
        assert results[0].passed is True

    def test_mass_residual_fails(self):
        from backend.physics.cross_solver import check_conservation_residuals
        data = _make_output_case(conservation_residuals=[
            {"law": "mass", "residual": 1.0, "tolerance": 1e-6},
        ])
        results = check_conservation_residuals(data)
        assert len(results) == 1
        assert results[0].passed is False

    def test_momentum_residual_passes(self):
        from backend.physics.cross_solver import check_conservation_residuals
        data = _make_output_case(conservation_residuals=[
            {"law": "momentum_x", "residual": 5e-5, "tolerance": 1e-4},
            {"law": "momentum_y", "residual": 3e-5, "tolerance": 1e-4},
        ])
        results = check_conservation_residuals(data)
        assert all(r.passed for r in results)

    def test_momentum_residual_fails(self):
        from backend.physics.cross_solver import check_conservation_residuals
        data = _make_output_case(conservation_residuals=[
            {"law": "momentum_x", "residual": 2e-3, "tolerance": 1e-4},
        ])
        results = check_conservation_residuals(data)
        assert not results[0].passed

    def test_apply_gate_marks_conservation_fail(self):
        """Negative control: set mass residual to 1.0 → conservation_fail."""
        from backend.physics.cross_solver import apply_conservation_gate
        data = _make_output_case(conservation_residuals=[
            {"law": "mass", "residual": 1.0, "tolerance": 1e-6},
        ])
        result = apply_conservation_gate(data)
        assert result["validation_status"] == "conservation_fail"

    def test_apply_gate_passes_when_within_tolerance(self):
        from backend.physics.cross_solver import apply_conservation_gate
        data = _make_output_case(conservation_residuals=[
            {"law": "mass", "residual": 1e-8, "tolerance": 1e-6},
            {"law": "momentum_x", "residual": 5e-5, "tolerance": 1e-4},
        ])
        result = apply_conservation_gate(data)
        assert result["validation_status"] == "pass"

    def test_no_residuals_returns_unchanged(self):
        from backend.physics.cross_solver import apply_conservation_gate
        data = _make_output_case()
        result = apply_conservation_gate(data)
        assert "conservation_residuals" not in result or result.get("conservation_residuals") is None


# ---------------------------------------------------------------------------
# LBM-specific conservation tests (cavity/cylinder/poiseuille)
# ---------------------------------------------------------------------------

class TestLBMConservation:
    """Test conservation for LBM canonical cases."""

    def _make_lbm_output(self, case_id: str, mass_res: float, mom_res: float):
        return _make_output_case(
            case_id=case_id,
            solver_name="TensorLBM",
            solver_version="1.0.0",
            conservation_residuals=[
                {"law": "mass", "residual": mass_res, "tolerance": 1e-6},
                {"law": "momentum_x", "residual": mom_res, "tolerance": 1e-4},
                {"law": "momentum_y", "residual": mom_res, "tolerance": 1e-4},
            ],
        )

    def test_cavity_mass_conservation(self):
        from backend.physics.cross_solver import check_conservation_residuals
        data = self._make_lbm_output("cavity_re1000", 1e-8, 5e-5)
        results = check_conservation_residuals(data)
        assert all(r.passed for r in results)
        # mass < 1e-6
        mass_result = [r for r in results if r.law == "mass"][0]
        assert mass_result.residual < 1e-6

    def test_cylinder_mass_conservation(self):
        from backend.physics.cross_solver import check_conservation_residuals
        data = self._make_lbm_output("cylinder_re100", 5e-7, 8e-5)
        results = check_conservation_residuals(data)
        assert all(r.passed for r in results)
        mass_result = [r for r in results if r.law == "mass"][0]
        assert mass_result.residual < 1e-6

    def test_poiseuille_mass_conservation(self):
        from backend.physics.cross_solver import check_conservation_residuals
        data = self._make_lbm_output("poiseuille_re500", 1e-9, 1e-5)
        results = check_conservation_residuals(data)
        assert all(r.passed for r in results)
        mass_result = [r for r in results if r.law == "mass"][0]
        assert mass_result.residual < 1e-6

    def test_momentum_within_tolerance(self):
        from backend.physics.cross_solver import check_conservation_residuals
        data = self._make_lbm_output("cavity_re1000", 1e-8, 5e-5)
        results = check_conservation_residuals(data)
        mom_results = [r for r in results if "momentum" in r.law]
        for r in mom_results:
            assert r.residual < 1e-4


# ---------------------------------------------------------------------------
# Auto mutual arbitration tests
# ---------------------------------------------------------------------------

class TestMutualArbitration:
    """Test automatic mutual arbitration engine."""

    def _make_solver_output(self, solver_name: str, ct_value: float, version: str = "1.0.0"):
        return _make_output_case(
            case_id="wigley_iii_fn0.25",
            solver_name=solver_name,
            solver_version=version,
            primary_quantities=[
                {"name": "C_T", "value": ct_value, "unit": "dimensionless"},
                {"name": "C_F", "value": ct_value * 0.6, "unit": "dimensionless"},
                {"name": "C_W", "value": ct_value * 0.4, "unit": "dimensionless"},
            ],
        )

    def test_two_solver_agreement(self):
        from backend.physics.cross_solver import run_mutual_arbitration
        outputs = [
            self._make_solver_output("TensorLBM", 0.00320),
            self._make_solver_output("TensorFVM", 0.00325),
        ]
        report = run_mutual_arbitration("wigley_iii_fn0.25", "C_T", outputs)
        assert len(report.entries) == 2
        assert len(report.pair_results) == 1
        pair = report.pair_results[0]
        assert pair.solver_a == "TensorLBM"
        assert pair.solver_b == "TensorFVM"
        assert pair.relative_difference > 0
        assert pair.within_tolerance is True  # ~1.5% < 5%

    def test_three_solver_arbitration(self):
        """Wigley III with three solvers (m5520 scenario)."""
        from backend.physics.cross_solver import run_mutual_arbitration
        outputs = [
            self._make_solver_output("TensorLBM", 0.00320, "1.0.0"),
            self._make_solver_output("TensorFVM", 0.00325, "2.0.0"),
            self._make_solver_output("TensorBEM", 0.00318, "2.1.0"),
        ]
        report = run_mutual_arbitration("wigley_iii_fn0.25", "C_T", outputs)
        assert len(report.entries) == 3
        assert len(report.pair_results) == 3  # C(3,2) = 3
        assert report.consensus_value > 0
        assert report.consensus_spread_pct >= 0
        # Check attribution is present
        for p in report.pair_results:
            assert p.attribution is not None
            assert len(p.attribution) > 0

    def test_disagreement_attribution(self):
        from backend.physics.cross_solver import run_mutual_arbitration
        outputs = [
            self._make_solver_output("TensorLBM", 0.00300),
            self._make_solver_output("TensorFVM", 0.00500),  # 66% difference
        ]
        report = run_mutual_arbitration("wigley_iii_fn0.25", "C_T", outputs)
        pair = report.pair_results[0]
        assert pair.within_tolerance is False
        assert "exceeds" in pair.attribution
        assert "possible causes" in pair.attribution

    def test_single_solver_returns_no_pairs(self):
        from backend.physics.cross_solver import run_mutual_arbitration
        outputs = [self._make_solver_output("TensorLBM", 0.00320)]
        report = run_mutual_arbitration("wigley_iii_fn0.25", "C_T", outputs)
        assert len(report.pair_results) == 0
        assert "Insufficient" in report.summary

    def test_arbitration_report_serializable(self):
        from backend.physics.cross_solver import run_mutual_arbitration, arbitration_report_to_dict
        outputs = [
            self._make_solver_output("TensorLBM", 0.00320),
            self._make_solver_output("TensorFVM", 0.00325),
        ]
        report = run_mutual_arbitration("wigley_iii_fn0.25", "C_T", outputs)
        d = arbitration_report_to_dict(report)
        # Must be JSON serializable
        json.dumps(d)
        assert "case_id" in d
        assert "pair_results" in d
        assert "consensus_value" in d


# ---------------------------------------------------------------------------
# Schema coverage analysis tests
# ---------------------------------------------------------------------------

class TestCoverageAnalysis:
    """Test schema coverage analysis."""

    def test_full_coverage(self):
        from backend.physics.cross_solver import analyze_coverage
        inputs = [
            _make_input_case("case1", geo_type="cavity"),
            _make_input_case("case2", geo_type="cylinder"),
            _make_input_case("case3", geo_type="poiseuille"),
        ]
        outputs = [
            _make_output_case("case1", primary_quantities=[{"name": "C_D", "value": 1.0, "unit": "N"}]),
            _make_output_case("case2", primary_quantities=[{"name": "C_D", "value": 2.0, "unit": "N"}]),
            _make_output_case("case3", primary_quantities=[{"name": "C_D", "value": 3.0, "unit": "N"}]),
        ]
        stats = analyze_coverage(inputs, outputs)
        assert stats.total_cases == 3
        assert stats.geometry_type_parsed == 3
        assert stats.geometry_type_pct == 100.0
        assert stats.primary_quantities_parsed == 3
        assert stats.primary_quantities_pct == 100.0

    def test_partial_coverage(self):
        from backend.physics.cross_solver import analyze_coverage
        inputs = [
            _make_input_case("case1", geo_type="cavity"),
            {"schema_version": "1.0.0", "case_id": "case2", "geometry": {}, "physics": {"boundary_conditions": {}}, "solver": {"name": "X", "version": "1"}},
        ]
        outputs = [
            _make_output_case("case1", primary_quantities=[{"name": "C_D", "value": 1.0, "unit": "N"}]),
            _make_output_case("case2", primary_quantities=[]),
        ]
        stats = analyze_coverage(inputs, outputs)
        assert stats.total_cases == 2
        assert stats.geometry_type_parsed == 1  # only case1 has type
        assert stats.geometry_type_pct == 50.0
        assert stats.primary_quantities_parsed == 1  # only case1 has non-empty pq
        assert stats.primary_quantities_pct == 50.0


# ---------------------------------------------------------------------------
# Negative control tests
# ---------------------------------------------------------------------------

class TestNegativeControls:
    """Negative controls: deliberate failures that must be caught."""

    def test_deleted_primary_quantities_raises_error(self):
        """Negative control (d): delete primary_quantities → parse error."""
        from backend.physics.cross_solver import validate_output_schema
        data = _make_output_case()
        del data["primary_quantities"]
        errors = validate_output_schema(data)
        assert len(errors) > 0, "Should detect missing primary_quantities"

    def test_mass_residual_one_marks_conservation_fail(self):
        """Negative control (d): mass residual = 1.0 → conservation_fail."""
        from backend.physics.cross_solver import apply_conservation_gate
        data = _make_output_case(conservation_residuals=[
            {"law": "mass", "residual": 1.0, "tolerance": 1e-6},
        ])
        result = apply_conservation_gate(data)
        assert result["validation_status"] == "conservation_fail", \
            f"Expected conservation_fail, got {result['validation_status']}"

    def test_missing_case_id_in_input(self):
        from backend.physics.cross_solver import validate_input_schema
        data = _make_input_case()
        del data["case_id"]
        errors = validate_input_schema(data)
        assert any("case_id" in e for e in errors)

    def test_missing_schema_version_in_output(self):
        from backend.physics.cross_solver import validate_output_schema
        data = _make_output_case()
        del data["schema_version"]
        errors = validate_output_schema(data)
        assert any("schema_version" in e for e in errors)


# ---------------------------------------------------------------------------
# Schema file existence tests
# ---------------------------------------------------------------------------

class TestSchemaFiles:
    """Test that schema files exist and are valid JSON."""

    def test_input_schema_exists(self):
        from backend.physics.cross_solver import INPUT_SCHEMA_PATH
        assert INPUT_SCHEMA_PATH.exists(), f"Input schema not found at {INPUT_SCHEMA_PATH}"

    def test_output_schema_exists(self):
        from backend.physics.cross_solver import OUTPUT_SCHEMA_PATH
        assert OUTPUT_SCHEMA_PATH.exists(), f"Output schema not found at {OUTPUT_SCHEMA_PATH}"

    def test_input_schema_valid_json(self):
        from backend.physics.cross_solver import load_input_schema
        schema = load_input_schema()
        assert "required" in schema
        assert "properties" in schema

    def test_output_schema_valid_json(self):
        from backend.physics.cross_solver import load_output_schema
        schema = load_output_schema()
        assert "required" in schema
        assert "properties" in schema


# ---------------------------------------------------------------------------
# Integration: full pipeline test
# ---------------------------------------------------------------------------

class TestFullPipeline:
    """End-to-end pipeline: input → output → validation → arbitration."""

    def test_wigley_iii_full_pipeline(self):
        """Simulate Wigley III with three solvers, full pipeline."""
        from backend.physics.cross_solver import (
            validate_input_schema, validate_output_schema,
            apply_conservation_gate, run_mutual_arbitration,
            arbitration_report_to_dict, compute_digest,
        )

        # Step 1: Validate inputs
        input_lbm = {
            "schema_version": "1.0.0",
            "case_id": "wigley_iii_fn0.25",
            "geometry": {"type": "wigley_parabolic", "dimensions": {"L": 100.0, "B": 10.0, "T": 6.25}},
            "physics": {"Fr": 0.25, "Re": 5e7, "boundary_conditions": {"free_surface": "linearized"}},
            "solver": {"name": "TensorLBM", "version": "1.0.0", "git_commit": "abc1234"},
        }
        assert validate_input_schema(input_lbm) == []

        # Step 2: Create outputs with conservation residuals
        output_lbm = {
            "schema_version": "1.0.0",
            "case_id": "wigley_iii_fn0.25",
            "solver_signature": {"name": "TensorLBM", "version": "1.0.0", "git_commit": "abc1234"},
            "primary_quantities": [
                {"name": "C_T", "value": 0.00320, "unit": "dimensionless"},
            ],
            "conservation_residuals": [
                {"law": "mass", "residual": 1e-8, "tolerance": 1e-6},
                {"law": "momentum_x", "residual": 5e-5, "tolerance": 1e-4},
            ],
        }
        assert validate_output_schema(output_lbm) == []

        # Step 3: Apply conservation gate
        output_lbm = apply_conservation_gate(output_lbm)
        assert output_lbm["validation_status"] == "pass"

        # Step 4: Add traceability
        output_lbm["traceability"] = {
            "input_digest": compute_digest(input_lbm),
            "output_digest": compute_digest(output_lbm),
            "computed_at": "2026-11-09T00:00:00Z",
        }

        # Step 5: Create two more solver outputs
        output_fvm = {
            "schema_version": "1.0.0",
            "case_id": "wigley_iii_fn0.25",
            "solver_signature": {"name": "TensorFVM", "version": "2.0.0", "git_commit": "def5678"},
            "primary_quantities": [
                {"name": "C_T", "value": 0.00325, "unit": "dimensionless"},
            ],
            "conservation_residuals": [
                {"law": "mass", "residual": 5e-7, "tolerance": 1e-6},
            ],
        }
        output_fvm = apply_conservation_gate(output_fvm)

        output_bem = {
            "schema_version": "1.0.0",
            "case_id": "wigley_iii_fn0.25",
            "solver_signature": {"name": "TensorBEM", "version": "2.1.0", "git_commit": "ghi9012"},
            "primary_quantities": [
                {"name": "C_T", "value": 0.00318, "unit": "dimensionless"},
            ],
            "conservation_residuals": [
                {"law": "mass", "residual": 2e-7, "tolerance": 1e-6},
            ],
        }
        output_bem = apply_conservation_gate(output_bem)

        # Step 6: Run mutual arbitration
        report = run_mutual_arbitration(
            "wigley_iii_fn0.25", "C_T",
            [output_lbm, output_fvm, output_bem],
        )
        report_dict = arbitration_report_to_dict(report)

        # Verify report
        assert report_dict["case_id"] == "wigley_iii_fn0.25"
        assert report_dict["quantity_name"] == "C_T"
        assert len(report_dict["entries"]) == 3
        assert len(report_dict["pair_results"]) == 3
        assert report_dict["consensus_value"] > 0

        # Verify all pairs have attribution
        for p in report_dict["pair_results"]:
            assert "attribution" in p
            assert len(p["attribution"]) > 0

        # Verify JSON serializable
        json_str = json.dumps(report_dict)
        assert len(json_str) > 0
