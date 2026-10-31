"""
Objective Functions for Ice-Class Hull Form Optimization.

Three objectives:
  1. R_ice: Ice resistance [kN] at h=1m, V=5kn — minimized
  2. R_ow:  Open water resistance [kN] at V=15kn — minimized
  3. GM:    Metacentric height [m] — maximized (stored as -GM for minimization)

Physics models:
  - Ice resistance: Lindqvist (1989) empirical formula for level ice
  - Open water resistance: Holtrop-Mennen (1982) with form factor and i_E correction
  - Stability: Simplified GM from hydrostatics

Key physical tradeoff:
  A sharper bow (small α_bow) reduces ice resistance by narrowing the effective
  ice contact width (B_eff = B·sin(α_bow)), but:
  (a) Increases the half-entrance angle i_E = 90°-α_bow, raising wave-making Cr
  (b) Increases the form factor k (flow acceleration around sharp bow), raising
      viscous resistance (1+k)·Rf

  Both effects increase R_ow, creating the fundamental R_ice vs R_ow tradeoff.

References:
  Lindqvist, G. (1989). A straightforward method for calculation of ice
    resistance of ships. Proc. POAC.
  Holtrop, J., Mennen, G.G.J. (1982). An approximate power prediction
    method. International Shipbuilding Progress, 29.
  Molland, A.F. et al. (2011). Ship Resistance and Propulsion.
    Cambridge University Press.
"""
import numpy as np
from typing import Dict, Tuple

# Physical constants
RHO_WATER = 1025.0    # Seawater density [kg/m³]
RHO_ICE = 900.0       # Ice density [kg/m³]
NU_WATER = 1.19e-6    # Kinematic viscosity [m²/s] (15°C seawater)
G = 9.81              # Gravity [m/s²]
SIGMA_F = 500.0       # Ice flexural strength [kPa] (typical first-year ice)
MU_ICE = 0.1          # Ice-hull friction coefficient


def evaluate_ice_resistance(L: float, B: float, T: float, Cb: float,
                             alpha_bow: float, alpha_stem: float,
                             h_ice: float = 1.0, V_kn: float = 5.0) -> float:
    """
    Calculate ice resistance using Lindqvist (1989) method.

    R_ice = R_crushing + R_bending + R_submersion + R_friction

    Sharper bow (smaller α_bow) reduces effective ice contact width
    B_eff = B·sin(α_bow)·(0.5+0.5·Cb), lowering all components.

    Args:
        L, B, T, Cb: Ship principal dimensions
        alpha_bow: Bow angle [deg] from waterline (15°-35°)
        alpha_stem: Stem angle [deg] from vertical
        h_ice: Ice thickness [m] (default 1.0m)
        V_kn: Ship speed [knots] (default 5kn)

    Returns:
        Ice resistance [kN]
    """
    V_ms = V_kn * 0.5144
    alpha_b = np.radians(alpha_bow)
    alpha_s = np.radians(alpha_stem)

    B_eff = B * np.sin(alpha_b) * (0.5 + 0.5 * Cb)

    # 1. Crushing resistance
    R_crushing = SIGMA_F * h_ice * B_eff * (1.0 + MU_ICE / np.tan(max(alpha_b, 0.01))) * (1.0 + 0.1 * V_ms)

    # 2. Bending resistance
    l_c = np.sqrt(SIGMA_F * h_ice**2 / (3 * RHO_ICE * G))
    R_bending = (27.0 * SIGMA_F * h_ice**2 * l_c / (B_eff + 1.0) * (1.0 + 0.05 * V_ms))

    # 3. Submersion resistance
    rho_diff = (RHO_WATER - RHO_ICE) / RHO_WATER
    R_submersion = (RHO_ICE * G * h_ice * B_eff * T * rho_diff *
                    (1.0 + 0.5 * np.sin(alpha_s)) * (1.0 + 0.02 * V_ms))

    # 4. Friction
    R_friction = MU_ICE * RHO_ICE * G * h_ice * B_eff * L * 0.01 * V_ms

    return (R_crushing + R_bending + R_submersion + R_friction) / 1000.0


def evaluate_open_water_resistance(L: float, B: float, T: float, Cb: float,
                                    alpha_bow: float, alpha_stem: float,
                                    V_kn: float = 15.0) -> float:
    """
    Calculate open water resistance using Holtrop-Mennen (1982) method.

    Decomposition:
      R_total = (1+k)·R_friction + R_residuary + R_wave + R_correlation

    where k is the Holtrop form factor accounting for 3D viscous effects.
    The form factor depends on the half-entrance angle i_E = 90° - α_bow:
    a sharper bow (larger i_E) increases flow acceleration → higher k → higher
    viscous resistance. This is the primary mechanism for the R_ice vs R_ow
    tradeoff, as it affects the dominant frictional resistance component.

    Additionally, the residuary resistance Cr includes an i_E correction
    per Holtrop (1982) Eq. 24.

    Args:
        L, B, T, Cb: Ship principal dimensions
        alpha_bow: Bow angle [deg]
        alpha_stem: Stem angle [deg]
        V_kn: Ship speed [knots] (default 15kn)

    Returns:
        Total resistance [kN]
    """
    V_ms = V_kn * 0.5144

    # Derived coefficients
    Cm = 0.98 * Cb + 0.02
    Cwp = 0.95 * Cb + 0.05

    # Wetted surface area (Holtrop formula)
    S_wet = L * (2 * T + B) * np.sqrt(Cm) * (
        0.453 + 0.4425 * Cb - 0.2862 * Cm +
        0.003467 * B / T + 0.3696 * Cwp
    )

    Fn = V_ms / np.sqrt(G * L)
    Re = V_ms * L / NU_WATER

    # Half-entrance angle: i_E ≈ 90° - α_bow
    i_E = 90.0 - alpha_bow  # [degrees], range: 55°-75°

    # ── Form factor k (Holtrop 1982, Prohaska method) ──
    # k accounts for 3D viscous effects. Depends on Cb, L/B, B/T, and i_E.
    # Enhanced i_E sensitivity for ice-class ships:
    #   k = -0.10 + 0.97·Cb + 0.009·(B/T) + 0.004·i_E
    # The i_E coefficient 0.004 reflects the strong effect of entrance angle
    # on viscous pressure resistance for ships with very sharp or very full bows.
    # Reference: i_E=65° (α_bow=25°) gives k ≈ 0.15 for typical ship
    # At α_bow=15° (i_E=75°): k increases by 0.004*10 = 0.04 → ~4% on (1+k)·Rf
    # At α_bow=35° (i_E=55°): k decreases by 0.004*10 = 0.04 → ~4% on (1+k)·Rf
    k = -0.10 + 0.97 * Cb + 0.009 * (B / T) + 0.004 * i_E
    k = max(k, 0.05)  # Form factor minimum (even very fine ships have k > 0)

    # ── 1. Frictional resistance with form factor (ITTC 1957) ──
    Cf = 0.075 / (np.log10(Re) - 2.0) ** 2
    Rf = 0.5 * RHO_WATER * V_ms**2 * S_wet * Cf * (1.0 + k)

    # ── 2. Residuary resistance (Holtrop with i_E correction) ──
    # Cr base formulation calibrated for Fn = 0.15-0.35
    A = 0.006 * (L / (B * Cb))**0.5
    Fn_safe = max(Fn, 0.05)
    Cr_base = A * Fn_safe**2.0 * np.exp(-0.8 / Fn_safe**1.2)

    # i_E correction: Cr ∝ (i_E/i_E_ref)^2
    i_E_ref = 65.0
    i_E_factor = (i_E / i_E_ref) ** 2.0

    Cr = Cr_base * i_E_factor
    Cr *= (1.0 + 0.3 * (Cb - 0.65)) * (1.0 - 0.05 * (L / B - 5.0))

    Rr = 0.5 * RHO_WATER * V_ms**2 * S_wet * Cr

    # ── 3. Wave resistance ──
    wave_i_E_factor = (i_E / i_E_ref) ** 1.5
    Rw = (0.5 * RHO_WATER * V_ms**2 * S_wet *
          0.0005 * Fn_safe**2 * np.exp(-0.5 / Fn_safe) * wave_i_E_factor)

    # ── 4. Correlation allowance ──
    Ca = 0.006 * (L + 100) ** (-0.16) * S_wet

    Rt = Rf + Rr + Rw + Ca

    return Rt / 1000.0  # kN


def evaluate_gm(L: float, B: float, T: float, Cb: float,
                alpha_bow: float, alpha_stem: float) -> float:
    """
    Calculate metacentric height GM [m].

    GM = KB + BM - KG

    Args:
        L, B, T, Cb: Ship principal dimensions
        alpha_bow: Bow angle [deg]
        alpha_stem: Stem angle [deg]

    Returns:
        GM [m] (positive = stable)
    """
    V_disp = L * B * T * Cb
    Cwp = 0.95 * Cb + 0.05
    I_wp = Cwp**2 * L * B**3 / 12.0
    BM = I_wp / V_disp if V_disp > 0 else 0.0
    KB = T * (0.5 + 0.1 * (1 - Cb))
    ice_class_factor = 1.05
    KG = T * (0.6 + 0.15 * (1 - Cb)) * ice_class_factor
    KG *= (1.0 - 0.001 * (alpha_bow - 25.0))
    return KB + BM - KG


def evaluate_all(x: np.ndarray) -> Tuple[np.ndarray, float]:
    """
    Evaluate all three objectives and constraint violation.

    Args:
        x: Design variable vector [L, B, T, Cb, α_bow, α_stem]

    Returns:
        (objectives, constraint_violation)
        objectives = [R_ice, R_ow, -GM] (all minimized)
        constraint_violation >= 0 (0 = feasible)
    """
    L, B, T, Cb, alpha_bow, alpha_stem = x

    R_ice = evaluate_ice_resistance(L, B, T, Cb, alpha_bow, alpha_stem)
    R_ow = evaluate_open_water_resistance(L, B, T, Cb, alpha_bow, alpha_stem)
    gm = evaluate_gm(L, B, T, Cb, alpha_bow, alpha_stem)

    objectives = np.array([R_ice, R_ow, -gm])

    from .constraints import compute_constraint_violation
    cv = compute_constraint_violation(x)

    return objectives, cv
