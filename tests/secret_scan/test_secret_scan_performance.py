"""Performance regression guard: a gate that is slow gets bypassed.

This file exists because of a real incident during development: one rule
(``password_kv``) used an unbounded ``[A-Za-z0-9_]*`` prefix before a required
literal, which backtracks quadratically — 4.7 s for 0.9 MB while all other rules
together took 0.3 s. A CI gate that takes seconds per file is a gate humans turn
off, so the cost is pinned by a test rather than by good intentions.
"""

from __future__ import annotations

import os
import time

import pytest

from tools.secret_scan.detectors import compile_rules
from tools.secret_scan.engine import Scanner
from tools.secret_scan.policy import load_policy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POLICY = os.path.join(REPO_ROOT, "data", "secret_scan", "policy.json")


@pytest.fixture(scope="module")
def policy():
    return load_policy(POLICY)


def test_no_rule_backtracks_catastrophically(policy):
    """Pathological inputs: one long lowercase run, one long alnum run."""
    inputs = [
        "a" * 200_000,
        ("abcdefgh" * 25_000),
        ("x1" * 100_000),
        "--BEGIN " * 10_000,
    ]
    budget = 0.75  # seconds per rule over ~1 MB of adversarial input
    offenders = []
    for rule in compile_rules(policy.patterns):
        for text in inputs:
            started = time.perf_counter()
            sum(1 for _ in rule.regex.finditer(text))
            elapsed = time.perf_counter() - started
            if elapsed > budget:
                offenders.append((rule.id, round(elapsed, 3)))
    assert offenders == []


def test_full_scan_throughput(policy):
    scanner = Scanner(policy)
    text = ("lorem ipsum dolor sit amet " * 40_000) + "\n"  # ~1.1 MB
    started = time.perf_counter()
    scanner.scan_text(text, path="bulk.txt")
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, "full scan of ~1 MB took {:.2f}s".format(elapsed)


def test_decode_layer_is_bounded(policy):
    scanner = Scanner(policy)
    import base64

    blob = base64.b64encode(b"filler text without credentials " * 400).decode()
    text = (blob + "\n") * 40
    started = time.perf_counter()
    result = scanner.scan_text(text, path="blobs.txt")
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, "decode layer took {:.2f}s".format(elapsed)
    assert result.scanned_chars == len(text)