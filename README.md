# TensorPlatform

TensorPlatform is the **general-purpose AI-era infrastructure layer** for the Tensor ecosystem: shared runtime and integration primitives that let agents discover and compose capabilities, coordinate work, persist task state, use governed tools and resources, and preserve auditable artifacts. Scientific computing is a major first domain, not the boundary of the platform.

**Canonical Tensor ecosystem vision and repository boundaries:** [Tensor Ecosystem: Vision for the AI Era](docs/architecture/TENSOR_ECOSYSTEM_AI_ERA_VISION.md). This is the strategic reference for maintainers and autonomous development agents across Tensor repositories.

**System-wide science/engineering overview:** [Tensor Ecosystem System Overview](https://github.com/cxs503/TensorPhysics/blob/main/docs/SYSTEM_OVERVIEW.md) — architecture, repository boundaries, shared contracts, end-to-end workflow and icebreaker application roadmap.

**Engineering plan / whitepaper:** [Tensor Autonomous Research Infrastructure — Engineering Development Plan v1.0](docs/roadmap/TENSOR_AUTONOMOUS_RESEARCH_INFRASTRUCTURE_WHITEPAPER_V1.0.md) — current project inventory, implemented capabilities, open gaps, phased roadmap, closed-loop demo and acceptance gates. This is a science-focused implementation plan, subordinate to the broader ecosystem vision.

**Agent-native IT architecture:** [Agent-Native IT Technology Stack and TensorPlatform Evolution Whitepaper v0.1](docs/architecture/AGENT_NATIVE_IT_TENSORPLATFORM_WHITEPAPER_v0.1.md) — compares human-first and agent-native infrastructure, defines capability contracts, persistent execution, runtime boundaries, governance and a phased migration roadmap. This is a discussion draft; planned capabilities are not claims of implementation.

**Implementation audit (2026-10-23):** [Agent-native IT implementation audit](docs/engineering/AGENT_NATIVE_IT_IMPLEMENTATION_AUDIT_2026-10-23.md) — source-level evidence, CI failure analysis, unverified deployment gates and prioritized next actions. This is not a live production certification.

## Core responsibility

TensorPlatform owns reusable, domain-neutral infrastructure: agent/runtime integration, durable task and event primitives, capability discovery and versioned contracts, tool integration, governed execution, identity and authorization, audit, observability, and shared artifact/data/model lifecycle services where they are genuinely cross-domain.

TensorPlatform does **not** own specialist domain knowledge or domain-specific acceptance rules. It should not embed physical formulas, solver algorithms, film grammar, game mechanics, or humanities methodology into its core. Those belong to domain systems and specialist tools, connected through stable interfaces.

The platform is not required to reimplement every underlying IT service. It should integrate mature external infrastructure when appropriate and build only where a demonstrated ecosystem need justifies it.

## Role in the Agent-first science factory

Science and engineering are the first demanding use cases for the broader AI-era infrastructure vision. In this domain, TensorPlatform is the **shared trust, data, and research-asset plane** for a lights-out science factory. It gives authorized agents and workflows governed access to datasets, immutable artifacts, provenance, experiment records, model versions, audit trails, and organizational policy. It does not decide scientific hypotheses or own solver execution logic.

The intended contract is: TensorPhysics executes a bounded experiment and emits a RunManifest plus artifact references; TensorPlatform catalogs those records, applies access and lineage policy, and supports promotion of qualified outputs into reusable datasets and models. A stored result is not automatically validated scientific evidence.

**Science/engineering architecture:** [Science Factory Architecture](https://github.com/cxs503/TensorPhysics/blob/main/docs/SCIENCE_FACTORY_ARCHITECTURE.md) · [System Overview](https://github.com/cxs503/TensorPhysics/blob/main/docs/SYSTEM_OVERVIEW.md)

## Role in the Tensor ecosystem

- **TensorPlatform** provides reusable, domain-neutral agent and platform infrastructure.
- **TensorPhysics** owns scientific workflow semantics, solver coordination, numerical/physical validation, and scientific acceptance rules.
- **TensorLBM**, **TensorFVM**, **TensorFEM**, **TensorDEM**, **TensorBEM**, **TensorSPH**, and **TensorDG** own their specialist numerical methods, solver implementations, diagnostics, and validation cases.
- **TensorCAD** owns geometry-specific representations and editing capabilities.
- Future video, game, humanities, education, and other domain capabilities should supply their own specialist logic and evaluators without requiring domain-specific changes to TensorPlatform core.

Domain capabilities may be separate repositories, plugins, capability packages, or adapters to external tools. Do not create a new repository merely to represent every new idea; choose the lightest boundary that supports independent testing, versioning, ownership, and dependencies.

The intended science workflow is: define a case and constraints → plan and execute a reproducible workflow → store run metadata and result artifacts → curate datasets → train and evaluate models → publish versioned models for permitted downstream use. Large fields, trajectories, and checkpoints should be stored as artifacts; the catalog should retain references, provenance, and validation status rather than duplicating large numerical payloads.

**Current integration increment:** the [Research-loop integration contract](docs/research-loop-integration-contract.md) defines the versioned run-manifest schema, a non-evidentiary fixture, and the next acceptance gates for registering actual TensorPhysics outputs.

**Engineering quality standard:** [Agent Engineering Quality Gates](docs/engineering/AGENT_ENGINEERING_QUALITY_GATES.md) defines risk-based checks, regression and scientific benchmark gates, evidence manifests, failure attribution, and safe adoption of semantic scanners.

**Maturity note:** this README describes target responsibilities, not a claim that every listed service or cross-repository integration is production-ready. See [Ecosystem Integration](docs/ecosystem-integration.md), the [system overview](https://github.com/cxs503/TensorPhysics/blob/main/docs/SYSTEM_OVERVIEW.md), and the implementation audit.

## Core capabilities

- Agent/runtime integration and replaceable model backends
- Durable tasks, events, background execution, and recovery primitives
- Capability discovery, typed/versioned integration contracts, and tool/service adapters
- Identity, organizations, RBAC, auditing, tenant isolation, and policy enforcement
- Data source integration, catalog, quality, cleaning, lineage, and labeling
- Feature engineering, experiments, training, evaluation, and model registry
- Typed pipelines, serving, monitoring, object storage, time-series, event, and API integration interfaces

Actual availability and maturity must be checked against implementation, tests, and deployment evidence.

## Security model

Runtime secrets are supplied through environment variables. The release must pass `scripts/open_source_gate.py`; generated data and artifacts are ignored. Agent actions must use least privilege, auditable policy enforcement, and resource limits; prompts alone are not a security boundary.

## Tensor ecosystem integration

This repository's role, interface boundaries, data-exchange requirements and recommended integration path are documented in [Ecosystem Integration](docs/ecosystem-integration.md). Every domain repository should document its own scope, explicit non-goals, integration contract, domain-specific quality gates, and current maturity, following the [canonical ecosystem vision](docs/architecture/TENSOR_ECOSYSTEM_AI_ERA_VISION.md).

## Optional resident agent community

Persistent sessions, personal memory, peer discussion and reviewed source development are available as an opt-in extension. See [setup and limitations](docs/factory/resident-community.md) and [comparison and validation report](docs/engineering/RESIDENT_COMMUNITY_EXTRACTION_2026-10-21.md).
