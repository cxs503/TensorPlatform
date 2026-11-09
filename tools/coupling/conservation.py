"""Conservation gates for coupling exchanges (domain-neutral).

A remap is only trustworthy if it can be *shown* to conserve.  The gates here
are the machine-checkable version of that statement:

``sum_rel_residual``
    ``|Σ_src - Σ_dst| / max(|Σ_src|, eps)`` — total (zeroth moment) conservation.
``moment_rel_residual``
    max over dimensions of ``|M_k(src) - M_k(dst)| / (Σ|F|·V · L_k)`` — first
    moment (lever-arm) conservation.  Only meaningful when both fields live on the
    same domain; the normaliser keeps the ratio dimensionless and finite even for
    sign-changing fields.
``min_value_dst`` / ``nonneg_violation``
    Positivity: the most negative destination value and how far it is below zero
    relative to the source scale.
``overshoot_ratio``
    ``max(|F_dst|) / max(|F_src|)`` — a convex (overlap) projection can never
    exceed 1; an interpolant that overshoots shows up here.

Each metric carries its own tolerance so a gate cannot be loosened implicitly by
changing another gate's threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Any, Dict, Optional

import numpy as np

from .mesh import Field

__all__ = ["ConservationMetrics", "GateResult", "measure", "check"]


@dataclass
class ConservationMetrics:
    sum_src: float
    sum_dst: float
    sum_rel_residual: float
    moment_src: np.ndarray
    moment_dst: np.ndarray
    moment_rel_residual: float
    min_src: float
    min_dst: float
    max_src: float
    max_dst: float
    nonneg_violation: float
    overshoot_ratio: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sum_src": self.sum_src,
            "sum_dst": self.sum_dst,
            "sum_rel_residual": self.sum_rel_residual,
            "moment_src": self.moment_src.tolist(),
            "moment_dst": self.moment_dst.tolist(),
            "moment_rel_residual": self.moment_rel_residual,
            "min_src": self.min_src,
            "min_dst": self.min_dst,
            "max_src": self.max_src,
            "max_dst": self.max_dst,
            "nonneg_violation": self.nonneg_violation,
            "overshoot_ratio": self.overshoot_ratio,
        }


@dataclass
class GateResult:
    passed: bool
    failures: list = _dc_field(default_factory=list)
    metrics: Optional[ConservationMetrics] = None
    thresholds: Dict[str, float] = _dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "failures": list(self.failures),
            "thresholds": dict(self.thresholds),
            "metrics": self.metrics.to_dict() if self.metrics else None,
        }

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.passed


def measure(src: Field, dst: Field) -> ConservationMetrics:
    """Compute the conservation metrics between a source and remapped field."""
    sum_src, sum_dst = src.total, dst.total
    scale_sum = max(abs(sum_src), abs(sum_dst), 1e-300)
    sum_res = abs(sum_dst - sum_src) / scale_sum

    m_src, m_dst = src.first_moment(), dst.first_moment()
    extent = np.maximum(dst.mesh.extent, src.mesh.extent)
    extent = np.where(extent > 0.0, extent, 1.0)
    mass_abs = float(np.sum(np.abs(src.values) * src.weights))
    norm = max(mass_abs, 1e-300) * extent
    moment_res = float(np.max(np.abs(m_dst - m_src) / norm))

    min_src, min_dst = float(src.values.min()), float(dst.values.min())
    max_src, max_dst = float(src.values.max()), float(dst.values.max())
    value_scale = max(abs(min_src), abs(max_src), 1e-300)
    nonneg = abs(min(min_dst, 0.0)) / value_scale
    mag_src = max(abs(min_src), abs(max_src), 1e-300)
    overshoot = max(abs(min_dst), abs(max_dst)) / mag_src

    return ConservationMetrics(
        sum_src=sum_src,
        sum_dst=sum_dst,
        sum_rel_residual=float(sum_res),
        moment_src=np.asarray(m_src, dtype=float),
        moment_dst=np.asarray(m_dst, dtype=float),
        moment_rel_residual=moment_res,
        min_src=min_src,
        min_dst=min_dst,
        max_src=max_src,
        max_dst=max_dst,
        nonneg_violation=float(nonneg),
        overshoot_ratio=float(overshoot),
    )


def check(
    src: Field,
    dst: Field,
    sum_tol: float = 1e-12,
    moment_tol: float = 1e-10,
    nonneg_tol: float = 1e-12,
    overshoot_tol: float = 1e-12,
) -> GateResult:
    """Apply every conservation gate; ``passed`` is the conjunction of all of them."""
    metrics = measure(src, dst)
    thresholds = {
        "sum_rel_residual": sum_tol,
        "moment_rel_residual": moment_tol,
        "nonneg_violation": nonneg_tol,
        "overshoot_ratio": 1.0 + overshoot_tol,
    }
    failures = []
    if not (metrics.sum_rel_residual <= sum_tol):
        failures.append(
            f"sum_rel_residual {metrics.sum_rel_residual:.3e} > {sum_tol:.3e}"
        )
    if not (metrics.moment_rel_residual <= moment_tol):
        failures.append(
            f"moment_rel_residual {metrics.moment_rel_residual:.3e} > {moment_tol:.3e}"
        )
    if not (metrics.nonneg_violation <= nonneg_tol):
        failures.append(
            f"nonneg_violation {metrics.nonneg_violation:.3e} > {nonneg_tol:.3e}"
        )
    if not (metrics.overshoot_ratio <= 1.0 + overshoot_tol):
        failures.append(
            f"overshoot_ratio {metrics.overshoot_ratio:.6f} > {1.0 + overshoot_tol:.6f}"
        )
    return GateResult(passed=not failures, failures=failures, metrics=metrics, thresholds=thresholds)