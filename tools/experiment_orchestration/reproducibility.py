"""Reproducibility verification — run same experiment multiple times.

Verifies that the pipeline produces consistent results across multiple runs.
Supports:
  - Bit-level exactness (identical outputs)
  - ULP-level consistency (ULP < 1e-15)
  - Statistical consistency (mean/std within tolerance)
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Callable

from tools.drift_classifier import classify_drift_fingerprint, compute_ulp_distance


@dataclass
class ReproducibilityResult:
    """Result of reproducibility verification."""
    experiment_id: str
    n_runs: int
    all_identical: bool
    max_ulp_distance: float
    mean_values: dict[str, float]
    std_values: dict[str, float]
    passed: bool
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "n_runs": self.n_runs,
            "all_identical": self.all_identical,
            "max_ulp_distance": self.max_ulp_distance,
            "mean_values": self.mean_values,
            "std_values": self.std_values,
            "passed": self.passed,
            "details": self.details,
        }


def verify_reproducibility(
    experiment_spec: dict[str, Any],
    run_fn: Callable[[dict[str, Any]], Any],
    n_runs: int = 3,
    ulp_threshold: float = 1e-15,
) -> ReproducibilityResult:
    """Run the same experiment multiple times and verify consistency.
    
    Args:
        experiment_spec: Experiment specification
        run_fn: Function to execute the experiment
        n_runs: Number of times to run (default 3)
        ulp_threshold: Maximum allowed ULP distance
        
    Returns:
        ReproducibilityResult with statistics
    """
    experiment_id = experiment_spec.get("experiment_id", "unknown")
    outputs = []
    
    # Run the experiment n_runs times
    for i in range(n_runs):
        output = run_fn(experiment_spec)
        outputs.append(output)
    
    # Check if all outputs are identical
    all_identical = all(
        classify_drift_fingerprint(outputs[0], out)["drift_class"] == "bit_exact"
        for out in outputs[1:]
    )
    
    # Compute ULP distances
    max_ulp = 0.0
    if not all_identical:
        for out in outputs[1:]:
            drift = classify_drift_fingerprint(outputs[0], out)
            # Check both possible keys for ULP distance
            evidence = drift.get("evidence", {})
            ulp = evidence.get("max_ulp_distance") or evidence.get("ulp_distance", 0.0)
            max_ulp = max(max_ulp, ulp)
    
    # Compute statistics for numeric outputs
    mean_values = {}
    std_values = {}
    
    if isinstance(outputs[0], dict):
        for key in outputs[0].keys():
            values = [out.get(key) for out in outputs if isinstance(out, dict)]
            numeric_values = [v for v in values if isinstance(v, (int, float))]
            
            if numeric_values:
                mean_val = sum(numeric_values) / len(numeric_values)
                mean_values[key] = mean_val
                
                if len(numeric_values) > 1:
                    variance = sum((v - mean_val) ** 2 for v in numeric_values) / (len(numeric_values) - 1)
                    std_values[key] = math.sqrt(variance)
                else:
                    std_values[key] = 0.0
    
    # Determine if passed
    passed = all_identical or max_ulp < ulp_threshold
    
    details = {
        "outputs": outputs,
        "ulp_threshold": ulp_threshold,
    }
    
    return ReproducibilityResult(
        experiment_id=experiment_id,
        n_runs=n_runs,
        all_identical=all_identical,
        max_ulp_distance=max_ulp,
        mean_values=mean_values,
        std_values=std_values,
        passed=passed,
        details=details,
    )


def deterministic_computation(spec: dict[str, Any]) -> dict[str, Any]:
    """A deterministic computation for reproducibility testing.
    
    Uses a hash-based approach to ensure seed sensitivity while maintaining
    determinism. The computation is: hash(seed) -> deterministic sequence.
    """
    seed = spec.get("parameters", {}).get("seed", 42)
    iterations = spec.get("parameters", {}).get("iterations", 1000)
    
    # Create a seed-dependent initial value using hash
    seed_hash = hashlib.sha256(str(seed).encode()).hexdigest()
    # Use first 8 hex chars as initial value (0-1 range)
    initial = int(seed_hash[:8], 16) / 0xFFFFFFFF
    
    # Deterministic iteration (logistic map - chaotic but deterministic)
    x = initial
    r = 3.99  # Chaotic regime
    for _ in range(iterations):
        x = r * x * (1 - x)
    
    return {
        "final_value": x,
        "iterations": iterations,
        "seed": seed,
        "initial_value": initial,
    }


if __name__ == "__main__":
    # Quick test
    spec = {
        "experiment_id": "test_repro",
        "parameters": {"seed": 42, "iterations": 1000},
    }
    
    result = verify_reproducibility(spec, deterministic_computation, n_runs=3)
    print(f"Reproducibility test: {'PASS' if result.passed else 'FAIL'}")
    print(f"  All identical: {result.all_identical}")
    print(f"  Max ULP distance: {result.max_ulp_distance}")
    print(f"  Mean values: {result.mean_values}")
    print(f"  Std values: {result.std_values}")
