"""Capability evidence chain (p474w1-n01): cross-repo evidence -> TOST/MDE.

Public API
----------
``load_config``            configuration model (repos, metric patterns, margins)
``scanner.scan``           discover verification artefacts and extract metrics
``binder.associate``       artefact -> capability association rate
``binder.evaluate``        TOST (mean / variance / distribution) + power + MDE
``reclassify.scan_claims`` historical PASS census with power/MDE audit
``pipeline.run``           one call that produces the report artefact

CLI::

    python3 -m tools.capability_evidence_chain.cli run \
        --config tools/capability_evidence_chain/configs/ecosystem_solvers.json \
        --out artifacts/p474w1_n01/evidence_chain.json

Design constraint: **zero domain semantics** — every physical word, margin and
repository path arrives through the JSON config; the tests use synthetic
repository trees (``synth_solver_a`` …) only.
"""
from __future__ import annotations

from . import binder, config, equivalence, pipeline, reclassify, scanner  # noqa: F401
from .config import EvidenceChainConfig, load_config  # noqa: F401
from .equivalence import TostResult  # noqa: F401

__all__ = [
    "binder",
    "config",
    "equivalence",
    "pipeline",
    "reclassify",
    "scanner",
    "EvidenceChainConfig",
    "load_config",
    "TostResult",
]

__version__ = "1.0.0"