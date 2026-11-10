"""Tests for the zero-domain-semantics scan adapter.

The denylist is data (``config/domain_word_denylist.json``); the fixtures below
use a word read from that file at test time, never a hard-coded domain term.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.governance_maturity.domain_scan import scan_paths

ROOT = Path(__file__).resolve().parents[3]
DENYLIST = ROOT / "config" / "domain_word_denylist.json"


def test_governance_suite_is_domain_clean():
    result = scan_paths(
        [
            "tools/governance_maturity/__init__.py",
            "tools/governance_maturity/artifact_registry.py",
            "tools/governance_maturity/adapters.py",
            "tools/governance_maturity/mutation_scan.py",
            "tools/governance_maturity/gate_power.py",
            "tools/governance_maturity/writeback_gate.py",
            "tools/governance_maturity/domain_scan.py",
            "tools/governance_maturity/tests",
            "data/governance_maturity",
        ],
        ROOT,
    )
    assert result["n_hits"] == 0, result["hits"][:5]
    assert result["verdict"] == "PASS"
    assert result["scanner_sha256"].startswith("sha256:")
    assert result["files_scanned"]


def test_detects_injected_word(tmp_path):
    word = json.loads(DENYLIST.read_text(encoding="utf-8"))["ascii_words"][0]
    fixture = tmp_path / "fixture.py"
    fixture.write_text(f'LABEL = "{word}"\n', encoding="utf-8")
    result = scan_paths(["fixture.py"], tmp_path)
    assert result["n_hits"] == 1
    assert result["hits"][0]["word"] == word
    assert result["verdict"] == "FAIL"


def test_clean_fixture_passes(tmp_path):
    fixture = tmp_path / "fixture.py"
    fixture.write_text("LABEL = 'synthetic_domain'\n", encoding="utf-8")
    result = scan_paths(["fixture.py"], tmp_path)
    assert result["n_hits"] == 0