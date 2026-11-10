# Salvage capability — dead-mission output → inheritable evidence

Campaign node: `p250-n01-dead-node-salvage-maturity` (mission 9223, plan 250).
This document is the capability contract; the machine-checked acceptance run is
`artifacts/salvage/acceptance.json` and the per-mission start points are rendered
into [`RESUME_START_POINTS.md`](RESUME_START_POINTS.md).

## 1. Why this belongs in TensorPlatform

Nothing here knows about ships, ice, fluids or structures. The module consumes a
git worktree plus a list of acceptance strings and produces a reproducible
evidence package. Domain words enter only as opaque payload (acceptance criteria
quoted from campaign metadata, file contents quoted from the dead worktree), never
as vocabulary in code, thresholds or tests — the pytest fixtures use synthetic
names (`alpha`, `widget`, `m1001`). Verified by construction: `tools/salvage/*.py`
contains no domain term and the classifier is a pure function of the path.

The problem it solves is organisational rather than numerical: a dead mission
(`agent_lost`, infra retries exhausted) leaves uncommitted work in its worktree.
Today that work is invisible: it is not committed, so it is not in the ledger; it
was never accepted, so it is not evidence; and the next wave re-derives it from
scratch. Salvage turns it into *material with provenance* while explicitly
refusing to turn traces into achievement.

## 2. Interface

```
python3 -m tools.salvage.cli replay        --plan-id 249 --out artifacts/salvage
python3 -m tools.salvage.cli negctl        --out artifacts/salvage/negative_control
python3 -m tools.salvage.cli acceptance    --plan-id 249 --out artifacts/salvage
python3 -m tools.salvage.cli resume-report --out artifacts/salvage
python3 -m tools.salvage.cli figures       --out artifacts/salvage
python3 -m pytest tools/salvage/tests -q
```

Modules: `gitutil` (read-only plumbing) · `classify` (lexical classes) · `dedupe`
(already-merged pruning) · `probe` (static usability) · `resume` (coverage
pre-screen) · `manifest` (package + canonical manifest) · `evidence` (backfill
rows) · `platform_api` (status snapshots) · `cli` (orchestration + acceptance).

Artefacts: `artifacts/salvage/index.json` (hash-chained index),
`artifacts/salvage/<mission>/manifest.json` + `files/` (byte-exact copies),
`artifacts/salvage/evidence/{salvage_evidence.jsonl, .db, backfill_report.json}`,
`artifacts/salvage/negative_control/negative_control.json`.

## 3. Method (six steps, all deterministic)

1. **Scan.** `git status --porcelain -uall -z` in the dead worktree — every
   untracked file, plus modified/renamed/deleted tracked files. Nothing is written
   into the scanned worktree (verified by a regression test).
2. **Classify.** Pure lexical: `code` / `doc` / `data` / `scratch` / `other`.
   `scratch` (`__pycache__`, `.scratch/`, `tmp/`, `*.pyc`, editor droppings) and
   oversized files are dropped and counted, never packaged.
3. **De-duplicate against the baseline.** Byte identity only:
   `head` tier — content equals the `main` blob at the same path (same work, already
   merged); `history` tier — content equals a blob at that path in one of the last
   10 baseline commits that touched it (landed, later superseded). Both prune.
   Everything else is a genuine delta and is kept.
4. **Package.** Kept files are copied to `artifacts/salvage/<mission>/files/<path>`
   and re-hashed after the copy (`copy_verified`). The manifest records, per file:
   path, status code, class + reason, sha256, size, dedupe tier/reason/baseline hash.
   Canonical JSON, `sort_keys`, content-derived clock (git commit time of the dead
   mission's HEAD) — never wall clock, so replays are byte-identical.
5. **Backfill.** One independent evidence row per mission **that actually has
   material**, `evidence_kind = 'salvage'`, written to a JSONL ledger and to SQLite
   whose `CHECK` constraint admits only `'salvage'`. No row is produced for a
   mission with zero kept files — that is what keeps the negative control free of
   false partial evidence.
6. **Resume start points.** Per mission: inherited material inventory + per
   acceptance-criterion lexical coverage with the numeric score, so a re-dispatch
   order can quote "module X exists, item Y still open" directly.

## 4. Machine-checked acceptance (run of 2026-11-10, plan 249, baseline `main` @ `638d5c6d`)

| # | Criterion | Measured | Verdict |
|---|---|---|---|
| A1 | replay plan 249, every entry classified, manifest sha256 re-computed | 11/11 entries (plan seed mission `m6866` + node missions `m7132`–`m7141`), 53 packaged files re-hashed, 0 mismatches, 0 unclassified records | PASS |
| A2 | 3 merged done missions: 100 % merged-content prune, zero false partial | 63 merge-carrying missions screened newest-first, 3 accepted (`m6834`, `m8696`, `m8543`), 19 files reaching the deduplicator, 19 pruned, 0 kept → prune rate 1.000, evidence rows 0 | PASS |
| A3 | status invariance: `campaign_nodes.status` snapshot diff = 0 | 10 nodes snapshotted before/after backfill, 0 changed rows, `status_sha256` identical; `UPDATE campaign_nodes` probe refused by the DB (`permission denied for table campaign_nodes`) — the invariant is enforced by privileges, not by discipline; AST scan of every process-spawning call in `tools/salvage/*.py`: 0 mutation sites | PASS |
| A4 | static usability for 8+ code files, no "assumed usable" | 11 code files probed: 9 `usable` (compile + imports resolved + dynamic import ok), 2 `import_error` (dependency-level failure recorded verbatim), 0 implicit assumptions | PASS |
| A5 | idempotency: two replays byte-identical | 12 manifests + index compared, 0 differing, 0 missing/extra | PASS |

Numbers are read from `artifacts/salvage/acceptance.json` (all five criteria are
recorded there with the raw measurement payloads, including the full probe table
and the negative-control screening log).

Salvage material found in plan 249: `m7134` (TensorSPH, 44 files: 16 code / 1 doc /
27 data), `m7136` (TensorFEM, 7 files after 24 scratch drops), `m6866` (plan seed,
2 docs/data). The other eight nodes left **no uncommitted material** — their entries
are recorded as `kept = 0`, which is the honest answer and the reason a re-dispatch
must start from their acceptance criteria rather than from inherited code.

## 5. Anti-forgery design (what stops a trace looking like an achievement)

* Salting: a salvage row can never move a node status — `evidence.assert_no_status_mutation`
  raises on any status drift and on any status word resembling acceptance
  (`done`/`verified`/`accepted`/`passed`).
* Zero material ⇒ zero row. The negative control therefore cannot manufacture
  "partial" evidence.
* De-duplication is byte-proven, never path-based, and the tier that fired is
  recorded. Identical bytes are somebody's merged work, so they are pruned even
  when the path is this mission's own deliverable.
* Probes are executed, not assumed: `py_compile` runs with the `.pyc` redirected to
  a temp dir, imports are resolved in an isolated subprocess, and dynamic import is
  attempted only when every top-level import resolves — the verdict is written per
  file.

## 6. Limitations (honest)

1. **Cross-script lexical blindness.** Criteria written in Chinese over files named
   in English score 0 overlap, so `m7136` shows 0/5 coverage even though
   `panel_smith_csr_verification.py` is clearly its deliverable. The bias is
   conservative (under-claiming), but coverage numbers are a *pre-screen*, not a
   judgement; the per-criterion scores are exported so a reviewer can override.
2. **History tier is bounded** to the last 10 baseline commits touching a path
   (`history_limit`). Content that landed and was rewritten much later can be
   mis-classified as a delta — the module then *keeps* it (over-salvage, not
   over-claim), and the manifest shows the tier decision.
3. **PostgreSQL backfill is blocked in this runtime**: the scanning role has no ACL
   on `public` (`permission denied for schema public`), so rows persist to the local
   JSONL + SQLite ledger and the refusal is recorded verbatim in
   `backfill_report.json`; the `salvage_evidence` DDL is shipped for the day a
   privileged channel exists. No row was silently dropped.
4. **`campaign_nodes` cannot be read via SQL by this role**, so the A3 snapshot is
   taken through the campaign API (`/tensor/campaigns/plans/249/nodes`) and paired
   with the SQL write probe above. If a privileged role ever grants read access, the
   same comparison can be replayed with `psql` unchanged.
5. **Dynamic import executes module bodies** (bounded by a 30 s timeout, in a
   subprocess, with `PYTHONDONTWRITEBYTECODE=1`). A dead-mission module with heavy
   import-time side effects is therefore slow to probe; probe cost is capped by
   `probe_limit` (8 files per mission) and a 4-worker pool.
6. **Plan-249 entry count** is 11 because it is derived mechanically as
   *plan seed mission + distinct node missions* (`m6866` + 10). The plan's node list
   itself holds 10 nodes; the derivation is recorded in `index.json`
   (`entry_derivation`).

## 7. How a re-dispatch uses it

1. Read `docs/salvage/RESUME_START_POINTS.md` for the mission you are about to
   re-issue; it lists inherited files and the still-open acceptance items.
2. Reuse the packaged copies under `artifacts/salvage/<mission>/files/` (hash-verified
   against the manifest) instead of re-deriving them.
3. Re-run the acceptance items from scratch anyway: the salvage row says "material
   exists", never "criterion met". The node status stays where it was until a real
   verification lands.