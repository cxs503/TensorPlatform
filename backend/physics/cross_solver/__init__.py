"""Cross-solver verification framework for TensorPlatform.

Provides:
- Unified input/output schema validation
- Physical conservation residual gate
- Automatic mutual arbitration engine for cross-solver comparison
- Traceability (SHA-256 digests, solver version, git commit)
- Wigley hull BEM (Neumann-Kelvin) / LBM (two-phase) cross-validation
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas"
INPUT_SCHEMA_PATH = SCHEMA_DIR / "input_schema.json"
OUTPUT_SCHEMA_PATH = SCHEMA_DIR / "output_schema.json"

# Default conservation tolerances (can be overridden per case)
DEFAULT_CONSERVATION_TOLERANCES = {
    "mass": 1e-6,
    "momentum_x": 1e-4,
    "momentum_y": 1e-4,
    "momentum_z": 1e-4,
    "energy": 1e-4,
    "angular_momentum": 1e-4,
}

# ---------------------------------------------------------------------------
# Schema loading (lazy, cached)
# ---------------------------------------------------------------------------
_schema_cache: dict[str, Any] = {}


def _load_schema(path: Path) -> dict:
    if str(path) not in _schema_cache:
        with open(path) as f:
            _schema_cache[str(path)] = json.load(f)
    return _schema_cache[str(path)]


def load_input_schema() -> dict:
    return _load_schema(INPUT_SCHEMA_PATH)


def load_output_schema() -> dict:
    return _load_schema(OUTPUT_SCHEMA_PATH)


# ---------------------------------------------------------------------------
# JSON Schema validation (stdlib-only, no jsonschema dependency required)
# ---------------------------------------------------------------------------
def _validate_required(obj: dict, required: list[str], context: str) -> list[str]:
    """Check required keys; return list of missing field paths."""
    errors = []
    for key in required:
        if key not in obj:
            errors.append(f"{context}: missing required field '{key}'")
    return errors


def _validate_type(value: Any, expected: str | list, context: str) -> list[str]:
    """Basic type validation."""
    errors = []
    if isinstance(expected, list):
        type_map = {"string": str, "number": (int, float), "integer": int,
                    "object": dict, "array": list, "boolean": bool, "null": type(None)}
        valid = False
        for t in expected:
            py_type = type_map.get(t)
            if py_type and isinstance(value, py_type):
                valid = True
                break
        if not valid:
            errors.append(f"{context}: expected type {expected}, got {type(value).__name__}")
    else:
        type_map = {"string": str, "number": (int, float), "integer": int,
                    "object": dict, "array": list, "boolean": bool}
        py_type = type_map.get(expected)
        if py_type and not isinstance(value, py_type):
            errors.append(f"{context}: expected type '{expected}', got {type(value).__name__}")
    return errors


def validate_input_schema(data: dict) -> list[str]:
    """Validate input data against input_schema.json. Returns list of errors (empty = valid)."""
    errors = []
    schema = load_input_schema()

    # Top-level required
    errors.extend(_validate_required(data, schema.get("required", []), "input"))

    # geometry
    if "geometry" in data:
        geo = data["geometry"]
        errors.extend(_validate_required(geo, ["type", "dimensions"], "input.geometry"))
        if "type" in geo:
            errors.extend(_validate_type(geo["type"], "string", "input.geometry.type"))
        if "dimensions" in geo:
            errors.extend(_validate_type(geo["dimensions"], "object", "input.geometry.dimensions"))

    # physics
    if "physics" in data:
        phys = data["physics"]
        errors.extend(_validate_required(phys, ["boundary_conditions"], "input.physics"))

    # solver
    if "solver" in data:
        solv = data["solver"]
        errors.extend(_validate_required(solv, ["name", "version"], "input.solver"))
        if "name" in solv:
            errors.extend(_validate_type(solv["name"], "string", "input.solver.name"))
        if "version" in solv:
            errors.extend(_validate_type(solv["version"], "string", "input.solver.version"))

    return errors


def validate_output_schema(data: dict) -> list[str]:
    """Validate output data against output_schema.json. Returns list of errors (empty = valid)."""
    errors = []
    schema = load_output_schema()

    errors.extend(_validate_required(data, schema.get("required", []), "output"))

    # primary_quantities must be non-empty array
    if "primary_quantities" in data:
        pq = data["primary_quantities"]
        if not isinstance(pq, list):
            errors.append("output.primary_quantities: must be an array")
        elif len(pq) == 0:
            errors.append("output.primary_quantities: must have at least one entry")
        else:
            for i, item in enumerate(pq):
                if not isinstance(item, dict):
                    errors.append(f"output.primary_quantities[{i}]: must be an object")
                else:
                    errors.extend(_validate_required(item, ["name", "value", "unit"],
                                                    f"output.primary_quantities[{i}]"))

    # conservation_residuals
    if "conservation_residuals" in data and data["conservation_residuals"] is not None:
        for i, item in enumerate(data["conservation_residuals"]):
            if not isinstance(item, dict):
                errors.append(f"output.conservation_residuals[{i}]: must be an object")
            else:
                errors.extend(_validate_required(item, ["law", "residual", "tolerance"],
                                                f"output.conservation_residuals[{i}]"))

    return errors


# ---------------------------------------------------------------------------
# Traceability: SHA-256 digest
# ---------------------------------------------------------------------------
def compute_digest(data: dict) -> str:
    """Compute SHA-256 of canonical JSON (sorted keys, no extra whitespace)."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def enrich_with_traceability(data: dict, role: str = "input") -> dict:
    """Add traceability metadata (digest, timestamp) to a record."""
    import datetime
    result = dict(data)
    digest_key = "input_digest" if role == "input" else "output_digest"
    if "traceability" not in result:
        result["traceability"] = {}
    result["traceability"][digest_key] = compute_digest(data)
    result["traceability"]["computed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return result


# ---------------------------------------------------------------------------
# Conservation residual gate
# ---------------------------------------------------------------------------
@dataclass
class ConservationCheckResult:
    law: str
    residual: float
    tolerance: float
    passed: bool
    message: str


def check_conservation_residuals(
    output_data: dict,
    tolerances: dict[str, float] | None = None,
) -> list[ConservationCheckResult]:
    """Check conservation residuals against tolerances.

    Returns list of ConservationCheckResult. If any residual exceeds tolerance,
    the case is marked 'conservation_fail'.
    """
    if tolerances is None:
        tolerances = DEFAULT_CONSERVATION_TOLERANCES

    residuals = output_data.get("conservation_residuals")
    if residuals is None:
        return []

    results = []
    for item in residuals:
        law = item.get("law", "unknown")
        residual = item.get("residual", 0.0)
        tol = item.get("tolerance", tolerances.get(law, 1e-6))

        passed = abs(residual) <= tol
        results.append(ConservationCheckResult(
            law=law,
            residual=residual,
            tolerance=tol,
            passed=passed,
            message=f"{law}: residual={residual:.2e}, tolerance={tol:.2e}, {'PASS' if passed else 'FAIL'}",
        ))

    return results


def has_conservation_failure(results: list[ConservationCheckResult]) -> bool:
    """Return True if any conservation check failed."""
    return any(not r.passed for r in results)


def apply_conservation_gate(output_data: dict) -> dict:
    """Apply conservation gate to output data. Updates validation_status and adds notes."""
    results = check_conservation_residuals(output_data)
    if not results:
        return output_data

    failed = has_conservation_failure(results)
    notes = output_data.get("validation_notes") or []

    for r in results:
        notes.append(r.message)

    output_data["validation_notes"] = notes
    if failed:
        output_data["validation_status"] = "conservation_fail"
    elif output_data.get("validation_status") == "conservation_fail":
        pass  # keep existing fail status
    elif output_data.get("validation_status") is None:
        output_data["validation_status"] = "pass"

    return output_data


# ---------------------------------------------------------------------------
# Auto mutual arbitration engine
# ---------------------------------------------------------------------------
@dataclass
class ArbitrationEntry:
    solver_name: str
    solver_version: str
    value: float
    unit: str


@dataclass
class ArbitrationPairResult:
    solver_a: str
    solver_b: str
    quantity_name: str
    value_a: float
    value_b: float
    relative_difference: float
    within_tolerance: bool
    tolerance_pct: float
    attribution: str


@dataclass
class ArbitrationReport:
    case_id: str
    quantity_name: str
    entries: list[ArbitrationEntry]
    pair_results: list[ArbitrationPairResult]
    consensus_value: float
    consensus_spread_pct: float
    summary: str


def extract_quantity(output_data: dict, quantity_name: str) -> float | None:
    """Extract a named quantity from output primary_quantities."""
    for pq in output_data.get("primary_quantities", []):
        if pq.get("name") == quantity_name:
            return pq.get("value")
    return None


def run_mutual_arbitration(
    case_id: str,
    quantity_name: str,
    outputs: list[dict],
    tolerance_pct: float = 5.0,
) -> ArbitrationReport:
    """Run mutual arbitration across multiple solver outputs for the same case/quantity."""
    entries = []
    for out in outputs:
        solver_sig = out.get("solver_signature", {})
        val = extract_quantity(out, quantity_name)
        if val is not None:
            unit = ""
            for pq in out.get("primary_quantities", []):
                if pq.get("name") == quantity_name:
                    unit = pq.get("unit", "dimensionless")
                    break
            entries.append(ArbitrationEntry(
                solver_name=solver_sig.get("name", "unknown"),
                solver_version=solver_sig.get("version", "unknown"),
                value=val,
                unit=unit,
            ))

    if len(entries) < 2:
        return ArbitrationReport(
            case_id=case_id,
            quantity_name=quantity_name,
            entries=entries,
            pair_results=[],
            consensus_value=entries[0].value if entries else 0.0,
            consensus_spread_pct=0.0,
            summary=f"Insufficient solvers for arbitration (need >= 2, got {len(entries)})",
        )

    # Pairwise comparisons
    pair_results = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            ea, eb = entries[i], entries[j]
            ref = abs(ea.value) if abs(ea.value) > 1e-30 else abs(eb.value)
            if ref < 1e-30:
                rel_diff = 0.0
            else:
                rel_diff = abs(ea.value - eb.value) / ref * 100.0

            pair_results.append(ArbitrationPairResult(
                solver_a=ea.solver_name,
                solver_b=eb.solver_name,
                quantity_name=quantity_name,
                value_a=ea.value,
                value_b=eb.value,
                relative_difference=rel_diff,
                within_tolerance=rel_diff <= tolerance_pct,
                tolerance_pct=tolerance_pct,
                attribution=f"{ea.solver_name} vs {eb.solver_name}",
            ))

    # Consensus
    values = [e.value for e in entries]
    consensus = float(np.mean(values))
    spread = float(np.std(values) / consensus * 100.0) if abs(consensus) > 1e-30 else 0.0

    all_pass = all(p.within_tolerance for p in pair_results)
    summary = (
        f"{'All' if all_pass else 'Some'} solver pairs within {tolerance_pct}% tolerance. "
        f"Consensus: {consensus:.6g} (spread {spread:.2f}%)"
    )

    return ArbitrationReport(
        case_id=case_id,
        quantity_name=quantity_name,
        entries=entries,
        pair_results=pair_results,
        consensus_value=consensus,
        consensus_spread_pct=spread,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Wigley hull cross-validation (BEM Neumann-Kelvin / LBM two-phase)
# ---------------------------------------------------------------------------
# The cross-validation layer depends on the optional solver-case modules; those
# files are not always present in a given checkout (they can be filtered by
# release gates).  Keep the package importable regardless: the schema /
# traceability / conservation-gate part above is always available, and the
# optional block degrades to ``_OPTIONAL_IMPORT_ERROR`` instead of breaking every
# importer of ``physics.cross_solver``.
_OPTIONAL_IMPORT_ERROR: Exception | None = None
try:  # pragma: no cover - availability depends on the checkout
    from .wigley_bem_nk import NKResult, solve_wigley_nk, WigleyGeometry
    from .wigley_lbm_twophase import (LBMResult, solve_wigley_lbm,
                                      grid_convergence_study)
    from .cross_validation import (
        CrossValidationEntry,
        SensitivityResult,
        CrossValidationReport,
        run_cross_validation,
        run_sensitivity_analysis,
        generate_comparison_figures,
        update_capability_ledger,
        build_full_report,
    )
except Exception as _exc:  # noqa: BLE001
    _OPTIONAL_IMPORT_ERROR = _exc

#: Bit-reproducible checkpoint / state-serialisation layer (always importable;
#: it depends on numpy only).
from .checkpoint import (  # noqa: E402
    CODEC_LZMA,
    CODEC_NONE,
    CODEC_ZSTD,
    CheckpointCorruption,
    CheckpointError,
    CheckpointManager,
    CheckpointStore,
    CoupledRunner,
    CoupledSolver,
    EnergyLedger,
    ExchangeBus,
    NoValidCheckpoint,
    SolverState,
    TimeSynchronizer,
    available_codecs,
    extrapolate_to,
    flip_bit_in_file,
    measure_io_throughput,
    run_restart_equivalence,
)

__all__ = [
    # Schema validation
    "load_input_schema",
    "load_output_schema",
    "validate_input_schema",
    "validate_output_schema",
    # Traceability
    "compute_digest",
    "enrich_with_traceability",
    # Conservation gate
    "ConservationCheckResult",
    "check_conservation_residuals",
    "has_conservation_failure",
    "apply_conservation_gate",
    "DEFAULT_CONSERVATION_TOLERANCES",
    # Arbitration
    "ArbitrationEntry",
    "ArbitrationPairResult",
    "ArbitrationReport",
    "extract_quantity",
    "run_mutual_arbitration",
    # Wigley cross-validation
    "WigleyGeometry",
    "NKResult",
    "LBMResult",
    "solve_wigley_nk",
    "solve_wigley_lbm",
    "grid_convergence_study",
    "CrossValidationEntry",
    "SensitivityResult",
    "CrossValidationReport",
    "run_cross_validation",
    "run_sensitivity_analysis",
    "generate_comparison_figures",
    "update_capability_ledger",
    "build_full_report",
    # Checkpoint / bit-reproducibility layer
    "CODEC_LZMA",
    "CODEC_NONE",
    "CODEC_ZSTD",
    "CheckpointCorruption",
    "CheckpointError",
    "CheckpointManager",
    "CheckpointStore",
    "CoupledRunner",
    "CoupledSolver",
    "EnergyLedger",
    "ExchangeBus",
    "NoValidCheckpoint",
    "SolverState",
    "TimeSynchronizer",
    "available_codecs",
    "extrapolate_to",
    "flip_bit_in_file",
    "measure_io_throughput",
    "run_restart_equivalence",
]
