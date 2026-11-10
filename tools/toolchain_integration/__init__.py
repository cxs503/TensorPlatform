"""Toolchain integration harness (platform mechanism, zero domain semantics).

The platform owns a set of toolchain stages that were each verified on their own:
an optimisation library, a forward-UQ framework, a conservative field-transfer
library with conservation gates, a feasibility-prior calibrator, and stages that
live in sibling repositories.  What was missing is a machine-checkable answer to
"do they *compose*?" -- interface compatibility, end-to-end wiring, reproducible
outputs, and the cost of composition.

This package answers exactly that, without knowing what any stage means:

* :mod:`~.schema` -- declarative pipeline spec (nodes, ports, bindings), validated
  before execution (topological order, unique ids, resolvable references);
* :mod:`~.registry` -- adapter port contracts (tagged ports: scalar / series /
  callable / field / report / flag) and the evidence level each adapter can claim;
* :mod:`~.adapters` -- the stage implementations behind those contracts;
* :mod:`~.dag` -- static port/tag checking, fail-closed execution, honest evidence
  roll-up, replayable hash-chained record;
* :mod:`~.prior` -- logistic feasibility-prior calibration with Brier / ECE /
  AUROC statistics (monotonicity is reported, never assumed);
* :mod:`~.perf` -- standalone vs integrated timing and the integration overhead;
* :mod:`~.report` -- artifact JSON, dependency graph, figures, markdown report;
* :mod:`~.cli` -- ``python -m tools.toolchain_integration.cli --spec ...``.

Design rule that makes the evidence trustworthy: a stage that cannot run degrades
to ``WITHHELD``; a stage that ran through a declared fallback is recorded as
``MOCK``; the run's evidence level is the weakest node's.  A green run therefore
means "the stages composed and produced reproducible, gate-passing numbers", not
"somebody typed PASS".
"""

from __future__ import annotations

from . import adapters as _adapters  # noqa: F401  (registration side effect)
from .dag import (
    Plan,
    PipelineError,
    PipelineRun,
    NodeRecord,
    build_plan,
    run_pipeline,
    run_spec,
)
from .perf import OverheadReport, StageTiming, measure
from .prior import CalibrationError, CalibrationReport, calibrate, fit_logistic
from .registry import (
    Adapter,
    AdapterError,
    AdapterUnavailable,
    ExecutionContext,
    Port,
    PortValue,
    catalogue,
    get as get_adapter,
    known as known_adapters,
)
from .schema import (
    EVIDENCE_LEVELS,
    SCHEMA_VERSION,
    Binding,
    NodeSpec,
    PipelineSpec,
    SpecError,
    weakest,
)

__all__ = [
    "Adapter",
    "AdapterError",
    "AdapterUnavailable",
    "Binding",
    "CalibrationError",
    "CalibrationReport",
    "EVIDENCE_LEVELS",
    "ExecutionContext",
    "NodeRecord",
    "NodeSpec",
    "OverheadReport",
    "PipelineError",
    "PipelineRun",
    "PipelineSpec",
    "Plan",
    "Port",
    "PortValue",
    "SCHEMA_VERSION",
    "SpecError",
    "StageTiming",
    "build_plan",
    "calibrate",
    "catalogue",
    "fit_logistic",
    "get_adapter",
    "known_adapters",
    "measure",
    "run_pipeline",
    "run_spec",
    "weakest",
]

TOOLCHAIN_SCHEMA = "tp.toolchain.integration/v1"