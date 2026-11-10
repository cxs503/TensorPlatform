"""Criterion #2: every WITHHELD entry gets a structured acquisition proposal.

A proposal is only useful if it is *complete* (type, magnitude, priority,
verification spec) and *ordered* (the queue has a deterministic order). Both are
machine-checked here on the real library and on the synthetic snapshot.
"""
from __future__ import annotations

from tools.evidence_governance.proposal import summarise_proposals
from tools.evidence_governance.scanner import LibraryScanner

from .conftest import ROOT

BANDS = {"P0", "P1", "P2", "P3"}


def test_every_withheld_row_has_a_proposal(real_config, synth_items):
    from tools.evidence_governance.runner import build_pipeline

    scanner, _c, compiler, _e = build_pipeline(real_config, ROOT)
    items = scanner.scan().items
    proposals = compiler.compile(items)
    assert len(proposals) == len(items) == 19
    for p in proposals:
        body = p.as_dict()
        # the three fields the criterion names explicitly
        assert body["baseline_type"]
        assert 0.0 <= body["missing_magnitude"] <= 1.0
        assert body["priority_band"] in BANDS
        assert body["priority_score"] >= 0.0
        assert body["magnitude_components"]
        # plus a machine-readable verification spec
        assert p.task_description
        assert isinstance(p.required_evidence, dict)


def test_synthetic_proposals_cover_all_baseline_types(pipeline):
    _s, _c, compiler, _e = pipeline
    scanner = _s
    proposals = compiler.compile(scanner.scan().items)
    types = {p.baseline_type for p in proposals}
    # the synthetic library was authored to exercise five of the six types
    assert len(types) >= 5, types
    assert "unknown" in types


def test_proposals_are_deterministically_ordered(pipeline):
    _s, _c, compiler, _e = pipeline
    items = _s.scan().items
    a = compiler.compile(items)
    b = compiler.compile(list(reversed(items)))
    assert [p.digest() for p in a] == [p.digest() for p in b]
    assert summarise_proposals(a)["digest"] == summarise_proposals(b)["digest"]


def test_missing_magnitude_shrinks_with_evidence(pipeline):
    """A row with corroborated evidence must rank below a bare declaration."""
    _s, _c, compiler, _e = pipeline
    items = {i.capability_id: i for i in _s.scan().items}
    bare = compiler.missing_magnitude(items["synth.zeta"])          # no evidence
    corroborated = compiler.missing_magnitude(items["synth.beta"])  # two refs
    assert bare > corroborated


def test_priority_bands_follow_the_configured_thresholds(pipeline):
    _s, _c, compiler, _e = pipeline
    config = compiler.config
    bands = config["priority"]["bands"]
    for p in compiler.compile(_s.scan().items):
        if not p.requires_evidence:
            assert p.priority_band == config["priority"]["non_evidence_band"]
            continue
        expected = next(b["band"] for b in bands if p.priority_score >= b["min"])
        assert p.priority_band == expected


def test_non_evidence_rows_are_routed_out_of_the_acquisition_queue(real_config):
    """Infrastructure rows must not dominate the evidence queue."""
    from tools.evidence_governance.runner import build_pipeline

    scanner, _c, compiler, _e = build_pipeline(real_config, ROOT)
    proposals = compiler.compile(scanner.scan().items)
    top = [p for p in proposals if p.priority_band == "P0"]
    assert top, "the real library has evidence-bearing debts"
    assert all(p.requires_evidence for p in top)
    infra = [p for p in proposals if not p.requires_evidence]
    assert infra and all(p.missing_evidence_units == 0 for p in infra)


def test_missing_units_are_positive_for_open_debts(pipeline):
    _s, _c, compiler, _e = pipeline
    for p in compiler.compile(_s.scan().items):
        if p.requires_evidence:
            assert p.missing_evidence_units >= 1