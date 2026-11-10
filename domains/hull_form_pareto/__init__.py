"""Hull-form multi-objective (resistance / stability / capacity) Pareto study.

Domain package (TensorPlatform *domains/* tree): the generic NSGA-II,
metrics and constraint machinery is imported from ``optimizer.multiobjective``;
everything ship-specific (parametric hull, hydrostatics, Holtrop–Mennen call,
cargo-volume integral, acceptance criteria) lives here.
"""

__version__ = "1.0.0"

from .hull import (  # noqa: F401
    DesignSpace,
    HullForm,
    build_hull,
    load_design_space,
    resistance,
    sectional_fullness_profile,
)
from .objectives import Evaluation, evaluate  # noqa: F401
from .pareto import (  # noqa: F401
    run_hull_pareto,
    verify_zdt1,
    negative_control_single_objective,
    save_artifacts,
)
