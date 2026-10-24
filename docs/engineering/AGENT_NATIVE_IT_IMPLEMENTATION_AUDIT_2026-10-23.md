# Agent-native IT / TensorPlatform implementation audit

**Audit date:** 2026-10-23  
**Scope:** public GitHub source on `cxs503/TensorPlatform` main, focused on the Agent-native IT whitepaper and the research-run integration path.  
**Method:** repository file inspection, test/workflow source inspection, and GitHub Actions metadata/log review.  
**Not performed:** SSH access, live server login, production API requests, deployment smoke tests, or numerical solver execution. This is a source-and-CI audit, not a production deployment certification.

## Executive summary

TensorPlatform has real implementation beyond architecture prose: a FastAPI run registry, tenant-scoped permission checks, canonical JSON SHA-256 idempotency, a manifest schema, tests for input validation/digest stability, and an S3 artifact streaming endpoint. The architecture is therefore not purely a mock.

However, the current research loop is still a **partial integration slice**, not yet a fully verified Agent-native research runtime. Key limitations are explicit in source/docs: the API does not enforce the full nested JSON Schema, registration does not prove artifact existence or checksum correctness, and a successful process is not equivalent to scientific validation. We could not verify the deployed service from this GitHub-only session.

## Evidence-backed findings

### 1. Implemented in source

- **Run registration API:** `backend/api/tensorphysics_runs.py` defines the `/tensorphysics/runs` API, request model, bounded manifest size, finite JSON validation, run/status consistency checks, canonical JSON serialization and SHA-256 digest.
- **Idempotency and conflict behavior:** the implementation uses organization + run ID as the logical key; identical manifest digests return an idempotent result, while a different manifest for the same key is rejected with HTTP 409. The database insert also uses conflict handling to address concurrent registration attempts.
- **Permission boundary:** registration/list/artifact routes declare `datasets:upload` or `datasets:read` permission checks and resolve the organization from the caller/request context. This is source evidence of intended enforcement, not a substitute for a live cross-tenant penetration test.
- **Artifact access path:** the API has an artifact streaming route for `s3://` references. It rejects non-S3 references and returns service-unavailable when the object store cannot be reached. The inspected path emits recorded checksum/size headers but does not hash the streamed bytes to independently verify them.
- **Contract and tests:** `contracts/tensorphysics-run-manifest-v1.schema.json` defines a versioned manifest shape. `backend/tests/test_tensorphysics_runs.py` covers valid request construction, oversized manifests, stable digest across key order, and rejection of non-finite numbers.
- **README discoverability:** the Agent-native IT whitepaper is now linked from the root README.

### 2. Explicit limitations / incomplete gates

- **Nested schema enforcement:** the API's Pydantic model validates the envelope and manifest JSON compatibility, but the source and `docs/research-loop-integration-contract.md` state that the complete nested JSON Schema is not yet enforced server-side.
- **Artifact integrity:** registration accepts metadata references; it does not prove the object exists or independently recompute the checksum and byte size. A client-side checksum can be wrong unless verified by a trusted ingestion/readback step.
- **Scientific validity:** `validation_status=not_run` is allowed and must remain distinct from execution success. Finite-value checks or a successful process do not establish conservation, convergence, benchmark agreement, or physical validity.
- **End-to-end integration:** the integration contract itself lists authorized registration of an actual TensorPhysics run, retrieval under the same organization boundary, idempotency/conflict, and cross-organization denial as acceptance gates. The docs do not claim these have all been proven in a live deployment.
- **Deployment:** no live server URL or SSH session was supplied/used in this audit. Production health, credentials, database migration state, S3 connectivity, and actual end-to-end execution remain **not verified**.

### 3. CI signals observed on 2026-10-23

- The commit adding the whitepaper triggered CI and Resident community workflows. At the time of inspection, the new README-link commit's CI and Resident community runs were still in progress; the current run is available at [GitHub Actions](https://github.com/cxs503/TensorPlatform/actions).
- The previous whitepaper commit's CI run failed in the **contract tests** job during test collection: `tests/factory/conftest.py` could not import `pgserver`, while `tests/test_public_wall.py` and `tests/test_single_tenant_fallback.py` could not import `fastapi`. The run log shows the contract job installs `pytest` but not the application/test dependencies required by those imports.
- In the inspected `.github/workflows/ci.yml`, the broad backend suite is marked `continue-on-error: true`; comments also describe known import/module failures. Consequently, a green overall workflow alone would not prove that the entire backend suite is healthy.
- The research-factory and frontend jobs passed in that prior run. The contract-test failure is a test-environment/dependency setup issue evidenced by collection errors; it is not proof that the run-registry implementation itself failed its focused tests.

## Priority actions

### P0 — Close the trust boundary

1. Enforce the versioned manifest schema at the API boundary, with explicit compatibility/version policy and tests for missing nested fields, invalid checksum syntax, invalid sizes, and unsupported schema versions.
2. Add a trusted artifact verification path: resolve permitted object-store references, verify byte size and SHA-256 against actual bytes, and persist verification status/time. Do not fetch arbitrary external URLs from the server.
3. Add integration tests using the real auth/organization policy: same-org register/read, cross-org denial, identical retry, changed-manifest conflict, missing object, bad checksum, and artifact proxy authorization.
4. Keep execution status, software integrity, numerical validation, and physical validation as separate status dimensions.

### P1 — Make CI evidence reliable

1. Repair the contract-test job dependency setup (including `fastapi` and the intended `pgserver` dependency or a documented service strategy), then rerun it.
2. Split fast contract tests from tests requiring PostgreSQL/services so collection does not fail due to missing environment dependencies.
3. Reduce the backend suite's advisory surface incrementally; each `continue-on-error` should have an owner, issue, and exit criterion.
4. Publish test counts, skips, and failure categories; never summarize an advisory job as “all tests passed.”

### P1 — Verify the deployed loop

1. Run the repository's documented read-only preflight and health check on the actual deployment.
2. Using an authorized non-admin identity, register one **real** TensorPhysics manifest with artifacts generated by an actual run; retrieve it and download each artifact through the platform.
3. Recompute hashes from downloaded bytes, repeat the same registration, submit a changed manifest under the same run ID, and test an unauthorized cross-organization request.
4. Save redacted request/response evidence, run IDs, commit SHAs, artifact hashes, exact commands, and exit codes in the run report.

## Acceptance state

| Capability | Audit status | Evidence / qualification |
|---|---|---|
| Manifest registration source | Implemented in source | `backend/api/tensorphysics_runs.py` |
| Digest/idempotency source | Implemented in source | canonical SHA-256 and conflict path present |
| Organization/permission checks | Present in source; live behavior not verified | Requires cross-organization integration test |
| Versioned manifest schema | Defined | Server-side full nested enforcement remains incomplete |
| Artifact streaming | Implemented for S3 references | Independent checksum verification is not implemented in inspected route |
| Focused run-registry tests | Present in source | This audit did not execute them locally |
| CI contract-test job | Failed on dependency collection in prior run | Current post-README CI was still in progress at inspection |
| Full backend CI | Advisory/non-blocking | Workflow uses `continue-on-error: true` |
| Production deployment | Not verified | No live request or SSH test in this audit |
| Scientific validation of a real run | Not verified | Requires solver-generated data and numerical/physical acceptance gates |

## Conclusion

The next engineering increment should not add another broad platform feature. It should make the existing research-run path trustworthy: enforce the contract, verify artifact bytes, test tenant boundaries, repair CI dependency setup, and then perform a real deployment smoke test. Until those gates have evidence, describe the system as a partially implemented research-loop integration—not a fully autonomous, production-validated science factory.
