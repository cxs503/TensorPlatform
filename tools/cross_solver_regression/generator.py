"""Template-based benchmark case generator for cross-solver regression.

Generates ≥10 benchmark cases covering ≥3 solvers from templates.
Each case has:
- Clear reference values (analytical/literature)
- Tolerance thresholds
- Solver assignment
- Synthetic domain name (zero domain semantics)

Generation time target: <1 minute per case.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from .solver_stubs import SOLVER_REGISTRY

__all__ = ["CaseTemplate", "BenchmarkCase", "CaseGenerator", "generate_suite"]


class CaseTemplate:
    """A reusable template for generating benchmark cases.

    Templates define the parameter space and reference solution type.
    """

    def __init__(
        self,
        template_id: str,
        solver_name: str,
        description: str,
        param_space: Dict[str, List[Any]],
        reference_type: str = "analytical",
        default_tolerance: float = 0.05,
    ):
        self.template_id = template_id
        self.solver_name = solver_name
        self.description = description
        self.param_space = param_space
        self.reference_type = reference_type
        self.default_tolerance = default_tolerance

    def to_dict(self) -> Dict[str, Any]:
        return {
            "template_id": self.template_id,
            "solver_name": self.solver_name,
            "description": self.description,
            "param_space": self.param_space,
            "reference_type": self.reference_type,
            "default_tolerance": self.default_tolerance,
        }


class BenchmarkCase:
    """A single benchmark case with reference values and tolerance."""

    def __init__(
        self,
        case_id: str,
        template_id: str,
        solver_name: str,
        parameters: Dict[str, Any],
        reference_values: Dict[str, float],
        tolerances: Dict[str, float],
        description: str = "",
        generation_time_s: float = 0.0,
    ):
        self.case_id = case_id
        self.template_id = template_id
        self.solver_name = solver_name
        self.parameters = parameters
        self.reference_values = reference_values
        self.tolerances = tolerances
        self.description = description
        self.generation_time_s = generation_time_s

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "template_id": self.template_id,
            "solver_name": self.solver_name,
            "parameters": self.parameters,
            "reference_values": self.reference_values,
            "tolerances": self.tolerances,
            "description": self.description,
            "generation_time_s": self.generation_time_s,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BenchmarkCase":
        return cls(
            case_id=d["case_id"],
            template_id=d["template_id"],
            solver_name=d["solver_name"],
            parameters=d["parameters"],
            reference_values=d["reference_values"],
            tolerances=d["tolerances"],
            description=d.get("description", ""),
            generation_time_s=d.get("generation_time_s", 0.0),
        )


class CaseGenerator:
    """Generates benchmark cases from templates.

    Ensures:
    - ≥10 cases covering ≥3 solvers
    - Each case has reference values and tolerances
    - Generation time <1 min per case
    - Zero domain semantics (synthetic domain names)
    """

    def __init__(self, solver_registry: Optional[Dict] = None):
        self.registry = solver_registry or SOLVER_REGISTRY
        self.templates = self._build_default_templates()

    def _build_default_templates(self) -> List[CaseTemplate]:
        """Build default templates for all registered solvers."""
        templates = []

        # TensorLBM templates (diffusion problems)
        for D in [0.05, 0.1, 0.2]:
            for N in [32, 64, 128]:
                templates.append(CaseTemplate(
                    template_id=f"lbm_diff_D{D}_N{N}",
                    solver_name="TensorLBM",
                    description=f"1D diffusion D={D}, N={N}",
                    param_space={
                        "grid_size": [N],
                        "diffusion_coeff": [D],
                        "time_steps": [100],
                    },
                    reference_type="analytical",
                    default_tolerance=0.80,  # FTCS has significant numerical diffusion
                ))

        # TensorFEM templates (Poisson problems)
        for A in [1.0, 2.0]:
            for ne in [16, 32, 64]:
                for p in [1, 2]:
                    templates.append(CaseTemplate(
                        template_id=f"fem_poisson_A{A}_ne{ne}_p{p}",
                        solver_name="TensorFEM",
                        description=f"Poisson A={A}, ne={ne}, p={p}",
                        param_space={
                            "n_elements": [ne],
                            "source_amplitude": [A],
                            "polynomial_order": [p],
                        },
                        reference_type="analytical",
                        default_tolerance=0.05,
                    ))

        # TensorDEM templates (packing problems)
        for phi in [0.55, 0.60, 0.64]:
            for n in [50, 100, 200]:
                templates.append(CaseTemplate(
                    template_id=f"dem_pack_phi{phi}_n{n}",
                    solver_name="TensorDEM",
                    description=f"Packing phi={phi}, n={n}",
                    param_space={
                        "n_particles": [n],
                        "packing_fraction": [phi],
                        "stiffness": [1e4],
                    },
                    reference_type="literature",
                    default_tolerance=0.10,  # DEM has higher inherent variability
                ))

        # TensorFVM templates (advection problems)
        for c in [0.5, 1.0, 2.0]:
            for nc in [32, 64, 128]:
                templates.append(CaseTemplate(
                    template_id=f"fvm_adv_c{c}_nc{nc}",
                    solver_name="TensorFVM",
                    description=f"Advection c={c}, nc={nc}",
                    param_space={
                        "n_cells": [nc],
                        "velocity": [c],
                        "time_steps": [50],
                    },
                    reference_type="analytical",
                    default_tolerance=0.15,  # upwind scheme has numerical diffusion
                ))

        return templates

    def generate_case(self, template: CaseTemplate) -> BenchmarkCase:
        """Generate a single benchmark case from a template.

        Returns a case with reference values computed from the reference solver.
        """
        t0 = time.time()

        # Extract parameters (take first value from each param space)
        params = {k: v[0] for k, v in template.param_space.items()}
        params["problem_id"] = template.template_id

        # Get reference solution
        solver_entry = self.registry.get(template.solver_name)
        if solver_entry is None:
            raise ValueError(f"Unknown solver: {template.solver_name}")

        ref_fn = solver_entry["reference"]
        ref_result = ref_fn(**params)

        # Extract reference values (numeric metrics only)
        reference_values = {
            k: float(v) for k, v in ref_result.items()
            if isinstance(v, (int, float)) and k not in ("grid_size", "time_steps",
                                                          "n_elements", "polynomial_order",
                                                          "n_particles", "n_cells")
        }

        # Set tolerances
        tolerances = {k: template.default_tolerance for k in reference_values}

        gen_time = time.time() - t0

        return BenchmarkCase(
            case_id=f"{template.solver_name}_{template.template_id}",
            template_id=template.template_id,
            solver_name=template.solver_name,
            parameters=params,
            reference_values=reference_values,
            tolerances=tolerances,
            description=template.description,
            generation_time_s=gen_time,
        )

    def generate_suite(
        self,
        min_cases: int = 10,
        min_solvers: int = 3,
        cases_per_solver: int = 3,
    ) -> List[BenchmarkCase]:
        """Generate a suite of benchmark cases.

        Ensures:
        - At least min_cases total cases
        - At least min_solvers different solvers covered
        - At least cases_per_solver cases per solver
        """
        cases = []
        solver_counts: Dict[str, int] = {}

        # Group templates by solver
        templates_by_solver: Dict[str, List[CaseTemplate]] = {}
        for t in self.templates:
            templates_by_solver.setdefault(t.solver_name, []).append(t)

        # Generate cases ensuring solver coverage
        for solver_name, solver_templates in templates_by_solver.items():
            count = 0
            for tmpl in solver_templates:
                if count >= cases_per_solver:
                    break
                case = self.generate_case(tmpl)
                cases.append(case)
                count += 1
            solver_counts[solver_name] = count

        # Ensure minimum total cases
        while len(cases) < min_cases:
            for solver_name, solver_templates in templates_by_solver.items():
                if len(cases) >= min_cases:
                    break
                # Pick next template not yet used
                used_ids = {c.template_id for c in cases if c.solver_name == solver_name}
                for tmpl in solver_templates:
                    if tmpl.template_id not in used_ids:
                        case = self.generate_case(tmpl)
                        cases.append(case)
                        solver_counts[solver_name] = solver_counts.get(solver_name, 0) + 1
                        break

        # Verify constraints
        assert len(cases) >= min_cases, f"Generated {len(cases)} cases, need {min_cases}"
        assert len(solver_counts) >= min_solvers, (
            f"Covered {len(solver_counts)} solvers, need {min_solvers}"
        )

        return cases


def generate_suite(
    min_cases: int = 10,
    min_solvers: int = 3,
    cases_per_solver: int = 3,
) -> List[Dict[str, Any]]:
    """Convenience function: generate suite and return as list of dicts."""
    gen = CaseGenerator()
    cases = gen.generate_suite(
        min_cases=min_cases,
        min_solvers=min_solvers,
        cases_per_solver=cases_per_solver,
    )
    return [c.to_dict() for c in cases]
