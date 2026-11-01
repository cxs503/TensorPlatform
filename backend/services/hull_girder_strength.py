"""Hull girder longitudinal strength (总纵强度) — verifiable reference implementation.

Scope
-----
The ship-performance platform had no hull-girder (结构/总纵强度) capability at all:
the structural evidence was limited to plate-panel buckling (`plate-stability`).
This module fills that hole with three *analytically verifiable* layers:

1. ``SectionProperties`` — built-up midship section (deck / bottom / side shell /
   inner bottom / longitudinal bulkheads) reduced by the parallel-axis theorem to
   area ``A``, neutral-axis height ``z_NA``, second moment of area ``I`` and the
   deck/bottom section moduli ``W_deck``, ``W_bottom``.
2. ``still_water_shear_bending`` — shear force / bending moment distribution of a
   floating body, obtained by double integration of the load curve
   ``q(x) = b(x) - w(x)``, with an explicit hydrostatic-equilibrium residual so
   a non-floating case cannot silently pass.
3. ``wave_bending_moment`` / ``minimum_section_modulus`` — IACS UR S11 (Rev.7)
   closed-form wave bending moment envelope and IACS UR S7 minimum midship
   section modulus, plus the yielding check ``sigma = M / W <= 175 / k``.

Evidence grading (platform convention)
--------------------------------------
* Layers 1–2 are **REAL**: pure mechanics, validated against closed-form
  solutions (see ``backend/tests/test_hull_girder_strength.py`` and
  ``artifacts/hull_girder/``).  For a polynomial load curve the integration is
  exact up to the trapezoidal truncation error, and the two analytic identities
  ``M(L/2) = ΔL/32`` (parabolic weight case) and ``SF(L) = BM(L) = 0`` are used
  as machine-checkable criteria.
* Layer 3 reproduces an *empirical regulation formula* (IACS UR S11/S7).  It is
  **REAL as a formula re-implementation**, but it is *not* first-principles
  physics: agreement with the rule is by construction and is reported as such.
  Nothing here constitutes a class-approval calculation.

Applicability limits (honest)
-----------------------------
* Beam theory only (Euler–Bernoulli, no shear lag, no warping, no torsion, no
  post-buckling reserve).  Ultimate strength, fatigue and local load effects are
  out of scope.
* IACS S11 wave moments are valid for ``90 m <= L <= 500 m`` (C formula
  branches for ``L < 90 m`` are provided but flagged ``extrapolated``).
* No still-water moment from tank/dry-cargo loads is invented: the caller must
  supply the actual weight distribution.  A missing/unbalanced weight curve is
  reported as an equilibrium residual, never patched by a fudge factor.

Units: SI throughout (m, kg, N, N·m), except the regulation formulas which are
fed metres and return kN·m, and the yielding check which reports N/mm² (MPa).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

G = 9.80665  # m/s²
RHO_SEA_WATER = 1025.0  # kg/m³

#: IACS UR S11 §3.2: permissible hull girder normal stress, sigma_perm = 175 / k
#: [N/mm²].  Material factor k for mild steel is 1.0; typical NV/AH higher
#: strength steels use k = 0.78 (AH32) and 0.72 (AH36).
MATERIAL_K = {"mild": 1.0, "AH32": 0.78, "AH36": 0.72, "AH40": 0.68}
SIGMA_PERM_BASE_MPA = 175.0

#: UR S11 applicability window for the wave-bending-moment formulas.
S11_MIN_LENGTH = 90.0
S11_MAX_LENGTH = 500.0


# ---------------------------------------------------------------------------
# 1. Section properties
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SectionElement:
    """A rectangular scantling element of a transverse section.

    ``width`` is measured horizontally (or along the plate for a vertical
    shell), ``height`` vertically; the element centre is at ``z_centre`` and its
    own local second moment about a horizontal axis through that centre is
    ``width * height**3 / 12``.
    """

    name: str
    width: float      # m — plate extent in the horizontal (for decks) or vertical (for shells) direction
    height: float     # m — extent in the direction perpendicular to the plate face used for I
    z_centre: float   # m above base line
    thickness: float = 0.0  # m — kept for reporting; must equal min(width, height)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def own_inertia(self) -> float:
        """Second moment about the element's own horizontal centroidal axis."""
        return self.width * self.height ** 3 / 12.0


def horizontal_plate(name: str, breadth: float, z: float, thickness: float) -> SectionElement:
    """Horizontal plate (deck / bottom / inner bottom) of the given scantling."""
    return SectionElement(name=name, width=breadth, height=thickness, z_centre=z, thickness=thickness)


def vertical_shell(name: str, height: float, z_base: float, thickness: float) -> SectionElement:
    """Vertical shell (side shell / longitudinal bulkhead) of the given scantling."""
    return SectionElement(
        name=name, width=thickness, height=height, z_centre=z_base + height / 2.0, thickness=thickness
    )


@dataclass
class SectionProperties:
    elements: List[SectionElement]
    total_area: float
    z_neutral_axis: float
    inertia: float
    section_modulus_deck: float
    section_modulus_bottom: float
    depth: float
    per_element: List[Dict[str, float]] = field(default_factory=list)

    def modulus_at(self, z: float) -> float:
        """Section modulus referred to the fibre at height ``z``."""
        dz = abs(z - self.z_neutral_axis)
        if dz <= 1e-12:
            raise ValueError("requested fibre lies on the neutral axis")
        return self.inertia / dz

    @property
    def neutral_axis_ratio(self) -> float:
        """z_NA / D — for a symmetric section this is 0.5."""
        return self.z_neutral_axis / self.depth if self.depth else float("nan")

    def as_dict(self) -> Dict[str, float]:
        return {
            "total_area_m2": self.total_area,
            "z_neutral_axis_m": self.z_neutral_axis,
            "inertia_m4": self.inertia,
            "section_modulus_deck_m3": self.section_modulus_deck,
            "section_modulus_bottom_m3": self.section_modulus_bottom,
            "depth_m": self.depth,
            "neutral_axis_ratio": self.neutral_axis_ratio,
        }


def section_properties(elements: Sequence[SectionElement], depth: float) -> SectionProperties:
    """Reduce a set of scantlings to A, z_NA, I, W_deck, W_bottom.

    Parallel-axis theorem: ``I = sum(I_own + A_i * (z_NA - z_i)**2)``.
    """
    if not elements:
        raise ValueError("section must contain at least one element")
    if depth <= 0:
        raise ValueError("section depth must be positive")

    total_area = 0.0
    first_moment = 0.0
    for el in elements:
        if el.width <= 0 or el.height <= 0:
            raise ValueError(f"element {el.name!r} has non-positive scantling")
        total_area += el.area
        first_moment += el.area * el.z_centre
    z_na = first_moment / total_area

    inertia = 0.0
    per_element: List[Dict[str, float]] = []
    for el in elements:
        delta = z_na - el.z_centre
        inertia += el.own_inertia + el.area * delta ** 2
        per_element.append(
            {
                "name": el.name,
                "area_m2": el.area,
                "z_centre_m": el.z_centre,
                "own_inertia_m4": el.own_inertia,
                "parallel_axis_m4": el.area * delta ** 2,
            }
        )

    w_deck = inertia / (depth - z_na) if depth - z_na > 1e-12 else float("inf")
    w_bottom = inertia / z_na if z_na > 1e-12 else float("inf")

    return SectionProperties(
        elements=list(elements),
        total_area=total_area,
        z_neutral_axis=z_na,
        inertia=inertia,
        section_modulus_deck=w_deck,
        section_modulus_bottom=w_bottom,
        depth=depth,
        per_element=per_element,
    )


def box_girder_section(
    breadth: float, depth: float, t_deck: float, t_bottom: float, t_side: float
) -> SectionProperties:
    """Single-skin box girder: deck, bottom, two side shells (no double bottom)."""
    elements = [
        horizontal_plate("deck", breadth, depth - t_deck / 2.0, t_deck),
        horizontal_plate("bottom", breadth, t_bottom / 2.0, t_bottom),
        vertical_shell("side_P", depth - t_bottom, t_bottom, t_side),
        vertical_shell("side_S", depth - t_bottom, t_bottom, t_side),
    ]
    return section_properties(elements, depth)


def t_section(
    flange_breadth: float, flange_thickness: float, web_depth: float, web_thickness: float
) -> SectionProperties:
    """Inverted-T (flange on top) — the standard textbook check case."""
    total_depth = flange_thickness + web_depth
    elements = [
        horizontal_plate("flange", flange_breadth, web_depth + flange_thickness / 2.0, flange_thickness),
        vertical_shell("web", web_depth, 0.0, web_thickness),
    ]
    return section_properties(elements, total_depth)


# ---------------------------------------------------------------------------
# 2. Still-water shear force and bending moment
# ---------------------------------------------------------------------------

@dataclass
class ShearBendingDistribution:
    x: List[float]
    shear_n: List[float]
    moment_nm: List[float]
    buoyancy_total_n: float
    weight_total_n: float
    equilibrium_residual: float
    moment_closure_nm: float

    @property
    def midship_moment_nm(self) -> float:
        return self.moment_nm[len(self.moment_nm) // 2]

    @property
    def max_moment_nm(self) -> float:
        return max(self.moment_nm, key=abs)

    @property
    def max_shear_n(self) -> float:
        return max(self.shear_n, key=abs)


def _validate_curve(name: str, values: Sequence[float], n: int) -> None:
    if len(values) != n:
        raise ValueError(f"{name} has {len(values)} samples, expected {n}")
    for v in values:
        if not math.isfinite(v):
            raise ValueError(f"{name} contains a non-finite sample")


def still_water_shear_bending(
    stations: Sequence[float],
    weight_per_m: Sequence[float],
    buoyancy_per_m: Sequence[float],
    *,
    equilibrium_tolerance: float = 0.01,
) -> ShearBendingDistribution:
    """Double-integrate the load curve ``q = b - w`` from the aft end.

    ``weight_per_m`` / ``buoyancy_per_m`` are line loads in N/m sampled at
    ``stations`` (m from AP).  The free-end boundary conditions ``SF(0)=0`` and
    ``BM(0)=0`` are imposed exactly; the closure values at the forward end are
    returned so a caller can see the discretisation residual instead of
    trusting a silent zero.

    ``equilibrium_tolerance`` is a *fraction of displacement*: if
    ``|buoyancy - weight| / weight`` exceeds it, no result is fabricated —
    a ``ValueError`` is raised, because a non-floating body has no still-water
    bending moment.
    """
    n = len(stations)
    if n < 3:
        raise ValueError("at least three stations are required")
    _validate_curve("weight_per_m", weight_per_m, n)
    _validate_curve("buoyancy_per_m", buoyancy_per_m, n)

    weight_total = 0.0
    buoyancy_total = 0.0
    for i in range(n - 1):
        dx = stations[i + 1] - stations[i]
        if dx <= 0:
            raise ValueError("stations must be strictly increasing")
        weight_total += 0.5 * (weight_per_m[i] + weight_per_m[i + 1]) * dx
        buoyancy_total += 0.5 * (buoyancy_per_m[i] + buoyancy_per_m[i + 1]) * dx

    if weight_total <= 0:
        raise ValueError("total weight must be positive")
    residual = (buoyancy_total - weight_total) / weight_total
    if abs(residual) > equilibrium_tolerance:
        raise ValueError(
            "hydrostatic equilibrium violated: buoyancy/weight - 1 = "
            f"{residual:.6f} exceeds tolerance {equilibrium_tolerance:.4f}; "
            "the body is not floating at the supplied waterline"
        )

    shear = [0.0] * n
    moment = [0.0] * n
    for i in range(n - 1):
        dx = stations[i + 1] - stations[i]
        q_i = buoyancy_per_m[i] - weight_per_m[i]
        q_i1 = buoyancy_per_m[i + 1] - weight_per_m[i + 1]
        shear[i + 1] = shear[i] + 0.5 * (q_i + q_i1) * dx
        moment[i + 1] = moment[i] + 0.5 * (shear[i] + shear[i + 1]) * dx

    return ShearBendingDistribution(
        x=list(stations),
        shear_n=shear,
        moment_nm=moment,
        buoyancy_total_n=buoyancy_total,
        weight_total_n=weight_total,
        equilibrium_residual=residual,
        moment_closure_nm=moment[-1],
    )


def uniform_buoyancy_from_displacement(
    stations: Sequence[float], displacement_kg: float, cb: float = 1.0
) -> List[float]:
    """Buoyancy line load (N/m) of a prismatic hull, zero outside the waterline.

    ``cb`` is only used for a note in the report path; a prismatic hull with
    vertical sides at the waterline has a *rectangular* buoyancy distribution,
    so the load is uniform over the length.  Non-prismatic hulls must supply
    their own Bonjean-derived curve.
    """
    if displacement_kg <= 0:
        raise ValueError("displacement must be positive")
    length = stations[-1] - stations[0]
    if length <= 0:
        raise ValueError("stations must span a positive length")
    if not (0.0 < cb <= 1.0):
        raise ValueError("cb must lie in (0, 1]")
    b0 = displacement_kg * G / length
    return [b0] * len(stations)


def parabolic_weight_per_m(stations: Sequence[float], displacement_kg: float) -> List[float]:
    """Textbook analytic weight case: ``w(x) = 6Δ/L · (x/L)(1 - x/L)``.

    Chosen because the resulting bending moment has the closed form
    ``BM(x) = Δ(x²/2L - x³/L² + x⁴/2L³)`` and therefore ``BM_mid = Δ·L/32``
    (hogging) — an independent check of the integration kernel.
    """
    length = stations[-1] - stations[0]
    total = displacement_kg * G
    w = []
    for x in stations:
        xi = (x - stations[0]) / length
        w.append(6.0 * total / length * xi * (1.0 - xi))
    return w


# ---------------------------------------------------------------------------
# 3. Wave bending moment / minimum section modulus (IACS UR S11 & S7)
# ---------------------------------------------------------------------------

def wave_coefficient(length: float) -> Tuple[float, bool]:
    """IACS UR S11 wave coefficient ``C`` and whether it is extrapolated.

    ``C = 10.75 - ((300 - L)/100)**1.5`` for ``90 <= L <= 300``;
    ``C = 10.75`` for ``300 < L <= 500``; ``C = 0.0412·L + 4.0`` below 90 m
    (flagged as outside the rule's validity window).
    """
    if length <= 0:
        raise ValueError("length must be positive")
    if length < S11_MIN_LENGTH:
        return 0.0412 * length + 4.0, True
    if length <= 300.0:
        return 10.75 - ((300.0 - length) / 100.0) ** 1.5, False
    return 10.75, length > S11_MAX_LENGTH


def wave_bending_moment(length: float, breadth: float, cb: float, *, hogging: bool = True) -> float:
    """IACS UR S11 §3.2 wave bending moment in kN·m (hogging positive)."""
    if not (0.0 < cb <= 1.0):
        raise ValueError("cb must lie in (0, 1]")
    if breadth <= 0:
        raise ValueError("breadth must be positive")
    c, _ = wave_coefficient(length)
    if hogging:
        return 0.19 * c * length ** 2 * breadth * cb
    return -0.11 * c * length ** 2 * breadth * (cb + 0.7)


def wave_bending_moment_envelope(length: float, breadth: float, cb: float) -> Dict[str, float]:
    c, extrapolated = wave_coefficient(length)
    return {
        "wave_coefficient_C": c,
        "within_s11_window": 0.0 if extrapolated else 1.0,
        "m_wave_hogging_knm": wave_bending_moment(length, breadth, cb, hogging=True),
        "m_wave_sagging_knm": wave_bending_moment(length, breadth, cb, hogging=False),
    }


def minimum_section_modulus(length: float, breadth: float, cb: float) -> float:
    """IACS UR S7 minimum midship section modulus in m³.

    ``W_min = C·L²·B·(Cb + 0.7) · 10**-6``.
    """
    if not (0.0 < cb <= 1.0):
        raise ValueError("cb must lie in (0, 1]")
    c, _ = wave_coefficient(length)
    return c * length ** 2 * breadth * (cb + 0.7) * 1e-6


def permissible_stress_mpa(material: str = "mild") -> float:
    key = material if material in MATERIAL_K else None
    if key is None:
        raise ValueError(f"unknown material {material!r}; known: {sorted(MATERIAL_K)}")
    return SIGMA_PERM_BASE_MPA / MATERIAL_K[key]


def bending_stress_mpa(bending_moment_nm: float, section_modulus_m3: float) -> float:
    """Beam-theory normal stress ``sigma = M / W`` in N/mm² (MPa)."""
    if section_modulus_m3 <= 0:
        raise ValueError("section modulus must be positive")
    return abs(bending_moment_nm) / section_modulus_m3 / 1e6


def yielding_check(
    bending_moment_nm: float, section_modulus_m3: float, material: str = "mild"
) -> Dict[str, object]:
    sigma = bending_stress_mpa(bending_moment_nm, section_modulus_m3)
    perm = permissible_stress_mpa(material)
    return {
        "material": material,
        "sigma_mpa": sigma,
        "sigma_perm_mpa": perm,
        "utilisation": sigma / perm,
        "pass": sigma <= perm,
    }


def hull_girder_assessment(
    length: float,
    breadth: float,
    depth: float,
    cb: float,
    displacement_kg: float,
    section: SectionProperties,
    *,
    t_deck: float,
    t_bottom: float,
    t_side: float,
    material: str = "mild",
    n_stations: int = 2001,
) -> Dict[str, object]:
    """End-to-end midship assessment: SWBM + wave envelope → stress → verdict.

    Deliberately explicit about what is assumed: the still-water case is the
    analytic parabolic weight distribution (a *benchmark*, not a real loading
    condition); the wave moments follow UR S11; the section is the caller's
    scantlings.  ``verdict`` is ``PASS`` only when the worst combined stress
    satisfies the UR S11 permissible stress **and** the minimum modulus of UR S7.
    """
    stations = [length * i / (n_stations - 1) for i in range(n_stations)]
    weight = parabolic_weight_per_m(stations, displacement_kg)
    buoyancy = uniform_buoyancy_from_displacement(stations, displacement_kg, cb)
    dist = still_water_shear_bending(stations, weight, buoyancy)

    envelope = wave_bending_moment_envelope(length, breadth, cb)
    m_wave = max(abs(envelope["m_wave_hogging_knm"]), abs(envelope["m_wave_sagging_knm"])) * 1e3

    # Worst combination per fibre: hogging puts the deck in compression, sagging
    # in tension; the governing stress magnitude uses the smaller modulus.
    w_governing = min(section.section_modulus_deck, section.section_modulus_bottom)
    swbm = max(abs(dist.max_moment_nm), abs(dist.midship_moment_nm))
    sigma_sw = bending_stress_mpa(swbm, w_governing)
    sigma_wave = bending_stress_mpa(m_wave, w_governing)
    sigma_total = sigma_sw + sigma_wave
    perm = permissible_stress_mpa(material)

    w_required = minimum_section_modulus(length, breadth, cb)
    w_actual = min(section.section_modulus_deck, section.section_modulus_bottom)

    return {
        "input": {
            "length_m": length,
            "breadth_m": breadth,
            "depth_m": depth,
            "cb": cb,
            "displacement_t": displacement_kg / 1000.0,
            "t_deck_mm": t_deck * 1e3,
            "t_bottom_mm": t_bottom * 1e3,
            "t_side_mm": t_side * 1e3,
            "material": material,
        },
        "section": section.as_dict(),
        "still_water": {
            "midship_moment_nm": dist.midship_moment_nm,
            "max_moment_nm": dist.max_moment_nm,
            "max_shear_n": dist.max_shear_n,
            "equilibrium_residual": dist.equilibrium_residual,
            "moment_closure_nm": dist.moment_closure_nm,
            "midship_analytic_nm": displacement_kg * G * length / 32.0,
        },
        "wave": envelope,
        "stress": {
            "sigma_still_water_mpa": sigma_sw,
            "sigma_wave_mpa": sigma_wave,
            "sigma_total_mpa": sigma_total,
            "sigma_perm_mpa": perm,
            "utilisation": sigma_total / perm,
        },
        "modulus": {
            "w_actual_m3": w_actual,
            "w_required_m3": w_required,
            "ratio": w_actual / w_required,
        },
        "verdict": "PASS" if (sigma_total <= perm and w_actual >= w_required) else "FAIL",
        "notes": [
            "Beam theory only: no shear lag / warping / torsion / ultimate strength.",
            "Still-water case is the analytic parabolic-weight benchmark, not a real loading condition.",
            "Wave bending moment per IACS UR S11; permissible stress 175/k N/mm2 (UR S11 §3.2).",
        ],
    }

def double_hull_section(
    breadth: float,
    depth: float,
    t_deck: float,
    t_bottom: float,
    t_side: float,
    *,
    double_bottom_height: float,
    t_inner_bottom: float,
    inner_side_offset: float,
    t_inner_side: float,
) -> SectionProperties:
    """Double-hull (double bottom + top-side tanks) midship section.

    This is the section type of a real bulker/tanker, so it is the one that can
    actually be compared with the UR S7 minimum modulus.  Only longitudinal
    material (plating) is counted: longitudinals/stiffeners, hopper knuckles and
    deck girders are *not* modelled, which makes this a lower bound on I — the
    error direction is conservative for W and therefore anti-conservative for
    stress, and it is reported as such.
    """
    if double_bottom_height <= 0 or double_bottom_height >= depth:
        raise ValueError("double_bottom_height must lie inside the depth")
    if not (0 < inner_side_offset < breadth / 2.0):
        raise ValueError("inner_side_offset must be less than half the breadth")

    elements = [
        horizontal_plate("deck", breadth, depth - t_deck / 2.0, t_deck),
        horizontal_plate("bottom", breadth, t_bottom / 2.0, t_bottom),
        horizontal_plate(
            "inner_bottom",
            breadth - 2.0 * inner_side_offset,
            double_bottom_height + t_inner_bottom / 2.0,
            t_inner_bottom,
        ),
        vertical_shell("side_P", depth - t_bottom, t_bottom, t_side),
        vertical_shell("side_S", depth - t_bottom, t_bottom, t_side),
        vertical_shell(
            "inner_side_P",
            depth - double_bottom_height - t_inner_bottom,
            double_bottom_height + t_inner_bottom,
            t_inner_side,
        ),
        vertical_shell(
            "inner_side_S",
            depth - double_bottom_height - t_inner_bottom,
            double_bottom_height + t_inner_bottom,
            t_inner_side,
        ),
    ]
    return section_properties(elements, depth)
