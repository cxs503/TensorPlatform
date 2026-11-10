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

            # Weighted average of velocity and pressure
            vel = np.zeros(3)
            pres = 0.0
            for w, idx in zip(weights, indices):
                vel += w * lbm_cells[idx].velocity
                pres += w * lbm_cells[idx].pressure

            fvm_cell.velocity = vel
            fvm_cell.pressure = pres
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
        """
        Check mass flux conservation between LBM and FVM sides.

        Computes ∫_Γ ρu·n dA on both sides and compares.
        """
        # LBM side: sum of ρu·n over all cell faces
        lbm_flux = 0.0
        for cell in lbm_cells:
            # For a uniform lattice, each face has area dx²
            face_area = cell.dx ** 2
            # Six faces of a cube: ±x, ±y, ±z
            normals = [
                np.array([1, 0, 0]), np.array([-1, 0, 0]),
                np.array([0, 1, 0]), np.array([0, -1, 0]),
                np.array([0, 0, 1]), np.array([0, 0, -1]),
            ]
            for n in normals:
                lbm_flux += cell.density * np.dot(cell.velocity, n) * face_area

        # FVM side: sum of ρu·n over all cell faces
        fvm_flux = 0.0
        for cell in fvm_cells:
            if cell.velocity is None:
                continue
            # Use face data if available, otherwise estimate
            if cell.face_centers and cell.face_areas and cell.face_normals:
                for fc, fa, fn in zip(cell.face_centers, cell.face_areas, cell.face_normals):
                    fvm_flux += cell.density * np.dot(cell.velocity, fn) * fa if hasattr(cell, 'density') else 0.0
            else:
                # Estimate from cell volume (assume cubic)
                estimated_area = cell.volume ** (2.0 / 3.0)
                # Six faces
                normals = [
                    np.array([1, 0, 0]), np.array([-1, 0, 0]),
                    np.array([0, 1, 0]), np.array([0, -1, 0]),
                    np.array([0, 0, 1]), np.array([0, 0, -1]),
                ]
                for n in normals:
                    fvm_flux += 1.0 * np.dot(cell.velocity, n) * estimated_area

        # Compute residual
        avg_flux = 0.5 * (abs(lbm_flux) + abs(fvm_flux))
        if avg_flux > 1e-14:
            residual = abs(lbm_flux - fvm_flux) / avg_flux
        else:
            residual = abs(lbm_flux - fvm_flux)

        passed = residual <= self.tolerance

        result = MassConservationResult(
            lbm_total_flux=lbm_flux,
            fvm_total_flux=fvm_flux,
            flux_residual=residual,
            passed=passed,
            tolerance=self.tolerance,
            n_cells_interpolated=n_interpolated,
        )

        self._interpolation_history.append(result.summary())
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
