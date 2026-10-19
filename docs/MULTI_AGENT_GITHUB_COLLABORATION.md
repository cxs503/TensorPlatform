# GitHub-Based Multi-Agent Collaboration Protocol

**Status:** v1 proposal for implementation. GitHub is the shared coordination ledger; it is not itself a remote command channel.

## 1. Goal and participants

This protocol coordinates agents that have different execution capabilities without requiring them to share a chat transcript or direct machine access.

- **Coordinator / task owner:** defines the objective, acceptance criteria, scope, risk, and deadline in a GitHub Issue.
- **Code agent (ChatGPT/GitHub connector or Claude Code):** inspects the current repository, implements changes on its own branch, commits, and opens or updates a PR.
- **Deployment/test agent:** runs tests on the authorized test host, deploys only approved revisions, captures logs and evidence, and reports results back to the same Issue/PR.
- **Reviewer:** checks code, evidence, acceptance criteria, and risk before merge.
- **GitHub Actions:** runs portable CI checks and records status where repository permissions and runners allow it.

One agent can hold multiple roles, but code authoring and validation evidence should remain distinguishable.

## 2. Single source of truth

The GitHub Issue is the task record; the PR is the proposed code change; CI and test reports are the evidence record. Chat messages are not the authoritative task state.

Every task must identify:
- stable task ID (Issue number);
- repository and target branch (normally `main`);
- exact goal and non-goals;
- acceptance criteria written as observable checks;
- expected test commands and environment;
- risk tier and required approval;
- artifact/log links and commit SHA for any test result.

Never put credentials, private keys, tokens, production data, or sensitive server details in Issues, PRs, commit messages, or logs.

## 3. State machine

Use labels (create them if absent) to represent one active state per task:

`agent:ready` → `agent:claimed` → `agent:implementing` → `agent:review` → `agent:testing` → `agent:verified` → `agent:done`

Alternative terminal states: `agent:blocked`, `agent:failed`, `agent:needs-human`.

- An agent claims work by commenting with role, UTC timestamp, branch name, and intended scope, then applying the claimed/implementing label.
- Before editing, it fetches the latest target branch and checks open PRs/issues for overlapping files or scope.
- One task should have one designated code owner. Other agents review or test unless the owner explicitly delegates a separate file/scope.
- If scope overlaps, agents coordinate in the Issue before pushing conflicting edits.
- State changes must include a short evidence-backed comment; do not label work verified based on an agent's assertion alone.

If labels cannot be created or are unavailable, use explicit comments with the same state names.

## 4. Branch and PR rules

1. Do not commit directly to `main` for routine development.
2. Use one branch per task, e.g. `agent/issue-123-short-name` or `claude/issue-123-short-name`.
3. Keep a PR focused on one Issue and link it with `Closes #123` only when the PR fully resolves that task.
4. Include changed behavior, tests actually run, test environment, known limitations, and rollback notes in the PR.
5. Rebase/merge the latest target branch and rerun relevant tests before merge.
6. Do not force-push shared branches or overwrite another agent's commit. If a conflict occurs, stop and coordinate.
7. Merge only after required CI checks pass and the risk-appropriate review/test evidence exists.

## 5. Remote test and deployment handshake

A GitHub comment does not remotely execute a command by itself. The deployment agent must be running a poller/webhook listener or respond manually to the Issue/PR.

Recommended handshake:
1. Code agent opens PR and posts `READY_FOR_TEST` with exact PR number, head SHA, test command, expected result, and any required fixtures.
2. Deployment agent verifies the repository and SHA, checks out that exact SHA in an isolated worktree/container, and runs the declared tests.
3. Agent posts `TEST_RESULT` containing SHA, UTC start/end, host/environment identifier (non-sensitive), commands, exit code, summary, log/artifact links, and any deviations.
4. The result is attached to the tested SHA. If the PR head changes, the prior test result is stale and must not verify the new revision.
5. Deployment to a shared or production service requires an explicit approval step unless the owner has pre-authorized that specific low-risk environment and action.

Never execute arbitrary shell commands copied from an Issue comment without validating the command against an allowlist and applying a least-privilege service account.

## 6. Evidence levels

Report these separately; never collapse them into one green status:

- **L0 — Static:** formatting, lint, type/schema checks.
- **L1 — Unit:** unit tests pass.
- **L2 — Integration:** actual services or adapters exercised.
- **L3 — Deployment smoke:** exact tested SHA deployed in the target test environment and health checks passed.
- **L4 — Scientific validation:** a versioned benchmark, numerical tolerances, reference data, conservation/error checks, and result artifacts are reviewed.

A deployment smoke test is not scientific validation. A successful solver exit is not a validated scientific result.

## 7. Concurrency, idempotency, and recovery

- Use Issue number + repository + target SHA as the task identity; record each attempt separately.
- A test result is idempotent only for the same tested SHA, environment fingerprint, command, and relevant configuration.
- A changed SHA or test configuration requires a new result record.
- Before retrying a failed task, classify the failure as code, environment, infrastructure, flaky test, or unknown.
- Retry known transient failures a bounded number of times. Escalate unknown failures rather than looping indefinitely.
- If the deployment agent goes offline, the Issue remains queued/claimed with a heartbeat timestamp. After a configurable lease expires, the task can be reclaimed; do not assume an in-flight deployment succeeded.
- Keep an append-only record of claim, start, completion, failure, and approval events.

## 8. Security boundaries

- Use separate least-privilege GitHub credentials for code and deployment where possible.
- The deployment runner should accept only repository/branch/SHA allowlists and fixed test profiles, not arbitrary commands.
- Secrets stay in host secret stores or GitHub Actions secrets/environments, never in the repository.
- PR code from untrusted branches must not receive production secrets.
- Destructive operations, production deployments, external paid compute, and data deletion require explicit approval.
- Log enough for audit without printing secrets or personal data.

## 9. Minimal implementation roadmap

### Phase A — GitHub protocol (this document)
Use Issues, branch-per-task, PRs, commit-SHA-bound test reports, and explicit state comments. This can start immediately without a new service.

### Phase B — Repository automation
Add issue/PR templates, CI checks, and labels. Add a machine-readable task-result schema. Prefer GitHub Actions for portable CI.

### Phase C — Test-host bridge
Install a small poller or webhook receiver on the authorized test host. It claims eligible tasks, validates allowlisted commands, runs an isolated checkout at the exact SHA, and posts signed/structured result metadata. It must support cancellation, timeouts, leases, and a safe offline state.

### Phase D — TensorPlatform integration
Register task/run/test metadata with actor identity, repository, commit SHA, environment fingerprint, artifact hashes, and evidence level. GitHub remains the source for code review; TensorPlatform becomes the searchable cross-repository audit and research asset plane.

## 10. First pilot

Pilot with one non-production TensorPhysics or TensorDEM test task:
1. Open a GitHub Issue with exact acceptance criteria.
2. Let one agent implement on a branch and open a PR.
3. Have the second agent test the exact PR head SHA on the test host.
4. Post a structured result and logs to the PR.
5. Merge only after review and the agreed evidence level.
6. Repeat once with a deliberate failing test to verify failure reporting and bounded recovery.

The pilot is complete only when a person can reconstruct who changed what, which exact code was tested, where it was tested, what happened, and why the result was accepted.
