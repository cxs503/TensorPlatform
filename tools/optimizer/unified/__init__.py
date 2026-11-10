"""Unified optimisation toolchain for TensorPlatform (domain neutral).

One interface, three external engines:

* **SciPy** -- SLSQP / L-BFGS-B / Nelder-Mead / Powell / COBYLA / trust-constr
* **NLopt** -- COBYLA / SLSQP / MMA / LBFGS / BOBYQA / NELDERMEAD / SBPLX /
  DIRECT / ISRES / AUGLAG
* **pyoptsparse** -- SNOPT / IPOPT / SLSQP / NLPQLP / PSQP (API implemented;
  the library is absent on this platform and every call reports
  ``status="unavailable"`` instead of inventing numbers)

Everything the engines exchange goes through two published JSON Schemas
(``schemas/optimizer_problem.v1.schema.json`` and
``.../optimizer_result.v1.schema.json``), so the toolchain can be composed with
any platform stage, script or audit without importing engine-specific objects.

Quick start
-----------
>>> from tools.optimizer.unified import get_problem, solve, validate_result
>>> spec = get_problem("rosenbrock_2d")
>>> res = solve(spec, engine="scipy", algorithm="SLSQP")
>>> res.status, round(res.objective, 12)
('converged', 0.0)
>>> validate_result(res)["schema"]
'optimizer/result/v1'
"""

from __future__ import annotations

from .adapters import (
    ENGINES,
    AdapterUnavailable,
    AlgorithmSpec,
    NLoptAdapter,
    OptimizerAdapter,
    ProblemNotSupported,
    PyOptSparseAdapter,
    ScipyAdapter,
    algorithm_matrix,
    available_engines,
    get_adapter,
    probe_all,
    probe_requested_algorithms,
    solve,
)
from .compiled import CompiledProblem
from .contract import (
    SCHEMA_PROBLEM,
    SCHEMA_RESULT,
    STATUSES,
    ConstraintSpec,
    ContractError,
    EngineProbe,
    HistoryPoint,
    ObjectiveSpec,
    ProblemSpec,
    SolveResult,
    SolveStatus,
    VariableSpec,
    environment_provenance,
)
from .crosscheck import crosscheck
from .dsl import (
    DSLValidationError,
    PROBLEM_SCHEMA_PATH,
    RESULT_SCHEMA_PATH,
    compile_problem,
    dump_problem,
    dump_result,
    load_problem,
    loads_problem,
    validate_problem_dict,
    validate_result,
    validate_result_dict,
)
from .kkt import kkt_diagnostics
from .problems import PROBLEM_IDS, get_problem, reference_table, standard_problems

__version__ = "1.0.0"

__all__ = [
    "__version__",
    # engines
    "ENGINES",
    "OptimizerAdapter",
    "ScipyAdapter",
    "NLoptAdapter",
    "PyOptSparseAdapter",
    "AdapterUnavailable",
    "ProblemNotSupported",
    "AlgorithmSpec",
    "get_adapter",
    "available_engines",
    "probe_all",
    "probe_requested_algorithms",
    "algorithm_matrix",
    "solve",
    # contract
    "ProblemSpec",
    "VariableSpec",
    "ObjectiveSpec",
    "ConstraintSpec",
    "SolveResult",
    "SolveStatus",
    "HistoryPoint",
    "EngineProbe",
    "ContractError",
    "SCHEMA_PROBLEM",
    "SCHEMA_RESULT",
    "STATUSES",
    "environment_provenance",
    "CompiledProblem",
    "kkt_diagnostics",
    # dsl
    "PROBLEM_SCHEMA_PATH",
    "RESULT_SCHEMA_PATH",
    "DSLValidationError",
    "load_problem",
    "loads_problem",
    "compile_problem",
    "dump_problem",
    "dump_result",
    "validate_problem_dict",
    "validate_result",
    "validate_result_dict",
    # problems / adjudication
    "PROBLEM_IDS",
    "get_problem",
    "standard_problems",
    "reference_table",
    "crosscheck",
]