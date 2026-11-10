# Multi-solver coupling checkpoint & bit-level reproducibility surface

*node `m9723` · generated 2026-11-10T14:17:48Z · host loongarch64 · python 3.11.6 · 128 cpus*

## 0. Scope and why this is a platform mechanism

A long coupled run is only as useful as its ability to be interrupted. This node adds the platform-generic layer that makes interruption safe: a **unified state protocol** (arrays + scalars + phase + logical time) that any solver can implement, an **atomic, zstd-compressed, chunk-digested checkpoint store**, an **invariant ledger** with acceptance gates, **coupling-instant bookkeeping** that distinguishes resumable from non-resumable instants, and a **restart supervisor** that verifies, quarantines and rolls back.

Zero domain vocabulary is involved: the layer never inspects what an array means. The three archetypes used for verification -- a discrete-velocity distribution array, a nodal field pair and a particle table -- are stand-ins for the state *shapes* any coupled code has.

## 1. Case / setup

| item | value |
|---|---|
| master clock | dt_master = 1.0 s, n_sub = 4 sub-steps (exact binary arithmetic) |
| scenario 1 (`kinetic_only`) | one distribution-array solver (D2Q9 streaming, 16x16x9 float64), 4 sub-steps/master |
| scenario 2 (`kinetic_field`) | + nodal field pair (64 nodes), 2 sub-steps/master, 1 coupling exchange |
| scenario 3 (`kinetic_field_particles`) | + particle table (16 particles, x[16,3], v[16,3]), 1 sub-step/master, 2 exchanges |
| gated invariant | E_total = Σ‖declared arrays‖² (weights 1) |
| diagnostic invariants | per-solver sub-energies (energy legitimately moves between solvers) |
| horizon | 1000 master steps, checkpoint at step 500 |
| codec | zstd level 3 via zstd-cli, frame `28b52ffd` |

## 2. Method

**State protocol.** `SolverSnapshot` (arrays, scalars, phase, logical time) and `StateBundle` (step + one snapshot per solver). Identity is a bit-level digest: per array `sha256(dtype|shape|chunk_bytes|chunk digests)` -- a Merkle root over fixed 8 MiB chunks, so hashing scales with cores -- and per bundle a digest over the per-solver digests. Floats in scalars are canonicalised as `float.hex()` so the digest is exact.

**Store.** Payloads are written into `.tmp-<step>-<uuid>/`, `fsync`-ed, then `os.replace`-d into place; the manifest carries the codec identity, the coupling grid, the sync verdict, the energy reading, per-chunk digests, `prev_digest`/`prev_step` (hash chain) and its own digest. Every verification failure raises instead of falling back to a stale state.

**Energy ledger.** Registered functionals (`fn(bundle) -> float`) with a gated subset; `check_boundary` (|ΔE|/|E| < 1e-10) and `check_drift` (< 1e-3 over 1000 steps). The archetypes are built from permutations/orthogonal rotations/coupling rotations, so E_total is conserved up to rounding *by construction* -- which is why a damped negative control is run through the same ledger: it must be flagged, otherwise the zeros would prove nothing.

**Coupling instants.** `CouplingTimeGrid` + `assess_sync`: a bundle is a coupling instant iff every solver's phase is 0 and every logical time is bit-equal to `master_time(step)`. Policies: `STRICT` (refuse), `WARN` (record `sync_point=false`), `ALLOW` (raw capture, forensics).

**Restart.** Newest-first, full verification before acceptance, corrupt candidates quarantined with a reason file, append-only JSONL audit, `RestartFailed` when nothing survives.

## 3. Results

### 3.1 Acceptance verdicts

| criterion | verdict | key numbers | artifact |
|---|---|---|---|
| AC1 | **PASS** | 4 runs, all_bit_identical=True | `artifacts/m9723/results.json#stages.repro` |
| AC2 | **PASS** | boundary residual 0.0e+00 (< 1e-10); drift 1.51e-15 (< 1e-3); control 7.040e-03 detected=True | `artifacts/m9723/results.json#stages.energy` |
| AC3 | **PASS** | digest differs=True, strict refused=True, mid resume diverges=True, sync resume matches=True, phase spread={'kinetic': 1, 'nodal': 2, 'particles': 4} | `artifacts/m9723/results.json#stages.sync` |
| AC4 | **PASS** | raw 20952 B -> 19315 B (ratio 0.9219), sha256 equal=True | `artifacts/m9723/results.json#stages.corruption.ac4_compression` |
| AC5 | **PASS** | step 300, byte 1065, detected=True (CheckpointUnreadable) | `artifacts/m9723/results.json#stages.corruption.ac5_bit_flip` |
| AC6 | **PASS** | rolled back 300 -> 200, quarantined=True, no_data_loss=True | `artifacts/m9723/results.json#stages.corruption.ac6_rollback` |
| AC7 | **FAIL** | measured save 0.071 GiB/s, load 0.093 GiB/s; 100 GiB -> 1407 s / 1079 s vs 60 s target (shortfall 23.5x / 18.0x) | `artifacts/m9723/results.json#stages.bench` |
| AC8 | **PASS** | 101 pytest cases, 4 figures | `artifacts/m9723/pytest_summary.json` |

### 3.2 Bit-level reproducibility (AC1)

| scenario | mode | checkpoint at | continuous sha256 | restarted sha256 | identical |
|---|---|---|---|---|---|
| kinetic_only | in-process | 500 | `00c034fc3841618a8d0753b5…` | `00c034fc3841618a8d0753b5…` | True |
| kinetic_field | in-process | 500 | `91ab61b81b9cae7b2768fa45…` | `91ab61b81b9cae7b2768fa45…` | True |
| kinetic_field_particles | in-process | 500 | `91c6b15474f2f3b19b41e565…` | `91c6b15474f2f3b19b41e565…` | True |
| kinetic_field_particles | cross-process | 500 | `91c6b15474f2f3b19b41e565…` | `91c6b15474f2f3b19b41e565…` | True |

### 3.3 Energy ledger (AC2)

| scenario | boundary residual | drift over 1000 steps | gated |
|---|---|---|---|
| kinetic_only | 0.0e+00 | 0.00e+00 | ['E_total'] |
| kinetic_field | 0.0e+00 | 2.17e-16 | ['E_total'] |
| kinetic_field_particles | 0.0e+00 | 1.51e-15 | ['E_total'] |

Negative control (same ledger, damped collision term, 4000 steps): drift 0.0070 -> gate False (detected). Conservative run over the same horizon: 5.6e-15.

### 3.4 Coupling instants (AC3)

* sync-point checkpoint digest `a1ef29a268608a5bb1e6d11a…`, phases {'kinetic': 0, 'nodal': 0, 'particles': 0}
* mid-interval checkpoint digest `88f9b6e11294744c6503b7d8…`, phases {'kinetic': 1, 'nodal': 2, 'particles': 4}, time spread 0.75 s
* STRICT policy refused the mid-interval state: True
* resuming the mid-interval state changes the trajectory: True; resuming the sync-point state reproduces it: True
* re-saving a restored sync state is digest-stable: True

### 3.5 Corruption detection and rollback (AC4-AC6)

* zstd round-trip: 20952 B -> 19315 B, sha256 identical: True
* one-bit flip at byte 1065 of the newest payload -> CheckpointUnreadable, load refused: True
* supervisor restored step 200 (attempted 300), quarantine `artifacts/m9723/drill/ckpt-000000000300.corrupt-20261110T141658-f11e69`, remaining checkpoints [100, 200]
* continuation after rollback equals the uninterrupted run: True

### 3.6 Throughput (AC7) — measured, and honestly extrapolated

| payload | solvers | save [s] | save [GiB/s] | load [s] | load [GiB/s] | ratio | lossless |
|---|---|---|---|---|---|---|---|
| 0.25 GiB | 3 | 3.52 | 0.071 | 2.70 | 0.093 | 0.958 | True |
| 0.50 GiB | 3 | 7.95 | 0.063 | 6.06 | 0.083 | 0.958 | True |

Stage decomposition at 0.25 GiB on loongarch64: sha256 819 MB/s, zstd compress 228 MB/s, write+fsync 300 MB/s, read 504 MB/s, decompress 162 MB/s.
Parallel payload writers (same payload, 6 solvers): 1 workers -> 0.050 GiB/s, 3 workers -> 0.082 GiB/s.

## 4. Figures

* `artifacts/m9723/figures/fig1_energy_ledger.png` — Left: relative drift of the gated total invariant for the three coupling scenarios (1000 master steps) against the AC2 1e-3 gate; the damped control is detected. Right: per-solver sub-energies exchange at coupling instants while E_total stays flat.
* `artifacts/m9723/figures/fig2_bit_reproducibility.png` — Left: bit-level restart equality for the three coupling scenarios (plus a cross-process continuation). Right: a checkpoint taken between coupling instants resumes onto a different trajectory, while the sync-point checkpoint reproduces the run exactly.
* `artifacts/m9723/figures/fig3_io_throughput.png` — Left: measured save/load throughput vs payload size with the rate AC7 would require (100 GiB in 60 s). Right: per-stage throughput on this host, showing that SHA-256 and zstd compression -- not the filesystem -- are the limiting stages.
* `artifacts/m9723/figures/fig4_rollback_drill.png` — Audit trail of the corruption drill: the flipped checkpoint is detected, quarantined, and the supervisor rolls back to the last verified step; the continuation is bit-identical to the uninterrupted run.

## 5. Error assessment against the criteria

| quantity | value | criterion | < 3 % / met? |
|---|---|---|---|
| boundary invariant residual | 0.0e+00 | < 1e-10 | met (exactly 0) |
| 1000-step drift (gated) | 1.51e-15 | < 1e-3 | met |
| restart state digest | identical (sha256) | exact | met (0 bits different) |
| mid-interval checkpoint digest | differs | must differ | met |
| AC7 save time for 100 GiB | 1407 s (extrapolated from 0.071 GiB/s) | < 60 s | **NOT met (23.5x short)** |

## 6. Caveats, limitations, what is *not* claimed

1. **AC7 is an honest FAIL on this hardware.** The measured pipeline is limited by CPU, not by the filesystem: single-stream SHA-256 on loongarch64 runs at ≈127 MB/s (no SHA extensions) and level-3 zstd compresses incompressible float64 noise at a few hundred MB/s while saving ≈4 % of bytes. Reaching 60 s for 100 GiB needs ≈1.67 GiB/s end-to-end, i.e. 15-19x more than measured. Paths that would close the gap (not implemented here): x86 SHA-NI nodes, sampling/spot verification policies, or hardware-accelerated digest.
2. **The archetypes are conservative by construction.** Streaming is a permutation, the nodal update and the coupling exchanges are orthogonal rotations. The zeros in §3.3 therefore measure *the checkpoint layer does not inject or destroy the invariant*, not the quality of any physical discretisation. The damped control is what makes the statement falsifiable.
3. **Not yet exercised:** solver state that lives in non-numpy objects (closures, GPU buffers), multi-node distributed snapshots (MPI/collective I/O), and asynchronous checkpointing while the time loop keeps running. The protocol would carry them as opaque arrays, but the mechanism for coordinating a distributed barrier is out of scope here.
4. **Time arithmetic uses powers of two.** The sync test relies on `dt ∈ {0.25, 0.5, 1.0}` being exact in binary floating point. With a non-dyadic dt the synchronisation test must use the grid tolerance instead of bit equality (the code already supports a tolerance, set to 0 by default).
5. **Bit-level reproducibility is machine-local by design.** numpy transcendental functions can differ across ISAs; the digests here are exact for a fixed binary on a fixed host, which is the contract a resume needs.

