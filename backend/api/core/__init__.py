"""Core layer of the platform API: mechanisms every domain depends on.

Zero domain semantics by construction: modules here may not import
``api.domain.*`` (structural gate: ``tools/audit/domain_surface_manifest.py``).
Domain capabilities reach the core through :mod:`api.core.domain_provider`.
"""