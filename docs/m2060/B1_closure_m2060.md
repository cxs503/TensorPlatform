# B1 gate-defect closure (m2060): samples bare entry `backfill_file()` now wired to the three-gate admission

**seat** tp-infra-2 · **repo** TensorPlatform · **branch** `agent/m2060` · **EDIT_WHITELIST**: `docs/`, `src/`, `tests/`, `tools/`, `artifacts/`, `outputs/`

## (1) Case / background
`tools/sample_db/backfill.py::backfill_file()` is the historical bulk-insert path into the `samples` table.
Until this mission it had **no admission control at all**: any artifact JSON was inserted regardless of a missing
RunManifest, mismatching checksums, or an unresolved / non-successful mission. Mission e2e1-B1 pinned that gap with
`xfail(strict=True)` and shipped a gated entry `src/sample_db/ingest.py::ingest_artifact()`, but the bare entry itself
stayed open — the lock was only on the side door.

## (2) What was done
1. `backfill_file()` now calls `_admission_decision()` -> `src.sample_db.admission.admit_sample()` before any INSERT;
   a failed gate raises `AdmissionRejected` (structured code + JSONL audit trail) and **nothing is inserted**.
2. Callers that already judged the artifact (`src/sample_db/ingest.py`) pass `decision=` down: no double adjudication,
   no duplicate audit rows, and no import cycle (`backfill -> admission`; `admission` never imports backfill).
3. The former `xfail(strict=True)` test became the regression `test_legacy_backfill_entry_is_now_gated`: the bare entry
   **must** raise `AdmissionRejected`, `samples` row count must be 0, and the audit log must carry `rejected` + code.
4. Probe `docs/m2060/probe_b1_closure.py`: scans historical sdaa artifacts, assigns a decision code per artifact,
   really executes the bare entry on rejected artifacts (end-to-end interception), and runs a fixture positive control
   so that the closure is shown not to over-reject.

## (3) Results (all numbers programmatically read from `artifacts/m2060/b1_closure.json`)

| metric | value |
|---|---|
| artifacts scanned | 240 |
| rows the legacy bare entry would have loaded | **265** |
| rows actually loaded after closure | **0** |
| row block rate | **100.0%** |
| rejection codes | `{"E_MANIFEST_MISSING": 240}` |
| end-to-end interception (real `backfill_file` calls) | 5 cases, all `inserted == 0` |
| positive control (fixture A-case via `ingest_artifact`) | `ok=true, inserted=1` |

## (4) Figure
`artifacts/m2060/fig_b1_closure.png` — left: rows loaded before/after; middle: rejection-code distribution;
right: end-to-end interception measured by really calling the bare entry.

## (5) Caveats / limits
- Every historical sdaa artifact fails with `E_MANIFEST_MISSING`: they predate the RunManifest regime, so after the
  closure they can **no longer** be bulk-loaded. That is the intended semantics (unverified products must not enter the
  sample DB); a future rescue would have to supply manifests + checksums, not a bypass.
- `main()` demo mode also goes through `backfill_file()`, so the demo now records each artifact in `errors` and loads
  0 rows — reported honestly rather than silently admitted.
- The gate logic itself (manifest / mission / checksum adjudication) is untouched; this mission only connects the bare
  entry to it.
- Audit trail defaults to `outputs/sample_db_admission_audit.jsonl`; the probe passes `audit_log=None` so the production
  audit stream is not polluted.

## (6) Error verdict
| key quantity | reference | deviation | <3%? |
|---|---|---|---|
| rows loaded for rejected artifacts | 0 (hard gate semantics) | 0 rows | yes (identity, not a statistic) |
| rejection code | `E_MANIFEST_MISSING` (decidable: manifest absent) | identical for all 240 artifacts | yes |
| positive-control admission | 1 sample (fixture carries exactly one) | 1 row(s) | yes |

There is no experimental/analytic reference to compare against: this is **boolean gate semantics**, not a physical
quantity. The acceptance criteria are written as identities (rejected -> 0 rows + code + audit row), so the error
surface is zero by construction; statistical accuracy statements would be meaningless here.
