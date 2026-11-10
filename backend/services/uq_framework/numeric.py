"""Numerical-error leg of the framework: discretization (GCI), iteration, round-off.

Three error sources are separable and each gets its own machine-checkable
estimator, so a reported band never mixes them silently:

1. **Discretization** -- three-grid Grid Convergence Index (Roache 1994;
   Celik et al. 2008; ASME V&V 20).  The estimator already exists in
   ``tools/gci/core.py`` (Richardson extrapolation with a general refinement
   ratio, observed-order solve by bisection, safety factor 1.25).  This module
   *reuses* it rather than re-deriving it, and adds the one thing a framework
   needs: a falsifiable **order check** ``|p_obs - p_expected| / p_expected``
   against the scheme's formal order.  If that fails the grids are
   pre-asymptotic and the band must not be published as a certificate -- the
   three-grid asymptotic check ``e32/(r^p e21) ~ 1`` is algebraically satisfied
   by construction and therefore *not* a test.
2. **Iteration** -- the residual norm ``||A x - b||_2 / ||b||_2`` of the linear
   solve behind a reported number, against a tolerance.  A non-converged solve
   makes every downstream UQ statement meaningless, so it is gated first.
3. **Round-off** -- the same kernel evaluated in float32 and float64; the
   relative difference is a *measured* (not assumed) round-off floor, which is
   reported alongside the discretization band so a band below the floor is
   visible as unphysical.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

import numpy as np

__all__ = [
    "grid_convergence",
    "residual_norm",
    "roundoff_study",
    "NumericalResult",
    "run_numerical_study",
]


def _gci_core():
    """Import the existing GCI estimator, tolerating an uninstalled repo root."""
    try:
        from tools.gci.core import gci  # type: ignore

        return gci
    except Exception:  # pragma: no cover - path fallback
        root = Path(__file__).resolve().parents[3]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tools.gci.core import gci  # type: ignore

        return gci


def grid_convergence(
    f1: float,
    f2: float,
    f3: float,
    r21: float = 2.0,
    r32: Optional[float] = None,
    p_expected: Optional[float] = None,
    order_tol: float = 0.10,
    fs: float = 1.25,
) -> Dict[str, Any]:
    """Three-grid GCI plus the order-of-accuracy check."""
    res = _gci_core()(f1, f2, f3, r21=r21, r32=r32, fs=fs)
    out = res.to_dict()
    out["p_expected"] = None if p_expected is None else float(p_expected)
    if p_expected is None or res.p_obs is None:
        out["order_deviation"] = None
        out["order_ok"] = None
    else:
        dev = abs(float(res.p_obs) - float(p_expected)) / abs(float(p_expected))
        out["order_deviation"] = float(dev)
        out["order_ok"] = bool(dev < float(order_tol))
    out["gci_fine_pct"] = float(100.0 * res.gci21)
    out["order_tol"] = float(order_tol)
    return out


def residual_norm(A: np.ndarray, x: np.ndarray, b: np.ndarray) -> Dict[str, float]:
    """Relative 2-norm of the linear-solve residual."""
    A = np.asarray(A, dtype=float)
    x = np.asarray(x, dtype=float).ravel()
    b = np.asarray(b, dtype=float).ravel()
    r = A @ x - b
    nb = float(np.linalg.norm(b))
    rel = float(np.linalg.norm(r)) / nb if nb > 0 else float("inf")
    return {"residual_abs": float(np.linalg.norm(r)), "residual_rel": rel}


def roundoff_study(
    kernel: Callable[[Any], float],
    reference_dtype: Any = np.float64,
    test_dtype: Any = np.float32,
) -> Dict[str, Any]:
    """Compare one kernel across two floating-point precisions.

    The relative difference is a *measured* round-off floor for this kernel on
    this input, so a discretization band narrower than the floor is visibly
    unphysical instead of silently plausible.
    """
    y_ref = float(kernel(reference_dtype))
    y_test = float(kernel(test_dtype))
    denom = abs(y_ref) if y_ref != 0.0 else 1.0
    rel = abs(y_test - y_ref) / denom
    return {
        "reference_dtype": str(np.dtype(reference_dtype)),
        "test_dtype": str(np.dtype(test_dtype)),
        "reference_value": y_ref,
        "test_value": y_test,
        "abs_diff": float(abs(y_test - y_ref)),
        "rel_diff": float(rel),
        "eps_ratio_32_over_64": float(np.finfo(np.float32).eps / np.finfo(np.float64).eps),
    }


@dataclass
class NumericalResult:
    """Container for the numerical-error leg."""

    gci: Optional[Dict[str, Any]] = None
    iteration: Optional[Dict[str, Any]] = None
    roundoff: Optional[Dict[str, Any]] = None

    @property
    def discrete_band(self) -> Optional[float]:
        """Half-width of the discretization band, relative to the QoI."""
        if not self.gci:
            return None
        return float(self.gci.get("gci21"))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "discretization": self.gci,
            "iteration": self.iteration,
            "roundoff": self.roundoff,
            "discrete_band_rel": self.discrete_band,
        }


def run_numerical_study(
    grid_solutions: Optional[Sequence[float]] = None,
    r21: float = 2.0,
    r32: Optional[float] = None,
    p_expected: Optional[float] = None,
    order_tol: float = 0.10,
    linear_system: Optional[Dict[str, Any]] = None,
    residual_tol: float = 1e-8,
    roundoff_kernel: Optional[Callable[[Any], float]] = None,
) -> NumericalResult:
    """Assemble the numerical-error leg from whatever evidence is supplied.

    ``grid_solutions``       : three QoI values on increasingly fine grids.
    ``linear_system``        : ``{"A":..., "x":..., "b":...}`` for the residual gate.
    ``roundoff_kernel``      : callable taking a float dtype and returning a
                               scalar, for the precision comparison.
    """
    gci_dict = None
    if grid_solutions is not None:
        vals = [float(v) for v in grid_solutions]
        if len(vals) != 3:
            raise ValueError("grid_solutions must hold exactly three values (fine -> coarse)")
        gci_dict = grid_convergence(vals[0], vals[1], vals[2], r21=r21, r32=r32,
                                    p_expected=p_expected, order_tol=order_tol)

    iter_dict = None
    if linear_system is not None:
        r = residual_norm(linear_system["A"], linear_system["x"], linear_system["b"])
        r["tolerance"] = float(residual_tol)
        r["ok"] = bool(r["residual_rel"] < float(residual_tol))
        iter_dict = r

    ro_dict = None
    if roundoff_kernel is not None:
        ro_dict = roundoff_study(roundoff_kernel)

    return NumericalResult(gci=gci_dict, iteration=iter_dict, roundoff=ro_dict)