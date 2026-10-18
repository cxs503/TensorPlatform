# Research-loop integration contract

This document defines the first shared boundary between TensorPhysics' research runner and TensorPlatform's run registry. It deliberately does not move scientific orchestration into TensorPlatform.

## Ownership

- **TensorPhysics** owns task lifecycle, Agent/backend adapters, solver execution, independent numerical validation, run-manifest production, and next-action selection.
- **TensorPlatform** owns authenticated/tenant-scoped registration and retrieval of compact run metadata, artifact references, dataset lineage, experiment/model lifecycle, and audit.
- **Solver repositories** own numerical algorithms, solver inputs/outputs, and solver-specific benchmarks.

## Contract files

- JSON Schema: [`contracts/tensorphysics-run-manifest-v1.schema.json`](../contracts/tensorphysics-run-manifest-v1.schema.json)
- Illustrative fixture: [`examples/research-loop/manifest.fixture.json`](../examples/research-loop/manifest.fixture.json)
- Registry endpoint details: [TensorPhysics run registry](tensorphysics-run-registry.md)

The fixture contains deliberately fake paths, zero hashes, and placeholder revisions. It is a shape example, not a real run record; do not register it as scientific evidence.

## Required behavior

1. TensorPhysics assigns a stable `run_id` before execution and uses it in both the manifest and registry request.
2. The manifest records the run status and validation status separately. A process that exits successfully is not automatically scientifically valid.
3. Every published output reference declares a URI, media type, SHA-256 checksum, and byte size. Where applicable, also record schema, units, role, and producer revision.
4. Large fields, trajectories, checkpoints, and images remain in an artifact/object store. The registry holds compact metadata and references.
5. A failed, cancelled, preempted, or incomplete run remains discoverable with its failure/limitation evidence; it must not be silently promoted to a successful dataset.
6. Retrying a registration with the same manifest is idempotent. Reusing a run ID with a different manifest is a conflict.
7. Only independently validated outputs may be proposed for promotion into reusable datasets or model-training corpora. Preserve the validation report and its artifact references.

## Current implementation boundary

The existing `POST /api/v1/tensorphysics/runs` endpoint validates the outer status vocabulary, run ID consistency, JSON serializability, manifest size, and idempotent digest. The JSON Schema in this directory is a caller-side shared contract in this increment; the endpoint does **not yet** enforce every nested schema field, upload artifacts, verify that a remote URI exists, or prove numerical correctness.

The existing registry is therefore a metadata registration primitive, not a complete artifact ingestion or dataset-lineage service. The next integration gate is an executable TensorPhysics demo that registers its actual manifest using an authorized platform identity, then proves that artifact references and validation state can be retrieved under the same organization boundary.

## Minimal acceptance checklist

- [ ] Schema validation runs before registration.
- [ ] Every checksum is computed from the exact artifact bytes, not copied from a template.
- [ ] Registry response digest matches the submitted canonical manifest.
- [ ] A repeated identical registration succeeds idempotently.
- [ ] A changed manifest under the same organization/run ID is rejected with conflict.
- [ ] Unauthorized cross-organization reads/writes are rejected.
- [ ] `validation_status=not_run`, `failed`, and `incomplete` are never treated as validated evidence.
- [ ] A later dataset-promotion record retains source run IDs, source artifact references, transformations, and validation evidence.

## Artifact integrity and evidence bundle (local research runner)

The SpringChainBenchmark runner writes SHA-256 and byte-size metadata for each downloadable artifact. The manifest records a schema_version, run status, independent validation status, configuration digest, producer revision (or unknown when the deployment does not inject GIT_COMMIT_SHA), output checksums, metrics, and explicit limitations. The manifest intentionally does not embed its own checksum; the API's persisted task result carries the manifest file's checksum to avoid a self-referential hash.

validation_status=not_run is deliberate: finite-value checks only establish that the computed state remained finite. They do not establish physical correctness, conservation, convergence, or validity for icebreaking. Consumers must not promote this run to scientifically validated evidence based only on process completion.

The integration test verifies that checksums and sizes match the exact artifact bytes written by the runner. This is an integrity/provenance gate, not a cryptographic signature or proof that the computation itself is scientifically correct.

## Unified research asset search (first executable slice)

GET /api/v1/research-factory/assets/search?q=...&limit=10 searches persisted local run records and optionally queries a configured XERJ native index. Local search reads the actual research_runs SQLite table and searches task goals, solver/status metadata, parameters, run metrics, and error text; it does not invent paper or benchmark records. The response includes per-provider status so an unconfigured or unavailable XERJ service is never presented as a successful search.

To enable XERJ, the deployment operator must set XERJ_BASE_URL and XERJ_INDEX; XERJ_API_KEY is optional for local development but should be set when the XERJ node requires authentication. The adapter uses XERJ's native POST /v1/indices/{index}/search endpoint. Indexing remains a separate ingestion responsibility: source repositories, papers, benchmark reports, and failure notes will only appear after they have actually been indexed into the configured corpus.

This first slice deliberately does not merge tenant-scoped TensorPhysics registry rows or the general data catalog into the endpoint yet. Those stores have separate authorization/ownership boundaries and need a permission-preserving federated adapter rather than a cross-database shortcut. XERJ errors degrade gracefully to local-only results; no query content is sent externally unless XERJ is explicitly configured.
