"""Cross-library field-exchange conservation gate (domain-neutral).

This module is the ``conservation`` gate *type* of the pipeline: a benchmark
configuration names two fields (``src``, ``dst``) inside the generator payload
and the thresholds for the four conservation metrics; the gate rebuilds
:class:`tools.coupling.mesh.Field` objects and delegates the metric definitions
to :func:`tools.coupling.conservation.check`, so the evidence pipeline and the
coupling toolkit can never drift apart on what "conserved" means.

Two failure modes are deliberately *not* exceptions but gate failures
(fail-closed, machine-readable reason kept in the artifact):

* a declared field is missing from the payload, or cannot be reconstructed
  (non-finite values, shape mismatch) → the exchange cannot be judged, so the
  gate fails with ``field_unavailable``;
* an unknown threshold key → the configuration is rejected at load time already
  (:mod:`tools.evidence_pipeline.config`), not here.

The metrics are the ones the coupling toolkit documents: zeroth-moment
(``sum_rel_residual``), first-moment (``moment_rel_residual``), positivity
(``nonneg_violation``) and overshoot (``overshoot_ratio``).
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Any, Dict, List, Mapping

from tools.coupling.conservation import check as conservation_check
from tools.coupling.mesh import BoxMesh, Field, MeshError, QuantityKind

__all__ = ["GateOutcome", "run_conservation_gate"]


@dataclass
class GateOutcome:
    """Verdict of one gate (the artifact keeps the whole object)."""

    id: str
    type: str
    passed: bool
    failures: List[str] = _dc_field(default_factory=list)
    metrics: Dict[str, Any] = _dc_field(default_factory=dict)
    thresholds: Dict[str, Any] = _dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "passed": self.passed,
            "failures": list(self.failures),
            "metrics": dict(self.metrics),
            "thresholds": dict(self.thresholds),
        }


def _failed(spec, reason: str) -> GateOutcome:
    return GateOutcome(
        id=spec.id,
        type=spec.type,
        passed=False,
        failures=[reason],
        metrics={},
        thresholds=dict(spec.thresholds),
    )


def _build_field(desc: Mapping[str, Any], role: str) -> Field:
    if not isinstance(desc, Mapping):
        raise ValueError(f"field {role!r} is not an object")
    mesh_desc = desc.get("mesh")
    if not isinstance(mesh_desc, Mapping) or "lo" not in mesh_desc or "hi" not in mesh_desc:
        raise ValueError(f"field {role!r}.mesh needs 'lo' and 'hi'")
    mesh = BoxMesh(
        lo=mesh_desc["lo"],
        hi=mesh_desc["hi"],
        name=str(mesh_desc.get("name", f"{role}_mesh")),
    )
    values = desc.get("values")
    if not isinstance(values, list) or not values:
        raise ValueError(f"field {role!r}.values must be a non-empty list")
    kind = QuantityKind(str(desc.get("kind", "density")))
    return Field(values=values, mesh=mesh, kind=kind, name=str(desc.get("name", role)))


def run_conservation_gate(spec, payload: Mapping[str, Any]) -> GateOutcome:
    """Judge one cross-library exchange against the declared thresholds."""
    fields = payload.get("fields") if isinstance(payload, Mapping) else None
    if not isinstance(fields, Mapping) or not fields:
        return _failed(spec, "field_unavailable: payload carries no 'fields' object")

    src_key = spec.fields.get("src", "src")
    dst_key = spec.fields.get("dst", "dst")
    missing = [k for k in (src_key, dst_key) if k not in fields]
    if missing:
        return _failed(spec, f"field_unavailable: missing fields {missing}")

    try:
        src = _build_field(fields[src_key], src_key)
        dst = _build_field(fields[dst_key], dst_key)
    except (ValueError, MeshError, KeyError) as exc:
        return _failed(spec, f"field_unavailable: {type(exc).__name__}: {exc}")

    if src.mesh.dim != dst.mesh.dim:
        return _failed(
            spec, f"field_unavailable: dimension mismatch {src.mesh.dim} vs {dst.mesh.dim}"
        )

    result = conservation_check(
        src,
        dst,
        sum_tol=spec.thresholds["sum_rel_residual"],
        moment_tol=spec.thresholds["moment_rel_residual"],
        nonneg_tol=spec.thresholds["nonneg_violation"],
        # the configuration states the *bound* on the ratio; the toolkit takes a
        # tolerance around 1.0, so the translation happens here, once.
        overshoot_tol=spec.thresholds["overshoot_ratio"] - 1.0,
    )
    return GateOutcome(
        id=spec.id,
        type=spec.type,
        passed=bool(result.passed),
        failures=list(result.failures),
        metrics=result.metrics.to_dict() if result.metrics else {},
        thresholds=dict(result.thresholds),
    )