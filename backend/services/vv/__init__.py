"""Numerical verification & validation (solution verification) toolbox.

Currently exposes the grid-convergence / GCI machinery used to certify that a
reported number is resolved to a stated discretisation error:

    from services.vv import Level, assess_convergence
    rep = assess_convergence([Level(h=0.02, value=81.3), Level(h=0.04, value=83.9),
                              Level(h=0.08, value=89.4)],
                             quantity="C_T", expected_order=2)
    rep.passed, rep.gci_fine, rep.observed_order

See ``backend/services/vv/gci.py`` for the equations and
``docs/verifier/NUMERICAL_VV_GCI.md`` for the verification of this module
itself against analytic solutions.
"""
from services.vv.gci import (  # noqa: F401
    DIVERGENCE,
    INDETERMINATE,
    MONOTONE,
    OSCILLATORY,
    ConvergenceReport,
    Level,
    assess_convergence,
    gci_pair,
    observed_order,
    richardson_value,
)

__all__ = [
    "Level",
    "ConvergenceReport",
    "assess_convergence",
    "observed_order",
    "richardson_value",
    "gci_pair",
    "MONOTONE",
    "OSCILLATORY",
    "DIVERGENCE",
    "INDETERMINATE",
]