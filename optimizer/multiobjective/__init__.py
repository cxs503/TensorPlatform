"""
Multi-Objective Optimization Framework for TensorPlatform.

NSGA-II implementation with comprehensive verification suite:
- Analytical test functions (ZDT, DTLZ)
- Convergence metrics (GD, IGD, Hausdorff)
- Diversity metrics (Spread, Spacing, Hypervolume)
- Constrained (feasibility-rule) non-dominated sorting

Ship/domain instances are *not* bundled with this generic package: domain
content lives in the domain repositories (here: ``domains/<domain>/``).
"""

__version__ = "1.1.0"

from .nsga2 import (
    NSGA2,
    fast_non_dominated_sort,
    fast_constrained_non_dominated_sort,
    crowding_distance,
)
from .test_functions import ZDT1, ZDT2, ZDT3, DTLZ1, DTLZ2
from .metrics import (
    generational_distance,
    inverted_generational_distance,
    spread,
    spacing,
    hypervolume,
    hausdorff_distance,
    hypervolume_convergence,
)

__all__ = [
    'NSGA2',
    'fast_non_dominated_sort', 'fast_constrained_non_dominated_sort',
    'crowding_distance',
    'ZDT1', 'ZDT2', 'ZDT3', 'DTLZ1', 'DTLZ2',
    'generational_distance', 'inverted_generational_distance',
    'spread', 'spacing', 'hypervolume', 'hausdorff_distance',
    'hypervolume_convergence',
]