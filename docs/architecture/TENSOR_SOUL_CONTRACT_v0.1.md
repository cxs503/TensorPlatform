# Tensor Soul Contract v0.1 (Proposal)

Status: **proposal; not an implementation claim**  
Owner: TensorPlatform  
Related: [RFC #72](https://github.com/cxs503/TensorPlatform/issues/72)

## Purpose

Define a model-independent contract for an Agent that persists across sessions and can later host optional learned/neural memory. Reuse TensorPlatform's existing resident-community, task, event, and memory mechanisms where possible; do not create a parallel task engine.

## Design invariants

1. `agent_id` is stable across sessions and model/provider changes. A `session_id` identifies one interaction/work episode, not the Agent itself.
2. Task state, execution attempts, model calls, and memory events have distinct identities and lifecycles.
3. Durable facts and workflow state remain inspectable and auditable. Neural state is optional and never the sole copy of critical parameters, permissions, checkpoints, or scientific evidence.
4. Model output is an observation, not automatically a verified fact or learning label.
5. Shared learning requires provenance, sensitivity screening, validation status, and a versioned promotion decision.
6. A provider/model switch must not change Agent identity or erase durable state.
7. Claims about deployment or scientific validity require evidence from the corresponding environment and validator.

## Core entities

| Entity | Required meaning | Persistence |
|---|---|---|
| `AgentIdentity` | Stable ID, role, goal/constraint revision, policy references, created/updated timestamps | Long-lived |
| `SessionState` | Session ID, agent ID, current task, active plan reference, status, checkpoint reference | Per session; resumable |
| `MemoryEvent` | Event ID, agent/session/task references, event type, timestamp, source, payload or artifact reference, sensitivity, verification status | Append-only event history |
| `NeuralMemoryState` | State/artifact reference, encoder/model compatibility, version, checksum, update provenance | Optional and versioned |
| `LearningCandidate` | Source event IDs, proposed generalization, scope, evidence, evaluation result, promotion status | Until accepted/rejected |
| `ModelBinding` | Provider/model identifier, adapter configuration, capability limits; never the identity key | Versioned configuration |
| `EvidenceRecord` | Code SHA, task/run/config IDs, environment reference, validator/version, metrics, artifact hashes, evidence level | Durable |

## Runtime interface semantics

These names describe a proposed interface, not existing callable methods.

- `observe(event)`: validate and persist a new observation with provenance; deduplicate by idempotency key.
- `recall(task_context)`: return relevant memories with source references, scope and verification status.
- `prepare(task)`: build a bounded, task-specific model input from durable state and selected memories. Do not dump the entire transcript by default.
- `decide(observation)`: produce a proposed next action and its required capability/permission; policy enforcement remains outside the LLM.
- `learn(feedback)`: update individual state only when the learning signal and source are recorded. Feedback may be delayed until tool execution or validation finishes.
- `consolidate()`: propose generalizable experience; never silently promote raw model output to shared knowledge.
- `checkpoint()` / `restore(checkpoint)`: persist and recover a consistent state snapshot; use versions, checksums and idempotent transitions.

## State and trust model

Memory/event status should distinguish at least:

- `observed`: recorded, not independently verified;
- `validated`: passed a named validator with recorded version and evidence;
- `rejected`: disproven, unsafe, or out of scope;
- `superseded`: replaced by a newer version but retained for audit.

A memory can be useful without being a fact; relevance and truth are separate dimensions. Shared-memory promotion must consider both.

## Initial implementation boundary

### Required for MVP

- Stable Agent identity independent of session and model.
- Persistent session/task references and checkpoint/restore semantics.
- Append-only, source-linked memory events.
- Bounded recall and context preparation.
- Model binding switch without loss of durable state.
- Tests for cross-session continuity, process restart, idempotency and tenant isolation.

### Optional experiment after the baseline

- Small external neural memory encoder/state updater, behind a feature flag.
- A/B evaluation against no-memory and current structured/text memory baselines.
- Versioned learned state with rollback and compatibility metadata.

### Explicitly out of scope for v0.1

- Directly modifying hidden states of closed LLM APIs.
- Unrestricted per-request gradient updates to shared weights.
- Treating model-generated prose as ground truth.
- Replacing exact workflow state, numerical parameters or evidence artifacts with latent vectors.
- Claiming improved intelligence without controlled evaluation.

## Acceptance tests

1. One Agent continues across two independent sessions with identity, goal, and unfinished task preserved.
2. After a test-process restart, the latest committed checkpoint restores consistently.
3. Switching between two configured model bindings does not change the Agent ID or lose durable state.
4. Duplicate events do not duplicate side effects; conflicting writes are detected.
5. Session/tenant-private memory cannot be retrieved by another unauthorized Agent.
6. Unverified or rejected content is not silently promoted to shared memory.
7. An optional neural-memory experiment reports results against fixed baselines using the same task set and budget. Unrun metrics are explicitly marked `NOT RUN`.

## Evidence reporting

Every implementation/test report must include repository, branch, exact commit SHA, commands actually run, exit codes, and links to logs/artifacts where available. Unit tests, service deployment checks, and scientific validation are separate evidence categories.
