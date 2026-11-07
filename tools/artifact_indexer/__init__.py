"""Artifact sha256 indexer (IN15-O5, m4963).

Cross-repo artifact directory integrity index:
    path -> sha256 -> size -> mtime

Additive-only tool: no existing API surface, schema or table is touched.
"""

__all__ = ["indexer"]
__version__ = "1.0.0"
