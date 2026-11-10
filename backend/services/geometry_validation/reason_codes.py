"""Machine-readable reason-code registry for the geometry / mesh validation gate.

Every拦截 decision must be attributable to one of these codes so that downstream
statistics, audit review and regression tests never have to parse free text.
Codes are *generic*; domain wording belongs in the profile data / report layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# severity levels -----------------------------------------------------------
ERROR = "error"        # hard拦截: input must not be dispatched to a solver
WARNING = "warning"    # recorded, does not block (e.g. out-of-envelope extrapolation)
INFO = "info"

# stage tags ----------------------------------------------------------------
STAGE_INPUT = "input"
STAGE_GEOMETRY = "geometry"
STAGE_MESH = "mesh"
STAGE_DOMAIN = "domain"
STAGE_PIPELINE = "pipeline"


@dataclass(frozen=True)
class ReasonCode:
    code: str
    severity: str
    stage: str
    description: str

    def as_dict(self) -> Dict[str, str]:
        return asdict(self)


_RAW: List[ReasonCode] = [
    # ---- input / IO -------------------------------------------------------
    ReasonCode("E_INPUT_MISSING", ERROR, STAGE_INPUT,
               "required input field is absent from the batch item"),
    ReasonCode("E_INPUT_TYPE", ERROR, STAGE_INPUT,
               "input field has the wrong type or a non-finite value"),
    ReasonCode("E_FILE_NOT_FOUND", ERROR, STAGE_INPUT,
               "declared geometry / mesh file does not exist"),
    ReasonCode("E_FORMAT_UNSUPPORTED", ERROR, STAGE_INPUT,
               "file suffix or content is outside the supported format set"),
    ReasonCode("E_PARSE", ERROR, STAGE_INPUT,
               "file could not be parsed (truncated / malformed / inconsistent index)"),
    ReasonCode("E_ITEM_EXCEPTION", ERROR, STAGE_PIPELINE,
               "unexpected exception while validating this item (error recovery path)"),
    # ---- surface geometry -------------------------------------------------
    ReasonCode("E_GEOM_EMPTY", ERROR, STAGE_GEOMETRY,
               "surface contains no triangle"),
    ReasonCode("E_GEOM_DEGENERATE", ERROR, STAGE_GEOMETRY,
               "more than the allowed fraction of the surface is degenerate "
               "(zero-area / repeated-vertex) triangles"),
    ReasonCode("W_GEOM_DEGENERATE_DROPPED", WARNING, STAGE_GEOMETRY,
               "a few zero-area triangles were dropped from the integration (no volume impact)"),
    ReasonCode("E_GEOM_NOT_CLOSED", ERROR, STAGE_GEOMETRY,
               "surface is not watertight: boundary or non-manifold edges present"),
    ReasonCode("E_GEOM_INCONSISTENT_ORIENTATION", ERROR, STAGE_GEOMETRY,
               "surface is closed but edge winding is inconsistent (signed volume unreliable)"),
    ReasonCode("E_GEOM_NONPOSITIVE_VOLUME", ERROR, STAGE_GEOMETRY,
               "derived displacement volume is <= 0"),
    ReasonCode("E_GEOM_DECLARED_MISMATCH", ERROR, STAGE_GEOMETRY,
               "derived quantity deviates from the declared value beyond tolerance"),
    ReasonCode("W_GEOM_DECLARED_MISSING", WARNING, STAGE_GEOMETRY,
               "declared geometry value absent; only the derived value is available"),
    ReasonCode("W_GEOM_ORIENTATION_REPAIRED", WARNING, STAGE_GEOMETRY,
               "mixed/inverted face winding was repaired before integration"),
    ReasonCode("W_GEOM_LENGTH_REFERENCE", WARNING, STAGE_GEOMETRY,
               "length reference is bounding-box extent (overhangs may inflate Cb denominator)"),
    # ---- volume mesh ------------------------------------------------------
    ReasonCode("E_MESH_EMPTY", ERROR, STAGE_MESH, "volume mesh contains no cell"),
    ReasonCode("E_MESH_NEGATIVE_VOLUME", ERROR, STAGE_MESH,
               "one or more cells have non-positive volume (inverted / collapsed element)"),
    ReasonCode("E_MESH_SKEWNESS", ERROR, STAGE_MESH,
               "cell skewness exceeds the profile limit"),
    ReasonCode("E_MESH_ASPECT_RATIO", ERROR, STAGE_MESH,
               "cell aspect ratio exceeds the profile limit"),
    ReasonCode("E_MESH_YPLUS", ERROR, STAGE_MESH,
               "wall y+ lies outside the profile target band for the selected wall treatment"),
    ReasonCode("W_MESH_YPLUS_UNKNOWN", WARNING, STAGE_MESH,
               "y+ could not be evaluated (missing wall distance / speed / viscosity)"),
    ReasonCode("W_MESH_QUALITY_UNCHECKED", WARNING, STAGE_MESH,
               "mesh source present but the requested metric is unavailable for this format"),
    # ---- applicability domain --------------------------------------------
    ReasonCode("W_DOMAIN_OUT_OF_ENVELOPE", WARNING, STAGE_DOMAIN,
               "a geometry parameter leaves the applicability envelope of a registered model"),
    ReasonCode("E_DOMAIN_OUT_OF_ENVELOPE", ERROR, STAGE_DOMAIN,
               "a geometry parameter leaves a *blocking* applicability envelope"),
    ReasonCode("W_DOMAIN_NOT_EVALUATED", WARNING, STAGE_DOMAIN,
               "envelope referenced a metric not produced by this item"),
    # ---- profile ----------------------------------------------------------
    ReasonCode("E_PROFILE_INVALID", ERROR, STAGE_PIPELINE,
               "validation profile fails its schema check"),
]

REASON_CODES: Dict[str, ReasonCode] = {rc.code: rc for rc in _RAW}


class GeometryValidationError(Exception):
    """Structured failure carrying a reason code (never a bare exception)."""

    def __init__(self, code: str, message: str, **context: Any) -> None:
        if code not in REASON_CODES:
            raise KeyError(f"unregistered reason code: {code}")
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.context = context

    def as_finding(self) -> "Finding":
        return make_finding(self.code, self.message, **self.context)


@dataclass
class Finding:
    """One auditable check outcome."""

    code: str
    message: str
    severity: str = ERROR
    stage: str = STAGE_PIPELINE
    metric: Optional[str] = None
    value: Optional[float] = None
    limit: Optional[Any] = None
    context: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        out = {
            "code": self.code,
            "severity": self.severity,
            "stage": self.stage,
            "message": self.message,
        }
        if self.metric is not None:
            out["metric"] = self.metric
        if self.value is not None:
            out["value"] = self.value
        if self.limit is not None:
            out["limit"] = self.limit
        if self.context:
            out["context"] = self.context
        return out


_FINDING_FIELDS = {"severity", "stage", "metric", "value", "limit", "context"}


def make_finding(code: str, message: str, **kwargs: Any) -> Finding:
    """Build a :class:`Finding`; unknown keywords are folded into ``context``.

    This keeps call sites terse (``make_finding(code, msg, path=..., format=...)``)
    while guaranteeing that no diagnostic is silently dropped.
    """
    rc = REASON_CODES.get(code)
    if rc is None:
        raise KeyError(f"unregistered reason code: {code}")
    kwargs.setdefault("severity", rc.severity)
    kwargs.setdefault("stage", rc.stage)
    extra = {k: v for k, v in kwargs.items() if k not in _FINDING_FIELDS}
    if extra:
        context = dict(kwargs.get("context") or {})
        context.update(extra)
        kwargs["context"] = context
        for key in extra:
            kwargs.pop(key)
    return Finding(code=code, message=message, **kwargs)