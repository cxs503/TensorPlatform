"""去重门 / 孪生门 / 幂等门的行为测试（脱库：临时 sqlite + 临时隔离区）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proposal_gate_support import (  # noqa: E402
    PARAPHRASE_VARIANTS,
    distinct_payload,
    synth_config,
    v1_payload,
    v2_payload,
)
from tools.proposal_gate.contract import validate_proposal  # noqa: E402
from tools.proposal_gate.gate import ProposalGate  # noqa: E402
from tools.proposal_gate.index import ProposalIndex, dependency_overlap  # noqa: E402
from tools.proposal_gate.ledger import LedgerStore  # noqa: E402


@pytest.fixture()
def cfg():
    return synth_config()


@pytest.fixture()
def gate(tmp_path, cfg):
    store = LedgerStore.for_sqlite(str(tmp_path / "ledger.sqlite"), cfg)
    g = ProposalGate(
        cfg,
        store,
        quarantine_dir=tmp_path / "quarantine",
        events_file=tmp_path / "events.jsonl",
    )
    yield g
    store.dispose()


def test_first_submission_is_accepted_and_indexed(gate):
    res = gate.evaluate(v2_payload(), mission_id=7001)
    assert res.verdict == "accepted" and res.state == "proposal"
    assert res.created is True
    assert len(gate.index) == 1  # 入索引，供后续对拍
    assert gate.ledger.counts()["proposal"] == 1


def test_exact_resubmission_is_duplicate_without_new_row(gate):
    first = gate.evaluate(v2_payload(), mission_id=7001)
    again = gate.evaluate(v2_payload(), mission_id=7001)
    assert again.verdict == "duplicate"
    assert again.dup_of == first.sha256
    assert again.created is False
    assert "idempotent_resubmission" in again.reasons
    rows = gate.ledger.rows(limit=100)
    assert len(rows) == 1, rows  # 反作弊 (a)：不新增行


def test_light_rewording_is_auto_rejected_as_duplicate(gate):
    first = gate.evaluate(v2_payload(), mission_id=7001)
    # 单处词替换量级的改写（实测 0.92）：必须落自动拒
    payload = v2_payload(objective="为 foo 通道构建产物契约、去重器与覆盖率账本三道机器门")
    res = gate.evaluate(payload, mission_id=7002)
    assert res.verdict == "duplicate", res.as_dict()
    assert res.dup_of == first.sha256
    assert res.score >= float(gate.cfg.need("dedup.dup_threshold"))
    assert gate.ledger.counts()["dedup_rejected"] == 1


def test_heavy_paraphrase_is_held_for_review(gate):
    gate.evaluate(v2_payload(), mission_id=7001)
    variant = PARAPHRASE_VARIANTS[2]
    res = gate.evaluate(v2_payload(title=variant, objective=variant), mission_id=7003)
    assert res.verdict == "near_duplicate", res.as_dict()
    assert res.state == "quarantined"
    assert res.dup_of is not None
    review = float(gate.cfg.need("dedup.review_threshold"))
    assert res.score >= review
    assert res.quarantine_path and Path(res.quarantine_path).exists()
    # 隔离区文件里带着分值证据，事后可复核
    body = json.loads(Path(res.quarantine_path).read_text(encoding="utf-8"))
    assert body["payload"]["match"]["score"] == pytest.approx(res.score)


def test_distinct_topics_are_all_accepted(gate):
    """precision 门：三条主题不同的合法提案不得被误判。"""
    verdicts = []
    for i in range(3):
        res = gate.evaluate(distinct_payload(i), mission_id=7100 + i)
        verdicts.append((res.verdict, res.score))
    assert [v for v, _ in verdicts] == ["accepted"] * 3, verdicts
    assert max(s for _, s in verdicts) < float(gate.cfg.need("dedup.review_threshold"))


def test_same_repo_dependency_overlap_is_twin(gate):
    gate.evaluate(
        v2_payload(deps=("门-契约", "门-去重", "门-账本", "门-回放", "门-统计")), mission_id=7201
    )
    # 文本完全不同 + 依赖集合重合 2/3 → 结构化孪生
    payload = v2_payload(
        title="bar 侧的账本对账脚本",
        repo="foo",
        objective="为 foo 侧补一个每日对账脚本，核对账本条目",
        criteria=("对账失败率 ≤ 0.01", "覆盖条目 ≥ 5000", "耗时 ≤ 30 秒"),
        deps=("门-契约", "门-去重", "门-账本", "门-回放"),
    )
    res = gate.evaluate(payload, mission_id=7202)
    assert res.verdict == "twin", res.as_dict()
    assert res.state == "dedup_rejected"
    assert res.score >= float(gate.cfg.need("dedup.twin_dependency_overlap"))


def test_twin_needs_same_repo(gate):
    gate.evaluate(
        v2_payload(deps=("门-契约", "门-去重", "门-账本", "门-回放", "门-统计")), mission_id=7301
    )
    payload = v2_payload(
        title="widget 账本导出",
        repo="widget",
        objective="为 widget 侧导出账本给外部审计",
        criteria=("导出成功率 ≥ 0.99", "字段完整率 = 1.00", "耗时 ≤ 10 秒"),
        deps=("门-契约", "门-去重", "门-账本", "门-回放"),
    )
    res = gate.evaluate(payload, mission_id=7302)
    assert res.verdict == "accepted", res.as_dict()


def test_twin_needs_dependency_overlap(gate):
    gate.evaluate(
        v2_payload(deps=("门-契约", "门-去重", "门-账本", "门-回放", "门-统计")), mission_id=7401
    )
    payload = v2_payload(
        title="bar 报表分片",
        repo="foo",
        objective="把 bar 报表按时间分片重建",
        criteria=("分片偏差 ≤ 0.02", "重建耗时 ≤ 60 秒", "丢失率 ≤ 0.001"),
        deps=("另一条链-a", "另一条链-b"),
    )
    res = gate.evaluate(payload, mission_id=7402)
    assert res.verdict == "accepted", res.as_dict()


def test_dependency_overlap_is_jaccard(cfg):
    assert dependency_overlap(frozenset({"a", "b"}), frozenset({"a", "b"})) == pytest.approx(1.0)
    assert dependency_overlap(frozenset({"a", "b"}), frozenset({"b", "c"})) == pytest.approx(1 / 3)
    assert dependency_overlap(frozenset(), frozenset({"a"})) == 0.0


def test_campaign_node_index_blocks_cross_plan_duplicate(gate):
    """跨战役口径：与真库节点名撞车（同一条目被重复播种）必须被判重。"""
    gate.index.add_node("node:9001", v2_payload()["title"] + "\n" + v2_payload()["goal"]["objective"])
    gate.index.add_node("node:9002", "widget 报表延迟归因\n把 widget 报表的端到端延迟拆到各阶段")
    res = gate.evaluate(v2_payload(), mission_id=7501)
    assert res.verdict in ("duplicate", "near_duplicate"), res.as_dict()
    assert res.dup_of == "node:9001"
    assert res.score >= float(gate.cfg.need("dedup.review_threshold"))


def test_v1_historical_proposal_flows_through_same_gate(gate):
    """v1 历史口径提案经 compat 层后可被同一套去重门裁决。"""
    first = gate.evaluate(v1_payload(), mission_id=7601)
    assert first.verdict == "accepted", first.as_dict()
    dup = gate.evaluate(v1_payload(), mission_id=7602)
    assert dup.verdict == "duplicate" and dup.dup_of == first.sha256
    assert "cross_mission_resubmission" in dup.reasons
    assert gate.ledger.counts()["dedup_rejected"] == 1


def test_quarantine_blocks_oracle_entry(gate, tmp_path):
    bad = v2_payload(criteria=("效果更好", "更稳定", "更省事"))
    res = gate.evaluate(bad, mission_id=7701)
    assert res.verdict == "quarantined" and res.state == "quarantined"
    assert res.quarantine_path and Path(res.quarantine_path).exists()
    assert len(gate.index) == 0  # 不进仲裁/不进提案池
    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert any('"proposal_quarantined"' in line for line in events)


def test_index_kinds_and_len(gate):
    gate.index.add_node("node:1", "foo 节点甲")
    gate.evaluate(v2_payload(), mission_id=7801)
    kinds = gate.index.kinds()
    assert kinds.get("campaign_node") == 1
    assert kinds.get("proposal") == 1


def test_index_excludes_self_by_sha(gate):
    doc = validate_proposal(v2_payload(), gate.cfg).doc
    idx = ProposalIndex(gate.cfg)
    idx.add_doc(doc, key="self")
    assert idx.best_text_match(doc) is None  # 自我比较不算重复证据