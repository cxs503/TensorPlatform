"""Reference test problems with closed-form optima (domain-free).

Every entry carries the analytic optimum so a run is judged against an external
reference, never against "the optimizer improved the value".  Used by
``optimizer/gopt/validation/run_benchmarks.py`` and by
``tests/test_gopt_*.py``.

Sources: Hansen (2016) arXiv:1604.00772 (sphere/ellipsoid/Rosenbrock/Cigar/
Tablet); Momin & Yang (2013) for Rastrigin/Ackley conventions; Nocedal & Wright
(2006) *Numerical Optimization*, Example 15.1 / 17.3 for the constrained cases
(their KKT points are reproduced exactly below).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np

from .interfaces import ConstraintSet, FunctionObjective, Objective

__all__ = [
    "BenchmarkProblem",
    "BENCHMARKS",
    "get_benchmark",
    "constrained_quadratic_eq",
    "nocedal_17_3",
    "box_quadratic",
    "eq_ineq_quadratic",
]


@dataclass
class BenchmarkProblem:
    key: str
    dim: int
    fun: Callable[[np.ndarray], float]
    jac: Optional[Callable[[np.ndarray], np.ndarray]]
    optimum_x: np.ndarray
    optimum_f: float
    bounds: Optional[np.ndarray] = None
    constraints: Optional[ConstraintSet] = None
    f_tol_abs: float = 1e-8
    x_tol: float = 1e-4
    note: str = ""

    def objective(self) -> Objective:
        return FunctionObjective(self.fun, self.jac, name=self.key)


# ---------------------------------------------------------------------------
# Unconstrained (closed-form optima)
# ---------------------------------------------------------------------------
def _sphere(x):
    return float(np.sum(x ** 2))


def _sphere_jac(x):
    return 2.0 * x


def _ellipsoid(n: int, cond: float = 1e3):
    w = np.logspace(0, np.log10(cond), n)

    def f(x):
        return float(np.sum(w * x ** 2))

    def g(x):
        return 2.0 * w * x

    return f, g


def _rosenbrock(x):
    return float(np.sum(100.0 * (x[1:] - x[:-1] ** 2) ** 2 + (1 - x[:-1]) ** 2))


def _rosenbrock_jac(x):
    n = x.size
    g = np.zeros(n)
    g[:-1] = -400.0 * x[:-1] * (x[1:] - x[:-1] ** 2) - 2.0 * (1 - x[:-1])
    g[1:] += 200.0 * (x[1:] - x[:-1] ** 2)
    return g


def _cigar(x):
    return float(x[0] ** 2 + 1e5 * np.sum(x[1:] ** 2))


def _cigar_jac(x):
    g = 2.0 * 1e5 * x
    g[0] = 2.0 * x[0]
    return g


def _tablet(x):
    return float(1e5 * x[0] ** 2 + np.sum(x[1:] ** 2))


def _tablet_jac(x):
    g = 2.0 * x
    g[0] = 2.0 * 1e5 * x[0]
    return g


def _rastrigin(x):
    n = x.size
    return float(10 * n + np.sum(x ** 2 - 10 * np.cos(2 * np.pi * x)))


def _rastrigin_jac(x):
    return 2 * x + 20 * np.pi * np.sin(2 * np.pi * x)


# ---------------------------------------------------------------------------
# Constrained (analytic KKT points)
# ---------------------------------------------------------------------------
def constrained_quadratic_eq(n: int = 2):
    """min sum x_i^2 s.t. sum x_i = n  ->  x* = 1, f* = n."""

    def f(x):
        return float(np.sum(x ** 2))

    def g(x):
        return 2.0 * x

    def ce(x):
        return np.array([np.sum(x) - x.size])

    def ce_jac(x):
        return np.ones((1, x.size))

    return BenchmarkProblem(
        key="eq_quadratic",
        dim=n,
        fun=f,
        jac=g,
        optimum_x=np.ones(n),
        optimum_f=float(n),
        constraints=ConstraintSet(eq=ce, eq_jac=ce_jac),
        note="equality-constrained quadratic; KKT point x=1 (Nocedal-Wright style)",
    )


def nocedal_17_3():
    """Nocedal & Wright (2006) Example 17.3.

    min (x1-2)^2 + (x2-1)^2
    s.t. x1^2/4 + x2^2 - 1 <= 0   (written here as 1 - x1^2/4 - x2^2 >= 0)
         x2 - x1 - 1 >= 0  (equivalent to x1 - 2 x2 <= -1 in the book)
    optimum x* = (0.8228757, 0.9114378), f* = 1.3934648
    """

    def f(x):
        return float((x[0] - 2) ** 2 + (x[1] - 1) ** 2)

    def g(x):
        return np.array([2 * (x[0] - 2), 2 * (x[1] - 1)])

    def ci(x):
        return np.array([1.0 - x[0] ** 2 / 4.0 - x[1] ** 2, x[2] if x.size > 2 else 0.0])

    def f_vec(x):
        return float((x[0] - 2) ** 2 + (x[1] - 1) ** 2)

    # Use exactly the book's constraints: c1: x1^2/4 + x2^2 - 1 <= 0 -> ineq >=0 form
    #                                       c2: x1 - 2 x2 <= -1        -> -(x1 - 2x2 + 1) >= 0
    def ci2(x):
        return np.array([1.0 - x[0] ** 2 / 4.0 - x[1] ** 2, -(x[0] - 2.0 * x[1] + 1.0)])

    def ci2_jac(x):
        return np.array([[-x[0] / 2.0, -2.0 * x[1]], [-1.0, 2.0]])

    x_star = np.array([0.8228757, 0.9114378])
    return BenchmarkProblem(
        key="nocedal_17_3",
        dim=2,
        fun=f_vec,
        jac=g,
        optimum_x=x_star,
        optimum_f=float((x_star[0] - 2) ** 2 + (x_star[1] - 1) ** 2),
        constraints=ConstraintSet(ineq=ci2, ineq_jac=ci2_jac),
        f_tol_abs=1e-7,
        x_tol=1e-4,
        note="two active inequalities; analytic KKT point from Nocedal & Wright (2006)",
    )


def box_quadratic():
    """min (x1-2)^2 + (x2-1)^2 with box [-1, 1]^2.

    Optimum by inspection: x* = (1, 1) (the x1 optimum 2 lies outside the box and
    is clipped to the upper bound), f* = 1.
    """

    def f(x):
        return float((x[0] - 2.0) ** 2 + (x[1] - 1.0) ** 2)

    def g(x):
        return np.array([2.0 * (x[0] - 2.0), 2.0 * (x[1] - 1.0)])

    return BenchmarkProblem(
        key="box_quadratic",
        dim=2,
        fun=f,
        jac=g,
        optimum_x=np.array([1.0, 1.0]),
        optimum_f=1.0,
        bounds=np.array([[-1.0, 1.0], [-1.0, 1.0]]),
        f_tol_abs=1e-10,
        x_tol=1e-6,
        note="active upper bound; optimum verifiable by inspection",
    )


def eq_ineq_quadratic():
    """min x1^2 + x2^2 s.t. x1 - x2 = 0 (equality), x1 + x2 >= 2 (inequality).

    KKT solution by hand: the inequality is active, stationarity gives x = (1, 1)
    with objective 2 and multipliers (nu=0, mu=1) -- i.e. one active inequality and
    one equality, the smallest non-trivial mixed case.
    """

    def f(x):
        return float(np.sum(x ** 2))

    def g(x):
        return 2.0 * x

    def ce(x):
        return np.array([x[0] - x[1]])

    def ce_jac(x):
        return np.array([[1.0, -1.0]])

    def ci(x):
        return np.array([x[0] + x[1] - 2.0])

    def ci_jac(x):
        return np.array([[1.0, 1.0]])

    return BenchmarkProblem(
        key="eq_ineq_quadratic",
        dim=2,
        fun=f,
        jac=g,
        optimum_x=np.array([1.0, 1.0]),
        optimum_f=2.0,
        constraints=ConstraintSet(eq=ce, eq_jac=ce_jac, ineq=ci, ineq_jac=ci_jac),
        f_tol_abs=1e-10,
        x_tol=1e-6,
        note="mixed equality + active inequality, analytic KKT point",
    )


BENCHMARKS: Dict[str, BenchmarkProblem] = {
    "sphere2": BenchmarkProblem(
        key="sphere2", dim=2, fun=_sphere, jac=_sphere_jac,
        optimum_x=np.zeros(2), optimum_f=0.0,
        bounds=np.array([[-5.0, 5.0], [-5.0, 5.0]]), f_tol_abs=1e-10, x_tol=1e-4,
    ),
    "sphere10": BenchmarkProblem(
        key="sphere10", dim=10, fun=_sphere, jac=_sphere_jac,
        optimum_x=np.zeros(10), optimum_f=0.0,
        bounds=np.tile([-5.0, 5.0], (10, 1)), f_tol_abs=1e-10, x_tol=1e-4,
    ),
    "ellipsoid5": BenchmarkProblem(
        key="ellipsoid5", dim=5, fun=_ellipsoid(5, 1e3)[0], jac=_ellipsoid(5, 1e3)[1],
        optimum_x=np.zeros(5), optimum_f=0.0,
        bounds=np.tile([-5.0, 5.0], (5, 1)), f_tol_abs=1e-8, x_tol=1e-3,
    ),
    "cigar5": BenchmarkProblem(
        key="cigar5", dim=5, fun=_cigar, jac=_cigar_jac,
        optimum_x=np.zeros(5), optimum_f=0.0,
        bounds=np.tile([-5.0, 5.0], (5, 1)), f_tol_abs=1e-6, x_tol=1e-3,
    ),
    "tablet5": BenchmarkProblem(
        key="tablet5", dim=5, fun=_tablet, jac=_tablet_jac,
        optimum_x=np.zeros(5), optimum_f=0.0,
        bounds=np.tile([-5.0, 5.0], (5, 1)), f_tol_abs=1e-6, x_tol=1e-3,
    ),
    "rosenbrock2": BenchmarkProblem(
        key="rosenbrock2", dim=2, fun=_rosenbrock, jac=_rosenbrock_jac,
        optimum_x=np.ones(2), optimum_f=0.0,
        bounds=np.array([[-2.0, 2.0], [-2.0, 2.0]]), f_tol_abs=1e-8, x_tol=1e-3,
    ),
    "rosenbrock5": BenchmarkProblem(
        key="rosenbrock5", dim=5, fun=_rosenbrock, jac=_rosenbrock_jac,
        optimum_x=np.ones(5), optimum_f=0.0,
        bounds=np.tile([-2.0, 2.0], (5, 1)), f_tol_abs=1e-6, x_tol=1e-2,
    ),
    "rastrigin5": BenchmarkProblem(
        key="rastrigin5", dim=5, fun=_rastrigin, jac=_rastrigin_jac,
        optimum_x=np.zeros(5), optimum_f=0.0,
        bounds=np.tile([-4.0, 4.0], (5, 1)), f_tol_abs=1e-6, x_tol=1e-1,
        note="multimodal; only reachable with restarts, tolerance relaxed",
    ),
    "eq_quadratic": constrained_quadratic_eq(2),
    "nocedal_17_3": nocedal_17_3(),
    "box_quadratic": box_quadratic(),
    "eq_ineq_quadratic": eq_ineq_quadratic(),
}


def get_benchmark(key: str) -> BenchmarkProblem:
    if key not in BENCHMARKS:
        raise KeyError(f"unknown benchmark {key!r}; known: {sorted(BENCHMARKS)}")
    return BENCHMARKS[key]


def keys(gradient_only: bool = False, constrained_only: bool = False) -> List[str]:
    out = []
    for k, p in BENCHMARKS.items():
        if gradient_only and p.jac is None:
            continue
        if constrained_only and p.constraints is None:
            continue
        out.append(k)
    return sorted(out)