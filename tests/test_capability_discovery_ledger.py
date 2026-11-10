"""台账层（C3 / C4 回滚）：字段完整、幂等、可回滚、哈希链可校验。

C3：新增能力自动登记，字段完整；
回滚机制：误登记必须能撤回并记录原因，且自动登记不得悄悄把人工撤回翻回去。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.capability_discovery.grading import grade_feature
from tools.capability_discovery.model import CapabilityFeature
from tools.capability_discovery.store import LEDGER_SCHEMA, Ledger, LedgerError

REQUIRED_ENTRY_FIELDS = {
    "capability_id",
    "label",
    "level",
    "state",
    "first_seen",
    "updated_at",
    "revision",
    "grade",
    "evidence",
    "level_history",
    "retraction",
    "provenance",
    "entry_hash",
}
REQUIRED_EVIDENCE_FIELDS = {
    "declared_level",
    "anchors",
    "params",
    "tags",
    "tests",
    "sources",
    "marker_formats",
    "outcomes",
    "declared_levels_seen",
    "fingerprint",
}


def make_feature(cap_id="synth.ledger.case", outcomes=None, declared="REAL", anchors=("ref:a",)) -> CapabilityFeature:
    outcomes = {"passed": 1} if outcomes is None else outcomes
    n = sum(outcomes.values()) or 1
    return CapabilityFeature(
        capability_id=cap_id,
        label="synthetic ledger case",
        declared_level=declared,
        anchors=list(anchors),
        params={"n_samples": {"min": 8, "max": 64}},
        tags=["numeric"],
        tests=[
            {
                "nodeid": f"t.py::test_{i}",
                "source_file": "t.py",
                "lineno": i,
                "marker_formats": ["function_kwargs"],
                "scopes": ["function"],
                "modifiers": [],
                "outcome": next(iter(outcomes)),
            }
            for i in range(n)
        ],
        sources=[{"path": "t.py", "sha256": "1" * 64}],
        marker_formats=["function_kwargs"],
        outcomes=dict(outcomes),
        declared_levels_seen=[declared] if declared else [],
    )


class TestRegistration:
    def test_new_capability_registered_with_complete_fields(self):
        ledger = Ledger()
        feat = make_feature()
        action, info = ledger.apply_grade(feat, grade_feature(feat), run_id="run-1")
        assert action == "added"
        entry = ledger.get("synth.ledger.case")
        assert REQUIRED_ENTRY_FIELDS <= set(entry)
        assert REQUIRED_EVIDENCE_FIELDS <= set(entry["evidence"])
        assert entry["level"] == "REAL"
        assert entry["state"] == "active"
        assert entry["revision"] == 1
        assert entry["provenance"]["run_id"] == "run-1"
        assert entry["evidence"]["fingerprint"] == feat.evidence_fingerprint()
        assert ledger.verify_chain()[0]

    def test_second_identical_run_is_idempotent(self):
        ledger = Ledger()
        feat = make_feature()
        g = grade_feature(feat)
        ledger.apply_grade(feat, g, run_id="r1")
        before = ledger.dumps()
        action, _ = ledger.apply_grade(feat, g, run_id="r2")
        assert action == "unchanged"
        assert ledger.dumps() == before, "证据未变时不得刷新时间戳/追加链记录"
        assert len(ledger.chain) == 1

    def test_evidence_change_updates_revision_and_history(self):
        ledger = Ledger()
        feat = make_feature()
        ledger.apply_grade(feat, grade_feature(feat), run_id="r1")
        worse = make_feature(outcomes={"failed": 1})
        action, info = ledger.apply_grade(worse, grade_feature(worse), run_id="r2")
        assert action == "updated"
        entry = ledger.get("synth.ledger.case")
        assert entry["level"] == "MOCK"
        assert entry["revision"] == 2
        assert [h["to"] for h in entry["level_history"]] == ["REAL", "MOCK"]
        assert entry["level_history"][-1]["reason"]
        assert ledger.verify_chain()[0]

    def test_level_only_change_is_recorded(self):
        ledger = Ledger()
        feat = make_feature()
        ledger.apply_grade(feat, grade_feature(feat))
        lax = make_feature(anchors=())
        action, info = ledger.apply_grade(lax, grade_feature(lax))
        assert action == "updated"
        assert info["level_changed"] is True
        assert ledger.get("synth.ledger.case")["level"] == "WITHHELD"


class TestRollback:
    def test_retract_requires_reason(self):
        ledger = Ledger()
        feat = make_feature()
        ledger.apply_grade(feat, grade_feature(feat))
        with pytest.raises(LedgerError):
            ledger.retract("synth.ledger.case", "  ")
        with pytest.raises(LedgerError):
            ledger.retract("synth.absent", "typo")

    def test_retract_then_restore_leaves_trail(self):
        ledger = Ledger()
        feat = make_feature()
        ledger.apply_grade(feat, grade_feature(feat))
        entry = ledger.retract("synth.ledger.case", "marker added by mistake", actor="reviewer")
        assert entry["state"] == "retracted"
        assert entry["retraction"]["reason"] == "marker added by mistake"
        assert entry["retraction"]["by"] == "reviewer"
        assert [c["action"] for c in ledger.chain] == ["registered", "retracted"]
        with pytest.raises(LedgerError):
            ledger.retract("synth.ledger.case", "again")
        restored = ledger.restore("synth.ledger.case", "marker verified by hand", actor="reviewer")
        assert restored["state"] == "active"
        assert restored["retraction"] is None
        assert [c["action"] for c in ledger.chain] == ["registered", "retracted", "restored"]
        assert ledger.verify_chain()[0]

    def test_auto_registration_does_not_resurrect_retracted(self):
        ledger = Ledger()
        feat = make_feature()
        ledger.apply_grade(feat, grade_feature(feat))
        ledger.retract("synth.ledger.case", "false positive marker")
        action, info = ledger.apply_grade(feat, grade_feature(feat), run_id="r2")
        assert action == "held_retracted"
        assert ledger.get("synth.ledger.case")["state"] == "retracted"
        assert "false positive" in info["retraction_reason"]

    def test_missing_entries_reported_not_deleted(self):
        ledger = Ledger()
        feat = make_feature("synth.present")
        ledger.apply_grade(feat, grade_feature(feat))
        gone = make_feature("synth.vanished")
        ledger.apply_grade(gone, grade_feature(gone))
        missing = ledger.missing(["synth.present"])
        assert [m["capability_id"] for m in missing] == ["synth.vanished"]
        assert ledger.get("synth.vanished") is not None, "缺失只报告，不删除"
        assert missing[0]["note"]


class TestHashChain:
    def test_chain_detects_manual_entry_edit(self):
        ledger = Ledger()
        feat = make_feature()
        ledger.apply_grade(feat, grade_feature(feat))
        ledger.get("synth.ledger.case")["level"] = "REAL"
        ledger.get("synth.ledger.case")["evidence"]["anchors"] = ["forged:ref"]
        ok, msg = ledger.verify_chain()
        assert not ok and "篡改" in msg

    def test_chain_detects_ghost_entry(self):
        ledger = Ledger()
        ledger.entries["synth.ghost"] = {"capability_id": "synth.ghost", "state": "active", "level": "REAL"}
        ok, msg = ledger.verify_chain()
        assert not ok and "缺写操作链记录" in msg

    def test_chain_detects_chain_record_edit(self):
        ledger = Ledger()
        feat = make_feature()
        ledger.apply_grade(feat, grade_feature(feat))
        ledger.chain[0]["reason"] = "rewritten"
        ok, msg = ledger.verify_chain()
        assert not ok and "篡改" in msg


class TestPersistence:
    def test_roundtrip_and_schema(self, tmp_path):
        path = tmp_path / "ledger.json"
        ledger = Ledger(path=path)
        feat = make_feature()
        ledger.apply_grade(feat, grade_feature(feat))
        ledger.save()
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["schema"] == LEDGER_SCHEMA
        reloaded = Ledger.load(path)
        assert reloaded.dumps() == ledger.dumps()
        assert reloaded.verify_chain()[0]
        assert reloaded.active_ids() == ["synth.ledger.case"]

    def test_load_or_empty_creates_fresh(self, tmp_path):
        ledger = Ledger.load_or_empty(tmp_path / "nope.json")
        assert ledger.entries == {} and ledger.chain == []
        assert not (tmp_path / "nope.json").exists()