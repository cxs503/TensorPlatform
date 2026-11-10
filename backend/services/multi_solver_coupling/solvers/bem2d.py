"""2-D constant-panel boundary-element kernel (auxiliary validation).

Used as *independent evidence* that the BEM family in this package is a real
method implementation and not a stub: the non-degenerate parts of a BEM -- panel
geometry, dense influence matrices ``G``/``H``, and the singular self-terms --
only appear in 2-D and above, so they are exercised here on a circular boundary
with a closed-form harmonic solution.

Interior Dirichlet problem for Laplace's equation on a disk of radius ``R``::

    (1/2) T_i + sum_j H_ij T_j = sum_j G_ij q_j

with constant panels (midpoint rule off the diagonal, analytic self-terms)::

    G_ij = (1/2pi) L ln(1/r_ij),      G_ii = (L/2pi) (1 - ln(L/2))
    H_ij = -(1/2pi) L ((x_j - x_i) . n_j) / r_ij^2,   H_ii = 0 (flat panel)

Given Dirichlet data ``T`` the unknown Neumann data follow from
``G q = (1/2 I + H) T``.  Accuracy is measured against the analytic
``q = dT/dn`` of a chosen harmonic function.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PanelResult:
    theta: np.ndarray
    G: np.ndarray
    H: np.ndarray
    q_numeric: np.ndarray
    q_exact: np.ndarray

    @property
    def max_rel_error(self) -> float:
        scale = max(float(np.max(np.abs(self.q_exact))), 1e-30)
        return float(np.max(np.abs(self.q_numeric - self.q_exact)) / scale)

    @property
    def rms_rel_error(self) -> float:
        scale = max(float(np.sqrt(np.mean(self.q_exact**2))), 1e-30)
        return float(np.sqrt(np.mean((self.q_numeric - self.q_exact) ** 2)) / scale)


def circle_panels(n: int, radius: float = 1.0):
    """Panel mid-points, normals and length of a circular boundary."""
    theta = 2.0 * np.pi * (np.arange(n) + 0.5) / n
    x = np.column_stack([radius * np.cos(theta), radius * np.sin(theta)])
    normals = np.column_stack([np.cos(theta), np.sin(theta)])
    length = 2.0 * radius * np.sin(np.pi / n)
    return theta, x, normals, length


def solve_disk_dirichlet(
    T_fun,
    q_fun,
    n: int = 96,
    radius: float = 1.0,
) -> PanelResult:
    """Solve the interior Dirichlet problem and compare with the analytic flux.

    ``T_fun(xy)`` is the harmonic trace and ``q_fun(xy, normals)`` the analytic
    normal derivative used only for the error metric.
    """
    theta, x, normals, length = circle_panels(n, radius)
    diff = x[:, None, :] - x[None, :, :]          # (i, j, 2) = x_j - x_i
    r = np.linalg.norm(diff, axis=2)
    np.fill_diagonal(r, 1.0)
    rn = np.einsum("ijd,jd->ij", diff, normals)   # (x_j - x_i) . n_j

    G = (length / (2.0 * np.pi)) * np.log(1.0 / r)
    np.fill_diagonal(G, (length / (2.0 * np.pi)) * (1.0 - np.log(length / 2.0)))
    H = -(length / (2.0 * np.pi)) * rn / r**2
    np.fill_diagonal(H, 0.0)

    T = np.asarray(T_fun(x), dtype=float)
    q = np.linalg.solve(G, (0.5 * np.eye(n) + H) @ T)
    return PanelResult(theta=theta, G=G, H=H, q_numeric=q, q_exact=np.asarray(q_fun(x, normals), dtype=float))


# -- harmonic test functions (closed-form, no data files) --------------------
def harmonic_linear(xy: np.ndarray) -> np.ndarray:
    """``T = x`` (harmonic), ``dT/dn = n_x``."""
    return xy[:, 0]


def harmonic_linear_flux(xy: np.ndarray, normals: np.ndarray) -> np.ndarray:
    return normals[:, 0]


def harmonic_quadratic(xy: np.ndarray) -> np.ndarray:
    """``T = x^2 - y^2`` (harmonic), ``dT/dn = 2(x n_x - y n_y)``."""
    return xy[:, 0] ** 2 - xy[:, 1] ** 2


def harmonic_quadratic_flux(xy: np.ndarray, normals: np.ndarray) -> np.ndarray:
    return 2.0 * (xy[:, 0] * normals[:, 0] - xy[:, 1] * normals[:, 1])