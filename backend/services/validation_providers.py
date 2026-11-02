"""Domain-neutral validation provider seam (boundary ruling 2026-11-02).

TensorPlatform core audit layers must not import domain physics modules
directly.  Domains register providers (constants, friction lines, model
callables) here at startup; core code resolves them through this seam and
fails loudly -- never silently -- when a domain is not wired.

Rule: no silent fallback.  A missing provider is a wiring bug, and the error
must say exactly what is missing and what is registered.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Tuple

_Key = Tuple[str, str]

_LOCK = threading.Lock()
_REGISTRY: Dict[_Key, Any] = {}


class ProviderNotRegistered(RuntimeError):
    """A domain capability was required but never registered."""


def register_provider(domain: str, capability: str, provider: Any) -> None:
    """Register ``provider`` under ``(domain, capability)``.

    Re-registering the identical object is a no-op (safe double import).
    Registering a *different* object for an existing key is a wiring bug and
    raises -- it would silently change physics for every consumer.
    """
    key = (domain, capability)
    with _LOCK:
        existing = _REGISTRY.get(key)
        if existing is not None and existing is not provider:
            raise ProviderNotRegistered(
                f"provider ({domain!r}, {capability!r}) already registered to a "
                "different object -- refusing to silently swap physics"
            )
        _REGISTRY[key] = provider


def require_provider(domain: str, capability: str) -> Any:
    try:
        with _LOCK:
            return _REGISTRY[(domain, capability)]
    except KeyError:
        registered = sorted(f"{d}/{c}" for d, c in _REGISTRY)
        raise ProviderNotRegistered(
            f"no provider registered for ({domain!r}, {capability!r}); "
            f"registered: {registered or 'nothing'}. Wire the domain package "
            "(e.g. api.domain.physics_providers.register_ship_resistance_"
            "providers) before core audit code runs."
        ) from None


def physical_constant(domain: str, name: str) -> float:
    return require_provider(domain, f"constant:{name}")


def friction_line(domain: str, method: str, reynolds: float) -> float:
    line: Callable[[float], float] = require_provider(domain, f"friction:{method}")
    return line(reynolds)


def reset_providers() -> None:
    """Test-only: drop all registrations."""
    with _LOCK:
        _REGISTRY.clear()
