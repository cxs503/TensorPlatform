"""Number-level provenance gate for reports (P251-W2-N04).

``sidecar``  -- the ``{key: query_hash | artifact_sha256, source, value,
                computed_at}`` record set;
``render``   -- ``{{prov:key}}`` substitution at publish time plus the render
                manifest that pins the published bytes;
``gate``     -- the gate: render fidelity, key coverage, query re-computation
                and the bare-number ratio policy.

Zero domain semantics: no measurement name, unit or judgement lives here; the
domain supplies the sidecar through the API/config, this package only checks
that the numbers in a report are the ones the sidecar says they are.
"""
from .sidecar import (  # noqa: F401
    PLACEHOLDER_FORMAT,
    PLACEHOLDER_RE,
    SCHEMA_VERSION as SIDECAR_SCHEMA_VERSION,
    ArtifactRecord,
    NumberRecord,
    Sidecar,
    canonical_query,
    query_hash,
    render_number_text,
    sha256_file,
    sha256_text,
    validate_sidecar,
)
from .render import (  # noqa: F401
    MANIFEST_SCHEMA_VERSION,
    MissingKeyError,
    RenderEntry,
    RenderResult,
    render,
)
from .gate import GateConfig, gate_report, scope_regions  # noqa: F401
from .sdk import PublishResult, render_and_gate, verify_published  # noqa: F401

__all__ = [
    "PLACEHOLDER_FORMAT", "PLACEHOLDER_RE", "SIDECAR_SCHEMA_VERSION",
    "ArtifactRecord", "NumberRecord", "Sidecar", "canonical_query", "query_hash",
    "render_number_text", "sha256_file", "sha256_text", "validate_sidecar",
    "MANIFEST_SCHEMA_VERSION", "MissingKeyError", "RenderEntry", "RenderResult", "render",
    "GateConfig", "gate_report", "scope_regions",
    "PublishResult", "render_and_gate", "verify_published",
]
