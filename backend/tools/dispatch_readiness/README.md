# dispatch_readiness — pre-dispatch interface readiness gate

Turns "this node is blocked because its upstream has not delivered / delivered the
wrong caliber" from a human discovery into a **fail-closed decision taken before
dispatch**. The mechanism is deliberately domain free: it only understands
*artefact classes, required fields, units, versions and requirement strength*.
Domain data arrives through configuration or API calls, never through code.

## Three states

| state | meaning | dispatch? |
|---|---|---|
| `READY` | every declared upstream artefact is there and matches the caliber contract | yes |
| `NOT_READY` | a required upstream artefact is missing (upstream task not even handed out, or not finished) | no |
| `CONFLICT` | artefact delivered but fields / units / version do not match the consumer contract | no |

Every verdict carries human-readable attributions (`requirement`, `outcome`,
`message`), so a dispatcher log answers "why is this node waiting" without a human
inspecting the plan.

## Wiring it into a dispatcher

```python
from tools.dispatch_readiness import DispatchGate, UpstreamState, ArtifactRecord

gate = DispatchGate(
    nodes=plan_nodes,                       # [{"key","node_type","dependencies","input_contract"?}]
    contracts_path="config/contracts.json", # optional, declared contracts win over inference
)

verdict = gate.preflight(
    node_key="42",
    upstream_states={
        "41": UpstreamState(node="41", dispatched=True, delivered=True, succeeded=True,
                            record=ArtifactRecord(node="41", artifact="delivery",
                                                  fields=("summary", "digest"), version="1.2")),
    },
    at="2026-11-10T12:00:00+00:00",
)
if verdict["decision"] == "defer":          # fail closed
    log(verdict["blocking_reasons"])
```

`DispatchGate.preflight` never raises: an unknown node, an unparsable contract or
a resolver error all end as `decision="defer"`.

`DeliveryLedger` (attached as `gate.ledger`) records interceptions, later releases
and whether the release passed first try, i.e. the
"intercept → supply upstream → dispatch again → first-pass success" rate.

## Contract shapes

```json
{
  "contracts": {
    "42": {
      "version": "1.0",
      "requires": [
        {"upstream": "41", "artifact": "delivery", "mode": "terminal_success",
         "fields": ["summary"], "units": "u/x", "version_min": "1.1", "required": true}
      ]
    }
  }
}
```

`mode`: `declared` (an upstream task exists) < `delivered` (it finished) <
`terminal_success` (it finished successfully).

Without a declared contract the contract is inferred from structure only:
gate/checkpoint nodes require `terminal_success` from their upstream set, task
nodes require `declared` — the latter mirrors the platform's contract-parallel
dispatch policy (`READY = {done, in-flight}`) and is an a-priori choice, not a
tuned threshold.

## Backtest

```bash
cd backend
python3 -m tools.dispatch_readiness.cli export     # read-only SQL dump -> artefacts/snapshot
python3 -m tools.dispatch_readiness.cli backtest   # confusion matrix, ROC, permutation null
python3 -m tools.dispatch_readiness.cli audit      # AC4 zero-domain-vocabulary scan
python3 -m tools.dispatch_readiness.cli check      # exit code 1 if a frozen AC fails
```

The backtester rebuilds, per historical node, what the delivery registry looked
like at a given moment (mission `created_at` / `finished_at` of the upstream
nodes) and replays the gate. Labels come from configuration
(`config/dispatch_readiness_labels.json`) and are SQL traceable.

## Degradation

* missing contract file → empty contract table → gate answers `READY` (identity)
* missing label file → no labels → backtest reports `unlabeled_no_configuration`
* missing domain-term file → audit reports `degraded_identity`, no violations

A missing configuration therefore never blocks dispatch and never crashes the
backtest.

## Results

`docs/reports/P233W2-N08_interface_readiness_gate.md` (paper-quality report) and
`artifacts/dispatch_readiness/{summary,backtest_report,attribution_examples}.json|md`
plus five figures. Headline: recall 64.7% (11/17 upstream-gap blocks caught,
95% CI 41–83%), FPR 4.53% on 2492 done nodes (CI 3.8–5.4%), AUC 0.806; the
permutation null (200 shuffles) has a 95% interval of [0.475, 0.564] which covers
0.5. The report also records that the four blocked nodes of plan 233 were caused
by `infra x5 (agent_lost)`, not by missing upstream delivery.