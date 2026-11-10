"""Conservation projections: exact zeroth + first moment, surface measures, vectors.

The overlap operators in :mod:`tools.coupling.remap` conserve the *total* of a
field.  A coupled run additionally needs the **first moment** to be conserved:
if an exchange changes ``sum(r * F)`` it injects a spurious torque into the
coupled system every cycle, and the error accumulates even when every single
exchange looks "conservative" by the total.

The projection implemented here is an affine correction of the destination
intensive field,

    v'_j = v_j + a + b . (r_j - r_ref),

whose ``1 + dim`` coefficients are obtained in closed form from the two
constraints ``sum(m_j v'_j) = T`` and ``sum(r_j m_j v'_j) = M``:

    a = (T - sum(m_j v_j)) / M0
    b = C^{-1} (M - sum(r_j m_j v_j) - a M0 r_ref),   C = sum(m_j d_j d_j^T)

with ``d_j = r_j - r_ref`` and ``r_ref`` the measure-weighted centroid of the
destination cells (so ``sum(m_j d_j) = 0`` and ``a`` decouples from ``b``).
``C`` is the discrete covariance of the destination cells: symmetric positive
definite whenever the cells spread along every axis, hence the system is solved
directly (no iteration) and the residuals sit at round-off level.

For a *vector* quantity the projection is applied per component, which preserves
the full first-moment tensor ``sum(r_i (x) F_i)``; the lumped torque
``sum(r x F)`` is a linear combination of those tensor entries and is therefore
conserved exactly as well.

Nothing in this module knows what the field means physically; the quantity law
enters through :class:`QuantityLaw` rows selected by configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .mesh import BoxMesh, Field, MeshError, QuantityKind, face_areas

__all__ = [
    "QuantityLaw",
    "QUANTITY_LAWS",
    "law_for",
    "MomentReport",
    "moment_projection",
    "first_moment_tensor",
    "torque",
    "measure_of",
    "interval_overlap_length",
    "area_overlap_matrix",
    "VectorField",
    "rigid_motion_field",
    "rigid_motion_patch_test",
    "check_flux_dim",
    "DegenerateMeasureError",
]


class DegenerateMeasureError(ValueError):
    """The destination cells cannot support the requested moment constraints."""


# --------------------------------------------------------------------------- laws
@dataclass(frozen=True)
class QuantityLaw:
    """One row of the exchange-law table.

    ``measure``      per-cell weight turning values into a total
    ``conserved``    whether a sum constraint is admissible at all
    ``first_moment`` whether the exchange must additionally preserve ``sum(r*F)``
    ``positive``     whether non-negativity is part of the admissible law
    ``bounded``      whether destination extremes must stay inside source extremes
    ``signed``       whether values may legitimately change sign
    """

    name: str
    measure: str
    conserved: bool
    first_moment: bool
    positive: bool
    bounded: bool
    signed: bool


QUANTITY_LAWS: Dict[str, QuantityLaw] = {
    "density": QuantityLaw("density", "volume", True, True, True, True, False),
    "flux": QuantityLaw("flux", "surface", True, True, False, False, True),
    "extensive": QuantityLaw("extensive", "unity", True, True, False, False, True),
    "scalar": QuantityLaw("scalar", "volume", False, False, False, False, True),
}


def law_for(kind: "QuantityKind | str") -> QuantityLaw:
    """Exchange law for a quantity kind (fail-closed on an unknown kind)."""
    key = kind.value if isinstance(kind, QuantityKind) else str(kind)
    try:
        return QUANTITY_LAWS[key]
    except KeyError as exc:  # pragma: no cover - guarded by QuantityKind
        raise MeshError(f"no exchange law declared for kind {kind!r}") from exc


def check_flux_dim(mesh: BoxMesh, normal: int = -1) -> None:
    """A surface measure needs at least two dimensions; smaller is ill-posed."""
    if mesh.dim < 2:
        raise DegenerateMeasureError(
            f"flux exchange on a {mesh.dim}-D mesh: a surface patch degenerates to a point, "
            "so an area-conservative law is not defined"
        )
    face_areas(mesh, normal)  # validates the normal index


def measure_of(mesh: BoxMesh, kind: "QuantityKind | str", face_normal: int = -1) -> np.ndarray:
    """Per-cell measure for ``kind`` (volume / surface area / unity)."""
    law = law_for(kind)
    if law.measure == "volume":
        return mesh.volume
    if law.measure == "surface":
        return face_areas(mesh, face_normal)
    return np.ones(mesh.n_cells)


# --------------------------------------------------------------------- moments
@dataclass(frozen=True)
class MomentReport:
    """Diagnostics of one moment projection (all normalised, all dimensionless)."""

    total_residual: float
    first_moment_residual: float
    offset: float
    slope: np.ndarray
    condition_number: float
    applied: bool

    def to_dict(self) -> dict:
        return {
            "total_residual": self.total_residual,
            "first_moment_residual": self.first_moment_residual,
            "offset": self.offset,
            "slope": [float(x) for x in np.asarray(self.slope, dtype=float)],
            "condition_number": self.condition_number,
            "applied": self.applied,
        }


def first_moment_tensor(
    values: np.ndarray,
    centers: np.ndarray,
    measure: np.ndarray,
    reference: Optional[np.ndarray] = None,
) -> np.ndarray:
    """``sum_j r_j * value_j * m_j`` per component, or about ``reference``."""
    r = np.asarray(centers, dtype=float)
    ref = np.zeros(r.shape[1]) if reference is None else np.asarray(reference, dtype=float)
    return ((r - ref) * (np.asarray(values, dtype=float) * measure)[:, None]).sum(axis=0)


def moment_projection(
    values: np.ndarray,
    measure: np.ndarray,
    centers: np.ndarray,
    target_total: float,
    target_first_moment: np.ndarray,
    *,
    reference: Optional[np.ndarray] = None,
    tol: float = 1e-12,
    max_condition: float = 1e12,
) -> Tuple[np.ndarray, MomentReport]:
    """Apply the affine projection that makes total and first moment exact.

    Parameters
    ----------
    values
        Intensive destination values (per unit measure), shape ``(n_cells,)``.
    measure
        Destination per-cell measure, shape ``(n_cells,)``.
    centers
        Destination cell centroids, shape ``(n_cells, dim)``.
    target_total, target_first_moment
        Source-side lumped total and first moment (the discrete quantities the
        coupled system must not change).
    reference
        Point the first moment is reported about; defaults to the measure-weighted
        centroid of the destination cells.

    Returns
    -------
    ``(projected_values, MomentReport)``.  The projection is exact up to
    round-off (``tol`` is the acceptance level of the projection itself, not the
    accuracy of the exchange); ``MomentReport.condition_number`` exposes how well the destination
    cells condition the linear system, and a degenerate configuration raises
    :class:`DegenerateMeasureError` instead of returning a silently wrong field.
    """
    v = np.array(values, dtype=float, copy=True)
    m = np.asarray(measure, dtype=float)
    r = np.asarray(centers, dtype=float)
    if v.shape != m.shape or r.shape[0] != v.size:
        raise ValueError("values, measure and centers disagree on the cell count")

    total0 = float((v * m).sum())
    m0 = float(m.sum())
    if not np.isfinite(m0) or m0 <= 0.0:
        raise DegenerateMeasureError("destination measure is zero or non-finite")
    if reference is None:
        ref = (m[:, None] * r).sum(axis=0) / m0
    else:
        ref = np.asarray(reference, dtype=float)

    d = r - ref
    sd = (m[:, None] * d).sum(axis=0)          # sum_j m_j d_j (0 for a centroid reference)
    cov = (m[:, None] * d).T @ d                # sum_j m_j d_j d_j^T
    # eliminating ``a`` with the total constraint leaves the *centred* covariance,
    # which is what must be non-singular for the constraints to be independent
    cov_c = cov - np.outer(sd, sd) / m0
    cov_scale = cov_c / max(float(np.abs(m).sum()), 1e-300)
    cond = float(np.linalg.cond(cov_scale))
    if not np.isfinite(cond) or cond > max_condition:
        raise DegenerateMeasureError(
            "destination cells do not span every axis (centred covariance condition number "
            f"{cond:.3e} > {max_condition:.0e}): the first-moment constraint is not "
            "separable; exchange on a spanning mesh or supply a non-degenerate destination"
        )

    current_first = first_moment_tensor(v, r, m, reference=ref)
    delta0 = float(target_total) - total0
    rhs = np.asarray(target_first_moment, dtype=float) - current_first - sd * (delta0 / m0)
    b = np.linalg.solve(cov_c, rhs)
    a = (delta0 - float(sd @ b)) / m0
    vprime = v + a + d @ b

    after_total = float((vprime * m).sum())
    after_first = first_moment_tensor(vprime, r, m, reference=ref)
    total_res = abs(after_total - float(target_total)) / max(abs(float(target_total)), 1e-300)
    fm_scale = max(
        float(np.max(np.abs(np.asarray(target_first_moment, dtype=float)))),
        float(np.max(np.abs(current_first))),
        1e-300,
    )
    fm_res = float(np.max(np.abs(after_first - np.asarray(target_first_moment, dtype=float)))) / fm_scale

    applied = bool(abs(a) > 0.0 or np.any(b != 0.0))
    report = MomentReport(
        total_residual=float(total_res),
        first_moment_residual=float(fm_res),
        offset=float(a),
        slope=np.asarray(b, dtype=float),
        condition_number=cond,
        applied=applied,
    )
    if total_res > tol or fm_res > tol:  # pragma: no cover - guarded by the algebra
        raise DegenerateMeasureError(
            f"moment projection failed to converge (total {total_res:.3e}, first {fm_res:.3e})"
        )
    return vprime, report


def torque(
    values: np.ndarray,
    measure: np.ndarray,
    centers: np.ndarray,
    reference: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Lumped torque ``sum_j (r_j - reference) x F_j`` for a vector field."""
    r = np.asarray(centers, dtype=float)
    ref = np.zeros(r.shape[1]) if reference is None else np.asarray(reference, dtype=float)
    f = np.asarray(values, dtype=float)
    if f.ndim == 1:
        raise ValueError("torque needs a vector field (n_cells, dim)")
    moments = (f * np.asarray(measure, dtype=float)[:, None])
    arm = r - ref
    if arm.shape[1] == 2:
        # 2-D: the torque has a single component r_x F_y - r_y F_x
        return np.array([(arm[:, 0] * moments[:, 1] - arm[:, 1] * moments[:, 0]).sum()])
    return np.cross(arm, moments).sum(axis=0)


# --------------------------------------------------------------- surface measure
def interval_overlap_length(lo_a: np.ndarray, hi_a: np.ndarray, lo_b: np.ndarray, hi_b: np.ndarray) -> np.ndarray:
    """Pairwise overlap lengths of two interval sets, shape ``(len(a), len(b))``."""
    lo = np.maximum(np.asarray(lo_a, dtype=float)[:, None], np.asarray(lo_b, dtype=float)[None, :])
    hi = np.minimum(np.asarray(hi_a, dtype=float)[:, None], np.asarray(hi_b, dtype=float)[None, :])
    return np.maximum(hi - lo, 0.0)


def _normal_key(mesh: BoxMesh, axis: int, tol: float = 1e-12) -> List[Tuple[float, float]]:
    """Quantised normal extent identifying the surface a patch belongs to."""
    scale = max(float(np.max(mesh.hi - mesh.lo)), 1e-300)
    q = lambda a: round(float(a) / (tol * scale))
    return [(q(lo), q(hi)) for lo, hi in zip(mesh.lo[:, axis], mesh.hi[:, axis])]


def area_overlap_matrix(
    src: BoxMesh, dst: BoxMesh, face_normal: int = -1
) -> np.ndarray:
    """Surface-patch overlap areas ``|P_i ∩ Q_j|`` for a per-unit-area quantity.

    A flux is defined *on a surface*.  A cell that spans a range in the normal
    direction does not describe a surface, it describes a slab: the same area
    would then be counted once per overlapping slab and the total would inflate
    by the number of slabs.  The only well-posed reading is therefore

    * a patch is the cell's tangential footprint (product of the two tangential
      extents), and
    * two patches exchange **only if they lie on the same surface**, i.e. their
      normal extents coincide to ``1e-12`` relative tolerance.

    The overlap weight is then the tangential intersection area, which tiles
    exactly, so ``sum_i |P_i ∩ Q_j| == |Q_j|`` for a fully covered surface patch
    and the exchange is area-conservative.  Destination patches with no
    coincident source patch stay uncovered, which the caller's
    ``outside_policy`` turns into a fail-closed error.

    Exchanges between slabs (density through a volume) must use
    :func:`~tools.coupling.remap.overlap_matrix` instead.
    """
    check_flux_dim(src, face_normal)
    if src.dim != dst.dim:
        raise MeshError(f"dimension mismatch: {src.dim} vs {dst.dim}")
    dim = src.dim
    axis = dim - 1 if face_normal is None or face_normal < 0 else int(face_normal)
    if not (0 <= axis < dim):
        raise MeshError(f"face_normal {face_normal!r} is out of range for dim={dim}")
    tangential = [k for k in range(dim) if k != axis]

    src_keys = _normal_key(src, axis)
    dst_keys = _normal_key(dst, axis)
    by_key_src: Dict[Tuple[float, float], List[int]] = {}
    for i, key in enumerate(src_keys):
        by_key_src.setdefault(key, []).append(i)

    out = np.zeros((src.n_cells, dst.n_cells), dtype=float)
    for j, key in enumerate(dst_keys):
        rows = by_key_src.get(key)
        if not rows:
            continue  # different surface: cannot exchange, stays uncovered
        rows_arr = np.asarray(rows, dtype=int)
        area = np.ones(rows_arr.size, dtype=float)
        for ax in tangential:
            area *= interval_overlap_length(
                src.lo[rows_arr, ax], src.hi[rows_arr, ax], dst.lo[j : j + 1, ax], dst.hi[j : j + 1, ax]
            )[:, 0]
        out[rows_arr, j] = area
    return out


# ------------------------------------------------------------------ vector fields
@dataclass
class VectorField:
    """A cell-centred vector quantity (``values`` is ``(n_cells, dim)``).

    Kept separate from :class:`~tools.coupling.mesh.Field` so that the scalar
    exchange path stays a single contiguous array; each component is exchanged
    with the scalar law of ``kind`` and the moment projection is applied per
    component, which is what preserves the first-moment tensor and the torque.
    """

    values: np.ndarray
    mesh: BoxMesh
    kind: QuantityKind = QuantityKind.EXTENSIVE
    name: str = "vector-field"
    face_normal: int = -1

    def __post_init__(self) -> None:
        vals = np.ascontiguousarray(np.asarray(self.values, dtype=float))
        if vals.ndim != 2 or vals.shape[0] != self.mesh.n_cells or vals.shape[1] != self.mesh.dim:
            raise MeshError(
                f"vector field {self.name!r} must be (n_cells={self.mesh.n_cells}, "
                f"dim={self.mesh.dim}), got {vals.shape}"
            )
        from .mesh import _coerce_kind

        object.__setattr__(self, "values", vals)
        object.__setattr__(self, "kind", _coerce_kind(self.kind))

    @property
    def measure(self) -> np.ndarray:
        return measure_of(self.mesh, self.kind, self.face_normal)

    @property
    def total(self) -> np.ndarray:
        return (self.values * self.measure[:, None]).sum(axis=0)

    def first_moment_tensor(self, reference: Optional[np.ndarray] = None) -> np.ndarray:
        """``sum_j r_j (x) F_j`` — shape ``(dim, dim)`` (per r-component, per F-component)."""
        return (self.values * self.measure[:, None]).T @ (
            self.mesh.centers - (np.zeros(self.mesh.dim) if reference is None else reference)
        )

    def torque(self, reference: Optional[np.ndarray] = None) -> np.ndarray:
        return torque(self.values, self.measure, self.mesh.centers, reference)

    def components(self) -> List[Field]:
        return [
            Field(values=self.values[:, d], mesh=self.mesh, kind=self.kind,
                  name=f"{self.name}[{d}]", face_normal=self.face_normal)
            for d in range(self.mesh.dim)
        ]

    def with_values(self, values: np.ndarray, name: Optional[str] = None) -> "VectorField":
        return VectorField(values=values, mesh=self.mesh, kind=self.kind,
                           name=name or self.name, face_normal=self.face_normal)

    def digest(self) -> str:
        import hashlib

        h = hashlib.sha256()
        h.update(np.ascontiguousarray(self.values, dtype=np.float64).tobytes())
        h.update(self.kind.value.encode())
        h.update(self.mesh.digest().encode())
        h.update(self.name.encode())
        return h.hexdigest()


# --------------------------------------------------------------- patch testing
def rigid_motion_field(points: np.ndarray, translation: Sequence[float], rotation: Sequence[float]) -> np.ndarray:
    """Rigid motion ``v(x) = t + w x (x - x0)`` sampled at ``points``."""
    p = np.asarray(points, dtype=float)
    t = np.asarray(translation, dtype=float)
    w = np.asarray(rotation, dtype=float)
    if p.shape[1] == 3:
        return t + np.cross(np.broadcast_to(w, p.shape), p)
    if p.shape[1] == 2:
        wz = float(w[0]) if w.size else 0.0
        return t + wz * np.stack([-p[:, 1], p[:, 0]], axis=1)
    return t + 0.0 * p  # 1-D: rotation is undefined, pure translation


def rigid_motion_patch_test(
    src: BoxMesh, dst: BoxMesh, translation: Sequence[float], rotation: Sequence[float]
) -> Dict[str, object]:
    """Reproduce a rigid motion exactly on the destination mesh (patch test).

    A linear velocity field has a cell average equal to its centroid value, so an
    overlap exchange that integrates the source field exactly must return the
    analytic rigid motion at the destination centroids.  Any deviation measures
    the operator's lack of affine precision — the property that *must* hold
    before a moving-body or rotating-frame exchange can be trusted.

    The test is deliberately run on the **unprojected** operator: the moment
    projection is a conservation projection and shifts the field by its O(h^2)
    discretisation bias, which is reported separately (see the gate report).
    """
    from .remap import RemapOperator, RemapSpec, remap

    src_vals = rigid_motion_field(src.centers, translation, rotation)
    analytic = rigid_motion_field(dst.centers, translation, rotation)
    errs: Dict[str, float] = {}
    fields: Dict[str, VectorField] = {}
    for name in ("overlap_l2", "overlap_p1", "rbf_conservative"):
        op = RemapOperator(name)
        cols = []
        for d in range(src.dim):
            # a velocity component is intensive per unit volume (a momentum
            # density); representing it as a per-cell total would rescale it by
            # the cell volume and destroy the comparison
            f = Field(values=src_vals[:, d], mesh=src, kind=QuantityKind.DENSITY,
                      name=f"rigid[{d}]")
            spec = RemapSpec(operator=op, quantity_kind=QuantityKind.DENSITY,
                             apply_conservation_correction=True)
            got = remap(f, src, dst, "density", spec=spec).field
            cols.append(got.values)
        got = np.stack(cols, axis=1)
        fields[name] = VectorField(values=got, mesh=dst, kind=QuantityKind.EXTENSIVE,
                                   name=f"rigid@{dst.name}:{name}")
        scale = max(float(np.max(np.abs(analytic))), 1e-300)
        errs[name] = float(np.max(np.abs(got - analytic))) / scale
    return {"errors": errs, "fields": fields, "analytic": analytic}

# ------------------------------------------------- second-order (linear) transfer
def cell_gradients(values: np.ndarray, mesh: BoxMesh, k: Optional[int] = None) -> np.ndarray:
    """Least-squares cell gradient of a cell-centred field, shape ``(n_cells, dim)``.

    Each cell fits an affine function through its ``k`` nearest cell centres, so a
    field that *is* affine is reproduced exactly (up to round-off) — the property
    the patch test checks.  The fit is a batched least-squares solve, evaluated
    with a pseudo-inverse so that a locally degenerate stencil (collinear or
    duplicated centres) degrades gracefully instead of raising.
    """
    from scipy.spatial import cKDTree

    c = np.asarray(mesh.centers, dtype=float)
    v = np.asarray(values, dtype=float)
    n, dim = c.shape
    if v.size != n:
        raise ValueError("values and mesh disagree on the cell count")
    kk = max(int(k) if k is not None else 2 * dim + 1, dim + 1)
    kk = min(kk, n)
    if kk < dim + 1:
        raise DegenerateMeasureError(
            f"a {dim}-D gradient needs at least {dim + 1} cells, the mesh has {n}"
        )
    tree = cKDTree(c)
    _, idx = tree.query(c, k=kk + 1)
    idx = np.atleast_2d(idx)[:, 1:]                       # drop self
    dX = c[idx] - c[:, None, :]                           # (n, k, dim)
    dV = v[idx] - v[:, None]                              # (n, k)
    gram = np.einsum("nki,nkj->nij", dX, dX)
    rhs = np.einsum("nki,nk->ni", dX, dV)
    return np.linalg.pinv(gram) @ rhs[..., None]
    # shape (n, dim, 1) -> squeezed by the caller


def _squeeze_grad(g: np.ndarray) -> np.ndarray:
    return np.asarray(g).reshape(np.asarray(g).shape[0], -1)


def overlap_matrix_with_centroids(
    src: BoxMesh, dst: BoxMesh, chunk: int = 1024
) -> Tuple[np.ndarray, np.ndarray]:
    """Intersection volumes and their centroids: ``(V_ij, C_ij)``.

    ``V_ij = |src_i ∩ dst_j|`` and ``C_ij`` is the centroid of that intersection.
    Both are exact for axis-aligned boxes (the intersection is itself a box), which
    is what makes an exact quadrature of a linear reconstruction possible.
    """
    if src.dim != dst.dim:
        raise MeshError(f"dimension mismatch: {src.dim} vs {dst.dim}")
    ns, nd, dim = src.n_cells, dst.n_cells, src.dim
    vols = np.zeros((ns, nd), dtype=float)
    cents = np.zeros((ns, nd, dim), dtype=float)
    chunk = max(1, int(chunk))
    for start in range(0, nd, chunk):
        stop = min(nd, start + chunk)
        vol = np.ones((ns, stop - start), dtype=float)
        cen = np.empty((ns, stop - start, dim), dtype=float)
        for k in range(dim):
            lo = np.maximum(src.lo[:, k][:, None], dst.lo[start:stop, k][None, :])
            hi = np.minimum(src.hi[:, k][:, None], dst.hi[start:stop, k][None, :])
            lengths = hi - lo
            vol *= np.clip(lengths, 0.0, None)
            cen[:, :, k] = 0.5 * (lo + hi)
        vols[:, start:stop] = vol
        cents[:, start:stop, :] = cen
    return vols, cents


def apply_linear_reconstruction(
    rho_src: np.ndarray,
    src: BoxMesh,
    dst: BoxMesh,
    chunk: int = 1024,
    gradients: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Integrate a piecewise-linear reconstruction of ``rho_src`` exactly.

    Returns ``(rho_dst, coverage)``: the destination intensive value and the
    geometric measure actually covered in each destination cell.  Because the
    reconstruction of a cell integrates to its own value, the total is conserved
    *by construction* (no rescaling needed), and because the intersection boxes
    are integrated exactly, an affine input is reproduced exactly — the
    second-order property the first-order projection cannot have.
    """
    g = _squeeze_grad(
        cell_gradients(rho_src, src) if gradients is None else gradients
    )
    vols, cents = overlap_matrix_with_centroids(src, dst, chunk=chunk)
    offsets = cents - src.centers[:, None, :]
    lin = np.einsum("ikd,id->ik", offsets, g) * vols   # g_i . (c_ij - x_i) * V_ij
    numerator = vols.T @ rho_src + lin.sum(axis=0)
    coverage = vols.sum(axis=0)
    safe = np.where(coverage > 0.0, coverage, 1.0)
    return numerator / safe, coverage
