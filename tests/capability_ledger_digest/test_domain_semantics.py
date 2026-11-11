"""Criterion (d): the mechanism must carry zero domain semantics.

Positive control: the classifier / evidence-chain / test tree scans clean.
Negative control: the scanner really does fire on a seeded violation, so a clean
result cannot come from a scanner that silently does nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import evidence_domain_scan as scan  # noqa: E402

TARGETS = [
    "tools/capability_ledger_digest",
    "tests/capability_ledger_digest",
]


def test_mechanism_has_zero_domain_hits():
    result = scan.scan_paths(TARGETS, root=ROOT)
    assert result["hits"] == [], result["hits"]
    assert result["scanned_files"] > 5, "scan must actually cover the module and test files"


def test_scan_terms_come_from_the_shared_lexicon():
    lexicon = scan.load_lexicon()
    assert lexicon["ascii_terms"] and lexicon["cjk_terms"]


def test_negative_control_scanner_fires(tmp_path):
    """Seed one lexicon term outside the scanned tree: the scanner must flag it."""
    lexicon = scan.load_lexicon()
    term = lexicon["ascii_terms"][0]
    seeded = tmp_path / "seeded_violation.py"
    seeded.write_text(f"# adversarial fixture mentions {term} on purpose\n", encoding="utf-8")
    result = scan.scan_paths([seeded], root=tmp_path)
    assert result["hits"], "scanner failed to detect a seeded domain term"
    assert result["hits"][0]["term"] == term


def test_module_imports_no_domain_artifacts():
    import capability_ledger_digest as pkg

    names = [n for n in pkg.__all__]
    assert names, "package must export its public API"
    module_dir = Path(pkg.__file__).resolve().parent
    # the audit kernel reads no repository location at all: spec in, ledger out.
    # (materialize.py is the only file that knows a default output directory and
    # is excluded here; it is a caller-side convenience, not part of the kernel.)
    kernel = ("spec.py", "classifier.py", "evidence_chain.py", "sql_ledger.py", "audit.py")
    for name in kernel:
        text = (module_dir / name).read_text(encoding="utf-8", errors="replace")
        assert "artifacts/" not in text, name
        assert "domains/" not in text, name
        assert "ROOT" not in text, name