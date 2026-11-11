"""Coupled-loop contracts: load, validate fail-closed, apply to a certificate.

A loop certificate says what the iteration did; a *contract* says what it is
allowed to do.  ``contracts/coupled_loop.schema.json`` is the document format,
this module is the enforcement:

``validate_coupled_loop_contract``
    structural errors (missing / unknown keys, bad enums) plus two semantic
    cross-checks the JSON schema cannot express: discipline names must be unique,
    and every ``coupling_var`` must name a producer and a consumer that both
    exist in ``disciplines`` — a dangling exchange is a design inconsistency, not
    a formatting detail.
``load_coupled_loop_contract``
    read + validate, raising :class:`LoopContractError` on any problem (so a bad
    contract can never silently degrade into defaults).
``gate_certificate`` / ``gate_sensitivity`` / ``contract_gate``
    turn the contract into machine verdicts over a :class:`~tools.coupled_loop.Certificate`
    and over a sensitivity-gate record, and combine them into one record with a
    top-level ``passed``.

Discipline names, variable names and parameter names are opaque here: nothing in
this module interprets what a block computes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..coupled_loop import CONVERGED, Certificate

__all__ = [
    "COUPLED_LOOP_SCHEMA_VERSION",
    "LoopContractError",
    "contract_gate",
    "gate_certificate",
    "gate_sensitivity",
    "load_coupled_loop_contract",
    "validate_coupled_loop_contract",
]

COUPLED_LOOP_SCHEMA_VERSION = "coupled-loop.v1"

_REQUIRED = (
    "schema_version",
    "loop_id",
    "disciplines",
    "coupling_vars",
    "scheme",
    "tolerance",
    "max_iter",
    "rho_max",
    "convergence_gate",
)
_ALLOWED = set(_REQUIRED) | {
    "description",
    "relaxation",
    "patience",
    "sensitivity",
    "conservation_laws",
    "negative_controls",
    "provenance",
}
_SCHEMES = ("jacobi", "gauss_seidel")
_RELAXATIONS = ("none", "aitken")
_CONTROL_KINDS = ("divergent_loop", "corrupted_partials", "stalled_loop")


class LoopContractError(ValueError):
    """Raised when a coupled-loop contract is malformed (fail-closed)."""


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) > 0.0


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate_coupled_loop_contract(document: Any) -> List[str]:
    """Return the list of structural/semantic errors; empty means valid."""
    errors: List[str] = []
    if not isinstance(document, dict):
        return ["contract must be a JSON object"]

    for key in _REQUIRED:
        if key not in document:
            errors.append(f"missing required key: {key}")
    for key in document:
        if key not in _ALLOWED:
            errors.append(f"unknown key: {key}")
    if errors:
        return errors

    if document["schema_version"] != COUPLED_LOOP_SCHEMA_VERSION:
        errors.append(f"schema_version must be {COUPLED_LOOP_SCHEMA_VERSION!r}")

    loop_id = document["loop_id"]
    if not isinstance(loop_id, str) or len(loop_id) < 3 or len(loop_id) > 64:
        errors.append("loop_id must be a string of 3..64 characters")

    if document.get("description") is not None and not isinstance(document["description"], str):
        errors.append("description must be a string")

    disciplines = document["disciplines"]
    names: List[str] = []
    if not isinstance(disciplines, list) or not (2 <= len(disciplines) <= 16):
        errors.append("disciplines must be a list of 2..16 blocks")
        disciplines = []
    for block in disciplines:
        if not isinstance(block, dict):
            errors.append("each discipline must be an object")
            continue
        extra = set(block) - {"name", "state_size", "declares_partials"}
        if extra:
            errors.append(f"unknown discipline keys: {sorted(extra)}")
        name = block.get("name")
        if not isinstance(name, str) or not name:
            errors.append("discipline name must be a non-empty string")
        else:
            names.append(name)
        if not _is_positive_int(block.get("state_size")):
            errors.append(f"discipline {name!r}: state_size must be a positive integer")
        if "declares_partials" in block and not isinstance(block["declares_partials"], bool):
            errors.append(f"discipline {name!r}: declares_partials must be a boolean")
    if len(set(names)) != len(names):
        errors.append("discipline names must be unique")

    coupling_vars = document["coupling_vars"]
    seen_vars: List[str] = []
    if not isinstance(coupling_vars, list) or not coupling_vars:
        errors.append("coupling_vars must be a non-empty list")
        coupling_vars = []
    for var in coupling_vars:
        if not isinstance(var, dict):
            errors.append("each coupling_var must be an object")
            continue
        extra = set(var) - {"name", "producer", "consumer"}
        if extra:
            errors.append(f"unknown coupling_var keys: {sorted(extra)}")
        name = var.get("name")
        if not isinstance(name, str) or not name:
            errors.append("coupling_var name must be a non-empty string")
        else:
            seen_vars.append(name)
        for role in ("producer", "consumer"):
            value = var.get(role)
            if not isinstance(value, str) or not value:
                errors.append(f"coupling_var {name!r}: {role} must be a discipline name")
            elif names and value not in names:
                errors.append(f"coupling_var {name!r}: {role} {value!r} is not a declared discipline")
        if var.get("producer") == var.get("consumer") and var.get("producer"):
            errors.append(f"coupling_var {name!r}: producer and consumer must differ")
    if len(set(seen_vars)) != len(seen_vars):
        errors.append("coupling_var names must be unique")
    if names:
        # a fixed-point loop needs feedback: every block must both hand a coupling
        # variable over and receive one, otherwise it is not part of the iteration.
        producers = {v.get("producer") for v in coupling_vars if isinstance(v, dict)}
        consumers = {v.get("consumer") for v in coupling_vars if isinstance(v, dict)}
        never_produce = [n for n in names if n not in producers]
        never_consume = [n for n in names if n not in consumers]
        if never_produce:
            errors.append(f"disciplines that never produce a coupling variable: {sorted(never_produce)}")
        if never_consume:
            errors.append(f"disciplines that never consume a coupling variable: {sorted(never_consume)}")

    if document["scheme"] not in _SCHEMES:
        errors.append(f"scheme must be one of {_SCHEMES}")
    if document.get("relaxation", "none") not in _RELAXATIONS:
        errors.append(f"relaxation must be one of {_RELAXATIONS}")
    if not _is_positive_number(document["tolerance"]) or float(document["tolerance"]) > 1.0:
        errors.append("tolerance must be a positive number <= 1")
    if not _is_positive_int(document["max_iter"]):
        errors.append("max_iter must be a positive integer")
    if not _is_positive_number(document["rho_max"]) or float(document["rho_max"]) > 2.0:
        errors.append("rho_max must be a positive number <= 2")
    if "patience" in document and not _is_positive_int(document["patience"]):
        errors.append("patience must be a positive integer")

    gate = document["convergence_gate"]
    gate_allowed = {
        "rho_deviation",
        "min_iteration_reduction",
        "max_iterations_to_flag_divergence",
    }
    if not isinstance(gate, dict):
        errors.append("convergence_gate must be an object")
    else:
        if "rho_deviation" not in gate:
            errors.append("convergence_gate.rho_deviation is required")
        for key in gate:
            if key not in gate_allowed:
                errors.append(f"unknown convergence_gate key: {key}")
        if "rho_deviation" in gate and (
            not _is_positive_number(gate["rho_deviation"]) or float(gate["rho_deviation"]) > 1.0
        ):
            errors.append("convergence_gate.rho_deviation must be a positive number <= 1")
        if "min_iteration_reduction" in gate and not (
            isinstance(gate["min_iteration_reduction"], (int, float))
            and not isinstance(gate["min_iteration_reduction"], bool)
            and 0.0 <= float(gate["min_iteration_reduction"]) < 1.0
        ):
            errors.append("convergence_gate.min_iteration_reduction must lie in [0, 1)")
        if "max_iterations_to_flag_divergence" in gate and not _is_positive_int(
            gate["max_iterations_to_flag_divergence"]
        ):
            errors.append("convergence_gate.max_iterations_to_flag_divergence must be a positive integer")

    if "sensitivity" in document:
        sens = document["sensitivity"]
        if not isinstance(sens, dict):
            errors.append("sensitivity must be an object")
        else:
            extra = set(sens) - {"parameter", "rtol", "step", "require_declared_partials"}
            if extra:
                errors.append(f"unknown sensitivity keys: {sorted(extra)}")
            if not isinstance(sens.get("parameter"), str) or not sens.get("parameter"):
                errors.append("sensitivity.parameter must be a non-empty string")
            if "rtol" not in sens or not _is_positive_number(sens["rtol"]) or float(sens["rtol"]) > 1.0:
                errors.append("sensitivity.rtol must be a positive number <= 1")
            if "step" in sens and not _is_positive_number(sens["step"]):
                errors.append("sensitivity.step must be a positive number")
            if "require_declared_partials" in sens and not isinstance(
                sens["require_declared_partials"], bool
            ):
                errors.append("sensitivity.require_declared_partials must be a boolean")

    if "conservation_laws" in document and not isinstance(document["conservation_laws"], list):
        errors.append("conservation_laws must be a list of contract ids")

    if "negative_controls" in document:
        controls = document["negative_controls"]
        if not isinstance(controls, list):
            errors.append("negative_controls must be a list")
        else:
            for control in controls:
                if not isinstance(control, dict):
                    errors.append("each negative control must be an object")
                    continue
                extra = set(control) - {"name", "kind", "must_fail", "state_scale", "param_scale"}
                if extra:
                    errors.append(f"unknown negative control keys: {sorted(extra)}")
                if control.get("kind") not in _CONTROL_KINDS:
                    errors.append(f"negative control kind must be one of {_CONTROL_KINDS}")
                if control.get("must_fail") is not True:
                    errors.append("negative control must declare must_fail: true")

    return errors


def load_coupled_loop_contract(path: "str | Path") -> Dict[str, Any]:
    """Read and validate a coupled-loop contract from disk."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_coupled_loop_contract(raw)
    if errors:
        raise LoopContractError("; ".join(errors))
    return raw


def gate_certificate(
    contract: Dict[str, Any],
    certificate: Certificate,
    *,
    spectral_radius: Optional[float] = None,
    unrelaxed_iterations: Optional[int] = None,
) -> Dict[str, Any]:
    """Apply the loop part of the contract to one certificate."""
    errors = validate_coupled_loop_contract(contract)
    if errors:
        raise LoopContractError("; ".join(errors))
    gate = contract["convergence_gate"]
    checks: Dict[str, bool] = {
        "converged": bool(certificate.converged),
        "status_is_converged": certificate.status == CONVERGED,
        "rho_below_max": bool(
            certificate.rho_hat == certificate.rho_hat and certificate.rho_hat < float(contract["rho_max"])
        ),
        "within_iteration_cap": certificate.iterations <= int(contract["max_iter"]),
        "residual_below_tolerance": bool(certificate.final_residual <= float(contract["tolerance"])),
        "scheme_matches_contract": certificate.scheme == contract["scheme"],
        "relaxation_matches_contract": certificate.relaxation == contract.get("relaxation", "none"),
    }
    rho_deviation = None
    if spectral_radius is not None:
        rho_deviation = abs(certificate.rho_hat - float(spectral_radius)) / max(abs(float(spectral_radius)), 1e-300)
        checks["rho_matches_spectral_radius"] = bool(rho_deviation <= float(gate["rho_deviation"]))
    reduction = None
    if unrelaxed_iterations is not None:
        base = max(int(unrelaxed_iterations), 1)
        reduction = 1.0 - certificate.iterations / base
        if "min_iteration_reduction" in gate:
            checks["relaxation_saves_iterations"] = bool(
                reduction >= float(gate["min_iteration_reduction"])
            )
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "rho_hat": float(certificate.rho_hat),
        "rho_max": float(contract["rho_max"]),
        "rho_deviation": rho_deviation,
        "iteration_reduction": reduction,
        "iterations": int(certificate.iterations),
        "status": certificate.status,
    }


def gate_sensitivity(contract: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the sensitivity part of the contract to a gate record."""
    errors = validate_coupled_loop_contract(contract)
    if errors:
        raise LoopContractError("; ".join(errors))
    if "sensitivity" not in contract:
        return {"passed": True, "checks": {}, "reason": "contract declares no sensitivity gate"}
    declared_rtol = float(contract["sensitivity"]["rtol"])
    observed = float(record["relative_error"])
    checks = {
        "composed_matches_resolved": bool(observed <= declared_rtol),
        "gate_record_passed": bool(record.get("passed", False)),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "declared_rtol": declared_rtol,
        "observed_relative_error": observed,
        "parameter": contract["sensitivity"]["parameter"],
    }


def contract_gate(
    contract: Dict[str, Any],
    certificate: Certificate,
    sensitivity: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """One verdict combining the loop certificate and (optionally) sensitivity."""
    loop = gate_certificate(contract, certificate, **kwargs)
    parts = {"loop": loop}
    passed = loop["passed"]
    if sensitivity is not None:
        sens = gate_sensitivity(contract, sensitivity)
        parts["sensitivity"] = sens
        passed = passed and sens["passed"]
    return {
        "passed": bool(passed),
        "loop_id": contract.get("loop_id"),
        "parts": parts,
    }