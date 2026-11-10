# Toolchain integration harness — usage

Node **P475W1-N08** (platform toolchain integration validation). Package:
`tools/toolchain_integration/`. Zero domain semantics: every stage content
(parameters, thresholds, module paths) is injected through a JSON spec.

## 1. What problem this solves

The platform's stages were each verified alone: the optimisation library, the
forward-UQ framework, the conservative field-transfer library with its
conservation gates, and (in sibling repositories) further stages. A set of
individually-green tools says nothing about whether they **compose**. The failures
that composition can hide are exactly the expensive ones:

* a stage silently receiving the wrong *kind* of value (a report where a scalar is
  expected) and converting it by accident;
* a missing dependency that returns zeros instead of failing;
* outputs that change between runs, so a "verified" number is not reproducible;
* an orchestrator whose own cost dominates the computation.

The harness turns each of those into a machine-checked property.

## 2. Quick start

```bash
python -m tools.toolchain_integration.cli \
  --spec tools/toolchain_integration/specs/synthetic_toolchain_v1.json \
  --out artifacts/toolchain_integration/latest \
  --repeats 3 --seed 3
```

Exit code 0 requires: all stages `OK`, all non-informational criteria `PASS`, and
a reproducible run digest. `--allow-fail` records a failing run as evidence and
still exits 0 (used for negative controls).

From Python:

```python
from tools.toolchain_integration import PipelineSpec, ExecutionContext, measure
from tools.toolchain_integration.dag import run_pipeline

spec = PipelineSpec.load("tools/toolchain_integration/specs/synthetic_toolchain_v1.json")
run = run_pipeline(spec, ExecutionContext(seed=3))
print(run.status, run.evidence, run.chain_digest)
print(run.value("rollup.status").value["criteria"])   # per-criterion detail

rep = measure(spec, repeats=3, ctx=ExecutionContext(seed=3))
print(rep.orchestration_ratio, rep.overhead_ratio, rep.determinism)
```

## 3. Writing your own spec

A spec is a JSON object with `schema`, `name`, and `nodes`. Each node names an
adapter and binds its input ports:

```json
{
  "id": "design",
  "adapter": "optimize",
  "inputs": {"objective": {"from": "surface.f"}},
  "params": {"variables": [{"name": "u", "lower": -1, "upper": 1}],
             "method": "DE", "max_iter": 40, "pop_size": 12}
}
```

Rules enforced before anything executes:

* nodes are declared in dependency order (a forward reference is a spec error);
* every required input port of the adapter is bound;
* a bound edge must carry the port's declared tag (`scalar`, `series`,
  `callable`, `field`, `report`, `flag`) — a type mismatch is a spec error;
* node ids are unique; the graph is acyclic.

Use `{"value": ...}` for a literal and `{"optional": true}` on ports a spec may
leave unbound. Mark a node `"required": false` when its failure should degrade the
run (`DEGRADED`) instead of failing it.

## 4. Adapters

| adapter | stage | backing code | evidence |
|---|---|---|---|
| `objective` | declarative response surface | `adapters.build_response` | REAL |
| `probe` | score/label generator for calibration | `adapters._run_probe` | REAL |
| `optimize` | DOE + surrogate / DE / SLSQP | `tools/optimizer/optimizer_tool.py` | REAL |
| `uq` | forward propagation + quantile CIs | `backend/services/uq_propagation.py` | REAL |
| `calibrate_prior` | logistic feasibility prior | `tools/toolchain_integration/prior.py` | REAL |
| `remap` | conservative field transfer | `tools/coupling/remap.py` | REAL |
| `conservation_gate` | sum/moment/positivity/overshoot gates | `tools/coupling/conservation.py` | REAL |
| `external` | run-time probe of any callable, declared fallback only | `importlib` | REAL/MOCK |
| `audit` | criteria roll-up (PASS/FAIL/WITHHELD) | `adapters._run_audit` | REAL |

`API.md` in the generated bundle lists every port with its tag and requiredness.

## 5. Evidence semantics (why a green run means something)

| situation | node status | node evidence | run evidence |
|---|---|---|---|
| stage ran on its real library | `OK` | REAL | REAL |
| stage ran through its declared fallback | `OK` | **MOCK** | MOCK |
| backing module missing, no fallback | `FAIL`/`DEGRADED` | WITHHELD | WITHHELD |
| required input unavailable (upstream failed) | `SKIPPED` | WITHHELD | WITHHELD |
| adapter raised | `FAIL`/`DEGRADED` | WITHHELD | WITHHELD |

The run's evidence is the **weakest** node's. Fallbacks are allowed — they are how
a pipeline whose stage lives in another repository can still be wired and tested —
but a substituted stage can never be reported as REAL, and the substitution reason
is recorded in the node's notes.

## 6. Cost accounting

`measure()` reports two numbers:

* `orchestration_ratio` (primary) — `(whole-run wall − Σ stage walls) / Σ stage
  walls` from the *same* run: the harness's own cost (binding, tagging, digesting,
  chaining, error handling). Campaign gate: **< 20 %**.
* `overhead_ratio` (secondary) — whole-run wall versus a replay of each stage on
  its captured inputs. Noisier (CPU state differs between loops), reported with
  its samples; the gate is applied to its absolute value.

A warm-up run precedes all timing so library import cost is excluded from both
sides. `determinism` must be `True`: if the run digest changes between repeats, the
two timings describe different work and the report says so.

## 7. Provenance

Each node record carries `input_digest`, `output_digest` and a `chain_digest` that
folds in the previous node's chain digest — so the run hash changes if any stage's
inputs, outputs or ordering change. Timing keys (`wall_s`, `elapsed_s`,
timestamps) are excluded from digests by construction, which is what makes
"reproducible" a testable property rather than an aspiration. Callables digest by
bytecode + constants + captured closure values, never by `repr` (which embeds a
memory address).

## 8. Extending

* **New stage** — register an `Adapter` in `adapters.py` with declared ports,
  evidence level and `run(ctx, params, inputs) -> {port: PortValue}`; nothing in
  the runner changes.
* **Stage in another repository** — use the `external` adapter with
  `module`/`attr` from your spec. If the module is absent the node records
  `substituted: true` and the run's evidence drops to MOCK; when the module lands,
  the same spec picks it up with no edit.
* **New criterion type** — extend `_OPS` in `adapters._run_audit`; keep unknown
  operators an error (fail-closed).

## 9. Limits (honest)

* The harness validates *composition*, not *physics*: it cannot tell whether a
  stage's number is physically right, only that it is reproducible, type-compatible
  and consistent with the criteria you declared.
* Timing gates are wall-clock measurements on a shared machine; they are
  directional (order-of-magnitude), not benchmark-grade.
* A pipeline can pass while its weakest evidence level is MOCK — by design. Read
  `run.evidence` before quoting any number from it.