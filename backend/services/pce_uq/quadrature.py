"""Non-intrusive quadrature grids for spectral projection.

Two grid families are provided, both living in the *standard variable* space of
each input (see :mod:`.basis`), so the quadrature weights are exactly the
weights of the orthonormal basis measure and the projection

    ``c_alpha = sum_j w_j f(x_j) phi_alpha(z_j)``

needs no norm correction:

* :func:`tensor_grid` -- full tensor product of 1-D Gauss rules.  Cost
  ``prod_k m_k``; used when the dimension is small (``d <= 3``).
* :func:`smolyak_grid` -- Smolyak sparse combination of the same 1-D Gauss
  rules, cost grows only polynomially in the level.  Used for ``d >= 4`` where
  the tensor product is unaffordable.

Duplicate nodes produced by the combination technique are merged with summed
(SIGNED) weights -- Smolyak weights are negative for some nodes, which is
inherent to the combination technique and is why the projection must be
validated against closed-form integrals (see ``tests/test_pce_uq_kernel_m9011.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Sequence, Tuple

import numpy as np

from .marginals import Marginal


# --------------------------------------------------------------------------- #
# 1-D Gauss rules, expressed in the standard variable of the matching basis
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=256)
def _gauss_rule_1d_cached(family: str, n: int) -> Tuple[np.ndarray, np.ndarray]:
    """``n``-point Gauss rule for the standard measure.

    ``legendre``: nodes on ``[-1, 1]`` with the uniform density ``1/2``;
    ``hermite``: nodes for ``N(0, 1)`` (probabilists' convention).
    Exact for polynomials of degree ``<= 2n-1``; weights sum to one.
    """
    n = int(n)
    if n < 1:
        raise ValueError("n must be >= 1")
    fam = str(family).lower()
    if fam == "legendre":
        from numpy.polynomial.legendre import leggauss

        x, w = leggauss(n)
        return np.asarray(x, dtype=float), np.asarray(w, dtype=float) / 2.0
    if fam == "hermite":
        from numpy.polynomial.hermite_e import hermegauss

        # hermegauss integrates exp(-x^2/2) dx; probabilists' density is
        # exp(-x^2/2)/sqrt(2 pi), hence the 1/sqrt(2 pi) factor.
        x, w = hermegauss(n)
        return np.asarray(x, dtype=float), np.asarray(w, dtype=float) / math.sqrt(2.0 * math.pi)
    raise ValueError(f"unknown basis family {family!r}")


def gauss_rule_1d(family: str, n: int) -> Tuple[np.ndarray, np.ndarray]:
    """Cached 1-D Gauss rule (arrays are returned as copies, safe to mutate)."""
    x, w = _gauss_rule_1d_cached(str(family).lower(), int(n))
    return np.array(x), np.array(w)


@dataclass(frozen=True)
class QuadratureGrid:
    """A quadrature rule in standard-variable space."""

    points: np.ndarray      # (n, d) standard coordinates
    weights: np.ndarray     # (n,)
    families: Tuple[str, ...]
    kind: str               # "tensor" | "smolyak"
    level: int

    @property
    def n_points(self) -> int:
        return int(self.points.shape[0])

    def physical(self, marginals: Sequence[Marginal]) -> np.ndarray:
        from .marginals import from_standard

        return np.column_stack([from_standard(m, self.points[:, j]) for j, m in enumerate(marginals)])

    def as_dict(self) -> Dict[str, object]:
        return {
            "kind": self.kind,
            "level": int(self.level),
            "n_points": self.n_points,
            "weight_sum": float(np.sum(self.weights)),
            "min_abs_weight": float(np.min(np.abs(self.weights))),
            "n_negative_weights": int(np.sum(self.weights < 0.0)),
        }


def tensor_grid(families: Sequence[str], n_per_dim: int) -> QuadratureGrid:
    """Full tensor product of ``n_per_dim``-point Gauss rules."""
    d = len(families)
    if d < 1:
        raise ValueError("need at least one dimension")
    rules = [gauss_rule_1d(fam, n_per_dim) for fam in families]
    per_dim_x = [r[0] for r in rules]
    per_dim_w = [r[1] for r in rules]
    mesh = np.meshgrid(*per_dim_x, indexing="ij")
    points = np.column_stack([m.ravel() for m in mesh])
    weights = np.ones(points.shape[0], dtype=float)
    for j in range(d):
        rep = int(np.prod([len(per_dim_x[k]) for k in range(j + 1, d)])) or 1
        tile = int(np.prod([len(per_dim_x[k]) for k in range(j)])) or 1
        weights *= np.tile(np.repeat(per_dim_w[j], rep), tile)
    return QuadratureGrid(points=points, weights=weights, families=tuple(families),
                          kind="tensor", level=int(n_per_dim))


def _compositions(total: int, dim: int):
    """All ``dim``-tuples of integers ``>= 1`` summing to ``total``."""
    if dim == 1:
        if total >= 1:
            yield (total,)
        return
    for k in range(1, total - dim + 2):
        for rest in _compositions(total - k, dim - 1):
            yield (k,) + rest


def _smolyak_indices(level: int, dim: int) -> Sequence[Tuple[Tuple[int, ...], int]]:
    """Multi-indices and combination coefficients for sparse level ``level``.

    ``q = level + dim - 1`` (the offset makes ``level = 1`` the single-node rule
    in any dimension); coefficients are ``(-1)^{q-|i|} C(dim-1, q-|i|)``.
    """
    q = int(level) + int(dim) - 1
    out = []
    for total in range(max(dim, q - dim + 1), q + 1):
        coef = int(round(((-1.0) ** (q - total)) * math.comb(dim - 1, q - total)))
        if coef == 0:
            continue
        for idx in _compositions(total, dim):
            out.append((idx, coef))
    return out


@lru_cache(maxsize=64)
def _smolyak_grid_cached(families: Tuple[str, ...], level: int) -> Tuple[np.ndarray, np.ndarray]:
    grid = _smolyak_grid_uncached(families, level)
    grid.points.setflags(write=False)
    grid.weights.setflags(write=False)
    return grid.points, grid.weights


def smolyak_grid(families: Sequence[str], level: int) -> QuadratureGrid:
    """Cached wrapper; the grid depends only on (families, level)."""
    pts, w = _smolyak_grid_cached(tuple(str(f).lower() for f in families), int(level))
    return QuadratureGrid(points=np.array(pts), weights=np.array(w),
                          families=tuple(families), kind="smolyak", level=int(level))


def _smolyak_grid_uncached(families: Sequence[str], level: int) -> QuadratureGrid:
    """Smolyak (combination-technique) sparse grid of Gauss rules.

    The 1-D rule for index ``i_k`` uses ``m(i_k) = 2 i_k - 1`` nodes, so the
    sparse rule ``A(q, d)`` with ``q = level + d - 1`` integrates every
    polynomial of *total degree* ``<= 2*level - 1`` exactly.  That exactness is
    asserted numerically in the test suite rather than assumed, because the
    combination weights are signed and a mis-sign is silent otherwise.
    """
    d = len(families)
    level = int(level)
    if level < 1:
        raise ValueError("level must be >= 1")
    if d < 1:
        raise ValueError("need at least one dimension")
    q = level + d - 1
    rules_1d = {(i, fam): gauss_rule_1d(fam, 2 * i - 1)
                for fam in set(families) for i in range(1, q + 1)}

    pts_all = []
    w_all = []
    for idx, coef in _smolyak_indices(level, d):
        per_dim = [rules_1d[(i, fam)] for i, fam in zip(idx, families)]
        mesh = np.meshgrid(*[p[0] for p in per_dim], indexing="ij")
        pts = np.column_stack([m.ravel() for m in mesh])
        w = np.full(pts.shape[0], float(coef), dtype=float)
        for j in range(d):
            rep = int(np.prod([len(per_dim[k][0]) for k in range(j + 1, d)])) or 1
            tile = int(np.prod([len(per_dim[k][0]) for k in range(j)])) or 1
            w *= np.tile(np.repeat(per_dim[j][1], rep), tile)
        pts_all.append(pts)
        w_all.append(w)

    points = np.vstack(pts_all)
    weights = np.concatenate(w_all)
    # identical nodes come from identical 1-D rules, so exact float equality is
    # the right merge criterion (no tolerance knob to tune).
    uniq, inverse = np.unique(points, axis=0, return_inverse=True)
    merged = np.zeros(uniq.shape[0], dtype=float)
    np.add.at(merged, inverse, weights)
    keep = np.abs(merged) > 1e-14
    return QuadratureGrid(points=uniq[keep], weights=merged[keep],
                          families=tuple(families), kind="smolyak", level=level)


def build_grid(families: Sequence[str], level_or_n: int, max_tensor_dim: int = 3) -> QuadratureGrid:
    """Pick a tensor grid for low dimension and a sparse grid otherwise."""
    d = len(families)
    if d <= int(max_tensor_dim):
        # tensor: (2n-1) points per dim would be wasteful; for a total-degree
        # order p, a (p+1)-point Gauss rule per dim is exact to degree 2p+1.
        return tensor_grid(families, int(level_or_n))
    return smolyak_grid(families, int(level_or_n))