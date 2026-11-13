"""External tool-class onboarding: probe → smoke call → invariant → evidence.

The platform's question is not "did we import a library" but "is this class of
external tool *usable here*, and can we prove it with a number".  This package
answers that with a declarative spec (:mod:`~.spec`), observed availability
(:mod:`~.probe`), one real call per class (:mod:`~.adapters`) and a fail-closed
ledger (:mod:`~.harness`).  The KPI it serves is coverage: how many classes reach
``REAL`` against the declared target.

Method lineage (why this shape, and where it comes from):

* Registry/Plugin pattern -- Fowler, *Patterns of Enterprise Application
  Architecture* (2002), "Plugin" + "Registry"; Python's ``entry point`` mechanism
  (https://packaging.python.org/en/latest/specifications/entry-points/) is the
  standard-library-grade instance, and it is how pytest/MLflow/DVC find plugins.
* Capability negotiation before use -- Model Context Protocol spec, ``initialize``
  then ``tools/list`` (https://modelcontextprotocol.io/specification/2025-06-18):
  declare nothing you have not probed.
* Probe-based health semantics -- Kubernetes liveness/readiness/startup probes
  (https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/):
  "running" is not "ready".
* Quality model vocabulary -- ISO/IEC 25010:2011 interoperability/co-existence,
  i.e. a component is only integrated when it demonstrably exchanges data.

Nothing in this package knows what a tool is *for*: ids, categories and thresholds
come from the spec file (data), so the mechanism stays domain-neutral.
"""

from __future__ import annotations

from .harness import (  # noqa: F401
    VERDICT_FAIL,
    VERDICT_PARTIAL,
    VERDICT_PASS,
    run_class,
    run_suite,
    verdict,
)
from .report import render_markdown, write_artifacts  # noqa: F401
from .spec import (  # noqa: F401
    EVIDENCE_LEVELS,
    Invariant,
    SPEC_VERSION,
    SpecError,
    Suite,
    ToolClass,
    load_suite,
    weakest,
)

__all__ = [
    "EVIDENCE_LEVELS",
    "VERDICT_FAIL",
    "VERDICT_PARTIAL",
    "VERDICT_PASS",
    "Invariant",
    "SPEC_VERSION",
    "SpecError",
    "Suite",
    "ToolClass",
    "load_suite",
    "render_markdown",
    "run_class",
    "run_suite",
    "verdict",
    "weakest",
    "write_artifacts",
]