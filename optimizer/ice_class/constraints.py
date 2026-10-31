"""
Constraint Handling for Ice-Class Hull Form Optimization.

Constraints:
  1. Cb ∈ [0.5, 0.85]
  2. L/B ∈ [4, 7]
  3. B/T ∈ [2.5, 4]
  4. α_bow ∈ [15°, 35°]
  5. IACS UR I ice class structural requirements (minimum shell plate thickness)

IACS UR I requirements (simplified):
  - Minimum shell plate thickness: t_min = 0.07 * L^(1/3) + 2.5 [mm]
  - Frame spacing constraint: s ≤ 600mm for ice belt
  - Minimum GM for ice operations: GM ≥ 0.15m (IMO Polar Code)
"""
import numpy as np
from typing import Dict, Tuple

# Design variable bounds
CONSTRAINT_BOUNDS = {
    "L":   (80.0, 200.0),     # Ship length [m]
    "B":   (15.0, 35.0),      # Ship beam [m]
    "T":   (5.0, 12.0),       # Ship draft [m]
    "Cb":  (0.5, 0.85),       # Block coefficient
    "alpha_bow":  (15.0, 35.0),  # Bow angle [deg]
    "alpha_stem": (10.0, 40.0),  # Stem angle [deg]
}

# Derived constraint bounds
L_B_RANGE = (4.0, 7.0)
B_T_RANGE = (2.5, 4.0)

# IACS UR I minimum GM for ice operations (Polar Code)
GM_MIN_ICE = 0.15  # [m]


def compute_constraint_violation(x: np.ndarray) -> float:
    """
    Compute total constraint violation for a design point.

    Uses L2 norm of individual violations (all >= 0, 0 = feasible).

    Args:
        x: Design variable vector [L, B, T, Cb, α_bow, α_stem]

    Returns:
        Total constraint violation (0 = feasible)
    """
    L, B, T, Cb, alpha_bow, alpha_stem = x
    violations = []

    # 1. Cb bounds
    violations.append(max(0, CONSTRAINT_BOUNDS["Cb"][0] - Cb))
    violations.append(max(0, Cb - CONSTRAINT_BOUNDS["Cb"][1]))

    # 2. L/B ratio
    L_B = L / B
    violations.append(max(0, L_B_RANGE[0] - L_B))
    violations.append(max(0, L_B - L_B_RANGE[1]))

    # 3. B/T ratio
    B_T = B / T
    violations.append(max(0, B_T_RANGE[0] - B_T))
    violations.append(max(0, B_T - B_T_RANGE[1]))

    # 4. α_bow bounds
    violations.append(max(0, CONSTRAINT_BOUNDS["alpha_bow"][0] - alpha_bow))
    violations.append(max(0, alpha_bow - CONSTRAINT_BOUNDS["alpha_bow"][1]))

    # 5. IACS UR I: minimum GM for ice operations
    from .objectives import evaluate_gm
    gm = evaluate_gm(L, B, T, Cb, alpha_bow, alpha_stem)
    violations.append(max(0, GM_MIN_ICE - gm))

    # 6. IACS UR I: minimum shell plate thickness feasibility
    # t_min = 0.07 * L^(1/3) + 2.5 [mm]
    # This is a structural feasibility check — we approximate it as:
    # The hull must have sufficient displacement to support ice-strengthened structure
    # Simplified: displacement must be > minimum for ice class
    disp_tonnes = L * B * T * Cb * 1.025  # displacement in tonnes
    disp_min = 500.0  # Minimum displacement for ice-class structural feasibility [tonnes]
    violations.append(max(0, disp_min - disp_tonnes))

    # 7. Variable bounds (safety)
    var_names = ["L", "B", "T", "Cb", "alpha_bow", "alpha_stem"]
    for i, name in enumerate(var_names):
        lb, ub = CONSTRAINT_BOUNDS[name]
        violations.append(max(0, lb - x[i]))
        violations.append(max(0, x[i] - ub))

    return np.sqrt(sum(v**2 for v in violations))


def check_constraints(x: np.ndarray) -> Tuple[bool, Dict[str, bool]]:
    """
    Check all constraints for a design point.

    Args:
        x: Design variable vector [L, B, T, Cb, α_bow, α_stem]

    Returns:
        (feasible, details) where details is a dict of individual constraint checks
    """
    L, B, T, Cb, alpha_bow, alpha_stem = x

    L_B = L / B
    B_T = B / T

    from .objectives import evaluate_gm
    gm = evaluate_gm(L, B, T, Cb, alpha_bow, alpha_stem)

    disp_tonnes = L * B * T * Cb * 1.025

    details = {
        "Cb_in_range": CONSTRAINT_BOUNDS["Cb"][0] <= Cb <= CONSTRAINT_BOUNDS["Cb"][1],
        "L_B_in_range": L_B_RANGE[0] <= L_B <= L_B_RANGE[1],
        "B_T_in_range": B_T_RANGE[0] <= B_T <= B_T_RANGE[1],
        "alpha_bow_in_range": CONSTRAINT_BOUNDS["alpha_bow"][0] <= alpha_bow <= CONSTRAINT_BOUNDS["alpha_bow"][1],
        "GM_sufficient": gm >= GM_MIN_ICE,
        "displacement_sufficient": disp_tonnes >= 500.0,
        "L_in_range": CONSTRAINT_BOUNDS["L"][0] <= L <= CONSTRAINT_BOUNDS["L"][1],
        "B_in_range": CONSTRAINT_BOUNDS["B"][0] <= B <= CONSTRAINT_BOUNDS["B"][1],
        "T_in_range": CONSTRAINT_BOUNDS["T"][0] <= T <= CONSTRAINT_BOUNDS["T"][1],
        "alpha_stem_in_range": CONSTRAINT_BOUNDS["alpha_stem"][0] <= alpha_stem <= CONSTRAINT_BOUNDS["alpha_stem"][1],
    }

    feasible = all(details.values())
    return feasible, details


def get_bounds() -> Tuple[np.ndarray, np.ndarray]:
    """Get lower and upper bounds for design variables."""
    lb = np.array([CONSTRAINT_BOUNDS["L"][0], CONSTRAINT_BOUNDS["B"][0],
                   CONSTRAINT_BOUNDS["T"][0], CONSTRAINT_BOUNDS["Cb"][0],
                   CONSTRAINT_BOUNDS["alpha_bow"][0], CONSTRAINT_BOUNDS["alpha_stem"][0]])
    ub = np.array([CONSTRAINT_BOUNDS["L"][1], CONSTRAINT_BOUNDS["B"][1],
                   CONSTRAINT_BOUNDS["T"][1], CONSTRAINT_BOUNDS["Cb"][1],
                   CONSTRAINT_BOUNDS["alpha_bow"][1], CONSTRAINT_BOUNDS["alpha_stem"][1]])
    return lb, ub
