"""Axis-aligned cell meshes and cell-centred fields (domain-neutral).

Multi-solver coupling on this platform exchanges *fields* between solvers that
do not share a mesh.  Both sides of such an exchange are described here by the
smallest structure that is enough to make conservation auditable:

* :class:`BoxMesh` —— a set of axis-aligned boxes (1-D, 2-D or 3-D) with an
  explicit volume per cell.  Structured uniform meshes (:meth:`BoxMesh.uniform`)
  and arbitrary box partitions (:meth:`BoxMesh.from_bounds`) are both supported,
  and :meth:`BoxMesh.refine` produces a strictly nested coarse/fine pair.
* :class:`Field` —— cell values with a declared :class:`QuantityKind`:

  ``density``    value per unit volume (a conserved *density*; the conserved
                 total is ``sum(value * volume)``),
  ``extensive``  value already integrated over the cell (the conserved total is
                 ``sum(value)``).

  Keeping the kind explicit is what allows the conservation gates in
  :mod:`tools.coupling.conservation` to be written once instead of per solver.

Nothing in this module knows what the fields mean physically: meshes, volumes
and quantity kinds are the whole vocabulary.  Domain meaning arrives through
artifacts/configuration at the call site.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field as _dc_field
from enum import Enum
from typing import Iterable, Sequence

import numpy as np

__all__ = ["MeshError", "QuantityKind", "BoxMesh", "Field"]


class MeshError(ValueError):
    """Raised when a mesh or field description is inconsistent."""


class QuantityKind(str, Enum):
    """How cell values relate to the conserved total."""

    DENSITY = "density"        # per-unit-volume value, total = sum(value * V)
    EXTENSIVE = "extensive"    # per-cell total,       total = sum(value)


def _coerce_kind(kind: "QuantityKind | str") -> QuantityKind:
    try:
        return QuantityKind(kind)
    except ValueError as exc:  # fail-closed on an unknown kind
        raise MeshError(f"unknown quantity kind: {kind!r}") from exc


def _as_2d(values, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim == 0:
        arr = arr.reshape(1, 1)
    elif arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    if arr.ndim != 2:
        raise MeshError(f"{name} must be scalar, 1-D or 2-D (cells x dim), got shape {arr.shape}")
    if not np.all(np.isfinite(arr)):
        raise MeshError(f"{name} contains non-finite entries")
    return np.ascontiguousarray(arr)


def _array_digest(arr: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(arr, dtype=np.float64).tobytes()).hexdigest()


@dataclass(frozen=True)
class BoxMesh:
    """A partition-shaped set of axis-aligned boxes.

    ``lo``/``hi`` are ``(n_cells, dim)`` arrays of lower/upper corners.
    Boxes are *not* required to tile a domain — but every operator in
    :mod:`tools.coupling.remap` reports the covered fraction so that a partially
    covered destination mesh cannot silently lose mass.
    """

    lo: np.ndarray
    hi: np.ndarray
    name: str = "mesh"

    def __post_init__(self) -> None:
        lo = _as_2d(self.lo, "lo")
        hi = _as_2d(self.hi, "hi")
        if lo.shape != hi.shape:
            raise MeshError(f"lo{lo.shape} and hi{hi.shape} must have the same shape")
        if lo.shape[0] == 0:
            raise MeshError("mesh needs at least one cell")
        if not np.all(hi > lo):
            raise MeshError("every cell needs hi > lo in all dimensions")
        object.__setattr__(self, "lo", lo)
        object.__setattr__(self, "hi", hi)

    # ---------------------------------------------------------------- builders
    @classmethod
    def from_bounds(cls, lo, hi, name: str = "mesh") -> "BoxMesh":
        return cls(lo=lo, hi=hi, name=name)

    @classmethod
    def uniform(cls, lo, hi, n, name: str = "mesh") -> "BoxMesh":
        """Structured uniform mesh: ``n[i]`` cells between ``lo[i]`` and ``hi[i]``.

        Cell ordering is C order with the last axis varying fastest.
        """
        lo1 = np.atleast_1d(np.asarray(lo, dtype=float))
        hi1 = np.atleast_1d(np.asarray(hi, dtype=float))
        n1 = np.atleast_1d(np.asarray(n, dtype=int))
        if not (lo1.size == hi1.size == n1.size):
            raise MeshError("lo, hi and n must have the same length")
        if np.any(n1 <= 0):
            raise MeshError("cell counts must be positive")
        if not np.all(hi1 > lo1):
            raise MeshError("hi must exceed lo in every dimension")
        axes = [np.linspace(lo1[k], hi1[k], int(n1[k]) + 1) for k in range(lo1.size)]
        grids = np.meshgrid(*[0.5 * (a[1:] + a[:-1]) for a in axes], indexing="ij")
        del grids  # centres are derived later; only faces are needed here
        shape = tuple(int(k) for k in n1)
        lo_cells = np.empty(shape + (lo1.size,), dtype=float)
        hi_cells = np.empty(shape + (lo1.size,), dtype=float)
        for k, axis in enumerate(axes):
            lo_k = np.broadcast_to(axis[:-1].reshape([-1 if j == k else 1 for j in range(lo1.size)]), shape)
            hi_k = np.broadcast_to(axis[1:].reshape([-1 if j == k else 1 for j in range(lo1.size)]), shape)
            lo_cells[..., k] = lo_k
            hi_cells[..., k] = hi_k
        return cls(lo=lo_cells.reshape(-1, lo1.size), hi=hi_cells.reshape(-1, lo1.size), name=name)

    # -------------------------------------------------------------- properties
    @property
    def n_cells(self) -> int:
        return int(self.lo.shape[0])

    @property
    def dim(self) -> int:
        return int(self.lo.shape[1])

    @property
    def volume(self) -> np.ndarray:
        return np.prod(self.hi - self.lo, axis=1)

    @property
    def total_volume(self) -> float:
        return float(self.volume.sum())

    @property
    def centers(self) -> np.ndarray:
        return 0.5 * (self.lo + self.hi)

    @property
    def cell_size(self) -> np.ndarray:
        """Per-cell length in each dimension, shape ``(n_cells, dim)``."""
        return self.hi - self.lo

    @property
    def extent(self) -> np.ndarray:
        """Bounding-box extent per dimension."""
        return self.hi.max(axis=0) - self.lo.min(axis=0)

    @property
    def length_scale(self) -> float:
        """Mean cell length ``(V / n_cells) ** (1/dim)`` (used to scale kernels)."""
        return float((self.total_volume / self.n_cells) ** (1.0 / self.dim))

    def refined(self, factor, name: "str | None" = None) -> "BoxMesh":
        """Strictly nested refinement: every cell split into ``factor`` parts per axis."""
        f = np.atleast_1d(np.asarray(factor, dtype=int))
        if f.size == 1:
            f = np.repeat(f, self.dim)
        if f.size != self.dim or np.any(f <= 0):
            raise MeshError("refinement factor must be positive and per-dimension")
        lo_parts, hi_parts = [], []
        for i in range(self.n_cells):
            axes = [np.linspace(self.lo[i, k], self.hi[i, k], int(f[k]) + 1) for k in range(self.dim)]
            shape = tuple(int(k) for k in f)
            lo_c = np.empty(shape + (self.dim,))
            hi_c = np.empty(shape + (self.dim,))
            for k, axis in enumerate(axes):
                view = [-1 if j == k else 1 for j in range(self.dim)]
                lo_c[..., k] = np.broadcast_to(axis[:-1].reshape(view), shape)
                hi_c[..., k] = np.broadcast_to(axis[1:].reshape(view), shape)
            lo_parts.append(lo_c.reshape(-1, self.dim))
            hi_parts.append(hi_c.reshape(-1, self.dim))
        return BoxMesh(
            lo=np.concatenate(lo_parts),
            hi=np.concatenate(hi_parts),
            name=name or f"{self.name}~refined",
        )

    def digest(self) -> str:
        """Content digest of the mesh geometry (stable across processes)."""
        return hashlib.sha256(
            _array_digest(self.lo).encode() + _array_digest(self.hi).encode() + self.name.encode()
        ).hexdigest()

    def contains(self, points) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(points, dtype=float))
        inside = np.all((pts >= self.lo.min(axis=0)) & (pts <= self.hi.max(axis=0)), axis=1)
        return inside

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"BoxMesh(name={self.name!r}, n_cells={self.n_cells}, dim={self.dim})"


@dataclass(frozen=True)
class Field:
    """Cell values plus the mesh and quantity kind needed to conserve them."""

    values: np.ndarray
    mesh: BoxMesh
    kind: QuantityKind = QuantityKind.DENSITY
    name: str = "field"

    def __post_init__(self) -> None:
        values = np.ascontiguousarray(np.asarray(self.values, dtype=float).reshape(-1))
        if values.size != self.mesh.n_cells:
            raise MeshError(
                f"field {self.name!r} has {values.size} values for {self.mesh.n_cells} cells"
            )
        if not np.all(np.isfinite(values)):
            raise MeshError(f"field {self.name!r} contains non-finite values")
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "kind", _coerce_kind(self.kind))

    # -------------------------------------------------------------- semantics
    @property
    def weights(self) -> np.ndarray:
        """Per-cell measure used to turn values into a conserved total."""
        return self.mesh.volume if self.kind is QuantityKind.DENSITY else np.ones(self.mesh.n_cells)

    @property
    def total(self) -> float:
        return float((self.values * self.weights).sum())

    def as_density(self) -> np.ndarray:
        """Values reinterpreted per unit volume (remap works in this space)."""
        if self.kind is QuantityKind.DENSITY:
            return self.values
        return self.values / self.mesh.volume

    def with_values(self, values, name: "str | None" = None) -> "Field":
        return Field(values=values, mesh=self.mesh, kind=self.kind, name=name or self.name)

    def first_moment(self) -> np.ndarray:
        """Conserved first moment ``sum(x * value * weight)`` per dimension."""
        return (self.mesh.centers * (self.values * self.weights)[:, None]).sum(axis=0)

    def digest(self) -> str:
        return hashlib.sha256(
            (_array_digest(self.values) + self.kind.value + self.mesh.digest() + self.name).encode()
        ).hexdigest()

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"Field(name={self.name!r}, kind={self.kind.value!r}, "
            f"n_cells={self.values.size}, total={self.total:.12g})"
        )