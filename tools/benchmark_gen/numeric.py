"""Offset-driven body-form integration — the *production* numeric path.

Design contract
---------------
This module is deliberately **body-agnostic**: a body form is fully described by
seven metric/dimensionless parameters (:class:`BodyForm`), so no particular
vessel, class or fleet name appears in the code.  Any body is injected through
a configuration file (see ``specs/bodies/*.json``), which is what makes the
pipeline reusable across body families and physical domains.

Integrals are evaluated on a *discrete offset grid* (stations x waterlines,
composite Simpson rule), exactly the way a tabulated-offsets hydrostatics
routine would: no separability of the parametric form is exploited, so the
discretisation error measured against :mod:`tools.benchmark_gen.analytic` is a
genuine station/panel error and not an algebraic identity.

Body coordinate frame
---------------------
``x`` longitudinal (origin amidships, bow at ``+L/2``), ``z`` vertical (design
waterline at ``z = 0``, keel at ``z = -form_depth``); transverse half-breadth
comes from :meth:`BodyForm.half_breadth`.  A *condition* is an immersion
``draft`` measured downwards from the design waterline, so the case waterline
sits at ``z_wl = draft - form_depth``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

__all__ = ["BodyForm", "simpson", "hydrostatics", "wetted_surface", "GAUSS2", "GAUSS8"]

# Gauss-Legendre abscissae / weights on [-1, 1] (hard-coded, no numpy dependency).
GAUSS2: Tuple[Tuple[float, float], ...] = (
    (-0.5773502691896257, 1.0),
    (0.5773502691896257, 1.0),
)

# 8-node rule, used where a very high order is wanted (references / convergence).
GAUSS8: Tuple[Tuple[float, float], ...] = (
    (-0.9602898564975363, 0.1012285362903763),
    (-0.7966664774136267, 0.2223810344533745),
    (-0.5255324099163290, 0.3137066458778873),
    (-0.1834346424956498, 0.3626837833783620),
    (0.1834346424956498, 0.3626837833783620),
    (0.5255324099163290, 0.3137066458778873),
    (0.7966664774136267, 0.2223810344533745),
    (0.9602898564975363, 0.1012285362903763),
)


@dataclass(frozen=True)
class BodyForm:
    """Parametric body form (symmetric about the centreplane).

    ``half_breadth(x, z) = beam/2 * f(u) * g(z)`` with ``u = 2|x| / length``::

        f(u) = 1                                        u <= midbody_fraction
             = (1 - v^plan_exponent)^plan_shape         v = (u-m)/(1-m)
        g(z) = 1 - (|z| / form_depth)^section_exponent  (g = 0 at the keel)

    Every parameter is a positive length or a dimensionless exponent >= 1; this
    keeps all integrals finite (integrable, non-singular derivatives), which is
    what makes the closed forms in :mod:`tools.benchmark_gen.analytic` exact.
    """

    length: float
    beam: float
    form_depth: float
    midbody_fraction: float = 0.0
    plan_exponent: float = 2.0
    plan_shape: float = 1.0
    section_exponent: float = 2.0

    def __post_init__(self) -> None:
        for key in ("length", "beam", "form_depth"):
            value = float(getattr(self, key))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"BodyForm.{key} must be a positive finite length, got {value!r}")
        m = float(self.midbody_fraction)
        if not (0.0 <= m < 1.0):
            raise ValueError(f"BodyForm.midbody_fraction must be in [0, 1), got {m!r}")
        for key in ("plan_exponent", "plan_shape", "section_exponent"):
            value = float(getattr(self, key))
            if not math.isfinite(value) or value < 1.0:
                raise ValueError(f"BodyForm.{key} must be finite and >= 1, got {value!r}")

    # -- geometry ---------------------------------------------------------
    def plan_factor(self, u: float) -> float:
        """Longitudinal distribution factor ``f(u)``, ``u = 2|x|/length``."""
        u = abs(u)
        if u >= 1.0:
            return 0.0
        if u <= self.midbody_fraction:
            return 1.0
        v = (u - self.midbody_fraction) / (1.0 - self.midbody_fraction)
        return (1.0 - v ** self.plan_exponent) ** self.plan_shape

    def section_factor(self, z: float) -> float:
        """Vertical (sectional) factor ``g(z)`` on ``[-form_depth, 0]``."""
        if z >= 0.0:
            return 1.0
        r = abs(z) / self.form_depth
        if r >= 1.0:
            return 0.0
        return 1.0 - r ** self.section_exponent

    def half_breadth(self, x: float, z: float) -> float:
        """Transverse half-breadth at station ``x``, height ``z``."""
        return 0.5 * self.beam * self.plan_factor(2.0 * x / self.length) * self.section_factor(z)

    def waterline_z(self, draft: float) -> float:
        """Height of the case waterline for an immersion ``draft``."""
        if not math.isfinite(draft) or draft <= 0.0:
            raise ValueError(f"draft must be a positive finite length, got {draft!r}")
        if draft > self.form_depth * (1.0 + 1e-12):
            raise ValueError(
                f"draft {draft!r} exceeds the body form depth {self.form_depth!r}; "
                "deck immersion is outside this form's validity envelope"
            )
        return draft - self.form_depth

    # -- serialisation ----------------------------------------------------
    def as_dict(self) -> Dict[str, float]:
        return {
            "length": self.length,
            "beam": self.beam,
            "form_depth": self.form_depth,
            "midbody_fraction": self.midbody_fraction,
            "plan_exponent": self.plan_exponent,
            "plan_shape": self.plan_shape,
            "section_exponent": self.section_exponent,
        }

    @classmethod
    def from_spec(cls, spec: Dict[str, object]) -> "BodyForm":
        """Build from a configuration mapping (unknown keys are rejected)."""
        if not isinstance(spec, dict):
            raise TypeError(f"body spec must be a mapping, got {type(spec).__name__}")
        allowed = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        unknown = set(spec) - allowed
        if unknown:
            raise ValueError(f"unknown body-form parameters: {sorted(unknown)}")
        missing = [k for k in ("length", "beam", "form_depth") if k not in spec]
        if missing:
            raise ValueError(f"body spec is missing required parameters: {missing}")
        return cls(**{k: float(v) for k, v in spec.items()})  # type: ignore[arg-type]


def simpson(values: Sequence[float], h: float) -> float:
    """Composite Simpson 1/3 rule on a uniform grid (even number of intervals)."""
    n = len(values) - 1
    if n < 2 or n % 2:
        raise ValueError(f"Simpson rule needs an odd number of samples (>= 3), got {len(values)}")
    odd = sum(values[1:-1:2])
    even = sum(values[2:-1:2])
    return h / 3.0 * (values[0] + values[-1] + 4.0 * odd + 2.0 * even)


def _uniform(a: float, b: float, n: int) -> List[float]:
    """``n`` (odd) uniformly spaced nodes on ``[a, b]``."""
    if n < 3:
        raise ValueError(f"need n >= 3 nodes, got {n}")
    if n % 2 == 0:
        n += 1
    step = (b - a) / (n - 1)
    return [a + i * step for i in range(n)]


def hydrostatics(form: BodyForm, draft: float, n_stations: int = 801,
                 n_waterlines: int = 401) -> Dict[str, float]:
    """Numerically integrated hydrostatics of ``form`` immersed to ``draft``.

    Returns the standard coefficient set (volumes, coefficients, centres,
    metacentric radii).  The grid sizes are reported back so that the dataset is
    self-describing and the discretisation is auditable.
    """
    z_wl = form.waterline_z(draft)
    xs = _uniform(-0.5 * form.length, 0.5 * form.length, n_stations)
    zs = _uniform(-form.form_depth, z_wl, n_waterlines)
    h_x = xs[1] - xs[0]
    h_z = zs[1] - zs[0]

    areas: List[float] = []       # submerged sectional area A(x)
    z_moments: List[float] = []   # \int 2 y z dz  (vertical first moment of dV)
    for x in xs:
        # Integral in z is station-local: emulates a per-station offset table.
        y = [form.half_breadth(x, z) for z in zs]
        areas.append(simpson([2.0 * yi for yi in y], h_z))
        z_moments.append(simpson([2.0 * yi * zi for yi, zi in zip(y, zs)], h_z))

    volume = simpson(areas, h_x)                       # displacement volume
    mz = simpson(z_moments, h_x)                       # \int z dV
    # Longitudinal first moment of volume (zero for a symmetric form).
    mx = simpson([x * a for x, a in zip(xs, areas)], h_x)

    # Waterplane quantities (evaluated exactly on the case waterline).
    yw = [form.half_breadth(x, z_wl) for x in xs]
    awp = simpson([2.0 * yi for yi in yw], h_x)
    it = simpson([(2.0 / 3.0) * yi ** 3 for yi in yw], h_x)      # transverse inertia
    il = simpson([2.0 * yi * x * x for x, yi in zip(xs, yw)], h_x)  # longitudinal inertia
    mcf = simpson([x * 2.0 * yi for x, yi in zip(xs, yw)], h_x)

    z_c = mz / volume
    kb = z_c + form.form_depth            # centre of buoyancy above the keel
    draft = z_wl + form.form_depth        # sanity: geometry of the immersion
    am = form.half_breadth(0.0, z_wl) * 2.0
    # midship section area (submerged) integrated properly (not the waterline value)
    y_mid = [form.half_breadth(0.0, z) for z in zs]
    am = simpson([2.0 * yi for yi in y_mid], h_z)

    lbt = form.length * form.beam * draft
    bmt = it / volume
    bml = il / volume
    return {
        "draft": draft,
        "waterline_z": z_wl,
        "displacement_volume": volume,
        "section_area_midship": am,
        "waterplane_area": awp,
        "cb": volume / lbt,
        "cm": am / (form.beam * draft),
        "cp": volume / (am * form.length),
        "cwp": awp / (form.length * form.beam),
        "cvp": volume / (awp * draft),
        "kb": kb,
        "lcb": mx / volume,
        "lcf": mcf / awp,
        "transverse_inertia": it,
        "longitudinal_inertia": il,
        "bmt": bmt,
        "bml": bml,
        "kmt": kb + bmt,
        "grid": {"n_stations": len(xs), "n_waterlines": len(zs),
                 "h_station": h_x, "h_waterline": h_z},
    }


def wetted_surface(form: BodyForm, draft: float, n_x: int = 200,
                   n_z: int = 100) -> float:
    """Wetted (submerged) surface area by cell-wise 2x2 Gauss quadrature.

    Integrand ``2 * sqrt(1 + y_x^2 + y_z^2)``; the factor 2 accounts for the
    port/starboard pair (the form is symmetric about the centreplane, so only
    ``x >= 0`` is swept and doubled).
    """
    z_wl = form.waterline_z(draft)
    half = 0.5 * form.length

    def derivatives(x: float, z: float) -> Tuple[float, float]:
        """``(y_x, y_z)`` of the half-breadth surface at ``(x, z)``."""
        u = 2.0 * abs(x) / form.length
        f = form.plan_factor(u)
        g = form.section_factor(z)
        # df/du (piecewise; f is flat inside the parallel mid-body)
        if u >= 1.0 or u <= form.midbody_fraction:
            df_du = 0.0
        else:
            v = (u - form.midbody_fraction) / (1.0 - form.midbody_fraction)
            base = 1.0 - v ** form.plan_exponent
            df_du = (-form.plan_exponent * form.plan_shape * v ** (form.plan_exponent - 1.0)
                     * (base ** (form.plan_shape - 1.0)) / (1.0 - form.midbody_fraction))
        # dg/dz
        if z >= 0.0 or abs(z) >= form.form_depth:
            dg_dz = 0.0
        else:
            dg_dz = (form.section_exponent
                     * (abs(z) / form.form_depth) ** (form.section_exponent - 1.0)
                     / form.form_depth)
        y_x = 0.5 * form.beam * (df_du * 2.0 / form.length) * g
        y_z = 0.5 * form.beam * f * dg_dz
        return y_x, y_z

    total = 0.0
    dx = half / n_x
    dz = (z_wl + form.form_depth) / n_z
    for i in range(n_x):
        x0 = 0.0 + i * dx
        for j in range(n_z):
            z0 = -form.form_depth + j * dz
            cell = 0.0
            for xi, wi in GAUSS2:
                x = x0 + 0.5 * dx * (1.0 + xi)
                for zj, wj in GAUSS2:
                    z = z0 + 0.5 * dz * (1.0 + zj)
                    y_x, y_z = derivatives(x, z)
                    cell += wi * wj * math.sqrt(1.0 + y_x * y_x + y_z * y_z)
            total += 0.25 * dx * dz * cell
    return 2.0 * total