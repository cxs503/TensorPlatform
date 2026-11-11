"""Zero-domain-vocabulary gate for the governance package (J7)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.evidence_domain_scan import load_lexicon, scan_paths  # noqa: E402

TARGETS = [
    "tools/platform_governance_gates",
    "tests/platform_governance_gates",
    "config/platform_governance_gates.json",
]


def test_package_and_tests_carry_no_domain_vocabulary():
    result = scan_paths(TARGETS, lexicon=load_lexicon(), root=REPO)
    assert result["scanned_files"] >= 10
    assert result["hits"] == [], result["hits"][:5]


def test_scanner_positive_control(tmp_path: Path):
    lexicon = load_lexicon()
    term = lexicon["ascii_terms"][0]
    probe = tmp_path / "probe.py"
    probe.write_text(f'x = "{term}"\n', encoding="utf-8")
    result = scan_paths([str(probe)], lexicon=lexicon, root=tmp_path)
    assert len(result["hits"]) == 1
    assert result["hits"][0]["term"] == term