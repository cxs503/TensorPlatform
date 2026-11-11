# `tools/invariant_audit` — cross-solver physical-invariant audit

Domain-neutral mechanism that answers a complementary question to
`tools/verification_harness`:

| harness | invariant audit |
|---|---|
| "does this number match an authoritative value?" | "does this solver obey the structural laws any correct implementation must obey?" |
| needs reference data | needs only the solver's own inputs/outputs |
| one criterion per case | four law families per (solver, case) pair |

## Layout

```
dimensions.py    exact rational-exponent dimension algebra (base symbols injected)
model.py         plan / target / invariant / result model + state folding
checks.py        evaluators: conservation, covariance, dimension_consistency, relation
engine.py        fail-closed plan validation, execution, ledger (byte-deterministic)
bundle.py        sha256 chain + environment fingerprint + tamper classification
cli.py           python3 -m tools.invariant_audit run|verify
report_figures.py  figures + figure data (application layer)
anchors/         synthetic models (mechanism never imports this package)
```

## States

| state | meaning |
|---|---|
| `PASS` | evaluated, inside the tolerance band |
| `FAIL` | evaluated, violated |
| `WITHHELD` | the evidence needed to judge is absent — never silently a PASS |
| `ERROR` | the target broke the observable contract (raised, wrong shape) |

## Declaring a target (configuration only)

```json
{
  "id": "opaque-target-id",
  "label": "free text",
  "callable": "package.module:function",
  "config": {"any": "json parameters"},
  "invariants": [
    {"id": "mass-conservation", "kind": "conservation",
     "params": {"quantity": "mass", "initial": "mass_initial",
                "final": "mass_final", "flux": "boundary_flux"},
     "tolerance": {"rtol": 1e-12, "atol": 0.0}}
  ]
}
```

The callable receives `config` and returns a mapping of observable name ->
number | list[number]; reserved keys (`_dimensions`, ...) carry metadata and are
never treated as observables.  Nothing in the mechanism knows what the numbers
mean physically.

## Commands

```bash
python3 -m tools.invariant_audit run --plan config/invariant_audit/plans/synthetic_solver_matrix.json \
    --out artifacts/invariant_audit/run_matrix
python3 -m tools.invariant_audit verify --bundle artifacts/invariant_audit/run_matrix
python3 -m pytest tests/invariant_audit -q
```

Evidence bundle layout: `plan.json`, `ledger.json`, `LEDGER.md`,
`targets/<id>.json`, `manifest.json` (sha256 + chain hash + root digest +
environment fingerprint).

Honest limitation: the chain is unkeyed. It proves internal consistency and
byte-level tampering, not authorship.