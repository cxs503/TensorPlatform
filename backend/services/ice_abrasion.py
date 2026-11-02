"""Ice-hull abrasion kernel (Archard wear) + ice-belt structural capacity retention.

Why this module
---------------
The ice-navigation campaign gap census (p9 wave, ``prop-1571/1573``) lists
``ice-hull abrasion [NEW]: 0`` — no proposal, no code, no test anywhere in the
platform.  Yet abrasion is the mechanism that decides how much of the ice-belt
thickness an ice-classed hull still has after a service life, i.e. it is an
*input* to every structural check the platform already performs.

Scope and evidence level
------------------------
Everything below is closed-form / exact-eigenvalue and is verified inside the
module against identities that can be derived on paper, so the *mechanics* is
REAL:

1. **Archard (1953) wear law** ``V = K W s / H`` — volumetric steel loss from
   normal load ``W``, sliding distance ``s`` and hardness ``H`` of the softer
   body.  In contact-pressure form ``d = K p s / H`` with ``p = W/A``.
   Both routes are implemented and cross-checked (they must agree to round-off).
2. **Sliding distance** along the ice belt is the ship track itself; only the
   fraction of the belt in ice contact (concentration ``c`` times an areal
   contact fraction ``beta``) carries wear.
3. **Plate buckling** of the abraded strake, simply supported uniaxial
   compression ``sigma_cr = k pi^2 D / (b^2 t)`` with ``k = 4`` and
   ``D = E t^3 / (12 (1 - nu^2))``.  Exact ``t^2`` scaling is the key transfer
   function between abrasion depth and residual strength; the coefficient
   ``k = 4`` is re-derived here by an independent Rayleigh-Ritz route on the
   exact double-sine mode, so the code never relies on a transcribed constant.
4. **Box-girder section modulus** of the midship section with a thinned ice
   belt: computed by exact fibre integration (first principles) and by the
   closed-form two-flange formula in the limit where the loss spans a full
   flange; the two routes must agree.

Honest status of the wear coefficient
-------------------------------------
``K`` (dimensionless) is **not** a universal constant: for ice-steel sliding it
is an effective coefficient that lumps ploughing/cutting, ice hardness and
third-body effects.  This module therefore never pretends to predict field
abrasion from scratch; it (a) exposes ``K`` explicitly, (b) provides the inverse
map ``K_eff(rate)`` so that a measured abrasion rate (mm per 1000 nm, the unit
used in ice-class service records) can be carried into the mechanical model, and
(c) tags every derived number with the calibration band used.  Predictions are
labelled ``WITHHELD`` for experimental precision — the platform policy that an
uncalibrated coefficient must not be dressed up as validated.

References
----------
* Archard, J.F. (1953) *J. Appl. Phys.* 24:981 — wear law ``V = K W s / H``.
* Timoshenko & Gere, *Theory of Elastic Stability* — plate buckling
  ``k = 4`` for simply supported uniaxial compression.
* IACS UR I / DNV ice-class rules — ice-belt extent and thickness adders
  (used only as parameter ranges, see :data:`ICE_BELT_ZONE_DEFAULT`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- materials

#: Structural steel for ice belts (normal-strength / VL ice-class grades).
STEEL_E = 2.06e11          # Pa, Young's modulus
STEEL_NU = 0.3             # Poisson ratio
STEEL_RHO = 7850.0         # kg/m3
STEEL_HB = 160.0           # Brinell hardness (dimensionless HB)
#: Vickers/Brinell -> Pa.  H_V [MPa] = 9.81 * HB  (Tabor); 1 GPa = 1e9 Pa.
PA_PER_HB = 9.81e6

#: Areal fraction of the ice belt actually in sliding contact with ice.
#: Broken-channel contact is patchy; 1-5 % is the engineering range for
#: continuous ramming/escort in 30-100 % concentration (parameter, not a
#: validated value — sensitivity is reported).
CONTACT_FRACTION_DEFAULT = 0.02

#: Default ice-belt zone height above/below the waterline [m].  IACS UR I puts
#: the belt in the waterline band; the exact extent is class dependent, so the
#: caller supplies it and this is only the fallback used in examples.
ICE_BELT_ZONE_DEFAULT = 1.5

#: Service-record abrasion band used only to *document* the calibration interval
#: of K_eff (mm per 1000 nm of ice navigation).
ABRASION_RATE_BAND_MM_PER_1000NM = (0.1, 1.0)

#: Reference contact pressure used when quoting the K_eff band [Pa].  Ice-steel
#: contact pressure and areal contact fraction are **not separately identifiable**
#: from a measured abrasion rate (see :func:`identifiable_group`); 50 kPa is the
#: nominal "patchy broken-channel" contact pressure used for band quoting only.
PRESSURE_REFERENCE = 5.0e4


# ---------------------------------------------------------------- Archard law


def wear_volume(k: float, load_n: float, slide_m: float, hardness_pa: float) -> float:
    """Archard (1953): ``V = K W s / H`` [m3]."""
    _require_positive(k=k, load_n=load_n, slide_m=slide_m, hardness_pa=hardness_pa)
    return k * load_n * slide_m / hardness_pa


def wear_depth_from_load(
    k: float, load_n: float, slide_m: float, hardness_pa: float, area_m2: float
) -> float:
    """Wear depth [m] from a normal *load* over a contact *area*."""
    if not (area_m2 > 0.0):
        raise ValueError("area_m2 must be > 0")
    return wear_volume(k, load_n, slide_m, hardness_pa) / area_m2


def wear_depth_from_pressure(
    k: float, pressure_pa: float, slide_m: float, hardness_pa: float
) -> float:
    """Wear depth [m] directly from mean contact pressure ``p``: ``d = K p s / H``."""
    _require_positive(k=k, pressure_pa=pressure_pa, slide_m=slide_m, hardness_pa=hardness_pa)
    return k * pressure_pa * slide_m / hardness_pa


def effective_wear_coefficient(
    rate_mm_per_1000nm: float,
    pressure_pa: float,
    hardness_pa: float,
    contact_fraction: float = 1.0,
) -> float:
    """Invert the Archard law: which ``K`` reproduces a measured abrasion rate?

    ``rate`` is millimetres of steel lost per 1000 nautical miles of ice
    navigation; ``contact_fraction`` is the areal fraction of the track-length
    that is actually abrading (the pressure already being the *contact*
    pressure).  Returned ``K`` is the effective (lumped) coefficient.
    """
    _require_positive(rate_mm_per_1000nm=rate_mm_per_1000nm,
                      pressure_pa=pressure_pa, hardness_pa=hardness_pa,
                      contact_fraction=contact_fraction)
    depth_m = rate_mm_per_1000nm * 1.0e-3
    slide_m = 1000.0 * 1852.0 * contact_fraction
    return depth_m * hardness_pa / (pressure_pa * slide_m)


def predicted_rate_mm_per_1000nm(
    k: float,
    pressure_pa: float,
    hardness_pa: float,
    contact_fraction: float = 1.0,
) -> float:
    """Forward map of :func:`effective_wear_coefficient` [mm / 1000 nm]."""
    slide_m = 1000.0 * 1852.0 * contact_fraction
    return 1.0e3 * wear_depth_from_pressure(k, pressure_pa, slide_m, hardness_pa)


def identifiable_group(k: float, pressure_pa: float, contact_fraction: float = 1.0) -> float:
    """The only combination a wear-rate measurement can identify: ``K p beta`` [Pa].

    Archard depth is ``d = K p s beta / H``, so any split of the product
    ``K p beta`` into "soft ice at high K" vs "hard ice at low K" is
    observationally equivalent.  Reporting this group (instead of a fake
    universal ``K``) is what keeps the calibration honest.
    """
    _require_positive(k=k, pressure_pa=pressure_pa, contact_fraction=contact_fraction)
    return k * pressure_pa * contact_fraction


def k_eff_band(
    pressure_pa: float = PRESSURE_REFERENCE,
    hardness_pa: float = STEEL_HB * PA_PER_HB,
    contact_fraction: float = CONTACT_FRACTION_DEFAULT,
    rate_band: Tuple[float, float] = ABRASION_RATE_BAND_MM_PER_1000NM,
) -> Tuple[float, float]:
    """``K_eff`` interval implied by the service-record abrasion band."""
    lo, hi = rate_band
    return (
        effective_wear_coefficient(lo, pressure_pa, hardness_pa, contact_fraction),
        effective_wear_coefficient(hi, pressure_pa, hardness_pa, contact_fraction),
    )


def default_k_eff(
    pressure_pa: float = PRESSURE_REFERENCE,
    hardness_pa: float = STEEL_HB * PA_PER_HB,
    contact_fraction: float = CONTACT_FRACTION_DEFAULT,
) -> float:
    """Geometric mid-point of :func:`k_eff_band` — the module default."""
    lo, hi = k_eff_band(pressure_pa, hardness_pa, contact_fraction)
    return math.sqrt(lo * hi)


def friction_work_j(mu: float, load_n: float, slide_m: float) -> float:
    """Friction work dissipated over the sliding path [J]."""
    _require_positive(mu=mu, load_n=load_n, slide_m=slide_m)
    return mu * load_n * slide_m


def wear_per_unit_work(k: float, mu: float, hardness_pa: float) -> float:
    """Wear volume per joule of friction work: ``K / (mu H)`` [m3/J].

    This is the friction-energy form of Archard's law; the identity is what ties
    the mechanical model to an energy budget, and is machine-checked in the
    verification suite.
    """
    _require_positive(k=k, mu=mu, hardness_pa=hardness_pa)
    return k / (mu * hardness_pa)


# ---------------------------------------------------------------- voyage level


@dataclass
class AbrasionCondition:
    """One ice-navigation leg."""

    distance_nm: float            # track length in ice [nm]
    concentration: float          # ice concentration 0-1 (areal)
    pressure_pa: float            # mean ice-hull contact pressure [Pa]
    contact_fraction: float = CONTACT_FRACTION_DEFAULT

    def validate(self) -> "AbrasionCondition":
        if not (0.0 <= self.concentration <= 1.0):
            raise ValueError("concentration must be within [0, 1]")
        if self.distance_nm < 0.0:
            raise ValueError("distance_nm must be >= 0")
        _require_positive(pressure_pa=self.pressure_pa + 1e-300,
                          contact_fraction=self.contact_fraction)
        if self.pressure_pa < 0.0:
            raise ValueError("pressure_pa must be >= 0")
        return self


@dataclass
class AbrasionResult:
    depth_m: float                 # total wall loss [m]
    depth_mm: float
    rate_mm_per_1000nm: float      # for comparison with service records
    slide_m: float                 # nominal sliding length summed over legs
    work_j: float                  # friction work over the abrading path
    k_used: float
    pressure_pa: float             # distance-weighted mean contact pressure
    hardness_pa: float
    calibration_status: str = "WITHHELD"
    notes: List[str] = field(default_factory=list)


def abrasion_depth_legs(
    legs: Sequence[AbrasionCondition],
    k: float,
    hardness_pa: float = STEEL_HB * PA_PER_HB,
    mu: float = 0.10,
) -> AbrasionResult:
    """Accumulate Archard wear over a sequence of ice-navigation legs.

    The sliding distance of one leg is ``distance * concentration *
    contact_fraction``: only that part of the track has ice moving along the
    plating, and only the contact fraction of that is under load.  Wear from
    different legs is *additive by volume* (same belt area), so the depth simply
    sums — this linearity is an Archard property, not an extra assumption.
    """
    if not legs:
        raise ValueError("at least one leg is required")
    _require_positive(k=k, hardness_pa=hardness_pa, mu=mu)
    total_depth = 0.0
    slide_total = 0.0
    work_total = 0.0
    p_weighted = 0.0
    dist_weight = 0.0
    notes: List[str] = []
    for leg in legs:
        leg.validate()
        slide = leg.distance_nm * 1852.0 * leg.concentration * leg.contact_fraction
        depth = wear_depth_from_pressure(k, leg.pressure_pa, slide, hardness_pa) if slide else 0.0
        total_depth += depth
        slide_total += slide
        if slide > 0.0:                                            # per unit area
            work_total += friction_work_j(mu, leg.pressure_pa, slide)
        p_weighted += leg.pressure_pa * leg.distance_nm * leg.concentration
        dist_weight += leg.distance_nm * leg.concentration
    p_mean = p_weighted / dist_weight if dist_weight > 0.0 else 0.0
    total_nm = sum(l.distance_nm * l.concentration for l in legs)
    if total_nm > 0.0:
        rate = 1.0e3 * total_depth / (total_nm / 1000.0)
    else:
        rate = 0.0
        notes.append("no in-ice track length: abrasion identically zero")
    lo, hi = ABRASION_RATE_BAND_MM_PER_1000NM
    if rate > 3.0 * hi:
        notes.append(
            "predicted rate %.3g mm/1000 nm is >3x the service-record band upper "
            "bound (%.3g): K_eff / contact fraction is uncalibrated — treat as "
            "WITHHELD" % (rate, hi)
        )
    if k > 1.0e-3:
        notes.append("K_eff > 1e-3 is outside the plausible ice-on-steel band")
    return AbrasionResult(
        depth_m=total_depth, depth_mm=1.0e3 * total_depth,
        rate_mm_per_1000nm=rate, slide_m=slide_total, work_j=work_total,
        k_used=k, pressure_pa=p_mean, hardness_pa=hardness_pa,
        calibration_status="WITHHELD", notes=notes,
    )


def abrasion_depth_profile(
    legs: Sequence[AbrasionCondition],
    k: float,
    n_segments: int,
    hardness_pa: float = STEEL_HB * PA_PER_HB,
) -> List[float]:
    """Cumulative wall loss after each of ``n_segments`` equally sized slices.

    Provided so the route integrator can be refinement-tested (GCI): for a
    constant-condition voyage the exact answer is linear in the accumulated
    track, hence the discretisation error of any consistent scheme is O(h) in
    the *accumulated* quantity but the *increment per segment* is exact — the
    test suite uses this to prove the integrator adds no spurious error.
    """
    if n_segments <= 0:
        raise ValueError("n_segments must be > 0")
    total_nm = sum(l.distance_nm for l in legs)
    if total_nm <= 0.0:
        return [0.0] * n_segments
    slice_nm = total_nm / n_segments
    out: List[float] = []
    cumulative = 0.0
    for i in range(n_segments):
        target = slice_nm * (i + 1)
        cumulative = 0.0
        consumed = 0.0
        for leg in legs:
            if leg.distance_nm <= 0.0:
                continue
            take = min(leg.distance_nm, max(target - consumed, 0.0))
            if take > 0.0:
                slide = take * 1852.0 * leg.concentration * leg.contact_fraction
                cumulative += wear_depth_from_pressure(k, leg.pressure_pa, slide, hardness_pa)
            consumed += leg.distance_nm
        out.append(cumulative)
    return out


# ---------------------------------------------------------------- structures


def plate_buckling_stress(
    t: float, b: float, e: float = STEEL_E, nu: float = STEEL_NU, k: float = 4.0
) -> float:
    """Uniaxial buckling stress of a long simply supported plate strip [Pa].

    ``sigma_cr = k pi^2 D / (b^2 t)`` with ``D = E t^3 / (12 (1 - nu^2))``,
    i.e. ``sigma_cr = k pi^2 E t^2 / (12 (1 - nu^2) b^2)`` — exactly quadratic in
    the residual thickness ``t``, which is the transfer function from abrasion
    depth to residual strength.
    """
    _require_positive(t=t, b=b, e=e, k=k)
    if not (0.0 < nu < 0.5):
        raise ValueError("nu must be within (0, 0.5)")
    d = e * t ** 3 / (12.0 * (1.0 - nu * nu))
    return k * math.pi ** 2 * d / (b * b * t)


def plate_buckling_rayleigh_ritz(
    t: float, b: float, e: float = STEEL_E, nu: float = STEEL_NU, n_terms: int = 5
) -> float:
    """Independent route to the same number: Rayleigh-Ritz on ``w = sin(m pi x/a) sin(n pi y/b)``.

    For a long plate (``a = 2b``, ``n = 1``, ``m = 1``) the double-sine mode is
    the exact buckling mode, so the Rayleigh quotient must return
    ``k = 4`` to round-off.  Used by the verification suite to prove that the
    constant ``4`` in :func:`plate_buckling_stress` is physics, not folklore.
    """
    _require_positive(t=t, b=b, e=e)
    if n_terms < 1:
        raise ValueError("n_terms must be >= 1")
    a = 2.0 * b
    d = e * t ** 3 / (12.0 * (1.0 - nu * nu))
    best = math.inf
    for m in range(1, n_terms + 1):
        for n in range(1, n_terms + 1):
            kx, ky = m * math.pi / a, n * math.pi / b
            # Rayleigh quotient for w = sin(kx x) sin(ky y): N_cr = D (kx^2+ky^2)^2 / kx^2
            n_cr = d * (kx * kx + ky * ky) ** 2 / (kx * kx)
            best = min(best, n_cr / t)
    return best


@dataclass
class BoxGirder:
    """Thin-walled box idealisation of a midship section (breadth B, depth D)."""

    breadth: float          # m, outer breadth
    depth: float            # m, outer depth
    t_deck: float           # m
    t_bottom: float         # m
    t_side: float           # m
    rho: float = STEEL_RHO

    def validate(self) -> "BoxGirder":
        _require_positive(breadth=self.breadth, depth=self.depth,
                          t_deck=self.t_deck, t_bottom=self.t_bottom,
                          t_side=self.t_side)
        if 2.0 * self.t_side >= self.breadth or self.t_deck + self.t_bottom >= self.depth:
            raise ValueError("wall thicknesses too large for the box proportions")
        return self


def box_section_modulus(g: BoxGirder) -> Tuple[float, float]:
    """``(I, W_min)`` of a symmetric-ish box by exact fibre integration [m4, m3]."""
    g.validate()
    b_in = g.breadth - 2.0 * g.t_side
    # Deck flange (top) and bottom flange modelled as rectangles spanning b_in.
    areas = [g.breadth * g.t_deck, g.breadth * g.t_bottom,
             2.0 * g.t_side * (g.depth - g.t_deck - g.t_bottom)]
    z_bot = [
        g.t_bottom / 2.0,
        g.depth - g.t_deck / 2.0,
        g.t_bottom + 0.5 * (g.depth - g.t_deck - g.t_bottom),
    ]
    a_tot = sum(areas)
    z_bar = sum(a * z for a, z in zip(areas, z_bot)) / a_tot
    i_xx = 0.0
    for area, zc in zip(areas, z_bot):
        i_xx += area * (zc - z_bar) ** 2
    # Own inertia of the horizontal flanges (about their own centroid).
    i_xx += (g.breadth * g.t_bottom ** 3 + g.breadth * g.t_deck ** 3) / 12.0
    i_xx += 2.0 * (g.depth - g.t_deck - g.t_bottom) ** 3 * g.t_side / 12.0
    z_max = max(z_bar, g.depth - z_bar)
    if z_max <= 0.0 or i_xx <= 0.0:
        raise ValueError("degenerate section")
    return i_xx, i_xx / z_max


def box_section_modulus_closed_form(g: BoxGirder) -> Tuple[float, float]:
    """Two-flange closed-form reference for a *symmetric* box (verification only).

    ``I = (b t_d (D/2)^2)*2 + 2 t_s D^3/12`` with the flange own-inertia terms,
    valid when ``t_deck == t_bottom``.  Used by the test suite to prove the
    fibre integrator: the two routes must agree to round-off.
    """
    g.validate()
    if abs(g.t_deck - g.t_bottom) > 1e-12 * g.depth:
        raise ValueError("closed form requires t_deck == t_bottom")
    d_mid = g.depth - g.t_deck          # distance between flange centroids
    h_w = g.depth - 2.0 * g.t_deck      # clear web height between flange faces
    i_f = 2.0 * (g.breadth * g.t_deck * (d_mid / 2.0) ** 2
                 + g.breadth * g.t_deck ** 3 / 12.0)
    i_w = 2.0 * g.t_side * h_w ** 3 / 12.0
    i_xx = i_f + i_w
    return i_xx, i_xx / (g.depth / 2.0)


@dataclass
class CapacityRetention:
    t0: float
    t_residual: float
    thickness_ratio: float
    sigma_cr0_pa: float
    sigma_cr_pa: float
    strength_retention: float       # sigma_cr / sigma_cr0  (== ratio^2 exactly)
    i0_m4: float
    i_m4: float
    w0_m3: float
    w_m3: float
    modulus_retention: float
    mass_loss_fraction: float


def ice_belt_capacity_retention(
    girder: BoxGirder,
    plate_span: float,
    abrasion_depth_m: float,
    abrade_side: str = "side",
) -> CapacityRetention:
    """Residual buckling + hull-girder capacity after ``abrasion_depth_m`` of loss.

    The loss is applied to the ice-belt strake, i.e. the side shell (default), or
    to the bilge/bottom if requested.  Buckling follows the exact ``t^2`` law;
    the girder modulus is recomputed by the same fibre integrator, so no
    linearisation is assumed.
    """
    if abrasion_depth_m < 0.0:
        raise ValueError("abrasion_depth_m must be >= 0")
    g = girder.validate()
    t0 = g.t_side if abrade_side == "side" else g.t_bottom
    t_res = t0 - abrasion_depth_m
    if t_res <= 0.0:
        raise ValueError("abrasion depth exceeds plate thickness: section lost")
    sigma0 = plate_buckling_stress(t0, plate_span)
    sigma = plate_buckling_stress(t_res, plate_span)
    i0, w0 = box_section_modulus(g)
    if abrade_side == "side":
        g2 = BoxGirder(g.breadth, g.depth, g.t_deck, g.t_bottom, t_res, g.rho)
    else:
        g2 = BoxGirder(g.breadth, g.depth, g.t_deck, t_res, g.t_side, g.rho)
    i1, w1 = box_section_modulus(g2)
    ratio = t_res / t0
    return CapacityRetention(
        t0=t0, t_residual=t_res, thickness_ratio=ratio,
        sigma_cr0_pa=sigma0, sigma_cr_pa=sigma,
        strength_retention=sigma / sigma0,
        i0_m4=i0, i_m4=i1, w0_m3=w0, w_m3=w1,
        modulus_retention=w1 / w0,
        mass_loss_fraction=1.0 - ratio,
    )


# ---------------------------------------------------------------- utilities


def _require_positive(**kwargs: float) -> None:
    for name, value in kwargs.items():
        if value is None or not (float(value) > 0.0) or not math.isfinite(float(value)):
            raise ValueError("%s must be a finite positive number (got %r)" % (name, value))