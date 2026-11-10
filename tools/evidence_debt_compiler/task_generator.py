"""Verification task generator: create task specs from classified debts.

Each classified debt generates at least one VerificationTask with a spec
that describes what needs to be computed/checked to resolve the debt.

Zero-domain-semantics: task specs are pure data structures. The actual
computation is delegated to the cluster/runtime via the spec's ``cmd``
or ``api_call`` fields, which are also data-driven.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from .model import BaselineType, ClassifiedDebt, VerificationTask


# Default task templates per baseline type — domain-agnostic structure only.
_DEFAULT_TEMPLATES: Dict[str, Dict[str, Any]] = {
    BaselineType.ANALYTICAL_SOLUTION.value: {
        "verification_kind": "analytical_comparison",
        "required_evidence": {
            "kind": "numeric_verification",
            "metrics": [
                {"name": "error_pct", "op": "<=", "value": 3.0},
            ],
        },
        "task_description": "Compare numerical result against analytical solution",
        "acceptance_criteria": {
            "max_error_pct": 3.0,
            "min_cases": 1,
        },
    },
    BaselineType.EXPERIMENTAL.value: {
        "verification_kind": "experimental_validation",
        "required_evidence": {
            "kind": "experimental_comparison",
            "metrics": [
                {"name": "error_pct", "op": "<=", "value": 10.0},
            ],
        },
        "task_description": "Compare numerical result against experimental data",
        "acceptance_criteria": {
            "max_error_pct": 10.0,
            "min_cases": 1,
        },
    },
    BaselineType.MESH_CONVERGENCE.value: {
        "verification_kind": "mesh_convergence_study",
        "required_evidence": {
            "kind": "convergence_study",
            "metrics": [
                {"name": "convergence_order", "op": ">=", "value": 1.0},
                {"name": "asymptotic_range", "op": "==", "value": True},
            ],
        },
        "task_description": "Perform mesh refinement study to verify convergence",
        "acceptance_criteria": {
            "min_mesh_levels": 3,
            "convergence_order_min": 1.0,
        },
    },
    BaselineType.PHYSICAL_CONSISTENCY.value: {
        "verification_kind": "physical_consistency_check",
        "required_evidence": {
            "kind": "conservation_check",
            "metrics": [
                {"name": "residual_pct", "op": "<=", "value": 1.0},
            ],
        },
        "task_description": "Verify physical conservation laws / symmetry / dimensional consistency",
        "acceptance_criteria": {
            "max_residual_pct": 1.0,
        },
    },
    BaselineType.CROSS_VALIDATION.value: {
        "verification_kind": "cross_validation",
        "required_evidence": {
            "kind": "cross_method_comparison",
            "metrics": [
                {"name": "disagreement_pct", "op": "<=", "value": 5.0},
            ],
        },
        "task_description": "Compare against independent method/code for cross-validation",
        "acceptance_criteria": {
            "max_disagreement_pct": 5.0,
        },
    },
    BaselineType.UNKNOWN.value: {
        "verification_kind": "general_verification",
        "required_evidence": {
            "kind": "numeric_verification",
            "metrics": [],
        },
        "task_description": "Perform general verification (baseline type unknown)",
        "acceptance_criteria": {},
    },
}


class VerificationTaskGenerator:
    """Generate verification task specs from classified debts.

    Parameters
    ----------
    templates : dict, optional
        Task templates keyed by BaselineType value. Defaults to built-in
        domain-agnostic templates.
    """

    def __init__(self, templates: Optional[Dict[str, Dict[str, Any]]] = None):
        self.templates = templates or _DEFAULT_TEMPLATES

    def generate(self, classified: ClassifiedDebt) -> VerificationTask:
        """Generate a verification task for a classified debt."""
        template = self.templates.get(
            classified.baseline_type.value,
            self.templates[BaselineType.UNKNOWN.value],
        )

        spec = {
            **template,
            "capability_id": classified.capability_id,
            "debt_id": classified.debt_id,
            "baseline_type": classified.baseline_type.value,
            "confidence": classified.confidence,
        }

        return VerificationTask(
            task_id=f"vtask-{uuid.uuid4().hex[:12]}",
            debt_id=classified.debt_id,
            capability_id=classified.capability_id,
            baseline_type=classified.baseline_type,
            spec=spec,
        )

    def generate_batch(
        self, classified_debts: List[ClassifiedDebt]
    ) -> List[VerificationTask]:
        """Generate tasks for a batch of classified debts."""
        return [self.generate(c) for c in classified_debts]

    def generate_with_overrides(
        self,
        classified: ClassifiedDebt,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> VerificationTask:
        """Generate a task with spec overrides (e.g., from campaign config)."""
        task = self.generate(classified)
        if overrides:
            task.spec.update(overrides)
        return task
