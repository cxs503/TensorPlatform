"""``SolverAdapter`` — the protocol v1 abstract base class.

Shape of the contract (machine-checked by ``tests/test_solver_adapter/test_base_contract.py``):

* **4 abstract methods** a library adapter must implement —
  :meth:`SolverAdapter.init`, :meth:`SolverAdapter.step`,
  :meth:`SolverAdapter.get_output`, :meth:`SolverAdapter.cleanup`;
* **8 concrete methods** the base class provides once and for all —
  :meth:`~SolverAdapter.manifest`, :meth:`~SolverAdapter.check_compatibility`,
  :meth:`~SolverAdapter.export_field`, :meth:`~SolverAdapter.import_field`,
  :meth:`~SolverAdapter.map_to`, :meth:`~SolverAdapter.conservation_report`,
  :meth:`~SolverAdapter.checkpoint`, :meth:`~SolverAdapter.restore`.

Lifecycle enforcement is *not* left to the subclass: :meth:`__init_subclass__`
wraps the four abstract methods in guards derived from
:mod:`tools.solver_adapter.lifecycle`.  A subclass cannot forget to check its
own state, and a caller cannot step an uninitialised solver.

Declaration surface for a concrete adapter (class attributes, all validated
when :meth:`manifest` is first built)::

    SOLVER_ID    : str                      e.g. "lbm"
    VERSION      : str                      semantic version of the adapter
    FIELDS       : tuple[FieldSpec, ...]    fields this adapter can publish
    BOUNDARIES   : tuple[BoundarySpec, ...] declared boundary records
    MAPPING_MODES: tuple[MappingMode, ...]  modes it can honour
    NATIVE_IMPL  : bool                     True only when a real library runs
    DIM          : int                      mesh dimension (1..3)

Field payloads live in ``self._fields`` (``dict[str, np.ndarray]`` of length
``n_cells``); ``checkpoint``/``restore`` operate on that dict, so every adapter
gets state serialisation for free.
"""

from __future__ import annotations

import abc
import functools
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, TypeVar, cast

import numpy as np

from ..coupling.mesh import BoxMesh, Field
from ..coupling.provenance import canonical_digest
from .errors import (
    AdapterError,
    CompatibilityError,
    FieldLookupError,
    LifecycleError,
    MappingError,
    StateMismatchError,
)
from .lifecycle import LifecycleTrace, State, transition
from .mapping import MappingResult, map_field, operator_for_mode
from .protocol import (
    PROTOCOL_VERSION,
    AdapterManifest,
    BoundarySpec,
    ExchangeRecord,
    ExchangeRequest,
    FieldSpec,
    MappingMode,
    MappingSpec,
    MeshSpec,
)

__all__ = ["StepReport", "CompatibilityReport", "SolverAdapter", "CHECKPOINT_SCHEMA"]

CHECKPOINT_SCHEMA = "solver-adapter-checkpoint/v1"

_F = TypeVar("_F", bound=Callable[..., Any])


@dataclass(frozen=True)
class StepReport:
    """What one ``step`` must report back (measured, not asserted)."""

    step_index: int
    time: float
    dt: float
    max_abs: float
    finite: bool
    state_digest: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "time": self.time,
            "dt": self.dt,
            "max_abs": self.max_abs,
            "finite": self.finite,
            "state_digest": self.state_digest,
        }


@dataclass(frozen=True)
class CompatibilityReport:
    """Answer of ``check_compatibility``; content problems are reported, not raised."""

    compatible: bool
    source: str
    target: str
    field_name: str
    reasons: Tuple[str, ...] = ()
    shared_modes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "compatible": self.compatible,
            "source": self.source,
            "target": self.target,
            "field_name": self.field_name,
            "reasons": list(self.reasons),
            "shared_modes": list(self.shared_modes),
        }


def _guarded(action: str, fn: _F) -> _F:
    """Wrap an abstract hook with the lifecycle guard for ``action``."""

    @functools.wraps(fn)
    def wrapper(self: "SolverAdapter", *args: Any, **kwargs: Any) -> Any:
        target = transition(self._state, action)      # raises LifecycleError
        self._trace.append(action, self._state, target, detail=type(self).SOLVER_ID)
        try:
            result = fn(self, *args, **kwargs)
        except AdapterError:
            raise
        except (ValueError, TypeError) as exc:        # normalise into the taxonomy
            raise AdapterError(f"{action} failed: {exc}") from exc
        if target is not self._state:
            self._state = target
        return result

    return cast(_F, wrapper)


class SolverAdapter(abc.ABC):
    """Protocol v1 adapter: 4 abstract hooks + 8 concrete operations."""

    SOLVER_ID: str = ""
    VERSION: str = "1.0.0"
    FIELDS: Tuple[FieldSpec, ...] = ()
    BOUNDARIES: Tuple[BoundarySpec, ...] = ()
    MAPPING_MODES: Tuple[MappingMode, ...] = (
        MappingMode.CONSERVATIVE,
        MappingMode.STANDARD,
    )
    NATIVE_IMPL: bool = False
    DIM: int = 3

    # ---- lifecycle guard installation ---------------------------------
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for action in ("init", "step", "get_output", "cleanup"):
            hook = cls.__dict__.get(action)
            if hook is None:
                continue
            raw = cast(Callable[..., Any], hook)
            if not getattr(raw, "__isabstractmethod__", False):
                setattr(cls, action, _guarded(action, raw))

    def __init__(self, config: Optional[Mapping[str, Any]] = None) -> None:
        if not type(self).SOLVER_ID:
            raise AdapterError(f"{type(self).__name__} must declare SOLVER_ID")
        if not type(self).FIELDS:
            raise AdapterError(f"{type(self).__name__} must declare FIELDS")
        self.config: Dict[str, Any] = dict(config or {})
        self._state: State = State.NEW
        self._trace = LifecycleTrace()
        self._fields: Dict[str, np.ndarray] = {}
        self._records: List[ExchangeRecord] = []
        self._step_index: int = 0
        self._time: float = 0.0
        self._mesh: BoxMesh = self._build_mesh(self.config)

    # ------------------------------------------------------------------
    # 4 abstract methods
    # ------------------------------------------------------------------
    @abc.abstractmethod
    def init(self, config: Optional[Mapping[str, Any]] = None) -> None:
        """Allocate state and leave the solver in ``READY``."""

    @abc.abstractmethod
    def step(self, dt: float) -> StepReport:
        """Advance the solver by ``dt``; legal in ``READY`` only."""

    @abc.abstractmethod
    def get_output(self, field_name: str) -> Field:
        """Return the current value of a declared field; legal in ``READY`` only."""

    @abc.abstractmethod
    def cleanup(self) -> None:
        """Release state and leave the solver in ``FINALIZED``."""

    # ------------------------------------------------------------------
    # 8 concrete methods
    # ------------------------------------------------------------------
    def manifest(self) -> AdapterManifest:
        """1/8 — the protocol-conformant self-description of this adapter."""
        return AdapterManifest(
            solver_id=type(self).SOLVER_ID,
            version=type(self).VERSION,
            mesh=MeshSpec.from_box_mesh(self._mesh),
            fields=tuple(type(self).FIELDS),
            boundaries=tuple(type(self).BOUNDARIES),
            mapping_modes=tuple(type(self).MAPPING_MODES),
            native_impl=bool(type(self).NATIVE_IMPL),
        )

    def check_compatibility(self, target: "SolverAdapter", field_name: str) -> CompatibilityReport:
        """2/8 — can ``field_name`` be sent from this adapter to ``target``?"""
        reasons: List[str] = []
        if not isinstance(target, SolverAdapter):
            raise CompatibilityError(
                f"target must be a SolverAdapter, got {type(target).__name__}"
            )
        src_manifest, dst_manifest = self.manifest(), target.manifest()
        src_spec: Optional[FieldSpec] = None
        dst_spec: Optional[FieldSpec] = None
        try:
            src_spec = src_manifest.field(field_name)
        except AdapterError:
            reasons.append(f"source {src_manifest.solver_id!r} declares no field {field_name!r}")
        try:
            dst_spec = dst_manifest.field(field_name)
        except AdapterError:
            reasons.append(f"target {dst_manifest.solver_id!r} declares no field {field_name!r}")
        if src_spec is not None and dst_spec is not None:
            if src_spec.kind != dst_spec.kind:
                reasons.append(f"kind mismatch: {src_spec.kind!r} vs {dst_spec.kind!r}")
            if src_spec.dim_vector != dst_spec.dim_vector:
                reasons.append(
                    f"dimensional mismatch: {src_spec.dim_vector} vs {dst_spec.dim_vector}"
                )
        if src_manifest.mesh.dim != dst_manifest.mesh.dim:
            reasons.append(
                f"mesh dimension mismatch: {src_manifest.mesh.dim} vs {dst_manifest.mesh.dim}"
            )
        if src_manifest.solver_id == dst_manifest.solver_id:
            reasons.append("source and target are the same library (not a boundary)")
        shared = tuple(
            m.value
            for m in src_manifest.mapping_modes
            if m in dst_manifest.mapping_modes
        )
        if not shared:
            reasons.append("no shared mapping mode")
        return CompatibilityReport(
            compatible=not reasons,
            source=src_manifest.solver_id,
            target=dst_manifest.solver_id,
            field_name=field_name,
            reasons=tuple(reasons),
            shared_modes=shared,
        )

    def export_field(self, field_name: str) -> Field:
        """3/8 — validated export of a declared field (checks shape, finiteness, kind)."""
        self._require_ready("export_field")
        return self._validated_field(field_name)

    def import_field(self, field_name: str, field: Field) -> Field:
        """4/8 — validated adoption of a foreign field onto this adapter's own mesh."""
        self._require_ready("import_field")
        spec = self._spec(field_name)
        if not isinstance(field, Field):
            raise FieldLookupError(f"import_field expects a Field, got {type(field).__name__}")
        if field.kind.value != spec.kind:
            raise AdapterError(
                f"field {field_name!r} is declared {spec.kind!r} but the payload is "
                f"{field.kind.value!r}"
            )
        if field.mesh.n_cells != self._mesh.n_cells or field.mesh.dim != self._mesh.dim:
            raise AdapterError(
                f"payload mesh ({field.mesh.dim}-D, {field.mesh.n_cells} cells) does not "
                f"match adapter mesh ({self._mesh.dim}-D, {self._mesh.n_cells} cells)"
            )
        values = np.asarray(field.values, dtype=float)
        if values.shape != (self._mesh.n_cells,):
            raise AdapterError(f"payload shape {values.shape} != ({self._mesh.n_cells},)")
        if not np.all(np.isfinite(values)):
            raise AdapterError("payload contains non-finite values")
        self._fields[field_name] = values.copy()
        return self._validated_field(field_name)

    def map_to(
        self,
        target: "SolverAdapter",
        field_name: str,
        *,
        mode: MappingMode = MappingMode.CONSERVATIVE,
        operator: Optional[str] = None,
        sum_tol: float = 1e-3,
        moment_tol: float = 1e-2,
        moment_exact: bool = False,
        apply_conservation_correction: bool = True,
    ) -> MappingResult:
        """5/8 — exchange ``field_name`` to ``target`` (mode switch + landed payload)."""
        self._require_ready("map_to")
        target._require_ready("map_to")
        report = self.check_compatibility(target, field_name)
        if not report.compatible:
            raise CompatibilityError(
                f"{self.SOLVER_ID} -> {target.SOLVER_ID} on {field_name!r} is not admissible: "
                + "; ".join(report.reasons)
            )
        mapping = MappingSpec(
            mode=mode,
            operator=operator_for_mode(mode, operator),
            sum_tol=sum_tol,
            moment_tol=moment_tol,
            moment_exact=moment_exact,
        )
        request = ExchangeRequest(
            solver_id=self.SOLVER_ID,
            version=self.VERSION,
            target_solver=target.SOLVER_ID,
            field_name=field_name,
            mapping=mapping,
        )
        result = map_field(
            self.export_field(field_name),
            self._spec(field_name),
            target._spec(field_name),
            target.mesh,
            request,
            apply_conservation_correction=apply_conservation_correction,
        )
        target.import_field(field_name, result.field)
        self._records.append(result.record)
        target._records.append(result.record)
        return result

    def conservation_report(self) -> Dict[str, Any]:
        """6/8 — audit summary of every exchange this adapter took part in."""
        worst = 0.0
        failures: List[Dict[str, Any]] = []
        for record in self._records:
            worst = max(worst, record.sum_rel_residual)
            if not record.passed:
                failures.append(record.to_dict())
        return {
            "solver_id": self.SOLVER_ID,
            "exchanges": len(self._records),
            "worst_sum_rel_residual": worst,
            "tolerance": 1e-3,
            "within_tolerance": not failures,
            "failures": failures,
            "records": [r.to_dict() for r in self._records],
        }

    def checkpoint(self) -> Dict[str, Any]:
        """7/8 — serialise state (fields + clocks + trace) with a self-digest."""
        self._require_actions("checkpoint")
        fields = {
            name: {
                "values": [float(v) for v in values],
                "digest": canonical_digest({"values": [float(v) for v in values]}),
            }
            for name, values in sorted(self._fields.items())
        }
        body: Dict[str, Any] = {
            "schema": CHECKPOINT_SCHEMA,
            "protocol_version": PROTOCOL_VERSION,
            "solver_id": self.SOLVER_ID,
            "version": self.VERSION,
            "manifest_digest": self.manifest().digest(),
            "state": self._state.value,
            "step_index": self._step_index,
            "time": self._time,
            "fields": fields,
            "extra": self._state_payload(),
            # ``state_digest`` covers the solver state only, so two solvers can be
            # proven identical; ``digest`` additionally covers the lifecycle trace,
            # i.e. the provenance of *how* the state was reached.  A restore
            # appends a provenance entry, so digests differ by design while
            # state_digest agrees — that distinction is what makes an equality
            # claim about a checkpoint testable.
            "state_digest": canonical_digest(
                {
                    "solver_id": self.SOLVER_ID,
                    "version": self.VERSION,
                    "protocol_version": PROTOCOL_VERSION,
                    "manifest_digest": self.manifest().digest(),
                    "step_index": self._step_index,
                    "time": self._time,
                    "fields": fields,
                    "extra": self._state_payload(),
                }
            ),
        }
        body["lifecycle"] = self._trace.to_dict()
        body["digest"] = canonical_digest(body)
        return body

    def restore(self, state: Mapping[str, Any]) -> None:
        """8/8 — adopt a checkpoint; legal in ``NEW`` only, digest-verified."""
        if not isinstance(state, Mapping):
            raise StateMismatchError("restore expects a mapping payload")
        transition(self._state, "restore")           # raises LifecycleError if not NEW
        payload = dict(state)
        digest = payload.pop("digest", None)
        if digest is None:
            raise StateMismatchError("checkpoint payload carries no digest")
        if canonical_digest(payload) != digest:
            raise StateMismatchError("checkpoint digest does not match its content")
        if payload.get("schema") != CHECKPOINT_SCHEMA:
            raise StateMismatchError(f"unknown checkpoint schema {payload.get('schema')!r}")
        if payload.get("solver_id") != self.SOLVER_ID:
            raise StateMismatchError(
                f"checkpoint belongs to {payload.get('solver_id')!r}, not {self.SOLVER_ID!r}"
            )
        if payload.get("manifest_digest") != self.manifest().digest():
            raise StateMismatchError("checkpoint manifest digest differs from this adapter")
        raw_fields = payload.get("fields")
        if not isinstance(raw_fields, Mapping):
            raise StateMismatchError("checkpoint has no 'fields' mapping")
        restored: Dict[str, np.ndarray] = {}
        for name, entry in raw_fields.items():
            if not isinstance(entry, Mapping) or "values" not in entry:
                raise StateMismatchError(f"malformed checkpoint field {name!r}")
            values = np.asarray(entry["values"], dtype=float)
            if values.shape != (self._mesh.n_cells,):
                raise StateMismatchError(
                    f"checkpoint field {name!r} has {values.size} values, "
                    f"expected {self._mesh.n_cells}"
                )
            if canonical_digest({"values": [float(v) for v in values]}) != entry.get("digest"):
                raise StateMismatchError(f"checkpoint field {name!r} digest mismatch")
            restored[str(name)] = values
        self._fields = restored
        self._step_index = int(payload.get("step_index", 0))
        self._time = float(payload.get("time", 0.0))
        lifecycle = payload.get("lifecycle")
        self._trace = LifecycleTrace.from_dict(lifecycle) if isinstance(lifecycle, Mapping) \
            else LifecycleTrace()
        self._trace.append("restore", State.NEW, State.READY, detail=self.SOLVER_ID)
        self._state = State.READY
        self._load_state_payload(payload.get("extra", {}))

    # ------------------------------------------------------------------
    # shared helpers (not part of the 8-method surface)
    # ------------------------------------------------------------------
    @property
    def mesh(self) -> BoxMesh:
        return self._mesh

    @property
    def state(self) -> State:
        return self._state

    @property
    def lifecycle_trace(self) -> LifecycleTrace:
        return self._trace

    @property
    def step_index(self) -> int:
        return self._step_index

    @property
    def time(self) -> float:
        return self._time

    def _build_mesh(self, config: Mapping[str, Any]) -> BoxMesh:
        dim = int(config.get("dim", type(self).DIM))
        cells = int(config.get("n_cells", 4))
        lo = tuple(float(v) for v in config.get("lo", (0.0,) * dim))
        hi = tuple(float(v) for v in config.get("hi", (1.0,) * dim))
        if len(lo) != dim or len(hi) != dim:
            raise AdapterError("config lo/hi must have DIM entries")
        return BoxMesh.uniform(lo, hi, (cells,) * dim, name=f"{type(self).SOLVER_ID}-mesh")

    def _spec(self, field_name: str) -> FieldSpec:
        for spec in type(self).FIELDS:
            if spec.name == field_name:
                return spec
        raise FieldLookupError(
            f"{self.SOLVER_ID!r} declares no field {field_name!r} "
            f"(declared: {[f.name for f in type(self).FIELDS]})"
        )

    def _validated_field(self, field_name: str) -> Field:
        spec = self._spec(field_name)
        if field_name not in self._fields:
            raise FieldLookupError(f"field {field_name!r} has no payload yet")
        values = np.asarray(self._fields[field_name], dtype=float)
        if values.shape != (self._mesh.n_cells,):
            raise AdapterError(
                f"field {field_name!r} has shape {values.shape}, expected "
                f"({self._mesh.n_cells},)"
            )
        if not np.all(np.isfinite(values)):
            raise AdapterError(f"field {field_name!r} contains non-finite values")
        from ..coupling.mesh import QuantityKind  # local: avoid a cycle at import time

        return Field(
            values=values,
            mesh=self._mesh,
            kind=QuantityKind(spec.kind),
            name=spec.name,
            face_normal=spec.face_normal,
        )

    def _require_ready(self, action: str) -> None:
        if self._state is not State.READY:
            raise LifecycleError(
                f"{action} requires state 'ready', adapter {self.SOLVER_ID!r} is "
                f"'{self._state.value}'"
            )

    def _require_actions(self, action: str) -> None:
        transition(self._state, action)
        self._trace.append(action, self._state, transition(self._state, action),
                           detail=self.SOLVER_ID)

    # overridable state hooks (default: nothing beyond fields/clocks)
    def _state_payload(self) -> Dict[str, Any]:
        return {}

    def _load_state_payload(self, payload: Any) -> None:
        if payload not in (None, {}):
            raise StateMismatchError(f"adapter {self.SOLVER_ID!r} has no extra state to restore")


def assert_not_abstract(adapter_cls: type) -> None:  # pragma: no cover - helper for docs
    """Raise if ``adapter_cls`` still has abstract members (used by the guide)."""
    abstract = set(getattr(adapter_cls, "__abstractmethods__", ()))
    if abstract:
        raise MappingError(
            f"{adapter_cls.__name__} implements only part of the protocol: {sorted(abstract)}"
        )