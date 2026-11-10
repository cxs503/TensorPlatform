"""Cross-solver verification framework for TensorPlatform.

Provides:
- Unified input/output schema validation
- Physical conservation residual gate
- Automatic mutual arbitration engine for cross-solver comparison
- Traceability (SHA-256 digests, solver version, git commit)
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
    """Run mutual arbitration across multiple solver outputs for the same case/quantity.

    Args:
        case_id: The physical case identifier (e.g., 'wigley_iii_fn0.25')
        quantity_name: The quantity to compare (e.g., 'C_T')
        outputs: List of output_schema dicts from different solvers
        tolerance_pct: Relative difference tolerance in percent

    Returns:
        ArbitrationReport with pairwise comparisons and consensus.
    """
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
            a, b = entries[i], entries[j]
            ref = abs(a.value) if abs(a.value) > 1e-15 else abs(b.value)
            if ref < 1e-15:
                rel_diff = 0.0 if abs(a.value - b.value) < 1e-15 else float("inf")
            else:
                rel_diff = abs(a.value - b.value) / ref * 100.0

            within = rel_diff <= tolerance_pct

            # Attribution: explain the difference
            if within:
                attribution = f"Difference ({rel_diff:.2f}%) within {tolerance_pct}% tolerance; solvers agree"
            else:
                attribution = (
                    f"Difference ({rel_diff:.2f}%) exceeds {tolerance_pct}% tolerance; "
                    f"possible causes: grid resolution, turbulence model, boundary condition treatment, "
                    f"or numerical scheme differences between {a.solver_name} and {b.solver_name}"
                )

            pair_results.append(ArbitrationPairResult(
                solver_a=a.solver_name,
                solver_b=b.solver_name,
                quantity_name=quantity_name,
                value_a=a.value,
                value_b=b.value,
                relative_difference=round(rel_diff, 4),
                within_tolerance=within,
                tolerance_pct=tolerance_pct,
                attribution=attribution,
            ))

    # Consensus
    values = [e.value for e in entries]
    consensus = sum(values) / len(values)
    spread = max(values) - min(values)
    spread_pct = (spread / abs(consensus) * 100.0) if abs(consensus) > 1e-15 else 0.0

    all_within = all(p.within_tolerance for p in pair_results)
    summary = (
        f"{'All' if all_within else 'Some'} solver pairs {'agree' if all_within else 'disagree'} "
        f"on {quantity_name} for case {case_id}. "
        f"Consensus={consensus:.6g}, spread={spread_pct:.2f}%, "
        f"{len(entries)} solvers compared."
    )

    return ArbitrationReport(
        case_id=case_id,
        quantity_name=quantity_name,
        entries=entries,
        pair_results=pair_results,
        consensus_value=round(consensus, 8),
        consensus_spread_pct=round(spread_pct, 4),
        summary=summary,
    )


def arbitration_report_to_dict(report: ArbitrationReport) -> dict:
    """Convert ArbitrationReport to JSON-serializable dict."""
    return {
        "case_id": report.case_id,
        "quantity_name": report.quantity_name,
        "entries": [asdict(e) for e in report.entries],
        "pair_results": [asdict(p) for p in report.pair_results],
        "consensus_value": report.consensus_value,
        "consensus_spread_pct": report.consensus_spread_pct,
        "summary": report.summary,
    }


# ---------------------------------------------------------------------------
# Schema coverage analysis
# ---------------------------------------------------------------------------
@dataclass
class CoverageStats:
    total_cases: int = 0
    geometry_type_parsed: int = 0
    geometry_dimensions_parsed: int = 0
    primary_quantities_parsed: int = 0
    conservation_residuals_present: int = 0
    traceability_complete: int = 0

    @property
    def geometry_type_pct(self) -> float:
        return (self.geometry_type_parsed / self.total_cases * 100.0) if self.total_cases else 0.0

    @property
    def primary_quantities_pct(self) -> float:
        return (self.primary_quantities_parsed / self.total_cases * 100.0) if self.total_cases else 0.0


def analyze_coverage(
    inputs: list[dict],
    outputs: list[dict],
) -> CoverageStats:
    """Analyze schema coverage across a set of cases."""
    stats = CoverageStats(total_cases=len(inputs))

    for inp in inputs:
        geo = inp.get("geometry", {})
        if geo.get("type"):
            stats.geometry_type_parsed += 1
        if geo.get("dimensions"):
            stats.geometry_dimensions_parsed += 1

    for out in outputs:
        pq = out.get("primary_quantities")
        if pq and isinstance(pq, list) and len(pq) > 0:
            stats.primary_quantities_parsed += 1
        if out.get("conservation_residuals"):
            stats.conservation_residuals_present += 1
        trace = out.get("traceability", {})
        if trace.get("output_digest") and trace.get("computed_at"):
            stats.traceability_complete += 1

    return stats
