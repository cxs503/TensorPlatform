"""Model-ship scale effect and extrapolation similarity kernel (ITTC-1957 / ITTC-1978 + low-Re transition).

Why this module exists
----------------------
A repository-wide audit (mission m9028, node ``p234w2-n02-scale-law-maturity``)
found **ten private copies** of the ITTC-1957 friction line
(``0.075 / (log10 Re - 2)**2``) scattered over ``backend/api/domain``,
``backend/services`` and ``backend/physics``, but **zero** model-ship
extrapolation machinery:

* no form-factor / ``(1+k)`` decomposition,
* no tank-to-ship ``Delta C_F`` correction,
* no correlation allowance ``C_A``,
* no propeller scale-effect correction (``Delta K_T``),
* and no treatment whatsoever of models whose Reynolds number sits in (or
  below) the laminar-turbulent transition, which is the normal regime for
  micro-USV / small towing-tank models (``Re_M < 1e6``).

This module is the single, tested home for that physics.  It is a pure
function library (no I/O, no fitted-to-platform numbers), so the served
endpoints can import it and the audit can replay it.

What it provides
----------------
1. Fluid properties: fresh-water density (Kell 1975 polynomial) and dynamic
   viscosity (Vogel equation), plus a salinity correction calibrated on the
   sea-water anchor ``nu(15 degC, 35 ppt) = 1.1883e-6 m^2/s``.
2. Friction lines: ITTC-1957 (correlation line), Schoenherr/ATTC-1947
   (independent implicit line, solved by bisection), Prandtl-Schlichting
   ``0.455/(log10 Re)^2.58``, Blasius laminar ``1.328/sqrt(Re)``,
   Schlichting's transition-corrected plate formula
   ``0.455/(log10 Re)^2.58 - 1700/Re`` and a transition-aware composite
   (intermittency blend) for ``Re < 1e6``.
3. Similarity: Froude velocity scaling, Reynolds scaling and the exact
   identity ``Re_S / Re_M = lambda^1.5 * (nu_M / nu_S)`` for
   geometrically similar, Froude-scaled model/ship pairs.
4. Extrapolation: ITTC-1978 performance prediction method (form factor
   ``1+k`` + residuary + correlation allowance) and the ``Delta C_F``
   (two-dimensional) method.  Both are implemented with a *model-side* and a
   *ship-side* friction line that can be chosen independently, because the
   whole point of the low-Re track is that the model-side closure must not be
   the ship-correlation line when ``Re_M < 1e6``.
5. Propeller scale effect (ITTC-1978): section drag coefficient
   ``C_D = 2(1 + 2 t/c)(0.044/Re_co^(1/6) - 5/Re_co^(2/3))``, ``Delta C_D``,
   and ``Delta K_T = -Delta C_D * 0.3 * (Z c P/D) / D``.
6. ``self_consistency_spread`` -- the machine-checkable similarity gate:
   extrapolating the *same* ship from several model scales must give the same
   ship resistance.

Deviation from the mission text (declared, not silent)
-----------------------------------------------------
The mission quotes the 2-D method as

    R_S = R_M (rho_S/rho_M)(V_S/V_M)^2 (S_S/S_M) + Delta C_F * 1/2 rho_S V_S^2 S_S

with ``Delta C_F = C_FM - C_FS > 0``.  That *sign* is wrong: the ship is
larger, so its friction coefficient is smaller, and the extrapolated ship
resistance must be **smaller** than the raw ``R_M`` scaled by ``lambda^3``.
With ``k = 0`` and ``C_A = 0`` the ``Delta C_F`` method has to reduce to
ITTC-1978, which algebraically requires

    R_TS = R_TM s - (C_FM - C_FS) q_S S_S,     s = (rho_S V_S^2 S_S)/(rho_M V_M^2 S_M)

``extrapolate_delta_cf`` therefore defaults to ``delta_cf_sign=-1`` (physics),
and ``delta_cf_sign=+1`` reproduces the mission's literal expression so the
inconsistency can be quantified instead of argued about (see the m9028 test
suite: the literal form over-predicts by tens of percent).

Evidence grades (mission evidence taxonomy)
-------------------------------------------
* ITTC-1957 / Schoenherr / Prandtl-Schlichting / Blasius / Schlichting
  transition / ITTC-1978 extrapolation algebra / ITTC-1978 propeller
  ``Delta K_T`` -- REAL (published closed forms, reproduced against printed
  4-digit handbook values and independent formulations).
* Transition-aware composite ``low_re_friction_cf`` -- REAL in structure
  (continuous, monotone, asymptotically correct) but the intermittency ramp
  ``[3e5, 1e6]`` is a model choice, not a fitted correlation: graded
  WITHHELD at value level, and always reported next to the Schlichting
  composite it brackets.
* Sea-water viscosity at temperatures other than the 15 degC anchor --
  engineering approximation (constant ``nu_sea/nu_fresh`` ratio); graded
  WITHHELD, flagged in the docstring of ``kinematic_viscosity``.

Units: SI throughout (m, s, N, kg, Pa).  Reynolds numbers are based on
waterline length for hull quantities and on ``Re_co`` (chord-based, resultant
inflow) for propeller sections, as prescribed by ITTC-1978.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence

__all__ = [
    "G",
    "density_water",
    "kinematic_viscosity",
    "ittc1957_cf",
    "schoenherr_cf",
    "prandtl_schlichting_cf",
    "blasius_cf",
    "schlichting_plate_cf",
    "transition_intermittency",
    "low_re_friction_cf",
    "low_re_model_cf_line",
    "CF_LINES",
    "ScaleState",
    "Extrapolation",
    "froude_velocity",
    "reynolds_number",
    "reynolds_ratio_similar",
    "correlation_allowance_ittc78",
    "extrapolate_ittc78",
    "extrapolate_delta_cf",
    "relative_spread",
    "self_consistency_spread",
    "section_drag_coefficient",
    "section_drag_in_range",
    "SECTION_DRAG_RE_MIN",
    "SECTION_DRAG_RECOMMENDED_MIN",
    "propeller_re_co",
    "propeller_scale_effect",
]

G = 9.80665  # m/s^2, standard gravity

# ---------------------------------------------------------------------------
# 1. Fluid properties
# ---------------------------------------------------------------------------

#: sea-water kinematic-viscosity anchor used to calibrate the salinity term
NU_SEA_15_35 = 1.1883e-6  # m^2/s  (ITTC table value, 15 degC, 35 ppt)
#: fresh-water kinematic-viscosity anchor (15 degC)
NU_FRESH_15 = 1.1380e-6   # m^2/s

#: relative viscosity increase per ppt salinity, calibrated so that
#: nu(15 degC, 35 ppt) reproduces the ITTC anchor exactly.
_SALINITY_NU_PER_PPT = (NU_SEA_15_35 / NU_FRESH_15 - 1.0) / 35.0
#: density increase per ppt salinity (linear, anchored at 35 ppt -> +26.9 kg/m^3)
_SALINITY_RHO_PER_PPT = 26.9 / 35.0


def _vogel_dynamic_viscosity(temp_c: float) -> float:
    """Water dynamic viscosity [Pa s] from the Vogel equation.

    ``mu = 2.414e-5 * 10**(247.8 / (T + 133.15))``.  Accuracy is ~0.1 % over
    5-35 degC, worse (2 %) near 0 degC, hence the guard below.
    """
    if not -1.0 < temp_c < 60.0:
        raise ValueError(f"temperature {temp_c} degC outside the Vogel validity band")
    return 2.414e-5 * 10.0 ** (247.8 / (temp_c + 133.15))


def _kell_density_fresh(temp_c: float) -> float:
    """Fresh-water density [kg/m^3], Kell (1975) polynomial (1 atm, 0-150 degC)."""
    t = float(temp_c)
    return (
        999.842594
        + 6.793952e-2 * t
        - 9.095290e-3 * t ** 2
        + 1.001685e-4 * t ** 3
        - 1.120083e-6 * t ** 4
        + 6.536332e-9 * t ** 5
    )


def density_water(temp_c: float = 15.0, salinity_ppt: float = 0.0) -> float:
    """Water density [kg/m^3] as a function of temperature and salinity.

    Fresh water is exact to the Kell polynomial; the salinity part is a linear
    engineering approximation anchored on 35 ppt / 15 degC (1026.0 kg/m^3,
    ITTC tabulates 1025.9).  Graded WITHHELD away from that anchor.
    """
    if salinity_ppt < 0.0:
        raise ValueError("salinity must be non-negative")
    return _kell_density_fresh(temp_c) + _SALINITY_RHO_PER_PPT * salinity_ppt


def kinematic_viscosity(temp_c: float = 15.0, salinity_ppt: float = 0.0) -> float:
    """Kinematic viscosity [m^2/s] of fresh or sea water.

    ``nu = mu(T) / rho(T, S)`` with the Vogel/Kell pair for fresh water and a
    constant ``nu_sea/nu_fresh`` ratio for salt water, calibrated on the
    ITTC sea-water anchor ``nu(15 degC, 35 ppt) = 1.1883e-6`` (exact by
    construction).  Away from 15 degC the ratio is an approximation
    (WITHHELD at value level); the ship-performance platform almost always
    evaluates at 15 degC, which is the calibrated point.
    """
    if salinity_ppt < 0.0:
        raise ValueError("salinity must be non-negative")
    mu = _vogel_dynamic_viscosity(temp_c)
    rho = _kell_density_fresh(temp_c)
    return (mu / rho) * (1.0 + _SALINITY_NU_PER_PPT * salinity_ppt)


# ---------------------------------------------------------------------------
# 2. Friction lines
# ---------------------------------------------------------------------------

def ittc1957_cf(reynolds: float) -> float:
    """ITTC-1957 model-ship correlation line ``C_F = 0.075/(log10 Re - 2)^2``.

    This is the line the whole platform uses; it is a *correlation* line (it
    absorbs part of the model-ship correlation allowance), not a physical
    friction law, which is why it must not be used unmodified as the
    model-side friction closure at ``Re < 1e6`` (see ``low_re_friction_cf``).

    Valid for ``Re >= 1e5`` in the sense of the ITTC recommended procedure;
    below ``Re = 1e4`` the expression has no physical meaning and is rejected.
    """
    if reynolds <= 0.0:
        raise ValueError("Reynolds number must be positive")
    if reynolds < 1e4:
        raise ValueError("ITTC-1957 line is not defined below Re = 1e4")
    return 0.075 / (math.log10(reynolds) - 2.0) ** 2


def schoenherr_cf(reynolds: float, tol: float = 1e-14) -> float:
    """ATTC-1947 / Schoenherr line, solved from ``0.242/sqrt(C_F) = log10(Re C_F)``.

    Independent formulation (implicit, bisection) used as a cross-check on
    ITTC-1957.  The two lines coincide within 0.5 % at ``Re >= 1e9`` and
    differ by ~6 % at ``Re = 1e6`` -- the difference is what the model-ship
    correlation allowance historically absorbs.
    """
    if reynolds <= 0.0:
        raise ValueError("Reynolds number must be positive")

    def residual(cf: float) -> float:
        return 0.242 / math.sqrt(cf) - math.log10(reynolds * cf)

    lo, hi = 1e-5, 5e-2
    if residual(lo) < 0.0 or residual(hi) > 0.0:  # pragma: no cover - guard
        raise RuntimeError("Schoenherr root not bracketed")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if residual(mid) > 0.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol * mid:
            break
    return 0.5 * (lo + hi)


def blasius_cf(reynolds: float) -> float:
    """Laminar flat-plate skin friction ``C_F = 1.328/sqrt(Re)`` (Blasius)."""
    if reynolds <= 0.0:
        raise ValueError("Reynolds number must be positive")
    return 1.328 / math.sqrt(reynolds)


def prandtl_schlichting_cf(reynolds: float) -> float:
    """Fully turbulent smooth-plate line ``C_F = 0.455/(log10 Re)^2.58``."""
    if reynolds <= 0.0:
        raise ValueError("Reynolds number must be positive")
    return 0.455 / math.log10(reynolds) ** 2.58


def schlichting_plate_cf(reynolds: float, re_crit: float = 5.0e5) -> float:
    """Schlichting's plate friction with a transition correction.

    * ``Re < Re_crit``: laminar Blasius ``1.328/sqrt(Re)``.
    * ``Re_crit <= Re <= 1e7``: ``0.455/(log10 Re)^2.58 - 1700/Re``
      (Schlichting, *Boundary-Layer Theory*, plate with transition at
      ``Re_crit``).
    * ``Re > 1e7``: fully turbulent ``0.455/(log10 Re)^2.58``.

    Note: the transition branch is only meaningful above the Reynolds number
    where it exceeds the laminar branch (the ``-1700/Re`` term drives it to
    zero near ``Re ~ 3e5``); below that the piecewise join above is used, and
    the composite ``low_re_friction_cf`` is the smooth alternative that should
    be used for extrapolation.
    """
    if reynolds <= 0.0:
        raise ValueError("Reynolds number must be positive")
    if reynolds < re_crit:
        return blasius_cf(reynolds)
    if reynolds <= 1.0e7:
        return prandtl_schlichting_cf(reynolds) - 1700.0 / reynolds
    return prandtl_schlichting_cf(reynolds)


def transition_intermittency(
    reynolds: float, re_1: float = 3.0e5, re_2: float = 1.0e6
) -> float:
    """Smooth intermittency ramp 0 -> 1 over ``[re_1, re_2]`` (cosine blending).

    ``gamma = 1`` means fully turbulent, ``0`` fully laminar.  The band
    ``[3e5, 1e6]`` follows the classical Dhawan-Knapp transition window for a
    smooth plate with low free-stream turbulence; it is a *model choice*
    (WITHHELD at value level), hence the explicit bracket + the unit test that
    pins continuity and monotonicity of the resulting line.
    """
    if re_1 <= 0.0 or re_2 <= re_1:
        raise ValueError("need 0 < re_1 < re_2")
    if reynolds <= re_1:
        return 0.0
    if reynolds >= re_2:
        return 1.0
    x = (math.log10(reynolds) - math.log10(re_1)) / (
        math.log10(re_2) - math.log10(re_1)
    )
    return 0.5 * (1.0 - math.cos(math.pi * x))


def low_re_friction_cf(
    reynolds: float, re_1: float = 3.0e5, re_2: float = 1.0e6
) -> float:
    """Transition-aware model-side friction line (laminar/turbulent blend).

    ``C_F = (1 - gamma) * 1.328/sqrt(Re) + gamma * 0.455/(log10 Re)^2.58``

    Continuous, asymptotically equal to the Prandtl-Schlichting line for
    ``Re >= 1e6`` and to the Blasius laminar line for ``Re <= 3e5``, which is
    what makes it safe to use as the model-side closure down to
    ``Re ~ 1e4`` (micro-USV models).

    Monotonicity is *not* global and that is physical: the mean plate
    coefficient rises through the transition band (the boundary layer becomes
    turbulent) and decreases on both sides of it.  A caller that assumes
    "C_F always decreases with Re" will be wrong by up to a factor of ~2 at
    ``Re ~ 5e5``; the unit tests assert the three-region behaviour instead.
    This is the line the platform should use *instead of* ITTC-1957 whenever
    ``Re_M < 1e6``; ``low_re_model_cf_line`` implements exactly that switch.
    """
    if reynolds <= 0.0:
        raise ValueError("Reynolds number must be positive")
    gamma = transition_intermittency(reynolds, re_1, re_2)
    return (1.0 - gamma) * blasius_cf(reynolds) + gamma * prandtl_schlichting_cf(
        reynolds
    )


def low_re_model_cf_line(reynolds: float) -> float:
    """Recommended model-side friction closure.

    ``Re_M < 1e6`` -> transition-aware ``low_re_friction_cf``;
    ``Re_M >= 1e6`` -> standard ITTC-1957 correlation line (turbulent model,
    the classical towing-tank regime).  This switch is the operational
    deliverable of the m9028 low-Re track.
    """
    if reynolds < 1.0e6:
        return low_re_friction_cf(reynolds)
    return ittc1957_cf(reynolds)


CF_LINES: Dict[str, Callable[[float], float]] = {
    "ittc1957": ittc1957_cf,
    "schoenherr": schoenherr_cf,
    "prandtl_schlichting": prandtl_schlichting_cf,
    "blasius": blasius_cf,
    "schlichting": schlichting_plate_cf,
    "low_re": low_re_friction_cf,
    "auto": low_re_model_cf_line,
}


def _cf(name: str, reynolds: float) -> float:
    try:
        line = CF_LINES[name]
    except KeyError:  # pragma: no cover - guard
        raise KeyError(f"unknown friction line {name!r}; known: {sorted(CF_LINES)}")
    return line(reynolds)


# ---------------------------------------------------------------------------
# 3. Similarity relations
# ---------------------------------------------------------------------------

def froude_velocity(velocity_ship: float, length_ratio: float) -> float:
    """Model velocity for Froude similarity, ``V_M = V_S sqrt(lambda)``."""
    if length_ratio <= 0.0:
        raise ValueError("length ratio must be positive")
    return velocity_ship * math.sqrt(length_ratio)


def reynolds_number(velocity: float, length: float, nu: float) -> float:
    """``Re = V L / nu`` with an explicit positivity guard."""
    if velocity <= 0.0 or length <= 0.0 or nu <= 0.0:
        raise ValueError("velocity, length and nu must be positive")
    return velocity * length / nu


def reynolds_ratio_similar(
    scale_factor: float, nu_model: float, nu_ship: float
) -> float:
    """Exact ``Re_S/Re_M`` for geometrically similar, Froude-scaled hulls.

    ``scale_factor = Lambda = L_S/L_M`` (the model is built at ``1/Lambda``).
    With ``V_M = V_S sqrt(1/Lambda)`` the Reynolds numbers satisfy

        ``Re_S/Re_M = Lambda^1.5 (nu_M / nu_S)``

    so a 1/50 model runs at ``50^1.5 = 354`` times lower ``Re`` -- which is why
    a 10 m USV measured at 1/50 lands at ``Re_M ~ 5e4``, far below transition.
    """
    if scale_factor <= 0.0 or nu_model <= 0.0 or nu_ship <= 0.0:
        raise ValueError("scale factor and viscosities must be positive")
    return scale_factor ** 1.5 * (nu_model / nu_ship)


@dataclass(frozen=True)
class ScaleState:
    """One side of the model/ship pair, with the derived non-dimensional groups."""

    length: float
    velocity: float
    density: float
    viscosity: float
    wetted_surface: float

    @property
    def reynolds(self) -> float:
        return reynolds_number(self.velocity, self.length, self.viscosity)

    @property
    def dynamic_pressure(self) -> float:
        return 0.5 * self.density * self.velocity ** 2

    @property
    def froude(self) -> float:
        return self.velocity / math.sqrt(G * self.length)

    def scaled_to(self, other: "ScaleState") -> float:
        """Resistance scale factor ``(rho_o/rho) (V_o/V)^2 (S_o/S)``."""
        return (
            (other.density / self.density)
            * (other.velocity / self.velocity) ** 2
            * (other.wetted_surface / self.wetted_surface)
        )


def correlation_allowance_ittc78(length_m: float) -> float:
    """ITTC-1978 correlation allowance ``C_A = 0.006 (L+100)^-0.16 - 0.00205``.

    The formula is a scattering band fitted on seagoing hulls with
    ``L >= 50 m``; for shorter craft (micro-USV) it is outside its validity
    range and the result is flagged OUT_OF_RANGE by the caller, not silently
    trusted.
    """
    if length_m <= 0.0:
        raise ValueError("length must be positive")
    return 0.006 * (length_m + 100.0) ** -0.16 - 0.00205


# ---------------------------------------------------------------------------
# 4. Extrapolation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Extrapolation:
    """Result of a model -> ship extrapolation, fully decomposed for audit."""

    method: str
    cf_line_model: str
    cf_line_ship: str
    re_model: float
    re_ship: float
    cf_model: float
    cf_ship: float
    r_tm: float
    r_fm: float
    r_fs: float
    r_rm: float
    r_rs: float
    r_a: float
    r_ts: float
    form_factor_k: float
    correlation_allowance: float

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


def extrapolate_ittc78(
    r_tm: float,
    model: ScaleState,
    ship: ScaleState,
    *,
    cf_line_ship: str = "ittc1957",
    cf_line_model: Optional[str] = None,
    form_factor_k: float = 0.0,
    c_a: float = 0.0,
) -> Extrapolation:
    """ITTC-1978 performance prediction (three-dimensional, form-factor) method.

    ``R_FS = q_S S_S C_FS``; ``R_RM = R_TM - (1+k) R_FM``;
    ``R_RS = R_RM * (rho_S/rho_M)(V_S/V_M)^2(S_S/S_M)``;
    ``R_A = q_S S_S C_A``; ``R_TS = (1+k) R_FS + R_RS + R_A``.

    ``cf_line_model`` defaults to ``cf_line_ship``; pass ``"low_re"`` (or the
    ``"auto"`` switch) when the model Reynolds number is below the transition
    band.
    """
    if r_tm <= 0.0:
        raise ValueError("model total resistance must be positive")
    if form_factor_k < 0.0:
        raise ValueError("form factor cannot be negative")
    line_model = cf_line_model or cf_line_ship
    cf_m = _cf(line_model, model.reynolds)
    cf_s = _cf(cf_line_ship, ship.reynolds)

    r_fm = model.dynamic_pressure * model.wetted_surface * cf_m
    r_fs = ship.dynamic_pressure * ship.wetted_surface * cf_s
    r_rm = r_tm - (1.0 + form_factor_k) * r_fm
    r_rs = r_rm * model.scaled_to(ship)
    r_a = ship.dynamic_pressure * ship.wetted_surface * c_a
    r_ts = (1.0 + form_factor_k) * r_fs + r_rs + r_a
    return Extrapolation(
        method="ittc1978",
        cf_line_model=line_model,
        cf_line_ship=cf_line_ship,
        re_model=model.reynolds,
        re_ship=ship.reynolds,
        cf_model=cf_m,
        cf_ship=cf_s,
        r_tm=r_tm,
        r_fm=r_fm,
        r_fs=r_fs,
        r_rm=r_rm,
        r_rs=r_rs,
        r_a=r_a,
        r_ts=r_ts,
        form_factor_k=form_factor_k,
        correlation_allowance=c_a,
    )


def extrapolate_delta_cf(
    r_tm: float,
    model: ScaleState,
    ship: ScaleState,
    *,
    cf_line_ship: str = "ittc1957",
    cf_line_model: Optional[str] = None,
    c_a: float = 0.0,
    delta_cf_sign: int = -1,
) -> Extrapolation:
    """``Delta C_F`` (two-dimensional) extrapolation method.

    ``R_TS = R_TM s + delta_cf_sign * (C_FM - C_FS - C_A) q_S S_S``

    with ``s = (rho_S/rho_M)(V_S/V_M)^2(S_S/S_M)`` and ``q_S = 1/2 rho_S V_S^2``.

    ``delta_cf_sign=-1`` is the physically consistent convention: it makes the
    method identical to ITTC-1978 with ``k = 0`` and ``C_A = 0`` (proved as a
    unit test).  ``delta_cf_sign=+1`` reproduces the literal expression quoted
    in the mission text and is kept only so its bias can be measured.
    """
    if r_tm <= 0.0:
        raise ValueError("model total resistance must be positive")
    if delta_cf_sign not in (-1, 1):
        raise ValueError("delta_cf_sign must be -1 (physics) or +1 (mission text)")
    line_model = cf_line_model or cf_line_ship
    cf_m = _cf(line_model, model.reynolds)
    cf_s = _cf(cf_line_ship, ship.reynolds)

    scale = model.scaled_to(ship)
    q_s_area = ship.dynamic_pressure * ship.wetted_surface
    delta_cf = cf_m - cf_s - c_a
    r_ts = r_tm * scale + delta_cf_sign * delta_cf * q_s_area
    return Extrapolation(
        method="delta_cf" if delta_cf_sign < 0 else "delta_cf_mission_literal",
        cf_line_model=line_model,
        cf_line_ship=cf_line_ship,
        re_model=model.reynolds,
        re_ship=ship.reynolds,
        cf_model=cf_m,
        cf_ship=cf_s,
        r_tm=r_tm,
        r_fm=q_s_area * cf_m,
        r_fs=q_s_area * cf_s,
        r_rm=r_tm - q_s_area * cf_m,
        r_rs=r_tm * scale - q_s_area * cf_m,
        r_a=q_s_area * c_a,
        r_ts=r_ts,
        form_factor_k=0.0,
        correlation_allowance=c_a,
    )


def relative_spread(values: Sequence[float]) -> float:
    """``(max - min)/mean``; the similarity-consistency metric."""
    vals = [float(v) for v in values]
    if not vals:
        raise ValueError("need at least one value")
    mean = sum(vals) / len(vals)
    if mean == 0.0:
        raise ValueError("mean is zero, spread undefined")
    return (max(vals) - min(vals)) / abs(mean)


def self_consistency_spread(results: Mapping[str, float]) -> Dict[str, float]:
    """Summarise per-model-scale extrapolations of the *same* ship.

    Returns ``{"n", "mean", "spread", "spread_pct"}``; ``spread`` is the
    dimensionless ``(max-min)/mean`` used by the acceptance gate
    (``spread_pct < 3 %``).
    """
    vals = [float(v) for v in results.values()]
    if not vals:
        raise ValueError("need at least one extrapolation result")
    mean = sum(vals) / len(vals)
    spread = relative_spread(vals)
    return {
        "n": float(len(vals)),
        "mean": mean,
        "spread": spread,
        "spread_pct": 100.0 * spread,
    }


# ---------------------------------------------------------------------------
# 5. Propeller scale effect (ITTC-1978)
# ---------------------------------------------------------------------------

def section_drag_coefficient(
    chord: float, max_thickness: float, re_co: float
) -> float:
    """ITTC-1978 blade-section drag coefficient.

    ``C_D = 2 (1 + 2 t/c) (0.044 / Re_co^(1/6) - 5 / Re_co^(2/3))``

    ``chord`` and ``max_thickness`` in metres at the same radius (0.75 R by
    convention), ``re_co`` the chord-based Reynolds number of the resultant
    inflow.
    """
    if chord <= 0.0 or max_thickness <= 0.0 or re_co <= 0.0:
        raise ValueError("chord, thickness and Re_co must be positive")
    tc = max_thickness / chord
    return 2.0 * (1.0 + 2.0 * tc) * (
        0.044 / re_co ** (1.0 / 6.0) - 5.0 / re_co ** (2.0 / 3.0)
    )


def section_drag_in_range(re_co: float) -> bool:
    """Validity guard for ``section_drag_coefficient``.

    ``C_D`` from the ITTC-1978 formula is positive only for
    ``Re_co > SECTION_DRAG_RE_MIN`` (the bracket changes sign at
    ``Re = (5/0.044)^2 = 1.29e4``); the correlation itself was fitted in the
    ``2e5 <= Re_co <= 1e7`` band.  Anything below ``2e5`` should be flagged
    OUT_OF_RANGE by the caller, never reported as a validated correction.
    """
    return re_co >= SECTION_DRAG_RECOMMENDED_MIN


SECTION_DRAG_RE_MIN = (5.0 / 0.044) ** 2  # 1.29e4, C_D changes sign here
SECTION_DRAG_RECOMMENDED_MIN = 2.0e5


def propeller_re_co(
    chord: float, v_a: float, n: float, diameter: float, nu: float
) -> float:
    """Chord Reynolds number of the resultant inflow at 0.75 R (ITTC-1978)."""
    if chord <= 0.0 or n <= 0.0 or diameter <= 0.0 or nu <= 0.0:
        raise ValueError("chord, n, D and nu must be positive")
    v_res = math.sqrt(v_a ** 2 + (0.75 * math.pi * n * diameter) ** 2)
    return chord * v_res / nu


def propeller_scale_effect(
    kt_model: float,
    kq_model: float,
    *,
    chord: float,
    thickness_ratio: float,
    pitch_ratio: float,
    n_blades: float,
    diameter: float,
    re_co_model: float,
    re_co_ship: float,
    advance_ratio: float = 0.7,
    torque_model: str = "relative",
) -> Dict[str, float]:
    """ITTC-1978 propeller open-water scale correction.

    ``Delta K_T = -Delta C_D * 0.3 * (Z c P/D) / D`` with ``Delta C_D =
    C_DM - C_DS >= 0``, so the full-scale thrust coefficient is smaller than the
    model one (the blade sections are smoother at higher ``Re``).

    ``Delta K_Q`` is *not* prescribed by ITTC-1978.  Two declared bounds are
    offered through ``torque_model``:

    * ``"relative"`` (default): ``Delta K_Q = K_QM Delta K_T / K_TM`` -- torque
      and thrust change by the same relative amount, so ``eta_0`` is unchanged;
    * ``"none"``: ``Delta K_Q = 0`` -- the correction is applied to thrust only,
      which *reduces* ``eta_0`` slightly.

    Both bounds give ``Delta eta_0 <= 0``: the classical "full-scale efficiency
    is a little better" result needs ``Delta K_Q`` to fall faster than
    ``Delta K_T``, and ITTC-1978 does not prescribe that.  The module therefore
    reports the bracket and flags the efficiency *gain* as WITHHELD instead of
    inventing a torque correction.
    """
    if torque_model not in ("relative", "none"):
        raise ValueError("torque_model must be 'relative' or 'none'")
    if kt_model <= 0.0 or kq_model <= 0.0:
        raise ValueError("open-water coefficients must be positive")
    thickness = chord * thickness_ratio
    cd_m = section_drag_coefficient(chord, thickness, re_co_model)
    cd_s = section_drag_coefficient(chord, thickness, re_co_ship)
    delta_cd = cd_m - cd_s
    delta_kt = -delta_cd * 0.3 * (n_blades * chord * pitch_ratio) / diameter
    kt_ship = kt_model + delta_kt
    if torque_model == "relative":
        delta_kq = kq_model * delta_kt / kt_model
    else:
        delta_kq = 0.0
    kq_ship = kq_model + delta_kq
    j = advance_ratio
    eta_model = j * kt_model / (2.0 * math.pi * kq_model)
    eta_ship = j * kt_ship / (2.0 * math.pi * kq_ship)
    return {
        "cd_model": cd_m,
        "cd_ship": cd_s,
        "delta_cd": delta_cd,
        "delta_kt": delta_kt,
        "delta_kq": delta_kq,
        "kt_model": kt_model,
        "kt_ship": kt_ship,
        "kq_model": kq_model,
        "kq_ship": kq_ship,
        "eta_model": eta_model,
        "eta_ship": eta_ship,
        "eta_rel_change": (eta_ship - eta_model) / eta_model,
        "advance_ratio": j,
        "torque_model": torque_model,
        "cd_model_in_range": section_drag_in_range(re_co_model),
        "cd_ship_in_range": section_drag_in_range(re_co_ship),
    }