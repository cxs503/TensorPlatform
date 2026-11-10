"""Error codes and exceptions for the property registry.

Fail-closed semantics: missing provenance or out-of-domain state are hard errors.
No silent fallback. Ever.
"""
from __future__ import annotations

from typing import Any, Dict


# Error codes
PROP_NO_PROVENANCE = "PROP_NO_PROVENANCE"
PROP_OUT_OF_DOMAIN = "PROP_OUT_OF_DOMAIN"
PROP_NOT_REGISTERED = "PROP_NOT_REGISTERED"


class PropertyRegistryError(Exception):
    """Base exception for property registry errors."""
    
    def __init__(self, message: str, error_code: str, details: Dict[str, Any] = None):
        super().__init__(message)
        self.error_code = error_code
        self.details = details or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for API responses."""
        return {
            "error_code": self.error_code,
            "message": str(self),
            "details": self.details,
        }


class PropertyNoProvenance(PropertyRegistryError):
    """Property has no provenance (source_sha256 missing).
    
    This is a hard error - the property cannot be used without provenance.
    Error code: PROP_NO_PROVENANCE
    """
    
    def __init__(self, attr_id: str, details: Dict[str, Any] = None):
        message = (
            f"Property '{attr_id}' has no provenance. "
            f"All properties must have a source_sha256 and confidence level. "
            f"Cannot use property without provenance (fail-closed)."
        )
        super().__init__(
            message=message,
            error_code=PROP_NO_PROVENANCE,
            details={"attr_id": attr_id, **(details or {})}
        )
        self.attr_id = attr_id


class PropertyOutOfDomain(PropertyRegistryError):
    """Property requested at a state outside its applicability domain.
    
    This is a hard error - the property cannot be used outside its valid range.
    Error code: PROP_OUT_OF_DOMAIN
    """
    
    def __init__(
        self,
        attr_id: str,
        state: Dict[str, float],
        applicability: Dict[str, Any],
        details: Dict[str, Any] = None
    ):
        message = (
            f"Property '{attr_id}' requested at state {state} is outside "
            f"its applicability domain {applicability}. "
            f"Cannot use property outside its valid range (fail-closed)."
        )
        super().__init__(
            message=message,
            error_code=PROP_OUT_OF_DOMAIN,
            details={
                "attr_id": attr_id,
                "state": state,
                "applicability": applicability,
                **(details or {})
            }
        )
        self.attr_id = attr_id
        self.state = state
        self.applicability = applicability


class PropertyNotRegistered(PropertyRegistryError):
    """Property attr_id is not registered in the registry.
    
    This is a hard error - the property must be registered before use.
    Error code: PROP_NOT_REGISTERED
    """
    
    def __init__(self, attr_id: str, registered: list = None, details: Dict[str, Any] = None):
        registered_str = ", ".join(sorted(registered or [])) or "(none)"
        message = (
            f"Property '{attr_id}' is not registered. "
            f"Registered properties: {registered_str}. "
            f"Register the property before use."
        )
        super().__init__(
            message=message,
            error_code=PROP_NOT_REGISTERED,
            details={
                "attr_id": attr_id,
                "registered": registered or [],
                **(details or {})
            }
        )
        self.attr_id = attr_id
