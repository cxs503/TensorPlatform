"""Data model: scan targets, probe records, verdicts.

All records serialise to plain JSON so the report can be regenerated from the
scan artefact alone (no hand-copied numbers).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

#: evidence ladder, weakest → strongest.
LEVELS = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")

LEVEL_RANK = {name: i for i, name in enumerate(LEVELS)}


@dataclass
class Target:
    """One HTTP capability endpoint under test.

    ``base_payload`` is injected as *data* (from an OpenAPI-derived spec, a
    config file or a domain repo); the detector itself carries no knowledge of
    what the fields mean.
    """
    cap_id: str
    method: str
    path: str
    base_payload: Optional[Dict[str, Any]] = None
    sweep_fields: Optional[List[str]] = None
    timeout_s: float = 20.0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProbeReport:
    """Result of the three structural probes against one target."""
    cap_id: str
    method: str
    path: str
    status: int = 0
    http_ok: bool = False
    error: Optional[str] = None
    error_kind: Optional[str] = None   # TRANSPORT | TIMEOUT | PARSE | HTTP

    # determinism: same request twice → same numeric vector
    det_hash: List[str] = field(default_factory=list)
    det_worst_rel: Optional[float] = None
    det_path: Optional[str] = None
    det_ok: Optional[bool] = None
    volatile_metadata: bool = False   # full-payload hash moved, numeric one did not

    # idempotency: A, B(different request), A → A must be unchanged; plus 3x A
    idem_worst_rel: Optional[float] = None
    idem_path: Optional[str] = None
    idem_ok: Optional[bool] = None
    idem_isolation_worst_rel: Optional[float] = None  # A before vs after B
    idem_isolation_ok: Optional[bool] = None
    idem_repeats: int = 0

    # monotonicity / response shape per swept input axis
    axes: List[Dict[str, Any]] = field(default_factory=list)
    axes_probed: int = 0
    axes_rejected: int = 0
    axes_invariant: int = 0
    axes_respond: int = 0

    # global perturbation control: every numeric input scaled at once
    global_scale_worst_rel: Optional[float] = None
    global_scale_ok: Optional[bool] = None

    # global response profile
    n_samples: int = 0
    n_distinct_vectors: int = 0
    n_output_fields: int = 0
    output_fields_sample: List[str] = field(default_factory=list)
    constant_field_fraction: Optional[float] = None

    # verdict
    level: str = "WITHHELD"
    fired_rule: str = ""
    reason_codes: List[str] = field(default_factory=list)
    level_scope: str = "STRUCTURAL"
    accuracy_verified: bool = False
    flags: List[str] = field(default_factory=list)
    detail: str = ""

    def level_placeholder(self) -> str:
        """Coarse outcome class, used by the scanner to decide whether to retry.

        ``rejected`` (400/422) is the only class worth retrying: the payload was
        refused, so nothing was measured yet.
        """
        if self.status in (404, 405, 410, 501):
            return "absent"
        if self.status and 400 <= self.status < 500 and not self.http_ok:
            return "rejected"
        if self.error_kind in ("TRANSPORT", "TIMEOUT", "PARSE"):
            return "unreachable"
        if self.status and 500 <= self.status < 600:
            return "unreachable"
        return "ok"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScanReport:
    schema: str = "mock-detector-scan/1.0"
    generated_at: str = ""
    base_url: str = ""
    config_sha256: str = ""
    code_commit: Optional[str] = None
    probes: List[ProbeReport] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    limits: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["schema"] = self.schema
        return out