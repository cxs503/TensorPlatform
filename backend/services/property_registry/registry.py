"""Core property registry with fail-closed semantics.

Modules obtain property values ONLY via get_property(attr_id, state).
Missing provenance or out-of-domain state → hard error. No silent fallback.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.property_registry.schema import (
    PropertyDefinition,
    PropertyState,
    ConfidenceLevel,
)
from services.property_registry.errors import (
    PropertyNoProvenance,
    PropertyOutOfDomain,
    PropertyNotRegistered,
)


class PropertyRegistry:
    """Thread-safe registry for physical/material properties.
    
    All properties must be registered with full provenance before use.
    get_property() enforces fail-closed: missing provenance or out-of-domain
    state raises hard errors.
    """
    
    def __init__(self):
        self._lock = threading.RLock()
        self._properties: Dict[str, PropertyDefinition] = {}
    
    def register(self, prop: PropertyDefinition) -> None:
        """Register a property definition.
        
        Validates that the property has provenance (source_sha256 and confidence).
        Re-registering the same attr_id with the same value is a no-op (idempotent).
        Re-registering with a different value raises an error (wiring bug).
        """
        # Validate provenance
        if not prop.provenance.source_sha256:
            raise PropertyNoProvenance(
                attr_id=prop.attr_id,
                details={"reason": "source_sha256 is empty"}
            )
        
        if prop.confidence not in (ConfidenceLevel.MEASURED, ConfidenceLevel.DERIVED, ConfidenceLevel.ASSUMED):
            raise PropertyNoProvenance(
                attr_id=prop.attr_id,
                details={"reason": f"invalid confidence level: {prop.confidence}"}
            )
        
        with self._lock:
            existing = self._properties.get(prop.attr_id)
            if existing is not None:
                # Idempotent: same value is OK
                if existing.value == prop.value and existing.provenance.source_sha256 == prop.provenance.source_sha256:
                    return
                # Different value = wiring bug
                raise ValueError(
                    f"Property '{prop.attr_id}' already registered with different value "
                    f"(existing={existing.value}, new={prop.value}). "
                    f"Refusing to silently change property value."
                )
            self._properties[prop.attr_id] = prop
    
    def get(self, attr_id: str, state: Optional[PropertyState] = None) -> float:
        """Get property value at the given state.
        
        Fail-closed:
        - Property not registered → PropertyNotRegistered
        - Property has no provenance → PropertyNoProvenance
        - State outside applicability domain → PropertyOutOfDomain
        
        Returns the property value (float) in SI units.
        """
        with self._lock:
            prop = self._properties.get(attr_id)
        
        if prop is None:
            raise PropertyNotRegistered(
                attr_id=attr_id,
                registered=list(self._properties.keys())
            )
        
        # Check provenance (should already be validated at registration, but double-check)
        if not prop.provenance.source_sha256:
            raise PropertyNoProvenance(attr_id=attr_id)
        
        # Evaluate at state
        if state is None:
            state = PropertyState()
        
        return prop.evaluate_at_state(state)
    
    def get_definition(self, attr_id: str) -> PropertyDefinition:
        """Get the full property definition (not just the value)."""
        with self._lock:
            prop = self._properties.get(attr_id)
        
        if prop is None:
            raise PropertyNotRegistered(
                attr_id=attr_id,
                registered=list(self._properties.keys())
            )
        
        return prop
    
    def list_properties(self) -> List[str]:
        """List all registered property attr_ids."""
        with self._lock:
            return sorted(self._properties.keys())
    
    def clear(self) -> None:
        """Clear all registered properties (test-only)."""
        with self._lock:
            self._properties.clear()
    
    def sweep(
        self,
        attr_id: str,
        values: List[float],
        endpoint_fn: Callable[[float], Any],
        state: Optional[PropertyState] = None
    ) -> List[Dict[str, Any]]:
        """Sweep property values and evaluate endpoint for each.
        
        For each value in `values`, temporarily replaces the registered value
        and calls `endpoint_fn(value)`. Collects results.
        
        Used for sensitivity analysis: does the conclusion cross a decision
        threshold when the property varies within its uncertainty band?
        
        Args:
            attr_id: Property to sweep
            values: List of candidate values to test
            endpoint_fn: Function that takes the property value and returns a result
            state: Optional state for property evaluation (not used in sweep,
                   but kept for API consistency)
        
        Returns:
            List of dicts with keys: 'value', 'result', 'crossed_threshold' (if applicable)
        """
        with self._lock:
            prop = self._properties.get(attr_id)
        
        if prop is None:
            raise PropertyNotRegistered(
                attr_id=attr_id,
                registered=list(self._properties.keys())
            )
        
        results = []
        original_value = prop.value
        
        try:
            for val in values:
                # Temporarily replace value
                with self._lock:
                    self._properties[attr_id].value = val
                
                try:
                    result = endpoint_fn(val)
                    results.append({
                        "value": val,
                        "result": result,
                        "error": None,
                    })
                except Exception as e:
                    results.append({
                        "value": val,
                        "result": None,
                        "error": str(e),
                    })
        finally:
            # Restore original value
            with self._lock:
                self._properties[attr_id].value = original_value
        
        return results


# Global default registry
_DEFAULT_REGISTRY = PropertyRegistry()


def get_default_registry() -> PropertyRegistry:
    """Get the global default property registry."""
    return _DEFAULT_REGISTRY


def register_property(prop: PropertyDefinition) -> None:
    """Register a property in the default registry."""
    _DEFAULT_REGISTRY.register(prop)


def get_property(attr_id: str, state: Optional[PropertyState] = None) -> float:
    """Get a property value from the default registry.
    
    Fail-closed: raises on missing provenance or out-of-domain state.
    """
    return _DEFAULT_REGISTRY.get(attr_id, state)


def sweep_property(
    attr_id: str,
    values: List[float],
    endpoint_fn: Callable[[float], Any],
    state: Optional[PropertyState] = None
) -> List[Dict[str, Any]]:
    """Sweep property values in the default registry."""
    return _DEFAULT_REGISTRY.sweep(attr_id, values, endpoint_fn, state)


def reset_registry() -> None:
    """Clear the default registry (test-only)."""
    _DEFAULT_REGISTRY.clear()
