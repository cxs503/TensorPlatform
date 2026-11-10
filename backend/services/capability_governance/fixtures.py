"""Synthetic fixture cases for the four-dimension attribution chain.

Each case is built so the *injected* perturbation is known exactly, which is what
makes the attribution testable: the chain must recover the injected dimension as
the dominant contribution and drive the unexplained residual to zero.  The last
case deliberately carries an unmodelled 3 % difference and declares only a single
grid level, so it must be reported as *not* attributed — the residual gate has to
be able to say no.

Convention: ``grid_levels`` of an arm are expressed in the **same reported
coordinates as that arm's ``value``** (i.e. before unit/definition correction),
exactly like a solver would report each refinement level.  Getting this wrong is
the single easiest way to make the discretisation channel absorb everything, so
:func:`_grid` takes the physical value and the arm's presentation scale.

Generic arithmetic only: values, units and "grid levels" are abstract numbers.
"""
from __future__ import annotations

from typing import Any, Dict, List

BASE_VALUE = 100.0
CURVATURE = 12.0  # value(h) = base + CURVATURE * h^2


def _levels(physical_base: float, presentation_scale: float,
            hs=(0.02, 0.01)) -> List[Dict[str, float]]:
    """Grid sequence in the arm's reported coordinates."""
    return [{"h": h, "value": (physical_base + CURVATURE * h * h) / presentation_scale}
            for h in hs]


def _reference_arm() -> Dict[str, Any]:
    return {
        "arm_id": "ref",
        "value": BASE_VALUE,
        "inputs": {"u": 1.0, "w": 2.0},
        "sensitivities": {"u": 2.0, "w": 0.5},
        "format": {"unit_scale": 1.0, "normalization_base": 1.0, "sign": 1.0},
        "output_definition": {"definition_scale": 1.0},
        "grid_levels": _levels(BASE_VALUE, 1.0),
        "observed_order": 2.0,
    }


def attribution_cases() -> List[Dict[str, Any]]:
    """Six cases: one per dimension, one combined, one deliberately unexplained."""
    ref = _reference_arm()
    input_factor = 1.01

    format_arm = {**_reference_arm(), "arm_id": "arm_format",
                  "value": BASE_VALUE / 1000.0,
                  "format": {"unit_scale": 1000.0, "normalization_base": 1.0, "sign": 1.0},
                  "grid_levels": _levels(BASE_VALUE, 1000.0)}

    output_arm = {**_reference_arm(), "arm_id": "arm_output",
                  "value": BASE_VALUE / 2.0,
                  "output_definition": {"definition_scale": 2.0},
                  "grid_levels": _levels(BASE_VALUE, 2.0)}

    input_physical = BASE_VALUE * input_factor ** 2.0
    input_arm = {**_reference_arm(), "arm_id": "arm_input",
                 "value": input_physical,
                 "inputs": {"u": input_factor, "w": 2.0},
                 "grid_levels": _levels(input_physical, 1.0)}

    mesh_arm = {**_reference_arm(), "arm_id": "arm_mesh",
                "value": BASE_VALUE + CURVATURE * 0.01 ** 2,
                "grid_levels": [{"h": 0.04, "value": BASE_VALUE + CURVATURE * 0.04 ** 2},
                                {"h": 0.02, "value": BASE_VALUE + CURVATURE * 0.02 ** 2}]}

    combined_physical = input_physical
    combined_scale = 1000.0 * 2.0
    combined_arm = {**_reference_arm(), "arm_id": "arm_combined",
                    "value": combined_physical / combined_scale,
                    "inputs": {"u": input_factor, "w": 2.0},
                    "format": {"unit_scale": 1000.0, "normalization_base": 1.0, "sign": 1.0},
                    "output_definition": {"definition_scale": 2.0},
                    "grid_levels": _levels(combined_physical, combined_scale)}

    # unmodelled 3 % difference, single grid level: nothing may absorb it
    unexplained_arm = {**_reference_arm(), "arm_id": "arm_unexplained",
                       "value": BASE_VALUE * 1.03,
                       "grid_levels": []}

    return [
        {"case_id": "case_format", "quantity": "q", "reference_id": "ref",
         "arms": [ref, format_arm], "expect_dominant": "format"},
        {"case_id": "case_output", "quantity": "q", "reference_id": "ref",
         "arms": [ref, output_arm], "expect_dominant": "output"},
        {"case_id": "case_input", "quantity": "q", "reference_id": "ref",
         "arms": [ref, input_arm], "expect_dominant": "input_definition"},
        {"case_id": "case_mesh", "quantity": "q", "reference_id": "ref",
         "arms": [ref, mesh_arm], "expect_dominant": "discretisation"},
        {"case_id": "case_combined", "quantity": "q", "reference_id": "ref",
         "arms": [ref, combined_arm], "expect_dominant": None},
        {"case_id": "case_unexplained", "quantity": "q", "reference_id": "ref",
         "arms": [ref, unexplained_arm], "expect_dominant": None, "expect_attributed": False},
    ]