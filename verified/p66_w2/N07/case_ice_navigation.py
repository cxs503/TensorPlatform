"""Ice-navigation UQ case: input model, forward chain and quantities of interest.

Scope of this file
------------------
This is the *application* layer of the UQ study (campaign node P66-W2-N07).  The
generic machinery lives in :mod:`backend.services.uq_propagation` and carries no
domain semantics; everything ice-navigation-specific is here, as an artifact
that injects parameters into that machinery.

Physics used, and what it is anchored on
----------------------------------------
1. **Ice-hull contact pressure closure** (stage 1)::

       p_s = p_ref * (sigma_c / sigma_c,ref) * sqrt(h / h_ref)
                 * sqrt(V / V_ref) * phi_s

   * ``p_ref = 50 kPa`` is the platform's abrasion kernel reference pressure
     (``backend.services.ice_abrasion.PRESSURE_REFERENCE``), so the nominal case
     reproduces the kernel's own nominal condition (no free knob at the anchor
     point).
   * ``p ~ sigma_c``: indentation pressure of ice scales with its compressive
     strength; ``p ~ sqrt(h)`` and ``p ~ sqrt(V)``: larger contact patch and
     stronger dynamic loading with thicker ice / higher speed (Lindqvist's
     velocity multiplier is the same physical idea, see
     ``backend.services.ice_resistance_uq.lindqvist_terms``).
   * ``phi_s`` is a deterministic station shape factor on the ice belt,
     normalised to mean 1 over the stations, so it redistributes wear along the
     hull without changing the voyage mean.
   * **Status: WITHHELD for absolute accuracy** -- the pressure closure is a
     surrogate for a contact-mechanics computation, not a validated model.  The
     UQ conclusions below are conditional on it (stated again in the report).

2. **Archard abrasion** (stage 2) -- exactly the platform kernel::

       d_s = K * p_s * s_s / H,   s_s = L_ice * 1852 * c * beta * phi_s

   with ``K`` the effective wear coefficient, ``H`` the ice-belt steel hardness,
   ``c`` the ice concentration, ``beta`` the areal contact fraction and ``L_ice``
   the in-ice track length.  At nominal inputs the predicted rate is
   ~0.22 mm / 1000 nm, inside the platform's service-record band
   (``ABRASION_RATE_BAND_MM_PER_1000NM = (0.1, 1.0)`` mm/1000 nm).

3. **Temporal evolution** (N05) -- year ``y`` of the service life sees a
   thickness multiplier ``exp(eps_y)`` with i.i.d. ``eps_y ~ N(0, 0.25)``
   (year-to-year ice severity).  The cumulative field after ``Y`` years is the
   sum of the annual increments.  Because the yearly factors are i.i.d. and
   enter the increment, the *variance of the cumulative field grows linearly in
   ``Y``*, i.e. the standard deviation grows as ``sqrt(Y)`` -- an analytic
   anchor the run checks numerically (fitted log-log slope must be 0.5).

4. **Level-ice resistance** (N04) -- the platform's Lindqvist implementation,
   *uncalibrated* (``lindqvist_ice_resistance_uncalibrated``), so the reported
   uncertainty is not contaminated by a trial-anchored calibration factor.
   Nominal raw value 4834 kN vs. the platform-recorded trial mean 4800 kN
   (-0.7 %), which is a sanity check, not a validation of the UQ itself.

Quantities of interest
----------------------
* ``qoi_p95_depth_mm``  (N03): 95th percentile of the abrasion-depth field over
  the ice-belt stations after the full service life [mm];
* ``qoi_resistance_kN`` (N04): total level-ice resistance [kN];
* ``qoi_field_max_mm``  (N05): maximum station depth of the *cumulative* field
  at year ``Y`` (per-year curve, with confidence bands).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from backend.services.ice_resistance_uq import (
    SHIP_TRIAL_DATA,
    lindqvist_ice_resistance_uncalibrated,
    lindqvist_terms,
)
from backend.services.ice_abrasion import (
    CONTACT_FRACTION_DEFAULT,
    PA_PER_HB,
    PRESSURE_REFERENCE,
    STEEL_HB,
    default_k_eff,
    wear_depth_from_pressure,
)
from backend.services.uq_propagation import ChainStage, InputSpec

# ---------------------------------------------------------------------------
# Case constants (all documented; no hidden tuning knobs)
# ---------------------------------------------------------------------------

SHIP_NAME = "MV_Arctic"
SHIP = SHIP_TRIAL_DATA[SHIP_NAME]["params"]

N_STATIONS = 12                       # ice-belt stations along the hull
P_REF = PRESSURE_REFERENCE            # 50 kPa, abrasion-kernel reference pressure
SIGMA_C_REF = 1.5e6                   # Pa, reference ice compressive strength
H_REF = 0.8                           # m, reference level-ice thickness
V_REF = 3.0                           # m/s, reference ship speed
BETA = CONTACT_FRACTION_DEFAULT       # areal contact fraction (0.02)
SERVICE_YEARS = 20
IN_ICE_TRACK_NM = 40000.0             # in-ice track over the service life [nm]
ANNUAL_IN_ICE_NM = IN_ICE_TRACK_NM / SERVICE_YEARS
YEARLY_LOG_SIGMA = 0.25               # i.i.d. year-to-year ice severity
K_NOMINAL = default_k_eff()           # 2.680e-4 (platform Archard anchor band)
H_NOMINAL = STEEL_HB * PA_PER_HB      # 160 HB steel -> Pa

YEAR_MARKS = (5, 10, 15, 20)          # years at which the field is reported


def station_shape(n: int = N_STATIONS, waviness: float = 0.3) -> np.ndarray:
    """Deterministic ice-belt shape factor, normalised to mean 1."""
    s = (np.arange(n) + 0.5) / n
    phi = 1.0 + waviness * np.cos(2.0 * np.pi * (s - 0.15))
    return phi / float(np.mean(phi))


PHI = station_shape()


# ---------------------------------------------------------------------------
# Input model (7 epistemic inputs)
# ---------------------------------------------------------------------------

INPUT_SPECS: List[InputSpec] = [
    InputSpec(name="h_ice", kind="lognormal", mean=H_REF, std=0.25, truncate_sigma=4.0),
    InputSpec(name="c_ice", kind="normal", mean=0.70, std=0.12, truncate_sigma=3.0),
    InputSpec(name="V_ship", kind="normal", mean=V_REF, std=0.35, truncate_sigma=3.0),
    InputSpec(name="sigma_c", kind="lognormal", mean=1.5e6, std=0.45e6, truncate_sigma=4.0),
    InputSpec(name="mu_ice", kind="normal", mean=0.15, std=0.03, truncate_sigma=3.0),
    InputSpec(name="K_wear", kind="lognormal", mean=K_NOMINAL, std=1.0e-4, truncate_sigma=4.0),
    InputSpec(name="H_steel", kind="normal", mean=H_NOMINAL, std=8.0 * PA_PER_HB, truncate_sigma=3.0),
]

NAMES = [s.name for s in INPUT_SPECS]
IDX = {nm: i for i, nm in enumerate(NAMES)}
# resistance branch only sees thickness, strength, friction and speed
RESISTANCE_IDX = [IDX["h_ice"], IDX["sigma_c"], IDX["mu_ice"], IDX["V_ship"]]
RESISTANCE_SPECS = [INPUT_SPECS[j] for j in RESISTANCE_IDX]

NOMINAL = np.array([s.moments()[0] for s in INPUT_SPECS], dtype=float)


# ---------------------------------------------------------------------------
# Chain stages
# ---------------------------------------------------------------------------


def contact_pressure(X: np.ndarray) -> np.ndarray:
    """Stage 1: ice-hull contact pressure field [Pa], shape ``(n, N_STATIONS)``."""
    X = np.atleast_2d(np.asarray(X, dtype=float))
    h = X[:, IDX["h_ice"]]
    sigma_c = X[:, IDX["sigma_c"]]
    V = X[:, IDX["V_ship"]]
    p = (P_REF
         * (sigma_c / SIGMA_C_REF)[:, None]
         * np.sqrt(h / H_REF)[:, None]
         * np.sqrt(V / V_REF)[:, None]
         * PHI[None, :])
    return p


def wear_field(X: np.ndarray) -> np.ndarray:
    """Stage 2: end-of-life abrasion depth field [m], shape ``(n, N_STATIONS)``."""
    X = np.atleast_2d(np.asarray(X, dtype=float))
    p = contact_pressure(X)
    K = X[:, IDX["K_wear"]]
    H = X[:, IDX["H_steel"]]
    c = X[:, IDX["c_ice"]]
    slide = (IN_ICE_TRACK_NM * 1852.0) * c[:, None] * BETA * PHI[None, :]
    out = np.empty_like(p)
    for j in range(N_STATIONS):
        out[:, j] = [wear_depth_from_pressure(float(K[i]), float(p[i, j]),
                                              float(slide[i, j]), float(H[i]))
                     for i in range(X.shape[0])]
    return out


def annual_increment(X: np.ndarray) -> np.ndarray:
    """Annual abrasion-depth increment per station [m], shape ``(n, N_STATIONS)``."""
    X = np.atleast_2d(np.asarray(X, dtype=float))
    p = contact_pressure(X)
    K = X[:, IDX["K_wear"]]
    H = X[:, IDX["H_steel"]]
    c = X[:, IDX["c_ice"]]
    slide = (ANNUAL_IN_ICE_NM * 1852.0) * c[:, None] * BETA * PHI[None, :]
    out = np.empty_like(p)
    for j in range(N_STATIONS):
        out[:, j] = [wear_depth_from_pressure(float(K[i]), float(p[i, j]),
                                              float(slide[i, j]), float(H[i]))
                     for i in range(X.shape[0])]
    return out


def cumulative_field(X: np.ndarray, eps: np.ndarray) -> np.ndarray:
    """Cumulative field over the service life, shape ``(n, years, stations)`` [m].

    ``eps`` holds the year-to-year log multipliers, shape ``(n, years)``; the
    annual increment scales with ``sqrt(exp(eps_y))`` because the contact
    pressure scales with ``sqrt(h_y)``.
    """
    X = np.atleast_2d(np.asarray(X, dtype=float))
    eps = np.asarray(eps, dtype=float)
    inc = annual_increment(X)                                  # (n, stations)
    scale = np.exp(0.5 * eps)                                  # (n, years)
    yearly = inc[:, None, :] * scale[:, :, None]               # (n, years, stations)
    return np.cumsum(yearly, axis=1)                           # (n, years, stations)


# ---------------------------------------------------------------------------
# Quantities of interest
# ---------------------------------------------------------------------------


def qoi_p95_depth_mm(X: np.ndarray) -> np.ndarray:
    """N03: 95th percentile of the abrasion-depth field over the stations [mm]."""
    d = wear_field(X)
    return 1.0e3 * np.quantile(d, 0.95, axis=1)


def qoi_max_depth_mm(X: np.ndarray) -> np.ndarray:
    d = wear_field(X)
    return 1.0e3 * np.max(d, axis=1)


def qoi_mean_depth_mm(X: np.ndarray) -> np.ndarray:
    d = wear_field(X)
    return 1.0e3 * np.mean(d, axis=1)


def qoi_resistance_kN(X: np.ndarray) -> np.ndarray:
    """N04: uncalibrated Lindqvist total level-ice resistance [kN]."""
    X = np.atleast_2d(np.asarray(X, dtype=float))
    h = X[:, IDX["h_ice"]]
    sigma_c = X[:, IDX["sigma_c"]] / 1.0e6                 # Pa -> MPa for the kernel
    mu = X[:, IDX["mu_ice"]]
    V = X[:, IDX["V_ship"]]
    return np.array([lindqvist_ice_resistance_uncalibrated(SHIP, float(h[i]), float(sigma_c[i]),
                                                          float(mu[i]), float(V[i]))
                     for i in range(X.shape[0])])


def resistance_bundle(X4: np.ndarray) -> np.ndarray:
    """Resistance branch as a 5-column stage output (4 terms + total) [kN]."""
    X4 = np.atleast_2d(np.asarray(X4, dtype=float))
    rows = []
    for i in range(X4.shape[0]):
        t = lindqvist_terms(SHIP, float(X4[i, 0]), float(X4[i, 1]) / 1.0e6,
                            float(X4[i, 2]), float(X4[i, 3]))
        total = ((t["crushing"] + t["bending"]) * t["velocity_multiplier"]
                 + t["submersion"] + t["speed"])
        rows.append([t["crushing"], t["bending"], t["submersion"], t["speed"], total])
    return np.asarray(rows, dtype=float)


RESISTANCE_OUT_NAMES = ["R_crushing", "R_bending", "R_submersion", "R_speed", "R_total"]


# ---------------------------------------------------------------------------
# Propagation chains (structure for the error-propagation-path analysis)
# ---------------------------------------------------------------------------


def _expand6(X6: np.ndarray) -> np.ndarray:
    """Map the 6 abrasion inputs back onto the full 7-input layout (mu untouched)."""
    X6 = np.atleast_2d(np.asarray(X6, dtype=float))
    X7 = np.tile(NOMINAL, (X6.shape[0], 1))
    X7[:, ABRASION_IDX] = X6
    return X7


def _stage_state(X6: np.ndarray) -> np.ndarray:
    """Carry the pass-through state: ``[p_1..p_n, c, K, H]``."""
    X = _expand6(X6)
    p = contact_pressure(X)
    return np.column_stack([p, X[:, IDX["c_ice"]], X[:, IDX["K_wear"]], X[:, IDX["H_steel"]]])


def _stage_depth_from_state(S: np.ndarray) -> np.ndarray:
    S = np.atleast_2d(np.asarray(S, dtype=float))
    p = S[:, :N_STATIONS]
    c = S[:, N_STATIONS]
    K = S[:, N_STATIONS + 1]
    H = S[:, N_STATIONS + 2]
    slide = (IN_ICE_TRACK_NM * 1852.0) * c[:, None] * BETA * PHI[None, :]
    out = np.empty_like(p)
    for j in range(N_STATIONS):
        out[:, j] = [wear_depth_from_pressure(float(K[i]), float(p[i, j]),
                                              float(slide[i, j]), float(H[i]))
                     for i in range(S.shape[0])]
    return out


def _stage_annual_from_state(S: np.ndarray) -> np.ndarray:
    S = np.atleast_2d(np.asarray(S, dtype=float))
    p = S[:, :N_STATIONS]
    c = S[:, N_STATIONS]
    K = S[:, N_STATIONS + 1]
    H = S[:, N_STATIONS + 2]
    slide = (ANNUAL_IN_ICE_NM * 1852.0) * c[:, None] * BETA * PHI[None, :]
    out = np.empty_like(p)
    for j in range(N_STATIONS):
        out[:, j] = [wear_depth_from_pressure(float(K[i]), float(p[i, j]),
                                              float(slide[i, j]), float(H[i]))
                     for i in range(S.shape[0])]
    return out


def _stage_year_marks(inc: np.ndarray) -> np.ndarray:
    """Expected-severity cumulative field at the year marks -> ``(n, 4*stations)``."""
    inc = np.atleast_2d(np.asarray(inc, dtype=float))
    return np.concatenate([inc * float(y) for y in YEAR_MARKS], axis=1)


# the abrasion branch has no friction input at all: including it would make the
# chain Jacobian structurally rank deficient over its 7-dimensional input space
ABRASION_IDX = [j for j in range(len(INPUT_SPECS)) if j != IDX["mu_ice"]]
ABRASION_SPECS = [INPUT_SPECS[j] for j in ABRASION_IDX]
ABRASION_NOMINAL = np.array([NOMINAL[j] for j in ABRASION_IDX], dtype=float)


def abrasion_inputs(X: np.ndarray) -> np.ndarray:
    """Project a full 7-input sample onto the 6 abrasion inputs."""
    X = np.atleast_2d(np.asarray(X, dtype=float))
    return X[:, ABRASION_IDX]


ABRASION_CHAIN = [
    ChainStage("contact_pressure", _stage_state,
               output_names=[f"p{j + 1}" for j in range(N_STATIONS)] + ["c_ice", "K_wear", "H_steel"]),
    ChainStage("annual_increment", _stage_annual_from_state,
               output_names=[f"inc{j + 1}" for j in range(N_STATIONS)]),
    ChainStage("cumulative_field", _stage_year_marks,
               output_names=[f"d_y{y}_s{j + 1}" for y in YEAR_MARKS for j in range(N_STATIONS)]),
]


def _stage_resistance_terms(X4: np.ndarray) -> np.ndarray:
    X4 = np.atleast_2d(np.asarray(X4, dtype=float))
    rows = []
    for i in range(X4.shape[0]):
        t = lindqvist_terms(SHIP, float(X4[i, 0]), float(X4[i, 1]) / 1.0e6,
                            float(X4[i, 2]), float(X4[i, 3]))
        m = t["velocity_multiplier"]
        rows.append([t["crushing"] * m, t["bending"] * m, t["submersion"], t["speed"]])
    return np.asarray(rows, dtype=float)


def _stage_resistance_total(terms: np.ndarray) -> np.ndarray:
    terms = np.atleast_2d(np.asarray(terms, dtype=float))
    r_cr, r_be, r_sub, r_sp = terms[:, 0], terms[:, 1], terms[:, 2], terms[:, 3]
    # the Lindqvist velocity multiplier is already folded into the crushing and
    # bending terms by `_stage_resistance_terms`
    total = r_cr + r_be + r_sub + r_sp
    return np.column_stack([r_cr, r_be, r_sub, r_sp, total])


RESISTANCE_CHAIN = [
    ChainStage("lindqvist_terms", _stage_resistance_terms,
               output_names=["R_crushing", "R_bending", "R_submersion", "R_speed"]),
    ChainStage("total_resistance", _stage_resistance_total,
               output_names=RESISTANCE_OUT_NAMES),
]


# ---------------------------------------------------------------------------
# Analytic / independent anchors used by the run
# ---------------------------------------------------------------------------


def nominal_depth_closed_form() -> Dict[str, float]:
    """Closed-form nominal end-of-life depth and rate (paper-and-pencil value).

    The station shape factor enters twice -- once through the contact pressure
    and once through the sliding distance -- so the mean over the belt is the
    unit-shape value times ``mean(phi^2)``; both numbers are returned so the
    model can be scored against an *exact* anchor.
    """
    p_nom = P_REF * (SIGMA_C_REF / SIGMA_C_REF) * math.sqrt(H_REF / H_REF) * math.sqrt(V_REF / V_REF)
    slide = IN_ICE_TRACK_NM * 1852.0 * 0.70 * BETA
    depth = K_NOMINAL * p_nom * slide / H_NOMINAL
    phi2 = float(np.mean(PHI ** 2))
    return {
        "shape_second_moment": phi2,
        "depth_unit_shape_mm": 1.0e3 * depth,
        "depth_mean_station_mm": 1.0e3 * depth * phi2,
        "pressure_pa": p_nom,
        "slide_m_per_station": slide,
        "depth_m": depth,
        "depth_mm": 1.0e3 * depth,
        "rate_mm_per_1000nm": 1.0e3 * depth / (IN_ICE_TRACK_NM / 1000.0),
        "service_band_mm_per_1000nm": [0.1, 1.0],
        "inside_service_band": bool(0.1 <= 1.0e3 * depth / (IN_ICE_TRACK_NM / 1000.0) <= 1.0),
    }


def _growth_slope(years: np.ndarray, std: np.ndarray) -> float:
    return float(np.polyfit(np.log(years), np.log(np.maximum(std, 1e-30)), 1)[0])


def nominal_depth_exact() -> Dict[str, float]:
    """Exact anchor at the *truncated* nominal point (no geometry approximation).

    Uses the truncated-distribution means that the sampler actually realises, so
    the model's mean-station depth must match this closed form to round-off.
    """
    h, c, V, sigma_c, mu, K, H = NOMINAL
    p = P_REF * (sigma_c / SIGMA_C_REF) * math.sqrt(h / H_REF) * math.sqrt(V / V_REF)
    slide = IN_ICE_TRACK_NM * 1852.0 * c * BETA
    unit = K * p * slide / H
    phi2 = float(np.mean(PHI ** 2))
    return {
        "mean_station_depth_mm": 1.0e3 * unit * phi2,
        "p95_station_depth_mm": 1.0e3 * unit * float(np.quantile(PHI ** 2, 0.95)),
        "rate_mm_per_1000nm": 1.0e3 * unit / (IN_ICE_TRACK_NM / 1000.0),
    }


def field_growth_check(
    n_outer: int = 4096, n_conditional: int = 20000, seed: int = 0
) -> Dict[str, Any]:
    """Nested MC of the cumulative field: two error-growth regimes.

    Two spreads are measured, because they obey different laws:

    * **aleatory** (yearly ice-severity noise only, inputs held at nominal):
      the cumulative increment is a *sum of i.i.d.* annual terms, so
      ``Var[D(Y)] ~ Y`` and ``std ~ sqrt(Y)`` -- the fitted log-log slope must be
      0.5 (analytic anchor);
    * **total** (epistemic input uncertainty + yearly noise): the input
      uncertainty enters every annual increment coherently, so its standard
      deviation grows ~ linearly in ``Y``; once it dominates the fitted slope
      tends to 1.0.  Comparing the two slopes is the quantitative statement that
      the field error at end of life is input-uncertainty dominated, and that
      the temporal part is a ``sqrt`` -growing minority.
    """
    from backend.services.uq_propagation import sample_inputs

    rng = np.random.default_rng(seed)
    X = sample_inputs(INPUT_SPECS, n=n_outer, method="qmc", seed=seed)
    eps = rng.normal(0.0, YEARLY_LOG_SIGMA, size=(n_outer, SERVICE_YEARS))
    field = cumulative_field(X, eps)                      # (n, years, stations)
    dmax = field.max(axis=2) * 1.0e3                      # (n, years) in mm
    years = np.arange(1, SERVICE_YEARS + 1, dtype=float)
    std_by_year = np.std(dmax, axis=0, ddof=1)

    # conditional (aleatory-only) ensemble at the nominal design point
    X_nom = np.tile(NOMINAL, (n_conditional, 1))
    eps_c = rng.normal(0.0, YEARLY_LOG_SIGMA, size=(n_conditional, SERVICE_YEARS))
    dmax_c = cumulative_field(X_nom, eps_c).max(axis=2) * 1.0e3
    std_cond = np.std(dmax_c, axis=0, ddof=1)
    mean_cond = np.mean(dmax_c, axis=0)

    slope_total = _growth_slope(years, std_by_year)
    slope_aleatory = _growth_slope(years, std_cond)
    # share of the end-of-life variance carried by the yearly severity noise
    share_aleatory = float(std_cond[-1] ** 2 / std_by_year[-1] ** 2)
    return {
        "n_outer": int(n_outer),
        "n_conditional": int(n_conditional),
        "years": years.astype(int).tolist(),
        "mean_max_mm": np.mean(dmax, axis=0).tolist(),
        "std_max_mm": std_by_year.tolist(),
        "q05_max_mm": np.quantile(dmax, 0.05, axis=0).tolist(),
        "q95_max_mm": np.quantile(dmax, 0.95, axis=0).tolist(),
        "field_mean_mm": (field.mean(axis=0) * 1.0e3).tolist(),      # (years, stations)
        "field_std_mm": (field.std(axis=0, ddof=1) * 1.0e3).tolist(),
        "conditional_mean_max_mm": mean_cond.tolist(),
        "conditional_std_max_mm": std_cond.tolist(),
        "growth_exponent_total": slope_total,
        "growth_exponent_aleatory": slope_aleatory,
        "growth_exponent_aleatory_expected": 0.5,
        "growth_exponent_aleatory_rel_error": abs(slope_aleatory - 0.5) / 0.5,
        "aleatory_variance_share_at_end": share_aleatory,
    }