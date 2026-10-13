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
