"""
Resistance Evaluation Pipeline (m547 interface mock)

Implements total resistance coefficient calculation at design Fn.
Uses Holtrop-Mennen method for calm water resistance with parameter sensitivity.

Interface contract (m547):
- Input: geometry dict (from m546) + ship speed
- Output: resistance components and total Ct
- Constraints: Holtrop calibration domain check

Reference: Holtrop J, Mennen GGA. "An approximate power prediction method",
           International Shipbuilding Progress, 1982.
"""
import numpy as np
from typing import Dict, Any, Tuple

# Physical constants
RHO_WATER = 1025.0  # Seawater density [kg/m³]
NU_WATER = 1.19e-6  # Kinematic viscosity [m²/s] (15°C seawater)

# 证据等级（m1601 复核：本实现等价于裸 ITTC-57 摩擦线，见
# docs/verification/optimizer-objective-mock-audit.md）。下游消费者必须读这两个字段，
# 不得把本目标函数的结果当 REAL 引用。
EVIDENCE = {
    "level": "MOCK",
    "source": "optimizer/interfaces/resistance_eval.py (m547 mock): ITTC-57 friction line only; "
              "residuary/wave terms are exponential-floored to ~0, 相关补贴 Ca 量纲错误; "
              "audit artifact artifacts/optimizer_objective_audit/optimizer_objective_audit.json",
}
G = 9.81  # Gravity [m/s²]

# Holtrop calibration domain (validity limits)
# Adjusted for KCS model scale (L=7.278m, B=1.019m, T=0.342m)
HOLTROP_DOMAIN = {
    "Cb": (0.55, 0.75),      # Block coefficient
    "L_B": (3.0, 8.0),       # Length/Beam ratio (expanded for KCS)
    "B_T": (2.0, 4.0),       # Beam/Draft ratio
    "Fn": (0.10, 0.35),      # Froude number
}


def check_holtrop_domain(geom: Dict[str, Any], Fn: float) -> Tuple[bool, str]:
    """
    Check if geometry and speed are within Holtrop calibration domain.
    
    Args:
        geom: Geometry dictionary
        Fn: Froude number
    
    Returns:
        (in_domain, reason) tuple
    """
    L = geom["L"]
    B = geom["B"]
    T = geom["T"]
    Cb = geom["Cb"]
    
    L_B = L / B
    B_T = B / T
    
    checks = [
        (HOLTROP_DOMAIN["Cb"][0] <= Cb <= HOLTROP_DOMAIN["Cb"][1],
         f"Cb={Cb:.3f} out of [{HOLTROP_DOMAIN['Cb']}]"),
        (HOLTROP_DOMAIN["L_B"][0] <= L_B <= HOLTROP_DOMAIN["L_B"][1],
         f"L/B={L_B:.2f} out of [{HOLTROP_DOMAIN['L_B']}]"),
        (HOLTROP_DOMAIN["B_T"][0] <= B_T <= HOLTROP_DOMAIN["B_T"][1],
         f"B/T={B_T:.2f} out of [{HOLTROP_DOMAIN['B_T']}]"),
        (HOLTROP_DOMAIN["Fn"][0] <= Fn <= HOLTROP_DOMAIN["Fn"][1],
         f"Fn={Fn:.3f} out of [{HOLTROP_DOMAIN['Fn']}]"),
    ]
    
    for valid, reason in checks:
        if not valid:
            return False, reason
    
    return True, "Within Holtrop domain"


def holtrop_resistance(geom: Dict[str, Any], V_ms: float) -> Dict[str, float]:
    """
    Calculate resistance using Holtrop-Mennen method with parameter sensitivity.
    
    Args:
        geom: Geometry dictionary with L, B, T, Cb, volume_ratio, params
        V_ms: Ship speed [m/s]
    
    Returns:
        Dictionary with resistance components [N] and coefficients
    """
    L = geom["L"]
    B = geom["B"]
    T = geom["T"]
    Cb = geom["Cb"]
    volume_ratio = geom["volume_ratio"]
    params = geom.get("params", {})
    
    # Derived quantities
    # Wetted area approximation (Holtrop formula)
    S_wet = L * (2 * T + B) * (0.453 + 0.4425 * Cb - 0.2862 * 0.57 - 
                                 0.003467 * B / T + 0.3696 * 0.65)
    
    # Froude number
    Fn = V_ms / np.sqrt(G * L)
    
    # Reynolds number
    Re = V_ms * L / NU_WATER
    
    # 1. Frictional resistance (ITTC 1957)
    Cf = 0.075 / (np.log10(Re) - 2) ** 2
    Rf = 0.5 * RHO_WATER * V_ms**2 * S_wet * Cf
    
    # 2. Residuary resistance (Holtrop simplified with bow form sensitivity)
    # Base Cr depends on Cb, L/B, B/T, Fn
    Cr_base = (
        0.2 * np.exp(-1.5 / Fn**1.2) *  # Fn dependence
        (1.0 + 0.3 * (Cb - 0.65)) *      # Cb correction
        (1.0 - 0.05 * (L / B - 5.0))     # L/B correction
    )
    
    # Bow form sensitivity (mock: parameters affect wave making)
    # Bow entrance angle: affects wave making resistance (increased sensitivity)
    bow_entrance_delta = params.get("bow_entrance_angle_delta", 0.0)
    bow_factor = 1.0 - 0.08 * bow_entrance_delta  # Negative angle reduces resistance
    
    # Bulb volume: affects wave interference (increased sensitivity)
    bulb_scale = params.get("bulb_volume_scale", 1.0)
    bulb_factor = 1.0 - 0.15 * (bulb_scale - 1.0)  # Optimal bulb reduces resistance
    
    # Stem rake: affects bow wave (increased sensitivity)
    stem_rake = params.get("stem_rake_delta", 0.0)
    stem_factor = 1.0 - 0.03 * abs(stem_rake)  # Extreme rake increases resistance
    
    # Combined bow form factor
    bow_form_factor = bow_factor * bulb_factor * stem_factor
    
    Cr_coeff = Cr_base * bow_form_factor
    Rr = 0.5 * RHO_WATER * V_ms**2 * S_wet * Cr_coeff * 1e-3
    
    # 3. Wave resistance (simplified with parameter sensitivity)
    Rw_base = 0.5 * RHO_WATER * V_ms**2 * S_wet * 0.002 * np.exp(-2.0 / Fn**2)
    
    # Wave resistance sensitivity to bow form (increased sensitivity)
    waterline_flare = params.get("waterline_flare_fp", 0.0)
    wave_factor = 1.0 - 0.10 * waterline_flare  # Flare can reduce wave resistance
    
    Rw = Rw_base * wave_factor
    
    # 4. Correlation allowance
    Ca = 0.006 * (L + 100) ** (-0.16) * S_wet
    
    # Total resistance
    Rt = Rf + Rr + Rw + Ca
    
    # Resistance coefficients
    Ct = Rt / (0.5 * RHO_WATER * V_ms**2 * S_wet)
    Cf_coeff = Cf
    Cr_total = (Rr + Rw) / (0.5 * RHO_WATER * V_ms**2 * S_wet)
    
    return {
        "V_ms": V_ms,
        "Fn": Fn,
        "Re": Re,
        "S_wet": S_wet,
        "Rf_N": Rf,
        "Rr_N": Rr,
        "Rw_N": Rw,
        "Ca_N": Ca,
        "Rt_N": Rt,
        "Ct": Ct,
        "Cf": Cf_coeff,
        "Cr": Cr_total,
    }


def evaluate_resistance(geom: Dict[str, Any], Fn_design: float = None, 
                        seed: int = None) -> Dict[str, Any]:
    """
    Evaluate total resistance at design Fn (m547 interface).
    
    This is a MOCK implementation using Holtrop-Mennen method.
    Real implementation will call m547 batch evaluation pipeline.
    
    Args:
        geom: Geometry dictionary from m546
        Fn_design: Design Froude number (default: KCS design Fn=0.26)
        seed: Random seed for reproducibility (not used in deterministic mock)
    
    Returns:
        Evaluation dictionary with:
        - Ct: total resistance coefficient
        - components: resistance breakdown
        - in_holtrop_domain: bool
        - valid: bool (geometry valid + in domain)
    """
    if seed is not None:
        np.random.seed(seed)
    
    if Fn_design is None:
        Fn_design = 0.26  # KCS design Fn
    
    # Check Holtrop domain
    in_domain, domain_reason = check_holtrop_domain(geom, Fn_design)
    
    if not geom["valid"]:
        return {
            "Ct": np.inf,
            "components": {},
            "in_holtrop_domain": False,
            "valid": False,
            "evidence_level": EVIDENCE["level"],
            "physics_source": EVIDENCE["source"],
            "reason": f"Geometry invalid"
        }
    
    if not in_domain:
        return {
            "Ct": np.inf,
            "components": {},
            "in_holtrop_domain": False,
            "valid": False,
            "evidence_level": EVIDENCE["level"],
            "physics_source": EVIDENCE["source"],
            "reason": f"Outside Holtrop domain: {domain_reason}"
        }
    
    # Calculate speed from Fn
    V_ms = Fn_design * np.sqrt(G * geom["L"])
    
    # Calculate resistance
    result = holtrop_resistance(geom, V_ms)
    
    return {
        "Ct": result["Ct"],
        "components": {
            "Cf": result["Cf"],
            "Cr": result["Cr"],
            "Rt_N": result["Rt_N"],
            "Rf_N": result["Rf_N"],
            "Rr_N": result["Rr_N"],
            "Rw_N": result["Rw_N"],
        },
        "Fn": result["Fn"],
        "V_ms": result["V_ms"],
        "Re": result["Re"],
        "in_holtrop_domain": True,
        "valid": True,
        "evidence_level": EVIDENCE["level"],
        "physics_source": EVIDENCE["source"],
        "reason": "OK"
    }


def get_baseline_resistance() -> Dict[str, float]:
    """
    Get baseline KCS resistance at design Fn.
    
    Returns:
        Baseline evaluation dictionary
    """
    from .kcs_bow_param import generate_geometry, KCS_BASELINE
    
    # Baseline = zero deformation
    baseline_params = np.zeros(8)
    geom = generate_geometry(baseline_params)
    
    return evaluate_resistance(geom, KCS_BASELINE["Fn_design"])
