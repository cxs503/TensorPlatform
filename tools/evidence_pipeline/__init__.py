"""End-to-end evidence-governance pipeline (domain-neutral).

The package wires four previously separate capabilities into one chain:

``benchmark config`` → ``generator execution`` → ``gate verdict`` →
``artifact`` → ``ingest gate (fail-closed)`` → ``regression`` → ``provenance``

Nothing here knows what a number *means*: a benchmark declares a generator, the
metrics that generator must emit, and the gates those metrics have to satisfy.
Domain meaning arrives only through configuration/artifacts, which keeps the
platform layer free of domain vocabulary.

Modules
-------
:mod:`tools.evidence_pipeline.config`
    Benchmark configuration (generator, params, gates, regression policy) and
    its canonical digest.
:mod:`tools.evidence_pipeline.execution`
    Subprocess execution of ``generators/<node>.py`` plus payload validation.
:mod:`tools.evidence_pipeline.gate`
    Gate registry and dispatch (``numeric`` plus the ``conservation`` gate type).
:mod:`tools.evidence_pipeline.conservation_gate`
    Cross-library field-exchange conservation gate; delegates the metric
    definitions to :mod:`tools.coupling.conservation`.
:mod:`tools.evidence_pipeline.artifact`
    Artifact construction and the ``--gate`` ingest mode (generator identity +
    replay consistency, fail-closed).
:mod:`tools.evidence_pipeline.regression`
    Baseline comparison, drift classification and re-run policy.
:mod:`tools.evidence_pipeline.runner`
    The orchestrator (CLI): config → execute → gates → artifact → ingest gate →
    regression → provenance chain.
"""

from __future__ import annotations

__all__ = [
    "ARTIFACT_SCHEMA",
    "PAYLOAD_SCHEMA",
    "RUN_SCHEMA",
    "PipelineError",
]


class PipelineError(RuntimeError):
    """Raised for configuration/contract violations (fail-closed)."""


ARTIFACT_SCHEMA = "evidence-pipeline/artifact/1"
PAYLOAD_SCHEMA = "evidence-pipeline/payload/1"
RUN_SCHEMA = "evidence-pipeline/run/1"