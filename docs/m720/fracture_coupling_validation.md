# FEM Cohesive → DEM Fracture Fragment Handoff (m720)

## Physical Background

In ice-zone navigation simulations, structural components (ice plates, hull sections) undergo progressive fracture modeled via **cohesive zone elements** in FEM. When a cohesive element fails (reaches critical energy release rate G_c via the Benzeggagh-Kenane (BK) mixed-mode law), the two adjacent surfaces separate, generating fracture fragments.

These fragments must be **seamlessly transferred** to a Discrete Element Method (DEM) solver as free granular particles for subsequent contact, drift, and secondary fragmentation simulation.

### Conservation Requirements

The handoff must satisfy strict conservation laws:

| Quantity | Symbol | Threshold | Physical Meaning |
|----------|--------|-----------|------------------|
| Mass | Σ m_i | < 1e-10 kg | No material creation/loss |
| Linear Momentum | Σ (m·v)_i | < 1e-8 kg·m/s | Newton's 2nd law consistency |
| Orphan Check | | True | Every fragment → exactly 1 particle |

## Implementation

### Module Structure

```
src/tensorphysics/fracture_coupling/
├── __init__.py          # Public API
└── coupling.py          # Core transfer engine
```

### Key Classes

- **`CohesiveFragment`**: Represents a failed cohesive element with mass, position, velocity, angular velocity, inertia tensor, and fracture surface properties.
- **`DEMParticle`**: Represents a DEM particle with equivalent properties plus radius and source tracking.
- **`FractureCouplingTransfer`**: Engine supporting two transfer modes:
  - `one_to_one`: Direct property copy (exact conservation)
  - `subdivided`: Mass-weighted 1:N split (near-exact conservation)

### Conservation Mechanism

**1:1 Mapping (exact by construction):**
```
m_particle = m_fragment          # direct copy
v_particle = v_fragment          # direct copy
p_particle = m_particle × v_particle = m_fragment × v_fragment = p_fragment
```

Since mass and velocity are copied without arithmetic, the error is **exactly zero** in IEEE 754 double precision.

**1:N Subdivision (bounded error):**
```
m_sub = m_fragment / N           # one division
v_sub = v_fragment               # direct copy
Σ m_sub × v_sub = N × (m/N) × v = m × v   # momentum conserved
```

The mass subdivision introduces rounding error bounded by `N × ε_machine × max_mass ≈ N × 2.2e-16 × max_mass`.

## Validation Results

### Acceptance Criteria

| Criterion | Threshold | Measured | Status |
|-----------|-----------|----------|--------|
| mass_transfer_error | < 1e-10 kg | 0.0 kg (1:1) / 4.55e-13 kg (1:32) | ✅ PASS |
| momentum_transfer_error | < 1e-8 kg·m/s | 0.0 kg·m/s (1:1) / 2.60e-13 kg·m/s (1:32) | ✅ PASS |
| no_orphan_particles | True | True | ✅ PASS |

### Parameter Sweep (1:1 Mapping)

| N fragments | Mass Error [kg] | Momentum Error [kg·m/s] |
|-------------|-----------------|------------------------|
| 1 | 0.0 | 0.0 |
| 10 | 0.0 | 0.0 |
| 100 | 0.0 | 0.0 |
| 1000 | 0.0 | 0.0 |
| 5000 | 0.0 | 0.0 |

### Subdivided Transfer (50 fragments, 1:N)

| Subdivisions | Mass Error [kg] | Momentum Error [kg·m/s] |
|--------------|-----------------|------------------------|
| 1:1 | 0.0 | 0.0 |
| 1:2 | 1.42e-14 | 1.56e-14 |
| 1:4 | 8.53e-14 | 4.90e-14 |
| 1:8 | 1.42e-13 | 5.84e-14 |
| 1:16 | 4.55e-13 | 1.37e-13 |
| 1:32 | 4.26e-14 | 2.60e-13 |

All values are **orders of magnitude** below thresholds.

## Test Suite

37 pytest cases covering:
- Single/multiple fragment transfer
- Large-scale (up to 5000 fragments)
- Subdivided transfer (1:N)
- Edge cases (zero velocity, extreme masses, empty input)
- Ice-specific scenario (plate fracture)
- Floating-point stress tests
- API contract verification
- Direct acceptance criteria tests

```bash
PYTHONPATH=src:$PYTHONPATH python -m pytest tests/test_fracture_coupling.py -v
# 37 passed in 1.39s
```

## Upstream Dependencies

- **m668 (F03)**: FEM cohesive element implementation with BK mixed-mode law
- **m671 (V04)**: Mixed-mode cohesive validation (Gc error < 1e-6, BK law verified)

## Limitations

1. Rigid body assumption for DEM particles (no internal deformation post-transfer)
2. Thermal effects not included in transfer
3. Fragment shape approximated as equivalent sphere for DEM
4. No contact detection during the transfer instant
5. Subdivided transfer does not conserve kinetic energy (intentional: fragmentation dissipates energy)

## Figures

- `conservation_error_vs_nfrag.png`: Mass/momentum error vs fragment count
- `subdivided_conservation.png`: Conservation under 1:N subdivision
- `mass_momentum_balance.png`: Total mass/momentum scaling
- `fragment_particle_mapping.png`: Visual mapping of fragments to particles
