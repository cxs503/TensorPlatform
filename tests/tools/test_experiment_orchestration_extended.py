"""Extended tests for experiment orchestration — reproducibility, capability integration, synthetic fixtures.

Covers:
  - Reproducibility verification (3 runs, bit-level consistency)
  - Capability ledger integration
  - Synthetic domain fixtures
  - End-to-end pipeline with all features
  - Zero domain semantics validation

All tests use synthetic data with generic terminology only.
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import pytest

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.experiment_orchestration import (
    ExperimentPipeline,
    PipelineConfig,
    verify_reproducibility,
    deterministic_computation,
    CapabilityLedger_Integrator,
    get_synthetic_experiment_1,
    get_synthetic_experiment_2,
    get_synthetic_experiment_3,
    get_synthetic_schema,
    save_synthetic_fixtures,
)


# ═══════════════════════════════════════════════════════════════════════
# Reproducibility Tests
# ═══════════════════════════════════════════════════════════════════════

class TestReproducibility:
    """Reproducibility verification tests."""

    def test_deterministic_computation_bit_exact(self):
        """Deterministic computation produces bit-exact results across 3 runs."""
        spec = {
            "experiment_id": "repro_test_001",
            "parameters": {"seed": 42, "iterations": 1000},
        }
        
        result = verify_reproducibility(spec, deterministic_computation, n_runs=3)
        
        assert result.passed is True
        assert result.all_identical is True
        assert result.max_ulp_distance == 0.0
        assert result.n_runs == 3
        assert "final_value" in result.mean_values

    def test_deterministic_computation_different_seeds(self):
        """Different seeds produce different results (sanity check)."""
        spec1 = {"experiment_id": "test1", "parameters": {"seed": 42, "iterations": 100}}
        spec2 = {"experiment_id": "test2", "parameters": {"seed": 99, "iterations": 100}}
        
        out1 = deterministic_computation(spec1)
        out2 = deterministic_computation(spec2)
        
        assert out1["final_value"] != out2["final_value"]

    def test_reproducibility_with_tolerance(self):
        """Reproducibility check with ULP tolerance."""
        counter = [0]
        
        def slightly_varying_fn(spec):
            """Function that varies slightly (within ULP threshold)."""
            counter[0] += 1
            base = 1.0
            # Add tiny variation (within ULP threshold)
            variation = counter[0] * 1e-16
            return {"value": base + variation}
        
        spec = {"experiment_id": "repro_test_002"}
        result = verify_reproducibility(spec, slightly_varying_fn, n_runs=3, ulp_threshold=1e-15)
        
        # Should pass because variation is within ULP threshold
        assert result.passed is True
        assert result.max_ulp_distance < 1e-15

    def test_reproducibility_failure_on_large_drift(self):
        """Reproducibility fails when drift exceeds threshold."""
        counter = [0]
        
        def drifting_fn(spec):
            """Function that drifts significantly."""
            counter[0] += 1
            return {"value": 1.0 + counter[0] * 0.01}  # 1% drift per run
        
        spec = {"experiment_id": "repro_test_003"}
        result = verify_reproducibility(spec, drifting_fn, n_runs=3, ulp_threshold=1e-15)
        
        # Should fail because drift is large
        assert result.passed is False
        assert result.all_identical is False

    def test_reproducibility_statistics(self):
        """Reproducibility result includes mean and std statistics."""
        spec = {
            "experiment_id": "repro_test_004",
            "parameters": {"seed": 42, "iterations": 500},
        }
        
        result = verify_reproducibility(spec, deterministic_computation, n_runs=3)
        
        assert "mean_values" in result.to_dict()
        assert "std_values" in result.to_dict()
        assert "final_value" in result.mean_values
        # For deterministic computation, std should be 0
        assert result.std_values["final_value"] == 0.0


# ═══════════════════════════════════════════════════════════════════════
# Capability Ledger Integration Tests
# ═══════════════════════════════════════════════════════════════════════

class TestCapabilityIntegration:
    """Capability ledger integration tests."""

    def test_capability_update_from_report(self, tmp_path):
        """Capability ledger is updated from a report."""
        from tools.experiment_orchestration import ReportPayload
        from datetime import datetime, timezone
        
        ledger_path = str(tmp_path / "test_ledger.json")
        
        report = ReportPayload(
            report_id="test_report_001",
            experiment_id="synthetic_exp_001",
            content={
                "sections": {
                    "verification": {"passed": True, "verdict": "pass", "drift_class": "bit_exact"},
                    "execution": {"exit_code": 0, "duration_sec": 1.5},
                    "baseline_comparison": {"available": True, "drift_class": "bit_exact"},
                    "audit_chain": [{"stage": "prereg", "sha256": "abc123"}],
                }
            },
            artifacts=[],
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        
        integrator = CapabilityLedger_Integrator(ledger_path=ledger_path)
        updates = integrator.update_from_report(report)
        
        assert len(updates) > 0
        assert all(u.experiment_id == "synthetic_exp_001" for u in updates)
        assert all(u.report_id == "test_report_001" for u in updates)
        
        # Check ledger file was created
        assert Path(ledger_path).exists()
        
        # Check ledger content
        with open(ledger_path, "r") as f:
            ledger = json.load(f)
        assert len(ledger) == len(updates)

    def test_capability_evidence_extraction(self, tmp_path):
        """Capability evidence is correctly extracted from report."""
        from tools.experiment_orchestration import ReportPayload
        from datetime import datetime, timezone
        
        ledger_path = str(tmp_path / "test_ledger_2.json")
        
        report = ReportPayload(
            report_id="test_report_002",
            experiment_id="synthetic_exp_002",
            content={
                "sections": {
                    "verification": {
                        "passed": True,
                        "verdict": "pass",
                        "drift_class": "ulp_level",
                        "criteria_passed": 3,
                    },
                    "execution": {"exit_code": 0, "duration_sec": 2.5},
                    "baseline_comparison": {"available": False},
                    "audit_chain": [
                        {"stage": "prereg", "sha256": "hash1"},
                        {"stage": "output", "sha256": "hash2"},
                    ],
                }
            },
            artifacts=[],
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        
        integrator = CapabilityLedger_Integrator(ledger_path=ledger_path)
        updates = integrator.update_from_report(report)
        
        # Check specific evidence types
        evidence_types = {u.evidence_type for u in updates}
        assert "verification_passed" in evidence_types
        assert "drift_class" in evidence_types
        assert "exit_code" in evidence_types
        assert "duration_sec" in evidence_types
        assert "audit_chain_length" in evidence_types

    def test_capability_ledger_append(self, tmp_path):
        """Capability ledger appends (not overwrites) on multiple updates."""
        from tools.experiment_orchestration import ReportPayload
        from datetime import datetime, timezone
        
        ledger_path = str(tmp_path / "test_ledger_3.json")
        integrator = CapabilityLedger_Integrator(ledger_path=ledger_path)
        
        # First update
        report1 = ReportPayload(
            report_id="report_1",
            experiment_id="exp_1",
            content={"sections": {"verification": {"passed": True, "verdict": "pass"}}},
            artifacts=[],
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        updates1 = integrator.update_from_report(report1)
        
        # Second update
        report2 = ReportPayload(
            report_id="report_2",
            experiment_id="exp_2",
            content={"sections": {"verification": {"passed": False, "verdict": "fail"}}},
            artifacts=[],
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        updates2 = integrator.update_from_report(report2)
        
        # Check ledger has both
        with open(ledger_path, "r") as f:
            ledger = json.load(f)
        
        assert len(ledger) == len(updates1) + len(updates2)


# ═══════════════════════════════════════════════════════════════════════
# Synthetic Fixtures Tests
# ═══════════════════════════════════════════════════════════════════════

class TestSyntheticFixtures:
    """Synthetic domain fixtures tests."""

    def test_synthetic_experiment_1_structure(self):
        """Synthetic experiment 1 has correct structure."""
        exp = get_synthetic_experiment_1()
        
        assert "experiment_id" in exp
        assert "domain" in exp
        assert "parameters" in exp
        assert "acceptance_criteria" in exp
        assert exp["domain"] == "synthetic_domain_alpha"

    def test_synthetic_experiment_2_structure(self):
        """Synthetic experiment 2 has correct structure."""
        exp = get_synthetic_experiment_2()
        
        assert "experiment_id" in exp
        assert "domain" in exp
        assert exp["domain"] == "synthetic_domain_beta"

    def test_synthetic_experiment_3_deterministic(self):
        """Synthetic experiment 3 is deterministic."""
        exp = get_synthetic_experiment_3()
        
        assert "experiment_id" in exp
        assert exp["domain"] == "synthetic_domain_gamma"
        assert "reference_values" in exp
        # Should have a deterministic reference value
        assert "final_value" in exp["reference_values"]

    def test_synthetic_schema_valid(self):
        """Synthetic schema is valid JSON schema."""
        schema = get_synthetic_schema()
        
        assert schema["type"] == "object"
        assert "required" in schema
        assert "properties" in schema
        assert "experiment_id" in schema["required"]

    def test_save_synthetic_fixtures(self, tmp_path):
        """Synthetic fixtures can be saved to disk."""
        output_dir = str(tmp_path / "fixtures")
        paths = save_synthetic_fixtures(output_dir)
        
        assert len(paths) == 4  # 3 experiments + 1 schema
        assert all(Path(p).exists() for p in paths.values())
        
        # Check content
        with open(paths["experiment_1"], "r") as f:
            exp1 = json.load(f)
        assert exp1["experiment_id"] == "synthetic_exp_001"

    def test_synthetic_fixtures_no_domain_terms(self):
        """Synthetic fixtures contain no real domain terms."""
        # List of real domain terms that should NOT appear
        forbidden_terms = [
            "hull", "resistance", "propulsion", "seakeeping", "maneuvering",
            "stability", "structure", "vibration", "noise", "ice",
            "ship", "vessel", "boat", "water", "wave", "wind",
        ]
        
        exp1 = json.dumps(get_synthetic_experiment_1())
        exp2 = json.dumps(get_synthetic_experiment_2())
        exp3 = json.dumps(get_synthetic_experiment_3())
        
        all_text = (exp1 + exp2 + exp3).lower()
        
        for term in forbidden_terms:
            assert term not in all_text, f"Forbidden domain term '{term}' found in synthetic fixtures"


# ═══════════════════════════════════════════════════════════════════════
# End-to-End Pipeline Tests
# ═══════════════════════════════════════════════════════════════════════

class TestEndToEndPipeline:
    """End-to-end pipeline tests with all features."""

    def test_full_pipeline_with_reproducibility(self, tmp_path):
        """Full pipeline includes reproducibility verification."""
        output_dir = str(tmp_path / "artifacts")
        config = PipelineConfig(output_dir=output_dir)
        pipeline = ExperimentPipeline(config=config)
        
        spec = {
            "experiment_id": "e2e_repro_test",
            "parameters": {"seed": 42, "iterations": 500},
            "python_code": """
import math
x = 42.0
for _ in range(500):
    x = math.cos(x)
result = {"final_value": x}
""",
        }
        
        result = pipeline.execute(spec)
        
        assert result.status in ["reported", "verified"]
        assert result.report is not None
        
        # Now verify reproducibility
        def run_fn(s):
            r = pipeline.execute(s)
            return r.run_result.output if r.run_result else None
        
        repro_result = verify_reproducibility(spec, run_fn, n_runs=3)
        assert repro_result.passed is True

    def test_full_pipeline_with_capability_integration(self, tmp_path):
        """Full pipeline integrates with capability ledger."""
        output_dir = str(tmp_path / "artifacts")
        ledger_path = str(tmp_path / "ledger.json")
        
        config = PipelineConfig(output_dir=output_dir)
        pipeline = ExperimentPipeline(config=config)
        
        spec = get_synthetic_experiment_1()
        spec["simulate_output"] = {"convergence_rate": 2.0, "final_error": 5e-5}
        
        result = pipeline.execute(spec)
        
        assert result.report is not None
        
        # Integrate with capability ledger
        integrator = CapabilityLedger_Integrator(ledger_path=ledger_path)
        updates = integrator.update_from_report(result.report)
        
        assert len(updates) > 0
        assert Path(ledger_path).exists()

    def test_full_pipeline_with_synthetic_fixtures(self, tmp_path):
        """Full pipeline works with synthetic fixtures."""
        output_dir = str(tmp_path / "artifacts")
        config = PipelineConfig(output_dir=output_dir)
        pipeline = ExperimentPipeline(config=config)
        
        # Use all 3 synthetic experiments
        for exp_fn in [get_synthetic_experiment_1, get_synthetic_experiment_2, get_synthetic_experiment_3]:
            spec = exp_fn()
            spec["simulate_output"] = spec.get("expected_outputs", {})
            
            result = pipeline.execute(spec)
            
            assert result.status in ["reported", "verified"]
            assert result.report is not None


# ═══════════════════════════════════════════════════════════════════════
# Zero Domain Semantics Tests (Extended)
# ═══════════════════════════════════════════════════════════════════════

class TestZeroDomainSemanticsExtended:
    """Extended zero domain semantics tests."""

    def test_reproducibility_module_no_domain_terms(self):
        """Reproducibility module contains no domain terms."""
        import tools.experiment_orchestration.reproducibility as repro_module
        
        source_file = Path(repro_module.__file__)
        source_code = source_file.read_text().lower()
        
        forbidden_terms = ["hull", "resistance", "propulsion", "seakeeping", "ship"]
        for term in forbidden_terms:
            assert term not in source_code, f"Domain term '{term}' found in reproducibility module"

    def test_capability_integration_no_domain_terms(self):
        """Capability integration module contains no domain terms."""
        import tools.experiment_orchestration.capability_integration as cap_module
        
        source_file = Path(cap_module.__file__)
        source_code = source_file.read_text().lower()
        
        forbidden_terms = ["hull", "resistance", "propulsion", "seakeeping", "ship"]
        for term in forbidden_terms:
            assert term not in source_code, f"Domain term '{term}' found in capability integration module"

    def test_synthetic_fixtures_no_domain_terms(self):
        """Synthetic fixtures module contains no domain terms."""
        import tools.experiment_orchestration.synthetic_fixtures as fix_module
        
        source_file = Path(fix_module.__file__)
        source_code = source_file.read_text().lower()
        
        forbidden_terms = ["hull", "resistance", "propulsion", "seakeeping", "ship"]
        for term in forbidden_terms:
            assert term not in source_code, f"Domain term '{term}' found in synthetic fixtures module"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
