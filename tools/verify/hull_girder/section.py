"""Hull-girder cross-section properties (structural strength discipline).

Three *independent* descriptions of the same midship section are implemented on
purpose, so that they can check each other (see ``benchmarks.py``):

1. ``rect_props``          - exact analytic integration over axis-aligned rectangles
                             (each structural part of the midship section is a
                             rectangle in the (y, z) plane; both sides are given
                             explicitly, nothing is silently mirrored).
2. ``polygon_props``       - Green's theorem over closed contours, with signed
                             orientation so that voids (holes) are handled exactly.
                             Shares no code with (1).
3. ``box_closed_form``     - textbook hollow-rectangle formula
                             ``A = B D - b h``, ``I = (B D^3 - b h^3)/12``, valid for a
                             *concentric* single-skin box (t_deck == t_bottom).
                             This is the external closed-form reference in the
                             sense of a literature/standard formula.

The strip model (``Section.elements``) is the discretisation used by the Smith
progressive-collapse method in ``smith.py``; its discretisation error is measured
explicitly instead of being assumed small.

Sign conventions: ``z`` is vertical, 0 at baseline, positive upwards [m];
``y`` is transverse, 0 at centreline [m]; all lengths in metres, stresses in Pa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

__all__ = [
    "Part",
    "Element",
    "Section",
    "rect_props",
    "polygon_props",
    "box_closed_form",
    "plate_buckling_stress",
    "make_midship_section",
    "make_box_section",
    "make_two_flange_section",
]


@dataclass(frozen=True)
class Part:
    """One structural part of the midship section (axis-aligned rectangle).

    ``panel_breadth`` / ``t_plate`` describe the *buckling panel* of that part
    (stiffener spacing across the load direction and plate thickness).  They are
    independent of the rectangle thickness because e.g. a bottom plate is a
    rectangle that is wide in y and thin in z, while its buckling panel is a
    square-ish plate of breadth ``panel_breadth``.
    """

    label: str
    y0: float
    y1: float
    z0: float
    z1: float
    panel_breadth: float
    t_plate: float

    def __post_init__(self) -> None:
        if self.y1 <= self.y0 or self.z1 <= self.z0:
            raise ValueError(f"degenerate part {self.label}")
        if self.panel_breadth <= 0 or self.t_plate <= 0:
            raise ValueError(f"bad buckling panel for {self.label}")

    @property
    def area(self) -> float:
        return (self.y1 - self.y0) * (self.z1 - self.z0)

    def contour(self) -> List[Tuple[float, float]]:
        """Rectangle outline, counter-clockwise in (y, z)."""
        return [(self.y0, self.z0), (self.y1, self.z0), (self.y1, self.z1), (self.y0, self.z1)]


@dataclass(frozen=True)
class Element:
    """Horizontal strip of a part, used by Smith's method."""

    label: str
    area: float
    z: float  # centroid height [m]
    sigma_cr: float  # plate buckling stress [Pa]


@dataclass
class Section:
    """A midship section built from structural parts."""

    name: str
    parts: List[Part]
    breadth: float  # B [m]
    depth: float  # D [m]
    meta: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- properties
    def rect_moments(self) -> Tuple[float, float, float]:
        """(A, z_c, I_c) from exact rectangle integration."""
        return rect_props(self.parts)

    def polygon_moments(self) -> Tuple[float, float, float]:
        """(A, z_c, I_c) from Green's theorem over the part contours."""
        return polygon_props([p.contour() for p in self.parts])

    def section_moduli(self) -> Tuple[float, float]:
        """(Z_bottom, Z_deck) about the elastic neutral axis [m^3]."""
        a, zc, ic = self.rect_moments()
        return ic / zc, ic / (self.depth - zc)

    def neutral_axis(self) -> float:
        _, zc, _ = self.rect_moments()
        return zc

    def inertia(self) -> float:
        _, _, ic = self.rect_moments()
        return ic

    def area(self) -> float:
        a, _, _ = self.rect_moments()
        return a

    # ---------------------------------------------------------------- strip model
    def elements(self, n_strips: int = 40, E: float = 206e9, nu: float = 0.3) -> List[Element]:
        """Discretise every part into ``n_strips`` horizontal strips."""
        out: List[Element] = []
        for p in self.parts:
            dz = (p.z1 - p.z0) / n_strips
            width = p.y1 - p.y0
            scr = plate_buckling_stress(p.t_plate, p.panel_breadth, E=E, nu=nu)
            for k in range(n_strips):
                z = p.z0 + (k + 0.5) * dz
                out.append(Element(label=p.label, area=width * dz, z=z, sigma_cr=scr))
        return out

    def strip_props(self, n_strips: int = 40) -> Tuple[float, float, float]:
        """(A, z_c, I_c) from the strip model - sizes the discretisation error."""
        els = self.elements(n_strips)
        a = sum(e.area for e in els)
        zc = sum(e.area * e.z for e in els) / a
        ic = sum(e.area * (e.z - zc) ** 2 for e in els)  # own inertia of a thin strip negligible
        return a, zc, ic


# ---------------------------------------------------------------------- helpers
def plate_buckling_stress(t: float, b: float, E: float = 206e9, nu: float = 0.3) -> float:
    """Euler buckling stress of a simply-supported plate strip under uniaxial
    compression: ``sigma_cr = pi^2 E / (12 (1 - nu^2)) * (t/b)^2``."""
    if t <= 0 or b <= 0:
        raise ValueError("t and b must be positive")
    return math.pi ** 2 * E / (12.0 * (1.0 - nu ** 2)) * (t / b) ** 2


def rect_props(parts: Sequence[Part]) -> Tuple[float, float, float]:
    """Exact (A, z_c, I_c) of a set of axis-aligned rectangles.

    ``A = sum W dz``, ``Q = sum W (z1^2 - z0^2)/2``, ``I_0 = sum W (z1^3 - z0^3)/3``,
    then ``I_c = I_0 - A z_c^2`` (parallel-axis theorem).
    """
    if not parts:
        raise ValueError("empty section")
    a = q = i0 = 0.0
    for p in parts:
        w = p.y1 - p.y0
        a += w * (p.z1 - p.z0)
        q += w * (p.z1 ** 2 - p.z0 ** 2) / 2.0
        i0 += w * (p.z1 ** 3 - p.z0 ** 3) / 3.0
    if a <= 0:
        raise ValueError("non-positive area")
    zc = q / a
    return a, zc, i0 - a * zc ** 2


def polygon_props(contours: Sequence[Sequence[Tuple[float, float]]]) -> Tuple[float, float, float]:
    """Exact (A, z_c, I_c) of a set of closed contours via Green's theorem.

    Contours wound counter-clockwise add area, clockwise subduct it, so holes are
    handled exactly.  Implementation is independent of :func:`rect_props`
    (deliberately: shared code would defeat the cross-check).
    """
    a2 = q6 = i12 = 0.0
    for pts in contours:
        n = len(pts)
        if n < 3:
            raise ValueError("contour needs >= 3 vertices")
        for i in range(n):
            y0, z0 = pts[i]
            y1, z1 = pts[(i + 1) % n]
            cross = y0 * z1 - y1 * z0
            a2 += cross
            q6 += (z0 + z1) * cross
            i12 += (z0 * z0 + z0 * z1 + z1 * z1) * cross
    area = a2 / 2.0
    if abs(area) < 1e-15:
        raise ValueError("degenerate contour set")
    zc = (q6 / 6.0) / area
    i0 = i12 / 12.0
    return abs(area), zc, abs(i0 - area * zc ** 2)


def box_closed_form(B: float, D: float, t: float) -> Tuple[float, float, float]:
    """Textbook closed form of a *concentric* single-skin box girder.

    Outer ``B x D`` with uniform shell thickness ``t``, so the void is
    ``b x h = (B - 2t) x (D - 2t)``::

        A = B D - b h,   z_c = D / 2,   I = (B D^3 - b h^3) / 12

    Returns ``(A, z_c, I_c)``.  This is the external reference the numeric
    integration is judged against, and it is exact only when the deck and bottom
    plate thicknesses are equal (concentric void).
    """
    b = B - 2.0 * t
    h = D - 2.0 * t
    if b <= 0 or h <= 0:
        raise ValueError("shell too thick for the given box")
    return B * D - b * h, D / 2.0, (B * D ** 3 - b * h ** 3) / 12.0


def make_box_section(B: float = 20.0, D: float = 12.0, t: float = 0.020,
                     panel_breadth: float = 0.8, name: str = "single-skin box girder") -> Section:
    """Single-skin box girder with uniform shell thickness (matches ``box_closed_form``)."""
    parts = [
        Part("deck", -B / 2.0, B / 2.0, D - t, D, panel_breadth, t),
        Part("bottom", -B / 2.0, B / 2.0, 0.0, t, panel_breadth, t),
        Part("side_stbd", B / 2.0 - t, B / 2.0, t, D - t, panel_breadth, t),
        Part("side_port", -B / 2.0, -B / 2.0 + t, t, D - t, panel_breadth, t),
    ]
    return Section(name=name, parts=parts, breadth=B, depth=D,
                   meta={"B": B, "D": D, "t": t, "panel_breadth": panel_breadth})


def make_midship_section(
    B: float = 50.0,
    D: float = 25.0,
    t_deck: float = 0.030,
    t_bottom: float = 0.032,
    t_side_outer: float = 0.022,
    b_inner: float = 40.0,
    h_inner_bottom: float = 2.5,
    h_inner_top: float = 22.0,
    t_inner_bottom: float = 0.020,
    t_inner_deck: float = 0.020,
    t_bulkhead: float = 0.018,
    panel_breadth: float = 0.85,
    name: str = "idealised double-hull midship section",
) -> Section:
    """Idealised double-hull / double-bottom midship section (a VLCC-scale proxy).

    Parts (metres, both sides explicit):
      * deck plate, full breadth, at the top;
      * bottom plate, full breadth, at the baseline;
      * two outer side shells;
      * inner bottom and inner deck of the double-bottom / double-hull box;
      * two longitudinal bulkheads closing the inner box.

    The section is a *structural* idealisation: no longitudinals, brackets or
    cut-outs, hence its section modulus is an upper bound of the real ship's.
    """
    parts: List[Part] = [
        Part("deck", -B / 2.0, B / 2.0, D - t_deck, D, panel_breadth, t_deck),
        Part("bottom", -B / 2.0, B / 2.0, 0.0, t_bottom, panel_breadth, t_bottom),
        Part("side_stbd", B / 2.0 - t_side_outer, B / 2.0, t_bottom, D - t_deck,
             panel_breadth, t_side_outer),
        Part("side_port", -B / 2.0, -B / 2.0 + t_side_outer, t_bottom, D - t_deck,
             panel_breadth, t_side_outer),
        Part("inner_bottom", -b_inner / 2.0, b_inner / 2.0,
             h_inner_bottom - t_inner_bottom, h_inner_bottom, panel_breadth, t_inner_bottom),
        Part("inner_deck", -b_inner / 2.0, b_inner / 2.0,
             h_inner_top, h_inner_top + t_inner_deck, panel_breadth, t_inner_deck),
        Part("bulkhead_stbd", b_inner / 2.0 - t_bulkhead, b_inner / 2.0,
             h_inner_bottom, h_inner_top, panel_breadth, t_bulkhead),
        Part("bulkhead_port", -b_inner / 2.0, -b_inner / 2.0 + t_bulkhead,
             h_inner_bottom, h_inner_top, panel_breadth, t_bulkhead),
    ]
    return Section(name=name, parts=parts, breadth=B, depth=D,
                   meta={"B": B, "D": D, "t_deck": t_deck, "t_bottom": t_bottom,
                         "t_side_outer": t_side_outer, "b_inner": b_inner,
                         "h_inner_bottom": h_inner_bottom, "h_inner_top": h_inner_top,
                         "t_inner_bottom": t_inner_bottom, "t_inner_deck": t_inner_deck,
                         "t_bulkhead": t_bulkhead, "panel_breadth": panel_breadth})

def make_two_flange_section(B: float = 20.0, D: float = 12.0, t_deck: float = 0.020,
                            t_bottom: float = 0.030, panel_breadth: float = 1.2,
                            name: str = "two-flange girder") -> Section:
    """Deck + bottom plate only, no side shells.

    Exists purely as an analytic benchmark fixture: with a buckled compression
    (deck) flange and a fully yielded tension (bottom) flange the collapse moment
    has a hand-derived closed form (``smith.two_flange_closed_form``), so the
    incremental solver can be judged against it in the *buckling* regime - the
    regime where the rigid-plastic limit no longer applies.
    """
    parts = [
        Part("deck", -B / 2.0, B / 2.0, D - t_deck, D, panel_breadth, t_deck),
        Part("bottom", -B / 2.0, B / 2.0, 0.0, t_bottom, panel_breadth, t_bottom),
    ]
    return Section(name=name, parts=parts, breadth=B, depth=D,
                   meta={"B": B, "D": D, "t_deck": t_deck, "t_bottom": t_bottom,
                         "panel_breadth": panel_breadth})
