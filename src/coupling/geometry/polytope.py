"""Exact mass properties of convex polytopes (volume / centroid / inertia tensor).

This is the geometric kernel behind the cross-solver transfer ledgers.  Every
ledger residual in :mod:`src.coupling.fem_to_dem_exact` and
:mod:`src.coupling.lbm_to_fvm_conservative_remap` is a statement about
``integral`` quantities, so the integrals have to be evaluated exactly rather
than on a surrogate (e.g. an equivalent sphere).  A convex polytope is
represented by its hull vertices together with its half-space description
``n.x <= d``; integrals are evaluated by decomposing the polytope into
tetrahedra and using the closed-form tetrahedron moments::

    ∫_T x dV            = V (a+b+c+d)/4
    ∫_T x x^T dV        = V/20 (Σ_k v_k v_k^T + s s^T),  s = Σ_k v_k
    I_ab                = ρ ∫ (|x-c|² δ_ab - x_a x_b) dV

which are exact for a tetrahedron, hence exact (to round-off) for the polytope.
No domain semantics: the module knows only geometry and density.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import linprog
from scipy.spatial import ConvexHull, HalfspaceIntersection, QhullError

# Relative volume below which a clipped cell is treated as degenerate (empty).
VOLUME_TOL = 1e-12


def _normalise_equations(equations: np.ndarray) -> np.ndarray:
    """Scale rows so that the plane normal is a unit vector."""
    eq = np.asarray(equations, dtype=float)
    normals = np.linalg.norm(eq[:, :3], axis=1)
    normals[normals < 1e-300] = 1.0
    return eq / normals[:, None]


def _chebyshev_centre(equations: np.ndarray) -> Optional[np.ndarray]:
    """Interior point of ``{x : A x <= b}`` via the Chebyshev centre LP."""
    eq = _normalise_equations(equations)
    A, b = eq[:, :3], eq[:, 3]
    # maximise r  s.t.  A x + r |A_i| <= b_i  (|A_i| = 1 after normalisation)
    c = np.array([0.0, 0.0, 0.0, -1.0])
    A_ub = np.hstack([A, np.ones((A.shape[0], 1))])
    bounds = [(None, None)] * 3 + [(0.0, None)]
    res = linprog(c, A_ub=A_ub, b_ub=b, bounds=bounds, method="highs")
    if not res.success or res.x is None:
        return None
    return np.asarray(res.x[:3], dtype=float)


@dataclass
class Polytope:
    """A convex polytope with cached exact mass properties."""

    vertices: np.ndarray          # (M, 3) hull vertices
    equations: np.ndarray         # (K, 4) half-spaces n.x <= d
    volume: float
    centroid: np.ndarray          # (3,)
    _second_moment: np.ndarray    # (3, 3) ∫ x x^T dV about the origin
    _first_moment: np.ndarray     # (3,)   ∫ x dV about the origin
    simplicies: np.ndarray        # (T, 3) outward oriented hull triangles
    bbox_lo: Optional[np.ndarray] = None
    bbox_hi: Optional[np.ndarray] = None
    is_axis_box: bool = False

    # ------------------------------------------------------------------ #
    def inertia(self, density: float) -> np.ndarray:
        """Inertia tensor about the centroid: ρ ∫ (|r|²I - r r^T) dV, r = x-c."""
        c = self.centroid
        m2 = self._second_moment - self.volume * np.outer(c, c)
        return density * (np.trace(m2) * np.eye(3) - m2)

    def mass(self, density: float) -> float:
        return density * self.volume

    def mass_properties(self, density: float) -> dict:
        return {
            "volume": float(self.volume),
            "mass": float(self.mass(density)),
            "centroid": self.centroid.copy(),
            "inertia": self.inertia(density),
        }

    def translated(self, shift: Sequence[float]) -> "Polytope":
        shift = np.asarray(shift, dtype=float)
        return polytope_from_halfspaces(
            self.equations.copy(),
            np.asarray(self.vertices, dtype=float) + shift,
        )


# ---------------------------------------------------------------------- #
def _moments_from_vertices(vertices: np.ndarray,
                           simplices: np.ndarray) -> Tuple[float, np.ndarray, np.ndarray]:
    """Volume, first moment and second moment of a star-shaped decomposition."""
    interior = vertices.mean(axis=0)
    vol = 0.0
    first = np.zeros(3)
    second = np.zeros((3, 3))
    for tri in simplices:
        a, b, c = vertices[tri[0]], vertices[tri[1]], vertices[tri[2]]
        # Signed volume of (interior, a, b, c); hull triangles are outward
        # oriented so a positive value means the interior point is behind them.
        v6 = np.dot(a - interior, np.cross(b - interior, c - interior))
        tet_v = v6 / 6.0
        if tet_v < 0.0:
            # Numerical orientation flip: use the absolute measure.
            tet_v = -tet_v
        if tet_v <= 0.0:
            continue
        corners = np.vstack([interior, a, b, c])
        s = corners.sum(axis=0)
        vol += tet_v
        first += tet_v * s / 4.0
        second += (tet_v / 20.0) * (corners.T @ corners + np.outer(s, s))
    return vol, first, second


def polytope_from_halfspaces(equations: np.ndarray,
                             fallback_vertices: Optional[np.ndarray] = None) -> Optional[Polytope]:
    """Build a polytope from half-spaces ``n.x <= d`` (``None`` if empty)."""
    eq = _normalise_equations(np.atleast_2d(equations))
    # De-duplicate coplanar half-spaces (concatenating two polytopes repeats them).
    eq = np.unique(np.round(eq, 12), axis=0)
    interior = _chebyshev_centre(eq)
    if interior is None:
        return None
    # scipy's convention is A.x + b <= 0, ours is A.x <= d.
    qh_equations = np.hstack([eq[:, :3], -eq[:, 3:4]])
    try:
        hs = HalfspaceIntersection(qh_equations, interior)
    except (QhullError, ValueError):
        return None
    pts = np.asarray(hs.intersections, dtype=float)
    if pts.shape[0] < 4:
        return None
    # De-duplicate (Qhull can emit repeated points at degenerate vertices).
    pts = np.unique(np.round(pts, 12), axis=0)
    if pts.shape[0] < 4:
        return None
    try:
        hull = ConvexHull(pts)
    except QhullError:
        return None
    verts = pts[hull.vertices]
    volume, first, second = _moments_from_vertices(pts, hull.simplices)
    if not np.isfinite(volume) or volume <= VOLUME_TOL * max(1.0, float(np.abs(pts).max()) ** 3):
        return None
    return Polytope(
        vertices=verts,
        equations=eq,
        volume=float(volume),
        centroid=first / volume,
        _second_moment=second,
        _first_moment=first,
        simplicies=np.asarray(hull.simplices, dtype=int),
        bbox_lo=pts.min(axis=0),
        bbox_hi=pts.max(axis=0),
        is_axis_box=False,
    )


def polytope_from_vertices(vertices: np.ndarray) -> Optional[Polytope]:
    """Build a polytope from its hull vertices."""
    pts = np.asarray(vertices, dtype=float).reshape(-1, 3)
    try:
        hull = ConvexHull(pts)
    except QhullError:
        return None
    # hull.equations are outward normals with n.x + d <= 0  ->  n.x <= -d
    eq = np.hstack([hull.equations[:, :3], -hull.equations[:, 3:4]])
    return polytope_from_halfspaces(eq, fallback_vertices=pts)


def box(center: Sequence[float], half_extents: Sequence[float]) -> Optional[Polytope]:
    """Axis-aligned box polytope with exact analytic mass properties.

    The closed form keeps the lattice/finite-volume transfer path free of
    Qhull calls, which matters because the supermesh loops over every
    cell pair.
    """
    c = np.asarray(center, dtype=float).reshape(3)
    h = np.asarray(half_extents, dtype=float).reshape(3)
    if np.any(h <= 0.0):
        return None
    signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], dtype=float)
    verts = c + signs * h
    eq = np.array([
        [1.0, 0.0, 0.0, c[0] + h[0]],
        [-1.0, 0.0, 0.0, -(c[0] - h[0])],
        [0.0, 1.0, 0.0, c[1] + h[1]],
        [0.0, -1.0, 0.0, -(c[1] - h[1])],
        [0.0, 0.0, 1.0, c[2] + h[2]],
        [0.0, 0.0, -1.0, -(c[2] - h[2])],
    ])
    volume = float(8.0 * h[0] * h[1] * h[2])
    second = volume * (np.outer(c, c) + np.diag(h ** 2) / 3.0)
    return Polytope(
        vertices=verts,
        equations=eq,
        volume=volume,
        centroid=c.copy(),
        _second_moment=second,
        _first_moment=volume * c,
        simplicies=np.zeros((0, 3), dtype=int),
        bbox_lo=c - h,
        bbox_hi=c + h,
        is_axis_box=True,
    )


def clip(poly: Optional[Polytope], equations: np.ndarray) -> Optional[Polytope]:
    """Intersect ``poly`` with the half-spaces ``n.x <= d``."""
    if poly is None:
        return None
    eq = np.vstack([poly.equations, _normalise_equations(np.atleast_2d(equations))])
    return polytope_from_halfspaces(eq)


def split(poly: Polytope, point: Sequence[float],
          normal: Sequence[float]) -> Tuple[Optional[Polytope], Optional[Polytope]]:
    """Split ``poly`` by the plane through ``point`` with ``normal``.

    Returns ``(positive_side, negative_side)`` where the positive side is
    ``n.(x-point) >= 0``.  Either side may be ``None`` when degenerate.
    """
    n = np.asarray(normal, dtype=float)
    n = n / max(np.linalg.norm(n), 1e-300)
    d = float(np.dot(n, np.asarray(point, dtype=float)))
    pos = clip(poly, np.array([[-n[0], -n[1], -n[2], -d]]))
    neg = clip(poly, np.array([[n[0], n[1], n[2], d]]))
    return pos, neg


def intersect(a: Optional[Polytope], b: Optional[Polytope]) -> Optional[Polytope]:
    """Intersection volume cell of two convex polytopes."""
    if a is None or b is None:
        return None
    if a.is_axis_box and b.is_axis_box:
        lo = np.maximum(a.bbox_lo, b.bbox_lo)
        hi = np.minimum(a.bbox_hi, b.bbox_hi)
        scale = max(1.0, float(np.abs(a.bbox_hi).max()), float(np.abs(b.bbox_hi).max()))
        if np.any(hi - lo <= 1e-12 * scale):
            return None
        return box(0.5 * (lo + hi), 0.5 * (hi - lo))
    return polytope_from_halfspaces(np.vstack([a.equations, b.equations]))


def hexahedron(vertices: Sequence[Sequence[float]]) -> Optional[Polytope]:
    """Generic convex hexahedron from 8 corner coordinates."""
    return polytope_from_vertices(np.asarray(vertices, dtype=float))


__all__ = [
    "Polytope",
    "box",
    "clip",
    "hexahedron",
    "intersect",
    "polytope_from_halfspaces",
    "polytope_from_vertices",
    "split",
]