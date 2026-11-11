# GAP-PP-5 · differentiable path device line: documented CPU baseline a

Mission m15359 (TensorPlatform) — anchor `GAP-PP-5-gpu-cluster`.
All numbers below are emitted programmatically from `run_x86_controlplane/results.json`.

## 1 Problem setting & baseline provenance
- Gap: JOUR-DIFFLBM §4 cost table needs a device footnote for the differentiable eager/autograd path (published data = activation memory on one RTX 5090, TensorLBM `docs/differentiable_path.md`; no wall clock, no pool device).
- Acceptance: `differentiable_lbm.py` runs end to end on CPU + a wall-clock table, or GPU node into the pool.
- External anchors: BGK shear-wave decay u=U0 sin(ky) exp(-nu k^2 t), nu=(tau-0.5)/3 (Krueger et al., *The Lattice Boltzmann Method: Principles and Practice*, Springer 2017, ch.4/10; carrier identical to `TensorLBM/benchmarks/verified/shear_wave_decay`); ground truth of the inverse problem tau*=0.9 is exact by construction.

## 2 Method & implementation
- Harness `artifacts/gap_pp5_cpu_baseline/bench_diff_lbm_cpu.py` (child-per-config, median over reps, VmHWM peak RSS, autograd vs central-FD gradient check).
- Device: loongarch64 / Loongson-3C6000/D / 128 cores / torch 2.7.1 / threads 8 / dtype float64, CUDA available=False.
- TensorLBM commit `16b48fe4de98c054c68674f60b11245e79f5489d`; example CLI run unmodified as a subprocess.
- **A/B control**: while building the baseline we found a live regression. `input_contract.check_relaxation_time` (guard from commit 5db76f6d, mission m14763, 2026-11-11 19:49) coerces a 0-dim tensor tau via `float()`, so `collide_bgk` detaches tau and the tau-mode gradient is exactly zero. Variant A = as shipped; variant B = identical checkout + a 2-line private overlay of `_as_float` in `/tmp` (checkout untouched) that returns tensors unchanged.

## 3 Parameters
| parameter | value |
|---|---|
| tau* (ground truth) | 0.9 |
| tau init | 0.55 |
| lr (tau mode) | 2000 (example default) |
| grids | [64, 128, 256] |
| steps | [10, 50, 200] |
| reps (median) | 2 |
| initial shear amplitude | 0.05 |

## 4 ref vs got
### 4.1 tau recovery end-to-end (ref = tau* = 0.9, exact by construction)
| variant | grid | steps | iters | wall [s] | tau_got | rel err [%] | judged |
|---|---|---|---|---|---|---|---|
| A | 64 | 50 | 60 | 14.57 | 0.55 | 38.8889 | FAIL >15% |
| B | 64 | 50 | 60 | 43.1 | 0.89971 | 0.0322 | PASS <15% |

### 4.2 gradient vs central finite difference (fp64)
| variant | graph present | dL/dtau autograd | FD | rel err |
|---|---|---|---|---|
| A | False | 0.000000e+00 | -2.135995e-05 | 1.000e+00 |
| B | True | -2.135995e-05 | -2.135995e-05 | 1.013e-09 |

### 4.3 shear-wave decay anchor (analytic nu = (tau-0.5)/3), fp64
| tau | nu analytic | nu effective | rel err [%] |
|---|---|---|---|
| 0.6 | 0.03333333 | 0.03496079 | 4.8824 |
| 0.9 | 0.13333333 | 0.13397157 | 0.4787 |
| 1.4 | 0.3 | 0.29575345 | 1.4155 |

### 4.4 upstream regression test A/B (`pytest tests/test_autograd.py -k collide_step_gradients`)
| variant | rc | summary |
|---|---|---|
| A | 1 | 2 failed, 3 passed, 10 deselected, 1 warning in 6.17s |
| B | 0 | 5 passed, 10 deselected, 1 warning in 4.90s |

## 5 Wall-clock / throughput table (variant B = intended differentiable path, median of reps)
| grid | steps | fwd [s] | fwd+bwd [s] | MLUPS fwd | MLUPS fwd+bwd | peak RSS [MiB] |
|---|---|---|---|---|---|---|
| 64 | 10 | 0.0279 | 0.2760 | 1.471 | 0.148 | 668.1 |
| 64 | 50 | 0.1204 | 0.5201 | 1.701 | 0.394 | 736.8 |
| 64 | 200 | 2.9441 | 29.9994 | 0.278 | 0.027 | 858.4 |
| 128 | 10 | 0.0711 | 0.2094 | 2.305 | 0.783 | 974.7 |
| 128 | 50 | 0.4470 | 1.4332 | 1.832 | 0.572 | 1509.4 |
| 128 | 200 | 1.7009 | 9.5242 | 1.927 | 0.344 | 3800.9 |
| 256 | 10 | 0.2920 | 2.3865 | 2.244 | 0.275 | 1659.0 |
| 256 | 50 | 2.6615 | 14.6481 | 1.231 | 0.224 | 3598.4 |
| 256 | 200 | 11.0743 | 13.1432 | 1.184 | 0.997 | 19211.9 |

Sensitivity: 3 grids (64/128/256) x 3 step counts (10/50/200) = 9 configurations; throughput stays in 0.28-2.31 MLUPS (fwd) and 0.03-1.00 MLUPS (fwd+bwd); peak RSS grows with steps (autograd activation storage), the CPU analogue of the published GPU memory table.

## 6 Failure modes / scope / repro
- **Cluster leg not completed in this round**: job `cj1081` (stage-dir submit to TensorLBM, 8 cores, timeout 2400 s, full 9-config matrix + both example variants) was still `queued` (289 jobs ahead) when the round closed; no cluster numbers are claimed. Numbers above come from the control-plane x86_64 host, which is *not* a pool node.
- No GPU node was added to the pool (no CUDA device on the control plane, cluster nodes are loong64 CPU); the GPU-alternative half of the anchor is NOT achieved.
- Tau-mode convergence curve figure omitted: the example's `convergence_tau.csv` parsed to an empty loss column in this run (loss values are reported through the CLI line, which is what 4.1 uses) — no curve was fabricated.
- repro_cmd (self-contained tree, x86): `cd /tmp/gp5_stage && PYTHONPATH=src python3 bench_diff_lbm_cpu.py --grids 64,128,256 --steps 10,50,200 --threads 8 --reps 2 --example-cases 64:50 --example-iters 60 --gradcheck-grid 64 --gradcheck-steps 50 --out runs/gap_pp5_cpu_baseline` then `python3 analyze.py --run runs/gap_pp5_cpu_baseline`.
- repro_cmd (cluster, queued): `run_cluster submit repo=TensorLBM stage_dir=<stage> cores=8 timeout=2400 cmd=...` = job `cj1081`.

## 7 Findings handed back to TensorLBM (cross-repo, not fixed here)
- `check_relaxation_time` destroys the autograd graph for tensor tau → `examples/differentiable_lbm.py --mode tau` cannot recover tau (variant A: 0.55 vs 0.9, 38.8889% error, loss flat). With the 2-line overlay (variant B) tau reaches 0.89971 (0.0322% error) and `pytest -k collide_step_gradients` flips red→green (A: 2 failed/3 passed, B: 5 passed).
- Fix belongs in TensorLBM (`_as_float`: return None for torch tensors), pinned by the upstream test that is currently red on main.
