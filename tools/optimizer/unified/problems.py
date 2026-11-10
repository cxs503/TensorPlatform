"""Reference problems for the unified optimizer toolchain (domain neutral).

Six continuous optimisation problems, chosen so that every claim of the
toolchain can be checked against a published optimum instead of against another
run of the same code:

===============  ==========================================  ==================
id               class                                       reference optimum
===============  ==========================================  ==================
rosenbrock_2d    unconstrained, narrow curved valley        0.0
g06              nonlinear inequality (2 constraints)       -6961.81387558
g08              nonlinear inequality + nonconvex            -0.0958250415
g09              nonlinear quartic, 4 inequalities           680.630057375
g10              nonlinear, 6 inequalities (engineering)     7049.24802053
sphere_eq_ineq   convex quadratic, 2 equalities + 1 ineq    3.0 (analytic KKT)
===============  ==========================================  ==================

G06/G08/G09/G10 are the standard constrained test functions from the
Hock–Schittkowski / CEC-2006 collection; ``sphere_eq_ineq`` is a convex problem
whose optimum and KKT multipliers can be written down by hand (x=(1,1,1), f=3,
nu=(2,0), mu=0), which makes it the reference for the stationarity gate.

All numbers below are the published optima; sources are recorded per problem in
``metadata.reference.source`` so the audit can trace every error percentage.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .contract import ConstraintSpec, ObjectiveSpec, ProblemSpec, VariableSpec

__all__ = ["PROBLEM_BUILDERS", "PROBLEM_IDS", "get_problem", "standard_problems"]

_CEC2006 = "CEC-2006 constrained real-parameter optimisation benchmark (Liang et al. 2006); see also Hock & Schittkowski (1981)"


def _rosenbrock_2d() -> ProblemSpec:
    return ProblemSpec(
        problem_id="rosenbrock_2d",
        variables=[
            VariableSpec("x", -2.0, 2.0, x0=-1.2),
            VariableSpec("y", -2.0, 2.0, x0=1.0),
        ],
        objective=ObjectiveSpec(
            expr="(1 - x)**2 + 100*(y - x**2)**2",
            sense="minimize",
            gradient=["-2*(1 - x) - 400*x*(y - x**2)", "200*(y - x**2)"],
        ),
        constraints=[],
        metadata={
            "reference": {
                "objective": 0.0,
                "x": {"x": 1.0, "y": 1.0},
                "source": "Rosenbrock (1960); analytic global minimum f=0 at (1,1)",
                "kind": "analytic",
            },
            "class": "unconstrained nonconvex",
        },
    )


def _g06() -> ProblemSpec:
    # minimise (x1-10)^3 + (x2-20)^3
    # s.t.     (x1-5)^2 + (x2-5)^2 - 100 >= 0 ;  (x1-6)^2 + (x2-5)^2 - 82.81 <= 0
    return ProblemSpec(
        problem_id="g06",
        variables=[
            VariableSpec("x1", 13.0, 100.0, x0=14.0),
            VariableSpec("x2", 0.0, 100.0, x0=0.5),
        ],
        objective=ObjectiveSpec(expr="(x1 - 10)**3 + (x2 - 20)**3", sense="minimize"),
        constraints=[
            ConstraintSpec(
                name="c1", expr="(x1 - 5)**2 + (x2 - 5)**2 - 100", kind="ineq", sense="ge"
            ),
            ConstraintSpec(
                name="c2", expr="(x1 - 6)**2 + (x2 - 5)**2 - 82.81", kind="ineq", sense="le"
            ),
        ],
        metadata={
            "reference": {
                "objective": -6961.81387558015,
                "x": {"x1": 14.095, "x2": 0.84296079},
                "source": _CEC2006 + " — G06 published optimum f*=-6961.8138756",
                "kind": "published",
            },
            "class": "2 nonlinear inequalities",
        },
    )


def _g08() -> ProblemSpec:
    # minimise -sin^3(2 pi x1) sin(2 pi x2) / (x1^3 (x1 + x2))
    # s.t.     x1^2 - x2 + 1 <= 0 ;  1 - x1 + (x2 - 4)^2 <= 0
    return ProblemSpec(
        problem_id="g08",
        variables=[
            VariableSpec("x1", 0.0, 10.0, x0=1.0),
            VariableSpec("x2", 0.0, 10.0, x0=5.0),
        ],
        objective=ObjectiveSpec(
            expr="-sin(2*pi*x1)**3 * sin(2*pi*x2) / (x1**3 * (x1 + x2))", sense="minimize"
        ),
        constraints=[
            ConstraintSpec(name="c1", expr="x1**2 - x2 + 1", kind="ineq", sense="le"),
            ConstraintSpec(name="c2", expr="1 - x1 + (x2 - 4)**2", kind="ineq", sense="le"),
        ],
        metadata={
            "reference": {
                "objective": -0.0958250414180359,
                "x": {"x1": 1.2279713, "x2": 4.2453733},
                "source": _CEC2006 + " — G08 published optimum f*=-0.0958250414",
                "kind": "published",
            },
            "class": "nonconvex objective, 2 nonlinear inequalities",
        },
    )


def _g09() -> ProblemSpec:
    obj = (
        "(x1 - 10)**2 + 5*(x2 - 12)**2 + x3**4 + 3*(x4 - 11)**2 + 10*x5**6 "
        "+ 7*x6**2 + x7**4 - 4*x6*x7 - 10*x6 - 8*x7"
    )
    return ProblemSpec(
        problem_id="g09",
        variables=[VariableSpec(f"x{i}", -10.0, 10.0) for i in range(1, 8)],
        objective=ObjectiveSpec(expr=obj, sense="minimize"),
        constraints=[
            ConstraintSpec(
                name="c1", expr="-127 + 2*x1**2 + 3*x2**4 + x3 + 4*x4**2 + 5*x5", kind="ineq", sense="le"
            ),
            ConstraintSpec(name="c2", expr="-282 + 7*x1 + 3*x2 + 10*x3**2 + x4 - x5", kind="ineq", sense="le"),
            ConstraintSpec(name="c3", expr="-196 + 23*x1 + x2**2 + 6*x6**2 - 8*x7", kind="ineq", sense="le"),
            ConstraintSpec(
                name="c4",
                expr="4*x1**2 + x2**2 - 3*x1*x2 + 2*x3**2 + 5*x6 - 11*x7",
                kind="ineq",
                sense="le",
            ),
        ],
        metadata={
            "reference": {
                "objective": 680.6300573745,
                "x": {
                    "x1": 2.330499,
                    "x2": 1.951372,
                    "x3": -0.4775414,
                    "x4": 4.365726,
                    "x5": -0.6244870,
                    "x6": 1.038131,
                    "x7": 1.594227,
                },
                "source": _CEC2006 + " — G09 published optimum f*=680.6300573",
                "kind": "published",
            },
            "class": "quartic objective, 4 nonlinear inequalities",
        },
    )


def _g10() -> ProblemSpec:
    return ProblemSpec(
        problem_id="g10",
        variables=[
            VariableSpec("x1", 100.0, 10000.0, x0=1000.0),
            VariableSpec("x2", 1000.0, 10000.0, x0=2000.0),
            VariableSpec("x3", 1000.0, 10000.0, x0=3000.0),
            VariableSpec("x4", 10.0, 1000.0, x0=200.0),
            VariableSpec("x5", 10.0, 1000.0, x0=300.0),
            VariableSpec("x6", 10.0, 1000.0, x0=400.0),
            VariableSpec("x7", 10.0, 1000.0, x0=500.0),
            VariableSpec("x8", 10.0, 1000.0, x0=600.0),
        ],
        objective=ObjectiveSpec(
            expr="x1 + x2 + x3",
            sense="minimize",
            gradient=["1", "1", "1", "0", "0", "0", "0", "0"],
        ),
        constraints=[
            ConstraintSpec(
                name="c1",
                expr="-1 + 0.0025*(x4 + x6)",
                kind="ineq",
                sense="le",
                jac=["0", "0", "0", "0.0025", "0", "0.0025", "0", "0"],
            ),
            ConstraintSpec(
                name="c2",
                expr="-1 + 0.0025*(x5 + x7 - x4)",
                kind="ineq",
                sense="le",
                jac=["0", "0", "0", "-0.0025", "0.0025", "0", "0.0025", "0"],
            ),
            ConstraintSpec(
                name="c3",
                expr="-1 + 0.01*(x8 - x5)",
                kind="ineq",
                sense="le",
                jac=["0", "0", "0", "0", "-0.01", "0", "0", "0.01"],
            ),
            ConstraintSpec(
                name="c4",
                expr="100*x1 - x1*x6 + 833.33252*x4 - 83333.333",
                kind="ineq",
                sense="le",
                jac=["100 - x6", "0", "0", "833.33252", "0", "-x1", "0", "0"],
            ),
            ConstraintSpec(
                name="c5",
                expr="x2*x4 - x2*x7 - 1250*x4 + 1250*x5",
                kind="ineq",
                sense="le",
                jac=["0", "x4 - x7", "0", "x2 - 1250", "1250", "0", "-x2", "0"],
            ),
            ConstraintSpec(
                name="c6",
                expr="x3*x5 - x3*x8 - 2500*x5 + 1250000",
                kind="ineq",
                sense="le",
                jac=["0", "0", "x5 - x8", "0", "x3 - 2500", "0", "0", "-x3"],
            ),
        ],
        metadata={
            "reference": {
                "objective": 7049.2480205286,
                "x": {
                    "x1": 579.3167,
                    "x2": 1359.943,
                    "x3": 5110.071,
                    "x4": 182.0174,
                    "x5": 295.5985,
                    "x6": 217.9799,
                    "x7": 286.4162,
                    "x8": 395.5979,
                },
                "source": _CEC2006 + " — G10 published optimum f*=7049.2480205",
                "kind": "published",
            },
            "class": "8 variables, 6 nonlinear inequalities, badly scaled",
        },
    )


def _sphere_eq_ineq() -> ProblemSpec:
    return ProblemSpec(
        problem_id="sphere_eq_ineq",
        variables=[
            VariableSpec("x", -10.0, 10.0, x0=0.0),
            VariableSpec("y", -10.0, 10.0, x0=0.0),
            VariableSpec("z", -10.0, 10.0, x0=0.0),
        ],
        objective=ObjectiveSpec(
            expr="x**2 + y**2 + z**2",
            sense="minimize",
            gradient=["2*x", "2*y", "2*z"],
        ),
        constraints=[
            ConstraintSpec(name="sum_eq3", expr="x + y + z - 3", kind="eq", threshold=0.0,
                           jac=["1", "1", "1"]),
            ConstraintSpec(name="x_eq_y", expr="x - y", kind="eq", threshold=0.0,
                           jac=["1", "-1", "0"]),
            ConstraintSpec(name="z_ge_half", expr="z - 0.5", kind="ineq", sense="ge",
                           jac=["0", "0", "1"]),
        ],
        metadata={
            "reference": {
                "objective": 3.0,
                "x": {"x": 1.0, "y": 1.0, "z": 1.0},
                "source": "hand KKT: x=y=z=1, f=3, nu=(2,0) for the equalities, mu=0 (z-0.5>0 inactive)",
                "kind": "analytic",
            },
            "class": "convex quadratic, 2 equalities + 1 inequality",
        },
    )


PROBLEM_BUILDERS: Dict[str, Callable[[], ProblemSpec]] = {
    "rosenbrock_2d": _rosenbrock_2d,
    "g06": _g06,
    "g08": _g08,
    "g09": _g09,
    "g10": _g10,
    "sphere_eq_ineq": _sphere_eq_ineq,
}

PROBLEM_IDS = tuple(PROBLEM_BUILDERS)


def get_problem(problem_id: str) -> ProblemSpec:
    key = problem_id.strip()
    if key not in PROBLEM_BUILDERS:
        lowered = {k.lower(): k for k in PROBLEM_BUILDERS}
        if key.lower() in lowered:
            key = lowered[key.lower()]
        else:
            raise KeyError(f"unknown reference problem {problem_id!r}; known: {list(PROBLEM_BUILDERS)}")
    return PROBLEM_BUILDERS[key]().validate()


def standard_problems(ids: Optional[List[str]] = None) -> List[ProblemSpec]:
    ids = list(ids) if ids else list(PROBLEM_IDS)
    return [get_problem(pid) for pid in ids]


def reference_table() -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for spec in standard_problems():
        ref = spec.reference
        rows.append(
            {
                "problem_id": spec.problem_id,
                "n_variables": spec.n_variables,
                "n_inequalities": sum(1 for c in spec.constraints if c.kind == "ineq"),
                "n_equalities": sum(1 for c in spec.constraints if c.kind == "eq"),
                "reference_objective": ref.get("objective"),
                "reference_source": ref.get("source", ""),
                "reference_kind": ref.get("kind", ""),
                "fingerprint": spec.fingerprint(),
            }
        )
    return rows