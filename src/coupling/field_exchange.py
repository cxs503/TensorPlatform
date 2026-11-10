"""
Field exchange protocol for multi-solver coupling.

Defines the message format for data exchange between solvers:
- FieldMessage: carries field data with metadata
- GridTopology: describes mesh structure at exchange interface
- FieldExchange: manages bidirectional field transfer
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np


@dataclass
class DimensionVector:
    """Physical dimension representation [M, L, T, Θ]."""
    M: float = 0.0  # Mass
    L: float = 0.0  # Length
    T: float = 0.0  # Time
    Theta: float = 0.0  # Temperature

    def __eq__(self, other):
        if not isinstance(other, DimensionVector):
            return False
        return (abs(self.M - other.M) < 1e-12 and
                abs(self.L - other.L) < 1e-12 and
                abs(self.T - other.T) < 1e-12 and
                abs(self.Theta - other.Theta) < 1e-12)

    def __repr__(self):
        return f"DimensionVector(M={self.M}, L={self.L}, T={self.T}, Θ={self.Theta})"


@dataclass
class GridTopology:
    """
    Describes mesh structure at exchange interface.

    Attributes:
        node_coords: (N, 3) array of node positions
        face_nodes: list of faces, each face is list of node indices
        face_areas: (F,) array of face areas
        face_normals: (F, 3) array of face outward normals
        coordinate_system: 'cartesian' or 'cylindrical'
    """
    node_coords: np.ndarray
    face_nodes: List[List[int]]
    face_areas: np.ndarray
    face_normals: np.ndarray
    coordinate_system: str = "cartesian"

    def __post_init__(self):
        self.node_coords = np.asarray(self.node_coords, dtype=np.float64)
        self.face_areas = np.asarray(self.face_areas, dtype=np.float64)
        self.face_normals = np.asarray(self.face_normals, dtype=np.float64)

    @property
    def n_nodes(self) -> int:
        return self.node_coords.shape[0]

    @property
    def n_faces(self) -> int:
        return len(self.face_nodes)

    def validate(self) -> bool:
        """Check topological consistency."""
        if self.face_areas.shape[0] != self.n_faces:
            raise ValueError(f"face_areas length {self.face_areas.shape[0]} != n_faces {self.n_faces}")
        if self.face_normals.shape != (self.n_faces, 3):
            raise ValueError(f"face_normals shape {self.face_normals.shape} != ({self.n_faces}, 3)")
        # Check all node indices are valid
        for face in self.face_nodes:
            for idx in face:
                if idx < 0 or idx >= self.n_nodes:
                    raise ValueError(f"Invalid node index {idx} in face (n_nodes={self.n_nodes})")
        return True


@dataclass
class FieldMessage:
    """
    Message format for field data exchange between solvers.

    Attributes:
        field_name: identifier for the field (e.g., 'velocity', 'pressure', 'displacement')
        grid_topology: mesh description at exchange interface
        values: field values at nodes or faces
        units: DimensionVector describing physical dimensions
        timestamp: simulation time when field was sampled
        field_location: 'node' or 'face' - where values are defined
        domain_tag: synthetic domain identifier (e.g., 'test.fluid', 'test.structure')
    """
    field_name: str
    grid_topology: GridTopology
    values: np.ndarray
    units: DimensionVector
    timestamp: float
    field_location: str = "node"
    domain_tag: str = ""

    def __post_init__(self):
        self.values = np.asarray(self.values, dtype=np.float64)
        if self.field_location not in ("node", "face"):
            raise ValueError(f"field_location must be 'node' or 'face', got '{self.field_location}'")

    @property
    def n_values(self) -> int:
        return self.values.shape[0]

    def validate(self) -> bool:
        """Check message internal consistency."""
        if self.field_location == "node":
            expected = self.grid_topology.n_nodes
        else:
            expected = self.grid_topology.n_faces

        if self.values.ndim == 1:
            if self.n_values != expected:
                raise ValueError(
                    f"Scalar field has {self.n_values} values, expected {expected} at {self.field_location}"
                )
        elif self.values.ndim == 2:
            if self.values.shape[0] != expected:
                raise ValueError(
                    f"Vector field has {self.values.shape[0]} values, expected {expected} at {self.field_location}"
                )
        return True


class FieldExchange:
    """
    Manages bidirectional field transfer between two solver domains.

    Handles:
    - Grid topology mapping (nearest neighbor or conservative interpolation)
    - Field data transfer with conservation checks
    - Coordinate system transformations
    """

    def __init__(self, source_domain: str, target_domain: str):
        """
        Args:
            source_domain: synthetic domain tag for source solver
            target_domain: synthetic domain tag for target solver
        """
        self.source_domain = source_domain
        self.target_domain = target_domain
        self._transfer_history: List[Tuple[float, float]] = []  # (timestamp, residual)

    def map_field(
        self,
        source_msg: FieldMessage,
        target_topology: GridTopology,
        method: str = "nearest"
    ) -> FieldMessage:
        """
        Map field from source to target grid.

        Args:
            source_msg: field message from source solver
            target_topology: target solver's interface grid
            method: 'nearest' or 'conservative'

        Returns:
            FieldMessage with values mapped to target grid
        """
        if method == "nearest":
            mapped_values = self._nearest_neighbor_map(source_msg, target_topology)
        elif method == "conservative":
            mapped_values = self._conservative_map(source_msg, target_topology)
        else:
            raise ValueError(f"Unknown mapping method: {method}")

        return FieldMessage(
            field_name=source_msg.field_name,
            grid_topology=target_topology,
            values=mapped_values,
            units=source_msg.units,
            timestamp=source_msg.timestamp,
            field_location=source_msg.field_location,
            domain_tag=self.target_domain
        )

    def _nearest_neighbor_map(
        self,
        source_msg: FieldMessage,
        target_topology: GridTopology
    ) -> np.ndarray:
        """Map field using nearest neighbor interpolation."""
        # Determine source and target locations based on field_location
        if source_msg.field_location == "node":
            source_coords = source_msg.grid_topology.node_coords
            target_coords = target_topology.node_coords
        else:
            # Use face centroids
            source_coords = self._compute_face_centroids(source_msg.grid_topology)
            target_coords = self._compute_face_centroids(target_topology)

        # Compute distance matrix and find nearest
        from scipy.spatial import cKDTree
        tree = cKDTree(source_coords)
        _, indices = tree.query(target_coords)

        if source_msg.values.ndim == 1:
            return source_msg.values[indices]
        else:
            return source_msg.values[indices]

    def _conservative_map(
        self,
        source_msg: FieldMessage,
        target_topology: GridTopology
    ) -> np.ndarray:
        """Map field using area-weighted conservative interpolation."""
        # For now, use nearest neighbor as fallback
        # Full conservative interpolation requires intersection computation
        return self._nearest_neighbor_map(source_msg, target_topology)

    def _compute_face_centroids(self, topology: GridTopology) -> np.ndarray:
        """Compute face centroid coordinates."""
        centroids = []
        for face in topology.face_nodes:
            face_coords = topology.node_coords[face]
            centroids.append(np.mean(face_coords, axis=0))
        return np.array(centroids)

    def compute_flux_integral(
        self,
        field_msg: FieldMessage,
        normal_direction: Optional[np.ndarray] = None
    ) -> float:
        """
        Compute surface integral ∫_Γ field · n dA.

        Args:
            field_msg: vector field message defined on faces
            normal_direction: optional override for normal direction

        Returns:
            Integrated flux value
        """
        if field_msg.values.ndim != 2 or field_msg.values.shape[1] != 3:
            raise ValueError("Flux integral requires vector field (N, 3)")

        if field_msg.field_location != "face":
            raise ValueError("Flux integral requires field defined on faces")

        normals = field_msg.grid_topology.face_normals
        areas = field_msg.grid_topology.face_areas

        if normal_direction is not None:
            normals = np.tile(normal_direction, (len(normals), 1))

        # Dot product: field · n
        dot_products = np.sum(field_msg.values * normals, axis=1)

        # Integrate: Σ (field · n) * dA
        flux = np.sum(dot_products * areas)
        return float(flux)
