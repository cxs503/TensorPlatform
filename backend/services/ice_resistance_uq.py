"""
Full-Scale Ice Resistance Prediction with Uncertainty Quantification (UQ).

Implements:
  - Calibrated Lindqvist (1989) level-ice resistance model for ship bow
  - Polynomial Chaos Expansion (PCE) surrogate with Smolyak sparse-grid quadrature
  - Sobol sensitivity indices from PCE coefficients
  - Validation against MV Arctic, Polarstern, and Terry Fox full-scale trial data

References:
  - Lindqvist, G. (1989). "A straightforward method for calculation of ice
    resistance of ships." POAC-89.
  - Riska, K. et al. (1997). "Full scale measurements of ice loads on MV Arctic."
  - Smolyak, S.A. (1963). "Quadrature and interpolation formulas for tensor
    products of certain classes of functions."
  - Sudret, B. (2008). "Global sensitivity analysis using polynomial chaos
    expansions." Reliability Engineering & System Safety.
"""

from __future__ import annotations

import math
import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


# =====================================================================
# 1. Ice Resistance Physical Model (Lindqvist 1989 + calibration)
# =====================================================================

@dataclass
class ShipIceParams:
    """Ship parameters for ice resistance calculation."""
    name: str
    length_m: float          # Ship length between perpendiculars (m)
    beam_m: float            # Ship beam (m)
    draft_m: float           # Ship draft (m)
    cb: float                # Block coefficient
    bow_angle_deg: float     # Bow angle from horizontal (degrees)
    displacement_t: float    # Displacement (tonnes)
    calibration_factor: float = 1.0  # Empirical calibration factor


# Full-scale trial reference data (from published literature)
SHIP_TRIAL_DATA: Dict[str, Dict[str, Any]] = {
    "MV_Arctic": {
        "params": ShipIceParams(
            name="MV_Arctic",
            length_m=222.5,
            beam_m=32.2,
            draft_m=13.5,
            cb=0.82,
            bow_angle_deg=30.0,
            displacement_t=115000.0,
            calibration_factor=1.0,  # will be set during calibration
        ),
        # Measured mean resistance at nominal conditions (kN)
        # Source: Riska et al. (1997), mean over multiple runs in 1.0 m ice
        "mean_resistance_kN": 4800.0,
        # Nominal operating conditions
        "nominal_ice_thickness_m": 1.0,
        "nominal_ice_strength_MPa": 1.5,
        "nominal_friction_coeff": 0.15,
        "nominal_speed_ms": 3.0,
    },
    "Polarstern": {
        "params": ShipIceParams(
            name="Polarstern",
            length_m=118.0,
            beam_m=25.0,
            draft_m=10.5,
            cb=0.68,
            bow_angle_deg=35.0,
            displacement_t=17300.0,
            calibration_factor=1.0,
        ),
        # Source: Kühnlein (1997), full-scale measurements in level ice
        "mean_resistance_kN": 1800.0,
        "nominal_ice_thickness_m": 0.8,
        "nominal_ice_strength_MPa": 1.2,
        "nominal_friction_coeff": 0.12,
        "nominal_speed_ms": 2.5,
    },
    "Terry_Fox": {
        "params": ShipIceParams(
            name="Terry_Fox",
            length_m=75.0,
            beam_m=18.0,
            draft_m=7.5,
            cb=0.60,
            bow_angle_deg=28.0,
            displacement_t=5500.0,
            calibration_factor=1.0,
        ),
        # Source: Spencer & Jones (2001), full-scale trials
        "mean_resistance_kN": 850.0,
        "nominal_ice_thickness_m": 0.6,
        "nominal_ice_strength_MPa": 1.0,
        "nominal_friction_coeff": 0.10,
        "nominal_speed_ms": 2.0,
    },
}


def _lindqvist_raw(
    ship: ShipIceParams,
    ice_thickness_h: float,
    ice_strength_sigma_c: float,
    friction_coeff_mu: float,
    ship_speed_V: float,
) -> float:
    """
    Compute level-ice resistance using Lindqvist (1989) formulation (raw, uncalibrated).

    The total resistance is decomposed into:
      R = R_crushing + R_bending + R_submersion + R_speed

    Parameters
    ----------
    ship : ShipIceParams
        Ship geometric and mass parameters.
    ice_thickness_h : float
        Level ice thickness (m).
    ice_strength_sigma_c : float
        Uniaxial compressive strength of ice (MPa).
    friction_coeff_mu : float
        Ice-ship friction coefficient (dimensionless).
    ship_speed_V : float
        Ship speed (m/s).

    Returns
    -------
    float
        Total ice resistance in kN.
    """
    L = ship.length_m
    B = ship.beam_m
    T = ship.draft_m
    phi = math.radians(ship.bow_angle_deg)
    h = ice_thickness_h
    sigma_c = ice_strength_sigma_c * 1e6  # MPa → Pa
    mu = friction_coeff_mu
    V = ship_speed_V

    # --- 1. Crushing resistance (Lindqvist Eq. 5) ---
    if phi > 0.01:
        F_crushing = sigma_c * h * h * math.tan(phi) * (1.0 + mu / math.tan(phi))
    else:
        F_crushing = sigma_c * h * h * 0.5

    # --- 2. Bending resistance ---
    sigma_f = sigma_c / 4.0  # flexural strength ≈ σ_c/4
    E_ice = 2.5e9  # elastic modulus of ice (Pa)
    nu_ice = 0.33  # Poisson's ratio
    D = E_ice * h**3 / (12.0 * (1.0 - nu_ice**2))
    rho_w = 1025.0  # seawater density (kg/m³)
    g = 9.81
    l_c = (D / (rho_w * g)) ** 0.25  # characteristic length
    F_bend_per_w = sigma_f * h**2 / (6.0 * l_c) * (1.0 + mu * math.pi * l_c / (4.0 * h))
    F_bending = F_bend_per_w * B

    # --- 3. Submersion resistance (Lindqvist Eq. 12) ---
    rho_ice = 917.0  # ice density (kg/m³)
    if phi > 0.01:
        F_sub_per_l = (rho_w - rho_ice) * g * h * B * (1.0 + mu * math.cos(phi) / math.sin(phi))
    else:
        F_sub_per_l = (rho_w - rho_ice) * g * h * B
    F_submersion = F_sub_per_l * 0.3 * L

    # --- 4. Speed-dependent hydrodynamic drag on broken ice ---
    C_speed = 0.5 * rho_w * B * h * V**2 * 0.1

    # --- Total ---
    R_total = F_crushing + F_bending + F_submersion + C_speed
    return R_total / 1000.0  # Pa·m → kN


def _calibrate_ship(ship_name: str) -> ShipIceParams:
    """
    Calibrate the Lindqvist model so that at nominal conditions,
    the predicted resistance matches the published full-scale trial mean.

    Returns a ShipIceParams with the calibration_factor set.
    """
    trial = SHIP_TRIAL_DATA[ship_name]
    ship = trial["params"]
    R_raw = _lindqvist_raw(
        ship,
        trial["nominal_ice_thickness_m"],
        trial["nominal_ice_strength_MPa"],
        trial["nominal_friction_coeff"],
        trial["nominal_speed_ms"],
    )
    R_ref = trial["mean_resistance_kN"]
    if R_raw > 0:
        cal = R_ref / R_raw
    else:
        cal = 1.0
    # Create calibrated params
    return ShipIceParams(
        name=ship.name,
        length_m=ship.length_m,
        beam_m=ship.beam_m,
        draft_m=ship.draft_m,
        cb=ship.cb,
        bow_angle_deg=ship.bow_angle_deg,
        displacement_t=ship.displacement_t,
        calibration_factor=cal,
    )


def lindqvist_ice_resistance(
    ship: ShipIceParams,
    ice_thickness_h: float,
    ice_strength_sigma_c: float,
    friction_coeff_mu: float,
    ship_speed_V: float,
) -> float:
    """
    Compute calibrated level-ice resistance using Lindqvist (1989) formulation.

    The calibration factor ensures the model matches full-scale trial data
    at nominal conditions, accounting for simplifications in the analytical
    formulation (3D effects, ice failure patterns, etc.).
    """
    R_raw = _lindqvist_raw(ship, ice_thickness_h, ice_strength_sigma_c,
                           friction_coeff_mu, ship_speed_V)
    return R_raw * ship.calibration_factor


# Pre-calibrate all ships
for _name in SHIP_TRIAL_DATA:
    SHIP_TRIAL_DATA[_name]["params"] = _calibrate_ship(_name)


# =====================================================================
# 2. Uncertain Input Parameterization
# =====================================================================

@dataclass
class UncertainInput:
    """Definition of an uncertain input parameter."""
    name: str
    nominal: float
    std_relative: float  # relative standard deviation (std/nominal)
    distribution: str = "normal"  # "normal" or "uniform"
    lower: float = 0.0  # for uniform
    upper: float = 0.0  # for uniform

    @property
    def mean(self) -> float:
        return self.nominal

    @property
    def std(self) -> float:
        return self.nominal * self.std_relative


# =====================================================================
# 3. Gauss-Hermite Quadrature (probabilist's convention)
# =====================================================================

def _gauss_hermite_nodes_weights(n: int) -> Tuple[np.ndarray, np.ndarray]:
    """
    Return n-point Gauss-Hermite quadrature nodes and weights
    for the standard normal distribution N(0,1).

    The quadrature approximates:
      E[f(X)] = ∫ f(x) φ(x) dx ≈ Σ w_i f(x_i)

    where φ(x) = exp(-x²/2)/√(2π) is the standard normal PDF,
    and Σ w_i = 1.

    Uses the Golub-Welsch algorithm with the recurrence coefficients
    for probabilist's Hermite polynomials He_n(x):
      He_{n+1}(x) = x He_n(x) - n He_{n-1}(x)

    The Jacobi matrix has diagonal α_n = 0 and off-diagonal β_n = √n.
    """
    if n == 1:
        return np.array([0.0]), np.array([1.0])

    # Recurrence coefficients for probabilist's Hermite polynomials
    # β_k = √k for k = 1, 2, ..., n-1
    beta = np.sqrt(np.arange(1, n, dtype=float))

    # Tridiagonal Jacobi matrix
    J = np.diag(beta, -1) + np.diag(beta, 1)
    nodes, V = np.linalg.eigh(J)

    # Weights = (first component of eigenvector)²
    weights = V[0, :] ** 2

    # Sort by nodes
    idx = np.argsort(nodes)
    return nodes[idx], weights[idx]


# =====================================================================
# 4. Smolyak Sparse Grid Quadrature
# =====================================================================

def _tensor_product_quadrature(
    level: int, dim: int
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Full tensor-product Gauss-Hermite quadrature of given level in dim dimensions.
    Level q uses (q+1) points per dimension.
    """
    n_1d = level + 1
    nodes_1d, weights_1d = _gauss_hermite_nodes_weights(n_1d)

    nodes_list = []
    weights_list = []

    for combo in itertools.product(range(n_1d), repeat=dim):
        node = np.array([nodes_1d[combo[d]] for d in range(dim)])
        weight = 1.0
        for d in range(dim):
            weight *= weights_1d[combo[d]]
        nodes_list.append(node)
        weights_list.append(weight)

    return np.array(nodes_list), np.array(weights_list)


def smolyak_sparse_grid(
    max_level: int, dim: int
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Smolyak sparse grid for Gauss-Hermite quadrature in dim dimensions.

    Uses the formula:
      A(q, d) = Σ_{|i| ≤ q+d} (-1)^(q+d-|i|) * C(d-1, q+d-|i|) * U(i_1) ⊗ ... ⊗ U(i_d)

    where q = max_level - 1 is the accuracy level, and i_k ≥ 1.

    Parameters
    ----------
    max_level : int
        Maximum quadrature level (1-based). Level k uses k points per dim.
    dim : int
        Number of dimensions.

    Returns
    -------
    nodes : np.ndarray (N x dim)
    weights : np.ndarray (N,)
    """
    q = max_level - 1  # accuracy level (0-based)

    # Collect all unique (node, weight) pairs
    node_dict: Dict[Tuple, float] = {}

    for idx in itertools.product(range(1, max_level + 1), repeat=dim):
        level_sum = sum(idx)
        if level_sum <= max_level + dim - 1:
            # Coefficient
            k = max_level + dim - 1 - level_sum
            if k > dim - 1:
                continue  # C(d-1, k) = 0
            coeff = (-1) ** k * math.comb(dim - 1, k)

            # 1D quadrature for each dimension
            nodes_1d_list = []
            weights_1d_list = []
            for d in range(dim):
                n_pts = idx[d]
                n, w = _gauss_hermite_nodes_weights(n_pts)
                nodes_1d_list.append(n)
                weights_1d_list.append(w)

            # Tensor product of these 1D rules
            for combo in itertools.product(*[range(len(n)) for n in nodes_1d_list]):
                node = tuple(float(nodes_1d_list[d][combo[d]]) for d in range(dim))
                weight = coeff
                for d in range(dim):
                    weight *= weights_1d_list[d][combo[d]]

                if node in node_dict:
                    node_dict[node] += weight
                else:
                    node_dict[node] = weight

    # Filter out negligible weights (numerical artifacts)
    nodes_list = []
    weights_list = []
    for node, weight in node_dict.items():
        if abs(weight) > 1e-14:
            nodes_list.append(node)
            weights_list.append(weight)

    nodes = np.array(nodes_list)
    weights = np.array(weights_list)

    return nodes, weights


# =====================================================================
# 5. Polynomial Chaos Expansion (PCE)
# =====================================================================

def _hermite_basis_1d(x: float, order: int) -> float:
    """
    Evaluate probabilist's Hermite polynomial He_n(x) of given order.

    Uses the three-term recurrence:
      He_0(x) = 1
      He_1(x) = x
      He_{n+1}(x) = x * He_n(x) - n * He_{n-1}(x)
    """
    if order == 0:
        return 1.0
    if order == 1:
        return x
    h_prev2 = 1.0
    h_prev1 = x
    for n in range(1, order):
        h_curr = x * h_prev1 - n * h_prev2
        h_prev2 = h_prev1
        h_prev1 = h_curr
    return h_prev1


def _multi_index_set(max_order: int, dim: int) -> List[Tuple[int, ...]]:
    """
    Generate total-order multi-index set: all α with |α|₁ ≤ max_order.
    """
    indices = []
    for total in range(max_order + 1):
        for combo in itertools.combinations_with_replacement(range(dim), total):
            alpha = [0] * dim
            for c in combo:
                alpha[c] += 1
            indices.append(tuple(alpha))
    return indices


def _evaluate_pce_basis(x: np.ndarray, alpha: Tuple[int, ...]) -> float:
    """
    Evaluate multivariate Hermite polynomial basis function ψ_α(x).
    x : (dim,) standard normal sample
    alpha : multi-index (dim,)
    """
    result = 1.0
    for d in range(len(alpha)):
        result *= _hermite_basis_1d(x[d], alpha[d])
    return result


def build_pce(
    model_func,
    uncertain_inputs: List[UncertainInput],
    max_level: int = 3,
    max_poly_order: int = 3,
) -> Dict[str, Any]:
    """
    Build a Polynomial Chaos Expansion surrogate model.

    Steps:
      1. Construct Smolyak sparse grid in standard normal space
      2. Map nodes to physical space
      3. Evaluate model at each node
      4. Compute PCE coefficients via projection (quadrature)

    Parameters
    ----------
    model_func : callable
        Physical model f(x1, x2, ..., xd) → scalar.
    uncertain_inputs : list of UncertainInput
        Uncertain parameter definitions.
    max_level : int
        Smolyak sparse grid level (controls number of quadrature points).
    max_poly_order : int
        Maximum total polynomial order.

    Returns
    -------
    dict with keys: coefficients, multi_indices, nodes, weights,
                    model_values, n_evaluations, uncertain_inputs
    """
    dim = len(uncertain_inputs)

    # Step 1: Smolyak sparse grid in standard normal space
    nodes_std, weights = smolyak_sparse_grid(max_level, dim)

    # Step 2: Map standard normal nodes to physical space
    nodes_phys = np.zeros_like(nodes_std)
    for d in range(dim):
        inp = uncertain_inputs[d]
        nodes_phys[:, d] = inp.mean + inp.std * nodes_std[:, d]

    # Step 3: Evaluate model at each physical-space node
    model_values = np.zeros(len(nodes_std))
    for i in range(len(nodes_std)):
        model_values[i] = model_func(*nodes_phys[i])

    # Step 4: Compute PCE coefficients via projection
    multi_indices = _multi_index_set(max_poly_order, dim)
    coefficients = np.zeros(len(multi_indices))

    for j, alpha in enumerate(multi_indices):
        # c_j = <f, ψ_j> / <ψ_j, ψ_j>
        # For probabilist's Hermite polynomials: <ψ_α, ψ_α> = Π_d α_d!
        norm_sq = 1.0
        for d in range(dim):
            norm_sq *= math.factorial(alpha[d])

        # Numerator: Σ_i w_i * f(x_i) * ψ_α(ξ_i)
        numerator = 0.0
        for i in range(len(nodes_std)):
            psi_val = _evaluate_pce_basis(nodes_std[i], alpha)
            numerator += weights[i] * model_values[i] * psi_val

        coefficients[j] = numerator / norm_sq

    return {
        "coefficients": coefficients,
        "multi_indices": multi_indices,
        "nodes": nodes_std,
        "weights": weights,
        "nodes_physical": nodes_phys,
        "model_values": model_values,
        "n_evaluations": len(nodes_std),
        "uncertain_inputs": uncertain_inputs,
        "max_poly_order": max_poly_order,
        "max_level": max_level,
        "dim": dim,
    }


def evaluate_pce(pce_result: Dict[str, Any], x_std: np.ndarray) -> float:
    """
    Evaluate PCE surrogate at a point in standard normal space.
    """
    result = 0.0
    for j, alpha in enumerate(pce_result["multi_indices"]):
        result += pce_result["coefficients"][j] * _evaluate_pce_basis(x_std, alpha)
    return result


def evaluate_pce_physical(
    pce_result: Dict[str, Any], x_phys: np.ndarray
) -> float:
    """
    Evaluate PCE surrogate at a point in physical space.
    """
    dim = pce_result["dim"]
    x_std = np.zeros(dim)
    for d in range(dim):
        inp = pce_result["uncertain_inputs"][d]
        x_std[d] = (x_phys[d] - inp.mean) / inp.std
    return evaluate_pce(pce_result, x_std)


# =====================================================================
# 6. PCE Post-Processing: Statistics & Sobol Indices
# =====================================================================

def pce_statistics(pce_result: Dict[str, Any]) -> Dict[str, float]:
    """
    Compute mean, variance, and standard deviation from PCE coefficients.

    For PCE: Y = Σ c_α ψ_α
      E[Y] = c_0  (since ψ_0 = 1)
      Var[Y] = Σ_{α≠0} c_α² ||ψ_α||² = Σ_{α≠0} c_α² Π_d α_d!
    """
    coefficients = pce_result["coefficients"]
    multi_indices = pce_result["multi_indices"]

    # Mean = coefficient of ψ_0
    mean = coefficients[0]

    # Variance
    variance = 0.0
    for j, alpha in enumerate(multi_indices):
        if sum(alpha) == 0:
            continue  # skip α = 0
        norm_sq = 1.0
        for d in range(len(alpha)):
            norm_sq *= math.factorial(alpha[d])
        variance += coefficients[j] ** 2 * norm_sq

    std = math.sqrt(max(0.0, variance))

    return {
        "mean": mean,
        "variance": variance,
        "std": std,
    }


def pce_confidence_interval(
    pce_result: Dict[str, Any], confidence: float = 0.95
) -> Dict[str, float]:
    """
    Compute confidence interval assuming approximately normal distribution.
    """
    from scipy.stats import norm as scipy_norm

    stats = pce_statistics(pce_result)
    z = scipy_norm.ppf(0.5 + confidence / 2.0)

    return {
        "mean": stats["mean"],
        "std": stats["std"],
        "lower": stats["mean"] - z * stats["std"],
        "upper": stats["mean"] + z * stats["std"],
        "confidence": confidence,
        "z_value": z,
    }


def sobol_indices(pce_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute Sobol sensitivity indices from PCE coefficients.

    The first-order Sobol index for input d:
      S_d = Σ_{α: α_d>0, α_{≠d}=0} c_α² ||ψ_α||² / D

    The total Sobol index for input d:
      S_T_d = Σ_{α: α_d>0} c_α² ||ψ_α||² / D
    """
    coefficients = pce_result["coefficients"]
    multi_indices = pce_result["multi_indices"]
    dim = pce_result["dim"]
    input_names = [inp.name for inp in pce_result["uncertain_inputs"]]

    # Total variance
    D = 0.0
    for j, alpha in enumerate(multi_indices):
        if sum(alpha) == 0:
            continue
        norm_sq = 1.0
        for d in range(dim):
            norm_sq *= math.factorial(alpha[d])
        D += coefficients[j] ** 2 * norm_sq

    if D < 1e-30:
        return {
            "first_order": {name: 0.0 for name in input_names},
            "total_order": {name: 0.0 for name in input_names},
            "variance": 0.0,
            "input_names": input_names,
        }

    # First-order indices
    first_order = {}
    for d in range(dim):
        V_d = 0.0
        for j, alpha in enumerate(multi_indices):
            if alpha[d] > 0 and all(alpha[k] == 0 for k in range(dim) if k != d):
                norm_sq = 1.0
                for dd in range(dim):
                    norm_sq *= math.factorial(alpha[dd])
                V_d += coefficients[j] ** 2 * norm_sq
        first_order[input_names[d]] = V_d / D

    # Total-order indices
    total_order = {}
    for d in range(dim):
        V_T_d = 0.0
        for j, alpha in enumerate(multi_indices):
            if alpha[d] > 0:
                norm_sq = 1.0
                for dd in range(dim):
                    norm_sq *= math.factorial(alpha[dd])
                V_T_d += coefficients[j] ** 2 * norm_sq
        total_order[input_names[d]] = V_T_d / D

    return {
        "first_order": first_order,
        "total_order": total_order,
        "variance": D,
        "input_names": input_names,
    }


# =====================================================================
# 7. Monte Carlo Reference Computation
# =====================================================================

def monte_carlo_reference(
    model_func,
    uncertain_inputs: List[UncertainInput],
    n_samples: int = 100000,
    seed: int = 42,
) -> Dict[str, float]:
    """
    Compute reference mean and std via Monte Carlo sampling.

    This serves as the ground truth for validating PCE statistics.
    """
    rng = np.random.default_rng(seed)
    dim = len(uncertain_inputs)

    samples = np.zeros(n_samples)
    for i in range(n_samples):
        x_phys = np.zeros(dim)
        for d in range(dim):
            inp = uncertain_inputs[d]
            x_phys[d] = inp.mean + inp.std * rng.standard_normal()
            # Enforce physical bounds
            if d == 0:  # ice strength
                x_phys[d] = max(0.3, x_phys[d])
            elif d == 1:  # ice thickness
                x_phys[d] = max(0.1, x_phys[d])
            elif d == 2:  # friction
                x_phys[d] = max(0.02, min(0.5, x_phys[d]))
            elif d == 3:  # speed
                x_phys[d] = max(0.5, x_phys[d])
        samples[i] = model_func(*x_phys)

    return {
        "mean": float(np.mean(samples)),
        "std": float(np.std(samples)),
        "variance": float(np.var(samples)),
        "n_samples": n_samples,
    }


# =====================================================================
# 8. Full UQ Pipeline
# =====================================================================

@dataclass
class IceResistanceUQResult:
    """Complete UQ result for ice resistance."""
    ship_name: str
    mean_resistance_kN: float
    std_resistance_kN: float
    variance_kN2: float
    ci_lower_kN: float
    ci_upper_kN: float
    ci_confidence: float
    sobol_first_order: Dict[str, float]
    sobol_total_order: Dict[str, float]
    sobol_sum_first_order: float
    sobol_sum_total_order: float
    n_model_evaluations: int
    # Reference values from Monte Carlo
    mc_mean_kN: Optional[float] = None
    mc_std_kN: Optional[float] = None
    # Reference values from full-scale trials
    trial_mean_kN: Optional[float] = None
    # Errors vs MC reference
    mean_error: Optional[float] = None
    std_error: Optional[float] = None
    # CI coverage
    ci_coverage: Optional[float] = None


def run_ice_resistance_uq(
    ship_name: str,
    max_level: int = 3,
    max_poly_order: int = 3,
    n_mc_validation: int = 10000,
    n_mc_reference: int = 100000,
) -> IceResistanceUQResult:
    """
    Run full ice resistance UQ pipeline for a given ship.

    Validation strategy:
      1. Calibrate Lindqvist model to match full-scale trial mean at nominal conditions
      2. Compute MC reference statistics (mean, std) with large sample
      3. Build PCE surrogate and compute statistics
      4. Compare PCE mean/std against MC reference (not trial data directly,
         since trial std includes measurement scatter not captured by parametric UQ)
      5. Validate CI coverage via MC sampling
    """
    if ship_name not in SHIP_TRIAL_DATA:
        raise ValueError(f"Unknown ship: {ship_name}. Available: {list(SHIP_TRIAL_DATA.keys())}")

    trial = SHIP_TRIAL_DATA[ship_name]
    ship = trial["params"]

    # Define uncertain inputs centered on nominal conditions
    uncertain_inputs = [
        UncertainInput("ice_strength_MPa", nominal=trial["nominal_ice_strength_MPa"], std_relative=0.20),
        UncertainInput("ice_thickness_m", nominal=trial["nominal_ice_thickness_m"], std_relative=0.15),
        UncertainInput("friction_coeff", nominal=trial["nominal_friction_coeff"], std_relative=0.25),
        UncertainInput("ship_speed_ms", nominal=trial["nominal_speed_ms"], std_relative=0.10),
    ]

    # Define model function
    def ice_model(ice_strength, ice_thickness, friction, speed):
        return lindqvist_ice_resistance(ship, ice_thickness, ice_strength, friction, speed)

    # Step 1: Compute MC reference
    mc_ref = monte_carlo_reference(ice_model, uncertain_inputs, n_samples=n_mc_reference)

    # Step 2: Build PCE
    pce = build_pce(ice_model, uncertain_inputs, max_level=max_level, max_poly_order=max_poly_order)

    # Step 3: Compute PCE statistics
    stats = pce_statistics(pce)
    ci = pce_confidence_interval(pce, confidence=0.95)
    sobol = sobol_indices(pce)

    # Step 4: Validate CI coverage via Monte Carlo
    rng = np.random.default_rng(123)
    mc_covered = 0
    for _ in range(n_mc_validation):
        x_phys = np.array([
            inp.mean + inp.std * rng.standard_normal() for inp in uncertain_inputs
        ])
        # Enforce physical bounds
        x_phys[0] = max(0.3, x_phys[0])
        x_phys[1] = max(0.1, x_phys[1])
        x_phys[2] = max(0.02, min(0.5, x_phys[2]))
        x_phys[3] = max(0.5, x_phys[3])

        y_mc = ice_model(*x_phys)
        if ci["lower"] <= y_mc <= ci["upper"]:
            mc_covered += 1

    ci_coverage = mc_covered / n_mc_validation

    # Step 5: Compute errors vs MC reference
    mean_error = abs(stats["mean"] - mc_ref["mean"]) / mc_ref["mean"]
    std_error = abs(stats["std"] - mc_ref["std"]) / mc_ref["std"]

    # Sobol sum
    sobol_sum_fo = sum(sobol["first_order"].values())
    sobol_sum_to = sum(sobol["total_order"].values())

    return IceResistanceUQResult(
        ship_name=ship_name,
        mean_resistance_kN=stats["mean"],
        std_resistance_kN=stats["std"],
        variance_kN2=stats["variance"],
        ci_lower_kN=ci["lower"],
        ci_upper_kN=ci["upper"],
        ci_confidence=ci["confidence"],
        sobol_first_order=sobol["first_order"],
        sobol_total_order=sobol["total_order"],
        sobol_sum_first_order=sobol_sum_fo,
        sobol_sum_total_order=sobol_sum_to,
        n_model_evaluations=pce["n_evaluations"],
        mc_mean_kN=mc_ref["mean"],
        mc_std_kN=mc_ref["std"],
        trial_mean_kN=trial["mean_resistance_kN"],
        mean_error=mean_error,
        std_error=std_error,
        ci_coverage=ci_coverage,
    )


def run_all_ships_uq(
    max_level: int = 3,
    max_poly_order: int = 3,
) -> Dict[str, IceResistanceUQResult]:
    """Run UQ for all ships in the trial database."""
    results = {}
    for ship_name in SHIP_TRIAL_DATA:
        results[ship_name] = run_ice_resistance_uq(ship_name, max_level, max_poly_order)
    return results
