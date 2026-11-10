"""Conservative vs standard field mapping between two adapters (protocol v1).

The numeric work is *not* re-implemented here: it is delegated to
:mod:`tools.coupling.remap` (volume/surface-overlap projection) and the
residuals are measured by :mod:`tools.coupling.conservation`.  What this module
adds is the protocol layer on top of those operators:

* a **mode switch** — ``conservative`` selects volume-weighted averaging
  (``overlap_l2``, exactly conservative on nested meshes),
  ``standard`` selects second-order reconstruction (``overlap_p1``, higher
  accuracy on smooth fields);
* **dimensional and lifecycle enforcement** before a single number is touched;
* an :class:`~tools.solver_adapter.protocol.ExchangeRecord` carrying the
  *measured* residuals and the source/destination digests, so a coupling
  statement can be audited without re-running it.

The residual used for the protocol's ``< 0.1 %`` acceptance is
``sum_rel_residual = |S_dst - S_src| / max(|S_src|, |S_dst|, eps)`` where
``S`` is the conserved total of the declared quantity kind (volume × value for
``density``, surface area × value for ``flux``, plain sum for ``extensive``).

Falsifiability note: a gate that cannot fail is not a gate.  The low-level
entry point :func:`map_field` takes ``apply_conservation_correction`` so that a
*deliberately lossy* exchange (``nearest_scaled`` without the correction) can be
run through the same code path; the contract tests use it as a negative control
and require the residual to exceed the tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..coupling.conservation import ConservationMetrics, measure
from ..coupling.mesh import BoxMesh, Field, QuantityKind
from ..coupling.remap import RemapError, RemapOperator, RemapReport, RemapSpec, remap
from .errors import MappingError
from .protocol import ExchangeRecord, ExchangeRequest, FieldSpec, MappingMode, MappingSpec

__all__ = [
    "CONSERVATIVE_OPERATOR",
    "STANDARD_OPERATOR",
    "MappingResult",
    "operator_for_mode",
    "map_field",
]

CONSERVATIVE_OPERATOR = RemapOperator.OVERLAP_L2.value
STANDARD_OPERATOR = RemapOperator.OVERLAP_P1.value


def operator_for_mode(mode: MappingMode, explicit: Optional[str] = None) -> str:
    """Resolve the remap operator a mapping mode stands for.

    ``explicit`` wins when given, but only if it does not silently downgrade a
    conservative request: ``MappingSpec`` already refuses non-conserving
    operators in conservative mode, and this helper re-checks at execution time
    so a hand-built spec cannot slip through.
    """
    default = CONSERVATIVE_OPERATOR if mode is MappingMode.CONSERVATIVE else STANDARD_OPERATOR
    operator = explicit or default
    if mode is MappingMode.CONSERVATIVE and operator not in (
        RemapOperator.OVERLAP_L2.value,
        RemapOperator.RBF_CONSERVATIVE.value,
    ):
        raise MappingError(
            f"operator {operator!r} cannot honour conservative mode "
            "(use overlap_l2 or rbf_conservative)"
        )
    return operator


@dataclass
class MappingResult:
    """Destination field plus the audit record of how it was obtained."""

    field: Field
    record: ExchangeRecord
    metrics: ConservationMetrics
    report: RemapReport

    @property
    def sum_rel_residual(self) -> float:
        return self.metrics.sum_rel_residual

    @property
    def moment_rel_residual(self) -> float:
        return self.metrics.moment_rel_residual

    @property
    def passed(self) -> bool:
        return self.record.passed

    def to_dict(self) -> dict:
        return {
            "record": self.record.to_dict(),
            "metrics": self.metrics.to_dict(),
            "operator": self.report.operator,
            "passed": self.passed,
        }


def _build_remap_spec(
    mapping: MappingSpec,
    operator: str,
    quantity_kind: str,
    face_normal: int,
    apply_conservation_correction: bool,
) -> RemapSpec:
    return RemapSpec(
        operator=RemapOperator(operator),
        quantity_kind=QuantityKind(quantity_kind),
        apply_conservation_correction=apply_conservation_correction,
        moment_exact=bool(mapping.moment_exact),
        conservation_tol=max(float(mapping.sum_tol), 1e-12),
        face_normal=int(face_normal) if face_normal >= 0 else -1,
    )


def map_field(
    src_field: Field,
    src_spec: FieldSpec,
    dst_spec: FieldSpec,
    dst_mesh: BoxMesh,
    request: ExchangeRequest,
    *,
    apply_conservation_correction: bool = True,
) -> MappingResult:
    """Map ``src_field`` onto ``dst_mesh`` under ``request.mapping``.

    Parameters
    ----------
    src_field
        Source payload on the source adapter's mesh.
    src_spec, dst_spec
        Declared specs of the two sides; their kind and dimensional exponents
        must agree (:class:`~tools.solver_adapter.errors.DimensionalError`).
    dst_mesh
        Destination mesh (the target adapter's mesh).
    request
        The exchange request being honoured; its ``field_name`` must equal
        ``src_spec.name`` so the audit record cannot name a different field.
    apply_conservation_correction
        ``False`` disables the post-projection total fix.  Only used for the
        documented negative control — production exchanges keep it on.
    """
    if request.field_name != src_spec.name:
        raise MappingError(
            f"exchange request names {request.field_name!r} but source spec is {src_spec.name!r}"
        )
    src_spec.assert_compatible(dst_spec)
    if src_field.kind.value != src_spec.kind:
        raise MappingError(
            f"payload kind {src_field.kind.value!r} disagrees with its declared spec "
            f"{src_spec.kind!r}"
        )
    if src_field.mesh.dim != dst_mesh.dim:
        raise MappingError(
            f"cannot map a {src_field.mesh.dim}-D field onto a {dst_mesh.dim}-D mesh"
        )
    if src_field.values.shape != (src_field.mesh.n_cells,):
        raise MappingError(
            f"source payload has shape {src_field.values.shape}, expected "
            f"({src_field.mesh.n_cells},)"
        )
    if not np.all(np.isfinite(src_field.values)):
        raise MappingError("source payload contains non-finite values")

    operator = operator_for_mode(request.mapping.mode, request.mapping.operator)
    face_normal = int(src_spec.face_normal) if src_spec.kind == "flux" else -1
    spec = _build_remap_spec(
        request.mapping, operator, src_spec.kind, face_normal, apply_conservation_correction
    )
    try:
        result = remap(src_field, src_mesh=src_field.mesh, dst_mesh=dst_mesh,
                       quantity_kind=src_spec.kind, spec=spec, dst_name=dst_spec.name)
    except RemapError as exc:                       # keep the adapter error taxonomy flat
        raise MappingError(f"remap failed: {exc}") from exc

    dst_field = result.field
    metrics = measure(src_field, dst_field)
    passed = (
        metrics.sum_rel_residual <= request.mapping.sum_tol
        and metrics.moment_rel_residual <= request.mapping.moment_tol
    )
    record = ExchangeRecord(
        request=request,
        sum_src=float(metrics.sum_src),
        sum_dst=float(metrics.sum_dst),
        sum_rel_residual=float(metrics.sum_rel_residual),
        moment_rel_residual=float(metrics.moment_rel_residual),
        operator=result.report.operator,
        src_digest=src_field.digest(),
        dst_digest=dst_field.digest(),
        passed=bool(passed),
    )
    return MappingResult(field=dst_field, record=record, metrics=metrics, report=result.report)