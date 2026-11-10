"""缺口账本解析 / 覆盖率账本 / 标注评估（合成数据，零领域语义）。"""

from __future__ import annotations

import pytest

from tools.proposal_chain import coverage, labels
from tools.proposal_chain.config import load_config
from tools.proposal_chain.dedup import Doc
from tools.proposal_chain.ledger import parse_gap_ledger

MARKDOWN = """# Ledger

## 一、背景

一些普通文本，不含条目结构。

## 四、映射表

| 节点 | 仓 | 能力 | 缺口 |
|---|---|---|---|
| N1 | RepoA | zeta widget solver | GAP-ZETA-P0 |
| N2 | RepoB | kilo pump curve | 无 |

## 五、未关闭缺口

1. 求解器硬缺口：zeta 求解器缺 GAP-ZETA-P0 的收敛证明
2. kilo 泵曲线缺实测锚点
"""


@pytest.fixture(scope="module")
def cfg():
    return load_config(
        overrides={
            "repo_whitelist": ["RepoA", "RepoB"],
            "tfidf": {"max_df_ratio": 1.0},
            "coverage": {
                "ledger_parse": {
                    "table_text_column": -1,
                    "text_max_chars": 200,
                    "section_status": [
                        {"heading_contains": "四、", "declared_status": "planned"},
                        {"heading_contains": "五、", "declared_status": "open"},
                    ],
                },
                "min_topic_df": 1,
                "max_topics": 10,
                "match_threshold": 0.3,
                "ledger_match_threshold": 0.3,
            },
        }
    )


def test_parse_ledger_extracts_table_and_list_items(cfg):
    items, meta = parse_gap_ledger(MARKDOWN, cfg.raw, source="synthetic")
    kinds = {i.kind for i in items}
    assert {"table_row", "list_item"} <= kinds
    assert meta["item_count"] == len(items)
    assert meta["sha256"]


def test_parse_ledger_assigns_declared_status_by_section(cfg):
    items, _ = parse_gap_ledger(MARKDOWN, cfg.raw)
    planned = [i for i in items if i.declared_status == "planned"]
    opened = [i for i in items if i.declared_status == "open"]
    assert planned and all("四、" in i.section for i in planned)
    assert opened and all("五、" in i.section for i in opened)


def test_parse_ledger_extracts_gap_ids(cfg):
    items, meta = parse_gap_ledger(MARKDOWN, cfg.raw)
    assert meta["gap_id_count"] >= 1
    assert any("GAP-ZETA-P0" in i.gap_ids for i in items)


def test_parse_ledger_on_empty_text_is_safe(cfg):
    items, meta = parse_gap_ledger("", cfg.raw)
    assert items == [] and meta["item_count"] == 0


def _pools() -> tuple[list[Doc], list[Doc]]:
    proposals = [
        Doc(id="p1", text="zeta widget solver convergence proof gap", campaign="w1",
            repo="RepoA", physics_domain="dom_x"),
        Doc(id="p2", text="kilo pump curve measured anchor", campaign="w1",
            repo="RepoB", physics_domain="dom_y"),
    ]
    nodes = [
        Doc(id="n1", text="zeta widget solver convergence proof gap study", campaign="1",
            repo="RepoA", physics_domain="dom_x"),
    ]
    return proposals, nodes


def test_topic_vocabulary_orders_by_weight(cfg):
    proposals, _ = _pools()
    topics = coverage.topic_vocabulary(proposals, cfg.raw)
    assert topics
    weights = [t.weight for t in topics]
    assert weights == sorted(weights, reverse=True)


def test_topic_vocabulary_empty_pool(cfg):
    assert coverage.topic_vocabulary([], cfg.raw) == []


def test_coverage_matrix_states(cfg):
    proposals, nodes_nodes = _pools()
    matrix = coverage.coverage_matrix(proposals, nodes_nodes, cfg.raw)
    by_domain = {row["domain"]: row for row in matrix["domain_matrix"]}
    assert by_domain["dom_x"]["state"] == "covered_by_node"
    assert by_domain["dom_y"]["state"] == "proposal_pending"
    assert matrix["totals"]["domains"] == 2


def test_coverage_matrix_empty_inputs(cfg):
    matrix = coverage.coverage_matrix([], [], cfg.raw)
    assert matrix["domain_matrix"] == [] and matrix["topic_matrix"] == []


def test_ledger_coverage_flags_stale_open_item(cfg):
    proposals, nodes_nodes = _pools()
    items, _ = parse_gap_ledger(MARKDOWN, cfg.raw)
    result = coverage.ledger_coverage(items, proposals, nodes_nodes, cfg.raw)
    open_rows = [r for r in result.rows if r.declared_status == "open"]
    assert open_rows
    for row in open_rows:
        assert row.state in ("covered_by_node", "proposal_pending", "uncovered")
    # 归类为“已被节点覆盖”的 open 条目必须被标成文档滞后候选，而不是静默通过
    stale = [r for r in open_rows if r.state == "covered_by_node"]
    assert all(r.note == "doc_stale_candidate" and not r.consistent for r in stale)


def test_ledger_coverage_stats_shape(cfg):
    proposals, nodes_nodes = _pools()
    items, _ = parse_gap_ledger(MARKDOWN, cfg.raw)
    stats = coverage.ledger_coverage(items, proposals, nodes_nodes, cfg.raw).stats
    assert stats["items"] == len(items)
    assert 0.0 <= stats["agreement_rate"] <= 1.0
    assert isinstance(stats["disagreement_items"], list)


def test_ledger_coverage_no_items_is_safe(cfg):
    proposals, nodes_nodes = _pools()
    result = coverage.ledger_coverage([], proposals, nodes_nodes, cfg.raw)
    assert result.rows == [] and result.stats["items"] == 0


def test_label_split_is_deterministic_and_disjoint(cfg):
    rows = [{"pair_id": f"P{i:04d}", "label": "overlap" if i % 2 else "distinct"} for i in range(20)]
    tune_a, hold_a = labels.split_labels(rows)
    tune_b, hold_b = labels.split_labels(rows)
    assert [r["pair_id"] for r in tune_a] == [r["pair_id"] for r in tune_b]
    assert set(r["pair_id"] for r in tune_a) & set(r["pair_id"] for r in hold_a) == set()


def test_calibrate_review_threshold_respects_fp_budget(cfg):
    sweep = [
        {"threshold": 0.60, "recall": 0.95, "fp_rate": 0.30},
        {"threshold": 0.65, "recall": 0.92, "fp_rate": 0.08},
        {"threshold": 0.85, "recall": 0.50, "fp_rate": 0.01},
    ]
    cal = labels.calibrate_review_threshold(sweep, target_recall=0.90, max_fp_rate=0.10)
    assert cal["threshold"] == 0.65
    assert cal["satisfies_target"] is True


def test_calibrate_review_threshold_no_feasible_point(cfg):
    sweep = [{"threshold": 0.9, "recall": 0.2, "fp_rate": 0.5}]
    cal = labels.calibrate_review_threshold(sweep, target_recall=0.90, max_fp_rate=0.10)
    assert cal["threshold"] is None
    assert cal["reason"] == "no_threshold_meets_fp_budget"


def test_evaluate_labels_counts_auto_and_review_tiers(cfg):
    docs = [
        Doc(id="a", text="zeta widget solver", campaign="c1"),
        Doc(id="b", text="zeta widget solver", campaign="c2"),
        Doc(id="d", text="kilo pump curve", campaign="c3"),
    ]
    space, index = labels.score_matrix(docs, cfg.raw, representation="claim", use_lsa=False)
    labeled = [
        {"pair_id": "P1", "a": "a", "b": "b", "label": "overlap"},
        {"pair_id": "P2", "a": "a", "b": "d", "label": "distinct"},
    ]
    ev = labels.evaluate_labels(
        docs, labeled, cfg.raw, representation="claim", use_lsa=False,
        dup_threshold=0.85, review_threshold=0.55, space=space,
    )
    assert ev.positives == 1 and ev.negatives == 1
    assert ev.tp == 1 and ev.fp == 0
    assert ev.catch_recall == 1.0 and ev.fp_rate == 0.0
    as_dict = ev.as_dict()
    assert set(("recall", "fp_rate", "catch_recall", "review_fp_rate")) <= set(as_dict)


def test_doc_text_representation_modes(cfg):
    doc = Doc(id="x", text="title line\nfirst sentence。second sentence。third sentence。", campaign="c")
    assert labels.doc_text_for(doc, "title", cfg.raw) == "title line"
    claim = labels.doc_text_for(doc, "claim", cfg.raw)
    assert "title line" in claim and "first sentence" in claim
    assert labels.doc_text_for(doc, "full", cfg.raw) == doc.text