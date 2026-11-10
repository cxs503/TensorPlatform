"""Synthetic (domain-free) case definitions for the cross-solver coupling benchmark.

Everything here is a *synthetic* transfer case: a unit square, two uniform grids, and
three analytic fields that stand in for whatever a real solver would exchange
(a conserved density, a conserved vector, and an energy-like density).  No physical
identity is attached: the benchmark measures the *infrastructure*, and the field
shapes are chosen only so that a projection error is visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

import numpy as np

from tools.field_mapper import CellSet

__all__ = ["SyntheticCase", "unit_square_mesh", "default_case", "analytic_cell_averages", "FIELD_NAMES"]

FIELD_NAMES = ("density", "momentum_x", "momentum_y", "energy")


def unit_square_mesh(n: int, name: str | None = None) -> CellSet:
    """Uniform ``n x n`` box mesh on the unit square."""
    edges = np.linspace(0.0, 1.0, int(n) + 1)
    lo_x, hi_x = edges[:-1], edges[1:]
    lo = np.array([[x, y] for x in lo_x for y in lo_x], dtype=float)
    hi = np.array([[x, y] for x in hi_x for y in hi_x], dtype=float)
    return CellSet.from_boxes(lo, hi, name=name or f"uniform{n}")


@dataclass(frozen=True)
class SyntheticCase:
    """Analytic fields on the unit square, evaluated as cell averages."""

    density: Callable[[np.ndarray], np.ndarray]
    velocity: Callable[[np.ndarray], np.ndarray]
    internal_energy: Callable[[np.ndarray], np.ndarray]

    def density_at(self, centers: np.ndarray) -> np.ndarray:
        return np.asarray(self.density(centers), dtype=float)

    def momentum_at(self, centers: np.ndarray) -> np.ndarray:
        rho = self.density_at(centers)
        return rho[:, None] * self.velocity(centers)

    def energy_at(self, centers: np.ndarray) -> np.ndarray:
        rho = self.density_at(centers)
        vel = self.velocity(centers)
        kinetic = 0.5 * np.sum(vel * vel, axis=1)
        return rho * (kinetic + self.internal_energy(centers))

    def sample(self, cells: CellSet) -> Dict[str, np.ndarray]:
        return {
            "density": self.density_at(cells.centers),
            "momentum": self.momentum_at(cells.centers),
            "energy": self.energy_at(cells.centers),
        }


def default_case() -> SyntheticCase:
    """Smooth, strictly positive, sign-varying components — a hard projection case."""

    def density(c: np.ndarray) -> np.ndarray:
        return 2.0 + np.sin(np.pi * c[:, 0]) * np.cos(np.pi * c[:, 1])

    def velocity(c: np.ndarray) -> np.ndarray:
        ux = 0.5 + np.cos(np.pi * c[:, 0]) * np.sin(0.5 * np.pi * c[:, 1])
        uy = 1.0 + np.sin(np.pi * c[:, 0]) * np.cos(np.pi * c[:, 1])
        return np.stack([ux, uy], axis=1)

    def internal_energy(c: np.ndarray) -> np.ndarray:
        return 1.0 + 0.25 * np.cos(2.0 * np.pi * c[:, 0] * c[:, 1])

    return SyntheticCase(density=density, velocity=velocity, internal_energy=internal_energy)


def analytic_cell_averages(case: SyntheticCase, cells: CellSet, order: int = 4) -> Dict[str, np.ndarray]:
    """Gauss-Legendre cell averages of the analytic fields (reference values).

    Using quadrature instead of point sampling keeps the reference independent of
    the cell-centre approximation, which is what makes the accuracy metric an
    accuracy metric rather than a self-comparison.
    """
    nodes, weights = np.polynomial.legendre.leggauss(order)
    nodes = 0.5 * (nodes + 1.0)
    weights = 0.5 * weights
    acc = {name: np.zeros(cells.n_cells) for name in ("density", "energy")}
    mom = np.zeros((cells.n_cells, 2))
    for wx, x in zip(weights, nodes):
        for wy, y in zip(weights, nodes):
            pts = cells.lo + np.array([x, y])[None, :] * (cells.hi - cells.lo)
            quad_w = wx * wy
            rho = case.density_at(pts)
            vel = case.velocity(pts)
            acc["density"] += quad_w * rho
            mom += (quad_w * rho)[:, None] * vel
            acc["energy"] += quad_w * case.density_at(pts) * (
                0.5 * np.sum(vel * vel, axis=1) + case.internal_energy(pts)
            )
    return {"density": acc["density"], "momentum": mom, "energy": acc["energy"]}