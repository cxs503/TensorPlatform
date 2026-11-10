"""Execution layer: evaluator registry, per-coupling consistency, coverage, e2e.

Domain-agnostic.  Every number comes from a case pack loaded through
``spec``; this module only decides *how* primary and independent values are
compared and how verdicts aggregate (PASS / FAIL / WITHHELD).

Aggregation rules (never upgrade evidence):
  * a criterion FAILs if any evaluated point exceeds its tolerance;
  * a criterion is WITHHELD if it cannot be evaluated (missing evaluator,
    malformed output, dimension mismatch) — WITHHELD is never PASS;
  * the gate PASSes only when every criterion PASSes.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence, Tuple

from .spec import Coupling, GateSpec, REQUIRED_CONTRACT_FIELDS, SpecError, load_spec

PASS = "PASS"
FAIL = "FAIL"
WITHHELD = "WITHHELD"


# --------------------------------------------------------------------- helpers
def relative_error(primary: float, independent: float, atol: float = 1e-12) -> float:
    """Relative discrepancy in percent, floored at ``atol`` to stay finite."""
    denom = max(abs(independent), atol)
    return abs(primary - independent) / denom * 100.0


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _floats(values: Iterable[Any], where: str) -> List[float]:
    out: List[float] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise SpecError(f"{where}: non-numeric evaluator output {v!r}")
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            raise SpecError(f"{where}: non-finite evaluator output")
        out.append(f)
    return out


# ------------------------------------------------------------------- registry
class EvaluatorRegistry:
    """Loads a pack's evaluator module by path and records its provenance."""

    def __init__(self, module_path: str | Path, allow_root: str | Path | None = None) -> None:
        self.path = Path(module_path).resolve()
        if allow_root is not None:
            root = Path(allow_root).resolve()
            if root not in self.path.parents and self.path != root:
                raise SpecError(f"evaluator module {self.path} escapes allow-root {root}")
        if not self.path.exists():
            raise SpecError(f"evaluator module not found: {self.path}")
        self.sha256 = _sha256_file(self.path)
        spec = importlib.util.spec_from_file_location(
            f"_cross_domain_coupling_pack_{self.sha256[:12]}", self.path
        )
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise SpecError(f"cannot import evaluator module {self.path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.module = module
        self.path_str = str(self.path)

    def has(self, name: str) -> bool:
        return callable(getattr(self.module, name, None))

    def call(self, name: str, payload: Any) -> Any:
        fn: Callable[[Any], Any] | None = getattr(self.module, name, None)
        if not callable(fn):
            raise SpecError(f"evaluator {name!r} not registered in {self.path.name}")
        return fn(payload)


# ------------------------------------------------------------------ coverage
def coverage_report(spec: GateSpec, registry: EvaluatorRegistry) -> Dict[str, Any]:
    """Interface-contract coverage: declared vs complete vs wired."""
    items: List[Dict[str, Any]] = []
    for c in spec.couplings:
        missing = [f for f in REQUIRED_CONTRACT_FIELDS if not str(c.contract_fields().get(f, "")).strip()]
        dim_ok = True
        factor = 1.0
        dim_note = ""
        try:
            factor = spec.conversion_factor(c.unit, c.consumer_unit)
        except SpecError as exc:
            dim_ok = False
            dim_note = str(exc)
        wired = registry.has(c.primary) and registry.has(c.independent)
        covered = (not missing) and dim_ok and wired
        items.append(
            {
                "coupling": c.id,
                "producer": c.producer,
                "consumer": c.consumer,
                "quantity": c.quantity,
                "unit": c.unit,
                "consumer_unit": c.consumer_unit,
                "unit_dimension": spec.unit(c.unit).dimension if c.unit in spec.units else None,
                "unit_conversion_factor": factor,
                "missing_contract_fields": missing,
                "dimension_compatible": dim_ok,
                "dimension_note": dim_note,
                "evaluators_wired": wired,
                "covered": covered,
            }
        )
    total = len(items)
    covered_n = sum(1 for i in items if i["covered"])
    return {
        "criterion": "interface_contract_coverage",
        "required_fields": list(REQUIRED_CONTRACT_FIELDS),
        "items": items,
        "total_interfaces": total,
        "covered_interfaces": covered_n,
        "coverage_pct": 100.0 * covered_n / total if total else 0.0,
        "verdict": PASS if covered_n == total and total > 0 else FAIL,
    }


# ------------------------------------------------------------ coupling check
def coupling_check(spec: GateSpec, coupling: Coupling, registry: EvaluatorRegistry) -> Dict[str, Any]:
    case = spec.cases[coupling.case_key]
    payload = dict(case)
    payload.setdefault("conversion_factor", spec.conversion_factor(coupling.unit, coupling.consumer_unit))
    record: Dict[str, Any] = {
        "criterion": f"coupling:{coupling.id}",
        "id": coupling.id,
        "title": coupling.title or f"{coupling.producer} -> {coupling.consumer}",
        "quantity": coupling.quantity,
        "unit": coupling.unit,
        "tolerance_pct": coupling.tolerance_pct,
        "evidence_grade": coupling.evidence_grade,
        "case_key": coupling.case_key,
        "primary_evaluator": coupling.primary,
        "independent_evaluator": coupling.independent,
    }
    try:
        primary = _floats(registry.call(coupling.primary, payload), f"{coupling.id}.primary")
        independent = _floats(registry.call(coupling.independent, payload), f"{coupling.id}.independent")
    except SpecError as exc:
        record.update({"verdict": WITHHELD, "reason": str(exc)})
        return record
    if not primary or len(primary) != len(independent):
        record.update(
            {
                "verdict": WITHHELD,
                "reason": f"length mismatch primary={len(primary)} independent={len(independent)}",
            }
        )
        return record

    errors = [relative_error(p, i, coupling.atol) for p, i in zip(primary, independent)]
    max_err = max(errors)
    mean_err = sum(errors) / len(errors)
    worst = errors.index(max_err)
    record.update(
        {
            "primary": primary,
            "independent": independent,
            "errors_pct": errors,
            "n_points": len(primary),
            "max_error_pct": max_err,
            "mean_error_pct": mean_err,
            "worst_point_index": worst,
            "worst_independent_value": independent[worst],
            "margin_pct": coupling.tolerance_pct - max_err,
            "verdict": PASS if max_err <= coupling.tolerance_pct else FAIL,
        }
    )
    return record


# ---------------------------------------------------------------- end-to-end
def _flatten(prefix: str, node: Any, out: Dict[str, float]) -> None:
    if isinstance(node, Mapping):
        for k, v in node.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(node, bool) or not isinstance(node, (int, float)):
        raise SpecError(f"end_to_end: non-numeric leaf at {prefix!r}: {node!r}")
    else:
        out[prefix] = float(node)


def end_to_end_check(spec: GateSpec, registry: EvaluatorRegistry, pack_dir: Path) -> Dict[str, Any]:
    e2e = spec.end_to_end
    baseline_path = Path(e2e.baseline_path)
    if not baseline_path.is_absolute():
        baseline_path = (pack_dir / baseline_path).resolve()
    record: Dict[str, Any] = {
        "criterion": "end_to_end_regression",
        "title": e2e.title or "end-to-end regression",
        "tolerance_pct": e2e.tolerance_pct,
        "baseline_path": str(baseline_path),
        "case_set_key": e2e.case_set_key,
    }
    if not baseline_path.exists():
        record.update({"verdict": WITHHELD, "reason": f"baseline missing: {baseline_path}"})
        return record
    record["baseline_sha256"] = _sha256_file(baseline_path)
    baseline_raw = json.loads(baseline_path.read_text())
    cases = spec.end_to_end_cases[e2e.case_set_key]
    try:
        produced = registry.call(e2e.evaluator, cases)
    except SpecError as exc:
        record.update({"verdict": WITHHELD, "reason": str(exc)})
        return record
    flat_b: Dict[str, float] = {}
    flat_p: Dict[str, float] = {}
    _flatten("", baseline_raw, flat_b)
    _flatten("", produced, flat_p)
    common = sorted(set(flat_b) & set(flat_p))
    only_base = sorted(set(flat_b) - set(flat_p))
    only_prod = sorted(set(flat_p) - set(flat_b))
    if not common or only_base or only_prod:
        record.update(
            {
                "verdict": WITHHELD,
                "reason": "metric set mismatch between pipeline and baseline",
                "missing_metrics": only_base,
                "extra_metrics": only_prod,
            }
        )
        return record

    rows = []
    for key in common:
        err = relative_error(flat_p[key], flat_b[key], 1e-12)
        rows.append({"metric": key, "pipeline": flat_p[key], "baseline": flat_b[key], "error_pct": err})
    worst = max(rows, key=lambda r: r["error_pct"])
    record.update(
        {
            "rows": rows,
            "n_metrics": len(rows),
            "max_error_pct": worst["error_pct"],
            "worst_metric": worst["metric"],
            "mean_error_pct": sum(r["error_pct"] for r in rows) / len(rows),
            "margin_pct": e2e.tolerance_pct - worst["error_pct"],
            "verdict": PASS if worst["error_pct"] <= e2e.tolerance_pct else FAIL,
        }
    )
    return record


# ----------------------------------------------------------------------- gate
def run_gate(spec_path: str | Path, allow_root: str | Path | None = None) -> Dict[str, Any]:
    spec_path = Path(spec_path).resolve()
    spec = load_spec(spec_path)
    pack_dir = spec_path.parent
    root = Path(allow_root).resolve() if allow_root is not None else spec_path.parents[2]
    module_path = Path(spec.evaluator_module)
    if not module_path.is_absolute():
        module_path = (root / module_path).resolve()
    registry = EvaluatorRegistry(module_path, allow_root=root)

    coverage = coverage_report(spec, registry)
    couplings = [coupling_check(spec, c, registry) for c in spec.couplings]
    e2e = end_to_end_check(spec, registry, pack_dir)

    criteria = [coverage] + couplings + [e2e]
    verdicts = {c["criterion"]: c["verdict"] for c in criteria}
    if any(v == FAIL for v in verdicts.values()):
        overall = FAIL
    elif any(v == WITHHELD for v in verdicts.values()):
        overall = WITHHELD
    else:
        overall = PASS

    body = {
        "pack": {
            "name": spec.name,
            "version": spec.version,
            "spec_path": str(spec_path),
            "spec_sha256": spec.sha256,
            "evaluator_module": str(module_path),
            "evaluator_module_sha256": registry.sha256,
            "description": spec.description,
            "provenance": spec.provenance,
        },
        "criteria": criteria,
        "verdicts": verdicts,
        "overall_verdict": overall,
        "summary": {
            "n_couplings": len(couplings),
            "coupling_max_errors_pct": {c["id"]: c.get("max_error_pct") for c in couplings},
            "coupling_tolerances_pct": {c.id: c.tolerance_pct for c in spec.couplings},
            "coverage_pct": coverage["coverage_pct"],
            "end_to_end_max_error_pct": e2e.get("max_error_pct"),
        },
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    body["report_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return body
