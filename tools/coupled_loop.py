"""Generic fixed-point iteration for block-coupled systems, with certificates.

A coupled solve is a fixed-point problem ``x = G(x)`` (or, for a linearised
exchange, ``x = A x + b``).  What a consumer needs back is not only the answer but
a *certificate*: did it converge, what is the estimated contraction factor, how
many iterations did it take, and was divergence detected early instead of being
burned to the iteration cap.

Provided here (all domain-neutral):

``solve_fixed_point``
    one global map, Jacobi sweeps, optional Aitken (Irons-Tuck) dynamic
    relaxation.
``solve_coupled``
    block-structured systems: Jacobi (all blocks from the previous iterate) or
    Gauss-Seidel (blocks updated in order, each seeing the freshest values).
``solve_linear_coupled`` / ``linear_blocks``
    convenience wrappers for ``x = A x + b``.
``Certificate``
    ``{converged, status, rho_hat, iterations, residual_series, ...}`` with a
    fail-closed contract check (``meets_loop_gate``).

Divergence is a first-class outcome: the estimated contraction factor is
monitored every iteration, and ``rho_hat > rho_max`` for ``patience`` consecutive
iterations yields ``DIVERGED`` long before the iteration cap is reached.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "Certificate",
    "CONVERGED",
    "DIVERGED",
    "MAX_ITERATIONS",
    "linear_blocks",
    "meets_loop_gate",
    "solve_coupled",
    "solve_fixed_point",
    "solve_linear_coupled",
]

CONVERGED = "CONVERGED"
DIVERGED = "DIVERGED"
MAX_ITERATIONS = "MAX_ITERATIONS"

Block = Tuple[Sequence[int], Callable[[np.ndarray], np.ndarray]]


@dataclass
class Certificate:
    """Outcome and quality record of one coupled fixed-point solve."""

    converged: bool
    status: str
    rho_hat: float
    iterations: int
    final_residual: float
    residual_series: Tuple[float, ...] = ()
    scheme: str = "jacobi"
    relaxation: str = "none"
    omega: float = 1.0
    reason: str = ""
    metadata: Dict[str, Any] = _dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "converged": bool(self.converged),
            "status": self.status,
            "rho_hat": float(self.rho_hat),
            "iterations": int(self.iterations),
            "final_residual": float(self.final_residual),
            "residual_series": [float(r) for r in self.residual_series],
            "scheme": self.scheme,
            "relaxation": self.relaxation,
            "omega": float(self.omega),
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


def _norm(vec: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(vec, dtype=float).ravel()))


def _estimate_rho(series: Sequence[float]) -> float:
    """Contraction estimate from the residual history.

    Geometric fit over the tail: ``rho = exp(d ln r / d n)``.  Fitting (rather
    than taking the last ratio) keeps the estimate stable against round-off and
    against the transient that Aitken relaxation deliberately introduces.
    """
    vals = [float(v) for v in series if v is not None and math.isfinite(v) and v > 0.0]
    if len(vals) < 2:
        return float("nan")
    tail = vals[-min(8, len(vals)):]
    steps = np.arange(len(tail), dtype=float)
    slope = float(np.polyfit(steps, np.log(np.asarray(tail)), 1)[0])
    return float(math.exp(slope))


def _aitken_omega(
    omega_prev: float,
    residual_prev: Optional[np.ndarray],
    residual: np.ndarray,
    bounds: Tuple[float, float],
) -> float:
    """Aitken (Irons-Tuck) dynamic relaxation factor.

    ``omega_k = -omega_{k-1} (r_{k-1} . dr) / ||dr||^2`` with ``dr = r_k - r_{k-1}``.
    For a scalar linear map this returns exactly ``1 / (1 - rho)``, i.e. the
    optimal relaxation, in one step.
    """
    if residual_prev is None:
        return omega_prev
    dr = residual - residual_prev
    denom = float(np.dot(dr, dr))
    if not math.isfinite(denom) or denom <= 1e-300:
        return omega_prev
    raw = -omega_prev * float(np.dot(residual_prev, dr)) / denom
    if not math.isfinite(raw):
        return omega_prev
    lo, hi = bounds
    return float(min(max(raw, lo), hi))


def _run_loop(
    sweep: Callable[[np.ndarray], np.ndarray],
    x0: np.ndarray,
    *,
    scheme: str,
    relaxation: str,
    omega0: float,
    tolerance: float,
    max_iterations: int,
    rho_max: float,
    patience: int,
    omega_bounds: Tuple[float, float],
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[np.ndarray, Certificate]:
    x = np.array(x0, dtype=float).copy()
    if x.size == 0:
        raise ValueError("initial guess must be non-empty")
    omega = float(omega0)
    series: List[float] = []      # relative residuals (human/report reading)
    absolute: List[float] = []    # absolute residuals (contraction estimate)
    residual_prev: Optional[np.ndarray] = None
    hot = 0            # consecutive iterations with rho > rho_max
    rho = float("nan")
    status, reason = MAX_ITERATIONS, "iteration cap reached without meeting the tolerance"
    converged = False
    init_scale = max(_norm(x), 1.0)

    for k in range(1, int(max_iterations) + 1):
        swept = np.asarray(sweep(np.array(x, dtype=float)), dtype=float).ravel()
        residual = swept - x
        scale = max(_norm(swept), 1e-300)
        res = _norm(residual) / scale
        series.append(res)
        absolute.append(float(_norm(residual)))

        if not math.isfinite(res):
            status, reason = DIVERGED, f"non-finite residual at iteration {k}"
            rho = _estimate_rho(absolute)
            break
        if res <= tolerance:
            converged, status, reason = True, CONVERGED, "residual below tolerance"
            rho = _estimate_rho(absolute)
            x = x + omega * residual
            break
        if absolute[-1] > 1e12 * init_scale:
            status, reason = DIVERGED, f"residual blow-up at iteration {k}"
            rho = _estimate_rho(absolute)
            break

        # the contraction factor of the iteration is the growth rate of the
        # *absolute* residual: dividing by the iterate (as the relative residual
        # does) would make a divergent loop look bounded, e.g. rho=1.2 read as 0.17
        rho = _estimate_rho(absolute)
        if math.isfinite(rho) and rho > rho_max:
            hot += 1
            if hot >= int(patience):
                status = DIVERGED
                reason = (
                    f"contraction estimate {rho:.4f} > {rho_max:.4f} for "
                    f"{hot} consecutive iterations (iteration {k})"
                )
                break
        else:
            hot = 0

        if relaxation == "aitken":
            omega = _aitken_omega(omega, residual_prev, residual, omega_bounds)
        x = x + omega * residual
        residual_prev = residual
    else:  # pragma: no cover - the loop always breaks on the cap branch
        pass

    if not math.isfinite(rho):
        rho = _estimate_rho(absolute)
    certificate = Certificate(
        converged=converged,
        status=status,
        rho_hat=float(rho),
        iterations=len(series),
        final_residual=float(series[-1]) if series else float("nan"),
        residual_series=tuple(float(r) for r in series),
        scheme=scheme,
        relaxation=relaxation,
        omega=float(omega),
        reason=reason,
        metadata=dict(metadata or {}),
    )
    return x, certificate


def solve_fixed_point(
    map_fn: Callable[[np.ndarray], np.ndarray],
    x0: Sequence[float],
    *,
    scheme: str = "jacobi",
    relaxation: str = "none",
    omega0: float = 1.0,
    tolerance: float = 1e-10,
    max_iterations: int = 500,
    rho_max: float = 1.0,
    patience: int = 2,
    omega_bounds: Tuple[float, float] = (1e-3, 5.0),
) -> Tuple[np.ndarray, Certificate]:
    """Solve ``x = map_fn(x)`` with a certified fixed-point iteration."""
    if scheme != "jacobi":
        raise ValueError("solve_fixed_point supports scheme='jacobi'; use solve_coupled for blocks")
    if relaxation not in ("none", "aitken"):
        raise ValueError(f"unknown relaxation: {relaxation!r}")
    if tolerance <= 0.0:
        raise ValueError("tolerance must be positive")

    def _sweep(x: np.ndarray) -> np.ndarray:
        return np.asarray(map_fn(x), dtype=float).ravel()

    return _run_loop(
        _sweep,
        np.asarray(x0, dtype=float).ravel(),
        scheme=scheme,
        relaxation=relaxation,
        omega0=omega0,
        tolerance=tolerance,
        max_iterations=max_iterations,
        rho_max=rho_max,
        patience=patience,
        omega_bounds=omega_bounds,
        metadata={"map": getattr(map_fn, "__name__", "anonymous")},
    )


def solve_coupled(
    blocks: Sequence[Block],
    x0: Sequence[float],
    *,
    scheme: str = "gauss_seidel",
    relaxation: str = "none",
    omega0: float = 1.0,
    tolerance: float = 1e-10,
    max_iterations: int = 500,
    rho_max: float = 1.0,
    patience: int = 2,
    omega_bounds: Tuple[float, float] = (1e-3, 5.0),
) -> Tuple[np.ndarray, Certificate]:
    """Block-coupled fixed point: Jacobi (stale) or Gauss-Seidel (fresh) sweeps.

    ``blocks`` is a sequence of ``(indices, apply_fn)`` where ``apply_fn`` maps the
    *full* current state to the new values of that block.  Gauss-Seidel evaluates
    block ``k`` on a state in which blocks ``0..k-1`` have already been updated.
    """
    if scheme not in ("jacobi", "gauss_seidel"):
        raise ValueError(f"unknown scheme: {scheme!r}")
    if relaxation not in ("none", "aitken"):
        raise ValueError(f"unknown relaxation: {relaxation!r}")
    if not blocks:
        raise ValueError("at least one block is required")
    start = np.asarray(x0, dtype=float).ravel()
    indices = [np.asarray(idx, dtype=int).ravel() for idx, _ in blocks]
    covered = np.concatenate(indices)
    if covered.size != start.size or sorted(covered.tolist()) != list(range(start.size)):
        raise ValueError("blocks must partition the state exactly once each")

    def _sweep(x: np.ndarray) -> np.ndarray:
        if scheme == "jacobi":
            out = np.empty_like(x)
            for idx, apply_fn in blocks:
                out[idx] = np.asarray(apply_fn(x), dtype=float).ravel()
            return out
        work = x.copy()
        for idx, apply_fn in blocks:
            work[idx] = np.asarray(apply_fn(work), dtype=float).ravel()
        return work

    return _run_loop(
        _sweep,
        start,
        scheme=scheme,
        relaxation=relaxation,
        omega0=omega0,
        tolerance=tolerance,
        max_iterations=max_iterations,
        rho_max=rho_max,
        patience=patience,
        omega_bounds=omega_bounds,
        metadata={"n_blocks": len(blocks)},
    )


def linear_blocks(
    matrix: Sequence[Sequence[float]],
    rhs: Sequence[float],
    splits: Sequence[Sequence[int]],
) -> List[Block]:
    """Partition ``x = A x + b`` into blocks for :func:`solve_coupled`."""
    a = np.asarray(matrix, dtype=float)
    b = np.asarray(rhs, dtype=float).ravel()
    if a.ndim != 2 or a.shape[0] != a.shape[1]:
        raise ValueError("matrix must be square")
    if a.shape[0] != b.size:
        raise ValueError("rhs size must match matrix")
    blocks: List[Block] = []
    for block_index, rows in enumerate(splits):
        rows_arr = np.asarray(rows, dtype=int).ravel()
        if rows_arr.size == 0:
            raise ValueError(f"block {block_index} is empty")

        def _apply(state: np.ndarray, rows_arr=rows_arr) -> np.ndarray:
            return a[rows_arr, :] @ state + b[rows_arr]

        blocks.append((rows_arr, _apply))
    return blocks


def solve_linear_coupled(
    matrix: Sequence[Sequence[float]],
    rhs: Sequence[float],
    *,
    scheme: str = "jacobi",
    splits: Optional[Sequence[Sequence[int]]] = None,
    **kwargs: Any,
) -> Tuple[np.ndarray, Certificate]:
    """Solve ``x = A x + b`` and return the certificate alongside the solution."""
    a = np.asarray(matrix, dtype=float)
    b = np.asarray(rhs, dtype=float).ravel()
    x0 = kwargs.pop("x0", np.zeros(b.size))
    if scheme == "jacobi":
        return solve_fixed_point(lambda x: a @ x + b, x0, scheme="jacobi", **kwargs)
    if splits is None:
        splits = [np.arange(b.size)] if b.size < 2 else [np.array([k]) for k in range(b.size)]
    blocks = linear_blocks(a, b, splits)
    return solve_coupled(blocks, x0, scheme=scheme, **kwargs)


def meets_loop_gate(certificate: Certificate, loop_gate: Dict[str, Any]) -> Dict[str, Any]:
    """Check a certificate against a contract's ``loop_gate`` block."""
    rho_max = float(loop_gate.get("rho_max", 1.0))
    checks = {
        "converged": bool(certificate.converged),
        "status_is_converged": certificate.status == CONVERGED,
        "rho_below_max": bool(math.isfinite(certificate.rho_hat) and certificate.rho_hat < rho_max),
        "within_iteration_cap": certificate.iterations <= int(loop_gate.get("max_iterations", 0) or 10 ** 9),
        "residual_below_tolerance": bool(
            certificate.final_residual <= float(loop_gate.get("tolerance", 0.0) or 0.0)
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "rho_hat": certificate.rho_hat,
        "rho_max": rho_max,
        "iterations": certificate.iterations,
    }