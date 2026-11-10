"""
FEM→DEM fracture transfer with four-ledger conservation verification.

When a FEM element fractures, the fracture surface (vertices + normals + areas)
is consumed by DEM to initialize fragment particles. This module implements
the transfer and verifies four conservation ledgers:

1. Mass:     Σm_DEM = m_FEM_element
2. Momentum: Σ(mv)_DEM = (mv)_FEM
3. Energy:   Σ(½mv² + ½Iω²)_DEM = ½mv²_FEM + U_elastic_released
4. Inertia:  I_DEM = ∫ρ(r²I - rr)dV  (matched to FEM element inertia)

All geometry is synthetic (no domain-specific semantics).
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np


@dataclass
class FailureFace:
    """
    A single fracture face produced by FEM failure analysis.

    Attributes:
        vertices: (N, 3) array of face vertex coordinates
        normal: (3,) outward unit normal
        area: face area
        centroid: (3,) face centroid (computed if not provided)
    """
    vertices: np.ndarray
    normal: np.ndarray
    area: float
    centroid: Optional[np.ndarray] = None

    def __post_init__(self):
        self.vertices = np.asarray(self.vertices, dtype=np.float64)
        self.normal = np.asarray(self.normal, dtype=np.float64)
        # Normalize normal
        norm = np.linalg.norm(self.normal)
        if norm > 1e-14:
            self.normal = self.normal / norm
        # Compute centroid if not provided
        if self.centroid is None:
            self.centroid = np.mean(self.vertices, axis=0)


@dataclass
class FEMElementState:
    """
    Pre-fracture state of a FEM element.

    Attributes:
        mass: element mass
        velocity: (3,) center-of-mass velocity
        angular_velocity: (3,) angular velocity
        inertia_tensor: (3, 3) inertia tensor about center of mass
        elastic_strain_energy: stored elastic energy to be released on fracture
        centroid: (3,) element centroid
    """
    mass: float
    velocity: np.ndarray
    angular_velocity: np.ndarray
    inertia_tensor: np.ndarray
    elastic_strain_energy: float
    centroid: np.ndarray

    def __post_init__(self):
        self.velocity = np.asarray(self.velocity, dtype=np.float64)
        self.angular_velocity = np.asarray(self.angular_velocity, dtype=np.float64)
        self.inertia_tensor = np.asarray(self.inertia_tensor, dtype=np.float64).reshape(3, 3)
        self.centroid = np.asarray(self.centroid, dtype=np.float64)


@dataclass
class DEMFragment:
    """
    A DEM fragment particle initialized from FEM fracture.

    Attributes:
        mass: fragment mass
        position: (3,) center of mass position
        velocity: (3,) linear velocity
        angular_velocity: (3,) angular velocity
        inertia_tensor: (3, 3) inertia tensor about fragment COM
        radius: equivalent sphere radius (for DEM contact)
    """
    mass: float
    position: np.ndarray
    velocity: np.ndarray
    angular_velocity: np.ndarray
    inertia_tensor: np.ndarray
    radius: float

    def __post_init__(self):
        self.position = np.asarray(self.position, dtype=np.float64)
        self.velocity = np.asarray(self.velocity, dtype=np.float64)
        self.angular_velocity = np.asarray(self.angular_velocity, dtype=np.float64)
        self.inertia_tensor = np.asarray(self.inertia_tensor, dtype=np.float64).reshape(3, 3)

    @property
    def kinetic_energy(self) -> float:
        """Total kinetic energy: ½mv² + ½ω·I·ω."""
        translational = 0.5 * self.mass * np.dot(self.velocity, self.velocity)
        rotational = 0.5 * np.dot(
            self.angular_velocity,
            self.inertia_tensor @ self.angular_velocity
        )
        return float(translational + rotational)

    @property
    def linear_momentum(self) -> np.ndarray:
        """Linear momentum: mv."""
        return self.mass * self.velocity

    @property
    def angular_momentum(self) -> np.ndarray:
        """Angular momentum about fragment COM: I·ω."""
        return self.inertia_tensor @ self.angular_velocity


@dataclass
class FourLedgerResult:
    """
    Result of the four-ledger conservation check.

    Each ledger reports the source value (FEM), target value (DEM sum),
    relative residual, and pass/fail status.
    """
    # Mass ledger
    mass_fem: float
    mass_dem: float
    mass_residual: float
    mass_passed: bool

    # Momentum ledger
    momentum_fem: np.ndarray
    momentum_dem: np.ndarray
    momentum_residual: float
    momentum_passed: bool

    # Energy ledger
    energy_fem: float
    energy_dem: float
    energy_residual: float
    energy_passed: bool

    # Inertia ledger
    inertia_fem: np.ndarray
    inertia_dem: np.ndarray
    inertia_residual: float
    inertia_passed: bool

    # Tolerance used
    tolerance: float

    def summary(self) -> dict:
        return {
            "mass": {
                "fem": self.mass_fem,
                "dem": self.mass_dem,
                "residual": self.mass_residual,
                "passed": self.mass_passed,
            },
            "momentum": {
                "fem_norm": float(np.linalg.norm(self.momentum_fem)),
                "dem_norm": float(np.linalg.norm(self.momentum_dem)),
                "residual": self.momentum_residual,
                "passed": self.momentum_passed,
            },
            "energy": {
                "fem": self.energy_fem,
                "dem": self.energy_dem,
                "residual": self.energy_residual,
                "passed": self.energy_passed,
            },
            "inertia": {
                "fem_trace": float(np.trace(self.inertia_fem)),
                "dem_trace": float(np.trace(self.inertia_dem)),
                "residual": self.inertia_residual,
                "passed": self.inertia_passed,
            },
            "tolerance": self.tolerance,
            "all_passed": (
                self.mass_passed and self.momentum_passed
                and self.energy_passed and self.inertia_passed
            ),
        }


def _compute_sphere_inertia(mass: float, radius: float) -> np.ndarray:
    """Inertia tensor of a solid sphere: (2/5)mr² I."""
    I = 0.4 * mass * radius ** 2
    return I * np.eye(3)


def _compute_equivalent_radius(volume: float) -> float:
    """Radius of sphere with given volume."""
    return (3.0 * volume / (4.0 * np.pi)) ** (1.0 / 3.0)


def _tetrahedron_volume(v0: np.ndarray, v1: np.ndarray, v2: np.ndarray, v3: np.ndarray) -> float:
    """Signed volume of a tetrahedron."""
    return abs(np.dot(v1 - v0, np.cross(v2 - v0, v3 - v0))) / 6.0


class FEMtoDEMTransfer:
    """
    Transfers FEM fracture data to DEM fragment initial conditions
    with four-ledger conservation verification.

    The transfer process:
    1. FEM produces failure_faces.json (vertices + normals + areas)
    2. This module consumes the faces and partitions the element mass
    3. DEM fragments are initialized with mass, velocity, angular velocity, inertia
    4. Four ledgers are checked for conservation
    """

    def __init__(self, tolerance: float = 0.03):
        """
        Args:
            tolerance: relative tolerance for all four ledgers (default 3%)
        """
        self.tolerance = tolerance
        self._transfer_history: List[dict] = []

    def transfer(
        self,
        fem_state: FEMElementState,
        failure_faces: List[FailureFace],
        n_fragments: int = 2
    ) -> Tuple[List[DEMFragment], FourLedgerResult]:
        """
        Perform FEM→DEM fracture transfer.

        Args:
            fem_state: pre-fracture FEM element state
            failure_faces: list of fracture faces from FEM
            n_fragments: number of DEM fragments to create

        Returns:
            Tuple of (DEM fragments, four-ledger conservation result)
        """
        if n_fragments < 1:
            raise ValueError("n_fragments must be >= 1")
        if len(failure_faces) == 0:
            raise ValueError("At least one failure face required")

        # Partition mass equally among fragments (ensures exact conservation)
        mass_per_fragment = fem_state.mass / n_fragments
        fragment_masses = [mass_per_fragment] * n_fragments

        # Create DEM fragments
        fragments = []
        for i in range(n_fragments):
            face = failure_faces[i % len(failure_faces)]
            mass = fragment_masses[i]

            # Position: offset from FEM centroid along face normal
            # Use different offsets for different fragments to spread them
            offset_dir = face.normal
            if i > 0:
                # Add small perpendicular offset for subsequent fragments
                perp = np.array([0.0, 0.0, 1.0])
                if abs(np.dot(offset_dir, perp)) > 0.9:
                    perp = np.array([1.0, 0.0, 0.0])
                perp = perp - np.dot(perp, offset_dir) * offset_dir
                perp_norm = np.linalg.norm(perp)
                if perp_norm > 1e-14:
                    offset_dir = offset_dir + 0.3 * perp / perp_norm

            offset = 0.1 * offset_dir
            position = fem_state.centroid + offset

            # Velocity: inherit FEM velocity + small perturbation from fracture
            velocity = fem_state.velocity.copy()
            # Add fracture-induced velocity proportional to elastic energy release
            energy_per_mass = fem_state.elastic_strain_energy / fem_state.mass
            if energy_per_mass > 0:
                fracture_speed = np.sqrt(2.0 * energy_per_mass / n_fragments)
                velocity += fracture_speed * face.normal * 0.1

            # Angular velocity: inherit FEM angular velocity
            angular_velocity = fem_state.angular_velocity.copy()

            # Inertia: compute from fragment geometry (approximate as sphere)
            density = fem_state.mass / max(1e-14, self._estimate_element_volume(fem_state))
            frag_volume = mass / max(density, 1e-14)
            radius = _compute_equivalent_radius(frag_volume)
            inertia = _compute_sphere_inertia(mass, radius)

            frag = DEMFragment(
                mass=mass,
                position=position,
                velocity=velocity,
                angular_velocity=angular_velocity,
                inertia_tensor=inertia,
                radius=radius,
            )
            fragments.append(frag)

        # Verify four ledgers
        ledger = self._verify_four_ledgers(fem_state, fragments)

        record = {
            "n_fragments": n_fragments,
            "fem_mass": fem_state.mass,
            "dem_total_mass": ledger.mass_dem,
            "all_passed": ledger.summary()["all_passed"],
        }
        self._transfer_history.append(record)

        return fragments, ledger

    def _estimate_element_volume(self, fem_state: FEMElementState) -> float:
        """
        Estimate element volume from inertia tensor trace.
        For a solid sphere: I = (2/5)mr², trace(I) = 3*(2/5)mr² = (6/5)mr²
        V = (4/3)πr³ → r = sqrt(5*trace(I)/(6m))
        """
        trace_I = np.trace(fem_state.inertia_tensor)
        m = fem_state.mass
        if m < 1e-14 or trace_I < 1e-14:
            return 1.0  # fallback
        r = np.sqrt(5.0 * trace_I / (6.0 * m))
        return (4.0 / 3.0) * np.pi * r ** 3

    def _verify_four_ledgers(
        self,
        fem_state: FEMElementState,
        fragments: List[DEMFragment]
    ) -> FourLedgerResult:
        """
        Verify the four conservation ledgers.

        1. Mass: Σm_DEM = m_FEM
        2. Momentum: Σ(mv)_DEM = (mv)_FEM
        3. Energy: Σ(½mv² + ½Iω²)_DEM = ½mv²_FEM + ½ω·I·ω_FEM + U_elastic
        4. Inertia: I_DEM ≈ I_FEM (trace comparison)
        """
        # --- Mass ledger ---
        mass_dem = sum(f.mass for f in fragments)
        mass_fem = fem_state.mass
        mass_avg = 0.5 * (abs(mass_fem) + abs(mass_dem))
        mass_res = abs(mass_fem - mass_dem) / max(mass_avg, 1e-14)
        mass_passed = mass_res <= self.tolerance

        # --- Momentum ledger ---
        momentum_dem = np.zeros(3)
        for f in fragments:
            momentum_dem += f.linear_momentum
        momentum_fem = fem_state.mass * fem_state.velocity
        mom_avg = 0.5 * (np.linalg.norm(momentum_fem) + np.linalg.norm(momentum_dem))
        mom_res = np.linalg.norm(momentum_fem - momentum_dem) / max(mom_avg, 1e-14)
        mom_passed = mom_res <= self.tolerance

        # --- Energy ledger ---
        energy_dem = sum(f.kinetic_energy for f in fragments)
        ke_fem = 0.5 * fem_state.mass * np.dot(fem_state.velocity, fem_state.velocity)
        re_fem = 0.5 * np.dot(
            fem_state.angular_velocity,
            fem_state.inertia_tensor @ fem_state.angular_velocity
        )
        energy_fem = ke_fem + re_fem + fem_state.elastic_strain_energy
        energy_avg = 0.5 * (abs(energy_fem) + abs(energy_dem))
        energy_res = abs(energy_fem - energy_dem) / max(energy_avg, 1e-14)
        energy_passed = energy_res <= self.tolerance

        # --- Inertia ledger ---
        inertia_dem = np.zeros((3, 3))
        for f in fragments:
            # Parallel axis theorem: I_total = I_com + m(d²I - dd)
            d = f.position - fem_state.centroid
            d2 = np.dot(d, d)
            inertia_dem += f.inertia_tensor + f.mass * (d2 * np.eye(3) - np.outer(d, d))
        inertia_fem = fem_state.inertia_tensor
        inertia_fem_trace = np.trace(inertia_fem)
        inertia_dem_trace = np.trace(inertia_dem)
        inertia_avg = 0.5 * (abs(inertia_fem_trace) + abs(inertia_dem_trace))
        inertia_res = abs(inertia_fem_trace - inertia_dem_trace) / max(inertia_avg, 1e-14)
        inertia_passed = inertia_res <= self.tolerance

        return FourLedgerResult(
            mass_fem=mass_fem,
            mass_dem=mass_dem,
            mass_residual=mass_res,
            mass_passed=mass_passed,
            momentum_fem=momentum_fem,
            momentum_dem=momentum_dem,
            momentum_residual=mom_res,
            momentum_passed=mom_passed,
            energy_fem=energy_fem,
            energy_dem=energy_dem,
            energy_residual=energy_res,
            energy_passed=energy_passed,
            inertia_fem=inertia_fem,
            inertia_dem=inertia_dem,
            inertia_residual=inertia_res,
            inertia_passed=inertia_passed,
            tolerance=self.tolerance,
        )

    def consume_failure_faces_json(self, json_path: str) -> List[FailureFace]:
        """
        Consume a failure_faces.json file produced by FEM.

        Expected JSON format:
        {
            "faces": [
                {
                    "vertices": [[x,y,z], ...],
                    "normal": [nx, ny, nz],
                    "area": <float>
                },
                ...
            ]
        }

        Args:
            json_path: path to failure_faces.json

        Returns:
            List of FailureFace objects
        """
        import json
        with open(json_path, 'r') as f:
            data = json.load(f)

        faces = []
        for face_data in data.get("faces", []):
            vertices = np.array(face_data["vertices"])
            normal = np.array(face_data["normal"])
            area = float(face_data["area"])
            centroid = None
            if "centroid" in face_data:
                centroid = np.array(face_data["centroid"])
            faces.append(FailureFace(
                vertices=vertices,
                normal=normal,
                area=area,
                centroid=centroid,
            ))
        return faces

    def get_transfer_history(self) -> List[dict]:
        """Get history of all transfers performed."""
        return self._transfer_history.copy()
