# Platform Governance Gates (p338w3-n07)

Three fail-closed guards for platform accounting, domain-neutral by
construction: capability slugs, thresholds, report bodies and mission states are
injected data; nothing here interprets what a capability means.

| Guard | Module | Contract |
|---|---|---|
| Capability ledger | `evidence.py` + `ledger_guard.py` | 6-requirement completeness; `>= 0.8` promotes to REAL, `< 0.5` demotes to WITHHELD, contradictions demote to MOCK, invalid input never promotes; independent verification and digest validity are per-requirement hard gates |
| Report fingerprint | `report_fingerprint.py` | `report_id <-> mission_id <-> body_sha256 <-> metadata_sha256` four-way binding; duplicates and mismatches close the gate |
| Status drift | `status_drift.py` | campaign-node status vs mission final state; detects `blocked`-but-merged, emits convergence routes; orphans are reported, never guessed |

## HTTP

Mounted by the platform at `/api/v1/platform-governance`:

```
GET  /health
POST /ledger/scan
POST /report-gate/check
POST /drift/scan
```

Every response carries `latency_ms` measured inside the handler.

## Reproduce the acceptance

```bash
python3 -m pytest tests/platform_governance_gates -q
python3 -m tools.platform_governance_gates.acceptance
```

The runner regenerates every J1-J7 number under
`artifacts/platform_governance_gates/` and the paper-style report at
`docs/P338W3_N07_platform_governance_gates.md`.

## Design notes

* The six requirements are the four contract dimensions (independent
  verification, benchmark artifact, sha256 verification, cross consistency)
  plus provenance completeness and evidence depth. With only four binary
  checks, a `0.8` threshold degenerates to "perfect score only".
* `independent_verification` and `sha256_verified` are hard gates on their own:
  a 5/6 score cannot paper over a missing independent verification.
* The audit log reuses the existing append-only sha256 chain
  (`tools.capability_auto_audit.append_only_log`); every decision, including
  HOLD, records timestamp, triggering evidence digest and completeness delta.
* Status convergence is emitted as a plan (`updates`); applying it to
  `campaign_nodes` is the scheduler's job, so this package needs no DB write
  permission.