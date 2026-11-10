"""Tests for the physical constants SSOT registry."""
import json
import tempfile
from pathlib import Path

import pytest

from backend.services.physical_constants import (
    PhysicalConstant,
    PhysicalConstantsRegistry,
    get_registry,
)


@pytest.fixture
def sample_registry_dict():
    """Minimal valid registry for testing."""
    return {
        "version": "1.0.0-test",
        "constants": {
            "gravity": {
                "symbol": "g",
                "value": 9.80665,
                "unit": "m/s²",
                "uncertainty": 0.0,
                "uncertainty_type": "exact",
                "source": "CGPM 1901",
                "scope": "global",
                "aliases": ["G", "g", "gravity"],
                "notes": "Standard gravity.",
            },
            "ice_density": {
                "symbol": "ρ_ice",
                "value": 917.0,
                "unit": "kg/m³",
                "uncertainty": 5.0,
                "uncertainty_type": "typical_range",
                "source": "ITTC",
                "scope": "ice_physics",
                "aliases": ["RHO_ICE", "rho_ice"],
                "notes": "Pure ice density.",
            },
        },
    }


@pytest.fixture
def sample_registry_file(sample_registry_dict, tmp_path):
    """Write sample registry to a temp file and return the path."""
    p = tmp_path / "test_registry.json"
    with open(p, "w") as f:
        json.dump(sample_registry_dict, f)
    return p


class TestPhysicalConstantsRegistry:
    def test_load_and_query_by_key(self, sample_registry_file):
        reg = PhysicalConstantsRegistry(path=sample_registry_file)
        assert reg.version == "1.0.0-test"
        g = reg.get("gravity")
        assert g.value == 9.80665
        assert g.unit == "m/s²"
        assert g.uncertainty_type == "exact"

    def test_query_by_alias(self, sample_registry_file):
        reg = PhysicalConstantsRegistry(path=sample_registry_file)
        g = reg.get("G")
        assert g.key == "gravity"
        assert g.value == 9.80665

    def test_value_shortcut(self, sample_registry_file):
        reg = PhysicalConstantsRegistry(path=sample_registry_file)
        assert reg.value("rho_ice") == 917.0
        assert reg.value("RHO_ICE") == 917.0

    def test_unknown_key_raises(self, sample_registry_file):
        reg = PhysicalConstantsRegistry(path=sample_registry_file)
        with pytest.raises(KeyError, match="unknown constant"):
            reg.get("nonexistent")

    def test_keys_list(self, sample_registry_file):
        reg = PhysicalConstantsRegistry(path=sample_registry_file)
        assert sorted(reg.keys) == ["gravity", "ice_density"]

    def test_all_constants(self, sample_registry_file):
        reg = PhysicalConstantsRegistry(path=sample_registry_file)
        consts = reg.all_constants()
        assert len(consts) == 2
        assert all(isinstance(c, PhysicalConstant) for c in consts)

    def test_as_dict(self, sample_registry_file):
        reg = PhysicalConstantsRegistry(path=sample_registry_file)
        d = reg.as_dict()
        assert "version" in d
        assert "constants" in d
        assert "gravity" in d["constants"]

    def test_tolerance_band_exact(self, sample_registry_file):
        reg = PhysicalConstantsRegistry(path=sample_registry_file)
        lo, hi = reg.tolerance_band("gravity")
        assert lo == 9.80665
        assert hi == 9.80665

    def test_tolerance_band_with_uncertainty(self, sample_registry_file):
        reg = PhysicalConstantsRegistry(path=sample_registry_file)
        lo, hi = reg.tolerance_band("ice_density", relative=0.01)
        # max(917*0.01, 5.0) = 9.17
        assert abs(lo - (917.0 - 9.17)) < 1e-10
        assert abs(hi - (917.0 + 9.17)) < 1e-10

    def test_sha256_is_deterministic(self, sample_registry_file):
        reg = PhysicalConstantsRegistry(path=sample_registry_file)
        g1 = reg.get("gravity")
        g2 = reg.get("gravity")
        assert g1.sha256 == g2.sha256
        assert len(g1.sha256) == 64  # sha256 hex digest length


class TestDefaultRegistry:
    """Test the real registry file in config/."""

    def test_default_registry_loads(self):
        reg = get_registry()
        assert reg.version
        assert len(reg.keys) > 0

    def test_gravity_in_default(self):
        reg = get_registry()
        g = reg.get("gravity_standard")
        assert g.value == 9.80665

    def test_ice_density_in_default(self):
        reg = get_registry()
        rho = reg.get("ice_density_fresh")
        assert rho.value == 917.0

    def test_alias_lookup_in_default(self):
        reg = get_registry()
        # RHO_ICE is an alias for ice_density_fresh
        rho = reg.get("RHO_ICE")
        assert rho.key == "ice_density_fresh"
