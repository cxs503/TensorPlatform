# Task salvage — circuit-breaker terminal tasks → machine-readable hand-over

Campaign node `p199w1-N06-task_salvage` (mission 9398). This is the capability
contract; the machine-checked run is `artifacts/task_salvage/acceptance.json`,
the pre-registered design is `artifacts/task_salvage/preregistration.json`, the
deviation log is `artifacts/task_salvage/deviations.json`.

## 1. What problem this solves (and what it does not)

A task can terminate in a **circuit-breaker state** — `llm_error` (model/runtime
error), `timeout` (wall-clock ceiling, recorded by the ledger as
`wall_exhausted`), `budget_exhausted` (token/call budget). Such a task is not a
"no-op": its worktree holds files, its acceptance list holds open obligations,
and its record holds the stop reason. Today that material is invisible — not
committed, therefore not in the ledger; never accepted, therefore not evidence —
and the next wave re-derives it from zero.

This package turns the remains into **material with provenance** while refusing,
in three explicit places, to turn traces into achievement:

* a file is inheritable only if it passes the three-gate sift;
* an obligation is never marked done — only `EVIDENCED` (a candidate to read) or
  `OPEN` (what the continuation seed is dispatched for);
* `salvage.json` carries no node verdict; nothing here moves a campaign status.

Sibling package `tools/salvage` (plan 250) packages a *dead node's* worktree
byte-exactly with a hash-chained index. This package handles the *task-level*
hand-over: terminal taxonomy, three-gate sift, residual list, fingerprint,
continuation seed, and the three backtest gates.

## 2. Interface

```
python3 -m tools.task_salvage.cli collect   --limit 20            # live records + worktrees → frozen corpus
python3 -m tools.task_salvage.cli collect   --offline             # rebuild from the frozen record snapshot
python3 -m tools.task_salvage.cli build     --mission-id 9311     # one salvage.json + continuation.json
python3 -m tools.task_salvage.cli backtest                        # recompute the machine gates offline
python3 -m tools.task_salvage.cli figures                         # render the report figures
python3 -m pytest tools/task_salvage/tests -q                     # unit + zero-domain guard
```

Modules: `taxonomy` (terminal buckets) · `sift` (three gates) · `salvage`
(`salvage.json`) · `continuation` (seed sub-task) · `backtest` (gates + exact
statistics) · `cli` (orchestration) · `figures` (plots) · `make_report`
(paper-grade report assembled from the artefacts).

Artefacts under `artifacts/task_salvage/`: `corpus.json` (frozen input snapshot
with sha256), `records/{failed,succeeded}.json` (the live-query projection, so a
network outage cannot destroy reproducibility), `missions/<id>/{salvage.json,
continuation.json}`, `acceptance.json`, `figures/*.png`.

## 3. The three gates

| gate | question | failure mode it catches |
|---|---|---|
| `parse` | is the file structurally readable? (`.py` → `ast.parse`, `.json/.toml` → parser, text → strict UTF-8, binary → magic) | half-written / truncated / corrupt output |
| `smoke` | does the module import once, package-aware, in an isolated subprocess in a temp cwd with a bounded timeout? | module-level breakage, missing dependency, relative-import breakage |
| `structural` | does the path map into the destination checkout layout? | paths that address something outside the repository, or land in a root the destination does not have |

Rules that matter more than the checks themselves:

* **a skipped smoke is a failure**, never a silent pass (`smoke_not_run`);
* the smoke *file subset* is chosen by sorted path, so the same inputs always
  smoke the same files (a thread-arrival-ordered budget made `salvage.json`
  irreproducible in the first build of this node — see `deviations.json` D5/D6);
* smoke results are retried once on timeout, because a contention-induced
  timeout would otherwise make the verdict random;
* residue (`__pycache__`, `node_modules`, `.scratch`, editor droppings, oversize
  files) is dropped before the gates run, not "blocked" after.

## 4. `salvage.json`

```
task          id / repo / status / model / claimed_by / headline
terminal      bucket, in_scope, reason_code, raw_stage, evidence_path
inheritable[] path, class, bytes, sha256, per-gate verdicts     (sorted)
blocked_files[] …
residual[]    item, item_sha256, source, state(EVIDENCED|OPEN), evidence[]
state         INHERITABLE | BLOCKED
blocked       {code, detail} | null
continuation  seed sub-task (see below)
fingerprint   sha256 of the canonical payload (no clock, no host, no absolute path)
```

`BLOCKED` codes: `no_worktree`, `no_candidates`, `parse_failed`,
`smoke_failed`, `structural_failed`.

## 5. `continuation.json`

`subtask_id = cont-<parent mission>-<first 12 hex of the payload's provisional
sha256>` — the same task salvaged twice yields the same seed id, so a dispatcher
cannot duplicate a re-dispatch. The seed carries `inputs` (path + sha256 +
bytes), `do_not_redo`, `residual` (OPEN items only), `budget_hint`
(`inherited_bytes`, `residual_open`, `retry_scale`) and the provenance link back
to the salvage fingerprint.

## 6. Backtest gates

* **salvage rate** — every in-scope terminal task must yield a written
  `salvage.json` with a decidable state.
* **consistency / false positives** — the gate verdict is compared per file with
  an independent oracle implementation (subprocess `compile()` instead of
  in-process `ast.parse`; package-aware import through a different loader;
  live `listdir` of the checkout instead of the configured root set). This
  detects implementation drift, not semantic truth.
* **idempotency** — every task is built twice; fingerprints and bytes must be
  identical.
* **replay gain** — paired A/B over campaign-node families that contain both a
  circuit-broken task and a succeeded re-dispatch: required set = what the
  successor delivered, inherited set = dead-task files that pass the sift *and*
  land in the required set, capacity = files the dead task had actually authored
  (measured). Fisher exact (as specified) plus exact McNemar (paired-correct).

## 7. Honest limits

* The replay judge is an **accounting model over measured sets**. It can show
  that inherited material closes a delivery gap; it cannot show that a real agent
  would author the same files. In the corpus of this node the measured overlap
  between dead-task material and the accepting run's deliverable set is close to
  zero, so the replay gate reports an honest FAIL with that reason — a real
  re-dispatch experiment (pre-registered, same model and budget, seed vs no seed)
  is the next step, not a re-tuned threshold.
* Consistency between two implementations does not prove either is correct.
* Worktree retention biases the corpus: tasks whose worktree was cleaned cannot
  be measured, so every rate here is conditional on retention and is reported
  with the retention counts.
* The smoke gate executes foreign code. It runs in a temporary cwd with a bounded
  timeout and never writes into the scanned worktree, but it is still code
  execution: untrusted worktrees need a container, not this gate.