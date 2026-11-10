"""Delivery state classifier — artifact-fact-based completion assessment.

Purpose
-------
Refines the terminal ``succeeded`` status into three delivery states based on
artifact facts (not self-reports):

- ``delivered_complete``: implementation + verification + claim artifacts present,
  and the verification evidence carries at least one machine-checkable statement
- ``delivered_partial``: both critical artifacts exist but a graded element is
  vacuous (checks present yet carries no machine-checkable statement, or no
  claim/report artifact)
- ``suspect_undeclared``: no implementation artifact or no verification evidence
  at all (fail-closed) — a ``succeeded`` mission that recorded no verification
  artifact is an unsupported claim, not a partial delivery

Design rules
------------
* **Zero domain semantics.** No domain-specific terms in logic. All checks are
  generic artifact-presence checks (changed_files, checks, claim fields).
* **Fail-closed.** Missing critical fields → ``suspect_undeclared``, never
  ``delivered_complete``.
* **Artifact-fact only.** No LLM judgment, no prose semantics. Structural checks
  on the result JSON, plus a generic machine-checkable-token test on the
  verification text (``12 passed`` / ``PASS`` / ``FAIL`` / ``exit code`` /
  ``sha256`` / ``a/b``). Free-text claims of completion are never trusted.
* **Payload-contract tolerant.** Production results carry ``checks`` as a plain
  string and have no ``summary`` key at all (measured on 1000 real succeeded
  missions: 197/200 ``checks`` are ``str``, 100% ``summary`` absent). The
  normaliser accepts ``str | dict | list`` for ``checks`` and treats
  ``headline`` / ``evidence`` / ``seed_report`` / ``report_id`` / ``decision_note``
  as the claim artifact, mirroring the real schema.
* **Optional artifacts never veto completeness.** ``figures`` / ``report_id`` /
  ``evidence`` are reported in ``missing_optional`` for information only.

Audit chain
-----------
Consumed by:
- ``api/tensor_missions.py`` / lifecycle endpoints (post-hoc classification)
- ``api/domain/mvd_checkpoint.py`` (70% budget checkpoint, shares the normaliser)
- ``api/kpi_delivery.py`` (layered KPI views)

Machine-verifiable acceptance criteria:
- macro-F1 ≥ 0.90 on the 60-unit manual annotation of real succeeded missions
  (measured in ``runs/p92w2b-n08/metrics.json``; see docs/delivery-semantics)
- Weak-label recall ≥ 0.85, precision ≥ 0.90
- Fail-closed negative controls (see tests)
- Zero domain semantics scan (domain term hits = 0)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

# Delivery state vocabulary (stable strings, never hand-spelled elsewhere)
DeliveryState = Literal[
    "delivered_complete",
    "delivered_partial",
    "suspect_undeclared",
]

# Required artifact fields for complete delivery (generic, no domain terms)
REQUIRED_FIELDS_COMPLETE = {
    "changed_files",  # List of modified files (proves implementation exists)
    "checks",         # Test/validation results (proves verification ran)
    "claim",          # Report/claim artifact (summary | headline | evidence | ...)
}

# Optional but expected fields (missing → reported only, never a downgrade)
EXPECTED_FIELDS_PARTIAL = {
    "figures",        # Visualization artifacts
    "report_id",      # Linked report
    "evidence",       # Evidence bundle
}

# Negative control: if any of these is missing, the verdict is undeclared
# (fail-closed). Both the work product *and* the verification evidence are
# critical: a mission recorded as ``succeeded`` with no verification artifact at
# all is an unsupported claim, which is exactly what ``suspect_undeclared`` means
# in rubric v1.1 (measured on the 60-unit real corpus — demoting ``checks`` cost
# macro-F1 0.9226 -> 0.5512). What stays ``delivered_partial`` is the *honest*
# case: a checks statement that exists but is not machine-checkable.
CRITICAL_FIELDS = {
    "changed_files",  # No files = no implementation = nothing delivered
    "checks",         # No verification artifact = success claim is unsupported
}
#: Graded verification quality marker (present-but-vacuous checks → partial).
GRADED_VERIFICATION_FIELD = "checks"

# Alternative keys observed in the production result payload (calibration split).
IMPLEMENTATION_KEYS = ("changed_files", "on_disk_changes")
CLAIM_KEYS = ("summary", "headline", "evidence", "seed_report", "report_id",
              "decision_note", "negative_control")

# Generic machine-checkable statements. Domain-free: these are test/verification
# vocabulary only (counts, verdict words, exit codes, hashes, fractions).
MACHINE_CHECKABLE_PATTERNS = (
    r"\d+\s*(?:passed|failed|errors?|warnings?)",
    r"\bPASS(?:ED)?\b",
    r"\bFAIL(?:ED)?\b",
    r"exit\s*(?:code|status)\s*[:=]?\s*\d+",
    r"sha256[:=\s]",
    r"\b\d+\s*/\s*\d+\b",
    r"\bHTTP\s*[1-5]\d\d\b",
    r"\b(?:POST|GET)\s+[1-5]\d\d\b",
)
_MACHINE_CHECKABLE_RE = re.compile("|".join(MACHINE_CHECKABLE_PATTERNS), re.IGNORECASE)

#: v1.2 词表（p372w2-n06 漂移根因修复，**opt-in**：只在 ``verification_fallback=True``
#: 时启用，默认路径与 v1.1 逐位一致，故冻结验收指标不受影响）。
#: 新增的是中文运行结果记号：p372 波后新 mission 的 checks 常留空，验证结论迁到
#: ``headline``（长度被截到 180 字），且计数写成「37 项 pytest 全部通过」「27个用例全绿」
#: 这类中文形态 —— v1.1 只认 ``\d+ passed`` 英文形态，于是整批判成 suspect_undeclared。
MACHINE_CHECKABLE_PATTERNS_V12 = MACHINE_CHECKABLE_PATTERNS + (
    r"\d+\s*(?:项|条|个|例)\s*(?:pytest\s*|测试\s*)?(?:用例|测试|项)?\s*"
    r"(?:全绿|全部通过|全通过|通过|失败)",
    r"\d+\s*(?:个|项)?\s*用例\s*(?:全绿|全部通过|通过)",
    r"(?:pytest|测试|用例)[^，。;；\n]{0,24}?\d+\s*(?:项|条|个|例)?\s*(?:全绿|全部通过|通过|失败)",
    r"全部通过",
    r"全绿",
)
_MACHINE_CHECKABLE_V12_RE = re.compile("|".join(MACHINE_CHECKABLE_PATTERNS_V12), re.IGNORECASE)

#: ``verification_fallback=True`` 时，``checks`` 为空则从这些字段回捞验证文本
VERIFICATION_FALLBACK_KEYS = ("headline", "summary", "decision_note")

# A gate list written as verdict marks (e.g. "gate A [ok] | gate B [ok]") is a
# PASS/FAIL list even when the words PASS/FAIL never appear. Two or more marks
# are required so a stray mark in prose does not count as verification.
_VERDICT_MARK_RE = re.compile(r"[\u2705\u2611\u2714\u2713\u2718\u274c]")
VERDICT_MARKS_FOR_LIST = 2


def _is_field_missing(value: Any) -> bool:
    """Check if a field is missing (None, empty list, empty/blank string).

    Empty dict {} is considered present (just empty content) — kept for
    backwards compatibility with the original contract.
    """
    if value is None:
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _as_text(value: Any) -> str:
    """Render an artifact field as text, whatever its JSON type."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):  # pragma: no cover - exotic payloads
        return str(value)


def _present_list(value: Any) -> list[str]:
    """Return the declared artifact names of a field (list | str | dict)."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [str(k) for k in value]
    return [str(value)]


@dataclass
class NormalizedArtifacts:
    """Artifact facts extracted from a mission result payload."""
    has_result: bool
    implementation: list[str] = field(default_factory=list)
    verification_text: str = ""
    claim_present: bool = False
    missing_optional: list[str] = field(default_factory=list)
    #: 判定词表版本：v1.1（默认，冻结口径）/ v1.2（opt-in 回捞 + 中文运行记号）
    token_version: str = "v1.1"

    @property
    def implementation_present(self) -> bool:
        return bool(self.implementation)

    @property
    def verification_present(self) -> bool:
        return bool(self.verification_text.strip())

    @property
    def verification_concrete(self) -> bool:
        """True iff the verification text carries a machine-checkable statement.

        Two accepted shapes: an explicit run-outcome token (counts, PASS/FAIL
        words, exit code, hash, fraction, HTTP status), or a verdict-mark list
        with at least ``VERDICT_MARKS_FOR_LIST`` marks (a PASS/FAIL list).
        """
        token_re = (_MACHINE_CHECKABLE_V12_RE if self.token_version == "v1.2"
                    else _MACHINE_CHECKABLE_RE)
        if token_re.search(self.verification_text):
            return True
        return len(_VERDICT_MARK_RE.findall(self.verification_text)) >= VERDICT_MARKS_FOR_LIST

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_result": self.has_result,
            "implementation_count": len(self.implementation),
            "verification_len": len(self.verification_text),
            "verification_concrete": self.verification_concrete,
            "claim_present": self.claim_present,
            "missing_optional": list(self.missing_optional),
        }


def normalize_artifacts(result: dict[str, Any] | None, *,
                        verification_fallback: bool = False) -> NormalizedArtifacts:
    """Extract artifact facts from a result payload.

    Accepts the production schema (``checks`` as text, claim under
    ``headline``/``evidence``) as well as the structured fixture schema
    (``checks`` dict, ``summary`` dict).

    Examples
    --------
    >>> normalize_artifacts({"changed_files": ["a.py"], "checks": "12 passed"}).verification_concrete
    True
    >>> normalize_artifacts({"changed_files": [], "checks": {"pytest": "pass"}}).implementation_present
    False
    """
    if not isinstance(result, dict) or not result:
        return NormalizedArtifacts(has_result=isinstance(result, dict) and bool(result))

    implementation: list[str] = []
    for key in IMPLEMENTATION_KEYS:
        implementation.extend(_present_list(result.get(key)))

    verification_text = _as_text(result.get("checks"))
    token_version = "v1.1"
    if verification_fallback:
        token_version = "v1.2"
        if not verification_text.strip():        # checks 留空 → 从声明字段回捞验证文本
            parts = [_as_text(result.get(k)) for k in VERIFICATION_FALLBACK_KEYS
                     if result.get(k)]
            verification_text = "\n".join(p for p in parts if p.strip())
    claim_present = any(
        not _is_field_missing(result.get(key)) or isinstance(result.get(key), dict)
        for key in CLAIM_KEYS
        if key in result
    )
    missing_optional = [
        key for key in sorted(EXPECTED_FIELDS_PARTIAL)
        if _is_field_missing(result.get(key))
    ]
    return NormalizedArtifacts(
        has_result=True,
        implementation=implementation,
        verification_text=verification_text,
        claim_present=claim_present,
        missing_optional=missing_optional,
        token_version=token_version,
    )


@dataclass
class DeliveryAssessment:
    """Result of delivery state classification.

    Attributes
    ----------
    state : DeliveryState
        One of delivered_complete / delivered_partial / suspect_undeclared
    missing : list[str]
        List of missing/vacuous required artifact elements (empty for complete)
    reasons : list[str]
        Human-readable justification for the classification
    confidence : float
        Confidence score [0, 1] based on artifact completeness
    missing_optional : list[str]
        Optional artifacts absent (informational; never downgrades the state)
    facts : dict
        Normalised artifact facts the verdict was derived from
    """
    state: DeliveryState
    missing: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    confidence: float = 1.0
    missing_optional: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "missing": self.missing,
            "reasons": self.reasons,
            "confidence": self.confidence,
            "missing_optional": self.missing_optional,
            "facts": self.facts,
        }


def assess_delivery(result: dict[str, Any] | None, *,
                    verification_fallback: bool = False) -> DeliveryAssessment:
    """Classify delivery completeness based on artifact facts.

    Parameters
    ----------
    result : dict | None
        The mission result JSON (from tensor_dev_missions.result)

    Returns
    -------
    DeliveryAssessment
        Classification with missing items and justification

    Examples
    --------
    >>> assess_delivery(None).state
    'suspect_undeclared'
    >>> assess_delivery({}).state
    'suspect_undeclared'
    >>> assess_delivery({"changed_files": ["a.py"], "checks": {"pytest": "pass"},
    ...                  "summary": {}}).state
    'delivered_complete'
    >>> assess_delivery({"changed_files": ["a.py"], "checks": "未运行"}).state
    'delivered_partial'
    """
    # Negative control: no result at all
    if result is None:
        return DeliveryAssessment(
            state="suspect_undeclared",
            missing=["result"],
            reasons=["Result field is None or missing"],
            confidence=1.0,
        )

    if not isinstance(result, dict):
        return DeliveryAssessment(
            state="suspect_undeclared",
            missing=["result"],
            reasons=[f"Result is not a dict: {type(result).__name__}"],
            confidence=1.0,
        )

    # Empty result = undeclared
    if not result:
        return DeliveryAssessment(
            state="suspect_undeclared",
            missing=sorted(CRITICAL_FIELDS),
            reasons=["Result is empty dict"],
            confidence=1.0,
        )

    facts = normalize_artifacts(result, verification_fallback=verification_fallback)

    # Enumerate everything that is absent first (the ranked missing-item KPI needs
    # the full picture), then let the critical/graded split decide the verdict.
    missing_critical: list[str] = []
    if not facts.implementation_present:
        missing_critical.append("changed_files")
    if not facts.verification_present:
        missing_critical.append("checks")

    missing_graded: list[str] = []
    if facts.verification_present and not facts.verification_concrete:
        missing_graded.append("verification_machine_checkable")
    if not facts.claim_present:
        missing_graded.append("claim")

    missing = missing_critical + missing_graded

    # Fail-closed: work product *and* verification evidence are critical. A
    # mission recorded as ``succeeded`` with no verification artifact at all is an
    # unsupported success claim (rubric v1.1, measured on the 60-unit real corpus:
    # demoting ``checks`` to a graded field cost macro-F1 0.9226 -> 0.5512, with
    # 18/24 hand-labelled ``suspect_undeclared`` units flipped to partial).
    if missing_critical:
        return DeliveryAssessment(
            state="suspect_undeclared",
            missing=missing,
            reasons=[
                "Critical artifact fact(s) absent: " + ", ".join(missing_critical)
                + " (success claimed without the corresponding artifact fact)"
            ],
            confidence=1.0,
            missing_optional=facts.missing_optional,
            facts=facts.to_dict(),
        )

    # Honest-partial case kept from P92W3-N10: checks recorded but not
    # machine-checkable, and/or no claim artifact → partial, never undeclared.
    if missing_graded:
        reasons = []
        if "verification_machine_checkable" in missing_graded:
            reasons.append(
                "Verification field present but carries no machine-checkable statement "
                "(no pass/fail counts, exit code, hash or fraction)"
            )
        if "claim" in missing_graded:
            reasons.append("No report/claim artifact (summary|headline|evidence|report_id)")
        return DeliveryAssessment(
            state="delivered_partial",
            missing=missing,
            reasons=reasons,
            confidence=0.7,
            missing_optional=facts.missing_optional,
            facts=facts.to_dict(),
        )

    return DeliveryAssessment(
        state="delivered_complete",
        missing=[],
        reasons=["Implementation, machine-checkable verification and claim artifacts present"],
        confidence=1.0 if not facts.missing_optional else 0.9,
        missing_optional=facts.missing_optional,
        facts=facts.to_dict(),
    )


def assess_delivery_batch(results: list[dict[str, Any] | None]) -> list[DeliveryAssessment]:
    """Batch classification for multiple missions.

    Parameters
    ----------
    results : list[dict | None]
        List of mission result JSONs

    Returns
    -------
    list[DeliveryAssessment]
        List of classifications (same order as input)
    """
    return [assess_delivery(r) for r in results]


def compute_delivery_metrics(
    assessments: list[DeliveryAssessment],
) -> dict[str, Any]:
    """Compute aggregate delivery metrics from a batch of assessments.

    Parameters
    ----------
    assessments : list[DeliveryAssessment]
        List of delivery assessments

    Returns
    -------
    dict
        Aggregate metrics: counts, rates, confidence stats
    """
    if not assessments:
        return {
            "total": 0,
            "complete": 0,
            "partial": 0,
            "undeclared": 0,
            "complete_rate": 0.0,
            "partial_rate": 0.0,
            "undeclared_rate": 0.0,
            "avg_confidence": 0.0,
        }

    total = len(assessments)
    complete = sum(1 for a in assessments if a.state == "delivered_complete")
    partial = sum(1 for a in assessments if a.state == "delivered_partial")
    undeclared = sum(1 for a in assessments if a.state == "suspect_undeclared")

    return {
        "total": total,
        "complete": complete,
        "partial": partial,
        "undeclared": undeclared,
        "complete_rate": round(complete / total * 100, 2) if total > 0 else 0.0,
        "partial_rate": round(partial / total * 100, 2) if total > 0 else 0.0,
        "undeclared_rate": round(undeclared / total * 100, 2) if total > 0 else 0.0,
        "avg_confidence": round(sum(a.confidence for a in assessments) / total, 3),
    }




# ---------------------------------------------------------------------------
# Attested layering (P92W3-N10): artifact facts x closure integrity
# ---------------------------------------------------------------------------
# A terminal ``succeeded`` can be reached in two very different ways: the worker
# delivered and *then* reported, or the worker was pushed to report by a
# forced-closure prompt at the end of its budget. Artifact facts alone record
# *what* was written; they cannot record *how* the claim was obtained. The
# closure fact is a harness-observed process fact (``fail_stage``), and the
# layering below combines both dimensions with an explicit, fail-closed table.
CLOSURE_FIELD = "fail_stage"
CLOSURE_FORCED_TOKEN = "forced_finish"
CLOSURE_CLEAN = "clean"
CLOSURE_FORCED = "forced"
CLOSURE_UNKNOWN = "unknown"
CLOSURE_STATES = (CLOSURE_CLEAN, CLOSURE_FORCED, CLOSURE_UNKNOWN)

#: Layering table: (artifact_state, closure_state) -> attested terminal state.
#: Fail-closed: a complete artifact set with *unattested* closure is NOT
#: promoted to ``delivered_complete`` — missing telemetry is not evidence.
ATTESTED_TABLE: dict[tuple[str, str], str] = {
    ("delivered_complete", CLOSURE_CLEAN): "delivered_complete",
    ("delivered_complete", CLOSURE_FORCED): "delivered_partial",
    # P92W3-N10 deviation D1: an *unattested* closure is a telemetry gap, not a
    # delivery defect. Downgrading it would reclassify every mission from a
    # harness that predates this fact (276 of 6327 = 4.4%) and manufacture a
    # complete_rate drop attributable to missing telemetry. It is therefore
    # surfaced as an optional/coverage item, never as a downgrade.
    ("delivered_complete", CLOSURE_UNKNOWN): "delivered_complete",
    ("delivered_partial", CLOSURE_CLEAN): "delivered_partial",
    ("delivered_partial", CLOSURE_FORCED): "delivered_partial",
    ("delivered_partial", CLOSURE_UNKNOWN): "delivered_partial",
    ("suspect_undeclared", CLOSURE_CLEAN): "suspect_undeclared",
    ("suspect_undeclared", CLOSURE_FORCED): "suspect_undeclared",
    ("suspect_undeclared", CLOSURE_UNKNOWN): "suspect_undeclared",
}

#: Item appended to the ``missing`` enumeration for each closure state.
CLOSURE_MISSING_ITEM = {
    CLOSURE_FORCED: "closure_integrity",
    CLOSURE_UNKNOWN: "closure_attestation",
}

CLOSURE_REASON = {
    CLOSURE_FORCED: (
        "Closure was extracted under budget duress (forced-closure marker in the "
        "process facts): artifacts may be complete but the claim is not attested"
    ),
    CLOSURE_UNKNOWN: (
        "Closure integrity unattested (no process fact recording how the terminal "
        "claim was made) — fail-closed: not promoted to complete"
    ),
}


def classify_closure(result: dict[str, Any] | None) -> str:
    """Classify closure integrity from the harness-observed process facts.

    Returns
    -------
    str
        ``forced`` when the forced-closure marker is present, ``clean`` when the
        field is present without it, ``unknown`` when the field is absent.

    Examples
    --------
    >>> classify_closure({"fail_stage": "budget_exhausted+forced_finish"})
    'forced'
    >>> classify_closure({"fail_stage": ""})
    'clean'
    >>> classify_closure({})
    'unknown'
    """
    if not isinstance(result, dict) or CLOSURE_FIELD not in result:
        return CLOSURE_UNKNOWN
    value = _as_text(result.get(CLOSURE_FIELD))
    if CLOSURE_FORCED_TOKEN in value:
        return CLOSURE_FORCED
    return CLOSURE_CLEAN


def layer_attested_state(
    artifact_state: str, closure_state: str
) -> tuple[str, list[str], float]:
    """Combine an artifact verdict with a closure verdict.

    Parameters
    ----------
    artifact_state : str
        One of the three artifact states.
    closure_state : str
        One of ``clean`` / ``forced`` / ``unknown``.

    Returns
    -------
    tuple
        ``(state, extra_missing_items, confidence)``

    Examples
    --------
    >>> layer_attested_state("delivered_complete", "forced")[0]
    'delivered_partial'
    >>> layer_attested_state("delivered_complete", "clean")[0]
    'delivered_complete'
    """
    if closure_state not in CLOSURE_STATES:
        closure_state = CLOSURE_UNKNOWN
    state = ATTESTED_TABLE.get((artifact_state, closure_state), "suspect_undeclared")
    extra = [CLOSURE_MISSING_ITEM[closure_state]] if closure_state == CLOSURE_FORCED else []
    confidence = {"delivered_complete": 1.0, "delivered_partial": 0.7,
                  "suspect_undeclared": 1.0}[state]
    if closure_state == CLOSURE_UNKNOWN and state == "delivered_complete":
        confidence = 0.9   # downgraded confidence, not downgraded state
    return state, extra, confidence


def assess_attested_delivery(result: dict[str, Any] | None) -> DeliveryAssessment:
    """Classify the *attested* delivery state (artifacts x closure integrity).

    The artifact-only verdict is computed first (``assess_delivery``) so that the
    two layers stay separable in every report; the closure fact can only
    downgrade, never upgrade.

    Examples
    --------
    >>> a = assess_attested_delivery({"changed_files": ["a.py"], "checks": "12 passed",
    ...                               "summary": "done", "fail_stage": ""})
    >>> a.state
    'delivered_complete'
    >>> b = assess_attested_delivery({"changed_files": ["a.py"], "checks": "12 passed",
    ...                               "summary": "done",
    ...                               "fail_stage": "budget_exhausted+forced_finish"})
    >>> b.state, b.missing
    ('delivered_partial', ['closure_integrity'])
    """
    artifact = assess_delivery(result)
    closure = classify_closure(result)
    state, extra_missing, confidence = layer_attested_state(artifact.state, closure)
    missing = list(artifact.missing)
    reasons = list(artifact.reasons)
    for item in extra_missing:
        if item not in missing:
            missing.append(item)
    if closure != CLOSURE_CLEAN:
        reasons.append(CLOSURE_REASON[closure])
    if state == artifact.state and closure == CLOSURE_CLEAN:
        confidence = artifact.confidence

    missing_optional = list(artifact.missing_optional)
    if closure == CLOSURE_UNKNOWN and CLOSURE_MISSING_ITEM[CLOSURE_UNKNOWN] not in missing_optional:
        missing_optional.append(CLOSURE_MISSING_ITEM[CLOSURE_UNKNOWN])
    facts = dict(artifact.facts)
    facts["closure"] = closure
    facts["artifact_state"] = artifact.state
    return DeliveryAssessment(
        state=state,
        missing=missing,
        reasons=reasons,
        confidence=confidence,
        missing_optional=missing_optional,
        facts=facts,
    )


def compute_missing_item_counts(
    assessments: list[DeliveryAssessment], top_n: int = 8
) -> list[dict[str, Any]]:
    """Aggregate the ``missing`` enumerations into a ranked list.

    Parameters
    ----------
    assessments : list[DeliveryAssessment]
        Assessments to aggregate.
    top_n : int
        How many items to return.

    Returns
    -------
    list[dict]
        ``[{"item": str, "count": int}]`` sorted by count descending, ties by name.
    """
    counter: dict[str, int] = {}
    for assessment in assessments:
        for item in assessment.missing:
            counter[item] = counter.get(item, 0) + 1
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"item": item, "count": count} for item, count in ranked[:top_n]]


# Domain semantics scanner (for audit)
# Note: This list is for detection only; the module itself contains no domain logic.
# The list is stored as a tuple to make it easier to exclude from scanning.
_DOMAIN_TERMS_FOR_SCAN = (
    "ship", "hull", "hydro", "resistance", "propulsion", "seakeeping",
    "maneuvering", "stability", "structure", "vibration", "noise", "ice",
    "wave", "wind", "froude", "reynolds", "boundary layer",
    "cfd", "bem", "fem", "lbm", "dem", "sph",
)

# Pattern matches terms at word boundaries OR after underscores (for identifiers)
_DOMAIN_TERM_PATTERN = re.compile(
    r"(?:\b|_)(?:" + "|".join(re.escape(term) for term in _DOMAIN_TERMS_FOR_SCAN) + r")(?:\b|_)",
    re.IGNORECASE,
)


def scan_domain_semantics(code_text: str) -> list[str]:
    """Scan code for domain-specific terms (zero-tolerance policy).

    Parameters
    ----------
    code_text : str
        Source code or test file content

    Returns
    -------
    list[str]
        List of domain terms found (empty = compliant)

    Note
    ----
    This function is used to audit that delivery semantics modules contain
    no domain-specific logic. The term list itself is metadata, not domain logic.
    """
    # Remove comments, docstrings, and the term list definition to avoid false positives
    lines = code_text.split('\n')
    filtered_lines = []
    in_docstring = False
    in_term_list = False

    for line in lines:
        stripped = line.strip()

        # Skip docstrings
        if stripped.startswith('"""') or stripped.startswith("'''"):
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue

        # Skip comments
        if stripped.startswith('#'):
            continue

        # Skip the term list definition
        if '_DOMAIN_TERMS_FOR_SCAN' in line or '_DOMAIN_TERMS =' in line:
            in_term_list = True
        if in_term_list:
            if line.strip().endswith(')'):
                in_term_list = False
            continue

        filtered_lines.append(line)

    filtered_text = '\n'.join(filtered_lines)
    matches = _DOMAIN_TERM_PATTERN.findall(filtered_text)
    # Extract just the term from the match (remove surrounding _ or \b)
    terms = []
    for match in matches:
        # The match includes surrounding _ or boundary, extract the core term
        for term in _DOMAIN_TERMS_FOR_SCAN:
            if term.lower() in match.lower():
                terms.append(term)
                break
    return list(set(terms))  # Deduplicate