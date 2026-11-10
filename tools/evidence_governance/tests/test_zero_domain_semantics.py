"""Discipline gate: the governance package carries zero subject vocabulary.

The check is the repository's own scanner (``tools/evidence_domain_scan.py``)
run over this package, plus a guard that the scanner scope actually includes this
directory — otherwise the test would pass by scanning nothing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from tools.evidence_governance.model import canonical_json, digest
from tools.evidence_governance.scanner import LibraryScanner

from .conftest import PKG, ROOT, load


def test_package_is_inside_the_domain_scan_scope():
    lexicon = load(ROOT / "tools/evidence_domain_lexicon.json")
    assert "tools/evidence_governance" in lexicon["targets"]


def test_domain_scan_reports_zero_hits_in_package():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import evidence_domain_scan as scan  # type: ignore
    finally:
        sys.path.pop(0)
    lexicon = scan.load_lexicon(ROOT / "tools/evidence_domain_lexicon.json")
    result = scan.scan_paths([str(PKG)], lexicon=lexicon, root=ROOT)
    assert result["scanned_files"] >= 10
    assert result["hits"] == [], result["hits"][:5]


def test_no_io_or_network_in_the_pure_layers():
    """Classification / proposal / manifest layers must stay offline & pure."""
    forbidden = re.compile(r"\b(requests|urllib|socket|httpx|subprocess)\b")
    for name in ("model.py", "classifier.py", "proposal.py", "artifacts.py"):
        text = (PKG / name).read_text(encoding="utf-8")
        assert not forbidden.search(text), name


def test_declaration_text_is_built_only_from_configured_fields(demo_config):
    scanner = LibraryScanner(demo_config, root=ROOT)
    item = {i.capability_id: i for i in scanner.scan().items}["synth.beta"]
    for value in item.text_fields.values():
        assert value in item.declaration_text


def test_canonical_json_and_digest_are_stable():
    payload = {"b": [1, 2, 3], "a": {"y": None, "x": "值"}}
    assert canonical_json(payload) == canonical_json(json.loads(canonical_json(payload)))
    assert digest(payload) == digest(json.loads(canonical_json(payload)))
    assert len(digest(payload)) == 64