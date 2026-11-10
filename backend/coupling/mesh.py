"""Cell-centered unstructured meshes for cross-solver field exchange.

Zero domain semantics: a mesh is just centroids + volumes (+ optional 2D
polygon loops for the exact overlap path). No solver names, no physics.

The 2D exact overlap path requires *convex* cell polygons (triangles,
quads, Voronoi cells). Non-convex loops are rejected (fail-closed) so a
silent accuracy loss can never masquerade as a conservative exchange.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

__all__ = [
    "Mesh",
    "MeshError",
    "structured_mesh_2d",
    "structured_mesh_3d",
    "polygon_area",
    "is_convex_ccw",
]


class MeshError(ValueError):
    """Structurally invalid mesh data (fail-closed)."""


def _as_2d_array(x, name: str) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if a.ndim != 2:
        raise MeshError(f"{name} must be a 2D array, got shape {a.shape}")
    if not np.all(np.isfinite(a)):
        raise MeshError(f"{name} contains non-finite values")
    return a


def polygon_area(poly: np.ndarray) -> float:
    """Signed area of a 2D polygon (shoelace). CCW -> positive."""
    poly = np.asarray(poly, dtype=float)
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def is_convex_ccw(poly: np.ndarray, tol: float = 1e-12) -> bool:
    """True if the 2D loop is strictly convex and counter-clockwise."""
    poly = np.asarray(poly, dtype=float)
    if len(poly) < 3:
        return False
    if polygon_area(poly) <= 0.0:
        return False
    for i in range(len(poly)):
        a, b, c = poly[i], poly[(i + 1) % len(poly)], poly[(i + 2) % len(poly)]
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if cross < -tol:
            return False
    return True


@dataclass(frozen=True)
class Mesh:
    """Cell-centered unstructured mesh.

    Parameters
    ----------
    dim : 2 or 3
    cells : (n, dim) centroids
    volumes : (n,) cell volumes (areas in 2D), all > 0
    polygons : 2D only. Sequence of (k_i, 2) CCW vertex loops, one per cell.
        Required for the exact overlap path; may be omitted in 3D.
    """

    dim: int
    cells: np.ndarray
    volumes: np.ndarray
    polygons: Optional[Sequence[np.ndarray]] = None

    def __post_init__(self) -> None:
        if self.dim not in (2, 3):
            raise MeshError(f"dim must be 2 or 3, got {self.dim}")
        cells = _as_2d_array(self.cells, "cells")
        if cells.shape[1] != self.dim:
            raise MeshError(f"cells shape {cells.shape} inconsistent with dim={self.dim}")
        vols = np.asarray(self.volumes, dtype=float)
        if vols.ndim != 1 or vols.shape[0] != cells.shape[0]:
            raise MeshError("volumes must be 1D with one entry per cell")
        if not np.all(np.isfinite(vols)) or np.any(vols <= 0.0):
            raise MeshError("volumes must be finite and strictly positive")
        if self.dim == 2 and self.polygons is not None:
            if len(self.polygons) != cells.shape[0]:
                raise MeshError("one polygon loop per cell required")
            for i, p in enumerate(self.polygons):
                p = np.asarray(p, dtype=float)
                if p.ndim != 2 or p.shape[1] != 2 or p.shape[0] < 3:
                    raise MeshError(f"cell {i}: polygon loop must be (k>=3, 2)")
                if not np.all(np.isfinite(p)):
                    raise MeshError(f"cell {i}: polygon contains non-finite values")
                if not is_convex_ccw(p):
                    raise MeshError(
                        f"cell {i}: non-convex or clockwise loop rejected "
                        "(exact 2D overlap path is fail-closed)"
                    )
        if self.dim == 3 and self.polygons is not None:
            raise MeshError("polygons are only supported for dim=2")

    @property
    def n_cells(self) -> int:
        return int(self.cells.shape[0])

    @property
    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.cells.min(axis=0), self.cells.max(axis=0)


def structured_mesh_2d(
    nx: int, ny: int, x0: float = 0.0, x1: float = 1.0, y0: float = 0.0, y1: float = 1.0
) -> Mesh:
    """Uniform quad mesh on [x0,x1]x[y0,y1] with CCW polygon loops."""
    if nx < 1 or ny < 1:
        raise MeshError("nx, ny must be >= 1")
    xs = np.linspace(x0, x1, nx + 1)
    ys = np.linspace(y0, y1, ny + 1)
    cells, polys = [], []
    for j in range(ny):
        for i in range(nx):
            p00, p10 = (xs[i], ys[j]), (xs[i + 1], ys[j])
            p11, p01 = (xs[i + 1], ys[j + 1]), (xs[i], ys[j + 1])
            cells.append(((p00[0] + p11[0]) / 2.0, (p00[1] + p11[1]) / 2.0))
            polys.append(np.array([p00, p10, p11, p01]))
    dx, dy = (x1 - x0) / nx, (y1 - y0) / ny
    return Mesh(dim=2, cells=np.array(cells), volumes=np.full(nx * ny, dx * dy), polygons=polys)


def structured_mesh_3d(
    nx: int, ny: int, nz: int,
    x0: float = 0.0, x1: float = 1.0,
    y0: float = 0.0, y1: float = 1.0,
    z0: float = 0.0, z1: float = 1.0,
) -> Mesh:
    """Uniform hex mesh on a box (no polygon loops; 3D uses the grid path)."""
    if min(nx, ny, nz) < 1:
        raise MeshError("nx, ny, nz must be >= 1")
    xs = np.linspace(x0, x1, nx + 1)
    ys = np.linspace(y0, y1, ny + 1)
    zs = np.linspace(z0, z1, nz + 1)
    cells = np.array([
        [(xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2, (zs[k] + zs[k + 1]) / 2]
        for k in range(nz) for j in range(ny) for i in range(nx)
    ])
    vol = ((x1 - x0) / nx) * ((y1 - y0) / ny) * ((z1 - z0) / nz)
    return Mesh(dim=3, cells=cells, volumes=np.full(nx * ny * nz, vol))
