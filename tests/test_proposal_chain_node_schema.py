"""战役节点 metadata 契约验证器（合成数据，零领域语义）。"""

from __future__ import annotations

import pytest

from tools.proposal_chain import node_schema
from tools.proposal_chain.config import load_config

CFG_OVERRIDES = {
    "repo_whitelist": ["RepoA", "RepoB"],
    "node_schema": {
        "sample_size": 5,
        "known_key_typos": {"is_marity": "is_maturity"},
        "degrade_defaults": {"repo": "__unknown_repo__", "goal": "__missing_goal__",
                             "acceptance_criteria": [], "ext_id": None},
    },
}


@pytest.fixture(scope="module")
def cfg():
    return load_config(overrides=CFG_OVERRIDES)


def complete_node(node_id: int = 1, **meta) -> dict:
    base = {
        "ext_id": "x-1",
        "repo": "RepoA",
        "goal": "raise zeta solver accuracy",
        "acceptance_criteria": ["metric ≤ 3 %"],
    }
    base.update(meta)
    return {"id": node_id, "metadata": base}


def test_complete_node_is_accepted(cfg):
    rec = node_schema.validate_node(complete_node(), cfg.raw)
    assert rec.ok and rec.action == "accept" and rec.missing == ()


def test_missing_field_degrades_without_raising(cfg):
    rec = node_schema.validate_node({"id": 2, "metadata": {"repo": "RepoA", "goal": "g"}}, cfg.raw)
    assert rec.action == "degrade"
    assert set(rec.missing) == {"ext_id", "acceptance_criteria"}
    assert rec.filled["acceptance_criteria"] == []
    assert rec.filled["ext_id"] is None


def test_non_dict_metadata_degrades_all_fields(cfg):
    rec = node_schema.validate_node({"id": 3, "metadata": ["not", "a", "dict"]}, cfg.raw)
    assert rec.action == "degrade"
    assert len(rec.missing) == 4
    assert "metadata_not_object" in rec.warnings


def test_unparsable_json_metadata_is_warning_not_crash(cfg):
    rec = node_schema.validate_node({"id": 4, "metadata": "{broken json"}, cfg.raw)
    assert "metadata_unparsable" in rec.warnings
    assert rec.action == "degrade"


def test_string_and_numbers_accepted_as_present(cfg):
    rec = node_schema.validate_node(complete_node(ext_id=17), cfg.raw)
    assert rec.ok  # 数字型 ext_id 归一后非空


def test_empty_acceptance_criteria_counts_as_missing(cfg):
    rec = node_schema.validate_node(complete_node(acceptance_criteria=[]), cfg.raw)
    assert "acceptance_criteria" in rec.missing


def test_alias_resolution_uses_configured_aliases(cfg):
    meta = {"node_ext_id": "x-9", "repository": "RepoB", "objective": "do", "criteria": ["a ≥ 1"]}
    rec = node_schema.validate_node({"id": 5, "metadata": meta}, cfg.raw)
    assert rec.ok, rec.missing


def test_flat_metadata_on_node_payload_is_tolerated(cfg):
    rec = node_schema.validate_node(
        {"id": 6, "ext_id": "x-6", "repo": "RepoA", "goal": "g", "acceptance_criteria": "m < 2"}
    , cfg.raw)
    assert rec.ok


def test_batch_reports_zero_missed_by_oracle(cfg):
    nodes = [
        complete_node(1),
        {"id": 2, "metadata": {"repo": "RepoA"}},
        {"id": 3, "metadata": None},
        complete_node(4, goal=""),
    ]
    report = node_schema.validate_batch(nodes, cfg.raw)
    assert report.total == 4
    assert report.degraded_count == 3
    assert report.missed_count == 0
    assert report.miss_rate == 0.0


def test_batch_fill_rate_and_missing_counts(cfg):
    report = node_schema.validate_batch([complete_node(1), {"id": 2, "metadata": {}}], cfg.raw)
    assert report.missing_field_counts["repo"] == 1
    assert report.field_fill_rate["repo"] == pytest.approx(0.5)


def test_oracle_missing_is_independent_simple_check(cfg):
    meta = {"ext_id": "x", "repo": "", "goal": "g"}
    missing = node_schema.oracle_missing(meta, ["ext_id", "repo", "goal", "acceptance_criteria"])
    assert missing == {"repo", "acceptance_criteria"}


def test_typo_key_produces_warning(cfg):
    rec = node_schema.validate_node(complete_node(1, is_marity=True), cfg.raw)
    assert any(w.startswith("key_typo:is_marity") for w in rec.warnings)
    assert rec.ok  # 拼错键不影响必填判定


def test_quality_flags_flag_repo_and_criteria(cfg):
    rec = node_schema.validate_node(
        complete_node(1, repo="RepoZ", acceptance_criteria="make it better"), cfg.raw
    )
    flags = node_schema._quality_flags(rec.filled, cfg.raw)
    assert "repo_not_in_whitelist" in flags
    assert "criteria_without_numeric_threshold" in flags


def test_empty_batch_is_safe(cfg):
    report = node_schema.validate_batch([], cfg.raw)
    assert report.total == 0 and report.miss_rate == 0.0
    assert report.as_dict()["degraded_ratio"] == 0.0