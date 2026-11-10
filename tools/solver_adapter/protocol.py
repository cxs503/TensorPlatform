"""SolverProtocol v1: the versioned contract for cross-library field exchange.

Three objects make up the protocol, all serialisable to JSON and all backed by a
JSON Schema that is *generated from the code* (so the schema can never drift
from the implementation — the drift check is a contract test):

``AdapterManifest``
    what an adapter *is*: ``solver_id``, ``version``, mesh description, the
    fields it can publish (name, quantity kind, dimensional exponents), its
    boundary records, the mapping modes it supports, and whether the adapter
    drives a native solver (``native_impl``) or only implements the protocol.

``MappingSpec``
    *how* a field crosses a library boundary: ``mode`` (``conservative`` =
    volume-weighted averaging, ``standard`` = second-order reconstruction),
    the concrete remap operator, tolerances, and whether the first moment is
    also pinned.

``ExchangeRequest`` / ``ExchangeRecord``
    one exchange: who sends, who receives, which field, under which mapping —
    and, on completion, the measured residuals and the audit digests.

Dimensional consistency is part of the contract: every field carries an
exponent vector over the basis ``(M, L, T, Theta)``.  Two fields may only be
exchanged when their vectors are *identical*, which is the machine-checkable
form of "the units agree" and is what stops a per-volume quantity being mapped
onto a per-mass one by accident.

Nothing here knows what a field physically means: solver ids are library names,
fields are ``name + kind + exponents``, and the numeric payload is a plain
array.  Meaning arrives at the call site through configuration/artifacts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as _dc_field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..coupling.mesh import BoxMesh, QuantityKind
from ..coupling.provenance import canonical_digest
from .errors import DimensionalError, MappingError, ProtocolViolation

__all__ = [
    "PROTOCOL_VERSION",
    "SCHEMA_ID",
    "SOLVER_IDS",
    "DIM_BASIS",
    "MappingMode",
    "MeshSpec",
    "FieldSpec",
    "BoundarySpec",
    "AdapterManifest",
    "MappingSpec",
    "ExchangeRequest",
    "ExchangeRecord",
    "solver_protocol_schema",
    "exchange_request_schema",
    "validate_with_schema",
]

PROTOCOL_VERSION = "solver-protocol/v1"
SCHEMA_ID = "https://tensorplatform.local/schemas/solver_protocol_v1.json"
DIM_BASIS: Tuple[str, ...] = ("M", "L", "T", "Theta")

#: The eight solver libraries the platform hosts.  Identities only — the
#: protocol never inspects their internals, so this tuple is a registry of
#: names, not a list of physics assumptions.
SOLVER_IDS: Tuple[str, ...] = ("lbm", "fem", "fvm", "dem", "bem", "sph", "dg", "cad")

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?$")
_QUANTITY_KINDS = tuple(k.value for k in QuantityKind)
_MAPPING_OPERATORS = ("overlap_l2", "overlap_p1", "rbf_conservative", "nearest_scaled")
_BOUNDARY_KINDS = ("dirichlet", "neumann", "periodic")


# --------------------------------------------------------------------- helpers
def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProtocolViolation(message)


def _check_name(value: Any, what: str) -> str:
    _require(isinstance(value, str), f"{what} must be a string, got {type(value).__name__}")
    _require(bool(_NAME_RE.match(value)), f"{what} {value!r} violates pattern {_NAME_RE.pattern}")
    return value


def _check_dim_vector(value: Any) -> Tuple[int, int, int, int]:
    _require(isinstance(value, (tuple, list)) and len(value) == len(DIM_BASIS),
             f"dim_vector must have {len(DIM_BASIS)} entries {DIM_BASIS}, got {value!r}")
    out: List[int] = []
    for item in value:
        _require(isinstance(item, int) and not isinstance(item, bool),
                 f"dim_vector entries must be ints, got {item!r}")
        out.append(int(item))
    _require(all(-8 <= exponent <= 8 for exponent in out),
             f"dim_vector exponents out of range: {tuple(out)}")
    return (out[0], out[1], out[2], out[3])


def _check_floats(value: Any, what: str) -> Tuple[float, ...]:
    _require(isinstance(value, (tuple, list)) and len(value) >= 1,
             f"{what} must be a non-empty sequence")
    out = []
    for item in value:
        _require(isinstance(item, (int, float)) and not isinstance(item, bool),
                 f"{what} entries must be real numbers, got {item!r}")
        out.append(float(item))
    return tuple(out)


# ------------------------------------------------------------------- datatypes
class MappingMode(str, Enum):
    """How a field crosses a boundary.

    ``CONSERVATIVE``
        volume- (or surface-) weighted averaging; the conserved total of the
        declared quantity kind is preserved to round-off.
    ``STANDARD``
        second-order reconstruction; accuracy on smooth fields is higher, the
        conserved total is only preserved up to the projection error.
    """

    CONSERVATIVE = "conservative"
    STANDARD = "standard"


@dataclass(frozen=True)
class MeshSpec:
    """Axis-aligned box mesh description (protocol v1 topology set is ``box``)."""

    dim: int
    n_cells: int
    lo: Tuple[float, ...]
    hi: Tuple[float, ...]
    topology: str = "box"

    def __post_init__(self) -> None:
        _require(isinstance(self.dim, int) and 1 <= self.dim <= 3,
                 f"mesh dim must be 1..3, got {self.dim!r}")
        _require(isinstance(self.n_cells, int) and self.n_cells >= 1,
                 f"n_cells must be a positive int, got {self.n_cells!r}")
        _require(self.topology == "box", f"unsupported topology {self.topology!r}")
        _require(len(self.lo) == self.dim and len(self.hi) == self.dim,
                 "lo/hi must have dim entries")
        for axis in range(self.dim):
            _require(self.hi[axis] > self.lo[axis],
                     f"mesh extent on axis {axis} is not positive: "
                     f"[{self.lo[axis]}, {self.hi[axis]}]")

    @property
    def extent(self) -> Tuple[float, ...]:
        return tuple(self.hi[a] - self.lo[a] for a in range(self.dim))

    @property
    def bounding_measure(self) -> float:
        measure = 1.0
        for axis in range(self.dim):
            measure *= self.hi[axis] - self.lo[axis]
        return measure

    @classmethod
    def from_box_mesh(cls, mesh: BoxMesh) -> "MeshSpec":
        return cls(
            dim=mesh.dim,
            n_cells=mesh.n_cells,
            lo=tuple(float(v) for v in mesh.lo.min(axis=0)),
            hi=tuple(float(v) for v in mesh.hi.max(axis=0)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dim": self.dim,
            "n_cells": self.n_cells,
            "lo": list(self.lo),
            "hi": list(self.hi),
            "topology": self.topology,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MeshSpec":
        _require(isinstance(payload, Mapping), "mesh spec must be an object")
        return cls(
            dim=int(payload["dim"]),
            n_cells=int(payload["n_cells"]),
            lo=_check_floats(payload["lo"], "mesh.lo"),
            hi=_check_floats(payload["hi"], "mesh.hi"),
            topology=str(payload.get("topology", "box")),
        )


@dataclass(frozen=True)
class FieldSpec:
    """One addressable field: a name, a quantity kind and dimensional exponents."""

    name: str
    kind: str
    dim_vector: Tuple[int, int, int, int]
    description: str = ""
    face_normal: int = -1

    def __post_init__(self) -> None:
        _check_name(self.name, "field name")
        _require(self.kind in _QUANTITY_KINDS,
                 f"field kind {self.kind!r} not in {_QUANTITY_KINDS}")
        object.__setattr__(self, "dim_vector", _check_dim_vector(self.dim_vector))
        if self.kind == QuantityKind.FLUX.value:
            _require(isinstance(self.face_normal, int) and self.face_normal >= 0,
                     "a flux field must declare face_normal >= 0")
        else:
            _require(self.face_normal == -1,
                     "only flux fields may declare face_normal")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FieldSpec":
        _require(isinstance(payload, Mapping), "field spec must be an object")
        return cls(
            name=str(payload["name"]),
            kind=str(payload["kind"]),
            dim_vector=_check_dim_vector(payload["dim_vector"]),
            description=str(payload.get("description", "")),
            face_normal=int(payload.get("face_normal", -1)),
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "dim_vector": list(self.dim_vector),
            "description": self.description,
        }
        if self.face_normal >= 0:
            out["face_normal"] = self.face_normal
        return out

    def assert_compatible(self, other: "FieldSpec") -> None:
        """Same kind + same exponents is the machine-checkable 'units agree'."""
        if self.kind != other.kind:
            raise DimensionalError(
                f"field {self.name!r} kind {self.kind!r} cannot be exchanged with "
                f"{other.name!r} kind {other.kind!r}"
            )
        if self.dim_vector != other.dim_vector:
            raise DimensionalError(
                f"dimensional mismatch on {self.name!r}: {self.dim_vector} vs "
                f"{other.dim_vector} over basis {DIM_BASIS}"
            )


@dataclass(frozen=True)
class BoundarySpec:
    """A declared boundary record (name, axis, side, kind, value)."""

    name: str
    axis: int
    side: str
    kind: str
    value: float = 0.0

    def __post_init__(self) -> None:
        _check_name(self.name, "boundary name")
        _require(isinstance(self.axis, int) and 0 <= self.axis <= 2,
                 f"boundary axis must be 0..2, got {self.axis!r}")
        _require(self.side in ("lo", "hi"), f"boundary side must be lo|hi, got {self.side!r}")
        _require(self.kind in _BOUNDARY_KINDS,
                 f"boundary kind {self.kind!r} not in {_BOUNDARY_KINDS}")
        _require(isinstance(self.value, (int, float)) and not isinstance(self.value, bool),
                 "boundary value must be a real number")

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "axis": self.axis, "side": self.side,
                "kind": self.kind, "value": float(self.value)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BoundarySpec":
        return cls(
            name=str(payload["name"]),
            axis=int(payload["axis"]),
            side=str(payload["side"]),
            kind=str(payload["kind"]),
            value=float(payload.get("value", 0.0)),
        )


@dataclass(frozen=True)
class MappingSpec:
    """How one field exchange is performed."""

    mode: MappingMode = MappingMode.CONSERVATIVE
    operator: str = "overlap_l2"
    sum_tol: float = 1e-3
    moment_tol: float = 1e-2
    moment_exact: bool = False

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "mode", MappingMode(self.mode))
        except ValueError:
            raise MappingError(
                f"unknown mapping mode {self.mode!r}; expected one of "
                f"{[m.value for m in MappingMode]}"
            ) from None
        _require(self.operator in _MAPPING_OPERATORS,
                 f"unknown mapping operator {self.operator!r}; expected {_MAPPING_OPERATORS}")
        for label, tol in (("sum_tol", self.sum_tol), ("moment_tol", self.moment_tol)):
            _require(isinstance(tol, (int, float)) and not isinstance(tol, bool),
                     f"{label} must be a real number")
            _require(0.0 < float(tol) < 1.0, f"{label} must lie in (0, 1), got {tol!r}")
        if self.mode is MappingMode.CONSERVATIVE:
            _require(self.operator in ("overlap_l2", "rbf_conservative"),
                     "conservative mode requires a conserving operator "
                     "(overlap_l2 | rbf_conservative), "
                     f"got {self.operator!r}")
        if self.moment_exact:
            _require(self.operator in ("overlap_l2", "rbf_conservative"),
                     "moment_exact is only meaningful for a conserving operator")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "operator": self.operator,
            "sum_tol": float(self.sum_tol),
            "moment_tol": float(self.moment_tol),
            "moment_exact": bool(self.moment_exact),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MappingSpec":
        _require(isinstance(payload, Mapping), "mapping must be an object")
        return cls(
            mode=MappingMode(str(payload.get("mode", "conservative"))),
            operator=str(payload.get("operator", "overlap_l2")),
            sum_tol=float(payload.get("sum_tol", 1e-3)),
            moment_tol=float(payload.get("moment_tol", 1e-2)),
            moment_exact=bool(payload.get("moment_exact", False)),
        )


@dataclass(frozen=True)
class AdapterManifest:
    """The self-description an adapter publishes (schema ``AdapterManifest``)."""

    solver_id: str
    version: str
    mesh: MeshSpec
    fields: Tuple[FieldSpec, ...]
    boundaries: Tuple[BoundarySpec, ...] = ()
    mapping_modes: Tuple[MappingMode, ...] = (MappingMode.CONSERVATIVE, MappingMode.STANDARD)
    native_impl: bool = False
    deterministic: bool = True
    protocol_version: str = PROTOCOL_VERSION
    repo_commit: str = ""

    def __post_init__(self) -> None:
        _check_name(self.solver_id, "solver_id")
        _require(bool(_VERSION_RE.match(self.version)),
                 f"version {self.version!r} is not a semantic version")
        _require(self.protocol_version == PROTOCOL_VERSION,
                 f"manifest protocol_version {self.protocol_version!r} != {PROTOCOL_VERSION!r}")
        _require(isinstance(self.fields, tuple) and len(self.fields) >= 1,
                 "a manifest must declare at least one field")
        names = [f.name for f in self.fields]
        _require(len(set(names)) == len(names), f"duplicate field names in manifest: {names}")
        for spec in self.fields:
            _require(isinstance(spec, FieldSpec), "manifest fields must be FieldSpec objects")
            if spec.kind == QuantityKind.FLUX.value:
                _require(self.mesh.dim >= 2,
                         "a flux field on a 1-D mesh is ill-posed (surface measure)")
                _require(spec.face_normal < self.mesh.dim,
                         f"face_normal {spec.face_normal} out of range for dim {self.mesh.dim}")
        modes = tuple(MappingMode(m) for m in self.mapping_modes)
        _require(len(modes) >= 1, "manifest must support at least one mapping mode")
        object.__setattr__(self, "mapping_modes", modes)

    # -- lookups ---------------------------------------------------------
    def field(self, name: str) -> FieldSpec:
        for spec in self.fields:
            if spec.name == name:
                return spec
        raise ProtocolViolation(
            f"solver {self.solver_id!r} declares no field {name!r} "
            f"(declared: {[f.name for f in self.fields]})"
        )

    def field_names(self) -> Tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    # -- serialisation ---------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "solver": {
                "solver_id": self.solver_id,
                "version": self.version,
                "repo_commit": self.repo_commit,
                "native_impl": bool(self.native_impl),
            },
            "mesh": self.mesh.to_dict(),
            "fields": [f.to_dict() for f in self.fields],
            "boundaries": [b.to_dict() for b in self.boundaries],
            "mapping_modes": [m.value for m in self.mapping_modes],
            "deterministic": bool(self.deterministic),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AdapterManifest":
        validate_with_schema(payload, solver_protocol_schema(), what="adapter manifest")
        solver = payload["solver"]
        return cls(
            solver_id=str(solver["solver_id"]),
            version=str(solver["version"]),
            mesh=MeshSpec.from_dict(payload["mesh"]),
            fields=tuple(FieldSpec.from_dict(f) for f in payload["fields"]),
            boundaries=tuple(BoundarySpec.from_dict(b) for b in payload.get("boundaries", [])),
            mapping_modes=tuple(MappingMode(str(m)) for m in payload["mapping_modes"]),
            native_impl=bool(solver.get("native_impl", False)),
            deterministic=bool(payload.get("deterministic", True)),
            protocol_version=str(payload["protocol_version"]),
            repo_commit=str(solver.get("repo_commit", "")),
        )

    def digest(self) -> str:
        return canonical_digest(self.to_dict())


@dataclass(frozen=True)
class ExchangeRequest:
    """One cross-library exchange, in the exact field set the protocol fixes."""

    solver_id: str
    version: str
    target_solver: str
    field_name: str
    mapping: MappingSpec = _dc_field(default_factory=MappingSpec)
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        for label, value in (("solver_id", self.solver_id),
                             ("target_solver", self.target_solver)):
            _check_name(value, label)
        _require(bool(_VERSION_RE.match(self.version)),
                 f"version {self.version!r} is not a semantic version")
        _check_name(self.field_name, "field_name")
        _require(self.solver_id != self.target_solver,
                 "an exchange must cross a library boundary (source != target)")
        _require(self.protocol_version == PROTOCOL_VERSION,
                 f"exchange protocol_version {self.protocol_version!r} != {PROTOCOL_VERSION!r}")
        _require(isinstance(self.mapping, MappingSpec), "mapping must be a MappingSpec")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "solver_id": self.solver_id,
            "version": self.version,
            "target_solver": self.target_solver,
            "field_name": self.field_name,
            "mapping": self.mapping.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExchangeRequest":
        validate_with_schema(payload, exchange_request_schema(), what="exchange request")
        return cls(
            solver_id=str(payload["solver_id"]),
            version=str(payload["version"]),
            target_solver=str(payload["target_solver"]),
            field_name=str(payload["field_name"]),
            mapping=MappingSpec.from_dict(payload["mapping"]),
            protocol_version=str(payload["protocol_version"]),
        )

    def digest(self) -> str:
        return canonical_digest(self.to_dict())


@dataclass(frozen=True)
class ExchangeRecord:
    """Audit record of one completed exchange (measured, not asserted)."""

    request: ExchangeRequest
    sum_src: float
    sum_dst: float
    sum_rel_residual: float
    moment_rel_residual: float
    operator: str
    src_digest: str
    dst_digest: str
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "sum_src": self.sum_src,
            "sum_dst": self.sum_dst,
            "sum_rel_residual": self.sum_rel_residual,
            "moment_rel_residual": self.moment_rel_residual,
            "operator": self.operator,
            "src_digest": self.src_digest,
            "dst_digest": self.dst_digest,
            "passed": bool(self.passed),
        }

    def digest(self) -> str:
        return canonical_digest(self.to_dict())


# ----------------------------------------------------------------- JSON Schema
def _dim_vector_schema() -> Dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "integer", "minimum": -8, "maximum": 8},
        "minItems": len(DIM_BASIS),
        "maxItems": len(DIM_BASIS),
    }


def _mapping_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["mode", "operator", "sum_tol", "moment_tol"],
        "properties": {
            "mode": {"enum": [m.value for m in MappingMode]},
            "operator": {"enum": list(_MAPPING_OPERATORS)},
            "sum_tol": {"type": "number", "exclusiveMinimum": 0.0, "exclusiveMaximum": 1.0},
            "moment_tol": {"type": "number", "exclusiveMinimum": 0.0, "exclusiveMaximum": 1.0},
            "moment_exact": {"type": "boolean"},
        },
    }


def solver_protocol_schema() -> Dict[str, Any]:
    """JSON Schema (draft 2020-12) for ``AdapterManifest`` — generated, not copied."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "SolverProtocol v1 AdapterManifest",
        "type": "object",
        "additionalProperties": False,
        "required": ["protocol_version", "solver", "mesh", "fields", "mapping_modes"],
        "properties": {
            "protocol_version": {"const": PROTOCOL_VERSION},
            "solver": {
                "type": "object",
                "additionalProperties": False,
                "required": ["solver_id", "version", "native_impl"],
                "properties": {
                    "solver_id": {"type": "string", "pattern": _NAME_RE.pattern},
                    "version": {"type": "string", "pattern": _VERSION_RE.pattern},
                    "repo_commit": {"type": "string"},
                    "native_impl": {"type": "boolean"},
                },
            },
            "mesh": {
                "type": "object",
                "additionalProperties": False,
                "required": ["dim", "n_cells", "lo", "hi"],
                "properties": {
                    "dim": {"type": "integer", "minimum": 1, "maximum": 3},
                    "n_cells": {"type": "integer", "minimum": 1},
                    "lo": {"type": "array", "items": {"type": "number"}, "minItems": 1, "maxItems": 3},
                    "hi": {"type": "array", "items": {"type": "number"}, "minItems": 1, "maxItems": 3},
                    "topology": {"enum": ["box"]},
                },
            },
            "fields": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "kind", "dim_vector"],
                    "properties": {
                        "name": {"type": "string", "pattern": _NAME_RE.pattern},
                        "kind": {"enum": list(_QUANTITY_KINDS)},
                        "dim_vector": _dim_vector_schema(),
                        "description": {"type": "string"},
                        "face_normal": {"type": "integer", "minimum": 0},
                    },
                    # a flux record without a face normal is ill-posed: the schema
                    # says so too, instead of leaving it to the dataclass only
                    "allOf": [
                        {
                            "if": {"properties": {"kind": {"const": "flux"}},
                                   "required": ["kind"]},
                            "then": {"required": ["face_normal"]},
                        }
                    ],
                },
            },
            "boundaries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "axis", "side", "kind"],
                    "properties": {
                        "name": {"type": "string", "pattern": _NAME_RE.pattern},
                        "axis": {"type": "integer", "minimum": 0, "maximum": 2},
                        "side": {"enum": ["lo", "hi"]},
                        "kind": {"enum": list(_BOUNDARY_KINDS)},
                        "value": {"type": "number"},
                    },
                },
            },
            "mapping_modes": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"enum": [m.value for m in MappingMode]},
            },
            "deterministic": {"type": "boolean"},
        },
    }


def exchange_request_schema() -> Dict[str, Any]:
    """JSON Schema for ``ExchangeRequest`` (solver_id/version/target/field/mapping)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID + "#/$defs/exchange_request",
        "title": "SolverProtocol v1 ExchangeRequest",
        "type": "object",
        "additionalProperties": False,
        "required": ["protocol_version", "solver_id", "version", "target_solver",
                     "field_name", "mapping"],
        "properties": {
            "protocol_version": {"const": PROTOCOL_VERSION},
            "solver_id": {"type": "string", "pattern": _NAME_RE.pattern},
            "version": {"type": "string", "pattern": _VERSION_RE.pattern},
            "target_solver": {"type": "string", "pattern": _NAME_RE.pattern},
            "field_name": {"type": "string", "pattern": _NAME_RE.pattern},
            "mapping": _mapping_schema(),
        },
    }


def validate_with_schema(payload: Any, schema: Mapping[str, Any], what: str = "payload") -> None:
    """Validate against a JSON Schema, fail-closed.

    ``jsonschema`` is used when importable; the structural checks in the
    dataclasses remain the primary gate, so the protocol does not depend on an
    optional third-party package being installed.
    """
    try:
        import jsonschema  # noqa: PLC0415  (optional dependency, imported lazily)
    except ImportError:  # pragma: no cover - exercised only without jsonschema
        if not isinstance(payload, Mapping):
            raise ProtocolViolation(f"{what} must be a JSON object, got {type(payload).__name__}")
        return
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        location = "/".join(str(p) for p in first.absolute_path) or "<root>"
        raise ProtocolViolation(
            f"{what} violates schema at {location}: {first.message} "
            f"({len(errors)} error(s) total)"
        )


def schema_json(schema: Mapping[str, Any]) -> str:
    """Canonical JSON rendering used by the on-disk schema artifact."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"