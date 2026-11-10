"""Protocol schema and manifest validation (C1/C2 surface).

The schema is *generated* from :mod:`tools.solver_adapter.protocol`; one test
re-derives it and compares with the on-disk artifact so the two cannot drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.solver_adapter import (
    PROTOCOL_VERSION,
    SOLVER_IDS,
    AdapterManifest,
    BoundarySpec,
    ExchangeRequest,
    FieldSpec,
    MappingMode,
    MappingSpec,
    MeshSpec,
    ProtocolViolation,
    adapter_class,
    build_adapter,
    exchange_request_schema,
    schema_json,
    solver_protocol_schema,
    validate_with_schema,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas" / "solver_protocol_v1.json"

VALID_FIELD = {"name": "density", "kind": "density", "dim_vector": [1, -3, 0, 0]}
VALID_MANIFEST = {
    "protocol_version": PROTOCOL_VERSION,
    "solver": {"solver_id": "lbm", "version": "1.0.0", "native_impl": False},
    "mesh": {"dim": 3, "n_cells": 8, "lo": [0.0, 0.0, 0.0], "hi": [1.0, 1.0, 1.0]},
    "fields": [VALID_FIELD],
    "mapping_modes": ["conservative", "standard"],
}


def _manifest_with(**patch):
    payload = json.loads(json.dumps(VALID_MANIFEST))
    payload.update(patch)
    return payload


# --------------------------------------------------------------- schema shape
def test_schema_is_on_disk_and_matches_generated():
    """The archived schema must equal the generated one (no hand-edited drift)."""
    assert SCHEMA_PATH.is_file(), f"missing schema artifact {SCHEMA_PATH}"
    assert SCHEMA_PATH.read_text(encoding="utf-8") == schema_json(solver_protocol_schema())


@pytest.mark.parametrize("key", ["$schema", "$id", "title", "type", "required", "properties"])
def test_schema_declares_core_keywords(key):
    assert key in solver_protocol_schema()


@pytest.mark.parametrize("prop", ["protocol_version", "solver", "mesh", "fields", "mapping_modes"])
def test_schema_requires_protocol_blocks(prop):
    assert prop in solver_protocol_schema()["required"]


@pytest.mark.parametrize("prop", ["protocol_version", "solver_id", "version", "target_solver",
                                  "field_name", "mapping"])
def test_exchange_request_schema_requires_field(prop):
    schema = exchange_request_schema()
    assert prop in schema["required"]
    assert prop in schema["properties"]


@pytest.mark.parametrize("mode", [m.value for m in MappingMode])
def test_exchange_schema_admits_both_mapping_modes(mode):
    assert mode in exchange_request_schema()["properties"]["mapping"]["properties"]["mode"]["enum"]


# ----------------------------------------------------------- valid round trips
@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_every_adapter_manifest_validates(solver_id):
    adapter = build_adapter(solver_id)
    payload = adapter.manifest().to_dict()
    validate_with_schema(payload, solver_protocol_schema(), what="manifest")
    assert payload["solver"]["solver_id"] == solver_id


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_every_adapter_manifest_round_trips(solver_id):
    manifest = build_adapter(solver_id).manifest()
    again = AdapterManifest.from_dict(manifest.to_dict())
    assert again == manifest
    assert again.digest() == manifest.digest()


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_every_adapter_declares_non_empty_fields_and_boundaries(solver_id):
    manifest = build_adapter(solver_id).manifest()
    assert manifest.field_names()
    assert all(isinstance(spec, FieldSpec) for spec in manifest.fields)
    assert all(isinstance(b, BoundarySpec) for b in manifest.boundaries)


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_every_adapter_is_declared_synthetic(solver_id):
    """Honesty gate: no scaffold may claim to drive a real library."""
    assert build_adapter(solver_id).manifest().native_impl is False


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_every_adapter_declares_both_mapping_modes(solver_id):
    manifest = build_adapter(solver_id).manifest()
    assert MappingMode.CONSERVATIVE in manifest.mapping_modes
    assert MappingMode.STANDARD in manifest.mapping_modes


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_every_adapter_has_matching_class_registration(solver_id):
    assert adapter_class(solver_id).SOLVER_ID == solver_id


# --------------------------------------------------------------- invalid input
@pytest.mark.parametrize(
    "patch, reason",
    [
        ({"protocol_version": "solver-protocol/v2"}, "wrong protocol version"),
        ({"solver": {"solver_id": "lbm", "version": "1.0", "native_impl": False}}, "bad version"),
        ({"solver": {"solver_id": "LBM", "version": "1.0.0", "native_impl": False}}, "bad id case"),
        ({"fields": []}, "no fields"),
        ({"mapping_modes": []}, "no modes"),
        ({"mapping_modes": ["creative"]}, "unknown mode"),
        ({"mesh": {"dim": 0, "n_cells": 8, "lo": [], "hi": []}}, "zero dim"),
        ({"mesh": {"dim": 4, "n_cells": 8, "lo": [0, 0, 0, 0], "hi": [1, 1, 1, 1]}}, "dim too large"),
        ({"mesh": {"dim": 3, "n_cells": 0, "lo": [0, 0, 0], "hi": [1, 1, 1]}}, "zero cells"),
        ({"extra_key": 1}, "additional property"),
        ({"fields": [{"name": "density", "kind": "enthalpy", "dim_vector": [1, -3, 0, 0]}]},
         "unknown quantity kind"),
        ({"fields": [{"name": "density", "kind": "density", "dim_vector": [1, -3]}]},
         "short dim vector"),
        ({"fields": [{"name": "density", "kind": "flux", "dim_vector": [1, -2, -1, 0]}]},
         "flux without face_normal"),
    ],
)
def test_manifest_schema_rejects_invalid(patch, reason):
    payload = _manifest_with(**patch)
    with pytest.raises(ProtocolViolation):
        validate_with_schema(payload, solver_protocol_schema(), what="manifest")


@pytest.mark.parametrize(
    "payload",
    [
        {"protocol_version": PROTOCOL_VERSION, "solver": {"solver_id": "lbm", "version": "1.0.0",
                                                          "native_impl": False},
         "mesh": {"dim": 3, "n_cells": 8, "lo": [0, 0, 0], "hi": [1, 1, 1]},
         "fields": [VALID_FIELD], "mapping_modes": ["conservative"]},
    ],
)
def test_valid_manifest_accepted_as_control(payload):
    validate_with_schema(payload, solver_protocol_schema(), what="manifest")
    assert AdapterManifest.from_dict(payload).field("density").kind == "density"


@pytest.mark.parametrize(
    "field_kwargs",
    [
        {"name": "Density"},
        {"name": "9density"},
        {"name": "density", "kind": "density", "dim_vector": [1, -3, 0]},
        {"name": "density", "kind": "density", "dim_vector": [1, -3, 0, 0], "face_normal": 2},
        {"name": "density", "kind": "flux", "dim_vector": [1, -2, -1, 0]},
    ],
)
def test_field_spec_constructor_rejects_invalid(field_kwargs):
    kwargs = {"kind": "density", "dim_vector": (1, -3, 0, 0)}
    kwargs.update(field_kwargs)
    with pytest.raises(ProtocolViolation):
        FieldSpec(**kwargs)


@pytest.mark.parametrize(
    "mesh_kwargs",
    [
        {"dim": 3, "n_cells": 8, "lo": (0, 0, 0), "hi": (1, 1)},
        {"dim": 3, "n_cells": 8, "lo": (0, 0, 0), "hi": (1, 1, 0)},
        {"dim": 2, "n_cells": 0, "lo": (0, 0), "hi": (1, 1)},
        {"dim": 2, "n_cells": 8, "lo": (0, 0), "hi": (1, 1), "topology": "tet"},
    ],
)
def test_mesh_spec_constructor_rejects_invalid(mesh_kwargs):
    with pytest.raises(ProtocolViolation):
        MeshSpec(**mesh_kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "conservative", "operator": "nearest_scaled"},
        {"mode": "conservative", "operator": "overlap_p1"},
        {"operator": "magic"},
        {"sum_tol": 0.0},
        {"sum_tol": 1.5},
        {"moment_tol": -1.0},
    ],
)
def test_mapping_spec_rejects_inadmissible_combinations(kwargs):
    with pytest.raises(Exception) as excinfo:
        MappingSpec(**kwargs)
    assert isinstance(excinfo.value, ValueError)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"solver_id": "lbm", "version": "1.0.0", "target_solver": "lbm", "field_name": "density"},
        {"solver_id": "lbm", "version": "1", "target_solver": "dem", "field_name": "density"},
        {"solver_id": "lbm", "version": "1.0.0", "target_solver": "dem", "field_name": ""},
        {"solver_id": "lbm", "version": "1.0.0", "target_solver": "dem", "field_name": "density",
         "protocol_version": "solver-protocol/v0"},
    ],
)
def test_exchange_request_rejects_invalid(kwargs):
    with pytest.raises(ProtocolViolation):
        ExchangeRequest(**kwargs)


def test_exchange_request_round_trip_and_digest():
    request = ExchangeRequest(
        solver_id="lbm", version="1.0.0", target_solver="dem", field_name="density",
        mapping=MappingSpec(mode=MappingMode.STANDARD, operator="overlap_p1"),
    )
    payload = request.to_dict()
    validate_with_schema(payload, exchange_request_schema(), what="request")
    again = ExchangeRequest.from_dict(payload)
    assert again == request
    assert again.digest() == request.digest()


@pytest.mark.parametrize("bad", [{"solver_id": "lbm"}, {"protocol_version": PROTOCOL_VERSION},
                                 {"solver_id": "lbm", "version": "1.0.0", "target_solver": "DEM",
                                  "field_name": "density", "mapping": {"mode": "conservative",
                                                                       "operator": "overlap_l2",
                                                                       "sum_tol": 0.001,
                                                                       "moment_tol": 0.01}}])
def test_exchange_request_schema_rejects_incomplete(bad):
    with pytest.raises(ProtocolViolation):
        validate_with_schema(bad, exchange_request_schema(), what="request")


def test_manifest_digest_is_stable_and_content_sensitive():
    one = build_adapter("lbm").manifest()
    two = build_adapter("lbm").manifest()
    assert one.digest() == two.digest()
    other = build_adapter("fem").manifest()
    assert other.digest() != one.digest()


def test_unknown_field_lookup_raises():
    manifest = build_adapter("cad").manifest()
    with pytest.raises(ProtocolViolation):
        manifest.field("no_such_field")