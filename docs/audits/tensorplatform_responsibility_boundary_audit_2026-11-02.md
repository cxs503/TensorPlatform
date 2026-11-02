# TensorPlatform responsibility-boundary audit (2026-11-02)

## Status and scope

This is a first-pass **source-structure and contract audit**, based on the current `main` tree and the repository's published integration contracts. It is not a runtime, deployment, or scientific-validity certification. No production routes or numerical code are changed by this report.

Primary question: does the implementation match the intended split between a domain-neutral TensorPlatform, TensorPhysics as the scientific execution/orchestration layer, and specialist/domain applications?

## Executive finding

**The documented boundary is clearer than the current source-tree boundary.** The integration docs say TensorPlatform is domain-neutral and TensorPhysics owns scientific workflow orchestration, solver lifecycle, coupling, run manifests and numerical acceptance. Yet TensorPlatform's current tree contains a sizeable ship-performance and ship-verification implementation, as well as research-mission/campaign/execution control routes. These are not just generic platform primitives.

This is a strong architecture-drift signal, but directory names alone do not prove that every module is active or should be moved. Migration must be based on import/route reachability, API consumers, data ownership and tests.

## Evidence reviewed

- `backend/main.py`: imports shared platform domains (`data`, `model`, `deployment`, `iam`, `observability`, `platform`, `iot_edge`, `ops`, `tools`, `misc`, `tensor`). The `tensor` aggregate imports research scenarios, executions, approvals, sessions, missions/lifecycle, campaigns, reports, papers, reproducibility and execution lineage.
- `backend/api/domain/tensor.py`: explicitly calls itself the “research console operation plane”; aggregates `tensor_scenarios`, `tensor_executions`, `tensor_approvals`, `tensor_sessions`, `tensor_missions`, `tensor_mission_lifecycle`, `campaign_plans`, `tensor_repro`, `execution_lineage`, and `truth_gates`.
- `backend/api/domain/`: contains domain-specific hull geometry, ice UQ, manoeuvring, polar, propeller open-water, resistance, ship batch/benchmark/green/icing/samples/seakeeping/vibration modules.
- `src/ship_perf/`: includes resistance, hydrostatics, propeller and radiation/seakeeping-related physics code, plus conformance checks.
- `src/ship_verification/`: includes Holtrop resistance and Wigley verification/reaudit code.
- `backend/api/domain/ship_batch.py`: implements a ship-resistance batch sweep and calls a ship-specific resistance endpoint. This is application workflow, not generic platform scheduling.
- `docs/ecosystem-integration.md` and `docs/research-loop-integration-contract.md`: explicitly state that TensorPlatform is the shared domain-neutral data/AI foundation and that TensorPhysics owns scientific orchestration, solver lifecycle, coupling and numerical validation.
- `docs/research-loop-integration-contract.md` also candidly states the current run-registry endpoint validates only a limited outer envelope and digest/idempotency; the endpoint does not enforce every nested schema field, upload artifacts, verify remote URI existence or prove numerical correctness.

## Findings by severity

### P1 — Ship-domain physics and verification live inside TensorPlatform

**Evidence:** `backend/api/domain/{hull_geometry,ice_uq,manoeuvring,polar,propeller_open_water,resistance_core,ship_batch,ship_benchmark,ship_green,ship_icing,ship_samples,ship_seakeeping,ship_vibration}.py`, `src/ship_perf/`, and `src/ship_verification/`.

These modules encode ship geometry, resistance methods, propeller/hydrostatic calculations, seakeeping/vibration, ship-specific batch execution, and numerical verification. They make TensorPlatform carry a specific engineering domain, contrary to the repository's own domain-neutral charter.

**Recommended ownership:**
- Generic storage/catalog/lineage/permissions/audit for these results remain in TensorPlatform.
- Research orchestration, solver lifecycle, run manifests, multi-solver coupling and acceptance-gate *frameworks* belong in TensorPhysics.
- Ship-specific equations, benchmark fixtures and domain acceptance rules should live in a ship-domain application/package (a future TensorShipDesign or a separately versioned ship-engineering adapter) or the appropriate specialist solver repository. Do **not** blindly move all equations into TensorPhysics; TensorPhysics should not become another monolithic domain solver.
- TensorPlatform should expose stable, permission-checked APIs and plugin/adapter registration rather than own the equations.

### P1 — Research workflow/control plane is aggregated inside the Platform API

**Evidence:** `backend/api/domain/tensor.py` aggregates scenarios, executions, approvals, research missions and lifecycle, campaign plans, reports, papers, reproducibility and execution lineage. The comments label this the research console operation plane.

Some capabilities are legitimately generic platform services (identity, agent sessions, workspace access, audit, generic task/resource metadata). But research-specific lifecycle and decisions—campaign progression, scientific run execution, reproducibility evidence, next-step selection and scientific acceptance—belong in TensorPhysics or a research application layer.

**Recommended split:**
- Keep generic identity/auth, agent/session primitives, generic task API conventions, UI shell, audit and resource metadata in TensorPlatform.
- Put science-run/campaign lifecycle, solver execution state, coupling state and scientific next-action decisions behind TensorPhysics APIs.
- Keep paper/knowledge search as a reusable service only if it is domain-neutral and separately permissioned; scientific evidence linking and promotion rules should be part of the research workflow contract.

### P2 — Run registry is a useful seam, but it is currently metadata registration rather than evidence verification

**Evidence:** `docs/research-loop-integration-contract.md` explicitly documents that the registry does not yet validate every nested schema field, ingest artifacts, verify remote URI existence or prove numerical correctness.

This is an appropriate Platform responsibility **if kept narrow**: authenticate caller, enforce tenant boundary, validate manifest structure, register metadata and artifact references, retain lineage/audit, and expose readback. Scientific validation itself should be performed by TensorPhysics/independent validators; Platform must store the verdict and evidence without manufacturing it.

**Next integration gates:**
1. Server-side schema validation against the versioned contract.
2. Verify canonical manifest digest, run-ID conflict and idempotency behavior.
3. Verify artifact URI/size/checksum policy according to the configured storage adapter.
4. Test organization/tenant isolation on reads and writes.
5. Preserve failed/incomplete/not-run states; never promote them as validated datasets.
6. Test real TensorPhysics registration/readback end-to-end.

### P2 — Competing implementations and test ownership need a dependency-level audit

The tree and search results show multiple ship-performance and geometry-validation paths, and the repository includes a documented superseded-module archive. This is a risk of duplicated equations and divergent acceptance criteria. The next phase should trace imports, route registration, tests and production callers before deciding canonical implementations. Do not delete “superseded” code merely because a document says so; confirm reachability and external consumers first.

### P2 — The Platform repository contains substantial research output material

The current tree includes many `artifacts/` and `agent_work/` paths, including figures, reports, calculation outputs and task-specific bundles. This is not itself proof of a runtime defect, but it increases repository size and makes the platform's product boundary harder to understand. Classify each artifact as a small, reproducible fixture; generated output that belongs in release/CI artifacts or object storage; or durable documentation/evidence that belongs in the repo. This cleanup is separate from the ownership migration.

## Responsibility matrix (target state)

| Capability | TensorPlatform | TensorPhysics | Domain application / solver |
|---|---|---|---|
| Identity, authorization, tenants, audit | Own | Consume | Consume |
| Generic dataset/model catalog, metadata, lineage | Own | Publish/read through contracts | Publish/read through contracts |
| Generic storage adapters and artifact references | Own integration contract | Produce artifact metadata | Produce domain outputs |
| Generic agent/session/runtime integration | Own reusable primitives | Use for science workflow | Use if needed |
| Scientific task/campaign lifecycle | Persist generic metadata only | Own orchestration/state transitions | Supply domain plan/config |
| Solver selection, process lifecycle, compute backends | Generic resource interfaces only | Own coordination | Solver adapter/kernels |
| Multiphysics field exchange/coupling | No domain semantics | Own coordination/contracts | Solver-specific semantics |
| Scientific acceptance framework | Store verdict + evidence | Own workflow gate protocol | Provide domain-specific validators/criteria |
| Ship resistance/hydrostatics/propeller/seakeeping equations | No | Only orchestration/adapters, not monolithic equation library | Ship domain package / specialist solver |
| Ship benchmark values and ship-specific verification | Generic artifact/lineage storage only | Invoke and record verdict | Own benchmark + reference method |
| Dataset promotion to reusable training corpus | Own catalog/permissions/lineage | Assert validation and propose promotion | Supply validated outputs |

## Safe migration plan

### Phase 0 — Freeze the boundary
Adopt the responsibility matrix above as the review rule for new PRs. No bulk deletion or directory move in this phase.

### Phase 1 — Build a dependency/route map
For each candidate module:
- identify imports and route inclusion from `backend/main.py` and aggregate routers;
- identify direct imports in tests, scripts, notebooks and deployment;
- identify frontend calls and stable public endpoints;
- identify DB tables and ownership/tenant assumptions;
- record duplicate algorithms and which implementation is authoritative.

Deliver a module-level table with `keep / wrap / deprecate / migrate`, evidence, consumers, compatibility route and test requirements.

### Phase 2 — Extract a ship-domain package without breaking URLs
Start with the pure numerical modules (`src/ship_perf`, `src/ship_verification`) because they are more separable than the API routers. Move or mirror them into a dedicated ship-domain repository/package, add versioned imports and conformance tests, and keep thin compatibility wrappers in TensorPlatform during a deprecation window. Do not remove the old implementation until parity tests and consumer inventory pass.

### Phase 3 — Move science orchestration behind TensorPhysics
Move research-specific mission/campaign/execution transitions incrementally behind TensorPhysics APIs. TensorPlatform remains the identity, permissions, UI shell, generic resource metadata and run/dataset/model registry surface. Keep generic agent/session primitives in Platform only when they are not tied to scientific state transitions.

### Phase 4 — Enforce architecture with CI
Add an architecture test or lint rule that rejects:
- ship-specific equations or benchmark constants added under TensorPlatform;
- imports from specialist solver internals into Platform API code;
- scientific acceptance decisions being made from process exit status alone;
- duplicate canonical equation implementations without explicit ownership and conformance tests.

### Phase 5 — End-to-end verification
Run API compatibility tests, TensorPhysics registration/readback, tenant isolation, artifact integrity, domain conformance benchmarks and deployment smoke tests. A green unit-test suite alone is not sufficient evidence of scientific validity.

## Explicit non-goals for this audit

- No automatic mass move of all ship-related code into TensorPhysics.
- No deletion of existing routes or artifacts before dependency/consumer analysis.
- No claim that production deployment was tested.
- No claim that a passing software test proves physical correctness.

## Source links

- [TensorPlatform main entrypoint](https://github.com/cxs503/TensorPlatform/blob/main/backend/main.py)
- [TensorPlatform research router aggregate](https://github.com/cxs503/TensorPlatform/blob/main/backend/api/domain/tensor.py)
- [Ship-domain API modules](https://github.com/cxs503/TensorPlatform/tree/main/backend/api/domain)
- [Ship performance package](https://github.com/cxs503/TensorPlatform/tree/main/src/ship_perf)
- [Ship verification package](https://github.com/cxs503/TensorPlatform/tree/main/src/ship_verification)
- [Ecosystem integration contract](https://github.com/cxs503/TensorPlatform/blob/main/docs/ecosystem-integration.md)
- [Research-loop integration contract](https://github.com/cxs503/TensorPlatform/blob/main/docs/research-loop-integration-contract.md)


## Addendum: route reachability check (same audit pass)

A targeted follow-up search confirms that at least some ship-specific code is not merely dead code:
- `backend/api/domain/misc.py` is documented by the repository's own router-wiring audit as registering `ship_batch_router`, `ship_benchmark_router` and `ship_samples_router`; `backend/tests/test_ship_domain_routers.py` checks these router objects.
- `backend/main.py` directly mounts `api.domain.ship_vibration.router` under `/api/v1` inside a try/except fallback. The repository's `docs/audit/m1849_vibration_wiring.md` documents that it was previously unwired and was explicitly connected.
- `src/ship_perf/` is described by its package documentation as a reference implementation and conformance specification for ship-performance APIs; its own documentation says the live :8001 service source is outside this working tree. Therefore, treat this package as a verified/reference candidate with consumers to inventory—not as proof that the deployed API executes these exact functions.
- The repo's historical audits report multiple route/verification gaps and fixes. That history reinforces the need to trace each route from API registration through implementation and test, rather than infer production behavior from filenames or green unit tests.

This narrows the finding: **ship-specific APIs are actively wired in at least several places, while the live usage status of each numerical package still needs route-to-implementation and deployment verification.** The report deliberately does not claim every ship module is mounted or used in production.


## Addendum: quantified duplicate-physics risk

A second existing audit, `docs/audits/m1873_dup_kernel_divergence.md`, gives stronger evidence than a directory-level smell:
- Two implementations coexist for ship resistance / hull geometry: `backend/api/domain/{resistance_core,hull_geometry}.py` and `backend/services/{resistance_core,hull_geometry}.py`.
- Its reported paired audit finds the default wetted-surface fallback overestimates published reference values by **50.62% for KVLCC2** and **75.55% for DTMB-5415**; default-path differences between the two kernel families are reported at **33.61%–43.04%** for the tested hulls.
- The same report identifies a contract difference for low Reynolds number: one implementation raises an error while the other silently returns zero.
- The audit says its 12 regression assertions pass, but that only establishes that the *audit's expected behavior* is pinned; it does not automatically establish that the live API uses the correct implementation.

This is a **scientific integrity risk**, not merely a repository organization issue. Until the authoritative implementation and live route wiring are established, a successful API response or green tests may still be scientifically misleading.

Immediate action recommended in the dependency-map phase:
1. Identify the exact implementation called by each live resistance/stability endpoint.
2. Lock the authoritative equation source and validity envelope.
3. Make all public endpoints delegate to that source or a versioned adapter.
4. Add independent reference-value tests for published hull cases and explicit out-of-domain behavior.
5. Keep the audit non-destructive until live consumers and deployment source are confirmed.
