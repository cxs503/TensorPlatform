"""Fail-closed error vocabulary for the solver adapter protocol (v1).

Every failure mode of the protocol has a *typed* error so that a caller (or a
contract test) can assert on the class instead of on a message string.  All of
them derive from :class:`AdapterError`, which is itself a ``ValueError``: a
protocol violation is a *programming* error at the call site, never a silent
degradation of the numerics.

The classes are deliberately narrow; there is no generic "something went wrong"
member, because a protocol whose failures cannot be told apart cannot be tested.
"""

from __future__ import annotations

__all__ = [
    "AdapterError",
    "ProtocolViolation",
    "LifecycleError",
    "FieldLookupError",
    "DimensionalError",
    "CompatibilityError",
    "MappingError",
    "ConservationError",
    "StateMismatchError",
    "CoverageError",
]


class AdapterError(ValueError):
    """Base class of every protocol / adapter failure."""


class ProtocolViolation(AdapterError):
    """A manifest, exchange request or schema object violates protocol v1."""


class LifecycleError(AdapterError):
    """An adapter method was called in a lifecycle state that forbids it."""


class FieldLookupError(AdapterError):
    """A field name is not declared by the adapter (or not yet produced)."""


class DimensionalError(AdapterError):
    """Two fields disagree on their dimensional exponents or quantity law."""


class CompatibilityError(AdapterError):
    """A source adapter cannot legally exchange the requested field with a target."""


class MappingError(AdapterError):
    """The requested mapping operator / mode combination is not admissible."""


class ConservationError(AdapterError):
    """A mapping violated its declared conservation tolerance."""


class StateMismatchError(AdapterError):
    """A restored checkpoint does not describe this adapter's state."""


class CoverageError(AdapterError):
    """A native input/output record has no protocol mapping (coverage < 100%)."""