# Mission Acceptance Criteria Contract

## Purpose

A mission's acceptance criteria are part of its original work contract, not agent-authored completion metadata. The platform must retain the exact criteria declared before execution and make any missing or legacy provenance explicit to downstream evaluators.

## Lifecycle

1. **Create / dispatch:** validate and canonicalize criteria, assign stable `criterion_id` values when omitted, calculate SHA-256 over canonical JSON, and persist the snapshot with `freeze_source=mission_created`.
2. **Claim:** return the frozen criteria and fingerprint to the worker. The worker must use these as the task's acceptance contract.
3. **Start:** verify the stored fingerprint before changing the mission to `running`. Legacy missions created before this schema may be frozen at start, but are marked `legacy_start`; this does not prove that the criteria existed before work began.
4. **Complete:** copy the criteria, digest, freeze timestamp, version, and provenance into the completion/approval evidence. Agent-supplied summary fields cannot replace the stored contract.
5. **Evaluate:** consumers must verify the digest and must not treat missing criteria or `legacy_start` criteria as proof of predeclared acceptance.

## Criterion schema

Every criterion must be a JSON object with:

- `metric`: non-empty string identifying what is measured
- `target`: non-empty string or finite number describing the target
- `tolerance`: non-empty string or finite number describing allowed deviation
- `criterion_id`: optional stable identifier; generated as `C001`, `C002`, ... when omitted

The full object is preserved, with canonical IDs, so domain-specific metadata can travel with the contract. An empty list remains legal for backward-compatible/simple engineering tasks, but is explicitly reported as `not_declared`; it is not an evaluable success condition.

## Immutability and provenance

Migration `111_mission_acceptance_criteria_freeze.sql` adds the JSONB snapshot, digest, version, freeze timestamp, and freeze source. A database trigger rejects changes to any of these fields after freezing. The source is one of:

- `mission_created`: criteria were persisted and frozen in the mission creation/dispatch transaction
- `legacy_start`: an older mission had no frozen snapshot; the currently stored value was fingerprinted at start
- `legacy_unfrozen`: an older mission has not yet passed through the start-time compatibility path

A SHA-256 match proves snapshot integrity, not that a criterion is scientifically valid or that its target was achieved. TensorBench remains responsible for independent evidence collection and verification.

## API surfaces

- `POST /tensor/missions` accepts and persists `acceptance_criteria`
- `POST /tensor/missions/dispatch` accepts criteria for child missions
- `POST /tensor/missions/claim` returns criteria and digest to the worker
- `POST /tensor/missions/{id}/start` verifies/freezes the snapshot and returns it
- `GET /tensor/missions/{id}` and list responses expose snapshot/provenance
- `POST /tensor/missions/{id}/complete` copies the original snapshot into approval evidence

## Compatibility

Existing missions are not retroactively represented as having predeclared criteria. They retain an empty default and `legacy_unfrozen` provenance until started after migration, at which point the then-current stored value is fingerprinted as `legacy_start`. Downstream evaluation should report these as legacy/inconclusive rather than silently upgrading their trust level.
