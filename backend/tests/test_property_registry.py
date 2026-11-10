"""Comprehensive tests for the property registry.

Tests cover all acceptance criteria:
C1: Schema validation - missing source_sha256 or confidence → FAIL
C2: Fail-closed - no provenance → PROP_NO_PROVENANCE (10/10)
C3: Out-of-domain → PROP_OUT_OF_DOMAIN (10/10)
C4: Sweep / sensitivity analysis
C5: Coverage audit
C6: Zero domain semantics in mechanism code
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import pytest

from services.property_registry.schema import (
    PropertyDefinition,
    PropertyState,
    DimensionVector,
    ApplicabilityDomain,
    Provenance,
    ConfidenceLevel,
    TemperatureDependence,
    ReplacementChain,
)
from services.property_registry.errors import (
    PropertyNoProvenance,
    PropertyOutOfDomain,
    PropertyNotRegistered,
    PROP_NO_PROVENANCE,
    PROP_OUT_OF_DOMAIN,
    PROP_NOT_REGISTERED,
)
from services.property_registry.registry import (
    PropertyRegistry,
    get_property,
    register_property,
    sweep_property,
    reset_registry,
    get_default_registry,
)
from services.property_registry.audit import (
    audit_module_constants,
    DebtLedger,
    _is_physical_constant,
    _extract_constants_from_ast,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_registry():
    """Reset the default registry before each test."""
    reset_registry()
    yield
    reset_registry()


def _make_prop(
    attr_id: str = "test_prop",
    value: float = 1025.0,
    source_sha256: str = "abc123",
    confidence: ConfidenceLevel = ConfidenceLevel.MEASURED,
    temp_range=None,
    sal_range=None,
) -> PropertyDefinition:
    """Helper to create a property definition for testing."""
    return PropertyDefinition(
        attr_id=attr_id,
        display_name="Test Property",
        description="A test property",
        dimensions=DimensionVector(mass=1, length=-3),
        si_unit="kg/m^3",
        value=value,
        confidence=confidence,
        provenance=Provenance(
            source_type="literature",
            reference="Test Reference",
            source_sha256=source_sha256,
        ),
        applicability=ApplicabilityDomain(
            temperature_range=temp_range,
            salinity_range=sal_range,
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# C1: Schema validation
# ─────────────────────────────────────────────────────────────────────────────

class TestSchemaValidation:
    """C1: Schema contract - missing source_sha256 or confidence → FAIL."""
    
    def test_missing_source_sha256_raises(self):
        """Property with empty source_sha256 cannot be registered."""
        prop = _make_prop(source_sha256="")
        with pytest.raises(PropertyNoProvenance) as exc_info:
            register_property(prop)
        assert exc_info.value.error_code == PROP_NO_PROVENANCE
    
    def test_none_source_sha256_raises(self):
        """Property with None source_sha256 cannot be registered."""
        prop = _make_prop()
        prop.provenance.source_sha256 = ""
        with pytest.raises(PropertyNoProvenance):
            register_property(prop)
    
    def test_valid_property_registers(self):
        """Property with valid provenance registers successfully."""
        prop = _make_prop()
        register_property(prop)
        assert get_property("test_prop") == 1025.0
    
    def test_dimension_vector(self):
        """Dimension vector correctly represents SI dimensions."""
        dv = DimensionVector(mass=1, length=-3)
        assert dv.to_tuple() == (1, -3, 0, 0, 0, 0, 0)
    
    def test_confidence_levels(self):
        """All confidence levels are valid."""
        for level in ConfidenceLevel:
            prop = _make_prop(confidence=level)
            register_property(prop)
            assert get_property("test_prop") == 1025.0
            reset_registry()
    
    def test_attr_id_validation_empty(self):
        """Empty attr_id is rejected."""
        with pytest.raises(Exception):
            PropertyDefinition(
                attr_id="",
                display_name="Test",
                description="Test",
                dimensions=DimensionVector(),
                si_unit="kg/m^3",
                value=1.0,
                confidence=ConfidenceLevel.MEASURED,
                provenance=Provenance(
                    source_type="test",
                    reference="test",
                    source_sha256="abc",
                ),
            )
    
    def test_attr_id_validation_invalid_chars(self):
        """attr_id with invalid characters is rejected."""
        with pytest.raises(Exception):
            PropertyDefinition(
                attr_id="test prop!",  # space and ! are invalid
                display_name="Test",
                description="Test",
                dimensions=DimensionVector(),
                si_unit="kg/m^3",
                value=1.0,
                confidence=ConfidenceLevel.MEASURED,
                provenance=Provenance(
                    source_type="test",
                    reference="test",
                    source_sha256="abc",
                ),
            )
    
    def test_negative_control_3_cases(self):
        """C1 negative control: ≥3 cases where schema validation fails."""
        # Case 1: empty source_sha256
        with pytest.raises(PropertyNoProvenance):
            register_property(_make_prop(source_sha256=""))
        
        # Case 2: empty attr_id
        with pytest.raises(Exception):
            PropertyDefinition(
                attr_id="",
                display_name="T", description="T",
                dimensions=DimensionVector(), si_unit="m",
                value=1.0, confidence=ConfidenceLevel.MEASURED,
                provenance=Provenance(source_type="t", reference="t", source_sha256="a"),
            )
        
        # Case 3: invalid attr_id characters
        with pytest.raises(Exception):
            PropertyDefinition(
                attr_id="bad prop!",
                display_name="T", description="T",
                dimensions=DimensionVector(), si_unit="m",
                value=1.0, confidence=ConfidenceLevel.MEASURED,
                provenance=Provenance(source_type="t", reference="t", source_sha256="a"),
            )


# ─────────────────────────────────────────────────────────────────────────────
# C2: Fail-closed - no provenance → PROP_NO_PROVENANCE
# ─────────────────────────────────────────────────────────────────────────────

class TestFailClosedNoProvenance:
    """C2: Inject property without provenance → 100% PROP_NO_PROVENANCE."""
    
    def test_no_provenance_raises_on_register(self):
        """Cannot register a property without source_sha256."""
        prop = _make_prop(source_sha256="")
        with pytest.raises(PropertyNoProvenance) as exc_info:
            register_property(prop)
        assert exc_info.value.error_code == PROP_NO_PROVENANCE
    
    def test_no_provenance_10_of_10(self):
        """10/10 attempts to use unprovenanced property fail with PROP_NO_PROVENANCE."""
        for i in range(10):
            prop = _make_prop(attr_id=f"no_prov_{i}", source_sha256="")
            with pytest.raises(PropertyNoProvenance) as exc_info:
                register_property(prop)
            assert exc_info.value.error_code == PROP_NO_PROVENANCE
            assert "no provenance" in str(exc_info.value).lower() or "source_sha256" in str(exc_info.value).lower()
    
    def test_no_silent_numeric_output(self):
        """No silent numeric output when provenance is missing."""
        prop = _make_prop(source_sha256="")
        try:
            register_property(prop)
            # If we get here, registration didn't raise - that's a failure
            pytest.fail("Should have raised PropertyNoProvenance")
        except PropertyNoProvenance:
            pass  # Expected
    
    def test_unregistered_property_raises(self):
        """Getting an unregistered property raises PropertyNotRegistered."""
        with pytest.raises(PropertyNotRegistered) as exc_info:
            get_property("nonexistent_property")
        assert exc_info.value.error_code == PROP_NOT_REGISTERED


# ─────────────────────────────────────────────────────────────────────────────
# C3: Out-of-domain → PROP_OUT_OF_DOMAIN
# ─────────────────────────────────────────────────────────────────────────────

class TestFailClosedOutOfDomain:
    """C3: State outside applicability domain → 100% PROP_OUT_OF_DOMAIN."""
    
    def test_temperature_out_of_range(self):
        """Temperature outside range raises PROP_OUT_OF_DOMAIN."""
        prop = _make_prop(temp_range=(273.15, 313.15))  # 0-40°C
        register_property(prop)
        
        state = PropertyState(temperature_K=350.0)  # 77°C - out of range
        with pytest.raises(PropertyOutOfDomain) as exc_info:
            get_property("test_prop", state)
        assert exc_info.value.error_code == PROP_OUT_OF_DOMAIN
    
    def test_salinity_out_of_range(self):
        """Salinity outside range raises PROP_OUT_OF_DOMAIN."""
        prop = _make_prop(sal_range=(30.0, 40.0))
        register_property(prop)
        
        state = PropertyState(salinity_ppt=50.0)  # out of range
        with pytest.raises(PropertyOutOfDomain) as exc_info:
            get_property("test_prop", state)
        assert exc_info.value.error_code == PROP_OUT_OF_DOMAIN
    
    def test_out_of_domain_10_of_10(self):
        """10/10 out-of-domain requests fail with PROP_OUT_OF_DOMAIN."""
        prop = _make_prop(temp_range=(273.15, 313.15))
        register_property(prop)
        
        for i in range(10):
            temp = 320.0 + i * 5  # 320K, 325K, ... all above 313.15K
            state = PropertyState(temperature_K=temp)
            with pytest.raises(PropertyOutOfDomain) as exc_info:
                get_property("test_prop", state)
            assert exc_info.value.error_code == PROP_OUT_OF_DOMAIN
    
    def test_in_domain_succeeds(self):
        """State within range succeeds."""
        prop = _make_prop(temp_range=(273.15, 313.15))
        register_property(prop)
        
        state = PropertyState(temperature_K=288.15)  # 15°C - in range
        value = get_property("test_prop", state)
        assert value == 1025.0
    
    def test_no_applicability_constraint_always_succeeds(self):
        """Property with no applicability constraints always succeeds."""
        prop = _make_prop()  # No temp/sal ranges
        register_property(prop)
        
        state = PropertyState(temperature_K=500.0)  # Any temperature
        value = get_property("test_prop", state)
        assert value == 1025.0
    
    def test_state_not_in_domain_dict(self):
        """State parameters not in domain dict are ignored (not constrained)."""
        prop = _make_prop(temp_range=(273.15, 313.15))
        register_property(prop)
        
        # pressure not constrained, so this should succeed
        state = PropertyState(temperature_K=288.15, pressure_Pa=1e9)
        value = get_property("test_prop", state)
        assert value == 1025.0


# ─────────────────────────────────────────────────────────────────────────────
# C4: Sweep / sensitivity analysis
# ─────────────────────────────────────────────────────────────────────────────

class TestSweepProperty:
    """C4: Sweep property values and evaluate endpoint sensitivity."""
    
    def test_basic_sweep(self):
        """Basic sweep returns results for each value."""
        prop = _make_prop(value=1025.0)
        register_property(prop)
        
        def endpoint(val):
            return val * 2  # Simple endpoint
        
        results = sweep_property("test_prop", [1000.0, 1025.0, 1050.0], endpoint)
        
        assert len(results) == 3
        assert results[0]["value"] == 1000.0
        assert results[0]["result"] == 2000.0
        assert results[1]["value"] == 1025.0
        assert results[1]["result"] == 2050.0
        assert results[2]["value"] == 1050.0
        assert results[2]["result"] == 2100.0
    
    def test_sweep_restores_original_value(self):
        """Sweep restores the original value after completion."""
        prop = _make_prop(value=1025.0)
        register_property(prop)
        
        sweep_property("test_prop", [999.0], lambda v: v)
        
        # Original value should be restored
        assert get_property("test_prop") == 1025.0
    
    def test_sweep_with_endpoint_error(self):
        """Sweep captures endpoint errors without crashing."""
        prop = _make_prop(value=1025.0)
        register_property(prop)
        
        def bad_endpoint(val):
            if val < 1010:
                raise ValueError("Too low!")
            return val
        
        results = sweep_property("test_prop", [1000.0, 1025.0], bad_endpoint)
        
        assert len(results) == 2
        assert results[0]["error"] is not None
        assert "Too low" in results[0]["error"]
        assert results[1]["result"] == 1025.0
    
    def test_sweep_unregistered_raises(self):
        """Sweep on unregistered property raises."""
        with pytest.raises(PropertyNotRegistered):
            sweep_property("nonexistent", [1.0], lambda v: v)
    
    def test_sweep_5_endpoints_3_values(self):
        """C4: ≥5 endpoints, ≥3 values each."""
        # Register multiple properties
        for i in range(5):
            prop = _make_prop(attr_id=f"prop_{i}", value=100.0 * (i + 1))
            register_property(prop)
        
        values = [0.9, 1.0, 1.1]  # ±10% sweep
        
        all_results = {}
        for i in range(5):
            attr_id = f"prop_{i}"
            base_val = 100.0 * (i + 1)
            
            def endpoint(v, base=base_val):
                return v / base  # Normalized result
            
            results = sweep_property(attr_id, [base_val * v for v in values], endpoint)
            all_results[attr_id] = results
        
        assert len(all_results) == 5
        for attr_id, results in all_results.items():
            assert len(results) == 3
    
    def test_sweep_json_output(self):
        """Sweep results can be serialized to JSON."""
        prop = _make_prop(value=1025.0)
        register_property(prop)
        
        results = sweep_property("test_prop", [1000.0, 1025.0, 1050.0], lambda v: v * 2)
        
        # Should be JSON-serializable
        json_str = json.dumps(results)
        parsed = json.loads(json_str)
        assert len(parsed) == 3


# ─────────────────────────────────────────────────────────────────────────────
# C5: Coverage audit
# ─────────────────────────────────────────────────────────────────────────────

class TestCoverageAudit:
    """C5: Scan modules for hardcoded constants, debt ledger."""
    
    def test_is_physical_constant_density(self):
        """Recognizes density constants."""
        assert _is_physical_constant("RHO_SEA_WATER", 1025.0) == "sea_water_density"
        assert _is_physical_constant("rho_fresh_water", 998.0) == "fresh_water_density"
    
    def test_is_physical_constant_viscosity(self):
        """Recognizes viscosity constants."""
        assert _is_physical_constant("NU_SEA_WATER_15C", 1.1892e-6) == "sea_water_viscosity"
        assert _is_physical_constant("nu_fresh_water", 1.004e-6) == "fresh_water_viscosity"
    
    def test_is_physical_constant_gravity(self):
        """Recognizes gravity constants."""
        assert _is_physical_constant("G", 9.81) == "gravity"
        assert _is_physical_constant("g_m_s2", 9.81) == "gravity"
    
    def test_is_physical_constant_non_physical(self):
        """Non-physical constants are not recognized."""
        assert _is_physical_constant("MAX_ITERATIONS", 1000) is None
        assert _is_physical_constant("PI", 3.14159) is None
    
    def test_extract_constants_from_ast(self):
        """Extracts numeric constants from Python source."""
        import ast
        source = """
RHO = 1025.0
G = 9.81
MAX_ITER = 100
name = "hello"
"""
        tree = ast.parse(source)
        constants = _extract_constants_from_ast(tree)
        
        names = [c[0] for c in constants]
        assert "RHO" in names
        assert "G" in names
        assert "MAX_ITER" in names
    
    def test_audit_module_constants(self):
        """Audit a module for physical constants."""
        # Create a temporary module
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("""
RHO_SEA_WATER = 1025.0
G = 9.81
MAX_ITER = 100
""")
            f.flush()
            module_path = f.name
        
        try:
            result = audit_module_constants(module_path, registry_attr_ids=set())
            
            assert result["physical_constants"] >= 2  # RHO and G
            assert result["unregistered"] >= 2
            assert result["coverage_pct"] == 0.0  # Nothing registered
        finally:
            os.unlink(module_path)
    
    def test_debt_ledger(self):
        """Debt ledger tracks unregistered constants."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            ledger_path = Path(f.name)
        
        try:
            ledger = DebtLedger(ledger_path)
            ledger.add_debt("test.py", "RHO", 1025.0, 10)
            
            assert ledger.total_debt() == 1
            assert ledger.get_unregistered_count("test.py") == 1
            
            ledger.mark_registered("test.py", "RHO", "rho_sea_water")
            assert ledger.total_debt() == 0
        finally:
            ledger_path.unlink(missing_ok=True)
    
    def test_debt_ledger_monotone_decreasing(self):
        """Debt ledger only shrinks (monotone decreasing gate)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            ledger_path = Path(f.name)
        
        try:
            ledger = DebtLedger(ledger_path)
            
            # Add debt
            ledger.add_debt("test.py", "RHO", 1025.0, 10)
            ledger.add_debt("test.py", "G", 9.81, 20)
            assert ledger.total_debt() == 2
            
            # Register one
            ledger.mark_registered("test.py", "RHO", "rho_sea_water")
            assert ledger.total_debt() == 1
            
            # Can't go back up by re-adding same constant
            ledger.add_debt("test.py", "RHO", 1025.0, 10)
            assert ledger.total_debt() == 1  # Still 1
        finally:
            ledger_path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# C6: Zero domain semantics
# ─────────────────────────────────────────────────────────────────────────────

def _has_domain_word(source: str, word: str) -> bool:
    """Check if source contains a domain word as a whole word (not substring)."""
    # Use word boundary regex
    pattern = r'\b' + re.escape(word) + r'\b'
    return bool(re.search(pattern, source, re.IGNORECASE))


class TestZeroDomainSemantics:
    """C6: Mechanism code has zero domain semantics."""
    
    def test_registry_no_domain_imports(self):
        """Registry module doesn't import domain-specific code."""
        import services.property_registry.registry as reg_module
        import inspect
        
        source = inspect.getsource(reg_module)
        
        # Should not contain domain-specific terms (as whole words)
        domain_terms = ["ship", "water", "sea", "ice", "steel", "hull"]
        for term in domain_terms:
            assert not _has_domain_word(source, term), f"Registry contains domain term: {term}"
    
    def test_schema_no_domain_imports(self):
        """Schema module doesn't import domain-specific code."""
        import services.property_registry.schema as schema_module
        import inspect
        
        source = inspect.getsource(schema_module)
        
        domain_terms = ["ship", "water", "sea", "ice", "steel", "hull"]
        for term in domain_terms:
            assert not _has_domain_word(source, term), f"Schema contains domain term: {term}"
    
    def test_errors_no_domain_imports(self):
        """Errors module doesn't import domain-specific code."""
        import services.property_registry.errors as errors_module
        import inspect
        
        source = inspect.getsource(errors_module)
        
        domain_terms = ["ship", "water", "sea", "ice", "steel", "hull"]
        for term in domain_terms:
            assert not _has_domain_word(source, term), f"Errors contains domain term: {term}"
    
    def test_domain_data_in_packet(self):
        """Domain data is in the data packet, not in mechanism code."""
        # The ship_properties module should contain domain terms
        from services.property_registry.data_packets import ship_properties
        import inspect
        
        source = inspect.getsource(ship_properties)
        
        # Should contain domain terms
        assert "sea" in source.lower() or "water" in source.lower()
    
    def test_synthetic_property_works(self):
        """Synthetic (non-domain) properties work fine."""
        prop = PropertyDefinition(
            attr_id="synthetic_density",
            display_name="Synthetic Density",
            description="A synthetic property for testing",
            dimensions=DimensionVector(mass=1, length=-3),
            si_unit="kg/m^3",
            value=1234.5,
            confidence=ConfidenceLevel.ASSUMED,
            provenance=Provenance(
                source_type="synthetic",
                reference="Test",
                source_sha256="synthetic123",
            ),
        )
        register_property(prop)
        assert get_property("synthetic_density") == 1234.5


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIntegration:
    """Integration tests with ship domain data packet."""
    
    def test_ship_properties_register(self):
        """Ship properties can be registered and retrieved."""
        from services.property_registry.data_packets.ship_properties import register_ship_properties
        
        register_ship_properties()
        
        assert get_property("rho_sea_water_kg_m3") == 1025.0
        assert get_property("g_m_s2") == 9.80665
        assert get_property("nu_sea_water_15c_m2_s") == 1.1892e-6
    
    def test_ship_properties_with_state(self):
        """Ship properties respect state constraints."""
        from services.property_registry.data_packets.ship_properties import register_ship_properties
        
        register_ship_properties()
        
        # In-domain
        state = PropertyState(temperature_K=288.15, salinity_ppt=35.0)
        value = get_property("rho_sea_water_kg_m3", state)
        assert value == 1025.0
        
        # Out-of-domain (too hot)
        state_hot = PropertyState(temperature_K=350.0)
        with pytest.raises(PropertyOutOfDomain):
            get_property("rho_sea_water_kg_m3", state_hot)
    
    def test_sweep_with_ship_properties(self):
        """Sweep works with ship properties."""
        from services.property_registry.data_packets.ship_properties import register_ship_properties
        
        register_ship_properties()
        
        def resistance_calc(rho):
            # Simplified resistance calculation
            return 0.5 * rho * 100.0 * 5.0**2  # 0.5 * rho * S * V^2
        
        results = sweep_property(
            "rho_sea_water_kg_m3",
            [1000.0, 1025.0, 1050.0],
            resistance_calc
        )
        
        assert len(results) == 3
        assert results[0]["result"] == 0.5 * 1000.0 * 100.0 * 25.0
        assert results[1]["result"] == 0.5 * 1025.0 * 100.0 * 25.0
        assert results[2]["result"] == 0.5 * 1050.0 * 100.0 * 25.0


# ─────────────────────────────────────────────────────────────────────────────
# Domain leak scan
# ─────────────────────────────────────────────────────────────────────────────

class TestDomainLeakScan:
    """C6: domain_leak_scan - 0 hits in mechanism code."""
    
    def test_domain_leak_scan_zero_hits(self):
        """Scan mechanism code for domain terms - should be 0 hits."""
        import services.property_registry.registry as reg
        import services.property_registry.schema as sch
        import services.property_registry.errors as err
        import inspect
        
        modules = [reg, sch, err]
        domain_terms = ["ship", "water", "sea", "ice", "steel", "hull", "resistance", "propulsion"]
        
        total_hits = 0
        for mod in modules:
            source = inspect.getsource(mod)
            for term in domain_terms:
                if _has_domain_word(source, term):
                    total_hits += 1
        
        assert total_hits == 0, f"Domain leak scan found {total_hits} hits"
