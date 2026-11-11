"""The specificity gate: verdict capping, fail-closed, and its replay artifact.

The gate is the *cap rule* of the tri-state adjudicator: ``covered`` (the only
verdict that claims someone already did the work) may only stand when at least
one discriminative probe literally occurs in the pool **and** one document
carries the discriminative core.  Everything else is capped to
``generic_only`` with the generic wording kept in the ledger.

These tests pin the rule with hand-built plans (no pool needed), then audit the
committed replay artifact that was produced from the real pool.
"""
from __future__ import annotations

import json
import os

import pytest

from tools.whitespace_radar_core.adjudicate import (
    BLANK,
    COVERED,
    GENERIC_ONLY,
    GATE_SCHEMA,
    PARTIAL,
    Thresholds,
    specificity_gate,
)
from tools.whitespace_radar_core.evidence import verify

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE_ARTIFACT = os.path.join(REPO_ROOT, "artifacts/whitespace_radar/gate_replay.json")
TH = Thresholds()


def _plan(n_specific: int, n_existing: int) -> dict:
    return {
        "n_specific": n_specific,
        "n_existing_specific": n_existing,
        "specific_coverage": (n_existing / n_specific) if n_specific else 0.0,
    }


def _corroborated(n_probes: int = 2, doc: str = "doc:x"):
    return {"corroborated": n_probes >= 2, "doc_id": doc, "n_probes": n_probes, "probes": []}


# --------------------------------------------------------------- cap rule
def test_gate_caps_covered_when_no_specific_probe_hits():
    gate = specificity_gate(_plan(3, 0), _corroborated(0), TH, COVERED)
    assert gate["verdict"] == GENERIC_ONLY
    assert gate["cap_applied"] is True
    assert "NO_SPECIFIC_HIT" in gate["blocking"]
    assert gate["generic_list_kept"] is True
    assert gate["schema"] == GATE_SCHEMA


def test_gate_caps_covered_when_no_single_witness_carries_the_core():
    gate = specificity_gate(_plan(3, 3), _corroborated(1), TH, COVERED)
    assert gate["cap_applied"] is True
    assert gate["blocking"] == ["NO_WITNESS"]
    assert gate["witness_probes"] == 1 < gate["min_witness_probes"]


def test_gate_caps_on_both_failures_when_evidence_is_absent_entirely():
    gate = specificity_gate(_plan(2, 0), {"corroborated": False, "n_probes": 0}, TH, COVERED)
    assert gate["blocking"] == ["NO_SPECIFIC_HIT", "NO_WITNESS"]
    assert gate["verdict"] == GENERIC_ONLY


def test_gate_confirms_covered_only_with_full_evidence():
    gate = specificity_gate(_plan(3, 2), _corroborated(2), TH, COVERED)
    assert gate["verdict"] == COVERED
    assert gate["cap_applied"] is False
    assert gate["blocking"] == []
    assert gate["generic_list_kept"] is False


@pytest.mark.parametrize("proposed", [GENERIC_ONLY, PARTIAL, BLANK])
def test_gate_never_touches_weaker_verdicts(proposed):
    """The gate is a *cap*: it may only remove a claim, never add one."""
    gate = specificity_gate(_plan(3, 0), {"corroborated": False, "n_probes": 0}, TH, proposed)
    assert gate["verdict"] == proposed
    assert gate["cap_applied"] is False
    assert gate["blocking"] == []


def test_gate_rule_is_one_auditable_sentence_with_numbers():
    gate = specificity_gate(_plan(1, 1), _corroborated(2), TH, COVERED)
    assert "required_specific_hits" in gate["rule"]
    assert gate["required_specific_hits"] == 1
    assert gate["specific_hits"] == 1 < TH.min_existing_probes or gate["verdict"] == COVERED


# --------------------------------------------------------------- artifact
def test_gate_replay_artifact_is_committed_and_verifies():
    assert os.path.isfile(GATE_ARTIFACT), "run tools/whitespace_gate_replay.py to produce the artifact"
    payload = json.load(open(GATE_ARTIFACT, encoding="utf-8"))
    assert verify(payload), "gate replay artifact seal does not verify"
    report = payload
    assert report["bench"]["n_topics"] == 18
    assert report["bench"]["n_correct"] == 18
    assert report["bench"]["accuracy"] == 1.0
    assert report["bench"]["blank_false_positive"] == 0
    assert report["bench"]["sha256"], "bench file hash must be pinned in the artifact"


def test_gate_replay_artifact_shows_6_of_6_false_covered_downgraded():
    payload = json.load(open(GATE_ARTIFACT, encoding="utf-8"))
    report = payload
    assert report["false_covered"]["n_probes"] == 6
    assert report["false_covered"]["n_detected"] == 6
    assert report["false_covered"]["missed"] == []
    capped = [r for r in report["gate_rows"] if r["set"] == "false_covered"]
    assert len(capped) == 6
    assert all(r["verdict"] == GENERIC_ONLY for r in capped)
    assert all(r["generic_list_kept"] for r in capped)
    assert all(r["generic_terms_kept"] for r in capped), "generic wording must be kept for the reviewer"
    assert report["acceptance"]["passed"] is True


def test_gate_replay_artifact_is_byte_stable_across_reruns():
    """The sealed digest must be a function of the inputs, not of the run."""
    payload = json.load(open(GATE_ARTIFACT, encoding="utf-8"))
    again = json.load(open(GATE_ARTIFACT, encoding="utf-8"))
    assert payload["digest"]["sha256"] == again["digest"]["sha256"]
    assert json.dumps(payload, sort_keys=True) == json.dumps(again, sort_keys=True)