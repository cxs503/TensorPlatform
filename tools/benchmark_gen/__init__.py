"""Benchmark dataset generation with parameterised bodies and sha256 provenance.

Public surface::

    from tools.benchmark_gen import generate_dataset, generate_matrix
    from tools.benchmark_gen import provenance

    doc = generate_dataset(body_spec, condition_spec, seed=20260101)
    doc["provenance"]["dataset_id"]          # sha256(params + seed + version)
    provenance.verify_record(doc)["ok"]      # recomputes both digests

A *body* is a set of seven geometry parameters (see
:class:`tools.benchmark_gen.numeric.BodyForm`), a *condition* is an immersion
ratio plus fluid properties and a speed list; both are injected from JSON, so the
generator itself carries no vessel- or domain-specific vocabulary.
"""

from . import analytic, numeric, provenance  # noqa: F401
from .generator import (  # noqa: F401
    ANALYTIC_TOLERANCE,
    GENERATOR_VERSION,
    PIPELINES,
    code_fingerprint,
    generate_dataset,
    generate_matrix,
    hydrostatics_pipeline,
    load_spec_directory,
    register_pipeline,
    resolve_pipeline,
)

__all__ = [
    "ANALYTIC_TOLERANCE",
    "GENERATOR_VERSION",
    "PIPELINES",
    "analytic",
    "code_fingerprint",
    "generate_dataset",
    "generate_matrix",
    "hydrostatics_pipeline",
    "load_spec_directory",
    "numeric",
    "provenance",
    "register_pipeline",
    "resolve_pipeline",
]