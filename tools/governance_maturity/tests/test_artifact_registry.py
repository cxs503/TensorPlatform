"""Tests for the artifact schema registry (synthetic schemas only)."""

from __future__ import annotations

import copy
import json

import pytest

from tools.governance_maturity.artifact_registry import (
    ArtifactRegistry,
    ArtifactSchema,
    ConsumerContract,
    canonical_payload_digest,
    make_envelope,
    semver_matches,
)

REGISTRY_DOC = {
    "schemas": [
        {
            "artifact_type": "metric_row",
            "version": "1.2.0",
            "producer": "RepoA::mod_a",
            "strict_fields": True,
            "fields": [
                {"name": "metric", "type": "string"},
                {"name": "value", "type": "number"},
                {"name": "unit", "type": "string", "enum": ["ratio", "count"]},
                {"name": "evidence_count", "type": "integer", "minimum": 0},
            ],
        },
        {
            "artifact_type": "case_result",
            "version": "1.0.0",
            "producer": "RepoB::mod_b",
            "fields": [
                {"name": "case_id", "type": "string"},
                {"name": "passed", "type": "boolean"},
                {"name": "tags", "type": "array", "items_type": "string"},
            ],
        },
        {
            "artifact_type": "metric_row",
            "version": "1.1.0",
            "producer": "RepoA::mod_a",
            "strict_fields": True,
            "fields": [
                {"name": "metric", "type": "string"},
                {"name": "value", "type": "number"},
                {"name": "unit", "type": "string", "enum": ["ratio", "count"]},
                {"name": "evidence_count", "type": "integer", "minimum": 0},
            ],
        },
        {
            "artifact_type": "metric_row",
            "version": "2.0.0",
            "producer": "RepoA::mod_a",
            "fields": [{"name": "metric", "type": "string"}, {"name": "value", "type": "number"}],
        },
    ],
    "consumers": [
        {
            "name": "sink",
            "repo": "RepoA",
            "module": "mod_sink",
            "accepts": {"metric_row": {"major": 1, "min_minor": 2}, "case_result": {"major": 1}},
        }
    ],
}


@pytest.fixture()
def registry() -> ArtifactRegistry:
    return ArtifactRegistry.from_dict(REGISTRY_DOC)


def _envelope(payload=None, **overrides):
    payload = payload if payload is not None else {
        "metric": "repro_rate",
        "value": 0.95,
        "unit": "ratio",
        "evidence_count": 19,
    }
    env = {
        "artifact_type": "metric_row",
        "schema_version": "1.2.0",
        "payload": payload,
        "checksum": canonical_payload_digest(payload),
        "provenance": {
            "producer": "RepoA::mod_a",
            "created_at": "2026-11-10T00:00:00Z",
            "source_digest": "sha256:" + "a" * 64,
            "tool_version": "test",
        },
    }
    env.update(overrides)
    return env


# --------------------------------------------------------------------------- #
# registration / lookup
# --------------------------------------------------------------------------- #
def test_lookup_latest_version(registry):
    assert registry.schema("metric_row").version == "2.0.0"
    assert registry.schema("metric_row", "1.2.0").version == "1.2.0"
    assert registry.versions("metric_row") == ["1.1.0", "1.2.0", "2.0.0"]


def test_unknown_type_raises(registry):
    with pytest.raises(KeyError):
        registry.schema("nope")


def test_semver_compatibility():
    assert semver_matches("1.2.3", 1, 2)
    assert not semver_matches("2.0.0", 1, 0)
    assert not semver_matches("1.1.0", 1, 2)
    assert not semver_matches("not-a-version", 1, 0)


def test_coverage_counts_repos_and_modules(registry):
    cov = registry.coverage()
    assert cov["n_repos"] == 2
    assert set(cov["modules"]) == {"RepoA::mod_a", "RepoB::mod_b", "RepoA::mod_sink"}
    assert cov["artifact_types"] == 2


def test_duplicate_field_rejected():
    doc = copy.deepcopy(REGISTRY_DOC["schemas"][0])
    doc["fields"].append({"name": "metric", "type": "string"})
    with pytest.raises(ValueError):
        ArtifactSchema.from_dict(doc)


def test_unsupported_field_type_rejected():
    doc = copy.deepcopy(REGISTRY_DOC["schemas"][0])
    doc["fields"][0]["type"] = "quantity"
    with pytest.raises(ValueError):
        ArtifactSchema.from_dict(doc)


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def test_valid_artifact_accepted(registry):
    report = registry.validate(_envelope(), consumer="sink")
    assert report.ok, [e.as_dict() for e in report.errors]
    assert report.checks["checksum_integrity"]
    assert report.checks["consumer_compatibility"]


def test_checksum_mismatch_rejected(registry):
    env = _envelope()
    env["checksum"] = "sha256:" + "0" * 64
    report = registry.validate(env)
    assert not report.ok
    assert {e.code for e in report.errors} == {"checksum_mismatch"}


def test_payload_tamper_rejected(registry):
    env = _envelope()
    env["payload"]["value"] = 0.5
    report = registry.validate(env)
    assert any(e.code == "checksum_mismatch" for e in report.errors)


def test_missing_required_field(registry):
    env = _envelope(payload={"metric": "x", "value": 1.0, "unit": "ratio"})
    report = registry.validate(env)
    assert {e.code for e in report.errors} == {"field_missing"}


def test_wrong_type_rejected(registry):
    env = _envelope(payload={"metric": "x", "value": "high", "unit": "ratio", "evidence_count": 1})
    report = registry.validate(env)
    assert any(e.code == "field_type" for e in report.errors)


def test_bool_is_not_integer(registry):
    env = _envelope(payload={"metric": "x", "value": 1.0, "unit": "ratio", "evidence_count": True})
    report = registry.validate(env)
    assert any(e.code == "field_type" for e in report.errors)


def test_enum_violation(registry):
    env = _envelope(payload={"metric": "x", "value": 1.0, "unit": "widgets", "evidence_count": 1})
    report = registry.validate(env)
    assert any(e.code == "field_enum" for e in report.errors)


def test_minimum_violation(registry):
    env = _envelope(payload={"metric": "x", "value": 1.0, "unit": "ratio", "evidence_count": -2})
    report = registry.validate(env)
    assert any(e.code == "field_below_minimum" for e in report.errors)


def test_non_finite_number_rejected(registry):
    env = _envelope(payload={"metric": "x", "value": float("nan"), "unit": "ratio", "evidence_count": 1})
    report = registry.validate(env)
    assert any(e.code == "field_not_finite" for e in report.errors)


def test_extra_field_rejected_under_strict_policy(registry):
    env = _envelope(payload={
        "metric": "x", "value": 1.0, "unit": "ratio", "evidence_count": 1, "note": "extra",
    })
    report = registry.validate(env)
    assert any(e.code == "field_unexpected" for e in report.errors)


def test_array_item_type_checked(registry):
    payload = {"case_id": "c1", "passed": True, "tags": ["a", 2]}
    env = _envelope(payload=payload, artifact_type="case_result", schema_version="1.0.0")
    env["provenance"]["producer"] = "RepoB::mod_b"
    report = registry.validate(env)
    assert any(e.code == "array_item_type" for e in report.errors)


def test_unregistered_producer_rejected(registry):
    env = _envelope()
    env["provenance"]["producer"] = "RepoA::impostor"
    report = registry.validate(env)
    assert any(e.code == "producer_unregistered" for e in report.errors)


def test_malformed_source_digest_rejected(registry):
    env = _envelope()
    env["provenance"]["source_digest"] = "deadbeef"
    report = registry.validate(env)
    assert any(e.code == "source_digest_malformed" for e in report.errors)


def test_malformed_created_at_rejected(registry):
    env = _envelope()
    env["provenance"]["created_at"] = "yesterday"
    report = registry.validate(env)
    assert any(e.code == "created_at_malformed" for e in report.errors)


def test_missing_envelope_key_short_circuits(registry):
    env = _envelope()
    del env["checksum"]
    report = registry.validate(env)
    assert any(e.code == "envelope_missing_key" for e in report.errors)
    assert report.checks["envelope_shape"] is False


def test_consumer_version_floor_enforced(registry):
    env = _envelope(schema_version="1.1.0")
    report = registry.validate(env, consumer="sink")
    assert any(e.code == "consumer_version_mismatch" for e in report.errors)


def test_consumer_rejects_unknown_type(registry):
    payload = {"case_id": "c1", "passed": True, "tags": []}
    env = _envelope(payload=payload, artifact_type="case_result", schema_version="1.0.0")
    env["provenance"]["producer"] = "RepoB::mod_b"
    contract = ConsumerContract.from_dict(
        {"name": "narrow", "repo": "RepoA", "module": "m", "accepts": {"metric_row": {"major": 1}}}
    )
    registry.register_consumer(contract)
    report = registry.validate(env, consumer="narrow")
    assert any(e.code == "consumer_rejects_type" for e in report.errors)


def test_unknown_consumer_rejected(registry):
    report = registry.validate(_envelope(), consumer="missing")
    assert any(e.code == "consumer_unknown" for e in report.errors)


# --------------------------------------------------------------------------- #
# envelope construction
# --------------------------------------------------------------------------- #
def test_make_envelope_roundtrip(registry):
    env = make_envelope(
        registry,
        artifact_type="metric_row",
        schema_version="1.2.0",
        payload={"metric": "uplift", "value": 0.1, "unit": "ratio", "evidence_count": 3},
        producer="RepoA::mod_a",
        source_digest="sha256:" + "b" * 64,
        created_at="2026-11-10T00:00:00Z",
    )
    report = registry.validate(env, consumer="sink")
    assert report.ok, [e.as_dict() for e in report.errors]
    assert json.loads(json.dumps(env)) == env


def test_make_envelope_rejects_wrong_producer(registry):
    with pytest.raises(ValueError):
        make_envelope(
            registry,
            artifact_type="metric_row",
            payload={"metric": "x", "value": 1.0},
            producer="RepoB::mod_b",
            source_digest="sha256:" + "c" * 64,
        )