"""Orthonormal polynomial bases and multi-index truncation sets.

The basis is chosen per input dimension and is *orthonormal with respect to the
standard variable of that input*:

======================  ============================  =====================================
marginal                standard variable             orthonormal basis
======================  ============================  =====================================
Uniform ``[a, b]``      ``xi = 2(x-a)/(b-a)-1``       shifted Legendre ``sqrt(2k+1) P_k(xi)``
Normal / Lognormal      ``z``                         probabilists' Hermite ``He_k(z)/sqrt(k!)``
======================  ============================  =====================================

Both bases satisfy ``E[phi_i phi_j] = delta_ij`` under the *standard* measure
(``U(-1, 1)`` for Legendre, ``N(0, 1)`` for Hermite), so a truncated PCE written
in these functions has spectral moments ``mean = c_0`` and
``variance = sum_{alpha != 0} c_alpha^2`` with no norm bookkeeping.  That
property is what makes the variance decomposition auditable: a kernel that
normalises the basis differently fails the orthonormality test in
``tests/test_pce_uq_kernel_m9011.py``.

This module is deliberately *domain free*: it knows polynomial families and
multi-index truncation, nothing about the physical problem being studied.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import numpy as np

BASIS_FAMILIES: Tuple[str, ...] = ("legendre", "hermite")


# --------------------------------------------------------------------------- #
# one dimensional orthonormal bases
# --------------------------------------------------------------------------- #
def legendre_orthonormal(xi: np.ndarray, degree: int) -> np.ndarray:
    """``phi_k(xi) = sqrt(2k+1) P_k(xi)`` on ``[-1, 1]``, columns ``k=0..degree``."""
    from numpy.polynomial.legendre import legvander

    xi = np.asarray(xi, dtype=float)
    raw = legvander(xi, int(degree))
    scale = np.sqrt(2.0 * np.arange(int(degree) + 1) + 1.0)
    return raw * scale


def hermite_orthonormal(z: np.ndarray, degree: int) -> np.ndarray:
    """``phi_k(z) = He_k(z)/sqrt(k!)`` for ``z ~ N(0, 1)``, columns ``k=0..degree``."""
    from numpy.polynomial.hermite_e import hermevander

    z = np.asarray(z, dtype=float)
    raw = hermevander(z, int(degree))
    scale = 1.0 / np.sqrt(np.array([math.factorial(k) for k in range(int(degree) + 1)], dtype=float))
    return raw * scale


def one_dimensional_basis(family: str, x: np.ndarray, degree: int) -> np.ndarray:
    fam = str(family).lower()
    if fam == "legendre":
        return legendre_orthonormal(x, degree)
    if fam == "hermite":
        return hermite_orthonormal(x, degree)
    raise ValueError(f"unknown basis family {family!r}; expected one of {BASIS_FAMILIES}")


# --------------------------------------------------------------------------- #
# multi-index truncation
# --------------------------------------------------------------------------- #
def _compositions_zero(total: int, dim: int):
    """All ``dim``-tuples of non-negative integers summing to ``total``."""
    if dim == 1:
        yield (total,)
        return
    for k in range(total + 1):
        for rest in _compositions_zero(total - k, dim - 1):
            yield (k,) + rest


def multi_index_set(dim: int, order: int, q_norm: float = 1.0) -> np.ndarray:
    """Total-degree (``q_norm=1``) or hyperbolic (``q_norm < 1``) multi-index set.

    Returns an ``(n_terms, dim)`` integer array of exponents, constant term
    first, sorted by total degree then lexicographically (deterministic order,
    so ledger digests over coefficient vectors are reproducible).  The
    enumeration walks total degrees directly (stars and bars) instead of
    filtering the ``(order+1)^dim`` cube, which matters for ``dim = 6``.
    """
    dim = int(dim)
    order = int(order)
    if dim < 1:
        raise ValueError("dim must be >= 1")
    if order < 0:
        raise ValueError("order must be >= 0")
    q = float(q_norm)
    if not (0.0 < q <= 1.0):
        raise ValueError("q_norm must lie in (0, 1]")

    keep: List[Tuple[int, ...]] = []
    for total in range(order + 1):
        for term in _compositions_zero(total, dim):
            if q == 1.0:
                keep.append(term)
            else:
                a = np.asarray(term, dtype=float)
                if float(np.sum(a ** q)) ** (1.0 / q) <= order + 1e-12:
                    keep.append(term)
    keep = sorted(set(keep), key=lambda t: (int(sum(t)), t))
    return np.asarray(keep, dtype=int)


def design_matrix(indices: np.ndarray, standard: np.ndarray, families: Sequence[str]) -> np.ndarray:
    """Evaluate the multivariate orthonormal basis on standardised inputs.

    ``standard`` has shape ``(n, d)`` and must already be expressed in each
    input's standard variable (uniform -> ``[-1, 1]``, normal -> ``N(0, 1)``).
    """
    indices = np.atleast_2d(np.asarray(indices, dtype=int))
    standard = np.atleast_2d(np.asarray(standard, dtype=float))
    if indices.shape[1] != len(families):
        raise ValueError("indices and families disagree on dimension")
    if standard.shape[1] != indices.shape[1]:
        raise ValueError(
            f"dimension mismatch: {standard.shape[1]} standard variables vs "
            f"{indices.shape[1]} basis dimensions"
        )
    max_deg = int(indices.max()) if indices.size else 0
    per_dim = [one_dimensional_basis(fam, standard[:, j], max_deg) for j, fam in enumerate(families)]
    out = np.empty((standard.shape[0], indices.shape[0]), dtype=float)
    for a, alpha in enumerate(indices):
        col = np.ones(standard.shape[0], dtype=float)
        for j, k in enumerate(alpha):
            if k:
                col = col * per_dim[j][:, int(k)]
        out[:, a] = col
    return out


def support(alpha: Sequence[int]) -> Tuple[int, ...]:
    """Indices of the inputs a basis term depends on (its ANOVA group)."""
    return tuple(j for j, k in enumerate(alpha) if int(k) != 0)