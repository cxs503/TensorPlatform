"""Cross-solver verification frame (TensorPlatform, mission 7119 / node p83w1-n07).

Why this package exists
-----------------------
The ecosystem produces verified case artifacts in a dozen mutually incompatible
shapes: one repository writes ``grids/128/max_abs_dev_pct``, another writes
``cases[3].rel_error``, a third writes ``solver_results/<Fn>/<Solver>/c_t``.  As
long as every consumer reads those shapes itself, "the two solvers agree to X %"
is an unfalsifiable statement: nobody can say whether the difference is
numerical, or a different definition of the reported coefficient.

This package splits that into four mechanical steps, each independently
checkable:

``normalize``      artifact (any shape, described by config vocabulary) ->
                   canonical input record + canonical output record
``conservation``   physical residual gate (mass / momentum / energy); a case
                   that fails it is *excluded* from comparison instead of being
                   silently averaged in
``adjudicate``     group canonical records by the physical problem they answer,
                   compare like-for-quantity, and attribute every difference to
                   term coverage / normalisation / discretisation
``dag_consumer``   project the adjudication into the one document a campaign DAG
                   verification node consumes (``NodeVerdictUpdate``)

All domain vocabulary (which artifact key carries geometry, which name means
``C_T``, which solver belongs to which family) lives in ``config/xsolver/*.json``
and is injected at run time; the code carries mechanics only.  The JSON Schemas
of the two canonical records live in ``schemas/xsolver/``.
"""
from __future__ import annotations

from .conservation import gate as conservation_gate
from .corpus import harvest, load_corpus, save_corpus
from .normalize import build_case, canonical_json, digest_of
from .adjudicate import adjudicate

__all__ = [
    "adjudicate",
    "build_case",
    "canonical_json",
    "conservation_gate",
    "digest_of",
    "harvest",
    "load_corpus",
    "save_corpus",
]
__version__ = "1.0.0"