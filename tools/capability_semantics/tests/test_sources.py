"""声明源与配置装载的单测（三种来源形状 / 别名 / 降级 / 端点清单）。"""
from __future__ import annotations

import json
from pathlib import Path

from tools.capability_semantics.sources import (DEFAULT_THRESHOLDS, SemanticsConfig,
                                               file_sha256, inventory_endpoint_modules,
                                               load_capability_meta, load_declarations,
                                               load_ledger_evidence, load_semantics_config,
                                               parse_level, sha256_payload)
from tools.capability_semantics.tests.conftest import write_json


def test_parse_level_aliases_and_unknown():
    assert parse_level("real") == "REAL"
    assert parse_level("not-supported") == "NOT_SUPPORTED"
    assert parse_level("NOT_VERIFIED") == "WITHHELD"
    assert parse_level("bogus") is None
    assert parse_level(None) is None


def test_load_declarations_three_shapes(tmp_path: Path):
    ledger = write_json(tmp_path / "ledger.json", {"capabilities": [
        {"id": "synth.a", "claimed_level": "REAL"},
        {"id": "synth.b", "claimed_level": "not-a-level"},
        {"name": "synth.c", "evidence_level": "stub"},
    ]})
    decls, bad = load_declarations(ledger, "ledger")
    assert {d.capability_id: d.declared_level for d in decls} == {
        "synth.a": "REAL", "synth.c": "STUB"}
    assert bad == ["synth.b"]

    flat = write_json(tmp_path / "flat.json", [{"capability_id": "synth.d", "level": "MOCK"}])
    decls, bad = load_declarations(flat, "snapshot")
    assert [(d.capability_id, d.declared_level, d.source) for d in decls] == [
        ("synth.d", "MOCK", "snapshot")]
    assert bad == []

    cards = write_json(tmp_path / "cards.json", {"cards": [
        {"card_id": "synth.e", "verification_status": "NOT_VERIFIED"}]})
    decls, _bad = load_declarations(cards, "registry")
    assert decls[0].declared_level == "WITHHELD"


def test_load_declarations_missing_or_broken(tmp_path: Path):
    assert load_declarations(tmp_path / "nope.json", "x") == ([], [])
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_declarations(broken, "x") == ([], [])
    scalar = write_json(tmp_path / "scalar.json", 42)
    assert load_declarations(scalar, "x") == ([], [])


def test_load_ledger_evidence_and_meta(tmp_path: Path):
    p = write_json(tmp_path / "ledger.json", {"capabilities": [
        {"id": "synth.a", "domain": "d1", "evidence": [
            {"report_id": 11, "stance": "contradicts", "asserted_level": "mock",
             "as_of": "2026-01-01", "note": "n"},
            "not-a-dict",
        ]},
        {"id": "synth.b", "evidence": "not-a-list"},
    ]})
    ev = load_ledger_evidence(p)
    assert list(ev) == ["synth.a"]
    assert ev["synth.a"][0].asserted_level == "MOCK"
    assert ev["synth.a"][0].ref == "11"
    meta = load_capability_meta(p)
    assert meta["synth.a"]["domain"] == "d1"
    assert load_ledger_evidence(tmp_path / "missing.json") == {}
    assert load_capability_meta(tmp_path / "missing.json") == {}


def test_config_defaults_when_missing(tmp_path: Path):
    cfg = load_semantics_config(tmp_path / "missing.json")
    assert cfg.thresholds == DEFAULT_THRESHOLDS
    assert cfg.capabilities == []
    assert cfg.depends_on("synth.a") == []
    assert cfg.role("synth.a") == "domain"
    assert cfg.raw_sha256 == ""


def test_config_full_roundtrip(tmp_path: Path):
    p = write_json(tmp_path / "cfg.json", {
        "schema_version": "1.0",
        "level_rank": {"REAL": 4, "oops": "x"},
        "thresholds": {"withheld_ratio_max": 0.5},
        "delivery_discovery": {"endpoint_roots": ["backend/api/domain"]},
        "capabilities": [{"id": "synth.a", "depends_on": ["synth.b", 3],
                          "graph_role": "tooling"}],
    })
    cfg = load_semantics_config(p)
    assert cfg.schema_version == "1.0"
    assert cfg.level_rank == {"REAL": 4}
    assert cfg.threshold("withheld_ratio_max") == 0.5
    assert cfg.depends_on("synth.a") == ["synth.b", "3"]
    assert cfg.role("synth.a") == "tooling"
    assert cfg.raw_sha256 == file_sha256(p)
    assert cfg.discovery["endpoint_roots"] == ["backend/api/domain"]
    assert cfg.discovery["scan_subdirs"]  # 默认值被合并保留


def test_hashes_are_deterministic(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("hello", encoding="utf-8")
    assert file_sha256(p) == file_sha256(p) != ""
    assert file_sha256(tmp_path / "missing.txt") == ""
    assert sha256_payload({"b": 1, "a": 2}) == sha256_payload({"a": 2, "b": 1})


def test_endpoint_inventory(tmp_path: Path):
    mod = tmp_path / "backend/api/domain"
    mod.mkdir(parents=True)
    (mod / "synth_one.py").write_text(
        "@router.get('/a')\ndef a(): ...\n@router.post('/b')\ndef b(): ...\n",
        encoding="utf-8")
    (mod / "synth_two.py").write_text("@router.delete('/c')\ndef c(): ...\n",
                                      encoding="utf-8")
    (mod / "synth_none.py").write_text("x = 1\n", encoding="utf-8")
    (mod / "__init__.py").write_text("@router.get('/x')\n", encoding="utf-8")
    cfg = SemanticsConfig()
    cfg.discovery = {"endpoint_roots": ["backend/api/domain", "backend/api/missing"],
                     "endpoint_decorator_regex": r"@(?:router|app)\.(?:get|post|put|patch|delete)\(",
                     "unbound_module_allowlist": ["backend/api/domain/__init__.py"]}
    mods = inventory_endpoint_modules(tmp_path, cfg)
    assert [m.path for m in mods] == ["backend/api/domain/synth_one.py",
                                      "backend/api/domain/synth_two.py"]
    assert mods[0].endpoint_count == 2 and mods[0].verbs == ("GET", "POST")
    assert mods[0].sha256 and mods[0].to_dict()["endpoint_count"] == 2


def test_endpoint_inventory_bad_regex_falls_back(tmp_path: Path):
    mod = tmp_path / "backend/api/domain"
    mod.mkdir(parents=True)
    (mod / "synth.py").write_text("@app.get('/a')\n", encoding="utf-8")
    cfg = SemanticsConfig()
    cfg.discovery = {"endpoint_roots": ["backend/api/domain"],
                     "endpoint_decorator_regex": "([unclosed",
                     "unbound_module_allowlist": []}
    mods = inventory_endpoint_modules(tmp_path, cfg)
    assert len(mods) == 1 and mods[0].endpoint_count == 1


def test_config_json_not_object(tmp_path: Path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    cfg = load_semantics_config(p)
    assert cfg.capabilities == []
    assert cfg.thresholds == DEFAULT_THRESHOLDS