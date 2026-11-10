"""Node acceptance engine — machine adjudication of campaign-node evidence.

Pipeline (each stage is a separate module so it can be tested alone):

``scanner``        node artifact directory        -> NodeEvidence
``discovery``      artifacts                       -> declared Criterion list
``criteria``       criterion text                  -> Comparator + threshold + hint
``aggregator``     criteria x evidence             -> NodeVerdict (PASS/FAIL/INCOMPLETE)
``report``         NodeVerdict                     -> six-element JSON + Markdown
``cross_validate`` machine verdict x human status  -> agreement + origin table

Design rules baked in:

* **Domain-neutral**: no domain vocabulary in code; naming vocabulary lives in
  ``lexicon.json`` and domain meaning arrives only through artifacts.
* **Honest by construction**: FAIL is sticky, UNKNOWN is never guessed, a node
  is only PASS when every declared criterion is PASS.
* **Auditable**: every observation keeps artifact path + dotted pointer, and
  every artifact is sha256-hashed.

Schema version: node-acceptance/1.0
"""
from __future__ import annotations

from typing import Iterable, Sequence

from .aggregator import aggregate, judge, judge_node, observe
from .criteria import detect_hint, load_lexicon, parse_criteria, parse_criteria_text, parse_criterion
from .cross_validate import HumanClaim, NodeCrossCheck, cross_validate, extract_human_claims, summarise
from .discovery import discover_all, discover_criteria, discover_declared_checks, discover_from_items
from .model import (
    ENGINE_VERSION,
    SCHEMA_VERSION,
    ArtifactRef,
    Comparator,
    Criterion,
    CriterionOutcome,
    MetricRecord,
    NodeEvidence,
    NodeVerdict,
    Observation,
    Verdict,
    node_verdict_from_outcomes,
)
from .report import SIX_ELEMENTS, build_acceptance_report, render_markdown
from .scanner import derive, flatten_metrics, scan_node_dir

__all__ = [
    "ENGINE_VERSION", "SCHEMA_VERSION", "SIX_ELEMENTS",
    "ArtifactRef", "Comparator", "Criterion", "CriterionOutcome", "HumanClaim",
    "MetricRecord", "NodeCrossCheck", "NodeEvidence", "NodeVerdict", "Observation", "Verdict",
    "aggregate", "build_acceptance_report", "cross_validate", "derive", "detect_hint",
    "discover_all", "discover_criteria", "discover_declared_checks", "discover_from_items", "evaluate_node", "extract_human_claims",
    "flatten_metrics", "judge", "judge_node", "load_lexicon", "node_verdict_from_outcomes",
    "observe", "parse_criteria", "parse_criteria_text", "parse_criterion", "render_markdown",
    "scan_node_dir", "summarise",
]


def evaluate_node(
    root: str,
    criteria_items: Iterable | None = None,
    node_id: str | None = None,
    lex: dict | None = None,
    provided_elements: Sequence[str] | None = None,
) -> tuple[NodeVerdict, dict, NodeCrossCheck]:
    """End-to-end: scan -> criteria -> verdict -> six-element report -> cross-check."""
    lex = lex or load_lexicon()
    evidence = scan_node_dir(root, node_id=node_id, lex=lex)
    if criteria_items is None:
        crits = discover_all(evidence, lex)
    else:
        crits = discover_from_items(criteria_items, source="explicit", lex=lex)
    verdict = aggregate(evidence, crits, lex, provided_elements=provided_elements)
    report = build_acceptance_report(verdict, lex)
    check = cross_validate(verdict, evidence)
    return verdict, report, check