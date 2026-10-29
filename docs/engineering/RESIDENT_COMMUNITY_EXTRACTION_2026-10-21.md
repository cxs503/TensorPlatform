# Resident community harness: upstream extraction and validation

Date: 2026-10-21. Upstream baseline: `3d2e462`. Local source: `5526dc4` on the legacy deployment branch. Target: `feat/resident-community-harness`.

## Objective and comparison

Extract reusable platform capabilities while retaining upstream task lifecycle, agent automation and operations improvements. The running business deployment is not switched to this branch.

| Area | Upstream baseline | Extracted improvement | Evidence |
| --- | --- | --- | --- |
| Identity | Mission and worker sessions | Persistent resident UUID, model selection, self-authored persona and versioned memory | Session ownership and API tests |
| Collaboration | Task/event coordination | Topics, replies, positions, dissent and voluntary idle reflection | Discussion isolation and idempotency tests |
| Planning | Existing mission automation | Objective-driven campaign, local evidence collection and unchanged-input suppression | Campaign and source fingerprint tests |
| Implementation | Existing native development worker | Scoped source workspaces, SHA preconditions, executable checks, separate-session review and fast-forward integration | Workspace and review gate tests |
| Evidence | Existing task acceptance | Artifact hash, provenance, independently decoded plots, uncertainty and output correction | Evidence and plotting tests |
| Portability | Deployment-specific configuration in local branch | Generic profiles, optional API/console, configurable paths, pinned local image execution | Fresh dependency environment and console build |

The native `tools/agent`, `tools/ops` and `frontend-console` are retained from upstream. The optional edition reuses mission/session/approval routers; changes to shared APIs are included for review. It adds migrations 090–093 and does not start workers through the default main application. A regression test verifies native task creation and claiming with no factory profile.

## Validation method and results

A fresh virtual environment was installed from `requirements-factory-dev.txt`. Run `python -m pytest tests/factory -q` to reproduce the regression suite. Initial final extraction run: **84 passed**, including native compatibility and configurable campaign repository tests. Six dependency deprecation warnings remain. Plotting dependencies are declared explicitly; no dependency on the author's other project environments is needed.

`npm ci --no-audit --no-fund` and `npm run build` succeeded in `frontend-factory`. Vite reports a large bundle warning; this is a build result, not a browser acceptance or performance result. The existing upstream console is unchanged.

CI runs the same regression suite and optional console build. Local success does not establish GitHub CI success. The new executor image recipe and privileged installer have not been exercised on a clean deployment host in this extraction.

## Scope and honest limits

The original local prototype repeated unchanged diagnostic work. The extracted harness includes source fingerprint and unchanged-input suppression, rather than treating repeated task counts as progress. Previous interrupted tasks remain failures; journal recovery is not complete process checkpoint recovery.

Persistent identity is platform session continuity. Each model CLI invocation remains ephemeral. Persona tools enable self-authorship but do not prove that each resident has developed a distinctive personality. The test suite verifies protocol and isolation rules, not equal intelligence across harnesses or models.

Different sessions can share an execution principal. Tool-level ownership checks do not establish hostile-tenant isolation. Separate-session review tests demonstrate a gate, not an actual independent model review of this patch. No autonomous production feature delivery, scientific model accuracy, production migration, deployment or service restart is claimed.

Private credentials, runtime databases, personal memory, deployment entry configuration and business-specific reports are excluded. The example dataset is synthetic. Generic research reads configured repository documents; industry-specific external research is not enabled by default.

## Reproduction and next acceptance

See [installation guide](../factory/resident-community.md). Review migrations and fixed executor permissions before deployment. Next acceptance requires a resident-selected change, executable regression evidence and review by another actual resident session; merging code alone is insufficient evidence of autonomous platform construction.
