# TensorPhysics run registry API

TensorPlatform exposes a small metadata-only registry for scientific runs. TensorPhysics remains the owner of solver orchestration and numerical acceptance; TensorPlatform stores run identity, manifest, artifact references, validation status and tenant-scoped provenance.

The cross-repository schema and integration acceptance criteria are defined in [Research-loop integration contract](research-loop-integration-contract.md), with the versioned [manifest JSON Schema](../contracts/tensorphysics-run-manifest-v1.schema.json) and an explicitly non-evidentiary [fixture](../examples/research-loop/manifest.fixture.json).

## Endpoint

Base path: `/api/v1/tensorphysics/runs`

- `POST /api/v1/tensorphysics/runs` — register a run manifest. Requires an authenticated user with `datasets:upload` permission.
- `GET /api/v1/tensorphysics/runs/{run_id}?organization_id=...` — retrieve a run. Requires `datasets:read` permission.

The endpoint uses the platform's existing bearer-token authentication and organization boundary. A non-admin may only register/read runs in their own organization. An administrator may specify `organization_id`; if neither a requested nor default organization is available, the request is rejected.

## Registration body

```json
{
  "run_id": "dem-smoke-001",
  "status": "succeeded",
  "validation_status": "not_run",
  "organization_id": 7,
  "manifest": {
    "run_id": "dem-smoke-001",
    "status": "succeeded",
    "validation_status": "not_run",
    "schema_version": 1,
    "outputs": [
      {
        "uri": "s3://bucket/run-001/result.csv",
        "media_type": "text/csv",
        "checksum_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "size_bytes": 1234
      }
    ]
  }
}
```

The real checksum must be the lowercase SHA-256 digest of the referenced artifact. The example value above is illustrative only.

## Reliability and limits

- The unique key is `(organization_id, run_id)`.
- Reposting the exact same manifest is idempotent; a different manifest for the same key returns HTTP 409.
- The canonical manifest digest is stored as SHA-256 and JSON is stored in PostgreSQL JSONB.
- Manifests are limited to 1 MB. Large fields, trajectories and checkpoints must remain in artifact/object storage; the API stores references, not payloads.
- Run status and numerical validation status are independent. A successful run with `validation_status=not_run` remains explicitly unvalidated.
- The endpoint does not itself ingest files, verify remote object existence, enforce the complete nested JSON Schema, or claim solver correctness. Schema enforcement in this increment is a caller-side responsibility.

## Database

Migration `999_tensorphysics_run_registry.sql` creates the registry table and indexes. The deployment must run the repository's normal numbered migration runner before using the endpoint.
