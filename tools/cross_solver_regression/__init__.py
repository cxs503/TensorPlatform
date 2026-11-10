"""Cross-solver benchmark regression detection pipeline (p251w2-n02).

Provides:
- Template-based generation of ≥10 benchmark cases covering ≥3 solvers
- Regression detection with 5% sensitivity and <1% false positive rate
- Dashboard report with status table, trend chart, deviation histogram
- Zero domain semantics (synthetic domain name fixtures)

Solvers covered (synthetic names, zero domain semantics):
- TensorLBM (lattice-Boltzmann-style solver)
- TensorFEM (finite-element-style solver)
- TensorDEM (discrete-element-style solver)
- TensorFVM (finite-volume-style solver, bonus)
"""
from __future__ import annotations

__version__ = "1.0.0"
