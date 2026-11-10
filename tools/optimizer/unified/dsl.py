"""JSON DSL for optimisation problems: schema-validated load / save / compile.

Two JSON Schemas live in the repository:

``schemas/optimizer_problem.v1.schema.json``
    the problem definition (variables / objective / constraints / reference);
``schemas/optimizer_result.v1.schema.json``
    the standardised solve output (optimal point, status, history, violation,
    KKT residual, provenance).

The DSL is deliberately engine-agnostic and domain-neutral: a problem file may
describe *any* continuous optimisation problem; expressions may only use
arithmetic and whitelisted math calls (see :mod:`.expr`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from .compiled import CompiledProblem
from .contract import (
    SCHEMA_PROBLEM,
    SCHEMA_RESULT,
    ConstraintSpec,
    ContractError,
    ObjectiveSpec,
    ProblemSpec,
    SolveResult,
    VariableSpec,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = _REPO_ROOT / "schemas"
PROBLEM_SCHEMA_PATH = SCHEMA_DIR / "optimizer_problem.v1.schema.json"
RESULT_SCHEMA_PATH = SCHEMA_DIR / "optimizer_result.v1.schema.json"

_CACHE: Dict[str, Any] = {}


class DSLValidationError(ContractError):
    """Raised when a JSON document violates the published schema."""


def _load_schema(path: Path) -> Dict[str, Any]:
    key = str(path)
    if key not in _CACHE:
        with open(path, "r", encoding="utf-8") as fh:
            _CACHE[key] = json.load(fh)
    return _CACHE[key]


def _validator(path: Path):
    try:
        import jsonschema
    except Exception as exc:  # pragma: no cover - jsonschema is a hard dependency here
        raise DSLValidationError(f"jsonschema is required for DSL validation: {exc}") from exc
    schema = _load_schema(path)
    return jsonschema.Draft7Validator(schema)


def validate_document(document: Mapping[str, Any], schema_path: Union[str, Path]) -> None:
    """Raise :class:`DSLValidationError` if ``document`` violates the schema."""
    validator = _validator(Path(schema_path))
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))
    if errors:
        details = "; ".join(
            f"{'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}" for err in errors[:8]
        )
        raise DSLValidationError(f"{Path(schema_path).name} validation failed -> {details}")


# ---------------------------------------------------------------------------
# problem documents
# ---------------------------------------------------------------------------


def validate_problem_dict(document: Mapping[str, Any]) -> None:
    validate_document(document, PROBLEM_SCHEMA_PATH)


def problem_from_dict(document: Mapping[str, Any], validate_schema: bool = True) -> ProblemSpec:
    if validate_schema:
        validate_problem_dict(document)
    spec = ProblemSpec.from_dict(document)
    spec.validate()
    return spec


def load_problem(path: Union[str, Path], validate_schema: bool = True) -> ProblemSpec:
    with open(path, "r", encoding="utf-8") as fh:
        document = json.load(fh)
    return problem_from_dict(document, validate_schema=validate_schema)


def loads_problem(text: str, validate_schema: bool = True) -> ProblemSpec:
    return problem_from_dict(json.loads(text), validate_schema=validate_schema)


def problem_to_dict(spec: ProblemSpec) -> Dict[str, Any]:
    return spec.to_dict()


def dump_problem(spec: ProblemSpec, path: Union[str, Path]) -> Path:
    document = spec.to_dict()
    validate_problem_dict(document)          # only schema-valid files are written
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2)
        fh.write("\n")
    return path


def compile_problem(spec: Union[ProblemSpec, Mapping[str, Any], str, Path]) -> CompiledProblem:
    """Compile anything problem-shaped into callables (expression whitelist applied)."""
    if isinstance(spec, CompiledProblem):
        return spec
    if isinstance(spec, ProblemSpec):
        return CompiledProblem(spec)
    if isinstance(spec, Mapping):
        return CompiledProblem(problem_from_dict(spec))
    if isinstance(spec, (str, Path)) and Path(spec).exists():
        return CompiledProblem(load_problem(spec))
    if isinstance(spec, str):
        return CompiledProblem(loads_problem(spec))
    raise DSLValidationError(f"cannot compile problem from {type(spec)!r}")


# ---------------------------------------------------------------------------
# result documents
# ---------------------------------------------------------------------------


def validate_result_dict(document: Mapping[str, Any]) -> None:
    validate_document(document, RESULT_SCHEMA_PATH)


def result_to_dict(result: SolveResult, history_limit: Optional[int] = 200) -> Dict[str, Any]:
    return result.to_dict(history_limit=history_limit)


def validate_result(result: SolveResult, history_limit: Optional[int] = 200) -> Dict[str, Any]:
    """Serialise a result and prove it satisfies the standardised output schema."""
    document = result.to_dict(history_limit=history_limit)
    validate_result_dict(document)
    return document


def dump_result(result: SolveResult, path: Union[str, Path], history_limit: Optional[int] = 200) -> Path:
    document = validate_result(result, history_limit=history_limit)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2)
        fh.write("\n")
    return path


__all__ = [
    "PROBLEM_SCHEMA_PATH",
    "RESULT_SCHEMA_PATH",
    "SCHEMA_PROBLEM",
    "SCHEMA_RESULT",
    "DSLValidationError",
    "validate_document",
    "validate_problem_dict",
    "validate_result_dict",
    "validate_result",
    "problem_from_dict",
    "load_problem",
    "loads_problem",
    "dump_problem",
    "dump_result",
    "problem_to_dict",
    "result_to_dict",
    "compile_problem",
]