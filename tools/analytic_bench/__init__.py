"""Analytic benchmark harness for the ship overall-performance platform.

Independent, closed-form / first-principles reference values used to audit
capabilities that the platform grades as REAL (see ``audit_capabilities``).

Modules
-------
reference
    Analytic and independent-numerical references (no platform code imported).
stability_audit
    Runner that probes the live platform API, compares against the references
    and writes machine-readable + human-readable audit artifacts.
"""

from . import reference  # noqa: F401

__all__ = ["reference"]