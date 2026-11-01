"""Ship hydrostatics from hull offset tables (waterplane / sectional integration).

Why this module exists
----------------------
The platform's stability endpoint (``POST /calculations/stability``) accepts
``GM`` and ``BM`` as *given* parameters.  Nothing in the platform derives
hydrostatic quantities (displacement volume, block coefficient, waterplane
area, KB, BM) from the hull form itself, so the first half of the 稳性
calculation chain (geometry -> hydrostatics -> GM -> righting arm) was a gap.

This module closes that gap with a pure-Python/numpy integrator over a hull
offset table (stations x waterlines of half-breadths) and ships three
machine-checkable analytic anchors:

Box (rectangular barge) hull -- textbook formulas, ``y = B/2`` for ``z < T``::

    nabla = B*L*T            Cb = 1
    Aw    = B*L              KB = T/2
    BM_T  = B^2/(12*T)       BM_L = L^2/(12*T)

Wigley parabolic hull (classic form, full beam at the design waterline and a
point section at the keel) -- ``y(x,z) = (B/2)(1-(2x/L)^2)(1-(1-z/T)^2)``::

    nabla = 4*B*L*T/9        Cb   = 4/9           (exact, independent of L/B/T)
    Aw    = 2*B*L/3          LCF  = 0             (about midship)
    KB    = 5*T/8            Cm   = 2/3
    BM_T  = 3*B^2/(35*T)     BM_L = 3*L^2/(40*T)

The Wigley closed forms are obtained analytically (see
``docs/ship_hydrostatics_verification.md`` section 3) and are used as
regression anchors by ``backend/tests/test_ship_hydrostatics.py``.

Conventions
-----------
* ``stations`` are measured along the hull from the aft perpendicular (x=0 at
  AP, increasing towards FP); no assumption of symmetry is made.
* ``waterlines`` are measured vertically from the keel (z=0 at keel).
* ``half_breadths[i][j]`` is the half-breadth of station ``i`` at waterline
  ``j`` (metres, >= 0).  The top waterline is the design waterline.
* LCB / LCF are reported with sign: positive forward of midships.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

__all__ = [
    "GeometryError",
    "HullOffsets",
    "HydrostaticResult",
    "compute_hydrostatics",
    "wigley_offsets",
    "barge_offsets",
    "simpson_integrate",
]

# Physical gate: block coefficients outside this window are not ships.
CB_MIN = 0.15
CB_MAX = 1.0 + 1e-9


class GeometryError(ValueError):
    """Raised when the supplied hull geometry is not physically admissible."""


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HullOffsets:
    """Discrete hull form: stations x waterlines half-breadth table."""

    length: float
    stations: Sequence[float]
    waterlines: Sequence[float]
    half_breadths: Sequence[Sequence[float]]

    def validate(self) -> None:
        if not (self.length > 0):
            raise GeometryError(f"length must be positive, got {self.length}")
        if len(self.stations) < 2:
            raise GeometryError("need at least 2 stations")
        if len(self.waterlines) < 2:
            raise GeometryError("need at least 2 waterlines")
        for s in self.stations:
            if not (-1e-9 <= s <= self.length + 1e-9):
                raise GeometryError(
                    f"station x={s} outside hull extent [0, {self.length}]"
                )
        _require_increasing(self.stations, "stations")
        _require_increasing(self.waterlines, "waterlines")
        if abs(self.waterlines[0]) > 1e-9:
            raise GeometryError(
                f"first waterline must be the keel (z=0), got {self.waterlines[0]}"
            )
        if len(self.half_breadths) != len(self.stations):
            raise GeometryError(
                f"half_breadths has {len(self.half_breadths)} rows, "
                f"expected {len(self.stations)} (one per station)"
            )
        for i, row in enumerate(self.half_breadths):
            if len(row) != len(self.waterlines):
                raise GeometryError(
                    f"station {i} has {len(row)} half-breadths, "
                    f"expected {len(self.waterlines)} (one per waterline)"
                )
            for v in row:
                if v < -1e-12:
                    raise GeometryError(
                        f"station {i} has negative half-breadth {v}"
                    )


@dataclass
class HydrostaticResult:
    """Hydrostatic quantities derived from the hull offset table."""

    volume_m3: float
    displacement_t: float
    cb: float
    cm: float
    cp: float
    cw: float
    waterplane_area_m2: float
    lcb_m: float
    lcf_m: float
    kb_m: float
    bm_t_m: float
    bm_l_m: float
    km_t_m: float
    gm_m: Optional[float]
    length_m: float
    beam_m: float
    draft_m: float
    kg_m: Optional[float]
    wetted_surface_m2: float
    n_stations: int
    n_waterlines: int
    integration: str
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "volume_m3": self.volume_m3,
            "displacement_t": self.displacement_t,
            "cb": self.cb,
            "cm": self.cm,
            "cp": self.cp,
            "cw": self.cw,
            "waterplane_area_m2": self.waterplane_area_m2,
            "lcb_m": self.lcb_m,
            "lcf_m": self.lcf_m,
            "kb_m": self.kb_m,
            "bm_t_m": self.bm_t_m,
            "bm_l_m": self.bm_l_m,
            "km_t_m": self.km_t_m,
            "gm_m": self.gm_m,
            "length_m": self.length_m,
            "beam_m": self.beam_m,
            "draft_m": self.draft_m,
            "kg_m": self.kg_m,
            "wetted_surface_m2": self.wetted_surface_m2,
            "n_stations": self.n_stations,
            "n_waterlines": self.n_waterlines,
            "integration": self.integration,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_increasing(values: Sequence[float], name: str) -> None:
    for a, b in zip(values, values[1:]):
        if not (b > a):
            raise GeometryError(
                f"{name} must be strictly increasing, got {a} then {b}"
            )


def _is_uniform(x: Sequence[float], rtol: float = 1e-9) -> bool:
    if len(x) < 3:
        return True
    h = x[1] - x[0]
    if h <= 0:
        return False
    scale = max(abs(x[-1] - x[0]), 1.0)
    return all(abs((b - a) - h) <= rtol * scale for a, b in zip(x, x[1:]))


def simpson_integrate(y: Sequence[float], x: Sequence[float]) -> Tuple[float, str]:
    """Integrate ``y`` over ``x``.

    Composite Simpson on uniform grids with an even number of intervals,
    trapezoidal otherwise.  Returns ``(value, method)`` so callers can report
    which rule produced the numbers.
    """
    n = len(x)
    if n != len(y):
        raise GeometryError("x and y lengths differ")
    if n < 2:
        raise GeometryError("need at least 2 samples to integrate")

    if n >= 3 and (n - 1) % 2 == 0 and _is_uniform(x):
        h = x[1] - x[0]
        total = y[0] + y[-1]
        total += 4.0 * math.fsum(y[1:-1:2])
        total += 2.0 * math.fsum(y[2:-1:2])
        return total * h / 3.0, "simpson"

    total = 0.0
    for i in range(n - 1):
        total += 0.5 * (y[i] + y[i + 1]) * (x[i + 1] - x[i])
    return total, "trapezoid"


# ---------------------------------------------------------------------------
# Hull generators used for verification / smoke cases
# ---------------------------------------------------------------------------

def wigley_offsets(
    length: float = 6.0,
    beam: float = 0.6,
    draft: float = 0.375,
    n_stations: int = 81,
    n_waterlines: int = 41,
) -> HullOffsets:
    """Wigley parabolic hull: ``y = (B/2)(1-(2x/L)^2)(1-(1-z/T)^2)``.

    ``beam`` is the beam at the design waterline (z = draft); the section
    collapses to a point at the keel, as in the classical Wigley test hull.
    """
    if n_stations < 3 or n_waterlines < 3:
        raise GeometryError("wigley_offsets needs >= 3 stations and waterlines")
    stations = [length * i / (n_stations - 1) for i in range(n_stations)]
    waterlines = [draft * j / (n_waterlines - 1) for j in range(n_waterlines)]
    rows: List[List[float]] = []
    for xs in stations:
        u = 2.0 * (xs - 0.5 * length) / length
        along = max(0.0, 1.0 - u * u)
        row = []
        for z in waterlines:
            d = 1.0 - z / draft                      # depth below design WL
            row.append(0.5 * beam * along * max(0.0, 1.0 - d * d))
        rows.append(row)
    return HullOffsets(length, stations, waterlines, rows)


def barge_offsets(
    length: float = 100.0,
    beam: float = 20.0,
    draft: float = 8.0,
    n_stations: int = 41,
    n_waterlines: int = 21,
) -> HullOffsets:
    """Rectangular barge (box hull): constant half-breadth ``B/2``."""
    stations = [length * i / (n_stations - 1) for i in range(n_stations)]
    waterlines = [draft * j / (n_waterlines - 1) for j in range(n_waterlines)]
    rows = [[0.5 * beam for _ in waterlines] for _ in stations]
    return HullOffsets(length, stations, waterlines, rows)


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

def compute_hydrostatics(
    offsets: HullOffsets,
    *,
    kg: Optional[float] = None,
    rho: float = 1025.0,
    cb_reference: Optional[float] = None,
    cb_tolerance: float = 1e-2,
) -> HydrostaticResult:
    """Hydrostatics of a hull given its offset table.

    Parameters
    ----------
    offsets:
        Hull offset table (see :class:`HullOffsets`).
    kg:
        Vertical centre of gravity above keel (m).  When given, ``GM`` is
        returned as ``KB + BM_T - KG``.
    rho:
        Water density (kg/m^3) used only for the reported displacement.
    cb_reference:
        Optional block coefficient from the design table.  The geometry gate
        required by the platform precision standard compares it with the
        value integrated from the offsets and raises :class:`GeometryError`
        on a relative mismatch above ``cb_tolerance``.
    """
    offsets.validate()

    length = float(offsets.length)
    stations = [float(v) for v in offsets.stations]
    waterlines = [float(v) for v in offsets.waterlines]
    y = [[float(v) for v in row] for row in offsets.half_breadths]

    draft = waterlines[-1]
    beam = 2.0 * max(max(row) for row in y)
    if beam <= 0 or draft <= 0:
        raise GeometryError("degenerate hull: zero beam or draft")

    notes: List[str] = []
    methods = set()

    # --- sectional areas A(x) = 2*int_0^T y dz -----------------------------
    sectional: List[float] = []
    for row in y:
        val, method = simpson_integrate(row, waterlines)
        methods.add(method)
        sectional.append(2.0 * val)

    volume, method = simpson_integrate(sectional, stations)
    methods.add(method)
    if volume <= 0:
        raise GeometryError(f"integrated volume is non-positive ({volume})")
    volume = abs(volume)

    cb = volume / (length * beam * draft)
    if not (CB_MIN <= cb <= CB_MAX):
        raise GeometryError(
            f"block coefficient integrated from offsets is {cb:.4f}, "
            f"outside the physical window [{CB_MIN}, {CB_MAX}] "
            "(check units of the half-breadths / waterlines)"
        )

    # --- waterplane quantities (top waterline = design waterline) ----------
    y_wl = [row[-1] for row in y]
    half_area = [v for v in y_wl]                 # half-breadth distribution
    aw_half, method = simpson_integrate(half_area, stations)
    methods.add(method)
    aw = 2.0 * aw_half

    moment_half, method = simpson_integrate(
        [a * b for a, b in zip(half_area, stations)], stations
    )
    methods.add(method)
    x_centroid = moment_half / aw_half if aw_half > 0 else 0.0

    i_t_half, method = simpson_integrate(
        [a ** 3 for a in half_area], stations
    )
    methods.add(method)
    i_t = (2.0 / 3.0) * i_t_half

    dx = [s - x_centroid for s in stations]
    i_l_half, method = simpson_integrate(
        [a * d * d for a, d in zip(half_area, dx)], stations
    )
    methods.add(method)
    i_l = 2.0 * i_l_half

    # --- vertical distribution: waterplane area at each waterline ----------
    aw_z: List[float] = []
    for j in range(len(waterlines)):
        val, method = simpson_integrate([row[j] for row in y], stations)
        methods.add(method)
        aw_z.append(2.0 * val)
    kb_moment, method = simpson_integrate(
        [a * z for a, z in zip(aw_z, waterlines)], waterlines
    )
    methods.add(method)
    kb = kb_moment / volume

    # --- midship section coefficient / prismatic ---------------------------
    mid_idx = min(
        range(len(stations)), key=lambda i: abs(stations[i] - 0.5 * length)
    )
    am = sectional[mid_idx]
    cm = am / (beam * draft) if beam * draft > 0 else float("nan")
    cp = cb / cm if cm > 0 else float("nan")
    cw = aw / (length * beam)

    # --- wetted surface: per-station girth length x station spacing -------
    wetted = _wetted_surface(y, stations, waterlines, beam)

    bm_t = i_t / volume
    bm_l = i_l / volume
    km_t = kb + bm_t
    gm = (km_t - kg) if kg is not None else None

    lcb = _lcb_from_sections(sectional, stations, volume) - 0.5 * length
    lcf = x_centroid - 0.5 * length

    if cb_reference is not None:
        if cb_reference <= 0:
            raise GeometryError(f"cb_reference must be positive, got {cb_reference}")
        rel = abs(cb - cb_reference) / cb_reference
        if rel > cb_tolerance:
            raise GeometryError(
                f"geometry gate failed: integrated Cb={cb:.6f} disagrees with "
                f"design Cb={cb_reference:.6f} by {rel * 100:.3f}% "
                f"(tolerance {cb_tolerance * 100:.2f}%)"
            )

    if gm is not None and gm <= 0:
        notes.append(
            "GM <= 0: negative initial stability at the stated KG (honest result, "
            "not an error)"
        )

    return HydrostaticResult(
        volume_m3=volume,
        displacement_t=volume * rho / 1000.0,
        cb=cb,
        cm=cm,
        cp=cp,
        cw=cw,
        waterplane_area_m2=aw,
        lcb_m=lcb,
        lcf_m=lcf,
        kb_m=kb,
        bm_t_m=bm_t,
        bm_l_m=bm_l,
        km_t_m=km_t,
        gm_m=gm,
        length_m=length,
        beam_m=beam,
        draft_m=draft,
        kg_m=kg,
        wetted_surface_m2=wetted,
        n_stations=len(stations),
        n_waterlines=len(waterlines),
        integration="+".join(sorted(methods)),
        notes=notes,
    )


def _lcb_from_sections(
    sectional: Sequence[float], stations: Sequence[float], volume: float
) -> float:
    moment, _ = simpson_integrate(
        [a * s for a, s in zip(sectional, stations)], stations
    )
    return moment / volume


def _wetted_surface(
    y: Sequence[Sequence[float]],
    stations: Sequence[float],
    waterlines: Sequence[float],
    beam: float,
) -> float:
    """Approximate wetted surface by transverse girth length integrated along x.

    The girth of each section (keel to design waterline) is the polyline length
    through the offset points, doubled for port/starboard.  Reproduces the
    analytic barge value ``L*(B+2T)`` to quadrature accuracy, which is what the
    verification tests assert.
    """
    perimeters: List[float] = []
    for row in y:
        # start at the centreline of the keel: adds the flat-of-bottom width
        girth = row[0] if waterlines[0] <= 1e-12 else math.hypot(
            waterlines[0], row[0]
        )
        z_prev, y_prev = waterlines[0], row[0]
        for z, yi in zip(waterlines[1:], row[1:]):
            girth += math.hypot(z - z_prev, yi - y_prev)
            z_prev, y_prev = z, yi
        perimeters.append(2.0 * girth)
    if len(stations) < 2:
        return perimeters[0] * beam if perimeters else 0.0
    value, _ = simpson_integrate(perimeters, stations)
    return value
