"""Synthetic Domain Fixtures — zero domain semantics test data.

These fixtures use completely synthetic/generic terminology to verify
the experiment orchestration pipeline without any real domain semantics.

All names are generic: synthetic_object_001, generic_solver_A, etc.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def get_synthetic_experiment_1() -> dict[str, Any]:
    """Synthetic experiment 1: basic convergence study.
    
    Uses generic terminology only - no real physics domain.
    """
    return {
        "experiment_id": "synthetic_exp_001",
        "domain": "synthetic_domain_alpha",
        "description": "Generic convergence study for synthetic_object_001",
        "parameters": {
            "mesh_sizes": [10, 20, 40, 80],
            "solver": "generic_solver_A",
            "tolerance": 1e-6,
        },
        "expected_outputs": {
            "convergence_rate": 2.0,
            "final_error": 1e-4,
        },
        "acceptance_criteria": {
            "convergence_rate": {"min": 1.9, "max": 2.1},
            "final_error": {"max": 1e-3},
        },
        "reference_values": {
            "convergence_rate": 2.0,
            "final_error": 5e-5,
        },
    }


def get_synthetic_experiment_2() -> dict[str, Any]:
    """Synthetic experiment 2: parameter sweep.
    
    Uses generic terminology only - no real physics domain.
    """
    return {
        "experiment_id": "synthetic_exp_002",
        "domain": "synthetic_domain_beta",
        "description": "Parameter sweep for generic_config_X",
        "parameters": {
            "param_range": [0.1, 0.5, 1.0, 2.0],
            "iterations": 100,
        },
        "expected_outputs": {
            "optimal_param": 1.0,
            "objective_value": 0.95,
        },
        "acceptance_criteria": {
            "optimal_param": {"min": 0.9, "max": 1.1},
            "objective_value": {"min": 0.9},
        },
        "reference_values": {
            "optimal_param": 1.0,
            "objective_value": 0.95,
        },
    }


def get_synthetic_experiment_3() -> dict[str, Any]:
    """Synthetic experiment 3: reproducibility test.
    
    Deterministic computation to verify bit-level reproducibility.
    """
    return {
        "experiment_id": "synthetic_exp_003",
        "domain": "synthetic_domain_gamma",
        "description": "Deterministic computation for reproducibility verification",
        "parameters": {
            "seed": 42,
            "iterations": 1000,
        },
        "expected_outputs": {
            "final_value": 0.7390851332151607,  # deterministic result
        },
        "acceptance_criteria": {
            "final_value": {"tolerance": 1e-15},  # bit-level
        },
        "reference_values": {
            "final_value": 0.7390851332151607,
        },
    }


def get_synthetic_schema() -> dict[str, Any]:
    """JSON schema for synthetic experiments."""
    return {
        "type": "object",
        "required": ["experiment_id", "domain", "parameters"],
        "properties": {
            "experiment_id": {"type": "string"},
            "domain": {"type": "string"},
            "description": {"type": "string"},
            "parameters": {"type": "object"},
            "expected_outputs": {"type": "object"},
            "acceptance_criteria": {"type": "object"},
            "reference_values": {"type": "object"},
        },
        "additionalProperties": False,
    }


def save_synthetic_fixtures(output_dir: str) -> dict[str, Path]:
    """Save all synthetic fixtures to disk.
    
    Returns:
        Dictionary mapping fixture name to file path.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    fixtures = {
        "experiment_1": get_synthetic_experiment_1(),
        "experiment_2": get_synthetic_experiment_2(),
        "experiment_3": get_synthetic_experiment_3(),
        "schema": get_synthetic_schema(),
    }
    
    paths = {}
    for name, data in fixtures.items():
        file_path = output_path / f"{name}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        paths[name] = file_path
    
    return paths


if __name__ == "__main__":
    # Quick test
    import sys
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/synthetic_fixtures"
    paths = save_synthetic_fixtures(output_dir)
    print(f"Saved {len(paths)} fixtures to {output_dir}")
    for name, path in paths.items():
        print(f"  {name}: {path}")
