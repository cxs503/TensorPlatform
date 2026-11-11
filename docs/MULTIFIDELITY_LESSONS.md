# Distilled lessons: cross-solver clocks, conservation gates, model distillation

**Review status**: submitted with mission 13992 (branch `agent/m13992`), reviewed by
the platform's independent grading seat. **Last verified**: 2026-11-11,
`artifacts/multifidelity/verification.json` (sha256 in `provenance.json`).

Provenance: campaign node `p457w2-n08` (mission 13992, repo TensorPlatform),
package `src/multifidelity`, record `artifacts/multifidelity/`, tests
`tests/test_multifidelity_*.py` (43 tests, 4 negative controls), generator
`python3 -m tools.multifidelity_maturity.cli`.

---

## L1 — A net conservation balance cannot detect a missing global clock

**Rule.** On a coupled interface, gate on the **frame-wise** defect
(`Σ_k |booked_k − required_k| / Σ_k |required_k|`), not on the run-level net
balance. The left-endpoint Riemann error of *any* consistent sampler telescopes
to `h/2 · (q(T) − q(0))`, so the net balance passes even when every frame is
booked with a stale value.

**Context / applicability.** Interfaces whose consumer needs a per-frame
quantity and whose sampling lattices may be unaligned. Applies to any exchange
protocol built on a hold/extrapolate of the partner's last sample.

**Evidence.** Same nominal steps (1/960, 1/480, 1/60), same channels: barrier
protocol frame L1 `8.13e-05`, net `2.2e-16`; free-running frame L1 `0.134`
(worst consumer phase), net only `0.043`. See
`artifacts/multifidelity/criteria.json` (`b_conservation`,
`e_negative_controls`) and `figures/fig2_conservation_error.png`.

**Mechanism.** Telescoping of the first-order quadrature error; the residual is
a boundary term, not a per-frame error.

**Counterexample / limit.** If the exchanged quantity is consumed only as a
run-total (a total mass budget), the net balance is the right gate and the
frame metric over-reports.

**Regression guard.** `tests/test_multifidelity_exchange.py::test_negative_net_balance_alone_cannot_see_a_missing_clock`
asserts net `<1%` while frame L1 `>5%` on the same run — the guard fails if a
refactor silently weakens the frame accounting into a net recomputation.

---

## L2 — Keep co-simulation steps rational; rounding is not a small perturbation

**Rule.** Convert time steps to `fractions.Fraction` (denominator capped at 1e9)
and take gcd/lcm exactly; add a contract that rejects a grid needing more than
`1e7` steps. Never quantise steps and take the lcm of the integers.

**Context / applicability.** Any multi-solver scheduler with heterogeneous,
possibly incommensurate steps.

**Evidence.** Steps 1/960, 1/480, 1/60: exact lcm step `1/60 = 0.01667`, the
1 ms-quantised shortcut gives `0.034` (relative error `1.04`). An
incommensurate pair (1/3, √2/7) is rejected by both strategies: gcd would need
`1.6e9` steps, lcm `3.25e8` sub-steps per window.

**Counterexample / limit.** When all steps are exact binary fractions and small
integers (e.g. 1/1024, 1/2048), an integer lattice is exact and cheaper; the
guard exists for the general case, not as ceremony.

**Regression guard.** `tests/test_multifidelity_clock.py::test_skew_audit_separates_float_drift_from_the_rounding_shortcut`
and `test_incommensurate_clocks_are_rejected_by_both_strategies`.

---

## L3 — The soft-label bandwidth must exceed the output-bin spacing

**Rule.** For regression distillation with a discretised output axis, set the
temperature `T` to at least a few bin spacings and verify the KL is smooth in
the prediction. A bandwidth below the bin spacing turns the KL into a
nearest-bin loss and the distilled student loses to the hard-label student.

**Context / applicability.** KD over continuous targets implemented as softmax
over bins (`src/multifidelity/distill.py`); not applicable to logit-based
classification KD where the temperature acts on the model's own logits.

**Evidence.** Early measurement with `T = 0.05·σ_y` and 81 bins over a 2.2-wide
output range (spacing 0.024 > T = 0.02): distilled 91.2% vs hard-label 92.4%,
KL converging poorly. After `T = 0.10·σ_y` with 81–101 bins: distilled 96.25%
vs hard-label 84.32% at the same label budget.

**Counterexample / limit.** With plenty of hard labels the gap closes (measured
93.8 vs 94.0 points at 48 hard labels): distillation buys data efficiency, not
accuracy that labels already provide.

**Regression guard.** Distillation tests assert the *direction* of the effect
(`test_negative_distillation_disabled_loses_accuracy_when_labels_are_scarce`)
plus exact gradient agreement with finite differences, so a silently broken KL
gradient cannot hide behind a plausible-looking training curve.

---

## L4 — Limit BLAS threads for tiny-model training

**Rule.** For layer sizes of order 100x100 use non-BLAS products
(`np.einsum(..., optimize=False)`); a threaded gemm spends its time in the
thread pool.

**Evidence.** Measured on the 128-core node: 15.5 ms per `280x32 @ 32x32` under
the default thread pool vs 0.22 ms with `einsum` (70x), and the non-BLAS path is
bit-reproducible across thread counts. The first version of the study was 70x
slower for this reason alone; training one student dropped from 8.5 s to 0.39 s.

**Counterexample / limit.** Irrelevant for large batches where gemm arithmetic
dominates; do not apply this in solver kernels.

**Regression guard.** None mechanised (a timing assertion would be flaky); it is
recorded here and in the module docstring so the next author sees it before
profiling.