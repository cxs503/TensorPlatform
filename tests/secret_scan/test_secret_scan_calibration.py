"""Calibration tests: the scanner must be able to fail, and must generalise.

Two seeds are used. ``DEFAULT_SEED`` is the calibration seed; a second seed is a
held-out validation set, which is what keeps the thresholds from being fitted to
one corpus.
"""

from __future__ import annotations

import os

import pytest

from tools.secret_scan.calibrate import build_corpus
from tools.secret_scan.engine import Scanner
from tools.secret_scan.policy import load_policy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POLICY = os.path.join(REPO_ROOT, "data", "secret_scan", "policy.json")
HELD_OUT_SEED = "tp-secret-scan-held-out-2026-11"


@pytest.fixture(scope="module")
def scanner():
    return Scanner(load_policy(POLICY))


@pytest.mark.parametrize("seed", ["tp-secret-scan-calibration-v1", HELD_OUT_SEED])
def test_detection_rate_meets_criterion(scanner, seed):
    corpus = build_corpus(seed)
    detected = sum(1 for case in corpus.secrets if scanner.scan_text(case.text, path="p.txt").hits)
    assert len(corpus.secrets) == 20
    assert detected >= 19, "seed={} detected={}".format(seed, detected)


@pytest.mark.parametrize("seed", ["tp-secret-scan-calibration-v1", HELD_OUT_SEED])
def test_false_positive_rate_meets_criterion(scanner, seed):
    corpus = build_corpus(seed)
    flagged = [case.label for case in corpus.benign if scanner.scan_text(case.text, path="p.txt").hits]
    assert len(corpus.benign) == 20
    assert len(flagged) <= 1, flagged


def test_bare_high_entropy_keys_are_present_and_detected(scanner):
    corpus = build_corpus()
    bare = [case for case in corpus.secrets if case.label.startswith("bare_key")]
    assert len(bare) >= 4
    for case in bare:
        hits = scanner.scan_text(case.text, path="p.txt").hits
        assert hits, case.label
        assert any(hit.detector == "entropy" for hit in hits), case.label


def test_transport_cases_are_seen_through_encoding(scanner):
    corpus = build_corpus()
    assert len(corpus.transport) >= 2
    for case in corpus.transport:
        hits = scanner.scan_text(case.text, path="payload.json").hits
        assert hits, case.label
        assert any(hit.detector == "decoded" for hit in hits), case.label


def test_corpus_values_are_not_stored_in_the_repository():
    """The test fixture must not itself be the leak: nothing is hard-coded."""
    corpus = build_corpus()
    roots = [os.path.join(REPO_ROOT, "tools", "secret_scan"), os.path.join(REPO_ROOT, "data", "secret_scan")]
    blobs = {}
    for root in roots:
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    blobs[path] = handle.read()
    offenders = []
    for case in corpus.secrets + corpus.transport:
        for path, text in blobs.items():
            if case.text in text:
                offenders.append((case.label, os.path.basename(path)))
    assert offenders == []


def test_case_labels_are_unique():
    corpus = build_corpus()
    labels = [case.label for case in corpus.secrets + corpus.benign + corpus.transport]
    assert len(labels) == len(set(labels))


def test_corpus_is_reproducible_from_seed():
    first = build_corpus("seed-a")
    second = build_corpus("seed-a")
    third = build_corpus("seed-b")
    assert [c.text for c in first.secrets] == [c.text for c in second.secrets]
    assert [c.text for c in first.secrets] != [c.text for c in third.secrets]