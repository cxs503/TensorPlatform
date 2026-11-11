# Parallel scalability ledger (cores → wall-clock)

Status: **method + harness delivered** (`tools/scaling/`), measurements per run are
recorded in `artifacts/scaling_ledger/` (see "Measured ledger" below).

## 1. Why this exists

The platform account was missing one axis entirely: *core count vs wall-clock*.
Every capability ledger entry answers "is the physics right?", none answered "does
buying 16 cores actually make it faster, and where does it stop paying?". Without
that number, scheduling decisions (how many cores to request for a campaign node)
and cost claims ("this ran in 3 min on 32 cores") are unverifiable.

The ledger answers three questions per kernel with measurement, not assertion:

1. **Strong scaling** — fixed problem size, does `T(1)/T(n)` track the core count?
2. **Weak scaling** — fixed work per core, does time stay flat as both grow?
3. **Serial fraction** — what `s` in Amdahl's law does the timing data imply, with
   a confidence interval, and does the harness recover an *injected* `s`?

Plus a **negative control**: a kernel that claims `n` cores and serialises
everything must be flagged as `s → 1`. A ledger without a negative control cannot
distinguish "scales well" from "measures nothing".

## 2. Method

### 2.1 Kernel protocol

Kernels are injected, not hard-coded (`tools/scaling/protocol.py`):

```python
setup(n_cores)   # allocate state, fork the worker pool
run(n_cores)     # exactly one timed unit of work
checksum() -> int  # partition-invariant integer fingerprint
```

The harness owns timing, repetition, randomisation and fitting; a domain kernel
only has to declare *what one unit of work is* and *how to fingerprint its result*.

### 2.2 Timing protocol

| element | choice | rationale |
|---|---|---|
| workers | persistent fork pool, `SharedMemory` data | fork/exit cost must stay outside the timed region |
| region | between a start and an end `Barrier` | measures the parallel region, not Python call overhead |
| reps | 5 timed + 1 warm-up per core count | warm-up absorbs page faults / lazy allocation |
| order | core counts shuffled per round | drift (thermal, co-tenant load) must not align with core count |
| statistic | median | robust to one slow run |
| fingerprint | integer checksum mod 2**64 | exact, associative, so re-partitioning cannot change it |

Integer accumulation is not a stylistic choice: floating-point summation is not
associative, so a float checksum would differ between partitions for reasons that
have nothing to do with correctness. Reducing mod 2**64 makes the fingerprint
invariant under any regrouping of the domain (C1 is a bitwise comparison, not a
tolerance).

### 2.3 Kernel families

| kernel | bottleneck | structure |
|---|---|---|
| `stencil5p` | compute + memory bandwidth | 5-point Jacobi stencil on an int64 grid; contiguous row blocks; ghost rows are the neighbours' data rows in shared memory, one barrier per iteration |
| `halo_ring` | communication latency | ring exchange over pipes; one ≤ 64 KB slab per iteration to the next rank (payload bounded by the pipe buffer keeps `send` non-blocking and the ring deadlock-free) |
| `serial_frac` | injected truth | perfectly parallel phase + rank-0-only serial phase; `s` calibrated on the host by a feedback loop |
| `pseudo_par` | negative control | forks `n` workers, runs all work on rank 0 |

### 2.4 Fitting

Amdahl (fixed size): `A(n) = 1 / (s + (1 − s)/n)`.
Gustafson (fixed time): `G(n) = s_g + (1 − s_g)·n`, `G(n) = n·E_weak(n)`.

`s` is fitted by a dense grid sweep + golden-section refinement (no gradient
method: for a flat objective — precisely the pseudo-parallel case — a gradient
method wanders, while a grid reports the physically meaningful boundary `s → 1`).
95 % CIs come from a percentile bootstrap over the repetitions: resample timings →
median → re-derive speedup against the resampled `n = 1` median → refit.

### 2.5 Acceptance criteria

| id | criterion | meaning |
|---|---|---|
| C1 | all checksums bitwise identical | the parallel work is the *same* computation at every core count |
| C2 | stencil `E(8) ≥ 0.70`, `E(16) ≥ 0.50` | bandwidth-bound kernels must scale in the practical range |
| C3 | stencil weak `E(8) ≥ 0.85` | adding cores must not add fixed cost |
| C4 | `R² ≥ 0.98`, `|ŝ − s| ≤ 3 pp`, CI covers truth | the fit is trustworthy *and* recovers known ground truth |
| C5 | pseudo control `ŝ ≥ 0.90`, CI low `≥ 0.85`, `E(16) ≤ 0.15` | the ledger can detect fake parallelism |
| C6 | figures + quota recommendation | the result is consumable, not just a JSON blob |

## 3. How to run

```bash
# functional check (≈1 min, 3 core counts, 2 reps)
python -m tools.scaling.cli --plan tools/scaling/configs/smoke.json \
    --out /tmp/smoke/results.json --figdir /tmp/smoke/figs

# full ledger (6 core counts 1..32, 5 reps, 7 kernel instances)
python -m tools.scaling.cli --plan tools/scaling/configs/full.json \
    --out artifacts/scaling_ledger/results.json \
    --figdir artifacts/scaling_ledger/figures
```

Exit code 0 means every criterion passed, 1 means at least one failed; the JSON
carries the numbers each verdict was decided on.

**Host requirement.** Scaling must be measured on an *idle* multi-core node.
A host with `load_average ≫ cpu_count` (oversubscribed) delivers a roughly equal
CPU share to every rank at every core count, so speedup collapses to ≈ 1 and the
ledger measures the queue, not the code. `environment.load_1_5_15` is recorded in
the ledger for exactly this reason; check it before trusting a run.

## 4. Reading the ledger

* `E(n)` is the fraction of the nominal n-core speedup actually obtained. `E > 0.8`
  is good, `E < 0.5` means the extra cores mostly wait.
* `s` is the serial fraction. It bounds the achievable speedup: `1/s` at `n → ∞`.
  Report `s` with its CI, never as a bare point estimate.
* `quota.recommended_max_cores` is the largest measured core count that still both
  keeps `E(n) ≥ 0.5` and adds ≥ 15 % throughput over the previous step. Above it,
  cores are wall-clock waste for that kernel shape.

## 5. Limitations

* Parallelism here is **process + shared memory / pipes**, not MPI. It exercises
  the same scaling mechanisms (synchronisation, memory bandwidth, message
  latency) but not interconnect topology or rank pinning.
* No NUMA or thread-affinity pinning: on a multi-socket node, migration adds
  variance that a pinned run would avoid.
* Amdahl's model assumes the parallel part is perfectly parallel and the serial
  part constant. Real overheads (barrier cost that grows with n) are absorbed into
  the fitted `s`, so `ŝ` is an *effective* serial fraction, not a code inspection.
* Weak scaling holds *work per core* constant but the working set grows with n, so
  a cache-resident problem at n=1 becomes memory-bound at n=32; that is physics of
  the measurement, not a defect, and is why C3 only asks for `E(8) ≥ 0.85`.
* A single host defines the ledger; the numbers are not portable across machines.
  The environment block (CPU model, core count, load, numpy version) is part of the
  result for that reason.

## 6. Measured ledger

Run: `artifacts/scaling_ledger/results.json` (7 kernel instances, core counts
1, 2, 4, 8, 16, 32, 5 timed repetitions + 1 warm-up each, randomised order).
`artifacts/scaling_ledger/results.md` is the generated table reproduced below;
figures in `artifacts/scaling_ledger/figures/` each carry a recorded sha256.

### 6.1 Verdict

**3 / 6 criteria pass.** C1 (bitwise checksums) and C5 (pseudo-parallel control)
pass on real measurements; C2/C3/C4 fail. The environment block shows *why*: the
measurement host was 93.7 % busy (`/proc/stat`) with `load_1_5_15` ≈ 383–560 on 128
visible cores, and `host_precheck` measured a 4-process CPU-bound speedup of
**0.86×** where 4× is required. Every rank therefore received the same fixed CPU
share at every core count, so efficiency collapses towards `1/n` regardless of the
kernel's quality. This run is kept as the *environment negative control*: it shows
the ledger correctly separating "kernel does not scale" from "host cannot be
measured", and the numbers are published unaltered (honest FAIL, not withdrawn).
The clean-node run is recorded as **WITHHELD — no fit host available**; the cluster
queue was stalled during the whole window (job `cj801` queued > 50 min with ~300
jobs ahead while both nodes reported healthy at load 40/128).

### 6.2 Generated tables (verbatim from results.md)

(strong / weak scaling, Amdahl, pseudo-parallel control)

Host `36cb7828dbd5` — Loongson-3C6000/D — os.cpu_count=128 — Linux-6.6.52-1.7.lns23.loongarch64-loongarch64-with-glibc2.38
Protocol: 5 timed repetitions + 1 warm-up, randomised core order, median statistic, integer checksum reduced mod 2^64.

| kernel | mode | E(1) | E(2) | E(4) | E(8) | E(16) | E(32) | s_Amdahl | R² |
|---|---|---|---|---|---|---|---|---|---|
| stencil5p_strong | strong | 1.000 | 0.753 | 0.632 | 0.469 | 0.375 | 0.236 | 0.1111 | 0.9706 |
| stencil5p_weak | weak | 1.000 | 0.473 | 0.290 | 0.282 | 0.245 | 0.196 | - | - |
| halo_ring_strong | strong | 1.000 | 0.208 | 0.044 | 0.005 | 0.001 | 0.000 | 1.0000 | -4.1948 |
| halo_ring_weak | weak | 1.000 | 0.268 | 0.109 | 0.028 | 0.008 | 0.003 | - | - |
| serial_frac_s05 | strong | 1.000 | 0.831 | 0.749 | 0.456 | 0.298 | 0.148 | 0.1724 | 0.9733 |
| serial_frac_s20 | strong | 1.000 | 0.706 | 0.461 | 0.242 | 0.132 | 0.066 | 0.4413 | 0.9803 |
| pseudo_par_control | strong | 1.000 | 0.534 | 0.252 | 0.127 | 0.060 | 0.031 | 0.9972 | -0.0483 |

### 6.3 Acceptance verdicts (verbatim)

| id | criterion | verdict | key numbers |
|---|---|---|---|
| C1 | all checksums bitwise identical per kernel | PASS |  |
| C2 | stencil strong: E(8) >= 0.70 and E(16) >= 0.50 | FAIL | E8=0.469; E16=0.375 |
| C3 | stencil weak: E(8) >= 0.85 | FAIL | E8=0.282 |
| C4 | serial-fraction families: R2 >= 0.98 and |s_hat - s_injected| <= 3pp (95% CI covers truth) | FAIL | serial_frac_s05: s_hat=0.1724 truth=0.0727 err=9.97pp R2=0.9733; serial_frac_s20: s_hat=0.4413 truth=0.4200 err=2.14pp R2=0.9803 |
| C5 | pseudo-parallel kernel judged serial: s_hat >= 0.90, CI lower bound >= 0.85, E(16) <= 0.15 | PASS | E16=0.060; s_hat=0.997; ci_low=0.922 |
| C6 | per-kernel efficiency curves delivered + core quota recommendation | PASS |  |

**Overall: 3/6 criteria pass.**

## Core quota recommendation

| kernel | recommended max cores | marginal throughput gain per step |
|---|---|---|
| stencil5p_strong | 4 | 2->1.51, 4->1.68, 8->1.49, 16->1.60, 32->1.26 |
| stencil5p_weak | 1 | 2->0.95, 4->1.23, 8->1.94, 16->1.74, 32->1.60 |
| halo_ring_strong | 1 | 2->0.42, 4->0.42, 8->0.25, 16->0.34, 32->0.29 |
| halo_ring_weak | 1 | 2->0.54, 4->0.81, 8->0.51, 16->0.55, 32->0.76 |
| serial_frac_s05 | 4 | 2->1.66, 4->1.80, 8->1.22, 16->1.31, 32->0.99 |
| serial_frac_s20 | 2 | 2->1.41, 4->1.31, 8->1.05, 16->1.09, 32->1.01 |
| pseudo_par_control | 1 | 2->1.07, 4->0.94, 8->1.01, 16->0.94, 32->1.04 |

### 6.4 Estimator accuracy (synthetic, no hardware involved)

`artifacts/scaling_ledger/synthetic_recovery.json` — 30 seeds per truth value,
5 repetitions per core count, 1 % multiplicative timing noise, 100 bootstrap
resamples. This isolates the statistics from the machine: the fitted `s` lands
within a fraction of a percentage point of the injected truth, so a 3 pp miss in a
measured run is evidence about the host, not about the estimator.

| s (injected) | median abs. error [pp] | p95 [pp] | median R² | 95 % CI coverage | fraction within 3 pp |
|---|---|---|---|---|---|
| 0.00 | 0.00 | 0.02 | 1.0000 | 0.97 | 1.00 |
| 0.02 | 0.02 | 0.06 | 1.0000 | 0.97 | 1.00 |
| 0.05 | 0.04 | 0.09 | 1.0000 | 0.97 | 1.00 |
| 0.10 | 0.05 | 0.14 | 0.9999 | 0.97 | 1.00 |
| 0.20 | 0.09 | 0.24 | 0.9999 | 0.97 | 1.00 |
| 0.50 | 0.16 | 0.58 | 0.9995 | 1.00 | 1.00 |

### 6.5 What the numbers say about the kernels (host-limited, read with care)

Even on this host the shape of the curves is informative: the policy kernel
(`pseudo_par`) is flagged serial (`ŝ = 0.997`, CI low 0.92, `E(16) = 0.06`), the
communication-bound ring degrades fastest (per-iteration barrier latency, which is
exactly why it is excluded from the C2/C3 gates), and the injected-s family with the
larger serial fraction (`s20`) is still recovered inside 3 pp (`ŝ = 0.441` vs truth
`0.420`) — one family passing on a saturated host while the small-`s` family cannot
be resolved is consistent with the host's fixed per-run cost (~65 ms) being larger
than the serial work it is supposed to resolve (5 % of ≈1 s).

### 6.6 Reproducing the clean ledger

On a dedicated node (`host_precheck.host_ok == true`, i.e. `S(4) ≥ 3.0`):

```bash
python -m tools.scaling.harvest --plan tools/scaling/configs/full.json \
    --out artifacts/scaling_ledger/results_clean.json \
    --figdir artifacts/scaling_ledger/figures_clean --max-wait-min 120
```

`harvest` polls the precheck and only starts the sweep inside a genuinely quiet
window; the resulting ledger carries the precheck and the host load, so it is
self-describing.
