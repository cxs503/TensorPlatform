# `tools/pulse` — saturation pulse metrology (IN15-O1)

Autoregressive fuel #1 ("measure the scale"): a reproducible, testable
measurement of **how saturated the agent fleet is**, written into the
`saturation_pulse` table and readable as an ASCII trend.

```
tools/pulse/
├── saturation_pulse.py    # collector + CLI (dry-run | --commit | --ensure-schema)
├── pulse_report.py        # trend CLI (sparklines + table)
└── sql/001_saturation_pulse.sql   # the mission's single schema action (idempotent)
tests/pulse/test_saturation_pulse.py  # 24 tests, 22 pure + 2 live-DB
└── acceptance_evidence.py # rebuilds every acceptance artifact in one command
```

## What is measured

| quantity | source (read-only SELECT) | definition |
|---|---|---|
| `online` | `factory_resident_workers` ∪ `tensor_dev_missions` | enabled workers whose `heartbeat_at` is within the freshness window (default **300 s / 5 min**) **plus** claimants of missions whose own heartbeat is ≤ 150 s |
| `busy` | missions | claimants of a live active mission, plus workers self-reporting `state='running'` / `current_mission_id` on an active mission |
| `queued` | `tensor_dev_missions` | missions in status `queued` (the releasable backlog) |
| `idle` | derived | `online − busy` |
| `running_missions` | `tensor_dev_missions` | status `running` / `claimed`, fresh or not |
| `stale_claims` | missions | active missions whose heartbeat is older than 150 s (stuck claims) |
| `held` | `campaign_nodes`, `campaign_plan_nodes` | DAG nodes in status `held` / `blocked`, identities stored for release accounting |
| `released` | derived | ids held by the *previous* sample and no longer held now |

The union in `online` is not cosmetic. On 2026-11-07 all 54
`factory_resident_workers` rows were `state='idle'` with `current_mission_id IS
NULL` while ~40 missions were `running` and heartbeat-fresh, so a worker-table
only reading reports `busy = 0` for a fully loaded fleet. Counting both sides
keeps `busy ⊆ online` true (pinned by a test) and still exposes the
reconciliation gap.

## Storage contract (add-only)

`saturation_pulse` already exists in the deployed database:

```
ts | online | busy | held | released | note
```

IN15-O1 forbids schema surgery, so the collector **does not alter it**:

* `online / busy / held` — the three headline numbers;
* `released` — comma-separated ids released since the previous sample;
* `note` — the full machine-readable snapshot as single-line JSON
  (`v=1, window_s, online, busy, queued, idle, running_missions, fresh_workers,
  skewed, stale_claims, held_ids, released_ids, missing_tables`).

Legacy free-text notes (e.g. `online=43 busy=34 queued=2 idle=7 held16=11`) are
tolerated by the reader and reported as `source=legacy`; no `queued` value is
invented for them. `--ensure-schema` runs `sql/001_saturation_pulse.sql`
(`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`) and then verifies
the deployed column set — a drift raises `PulseSchemaError` instead of silently
writing into an unexpected shape.

## CLI

```bash
# one sample, no write (default; safe to run anywhere)
python3 -m tools.pulse.saturation_pulse

# persist one sample
python3 -m tools.pulse.saturation_pulse --commit

# idempotent DDL + contract check, then sample
python3 -m tools.pulse.saturation_pulse --ensure-schema --dry-run

# trend over the last 20 samples
python3 -m tools.pulse.pulse_report --limit 20
python3 -m tools.pulse.pulse_report --limit 20 --json
```

Exit codes: `0` ok, `2` rejected input (negative window, bad `--now`, bad
`--limit`), `1` runtime/connection error. DSN precedence:
`--dsn` → `$SATURATION_PULSE_DSN` → `$DATABASE_URL` →
`host=/var/run/postgresql dbname=$TP_PG_DB user=$TP_PG_USER`.

Suggested deployment cadence: one `--commit` every 5 minutes from the
supervisor loop (matches the freshness window); the trend CLI needs no
scheduler.

## Safety properties

* read phase runs inside `BEGIN` with `connection.read_only = True` and issues
  **SELECT statements only** — no `FOR UPDATE`, no `LOCK TABLE`, no writes;
* while a snapshot is open the session holds only `AccessShareLock` on the pulse
  tables, so concurrent dispatcher writes are **not blocked** (live test);
* the write phase is a single `INSERT`;
* `--dry-run` and `--commit` share one code path, so the printed snapshot is
  identical in both modes (test asserts dict equality and that dry-run issues no
  INSERT);
* negative/absurd inputs raise `PulseInputError` (exit 2) — never clamped.

```bash
pytest tests/pulse -q     # 24 passed (2 live-DB tests skip without PostgreSQL)
```