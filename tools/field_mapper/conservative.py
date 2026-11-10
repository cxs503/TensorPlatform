"""Conservative field mapping between non-matching discretisations (domain-neutral).

The operator is built from the exact intersection measures of
:mod:`tools.field_mapper.geometry`::

    f_dst[j] = ( Σ_i S[i, j] · f_src[i] ) / |Omega_j|

with ``S[i, j] = |Omega_i^src ∩ Omega_j^dst|``.  When the destination mesh tiles
the source domain this is a pure redistribution: every conserved total
``Σ f · |Omega|`` is preserved to round-off, positivity is preserved (all weights
are non-negative), no new extrema are created, and the map is an exact identity
when the two meshes coincide.

Why the law has to be declared
------------------------------
``density``   value per unit measure; conserved total is ``Σ f·V``.
``extensive`` value already integrated over the cell; conserved total is ``Σ f``.
``scalar``    intensive point value; **not conserved** — the admissible claims are
              precision preservation (constant/affine reproduction) and
              boundedness.  Declaring it stops a caller from silently applying a
              sum constraint that the quantity never obeyed.

Corrections
-----------
An *exact* overlap map needs no correction; corrections exist for the operators
that cannot be conservative by construction (nearest-cell injection, RBF
interpolation, partially covered targets).  Two are provided and both are
audited, never silent:

``none``          the raw operator (its conservation error is the measurement).
``global_scale``  multiply the destination by ``Σ_src / Σ_dst``; forces the
                  zeroth moment to close, keeps the sign, can move extrema.
``bounded``       distribute the residual over the cells that can still absorb it
                  without violating ``[lo, hi]``; keeps positivity and stops at
                  the bound instead of scaling everything.

Conservation is *not* accuracy: a nearest-cell map with a global scale closes the
mass budget exactly while its pointwise error stays at the cell size.  Both
numbers are therefore always reported.

Nothing in this module knows what a field means physically, and no threshold is
baked in: tolerances arrive from the caller (a benchmark) or from configuration.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field as _dc_field
from enum import Enum
from typing import Callable, Dict, Optional, Sequence

import numpy as np

from .geometry import (
    OverlapEntries,
    coverage_fraction,
    overlap_entries,
    polygon_area,
    polygon_centroid,
)

__all__ = [
    "MapLaw",
    "Correction",
    "CellSet",
    "ConservativeMap",
    "MapAudit",
    "MapError",
    "conserved_total",
    "l2_relative_error",
]


class MapError(ValueError):
    """Raised when a map cannot be built or applied as requested."""


class MapLaw(str, Enum):
    """How field values relate to the conserved total."""

    DENSITY = "density"
    EXTENSIVE = "extensive"
    SCALAR = "scalar"

    @property
    def conserves_total(self) -> bool:
        return self is not MapLaw.SCALAR


class Correction(str, Enum):
    """Post-hoc conservation correction applied to a mapped field."""

    NONE = "none"
    GLOBAL = "global_scale"
    BOUNDED = "bounded"


# --------------------------------------------------------------------------- #
# Discretisation views
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CellSet:
    """A cell family with an explicit measure (volume in 3-D, area in 2-D).

    ``lo``/``hi`` are present for box families, ``polygons`` for convex polygon
    families.  ``centers`` is always defined and is what nearest-cell operators
    use.  Keeping the measure explicit is what makes a 1-D, 2-D and 3-D mesh the
    same object to the rest of the package.
    """

    measure: np.ndarray
    centers: np.ndarray
    lo: Optional[np.ndarray] = None
    hi: Optional[np.ndarray] = None
    polygons: Optional[Sequence[np.ndarray]] = None
    name: str = "cells"
    family: str = "box"

    def __post_init__(self) -> None:
        measure = np.asarray(self.measure, dtype=float).reshape(-1)
        centers = np.atleast_2d(np.asarray(self.centers, dtype=float))
        if measure.size != centers.shape[0]:
            raise MapError("measure and centers must describe the same number of cells")
        if np.any(~np.isfinite(measure)) or np.any(measure <= 0.0):
            raise MapError("every cell needs a strictly positive finite measure")
        object.__setattr__(self, "measure", measure)
        object.__setattr__(self, "centers", centers)

    # ------------------------------------------------------------------ builders
    @classmethod
    def from_box_mesh(cls, mesh, name: Optional[str] = None) -> "CellSet":
        """Adapt a :class:`tools.coupling.mesh.BoxMesh` (interop, no copy of semantics)."""
        return cls(
            measure=np.asarray(mesh.volume, dtype=float),
            centers=np.asarray(mesh.centers, dtype=float),
            lo=np.asarray(mesh.lo, dtype=float),
            hi=np.asarray(mesh.hi, dtype=float),
            name=name or getattr(mesh, "name", "mesh"),
            family="box",
        )

    @classmethod
    def from_boxes(cls, lo, hi, name: str = "boxes") -> "CellSet":
        lo = np.atleast_2d(np.asarray(lo, dtype=float))
        hi = np.atleast_2d(np.asarray(hi, dtype=float))
        if lo.shape != hi.shape:
            raise MapError("lo/hi shape mismatch")
        if np.any(hi <= lo):
            raise MapError("every cell needs hi > lo")
        return cls(
            measure=np.prod(hi - lo, axis=1),
            centers=0.5 * (lo + hi),
            lo=lo,
            hi=hi,
            name=name,
            family="box",
        )

    @classmethod
    def from_polygons(cls, polygons: Sequence[np.ndarray], name: str = "polygons") -> "CellSet":
        polys = [np.asarray(p, dtype=float) for p in polygons]
        areas = np.array([polygon_area(p) for p in polys], dtype=float)
        centers = np.array([polygon_centroid(p) for p in polys], dtype=float)
        return cls(measure=areas, centers=centers, polygons=polys, name=name, family="convex_polygon")

    # --------------------------------------------------------------- properties
    @property
    def n_cells(self) -> int:
        return int(self.measure.size)

    @property
    def total_measure(self) -> float:
        return float(self.measure.sum())

    @property
    def dim(self) -> int:
        return int(self.centers.shape[1])

    @property
    def extent_scale(self) -> float:
        """Mean cell length ``(V/n)^(1/dim)`` — used to normalise moment residuals."""
        return float((self.total_measure / self.n_cells) ** (1.0 / self.dim))

    @property
    def bbox(self) -> np.ndarray:
        return np.concatenate([self.centers.min(axis=0), self.centers.max(axis=0)])

    def digest(self) -> str:
        payload = np.ascontiguousarray(self.measure[:, None] * 0.0 + self.centers)
        return hashlib.sha256(
            np.ascontiguousarray(self.measure).tobytes()
            + np.ascontiguousarray(payload).tobytes()
            + self.name.encode()
        ).hexdigest()


# --------------------------------------------------------------------------- #
# Law helpers
# --------------------------------------------------------------------------- #
def conserved_total(values, measure, law: "MapLaw | str") -> float:
    """Conserved total of ``values`` under ``law``.

    For ``scalar`` the returned number is the measure-weighted integral — it is
    reported for bookkeeping but carries **no** conservation claim.
    """
    law = MapLaw(law)
    values = np.asarray(values, dtype=float).reshape(-1)
    measure = np.asarray(measure, dtype=float).reshape(-1)
    if values.size != measure.size:
        raise MapError("values and measure must have the same length")
    if law is MapLaw.EXTENSIVE:
        return float(values.sum())
    return float((values * measure).sum())


def l2_relative_error(a, b, measure=None) -> float:
    """``‖a-b‖₂ / ‖b‖₂`` with an optional per-cell measure weight."""
    a = np.asarray(a, dtype=float).reshape(-1)
    b = np.asarray(b, dtype=float).reshape(-1)
    if a.shape != b.shape:
        raise MapError(f"shape mismatch in L2 comparison: {a.shape} vs {b.shape}")
    w = np.ones_like(b) if measure is None else np.asarray(measure, dtype=float).reshape(-1)
    num = float(np.sqrt(np.sum(w * (a - b) ** 2)))
    den = float(np.sqrt(np.sum(w * b**2)))
    return num / den if den > 0.0 else (0.0 if num == 0.0 else float("inf"))


# --------------------------------------------------------------------------- #
# The mapper
# --------------------------------------------------------------------------- #
@dataclass
class MapAudit:
    """Machine-checkable outcome of one mapping operation."""

    law: str
    correction: str
    operator: str
    total_src: float
    total_dst: float
    total_rel_error: float
    l2_rel_error: float
    l2_vs_reference: Optional[float]
    min_dst: float
    max_dst: float
    min_src: float
    max_src: float
    overshoot_ratio: float
    covered_fraction_min: float
    n_uncovered_dst: int
    residual: float
    matrix_digest: str
    src_digest: str
    dst_digest: str
    extra: Dict[str, float] = _dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "law": self.law,
            "correction": self.correction,
            "operator": self.operator,
            "total_src": self.total_src,
            "total_dst": self.total_dst,
            "total_rel_error": self.total_rel_error,
            "l2_rel_error": self.l2_rel_error,
            "l2_vs_reference": self.l2_vs_reference,
            "min_dst": self.min_dst,
            "max_dst": self.max_dst,
            "min_src": self.min_src,
            "max_src": self.max_src,
            "overshoot_ratio": self.overshoot_ratio,
            "covered_fraction_min": self.covered_fraction_min,
            "n_uncovered_dst": self.n_uncovered_dst,
            "residual": self.residual,
            "matrix_digest": self.matrix_digest,
            "src_digest": self.src_digest,
            "dst_digest": self.dst_digest,
            **self.extra,
        }


class ConservativeMap:
    """A reusable mapping operator between two cell families.

    Parameters
    ----------
    src, dst
        :class:`CellSet` descriptions of the two sides.
    operator
        ``"overlap"`` (exact intersection, conservative by construction) or
        ``"nearest"`` (cell-centre injection — kept as the *non-conservative*
        control arm of every conservation experiment).
    """

    OPERATORS = ("overlap", "nearest")

    def __init__(self, src: CellSet, dst: CellSet, operator: str = "overlap"):
        if operator not in self.OPERATORS:
            raise MapError(f"unknown operator {operator!r}; choose from {self.OPERATORS}")
        if src.dim != dst.dim:
            raise MapError(f"dimension mismatch: src {src.dim}-D vs dst {dst.dim}-D")
        self.src = src
        self.dst = dst
        self.operator = operator
        self._entries: Optional[OverlapEntries] = None
        self._matrix: Optional[np.ndarray] = None

    # ------------------------------------------------------------- construction
    @property
    def entries(self) -> OverlapEntries:
        if self._entries is None:
            family = "box" if self.src.family == self.dst.family == "box" else "polygon"
            if self.operator == "nearest" and family == "polygon":
                raise MapError("nearest operator is only defined for box families")
            if family == "box":
                self._entries = overlap_entries(
                    (self.src.lo, self.src.hi), (self.dst.lo, self.dst.hi), family="box"
                )
            else:
                self._entries = overlap_entries(self.src.polygons, self.dst.polygons, family="polygon")
        return self._entries

    @property
    def matrix(self) -> np.ndarray:
        """Intersection measures as a dense ``(n_dst, n_src)`` operator."""
        if self._matrix is None:
            self._matrix, _ = self.entries.as_coo()
        return self._matrix

    @property
    def weights(self) -> np.ndarray:
        """Normalised interpolation weights ``W = S / |Omega_dst|`` (rows sum to coverage)."""
        return self.matrix / self.dst.measure[:, None]

    def matrix_digest(self) -> str:
        return hashlib.sha256(np.ascontiguousarray(self.weights, dtype=np.float64).tobytes()).hexdigest()

    # ------------------------------------------------------------------ apply
    def apply(
        self,
        values,
        law: "MapLaw | str" = MapLaw.DENSITY,
        correction: "Correction | str" = Correction.NONE,
        bounds: Optional[tuple] = None,
        max_iter: int = 64,
        tol: float = 1e-15,
    ) -> np.ndarray:
        """Map ``values`` from the source to the destination discretisation."""
        law = MapLaw(law)
        correction = Correction(correction)
        raw = self._apply_raw(values, law)
        if correction is Correction.NONE:
            return raw
        total_src = conserved_total(values, self.src.measure, law)
        if correction is Correction.GLOBAL:
            total_raw = conserved_total(raw, self.dst.measure, law)
            if total_raw == 0.0:
                if total_src == 0.0:
                    return raw
                raise MapError("global_scale correction is undefined for a zero destination total")
            return raw * (total_src / total_raw)
        return self._bounded_fix(raw, values, law, total_src, bounds, max_iter, tol)

    # ---------------------------------------------------------------- internals
    def _apply_raw(self, values, law: MapLaw) -> np.ndarray:
        values = np.asarray(values, dtype=float).reshape(-1)
        if values.size != self.src.n_cells:
            raise MapError(f"expected {self.src.n_cells} source values, got {values.size}")
        if self.operator == "nearest":
            return self._nearest(values)
        if law is MapLaw.EXTENSIVE:
            # per-cell totals: distribute each source total by intersection fraction
            frac = self.matrix / self.src.measure[None, :]
            return frac @ values
        return self.weights @ values

    def _nearest(self, values: np.ndarray) -> np.ndarray:
        """Cell-centre injection: destination takes the enclosing source value.

        Deliberately *not* conservative — this is the control arm that shows a
        conservation gate can tell a redistribution from a resampling.
        """
        from .geometry import bbox_candidate_pairs

        out = np.full(self.dst.n_cells, np.nan, dtype=float)
        i_idx, j_idx = bbox_candidate_pairs(self.src.lo, self.src.hi, self.dst.lo, self.dst.hi)
        for i, j in zip(i_idx, j_idx):
            if np.isnan(out[j]):
                out[j] = values[i]
        missing = np.isnan(out)
        if np.any(missing):
            # destination centres outside the source partition: take the nearest centre
            d2 = ((self.dst.centers[missing][:, None, :] - self.src.centers[None, :, :]) ** 2).sum(axis=2)
            out[missing] = values[np.argmin(d2, axis=1)]
        return out

    def _bounded_fix(
        self,
        raw: np.ndarray,
        src_values: np.ndarray,
        law: MapLaw,
        total_src: float,
        bounds: Optional[tuple],
        max_iter: int,
        tol: float,
    ) -> np.ndarray:
        """Distribute the conservation residual without leaving ``bounds``."""
        measure = np.ones_like(raw) if law is MapLaw.EXTENSIVE else self.dst.measure
        src = np.asarray(src_values, dtype=float).reshape(-1)
        if bounds is None:
            lo = -np.inf if np.any(src < 0.0) else 0.0
            hi = np.inf
        else:
            lo, hi = float(bounds[0]), float(bounds[1])
        out = np.array(raw, dtype=float, copy=True)
        for _ in range(max_iter):
            residual = total_src - conserved_total(out, measure, law)
            scale = max(abs(total_src), np.finfo(float).tiny)
            if abs(residual) <= tol * scale:
                break
            if residual > 0.0:
                headroom = np.clip(hi - out, 0.0, None) * measure
                capacity = headroom.sum()
                if capacity <= 0.0:
                    break
                out += np.clip(hi - out, 0.0, None) * (residual / capacity)
            else:
                room = np.clip(out - lo, 0.0, None) * measure
                capacity = room.sum()
                if capacity <= 0.0:
                    break
                out -= np.clip(out - lo, 0.0, None) * (-residual / capacity)
        return out

    # ------------------------------------------------------------------- audit
    def audit(
        self,
        src_values,
        dst_values,
        law: "MapLaw | str" = MapLaw.DENSITY,
        correction: "Correction | str" = Correction.NONE,
        reference: Optional[Callable[[np.ndarray], np.ndarray]] = None,
        src_reference_values=None,
        extra: Optional[Dict[str, float]] = None,
    ) -> MapAudit:
        """Measure what the operation actually did.

        ``reference`` (optional) maps destination centres to the analytic cell
        average, which converts the audit from *conservation* into *accuracy*.
        ``src_reference_values`` (optional) gives the source field on the
        destination mesh, which is what the identity test compares against.
        """
        law = MapLaw(law)
        src_values = np.asarray(src_values, dtype=float).reshape(-1)
        dst_values = np.asarray(dst_values, dtype=float).reshape(-1)
        total_src = conserved_total(src_values, self.src.measure, law)
        total_dst = conserved_total(dst_values, self.dst.measure, law)
        denom = max(abs(total_src), np.finfo(float).tiny)
        covered = coverage_fraction(self.entries, self.dst.measure)
        l2_ref = None
        if reference is not None:
            l2_ref = l2_relative_error(dst_values, reference(self.dst.centers), self.dst.measure)
        l2_src = 0.0
        if src_reference_values is not None:
            l2_src = l2_relative_error(
                dst_values, np.asarray(src_reference_values, dtype=float).reshape(-1), self.dst.measure
            )
        return MapAudit(
            law=law.value,
            correction=Correction(correction).value,
            operator=self.operator,
            total_src=total_src,
            total_dst=total_dst,
            total_rel_error=abs(total_dst - total_src) / denom,
            l2_rel_error=l2_src,
            l2_vs_reference=l2_ref,
            min_dst=float(dst_values.min()),
            max_dst=float(dst_values.max()),
            min_src=float(src_values.min()),
            max_src=float(src_values.max()),
            overshoot_ratio=float(np.max(np.abs(dst_values)) / max(np.max(np.abs(src_values)), np.finfo(float).tiny)),
            covered_fraction_min=float(covered.min()) if covered.size else 0.0,
            n_uncovered_dst=int(np.sum(covered <= 0.0)),
            residual=float(total_dst - total_src),
            matrix_digest=self.matrix_digest(),
            src_digest=self.src.digest(),
            dst_digest=self.dst.digest(),
            extra=dict(extra or {}),
        )

    # ---------------------------------------------------------------- vector API
    def apply_vector(
        self,
        values,
        law: "MapLaw | str" = MapLaw.DENSITY,
        correction: "Correction | str" = Correction.NONE,
    ) -> np.ndarray:
        """Component-wise mapping of a vector field (same weights on every component)."""
        arr = np.asarray(values, dtype=float)
        if arr.ndim != 2 or arr.shape[0] != self.src.n_cells:
            raise MapError(f"expected shape ({self.src.n_cells}, n_components), got {arr.shape}")
        out = np.empty((self.dst.n_cells, arr.shape[1]), dtype=float)
        for k in range(arr.shape[1]):
            out[:, k] = self.apply(arr[:, k], law=law, correction=correction)
        return out