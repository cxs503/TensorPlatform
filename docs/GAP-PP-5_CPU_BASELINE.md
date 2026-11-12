# GAP-PP-5 · differentiable path device line: CPU baseline with verified tau recovery

Mission m15444 (TensorPlatform) — anchor `GAP-PP-5-gpu-cluster`.
All numbers below are emitted programmatically from `runs/gap_pp5_cpu_baseline/results.json`.

## 1 Problem setting & baseline provenance

- **Gap**: JOUR-DIFFLBM §4 cost table needs a device footnote for the differentiable eager/autograd path.
- **Acceptance**: `differentiable_lbm.py` runs end to end on CPU + wall-clock table with verified tau recovery.
- **External anchors**: BGK shear-wave decay u=U₀ sin(ky) exp(-νk²t), ν=(τ-0.5)/3 (Krueger et al., *The Lattice Boltzmann Method: Principles and Practice*, Springer 2017, ch.4/10).
- **Ground truth**: τ* = 0.9 is exact by construction (forward model with known τ produces target field; inverse problem recovers τ from target).

## 2 Method & implementation

### 2.1 TensorLBM `_as_float` fix (root cause)

The differentiable path was broken by `input_contract._as_float` coercing 0-dim tensors to Python float via `float()`, which detaches the autograd graph. Fix: detect `torch.Tensor` and return `None` so `check_relaxation_time` passes the tensor through unchanged.

**File**: `TensorLBM/src/tensorlbm/input_contract.py`
**Change**: Added `isinstance(value, torch.Tensor)` guard before `float()` coercion.

### 2.2 Benchmark harness (TensorPlatform)

- `artifacts/gap_pp5_cpu_baseline/bench_diff_lbm_cpu.py`: Runs TensorLBM `differentiable_lbm.py` as subprocess across config matrix, measures wall-clock time, parses tau recovery accuracy.
- `artifacts/gap_pp5_cpu_baseline/analyze.py`: Produces summary tables, convergence plots, and verdict from results.json.
- `artifacts/gap_pp5_cpu_baseline/entry_gap_pp5.py`: Entry point tying benchmark + analysis together.

### 2.3 Device & environment

| parameter | value |
|---|---|
| CPU | loongarch64 / Loongson-3C6000/D |
| torch | 2.7.1 |
| threads | 8 |
| dtype | float64 |
| CUDA available | False |

## 3 Parameters

| parameter | value |
|---|---|
| τ* (ground truth) | 0.9 |
| τ init | 0.55 |
| lr (tau mode) | 2000 |
| grids | [64] (extended matrix in full run) |
| steps | [50] (extended matrix in full run) |
| iters | 60 |
| reps | 1 (extended in full run) |
| initial shear amplitude | 0.05 |

## 4 ref vs got

### 4.1 Tau recovery end-to-end (ref = τ* = 0.9, exact by construction)

| grid | steps | iters | wall [s] | τ_got | rel err [%] | judged |
|---|---|---|---|---|---|---|
| 64 | 50 | 60 | 33.50 | 0.89971 | 0.03 | ✅ PASS |

### 4.2 Gradient verification (unit test)

| test | expected | got | pass? |
|---|---|---|---|
| `_as_float(tensor)` returns None | None | None | ✅ |
| `check_relaxation_time(tensor)` preserves grad | requires_grad=True | requires_grad=True | ✅ |
| Tau recovery error < 15% | <15% | 0.03% | ✅ |

### 4.3 Shear-wave decay anchor (analytic ν = (τ-0.5)/3), fp64

| τ | ν analytic | ν effective | rel err [%] |
|---|---|---|---|
| 0.6 | 0.03333 | 0.03496 | 4.88 |
| 0.9 | 0.13333 | 0.13397 | 0.48 |
| 1.4 | 0.30000 | 0.29575 | 1.42 |

(Values from TensorLBM `tests/test_autograd.py` — all <15%)

## 5 Wall-clock / throughput table

| grid | steps | wall [s] | τ_got |
|---|---|---|---|
| 64 | 50 | 33.50 | 0.89971 |

## 6 Failure modes & scope / repro

### 6.1 Root cause fixed

The `_as_float` bug in TensorLBM `input_contract.py` was identified and fixed. Before fix: τ recovery failed at 38.89% error (τ stayed at 0.55). After fix: τ recovery succeeds at 0.03% error (τ → 0.89971).

### 6.2 Cluster leg

Full config matrix (3×3 grids×steps) requires ~30 min on CPU. The single-config run above validates the pipeline. Extended runs can be submitted via `run_cluster`.

### 6.3 GPU leg

No CUDA device available on control plane. GPU benchmark requires GPU node in pool — not achieved in this round.

### 6.4 Repro command

```bash
cd /root/tp-agent/work/m15444
TENSORLBM_ROOT=/root/tensor-ecosystem/TensorLBM \
  python3 artifacts/gap_pp5_cpu_baseline/entry_gap_pp5.py \
  --grids 64,128 --steps 10,50,200 --threads 8 --reps 2 --out runs/gap_pp5_cpu_baseline
```

### 6.5 Artifacts

| file | description |
|---|---|
| `runs/gap_pp5_cpu_baseline/results.json` | Raw benchmark results |
| `runs/gap_pp5_cpu_baseline/wallclock.csv` | Wall-clock summary table |
| `runs/gap_pp5_cpu_baseline/summary.md` | Markdown summary with verdict |
| `runs/gap_pp5_cpu_baseline/convergence_rep0.png` | Tau recovery convergence plot |
| `artifacts/gap_pp5_cpu_baseline/bench_diff_lbm_cpu.py` | Benchmark harness |
| `artifacts/gap_pp5_cpu_baseline/analyze.py` | Analysis pipeline |
| `artifacts/gap_pp5_cpu_baseline/entry_gap_pp5.py` | Entry point |
| `tests/test_gap_pp5_cpu_baseline.py` | Test suite |

## 7 Verdict

- **L0** (Document-only): ❌ — real code exists
- **L1** (Math/unit verification): ✅ — gradient check + analytic anchor
- **L2** (Independent functional implementation): ✅ — bench_diff_lbm_cpu.py: input→compute→output
- **L3** (System integration): ❌ — no integration callers in main solver/platform
- **L4** (Benchmark verification): ⚠️ — single config validated; full matrix pending

**Final level: L2** (with L1 verified, L4 partial)
