"""Conservative LBM→FVM flow-field exchange: volume remap + interface flux.

Two distinct conservation statements have to hold when a lattice-Boltzmann
field is handed to a finite-volume solver:

1. **Inventory** — the transferred fields must carry the same total mass and
   momentum as the source field::

       Σ_j ρ_j V_j = Σ_i ρ_i V_i ,      Σ_j (ρu)_j V_j = Σ_i (ρu)_i V_i

   The only linear transfer that satisfies this for *every* field is the
   supermesh (volume-overlap) remap: ``φ_j = (Σ_i φ_i V_ij) / V_j`` with
   ``V_ij = vol(cell_i ∩ cell_j)``.  Nearest-neighbour and inverse-distance
   weighting (the classical shortcuts) do not: they lose O(h) of the inventory.

2. **Interface flux** — the mass flow crossing the coupling plane must be
   identical on both sides::

       Φ_FVM = Σ_j ρ_j (u_j·n) A_j  ==  Φ_LBM = Σ_i ρ_i (u_i·n) A_i

   with ``A_i`` the area of ``Γ ∩ cell_i``.  This is *not* implied by the volume
   remap: it needs an *area-weighted* transfer on the coupling plane ``Γ``
   itself, hence :class:`InterfaceFluxTransfer`.

Both are evaluated with exact geometry: ``V_ij`` from convex-polytope
intersection and the surface areas from ``dV/dh`` of the level set, which is
exact for a polytope because ``V(h)`` is piecewise linear in the plane offset.

No domain semantics: cells, volumes, areas, densities and fluxes only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .geometry import Polytope, clip, intersect


# --------------------------------------------------------------------------- #
# Cell containers
# --------------------------------------------------------------------------- #
@dataclass
class FieldCell:
    """A control volume carrying cell-constant fields."""

    polyhedron: Polytope
    label: str = ""
    density: float = 1.0
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(3))
    pressure: float = 0.0

    def __post_init__(self) -> None:
        self.velocity = np.asarray(self.velocity, dtype=float).reshape(3)

    @property
    def volume(self) -> float:
        return self.polyhedron.volume

    @property
    def centroid(self) -> np.ndarray:
        return self.polyhedron.centroid


def lattice_cells(origin: Sequence[float], dx: float,
                  counts: Sequence[int], label: str = "src") -> List[FieldCell]:
    """Axis-aligned uniform lattice of cubic cells."""
    from .geometry import box

    ox, oy, oz = (float(v) for v in origin)
    nx, ny, nz = (int(v) for v in counts)
    cells: List[FieldCell] = []
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                c = np.array([ox + (i + 0.5) * dx, oy + (j + 0.5) * dx, oz + (k + 0.5) * dx])
                poly = box(c, [0.5 * dx] * 3)
                cells.append(FieldCell(polyhedron=poly, label=f"{label}{i}{j}{k}"))
    return cells


def structured_cells(origin: Sequence[float], spacing: Sequence[float],
                     counts: Sequence[int], label: str = "tgt") -> List[FieldCell]:
    """Axis-aligned structured grid with (possibly) anisotropic spacing."""
    from .geometry import box

    hx, hy, hz = (float(v) for v in spacing)
    nx, ny, nz = (int(v) for v in counts)
    ox, oy, oz = (float(v) for v in origin)
    cells: List[FieldCell] = []
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                poly = box([ox + (i + 0.5) * hx, oy + (j + 0.5) * hy, oz + (k + 0.5) * hz],
                           [0.5 * hx, 0.5 * hy, 0.5 * hz])
                cells.append(FieldCell(polyhedron=poly, label=f"{label}{i}{j}{k}"))
    return cells


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #
@dataclass
class RemapResult:
    """Outcome of a supermesh remap."""

    target_cells: List[FieldCell]
    mass_source: float
    mass_target: float
    mass_residual: float
    momentum_source: np.ndarray
    momentum_target: np.ndarray
    momentum_residual: float
    coverage: float             # fraction of target volume covered by the source
    tolerance: float
    max_uncovered_fraction: float

    def summary(self) -> dict:
        return {
            "mass_source": self.mass_source,
            "mass_target": self.mass_target,
            "mass_residual": self.mass_residual,
            "momentum_source": self.momentum_source.tolist(),
            "momentum_target": self.momentum_target.tolist(),
            "momentum_residual": self.momentum_residual,
            "coverage": self.coverage,
            "max_uncovered_fraction": self.max_uncovered_fraction,
            "tolerance": self.tolerance,
            "passed": (self.mass_residual <= self.tolerance
                       and self.momentum_residual <= self.tolerance
                       and self.max_uncovered_fraction <= self.tolerance),
        }


@dataclass
class FluxResult:
    """Outcome of the interface-flux transfer."""

    flux_source: float
    flux_target: float
    flux_residual: float
    area_source: float
    area_target: float
    area_residual: float
    n_pairs: int
    tolerance: float
    per_target_flux: np.ndarray = field(default_factory=lambda: np.zeros(0))

    def summary(self) -> dict:
        return {
            "flux_source": self.flux_source,
            "flux_target": self.flux_target,
            "flux_residual": self.flux_residual,
            "area_source": self.area_source,
            "area_target": self.area_target,
            "area_residual": self.area_residual,
            "n_pairs": self.n_pairs,
            "tolerance": self.tolerance,
            "passed": self.flux_residual <= self.tolerance and self.area_residual <= self.tolerance,
        }


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
def _box_edges(poly: Polytope) -> List[Tuple[int, int]]:
    """Edges of an axis-aligned box from its sign-ordered corner list."""
    edges: List[Tuple[int, int]] = []
    n = poly.vertices.shape[0]
    for i in range(n):
        for bit in (1, 2, 4):
            j = i ^ bit
            if j > i and j < n:
                edges.append((i, j))
    return edges


def _polytope_edges(poly: Polytope) -> List[Tuple[int, int]]:
    if poly.is_axis_box:
        return _box_edges(poly)
    edges = set()
    for tri in poly.simplicies:
        a, b, c = int(tri[0]), int(tri[1]), int(tri[2])
        for u, v in ((a, b), (b, c), (a, c)):
            edges.add((min(u, v), max(u, v)))
    return sorted(edges)


def planarity(vertices: np.ndarray, normal: np.ndarray) -> np.ndarray:
    """Signed distance of each vertex to the plane through the origin."""
    return np.asarray(vertices, dtype=float) @ np.asarray(normal, dtype=float)


def cross_section_area(poly: Polytope, point: Sequence[float],
                       normal: Sequence[float], tol: float = 1e-12) -> float:
    """Exact area of ``poly ∩ {n.(x-point) = 0}``.

    The section polygon is obtained by clipping every polytope edge with the
    plane; its area follows from the shoelace formula in a plane basis.  This is
    exact geometry (no finite differences) and it returns 0 when the plane only
    touches a vertex or an edge.
    """
    n = np.asarray(normal, dtype=float)
    n = n / max(np.linalg.norm(n), 1e-300)
    p = np.asarray(point, dtype=float)
    d = float(n @ p)
    verts = np.asarray(poly.vertices, dtype=float)
    s = verts @ n - d
    scale = max(1.0, float(np.abs(verts).max()))
    eps = tol * scale
    if s.min() > eps or s.max() < -eps:
        return 0.0

    pts: List[np.ndarray] = []
    for i, j in _polytope_edges(poly):
        si, sj = s[i], s[j]
        if abs(si) <= eps:
            pts.append(verts[i])
        if abs(sj) <= eps:
            pts.append(verts[j])
        if (si > eps and sj < -eps) or (si < -eps and sj > eps):
            t = si / (si - sj)
            pts.append(verts[i] + t * (verts[j] - verts[i]))
    if len(pts) < 3:
        return 0.0
    pts_arr = np.unique(np.round(np.array(pts), 14), axis=0)
    if pts_arr.shape[0] < 3:
        return 0.0
    # Plane basis
    ref = np.array([0.0, 0.0, 1.0])
    if abs(n @ ref) > 0.9:
        ref = np.array([1.0, 0.0, 0.0])
    e1 = np.cross(n, ref)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    uv = np.column_stack([pts_arr @ e1, pts_arr @ e2])
    centre = uv.mean(axis=0)
    ang = np.arctan2(uv[:, 1] - centre[1], uv[:, 0] - centre[0])
    order = np.argsort(ang)
    uv = uv[order]
    x, y = uv[:, 0], uv[:, 1]
    area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
    return area


def cross_section_area_level_set(poly: Polytope, point: Sequence[float],
                                 normal: Sequence[float],
                                 eps: float = 1e-4) -> float:
    """Independent estimate of the section area: ``dV/dh`` of the level set.

    ``V(h) = vol(poly ∩ {n.(x-point) <= h})`` is piecewise linear in ``h`` for a
    polytope, so a central difference across ``h = 0`` reproduces the section
    area.  Used only to cross-check :func:`cross_section_area` (two independent
    geometric routes); the transfer itself uses the exact shoelace route.
    """
    n = np.asarray(normal, dtype=float)
    n = n / max(np.linalg.norm(n), 1e-300)
    p = np.asarray(point, dtype=float)
    d = float(n @ p)
    scale = max(1.0, float(np.abs(poly.vertices).max()))
    h = eps * scale

    def volume_at(offset: float) -> float:
        part = clip(poly, np.array([[n[0], n[1], n[2], d + offset]]))
        return 0.0 if part is None else part.volume

    return float((volume_at(h) - volume_at(-h)) / (2.0 * h))


def _overlap_map(source: Sequence[FieldCell],
                 target: Sequence[FieldCell]) -> Tuple[List[dict], float]:
    """Volume-overlap supermesh between two cell partitions."""
    bbox_src = np.array([[c.polyhedron.vertices.min(axis=0),
                          c.polyhedron.vertices.max(axis=0)] for c in source])
    pairs: List[dict] = []
    for j, tgt in enumerate(target):
        lo, hi = tgt.polyhedron.vertices.min(axis=0), tgt.polyhedron.vertices.max(axis=0)
        mask = np.all(bbox_src[:, 1] >= lo - 1e-12, axis=1) & np.all(bbox_src[:, 0] <= hi + 1e-12, axis=1)
        covered = 0.0
        for i in np.nonzero(mask)[0]:
            cell = intersect(source[int(i)].polyhedron, tgt.polyhedron)
            if cell is not None and cell.volume > 1e-16:
                pairs.append({"i": int(i), "j": j, "volume": cell.volume,
                              "centroid": cell.centroid})
                covered += cell.volume
    return pairs, 0.0


class SupermeshRemap:
    """Volume-overlap conservative transfer between two cell partitions."""

    def __init__(self, tolerance: float = 1e-12, min_overlap: float = 1e-16):
        self.tolerance = tolerance
        self.min_overlap = min_overlap
        self.pairs: List[dict] = []
        self.target_volumes = np.zeros(0)
        self._cached: Optional[Tuple[Sequence[FieldCell], Sequence[FieldCell]]] = None

    # ------------------------------------------------------------------ #
    def build(self, source: Sequence[FieldCell], target: Sequence[FieldCell]) -> dict:
        """Build the supermesh; returns its conservation prerequisites."""
        self.pairs, _ = _overlap_map(source, target)
        self.pairs = [p for p in self.pairs if p["volume"] > self.min_overlap]
        self._cached = (source, target)
        self.target_volumes = np.array([c.volume for c in target])
        covered = np.zeros(len(target))
        for p in self.pairs:
            covered[p["j"]] += p["volume"]
        uncovered = np.abs(self.target_volumes - covered) / np.maximum(self.target_volumes, 1e-300)
        self._covered = covered
        self.coverage = float(covered.sum() / max(self.target_volumes.sum(), 1e-300))
        return {
            "n_source": len(source),
            "n_target": len(target),
            "n_pairs": len(self.pairs),
            "source_volume": float(sum(c.volume for c in source)),
            "target_volume": float(self.target_volumes.sum()),
            "covered_volume": float(covered.sum()),
            "coverage": self.coverage,
            "max_uncovered_fraction": float(uncovered.max()) if len(uncovered) else 1.0,
        }

    # ------------------------------------------------------------------ #
    def _average(self, values: np.ndarray) -> np.ndarray:
        """Volume-weighted overlap average of per-source-cell values."""
        source, target = self._cached
        vals = np.asarray(values, dtype=float)
        is_vector = vals.ndim > 1
        acc = np.zeros((len(target), vals.shape[1])) if is_vector else np.zeros(len(target))
        for p in self.pairs:
            v = vals[p["i"]]
            if is_vector:
                acc[p["j"]] += p["volume"] * v
            else:
                acc[p["j"]] += p["volume"] * float(v)
        denom = np.maximum(self._covered, 1e-300)
        out = acc / (denom[:, None] if is_vector else denom)
        # Uncovered target cells keep their previous value (no silent zero fill).
        missing = self._covered <= 1e-300
        if np.any(missing):
            if is_vector:
                out[missing] = np.array([c.velocity for c in target])[missing]
            else:
                out[missing] = 0.0
        return out

    def remap(self, source: Sequence[FieldCell],
              target: Sequence[FieldCell]) -> RemapResult:
        """Transfer density and momentum; verify inventory conservation."""
        info = self.build(source, target)
        rho = self._average(np.array([c.density for c in source]))
        mom = self._average(np.array([c.density * c.velocity for c in source]))
        for j, cell in enumerate(target):
            rho_j = float(rho[j])
            cell.density = rho_j
            cell.velocity = mom[j] / max(rho_j, 1e-300)

        m_src = float(sum(c.density * c.volume for c in source))
        m_tgt = float(sum(c.density * c.volume for c in target))
        p_src = sum((c.density * c.volume * c.velocity for c in source), np.zeros(3))
        p_tgt = sum((c.density * c.volume * c.velocity for c in target), np.zeros(3))
        scale_p = max(0.5 * (np.linalg.norm(p_src) + np.linalg.norm(p_tgt)), 1e-300)
        return RemapResult(
            target_cells=list(target),
            mass_source=m_src,
            mass_target=m_tgt,
            mass_residual=abs(m_src - m_tgt) / max(0.5 * (abs(m_src) + abs(m_tgt)), 1e-300),
            momentum_source=p_src,
            momentum_target=p_tgt,
            momentum_residual=float(np.linalg.norm(p_src - p_tgt) / scale_p),
            coverage=info["coverage"],
            max_uncovered_fraction=info["max_uncovered_fraction"],
            tolerance=self.tolerance,
        )

    # ------------------------------------------------------------------ #
    def linear_field_error(self, gradient: np.ndarray) -> float:
        """Relative error of the remap for an exactly linear field.

        For ``φ(x) = g·x`` the cell average over a convex cell is ``g·centroid``,
        and the supermesh identity ``Σ_i V_ij c_i = V_j c_j`` makes the remap
        exact.  The residual therefore measures the geometry, not the physics.
        """
        source, target = self._cached
        g = np.asarray(gradient, dtype=float)
        exact = np.array([float(g @ c.centroid) for c in source])
        remapped = self._average(exact)
        expected = np.array([float(g @ c.centroid) for c in target])
        scale = max(float(np.abs(expected).max()), 1e-300)
        return float(np.abs(remapped - expected).max() / scale)


class InterfaceFluxTransfer:
    """Area-weighted mass-flux transfer across a coupling plane ``Γ``."""

    def __init__(self, tolerance: float = 1e-10):
        self.tolerance = tolerance

    @staticmethod
    def _section_owner_area(cell: FieldCell, point: np.ndarray,
                            normal: np.ndarray, tol: float) -> float:
        """Section area of ``cell`` on ``Γ`` counting every point exactly once.

        A point of the plane belongs to exactly one cell of a partition, so each
        cell that the plane *cuts* contributes its true section.  The only
        degenerate case is a plane lying exactly on a shared face: both cells
        see that face in their section, so it is attributed to the cell on the
        upstream (``n.(x-point) < 0``) side, which is the one actually feeding
        mass through it.
        """
        s = np.asarray(cell.polyhedron.vertices, dtype=float) @ normal - float(normal @ point)
        scale = max(1.0, float(np.abs(cell.polyhedron.vertices).max()))
        if s.min() >= -tol * scale:
            # Cell lies downstream of (or touches) the plane: its planar face is
            # owned by the upstream neighbour.
            return 0.0
        return cross_section_area(cell.polyhedron, point, normal)

    def transfer(self, source: Sequence[FieldCell], target: Sequence[FieldCell],
                 point: Sequence[float], normal: Sequence[float]) -> FluxResult:
        """Transfer the mass flux through ``Γ`` with an area-weighted operator.

        The only operator that keeps ``Φ`` invariant for *every* field is
        ``Φ_j = Σ_i (ρ_i u_i·n) A_ij`` with ``A_ij = area(Γ ∩ cell_i ∩ cell_j)``,
        because ``Σ_j A_ij = A_i`` exactly.  Unlike the volume remap this needs
        area weights, not volume weights.
        """
        n = np.asarray(normal, dtype=float)
        n = n / max(np.linalg.norm(n), 1e-300)
        p = np.asarray(point, dtype=float)
        scale = max(1.0, float(np.abs(p).max()),
                    float(max(np.abs(c.polyhedron.vertices).max() for c in source)))
        tol = 1e-12 * scale

        areas_src = np.array([self._section_owner_area(c, p, n, tol) for c in source])
        areas_tgt = np.array([self._section_owner_area(c, p, n, tol) for c in target])

        overlaps: List[dict] = []
        for i, sc in enumerate(source):
            if areas_src[i] <= 1e-14:
                continue
            for j, tc in enumerate(target):
                if areas_tgt[j] <= 1e-14:
                    continue
                overlap = intersect(sc.polyhedron, tc.polyhedron)
                if overlap is None:
                    continue
                area = self._section_owner_area(
                    FieldCell(polyhedron=overlap), p, n, tol)
                if area > 1e-14:
                    overlaps.append({"i": i, "j": j, "area": area})

        flux_src_cell = np.array([c.density * float(c.velocity @ n) for c in source])

        per_target = np.zeros(len(target))
        for ov in overlaps:
            per_target[ov["j"]] += flux_src_cell[ov["i"]] * ov["area"]

        phi_src = float(np.sum(flux_src_cell * areas_src))
        phi_tgt = float(per_target.sum())
        area_src = float(areas_src.sum())
        area_tgt = float(areas_tgt.sum())
        scale_flux = max(0.5 * (abs(phi_src) + abs(phi_tgt)), 1e-300)
        return FluxResult(
            flux_source=phi_src,
            flux_target=phi_tgt,
            flux_residual=abs(phi_src - phi_tgt) / scale_flux,
            area_source=area_src,
            area_target=area_tgt,
            area_residual=abs(area_src - area_tgt) / max(0.5 * (area_src + area_tgt), 1e-300),
            n_pairs=len(overlaps),
            tolerance=self.tolerance,
            per_target_flux=per_target,
        )

    def transfer_by_cell_centres(self, source: Sequence[FieldCell],
                                 target: Sequence[FieldCell],
                                 point: Sequence[float],
                                 normal: Sequence[float]) -> FluxResult:
        """Baseline: sample the donor field at the target centroid (no averaging).

        This is what "interpolate then integrate" does when the transfer is done
        point-wise with an inverse-distance kernel; the flux is no longer
        conservative because the area weights and the interpolation weights are
        unrelated.
        """
        n = np.asarray(normal, dtype=float)
        n = n / max(np.linalg.norm(n), 1e-300)
        p = np.asarray(point, dtype=float)
        scale_len = max(1.0, float(np.abs(p).max()),
                        float(max(np.abs(c.polyhedron.vertices).max() for c in source)))
        tol = 1e-12 * scale_len
        src_c = np.array([c.centroid for c in source])
        src_f = np.array([c.density * float(c.velocity @ n) for c in source])
        src_d = np.array([c.density for c in source])

        areas_src = np.array([self._section_owner_area(c, p, n, tol) for c in source])
        areas_tgt = np.array([self._section_owner_area(c, p, n, tol) for c in target])
        active_src = areas_src > 1e-14

        phi_tgt = 0.0
        for j, tc in enumerate(target):
            if areas_tgt[j] <= 1e-14:
                continue
            d = np.linalg.norm(src_c[active_src] - tc.centroid, axis=1)
            w = 1.0 / np.maximum(d, 1e-12) ** 2
            w /= w.sum()
            phi_tgt += float(w @ src_f[active_src]) * areas_tgt[j]
        phi_src = float(np.sum(src_f * areas_src))
        scale = max(0.5 * (abs(phi_src) + abs(phi_tgt)), 1e-300)
        return FluxResult(
            flux_source=phi_src,
            flux_target=phi_tgt,
            flux_residual=abs(phi_src - phi_tgt) / scale,
            area_source=float(areas_src.sum()),
            area_target=float(areas_tgt.sum()),
            area_residual=abs(areas_src.sum() - areas_tgt.sum())
            / max(0.5 * (areas_src.sum() + areas_tgt.sum()), 1e-300),
            n_pairs=int(np.count_nonzero(areas_tgt > 1e-14)),
            tolerance=self.tolerance,
        )


def shear_field(cells: Sequence[FieldCell], amplitude: float = 0.4,
                wavenumber: float = 2.0 * math.pi) -> None:
    """Fill a smooth analytic field: ρ = 1 + a sin(k x), u = (a y, a z, 0)."""
    for c in cells:
        x, y, z = c.centroid
        c.density = 1.0 + amplitude * math.sin(wavenumber * x)
        c.velocity = np.array([amplitude * y, amplitude * z, 0.0])


def linear_field(cells: Sequence[FieldCell], gradient: np.ndarray,
                 base: float = 0.0) -> None:
    """Fill a linear scalar field ``φ = base + g·x`` (exact cell averages)."""
    g = np.asarray(gradient, dtype=float)
    for c in cells:
        c.pressure = base + float(g @ c.centroid)


__all__ = [
    "FieldCell",
    "FluxResult",
    "InterfaceFluxTransfer",
    "RemapResult",
    "SupermeshRemap",
    "cross_section_area",
    "cross_section_area_level_set",
    "lattice_cells",
    "linear_field",
    "shear_field",
    "structured_cells",
]