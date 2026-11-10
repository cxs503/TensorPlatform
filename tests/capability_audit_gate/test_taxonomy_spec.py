"""分类法 / spec 装载的单元测试（零领域语义：全部用合成能力名）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.capability_audit_gate import spec as spec_mod
from tools.capability_audit_gate import taxonomy


def test_rank_ordering_matches_declared_level_sequence():
    assert [taxonomy.rank(lv) for lv in taxonomy.LEVELS] == [0, 1, 2, 3, 4]
    assert taxonomy.rank("real") == taxonomy.rank("REAL") == 4


def test_rank_rejects_unknown_level():
    with pytest.raises(ValueError):
        taxonomy.rank("PROBABLY_FINE")


def test_classify_change_separates_downgrade_from_refutation():
    assert taxonomy.classify_change("REAL", "REAL") == "STABLE"
    assert taxonomy.classify_change("WITHHELD", "REAL") == "UPGRADE"
    assert taxonomy.classify_change("REAL", "WITHHELD") == "DOWNGRADE"
    assert taxonomy.classify_change("REAL", "MOCK") == "REFUTATION"
    assert taxonomy.classify_change("WITHHELD", "STUB") == "REFUTATION"
    assert taxonomy.classify_change("MOCK", "STUB") == "DOWNGRADE"


def test_majority_baseline_uses_plurality_and_reports_rate():
    level, rate = taxonomy.majority_baseline(["WITHHELD"] * 7 + ["REAL"] * 3)
    assert level == "WITHHELD"
    assert rate == pytest.approx(0.7)
    assert taxonomy.majority_baseline([]) == ("WITHHELD", 0.0)


def _minimal_spec(**over):
    cap = {"id": "cap.a", "bindings": {"code": ["impl.py"]}, "patterns": {},
           "benchmark": None, "expected_level": None}
    cap.update(over.pop("cap", {}))
    base = {"schema_version": "1.0", "capabilities": [cap], "thresholds": {}}
    base.update(over)
    return base


def test_spec_rejects_duplicate_capability_ids():
    with pytest.raises(spec_mod.SpecError, match="重复"):
        spec_mod.validate_spec(_minimal_spec(capabilities=[{"id": "x"}, {"id": "x"}]))


def test_spec_rejects_unknown_benchmark_kind():
    bad = {"id": "cap.a", "benchmark": {"kind": "psychic", "path": "a.json"}}
    with pytest.raises(spec_mod.SpecError, match="benchmark.kind"):
        spec_mod.validate_spec(_minimal_spec(cap=bad))


def test_spec_rejects_bad_binding_key():
    with pytest.raises(spec_mod.SpecError, match="bindings"):
        spec_mod.validate_spec(_minimal_spec(cap={"id": "cap.a",
                                                  "bindings": {"docs": ["a.md"]}}))


def test_spec_merges_default_thresholds_and_overrides():
    built = spec_mod.validate_spec(_minimal_spec(thresholds={"real_tolerance_pct": 1.5}))
    assert built["thresholds"]["real_tolerance_pct"] == 1.5
    assert built["thresholds"]["acceptable_tolerance_pct"] == 15.0
    assert built["thresholds"]["pattern_can_refute"] is False


def test_spec_missing_file_raises_spec_error(tmp_path):
    with pytest.raises(spec_mod.SpecError, match="not found"):
        spec_mod.load_spec(tmp_path / "nope.json")


def test_spec_from_registry_keeps_patterns_and_no_bindings():
    registry = REPO / "config" / "capabilities.json"
    if not registry.is_file():  # pragma: no cover - 仓内应恒在
        pytest.skip("registry missing")
    built = spec_mod.spec_from_registry(registry)
    entries = json.loads(registry.read_text(encoding="utf-8"))["capabilities"]
    assert len(built["capabilities"]) == len(entries)
    assert all(not c["bindings"] for c in built["capabilities"])
    assert all(c["benchmark"] is None for c in built["capabilities"])
    assert any(c["patterns"]["code"] for c in built["capabilities"])