# TensorPlatform

TensorPlatform is the shared data and AI platform for the Tensor engineering ecosystem. The upstream distribution name was Open Digital Foundation; this repository is the TensorPlatform-maintained integration and development home.

**System-wide overview:** [Tensor Ecosystem System Overview](https://github.com/cxs503/TensorPhysics/blob/main/docs/SYSTEM_OVERVIEW.md) — architecture, repository boundaries, shared contracts, end-to-end workflow and icebreaker application roadmap.


A domain-neutral foundation for data integration, governance, workflow
orchestration, AI model lifecycle management, deployment and observability.

This distribution contains no customer identity, production data, private
models, credentials, deployment addresses, backups, or commercial use cases.
Domain solutions must be delivered as separately maintained adapters/plugins.

## Role in the Tensor ecosystem

TensorPlatform is the **shared data and AI foundation**, not a second solver or scientific workflow engine.

- **TensorPhysics** owns scientific workflow execution, solver adapters, multiphysics coordination, run provenance, and numerical acceptance gates.
- **TensorLBM**, **TensorFVM**, **TensorFEM**, and **TensorDEM** own their specialist numerical kernels and solver-specific validation.
- **TensorPlatform** provides reusable services for identity and authorization, data catalog and lineage, datasets and experiments, model lifecycle, audit, and platform integration.

The intended engineering loop is: define a case and constraints → plan and execute a reproducible workflow → store run metadata and result artifacts → curate datasets → train and evaluate models → publish versioned models for permitted downstream use. Large fields, trajectories, and checkpoints should be stored as artifacts; the catalog should retain references, provenance, and validation status rather than duplicating large numerical payloads.

**Maturity note:** the README describes the target responsibility boundary, not a claim that every listed service or cross-repository integration is production-ready. See [Ecosystem Integration](docs/ecosystem-integration.md) and the [system overview](https://github.com/cxs503/TensorPhysics/blob/main/docs/SYSTEM_OVERVIEW.md).

## Core capabilities

- Identity, organizations, RBAC, auditing and tenant isolation
- Data source integration, catalog, quality, cleaning, lineage and labeling
- Feature engineering, experiments, training, evaluation and model registry
- Typed pipelines, background execution, serving and monitoring
- Object storage, time-series, event and API integration interfaces

## Security model

Runtime secrets are supplied through environment variables. The release must
pass `scripts/open_source_gate.py`; generated data and artifacts are ignored.

## Tensor ecosystem integration

This repository's role, interface boundaries, data-exchange requirements and recommended integration path are documented in [Ecosystem Integration](docs/ecosystem-integration.md).
