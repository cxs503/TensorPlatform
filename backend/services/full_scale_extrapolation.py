"""ITTC-1978 model->full-scale resistance extrapolation — the missing 换算 step.

Why this module exists
----------------------
Up to m1654 the platform could only evaluate :math:`R_T` at *one* scale
(``POST /api/v1/calculations/resistance``, see ``services/ship_resistance.py``) and had
**no** model->ship extrapolation anywhere in the tree (grep for
``delta_cf``/``roughness_allowance``/``full_scale``/``extrapolat`` returns nothing on the
physics path).  That gap makes the two headline capability claims untestable:

* ``propeller.self_propulsion`` and the powering chain consume a resistance at whatever
  scale the caller happened to type in, so "实船功率" can silently be a *model-scale*
  number;
* the 15 % / 5 % accuracy gates are stated against *model tests*, so a CFD/empirical
  result must first be brought to a comparable scale — without the correlation
  allowance ``dC_F`` the comparison carries an uncontrolled ``~0.4e-3`` bias on
  ``C_T`` (≈ 12 % of ``C_T`` for a 300 m ship).

This module implements the standard **1978 ITTC Performance Prediction Method**
(ITTC Recommended Procedures 7.5-02-03-01.4, "1978 ITTC Performance Prediction
Method", rev. 1978) for the bare-hull resistance, in the form that is used by every
towing tank when it extrapolates a model ``C_Tm`` to a full-scale ``C_Ts``:

.. math::

    C_w      &= C_{Tm} - (1+k)\\,C_{Fm} \\\\
    \\Delta C_F &= \\left[105\\left(\\frac{k_s}{L_{wl}}\\right)^{1/3} - 0.64\\right]\\times 10^{-3} \\\\
    C_{Ts}   &= C_w + (1+k)\\,C_{Fs} + \\Delta C_F \\\\
    R_{Ts}   &= C_{Ts}\\,\\tfrac12\\rho_s S_s V_s^2 ,\\qquad P_E = R_{Ts} V_s

with the wetted-surface ratio ``S_s/S_m = lambda^2`` (Froude scaling) when the full
scale area is not supplied, ``k_s = 150 um`` (ITTC-1978 standard roughness) by default
and the identical form factor ``(1+k)`` on both scales (ITTC-1978 assumes the form
factor is Reynolds-independent — that assumption is the main modelling risk and is
returned in ``warnings`` rather than hidden).

Evidence grade
--------------
* REAL for the *procedure*: ``C_F`` is the exact ITTC-1957 correlation line, ``dC_F`` is
  the published closed form, and the Froude/Reynolds similarity laws are enforced and
  asserted exactly (``backend/tests/feat/test_full_scale_extrapolation_m1654.py``).
* WITHHELD for *experimental validation of absolute full-scale ``R_T``*: the in-tree
  canon (``backend/data/benchmark_reference.json``) holds no reviewable full-scale
  ``R_T`` datum — the KVLCC2 full-scale rows are adjudicated ``WITHHELD`` (their filed
  ``R_T``/``C_T`` pair fails the ``S/(L*B)`` geometry closure), and the DTRC SUBOFF
  provenance is model-scale only.  No error percentage against a full-scale EFD is
  therefore quoted; see ``docs/audits/full_scale_extrapolation_ittc1978_m1654.md``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

G = 9.81

#: ITTC-1978 standard surface-roughness height for a new ship (m).
DEFAULT_KS_M = 150e-6

#: Model- and full-scale fluid defaults (ITTC 7.5-02-01-01 property tables).
FRESH_WATER_20C = (998.2, 1.004e-6)
SEA_WATER_15C = (1025.0, 1.188e-6)

#: Lowest Reynolds number for which the ITTC-1957 line is quoted as validated.
ITTC1957_RE_MIN = 1.0e5

METHOD = (
    "ITTC-1978 Performance Prediction Method (7.5-02-03-01.4): "
    "Cw = CTm - (1+k)CFm; CTs = Cw + (1+k)CFs + dCF; dCF = [105(ks/Lwl)^(1/3)-0.64]e-3"
)


def ittc1957_cf(reynolds: float) -> float:
    """ITTC-1957 correlation line ``Cf = 0.075 / (log10(Re) - 2)^2``.

    Exact published closed form (ITTC 7.5-02-01-01).  The ``Re`` guard rejects the
    ``log10(Re) -> 2`` singular branch (``Re <= 100``) where the line is meaningless.
    """
    if reynolds <= 100.0:
        raise ValueError(f"Reynolds number must exceed 100, got {reynolds!r}")
    return 0.075 / (math.log10(reynolds) - 2.0) ** 2


def correlation_allowance(length_wl: float, ks: float = DEFAULT_KS_M) -> float:
    """ITTC-1978 model-ship correlation allowance ``dC_F`` (dimensionless).

    ``dC_F = [105 (ks / Lwl)^(1/3) - 0.64] * 1e-3`` with ``ks`` and ``Lwl`` both in
    metres.  The expression is *linear in the roughness ratio* and therefore changes
    sign for long ships: the zero crossing sits at
    ``Lwl = ks / (0.64/105)^3`` (663.4 m for ``ks = 150 um``, see
    :func:`correlation_allowance_zero_length`).  A negative value is not a numerical
    error but it *is* outside the ITTC-1978 calibration range, so it is returned as-is
    and flagged by :func:`extrapolate` instead of being silently clamped.
    """
    if length_wl <= 0.0:
        raise ValueError("length_wl must be positive")
    if ks is None or ks <= 0.0:
        raise ValueError("ks must be positive (m); pass ks=None to extrapolate() to disable dCF")
    return (105.0 * (ks / length_wl) ** (1.0 / 3.0) - 0.64) * 1e-3


def correlation_allowance_zero_length(ks: float = DEFAULT_KS_M) -> float:
    """Waterline length (m) at which the ITTC-1978 ``dC_F`` changes sign."""
    if ks <= 0.0:
        raise ValueError("ks must be positive")
    return ks / (0.64 / 105.0) ** 3


def froude_speed(speed: float, length: float) -> float:
    """``Fn = V / sqrt(g L)``."""
    return speed / math.sqrt(G * length)


def speed_for_froude(froude: float, length: float) -> float:
    """Inverse of :func:`froude_speed`."""
    return froude * math.sqrt(G * length)


@dataclass
class Extrapolation:
    """Result of a model->full-scale extrapolation (all quantities dimensional SI)."""

    scale_ratio: float
    froude_number_model: float
    froude_number_ship: float
    speed_model_ms: float
    speed_ship_ms: float
    length_model_m: float
    length_ship_m: float
    wetted_surface_model_m2: float
    wetted_surface_ship_m2: float
    wetted_surface_source: str
    reynolds_model: float
    reynolds_ship: float
    cf_model: float
    cf_ship: float
    form_factor: float
    ct_model: float
    delta_cf: float
    delta_cf_source: str
    cw: float
    ct_ship: float
    delta_ct_total: float
    rt_model_n: float
    rt_ship_n: float
    effective_power_kw: float
    method: str = METHOD
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """Plain-dict view (JSON friendly, figures/tests consume this)."""
        out = {k: v for k, v in self.__dict__.items() if k != "warnings"}
        out["warnings"] = list(self.warnings)
        return out


def extrapolate(
    *,
    model_length_m: float,
    model_speed_ms: float,
    model_wetted_surface_m2: float,
    model_resistance_n: Optional[float] = None,
    model_total_coefficient: Optional[float] = None,
    ship_length_m: float,
    ship_wetted_surface_m2: Optional[float] = None,
    ship_speed_ms: Optional[float] = None,
    form_factor: float = 1.0,
    ks: Optional[float] = DEFAULT_KS_M,
    rho_model: float = FRESH_WATER_20C[0],
    nu_model: float = FRESH_WATER_20C[1],
    rho_ship: float = SEA_WATER_15C[0],
    nu_ship: float = SEA_WATER_15C[1],
    froude_tolerance: float = 1e-3,
) -> Extrapolation:
    """Extrapolate a model-scale resistance to full scale by the ITTC-1978 method.

    Parameters
    ----------
    model_length_m, model_wetted_surface_m2
        Model waterline length and wetted surface (both validated geometry, per the
        platform rule "geometry must be verified before performance numbers").
    model_speed_ms, ship_speed_ms
        Model speed is required; the ship speed defaults to the Froude-similar value
        ``V_m sqrt(lambda)``.  When a ship speed is supplied and it is *not*
        Froude-similar the result is still computed (the caller may want a
        non-similar comparison) but a warning is emitted, because ``C_w`` is then
        transported across Froude numbers, which is not what ITTC-1978 does.
    model_resistance_n / model_total_coefficient
        Exactly one of the two must be given; ``C_Tm = R_Tm / (0.5 rho_m S_m V_m^2)``.
    ks
        Roughness height (m). ``None`` disables the correlation allowance
        (``dC_F = 0``, ``delta_cf_source = "disabled"``) — useful for isolating the
        Froude-scaling limit laws in verification, never for a real prediction.
    form_factor
        ``1+k``; applied identically at both scales (ITTC-1978 assumption).
    """
    # --- input guards -----------------------------------------------------
    for name, value in (
        ("model_length_m", model_length_m),
        ("model_speed_ms", model_speed_ms),
        ("model_wetted_surface_m2", model_wetted_surface_m2),
        ("ship_length_m", ship_length_m),
        ("rho_model", rho_model),
        ("nu_model", nu_model),
        ("rho_ship", rho_ship),
        ("nu_ship", nu_ship),
    ):
        if value is None or value <= 0.0:
            raise ValueError(f"{name} must be positive, got {value!r}")
    if (model_resistance_n is None) == (model_total_coefficient is None):
        raise ValueError(
            "give exactly one of model_resistance_n / model_total_coefficient"
        )
    if form_factor < 1.0:
        raise ValueError(f"form factor must be >= 1, got {form_factor!r}")
    if model_resistance_n is not None and model_resistance_n <= 0.0:
        raise ValueError("model_resistance_n must be positive")
    if model_total_coefficient is not None and model_total_coefficient <= 0.0:
        raise ValueError("model_total_coefficient must be positive")
    if ship_wetted_surface_m2 is not None and ship_wetted_surface_m2 <= 0.0:
        raise ValueError("ship_wetted_surface_m2 must be positive when supplied")

    warnings: List[str] = []
    lam = ship_length_m / model_length_m

    # --- speeds and non-dimensional groups --------------------------------
    fn_model = froude_speed(model_speed_ms, model_length_m)
    if ship_speed_ms is None:
        speed_ship = speed_for_froude(fn_model, ship_length_m)
    else:
        if ship_speed_ms <= 0.0:
            raise ValueError("ship_speed_ms must be positive")
        speed_ship = float(ship_speed_ms)
        fn_ship_given = froude_speed(speed_ship, ship_length_m)
        if abs(fn_ship_given - fn_model) / fn_model > froude_tolerance:
            warnings.append(
                f"Froude mismatch: Fn_model={fn_model:.4f} vs Fn_ship={fn_ship_given:.4f} "
                f"(>{froude_tolerance:g} relative); Cw is transported across Froude "
                "numbers, so this is NOT the ITTC-1978 procedure"
            )
    fn_ship = froude_speed(speed_ship, ship_length_m)

    re_model = model_speed_ms * model_length_m / nu_model
    re_ship = speed_ship * ship_length_m / nu_ship
    if re_model < ITTC1957_RE_MIN:
        warnings.append(
            f"Re_model={re_model:.3e} below the ITTC-1957 validated range "
            f"({ITTC1957_RE_MIN:.0e}); Cf is an extrapolation of the line"
        )

    cf_model = ittc1957_cf(re_model)
    cf_ship = ittc1957_cf(re_ship)

    # --- model coefficient ------------------------------------------------
    if model_total_coefficient is not None:
        ct_model = float(model_total_coefficient)
        rt_model = ct_model * 0.5 * rho_model * model_wetted_surface_m2 * model_speed_ms ** 2
    else:
        rt_model = float(model_resistance_n)
        ct_model = rt_model / (
            0.5 * rho_model * model_wetted_surface_m2 * model_speed_ms ** 2
        )

    # --- wetted surface at full scale (Froude scaling by default) ---------
    if ship_wetted_surface_m2 is None:
        surface_ship = model_wetted_surface_m2 * lam ** 2
        surface_source = "froude_scaling_lambda2"
    else:
        surface_ship = float(ship_wetted_surface_m2)
        surface_source = "input_validated"

    # --- 1978 ITTC procedure ----------------------------------------------
    cw = ct_model - form_factor * cf_model
    if cw <= 0.0:
        raise ValueError(
            f"non-physical: C_Tm={ct_model:.6e} is not above the viscous baseline "
            f"(1+k)C_Fm={form_factor * cf_model:.6e} — check the model datum or (1+k)"
        )

    if ks is None:
        delta_cf = 0.0
        delta_cf_source = "disabled"
    else:
        delta_cf = correlation_allowance(ship_length_m, ks)
        delta_cf_source = "ittc1978"
        if delta_cf < 0.0:
            warnings.append(
                f"dC_F={delta_cf:.3e} is negative for Lwl={ship_length_m:.1f} m "
                f"(zero crossing at {correlation_allowance_zero_length(ks):.1f} m): "
                "outside the ITTC-1978 calibration range, value not clamped"
            )

    ct_ship = cw + form_factor * cf_ship + delta_cf
    rt_ship = ct_ship * 0.5 * rho_ship * surface_ship * speed_ship ** 2
    pe_kw = rt_ship * speed_ship / 1000.0

    return Extrapolation(
        scale_ratio=lam,
        froude_number_model=fn_model,
        froude_number_ship=fn_ship,
        speed_model_ms=float(model_speed_ms),
        speed_ship_ms=speed_ship,
        length_model_m=float(model_length_m),
        length_ship_m=float(ship_length_m),
        wetted_surface_model_m2=float(model_wetted_surface_m2),
        wetted_surface_ship_m2=float(surface_ship),
        wetted_surface_source=surface_source,
        reynolds_model=re_model,
        reynolds_ship=re_ship,
        cf_model=cf_model,
        cf_ship=cf_ship,
        form_factor=form_factor,
        ct_model=ct_model,
        delta_cf=delta_cf,
        delta_cf_source=delta_cf_source,
        cw=cw,
        ct_ship=ct_ship,
        delta_ct_total=ct_ship - ct_model,
        rt_model_n=rt_model,
        rt_ship_n=rt_ship,
        effective_power_kw=pe_kw,
        warnings=warnings,
    )


def decompose(result: Extrapolation) -> dict:
    """Split the model->ship ``C_T`` correction into its two physical parts.

    ``C_Ts - C_Tm = (1+k)(C_Fs - C_Fm) + dC_F`` identically, so the two terms sum to
    ``delta_ct_total`` to machine precision (asserted in the tests).  Reporting them
    separately is what makes the extrapolation auditable: the first term is the
    Reynolds effect on the friction line, the second is the empirical roughness
    allowance.
    """
    viscous = result.form_factor * (result.cf_ship - result.cf_model)
    return {
        "delta_ct_viscous": viscous,
        "delta_ct_correlation": result.delta_cf,
        "delta_ct_total": result.delta_ct_total,
        "closure_residual": viscous + result.delta_cf - result.delta_ct_total,
    }