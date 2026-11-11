"""System-sensitivity consistency gate for block-coupled designs (domain-neutral).

Why a *sensivity* gate on top of a convergence certificate
----------------------------------------------------------
A coupled loop can converge to the right fixed point and still be *designed*
inconsistently: each discipline may ship its own account of how it responds to
the exchanged (coupling) variables, and nothing guarantees that this account is
the derivative of what the discipline actually does.  A partitioned design with
a stale interface Jacobian converges (the loop only uses evaluations) but every
system-level derivative computed from it is wrong, silently.

The gate below makes the statement machine-checkable.  For one scalar parameter
``p`` and one scalar functional ``F`` of the coupled state, it computes

``total``
    :math:`dF/dp` from *re-solving* the coupled loop at ``p ± h`` and taking a
    central difference of ``F``.  Only discipline evaluations are used, so this
    is the ground truth of the coupled system.
``composed``
    :math:`dF/dp = \\partial F/\\partial p + \\partial F/\\partial x\\,(I-K)^{-1} g`
    where ``K`` and ``g`` are assembled from the *per-discipline* partial
    derivatives taken with the coupling variables frozen at the converged state
    (:math:`K_{ij} = \\partial f_i/\\partial x_j`, :math:`g_i = \\partial f_i/\\partial p`).
    This is the derivative a modular design would quote.
``analytic``
    closed-form :math:`dF/dp` when the fixture admits one.

The gate fails when ``|composed - total| / max(|composed|, |total|)`` exceeds
``rtol`` (2 % by default).  Because a discipline may *declare* its partials
(``DeclaredPartials``) instead of having them measured by finite differences, a
wrong design Jacobian is caught rather than averaged away; the
``scale_declared_partials`` helper builds exactly such a fault without touching
the physics, which is what makes the gate falsifiable.

Everything here is domain-neutral: a discipline is a black box
``(state, parameters) -> own block``, the parameter is a vector position and the
output is an opaque functional of the state.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..coupled_loop import CONVERGED, Certificate, solve_coupled

__all__ = [
    "CoupledSystem",
    "DeclaredPartials",
    "Discipline",
    "LinearTwoDisciplineSystem",
    "MAX_RESOLVENT_CONDITION",
    "SensitivityError",
    "compose_sensitivity",
    "constant_partials",
    "frozen_partials",
    "linear_two_discipline_system",
    "measure_output_gradient",
    "nonlinear_two_discipline_system",
    "resolve_sensitivity",
    "scale_declared_partials",
    "sensitivity_consistency_gate",
]

Update = Callable[[np.ndarray, np.ndarray], np.ndarray]


#: A composed derivative is only quoted when the coupling response matrix is
#: invertible to better than this condition number: at 1e8 a double-precision
#: solve keeps ~8 significant digits, enough to judge a percent-level tolerance.
MAX_RESOLVENT_CONDITION = 1e8


class SensitivityError(ValueError):
    """Raised when a sensitivity quantity is not defined (fail-closed)."""


def _fd_step(value: float, relative: float = 1e-6, floor: float = 1e-9) -> float:
    """Central-difference step, scaled to the magnitude of the perturbed quantity."""
    return float(relative * max(abs(float(value)), 1.0) + floor)


# ---------------------------------------------------------------------------
# disciplines and systems
# ---------------------------------------------------------------------------


@dataclass
class DeclaredPartials:
    """A discipline's own account of its derivatives, as a modular design ships it.

    Both callables receive the *frozen* converged state and parameter vector and
    return ``(state_size, n_state)`` and ``(state_size, n_params)`` respectively.
    """

    state_jacobian: Callable[[np.ndarray, np.ndarray], np.ndarray]
    param_jacobian: Callable[[np.ndarray, np.ndarray], np.ndarray]


def constant_partials(
    state_jacobian: Sequence[Sequence[float]],
    param_jacobian: Sequence[float],
) -> DeclaredPartials:
    """Declared partials that do not depend on the state or the parameter."""
    k = np.asarray(state_jacobian, dtype=float)
    g = np.asarray(param_jacobian, dtype=float).ravel()
    return DeclaredPartials(
        state_jacobian=lambda x, p, k=k: k.copy(),
        param_jacobian=lambda x, p, g=g: g.reshape(-1, 1).copy(),
    )


@dataclass
class Discipline:
    """One black-box block of a coupled system.

    ``update(state, params)`` returns this discipline's own block of the next
    iterate; it may read the full state (that is how coupling enters) and the
    parameter vector.  ``declared_partials`` is optional: when a design ships its
    interface Jacobian, the gate uses it instead of measuring the derivative of
    ``update`` — which is exactly how a wrong Jacobian becomes visible.
    """

    name: str
    update: Update
    state_size: int = 1
    declared_partials: Optional[DeclaredPartials] = None

    def evaluate(self, state: np.ndarray, params: np.ndarray) -> np.ndarray:
        out = np.asarray(self.update(np.asarray(state, dtype=float), np.asarray(params, dtype=float)), dtype=float)
        out = out.ravel()
        if out.size != self.state_size:
            raise SensitivityError(
                f"discipline {self.name!r} returned {out.size} values, declared {self.state_size}"
            )
        return out


@dataclass
class CoupledSystem:
    """A block-partitioned fixed point ``x = f(x; p)`` plus a scalar functional ``F``."""

    disciplines: Sequence[Discipline]
    output: Callable[[np.ndarray, np.ndarray], float]
    name: str = "coupled_system"

    def __post_init__(self) -> None:
        if not self.disciplines:
            raise SensitivityError("at least one discipline is required")
        names = [d.name for d in self.disciplines]
        if len(set(names)) != len(names):
            raise SensitivityError("discipline names must be unique")
        for d in self.disciplines:
            if int(d.state_size) < 1:
                raise SensitivityError(f"discipline {d.name!r} has a non-positive state size")

    # -- geometry -----------------------------------------------------------
    @property
    def sizes(self) -> List[int]:
        return [int(d.state_size) for d in self.disciplines]

    @property
    def size(self) -> int:
        return int(sum(self.sizes))

    def splits(self) -> List[np.ndarray]:
        out: List[np.ndarray] = []
        start = 0
        for s in self.sizes:
            out.append(np.arange(start, start + s))
            start += s
        return out

    # -- evaluation ---------------------------------------------------------
    def map(self, state: np.ndarray, params: np.ndarray) -> np.ndarray:
        state = np.asarray(state, dtype=float).ravel()
        params = np.asarray(params, dtype=float).ravel()
        if state.size != self.size:
            raise SensitivityError(f"state has size {state.size}, expected {self.size}")
        return np.concatenate([d.evaluate(state, params) for d in self.disciplines])

    def value(self, state: np.ndarray, params: np.ndarray) -> float:
        raw = self.output(np.asarray(state, dtype=float).ravel(), np.asarray(params, dtype=float).ravel())
        arr = np.asarray(raw, dtype=float).ravel()
        if arr.size != 1:
            raise SensitivityError(f"output functional must be scalar, got {arr.size} values")
        return float(arr[0])

    def solve(
        self,
        params: Optional[Sequence[float]] = None,
        x0: Optional[Sequence[float]] = None,
        **kwargs: Any,
    ) -> Tuple[np.ndarray, Certificate]:
        """Converge the coupled loop; the parameter vector is a constant here."""
        params = np.zeros(1) if params is None else np.asarray(params, dtype=float).ravel()
        start = np.zeros(self.size) if x0 is None else np.asarray(x0, dtype=float).ravel()
        blocks = [
            (idx, (lambda state, d=d, p=params: d.evaluate(state, p)))
            for idx, d in zip(self.splits(), self.disciplines)
        ]
        options = dict(scheme="jacobi", tolerance=1e-12, max_iterations=2000, rho_max=1.0)
        options.update(kwargs)
        return solve_coupled(blocks, start, **options)


# ---------------------------------------------------------------------------
# frozen per-discipline partials
# ---------------------------------------------------------------------------


def frozen_partials(
    system: CoupledSystem,
    state: Sequence[float],
    params: Sequence[float],
    *,
    param_index: int = 0,
    step: float = 1e-6,
) -> Tuple[np.ndarray, np.ndarray, List[Dict[str, Any]]]:
    """Assemble ``K`` and ``g`` discipline by discipline, coupling variables frozen.

    Each discipline is differentiated *alone*: the state and the parameter vector
    are held at the converged values and the discipline's own outputs are
    perturbed.  A discipline that declares its partials contributes the declared
    values; otherwise ``update`` is differentiated by central differences.
    """
    x = np.asarray(state, dtype=float).ravel()
    p = np.asarray(params, dtype=float).ravel()
    if x.size != system.size:
        raise SensitivityError(f"state has size {x.size}, expected {system.size}")
    if not (-p.size <= param_index < p.size):
        raise SensitivityError(f"param_index {param_index} outside a parameter vector of size {p.size}")

    n = x.size
    k_matrix = np.zeros((n, n))
    g_vector = np.zeros(n)
    records: List[Dict[str, Any]] = []

    for discipline, idx in zip(system.disciplines, system.splits()):
        rows = idx  # produce these components
        if discipline.declared_partials is not None:
            block = np.asarray(discipline.declared_partials.state_jacobian(x, p), dtype=float)
            gblock = np.asarray(discipline.declared_partials.param_jacobian(x, p), dtype=float)
            gblock = gblock.reshape(discipline.state_size, -1)[:, param_index]
            source = "declared"
            if block.shape != (discipline.state_size, n):
                raise SensitivityError(
                    f"declared state Jacobian of {discipline.name!r} has shape {block.shape}, "
                    f"expected {(discipline.state_size, n)}"
                )
        else:
            block = np.zeros((discipline.state_size, n))
            for j in range(n):
                h = _fd_step(x[j], relative=step)
                xp = x.copy()
                xp[j] += h
                xm = x.copy()
                xm[j] -= h
                block[:, j] = (discipline.evaluate(xp, p) - discipline.evaluate(xm, p)) / (2.0 * h)
            hp = _fd_step(p[param_index], relative=step)
            pp = p.copy()
            pp[param_index] += hp
            pm = p.copy()
            pm[param_index] -= hp
            gblock = (discipline.evaluate(x, pp) - discipline.evaluate(x, pm)) / (2.0 * hp)
            source = "measured"

        k_matrix[np.ix_(rows, np.arange(n))] = block
        g_vector[rows] = gblock
        records.append(
            {
                "discipline": discipline.name,
                "source": source,
                "state_jacobian": block.tolist(),
                "param_derivative": gblock.tolist(),
            }
        )
    return k_matrix, g_vector, records


def measure_output_gradient(
    system: CoupledSystem,
    state: Sequence[float],
    params: Sequence[float],
    *,
    param_index: int = 0,
    step: float = 1e-6,
) -> Tuple[np.ndarray, float]:
    """``(dF/dx, dF/dp)`` of the output functional at one point, by central differences."""
    x = np.asarray(state, dtype=float).ravel()
    p = np.asarray(params, dtype=float).ravel()
    grad = np.zeros(x.size)
    for j in range(x.size):
        h = _fd_step(x[j], relative=step)
        xp, xm = x.copy(), x.copy()
        xp[j] += h
        xm[j] -= h
        grad[j] = (system.value(xp, p) - system.value(xm, p)) / (2.0 * h)
    hp = _fd_step(p[param_index], relative=step)
    pp, pm = p.copy(), p.copy()
    pp[param_index] += hp
    pm[param_index] -= hp
    d_param = (system.value(x, pp) - system.value(x, pm)) / (2.0 * hp)
    return grad, float(d_param)


# ---------------------------------------------------------------------------
# the two derivative paths
# ---------------------------------------------------------------------------


def compose_sensitivity(
    system: CoupledSystem,
    state: Sequence[float],
    params: Sequence[float],
    *,
    param_index: int = 0,
    step: float = 1e-6,
) -> Dict[str, Any]:
    """``dF/dp`` composed from frozen per-discipline partials through ``(I-K)^{-1}``."""
    x = np.asarray(state, dtype=float).ravel()
    p = np.asarray(params, dtype=float).ravel()
    k_matrix, g_vector, records = frozen_partials(
        system, x, p, param_index=param_index, step=step
    )
    resolvent = np.eye(x.size) - k_matrix
    condition = float(np.linalg.cond(resolvent))
    if not math.isfinite(condition) or condition > MAX_RESOLVENT_CONDITION:
        raise SensitivityError(
            "singular coupling response matrix: (I-K) has condition number "
            f"{condition:.3e}, so the composed derivative is not defined"
        )
    try:
        state_sensitivity = np.linalg.solve(resolvent, g_vector)
    except np.linalg.LinAlgError as exc:  # pragma: no cover - guarded above
        raise SensitivityError(f"singular coupling response matrix: {exc}") from exc
    grad, d_param = measure_output_gradient(system, x, p, param_index=param_index, step=step)
    return {
        "value": float(d_param + grad @ state_sensitivity),
        "state_sensitivity": state_sensitivity.tolist(),
        "output_grad_state": grad.tolist(),
        "output_grad_param": float(d_param),
        "coupling_jacobian": k_matrix.tolist(),
        "param_derivative": g_vector.tolist(),
        "disciplines": records,
        "resolvent_condition": condition,
    }


def resolve_sensitivity(
    system: CoupledSystem,
    params: Sequence[float],
    *,
    param_index: int = 0,
    step: float = 1e-6,
    x0: Optional[Sequence[float]] = None,
    solve_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """``dF/dp`` from re-solving the coupled loop at ``p ± h`` (ground truth)."""
    p = np.asarray(params, dtype=float).ravel()
    h = _fd_step(p[param_index], relative=step)
    pp, pm = p.copy(), p.copy()
    pp[param_index] += h
    pm[param_index] -= h
    options = dict(solve_kwargs or {})
    if x0 is not None:
        options.setdefault("x0", np.asarray(x0, dtype=float).ravel())
    x_plus, cert_plus = system.solve(pp, **options)
    x_minus, cert_minus = system.solve(pm, **options)
    f_plus = system.value(x_plus, pp)
    f_minus = system.value(x_minus, pm)
    return {
        "value": float((f_plus - f_minus) / (2.0 * h)),
        "step": float(h),
        "state_plus": x_plus.tolist(),
        "state_minus": x_minus.tolist(),
        "output_plus": float(f_plus),
        "output_minus": float(f_minus),
        "converged_plus": bool(cert_plus.converged),
        "converged_minus": bool(cert_minus.converged),
        "iterations_plus": int(cert_plus.iterations),
    }


def _relative_gap(a: float, b: float) -> float:
    scale = max(abs(a), abs(b), 1e-300)
    return float(abs(a - b) / scale)


def sensitivity_consistency_gate(
    system: CoupledSystem,
    params: Optional[Sequence[float]] = None,
    *,
    param_index: int = 0,
    rtol: float = 0.02,
    step: float = 1e-6,
    analytic: Optional[float] = None,
    x0: Optional[Sequence[float]] = None,
    solve_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Gate: composed derivative vs re-solved derivative (and analytic when given)."""
    p = np.zeros(1) if params is None else np.asarray(params, dtype=float).ravel()
    x_star, certificate = system.solve(p, x0=x0, **(solve_kwargs or {}))
    if certificate.status != CONVERGED:
        raise SensitivityError(
            f"coupled loop did not converge ({certificate.status}: {certificate.reason}); "
            "a sensitivity gate needs a converged state"
        )
    composed = compose_sensitivity(system, x_star, p, param_index=param_index, step=step)
    total = resolve_sensitivity(
        system, p, param_index=param_index, step=step, x0=x_star, solve_kwargs=solve_kwargs
    )
    rel = _relative_gap(composed["value"], total["value"])
    checks = {"composed_matches_total": bool(rel <= rtol)}
    rel_analytic = None
    if analytic is not None:
        rel_analytic = _relative_gap(float(analytic), total["value"])
        checks["total_matches_analytic"] = bool(rel_analytic <= rtol)
        rel_analytic_composed = _relative_gap(float(analytic), composed["value"])
        checks["composed_matches_analytic"] = bool(rel_analytic_composed <= rtol)
    else:
        rel_analytic_composed = None
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "rtol": float(rtol),
        "composed": float(composed["value"]),
        "total": float(total["value"]),
        "analytic": None if analytic is None else float(analytic),
        "relative_error": float(rel),
        "relative_error_vs_analytic": rel_analytic,
        "relative_error_composed_vs_analytic": rel_analytic_composed,
        "state": x_star.tolist(),
        "rho_hat": float(certificate.rho_hat),
        "iterations": int(certificate.iterations),
        "detail": {"composed": composed, "total": total},
    }


# ---------------------------------------------------------------------------
# fault injection: a wrong interface Jacobian with the physics unchanged
# ---------------------------------------------------------------------------


def scale_declared_partials(
    discipline: Discipline,
    *,
    state_scale: float = 1.0,
    param_scale: float = 1.0,
    step: float = 1e-6,
) -> Discipline:
    """Wrap a discipline so it *declares* scaled (i.e. wrong) partials.

    The discipline's update is untouched, so the coupled fixed point and the
    re-solved derivative are unchanged; only the modular bookkeeping is wrong.
    This is the negative control for the consistency gate.
    """
    inner = discipline
    size = int(inner.state_size)

    def _state_jacobian(x: np.ndarray, p: np.ndarray) -> np.ndarray:
        block = np.zeros((size, x.size))
        for j in range(x.size):
            h = _fd_step(x[j], relative=step)
            xp, xm = x.copy(), x.copy()
            xp[j] += h
            xm[j] -= h
            block[:, j] = (inner.evaluate(xp, p) - inner.evaluate(xm, p)) / (2.0 * h)
        return block * float(state_scale)

    def _param_jacobian(x: np.ndarray, p: np.ndarray) -> np.ndarray:
        out = np.zeros((size, p.size))
        for k in range(p.size):
            h = _fd_step(p[k], relative=step)
            pp, pm = p.copy(), p.copy()
            pp[k] += h
            pm[k] -= h
            out[:, k] = (inner.evaluate(x, pp) - inner.evaluate(x, pm)) / (2.0 * h)
        return out * float(param_scale)

    return Discipline(
        name=f"{inner.name}[declared x{state_scale:g}/p{param_scale:g}]",
        update=inner.update,
        state_size=size,
        declared_partials=DeclaredPartials(_state_jacobian, _param_jacobian),
    )


# ---------------------------------------------------------------------------
# analytic fixtures
# ---------------------------------------------------------------------------


@dataclass
class LinearTwoDisciplineSystem:
    """Analytic anchor: ``x = A x + b0 + b1 p``, ``F = c . x + cp p``.

    The spectral radius of ``A`` (``A`` has zero diagonal, so it is exactly the
    contraction factor of the Jacobi loop) and ``dF/dp`` are available in closed
    form, which lets the loop's measured contraction rate and the gated
    sensitivity both be compared against theory rather than against themselves.
    """

    matrix: np.ndarray
    rhs0: np.ndarray
    rhs1: np.ndarray
    functional: np.ndarray
    functional_param: float = 0.0

    @property
    def spectral_radius(self) -> float:
        return float(max(abs(np.linalg.eigvals(self.matrix))))

    @property
    def analytic_sensitivity(self) -> float:
        resolvent = np.eye(self.matrix.shape[0]) - self.matrix
        return float(self.functional @ np.linalg.solve(resolvent, self.rhs1) + self.functional_param)

    @property
    def analytic_solution_derivative(self) -> np.ndarray:
        resolvent = np.eye(self.matrix.shape[0]) - self.matrix
        return np.linalg.solve(resolvent, self.rhs1)

    def to_system(self) -> CoupledSystem:
        a = np.asarray(self.matrix, dtype=float)
        b0 = np.asarray(self.rhs0, dtype=float)
        b1 = np.asarray(self.rhs1, dtype=float)
        c = np.asarray(self.functional, dtype=float)

        def make(row: int, size: int) -> Discipline:
            def _update(state: np.ndarray, params: np.ndarray, row=row) -> np.ndarray:
                return a[row : row + size, :] @ state + b0[row : row + size] + b1[row : row + size] * params[0]

            return Discipline(name=f"block{row}", update=_update, state_size=size)

        disciplines = [make(0, 1), make(1, 1)]

        def _output(state: np.ndarray, params: np.ndarray) -> float:
            return float(c @ state + self.functional_param * params[0])

        return CoupledSystem(disciplines=disciplines, output=_output, name="linear_two_discipline")


def linear_two_discipline_system(
    rho: float = 0.5,
    *,
    param_scale: float = 1.0,
) -> LinearTwoDisciplineSystem:
    """Two-discipline linear anchor with spectral radius exactly ``rho``."""
    rho = float(rho)
    if not math.isfinite(rho) or rho <= 0.0:
        raise SensitivityError("rho must be a positive finite number")
    a = np.array([[0.0, rho], [rho * param_scale, 0.0]])
    return LinearTwoDisciplineSystem(
        matrix=a,
        rhs0=np.array([1.0, 2.0]),
        rhs1=np.array([2.0, -1.0]),
        functional=np.array([1.5, 0.5]),
        functional_param=0.25,
    )


def nonlinear_two_discipline_system() -> CoupledSystem:
    """Smooth nonlinear two-discipline map (contraction ~0.35) for a measured gate."""

    def first(state: np.ndarray, params: np.ndarray) -> np.ndarray:
        return np.array([0.4 * state[1] + 1.0 + 0.2 * params[0]])

    def second(state: np.ndarray, params: np.ndarray) -> np.ndarray:
        return np.array([0.3 * state[0] / (1.0 + 0.1 * state[0]) + 2.0 * params[0]])

    disciplines = [
        Discipline(name="first", update=first, state_size=1),
        Discipline(name="second", update=second, state_size=1),
    ]

    def output(state: np.ndarray, params: np.ndarray) -> float:
        return float(state[0] ** 2 + 0.5 * state[1] + 0.1 * params[0] ** 2)

    return CoupledSystem(disciplines=disciplines, output=output, name="nonlinear_two_discipline")