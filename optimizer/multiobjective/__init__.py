"""
Multi-Objective Optimization Framework for TensorPlatform.

NSGA-II implementation with comprehensive verification suite:
- Analytical test functions (ZDT, DTLZ)
- Convergence metrics (GD, IGD)
- Diversity metrics (Spread, Hypervolume)
- Ship resistance-seakeeping dual-objective instance
"""

__version__ = "1.0.0"

from .nsga2 import NSGA2
from .test_functions import ZDT1, ZDT2, ZDT3, DTLZ1, DTLZ2
from .metrics import generational_distance, inverted_generational_distance, spread, hypervolume
from .ship_instance import ShipResistanceSeakeeping

__all__ = [
    'NSGA2',
    'ZDT1', 'ZDT2', 'ZDT3', 'DTLZ1', 'DTLZ2',
    'generational_distance', 'inverted_generational_distance',
    'spread', 'hypervolume',
    'ShipResistanceSeakeeping'
]
