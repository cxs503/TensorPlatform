"""
LBM→FVM flow field interpolation with mass conservation.

Transfers velocity and pressure fields from LBM (lattice Boltzmann) grid
to FVM (finite volume) grid using conservative interpolation that preserves
mass flux continuity: ∫_Γ u·dA is continuous across the interface.

All geometry is synthetic (no domain-specific semantics).
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
import numpy as np


@dataclass
class LBMCell:
    """
    A single LBM lattice cell.

    Attributes:
        center: (3,) cell center coordinates
        dx: lattice spacing (uniform grid assumed)
        velocity: (3,) macroscopic velocity
        density: fluid density
        pressure: thermodynamic pressure (p = ρcs²)
    """
    center: np.ndarray
    dx: float
    velocity: np.ndarray
    density: float
    pressure: float

    def __post_init__(self):
        self.center = np.asarray(self.center, dtype=np.float64)
        self.velocity = np.asarray(self.velocity, dtype=np.float64)


@dataclass
class FVMCell:
    """
    A single FVM control volume.

    Attributes:
        center: (3,) cell center coordinates
        volume: cell volume
        face_centers: list of (3,) face center coordinates
        face_areas: list of face areas
        face_normals: list of (3,) face outward normals
        velocity: (3,) interpolated velocity (to be filled)
        pressure: interpolated pressure (to be filled)
    """
    center: np.ndarray
    volume: float
    face_centers: List[np.ndarray] = field(default_factory=list)
    face_areas: List[float] = field(default_factory=list)
    face_normals: List[np.ndarray] = field(default_factory=list)
    velocity: Optional[np.ndarray] = None
    pressure: Optional[float] = None
    density: Optional[float] = None

    def __post_init__(self):
        self.center = np.asarray(self.center, dtype=np.float64)
        self.face_centers = [np.asarray(fc, dtype=np.float64) for fc in self.face_centers]
        self.face_normals = [np.asarray(fn, dtype=np.float64) for fn in self.face_normals]


@dataclass
class MassConservationResult:
    """
    Result of mass conservation check for LBM→FVM interpolation.

    Attributes:
        lbm_total_flux: total mass flux from LBM side
        fvm_total_flux: total mass flux from FVM side
        flux_residual: |lbm - fvm| / avg
        passed: whether residual is within tolerance
        tolerance: acceptance threshold
        n_cells_interpolated: number of FVM cells receiving data
    """
    lbm_total_flux: float
    fvm_total_flux: float
    flux_residual: float
    passed: bool
    tolerance: float
    n_cells_interpolated: int

    def summary(self) -> dict:
        return {
            "lbm_total_flux": self.lbm_total_flux,
            "fvm_total_flux": self.fvm_total_flux,
            "flux_residual": self.flux_residual,
            "passed": self.passed,
            "tolerance": self.tolerance,
            "n_cells_interpolated": self.n_cells_interpolated,
        }


class LBMtoFVMInterpolator:
    """
    Conservative interpolation from LBM grid to FVM grid.

    The interpolation ensures:
    1. Mass flux continuity: ∫_Γ ρu·n dA is preserved
    2. Velocity field is reconstructed via volume-weighted averaging
    3. Pressure field is interpolated with conservation of total pressure force

    Methods:
    - nearest_neighbor: simple nearest-cell interpolation
    - volume_weighted: volume-weighted conservative averaging
    - flux_corrected: flux-corrected transport (FCT) style conservation
    """

    def __init__(self, tolerance: float = 0.03):
        """
        Args:
            tolerance: relative tolerance for mass flux conservation (default 3%)
        """
        self.tolerance = tolerance
        self._interpolation_history: List[dict] = []

    def nearest_neighbor_interpolate(
        self,
        lbm_cells: List[LBMCell],
        fvm_cells: List[FVMCell]
    ) -> MassConservationResult:
        """
        Interpolate using nearest-neighbor mapping.

        Each FVM cell receives values from the nearest LBM cell.
        Mass flux is checked post-interpolation.

        Args:
            lbm_cells: source LBM cells
            fvm_cells: target FVM cells

        Returns:
            MassConservationResult
        """
        if not lbm_cells or not fvm_cells:
            raise ValueError("Both lbm_cells and fvm_cells must be non-empty")

        # Build KD-tree for nearest neighbor search
        from scipy.spatial import cKDTree
        lbm_centers = np.array([c.center for c in lbm_cells])
        tree = cKDTree(lbm_centers)

        n_interpolated = 0
        for fvm_cell in fvm_cells:
            _, idx = tree.query(fvm_cell.center)
            lbm_cell = lbm_cells[idx]
            fvm_cell.velocity = lbm_cell.velocity.copy()
            fvm_cell.pressure = lbm_cell.pressure
            fvm_cell.density = lbm_cell.density
            n_interpolated += 1

        return self._check_mass_flux(lbm_cells, fvm_cells, n_interpolated)

    def volume_weighted_interpolate(
        self,
        lbm_cells: List[LBMCell],
        fvm_cells: List[FVMCell],
        n_neighbors: int = 8
    ) -> MassConservationResult:
        """
        Interpolate using volume-weighted conservative averaging.

        Each FVM cell receives a weighted average from nearby LBM cells,
        with weights proportional to overlap volume estimates.

        Args:
            lbm_cells: source LBM cells
            fvm_cells: target FVM cells
            n_neighbors: number of nearest LBM cells to consider

        Returns:
            MassConservationResult
        """
        if not lbm_cells or not fvm_cells:
            raise ValueError("Both lbm_cells and fvm_cells must be non-empty")

        from scipy.spatial import cKDTree
        lbm_centers = np.array([c.center for c in lbm_cells])
        tree = cKDTree(lbm_centers)

        n_interpolated = 0
        for fvm_cell in fvm_cells:
            dists, indices = tree.query(fvm_cell.center, k=min(n_neighbors, len(lbm_cells)))

            # Inverse distance weighting
            if np.isscalar(dists):
                dists = np.array([dists])
                indices = np.array([indices])

            weights = 1.0 / (dists + 1e-14)
            weights /= weights.sum()

            # Weighted average of velocity, pressure and density
            vel = np.zeros(3)
            pres = 0.0
            rho = 0.0
            for w, idx in zip(weights, indices):
                vel += w * lbm_cells[idx].velocity
                pres += w * lbm_cells[idx].pressure
                rho += w * lbm_cells[idx].density

            fvm_cell.velocity = vel
            fvm_cell.pressure = pres
            fvm_cell.density = rho
            n_interpolated += 1

        return self._check_mass_flux(lbm_cells, fvm_cells, n_interpolated)

    def flux_corrected_interpolate(
        self,
        lbm_cells: List[LBMCell],
        fvm_cells: List[FVMCell],
        n_neighbors: int = 8
    ) -> MassConservationResult:
        """
        Flux-corrected transport (FCT) style conservative interpolation.

        First computes a high-order interpolation, then applies a correction
        to ensure exact mass flux conservation.

        Args:
            lbm_cells: source LBM cells
            fvm_cells: target FVM cells
            n_neighbors: number of nearest LBM cells for initial interpolation

        Returns:
            MassConservationResult
        """
        if not lbm_cells or not fvm_cells:
            raise ValueError("Both lbm_cells and fvm_cells must be non-empty")

        # Step 1: Volume-weighted interpolation (high-order)
        result = self.volume_weighted_interpolate(lbm_cells, fvm_cells, n_neighbors)

        # Step 2: Compute flux correction factor
        if abs(result.lbm_total_flux) > 1e-14:
            correction = result.lbm_total_flux / result.fvm_total_flux
        else:
            correction = 1.0

        # Step 3: Apply correction to FVM velocities
        for fvm_cell in fvm_cells:
            if fvm_cell.velocity is not None:
                fvm_cell.velocity *= correction

        # Step 4: Re-check mass flux
        return self._check_mass_flux(lbm_cells, fvm_cells, result.n_cells_interpolated)

    def _check_mass_flux(
        self,
        lbm_cells: List[LBMCell],
        fvm_cells: List[FVMCell],
        n_interpolated: int
    ) -> MassConservationResult:
        """Mass-inventory check across the transfer.

        The previous implementation summed ``ρ u·n`` over the six faces of every
        cubic cell and over ``hasattr(cell, 'density')`` on the FVM side.  The
        first sum is identically zero (each cell contributes ``+n`` and ``-n``),
        and :class:`FVMCell` had no ``density`` attribute, so the FVM sum was
        identically zero as well: the residual was ``|0 - 0| / 0`` for *every*
        field and the gate could not fail.  It is replaced by the inventory
        identity that a transfer actually has to satisfy::

            Σ_j ρ_j V_j  ==  Σ_i ρ_i |cell_i ∩ Ω_FVM|

        with ``Ω_FVM`` the union of the FVM cells (their bounding box, which is
        exact for the structured patches used in coupling).  A source cell that
        is only partially covered contributes only the covered part, so a
        transfer that over- or under-fills the target domain is detected.
        """
        if not fvm_cells:
            raise ValueError("fvm_cells must be non-empty")

        from .geometry import box, intersect

        lo = np.min([c.center - 0.5 * (c.volume ** (1.0 / 3.0)) for c in fvm_cells], axis=0)
        hi = np.max([c.center + 0.5 * (c.volume ** (1.0 / 3.0)) for c in fvm_cells], axis=0)
        domain = box(0.5 * (lo + hi), 0.5 * (hi - lo))

        lbm_mass = 0.0
        covered_volume = 0.0
        for cell in lbm_cells:
            half = 0.5 * cell.dx
            cube = box(cell.center, [half, half, half])
            overlap = intersect(cube, domain)
            if overlap is None:
                continue
            lbm_mass += cell.density * overlap.volume
            covered_volume += overlap.volume

        fvm_mass = 0.0
        fvm_volume = 0.0
        missing_density = 0
        for cell in fvm_cells:
            fvm_volume += cell.volume
            if cell.density is None:
                missing_density += 1
                continue
            fvm_mass += cell.density * cell.volume

        if fvm_mass == 0.0 or lbm_mass == 0.0:
            # No mass information: no conservation claim can be made.
            residual = float("inf")
        else:
            residual = abs(lbm_mass - fvm_mass) / max(
                0.5 * (abs(lbm_mass) + abs(fvm_mass)), 1e-300)

        coverage = covered_volume / max(fvm_volume, 1e-300)
        passed = bool(residual <= self.tolerance and coverage >= 1.0 - self.tolerance
                      and missing_density == 0)

        result = MassConservationResult(
            lbm_total_flux=lbm_mass,
            fvm_total_flux=fvm_mass,
            flux_residual=residual,
            passed=passed,
            tolerance=self.tolerance,
            n_cells_interpolated=n_interpolated,
        )
        summary = result.summary()
        summary.update({
            "quantity": "mass_inventory",
            "coverage": coverage,
            "missing_density_cells": missing_density,
            "fvm_volume": fvm_volume,
        })
        self._interpolation_history.append(summary)
        # Keep the public summary of the returned object identical to the stored
        # one so callers can rely on either.
        result.summary = lambda: dict(summary)  # type: ignore[assignment]
        return result

    def interpolate_pressure_field(
        self,
        lbm_cells: List[LBMCell],
        fvm_cells: List[FVMCell],
        method: str = "volume_weighted"
    ) -> Dict[str, float]:
        """
        Interpolate pressure field and check total pressure force conservation.

        Args:
            lbm_cells: source LBM cells
            fvm_cells: target FVM cells
            method: interpolation method

        Returns:
            Dictionary with pressure statistics
        """
        if method == "nearest":
            self.nearest_neighbor_interpolate(lbm_cells, fvm_cells)
        elif method == "volume_weighted":
            self.volume_weighted_interpolate(lbm_cells, fvm_cells)
        elif method == "flux_corrected":
            self.flux_corrected_interpolate(lbm_cells, fvm_cells)

        lbm_pressures = [c.pressure for c in lbm_cells]
        fvm_pressures = [c.pressure for c in fvm_cells if c.pressure is not None]

        lbm_total = sum(lbm_pressures)
        fvm_total = sum(fvm_pressures)

        avg = 0.5 * (abs(lbm_total) + abs(fvm_total))
        if avg > 1e-14:
            pressure_residual = abs(lbm_total - fvm_total) / avg
        else:
            pressure_residual = abs(lbm_total - fvm_total)

        return {
            "lbm_total_pressure": lbm_total,
            "fvm_total_pressure": fvm_total,
            "pressure_residual": pressure_residual,
            "lbm_mean_pressure": np.mean(lbm_pressures),
            "fvm_mean_pressure": np.mean(fvm_pressures),
            "n_fvm_with_pressure": len(fvm_pressures),
        }

    def get_interpolation_history(self) -> List[dict]:
        """Get history of all interpolations performed."""
        return self._interpolation_history.copy()
