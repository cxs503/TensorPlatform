"""Delivery state classifier — artifact-fact-based completion assessment.

Purpose
-------
Refines the terminal ``succeeded`` status into three delivery states based on
artifact facts (not self-reports):

- ``delivered_complete``: All required artifacts present and verifiable
- ``delivered_partial``: Some artifacts missing (with enumeration)
- ``suspect_undeclared``: Result missing critical fields (fail-closed)

Design rules
------------
* **Zero domain semantics.** No domain-specific terms in logic. All checks are
  generic artifact-presence checks (changed_files, checks, summary, figures).
* **Fail-closed.** Missing critical fields → ``suspect_undeclared``, never
  ``delivered_complete``.
* **Artifact-fact only.** No LLM judgment, no prose parsing. Pure structural
  checks on the result JSON.
* **Test fixtures use synthetic domain names.** Never reference real physics.

Audit chain
-----------
This module is consumed by:
- ``api/tensor_missions.py`` complete() endpoint (post-hoc classification)
- ``api/domain/mvd_checkpoint.py`` (70% budget checkpoint)
- ``api/kpi_delivery.py`` (layered KPI views)

Machine-verifiable acceptance criteria:
- F1 ≥ 0.90 on 60-unit manual annotation set
- Weak-label recall ≥ 0.85, precision ≥ 0.90
- Fail-closed negative controls (see tests)
- Zero domain semantics scan (domain term hits = 0)
"""
from __future__ import annotations

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
    "summary",        # Structured summary (proves reporting happened)
}

# Optional but expected fields (missing → partial, not undeclared)
EXPECTED_FIELDS_PARTIAL = {
    "figures",        # Visualization artifacts
    "report_id",      # Linked report
    "evidence",       # Evidence bundle
}

# Negative control: if these are missing, must be undeclared (fail-closed)
CRITICAL_FIELDS = {
    "changed_files",  # No files = no implementation
    "checks",         # No checks = no verification
}


def _is_field_missing(value: Any) -> bool:
    """Check if a field is missing (None or empty list).
    
    Empty dict {} is considered present (just empty content).
    Empty list [] is considered missing (no items).
    """
    if value is None:
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    return False


@dataclass
class DeliveryAssessment:
    """Result of delivery state classification.
    
    Attributes
    ----------
    state : DeliveryState
        One of delivered_complete / delivered_partial / suspect_undeclared
    missing : list[str]
        List of missing artifact fields (empty for complete)
    reasons : list[str]
        Human-readable justification for the classification
    confidence : float
        Confidence score [0, 1] based on artifact completeness
    """
    state: DeliveryState
    missing: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    confidence: float = 1.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "missing": self.missing,
            "reasons": self.reasons,
            "confidence": self.confidence,
        }


def assess_delivery(result: dict[str, Any] | None) -> DeliveryAssessment:
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
    >>> assess_delivery(None)
    DeliveryAssessment(state='suspect_undeclared', missing=['result'], ...)
    
    >>> assess_delivery({})
    DeliveryAssessment(state='suspect_undeclared', missing=['changed_files', 'checks'], ...)
    
    >>> assess_delivery({"changed_files": ["a.py"], "checks": {"pytest": "pass"}, "summary": {}})
    DeliveryAssessment(state='delivered_complete', missing=[], ...)
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
    
    # Check critical fields (fail-closed)
    missing_critical = []
    for field_name in CRITICAL_FIELDS:
        value = result.get(field_name)
        if _is_field_missing(value):
            missing_critical.append(field_name)
    
    if missing_critical:
        return DeliveryAssessment(
            state="suspect_undeclared",
            missing=missing_critical,
            reasons=[f"Critical field(s) missing or empty: {', '.join(missing_critical)}"],
            confidence=1.0,
        )
    
    # Check required fields for complete delivery
    missing_required = []
    for field_name in REQUIRED_FIELDS_COMPLETE:
        value = result.get(field_name)
        if _is_field_missing(value):
            missing_required.append(field_name)
    
    # Check expected fields for partial delivery
    missing_expected = []
    for field_name in EXPECTED_FIELDS_PARTIAL:
        value = result.get(field_name)
        if _is_field_missing(value):
            missing_expected.append(field_name)
    
    # Classification logic
    if not missing_required and not missing_expected:
        # All fields present
        return DeliveryAssessment(
            state="delivered_complete",
            missing=[],
            reasons=["All required and expected artifacts present"],
            confidence=1.0,
        )
    elif not missing_required and missing_expected:
        # Required present, some expected missing → partial
        return DeliveryAssessment(
            state="delivered_partial",
            missing=missing_expected,
            reasons=[f"Expected field(s) missing: {', '.join(missing_expected)}"],
            confidence=0.85,
        )
    else:
        # Some required missing → partial (not undeclared since critical present)
        return DeliveryAssessment(
            state="delivered_partial",
            missing=missing_required + missing_expected,
            reasons=[f"Required field(s) missing: {', '.join(missing_required)}"],
            confidence=0.70,
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
