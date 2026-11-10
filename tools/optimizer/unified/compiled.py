"""Compiled (callable) form of a :class:`ProblemSpec`.

A :class:`CompiledProblem` bundles the DSL spec with ready-to-call objective /
gradient / constraint functions in the canonical conventions every adapter
receives:

* objective is always **minimised** (``maximize`` is negated once, here);
* inequality constraints are ``g(x) >= 0``;
* equality constraints are ``h(x) == 0``;
* variables are handled as ``numpy.ndarray`` in declared order, with the name
  mapping kept for the standardised result.

Analytic gradients are used when the spec provides them; otherwise a central
finite-difference gradient is supplied on demand (``gradient(x)``) and flagged
with ``has_analytic_gradient = False`` so reports can say which was used.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .contract import ConstraintSpec, ProblemSpec
from .expr import compile_expression, compile_vector_expression

FD_STEP = 1e-6


class CompiledProblem:
    def __init__(self, spec: ProblemSpec):
        self.spec = spec.validate()
        self.names: List[str] = spec.variable_names
        self.n = len(self.names)
        self.index = {name: i for i, name in enumerate(self.names)}
        self.lower = np.array([v.lower for v in spec.variables], dtype=float)
        self.upper = np.array([v.upper for v in spec.variables], dtype=float)
        self.bounds: List[Tuple[float, float]] = list(zip(self.lower.tolist(), self.upper.tolist()))

        # objective -------------------------------------------------------
        self._raw_objective: Callable[[Mapping[str, float]], float] = compile_expression(
            spec.objective.expr, self.names
        )
        self.sign = -1.0 if spec.objective.sense == "maximize" else 1.0
        self.has_analytic_gradient = spec.objective.gradient is not None
        self._raw_gradient = (
            compile_vector_expression(spec.objective.gradient, self.names)
            if spec.objective.gradient is not None
            else None
        )

        # constraints -----------------------------------------------------
        self.ineq: List[Tuple[str, Callable[[Mapping[str, float]], float],
                              Optional[Callable[[Mapping[str, float]], np.ndarray]]]] = []
        self.eq: List[Tuple[str, Callable[[Mapping[str, float]], float],
                             Optional[Callable[[Mapping[str, float]], np.ndarray]]]] = []
        for con in spec.constraints:
            self._add_constraint(con)

    # -- helpers ------------------------------------------------------------
    def _add_constraint(self, con: ConstraintSpec) -> None:
        raw = compile_expression(con.expr, self.names)
        jac = compile_vector_expression(con.jac, self.names) if con.jac is not None else None
        if con.canonical_sense == "eq":
            # h(x) = expr - threshold == 0
            fn = (lambda vals, _raw=raw, _t=con.threshold: float(_raw(vals) - _t))
            self.eq.append((con.name, fn, jac))
            return
        if con.sense == "ge":
            # g(x) = expr - threshold >= 0
            fn = (lambda vals, _raw=raw, _t=con.threshold: float(_raw(vals) - _t))
            jac_g = jac
        else:
            # g(x) = threshold - expr >= 0  ->  Jacobian flips sign as well
            fn = (lambda vals, _raw=raw, _t=con.threshold: float(_t - _raw(vals)))
            if jac is not None:
                jac_g = (lambda vals, _jac=jac: -np.asarray(_jac(vals), dtype=float))
            else:
                jac_g = None
        self.ineq.append((con.name, fn, jac_g))

    # -- mapping helpers ----------------------------------------------------
    def values(self, x: Sequence[float]) -> Dict[str, float]:
        return {name: float(x[i]) for i, name in enumerate(self.names)}

    def x0(self) -> np.ndarray:
        xs = []
        for v in self.spec.variables:
            xs.append(float(v.x0) if v.x0 is not None else 0.5 * (v.lower + v.upper))
        return np.array(xs, dtype=float)

    # -- objective ----------------------------------------------------------
    def objective(self, x: Sequence[float]) -> float:
        """Canonical (minimised) objective value."""
        return self.sign * self._raw_objective(self.values(x))

    def raw_objective(self, x: Sequence[float]) -> float:
        """Objective value in the user's sense (before minimisation sign)."""
        return self._raw_objective(self.values(x))

    def gradient(self, x: Sequence[float]) -> np.ndarray:
        """Canonical objective gradient (analytic if available, else FD)."""
        if self._raw_gradient is not None:
            return self.sign * self._raw_gradient(self.values(x))
        return fd_gradient(lambda z: self.objective(z), np.asarray(x, dtype=float))

    # -- constraints --------------------------------------------------------
    def ineq_values(self, x: Sequence[float]) -> np.ndarray:
        vals = self.values(x)
        return np.array([fn(vals) for _, fn, _ in self.ineq], dtype=float)

    def eq_values(self, x: Sequence[float]) -> np.ndarray:
        vals = self.values(x)
        return np.array([fn(vals) for _, fn, _ in self.eq], dtype=float)

    def ineq_jacobian(self, x: Sequence[float]) -> Optional[np.ndarray]:
        if not self.ineq:
            return np.zeros((0, self.n))
        vals = self.values(x)
        rows = []
        for _, fn, jac in self.ineq:
            if jac is None:
                return None
            rows.append(np.asarray(jac(vals), dtype=float))
        return np.vstack(rows)

    def eq_jacobian(self, x: Sequence[float]) -> Optional[np.ndarray]:
        if not self.eq:
            return np.zeros((0, self.n))
        vals = self.values(x)
        rows = []
        for _, fn, jac in self.eq:
            if jac is None:
                return None
            rows.append(np.asarray(jac(vals), dtype=float))
        return np.vstack(rows)

    # -- violation ----------------------------------------------------------
    def violations(self, x: Sequence[float]) -> Dict[str, float]:
        """Per-constraint violation, zero when satisfied (canonical form)."""
        out: Dict[str, float] = {}
        for (name, _, _), value in zip(self.ineq, self.ineq_values(x)):
            out[name] = float(max(0.0, -value))
        for (name, _, _), value in zip(self.eq, self.eq_values(x)):
            out[name] = float(abs(value))
        return out

    def max_violation(self, x: Sequence[float]) -> float:
        v = self.violations(x)
        return float(max(v.values())) if v else 0.0

    # -- misc ---------------------------------------------------------------
    @property
    def has_constraints(self) -> bool:
        return bool(self.ineq or self.eq)

    def describe(self) -> Dict[str, object]:
        return {
            "problem_id": self.spec.problem_id,
            "n_variables": self.n,
            "n_ineq": len(self.ineq),
            "n_eq": len(self.eq),
            "analytic_gradient": self.has_analytic_gradient,
            "sense": self.spec.objective.sense,
            "fingerprint": self.spec.fingerprint(),
        }


def fd_gradient(fun: Callable[[np.ndarray], float], x: np.ndarray, step: float = FD_STEP) -> np.ndarray:
    """Central finite-difference gradient (used when no analytic form is given)."""
    x = np.asarray(x, dtype=float)
    g = np.zeros_like(x)
    for i in range(x.size):
        h = step * max(1.0, abs(float(x[i])))
        xp = x.copy()
        xm = x.copy()
        xp[i] += h
        xm[i] -= h
        g[i] = (fun(xp) - fun(xm)) / (2.0 * h)
    return g