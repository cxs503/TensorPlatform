"""Analytic benchmark problems used to verify the GCI machinery itself.

Each problem is a small, exactly solvable discrete system whose *exact* solution
is known in closed form, so the observed order of accuracy and the GCI band can
both be checked against truth.  The problems are deliberately cheap (a handful
of tridiagonal / 5-point solves on the coarsest possible grids) -- their purpose
is to certify the verification tool, not to model a ship.

Problems
--------
``convection_diffusion_1d(n, peclet)``
    ``-u'' + Pe u' = 0`` on [0,1], ``u(0)=0, u(1)=1`` with second-order central
    differences.  Exact: ``u(x) = (e^{Pe x} - 1) / (e^{Pe} - 1)``.
    QoI: ``u(0.5)``.  Nominal order 2.

``poisson_1d_numerov(n)``
    ``-u'' = pi^2 sin(pi x)`` on [0,1], ``u(0)=u(1)=0`` with the compact
    Numerov scheme ``(u_{i-1} - 2u_i + u_{i+1}) = h^2 (f_{i-1} + 10 f_i +
    f_{i+1}) / 12``.  Exact: ``u = sin(pi x)``.  QoI: ``u(0.5) = 1``.
    Nominal order 4.

``poisson_2d(n)``
    ``-lap(u) = 2 pi^2 sin(pi x) sin(pi y)`` on the unit square, homogeneous
    Dirichlet data, 5-point central differences.  Exact:
    ``u = sin(pi x) sin(pi y)``.  QoI: ``u(0.5, 0.5) = 1``.  Nominal order 2.

``power_law(h, p, exact, c)``
    A synthetic pure power law ``f(h) = exact + c h^p`` -- the ideal asymptotic
    sequence, used to test that the order estimator is unbiased for arbitrary
    (including non-integer) ``p`` and for unequal refinement ratios.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np

__all__ = [
    "BenchmarkCase",
    "convection_diffusion_1d",
    "poisson_1d_numerov",
    "poisson_2d",
    "power_law",
    "CASES",
]


def _thomas(lower: np.ndarray, diag: np.ndarray, upper: np.ndarray, rhs: np.ndarray) -> np.ndarray:
    """Tridiagonal solve (Thomas algorithm) -- no SciPy dependency."""
    n = diag.size
    c = np.zeros(n - 1)
    d = np.zeros(n)
    beta = diag[0]
    if beta == 0.0:
        raise ZeroDivisionError("singular tridiagonal system (zero pivot)")
    d[0] = rhs[0] / beta
    for i in range(1, n):
        c[i - 1] = upper[i - 1] / beta
        beta = diag[i] - lower[i - 1] * c[i - 1]
        if beta == 0.0:
            raise ZeroDivisionError("singular tridiagonal system (zero pivot)")
        d[i] = (rhs[i] - lower[i - 1] * d[i - 1]) / beta
    x = np.zeros(n)
    x[-1] = d[-1]
    for i in range(n - 2, -1, -1):
        x[i] = d[i] - c[i] * x[i + 1]
    return x


def convection_diffusion_1d(n: int, *, peclet: float = 5.0) -> Tuple[float, float]:
    """Second-order central-difference solution; returns ``(h, u(0.5))``."""
    if n < 4 or n % 2 != 0:
        raise ValueError("n must be an even integer >= 4 (so x=0.5 is a node)")
    h = 1.0 / n
    eps = 1.0
    b = peclet
    if b * h / (2.0 * eps) >= 1.0:
        raise ValueError("cell Peclet number >= 1: central differencing loses boundedness")
    # interior unknowns u_1 .. u_{n-1}
    m = n - 1
    lower = np.full(m - 1, -eps / h**2 - b / (2.0 * h))
    diag = np.full(m, 2.0 * eps / h**2)
    upper = np.full(m - 1, -eps / h**2 + b / (2.0 * h))
    rhs = np.zeros(m)
    # Dirichlet: u_0 = 0 (no contribution), u_n = 1 (adds to last row)
    rhs[-1] -= (-eps / h**2 + b / (2.0 * h)) * 1.0
    u_int = _thomas(lower, diag, upper, rhs)
    u = np.concatenate(([0.0], u_int, [1.0]))
    qoi = float(u[n // 2])
    return h, qoi


def convection_diffusion_1d_exact(x: float, *, peclet: float = 5.0) -> float:
    return math.expm1(peclet * x) / math.expm1(peclet)


def poisson_1d_numerov(n: int) -> Tuple[float, float]:
    """Fourth-order compact (Numerov) solution; returns ``(h, u(0.5))``."""
    if n < 4 or n % 2 != 0:
        raise ValueError("n must be an even integer >= 4 (so x=0.5 is a node)")
    h = 1.0 / n
    x = np.linspace(0.0, 1.0, n + 1)
    f = math.pi**2 * np.sin(math.pi * x)
    m = n - 1
    lower = np.ones(m - 1)
    diag = np.full(m, -2.0)
    upper = np.ones(m - 1)
    # -u'' = f  =>  u_{i-1} - 2u_i + u_{i+1} = -h^2 (f_{i-1} + 10 f_i + f_{i+1}) / 12
    rhs = -(h**2 / 12.0) * (f[:-2] + 10.0 * f[1:-1] + f[2:])
    u_int = _thomas(lower, diag, upper, rhs)
    u = np.concatenate(([0.0], u_int, [0.0]))
    qoi = float(u[n // 2])
    return h, qoi


def poisson_2d(n: int) -> Tuple[float, float]:
    """5-point Poisson solution; returns ``(h, u(0.5, 0.5))``."""
    if n < 4 or n % 2 != 0:
        raise ValueError("n must be an even integer >= 4 (so the centre is a node)")
    h = 1.0 / n
    xs = np.linspace(0.0, 1.0, n + 1)
    X, Y = np.meshgrid(xs, xs, indexing="ij")
    f = 2.0 * math.pi**2 * np.sin(math.pi * X) * np.sin(math.pi * Y)
    interior = slice(1, n)
    fi = f[interior, interior]
    # dense 5-point operator on (n-1)^2 unknowns (n <= 128 is fine and fast)
    m = n - 1
    size = m * m
    a = np.zeros((size, size))
    idx = np.arange(size).reshape(m, m)
    for i in range(m):
        for j in range(m):
            k = idx[i, j]
            a[k, k] = 4.0
            if i > 0:
                a[k, idx[i - 1, j]] = -1.0
            if i < m - 1:
                a[k, idx[i + 1, j]] = -1.0
            if j > 0:
                a[k, idx[i, j - 1]] = -1.0
            if j < m - 1:
                a[k, idx[i, j + 1]] = -1.0
    rhs = (h**2) * fi.reshape(-1)
    sol = np.linalg.solve(a, rhs).reshape(m, m)
    qoi = float(sol[m // 2, m // 2])
    return h, qoi


def poisson_2d_exact(x: float = 0.5, y: float = 0.5) -> float:
    return math.sin(math.pi * x) * math.sin(math.pi * y)


def power_law(h: float, *, p: float = 2.0, exact: float = 1.0, c: float = 0.05) -> float:
    """Ideal asymptotic sequence ``f(h) = exact + c h^p``."""
    return exact + c * h**p


@dataclass(frozen=True)
class BenchmarkCase:
    """One benchmark problem: grid -> (h, QoI), exact QoI, and nominal order."""

    name: str
    description: str
    quantity: str
    nominal_order: int
    default_grids: Sequence[int]
    solve: Callable[[int], Tuple[float, float]]
    exact: float

    def series(self, grids: Sequence[int] | None = None) -> List[Tuple[float, float]]:
        return [self.solve(n) for n in (grids if grids is not None else self.default_grids)]

    def exact_value(self) -> float:
        return self.exact


CASES: Dict[str, BenchmarkCase] = {
    "conv_diff_1d_p5": BenchmarkCase(
        name="conv_diff_1d_p5",
        description=(
            "1D steady convection-diffusion, -u''+5u'=0, central differences, "
            "QoI u(0.5), exact u=(e^{5x}-1)/(e^5-1)"
        ),
        quantity="u(0.5)",
        nominal_order=2,
        default_grids=(20, 40, 80),
        solve=lambda n: convection_diffusion_1d(n, peclet=5.0),
        exact=convection_diffusion_1d_exact(0.5, peclet=5.0),
    ),
    "poisson_1d_numerov": BenchmarkCase(
        name="poisson_1d_numerov",
        description=(
            "1D Poisson -u''=pi^2 sin(pi x) with the 4th-order compact Numerov "
            "scheme, QoI u(0.5), exact 1.0"
        ),
        quantity="u(0.5)",
        nominal_order=4,
        default_grids=(8, 16, 32),
        solve=poisson_1d_numerov,
        exact=1.0,
    ),
    "poisson_2d_5pt": BenchmarkCase(
        name="poisson_2d_5pt",
        description=(
            "2D Poisson on the unit square, 5-point central differences, "
            "QoI u(0.5,0.5), exact sin(pi/2)^2 = 1.0"
        ),
        quantity="u(0.5,0.5)",
        nominal_order=2,
        default_grids=(16, 32, 64),
        solve=poisson_2d,
        exact=poisson_2d_exact(0.5, 0.5),
    ),
}