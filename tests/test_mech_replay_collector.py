"""采集器测试：脱敏、确定性、sha256 清单、判词冻结。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.mech_replay import collector, mechanisms, spec

ART = Path(__file__).resolve().parents[1] / "artifacts" / "mech_replay"
SNAPSHOT = ART / "sources_snapshot.json"


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_snapshot_is_redacted_structural_projection(snapshot):
    """快照只允许出现结构化投影：不得含 summary/title/goal 等自由文本字段。"""
    banned = {"summary", "title", "goal", "description", "name", "tags", "figures"}
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in banned, f"快照泄露自由文本字段: {key}"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(snapshot)
    assert snapshot["schema"] == collector.SNAPSHOT_SCHEMA


def test_identifier_redaction_is_stable_and_irreversible():
    token_a = collector.id_token(12345)
    assert token_a == collector.id_token(12345)
    assert token_a.startswith("sha256:")
    assert "12345" not in token_a
    assert collector.id_token(12345) != collector.id_token(12346)


def test_derive_fixtures_labels_field_sources(snapshot):
    fixtures = collector.derive_fixtures(snapshot, per_mechanism=2)
    assert fixtures, "未派生任何 real fixture"
    for fixture in fixtures:
        assert fixture["origin"] == "real"
        assert fixture["field_sources"], "缺少字段来源标注"
        assert fixture["reference_agreement"] is True
        assert fixture["verdict_sha256"] == spec.digest(fixture["expected_verdict"])


def test_fixture_freeze_is_dual_implementation_consensus(snapshot):
    from tools.mech_replay import reference

    for fixture in collector.derive_fixtures(snapshot, per_mechanism=2):
        verdict = fixture["expected_verdict"]
        ref = reference.reference_verdict(fixture["mechanism"], fixture["record"])
        assert verdict["decision"] == ref["decision"]
        assert reference.spec_codes(verdict) == ref["codes"]


def test_collect_is_byte_deterministic(snapshot, tmp_path):
    first = collector.collect_fixtures(snapshot=snapshot, out_dir=tmp_path / "a",
                                       per_mechanism=2)
    second = collector.collect_fixtures(snapshot=snapshot, out_dir=tmp_path / "b",
                                        per_mechanism=2)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    for rel, sha in first["files"].items():
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes(), sha


def test_manifest_counts_match_files(snapshot, tmp_path):
    manifest = collector.collect_fixtures(snapshot=snapshot, out_dir=tmp_path,
                                          per_mechanism=2)
    assert manifest["fixture_count"] == len(manifest["files"])
    assert sum(manifest["per_mechanism"].values()) == manifest["fixture_count"]
    assert manifest["origin_counts"]["probe"] > 0
    assert manifest["origin_counts"]["real"] > 0


def test_verify_manifest_detects_tampering(snapshot, tmp_path):
    collector.collect_fixtures(snapshot=snapshot, out_dir=tmp_path, per_mechanism=2)
    assert collector.verify_manifest(tmp_path)["all_ok"] is True
    target = next((tmp_path / "fixtures").rglob("*.json"))
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["record"]["__tampered__"] = True
    target.write_text(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    report = collector.verify_manifest(tmp_path)
    assert report["all_ok"] is False
    assert report["sha_mismatch"], report


def test_checked_in_fixtures_are_intact():
    report = collector.verify_manifest(ART)
    assert report["all_ok"] is True, report
    assert report["files_checked"] >= 50


def test_every_mechanism_has_at_least_five_fixtures():
    manifest = json.loads((ART / "manifest.json").read_text(encoding="utf-8"))
    for name in mechanisms.MECHANISMS:
        assert manifest["per_mechanism"].get(name, 0) >= 5, name


def test_probe_set_is_deterministic():
    a = [f["fixture_id"] for f in collector.probe_fixtures()]
    b = [f["fixture_id"] for f in collector.probe_fixtures()]
    assert a == b
    assert len(a) >= 40


def test_probe_fixtures_never_claim_real_origin():
    for fixture in collector.probe_fixtures():
        assert fixture["origin"] == "probe"
        assert fixture["source"]["table"] == "synthetic-probe"