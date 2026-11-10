"""Physical / material property registry with fail-closed semantics.

Zero domain semantics: the registry knows nothing about ships, water, or steel.
Properties are defined purely by schema (attr_id, dimensions, units, domain of
applicability, provenance, confidence).  Domain data is injected as *data
packets* (JSON / Python dicts) that conform to the schema.

Core contract
-------------
* Modules obtain property values ONLY via ``get_property(attr_id, state)``.
* If the property has no provenance (``source_sha256`` missing) → raise
  ``PropertyNoProvenance`` (error code ``PROP_NO_PROVENANCE``).
* If the requested state falls outside the registered domain of applicability
  → raise ``PropertyOutOfDomain`` (error code ``PROP_OUT_OF_DOMAIN``).
* **No silent fallback.**  Ever.  Missing or out-of-range is a hard error.

Sweep / sensitivity
-------------------
``sweep_property(attr_id, values, endpoint_fn)`` replaces the registered value
with each candidate and re-runs the endpoint, collecting results.  Used for
impact-band regression: does the conclusion cross a decision threshold when
the property varies within its uncertainty band?

Coverage audit
--------------
``audit_module_constants(module_path)`` scans a Python module for numeric
constants that look like physical properties but are not registered.  The
debt ledger only shrinks (monotone decreasing gate).
"""
from services.property_registry.schema import (
    ConfidenceLevel,
    PropertyDefinition,
    PropertyState,
    DimensionVector,
    ApplicabilityDomain,
    Provenance,
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
    get_default_registry,
    get_property,
    register_property,
    sweep_property,
    reset_registry,
)
from services.property_registry.audit import (
    audit_module_constants,
    DebtLedger,
)

__all__ = [
    # Schema
    "ConfidenceLevel",
    "PropertyDefinition",
    "PropertyState",
    "DimensionVector",
    "ApplicabilityDomain",
    "Provenance",
    "ReplacementChain",
    # Errors
    "PropertyNoProvenance",
    "PropertyOutOfDomain",
    "PropertyNotRegistered",
    "PROP_NO_PROVENANCE",
    "PROP_OUT_OF_DOMAIN",
    "PROP_NOT_REGISTERED",
    # Registry
    "PropertyRegistry",
    "get_default_registry",
    "get_property",
    "register_property",
    "sweep_property",
    "reset_registry",
    # Audit
    "audit_module_constants",
    "DebtLedger",
]
