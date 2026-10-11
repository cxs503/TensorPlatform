# Tensor ecosystem integration

TensorPlatform is the shared, domain-neutral data and AI foundation for the Tensor engineering ecosystem. It should provide reusable platform services rather than duplicate solver numerics or scientific workflow orchestration.

## Responsibilities

- Dataset catalog, metadata, quality, labeling, lineage and access policy.
- Experiments, training/evaluation records, model registry and deployment lifecycle.
- Shared identity, authorization, audit, observability and storage integration.
- Reusable APIs and agent-facing capabilities with permission checks.

## Boundaries with sibling projects

- **TensorPhysics** owns scientific workflow orchestration, solver lifecycle, execution backends, multiphysics coupling, run manifests and numerical validation.
- **TensorLBM**, **TensorFVM**, **TensorFEM** and **TensorDEM** own their numerical methods, solver-specific configuration, field/state semantics and domain benchmarks.
- Large field outputs and trajectories belong in artifact/object storage; catalog metadata, permissions, lineage and status belong in platform services.
- Platform integrations should use versioned, documented contracts and stable artifact references instead of importing another solver's private internals.

## First end-to-end application

For an icebreaker/ice-fracture workflow, specialist solvers produce fluid, structural and bonded-particle results; TensorPhysics coordinates runs and validation; TensorPlatform catalogs datasets and artifacts, tracks provenance, and manages downstream AI/ROM experiments.

## Integration acceptance checklist

1. Every run has a durable identifier and a machine-readable manifest.
2. Artifacts include format, units, schema/version, checksum and producer revision where applicable.
3. Dataset creation records source artifacts, transformation/configuration and validation status.
4. Access to jobs, artifacts and models is permission-checked and auditable.
5. A minimal integration test proves artifact registration and lineage without requiring a specific storage vendor or ML framework.

## Repository map

- [TensorPlatform](https://github.com/cxs503/TensorPlatform) — shared data and AI services.
- [TensorPhysics](https://github.com/cxs503/TensorPhysics) — scientific workflow orchestration and multiphysics coordination.
- [TensorLBM](https://github.com/cxs503/TensorLBM) — lattice Boltzmann solver.
- [TensorFVM](https://github.com/cxs503/TensorFVM) — finite-volume solver.
- [TensorFEM](https://github.com/cxs503/TensorFEM) — finite-element solver.
- [TensorDEM](https://github.com/cxs503/TensorDEM) — discrete-element solver.
