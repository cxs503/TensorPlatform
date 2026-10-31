"""
Ice-Class Hull Form Multi-Objective Optimization Framework.

NSGA-II driven Pareto front search balancing:
  - Ice resistance (R_ice) at h=1m, V=5kn
  - Open water resistance (R_ow) at V=15kn
  - Stability (GM) — maximized

Design variables: L, B, T, Cb, α_bow, α_stem (6-dim)
Constraints: Cb∈[0.5,0.85], L/B∈[4,7], B/T∈[2.5,4], α_bow∈[15°,35°], IACS UR I
"""
__version__ = "1.0.0"

from .nsga2 import NSGA2Optimizer
from .objectives import evaluate_ice_resistance, evaluate_open_water_resistance, evaluate_gm
from .constraints import check_constraints, CONSTRAINT_BOUNDS
from .optimization import run_ice_class_optimization
