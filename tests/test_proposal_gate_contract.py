"""schema 契约门测试（v1/v2 双版本 + compat 层 + 口径判据，fail-closed）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proposal_gate_support import synth_config, v1_payload, v2_payload  # noqa: E402
from tools.proposal_gate.contract import (  # noqa: E402
    detect_version,
    has_quantified_threshold,
    validate_proposal,
)


@pytest.fixture(scope="module")
def cfg():
    return synth_config()


def test_v2_happy_path_yields_canonical_doc(cfg):
    res = validate_proposal(v2_payload(), cfg)
    assert res.ok, res.errors
    doc = res.doc
    assert doc.title == "foo 通道三道机器门"
    assert doc.repo == "foo"
    assert len(doc.criteria) == 3
    assert doc.is_maturity is True
    assert doc.schema_version == "2.0"
    assert doc.sha256() == res.doc.sha256()  # 摘要稳定
    assert doc.canonical()["goal"]["criteria"] == list(doc.criteria)


def test_sha256_is_content_stable_and_order_insensitive_for_deps(cfg):
    a = validate_proposal(v2_payload(deps=("门-契约", "门-去重")), cfg).doc
    b = validate_proposal(v2_payload(deps=("门-去重", "门-契约")), cfg).doc
    assert a.sha256() == b.sha256()


def test_title_empty_is_quarantined(cfg):
    res = validate_proposal(v2_payload(title="   "), cfg)
    assert not res.ok
    assert res.doc is None
    assert any(e.startswith("title_empty") or "title" in e for e in res.errors), res.errors


def test_repo_outside_whitelist_is_quarantined(cfg):
    res = validate_proposal(v2_payload(repo="not-a-repo"), cfg)
    assert not res.ok
    assert any(e.startswith("repo_not_whitelisted") for e in res.errors), res.errors


def test_criteria_below_minimum_is_quarantined(cfg):
    res = validate_proposal(v2_payload(criteria=("重复率 < 0.15", "覆盖率 ≥ 0.80")), cfg)
    assert not res.ok
    assert any(e.startswith("criteria_too_few") for e in res.errors), res.errors


def test_criteria_without_quantified_threshold_is_quarantined(cfg):
    res = validate_proposal(
        v2_payload(criteria=("重复率 < 0.15", "覆盖率 ≥ 0.80", "效果更好更稳定")), cfg
    )
    assert not res.ok
    assert any(e.startswith("criteria_not_quantified") for e in res.errors), res.errors


def test_number_without_direction_is_not_a_threshold(cfg):
    """「重复 3 次」有数字但没有方向/量纲 → 不可裁，必须拒。"""
    assert not has_quantified_threshold("每组合重复 3 次", cfg)
    assert has_quantified_threshold("误判率 ≤ 0.05", cfg)
    assert has_quantified_threshold("命中率 ≥ 85%", cfg)
    assert has_quantified_threshold("偏差 = 0", cfg)


def test_is_maturity_must_be_bool(cfg):
    res = validate_proposal(v2_payload(is_maturity="true"), cfg)
    assert not res.ok
    assert any(e.startswith("is_maturity_not_bool") for e in res.errors), res.errors
    res2 = validate_proposal(v2_payload(is_maturity=1), cfg)
    assert not res2.ok
    assert any(e.startswith("is_maturity_not_bool") for e in res2.errors), res2.errors


def test_v1_historical_payload_is_mapped_by_compat_layer(cfg):
    res = validate_proposal(v1_payload(), cfg)
    assert res.ok, res.errors
    doc = res.doc
    # compat 层字段映射（config: compat.v1_to_canonical）逐项生效
    assert doc.schema_version == "1.0"
    assert doc.title == "widget 链路覆盖率账本"
    assert doc.objective.startswith("widget 链路缺少面向播种的覆盖率账本")
    assert len(doc.criteria) == 3
    assert doc.dependencies_hint == ("ledger.json", "report.md")
    assert doc.repo == "bar"  # compat.v1_defaults.repo（夹具里指向合成仓）
    assert doc.is_maturity is True  # compat.v1_defaults.is_maturity
    assert doc.proposal_id == "20260101-widget-gate"
    # 规范模型可直接进入下游：sha 与 v2 同构
    assert len(doc.sha256()) == 64
    assert detect_version(v1_payload()) == "1.0"


def test_v1_criteria_without_numbers_are_rejected_after_mapping(cfg):
    """v1 也要过同一套口径判据（映射不改判据强度）。"""
    payload = v1_payload(criteria=("四态计数可由 SQL 复算", "重复检出率高", "误判少"))
    res = validate_proposal(payload, cfg)
    assert not res.ok
    assert sum(1 for e in res.errors if e.startswith("criteria_not_quantified")) == 3


def test_unknown_version_is_fail_closed(cfg):
    res = validate_proposal({"whatever": 1}, cfg)
    assert not res.ok
    assert res.errors and res.errors[0].startswith("schema_version_unresolved")


def test_wrong_structural_type_is_fail_closed(cfg):
    res = validate_proposal(v2_payload(goal=["not", "an", "object"]), cfg)
    assert not res.ok
    assert res.doc is None
    assert any("schema:" in e or "contract_exception" in e for e in res.errors), res.errors


def test_non_mapping_payload_does_not_raise(cfg):
    res = validate_proposal(["not", "a", "mapping"], cfg)  # type: ignore[arg-type]
    assert not res.ok
    assert res.doc is None


def test_real_repo_whitelist_config_is_loaded_from_file():
    """真配置文件必须能被装载（白名单/阈值都在文件里，不在代码里）。"""
    from tools.proposal_gate.config import GateConfig

    real = GateConfig.load()
    assert len(real.repo_whitelist) >= 1
    assert float(real.need("dedup.dup_threshold")) > float(real.need("dedup.review_threshold"))
    assert real.get("contract.min_criteria") >= 1