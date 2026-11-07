"""Verification & Validation toolkit (W10-N07 numerical-uncertainty certification).

Modules
-------
``gci``         grid-convergence-index / Richardson / U_SN / U_V / adjudicator
``cases``       case library (ice-channel representation cases, structural panel, grids)
``fvm``         finite-volume (artificial-compressibility) incompressible channel solver
``lbm``         D2Q9 lattice-Boltzmann channel solver
``structure``   Hermite beam-column buckling FEM (stiffened-panel interframe mode)
``benchmarks``  analytic references + independent (50-digit) recomputation of the GCI algebra
``run_certification``  driver: one command reproduces every number in the report
"""

__all__ = ["gci", "cases", "fvm", "lbm", "structure", "benchmarks"]