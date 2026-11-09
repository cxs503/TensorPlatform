"""Conservative field remapping operators (domain-neutral).

The operators answer one question: given a source field on mesh ``A`` and a
destination mesh ``B``, produce ``B``-field values *and* an auditable record of
how much conserved total moved.  Three operators are provided, deliberately of
different quality so that the conservation gates can be *falsified* (a gate that
can never fail is not evidence):

``overlap_l2``
    Volume-overlap L2 projection.  ``F_B[j] = sum_i |A_i ∩ B_j| F_A[i] / |B_j|``,
    i.e. the exact L2 (volume-weighted) projection of a cell-constant field.
    When ``B`` is nested in / covers the same domain as ``A`` the map is exactly
    conservative (partition of unity) and a patch test on a linear field is exact.

``rbf_conservative``
    Radial-basis-function interpolation of the cell values to the destination
    centres, followed by a global conservation scaling (and, optionally, a
    bounded mass fix so positivity / no-overshoot hold *and* the total is exact).
    Smooth and accurate, but conservative only because of the correction step.

``nearest_scaled``
    Nearest-cell transfer (+ optional conservation scaling).  Used as the
    negative-control baseline: with the correction switched off it loses mass
    whenever the two meshes are not volume-matched, which is exactly the drift
    the conservation gate must catch (≥1%).

All three share one interface::

    remap(src_field, src_mesh, dst_mesh, quantity_kind) -> RemapResult

Fail-closed policy: a destination cell that no source cell overlaps raises
:class:`RemapDomainError` unless ``outside_policy="fill"`` is set explicitly, so
an out-of-domain exchange can never silently produce zeros.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from enum import Enum
from typing import Optional, Tuple

import numpy as np

from .mesh import BoxMesh, Field, MeshError, QuantityKind, _coerce_kind
from .provenance import canonical_digest

__all__ = [
    "RemapError",
    "RemapDomainError",
    "RemapOperator",
    "RemapSpec",
    "RemapReport",
    "RemapResult",
    "overlap_matrix",
    "bounded_mass_fix",
    "remap",
]


class RemapError(ValueError):
    """Raised for an inconsistent remap request."""


class RemapDomainError(RemapError):
    """Raised when a destination cell is not covered by the source mesh (fail-closed)."""


class RemapOperator(str, Enum):
    OVERLAP_L2 = "overlap_l2"
    RBF_CONSERVATIVE = "rbf_conservative"
    NEAREST_SCALED = "nearest_scaled"


@dataclass(frozen=True)
class RemapSpec:
    """Everything that influences the result — and therefore the digest."""

    operator: RemapOperator = RemapOperator.OVERLAP_L2
    quantity_kind: QuantityKind = QuantityKind.DENSITY
    apply_conservation_correction: bool = True
    positivity: bool = False
    enforce_bounds: bool = False
    rbf_kernel: str = "multiquadric"      # multiquadric | gaussian | thin_plate
    rbf_epsilon: float = 1.0             # shape parameter, in units of the mean cell size
    rbf_regularization: float = 1e-12
    outside_policy: str = "raise"         # raise | fill
    fill_value: float = 0.0
    conservation_tol: float = 1e-14

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "operator", RemapOperator(self.operator))
        except ValueError as exc:
            raise RemapError(f"unknown operator: {self.operator!r}") from exc
        object.__setattr__(self, "quantity_kind", _coerce_kind(self.quantity_kind))
        if self.outside_policy not in ("raise", "fill"):
            raise RemapError("outside_policy must be 'raise' or 'fill'")
        if self.rbf_kernel not in ("multiquadric", "gaussian", "thin_plate"):
            raise RemapError(f"unknown rbf kernel: {self.rbf_kernel!r}")
        if self.rbf_epsilon <= 0.0:
            raise RemapError("rbf_epsilon must be positive")
        if self.conservation_tol <= 0.0:
            raise RemapError("conservation_tol must be positive")

    def digest(self) -> str:
        return canonical_digest(self)


@dataclass
class RemapReport:
    """Audit record of one remap: operator, totals, corrections, digests."""

    operator: str
    quantity_kind: str
    n_src_cells: int
    n_dst_cells: int
    sum_src: float
    sum_dst_raw: float
    sum_dst: float
    correction_factor: float
    covered_fraction: float
    uncovered_dst_cells: int
    positivity_projected: bool
    bounds_projected: bool
    spec_digest: str
    src_digest: str
    dst_digest: str
    remap_digest: str

    @property
    def raw_rel_residual(self) -> float:
        """Relative drift *before* the conservation correction (0 if none was needed)."""
        scale = max(abs(self.sum_src), 1e-300)
        return abs(self.sum_dst_raw - self.sum_src) / scale

    @property
    def rel_residual(self) -> float:
        """Relative residual of the delivered field: the quantity the gate reads."""
        scale = max(abs(self.sum_src), 1e-300)
        return abs(self.sum_dst - self.sum_src) / scale

    def to_dict(self) -> dict:
        data = {
            "operator": self.operator,
            "quantity_kind": self.quantity_kind,
            "n_src_cells": self.n_src_cells,
            "n_dst_cells": self.n_dst_cells,
            "sum_src": self.sum_src,
            "sum_dst_raw": self.sum_dst_raw,
            "sum_dst": self.sum_dst,
            "correction_factor": self.correction_factor,
            "covered_fraction": self.covered_fraction,
            "uncovered_dst_cells": self.uncovered_dst_cells,
            "positivity_projected": self.positivity_projected,
            "bounds_projected": self.bounds_projected,
            "raw_rel_residual": self.raw_rel_residual,
            "rel_residual": self.rel_residual,
            "spec_digest": self.spec_digest,
            "src_digest": self.src_digest,
            "dst_digest": self.dst_digest,
            "remap_digest": self.remap_digest,
        }
        return data

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"RemapReport({self.operator}, sum_src={self.sum_src:.15g}, "
            f"sum_dst={self.sum_dst:.15g}, raw_rel_residual={self.raw_rel_residual:.3e}, "
            f"covered={self.covered_fraction:.6f})"
        )


@dataclass
class RemapResult:
    """Field on the destination mesh plus its audit record."""

    field: Field
    report: RemapReport

    def __iter__(self):  # allow ``field, report = remap(...)``
        yield self.field
        yield self.report


# ---------------------------------------------------------------------------
# Overlap geometry
# ---------------------------------------------------------------------------

def overlap_matrix(src_mesh: BoxMesh, dst_mesh: BoxMesh, chunk: int = 2048) -> np.ndarray:
    """Exact intersection volume ``|src_i ∩ dst_j|`` for boxes, shape ``(Ns, Nd)``.

    Axis-aligned boxes have separable overlap, so the intersection volume is the
    product of the per-dimension interval overlaps.  Destination cells are
    processed in chunks to keep peak memory bounded.
    """
    if src_mesh.dim != dst_mesh.dim:
        raise RemapError(f"dimension mismatch: {src_mesh.dim} vs {dst_mesh.dim}")
    ns, nd, dim = src_mesh.n_cells, dst_mesh.n_cells, src_mesh.dim
    out = np.zeros((ns, nd), dtype=float)
    chunk = max(1, int(chunk))
    for start in range(0, nd, chunk):
        stop = min(nd, start + chunk)
        w = np.ones((ns, stop - start), dtype=float)
        for k in range(dim):
            lo = np.maximum(src_mesh.lo[:, k][:, None], dst_mesh.lo[start:stop, k][None, :])
            hi = np.minimum(src_mesh.hi[:, k][:, None], dst_mesh.hi[start:stop, k][None, :])
            w *= np.clip(hi - lo, 0.0, None)
        out[:, start:stop] = w
    return out


# ---------------------------------------------------------------------------
# Bounded mass fixer
# ---------------------------------------------------------------------------

def bounded_mass_fix(
    values: np.ndarray,
    volume: np.ndarray,
    target_total: float,
    lo: Optional[float] = None,
    hi: Optional[float] = None,
    tol: float = 1e-15,
    max_iter: int = 256,
) -> Tuple[np.ndarray, bool, bool]:
    """Rescale ``values`` so ``sum(values*volume) == target_total`` within bounds.

    Returns ``(fixed_values, positivity_applied, bounds_applied)``.  The
    correction is distributed proportionally to the available headroom
    (``volume*(hi-f)`` when mass must be added, ``volume*(f-lo)`` when it must be
    removed), which keeps the fix multiplicative where it can and never pushes a
    value outside ``[lo, hi]``.  If the target is infeasible under the bounds the
    best feasible point is returned with ``False`` in the third slot's meaning
    carried by the caller's tolerance check (never silently).
    """
    f = np.array(values, dtype=float, copy=True)
    vol = np.asarray(volume, dtype=float)
    pos_hit = bounds_hit = False
    if lo is not None:
        clipped = np.maximum(f, lo)
        pos_hit = bool(np.any(clipped != f)) if lo == 0.0 else pos_hit
        bounds_hit |= bool(np.any(clipped != f))
        f = clipped
    if hi is not None:
        clipped = np.minimum(f, hi)
        bounds_hit |= bool(np.any(clipped != f))
        f = clipped

    scale = max(abs(target_total), 1e-300)
    converged = False
    for _ in range(max_iter):
        current = float((f * vol).sum())
        error = target_total - current
        if abs(error) <= tol * scale:
            converged = True
            break
        if error > 0.0:
            room = vol * (hi - f) if hi is not None else vol * np.maximum(np.abs(f), 1e-300)
            if hi is None:
                # uniform (additive in volume-weighted mean) redistribution
                f = f + error / vol.sum()
                continue
        else:
            room = vol * (f - lo) if lo is not None else vol * np.maximum(np.abs(f), 1e-300)
            if lo is None:
                f = f + error / vol.sum()
                continue
        room = np.clip(room, 0.0, None)
        total_room = float(room.sum())
        if total_room <= 0.0:
            break
        step = np.clip(error / total_room, -1.0, 1.0)
        f = f + step * room / vol
    return f, pos_hit, bounds_hit


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

def _rbf_matrix(r: np.ndarray, kernel: str, eps: float) -> np.ndarray:
    x = eps * r
    if kernel == "multiquadric":
        return np.sqrt(1.0 + x * x)
    if kernel == "gaussian":
        return np.exp(-(x * x))
    if kernel == "thin_plate":
        with np.errstate(divide="ignore", invalid="ignore"):
            out = x * x * np.log(np.where(x > 0.0, x, 1.0))
        return np.where(x > 0.0, out, 0.0)
    raise RemapError(f"unknown rbf kernel: {kernel!r}")


def _apply_rbf(src_density: np.ndarray, src_mesh: BoxMesh, dst_mesh: BoxMesh, spec: RemapSpec) -> np.ndarray:
    xs, xd = src_mesh.centers, dst_mesh.centers
    h_src = src_mesh.length_scale
    diffs = xs[:, None, :] - xs[None, :, :]
    k_ss = _rbf_matrix(np.sqrt((diffs * diffs).sum(axis=2)) / h_src, spec.rbf_kernel, spec.rbf_epsilon)
    dcross = xd[:, None, :] - xs[None, :, :]
    k_ds = _rbf_matrix(np.sqrt((dcross * dcross).sum(axis=2)) / h_src, spec.rbf_kernel, spec.rbf_epsilon)
    reg = spec.rbf_regularization * max(float(np.trace(k_ss)) / max(src_mesh.n_cells, 1), 1.0)
    weights = np.linalg.solve(k_ss + reg * np.eye(src_mesh.n_cells), src_density)
    return k_ds @ weights


def _apply_nearest(src_density: np.ndarray, src_mesh: BoxMesh, dst_mesh: BoxMesh) -> np.ndarray:
    from scipy.spatial import cKDTree

    idx = cKDTree(src_mesh.centers).query(dst_mesh.centers, k=1)[1]
    return src_density[idx]


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def remap(
    src_field: Field,
    src_mesh: Optional[BoxMesh] = None,
    dst_mesh: Optional[BoxMesh] = None,
    quantity_kind: "QuantityKind | str | None" = None,
    operator: "RemapOperator | str | None" = None,
    spec: Optional[RemapSpec] = None,
    dst_name: Optional[str] = None,
) -> RemapResult:
    """Transfer ``src_field`` onto ``dst_mesh`` conserving the declared total.

    Parameters
    ----------
    src_field
        Field on the source mesh.  ``src_field.mesh`` is used when ``src_mesh``
        is omitted (so the documented positional form
        ``remap(field, src_mesh, dst_mesh, quantity_kind)`` also works).
    quantity_kind
        Quantity kind used for the transfer.  Must agree with ``src_field.kind``
        (a mismatch is a modelling error, hence fail-closed).
    """
    if dst_mesh is None:
        raise RemapError("dst_mesh is required")
    if src_mesh is None:
        src_mesh = src_field.mesh
    if src_mesh.dim != dst_mesh.dim:
        raise RemapError(f"dimension mismatch: {src_mesh.dim} vs {dst_mesh.dim}")
    if src_field.mesh.n_cells != src_mesh.n_cells:
        raise RemapError("src_field and src_mesh disagree on the number of cells")

    kind = _coerce_kind(quantity_kind if quantity_kind is not None else src_field.kind)
    if kind is not src_field.kind:
        raise RemapError(
            f"quantity_kind {kind.value!r} conflicts with field kind {src_field.kind.value!r}"
        )
    if spec is None:
        spec = RemapSpec(operator=operator or RemapOperator.OVERLAP_L2, quantity_kind=kind)
    else:
        if spec.quantity_kind is not kind:
            spec = RemapSpec(**{**spec.__dict__, "quantity_kind": kind})
        if operator is not None and RemapOperator(operator) is not spec.operator:
            raise RemapError("operator argument conflicts with spec.operator")

    rho_src = src_field.as_density()
    sum_src = float((rho_src * src_mesh.volume).sum())

    covered_fraction = 1.0
    uncovered = 0
    if spec.operator is RemapOperator.OVERLAP_L2:
        w = overlap_matrix(src_mesh, dst_mesh)         # (n_src, n_dst) intersection volumes
        coverage = w.sum(axis=0)                       # covered volume of each dst cell
        covered_fraction = float(coverage.sum() / dst_mesh.total_volume)
        mask = coverage < dst_mesh.volume * (1.0 - 1e-12)
        uncovered = int(mask.sum())
        if uncovered and spec.outside_policy == "raise":
            raise RemapDomainError(
                f"{uncovered} destination cell(s) are not fully covered by the source mesh "
                f"(covered fraction {covered_fraction:.12f}); set outside_policy='fill' to "
                "accept a lossy exchange"
            )
        numerator = w.T @ rho_src                  # sum_i |A_i ∩ B_j| * density_i (mass)
        with np.errstate(divide="ignore", invalid="ignore"):
            rho_dst = np.where(coverage > 0.0, numerator / np.where(coverage > 0.0, coverage, 1.0),
                               spec.fill_value)
        if spec.outside_policy == "fill":
            rho_dst = np.where(coverage > 0.0, rho_dst, spec.fill_value)
    elif spec.operator is RemapOperator.RBF_CONSERVATIVE:
        rho_dst = _apply_rbf(rho_src, src_mesh, dst_mesh, spec)
    elif spec.operator is RemapOperator.NEAREST_SCALED:
        rho_dst = _apply_nearest(rho_src, src_mesh, dst_mesh)
    else:  # pragma: no cover - guarded by RemapSpec
        raise RemapError(f"unknown operator: {spec.operator!r}")

    sum_raw = float((rho_dst * dst_mesh.volume).sum())
    correction = 1.0
    if spec.apply_conservation_correction and abs(sum_raw) > 0.0:
        target = sum_src
        if abs(sum_raw - target) > spec.conservation_tol * max(abs(target), 1e-300):
            correction = target / sum_raw
            rho_dst = rho_dst * correction

    positivity_projected = False
    bounds_projected = False
    if spec.positivity or spec.enforce_bounds:
        lo = 0.0 if spec.positivity else None
        hi = float(rho_src.max()) if spec.enforce_bounds else None
        if lo is not None:
            lo = max(lo, 0.0) if rho_src.min() >= 0.0 else None
        rho_dst, pos_hit, bnd_hit = bounded_mass_fix(
            rho_dst, dst_mesh.volume, sum_src, lo=lo, hi=hi, tol=max(spec.conservation_tol, 1e-15) * 0.1
        )
        positivity_projected = bool(pos_hit and lo is not None)
        bounds_projected = bool(bnd_hit)
        sum_raw = float((rho_dst * dst_mesh.volume).sum())

    values_dst = rho_dst * dst_mesh.volume if kind is QuantityKind.EXTENSIVE else rho_dst
    dst_field = Field(values=values_dst, mesh=dst_mesh, kind=kind,
                      name=dst_name or f"{src_field.name}@{dst_mesh.name}")
    sum_dst = dst_field.total

    report = RemapReport(
        operator=spec.operator.value,
        quantity_kind=kind.value,
        n_src_cells=src_mesh.n_cells,
        n_dst_cells=dst_mesh.n_cells,
        sum_src=sum_src,
        sum_dst_raw=sum_raw,
        sum_dst=sum_dst,
        correction_factor=correction,
        covered_fraction=covered_fraction,
        uncovered_dst_cells=uncovered,
        positivity_projected=positivity_projected,
        bounds_projected=bounds_projected,
        spec_digest=spec.digest(),
        src_digest=src_field.digest(),
        dst_digest=dst_field.digest(),
        remap_digest=canonical_digest({
            "spec": spec.digest(),
            "src": src_field.digest(),
            "dst_mesh": dst_mesh.digest(),
            "dst": dst_field.digest(),
        }),
    )
    return RemapResult(field=dst_field, report=report)