"""Moment-preserving conservative remap with an explicit Lagrange correction.

Motivation
----------
A conservative L2 projection of a cell-centred field between two meshes keeps the
0th moment (the global integral) but *not* the first and second moments: the
target value is attached to the target centroid, so

    sum_i V_i f_i x_i   !=   sum_j V_j (P f)_j x_j

because within target cell ``j`` the source field varies linearly.  The leading
error is ``O(h)`` for a donor-cell transfer and ``O(h^2)`` for a locally linear
one -- far above what a centroid-/inertia-sensitive coupling needs (rotational
dynamics, structural loads, free-surface tracking).

Operator
--------
1. **Local linear reconstruction** ``f_hat_i(x) = f_i + g_i . (x - x_i)`` with
   ``g_i`` the distance-weighted least-squares gradient over the ``k`` nearest
   cell centroids.  A spatially linear field is reproduced exactly on any
   full-rank stencil.
2. **Linearity-preserving limiter**: per-cell scaling ``phi_i in [0,1]`` of
   ``g_i`` so that the reconstruction evaluated at the cell vertices stays inside
   the vertex extent of the *reconstructed* field over the stencil (the cell
   itself included).  For a linear field every stencil member reconstructs the
   same linear function, hence ``phi_i == 1`` and the exactness of the operator
   is untouched; for a front the scaling damps the overshoot.
3. **Base conservative transfer** with *exact* overlap geometry
   (Sutherland-Hodgman clipping of convex 2D cells):
   ``V_j (P f)_j = sum_i (C_ji f_i + M_ji . g_i)`` with
   ``C_ji = int_{C_j cap C_i} 1 dA`` and ``M_ji = int_{C_j cap C_i} (x - x_i) dA``.
   This is exactly the cell average of ``f_hat`` over the target cell.
4. **Exact moment correction** -- the high-order operator of this module.  The
   target array is corrected by ``delta`` solving

       min ||delta||_2   s.t.   sum_j V_j delta_j phi_c(x_j) = b_c ,

   ``phi_c`` being all monomials of degree ``<= moment_order`` in shifted
   coordinates.  Stationarity of ``L = 1/2 ||delta||^2 - lambda.(Phi^T delta - b)``
   with ``Phi[j, c] = V_j phi_c(x_j)`` gives the closed form

       delta = Phi (Phi^T Phi)^{-1} b .

   ``Phi^T Phi`` is only ``n_basis x n_basis`` (6x6 in 2D at second order), so the
   correction is an *exact* solve, not an iteration.  The unweighted minimum-norm
   choice is deliberate: the volume-weighted norm ``sum_j V_j delta_j^2`` would
   concentrate the correction on the smallest cells (``delta_j ~ 1/V_j``) and
   destroy pointwise accuracy, while ``min ||delta||_2`` spreads it as evenly as
   possible in value space.
5. **Bounded active set**: a cell whose correction would leave ``bounds`` is
   frozen at the bound and the remaining constraints are re-solved on the free
   set.  If the bounds make the constraints unreachable, the 0th moment is
   restored by a bounded uniform redistribution (a coupling must never create or
   destroy mass) and the result reports ``constraints_feasible=False`` together
   with ``mass_repaired=True``.  Boundedness and exact high-order moments are in
   general incompatible; the trade-off is *reported*, never hidden.

Moment convention (``moment_reference``)
---------------------------------------
``"array"`` (default) -- the constraint target is the **discrete array moment**
of the source, ``sum_i V_i f_i phi_c(x_i)``.  The invariant is then "the target
array carries exactly the mass, centroid and inertia tensor that the source array
carried", so an auditor recomputing moments from the two arrays sees zero drift.
This is the convention a coupled simulation lives in, because the solvers only
ever see arrays.

``"continuous"`` -- the constraint target is the **exact continuous moment of the
reconstruction**, ``sum_i int_{C_i} f_hat_i phi_c dA`` (polygon monomial
integrals, exact to round-off).  The target array's moments then equal the true
field moments, i.e. the array becomes an unbiased moment estimator; measured
against the source *array* moments the residual is the source array's own
``O(h^2)`` quadrature bias, reported as the ``continuous_*`` diagnostics.

Both are exact solves; the choice only decides which of the two ``O(h^2)``
quantities is driven to zero.  Audit records always carry both numbers, so the
choice cannot hide an error.

Audit quantities returned by :meth:`MomentRemap.apply`: 0th/1st/2nd moment
residuals of the chosen convention, the same residuals against the continuous
reference (quadrature diagnostic), centroid drift in target-cell units, the
second central moment (inertia) tensor residual, bounds violations, correction
magnitude and the coverage defect of the exchange.

Scope / limitations (fail-closed)
---------------------------------
* 2D only: exact polygonal overlap with first moments is implemented for convex
  polygons; 3D raises :class:`MomentRemapError` instead of silently degrading.
* The exchanged meshes must cover the same domain; the coverage defect is audited
  on *both* sides and raises above ``coverage_tol``.
* The moment correction *repairs the array*; it is not an interpolation.  With
  the correction enabled the field deviates from the exact cell average of a
  linear field by the (small) correction -- ~1e-3 of the amplitude for a linear
  field between an 8x8 source and a perturbed 8x8 target.  That deviation is the
  price of zero drift and is reported as ``correction_rel``.

Zero domain semantics: meshes, arrays and volume weights only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .mesh import Mesh, MeshError, polygon_area
from .remap import _clip_polygon

__all__ = [
    "MomentRemapError",
    "MomentRemapResult",
    "MomentRemap",
    "OverlapGeometry",
    "overlap_geometry_2d",
    "polygon_centroid",
    "polygon_monomial_moments",
    "rectilinear_mesh_2d",
    "perturbed_mesh_2d",
    "basis_monomials",
]


class MomentRemapError(ValueError):
    """Invalid remap configuration or input (fail-closed)."""


# ---------------------------------------------------------------------------
# Mesh factories used by tests / the acceptance runner (no domain semantics)
# ---------------------------------------------------------------------------

def rectilinear_mesh_2d(xs: Sequence[float], ys: Sequence[float]) -> Mesh:
    """Tensor-product quad mesh from (possibly non-uniform) breakpoints."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    if xs.size < 2 or ys.size < 2:
        raise MomentRemapError("need at least 2 breakpoints per direction")
    if np.any(np.diff(xs) <= 0.0) or np.any(np.diff(ys) <= 0.0):
        raise MomentRemapError("breakpoints must be strictly increasing")
    cells, polys, vols = [], [], []
    for j in range(ys.size - 1):
        for i in range(xs.size - 1):
            x0, x1, y0, y1 = xs[i], xs[i + 1], ys[j], ys[j + 1]
            polys.append(np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float))
            cells.append((0.5 * (x0 + x1), 0.5 * (y0 + y1)))
            vols.append((x1 - x0) * (y1 - y0))
    return Mesh(dim=2, cells=np.array(cells), volumes=np.array(vols), polygons=polys)


def perturbed_mesh_2d(
    nx: int, ny: int, *, jitter: float = 0.2, seed: int = 0,
    x0: float = 0.0, x1: float = 1.0, y0: float = 0.0, y1: float = 1.0,
) -> Mesh:
    """Uniform quad mesh with interior nodes randomly displaced.

    Boundary nodes stay on the box, so the mesh covers exactly the same domain as
    the rectilinear mesh it is paired with (coverage defect zero), while the
    interior cells become non-orthogonal and their centroids are not aligned with
    any uniform grid.  ``jitter`` is relative to the uniform spacing and is kept
    small enough for every cell to stay convex (:class:`Mesh` validates this).
    """
    if nx < 1 or ny < 1:
        raise MomentRemapError("nx, ny must be >= 1")
    rng = np.random.default_rng(seed)
    gx = np.linspace(x0, x1, nx + 1)
    gy = np.linspace(y0, y1, ny + 1)
    nodes = np.array([[x, y] for y in gy for x in gx], dtype=float)
    dx, dy = (x1 - x0) / nx, (y1 - y0) / ny
    for jy in range(1, ny):
        for jx in range(1, nx):
            k = jy * (nx + 1) + jx
            nodes[k, 0] += jitter * dx * (rng.random() - 0.5)
            nodes[k, 1] += jitter * dy * (rng.random() - 0.5)
    cells, polys, vols = [], [], []
    for j in range(ny):
        for i in range(nx):
            k00 = j * (nx + 1) + i
            quad = np.array([
                nodes[k00], nodes[k00 + 1],
                nodes[k00 + 1 + (nx + 1)], nodes[k00 + (nx + 1)],
            ], dtype=float)
            polys.append(quad)
            cells.append(polygon_centroid(quad))
            vols.append(abs(polygon_area(quad)))
    return Mesh(dim=2, cells=np.array(cells), volumes=np.array(vols), polygons=polys)


# ---------------------------------------------------------------------------
# Exact polygon geometry
# ---------------------------------------------------------------------------

def polygon_centroid(poly: np.ndarray) -> np.ndarray:
    """Area centroid of a CCW simple polygon."""
    p = np.asarray(poly, dtype=float)
    x, y = p[:, 0], p[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y1 - x1 * y
    a2 = float(cross.sum())
    if abs(a2) < 1e-300:
        return p.mean(axis=0)
    cx = float(((x + x1) * cross).sum() / (3.0 * a2))
    cy = float(((y + y1) * cross).sum() / (3.0 * a2))
    return np.array([cx, cy])


def _triangle_moment(p0, p1, p2, a: int, b: int) -> float:
    """Exact ``int x^a y^b dA`` over a triangle (simplex/barycentric formula)."""
    area2 = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])
    area = 0.5 * abs(area2)
    if area <= 0.0:
        return 0.0
    n = a + b
    denom = math.factorial(n + 2)
    total = 0.0
    for i in range(a + 1):
        for j in range(a - i + 1):
            k = a - i - j
            ca = (
                math.comb(a, i) * math.comb(a - i, j)
                * p0[0] ** i * p1[0] ** j * p2[0] ** k
            )
            if ca == 0.0:
                continue
            for l in range(b + 1):
                for m in range(b - l + 1):
                    nn = b - l - m
                    cb = (
                        math.comb(b, l) * math.comb(b - l, m)
                        * p0[1] ** l * p1[1] ** m * p2[1] ** nn
                    )
                    if cb == 0.0:
                        continue
                    alpha, beta, gamma = i + l, j + m, k + nn
                    coef = (
                        math.factorial(alpha) * math.factorial(beta)
                        * math.factorial(gamma)
                    )
                    total += ca * cb * 2.0 * area * coef / denom
    return total


def polygon_monomial_moments(
    poly: np.ndarray, max_degree: int
) -> Dict[Tuple[int, int], float]:
    """Exact ``int x^a y^b dA`` for all ``a + b <= max_degree`` on a convex polygon."""
    p = np.asarray(poly, dtype=float)
    out: Dict[Tuple[int, int], float] = {}
    v0 = p[0]
    for a in range(max_degree + 1):
        for b in range(max_degree + 1 - a):
            acc = 0.0
            for k in range(1, len(p) - 1):
                acc += _triangle_moment(v0, p[k], p[k + 1], a, b)
            out[(a, b)] = acc
    return out


@dataclass(frozen=True)
class OverlapGeometry:
    """Exact pairwise overlap geometry between two 2D meshes.

    ``C[j, i] = int_{C_j cap C_i} 1 dA`` and ``M1[j, i, :]`` its first moment
    about the *source* centroid.  ``coverage_error`` is the worst relative
    coverage defect over both sides (should be ~1e-15 for matching domains).
    """

    C: np.ndarray
    M1: np.ndarray
    coverage_error: float
    n_pairs: int

    @property
    def tgt_area(self) -> np.ndarray:
        return self.C.sum(axis=1)

    @property
    def src_area(self) -> np.ndarray:
        return self.C.sum(axis=0)


def overlap_geometry_2d(src: Mesh, tgt: Mesh) -> OverlapGeometry:
    """Exact overlap areas and first moments (2D convex polygon clipping)."""
    if src.dim != 2 or tgt.dim != 2:
        raise MomentRemapError("exact overlap geometry is implemented for 2D only")
    if src.polygons is None or tgt.polygons is None:
        raise MomentRemapError("2D exact path requires polygon loops on both meshes")
    C = np.zeros((tgt.n_cells, src.n_cells))
    M1 = np.zeros((tgt.n_cells, src.n_cells, 2))
    n_pairs = 0
    for j, pj in enumerate(tgt.polygons):
        pj = np.asarray(pj, dtype=float)
        jlo, jhi = pj.min(axis=0), pj.max(axis=0)
        for i, pi in enumerate(src.polygons):
            pi = np.asarray(pi, dtype=float)
            ilo, ihi = pi.min(axis=0), pi.max(axis=0)
            if np.any(jhi <= ilo) or np.any(ihi <= jlo):
                continue
            inter = _clip_polygon(pi, pj)
            if inter.shape[0] < 3:
                continue
            a = abs(polygon_area(inter))
            if a <= 0.0:
                continue
            C[j, i] = a
            M1[j, i, :] = a * (polygon_centroid(inter) - src.cells[i])
            n_pairs += 1
    src_sum = C.sum(axis=0)
    tgt_sum = C.sum(axis=1)
    src_def = float(np.max(np.abs(src_sum - src.volumes) / np.maximum(src.volumes, 1e-300)))
    tgt_def = float(np.max(np.abs(tgt_sum - tgt.volumes) / np.maximum(tgt.volumes, 1e-300)))
    return OverlapGeometry(
        C=C, M1=M1, coverage_error=max(src_def, tgt_def), n_pairs=n_pairs
    )


# ---------------------------------------------------------------------------
# Moment basis
# ---------------------------------------------------------------------------

def basis_monomials(order: int) -> List[Tuple[int, int]]:
    """Monomial exponents ``(a, b)`` with ``a + b <= order`` (2D, degree ordered).

    Order 2 in 2D: ``[(0,0), (1,0), (0,1), (2,0), (1,1), (0,2)]`` i.e.
    ``[1, x, y, x^2, xy, y^2]``.
    """
    if order < 0:
        raise MomentRemapError("moment order must be >= 0")
    exps: List[Tuple[int, int]] = []
    for deg in range(order + 1):
        for a in range(deg, -1, -1):
            exps.append((a, deg - a))
    return exps


def _basis_matrix(xy: np.ndarray, order: int) -> np.ndarray:
    """Evaluate the monomial basis (total degree <= ``order``) at points ``xy``."""
    exps = basis_monomials(order)
    out = np.empty((xy.shape[0], len(exps)))
    for c, (a, b) in enumerate(exps):
        out[:, c] = (xy[:, 0] ** a) * (xy[:, 1] ** b)
    return out


# ---------------------------------------------------------------------------
# Local linear reconstruction
# ---------------------------------------------------------------------------

def _knn_stencils(cells: np.ndarray, k: int) -> List[np.ndarray]:
    """``k`` nearest *other* cells for every cell."""
    n = cells.shape[0]
    k = max(1, min(int(k), n - 1))
    out: List[np.ndarray] = []
    for i in range(n):
        d = np.linalg.norm(cells - cells[i], axis=1)
        d[i] = np.inf
        out.append(np.argsort(d)[:k])
    return out


def linear_reconstruction_gradients(
    mesh: Mesh, field: np.ndarray, *, n_neighbors: int = 6
) -> Tuple[np.ndarray, int]:
    """Distance-weighted least-squares gradients of ``field`` at cell centroids.

    For every cell: ``min sum_k w_k (g . (x_k - x_i) - (f_k - f_i))^2`` with
    ``w_k = 1/|x_k - x_i|^2``.  The model space contains all linear functions, so
    a linear field is reproduced exactly (to round-off) on any full-rank stencil.
    Rank-deficient stencils get ``g = 0`` and are counted in the second return
    value instead of producing a silent NaN.
    """
    f = np.asarray(field, dtype=float)
    if f.shape != (mesh.n_cells,):
        raise MomentRemapError(
            f"field shape {f.shape} != ({mesh.n_cells},) for the given mesh"
        )
    cells = mesh.cells
    grad = np.zeros((mesh.n_cells, 2))
    degenerate = 0
    for i, nb in enumerate(_knn_stencils(cells, n_neighbors)):
        d = cells[nb] - cells[i]
        df = f[nb] - f[i]
        dist2 = np.maximum(np.einsum("ij,ij->i", d, d), 1e-300)
        sw = np.sqrt(1.0 / dist2)
        A = d * sw[:, None]
        sol, *_ = np.linalg.lstsq(A, df * sw, rcond=None)
        if np.linalg.matrix_rank(A, tol=1e-12 * max(float(np.abs(A).max()), 1e-300)) < 2:
            degenerate += 1
            continue
        grad[i] = sol
    return grad, degenerate


def _vertex_extents(
    mesh: Mesh, field: np.ndarray, grad: np.ndarray
) -> Tuple[np.ndarray, np.ndarray]:
    """Min/max of the reconstructed sub-cell field over each cell's vertices."""
    f = np.asarray(field, dtype=float)
    lo = np.empty(mesh.n_cells)
    hi = np.empty(mesh.n_cells)
    for i, poly in enumerate(mesh.polygons):
        verts = np.asarray(poly, dtype=float) - mesh.cells[i]
        rec = f[i] + verts @ grad[i]
        lo[i] = float(np.min(rec))
        hi[i] = float(np.max(rec))
    return lo, hi


def _limit_gradients(
    mesh: Mesh,
    field: np.ndarray,
    grad: np.ndarray,
    stencils: Sequence[np.ndarray],
    mode: str,
) -> Tuple[np.ndarray, float]:
    """Scale reconstruction gradients so sub-cell values stay bounded.

    ``mode="vertex"`` (default) compares the reconstruction at the cell vertices
    with the extent of the **reconstructed** field over the stencil (vertex
    extents, the cell itself included).  This keeps the limiter linearity
    preserving: for a spatially linear field every stencil member reconstructs the
    same linear function, so the extent trivially contains the cell's own
    reconstruction and ``phi == 1`` everywhere -- the limiter never damages the
    exactness of the operator on its reconstruction space.  ``mode="stencil"`` is
    the classical Barth-Jespersen variant with raw cell-value bounds (more
    diffusive, used as a diagnostic baseline).

    Returns ``(limited_grad, fraction_of_cells_limited)``.
    """
    f = np.asarray(field, dtype=float)
    if mode not in ("vertex", "stencil"):
        raise MomentRemapError(f"unknown limiter mode {mode!r}")
    lo_ext, hi_ext = _vertex_extents(mesh, f, grad)
    lim = np.ones(mesh.n_cells)
    for i, poly in enumerate(mesh.polygons):
        nb = np.append(np.asarray(stencils[i], dtype=int), i)
        if mode == "vertex":
            lo, hi = float(lo_ext[nb].min()), float(hi_ext[nb].max())
        else:
            lo, hi = float(f[nb].min()), float(f[nb].max())
        verts = np.asarray(poly, dtype=float) - mesh.cells[i]
        rec = f[i] + verts @ grad[i]
        rmax, rmin = float(np.max(rec)), float(np.min(rec))
        phi = 1.0
        if rmax > hi:
            denom = rmax - f[i]
            if denom > 1e-300:
                phi = min(phi, max((hi - f[i]) / denom, 0.0))
            else:
                phi = 0.0
        if rmin < lo:
            denom = f[i] - rmin
            if denom > 1e-300:
                phi = min(phi, max((f[i] - lo) / denom, 0.0))
            else:
                phi = 0.0
        lim[i] = max(0.0, min(1.0, phi))
    return grad * lim[:, None], float(np.mean(lim < 1.0 - 1e-15))


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

def _rel(a: float, b: float) -> float:
    scale = max(abs(a), abs(b), 1e-300)
    return abs(a - b) / scale


@dataclass(frozen=True)
class MomentRemapResult:
    """Remapped field plus the complete conservation audit."""

    field: np.ndarray
    mass_source: float
    mass_target: float
    mass_rel_error: float
    m0_rel_error: float
    m1_rel_error: float
    m2_rel_error: float
    centroid_source: np.ndarray
    centroid_target: np.ndarray
    centroid_drift_cells: float
    inertia_source: np.ndarray
    inertia_target: np.ndarray
    inertia_rel_error: float
    continuous_m1_rel_error: float
    continuous_m2_rel_error: float
    centroid_drift_cells_continuous: float
    array_m1_rel_error: float
    array_m2_rel_error: float
    centroid_drift_cells_array: float
    bound_violations: int
    bound_violation_max: float
    bounds: Tuple[float, float]
    correction_rel: float
    limited_fraction: float
    degenerate_stencils: int
    coverage_error: float
    moments_enforced: bool
    moment_order: int
    moment_reference: str
    constraints_feasible: bool
    mass_repaired: bool

    def to_dict(self) -> dict:
        return {
            "mass_source": self.mass_source,
            "mass_target": self.mass_target,
            "mass_rel_error": self.mass_rel_error,
            "m0_rel_error": self.m0_rel_error,
            "m1_rel_error": self.m1_rel_error,
            "m2_rel_error": self.m2_rel_error,
            "centroid_source": [float(x) for x in self.centroid_source],
            "centroid_target": [float(x) for x in self.centroid_target],
            "centroid_drift_cells": self.centroid_drift_cells,
            "inertia_source": self.inertia_source.tolist(),
            "inertia_target": self.inertia_target.tolist(),
            "inertia_rel_error": self.inertia_rel_error,
            "continuous_m1_rel_error": self.continuous_m1_rel_error,
            "continuous_m2_rel_error": self.continuous_m2_rel_error,
            "centroid_drift_cells_continuous": self.centroid_drift_cells_continuous,
            "array_m1_rel_error": self.array_m1_rel_error,
            "array_m2_rel_error": self.array_m2_rel_error,
            "centroid_drift_cells_array": self.centroid_drift_cells_array,
            "bound_violations": self.bound_violations,
            "bound_violation_max": self.bound_violation_max,
            "bounds": [self.bounds[0], self.bounds[1]],
            "correction_rel": self.correction_rel,
            "limited_fraction": self.limited_fraction,
            "degenerate_stencils": self.degenerate_stencils,
            "coverage_error": self.coverage_error,
            "moments_enforced": self.moments_enforced,
            "moment_order": self.moment_order,
            "moment_reference": self.moment_reference,
            "constraints_feasible": self.constraints_feasible,
            "mass_repaired": self.mass_repaired,
        }


def _centroid_and_inertia(
    moments: np.ndarray, order: int
) -> Tuple[np.ndarray, np.ndarray]:
    """Centroid and second central moment tensor from a raw moment vector.

    ``moments`` follows :func:`basis_monomials`, i.e. ``[m0, mx, my, mxx, mxy,
    myy]`` for ``order=2`` in shifted coordinates.  The second central moment
    (inertia) tensor is ``M2 - m0 xc xc^T``.
    """
    m0 = moments[0]
    if abs(m0) < 1e-300:
        return np.zeros(2), np.zeros((2, 2))
    xc = np.array([moments[1], moments[2]]) / m0
    if order < 2:
        return xc, np.zeros((2, 2))
    M2 = np.array([[moments[3], moments[4]], [moments[4], moments[5]]])
    return xc, M2 - m0 * np.outer(xc, xc)


# ---------------------------------------------------------------------------
# The remap operator
# ---------------------------------------------------------------------------

class MomentRemap:
    """Conservative 2D remap with local linear reconstruction and exact moments.

    Parameters
    ----------
    src, tgt : :class:`~backend.coupling.mesh.Mesh` (2D, convex CCW polygons).
    reconstruction : ``"linear"`` (default) or ``"donor"`` (piecewise constant --
        the classic first-order baseline used as a negative control).
    limiter : ``"vertex"`` (default, linearity preserving), ``"stencil"``
        (classical Barth-Jespersen, more diffusive) or ``None``.
    enforce_moments : enable the Lagrange moment correction.  With ``False`` the
        operator is the plain (limited) conservative remap -- the negative control
        that must *fail* the moment criteria.
    moment_order : ``0``, ``1`` or ``2`` (default 2).  ``2`` matches all monomials
        of degree <= 2, i.e. the mass, the centroid and the full second central
        moment (inertia) tensor.
    moment_reference : ``"array"`` (default) or ``"continuous"``; see the module
        docstring.
    bounds : ``None`` -> ``(min(f), max(f))`` of the exchanged field (boundedness
        relative to the source field), ``(lo, hi)`` explicit, or ``(None, None)``
        to disable bound enforcement.
    n_neighbors : least-squares stencil size.
    coverage_tol : fail-closed tolerance on the coverage defect.
    """

    def __init__(
        self,
        src: Mesh,
        tgt: Mesh,
        *,
        reconstruction: str = "linear",
        limiter: Optional[str] = "vertex",
        enforce_moments: bool = True,
        moment_order: int = 2,
        moment_reference: str = "array",
        bounds: Optional[Tuple[Optional[float], Optional[float]]] = None,
        n_neighbors: int = 6,
        coverage_tol: float = 1e-8,
    ) -> None:
        if reconstruction not in ("linear", "donor"):
            raise MomentRemapError(
                f"unknown reconstruction {reconstruction!r}; use 'linear' or 'donor'"
            )
        if moment_order not in (0, 1, 2):
            raise MomentRemapError("moment_order must be 0, 1 or 2")
        if moment_reference not in ("array", "continuous"):
            raise MomentRemapError(
                f"unknown moment_reference {moment_reference!r}; "
                "use 'array' or 'continuous'"
            )
        if limiter in (True, False):
            limiter = "vertex" if limiter else None
        if limiter not in (None, "vertex", "stencil"):
            raise MomentRemapError(
                f"unknown limiter {limiter!r}; use 'vertex', 'stencil' or None"
            )
        if src.dim != 2 or tgt.dim != 2:
            raise MomentRemapError(
                "MomentRemap implements the exact 2D path only; "
                f"got dim={src.dim}/{tgt.dim} (3D would need overlap moments)"
            )
        self.src = src
        self.tgt = tgt
        self.reconstruction = reconstruction
        self.limiter = limiter
        self.enforce_moments = bool(enforce_moments)
        self.moment_order = int(moment_order)
        self.moment_reference = moment_reference
        self.bounds = bounds
        self.n_neighbors = int(n_neighbors)

        self.geom = overlap_geometry_2d(src, tgt)
        if self.geom.coverage_error > coverage_tol:
            raise MomentRemapError(
                "source and target meshes do not cover the same domain: "
                f"coverage defect {self.geom.coverage_error:.3e} > {coverage_tol:.1e}"
            )
        all_cells = np.vstack([src.cells, tgt.cells])
        self.x_ref = 0.5 * (all_cells.min(axis=0) + all_cells.max(axis=0))
        self._src_xy = src.cells - self.x_ref
        self._tgt_xy = tgt.cells - self.x_ref
        self._stencils = _knn_stencils(src.cells, self.n_neighbors)
        self._cell_moments = [
            polygon_monomial_moments(
                np.asarray(p, dtype=float) - self.x_ref, self.moment_order + 1
            )
            for p in src.polygons
        ]
        self._A_tgt = _basis_matrix(self._tgt_xy, self.moment_order)
        self._A_src = _basis_matrix(self._src_xy, self.moment_order)
        self._h_tgt = float(np.mean(np.sqrt(tgt.volumes)))
        # constraint matrix Phi[j, c] = V_j phi_c(x_j) for the Lagrange solve
        self._Phi = self._A_tgt * self.tgt.volumes[:, None]
        self._Phi_pinv = np.linalg.pinv(self._Phi)

    # -- reconstruction ----------------------------------------------------
    def _gradients(self, f: np.ndarray) -> Tuple[np.ndarray, float, int]:
        if self.reconstruction == "donor":
            return np.zeros((self.src.n_cells, 2)), 0.0, 0
        grad, degenerate = linear_reconstruction_gradients(
            self.src, f, n_neighbors=self.n_neighbors
        )
        limited = 0.0
        if self.limiter is not None:
            grad, limited = _limit_gradients(
                self.src, f, grad, self._stencils, self.limiter
            )
        return grad, limited, degenerate

    def _reconstruction_moments(self, f: np.ndarray, grad: np.ndarray) -> np.ndarray:
        """Exact moments ``int f_hat phi_c dA`` of the reconstruction."""
        out = np.zeros(self._A_tgt.shape[1])
        exps = basis_monomials(self.moment_order)
        for i in range(self.src.n_cells):
            I = self._cell_moments[i]
            xy = self._src_xy[i]
            gx, gy = grad[i]
            for c, (a, b) in enumerate(exps):
                ipq = I[(a, b)]
                out[c] += (
                    f[i] * ipq
                    + gx * (I[(a + 1, b)] - xy[0] * ipq)
                    + gy * (I[(a, b + 1)] - xy[1] * ipq)
                )
        return out

    def _array_moments(self, mesh: Mesh, xy: np.ndarray, field: np.ndarray,
                       A: np.ndarray) -> np.ndarray:
        return A.T @ (mesh.volumes * field)

    # -- bounded exact correction -----------------------------------------
    def _active_set(
        self, f_base: np.ndarray, b: np.ndarray, lo: float, hi: float, max_iter: int
    ) -> Tuple[np.ndarray, bool, int]:
        """Minimum-norm correction with ``Phi^T delta = b`` and bound constraints."""
        n = f_base.size
        nb = b.size
        Phi = self._Phi
        delta = np.zeros(n)
        free = np.ones(n, dtype=bool)
        active = 0
        tol = 1e-12 * max(abs(lo), abs(hi), 1.0)
        feasible = True
        for _ in range(max_iter):
            idx = np.flatnonzero(free)
            if idx.size < nb:
                feasible = False
                break
            need = b - Phi[~free].T @ delta[~free]
            Pf = Phi[idx]
            # column scaling: the monomial basis spans several decades
            col = np.maximum(np.linalg.norm(Pf, axis=0), 1e-300)
            Ps = Pf / col
            y, *_ = np.linalg.lstsq(Ps.T @ Ps, need / col, rcond=None)
            delta[idx] = Ps @ y
            trial = f_base + delta
            viol = ((trial > hi + tol) | (trial < lo - tol)) & free
            if not np.any(viol):
                break
            vidx = np.flatnonzero(viol)
            delta[vidx] = np.where(trial[vidx] > hi, hi - f_base[vidx], lo - f_base[vidx])
            free[vidx] = False
            active = int((~free).sum())
        else:
            feasible = False
        trial = f_base + delta
        if np.any((trial > hi + tol) | (trial < lo - tol)):
            feasible = False
            delta = np.clip(trial, lo, hi) - f_base
        return delta, feasible, active

    def _mass_repair(
        self, f_base: np.ndarray, delta: np.ndarray, need_m0: float,
        lo: float, hi: float, max_iter: int = 128,
    ) -> Tuple[np.ndarray, bool]:
        """Restore the 0th moment by a bounded uniform redistribution."""
        V = self.tgt.volumes
        delta = delta.copy()
        for _ in range(max_iter):
            deficit = float(need_m0 - np.dot(V, delta))
            scale = max(abs(need_m0),
                        float(np.dot(V, np.abs(f_base + delta))), 1e-300)
            if abs(deficit) <= 1e-14 * scale:
                return delta, True
            cur = f_base + delta
            cap = (hi - cur) if deficit > 0.0 else (cur - lo)
            cap = np.where(np.isfinite(cap), np.maximum(cap, 0.0), np.inf)
            free = cap > 0.0
            if not np.any(free):
                return delta, False
            idx = np.flatnonzero(free)
            sign = 1.0 if deficit > 0.0 else -1.0
            remaining = deficit
            while idx.size:
                shift = remaining / V[idx].sum()
                over = (sign * shift) > cap[idx]
                if not np.any(over):
                    delta[idx] += shift
                    remaining = 0.0
                    break
                sat = idx[over]
                step = sign * cap[sat]
                delta[sat] += step
                remaining -= float(np.dot(V[sat], step))
                idx = idx[~over]
            if remaining == 0.0:
                return delta, True
        deficit = float(need_m0 - np.dot(V, delta))
        scale = max(abs(need_m0), 1e-300)
        return delta, abs(deficit) <= 1e-12 * scale

    def _solve_correction(
        self, f_base: np.ndarray, b: np.ndarray, lo: float, hi: float, max_iter: int = 64
    ) -> Tuple[np.ndarray, bool, int, bool]:
        """Bounded moment correction -> ``(delta, feasible, n_active, repaired)``."""
        delta, feasible, active = self._active_set(f_base, b, lo, hi, max_iter)
        repaired = False
        if not feasible and np.isfinite(lo) and np.isfinite(hi):
            delta, repaired = self._mass_repair(f_base, delta, b[0], lo, hi)
        return delta, feasible, active, repaired

    # -- main entry point --------------------------------------------------
    def apply(self, field: np.ndarray) -> MomentRemapResult:
        """Remap ``field`` (source cell values) onto the target mesh."""
        f = np.asarray(field, dtype=float)
        if f.shape != (self.src.n_cells,):
            raise MomentRemapError(
                f"field shape {f.shape} != ({self.src.n_cells},) for the source mesh"
            )
        if not np.all(np.isfinite(f)):
            raise MomentRemapError("field contains non-finite values")

        grad, limited_fraction, degenerate = self._gradients(f)

        # ----- base conservative transfer (exact overlap geometry) ----------
        integrated = self.geom.C @ f + np.einsum("jid,id->j", self.geom.M1, grad)
        f_base = integrated / self.tgt.volumes

        if self.bounds is None:
            lo = float(np.min(f))
            hi = float(np.max(f))
        else:
            lo = -np.inf if self.bounds[0] is None else float(self.bounds[0])
            hi = np.inf if self.bounds[1] is None else float(self.bounds[1])

        # ----- reference moments and exact correction -----------------------
        recon_moments = self._reconstruction_moments(f, grad)
        array_src_moments = self._array_moments(self.src, self._src_xy, f, self._A_src)
        ref_moments = (
            array_src_moments if self.moment_reference == "array" else recon_moments
        )
        base_moments = self._array_moments(self.tgt, self._tgt_xy, f_base, self._A_tgt)

        feasible = True
        mass_repaired = False
        delta = np.zeros_like(f_base)
        if self.enforce_moments:
            delta, feasible, _nactive, mass_repaired = self._solve_correction(
                f_base, ref_moments - base_moments, lo, hi
            )
        f_tgt = f_base + delta

        # ----- audit ---------------------------------------------------------
        tgt_moments = self._array_moments(self.tgt, self._tgt_xy, f_tgt, self._A_tgt)
        idx1 = [1, 2]
        idx2 = [3, 4, 5] if self.moment_order >= 2 else []

        m0_scale = max(abs(ref_moments[0]), 1e-300)
        m0_rel = abs(tgt_moments[0] - ref_moments[0]) / m0_scale
        # first/second moment residuals are reported in physical units: the
        # reference scale is the natural scale of an order-p moment, m0 h^p
        # (h = mean target cell size), so the number reads as an offset in cell
        # units and does not blow up when a moment vanishes by symmetry.
        m1_scale = max(abs(ref_moments[0]) * self._h_tgt, 1e-300)
        m1_rel = float(np.max(np.abs(tgt_moments[idx1] - ref_moments[idx1]))) / m1_scale
        if idx2:
            m2_scale = max(
                min(float(np.max(np.abs(ref_moments[idx2]))),
                    abs(ref_moments[0]) * self._h_tgt ** 2),
                1e-300,
            )
            m2_rel = float(np.max(np.abs(tgt_moments[idx2] - ref_moments[idx2]))) / m2_scale
        else:
            m2_rel = 0.0

        mass_src = float(np.dot(self.src.volumes, f))
        mass_tgt = float(np.dot(self.tgt.volumes, f_tgt))

        cm_ref, inert_ref = _centroid_and_inertia(ref_moments, self.moment_order)
        cm_tgt, inert_tgt = _centroid_and_inertia(tgt_moments, self.moment_order)
        drift = float(np.linalg.norm(cm_tgt - cm_ref) / self._h_tgt)
        inert_rel = (
            float(np.linalg.norm(inert_tgt - inert_ref)
                  / max(np.linalg.norm(inert_ref), 1e-300))
            if idx2 else 0.0
        )

        # diagnostics for *both* conventions, whatever the active one is:
        # the target array measured against the exact continuous moments
        # (quadrature bias of the target mesh) and against the source array
        # moments (array drift visible to a downstream auditor).
        def _residual(other: np.ndarray) -> Tuple[float, float, float]:
            s1 = max(abs(other[0]) * self._h_tgt, 1e-300)
            r1 = float(np.max(np.abs(tgt_moments[idx1] - other[idx1]))) / s1
            if idx2:
                s2 = max(
                    min(float(np.max(np.abs(other[idx2]))),
                        abs(other[0]) * self._h_tgt ** 2),
                    1e-300,
                )
                r2 = float(np.max(np.abs(tgt_moments[idx2] - other[idx2]))) / s2
            else:
                r2 = 0.0
            cm_o, _ = _centroid_and_inertia(other, self.moment_order)
            return r1, r2, float(np.linalg.norm(cm_tgt - cm_o) / self._h_tgt)

        c_m1, c_m2, drift_c = _residual(recon_moments)
        a_m1, a_m2, drift_a = _residual(array_src_moments)

        viol_mask = (f_tgt > hi + 1e-12 * max(abs(hi), 1.0)) | (
            f_tgt < lo - 1e-12 * max(abs(lo), 1.0)
        )
        n_viol = int(viol_mask.sum())
        viol_max = 0.0
        if n_viol:
            viol_max = float(np.max(np.abs(
                f_tgt[viol_mask] - np.clip(f_tgt[viol_mask], lo, hi)
            )))
        corr_scale = max(float(np.max(np.abs(f_base))), 1e-300)

        return MomentRemapResult(
            field=f_tgt,
            mass_source=mass_src,
            mass_target=mass_tgt,
            mass_rel_error=_rel(mass_src, mass_tgt),
            m0_rel_error=float(m0_rel),
            m1_rel_error=m1_rel,
            m2_rel_error=m2_rel,
            centroid_source=cm_ref,
            centroid_target=cm_tgt,
            centroid_drift_cells=drift,
            inertia_source=inert_ref,
            inertia_target=inert_tgt,
            inertia_rel_error=inert_rel,
            continuous_m1_rel_error=c_m1,
            continuous_m2_rel_error=c_m2,
            centroid_drift_cells_continuous=drift_c,
            array_m1_rel_error=a_m1,
            array_m2_rel_error=a_m2,
            centroid_drift_cells_array=drift_a,
            bound_violations=n_viol,
            bound_violation_max=viol_max,
            bounds=(float(lo), float(hi)),
            correction_rel=float(np.max(np.abs(delta)) / corr_scale),
            limited_fraction=limited_fraction,
            degenerate_stencils=int(degenerate),
            coverage_error=self.geom.coverage_error,
            moments_enforced=self.enforce_moments,
            moment_order=self.moment_order,
            moment_reference=self.moment_reference,
            constraints_feasible=bool(feasible),
            mass_repaired=bool(mass_repaired),
        )