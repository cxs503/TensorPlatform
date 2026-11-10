# Evidence Governance (unified) — P210W2-N01

One platform capability that closes the loop the two previous mechanisms each
only half-owned:

```
whole-library scan  ->  baseline classification  ->  structured acquisition proposal
                    ->  upgrade gate (evidence, not status)  ->  closure-rate KPI
                    ->  hashed, byte-identical artifact set
```

* the **upgrade pipeline** half (`scan -> gate -> closure KPI`) comes from the
  WITHHELD→REAL automation (`tools/evidence_debt_compiler`, P519-N01),
* the **debt compiler** half (`classify -> proposal`) reuses the same module's
  extraction/classification/task-generation machinery,
* this package adds the missing platform-grade glue: library-wide scanning,
  configurable rules, a *quantified* proposal (magnitude + priority), an
  anti-fabrication upgrade gate, and reproducibility by construction.

## Acceptance criteria and where each one is proven

| # | criterion | how it is met | proof |
|---|-----------|---------------|-------|
| 1 | scan the whole library, classification accuracy ≥95% | `scanner` selects every WITHHELD row of the configured ledger/corpus; `classifier` scores weighted, config-delivered rules | `tests/test_scanner.py`, `tests/test_classification.py` (48-item labelled benchmark) |
| 2 | a structured proposal per WITHHELD entry (baseline type / missing magnitude / priority) | `proposal.ProposalCompiler` emits type + normalised magnitude + band + verification spec | `tests/test_proposals.py` |
| 3 | closure-rate KPI = upgraded / total, written to the ledger | `upgrade.UpgradeEngine` + `model.GovernanceLedger` (invariant validated on serialisation) | `tests/test_upgrade_kpi.py` |
| 4 | idempotent: same input twice → same artifact sha256 | no wall clock, canonical JSON, sha256 manifest | `tests/test_idempotency.py` |

## Quick start

```bash
# real library (capability ledger) -> artifacts/evidence_governance/
python -m tools.evidence_governance.runner --json

# synthetic loopback: proves the closed loop end-to-end
python -m tools.evidence_governance.runner \
    --config tools/evidence_governance/fixtures/governance.demo.v1.json \
    --verify-idempotent --json

# discipline gate
python tools/evidence_domain_scan.py tools/evidence_governance
pytest tools/evidence_governance/tests -q
```

## Artifacts

| file | content |
|---|---|
| `withheld_scan.json` | every scanned WITHHELD row + source statistics + per-row digest |
| `proposals.json` | proposals, aggregate counts, set digest |
| `upgrade_ledger.json` | per-row upgrade decision + gate configuration + orphans |
| `kpi.json` | closure rate, invariant statement, band/type distribution |
| `classification_accuracy.json` | accuracy, per-type recall, enumerated misses, threshold flag |
| `manifest.json` | sha256 of every artifact + `combined_sha256` |

## Design rules

* **Zero subject vocabulary in code.** Rule patterns, thresholds, level ranks,
  the evidence ladder, capability criticality and "is this row evidence-bearing"
  all arrive as configuration (`config/*.json`) or injected data
  (`artifacts/evidence_governance/inputs/*.json`). The repository scanner is part
  of the test-suite, so the rule is enforced, not promised.
* **The upgrade gate is structural.** A "pass" is accepted only with
  ≥`min_independent_refs` distinct sources, a non-empty list of acceptance checks
  all of which pass, and content hashes for every evidence reference. A
  fabricated pass therefore cannot move the KPI.
* **Honest by default.** No outcome → `no_outcome` and the debt stays open.
  Rejected claims are recorded with the reason, not silently dropped, and
  outcomes for capabilities outside the library are flagged as orphans.
* **Reproducible by construction.** No wall-clock, no random ids, sorted
  everything; identical inputs give an identical `combined_sha256`.

## Limits (stated, not hidden)

* The classification benchmark is authored by the same agent as the rules, so it
  measures rule/label consistency and tie-break behaviour rather than
  out-of-distribution generalisation.
* `missing_magnitude` is a normalised *index* of structural deficits, not a
  physical quantity; it is only meaningful as a ranking key within one config.
* The real library currently has no recorded verification outcomes, so its
  closure rate is honestly `0/19`. The closed loop itself is demonstrated on the
  synthetic loopback (`3/8`), which is a pipeline proof, not a library result.