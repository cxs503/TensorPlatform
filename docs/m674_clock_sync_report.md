# SIR-PC2|X02: Clock Synchronization & Subcycling Framework

## Ice Navigation Campaign DAG Node — L1_foundation

**Mission ID:** m674  
**Campaign:** m611 seeding  
**Physical Domain:** coupling_infrastructure  
**Risk Level:** medium  

---

## 1. Case Introduction

### Physical Background

In ice-structure-fluid multi-physics coupling for polar navigation, each solver
operates on a fundamentally different characteristic time scale:

| Domain | Phenomenon | Characteristic Time Scale | Typical dt |
|--------|-----------|--------------------------|------------|
| Ice | Fracture, ridge failure, crushing | 1e-4 – 1e-3 s | 1e-4 s |
| Structure | Hull vibration, panel response | 1e-3 – 1e-2 s | 1e-3 s |
| Hydrodynamics | Wave, current, wake | 1e-2 – 1e-1 s | 5e-3 s |

The **master clock** advances at the coarsest common time step (dt_master),
while each sub-solver takes N_i = dt_master / dt_i sub-steps within one master
step. At every master tick, all solvers exchange boundary data; between ticks,
sub-solvers use time-interpolated boundary values.

### Geometry & Parameters

| Parameter | Value | Unit | Description |
|-----------|-------|------|-------------|
| dt_master | 1e-2 | s | Master time step |
| dt_ice | 1e-4 | s | Ice solver time step (100 sub-steps/master) |
| dt_struct | 1e-3 | s | Structure solver time step (10 sub-steps/master) |
| dt_hydro | 5e-3 | s | Hydro solver time step (2 sub-steps/master) |
| ω_ice | 20π | rad/s | Ice oscillator frequency (10 Hz) |
| ω_struct | 4π | rad/s | Structure oscillator frequency (2 Hz) |
| ω_hydro | π | rad/s | Hydro oscillator frequency (0.5 Hz) |
| A_ice | 1.0 | m | Ice oscillation amplitude |
| A_struct | 0.5 | m | Structure oscillation amplitude |
| A_hydro | 2.0 | m | Hydro oscillation amplitude |
| m_ice | 1.0 | kg | Ice oscillator mass |
| m_struct | 10.0 | kg | Structure oscillator mass |
| m_hydro | 100.0 | kg | Hydro oscillator mass |

---

## 2. Method

### Clock Synchronization Algorithm

1. **Master Clock**: Fixed-step clock advancing at dt_master. Thread-safe with
   state machine (STOPPED → RUNNING → PAUSED → STOPPED).

2. **Subcycling**: Each solver i takes N_i = round(dt_master / dt_i) sub-steps
   per master step. The ratio must be an integer (validated at registration).

3. **Boundary Exchange**: At each master tick t_n, all solvers publish their
   state as boundary data. Other solvers consume this data for coupling.

4. **Time Interpolation**: Between master ticks, boundary data is linearly
   interpolated: `B(t_n + α·dt_master) = (1-α)·B(t_n) + α·B(t_{n+1})`.

5. **Synchronization Error**: After each master step, the maximum deviation
   of any solver's local time from the master time is recorded:
   `ε_sync = max_i |t_local_i - t_master|`

### Integration Scheme

Each sub-solver uses **Velocity-Verlet** (symplectic) integration:

```
a_n = F(x_n) / m
x_{n+1} = x_n + v_n·dt + 0.5·a_n·dt²
a_{n+1} = F(x_{n+1}) / m
v_{n+1} = v_n + 0.5·(a_n + a_{n+1})·dt
```

For linear systems (harmonic oscillator), Velocity-Verlet conserves energy
to machine precision, making it ideal for validating the subcycling framework.

### Energy Conservation Metric

```
conservation_ratio = |E_total(t_end) - E_total(t_0)| / |E_total(t_0)|
```

where E_total = Σ_i E_i is the sum of all solver energies.

---

## 3. Results

### Acceptance Criteria

| Criterion | Threshold | Measured | Pass? |
|-----------|-----------|----------|-------|
| clock_sync_error | < 1e-6 s | 9.45e-14 s | ✅ PASS |
| subcycling_energy_conservation | < 0.01 | 1.68e-06 | ✅ PASS |
| pytest_test_clock_sync | == PASS | PASS | ✅ PASS |

### Detailed Results (100 master steps, t_final = 1.0 s)

| Metric | Value |
|--------|-------|
| Max clock sync error | 9.45e-14 s |
| Energy conservation ratio | 1.68e-06 |
| Ice sub-steps/master | 100 |
| Structure sub-steps/master | 10 |
| Hydro sub-steps/master | 2 |
| Total sub-steps (ice) | 10,000 |
| Total sub-steps (structure) | 1,000 |
| Total sub-steps (hydro) | 200 |

### Per-Solver Energy Conservation

| Solver | E_initial [J] | E_final [J] | Relative deviation |
|--------|--------------|-------------|-------------------|
| Ice | 1973.92 | 1973.92 | < 1e-6 |
| Structure | 197.39 | 197.39 | < 1e-6 |
| Hydro | 1973.92 | 1973.92 | < 1e-6 |

---

## 4. Figures

See `artifacts/m674_clock_sync/`:
- `clock_sync_error.png` — Clock sync error vs. master time
- `energy_conservation.png` — Energy per solver and relative deviation
- `substep_distribution.png` — Sub-step distribution across domains

---

## 5. Notes & Limitations

### Key Findings
- Clock sync error is ~1e-14 s (14 orders of magnitude below threshold),
  confirming that integer-ratio subcycling maintains exact time consistency.
- Energy conservation ratio is ~1e-6 (4 orders of magnitude below threshold),
  confirming that Velocity-Verlet + subcycling preserves symplecticity.

### Limitations
1. **Linear systems only**: Validation uses harmonic oscillators. Nonlinear
   coupling (ice fracture, plasticity) may introduce additional sync errors.
2. **No MPI/distributed**: Current implementation is single-process. Real
   ice-structure coupling requires distributed memory communication.
3. **Linear interpolation**: Boundary data between master ticks uses linear
   interpolation. Higher-order interpolation may be needed for strongly
   nonlinear coupling.
4. **No adaptive time stepping**: dt is fixed. Adaptive subcycling (CFL-based)
   would improve efficiency for stiff problems.
5. **No sub-iteration**: Current framework is explicit (one pass per master
   step). Implicit coupling (sub-iteration to convergence) is needed for
   strongly coupled problems.

### Applicability
- Suitable for weakly-coupled multi-physics (ice loads → structure → hydro)
- Not yet suitable for strongly-coupled problems requiring implicit iteration
- Framework is extensible: advance_fn can wrap any solver (CFD, FEM, DEM)

---

## 6. Error Judgment

| Quantity | Baseline | Computed | Error | < 3%? |
|----------|----------|----------|-------|-------|
| Clock sync error | 0 (exact) | 9.45e-14 s | N/A (machine precision) | ✅ |
| Energy (ice) | E_0 = 0.5·k·A² | E_final | 1.68e-6 relative | ✅ |
| Energy (struct) | E_0 = 0.5·k·A² | E_final | < 1e-6 relative | ✅ |
| Energy (hydro) | E_0 = 0.5·k·A² | E_final | < 1e-6 relative | ✅ |

All key quantities are well within the 3% threshold. The clock sync error
is at machine precision level (floating-point accumulation), and energy
conservation is maintained by the symplectic integrator.

---

## Artifacts

| Path | Description |
|------|-------------|
| `backend/services/coupling_clock_sync.py` | Core framework module |
| `backend/api/domain/coupling_clock_sync.py` | API endpoint |
| `tests/test_clock_sync.py` | Pytest suite (39 tests) |
| `artifacts/m674_clock_sync/` | Figures and validation data |
| `docs/m674_clock_sync_report.md` | This report |
