# Adapter guide — implementing SolverProtocol v1 for the eight libraries

This guide is written for whoever wires a *real* library behind the protocol. It
states what the shipped adapters do today, what a native adapter must add, and
which claims are deliberately not made.

## 0. Read this first: what the shipped adapters are

`tools/solver_adapter/adapters.py` registers one adapter per library
(`lbm, fem, fvm, dem, bem, sph, dg, cad`). Each one implements the protocol for
real — declaration, lifecycle, checkpoint, exchange, audit — but its numeric
payload comes from a **generic synthetic kernel**:

| kernel | update | invariant |
|---|---|---|
| `diffusion` | explicit zero-flux Laplacian, `g += dt·α·∇²g` | constant field is an exact fixed point; total conserved |
| `upwind` | first-order upwind in flux form, walls at both ends | `Σ g` conserved to round-off for either sign of the speed |
| `static` | clock/checksum only | geometry-only carrier |

Consequently every manifest reports `native_impl = false`, and the conservation
numbers in `artifacts/solver_adapter/conservation_matrix.json` are **protocol
properties** (they hold for any payload), not evidence about a library's
physics. Promoting an adapter means replacing `_seed_fields`/`step` with calls
into the library and flipping `NATIVE_IMPL = True` — at which point the manifest
starts telling the truth about a native implementation.

## 1. Declaration

```python
from tools.solver_adapter import (
    BoundarySpec, FieldSpec, MappingMode, SolverAdapter,
)

class MyAdapter(SolverAdapter):
    SOLVER_ID = "lbm"            # one of the eight registry identities
    VERSION = "1.2.0"
    FIELDS = (
        FieldSpec(name="density",    kind="density",   dim_vector=(1, -3, 0, 0)),
        FieldSpec(name="velocity_x", kind="density",   dim_vector=(0, 1, -1, 0)),
        FieldSpec(name="flux_x",     kind="flux",      dim_vector=(1, -2, -1, 0), face_normal=0),
    )
    BOUNDARIES = (BoundarySpec(name="x_lo", axis=0, side="lo", kind="neumann"),)
    MAPPING_MODES = (MappingMode.CONSERVATIVE, MappingMode.STANDARD)
    NATIVE_IMPL = True
```

Field naming rules: lower case, `^[a-z][a-z0-9_]{1,31}$`, synthetic and neutral.
The protocol vocabulary is `density`, `velocity_{x,y,z}`, `offset_{x,y,z}`,
`flux_{x,y,z}` — protocol slots, not physical claims; no domain word is
admitted anywhere in the package (checked by the vocabulary scan).

`dim_vector` is `(M, L, T, Θ)` exponents of the **value**; `kind` is the
**weighting law**. Both must be right: the first stops unit confusion, the
second stops a per-volume quantity being summed as if it were a total.

## 2. The four hooks

| hook | must do | must not do |
|---|---|---|
| `init(config)` | allocate state, seed fields into `self._fields` | touch the clock |
| `step(dt)` | advance by `dt`, return a `StepReport` with a state digest | silently clamp `dt` — refuse unstable steps |
| `get_output(name)` | return the declared field as `tools.coupling.mesh.Field` | return a wrong-kind payload |
| `cleanup()` | release state | be callable twice |

State lives in `self._fields: dict[str, np.ndarray]` (one entry per declared
field, length `mesh.n_cells`). That single convention gives every adapter
`checkpoint()`/`restore()` for free; a kernel with extra parameters overrides
`_state_payload()` / `_load_state_payload()` (see `_ScaffoldSolver`).

Lifecycle guards are installed automatically by `SolverAdapter.__init_subclass__`;
do not re-check the state in a hook, and do not bypass `_fields`.

## 3. Per-library wiring notes

| solver | natural records | protocol slot(s) | native work still needed |
|---|---|---|---|
| LBM | moments of the distribution, velocity components, mesh motion | `density`, `velocity_*`, `offset_*` | extract moments from the population arrays; ALE offset from `offset_*` |
| FEM | nodal DOF → cell averages | `density`, `offset_*`, `velocity_*` | consistent lumping of nodal values onto the box partition |
| FVM | cell averages, face fluxes | `density`, `velocity_*`, `flux_*` | face-flux array → per-cell normal flux (area weighted) |
| DEM | per-particle mass, particle velocity | `density` (**extensive**), `velocity_*` | particle → cell binning; note the law difference below |
| BEM | panel scalars, panel fluxes | `density`, `flux_*` | panel-to-box aggregation; v1 has no panel topology |
| SPH | particle averages | `density`, `velocity_*` | kernel-weighted particle → cell projection |
| DG | modal → cell, numerical fluxes | `density`, `flux_*` | modal-to-cell projection with the element mass matrix |
| CAD | volume measure, cell scalars | `density` | geometry sampling onto the box partition |

**Law difference to know:** DEM declares `density` as `kind = extensive` with
`dim_vector = (1, 0, 0, 0)` (per-cell mass), while LBM/FEM/FVM/SPH/DG/BEM/CAD
declare it as `kind = density` (per unit volume). An exchange of `density`
between DEM and any of those is therefore *refused* with a kind-mismatch reason —
by design. Exchange a per-volume or a per-cell-total field instead, or add an
explicit conversion field on the DEM side.

## 4. Adding a ninth library

1. pick a `SOLVER_ID` matching `^[a-z][a-z0-9_]{1,31}$`;
2. declare `FIELDS`/`BOUNDARIES`/`MAPPING_MODES` and implement the four hooks;
3. register the class in `ADAPTER_CLASSES` (`tools/solver_adapter/adapters.py`);
4. add native records to `NATIVE_FORMATS` (`formats.py`) — `scan_coverage()` must
   stay at 100 %, otherwise `CoverageError` is raised;
5. run `python3 -m pytest tests/test_solver_adapter -q` and
   `python3 tools/solver_adapter/acceptance.py`.

Contract tests are parametrised over `SOLVER_IDS`; a new identity is picked up by
the matrix tests as soon as it appears in `protocol.SOLVER_IDS`.

## 5. Exchange checklist (before quoting a coupling number)

1. `src.check_compatibility(dst, field)` — read the reasons, do not guess;
2. choose the mode: `conservative` for budgets, `standard` for accuracy on
   smooth fields (see the trade-off in `CONTRACT_TEST_REPORT.md`);
3. for flux quantities refine **tangentially**, never along the normal;
4. keep the returned `ExchangeRecord` (digests + measured residual) in the
   artifact — an exchange without its record is not evidence;
5. check `conservation_report()["within_tolerance"]` when aggregating.

## 6. Common failure modes

| symptom | cause | fix |
|---|---|---|
| `LifecycleError: action 'step' is illegal in state 'new'` | hook called before `init`, or after `cleanup` | construct → `init` → use → `cleanup` |
| `DimensionalError: dimensional mismatch` | two fields with the same name but different exponents | align the declarations, or introduce a conversion field |
| `CompatibilityError: kind mismatch` | e.g. DEM `density` (extensive) vs LBM `density` (per volume) | exchange a field of the same law |
| `MappingError: N destination cells are not fully covered` | destination extends beyond the source | make the destination nested, or map with an explicit partial-coverage policy at the `tools/coupling` level |
| `MappingError: surface measure changed` (flux) | refinement along the flux normal | refine tangentially |
| `StateMismatchError` on restore | tampered payload, different manifest/mesh, or another solver | restore into an identically configured adapter |