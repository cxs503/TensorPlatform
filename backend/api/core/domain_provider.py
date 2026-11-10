"""Domain capability provider seam — the only allowed bridge core → domain.

Why
---
The platform core (``api/core``, ``services``, ``models``, ``utils``, ``physics``,
``tasks``, ``tools``) must stay free of concrete domain modules: it is shared
machinery, and a direct import of a domain module welds the core to one subject
area (see ``tools/audit/domain_surface_manifest.py`` for the enforcement gate).

So domain capabilities are *declared* (name → dotted target) and *resolved* at the
routing layer through this seam. Declarations are data (registry / configuration),
never literals typed into core code, and each carries an owner repo plus an
optional expiry so a temporary wiring cannot silently become permanent.

Contract
--------
* ``register_domain`` / ``load_registry`` — declare a capability.
* ``provide_domain`` — resolve (import lazily, cache) and call a capability.
* ``describe`` / ``provided_names`` — introspection for health/debug endpoints.
* Resolution failures raise :class:`DomainNotProvided` (fail closed, never a
  silent ``None``).

Nothing here imports a domain package: the dotted target is imported on first
use, which keeps the static dependency direction one-way (domain → core).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

__all__ = [
    "DomainNotProvided",
    "DomainProvider",
    "register_domain",
    "load_registry",
    "provide_domain",
    "provided_names",
    "describe",
    "reset_registry",
]

TARGET_SEPARATOR = ":"


class DomainNotProvided(LookupError):
    """Raised when a capability is unknown, malformed or unresolvable."""


@dataclass(frozen=True)
class DomainProvider:
    """One declared capability: a name plus how to obtain it."""

    name: str
    target: str
    owner_repo: str
    expires: str | None = None
    source: str = "declared"
    meta: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "target": self.target,
            "owner_repo": self.owner_repo,
            "expires": self.expires,
            "source": self.source,
            "meta": dict(self.meta),
        }


_REGISTRY: dict[str, DomainProvider] = {}
_CACHE: dict[str, Any] = {}


def reset_registry() -> None:
    """Drop declarations and the resolution cache (tests / reconfiguration)."""
    _REGISTRY.clear()
    _CACHE.clear()


def register_domain(
    name: str,
    target: str | Callable[..., Any],
    *,
    owner_repo: str,
    expires: str | None = None,
    source: str = "declared",
    meta: Mapping[str, Any] | None = None,
) -> DomainProvider:
    """Declare capability ``name``.

    ``target`` is either a callable or a ``"dotted.module:attribute"`` string.
    ``owner_repo`` is mandatory: an unowned capability is an untraceable one.
    """
    if not name or not isinstance(name, str):
        raise DomainNotProvided("capability name must be a non-empty string")
    if not owner_repo:
        raise DomainNotProvided(f"capability {name!r} needs an owner_repo")
    if isinstance(target, str):
        if TARGET_SEPARATOR not in target:
            raise DomainNotProvided(
                f"capability {name!r} target must be 'dotted.module:attribute', got {target!r}"
            )
    elif not callable(target):
        raise DomainNotProvided(f"capability {name!r} target must be callable or a dotted string")
    provider = DomainProvider(
        name=name,
        target=target if isinstance(target, str) else f"{target.__module__}:{target.__qualname__}",
        owner_repo=owner_repo,
        expires=expires,
        source=source,
        meta=dict(meta or {}),
    )
    _REGISTRY[name] = provider
    _CACHE.pop(name, None)
    return provider


def load_registry(mapping: Mapping[str, Mapping[str, Any]]) -> list[DomainProvider]:
    """Declare many capabilities from data (configuration / artifact injection)."""
    out = []
    for name, spec in mapping.items():
        out.append(
            register_domain(
                name,
                spec["target"],
                owner_repo=spec.get("owner_repo", ""),
                expires=spec.get("expires"),
                source=spec.get("source", "config"),
                meta=spec.get("meta"),
            )
        )
    return out


def _resolve(provider: DomainProvider) -> Any:
    cached = _CACHE.get(provider.name)
    if cached is not None:
        return cached
    target = provider.target
    module_name, _, attr = target.partition(TARGET_SEPARATOR)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise DomainNotProvided(f"capability {provider.name!r}: cannot import {module_name!r}: {exc}") from exc
    try:
        resolved = getattr(module, attr)
    except AttributeError as exc:
        raise DomainNotProvided(f"capability {provider.name!r}: {module_name!r} has no {attr!r}") from exc
    _CACHE[provider.name] = resolved
    return resolved


def provide_domain(name: str, /, *args: Any, **kwargs: Any) -> Any:
    """Resolve and call capability ``name``; fail closed when undeclared."""
    provider = _REGISTRY.get(name)
    if provider is None:
        raise DomainNotProvided(f"no capability declared under {name!r}")
    resolved = _resolve(provider)
    if callable(resolved):
        return resolved(*args, **kwargs)
    if args or kwargs:
        raise DomainNotProvided(f"capability {name!r} is not callable; drop the arguments")
    return resolved


def provided_names() -> list[str]:
    return sorted(_REGISTRY)


def describe(name: str) -> dict:
    provider = _REGISTRY.get(name)
    if provider is None:
        raise DomainNotProvided(f"no capability declared under {name!r}")
    return provider.as_dict()