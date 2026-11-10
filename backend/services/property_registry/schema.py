"""Schema definitions for physical/material properties.

All properties are defined by this schema - no domain-specific knowledge.
Domain data is injected as packets that conform to this schema.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field, field_validator


class ConfidenceLevel(str, Enum):
    """Confidence level of the property value.
    
    MEASURED: Directly measured in laboratory/field with documented uncertainty.
    DERIVED: Computed from other measured properties via known relationships.
    ASSUMED: Assumed/nominal value without direct measurement backing.
    """
    MEASURED = "MEASURED"
    DERIVED = "DERIVED"
    ASSUMED = "ASSUMED"


class DimensionVector(BaseModel):
    """SI dimension vector [M, L, T, Θ, N, J, I].
    
    Exponents for: mass, length, time, temperature, amount of substance,
    luminous intensity, electric current.
    
    Example: density [kg/m³] = [1, -3, 0, 0, 0, 0, 0]
    """
    mass: int = Field(default=0, description="Mass exponent")
    length: int = Field(default=0, description="Length exponent")
    time: int = Field(default=0, description="Time exponent")
    temperature: int = Field(default=0, description="Temperature exponent")
    amount: int = Field(default=0, description="Amount of substance exponent")
    luminous: int = Field(default=0, description="Luminous intensity exponent")
    current: int = Field(default=0, description="Electric current exponent")
    
    def to_tuple(self) -> Tuple[int, int, int, int, int, int, int]:
        return (self.mass, self.length, self.time, self.temperature,
                self.amount, self.luminous, self.current)


class ApplicabilityDomain(BaseModel):
    """Domain of applicability for the property.
    
    Specifies the range of state parameters (temperature, pressure, etc.)
    where the property value is valid. Outside this domain, the property
    must not be used (fail-closed).
    """
    temperature_range: Optional[Tuple[float, float]] = Field(
        default=None,
        description="Valid temperature range in Kelvin (min, max)"
    )
    pressure_range: Optional[Tuple[float, float]] = Field(
        default=None,
        description="Valid pressure range in Pascal (min, max)"
    )
    salinity_range: Optional[Tuple[float, float]] = Field(
        default=None,
        description="Valid salinity range in ppt (min, max)"
    )
    custom_ranges: Dict[str, Tuple[float, float]] = Field(
        default_factory=dict,
        description="Custom parameter ranges (param_name -> (min, max))"
    )
    failure_mode: str = Field(
        default="raise",
        description="How to handle out-of-domain: 'raise' (default) or 'warn'"
    )
    
    def check_state(self, state: Dict[str, float]) -> bool:
        """Check if the given state is within the applicability domain.
        
        Returns True if in-domain, False if out-of-domain.
        """
        if self.temperature_range is not None:
            temp_k = state.get("temperature_K")
            if temp_k is not None:
                t_min, t_max = self.temperature_range
                if not (t_min <= temp_k <= t_max):
                    return False
        
        if self.pressure_range is not None:
            pres_pa = state.get("pressure_Pa")
            if pres_pa is not None:
                p_min, p_max = self.pressure_range
                if not (p_min <= pres_pa <= p_max):
                    return False
        
        if self.salinity_range is not None:
            sal_ppt = state.get("salinity_ppt")
            if sal_ppt is not None:
                s_min, s_max = self.salinity_range
                if not (s_min <= sal_ppt <= s_max):
                    return False
        
        for param, (p_min, p_max) in self.custom_ranges.items():
            val = state.get(param)
            if val is not None:
                if not (p_min <= val <= p_max):
                    return False
        
        return True


class Provenance(BaseModel):
    """Source provenance for the property value.
    
    Tracks where the value came from: literature, specification, experiment.
    Required for all properties (fail-closed: no provenance = no use).
    """
    source_type: str = Field(
        description="Type of source: 'literature', 'specification', 'experiment', 'database'"
    )
    reference: str = Field(
        description="Full citation or specification identifier"
    )
    source_sha256: str = Field(
        description="SHA256 hash of the source document/content for integrity"
    )
    table_or_figure: Optional[str] = Field(
        default=None,
        description="Table/figure number in the source (if applicable)"
    )
    page_or_section: Optional[str] = Field(
        default=None,
        description="Page/section in the source (if applicable)"
    )
    url: Optional[str] = Field(
        default=None,
        description="URL to the source (if available online)"
    )


class TemperatureDependence(BaseModel):
    """Temperature dependence model for the property.
    
    Specifies how the property varies with temperature.
    """
    form: str = Field(
        description="Form of temperature dependence: 'constant', 'linear', 'polynomial', 'vogel', 'arrhenius', 'tabulated'"
    )
    coefficients: Optional[List[float]] = Field(
        default=None,
        description="Coefficients for the dependence model"
    )
    reference_temperature_K: Optional[float] = Field(
        default=None,
        description="Reference temperature for the value (Kelvin)"
    )
    tabulated_values: Optional[Dict[float, float]] = Field(
        default=None,
        description="Tabulated values (temperature_K -> property_value)"
    )


class ReplacementChain(BaseModel):
    """Version and replacement chain for the property.
    
    Tracks property evolution: which version superseded which, and why.
    """
    version: str = Field(
        description="Version identifier for this property definition"
    )
    superseded_by: Optional[str] = Field(
        default=None,
        description="attr_id of the property that superseded this one"
    )
    supersedes: Optional[str] = Field(
        default=None,
        description="attr_id of the property this one supersedes"
    )
    change_reason: Optional[str] = Field(
        default=None,
        description="Reason for the change (if superseding another)"
    )
    effective_date: Optional[str] = Field(
        default=None,
        description="Date this version became effective (ISO format)"
    )


class PropertyState(BaseModel):
    """State parameters for property evaluation.
    
    Represents the thermodynamic/mechanical state at which the property
    is to be evaluated.
    """
    temperature_K: Optional[float] = Field(
        default=None,
        description="Temperature in Kelvin"
    )
    pressure_Pa: Optional[float] = Field(
        default=None,
        description="Pressure in Pascal"
    )
    salinity_ppt: Optional[float] = Field(
        default=None,
        description="Salinity in parts per thousand"
    )
    custom: Dict[str, float] = Field(
        default_factory=dict,
        description="Custom state parameters"
    )
    
    def to_dict(self) -> Dict[str, float]:
        """Convert to dict for domain checking."""
        d = {}
        if self.temperature_K is not None:
            d["temperature_K"] = self.temperature_K
        if self.pressure_Pa is not None:
            d["pressure_Pa"] = self.pressure_Pa
        if self.salinity_ppt is not None:
            d["salinity_ppt"] = self.salinity_ppt
        d.update(self.custom)
        return d


class PropertyDefinition(BaseModel):
    """Complete definition of a physical/material property.
    
    This is the schema that all properties must conform to.
    Domain data is injected as instances of this schema.
    """
    attr_id: str = Field(
        description="Unique identifier for the property (e.g., 'rho_sea_water_kg_m3')"
    )
    display_name: str = Field(
        description="Human-readable name"
    )
    description: str = Field(
        description="Detailed description of the property"
    )
    dimensions: DimensionVector = Field(
        description="SI dimension vector"
    )
    si_unit: str = Field(
        description="SI unit string (e.g., 'kg/m^3', 'm^2/s')"
    )
    value: float = Field(
        description="Nominal value in SI units"
    )
    uncertainty: Optional[float] = Field(
        default=None,
        description="Uncertainty in the value (same units as value)"
    )
    uncertainty_fraction: Optional[float] = Field(
        default=None,
        description="Relative uncertainty (uncertainty/value)"
    )
    confidence: ConfidenceLevel = Field(
        description="Confidence level: MEASURED, DERIVED, or ASSUMED"
    )
    provenance: Provenance = Field(
        description="Source provenance (required - no provenance = no use)"
    )
    applicability: ApplicabilityDomain = Field(
        default_factory=ApplicabilityDomain,
        description="Domain of applicability"
    )
    temperature_dependence: Optional[TemperatureDependence] = Field(
        default=None,
        description="Temperature dependence model (if any)"
    )
    replacement_chain: Optional[ReplacementChain] = Field(
        default=None,
        description="Version/replacement chain"
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Tags for categorization and search"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata"
    )
    
    @field_validator("attr_id")
    @classmethod
    def validate_attr_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("attr_id cannot be empty")
        if not all(c.isalnum() or c in "_-." for c in v):
            raise ValueError(
                f"attr_id must contain only alphanumeric, underscore, hyphen, dot: {v!r}"
            )
        return v
    
    def evaluate_at_state(self, state: PropertyState) -> float:
        """Evaluate the property at the given state.
        
        For constant properties, returns the nominal value.
        For temperature-dependent properties, evaluates the dependence model.
        
        Raises PropertyOutOfDomain if state is outside applicability domain.
        """
        from services.property_registry.errors import PropertyOutOfDomain
        
        # Check applicability domain
        state_dict = state.to_dict()
        if not self.applicability.check_state(state_dict):
            raise PropertyOutOfDomain(
                attr_id=self.attr_id,
                state=state_dict,
                applicability=self.applicability.model_dump()
            )
        
        # If no temperature dependence or constant, return nominal value
        if self.temperature_dependence is None:
            return self.value
        
        if self.temperature_dependence.form == "constant":
            return self.value
        
        # For other forms, would need to evaluate the model
        # For now, return nominal value (TODO: implement full evaluation)
        return self.value
