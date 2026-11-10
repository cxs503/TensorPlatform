"""Experiment designs for the sample-driven PCE kernel (domain free).

Two families of designs are exposed:

* **space-filling** designs on the unit hypercube (Latin hypercube / scrambled
  Sobol' / plain random) for least-squares regression.  They are mapped to
  physical space through the caller's marginals, so the same design works for
  uniform and normal inputs.
* **quadrature** rules (Gauss-Legendre tensor, Gauss-Hermite tensor, Smolyak
  sparse grid) that come with weights, for spectral projection.  Feeding the
  returned weights into :func:`tools.pce.pce_fit` gives the projection
  coefficients.

Everything is a thin composition of the audited primitives in
:mod:`backend.services.pce_uq` (no duplicated maths, one source of truth).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

from backend.services.pce_uq.kernel import make_unit_sample
from backend.services.pce_uq.marginals import Marginal, to_physical
from backend.services.pce_uq.quadrature import smolyak_grid, tensor_grid

from .kernel import MarginalsArg, resolve_marginals

__all__ = [
    "unit_design",
    "physical_design",
    "quadrature_design",
]


def unit_design(n: int, d: int, sampler: str = "lhs", seed: int = 0) -> np.ndarray:
    """``(n, d)`` coordinates on ``[0, 1]^d`` (``lhs`` | ``qmc`` | ``mc``)."""
    return make_unit_sample(int(n), int(d), method=str(sampler), seed=int(seed))


def physical_design(
    n: int,
    marginals: Sequence[Marginal],
    sampler: str = "lhs",
    seed: int = 0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Space-filling design in physical units; returns ``(X, unit_coordinates)``."""
    mar = tuple(marginals)
    U = unit_design(int(n), len(mar), sampler=sampler, seed=seed)
    return to_physical(mar, U), U


def quadrature_design(
    marginals: MarginalsArg,
    X: Optional[np.ndarray] = None,
    kind: str = "tensor",
    n_per_dim: int = 4,
    level: int = 2,
    names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Quadrature rule in physical space, with weights, for projection PCE.

    ``kind="tensor"`` uses ``n_per_dim`` Gauss nodes per dimension (cost
    ``n_per_dim ** d``, exact to degree ``2 n_per_dim - 1`` per axis);
    ``kind="smolyak"`` uses the sparse combination at ``level`` (cost grows
    polylogarithmically in ``d``, at the price of negative weights).

    ``marginals`` accepts everything :func:`tools.pce.pce_fit` accepts.  Pass
    ``X`` only when ``marginals is None`` (the uniform laws are then inferred
    from those samples); for a quadrature rule you normally want to state the
    laws explicitly.

    Returns a dict with ``X`` (physical points), ``weights``, ``standard``
    (standard-variable coordinates), ``n_points``, ``kind``, ``level``,
    ``n_negative_weights`` and the resolved ``marginals``.
    """
    # resolve the laws; a dummy 2-row sample matrix is enough to name the dims
    if marginals is None:
        if X is None:
            raise ValueError("quadrature_design needs explicit marginals or a reference sample X")
        mar = resolve_marginals(X, None, names)
    else:
        specs = list(marginals) if not isinstance(marginals, str) else None
        if specs is None:
            if X is None:
                raise ValueError(
                    "a family shorthand for marginals needs a reference sample X to infer from"
                )
            mar = resolve_marginals(X, marginals, names)
        else:
            probe = np.zeros((2, len(specs)))
            mar = resolve_marginals(probe, specs, names)

    families = tuple(m.basis_family for m in mar)
    kind = str(kind).lower()
    if kind == "tensor":
        grid = tensor_grid(families, int(n_per_dim))
    elif kind == "smolyak":
        grid = smolyak_grid(families, int(level))
    else:
        raise ValueError(f"unknown quadrature kind {kind!r}; expected 'tensor' or 'smolyak'")

    Xp = grid.physical(mar)
    return {
        "X": Xp,
        "weights": np.asarray(grid.weights, dtype=float),
        "standard": np.asarray(grid.points, dtype=float),
        "n_points": int(grid.n_points),
        "kind": grid.kind,
        "level": int(grid.level),
        "n_negative_weights": int(np.sum(np.asarray(grid.weights) < 0.0)),
        "weight_sum": float(np.sum(grid.weights)),
        "marginals": mar,
        "families": families,
    }