# Contract test report — SolverProtocol v1

**Node:** `p337w1-n01-solver-adapter-protocol`  **Protocol:** `solver-protocol/v1`
**Reproduce:** `python3 -m pytest tests/test_solver_adapter -q` ·
`python3 -m mypy tools/solver_adapter --ignore-missing-imports` ·
`python3 tools/solver_adapter/acceptance.py --figures`

Every number below is copied from a machine-written artifact; the artifact path
is given next to it so the number can be re-derived rather than trusted.

## 1. Acceptance criteria

| # | criterion | result | evidence |
|---|---|---|---|
| C1 | 8 libraries' declared input/output records map 100 % onto protocol fields | **29 / 29 records, coverage 1.000**, 8/8 solvers, both directions | `artifacts/solver_adapter/format_coverage.json` |
| C2 | 4 abstract + 8 concrete methods, mypy clean | **4 abstract, 8 concrete, 0 mypy errors in the package** | `artifacts/solver_adapter/mypy.txt` |
| C3 | ≥100 contract cases, all green, covering input validation / output validation / lifecycle / cross-library exchange | **579 collected, 578 passed, 1 skipped, 0 failed** | `artifacts/solver_adapter/acceptance.json` |
| C4 | conservative mapping conserves the total to <0.1 % | **density 3.3e-16, flux 2.2e-16** (tolerance 1e-3); negative control **1.4e-2 → gate fails as designed** | `artifacts/solver_adapter/conservation_matrix.json` |
| C5 | zero domain vocabulary in code, tests, docs, schema | **0 hits / 20 files scanned** | `artifacts/solver_adapter/acceptance.json` |
| C6 | backward compatibility | **0 tracked files modified outside the new package**, pre-existing coupling suite exit code 0 | `artifacts/solver_adapter/acceptance.json` |
| C7 | protocol spec + adapter guide + this report | `docs/solver_adapter/PROTOCOL_V1.md`, `ADAPTER_GUIDE.md`, `CONTRACT_TEST_REPORT.md` | this file |

C6 is the weakest of the seven and is stated as such: the eight solver
repositories are separate checkouts and are **not** present in this worktree, so
their test suites cannot be executed here. Backward compatibility is argued
structurally (nothing outside `tools/solver_adapter/`, `tests/test_solver_adapter/`,
`docs/solver_adapter/`, `artifacts/solver_adapter/` and one new schema file is
touched) and by re-running the pre-existing coupling suites
(`tests/test_coupling_remap.py`, `tests/test_coupling_exchange_gate.py`,
`tests/test_multiphysics_coupling.py` → green).

## 2. Contract-test inventory

| file | what it proves |
|---|---|
| `test_protocol_schema.py` | schema/manifest/request validation; the on-disk schema equals the generated one (no hand-edited drift); every adapter manifest round-trips |
| `test_base_contract.py` | the abstraction shape: 4 hooks, 8 methods, no ninth public member; partial subclasses cannot be instantiated; guards installed on every adapter |
| `test_lifecycle_checkpoint.py` | legal/illegal transitions per adapter; checkpoint → restore reproduces state; tampering, foreign solver, foreign mesh all refused; stability guards |
| `test_conservation_mapping.py` | conservative vs standard, admissibility rules, negative controls (gate can fail) |
| `test_cross_library_exchange.py` | 8×8 exchange matrix with the incompatible pairs refused for a stated reason; FEM→LBM mesh-motion field; LBM→DEM velocity; import validation; conservation aggregation |
| `test_zero_domain_and_coverage.py` | vocabulary scan (platform lexicon), import-layer boundaries, C1 coverage and its four failure modes |

Parametrisation over the eight solver identities is what turns a handful of
invariants into 579 cases (578 passed, 1 skipped); each case is an independent
assertion about a different adapter, field or mesh pair, not a repeated constant.

Repository-level baseline (recorded so it is not mistaken for damage caused
here): a whole-tree `pytest tests --collect-only` reports **74 pre-existing
collection errors** in ship-performance test modules — identical with and without
`tests/test_solver_adapter` (7510 vs 8089 cases collected, the difference being
exactly the 579 cases added here, 0 of them in error).

## 3. Conservation measurements

Relative total residual `|S_dst − S_src| / max(|S_src|, |S_dst|)`; tolerance
`1e-3` (0.1 %). Source mesh 4³, destination resolutions 4³ … 12³, all eight
adapters (`artifacts/solver_adapter/conservation_matrix.json`, block
`density_conservative`):

| quantity kind | worst residual | verdict |
|---|---|---|
| `density` (volume measure), 40 exchanges | 3.3e-16 | passes with 13 orders of margin |
| `flux` (surface measure), tangential refinement ×2/×3 | 2.2e-16 | passes |
| lossy control (`nearest_scaled`, correction off, non-nested destination) | 1.39e-2 (1.39 %) | **fails as designed** |

The last row matters most: an acceptance threshold that nothing can violate is
decoration. The control runs through the *same* code path with only the
conservation correction disabled, and lands 14× above the tolerance.

## 4. Precision vs conservation (measured trade-off)

Linear field, source refined together with the destination
(4³→8³, 6³→12³, 8³→16³, 12³→24³), error against the exact linear field
(`artifacts/solver_adapter/conservation_matrix.json`, block `tradeoff`):

| source cells | `conservative` (`overlap_l2`) max error | `standard` (`overlap_p1`) max error |
|---|---|---|
| 64 | 1.25e-1 | 2.2e-16 |
| 216 | 8.33e-2 | 8.9e-16 |
| 512 | 6.25e-2 | 0.0 |
| 1728 | 4.17e-2 | 4.4e-16 |

`conservative` is a piecewise-constant (volume-weighted) projection: first order
in the source cell size, error halving as the source is refined — and it is the
mode that keeps budgets exact. `standard` reconstructs the linear field exactly
on nested meshes while still preserving the total up to projection round-off.
Neither mode dominates: the protocol exposes both and records which one produced
a given payload (`ExchangeRecord.operator`).

## 5. Known limitations (not hidden)

1. **No native library was executed.** All eight adapters run a synthetic kernel
   and declare `native_impl = false`. Exchange and conservation numbers are
   protocol properties; they say nothing about a library's physics.
2. **I/O record coverage is protocol-side only** (`verified_against_repo = 0`).
   The 29 declared records are internally consistent, but no library repository
   or output artifact was inspected. Promoting an entry requires a pointer to a
   real source/artifact — follow-up work, not a claim.
3. **v1 topology is box only.** A surface quantity is represented by a box mesh
   with a face-normal flux; there is no panel connectivity.
4. **Flux exchange is restricted** to meshes sharing the normal discretisation
   (refinement along the flux normal changes the surface measure; the exchange
   fails closed rather than rescaling).
5. **One contract case is skipped by design** — it targets a field a given
   adapter does not declare (a `${field}` slot the solver has no carrier for).
   It reports as `skipped`, never as `passed`.
6. **mypy** is clean for `tools/solver_adapter`, with `--ignore-missing-imports`.
   Five pre-existing errors in `tools/coupling` (untouched files) remain as
   baseline debt and are quoted verbatim in `artifacts/solver_adapter/mypy.txt`.

## 6. Residual risk and rollback

* **Risk:** another part of the platform starts importing `tools.solver_adapter`
  and treats `native_impl = false` adapters as real solvers. **Mitigation:** the
  flag is in every manifest and asserted by
  `test_every_adapter_is_declared_synthetic`, so a reader that ignores it is
  ignoring an explicit contract; the docs repeat it in the first section.
* **Risk:** a future edit loosens `sum_tol` to make a red gate green.
  **Mitigation:** the negative-control cases pin the gate from below — widening
  the tolerance makes them fail.
* **Rollback:** the delivery is purely additive (one package, one test directory,
  one docs directory, one schema file, one artifacts directory). Reverting the
  commit removes it without touching any pre-existing module.