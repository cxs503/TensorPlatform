"""Conservative field-exchange operators between unstructured meshes.

Two operators, both cell-centered (piecewise-constant source field):

* :class:`L2Remap` -- L2 projection of the source field onto the target
  cell space, ``f_j = (1/V_j) * int_{C_j} f dV``.
  - 2D: overlap integrals are computed *exactly* by Sutherland-Hodgman
    polygon clipping (convex cells). Global integral is conserved to
    machine precision.
  - 3D: overlap integrals are computed on a structured background grid
    (Voronoi assignment of background cells to the nearest cell centroid).
    The discrete integral is conserved exactly; the field is first-order
    accurate and the error shrinks with the background-grid resolution.

* :class:`RBFRemap` -- radial-basis-function interpolation (Wendland C2,
  compact support) from source centroids to target centroids. High-order
  for smooth fields, *not* conservative by construction; an optional
  global mass-correction factor restores exact integral conservation.

Zero domain semantics: fields are plain arrays; "mass" below means the
global integral of the scalar field, whatever the caller's quantity is.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .mesh import Mesh, MeshError, polygon_area

__all__ = ["L2Remap", "RBFRemap", "RemapError", "RemapResult"]


class RemapError(ValueError):
    """Invalid remap configuration or input (fail-closed)."""


@dataclass(frozen=True)
class RemapResult:
    """Remapped field plus the conservation audit of the exchange."""

    field: np.ndarray
    integral_source: float
    integral_target: float
    conservation_error: float  # |I_tgt - I_src| / max(|I_src|, tiny)

    def to_dict(self) -> dict:
        return {
            "integral_source": self.integral_source,
            "integral_target": self.integral_target,
            "conservation_error": self.conservation_error,
        }


def _rel_err(a: float, b: float) -> float:
    scale = max(abs(a), abs(b), 1e-300)
    return abs(a - b) / scale


# ---------------------------------------------------------------------------
# 2D exact overlap (Sutherland-Hodgman, convex clip polygons)
# ---------------------------------------------------------------------------

def _seg_line_intersect(s: np.ndarray, p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Intersection of segment s->p with the infinite line a->b."""
    d = p - s
    e = b - a
    denom = d[0] * e[1] - d[1] * e[0]
    if abs(denom) < 1e-30:
        return 0.5 * (s + p)
    t = ((a[0] - s[0]) * e[1] - (a[1] - s[1]) * e[0]) / denom
    return s + t * d


def _clip_polygon(subject: np.ndarray, clip: np.ndarray) -> np.ndarray:
    """Clip a polygon by a *convex CCW* clip polygon (Sutherland-Hodgman)."""
    out = [np.asarray(v, dtype=float) for v in subject]
    for i in range(len(clip)):
        a, b = clip[i], clip[(i + 1) % len(clip)]
        e = b - a

        def side(p: np.ndarray) -> float:
            # CCW polygon: interior is to the LEFT of each directed edge
            return float(e[0] * (p[1] - a[1]) - e[1] * (p[0] - a[0]))

        new: list[np.ndarray] = []
        if not out:
            break
        s = out[-1]
        for p in out:
            ps, pp = side(s), side(p)
            if pp >= -1e-14:
                if ps < -1e-14:
                    new.append(_seg_line_intersect(s, p, a, b))
                new.append(p)
            elif ps >= -1e-14:
                new.append(_seg_line_intersect(s, p, a, b))
            s = p
        out = new
        if not out:
            break
    return np.array(out) if out else np.empty((0, 2))


def _overlap_area_2d(poly_a: np.ndarray, poly_b: np.ndarray) -> float:
    """Exact overlap area of two convex CCW 2D polygons."""
    inter = _clip_polygon(poly_a, poly_b)
    if len(inter) < 3:
        return 0.0
    return abs(polygon_area(inter))


def _weights_2d(src: Mesh, tgt: Mesh) -> np.ndarray:
    """W[j, i] = A_ij / V_j with exact polygon clipping."""
    if src.polygons is None or tgt.polygons is None:
        raise RemapError("2D exact path requires polygon loops on both meshes")
    W = np.zeros((tgt.n_cells, src.n_cells))
    for j, pj in enumerate(tgt.polygons):
        for i, pi in enumerate(src.polygons):
            a = _overlap_area_2d(pi, pj)
            if a > 0.0:
                W[j, i] = a / tgt.volumes[j]
    return W


# ---------------------------------------------------------------------------
# 3D overlap on a structured background grid (Voronoi assignment)
# ---------------------------------------------------------------------------

def _weights_3d(src: Mesh, tgt: Mesh, n_grid: int, domain: tuple | None = None) -> np.ndarray:
    """W[j, i] = (sum of background cells assigned to both) / V_j.

    Every background cell is assigned to exactly one source cell and one
    target cell (nearest centroid), so the discrete integral
    ``sum_j V_j (W f)_j`` equals ``sum_bg v_bg f_src(bg)`` exactly; the
    only error is the first-order mismatch between Voronoi volumes and
    declared cell volumes, which shrinks with ``n_grid``.

    ``domain`` = (lo, hi) box the exchange takes place in. If omitted it
    defaults to the centroid bounding box, which under-covers the true
    domain for cell-centered meshes; the coverage check in :class:`L2Remap`
    then fails closed, so pass the true domain for cell-centered meshes.
    """
    if n_grid < 2:
        raise RemapError("n_grid must be >= 2")
    if domain is not None:
        lo, hi = np.asarray(domain[0], float), np.asarray(domain[1], float)
    else:
        lo = np.minimum(src.cells.min(axis=0), tgt.cells.min(axis=0))
        hi = np.maximum(src.cells.max(axis=0), tgt.cells.max(axis=0))
    if np.any(hi <= lo):
        raise RemapError("degenerate bounding box")
    edges = np.linspace(lo, hi, n_grid + 1, axis=0)
    centers = 0.5 * (edges[:-1] + edges[1:])
    gx, gy, gz = np.meshgrid(centers[:, 0], centers[:, 1], centers[:, 2], indexing="ij")
    bg = np.stack([gx.ravel(), gy.ravel(), gz.ravel()], axis=1)
    v_bg = float(np.prod(hi - lo)) / float(n_grid ** 3)

    W = np.zeros((tgt.n_cells, src.n_cells))
    chunk = 8192
    for s in range(0, len(bg), chunk):
        blk = bg[s : s + chunk]
        d_src = np.linalg.norm(blk[:, None, :] - src.cells[None, :, :], axis=2)
        d_src = d_src.argmin(axis=1)
        d_tgt = np.linalg.norm(blk[:, None, :] - tgt.cells[None, :, :], axis=2)
        d_tgt = d_tgt.argmin(axis=1)
        np.add.at(W, (d_tgt, d_src), v_bg)
    return W / tgt.volumes[:, None]


# ---------------------------------------------------------------------------
# L2 projection remap
# ---------------------------------------------------------------------------

class L2Remap:
    """Conservative L2-projection exchange operator.

    ``apply(f)`` returns the target field ``f_j = sum_i W[j,i] f_i`` with
    ``W[j,i] = V_ij / V_j``. The global integral is conserved exactly in
    2D (exact polygon overlap) and to background-grid accuracy in 3D.
    """

    def __init__(
        self,
        source: Mesh,
        target: Mesh,
        n_grid: int = 64,
        domain: tuple | None = None,
    ) -> None:
        if source.dim != target.dim:
            raise RemapError(f"dimension mismatch: source {source.dim}D vs target {target.dim}D")
        self.source = source
        self.target = target
        if source.dim == 2:
            self.W = _weights_2d(source, target)
        else:
            self.W = _weights_3d(source, target, n_grid, domain)
        # fail-closed: every source cell must be fully covered by the target
        # mesh (and vice versa), otherwise the exchange silently leaks mass.
        # covered volume of source cell i = sum_j A_ij = sum_j W[j,i] V_j
        cov_src = (self.W * target.volumes[:, None]).sum(axis=0)
        if np.any(np.abs(cov_src - source.volumes) > 1e-9 * source.volumes.max()):
            raise RemapError("source cells not fully covered by target mesh (leak)")
        cov_tgt = self.W.sum(axis=1) * target.volumes
        if np.any(np.abs(cov_tgt - target.volumes) > 1e-9 * target.volumes.max()):
            raise RemapError("target cells not fully covered by source mesh (leak)")

    def apply(self, field: np.ndarray) -> RemapResult:
        f = np.asarray(field, dtype=float)
        if f.shape != (self.source.n_cells,):
            raise RemapError(f"field shape {f.shape} != ({self.source.n_cells},)")
        if not np.all(np.isfinite(f)):
            raise RemapError("field contains non-finite values")
        out = self.W @ f
        i_src = float(np.sum(self.source.volumes * f))
        i_tgt = float(np.sum(self.target.volumes * out))
        return RemapResult(
            field=out,
            integral_source=i_src,
            integral_target=i_tgt,
            conservation_error=_rel_err(i_src, i_tgt),
        )


# ---------------------------------------------------------------------------
# RBF remap (Wendland C2, compact support)
# ---------------------------------------------------------------------------

def _wendland_c2(r: np.ndarray) -> np.ndarray:
    """Wendland C2 compactly supported RBF, r in [0,1] (0 outside)."""
    r = np.clip(r, 0.0, 1.0)
    out = (1.0 - r) ** 4 * (4.0 * r + 1.0)
    out[r > 1.0] = 0.0
    return out


class RBFRemap:
    """High-order RBF exchange for smooth fields (Wendland C2).

    Not conservative by construction; with ``correct_mass=True`` a global
    factor rescales the target field so the integral matches the source
    integral exactly (machine precision).
    """

    def __init__(
        self,
        source: Mesh,
        target: Mesh,
        length_scale: float | None = None,
        correct_mass: bool = True,
    ) -> None:
        if source.dim != target.dim:
            raise RemapError(f"dimension mismatch: source {source.dim}D vs target {target.dim}D")
        self.source = source
        self.target = target
        self.correct_mass = correct_mass
        n = source.n_cells
        d = np.linalg.norm(
            source.cells[:, None, :] - source.cells[None, :, :], axis=2
        )
        np.fill_diagonal(d, np.inf)
        nn = d.min(axis=1)
        self.length_scale = float(length_scale) if length_scale else float(np.median(nn)) * 1.5
        if self.length_scale <= 0:
            raise RemapError("non-positive length scale")
        A = _wendland_c2(d / self.length_scale)
        np.fill_diagonal(A, 1.0)
        try:
            self._solve = np.linalg.solve(A, np.eye(n))
        except np.linalg.LinAlgError as exc:
            raise RemapError(f"RBF system singular (length_scale={self.length_scale})") from exc
        K = _wendland_c2(
            np.linalg.norm(target.cells[:, None, :] - source.cells[None, :, :], axis=2)
            / self.length_scale
        )
        self.K = K @ self._solve  # (n_tgt, n_src)

    def apply(self, field: np.ndarray) -> RemapResult:
        f = np.asarray(field, dtype=float)
        if f.shape != (self.source.n_cells,):
            raise RemapError(f"field shape {f.shape} != ({self.source.n_cells},)")
        if not np.all(np.isfinite(f)):
            raise RemapError("field contains non-finite values")
        out = self.K @ f
        i_src = float(np.sum(self.source.volumes * f))
        if self.correct_mass:
            i_tgt = float(np.sum(self.target.volumes * out))
            if abs(i_tgt) > 1e-300:
                out = out * (i_src / i_tgt)
        i_tgt = float(np.sum(self.target.volumes * out))
        return RemapResult(
            field=out,
            integral_source=i_src,
            integral_target=i_tgt,
            conservation_error=_rel_err(i_src, i_tgt),
        )
