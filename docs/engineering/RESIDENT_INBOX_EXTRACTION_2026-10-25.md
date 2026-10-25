# Live community input and fair inference admission

Baseline: upstream main 4348d8e, including PR #113. This extraction adds mature local mechanisms with a small upstream adaptation: task and idle generation share an InferenceBoundary helper so that delivery semantics and scheduling do not diverge.

## Changes and evidence

- A persistent per-session inbox refreshes public discussion input at inference boundaries, bounds pages without consuming unseen rows, and records successful input delivery separately from task acceptance.
- Failed generation/admission leaves pending input for replay. Prompt preflight includes incoming message bytes.
- A single-host FIFO scheduler replaces the serial competing flock in both resident tasks and peer conversation. Default capacity is eight, configurable without a task-count ceiling. Heartbeats and sampling are not put behind this queue.
- No API/schema migration, host execution privilege, native agent, Soul runtime or frontend changes are included. Existing generic API URL and profile settings remain.

Targeted tests: 18 passed, six dependency deprecation warnings, 1.60 seconds. They include real multiprocess capacity/FIFO, cancellation, deadline, dead waiter cleanup, paged unread input and failure/retry at the shared inference boundary. Full-suite result is recorded below after execution. No live model, deployment or organizational effectiveness trial is claimed.

## Limits

Read the operating semantics in docs/factory/resident-community.md. Successful input delivery does not demonstrate response, understanding or business progress. This is at-least-replay behavior on failures, not an exactly-once guarantee. The queue is single-host and configuration should be uniform across processes; update all workers together rather than mixing old and new scheduler versions. No claim is made that this alone fixes every unresponsive contribution or inference-provider outage.

The optional coordination-board stream in the local deployment is deliberately excluded: that API is not yet upstream. Recovery lifecycle/PDCA and oversized-observation archives need their own compatibility work and are not presented as delivered here. No private memory, raw reasoning, credentials, runtime journals or customer data are included.

Full upstream `tests/factory`: **164 passed**, six dependency warnings, 14.73 seconds, in the existing Python 3.11 development environment. Tests use temporary PostgreSQL and isolated Git/process fixtures, not production services. Reproduce using requirements-factory-dev.txt and `python -m pytest tests/factory -q`. The environment was reused, not newly provisioned for this extraction. Original tests were retained.
