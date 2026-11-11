"""台账交叉器测试：dead 验证面 + 台账 REAL ⇒ fail-closed 降级建议；口径与既有台账同构。"""
from __future__ import annotations

import json

from verification_vitality.ledger_cross import cross_ledger, effective_level, load_feature_ledger
from verification_vitality.patrol import patrol


def _ledger(entries):
    return {cid: {"declared_level": lvl, "effective_level": lvl, "contradictions": [],
                  "raw": {"id": cid, "claimed_level": lvl}}
            for cid, lvl in entries.items()}


def test_dead_surface_with_real_ledger_is_fail_closed(migrated_repo):
    repo, _ = migrated_repo
    report = patrol(repo, registry_path=repo / "config/capabilities.json", timeout=90)
    ledger = _ledger({"synthetic.base": "REAL"})
    result = cross_ledger(report, ledger)
    assert result["blocked"] is True
    finding = result["findings"][0]
    assert finding["verdict"] == "FAIL_CLOSED"
    assert finding["n_dead_units"] == 1
    sug = result["suggestions"][0]
    assert sug["capability_id"] == "synthetic.base"
    assert (sug["action"], sug["from"], sug["to"]) == ("DOWNGRADE", "REAL", "WITHHELD")
    assert "tests/test_case.py" in sug["evidence"]["dead_units"]
    assert result["summary"]["n_fail_closed"] == 1


def test_healthy_surface_keeps_real_verdict(healthy_repo):
    report = patrol(healthy_repo, registry_path=healthy_repo / "config/capabilities.json",
                    timeout=90)
    result = cross_ledger(report, _ledger({"synthetic.base": "REAL", "synthetic.derive": "REAL"}))
    assert result["blocked"] is False
    assert result["summary"]["n_ok"] == 2
    assert result["suggestions"] == []


def test_real_capability_without_any_surface_is_review_not_fail_closed(healthy_repo):
    report = patrol(healthy_repo, registry_path=healthy_repo / "config/capabilities.json",
                    timeout=90)
    result = cross_ledger(report, _ledger({"ghost.capability": "REAL"}))
    assert result["blocked"] is False
    assert result["findings"][0]["verdict"] == "REVIEW_NO_SURFACE"
    assert result["suggestions"][0]["action"] == "REVIEW_MAPPING"


def test_non_real_capability_is_downgrade_recommended_not_blocking(migrated_repo):
    repo, _ = migrated_repo
    report = patrol(repo, registry_path=repo / "config/capabilities.json", timeout=90)
    result = cross_ledger(report, _ledger({"synthetic.base": "WITHHELD"}))
    assert result["findings"][0]["verdict"] == "DOWNGRADE_RECOMMENDED"
    assert result["blocked"] is False


def test_contradicting_evidence_lowers_effective_level():
    entry = {"id": "synthetic.prop", "claimed_level": "REAL",
             "evidence": [{"report_id": 1128, "stance": "contradicts", "asserted_level": "MOCK",
                           "note": "判据恒等式偏差 237%"},
                          {"report_id": 1133, "stance": "contradicts", "asserted_level": "STUB"}]}
    eff = effective_level(entry)
    assert eff["declared_level"] == "REAL"
    assert eff["effective_level"] == "STUB", "反证必须把有效等级压到最保守的那条"
    assert len(eff["contradictions"]) == 2


def test_load_feature_ledger_reads_real_artifact_shape(tmp_path):
    payload = {"capabilities": [
        {"id": "synthetic.count", "claimed_level": "REAL", "evidence": []},
        {"id": "synthetic.prop", "claimed_level": "REAL",
         "evidence": [{"stance": "contradicts", "asserted_level": "MOCK"}]},
    ]}
    p = tmp_path / "ledger.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    ledger = load_feature_ledger(p)
    assert set(ledger) == {"synthetic.count", "synthetic.prop"}
    assert ledger["synthetic.count"]["effective_level"] == "REAL"
    assert ledger["synthetic.prop"]["effective_level"] == "MOCK"


def test_deltas_exposed_when_declared_on_dead_surface(migrated_repo):
    repo, _ = migrated_repo
    report = patrol(repo, registry_path=repo / "config/capabilities.json", timeout=90)
    deltas = [{"id": 7, "mission_id": 999, "category": "new_capability",
               "capability_id": "synthetic.base", "summary": "synthetic capability added"},
              {"id": 8, "mission_id": 1000, "category": "defect_fix",
               "summary": "无关修复"}]
    result = cross_ledger(report, _ledger({"synthetic.base": "REAL"}), deltas=deltas)
    assert result["summary"]["n_deltas_exposed"] == 1
    assert result["deltas_exposed"][0]["match"] == "explicit"
    assert result["blocked"] is True


def test_deltas_text_match_is_labeled(tmp_path):
    report = {"capabilities": [{"capability_id": "synthetic.base", "status": "dead"}],
              "surfaces": [{"unit": "tests/test_case.py", "status": "dead",
                            "capability_ids": ["synthetic.base"],
                            "dead_reasons": ["static:symbol_missing"], "evidence": {}}],
              "repo": {"git_commit": "deadbeef"}}
    deltas = [{"id": 9, "summary": "synthetic.base capability added"}]
    result = cross_ledger(report, _ledger({"synthetic.base": "REAL"}), deltas=deltas)
    assert result["deltas_exposed"][0]["match"] == "text"
    assert result["deltas_exposed"][0]["capability_ids"] == ["synthetic.base"]

def test_delta_artifacts_pointing_at_dead_surface_are_exposed():
    """增量流水声称的验证面工件，如今是 dead 面 —— 硬证据（不靠文本猜）。"""
    report = {"capabilities": [{"capability_id": "synthetic.base", "status": "dead"}],
              "surfaces": [{"unit": "tests/test_case.py", "status": "dead",
                            "capability_ids": ["synthetic.base"],
                            "dead_reasons": ["static:symbol_missing"], "evidence": {}}],
              "repo": {"git_commit": "deadbeef"}}
    deltas = [{"id": 11, "category": "new_capability",
               "artifacts": ["tests/test_case.py", "docs/x.md"], "summary": "某能力增量"}]
    result = cross_ledger(report, _ledger({"synthetic.base": "REAL"}), deltas=deltas)
    rows = [e for e in result["deltas_exposed"] if e["match"] == "artifacts"]
    assert rows and rows[0]["dead_units"] == ["tests/test_case.py"]
    assert rows[0]["capability_ids"] == ["synthetic.base"]
