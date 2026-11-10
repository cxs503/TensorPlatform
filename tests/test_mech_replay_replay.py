"""重放器测试：golden 全绿、篡改必红、幂等/置换对/差分三条渠道在场。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.mech_replay import mechanisms, replay, spec

ART = Path(__file__).resolve().parents[1] / "artifacts" / "mech_replay"
FIXTURES = ART / "fixtures"


def test_load_fixtures_sorted_and_complete():
    fixtures = replay.load_fixtures(FIXTURES)
    assert len(fixtures) >= 50
    ids = [f["fixture_id"] for f in fixtures]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_replay_is_fully_consistent():
    report = replay.replay(FIXTURES)
    assert report["consistency"] == 1.0
    assert report["fixtures_failed"] == 0
    assert report["idempotency_violations"] == []
    assert report["reference_disagreements"] == []
    assert report["permutation_violations"] == []
    assert report["all_green"] is True


def test_replay_reports_per_mechanism_coverage():
    report = replay.replay(FIXTURES)
    assert set(report["per_mechanism"]) == set(mechanisms.MECHANISMS)
    for name, bucket in report["per_mechanism"].items():
        assert bucket["fixtures"] >= 5, name
        assert bucket["failed"] == 0, name


def test_replay_single_mechanism_filter():
    report = replay.replay(FIXTURES, "merge_gate")
    assert set(report["per_mechanism"]) == {"merge_gate"}
    assert report["all_green"] is True


def test_replay_detects_tampered_verdict(tmp_path):
    target_dir = tmp_path / "fixtures"
    src = next((FIXTURES / "merge_gate").glob("probe-all-green.json"))
    payload = json.loads(src.read_text(encoding="utf-8"))
    payload["expected_verdict"]["decision"] = "fail"
    dest = target_dir / "merge_gate" / "probe-all-green.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    report = replay.replay(target_dir)
    assert report["all_green"] is False
    assert report["fixtures_failed"] == 1
    assert report["consistency"] == 0.0
    assert report["results"][0]["diffs"], "篡改未被定位"


def test_replay_detects_mechanism_drift_via_rule_mutation(monkeypatch):
    """机制退化（阈值放宽）必须让重放变红——这是套件的存在理由。"""
    from tools.mech_replay import mutate

    original = mechanisms.RULES["merge_gate"]
    monkeypatch.setitem(mechanisms.RULES, "merge_gate",
                        mutate.relax_rules(original, 1.2))
    report = replay.replay(FIXTURES, "merge_gate")
    assert report["all_green"] is False
    assert report["fixtures_failed"] >= 1


def test_replay_fixture_checks_idempotency_and_reference():
    fixtures = replay.load_fixtures(FIXTURES)
    for fixture in fixtures[:8]:
        result = replay.replay_fixture(fixture)
        assert result["match"] is True
        assert result["idempotent"] is True
        assert result["reference_agree"] is True
        assert result["verdict_sha256_ok"] is True


def test_permutation_pairs_exist_for_collection_mechanisms():
    fixtures = replay.load_fixtures(FIXTURES)
    paired = {f["mechanism"] for f in fixtures
              if str(f.get("probe_class") or "").startswith("order-pair")}
    assert {"capability_registration", "evidence_grade", "machine_gate", "report_gate",
            "autonomy_proposal", "campaign_status"} <= paired


def test_summary_line_is_informative():
    line = replay.summary_line(replay.replay(FIXTURES))
    assert "fixtures=" in line and "consistency=1.0000" in line


def test_cli_exit_codes(tmp_path):
    assert replay.main(["--fixtures", str(FIXTURES)]) == 0
    out = tmp_path / "replay.json"
    assert replay.main(["--fixtures", str(FIXTURES), "--json-out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["all_green"] is True
    assert len(payload["results"]) >= 50


def test_fixture_records_are_offline_self_contained():
    """fixture 必须自带输入记录（重放不连库）。"""
    for fixture in replay.load_fixtures(FIXTURES):
        assert isinstance(fixture["record"], dict) and fixture["record"]
        assert fixture["expected_verdict"]["mechanism"] == fixture["mechanism"]
        assert spec.digest(fixture["expected_verdict"]) == fixture["verdict_sha256"]