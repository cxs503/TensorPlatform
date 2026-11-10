# Property Registry

Physical/material property registry with **fail-closed semantics** for TensorPlatform.

## Overview

The property registry provides a domain-neutral mechanism for managing physical constants and material properties. It enforces strict provenance tracking and applicability domain checking, ensuring that properties are never used silently without proper documentation or outside their valid range.

**Key Principle**: Zero domain semantics in mechanism code. The registry knows nothing about ships, water, or steel. Domain data is injected as *data packets* that conform to the schema.

## Core Contract

### Fail-Closed Semantics

1. **No Provenance → No Use**: Properties must have a `source_sha256` and `confidence` level. Missing provenance raises `PropertyNoProvenance` (error code `PROP_NO_PROVENANCE`).

2. **Out-of-Domain → No Use**: Properties have applicability domains (temperature, pressure, salinity ranges). Requesting a property outside its domain raises `PropertyOutOfDomain` (error code `PROP_OUT_OF_DOMAIN`).

3. **No Silent Fallback**: Ever. Missing or out-of-range is a hard error.

### API

```python
from services.property_registry import (
    get_property,
    register_property,
    sweep_property,
    PropertyDefinition,
    PropertyState,
    DimensionVector,
    ApplicabilityDomain,
    Provenance,
    ConfidenceLevel,
)

# Register a property
prop = PropertyDefinition(
    attr_id="rho_sea_water_kg_m3",
    display_name="Sea Water Density",
    description="Density of standard sea water at 15°C",
    dimensions=DimensionVector(mass=1, length=-3),
    si_unit="kg/m^3",
    value=1025.0,
    confidence=ConfidenceLevel.MEASURED,
    provenance=Provenance(
        source_type="specification",
        reference="ITTC 1957",
        source_sha256="e3b0c44298fc1c14...",
    ),
    applicability=ApplicabilityDomain(
        temperature_range=(273.15, 313.15),  # 0-40°C
        salinity_range=(30.0, 40.0),  # 30-40 ppt
    ),
)
register_property(prop)

# Get property value
value = get_property("rho_sea_water_kg_m3")  # 1025.0

# Get with state checking
state = PropertyState(temperature_K=288.15, salinity_ppt=35.0)
value = get_property("rho_sea_water_kg_m3", state)  # 1025.0

# Out-of-domain raises error
state_hot = PropertyState(temperature_K=350.0)
get_property("rho_sea_water_kg_m3", state_hot)  # Raises PropertyOutOfDomain

# Sweep for sensitivity analysis
def endpoint(rho):
    return 0.5 * rho * 100.0 * 5.0**2  # Resistance calculation

results = sweep_property(
    "rho_sea_water_kg_m3",
    [1000.0, 1025.0, 1050.0],
    endpoint
)
# Returns: [{"value": 1000.0, "result": 1250000.0}, ...]
```

## Schema

### PropertyDefinition

Complete definition of a physical/material property:

- `attr_id`: Unique identifier (e.g., `rho_sea_water_kg_m3`)
- `display_name`: Human-readable name
- `description`: Detailed description
- `dimensions`: SI dimension vector [M, L, T, Θ, N, J, I]
- `si_unit`: SI unit string (e.g., `kg/m^3`)
- `value`: Nominal value in SI units
- `uncertainty`: Uncertainty in the value (optional)
- `confidence`: `MEASURED`, `DERIVED`, or `ASSUMED`
- `provenance`: Source provenance (required)
- `applicability`: Domain of applicability
- `temperature_dependence`: Temperature dependence model (optional)
- `replacement_chain`: Version/replacement chain (optional)
- `tags`: Tags for categorization
- `metadata`: Additional metadata

### Provenance

Source provenance for the property value:

- `source_type`: `literature`, `specification`, `experiment`, `database`
- `reference`: Full citation or specification identifier
- `source_sha256`: SHA256 hash of the source document (required)
- `table_or_figure`: Table/figure number (optional)
- `page_or_section`: Page/section (optional)
- `url`: URL to source (optional)

### ApplicabilityDomain

Domain of applicability:

- `temperature_range`: Valid temperature range in Kelvin (min, max)
- `pressure_range`: Valid pressure range in Pascal (min, max)
- `salinity_range`: Valid salinity range in ppt (min, max)
- `custom_ranges`: Custom parameter ranges
- `failure_mode`: `raise` (default) or `warn`

## Domain Data Packets

Domain-specific properties are injected as data packets. Example:

```python
from services.property_registry.data_packets.ship_properties import register_ship_properties

# Register all ship-domain properties
register_ship_properties()
```

The ship properties packet includes:
- `rho_sea_water_kg_m3`: Sea water density (1025.0 kg/m³)
- `rho_fresh_water_15c_kg_m3`: Fresh water density at 15°C (999.1 kg/m³)
- `nu_sea_water_15c_m2_s`: Sea water kinematic viscosity (1.1892e-6 m²/s)
- `nu_fresh_water_15c_m2_s`: Fresh water kinematic viscosity (1.1385e-6 m²/s)
- `g_m_s2`: Standard gravity (9.80665 m/s²)
- `default_water_density_kg_m3`: Default water density for API (1025.0 kg/m³)
- `default_kinematic_viscosity_m2_s`: Default kinematic viscosity for API (1.004e-6 m²/s)

## Coverage Audit

Scan modules for hardcoded physical constants:

```python
from services.property_registry import audit_module_constants, DebtLedger

# Audit a module
result = audit_module_constants(
    "api/domain/resistance_model.py",
    registry_attr_ids={"rho_sea_water_kg_m3", "g_m_s2"}
)

print(f"Coverage: {result['coverage_pct']:.1f}%")
print(f"Unregistered: {result['unregistered']}")

# Debt ledger tracks unregistered constants (monotone decreasing gate)
ledger = DebtLedger("property_debt_ledger.json")
print(f"Total debt: {ledger.total_debt()}")
```

## Error Codes

- `PROP_NO_PROVENANCE`: Property has no provenance (source_sha256 missing)
- `PROP_OUT_OF_DOMAIN`: State outside applicability domain
- `PROP_NOT_REGISTERED`: Property attr_id not registered

## Testing

Run the comprehensive test suite:

```bash
cd backend
pytest tests/test_property_registry.py -v
```

Tests cover:
- C1: Schema validation (negative control ≥3 cases)
- C2: Fail-closed no provenance (10/10)
- C3: Fail-closed out-of-domain (10/10)
- C4: Sweep/sensitivity analysis (≥5 endpoints, ≥3 values)
- C5: Coverage audit and debt ledger
- C6: Zero domain semantics (domain leak scan = 0 hits)

## Design Principles

1. **Zero Domain Semantics**: Mechanism code (registry, schema, errors) contains no domain-specific terms. Domain data is in data packets.

2. **Fail-Closed**: Missing provenance or out-of-domain state are hard errors. No silent fallback.

3. **Provenance Required**: Every property must have a source with SHA256 hash for integrity.

4. **Applicability Domains**: Properties have valid ranges for state parameters. Using them outside these ranges is an error.

5. **Monotone Decreasing Debt**: The coverage audit debt ledger only shrinks. Once a constant is identified, it stays until registered.

6. **Sweep for Sensitivity**: The sweep API allows testing how conclusions change when properties vary within uncertainty bands.

## Migration Guide

To migrate existing hardcoded constants:

1. Identify the constant in the code (e.g., `RHO_SEA_WATER = 1025.0`)
2. Create a `PropertyDefinition` with full provenance
3. Add it to the appropriate domain data packet
4. Replace the hardcoded constant with `get_property("rho_sea_water_kg_m3")`
5. Run the coverage audit to verify registration

Example migration:

```python
# Before
RHO_SEA_WATER = 1025.0
resistance = 0.5 * RHO_SEA_WATER * S * V**2

# After
from services.property_registry import get_property
resistance = 0.5 * get_property("rho_sea_water_kg_m3") * S * V**2
```

## References

- ITTC 1957 Model Test Performance Prediction
- CGPM 1901 (Standard Gravity)
- Kell, G.S. (1975) Density of water from 0 to 40°C
- Kestin, J. et al. (1978) Viscosity of Water
