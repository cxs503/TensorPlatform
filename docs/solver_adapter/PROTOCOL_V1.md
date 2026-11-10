# SolverProtocol v1 — cross-library adapter protocol

**Version:** `solver-protocol/v1`  **Schema:** [`schemas/solver_protocol_v1.json`](../../schemas/solver_protocol_v1.json)
**Package:** `tools/solver_adapter/`  **Acceptance record:** [`artifacts/solver_adapter/acceptance.json`](../../artifacts/solver_adapter/acceptance.json)

## 1. Why a protocol

Eight solver libraries (LBM, FEM, FVM, DEM, BEM, SPH, DG, CAD) each carry their
own notion of a mesh, a field and a boundary. Field exchange between them
currently costs a bespoke script per pair, and every script re-answers the same
questions badly: which array is which, in what units, and did the conserved
total survive the transfer?

SolverProtocol v1 fixes the answers once:

* an **adapter manifest** says what a library can publish and in what units;
* a **lifecycle** (`new → ready → finalized`) says when a value may be read;
* one **exchange operation** with two modes — `conservative` (total-preserving)
  and `standard` (second-order) — carries every field across every boundary,
  and returns a *measured* residual, not an assurance.

The numerical work is not duplicated: meshes, overlap projections and
conservation metrics already live in `tools/coupling/` and are used unchanged.

## 2. Objects

### 2.1 `AdapterManifest`

| field | meaning |
|---|---|
| `protocol_version` | `solver-protocol/v1` (schema `const`) |
| `solver.solver_id` | library identity, `^[a-z][a-z0-9_]{1,31}$` |
| `solver.version` | semantic version of the adapter |
| `solver.native_impl` | `true` only when the adapter drives the real library |
| `solver.repo_commit` | provenance pointer (empty when unknown) |
| `mesh` | `{dim, n_cells, lo, hi, topology}`; v1 topology set is `box` |
| `fields[]` | `{name, kind, dim_vector, face_normal?, description}` |
| `boundaries[]` | `{name, axis, side, kind, value}` with `kind ∈ {dirichlet, neumann, periodic}` |
| `mapping_modes[]` | subset of `{conservative, standard}`, at least one |
| `deterministic` | whether repeated runs reproduce bit-identical payloads |

`fields[].kind` is the **exchange law** (how values are weighted into a
conserved total), `fields[].dim_vector` is the **unit** of the value itself,
expressed as exponents over `(M, L, T, Θ)`:

| kind | measure | conserved total |
|---|---|---|
| `density` | cell volume / face area | `Σ value · V` |
| `flux` | face area | `Σ value · A` (needs `face_normal`) |
| `extensive` | unity | `Σ value` |
| `scalar` | cell volume | not conserved (precision law) |

A per-cell `velocity` component is therefore `kind = density`,
`dim_vector = (0, 1, -1, 0)`: the law says *how it is summed*, the vector says
*what it is measured in*. Keeping the two apart is what lets one gate serve all
eight libraries.

### 2.2 `MappingSpec` / `ExchangeRequest`

`ExchangeRequest` is the literal field set required by the protocol:
`solver_id`, `version`, `target_solver`, `field_name`, `mapping`, plus
`protocol_version`.

`MappingSpec` = `{mode, operator, sum_tol, moment_tol, moment_exact}` with

* `mode = conservative` ⇒ `operator ∈ {overlap_l2, rbf_conservative}`;
* `mode = standard` ⇒ any operator, typically `overlap_p1`;
* `0 < sum_tol < 1`, `0 < moment_tol < 1`.

`MappingSpec` refuses a combination that cannot honour its own promise
(conservative + `nearest_scaled`, `moment_exact` + non-conserving operator).

### 2.3 `SolverAdapter`

Four abstract hooks — `init(config)`, `step(dt)`, `get_output(field_name)`,
`cleanup()` — and eight concrete operations provided once by the base class:

| # | method | contract |
|---|---|---|
| 1 | `manifest()` | protocol-conformant self-description |
| 2 | `check_compatibility(target, field)` | reports (does not raise) why a boundary is inadmissible |
| 3 | `export_field(field)` | validated (shape/finiteness/kind) export |
| 4 | `import_field(field, field)` | validated adoption onto the local mesh |
| 5 | `map_to(target, field, mode=…)` | the exchange: mode switch + landed payload + audit record |
| 6 | `conservation_report()` | aggregate of every exchange this adapter took part in |
| 7 | `checkpoint()` | serialise fields, clocks, kernel state, provenance |
| 8 | `restore(state)` | adopt a checkpoint (digest-verified, `new` only) |

Lifecycle guards are installed by `__init_subclass__`, so a subclass cannot
forget to check its own state and a caller cannot step an uninitialised solver.

## 3. Lifecycle

```
NEW --init()--> READY --step()*--> READY --cleanup()--> FINALIZED
                  |  |
                  |  +--> get_output()   (read-only, stays READY)
                  |  +--> checkpoint()   (read-only, stays READY)
                  +--> checkpoint()      (also legal in NEW)
NEW --restore(payload)--> READY            (restore into a live solver is refused)
```

The trace is append-only (`lifecycle.entries[]` with `seq/action/from/to`), so a
checkpoint proves which transitions produced it.

Two digests per checkpoint:

* `state_digest` — solver identity, manifest digest, step index, time, field
  values, kernel parameters. Two solvers with equal `state_digest` are the same
  solver.
* `digest` — everything above **plus** the lifecycle trace, i.e. state *and*
  provenance. A restore appends a `restore` entry, so this digest necessarily
  differs after a round trip while `state_digest` agrees.

## 4. Mapping semantics

Residual definition (measured, not asserted):

```
S   = Σ value · measure(kind)            # measure: volume | face area | unity
rel = |S_dst − S_src| / max(|S_src|, |S_dst|, eps)
```

v1 acceptance: `rel ≤ sum_tol`, default `sum_tol = 1e-3` (**0.1 %**).

Admissibility rules that fail closed:

1. **same kind and same `dim_vector`** — a per-volume quantity never silently
   becomes a per-cell total (`DimensionalError`);
2. **same mesh dimension** in both manifests;
3. **full coverage of the destination** — a partially covered destination raises
   instead of being zero-filled (`MappingError`, from `RemapDomainError`);
4. **flux quantities only across meshes that share the discretisation along the
   flux normal.** Refining *along* the normal doubles the number of faces over
   the same physical surface, so the surface measure itself changes and the
   totals are no longer comparable; refine tangentially instead. v1 refuses the
   former rather than rescaling silently;
5. **a boundary needs two libraries** — `solver_id == target_solver` is reported
   incompatible;
6. **finite payloads only** — NaN/inf is an error at export *and* at import.

Error taxonomy (`tools/solver_adapter/errors.py`): `ProtocolViolation`,
`LifecycleError`, `FieldLookupError`, `DimensionalError`, `CompatibilityError`,
`MappingError`, `ConservationError`, `StateMismatchError`, `CoverageError`, all
deriving from `AdapterError(ValueError)`.

## 5. Evidence levels of this delivery

| claim | level | evidence |
|---|---|---|
| protocol objects, schema, lifecycle, checkpoint/restore | **REAL** | `tests/test_solver_adapter/` |
| conservative / standard mapping with measured residuals | **REAL** (operators from `tools/coupling`) | `artifacts/solver_adapter/conservation_matrix.json` |
| the 0.1 % gate can fail (negative control) | **REAL** | same artifact, `negative_control` block |
| eight library adapters implement the protocol | **REAL** (scaffold) | manifests, `native_impl = false` |
| the eight adapters drive the real libraries | **NOT CLAIMED / WITHHELD** | no library repository is present in this worktree |
| native I/O record coverage against real library output | **WITHHELD** | declarations only, `verified_against_repo = 0` |

## 6. Non-goals (v1)

* topologies other than axis-aligned boxes;
* surface/panel connectivity (a `box` mesh with a face-normal flux is used to
  represent surface quantities — the abstraction is geometric, not physical);
* time-accurate co-simulation clocks (that is `tools/coupling/clock.py`);
* automatic unit inference — units are declared, never guessed.

## 7. Machine checks

```bash
python3 -m pytest tests/test_solver_adapter -q          # 579 contract cases
python3 -m mypy tools/solver_adapter --ignore-missing-imports
python3 tools/solver_adapter/acceptance.py --figures    # writes artifacts/solver_adapter/
```