"""
KCS Bow Form Parameterization (m546 interface mock)

Implements a 8-parameter deformation model for KCS bow section.
Parameters control bow shape modifications while maintaining hull continuity.

Interface contract (m546):
- Input: 8-dimensional parameter vector in normalized space [-1, 1]
- Output: geometry dict with verification flags (watertight, displacement)
- Constraints: geometric validity checks (watertight, displacement bounds)

m1610: besides the verification flags the geometry now carries the *hull
particulars* the canonical Holtrop-1984 kernel needs (``cm``, ``cwp``,
``lcb_pct``, ``vol``, ``abt``, ``hb``, ``at``) so that the resistance objective
can be evaluated by the canonical kernel instead of by hand-fitted multipliers.
``cm``/``cwp`` are taken from the canonical kernel's own estimates
(:mod:`optimizer.interfaces.canonical_bridge`) - never re-derived here.
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


# --- Hull particulars for the canonical Holtrop-1984 kernel (m1610) ---------
#: Bulb transverse area at FP as a fraction of the midship section area
#: (KCS-class container bow, Abt/Am ~ 0.09).
BULB_AREA_RATIO = 0.09
#: Bulb centre height above the keel as a fraction of the draft.
BULB_HEIGHT_RATIO = 0.45
#: KCS published LCB, percent of L aft of midship (negative = forward).
LCB_BASELINE_PCT = -0.7

#: Bow-parameter -> hull-particular mapping.  Only pathways that actually exist in
#: Holtrop-1984 are mapped; anything else is left inert and documented, instead of
#: being faked with an ad-hoc resistance multiplier (the m1601 finding).
PARAMETER_PATHWAYS = {
    "bow_entrance_angle": "Cwp x (1 + 0.020 p0): blunter entrance -> fuller waterplane",
    "forward_shoulder_curvature": "Cwp x (1 + 0.010 p1)",
    "waterline_flare_fp": "Cwp x (1 + 0.015 p2)",
    "deck_camber_bow": "no calm-water resistance pathway in Holtrop-1984 (inert)",
    "bulb_volume_scale": "Abt = Abt0 x (1 + 0.30 p4)^(2/3): area ~ volume^(2/3)",
    "bulb_vertical_pos": "hb = T x (0.45 + 0.08 p5)",
    "stem_rake_angle": "lcb_pct -= 0.25 p6 (rake shifts the waterline entry)",
    "forward_body_length_frac": "lcb_pct -= 1.5 p7 (longer forebody -> LCB forward)",
}


def holtrop_particulars(params_normalized: np.ndarray, Cb: float) -> Dict[str, Any]:
    """Hull particulars accepted by the canonical Holtrop-1984 kernel.

    ``cm`` / ``cwp`` come from the canonical kernel's own estimates through
    :mod:`optimizer.interfaces.canonical_bridge` (single source of truth); the bow
    parameters act through the pathways listed in :data:`PARAMETER_PATHWAYS`.

    The immersed transom area is 0: the KCS transom is above the waterline at the
    design Froude number, so no transom resistance term applies.
    """
    from .canonical_bridge import estimate_cm, estimate_cwp

    L = KCS_BASELINE["L"]
    B = KCS_BASELINE["B"]
    T = KCS_BASELINE["T"]
    p = np.asarray(params_normalized, dtype=float)

    cm = float(np.clip(estimate_cm(Cb), 0.85, 0.999))
    cwp_baseline = float(estimate_cwp(Cb))
    cwp = cwp_baseline * (1.0 + 0.020 * p[0] + 0.010 * p[1] + 0.015 * p[2])
    cwp = float(np.clip(cwp, 0.60, 0.90))
    lcb_pct = LCB_BASELINE_PCT - 0.25 * p[6] - 1.5 * p[7]

    am = B * T * cm
    abt_baseline = BULB_AREA_RATIO * am
    abt = abt_baseline * (1.0 + 0.30 * p[4]) ** (2.0 / 3.0)
    hb = T * (BULB_HEIGHT_RATIO + 0.08 * p[5])

    return {
        "L": L, "B": B, "T": T,
        "Cb": float(Cb), "Cm": cm, "Cwp": cwp, "Cp": float(Cb / cm),
        "lcb_pct": float(lcb_pct),
        "vol": float(Cb * L * B * T),
        "abt": float(abt), "hb": float(hb), "at": 0.0,
        "cwp_baseline": cwp_baseline, "abt_baseline": float(abt_baseline),
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
    
    parts = holtrop_particulars(params_normalized, Cb)

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
        # canonical Holtrop-1984 hull particulars (m1610)
        "Cm": parts["Cm"], "Cwp": parts["Cwp"], "Cp": parts["Cp"],
        "lcb_pct": parts["lcb_pct"], "vol": parts["vol"],
        "abt": parts["abt"], "hb": parts["hb"], "at": parts["at"],
        "holtrop_particulars": parts,
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
