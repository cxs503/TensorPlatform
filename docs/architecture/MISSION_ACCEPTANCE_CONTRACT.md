# Mission Acceptance Criteria Contract

Mission acceptance criteria are part of the original work contract, not agent-authored completion metadata.

## Lifecycle

1. Create/dispatch validates, canonicalizes and persists criteria with a SHA-256 fingerprint and `mission_created` provenance.
2. Claim returns the exact snapshot and digest to the worker.
3. Start verifies the snapshot before transitioning to `running`. Legacy rows without a fingerprint are frozen at start and marked `legacy_start`, never represented as predeclared.
4. Completion copies the original criteria, digest, timestamp, version and provenance into the approval evidence. Agent-supplied completion content cannot replace this contract.
5. TensorBench verifies the digest and independently checks whether evidence satisfies each criterion.

## Criterion shape

Each criterion is a JSON object with non-empty `metric`, `target`, and `tolerance`. Target and tolerance are non-empty strings or finite numbers. An optional `criterion_id` is preserved; otherwise deterministic IDs `C001`, `C002`, ... are assigned. Other JSON-safe domain metadata is preserved and included in the fingerprint.

An empty list is permitted for compatibility with simple/legacy tasks but is explicitly `not_declared`; it is not a passing acceptance contract.

## Provenance

- `mission_created`: criteria were stored and fingerprinted when the mission was created/dispatched.
- `legacy_start`: a legacy mission's stored criteria were fingerprinted only at start.
- `legacy_unfrozen`: a legacy mission has not yet reached the start compatibility path.

The database trigger prevents changes to criteria, digest, freeze timestamp, version or provenance after freezing. SHA-256 proves snapshot integrity only; it does not prove scientific validity or achievement. TensorBench remains responsible for independent evidence collection and verification.
