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
from typing import Optional, Tuple, List

import numpy as np

from .mesh import BoxMesh, Field, MeshError, QuantityKind, _coerce_kind
from .precision import (
    MomentReport,
    VectorField,
    apply_linear_reconstruction,
    area_overlap_matrix,
    check_flux_dim,
    law_for,
    measure_of,
    moment_projection,
)
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
    OVERLAP_L2 = "overlap_l2"              # first-order, piecewise-constant
    OVERLAP_P1 = "overlap_p1"              # second-order, piecewise-linear reconstruction
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
    moment_exact: bool = False            # also conserve sum(r * F) (force + torque)
    face_normal: int = -1                 # surface normal of a flux quantity

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
        law = law_for(self.quantity_kind)
        if self.moment_exact and not law.conserved:
            # a precision-law quantity has no sum to conserve, so a moment
            # constraint on it is a modelling contradiction: refuse explicitly
            raise RemapError(
                f"moment_exact requested for kind {self.quantity_kind.value!r}, whose "
                "admissible law is precision preservation, not a sum constraint"
            )
        if self.moment_exact and not self.apply_conservation_correction:
            raise RemapError(
                "moment_exact requires apply_conservation_correction: the projection is the "
                "correction (turning the correction off leaves the moments unconserved)"
            )

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
    law: str = "density"
    sum_conserved: bool = True
    moment_applied: bool = False
    moment_total_residual: float = float("nan")
    moment_first_residual: float = float("nan")
    moment_condition: float = float("nan")
    moment_broken_by_bounds: bool = False

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

    law = law_for(kind)
    if kind is QuantityKind.FLUX:
        if int(spec.face_normal) != int(src_field.face_normal):
            raise RemapError(
                "face_normal disagrees between the field and the spec "
                f"({src_field.face_normal} vs {spec.face_normal})"
            )
        check_flux_dim(src_mesh, spec.face_normal)
    # operator space is *intensive*; its 0th-moment weight is the geometric
    # transfer measure (volume, or patch area for a surface quantity), which is
    # the measure every value is reconstructed against below
    if kind is QuantityKind.FLUX:
        mea_src = measure_of(src_mesh, kind, spec.face_normal)
        mea_dst = measure_of(dst_mesh, kind, spec.face_normal)
    else:
        mea_src = src_mesh.volume
        mea_dst = dst_mesh.volume

    rho_src = src_field.as_density()
    sum_src = float((rho_src * mea_src).sum())
    # first moment is a statement *about a point*: both sides must use the same
    # reference, so the reference is fixed here (destination centroid) and used
    # for the target as well as inside the projection
    moment_ref = (mea_dst[:, None] * dst_mesh.centers).sum(axis=0) / float(mea_dst.sum())
    moment_src = np.asarray(
        ((src_mesh.centers - moment_ref) * (rho_src * mea_src)[:, None]).sum(axis=0)
    )

    covered_fraction = 1.0
    uncovered = 0
    if spec.operator is RemapOperator.OVERLAP_L2:
        if kind is QuantityKind.FLUX:
            w = area_overlap_matrix(src_mesh, dst_mesh, spec.face_normal)  # (n_src, n_dst) areas
        else:
            w = overlap_matrix(src_mesh, dst_mesh)                         # (n_src, n_dst) volumes
        coverage = w.sum(axis=0)                       # covered measure of each dst cell
        covered_fraction = float(coverage.sum() / mea_dst.sum())
        mask = coverage < mea_dst * (1.0 - 1e-12)
        uncovered = int(mask.sum())
        if uncovered and spec.outside_policy == "raise":
            raise RemapDomainError(
                f"{uncovered} destination cell(s) are not fully covered by the source mesh "
                f"(covered fraction {covered_fraction:.12f}); set outside_policy='fill' to "
                "accept a lossy exchange"
            )
        numerator = w.T @ rho_src                  # sum_i |A_i ∩ B_j| * intensive_i
        with np.errstate(divide="ignore", invalid="ignore"):
            rho_dst = np.where(coverage > 0.0, numerator / np.where(coverage > 0.0, coverage, 1.0),
                               spec.fill_value)
        if spec.outside_policy == "fill":
            rho_dst = np.where(coverage > 0.0, rho_dst, spec.fill_value)
    elif spec.operator is RemapOperator.OVERLAP_P1:
        if kind is QuantityKind.FLUX:
            raise RemapError(
                "overlap_p1 reconstructs a volume field; a surface quantity needs the "
                "area-overlap operator (use overlap_l2 for flux, or exchange a density)"
            )
        rho_dst, coverage = apply_linear_reconstruction(rho_src, src_mesh, dst_mesh)
        covered_fraction = float(coverage.sum() / mea_dst.sum())
        mask = coverage < mea_dst * (1.0 - 1e-12)
        uncovered = int(mask.sum())
        if uncovered and spec.outside_policy == "raise":
            raise RemapDomainError(
                f"{uncovered} destination cell(s) are not fully covered by the source mesh "
                f"(covered fraction {covered_fraction:.12f}); set outside_policy='fill' to "
                "accept a lossy exchange"
            )
        if spec.outside_policy == "fill":
            rho_dst = np.where(coverage > 0.0, rho_dst, spec.fill_value)
    elif spec.operator is RemapOperator.RBF_CONSERVATIVE:
        rho_dst = _apply_rbf(rho_src, src_mesh, dst_mesh, spec)
    elif spec.operator is RemapOperator.NEAREST_SCALED:
        rho_dst = _apply_nearest(rho_src, src_mesh, dst_mesh)
    else:  # pragma: no cover - guarded by RemapSpec
        raise RemapError(f"unknown operator: {spec.operator!r}")

    sum_raw = float((rho_dst * mea_dst).sum())
    correction = 1.0
    moment_report = None
    if law.conserved and spec.apply_conservation_correction and spec.moment_exact:
        # conservation projection: zeroth *and* first moment, in closed form
        rho_dst, moment_report = moment_projection(
            rho_dst, mea_dst, dst_mesh.centers, sum_src, moment_src, reference=moment_ref
        )
        correction = sum_src / sum_raw if abs(sum_raw) > 0.0 else 1.0
    elif law.conserved and spec.apply_conservation_correction and abs(sum_raw) > 0.0:
        target = sum_src
        if abs(sum_raw - target) > spec.conservation_tol * max(abs(target), 1e-300):
            correction = target / sum_raw
            rho_dst = rho_dst * correction

    positivity_projected = False
    bounds_projected = False
    moment_broken = False
    if law.conserved and (spec.positivity or spec.enforce_bounds):
        lo = 0.0 if spec.positivity else None
        hi = float(rho_src.max()) if spec.enforce_bounds else None
        if lo is not None:
            lo = max(lo, 0.0) if rho_src.min() >= 0.0 else None
        rho_dst, pos_hit, bnd_hit = bounded_mass_fix(
            rho_dst, mea_dst, sum_src, lo=lo, hi=hi, tol=max(spec.conservation_tol, 1e-15) * 0.1
        )
        positivity_projected = bool(pos_hit and lo is not None)
        bounds_projected = bool(bnd_hit)
        sum_raw = float((rho_dst * mea_dst).sum())

    # the reported first-moment residual is the one *delivered*: a bound
    # projection runs after the moment projection and may trade the exact first
    # moment for the bound, so the pre-projection number must not be published
    moment_delivered = float("nan")
    if moment_report is not None:
        delivered = np.asarray(
            ((dst_mesh.centers - moment_ref) * (rho_dst * mea_dst)[:, None]).sum(axis=0)
        )
        scale = max(float(np.max(np.abs(moment_src))), 1e-300)
        moment_delivered = float(np.max(np.abs(delivered - moment_src))) / scale
        moment_broken = bool(moment_delivered > 1e-12)
        moment_report = MomentReport(
            total_residual=moment_report.total_residual,
            first_moment_residual=moment_delivered,
            offset=moment_report.offset,
            slope=moment_report.slope,
            condition_number=moment_report.condition_number,
            applied=moment_report.applied,
        )

    values_dst = rho_dst * mea_dst if kind is QuantityKind.EXTENSIVE else rho_dst
    dst_field = Field(values=values_dst, mesh=dst_mesh, kind=kind,
                      name=dst_name or f"{src_field.name}@{dst_mesh.name}",
                      face_normal=spec.face_normal)
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
        law=law.name,
        sum_conserved=law.conserved,
        moment_applied=bool(moment_report is not None),
        moment_total_residual=(moment_report.total_residual if moment_report else float("nan")),
        moment_first_residual=(moment_report.first_moment_residual if moment_report else float("nan")),
        moment_condition=(moment_report.condition_number if moment_report else float("nan")),
        moment_broken_by_bounds=moment_broken,
    )
    return RemapResult(field=dst_field, report=report)

@dataclass
class VectorRemapResult:
    """Result of a vector exchange: per-component reports plus lumped moments."""

    field: "VectorField"
    reports: List[RemapReport]
    total_residual: float
    first_moment_residual: float
    torque_src: np.ndarray
    torque_dst: np.ndarray
    torque_residual: float
    digest: str

    def to_dict(self) -> dict:
        return {
            "digest": self.digest,
            "total_residual": self.total_residual,
            "first_moment_residual": self.first_moment_residual,
            "torque_src": [float(x) for x in self.torque_src],
            "torque_dst": [float(x) for x in self.torque_dst],
            "torque_residual": self.torque_residual,
            "components": [r.to_dict() for r in self.reports],
        }


def remap_vector(
    src_field: "VectorField",
    src_mesh: Optional[BoxMesh] = None,
    dst_mesh: Optional[BoxMesh] = None,
    quantity_kind: "QuantityKind | str | None" = None,
    operator: "RemapOperator | str | None" = None,
    spec: Optional[RemapSpec] = None,
    dst_name: Optional[str] = None,
) -> VectorRemapResult:
    """Exchange a vector quantity, conserving total, first-moment tensor and torque.

    Each component is exchanged with the scalar operator of the declared kind and
    the moment projection is applied per component, so the full first-moment
    tensor ``sum(r (x) F)`` is preserved.  The lumped torque ``sum(r x F)`` is a
    linear combination of tensor entries, hence it is conserved exactly too — the
    property a rotating reference frame coupling needs to avoid injecting spurious
    angular momentum every cycle.
    """
    from .precision import VectorField  # local import: avoid a cycle at module load

    if not isinstance(src_field, VectorField):
        raise RemapError("remap_vector expects a VectorField")
    if src_mesh is None:
        src_mesh = src_field.mesh
    if dst_mesh is None:
        raise RemapError("dst_mesh is required")
    kind = _coerce_kind(quantity_kind if quantity_kind is not None else src_field.kind)
    if spec is None:
        spec = RemapSpec(operator=operator or RemapOperator.OVERLAP_L2, quantity_kind=kind,
                         moment_exact=True)
    elif operator is not None and RemapOperator(operator) is not spec.operator:
        raise RemapError("operator argument conflicts with spec.operator")
    if spec.quantity_kind is not kind:
        spec = RemapSpec(**{**spec.__dict__, "quantity_kind": kind})

    reports: List[RemapReport] = []
    cols = []
    for d, comp in enumerate(src_field.components()):
        if comp.kind is not kind:
            comp = Field(values=comp.values, mesh=comp.mesh, kind=kind,
                         name=comp.name, face_normal=comp.face_normal)
        res = remap(comp, src_mesh, dst_mesh, kind, spec=spec,
                    dst_name=f"{dst_name or src_field.name}[{d}]")
        reports.append(res.report)
        cols.append(res.field.values)
    values = np.stack(cols, axis=1)
    dst_vec = VectorField(values=values, mesh=dst_mesh, kind=kind,
                          name=dst_name or f"{src_field.name}@{dst_mesh.name}",
                          face_normal=src_field.face_normal)

    tot_src = src_field.total
    tot_dst = dst_vec.total
    tot_scale = max(float(np.max(np.abs(tot_src))), 1e-300)
    total_res = float(np.max(np.abs(tot_dst - tot_src))) / tot_scale

    fm_src = src_field.first_moment_tensor()
    fm_dst = dst_vec.first_moment_tensor()
    fm_scale = max(float(np.max(np.abs(fm_src))), 1e-300)
    fm_res = float(np.max(np.abs(fm_dst - fm_src))) / fm_scale

    t_src = src_field.torque()
    t_dst = dst_vec.torque()
    t_scale = max(float(np.max(np.abs(t_src))), 1e-300)
    t_res = float(np.max(np.abs(t_dst - t_src))) / t_scale

    digest = canonical_digest({
        "spec": spec.digest(),
        "src": src_field.digest(),
        "dst_mesh": dst_mesh.digest(),
        "dst": dst_vec.digest(),
    })
    return VectorRemapResult(field=dst_vec, reports=reports, total_residual=total_res,
                             first_moment_residual=fm_res, torque_src=t_src,
                             torque_dst=t_dst, torque_residual=t_res, digest=digest)
