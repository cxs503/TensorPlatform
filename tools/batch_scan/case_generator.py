#!/usr/bin/env python3
"""Generate YAML case list for batch scan execution.

Generates ≥100 cases with varying parameters for ship performance calculations.
Cases include resistance, stability, and propeller open water calculations.
"""
import yaml
import random
from pathlib import Path
from typing import List, Dict, Any

def generate_resistance_cases(n: int = 40) -> List[Dict[str, Any]]:
    """Generate resistance calculation cases with varying speed and hull parameters."""
    cases = []
    for i in range(n):
        L = round(random.uniform(100, 300), 1)
        B = round(random.uniform(15, 50), 1)
        T = round(random.uniform(5, 15), 2)
        V = round(random.uniform(8, 22), 1)
        Cb = round(random.uniform(0.55, 0.85), 3)
        case = {
            "case_id": f"resistance_{i+1:03d}",
            "category": "resistance",
            "description": f"Resistance calculation case {i+1}",
            "parameters": {
                "ship_type": random.choice(["container", "tanker", "bulk_carrier", "frigate"]),
                "length_m": L,
                "beam_m": B,
                "draft_m": T,
                "speed_kn": V,
                "block_coefficient": Cb,
            },
            "expected_status": "success",
            "criteria": {
                "resistance_kn": {"min": 1, "max": 50000},
            }
        }
        cases.append(case)
    return cases

def generate_stability_cases(n: int = 40) -> List[Dict[str, Any]]:
    """Generate stability calculation cases with varying loading conditions."""
    cases = []
    for i in range(n):
        L = round(random.uniform(100, 300), 1)
        B = round(random.uniform(15, 50), 1)
        T = round(random.uniform(5, 15), 2)
        # Ensure KG is below KM for positive GM (stable ships)
        KB = T * 0.5
        BM = B ** 2 / (12 * T)
        KM = KB + BM
        KG = round(random.uniform(3, min(KM - 0.5, 12)), 2)
        case = {
            "case_id": f"stability_{i+1:03d}",
            "category": "stability",
            "description": f"Stability calculation case {i+1}",
            "parameters": {
                "ship_type": random.choice(["container", "tanker", "bulk_carrier", "frigate"]),
                "length_m": L,
                "beam_m": B,
                "draft_m": T,
                "kg_m": KG,
                "heel_angle_deg": round(random.uniform(0, 15), 1),
                "loading_condition": random.choice(["full_load", "ballast", "partial"]),
            },
            "expected_status": "success",
            "criteria": {
                "gm_m": {"min": -5.0, "max": 20.0},
                "displacement_t": {"min": 500, "max": 500000},
            }
        }
        cases.append(case)
    return cases

def generate_propeller_cases(n: int = 30) -> List[Dict[str, Any]]:
    """Generate propeller open water calculation cases."""
    cases = []
    for i in range(n):
        case = {
            "case_id": f"propeller_{i+1:03d}",
            "category": "propeller",
            "description": f"Propeller open water case {i+1}",
            "parameters": {
                "diameter_m": round(random.uniform(3, 8), 2),
                "pitch_ratio": round(random.uniform(0.6, 1.4), 2),
                "blade_area_ratio": round(random.uniform(0.3, 0.8), 2),
                "blade_count": random.choice([4, 5, 6, 7]),
                "rpm": round(random.uniform(60, 150), 0),
                "advance_speed_kn": round(random.uniform(8, 18), 1),
            },
            "expected_status": "success",
            "criteria": {
                "thrust_coefficient": {"min": 0.0, "max": 1.0},
                "torque_coefficient": {"min": 0.0, "max": 0.5},
                "efficiency": {"min": 0.0, "max": 1.0},
            }
        }
        cases.append(case)
    return cases

def generate_geometry_cases(n: int = 15) -> List[Dict[str, Any]]:
    """Generate geometry verification cases."""
    cases = []
    for i in range(n):
        case = {
            "case_id": f"geometry_{i+1:03d}",
            "category": "geometry",
            "description": f"Geometry verification case {i+1}",
            "parameters": {
                "ship_type": random.choice(["container", "tanker", "bulk_carrier", "frigate"]),
                "length_m": round(random.uniform(100, 300), 1),
                "beam_m": round(random.uniform(15, 50), 1),
                "draft_m": round(random.uniform(5, 15), 2),
                "block_coefficient": round(random.uniform(0.55, 0.85), 3),
            },
            "expected_status": "success",
            "criteria": {
                "volume_displacement_m3": {"min": 100, "max": 500000},
                "wetted_surface_m2": {"min": 500, "max": 100000},
            }
        }
        cases.append(case)
    return cases

def generate_failure_cases(n: int = 5) -> List[Dict[str, Any]]:
    """Generate cases expected to fail (for testing failure handling)."""
    cases = []
    for i in range(n):
        case = {
            "case_id": f"failure_{i+1:03d}",
            "category": "resistance",
            "description": f"Intentional failure case {i+1} - invalid parameters",
            "parameters": {
                "ship_type": "invalid_type",
                "length_m": -100,  # Invalid negative length
                "beam_m": 0,  # Invalid zero beam
                "draft_m": -5,  # Invalid negative draft
                "speed_kn": -10,  # Invalid negative speed
            },
            "expected_status": "failure",
            "criteria": {
                "should_fail": True,
            }
        }
        cases.append(case)
    return cases

def main():
    """Generate complete case list YAML."""
    random.seed(42)  # Reproducible
    
    all_cases = []
    all_cases.extend(generate_resistance_cases(40))
    all_cases.extend(generate_stability_cases(40))
    all_cases.extend(generate_propeller_cases(30))
    all_cases.extend(generate_geometry_cases(15))
    all_cases.extend(generate_failure_cases(5))
    
    # Shuffle to mix categories
    random.shuffle(all_cases)
    
    case_list = {
        "metadata": {
            "name": "PC-1 千工况批量执行器 - 冒烟测试集",
            "version": "1.0",
            "description": "130 cases covering resistance, stability, propeller, and geometry calculations",
            "total_cases": len(all_cases),
            "generated_at": "2026-10-31",
            "owner": "agent/m642",
        },
        "execution_config": {
            "max_concurrent": 10,
            "timeout_per_case_s": 30,
            "retry_on_failure": False,
            "continue_on_failure": True,
        },
        "cases": all_cases,
    }
    
    output_path = Path(__file__).parent / "case_list.yaml"
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(case_list, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    
    print(f"Generated {len(all_cases)} cases to {output_path}")
    print(f"  - Resistance: 40 cases")
    print(f"  - Stability: 40 cases")
    print(f"  - Propeller: 30 cases")
    print(f"  - Geometry: 15 cases")
    print(f"  - Failure (intentional): 5 cases")

if __name__ == "__main__":
    main()
