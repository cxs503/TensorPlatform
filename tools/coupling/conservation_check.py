"""Conservation-residual gates, refinement-order certificates, law contracts.

An *interface operator* is any map that transfers values living on one side of a
shared surface onto the discretisation of the other side.  Saying such an
exchange "conserves and converges" is only meaningful if it can be measured, so
this module turns the statement into machine checks:

``relative_residual``
    ``||F_A - F_B|| / ||F_A||`` for the two sides' interface values (l2 or linf).
``transfer`` / ``reconstruct``
    three conservative one-dimensional operators with documented design orders:
    ``donor_cell`` (1), ``linear_reconstruction`` (2), ``quadratic_projection``
    (3).  Each integrates the reconstruction exactly over the destination
    overlaps, so the zeroth moment is preserved to round-off.
``refinement_order`` / ``order_certificate``
    observed order from a refinement study (log-log fit) against the operator's
    design order; fail-closed when the observed order drops below the gate.
``diffusion_interface_case``
    manufactured two-subdomain steady diffusion fixture with a closed-form
    interface flux: the discrete solve gives each side its own interface flux,
    so accuracy against the analytic flux and two-sided consistency are both
    measurable, together with a jittered-flux negative control.
``sensitivity`` / ``sensitivity_gate``
    analytic ``d(gate)/dp`` against central differences.
``load_contract`` / ``validate_contract`` / ``contract_gate``
    the conservation-law contract (``contracts/conservation_law.schema.json``)
    loaded, validated fail-closed, and turned into a gate record.

Nothing in this module knows what the exchanged quantity physically *is*: the
name travels through the contract and stays opaque here.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .mesh import MeshError

__all__ = [
    "ContractError",
    "DiffusionInterfaceCase",
    "DiffusionInterfaceResult",
    "OrderCertificate",
    "SegmentMesh",
    "cell_averages",
    "contract_gate",
    "diffusion_interface_case",
    "jitter",
    "load_contract",
    "noisy_operator",
    "order_certificate",
    "refinement_order",
    "relative_residual",
    "reconstruct",
    "sensitivity_gate",
    "transfer",
    "validate_contract",
    "OPERATOR_ORDER",
]

#: Design order of each built-in interface operator, used as the default gate.
OPERATOR_ORDER: Dict[str, float] = {
    "donor_cell": 1.0,
    "linear_reconstruction": 2.0,
    "quadratic_projection": 3.0,
}

_GL_NODES, _GL_WEIGHTS = np.polynomial.legendre.leggauss(8)


# ---------------------------------------------------------------------------
# residuals
# ---------------------------------------------------------------------------


def relative_residual(
    field_a: Sequence[float],
    field_b: Sequence[float],
    norm: str = "l2",
) -> float:
    """``||F_A - F_B|| / ||F_A||`` — the interface conservation residual.

    ``norm="l2"`` is the discrete l2 norm of the interface values, ``norm="linf"``
    the maximum norm.  A zero reference side returns ``0.0`` when the difference
    is also zero and ``inf`` otherwise (fail-closed, never a silent pass).
    """
    a = np.asarray(field_a, dtype=float).ravel()
    b = np.asarray(field_b, dtype=float).ravel()
    if a.shape != b.shape:
        raise MeshError(f"residual requires equal shapes, got {a.shape} vs {b.shape}")
    if a.size == 0:
        raise MeshError("residual requires at least one interface value")
    diff = a - b
    if norm == "l2":
        num, den = float(np.linalg.norm(diff)), float(np.linalg.norm(a))
    elif norm == "linf":
        num, den = float(np.max(np.abs(diff))), float(np.max(np.abs(a)))
    else:
        raise ValueError(f"unknown norm: {norm!r}")
    if den == 0.0:
        return 0.0 if num == 0.0 else math.inf
    return num / den


def jitter(values: Sequence[float], amplitude: float = 0.3, seed: int = 0) -> np.ndarray:
    """Deterministic relative jitter — the negative control for a conservation gate."""
    arr = np.asarray(values, dtype=float).ravel()
    if amplitude < 0.0:
        raise ValueError("amplitude must be non-negative")
    rng = np.random.default_rng(seed)
    scale = float(np.max(np.abs(arr))) if arr.size else 0.0
    return arr + amplitude * scale * rng.uniform(-1.0, 1.0, arr.size)


# ---------------------------------------------------------------------------
# one-dimensional tangential meshes and conservative transfer operators
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentMesh:
    """A one-dimensional partition of an interface into ``n`` segments."""

    edges: np.ndarray

    def __post_init__(self) -> None:
        edges = np.asarray(self.edges, dtype=float).ravel()
        if edges.size < 2:
            raise MeshError("a segment mesh needs at least two edges")
        if not np.all(np.isfinite(edges)):
            raise MeshError("segment edges must be finite")
        if np.any(np.diff(edges) <= 0.0):
            raise MeshError("segment edges must be strictly increasing")
        object.__setattr__(self, "edges", edges)

    @classmethod
    def uniform(cls, lo: float, hi: float, n: int) -> "SegmentMesh":
        if n < 1:
            raise MeshError("n must be positive")
        if not hi > lo:
            raise MeshError("hi must exceed lo")
        return cls(np.linspace(float(lo), float(hi), int(n) + 1))

    @property
    def n(self) -> int:
        return int(self.edges.size - 1)

    @property
    def widths(self) -> np.ndarray:
        return np.diff(self.edges)

    @property
    def centers(self) -> np.ndarray:
        return 0.5 * (self.edges[:-1] + self.edges[1:])

    @property
    def total(self) -> float:
        return float(self.edges[-1] - self.edges[0])


def cell_averages(
    sampler: Callable[[np.ndarray], np.ndarray],
    mesh: SegmentMesh,
) -> np.ndarray:
    """Exact (Gauss-Legendre) segment averages of a smooth sampler."""
    lo, hi = mesh.edges[:-1], mesh.edges[1:]
    half = 0.5 * (hi - lo)
    mid = 0.5 * (hi + lo)
    out = np.zeros(mesh.n, dtype=float)
    for node, weight in zip(_GL_NODES, _GL_WEIGHTS):
        out += weight * np.asarray(sampler(mid + half * node), dtype=float)
    return out * 0.5


def _slopes(values: np.ndarray, centers: np.ndarray, widths: np.ndarray) -> np.ndarray:
    """Slopes of the reconstruction, three-point at the two ends.

    The end formulas are the ones that make the *cell averages* of the
    reconstruction over the two neighbouring segments exact, i.e. for a uniform
    grid ``s_0 = (4 f_1 - 3 f_0 - f_2) / (2 h)``.  A plain two-point difference
    would leave an ``O(h^2)`` local error at the ends that contaminates the
    measured order of an otherwise third-order operator.
    """
    n = values.size
    slopes = np.zeros(n, dtype=float)
    if n == 1:
        return slopes
    if n == 2:
        slopes[0] = slopes[-1] = (values[1] - values[0]) / (centers[1] - centers[0])
        return slopes
    slopes[1:-1] = (values[2:] - values[:-2]) / (centers[2:] - centers[:-2])
    slopes[0] = (
        4.0 * values[1] - 3.0 * values[0] - values[2]
    ) / (2.0 * (centers[1] - centers[0]))
    slopes[-1] = (
        3.0 * values[-1] - 4.0 * values[-2] + values[-3]
    ) / (2.0 * (centers[-1] - centers[-2]))
    return slopes


def _curvatures(values: np.ndarray, centers: np.ndarray, widths: np.ndarray) -> np.ndarray:
    """Half the second derivative from second differences, three-point at the ends."""
    n = values.size
    curvature = np.zeros(n, dtype=float)
    if n < 3:
        return curvature
    right = (values[2:] - values[1:-1]) / (centers[2:] - centers[1:-1])
    left = (values[1:-1] - values[:-2]) / (centers[1:-1] - centers[:-2])
    curvature[1:-1] = (right - left) / (centers[2:] - centers[:-2])
    curvature[0] = (values[2] - 2.0 * values[1] + values[0]) / (2.0 * (centers[1] - centers[0]) ** 2)
    curvature[-1] = (
        values[-1] - 2.0 * values[-2] + values[-3]
    ) / (2.0 * (centers[-1] - centers[-2]) ** 2)
    return curvature


def reconstruct(
    values: Sequence[float],
    mesh: SegmentMesh,
    operator: str = "donor_cell",
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-segment polynomial reconstruction ``(mean, slope, curvature)``.

    The reconstruction is ``p(s) = f_i + s_i (x - x_i) + c_i ((x - x_i)^2 - h_i^2 / 12)``
    where ``h_i`` is the segment width.  The ``-h_i^2/12`` term keeps the integral
    over the segment equal to ``f_i h_i``, so the operator is conservative by
    construction while still being third-order accurate for the quadratic case.
    """
    f = np.asarray(values, dtype=float).ravel()
    if f.size != mesh.n:
        raise MeshError(f"expected {mesh.n} segment values, got {f.size}")
    centers, widths = mesh.centers, mesh.widths
    if operator == "donor_cell":
        return f, np.zeros_like(f), np.zeros_like(f)
    if operator not in OPERATOR_ORDER:
        raise ValueError(f"unknown interface operator: {operator!r}")
    slopes = _slopes(f, centers, widths)
    if operator == "linear_reconstruction":
        return f, slopes, np.zeros_like(f)
    return f, slopes, _curvatures(f, centers, widths)


def _overlaps(src: SegmentMesh, dst: SegmentMesh) -> List[Tuple[int, int, float, float]]:
    """All ``(src_index, dst_index, a, b)`` interval overlaps of two partitions."""
    out: List[Tuple[int, int, float, float]] = []
    i = j = 0
    while i < src.n and j < dst.n:
        a = max(src.edges[i], dst.edges[j])
        b = min(src.edges[i + 1], dst.edges[j + 1])
        if b > a:
            out.append((i, j, a, b))
        if src.edges[i + 1] <= dst.edges[j + 1]:
            i += 1
        else:
            j += 1
    return out


def _integral(mean: float, slope: float, curvature: float, xi: float, width: float,
              a: float, b: float) -> float:
    """Integral and antiderivative of the reconstruction over ``[a, b]``."""
    def primitive(x: float) -> float:
        dx = x - xi
        return mean * x + 0.5 * slope * dx * dx + curvature * (
            dx ** 3 / 3.0 - (width ** 2 / 12.0) * x
        )

    return primitive(b) - primitive(a)


def transfer(
    values: Sequence[float],
    src: SegmentMesh,
    dst: SegmentMesh,
    operator: str = "donor_cell",
) -> np.ndarray:
    """Conservative transfer of segment values from ``src`` onto ``dst``.

    Returns destination segment *averages*; the total is preserved to round-off
    for every built-in operator because each overlap integral is computed
    analytically from the reconstruction.
    """
    mean, slope, curvature = reconstruct(values, src, operator)
    acc = np.zeros(dst.n, dtype=float)
    centers, widths = src.centers, src.widths
    for i, j, a, b in _overlaps(src, dst):
        acc[j] += _integral(mean[i], slope[i], curvature[i], centers[i], widths[i], a, b)
    return acc / dst.widths


def noisy_operator(
    amplitude: float = 1e-3,
    seed: int = 0,
    base: str = "quadratic_projection",
) -> Callable[[Sequence[float], SegmentMesh, SegmentMesh], np.ndarray]:
    """Wrap an operator with a fixed-amplitude perturbation.

    The perturbation does not shrink with the mesh, so the residual stagnates and
    the refinement-order gate must fail — the negative control for that gate.
    """

    def _op(values: Sequence[float], src: SegmentMesh, dst: SegmentMesh) -> np.ndarray:
        out = transfer(values, src, dst, base)
        rng = np.random.default_rng(seed)
        scale = float(np.max(np.abs(out))) if out.size else 0.0
        return out + amplitude * scale * rng.uniform(-1.0, 1.0, out.size)

    _op.__name__ = f"noisy_{base}"
    return _op


# ---------------------------------------------------------------------------
# refinement order
# ---------------------------------------------------------------------------


def refinement_order(
    h: Sequence[float],
    residuals: Sequence[float],
    floor: float = 1e-12,
) -> Dict[str, Any]:
    """Observed order of convergence from a refinement study.

    ``order = d ln(R) / d ln(h)`` fitted over the levels whose residual is above
    ``floor``; the per-pair orders are reported as well so a stalled study is
    visible rather than averaged away.
    """
    hh = np.asarray(h, dtype=float)
    rr = np.asarray(residuals, dtype=float)
    if hh.shape != rr.shape:
        raise ValueError("h and residuals must have the same shape")
    keep = np.isfinite(hh) & np.isfinite(rr) & (rr > floor) & (hh > 0.0)
    record: Dict[str, Any] = {
        "h": hh.tolist(),
        "residuals": rr.tolist(),
        "levels_used": int(keep.sum()),
        "pair_orders": [],
        "observed_order": float("nan"),
        "floor": float(floor),
    }
    idx = np.flatnonzero(keep)
    pairs = []
    for k in range(len(idx) - 1):
        i, j = idx[k], idx[k + 1]
        pairs.append(float(math.log(rr[j] / rr[i]) / math.log(hh[j] / hh[i])))
    record["pair_orders"] = pairs
    if idx.size >= 2:
        slope = float(np.polyfit(np.log(hh[idx]), np.log(rr[idx]), 1)[0])
        record["observed_order"] = slope
    return record


@dataclass
class OrderCertificate:
    """Result of a refinement study against an operator's declared design order."""

    operator: str
    h: List[float]
    residuals: List[float]
    observed_order: float
    required_order: float
    design_order: float
    passed: bool
    pair_orders: List[float] = _dc_field(default_factory=list)
    levels: List[int] = _dc_field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operator": self.operator,
            "levels": list(self.levels),
            "h": list(self.h),
            "residuals": list(self.residuals),
            "pair_orders": list(self.pair_orders),
            "observed_order": self.observed_order,
            "required_order": self.required_order,
            "design_order": self.design_order,
            "passed": bool(self.passed),
        }


def order_certificate(
    sampler: Callable[[np.ndarray], np.ndarray],
    operator: str = "donor_cell",
    levels: Sequence[int] = (24, 48, 96, 192),
    ratio: float = 1.5,
    span: Tuple[float, float] = (0.0, 1.0),
    required_order: Optional[float] = None,
    floor: float = 1e-12,
    norm: str = "l2",
) -> OrderCertificate:
    """Measure the observed order of ``operator`` and gate it fail-closed.

    The source mesh holds exact segment averages of ``sampler``; the destination
    mesh has ``round(ratio * n) + 1`` segments (never a multiple, so the two
    partitions are always non-matching) and holds exact averages as well.  The
    residual is the transfer error in the same norm the species call site uses,
    which is exactly what a refinement study of the operator must reproduce.
    """
    design = OPERATOR_ORDER.get(operator)
    if design is None:
        raise ValueError(f"unknown interface operator: {operator!r}")
    need = float(required_order if required_order is not None else 0.9 * design)
    h: List[float] = []
    residuals: List[float] = []
    for n in levels:
        src = SegmentMesh.uniform(span[0], span[1], int(n))
        dst = SegmentMesh.uniform(span[0], span[1], int(round(ratio * int(n))) + 1)
        exact = cell_averages(sampler, dst)
        moved = transfer(cell_averages(sampler, src), src, dst, operator)
        h.append(float(dst.widths.mean()))
        residuals.append(relative_residual(exact, moved, norm=norm))
    record = refinement_order(h, residuals, floor=floor)
    observed = record["observed_order"]
    passed = bool(math.isfinite(observed) and observed >= need)
    return OrderCertificate(
        operator=operator,
        h=h,
        residuals=residuals,
        observed_order=observed,
        required_order=need,
        design_order=float(design),
        passed=passed,
        pair_orders=record["pair_orders"],
        levels=[int(n) for n in levels],
    )


# ---------------------------------------------------------------------------
# manufactured two-subdomain diffusion fixture
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiffusionInterfaceCase:
    """Two subdomains sharing a planar interface, manufactured solution.

    ``phi`` is ``sin(2x) cos(pi y)`` on the left side and
    ``(alpha sin(2x) + beta) cos(pi y)`` on the right, with ``alpha = k_left/k_right``
    and ``beta = sin(1) (1 - alpha)``.  That choice makes the solution continuous
    *and* makes the normal flux continuous across the interface with a jump in
    conductivity, so the interface flux has a closed form
    ``q(y) = -2 k_left cos(1) cos(pi y)``.  Each side then reconstructs the
    interface flux from its own cells only, exactly what a coupling operator has
    to reconcile.
    """

    n_x: int = 96
    n_y: int = 96
    k_left: float = 1.0
    k_right: float = 4.0
    interface: float = 0.5

    def __post_init__(self) -> None:
        if self.n_x < 3 or self.n_y < 3:
            raise ValueError("need at least 3 cells per direction")
        if self.k_left <= 0.0 or self.k_right <= 0.0:
            raise ValueError("conductivities must be positive")
        if not 0.0 < self.interface < 1.0:
            raise ValueError("interface must lie strictly inside the domain")

    @property
    def alpha(self) -> float:
        return self.k_left / self.k_right

    @property
    def beta(self) -> float:
        return math.sin(2.0 * self.interface) * (1.0 - self.alpha)

    def phi(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        left = np.sin(2.0 * x) * np.cos(np.pi * y)
        right = (self.alpha * np.sin(2.0 * x) + self.beta) * np.cos(np.pi * y)
        return np.where(x < self.interface, left, right)

    def source(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """``-div(k grad phi)`` in closed form (per side)."""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        left = self.k_left * (4.0 + np.pi ** 2) * np.sin(2.0 * x) * np.cos(np.pi * y)
        right = self.k_right * (
            (4.0 + np.pi ** 2) * self.alpha * np.sin(2.0 * x)
            + np.pi ** 2 * self.beta
        ) * np.cos(np.pi * y)
        return np.where(x < self.interface, left, right)

    def flux(self, y: np.ndarray) -> np.ndarray:
        """Closed-form normal flux through the interface."""
        return -2.0 * self.k_left * math.cos(2.0 * self.interface) * np.cos(np.pi * np.asarray(y, dtype=float))

    def _conductivity(self, x: np.ndarray) -> np.ndarray:
        return np.where(np.asarray(x, dtype=float) < self.interface, self.k_left, self.k_right)

    def assemble(self):
        """Assemble the global finite-volume system ``(matrix, rhs, xc, yc, nx_l, ny)``.

        Both subdomains share one matrix: the interface face carries a single
        harmonic-mean conductance, so the discrete solution is the *coupled*
        solution.  Exposed separately so a consistency probe can evaluate the
        truncation residual of any candidate field against the same operator.

        Dirichlet walls use the three-point derivative at the wall
        (``(-8/3 f_w + 3 f_0 - 1/3 f_1) / h``), because the half-cell form is only
        first-order accurate in the *flux* and a first-order wall would cap the
        measured interface order at one no matter how good the operator is.
        """
        from scipy.sparse import coo_matrix

        nx_l = nx_r = int(self.n_x)
        ny = int(self.n_y)
        n_tot_x = nx_l + nx_r
        n_cells = n_tot_x * ny
        dx = self.interface / nx_l
        dy = 1.0 / ny
        xc = np.concatenate([
            (np.arange(nx_l) + 0.5) * dx,
            self.interface + (np.arange(nx_r) + 0.5) * dx,
        ])
        yc = (np.arange(ny) + 0.5) * dy
        xx, yy = np.meshgrid(xc, yc, indexing="ij")
        kx = np.where(np.arange(n_tot_x) < nx_l, self.k_left, self.k_right)
        k_cell = np.repeat(kx, ny)                       # flat index p = i * ny + j
        vol = dx * dy
        rhs = (np.asarray(self.source(xx, yy), dtype=float) * vol).ravel().copy()

        rows: List[np.ndarray] = []
        cols: List[np.ndarray] = []
        vals: List[np.ndarray] = []
        diag = np.zeros(n_cells, dtype=float)

        # -- x-direction pairs (p - ny, p) ---------------------------------
        k_face = k_cell.copy()
        k_harm = 2.0 * self.k_left * self.k_right / (self.k_left + self.k_right)
        k_face[(nx_l - 1) * ny: nx_l * ny] = k_harm      # the whole interface column
        cond_x = k_face[:-ny] * dy / dx
        p = np.arange(ny, n_cells)
        rows += [p - ny, p]
        cols += [p, p - ny]
        vals += [-cond_x, -cond_x]
        diag[p] += cond_x
        diag[p - ny] += cond_x

        # -- y-direction pairs (p, p + 1) ----------------------------------
        cond_y = k_cell * dx / dy
        j = np.tile(np.arange(ny), n_tot_x)
        p = np.arange(n_cells)
        inner = j < ny - 1
        rows += [p[inner], p[inner] + 1]
        cols += [p[inner] + 1, p[inner]]
        vals += [-cond_y[inner], -cond_y[inner]]
        diag[p[inner]] += cond_y[inner]
        diag[p[inner] + 1] += cond_y[inner]

        # -- Dirichlet walls, three-point derivative at the wall ------------
        def _wall(cells: np.ndarray, inner_cells: np.ndarray, k_face_value: np.ndarray,
                  distance: float, area: float, wall_values: np.ndarray) -> None:
            c = k_face_value * area / distance
            diag[cells] += 3.0 * c
            rows.append(cells)
            cols.append(inner_cells)
            vals.append(-c / 3.0)
            rhs[cells] += (8.0 / 3.0) * c * wall_values

        left = np.arange(ny)
        _wall(left, left + ny, np.full(ny, self.k_left), dx, dy,
              self.phi(np.full(ny, 0.0), yc))
        right = (n_tot_x - 1) * ny + np.arange(ny)
        _wall(right, right - ny, np.full(ny, self.k_right), dx, dy,
              self.phi(np.full(ny, 1.0), yc))
        bottom = p[j == 0]
        _wall(bottom, bottom + 1, k_cell[bottom], dy, dx,
              self.phi(xx.ravel()[bottom], np.zeros(bottom.size)))
        top = p[j == ny - 1]
        _wall(top, top - 1, k_cell[top], dy, dx,
              self.phi(xx.ravel()[top], np.full(top.size, 1.0)))

        rows.append(np.arange(n_cells))
        cols.append(np.arange(n_cells))
        vals.append(diag)
        mat = coo_matrix(
            (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
            shape=(n_cells, n_cells),
        ).tocsr()
        return mat, rhs, xc, yc, nx_l, ny

    def solve(self) -> "DiffusionInterfaceResult":
        """Solve the coupled system, then let each side reconstruct the interface flux.

        Each side uses its own three adjacent cells only, which is exactly what a
        coupling operator has to reconcile when it hands the flux across.
        """
        from scipy.sparse.linalg import spsolve

        mat, rhs, xc, yc, nx_l, ny = self.assemble()
        phi = np.asarray(spsolve(mat, rhs), dtype=float).reshape(xc.size, ny)
        dx = self.interface / nx_l
        dy = 1.0 / ny
        xc_l = xc[:nx_l]
        xc_r = xc[nx_l:]

        # interface flux from each side, using its own three adjacent cells only
        # (the points must be given in the same order as the rows they pair with)
        x_star = float(self.interface)
        left_pts = np.array([xc_l[-3], xc_l[-2], xc_l[-1]])
        right_pts = np.array([xc_r[0], xc_r[1], xc_r[2]])
        d_left = _lagrange_derivative(left_pts, x_star)
        d_right = _lagrange_derivative(right_pts, x_star)
        grad_left = d_left @ phi[nx_l - 3: nx_l, :]
        grad_right = d_right @ phi[nx_l: nx_l + 3, :]
        flux_left = -self.k_left * grad_left
        flux_right = -self.k_right * grad_right

        v_left = _lagrange_value(left_pts, x_star)
        v_right = _lagrange_value(right_pts, x_star)
        phi_iface_left = v_left @ phi[nx_l - 3: nx_l, :]
        phi_iface_right = v_right @ phi[nx_l: nx_l + 3, :]

        return DiffusionInterfaceResult(
            case=self,
            y_centers=yc,
            flux_left=flux_left,
            flux_right=flux_right,
            flux_exact=self.flux(yc),
            phi_left=phi[nx_l - 1, :].copy(),
            phi_right=phi[nx_l, :].copy(),
            x_left_cell=float(xc_l[-1]),
            x_right_cell=float(xc_r[0]),
            phi_iface_left=phi_iface_left,
            phi_iface_right=phi_iface_right,
            dx=dx,
            dy=dy,
        )


def _lagrange_derivative(points: np.ndarray, x_star: float) -> np.ndarray:
    """Weights ``w`` with ``sum_i w_i f(x_i) ≈ f'(x_star)`` (quadratic stencil)."""
    pts = np.asarray(points, dtype=float)
    n = pts.size
    weights = np.zeros(n, dtype=float)
    for k in range(n):
        # derivative of the k-th Lagrange basis polynomial at x_star
        total = 0.0
        for m in range(n):
            if m == k:
                continue
            num = 1.0
            for l in range(n):
                if l in (k, m):
                    continue
                num *= (x_star - pts[l]) / (pts[k] - pts[l])
            total += num / (pts[k] - pts[m])
        weights[k] = total
    return weights


def _lagrange_value(points: np.ndarray, x_star: float) -> np.ndarray:
    """Weights ``w`` with ``sum_i w_i f(x_i) ≈ f(x_star)`` (quadratic stencil)."""
    pts = np.asarray(points, dtype=float)
    n = pts.size
    weights = np.zeros(n, dtype=float)
    for k in range(n):
        num = 1.0
        for l in range(n):
            if l == k:
                continue
            num *= (x_star - pts[l]) / (pts[k] - pts[l])
        weights[k] = num
    return weights


@dataclass
class DiffusionInterfaceResult:
    """Discrete interface fluxes of both sides plus the closed-form reference."""

    case: DiffusionInterfaceCase
    y_centers: np.ndarray
    flux_left: np.ndarray
    flux_right: np.ndarray
    flux_exact: np.ndarray
    phi_left: np.ndarray
    phi_right: np.ndarray
    x_left_cell: float
    x_right_cell: float
    phi_iface_left: np.ndarray
    phi_iface_right: np.ndarray
    dx: float
    dy: float

    def residual(self, norm: str = "l2") -> float:
        """``||F_left - F_right|| / ||F_left||`` — two-sided interface consistency."""
        return relative_residual(self.flux_left, self.flux_right, norm=norm)

    def analytic_error(self, side: str = "left", norm: str = "l2") -> float:
        """Relative error of one side's reconstructed flux against the closed form."""
        values = self.flux_left if side == "left" else self.flux_right
        return relative_residual(self.flux_exact, values, norm=norm)

    def jump_error(self, norm: str = "l2") -> float:
        """Relative jump of the interface value reconstructed from each side.

        Both sides are quadratic-reconstructed at the interface, so a consistent
        discrete solution drives this to round-off at second order.
        """
        return relative_residual(self.phi_iface_left, self.phi_iface_right, norm=norm)

    def potential_error(self, norm: str = "l2") -> float:
        """Relative error of the interface value against the manufactured solution."""
        exact = np.broadcast_to(
            self.case.phi(np.array([self.case.interface]), self.y_centers),
            self.phi_iface_left.shape,
        )
        return relative_residual(exact, self.phi_iface_left, norm=norm)

    def jitter_control(self, amplitude: float = 0.3, seed: int = 0) -> float:
        """Negative control: perturbing one side's flux must break the gate."""
        return relative_residual(self.flux_left, jitter(self.flux_right, amplitude, seed))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_x": int(self.case.n_x),
            "n_y": int(self.case.n_y),
            "k_left": float(self.case.k_left),
            "k_right": float(self.case.k_right),
            "dx": float(self.dx),
            "dy": float(self.dy),
            "interface_residual_l2": self.residual("l2"),
            "interface_residual_linf": self.residual("linf"),
            "analytic_error_left": self.analytic_error("left"),
            "analytic_error_right": self.analytic_error("right"),
            "jump_error": self.jump_error(),
            "potential_error": self.potential_error(),
            "jitter_control_residual": self.jitter_control(),
            "flux_left_mean": float(np.mean(self.flux_left)),
            "flux_right_mean": float(np.mean(self.flux_right)),
            "flux_exact_mean": float(np.mean(self.flux_exact)),
        }


def diffusion_interface_case(**kwargs: Any) -> DiffusionInterfaceResult:
    """Convenience wrapper: build the fixture and solve it."""
    return DiffusionInterfaceCase(**kwargs).solve()


# ---------------------------------------------------------------------------
# sensitivity
# ---------------------------------------------------------------------------


def sensitivity_gate(
    analytic: float,
    gate_value: float,
    rtol: float = 0.02,
) -> Dict[str, Any]:
    """Compare an analytic derivative with the value a gate/loop actually returns."""
    scale = max(abs(analytic), abs(gate_value), 1e-300)
    rel = abs(gate_value - analytic) / scale
    return {
        "analytic": float(analytic),
        "gate_value": float(gate_value),
        "relative_error": float(rel),
        "rtol": float(rtol),
        "passed": bool(rel <= rtol),
    }


def central_difference(
    fn: Callable[[float], float],
    p0: float,
    step: Optional[float] = None,
) -> float:
    """Central finite difference of a scalar gate output."""
    h = float(step) if step else max(1e-7, 1e-6 * max(abs(p0), 1.0))
    return float((fn(p0 + h) - fn(p0 - h)) / (2.0 * h))


# ---------------------------------------------------------------------------
# conservation-law contract
# ---------------------------------------------------------------------------

CONTRACT_SCHEMA_VERSION = "conservation-law.v1"
_CONTRACT_REQUIRED = (
    "schema_version",
    "law_id",
    "quantity_kind",
    "interface_operator",
    "residual",
    "refinement_gate",
    "loop_gate",
)
_CONTRACT_ALLOWED = set(_CONTRACT_REQUIRED) | {
    "description",
    "norm",
    "sensitivity",
    "negative_controls",
    "provenance",
}


class ContractError(ValueError):
    """Raised when a conservation-law contract is malformed (fail-closed)."""


def load_contract(path: "str | Path") -> Dict[str, Any]:
    """Read and validate a conservation-law contract from disk."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_contract(raw)
    if errors:
        raise ContractError("; ".join(errors))
    return raw


def validate_contract(contract: Any) -> List[str]:
    """Structural validation of a conservation-law contract.

    Deliberately hand-rolled and strict: the gate must never pass on a document it
    only half understands (unknown keys are rejected, not ignored).
    """
    errors: List[str] = []
    if not isinstance(contract, dict):
        return ["contract must be a JSON object"]

    for key in _CONTRACT_REQUIRED:
        if key not in contract:
            errors.append(f"missing required key: {key}")
    unknown = sorted(set(contract) - _CONTRACT_ALLOWED)
    if unknown:
        errors.append(f"unknown keys: {unknown}")

    if contract.get("schema_version") not in (CONTRACT_SCHEMA_VERSION, None):
        errors.append(f"schema_version must be {CONTRACT_SCHEMA_VERSION!r}")
    law_id = contract.get("law_id")
    if not isinstance(law_id, str) or not (3 <= len(law_id) <= 64):
        errors.append("law_id must be a string of 3..64 characters")
    elif not all(c.islower() or c.isdigit() or c in "._-" for c in law_id) or not law_id[0].isalnum():
        errors.append("law_id must match ^[a-z0-9][a-z0-9._-]*$")
    kind = contract.get("quantity_kind")
    if not isinstance(kind, str) or not kind:
        errors.append("quantity_kind must be a non-empty string")

    norm = contract.get("norm", "l2_relative")
    if norm not in ("l2_relative", "linf_relative"):
        errors.append(f"unknown norm: {norm!r}")

    op = contract.get("interface_operator")
    if not isinstance(op, dict):
        errors.append("interface_operator must be an object")
    else:
        allowed_op = {"kind", "order_class", "stencil"}
        if sorted(set(op) - allowed_op):
            errors.append(f"unknown interface_operator keys: {sorted(set(op) - allowed_op)}")
        if op.get("kind") not in OPERATOR_ORDER:
            errors.append(f"unknown interface operator kind: {op.get('kind')!r}")
        order = op.get("order_class")
        if not isinstance(order, (int, float)) or isinstance(order, bool) or not (0 < order <= 6):
            errors.append("interface_operator.order_class must be in (0, 6]")
        stencil = op.get("stencil")
        if stencil is not None and (not isinstance(stencil, int) or isinstance(stencil, bool)
                                    or not (1 <= stencil <= 9)):
            errors.append("interface_operator.stencil must be an integer in [1, 9]")

    residual = contract.get("residual")
    if not isinstance(residual, dict):
        errors.append("residual must be an object")
    else:
        if sorted(set(residual) - {"relative_tolerance", "analytic_tolerance"}):
            errors.append("unknown residual keys")
        rel = residual.get("relative_tolerance")
        if not isinstance(rel, (int, float)) or isinstance(rel, bool) or not (0 < rel <= 1):
            errors.append("residual.relative_tolerance must be in (0, 1]")
        ana = residual.get("analytic_tolerance")
        if ana is not None and (not isinstance(ana, (int, float)) or isinstance(ana, bool)
                                or not (0 < ana <= 1)):
            errors.append("residual.analytic_tolerance must be in (0, 1]")

    gate = contract.get("refinement_gate")
    if not isinstance(gate, dict):
        errors.append("refinement_gate must be an object")
    else:
        if sorted(set(gate) - {"levels", "ratio", "min_observed_order", "floor"}):
            errors.append("unknown refinement_gate keys")
        levels = gate.get("levels")
        if not isinstance(levels, list) or not (3 <= len(levels) <= 8):
            errors.append("refinement_gate.levels must list 3..8 integers")
        elif any(not isinstance(n, int) or isinstance(n, bool) or not (4 <= n <= 4096) for n in levels):
            errors.append("refinement_gate.levels entries must be integers in [4, 4096]")
        elif levels != sorted(levels):
            errors.append("refinement_gate.levels must be increasing")
        min_order = gate.get("min_observed_order")
        if not isinstance(min_order, (int, float)) or isinstance(min_order, bool) or not (0 <= min_order <= 6):
            errors.append("refinement_gate.min_observed_order must be in [0, 6]")
        ratio = gate.get("ratio", 1.5)
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not (1 < ratio <= 4):
            errors.append("refinement_gate.ratio must be in (1, 4]")
        floor = gate.get("floor", 1e-12)
        if not isinstance(floor, (int, float)) or isinstance(floor, bool) or not (0 < floor < 1):
            errors.append("refinement_gate.floor must be in (0, 1)")

    loop = contract.get("loop_gate")
    if not isinstance(loop, dict):
        errors.append("loop_gate must be an object")
    else:
        if sorted(set(loop) - {"scheme", "relaxation", "tolerance", "max_iterations",
                               "rho_max", "min_iteration_reduction"}):
            errors.append("unknown loop_gate keys")
        if loop.get("scheme", "jacobi") not in ("jacobi", "gauss_seidel"):
            errors.append("loop_gate.scheme must be jacobi or gauss_seidel")
        if loop.get("relaxation", "aitken") not in ("none", "aitken"):
            errors.append("loop_gate.relaxation must be none or aitken")
        tol = loop.get("tolerance")
        if not isinstance(tol, (int, float)) or isinstance(tol, bool) or not (0 < tol <= 1):
            errors.append("loop_gate.tolerance must be in (0, 1]")
        iters = loop.get("max_iterations")
        if not isinstance(iters, int) or isinstance(iters, bool) or not (1 <= iters <= 100000):
            errors.append("loop_gate.max_iterations must be an integer in [1, 100000]")
        rho = loop.get("rho_max", 1.0)
        if not isinstance(rho, (int, float)) or isinstance(rho, bool) or not (0 < rho <= 2):
            errors.append("loop_gate.rho_max must be in (0, 2]")
        red = loop.get("min_iteration_reduction")
        if red is not None and (not isinstance(red, (int, float)) or isinstance(red, bool)
                               or not (0 <= red <= 1)):
            errors.append("loop_gate.min_iteration_reduction must be in [0, 1]")

    sens = contract.get("sensitivity")
    if sens is not None:
        if not isinstance(sens, dict):
            errors.append("sensitivity must be an object")
        else:
            if sorted(set(sens) - {"parameter", "relative_tolerance", "step"}):
                errors.append("unknown sensitivity keys")
            if not isinstance(sens.get("parameter"), str) or not sens.get("parameter"):
                errors.append("sensitivity.parameter must be a non-empty string")
            rtol = sens.get("relative_tolerance")
            if not isinstance(rtol, (int, float)) or isinstance(rtol, bool) or not (0 < rtol <= 1):
                errors.append("sensitivity.relative_tolerance must be in (0, 1]")

    controls = contract.get("negative_controls")
    if controls is not None:
        if not isinstance(controls, list):
            errors.append("negative_controls must be a list")
        else:
            for k, item in enumerate(controls):
                if not isinstance(item, dict):
                    errors.append(f"negative_controls[{k}] must be an object")
                    continue
                if sorted(set(item) - {"name", "kind", "must_fail", "amplitude", "seed"}):
                    errors.append(f"negative_controls[{k}] has unknown keys")
                if item.get("kind") not in ("jittered_flux", "noisy_operator", "divergent_loop"):
                    errors.append(f"negative_controls[{k}].kind is unknown")
                if not isinstance(item.get("name"), str) or not item.get("name"):
                    errors.append(f"negative_controls[{k}].name must be a non-empty string")

    return errors


def contract_gate(
    contract: Dict[str, Any],
    field_a: Sequence[float],
    field_b: Sequence[float],
) -> Dict[str, Any]:
    """Apply the contract's residual tolerance to one exchange record."""
    errors = validate_contract(contract)
    if errors:
        raise ContractError("; ".join(errors))
    norm = contract.get("norm", "l2_relative")
    norm_key = "linf" if norm == "linf_relative" else "l2"
    residual = relative_residual(field_a, field_b, norm=norm_key)
    tol = float(contract["residual"]["relative_tolerance"])
    record: Dict[str, Any] = {
        "law_id": contract["law_id"],
        "quantity_kind": contract["quantity_kind"],
        "norm": norm,
        "residual": residual,
        "relative_tolerance": tol,
        "passed": bool(residual <= tol),
    }
    analytic_tol = contract["residual"].get("analytic_tolerance")
    if analytic_tol is not None and contract.get("_analytic_reference") is not None:
        record["analytic_error"] = relative_residual(
            contract["_analytic_reference"], field_b, norm=norm_key
        )
        record["analytic_tolerance"] = float(analytic_tol)
        record["passed"] = bool(record["passed"] and record["analytic_error"] <= analytic_tol)
    return record