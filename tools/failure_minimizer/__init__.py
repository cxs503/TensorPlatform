"""
Failure Minimizer - Delta debugging for mission failure reproduction.

Given frozen snapshots of failed missions, automatically reduces them to
1-minimal executable reproduction scripts with signatures for auditability.

Zero domain semantics: all domain data injected via API/config/artifacts.
"""

__version__ = "1.0.0"
__all__ = ["corpus", "signature", "ddmin", "report", "cli"]
