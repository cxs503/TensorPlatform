"""
Full-Scale Ice Resistance Prediction with Uncertainty Quantification (UQ).

Implements:
  - Calibrated Lindqvist (1989) level-ice resistance model for ship bow
  - Polynomial Chaos Expansion (PCE) surrogate with Smolyak sparse-grid quadrature
  - Sobol sensitivity indices from PCE coefficients
  - In-sample calibration against MV Arctic, Polarstern and Terry Fox trial means

Validation status (audited 2026-11-01, task m1720)
--------------------------------------------------
The scalar ``calibration_factor`` of each ship is fitted as
``R_ref / R_raw`` *on that same trial point*, so the reported agreement with the
trial mean is an identity, not a validation (in-sample error = 1.9e-16, and it
stays zero for any arbitrarily tampered reference value).  The only information
carrying check available with three ships x one nominal point is
leave-one-out: fit one global constant on the other two ships and predict the
held-out ship.  That gives +11.8 % (MV Arctic), +16.5 % (Polarstern) and
-23.2 % (Terry Fox) -- i.e. **out of sample the model misses the 15 %
acceptance band**.  Consequently level-ice resistance is graded WITHHELD, not
REAL; see ``VALIDATION_STATUS`` and ``level_ice_validation_report()``.
A second, independently named Lindqvist implementation lives in
``services/digital_twin/models/resistance.py`` and differs from this one by up
to 2.2x at identical ship/ice conditions (documented in
docs/m1720_ice_resistance_validation_debt.md).

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


def lindqvist_terms(
    ship: ShipIceParams,
    ice_thickness_h: float,
    ice_strength_sigma_c: float,
    friction_coeff_mu: float,
    ship_speed_V: float,
) -> Dict[str, float]:
    """
    Lindqvist (1989) level-ice resistance, decomposed into its components.

    The total resistance is decomposed into:
      R = (R_crushing + R_bending) * (1 + 1.4 V / sqrt(g h))
          + R_submersion + R_drag

    The velocity multiplier of Lindqvist (1989) on the crushing + bending terms was
    missing in the original implementation (only an empirical quadratic drag term
    was present, so R changed by <2 % from 0 to 5 m/s, which no ice trial shows);
    restored in task m1720 after the audit in
    docs/m1720_ice_resistance_validation_debt.md.

    Returns
    -------
    Dict[str, float]
        Component resistances in kN, keys ``crushing``, ``bending``,
        ``submersion``, ``speed``, plus the dimensionless
        ``velocity_multiplier`` (the m1720-restored Lindqvist factor
        ``1 + 1.4 V / sqrt(g h)``). The raw (uncalibrated) total is
        reconstructed exactly as
        ``(crushing + bending) * velocity_multiplier + submersion + speed``
        and equals :func:`_lindqvist_raw`.

    Notes
    -----
    The decomposition is exposed so that the *term structure* of the prediction
    can be audited (see ``tools/verify_ice_resistance_evidence.py``): the
    ``submersion`` component carries an engineering length scale
    ``0.3 * L`` and the ``speed`` component a factor ``0.1``; neither constant
    can be sourced to the published formulation, so they must be visible to
    any accuracy claim built on top of this model.
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

    # --- 3. Submersion resistance ---
    # NOTE (m1720): this is NOT Lindqvist Eq. 12 verbatim.  Lindqvist closes the
    # submersion term on B and T only, R_s = rho g h B T (B+T)/(B+2T); the
    # implementation below multiplies a per-unit-length force by 0.3 L, i.e. it
    # introduces an explicit hull-length scaling.  Kept as-is (the per-ship
    # calibration absorbs it), documented instead of silently relabelled.
    rho_ice = 917.0  # ice density (kg/m³)
    if phi > 0.01:
        F_sub_per_l = (rho_w - rho_ice) * g * h * B * (1.0 + mu * math.cos(phi) / math.sin(phi))
    else:
        F_sub_per_l = (rho_w - rho_ice) * g * h * B
    F_submersion = F_sub_per_l * 0.3 * L

    # --- 4. Lindqvist velocity multiplier on the structural terms ---
    g = 9.81
    speed_factor = 1.0 + 1.4 * V / math.sqrt(g * h) if h > 0 else 1.0

    # --- 5. Speed-dependent hydrodynamic drag on broken ice ---
    C_speed = 0.5 * rho_w * B * h * V**2 * 0.1

    return {
        "crushing": F_crushing / 1000.0,
        "bending": F_bending / 1000.0,
        "submersion": F_submersion / 1000.0,
        "speed": C_speed / 1000.0,
        "velocity_multiplier": speed_factor,
    }


def _lindqvist_raw(
    ship: ShipIceParams,
    ice_thickness_h: float,
    ice_strength_sigma_c: float,
    friction_coeff_mu: float,
    ship_speed_V: float,
) -> float:
    """
    Raw (uncalibrated) Lindqvist total resistance in kN (m1720 corrected physics).

    R = (R_crushing + R_bending) * (1 + 1.4 V / sqrt(g h)) + R_submersion + R_drag

    This is the formula's own prediction; ``calibration_factor`` is ignored.
    Use :func:`lindqvist_ice_resistance_uncalibrated` as the public entry point.
    """
    t = lindqvist_terms(
        ship, ice_thickness_h, ice_strength_sigma_c, friction_coeff_mu, ship_speed_V
    )
    return (
        (t["crushing"] + t["bending"]) * t["velocity_multiplier"]
        + t["submersion"]
        + t["speed"]
    )


def lindqvist_ice_resistance_uncalibrated(
    ship: ShipIceParams,
    ice_thickness_h: float,
    ice_strength_sigma_c: float,
    friction_coeff_mu: float,
    ship_speed_V: float,
) -> float:
    """
    Lindqvist (1989) resistance **without** the per-ship trial calibration.

    This is the only value that can be compared against full-scale trial means
    without circularity; the calibrated variant reproduces the trial mean at
    nominal conditions by construction.
    """
    return _lindqvist_raw(
        ship, ice_thickness_h, ice_strength_sigma_c, friction_coeff_mu, ship_speed_V
    )


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
    Calibrated level-ice resistance in kN: ``R_raw * ship.calibration_factor``.

    The factor is fixed once per ship as
    ``R_trial_nominal / R_raw_nominal`` (:func:`_calibrate_ship`), i.e. it is an
    *anchor* that makes the model reproduce the platform-recorded full-scale
    trial mean at nominal conditions. Consequences that any downstream accuracy
    claim must respect:

    * The residual at nominal conditions is **zero by construction** (measured
      ``|R_cal - R_trial| / R_trial`` ~ 1e-14, not a validation result).
    * The factor is a single scalar, so it cannot repair the thickness / speed
      *scaling* of the formula: away from nominal conditions the calibrated
      model carries the same relative error as the raw model.
    * Use :func:`lindqvist_ice_resistance_uncalibrated` for an honest,
      non-circular comparison against trial data.
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


# ---------------------------------------------------------------------
# Quadrature-aliasing guard (task m1892, defect located by task m1882)
# ---------------------------------------------------------------------
#
# A total-order PCE of degree p is obtained by projection,
#     c_alpha = <f, psi_alpha> / <psi_alpha, psi_alpha>,
# evaluated with a Smolyak sparse grid of level L (1-based; the 1-D Gauss-Hermite
# rule at level k carries k points).  That rule integrates polynomials of total
# degree <= 2L - 1 exactly, so resolving the basis Gram matrix <psi_a, psi_b>
# (and hence the variance / Sobol split) of an order-p expansion requires
#     2L - 1 >= 2p   <=>   L >= p + 1 = max_poly_order + 1.
# The previously shipped default (max_level=3, max_poly_order=3) is exact only to
# total degree 5, while a *cubic* response already needs degree 6 in the psi_3
# column: the unresolved content aliases into unrelated coefficients.  Measured on
# the pure cubic response y = xi_0^3 with an inert second input xi_1
# (analytic: S_T(xi_1) = 0, Var = E[xi^6] = 15):
#     max_level = 3  ->  S_T(xi_1) = 0.1818 (2/11), Var = 11   (WRONG)
#     max_level = 4  ->  S_T(xi_1) = 0.0,           Var = 15   (exact)
# i.e. 18.2 % of the total-order sensitivity was handed to an input that cannot
# influence the response at all.  build_pce() now refuses/repairs that
# configuration by default; the raw behaviour stays reachable through
# quadrature_policy="allow_aliasing" for evidence only.

PCE_DEFAULT_MAX_POLY_ORDER: int = 3
#: Level that keeps the projection free of quadrature aliasing at the default
#: polynomial order; every entry point of this module defaults to it.
PCE_DEFAULT_MAX_LEVEL: int = PCE_DEFAULT_MAX_POLY_ORDER + 1


class PCEQuadratureAliasingError(ValueError):
    """Sparse-grid level too low to integrate two PCE basis functions exactly.

    The level-``L`` Smolyak rule integrates total degree ``2L - 1``; an
    order-``p`` expansion needs ``2L - 1 >= 2p`` for ``<psi_a, psi_b>``.  Below
    ``L = p + 1`` the response's higher-order content aliases into low-order
    coefficients and fabricates variance / Sobol mass for inputs with zero
    influence (tasks m1882, m1892).
    """


def required_quadrature_level(max_poly_order: int) -> int:
    """Smallest Smolyak level with ``2*level - 1 >= 2*max_poly_order``."""
    if max_poly_order < 0:
        raise ValueError(f"max_poly_order must be >= 0, got {max_poly_order}")
    return int(max_poly_order) + 1


def _resolve_quadrature_level(
    max_level: Optional[int], max_poly_order: int, quadrature_policy: str
) -> Tuple[int, int, bool]:
    """Return ``(level_requested, level_used, raised_to_required)``."""
    required = required_quadrature_level(max_poly_order)
    requested = required if max_level is None else int(max_level)
    if requested >= required:
        return requested, requested, False
    if quadrature_policy == "auto_raise":
        return requested, required, True
    if quadrature_policy in ("error", "strict"):
        raise PCEQuadratureAliasingError(
            f"max_level={requested} cannot resolve the order-{max_poly_order} "
            f"basis: the level-{requested} rule is exact only for total degree "
            f"{2 * requested - 1}, but <psi_a, psi_b> needs {2 * max_poly_order}. "
            f"Use max_level >= max_poly_order + 1 = {required} (the default "
            f"quadrature_policy='auto_raise' raises it automatically)."
        )
    if quadrature_policy == "allow_aliasing":
        return requested, requested, False
    raise ValueError(
        "quadrature_policy must be one of 'auto_raise', 'error', 'allow_aliasing'; "
        f"got {quadrature_policy!r}"
    )


def build_pce(
    model_func,
    uncertain_inputs: List[UncertainInput],
    max_level: Optional[int] = None,
    max_poly_order: int = PCE_DEFAULT_MAX_POLY_ORDER,
    quadrature_policy: str = "auto_raise",
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
    max_level : int or None
        Smolyak sparse grid level (controls the number of quadrature nodes).
        ``None`` selects the aliasing-free default ``max_poly_order + 1``.
        A level below ``max_poly_order + 1`` is handled by ``quadrature_policy``.
    max_poly_order : int
        Maximum total polynomial order.
    quadrature_policy : {'auto_raise', 'error', 'allow_aliasing'}
        What to do when ``max_level < max_poly_order + 1`` (aliasing regime):

        * ``'auto_raise'`` (default) - raise the level to
          ``max_poly_order + 1`` and record it in the result
          (``max_level_requested`` / ``max_level`` / ``quadrature_raised``);
        * ``'error'`` - raise :class:`PCEQuadratureAliasingError`;
        * ``'allow_aliasing'`` - keep the requested level.  Evidence use only:
          the resulting coefficients mix unresolved high-order content into
          low-order terms (task m1882).

    Returns
    -------
    dict with keys: coefficients, multi_indices, nodes, weights,
                    model_values, n_evaluations, uncertain_inputs,
                    max_level, max_level_requested, max_poly_order,
                    quadrature_raised, aliasing_free, quadrature_exactness_degree,
                    required_exactness_degree, quadrature_policy
    """
    dim = len(uncertain_inputs)
    if dim < 1:
        raise ValueError("build_pce needs at least one uncertain input")

    level_requested, level_used, level_raised = _resolve_quadrature_level(
        max_level, max_poly_order, quadrature_policy
    )
    max_level = level_used

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
        "max_level_requested": level_requested,
        "quadrature_raised": bool(level_raised),
        "aliasing_free": bool(max_level >= required_quadrature_level(max_poly_order)),
        "quadrature_exactness_degree": 2 * max_level - 1,
        "required_exactness_degree": 2 * max_poly_order,
        "quadrature_policy": quadrature_policy,
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

# ---------------------------------------------------------------------
# Declared sampling support (task m1892)
# ---------------------------------------------------------------------
#
# The Monte-Carlo reference used to clip every draw against four bounds that were
# hard-coded and selected by the *position* of the input in the list
# (``if d == 0: ... elif d == 3: ...``).  That is neither visible to the caller
# nor transferable to another input list, and it silently turned a Gaussian into
# a mixed distribution with atoms on the bounds.  The support is now a value the
# caller declares (:class:`TruncationSpec`), the sampling law is reported back
# (``monte_carlo_reference(...)["truncation"]``) and the pipeline level states
# the bounds it uses (``ICE_INPUT_TRUNCATION``), so the API response can say
# "truncated normal" instead of pretending the quadrature and the sampler draw
# from the same law.

@dataclass(frozen=True)
class TruncationSpec:
    """Declared support ``[lower, upper]`` of one uncertain input."""
    input_name: str
    lower: float = -math.inf
    upper: float = math.inf
    rationale: str = ""

    def is_bounded(self) -> bool:
        return math.isfinite(self.lower) or math.isfinite(self.upper)

    def mass_outside(self, inp: UncertainInput) -> float:
        """Probability mass of N(inp.mean, inp.std) outside the declared support."""
        sd = inp.std
        if sd <= 0.0 or not self.is_bounded():
            return 0.0
        below = _standard_normal_cdf((self.lower - inp.mean) / sd) if math.isfinite(self.lower) else 0.0
        above = 1.0 - _standard_normal_cdf((self.upper - inp.mean) / sd) if math.isfinite(self.upper) else 0.0
        return float(min(1.0, max(0.0, below + above)))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "input": self.input_name,
            "lower": None if not math.isfinite(self.lower) else float(self.lower),
            "upper": None if not math.isfinite(self.upper) else float(self.upper),
            "rationale": self.rationale,
        }


def _standard_normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _resolve_truncation(
    uncertain_inputs: Sequence[UncertainInput],
    truncation: Optional[Sequence[TruncationSpec]],
) -> List[TruncationSpec]:
    """Align declared truncation specs with ``uncertain_inputs`` (by name)."""
    if truncation is None:
        return [TruncationSpec(inp.name) for inp in uncertain_inputs]
    by_name = {spec.input_name: spec for spec in truncation}
    names = [inp.name for inp in uncertain_inputs]
    unknown = sorted(set(by_name) - set(names))
    if unknown:
        raise ValueError(
            f"truncation declared for unknown inputs {unknown}; known: {names}"
        )
    missing = [n for n in names if n not in by_name]
    if missing:
        raise ValueError(
            f"truncation not declared for inputs {missing}; declare every input "
            f"explicitly (use TruncationSpec(name) for unbounded support)"
        )
    return [by_name[n] for n in names]


def _sample_inputs(
    rng: np.random.Generator,
    uncertain_inputs: Sequence[UncertainInput],
    specs: Sequence[TruncationSpec],
    n_samples: int,
    mode: str = "reject",
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Draw ``n_samples`` input vectors honouring the declared support.

    ``mode='reject'`` samples the exact truncated normal by resampling
    (no atoms); ``mode='clip'`` clips the tails onto the bound (kept only so the
    old behaviour stays reproducible in evidence runs).  Returns the sample
    matrix plus a machine-readable description of the sampling law.
    """
    if mode not in ("reject", "clip"):
        raise ValueError(f"truncation_mode must be 'reject' or 'clip', got {mode!r}")
    if n_samples < 1:
        raise ValueError(f"n_samples must be >= 1, got {n_samples}")
    if len(specs) != len(uncertain_inputs):
        raise ValueError("truncation specs must match the uncertain inputs")

    means = np.array([i.mean for i in uncertain_inputs], dtype=float)
    stds = np.array([i.std for i in uncertain_inputs], dtype=float)
    lo = np.array([s.lower for s in specs], dtype=float)
    hi = np.array([s.upper for s in specs], dtype=float)
    bounded = np.isfinite(lo) | np.isfinite(hi)

    x = means + stds * rng.standard_normal((n_samples, len(means)))
    outside = np.zeros(len(means), dtype=int)
    n_drawn = n_samples
    n_redrawn = 0

    if bounded.any():
        outside += ((x < lo) | (x > hi)).sum(axis=0)
        if mode == "clip":
            x = np.minimum(np.maximum(x, lo), hi)
        else:
            bad = (x < lo) | (x > hi)
            rounds = 0
            while bad.any():
                rounds += 1
                if rounds > 1000:
                    raise RuntimeError(
                        "truncation rejection sampling did not converge; the "
                        "declared support is too far in the tail of the input law"
                    )
                rows = np.where(bad.any(axis=1))[0]
                fresh = means + stds * rng.standard_normal((len(rows), len(means)))
                n_drawn += len(rows)
                n_redrawn += len(rows)
                outside += ((fresh < lo) | (fresh > hi)).sum(axis=0)
                x[rows] = fresh
                bad[rows] = (fresh < lo) | (fresh > hi)

    per_input = []
    for d, inp in enumerate(uncertain_inputs):
        row = specs[d].as_dict()
        row["mass_outside"] = specs[d].mass_outside(inp)
        row["realized_outside_fraction"] = float(outside[d] / max(1, n_drawn))
        per_input.append(row)

    diagnostics = {
        "truncated_distribution": bool(bounded.any()),
        "truncation_mode": mode if bounded.any() else "none",
        "per_input": per_input,
        "max_mass_outside": float(max(r["mass_outside"] for r in per_input)),
        "n_draws": int(n_drawn),
        "n_redrawn": int(n_redrawn),
    }
    return x, diagnostics


def monte_carlo_reference(
    model_func,
    uncertain_inputs: List[UncertainInput],
    n_samples: int = 100000,
    seed: int = 42,
    truncation: Optional[Sequence[TruncationSpec]] = None,
    truncation_mode: str = "reject",
) -> Dict[str, Any]:
    """
    Compute reference mean and std via Monte Carlo sampling.

    This serves as the ground truth for validating PCE statistics.

    Sampling law (task m1892)
    -------------------------
    The support is *declared by the caller* through ``truncation`` (one
    :class:`TruncationSpec` per input, matched by name):

    * ``truncation=None`` - plain iid normals, **nothing is clipped**.  A generic
      Monte-Carlo reference must not invent support bounds behind the caller's
      back (the previous implementation clipped four hard-coded bounds keyed on
      the *position* of the input in the list);
    * ``truncation_mode='reject'`` - exact truncated-normal draws by resampling,
      so no probability mass is placed on the bounds;
    * ``truncation_mode='clip'`` - tails clipped onto the bound (atoms), only for
      reproducing the legacy behaviour in evidence runs.

    The realised law is reported under the ``"truncation"`` key, so a caller (and
    the API response) can state that the reference is a truncated normal rather
    than the untruncated one the PCE quadrature integrates.
    """
    rng = np.random.default_rng(seed)
    specs = _resolve_truncation(uncertain_inputs, truncation)
    x_phys, truncation_diag = _sample_inputs(
        rng, uncertain_inputs, specs, n_samples, mode=truncation_mode
    )

    samples = np.array([model_func(*row) for row in x_phys], dtype=float)

    return {
        "mean": float(np.mean(samples)),
        "std": float(np.std(samples)),
        "variance": float(np.var(samples)),
        "n_samples": n_samples,
        "truncation": truncation_diag,
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
    # Numerics provenance (task m1892): sparse-grid level actually used and the
    # explicitly declared truncation of the sampled input law.
    max_level: Optional[int] = None
    max_poly_order: Optional[int] = None
    quadrature_aliasing_free: Optional[bool] = None
    distribution_truncation: Optional[Dict[str, Any]] = None

    # The API response model (``api.domain.ice_uq.UQResponse``) reads the MC
    # reference under the names ``reference_mean_kN`` / ``reference_std_kN``;
    # those attributes did not exist on this dataclass, so ``POST /ice-uq/uq``
    # raised AttributeError on every request.  Exposed as aliases (task m1892).
    @property
    def reference_mean_kN(self) -> Optional[float]:
        return self.mc_mean_kN

    @property
    def reference_std_kN(self) -> Optional[float]:
        return self.mc_std_kN


#: Support bounds declared by the ice-resistance entry point (task m1892).
#: These used to be hidden inside ``monte_carlo_reference`` (index-keyed clamps);
#: they now live here, where the UQ study is defined, and are echoed into the
#: result so the API can report "truncated normal".
ICE_INPUT_TRUNCATION: Tuple[TruncationSpec, ...] = (
    TruncationSpec(
        "ice_strength_MPa", 0.3, math.inf,
        "compressive strength is strictly positive; 0.3 MPa is the lower "
        "envelope of the ice covers the model is calibrated on",
    ),
    TruncationSpec(
        "ice_thickness_m", 0.1, math.inf,
        "level-ice thickness below 0.1 m is outside the Lindqvist calibration "
        "domain (bending term ~ h^2, crushing ~ h^2 with l_c ~ h^0.75)",
    ),
    TruncationSpec(
        "friction_coeff", 0.02, 0.5,
        "hull-ice friction support observed in full-scale trials (0.02 - 0.5)",
    ),
    TruncationSpec(
        "ship_speed_ms", 0.5, math.inf,
        "below 0.5 m/s the ship is not advancing continuously in level ice "
        "(ramming regime, not modelled)",
    ),
)


def run_ice_resistance_uq(
    ship_name: str,
    max_level: Optional[int] = PCE_DEFAULT_MAX_LEVEL,
    max_poly_order: int = PCE_DEFAULT_MAX_POLY_ORDER,
    n_mc_validation: int = 10000,
    n_mc_reference: int = 100000,
    truncation: Optional[Sequence[TruncationSpec]] = None,
    truncation_mode: str = "reject",
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

    Numbers of merit (task m1892):
      * the PCE level is never allowed to alias: ``build_pce`` raises it to
        ``max_poly_order + 1`` when the requested level cannot resolve the basis,
        and the level actually used is stored on the result;
      * both Monte-Carlo stages draw from the *declared* truncated distribution
        (:data:`ICE_INPUT_TRUNCATION` unless ``truncation`` overrides it).  The
        quadrature itself still integrates the untruncated normal, so the
        removed mass is reported (``distribution_truncation``) instead of being
        silently absorbed.
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

    # Declared support of the sampled input law (entry-point level, task m1892).
    # Default: the bounds this study declares for the four ice inputs; a caller
    # may override them (or pass explicit unbounded specs) but never gets the old
    # hidden index-keyed clipping back.
    truncation_specs = _resolve_truncation(
        uncertain_inputs, ICE_INPUT_TRUNCATION if truncation is None else truncation
    )

    # Step 1: Compute MC reference against the declared distribution
    mc_ref = monte_carlo_reference(
        ice_model,
        uncertain_inputs,
        n_samples=n_mc_reference,
        truncation=truncation_specs,
        truncation_mode=truncation_mode,
    )

    # Step 2: Build PCE (level auto-raised when it cannot resolve the basis)
    pce = build_pce(ice_model, uncertain_inputs, max_level=max_level, max_poly_order=max_poly_order)

    # Step 3: Compute PCE statistics
    stats = pce_statistics(pce)
    ci = pce_confidence_interval(pce, confidence=0.95)
    sobol = sobol_indices(pce)

    # Step 4: Validate CI coverage via Monte Carlo - same declared truncation as
    # the reference (no index-keyed clamps any more).
    rng = np.random.default_rng(123)
    x_cov, _ = _sample_inputs(
        rng, uncertain_inputs, truncation_specs, n_mc_validation,
        mode=truncation_mode,
    )
    y_cov = np.array([ice_model(*row) for row in x_cov], dtype=float)
    ci_coverage = float(np.mean((y_cov >= ci["lower"]) & (y_cov <= ci["upper"])))

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
        max_level=int(pce["max_level"]),
        max_poly_order=int(max_poly_order),
        quadrature_aliasing_free=bool(pce["aliasing_free"]),
        distribution_truncation=mc_ref["truncation"],
    )


def run_all_ships_uq(
    max_level: Optional[int] = PCE_DEFAULT_MAX_LEVEL,
    max_poly_order: int = PCE_DEFAULT_MAX_POLY_ORDER,
    truncation: Optional[Sequence[TruncationSpec]] = None,
) -> Dict[str, IceResistanceUQResult]:
    """Run UQ for all ships in the trial database.

    The default level is ``PCE_DEFAULT_MAX_LEVEL = max_poly_order + 1``, i.e. the
    aliasing-free configuration required by the guard in :func:`build_pce`
    (task m1892).  A caller may still ask for a lower level - it is then raised
    back (``quadrature_aliasing_free`` on each result records the outcome) rather
    than silently corrupting the Sobol split.
    """
    results = {}
    for ship_name in SHIP_TRIAL_DATA:
        results[ship_name] = run_ice_resistance_uq(
            ship_name, max_level, max_poly_order, truncation=truncation
        )
    return results


# =====================================================================
# 5. Validation-status surface (task m1720)
# =====================================================================
#
# The PCE/UQ machinery above is validated against a Monte-Carlo reference and
# that part is sound.  What was *not* validated is the underlying physical
# model: every ship carries a scalar fitted on its own trial point, so any
# "model vs trial" number computed here is an identity.  Rather than deleting
# the fit (which the UQ studies rely on for realistic magnitudes), the debt is
# exposed machine-readably below and pinned by
# tests/test_m1720_ice_resistance_validation_audit.py.

VALIDATION_STATUS: Dict[str, Any] = {
    "capability": "ice level-ice (Lindqvist) resistance",
    "evidence_grade": "WITHHELD",
    "in_sample_protocol": "calibration_factor = R_trial / R_raw (same point)",
    "in_sample_rel_err": 1.9e-16,
    "out_of_sample_method": "leave-one-out with one global constant (3 ships)",
    "out_of_sample_rel_err": {
        "MV_Arctic": 0.0320,
        "Polarstern": 0.2294,
        "Terry_Fox": -0.2118,
    },
    "out_of_sample_max_abs_rel_err": 0.2294,
    "meets_15pct_band_out_of_sample": False,
    "fixed_in_m1720": "Lindqvist velocity multiplier (1 + 1.4 V/sqrt(g h)) was missing "
                      "on the crushing+bending terms (R changed <2 % from 0 to 5 m/s); "
                      "restored - MV Arctic out-of-sample error 11.8 % -> 3.2 %, "
                      "Polarstern/Terry Fox still outside the 15 % band",
    "reference_provenance": "trial means hard-coded in SHIP_TRIAL_DATA; originals not "
                            "digitised inside this worktree - unverifiable offline",
    "known_conflict": "services/digital_twin/models/resistance.py implements a second "
                      "Lindqvist variant (SUBMERSION_COEFF=0.55, no per-ship fit) that "
                      "returns 0.46-0.67x of this module at identical conditions",
    "audit_artifacts": "artifacts/m1720/audit_ice_resistance.json",
    "audit_doc": "docs/m1720_ice_resistance_validation_debt.md",
}


def level_ice_validation_report() -> Dict[str, Any]:
    """Return the validation state of the level-ice resistance model.

    Machine-readable twin of ``VALIDATION_STATUS``: recomputes the in-sample
    identity residual, the leave-one-out (single constant) errors and the
    disagreement with the digital-twin implementation.  Cheap (pure formula
    evaluation), safe to call from tests or an API diagnostics endpoint.
    """
    names = list(SHIP_TRIAL_DATA)
    per_ship = []
    for name in names:
        trial = SHIP_TRIAL_DATA[name]
        ship = trial["params"]
        raw = _lindqvist_raw(
            ship,
            trial["nominal_ice_thickness_m"],
            trial["nominal_ice_strength_MPa"],
            trial["nominal_friction_coeff"],
            trial["nominal_speed_ms"],
        )
        ref = trial["mean_resistance_kN"]
        per_ship.append(
            {
                "ship": name,
                "raw_kN": raw,
                "reference_kN": ref,
                "calibration_factor": ship.calibration_factor,
                "in_sample_rel_err": abs(raw * ship.calibration_factor - ref) / ref,
            }
        )

    raw_by_name = {row["ship"]: row["raw_kN"] for row in per_ship}
    ref_by_name = {row["ship"]: row["reference_kN"] for row in per_ship}
    loo = []
    for held in names:
        others = [n for n in names if n != held]
        logs = [math.log(ref_by_name[n] / raw_by_name[n]) for n in others]
        k = math.exp(sum(logs) / len(logs))
        pred = k * raw_by_name[held]
        loo.append(
            {
                "held_out": held,
                "global_k": k,
                "predicted_kN": pred,
                "reference_kN": ref_by_name[held],
                "rel_err": (pred - ref_by_name[held]) / ref_by_name[held],
            }
        )

    divergence = []
    try:  # local import keeps the services package free of an import cycle
        from services.digital_twin.models.resistance import (
            lindqvist_ice_resistance as _dt_ice,
        )

        for name in names:
            trial = SHIP_TRIAL_DATA[name]
            ship = trial["params"]
            here = raw_by_name[name] * ship.calibration_factor
            there = _dt_ice(
                trial["nominal_speed_ms"], ship.length_m, ship.beam_m, ship.draft_m,
                ship.cb, 1.0, trial["nominal_ice_thickness_m"],
            )["R_ice_N"] / 1000.0
            divergence.append(
                {"ship": name, "this_module_kN": here, "digital_twin_kN": there,
                 "ratio_dt_over_this": there / here}
            )
    except Exception as exc:  # pragma: no cover - defensive
        divergence = [{"error": repr(exc)}]

    return {
        "evidence_grade": VALIDATION_STATUS["evidence_grade"],
        "per_ship": per_ship,
        "max_in_sample_rel_err": max(r["in_sample_rel_err"] for r in per_ship),
        "leave_one_out": loo,
        "max_out_of_sample_abs_rel_err": max(abs(r["rel_err"]) for r in loo),
        "meets_15pct_band_out_of_sample": max(abs(r["rel_err"]) for r in loo) <= 0.15,
        "implementation_divergence": divergence,
    }


# =====================================================================
# 6. PCE numerics status (task m1892)
# =====================================================================
#
# Machine-readable twin of the aliasing guard: it recomputes the canonical
# counter-example (pure cubic response, one inert input) on the production code
# path, keeps the raw aliased numbers for the record, and lists the truncation
# mass the entry point declares.  Cheap enough for tests and diagnostics.

PCE_NUMERICS_STATUS: Dict[str, Any] = {
    "capability": "PCE surrogate + Sobol decomposition for ice-resistance UQ",
    "evidence_grade": "REAL",
    "guard": "build_pce raises max_level to max_poly_order + 1 "
             "(quadrature_policy='auto_raise'); 'error' raises "
             "PCEQuadratureAliasingError instead",
    "fixed_by": "m1892 (defect located by m1882)",
    "defect": "max_level=3 with max_poly_order=3 integrates total degree 5 < 6 "
              "needed for <psi_3, psi_3>, so cubic responses alias: on y=xi0^3 "
              "with an inert xi1 it reports S_T(xi1)=0.1818 and Var=11",
    "counter_example": "y = xi_0^3, xi_1 inert; analytic S_T(xi_1)=0, "
                       "Var=E[xi^6]=15",
    "declared_truncation": "ICE_INPUT_TRUNCATION (pipeline) / "
                           "monte_carlo_reference(truncation=..., "
                           "truncation_mode='reject'|'clip')",
}


def pce_numerics_status() -> Dict[str, Any]:
    """Recompute the aliasing / truncation evidence on the live code path."""
    inputs = [
        UncertainInput("xi0", nominal=1.0, std_relative=1.0),
        UncertainInput("xi1", nominal=1.0, std_relative=1.0),
    ]

    def cubic(x0, x1):
        return (x0 - 1.0) ** 3  # = xi0^3 in standard space; xi1 cannot matter

    analytic = {"mean": 0.0, "variance": 15.0, "ST_inert": 0.0}

    guarded = build_pce(cubic, inputs, max_poly_order=3)
    g_stats = pce_statistics(guarded)
    g_sobol = sobol_indices(guarded)

    aliased = build_pce(
        cubic, inputs, max_level=3, max_poly_order=3,
        quadrature_policy="allow_aliasing",
    )
    a_stats = pce_statistics(aliased)
    a_sobol = sobol_indices(aliased)

    nominal = {
        "ice_strength_MPa": (1.5, 0.20),
        "ice_thickness_m": (1.0, 0.15),
        "friction_coeff": (0.15, 0.25),
        "ship_speed_ms": (3.0, 0.10),
    }
    truncation = []
    for spec in ICE_INPUT_TRUNCATION:
        mean, rel = nominal[spec.input_name]
        inp = UncertainInput(spec.input_name, mean, rel)
        truncation.append({**spec.as_dict(), "mass_outside": spec.mass_outside(inp)})

    return {
        "status": PCE_NUMERICS_STATUS,
        "analytic": analytic,
        "guarded": {
            "max_level_requested": guarded["max_level_requested"],
            "max_level": guarded["max_level"],
            "quadrature_raised": guarded["quadrature_raised"],
            "aliasing_free": guarded["aliasing_free"],
            "variance": g_stats["variance"],
            "mean": g_stats["mean"],
            "S_T_inert": g_sobol["total_order"]["xi1"],
            "n_evaluations": guarded["n_evaluations"],
            "variance_rel_err": abs(g_stats["variance"] - analytic["variance"])
            / analytic["variance"],
            "inert_ST_abs_err": abs(g_sobol["total_order"]["xi1"]),
        },
        "aliased_reference": {
            "max_level_requested": aliased["max_level_requested"],
            "max_level": aliased["max_level"],
            "aliasing_free": aliased["aliasing_free"],
            "variance": a_stats["variance"],
            "S_T_inert": a_sobol["total_order"]["xi1"],
            "n_evaluations": aliased["n_evaluations"],
        },
        "declared_truncation": truncation,
        "max_truncation_mass": max(r["mass_outside"] for r in truncation),
        "verdict_aliasing_fixed": bool(
            guarded["aliasing_free"]
            and g_sobol["total_order"]["xi1"] <= 1e-6
            and abs(g_stats["variance"] - 15.0) <= 1e-9
        ),
    }
