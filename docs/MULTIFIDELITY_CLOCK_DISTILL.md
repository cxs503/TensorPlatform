# `src/multifidelity` — cross-solver clock synchronisation and distillation

Two platform-common mechanisms that any solver repository can call. The package
is domain-neutral: solvers are opaque ids, time is dimensionless, exchanged
quantities are analytic channels, and the student/teacher pair is defined by a
callable contract. Only the standard library and numpy are imported by the core
modules (`tests/test_multifidelity_neutrality.py` enforces both the vocabulary
and the import boundary).

## 1. Global clock manager

```python
from src.multifidelity import GlobalClockManager, Strategy

gcm = GlobalClockManager(Strategy.GCD)          # or Strategy.LCM
gcm.register("fluid",  1 / 960)                  # dt as float, Fraction or int
gcm.register("structure", 1 / 60)
grid = gcm.build(horizon=0.5)                    # SyncGrid
grid.dt_global                                   # Fraction(1, 960) — exact
grid.ratios                                      # {'fluid': 1, 'structure': 16}
grid.publication_nodes("structure")              # global indices it publishes at
grid.nesting_ok("fluid", "structure")            # are the frames on the producer lattice?
gcm.skew_audit(grid)                             # float drift vs rounding shortcut
```

Semantics

| strategy | `dt_global` | who sub-steps | typical use |
| --- | --- | --- | --- |
| `GCD` | `gcd(dt_i)` — finest common grid | nobody; every own step is an integer multiple of the global step | point-wise coupling, sample exchange |
| `LCM` | `lcm(dt_i)` — macro step | every solver whose step is finer than the macro step | macro-step / sub-cycling, integral exchange |

Contracts (both raise `ClockContractViolation`, never silently drift):

* every `dt_global / dt_i` (LCM) or `dt_i / dt_global` (GCD) must be an integer;
* the grid may not need more than `MAX_STEPS = 1e7` steps over the horizon —
  incommensurate clocks are rejected instead of producing an unusable step;
* `nesting_ok(producer, consumer)` must hold for the barrier exchange protocol.

Rational arithmetic is not decoration: quantising steps to 1 ms and taking the
lcm lands on 34 ms where the exact common step is 16.7 ms, and no amount of
solver refinement repairs that.

## 2. Boundary exchange and conservation accounting

```python
from src.multifidelity import Channel, exchange_synced, exchange_free_running, summarise

channels = [Channel("mass_flux", base=1.0, amp=0.5, freq=8.0)]
ledger = exchange_synced(gcm, grid, channels, producer="fluid", consumer="structure")
summarise(ledger)
# {'max_net_defect': 2e-16, 'max_frame_l1_defect': 8e-05, ...}
```

What crosses a frame is a quantity `Q_k = ∫_frame q dt`, not a point value, and
two accounts are kept: the producer's and the consumer's. Two metrics are
reported and they are **not** interchangeable:

* `net_defect` — run-level imbalance (the classical mass/momentum/energy
  balance). Structurally small for any consistent sampler, because the
  left-endpoint Riemann error telescopes to `h/2 (q(T) - q(0))`;
* `frame_l1_defect` — total variation of the per-frame booking error. This is
  the metric a conservation gate must use; it is what separates a protocol with
  a global clock (`~1e-4` here) from one without (`~13%` at the same nominal
  steps).

## 3. Teacher → student distillation

```python
from src.multifidelity import (DistillConfig, HighFidelityTeacher, train_student,
                               pareto_sweep, sample_params)

teacher = HighFidelityTeacher()          # grid-refined ensemble: mean + spread
X = sample_params(600)
y, spread = teacher.evaluate_batch(X)
config = DistillConfig(width=32, alpha=0.5, temperature_rel=0.1)
result = train_student(X[:12], y[:12], spread[:12], X, y, spread,
                       X_val, y_ref, config, teacher_cost=cost_us_teacher)
```

Objective (used verbatim, entropy term kept so the reported number is the
divergence):

```
p_teacher(b) = softmax_b( -(y_t - c_b)^2 / (2 (T^2 + spread^2)) )
p_student(b) = softmax_b( -(y_s - c_b)^2 / (2 T^2) )
L            = alpha * L_task + (1 - alpha) * KL(p_teacher || p_student)
```

`alpha = 1` disables distillation and reduces the budget to the hard labels, which
is the control arm; `temperature_rel` below the bin spacing degenerates the KL
into a nearest-bin loss (measured: the distilled student then loses to the
hard-label student). `pareto_sweep` walks width × alpha and returns the
non-dominated accuracy/cost set.

## 4. Relation to existing platform modules

* `tools/coupling/clock.py` — pairwise integer-ratio clock contract plus sample
  interpolation. This package takes an arbitrary number of participants, derives
  the step in exact rational arithmetic (`Fraction`), and adds a rejection
  contract for incommensurate clocks.
* `src/coupling/temporal_sync.py` — macro-stepping, causal order and an energy
  tracker. This package adds the explicit separation of *net* versus
  *frame-wise* conservation defect plus the analytic-rate audit; it does not
  replace the macro-stepper.
* `tools/knowledge_distill` — distils text corpora. Model distillation
  (teacher → student with a soft-label KL) is new here.

## 5. Maturity record

`python3 -m tools.multifidelity_maturity.cli --out artifacts/multifidelity`
regenerates the whole record (study JSON, criteria, three figures,
`verification.json` with per-artifact sha256, and the six-element `report.md`).
The current verdict is in `artifacts/multifidelity/criteria.json`.