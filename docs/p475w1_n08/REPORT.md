# Node report — toolchain integration `synthetic_toolchain_v1`

Run status **OK**, weakest evidence **MOCK**. Spec digest `1a274369ecee6aeb…`, run chain digest `ec15d89db118093b…`. 10 stages, 2.200 s per run.

## 1. Scenario and parameters

A synthetic end-to-end pipeline exercises the platform toolchain as *data*: a response surface is optimised, the same response is propagated under input uncertainty, a scalar field is transferred conservatively to a finer mesh and gate-checked, a score series is calibrated into a feasibility probability, an external callable (present) and an external repository stage (absent here) are probed, and all criteria are rolled up. No stage content is hard-coded: it enters through the spec below.

| stage | adapter | status | evidence | wall [s] |
|---|---|---|---|---|
| `surface` | `objective` | OK | REAL | 5.108e-05 |
| `design` | `optimize` | OK | REAL | 1.4237 |
| `uncertainty` | `uq` | OK | REAL | 0.515104 |
| `field_transfer` | `remap` | OK | REAL | 0.0611095 |
| `exchange_gate` | `conservation_gate` | OK | REAL | 0.000880411 |
| `score_probe` | `probe` | OK | REAL | 0.00155635 |
| `feasibility_prior` | `calibrate_prior` | OK | REAL | 0.0928674 |
| `external_local_callable` | `external` | OK | REAL | 0.000148969 |
| `external_repo_stage` | `external` | OK | MOCK | 0.000917491 |
| `rollup` | `audit` | OK | REAL | 0.0001653 |

Declared gates (from the spec metadata): `optimum_le=0.01`, `uq_anchor=nominal not beaten by q05, gap significant at 3 standard errors`, `remap_rel_residual_le=1.000e-12`, `prior_ece_le=0.05`, `prior_auroc_ge=0.6`, `integration_overhead_le=0.2`

## 2. Method

* **Port tags as a static type system.** Every adapter declares inputs and outputs tagged `scalar`/`series`/`callable`/`field`/`report`/`flag`; the plan builder rejects a binding whose producer tag differs from the consumer port tag, so an interface mismatch is a spec error, not a runtime surprise.
* **Declaration order = execution order, validated.** A forward reference or a cycle is refused before any stage runs.
* **Fail-closed degradation.** An absent backing module yields `WITHHELD` with the error text; dependents are `SKIPPED` rather than fed a stale or zero value. A stage that ran through a declared fallback is recorded `MOCK`, and the run's evidence is the weakest node's.
* **Replayable, hash-chained record.** Each node digests its resolved inputs and outputs; the link folds in the previous node's digest. Timing keys are excluded from digests, callables digest by bytecode+constants+closure values, never by `repr`.
* **Cost accounting.** A warm-up run precedes timing; harness cost is `whole-run wall − Σ stage walls` on the *same* run, and a standalone replay of each stage on its captured inputs gives the cross-run comparison.

## 3. Results

### 3.1 Acceptance criteria

| criterion | input | op | threshold | value | status |
|---|---|---|---|---|---|
| optimum_small | `f_best` | le | 0.01 | 2.375e-11 | PASS |
| optimizer_ran | `n_eval` | ge | 100 | 633 | PASS |
| anchor_below_q05 | `anchor_below_q05` | eq_true | None | true | PASS |
| anchor_gap_significant | `anchor_gap_stderr` | ge | 3 | 57.3679 | PASS |
| uq_ran | `uq_n_evals` | ge | 1000 | 4000 | PASS |
| remap_conservative | `rel_residual` | le | 1.000e-12 | 0 | PASS |
| exchange_gate_pass | `gate_passed` | eq_true | None | true | PASS |
| prior_calibrated | `prior_ece` | le | 0.05 | 0.00353664 | PASS |
| prior_monotone | `prior_monotone` | eq_true | None | true | PASS |
| prior_discriminative | `prior_auroc` | ge | 0.6 | 0.772347 | PASS |
| local_probe_low | `local_value` | ge | 1.41421 | 1.41421 | PASS |
| local_probe_high | `local_value` | le | 1.41421 | 1.41421 | PASS |
| repo_stage_available | `repo_stage_available` | eq_true | None | false | INFO |
| anchor_gap_sigma_descriptive | `anchor_gap_sigma` | ge | 0 | 0.907066 | PASS |

Verdict **PASS** — 13 pass, 0 fail, 0 withheld, 1 informational.

### 3.2 Integration cost

| stage | integrated median [s] | standalone replay [s] |
|---|---|---|
| `surface` | 8.325e-05 | 1.245e-05 |
| `design` | 0.15545 | 0.142441 |
| `uncertainty` | 0.467271 | 0.469052 |
| `field_transfer` | 0.0290479 | 0.0270635 |
| `exchange_gate` | 0.00080505 | 0.000527099 |
| `score_probe` | 0.00150163 | 0.00127018 |
| `feasibility_prior` | 0.0745965 | 0.0675513 |
| `external_local_callable` | 0.00011546 | 1.905e-05 |
| `external_repo_stage` | 0.00107971 | 0.000408301 |
| `rollup` | 0.00012321 | 5.294e-05 |

* stage work (sum of per-stage medians): 0.708398 s
* harness cost (same run): 0.0416526 s → **integration overhead 5.71 %** (gate < 20 %)
* cross-run comparison vs standalone replay: 7.19 %
* reproducible across repeats: True

### 3.3 Stage outputs

* propagated response (UQ stage): mean 0.0821637, std 0.0905819, q05 0.00364232, q95 0.268785
* optimiser nominal objective: 2.375e-11 (analytic minimum 0)
* conservative transfer: total source 4.625, total destination 4.625, relative residual 0
* feasibility prior: Brier 0.194112 (base-rate reference 0.249984), ECE 0.00353664, AUROC 0.772347, monotone true

### 3.4 Regression suites (run by this node)

| suite | result | passed | failed | skipped | duration [s] |
|---|---|---|---|---|---|
| `tests/test_uq_propagation.py` | PASS | 30 | 0 | 0 | 15.26 |
| `tests/test_coupling_remap.py` | PASS | 25 | 0 | 0 | 4.882 |
| `tests/test_coupling_clock.py` | PASS | 27 | 0 | 0 | 1.575 |
| `tests/test_coupling_twin.py` | PASS | 23 | 0 | 0 | 8.194 |
| `tests/test_coupling_platform_neutrality.py` | PASS | 10 | 0 | 0 | 3.102 |
| `tests/test_optimizer_objective_audit.py` | PASS | 11 | 0 | 1 | 5.66 |

## 4. Figures

See `fig_timings.png`, `fig_calibration.png`, `fig_uq_band.png` (generated from the same artifact).

## 5. Pitfalls and limitations

* An invariant that names the *wrong* direction is worse than no invariant: the first version of the cross-stage criterion required the optimiser's nominal value to lie inside the propagated [q05, q95] band. For a minimised response the optimum sits *below* q05 by construction, so a correct pipeline failed. The criterion was replaced by two statements that can fail for the right reason: the nominal is not beaten by the q05 of the ensemble, and the gap to the ensemble mean is statistically significant (57 standard errors here).
* Digests must exclude timing: the first run had non-reproducible hashes because stage reports carry `wall_s`, so 'determinism' was untestable. Timing keys are now dropped inside the canonicaliser and reproducibility is asserted in the test suite.
* Callables must not hash by `repr`: `repr(fn)` embeds a memory address, so two identical closures hashed differently. The digest now uses bytecode + constants + captured closure values, which also makes two closures with different constants differ (asserted).
* `overlap_l2` without the conservation correction is exactly total-conserving but **not** first-moment-conserving on a non-nested mesh (moment residual 5.6e-4 > the 1e-10 gate). The shipped spec therefore uses a nested finer mesh (a patch test), and a negative control test keeps the non-nested failure documented instead of hidden.
* The harness cannot judge whether a stage's physics is right; it can only prove composition, reproducibility and the criteria the caller declared. The run's evidence level is the weakest stage's — here MOCK, because the sibling-repository stage is not vendored in this repository and ran through its declared fallback.
* Timing on a shared machine is directional. Harness cost (same-run totals minus stage walls) is the primary number; the cross-run standalone comparison moves by several percent between sessions and is reported with its samples rather than to three digits.
* The full repository suite (6108 tests, 70 pre-existing collection errors) was submitted to the cluster because a single local run exceeds the node budget; the regression verdict recorded here covers the suites adjacent to the composed stages.

## 6. Error judgement against references

| quantity | this run | independent reference | relative error | < 3 % ? |
|---|---|---|---|---|
| UQ mean of response | 0.0821637 | 0.0821392 | 0.030 % | yes |
| UQ std of response | 0.0905819 | 0.0903467 | 0.260 % | yes |
| UQ q05 of response | 0.00364232 | 0.00360239 | 1.108 % | yes |
| UQ q95 of response | 0.268785 | 0.267453 | 0.498 % | yes |
| optimiser objective at optimum | 2.375e-11 | 0 | 0.000 % | yes |
| conservative transfer total | 4.625 | 4.625 | 0.000 % | yes |
| transfer relative residual | 0 | 0 | 0.000 % | yes |
| prior AUROC | 0.772347 | 0.769821 | 0.328 % | yes |
| prior base rate | 0.496 | 0.499864 | 0.773 % | yes |

**All quantities agree with their independent references within 3 %.** References: the propagated moments come from a 2,000,000-sample Monte-Carlo of the declared marginals (rejection-sampled truncation, own response arithmetic), the AUROC/base rate from a 4,000,000-sample simulation of the declared generative score model with a Mann-Whitney AUC, and the transfer total from the analytic integral of the declared linear field. Excluded by construction: the optimiser objective (reference = the analytic minimum 0) and the residual (reference = 0), where a relative error is only meaningful as an absolute one; both are reported as-is.
