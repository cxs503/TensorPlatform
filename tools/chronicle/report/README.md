# Chronicle report line v2 (`tools/chronicle/report/`)

IN18-T3 artifact: a ten-section journal framework whose every number is
machine-bound to a frozen evidence snapshot, plus the gate that says **FAIL**
when it is not.

## The failure this exists to stop

The paper line lost two drafts (W2 verdict) to the same root cause: numbers that
could not be resolved to any artifact, and figures synthesised inside the
plotting script. The existing gates judge prose (`tools/paper_gate/
traceability_gate.py`, PG1–PG8) or execute the figure generator and diff arrays
(`figure_provenance.py`, PG9). Both assume the referenced data has a state that
does not move. In a pipeline where artifacts are rebuilt by every downstream
node, it does. This module gives the report a **frozen, hash-anchored baseline**
and binds each number to it.

```
artifact JSON --ingest--> report.sqlite --sha256--> snapshot_hash
                              |  read-only SQL
                              v
        number value + {{prov:id}}  ---->  manuscript.md
                              ^                    |
                     provenance.json  <----- figure sha256
                              ^                    |
                        verify.py: re-run SQL, recompute hashes -> PASS | FAIL
```

## Modules

| file | responsibility | contract |
|---|---|---|
| `snapshot.py` | ingest a real platform artifact into SQLite, SHA-256 anchor it | `chronicle-snapshot/1` |
| `provenance.py` | `{{prov:<id>}}` marker protocol, record store, enforced blocks | `chronicle-provenance/1` |
| `skeleton.py` | ten-section journal skeleton + structural validator | `chronicle-skeleton/1` |
| `figures.py` | saturation / coverage-heatmap / funnel figures, PNG + PDF, hashed | figure records |
| `verify.py` | anti-fabrication gate (8 FAIL codes, 2 WARN codes) | `chronicle-verify/1` |
| `demo.py` | end-to-end build of the first issue | `chronicle-report/1` |

## Run

```bash
python3 -m tools.chronicle.report.demo                     # build + verify, exit 0 = PASS
python3 -m pytest tests/test_chronicle_report_gate.py -q   # 11 tests, 6 of them negative controls
```

Artifacts land in `artifacts/chronicle_report/` (snapshot, figures, provenance,
verify verdict, machine acceptance record) and the manuscript in
`docs/papers/chronicle-report-v2/manuscript.md`.

## FAIL codes

| code | meaning |
|---|---|
| `FABRICATED_NUMBER` | printed digit does not equal the recorded value |
| `SOURCE_DRIFT` | re-executed SQL does not reproduce the recorded value |
| `SNAP_HASH_MISMATCH` | snapshot sqlite bytes changed after the manifest hash |
| `SNAP_SOURCE_DRIFT` | source artifact changed after the snapshot was taken |
| `RECORD_SNAPSHOT_MISMATCH` | record pinned to a different snapshot than the one under test |
| `SOURCE_ERROR` | recorded SQL fails on the snapshot (unverifiable ≠ pass) |
| `FIGURE_HASH_MISMATCH` / `FIGURE_MISSING` | figure bytes changed / absent |
| `UNMARKED_NUMBER` | numeric literal inside an enforced block with no token |
| `DANGLING_TOKEN` / `TOKEN_KIND_MISMATCH` | token has no record / wrong record kind |
| `UNTERMINATED_PROV_BLOCK` | `prov:begin` without `prov:end` (enforcement silently off) |

WARN: `ORPHAN_RECORD` (record never printed), `NO_ENFORCED_BLOCK`.

## Scope, stated honestly

* **Enforcement is block-scoped.** Only numbers inside `<!-- prov:begin -->` …
  `<!-- prov:end -->` must carry a token (in the first issue: Abstract and
  Results, 2 blocks, 23 marked numbers). Narrative sections are not enforced,
  and the verifier reports the block count rather than claiming full coverage.
* **Structural numerals are exempt** (`表 1`, `图 1–3`, `Fig. 2`, `Section 4`):
  they are identifiers, not measurements. The exemption only fires when the
  numeral is glued to a label word.
* **Hashing is tamper evidence, not proof.** No signing key lives in the
  worktree; a consistent rewrite of artifact + snapshot + records is outside
  what this gate can see. External timestamping/signing is the honest next step.
* **One ingester only** (`capability_ledger_resolved.json`). Other artifact
  types need their own ingester; none is implemented or claimed.
* **Values, not physics.** The gate checks that a number equals its query, not
  that the query or the underlying physical claim is right. Domain accuracy
  (<3% / <15% against experiment or reference) remains the job of the physics
  validation nodes.
* **CJK rendering**: the build image has no CJK font, so figure ticks use an
  English transliteration of domain names; data values and provenance keep the
  original strings.