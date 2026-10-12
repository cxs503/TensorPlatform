import json

import pytest
from pydantic import ValidationError

from api.tensorphysics_runs import RunRegistration, _canonical_json, _digest


def _manifest():
    return {
        "run_id": "dem-smoke-001",
        "status": "succeeded",
        "validation_status": "not_run",
        "schema_version": 1,
        "outputs": [
            {
                "uri": "file:///tmp/result.csv",
                "media_type": "text/csv",
                "checksum_sha256": "a" * 64,
                "size_bytes": 12,
            }
        ],
    }


def test_run_registration_requires_matching_status_vocabulary():
    request = RunRegistration(
        run_id="dem-smoke-001",
        status="succeeded",
        validation_status="not_run",
        manifest=_manifest(),
        organization_id=7,
    )
    assert request.run_id == request.manifest["run_id"]


def test_run_registration_rejects_oversized_manifest():
    manifest = _manifest()
    manifest["debug_payload"] = "x" * 1_000_001
    with pytest.raises(ValidationError):
        RunRegistration(
            run_id="dem-smoke-001",
            status="succeeded",
            validation_status="not_run",
            manifest=manifest,
            organization_id=7,
        )


def test_canonical_digest_is_stable_for_key_order():
    first = {"run_id": "r1", "status": "succeeded", "validation_status": "not_run"}
    second = {"validation_status": "not_run", "status": "succeeded", "run_id": "r1"}
    assert _canonical_json(first) == _canonical_json(second)
    assert _digest(_canonical_json(first)) == _digest(_canonical_json(second))


def test_manifest_rejects_non_finite_numbers():
    with pytest.raises(ValidationError):
        RunRegistration(
            run_id="dem-smoke-001",
            status="succeeded",
            validation_status="not_run",
            manifest={**_manifest(), "invalid": float("nan")},
            organization_id=7,
        )
