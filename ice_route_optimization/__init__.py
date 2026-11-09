"""
Ice Route Speed Optimization Module
====================================

Energy-optimal speed decision for ice-zone voyage using dynamic programming.

Solves the bi-objective optimization problem:
  min (fuel_consumption, arrival_time)
subject to:
  - ship resistance model (open water + ice + wind)
  - speed bounds [v_min, v_max]
  - route graph with spatially-varying ice conditions

Outputs:
  - Pareto-optimal speed profiles
  - Fuel-time trade-off frontier
  - Sensitivity to ice concentration

Physics basis:
  - Holtrop-Mennen 1984 open-water resistance
  - TP-POLAR-1 level-ice resistance (crushing + breaking + submersion)
  - ITTC-1957 friction line
  - Quasi-static steady-state assumption (no acceleration energy)

Numerical method:
  - Dynamic programming on discretized route graph
  - Time-bucket discretization for arrival-time constraint
  - Exhaustive search for small graphs (verification)

Author: AI citizen agent (mission m7848)
Date: 2026-11-09
"""

from .core import (
    IceRouteOptimizer,
    RouteGraph,
    IceCondition,
    OptimizationResult,
    ParetoFront,
)

__version__ = "1.0.0"
__all__ = [
    "IceRouteOptimizer",
    "RouteGraph",
    "IceCondition",
    "OptimizationResult",
    "ParetoFront",
]
