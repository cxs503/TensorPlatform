"""Lightweight consistency tests for the shared research-run contract.

These tests intentionally use only the standard library so the contract can be
checked without installing an optional JSON Schema validator.
"""
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "tensorphysics-run-manifest-v1.schema.json"
FIXTURE_PATH = ROOT / "examples" / "research-loop" / "manifest.fixture.json"


def test_manifest_schema_declares_registry_status_vocabulary():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    properties = schema["properties"]

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["required"] == [
        "schema_version",
        "run_id",
        "status",
        "validation_status",
        "outputs",
    ]
    assert properties["status"]["enum"] == [
        "planned", "validating", "queued", "running", "succeeded",
        "failed", "cancelled", "preempted",
    ]
    assert properties["validation_status"]["enum"] == [
        "not_run", "passed", "failed", "incomplete",
    ]


def test_research_loop_fixture_matches_core_manifest_contract():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == schema["properties"]["schema_version"]["const"]
    assert manifest["run_id"]
    assert manifest["status"] in schema["properties"]["status"]["enum"]
    assert manifest["validation_status"] in schema["properties"]["validation_status"]["enum"]

    for required in schema["required"]:
        assert required in manifest

    assert manifest["run_id"] == "research-loop-dem-demo-001"
    assert manifest["validation"]["status"] == manifest["validation_status"]
    assert manifest["outputs"], "The fixture should demonstrate artifact references."

    for artifact in manifest["outputs"]:
        for key in ("uri", "media_type", "checksum_sha256", "size_bytes"):
            assert key in artifact
        assert re.fullmatch(r"[a-f0-9]{64}", artifact["checksum_sha256"])
        assert isinstance(artifact["size_bytes"], int) and artifact["size_bytes"] >= 0


def test_manifest_schema_keeps_large_artifacts_as_references():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    output_schema = schema["properties"]["outputs"]["items"]["properties"]

    assert "uri" in output_schema
    assert "checksum_sha256" in output_schema
    assert "size_bytes" in output_schema
    assert "payload" not in output_schema
