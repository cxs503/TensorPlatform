"""Criterion #1a: the pipeline scans the *whole library*, not a sample."""
from __future__ import annotations

import json

from tools.evidence_governance.scanner import LibraryScanner

from .conftest import ROOT, load


def test_real_library_scan_selects_every_withheld_row(real_config):
    scanner = LibraryScanner(real_config, root=ROOT)
    result = scanner.scan()

    ledger = load(ROOT / "artifacts/capability_ledger/capability_ledger_resolved.json")
    expected = sorted(
        c["id"] for c in ledger["capabilities"] if c["reconciled_level"] == "WITHHELD"
    )
    got = sorted(i.capability_id for i in result.items)
    assert got == expected
    assert len(result.items) == 19
    assert all(i.reconciled_level == "WITHHELD" for i in result.items)


def test_real_scan_enriches_declarations_from_aux_ledger(real_config):
    """Notes from the raw ledger must end up in the declaration text."""
    scanner = LibraryScanner(real_config, root=ROOT)
    items = {i.capability_id: i for i in scanner.scan().items}
    item = items["calc.resistance"]
    assert set(item.text_fields) >= {"basis", "divergence", "note", "action"}
    assert len(item.declaration_text) > 40


def test_synthetic_scan_is_deterministic_and_sorted(demo_config):
    a = LibraryScanner(demo_config, root=ROOT).scan()
    b = LibraryScanner(demo_config, root=ROOT).scan()
    assert a.digest() == b.digest()
    assert [i.capability_id for i in a.items] == sorted(i.capability_id for i in a.items)
    assert len(a.items) == 8
    assert a.source_stats["sources"]["synth_library"]["rows_seen"] == 10


def test_scan_reports_source_statistics(real_config):
    result = LibraryScanner(real_config, root=ROOT).scan()
    stats = result.source_stats["sources"]["resolved_ledger"]
    assert stats["rows_seen"] == 21
    assert stats["rows_selected"] == 19
    assert json.dumps(result.source_stats, sort_keys=True)  # JSON-able


def test_missing_source_is_an_explicit_error(real_config):
    broken = dict(real_config)
    broken["library_sources"] = [dict(real_config["library_sources"][0], path="nope.json")]
    try:
        LibraryScanner(broken, root=ROOT).scan()
    except FileNotFoundError as exc:
        assert "nope.json" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing library source should raise")