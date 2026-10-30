"""
KCS Bow Form Parameterization (m546 interface mock)

Implements a 8-parameter deformation model for KCS bow section.
Parameters control bow shape modifications while maintaining hull continuity.

Interface contract (m546):
- Input: 8-dimensional parameter vector in normalized space [-1, 1]
- Output: geometry dict with verification flags (watertight, displacement)
- Constraints: geometric validity checks (watertight, displacement bounds)
"""
import numpy as np
from typing import Dict, Any, Tuple

# KCS baseline parameters (from literature)
KCS_BASELINE = {
    "L": 7.278,      # Length overall [m] (model scale)
    "B": 1.019,      # Beam [m]
    "T": 0.342,      # Draft [m]
    "Cb": 0.651,     # Block coefficient
    "Fn_design": 0.26  # Design Froude number
}

# Parameter bounds (normalized space [-1, 1])
PARAM_BOUNDS = np.array([
    [-1.0, 1.0],  # p0: bow entrance angle modification
    [-1.0, 1.0],  # p1: forward shoulder curvature
    [-1.0, 1.0],  # p2: waterline flare at FP
    [-1.0, 1.0],  # p3: deck camber near bow
    [-1.0, 1.0],  # p4: bulbous bow volume scaling
    [-1.0, 1.0],  # p5: bulb vertical position
    [-1.0, 1.0],  # p6: stem rake angle
    [-1.0, 1.0],  # p7: forward body length distribution
])

# Displacement bounds (±5% of baseline)
DISP_BOUNDS = (0.95, 1.05)


def decode_parameters(params_normalized: np.ndarray) -> Dict[str, float]:
    """
    Decode normalized parameters to physical deformation values.
    
    Args:
        params_normalized: 8-dim array in [-1, 1]
    
    Returns:
        Dictionary of physical deformation parameters
    """
    assert len(params_normalized) == 8, "Expected 8 parameters"
    assert np.all(params_normalized >= -1.0) and np.all(params_normalized <= 1.0), \
        "Parameters must be in [-1, 1]"
    
    # Map to physical ranges (example scaling)
    return {
        "bow_entrance_angle_delta": params_normalized[0] * 5.0,      # ±5 deg
        "forward_shoulder_curvature": params_normalized[1] * 0.15,   # ±15% curvature
        "waterline_flare_fp": params_normalized[2] * 0.10,           # ±10% flare
        "deck_camber_bow": params_normalized[3] * 0.05,              # ±5% camber
        "bulb_volume_scale": 1.0 + params_normalized[4] * 0.30,      # 0.7-1.3x
        "bulb_vertical_pos": params_normalized[5] * 0.08,            # ±8% T
        "stem_rake_delta": params_normalized[6] * 8.0,               # ±8 deg
        "forward_body_length_frac": 0.5 + params_normalized[7] * 0.15  # 0.35-0.65
    }


def generate_geometry(params_normalized: np.ndarray, seed: int = None) -> Dict[str, Any]:
    """
    Generate hull geometry from parameters (m546 interface).
    
    This is a MOCK implementation that simulates geometry generation.
    Real implementation will call m546 geometry generator.
    
    Args:
        params_normalized: 8-dim parameter vector in [-1, 1]
        seed: Random seed for reproducibility (not used in deterministic mock)
    
    Returns:
        Geometry dictionary with:
        - params: decoded physical parameters
        - volume: displaced volume ratio (V/V_baseline)
        - watertight: bool, geometry validity flag
        - valid: bool, overall validity flag
        - Cb: block coefficient
    """
    if seed is not None:
        np.random.seed(seed)
    
    phys_params = decode_parameters(params_normalized)
    
    # Mock geometry validation (deterministic based on parameters)
    # Simulate realistic constraints:
    # - Extreme bulb volume changes may break watertight
    # - Extreme stem rake may cause self-intersection
    
    bulb_scale = phys_params["bulb_volume_scale"]
    stem_rake = phys_params["stem_rake_delta"]
    
    # Watertight check (mock logic)
    watertight = (bulb_scale > 0.75) and (abs(stem_rake) < 7.0)
    
    # Displacement calculation (mock: linear combination of parameters)
    # Baseline volume ratio = 1.0
    volume_ratio = 1.0 + (
        0.05 * params_normalized[0] +   # bow entrance
        0.03 * params_normalized[1] +   # shoulder
        0.08 * params_normalized[4] +   # bulb volume (dominant)
        0.02 * params_normalized[6]     # stem rake
    )
    
    # Block coefficient (mock: slight variation from baseline)
    Cb = KCS_BASELINE["Cb"] * (1.0 + 0.03 * params_normalized[4])
    
    return {
        "params": phys_params,
        "params_normalized": params_normalized.tolist(),
        "volume_ratio": volume_ratio,
        "watertight": watertight,
        "valid": watertight and (DISP_BOUNDS[0] <= volume_ratio <= DISP_BOUNDS[1]),
        "Cb": Cb,
        "L": KCS_BASELINE["L"],
        "B": KCS_BASELINE["B"],
        "T": KCS_BASELINE["T"],
    }


def check_geometric_constraints(geom: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Check geometric constraints for optimization feasibility.
    
    Args:
        geom: Geometry dictionary from generate_geometry
    
    Returns:
        (is_valid, reason) tuple
    """
    if not geom["watertight"]:
        return False, "Geometry not watertight"
    
    if not (DISP_BOUNDS[0] <= geom["volume_ratio"] <= DISP_BOUNDS[1]):
        return False, f"Displacement ratio {geom['volume_ratio']:.3f} out of bounds {DISP_BOUNDS}"
    
    return True, "OK"


def get_parameter_names() -> list:
    """Return parameter names for reporting."""
    return [
        "bow_entrance_angle",
        "forward_shoulder_curvature",
        "waterline_flare_fp",
        "deck_camber_bow",
        "bulb_volume_scale",
        "bulb_vertical_pos",
        "stem_rake_angle",
        "forward_body_length_frac"
    ]
