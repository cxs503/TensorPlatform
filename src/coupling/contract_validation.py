"""
Interface contract validation for multi-solver coupling.

Verifies consistency of:
- Physical dimensions (units)
- Coordinate systems
- Mesh topology compatibility
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
import numpy as np

from .field_exchange import FieldMessage, GridTopology, DimensionVector


@dataclass
class ContractViolation:
    """
    Describes a contract violation between coupled solvers.

    Attributes:
        violation_type: 'dimension', 'coordinate_system', 'topology', 'field_name'
        message: human-readable description
        expected: what was expected
        actual: what was found
        severity: 'error' or 'warning'
    """
    violation_type: str
    message: str
    expected: str
    actual: str
    severity: str = "error"

    def __repr__(self):
        return f"ContractViolation({self.violation_type}: {self.message})"


class ContractValidator:
    """
    Validates interface contracts between coupled solvers.

    Checks:
    - Dimensional consistency (units match)
    - Coordinate system compatibility
    - Mesh topology mapping validity
    - Field name consistency
    """

    def __init__(self):
        self._violations: List[ContractViolation] = []
        self._checks_performed: int = 0

    def validate_dimension_consistency(
        self,
        msg1: FieldMessage,
        msg2: FieldMessage
    ) -> List[ContractViolation]:
        """
        Check that two field messages have consistent physical dimensions.

        Args:
            msg1: first field message
            msg2: second field message

        Returns:
            List of violations (empty if consistent)
        """
        violations = []
        self._checks_performed += 1

        if msg1.units != msg2.units:
            violations.append(ContractViolation(
                violation_type="dimension",
                message=f"Field '{msg1.field_name}' has inconsistent dimensions",
                expected=str(msg1.units),
                actual=str(msg2.units),
                severity="error"
            ))

        self._violations.extend(violations)
        return violations

    def validate_coordinate_system(
        self,
        topo1: GridTopology,
        topo2: GridTopology
    ) -> List[ContractViolation]:
        """
        Check that two grids use compatible coordinate systems.

        Args:
            topo1: first grid topology
            topo2: second grid topology

        Returns:
            List of violations
        """
        violations = []
        self._checks_performed += 1

        if topo1.coordinate_system != topo2.coordinate_system:
            violations.append(ContractViolation(
                violation_type="coordinate_system",
                message="Grids use different coordinate systems",
                expected=topo1.coordinate_system,
                actual=topo2.coordinate_system,
                severity="error"
            ))

        self._violations.extend(violations)
        return violations

    def validate_topology_compatibility(
        self,
        source_topo: GridTopology,
        target_topo: GridTopology
    ) -> List[ContractViolation]:
        """
        Check that source and target topologies can be mapped.

        Args:
            source_topo: source grid topology
            target_topo: target grid topology

        Returns:
            List of violations
        """
        violations = []
        self._checks_performed += 1

        # Check spatial dimension
        if source_topo.node_coords.shape[1] != target_topo.node_coords.shape[1]:
            violations.append(ContractViolation(
                violation_type="topology",
                message="Grids have different spatial dimensions",
                expected=f"{source_topo.node_coords.shape[1]}D",
                actual=f"{target_topo.node_coords.shape[1]}D",
                severity="error"
            ))

        # Check for overlapping bounding boxes (necessary for mapping)
        src_min = np.min(source_topo.node_coords, axis=0)
        src_max = np.max(source_topo.node_coords, axis=0)
        tgt_min = np.min(target_topo.node_coords, axis=0)
        tgt_max = np.max(target_topo.node_coords, axis=0)

        # Check overlap in each dimension
        for dim in range(3):
            if src_max[dim] < tgt_min[dim] - 1e-10 or tgt_max[dim] < src_min[dim] - 1e-10:
                violations.append(ContractViolation(
                    violation_type="topology",
                    message=f"Grids do not overlap in dimension {dim}",
                    expected=f"[{src_min[dim]:.3f}, {src_max[dim]:.3f}]",
                    actual=f"[{tgt_min[dim]:.3f}, {tgt_max[dim]:.3f}]",
                    severity="warning"
                ))

        self._violations.extend(violations)
        return violations

    def validate_field_name_consistency(
        self,
        msg1: FieldMessage,
        msg2: FieldMessage
    ) -> List[ContractViolation]:
        """
        Check that field names are consistent (or mapped).

        Args:
            msg1: first field message
            msg2: second field message

        Returns:
            List of violations
        """
        violations = []
        self._checks_performed += 1

        if msg1.field_name != msg2.field_name:
            violations.append(ContractViolation(
                violation_type="field_name",
                message="Field names do not match",
                expected=msg1.field_name,
                actual=msg2.field_name,
                severity="warning"
            ))

        self._violations.extend(violations)
        return violations

    def validate_message_integrity(self, msg: FieldMessage) -> List[ContractViolation]:
        """
        Validate internal consistency of a field message.

        Args:
            msg: field message to validate

        Returns:
            List of violations
        """
        violations = []
        self._checks_performed += 1

        try:
            msg.validate()
        except ValueError as e:
            violations.append(ContractViolation(
                violation_type="integrity",
                message=str(e),
                expected="Valid message structure",
                actual="Invalid structure",
                severity="error"
            ))

        self._violations.extend(violations)
        return violations

    def validate_coordinate_transform(
        self,
        vector_field: np.ndarray,
        rotation_matrix: np.ndarray
    ) -> Tuple[np.ndarray, float]:
        """
        Apply coordinate transformation and verify vector norm preservation.

        Args:
            vector_field: (N, 3) array of vectors
            rotation_matrix: (3, 3) rotation matrix

        Returns:
            Tuple of (transformed_field, norm_preservation_error)
        """
        self._checks_performed += 1

        # Verify rotation matrix is orthogonal
        I = np.eye(3)
        orthogonality_error = np.linalg.norm(rotation_matrix @ rotation_matrix.T - I)

        if orthogonality_error > 1e-10:
            self._violations.append(ContractViolation(
                violation_type="coordinate_transform",
                message="Rotation matrix is not orthogonal",
                expected="R @ R.T = I",
                actual=f"||R @ R.T - I|| = {orthogonality_error:.2e}",
                severity="error"
            ))

        # Apply transformation
        transformed = vector_field @ rotation_matrix.T

        # Check norm preservation
        original_norms = np.linalg.norm(vector_field, axis=1)
        transformed_norms = np.linalg.norm(transformed, axis=1)

        norm_error = np.max(np.abs(original_norms - transformed_norms))

        if norm_error > 1e-10:
            self._violations.append(ContractViolation(
                violation_type="coordinate_transform",
                message="Vector norms not preserved under transformation",
                expected="||v'|| = ||v||",
                actual=f"max |Δ||v||| = {norm_error:.2e}",
                severity="error"
            ))

        return transformed, float(norm_error)

    def inject_dimension_mismatch(
        self,
        msg: FieldMessage,
        wrong_units: DimensionVector
    ) -> FieldMessage:
        """
        Intentionally create a dimension mismatch for testing.

        Args:
            msg: original field message
            wrong_units: incorrect dimension vector

        Returns:
            New message with wrong units
        """
        return FieldMessage(
            field_name=msg.field_name,
            grid_topology=msg.grid_topology,
            values=msg.values.copy(),
            units=wrong_units,
            timestamp=msg.timestamp,
            field_location=msg.field_location,
            domain_tag=msg.domain_tag
        )

    def get_validation_summary(self) -> dict:
        """Get summary of all validation checks."""
        errors = [v for v in self._violations if v.severity == "error"]
        warnings = [v for v in self._violations if v.severity == "warning"]

        return {
            "checks_performed": self._checks_performed,
            "total_violations": len(self._violations),
            "errors": len(errors),
            "warnings": len(warnings),
            "violations": [
                {
                    "type": v.violation_type,
                    "message": v.message,
                    "severity": v.severity
                }
                for v in self._violations
            ]
        }

    def clear_violations(self):
        """Clear violation history."""
        self._violations.clear()
        self._checks_performed = 0
