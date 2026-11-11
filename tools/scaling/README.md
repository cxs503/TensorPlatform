# tools/scaling — parallel scalability ledger harness

Domain-neutral measurement harness for the question *"how many cores actually buy
throughput?"*. It produces a machine-readable ledger (strong/weak scaling
efficiency, Amdahl/Gustafson fits with bootstrap CIs) plus the figures and the
core-quota recommendation that go with it.

Nothing here knows about physics: kernels are injected through a two-method
protocol, so solver kernels, data pipelines and synthetic micro-benchmarks are all
measured by the same protocol and scored by the same acceptance gates.

## Kernel protocol

```python
class Kernel(Protocol):
    name: str
    mode: str                  # "strong" | "weak"
    def setup(self, n_cores: int) -> None: ...
    def run(self, n_cores: int) -> None: ...   # one timed unit of work
    def checksum(self) -> int: ...             # deterministic integer fingerprint
    def teardown(self) -> None: ...
```

`checksum()` must be **partition invariant** — the same bit pattern for every
`n_cores`. Integer arithmetic (or reduction modulo 2**64) is required; floating
point is not associative under re-partitioning and cannot satisfy this.

## Measurement protocol

| element | choice | why |
|---|---|---|
| repetitions | 5 timed runs + 1 warm-up per core count | warm-up absorbs page faults / lazy allocation |
| order | core counts visited in random order each round | de-correlates drift (thermal, co-tenant load) from core count |
| statistic | median of the timed runs | robust to a single slow run |
| timing | `perf_counter` around a barrier-bounded region | excludes fork/exit cost; measures the parallel region only |
| workers | persistent fork pool per core count, `SharedMemory` for data | no per-run process start-up inside the timed region |
| checksums | recorded verbatim for every run | C1 gate is a bitwise comparison, not a tolerance |

Latest host's fixed per-run cost (barrier + worker wake-up) is measured by the
serial-fraction calibration and reported as `fixed_overhead_s`: it sets the floor
on the smallest serial fraction a host can resolve.

## Kernel families

| kernel | bottleneck | construction |
|---|---|---|
| `stencil5p` | compute + memory bandwidth | 5-point Jacobi stencil on an int64 grid, contiguous row blocks; ghost rows are the neighbours' data rows in shared memory, so one barrier per iteration is the only communication |
| `halo_ring` | communication latency | ring exchange over `Pipe`; each iteration ships one ≤64 KB slab to the next rank (≤64 KB keeps `send` non-blocking, hence deadlock-free) |
| `serial_frac` | injection truth | perfectly parallel phase + a rank-0-only serial phase; `s` is *calibrated on the measured host* by a feedback loop, never assumed |
| `pseudo_par` | negative control | accepts `n_cores`, forks `n_cores` workers, but every unit of work runs on rank 0 — a ledger that cannot flag `s → 1` here is broken |

`stencil5p`/`halo_ring` support `mode="weak"` (work per rank held constant) for
Gustafson analysis; `serial_frac`/`pseudo_par` are strong-scaling only.

## Fitting

* Amdahl: `A(n) = 1 / (s + (1 - s) / n)`, `s` fitted by grid sweep + golden-section
  refinement, `R²` on speedups.
* Gustafson: `G(n) = s_g + (1 - s_g) * n` from weak-scaling data, closed-form least
  squares.
* 95 % CI: percentile bootstrap over the repetitions (resample timings → median →
  re-derive speedup against the resampled `n = 1` median → refit).
* A flat objective (all speedups ≈ 1) is reported as `degenerate_flat_objective`
  with `s → 1` instead of a fake precise number.

## Acceptance criteria (evaluated mechanically)

| id | criterion |
|---|---|
| C1 | every checksum bitwise identical (all repetitions *and* all core counts) |
| C2 | stencil strong scaling `E(8) ≥ 0.70`, `E(16) ≥ 0.50` |
| C3 | stencil weak scaling `E(8) ≥ 0.85` |
| C4 | injected-s families: `R² ≥ 0.98`, `|ŝ − s_injected| ≤ 3 pp`, 95 % CI covers truth |
| C5 | pseudo-parallel control: `ŝ ≥ 0.90`, CI lower bound `≥ 0.85`, `E(16) ≤ 0.15` |
| C6 | per-kernel efficiency curves (PNG + sha256) and core-quota recommendation |

## Usage

```bash
python -m tools.scaling.cli \
    --plan tools/scaling/configs/full.json \
    --out  artifacts/scaling_ledger/results.json \
    --figdir artifacts/scaling_ledger/figures
# exit code 0 == all criteria pass, 1 == at least one criterion failed
```

`configs/smoke.json` is a fast functional check (2 reps, 3 core counts).
Heavy runs belong on a compute node (see `docs/perf/SCALING_LEDGER.md` for the
platform rule); the CLI prints progress per run so long sweeps can be tailed.

## Tests

```bash
python -m pytest tests/scaling/test_scaling_harness.py -q
```

Covers deterministic partitioning, partition-invariant checksums for every kernel
family, Amdahl recovery on synthetic data with known truth, the pseudo-parallel
control, acceptance rejection paths (C1/C2/C5/C6) and worker-exception propagation.