"""验收判据 AC1–AC5 的机器可验测试（直接对已提交的语料快照复算）。

语料：``artifacts/campaign_optimizer/corpus_snapshot.json``（只读快照，含 content_hash）。
标注：``artifacts/campaign_optimizer/rootcause_labels.json``（人工标注 56 例）。

这些测试**不联网**：快照与标注都在仓里，任何人 clone 后可直接复算。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tools.campaign_optimizer import backtest as bt
from tools.campaign_optimizer import detect as dt
from tools.campaign_optimizer import model, rootcause as rc

REPO = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOT = REPO / "artifacts" / "campaign_optimizer" / "corpus_snapshot.json"
LABELS = REPO / "artifacts" / "campaign_optimizer" / "rootcause_labels.json"
REPORT = REPO / "artifacts" / "campaign_optimizer" / "report.json"
FLAGSHIP = 233

pytestmark = pytest.mark.skipif(
    not (SNAPSHOT.exists() and LABELS.exists()),
    reason="corpus snapshot / labels not present (run: python3 -m tools.campaign_optimizer.cli snapshot ...)",
)


@pytest.fixture(scope="module")
def cfg():
    return model.load_config()


@pytest.fixture(scope="module")
def loaded(cfg):
    c = model_corpus(cfg)
    graphs = model.graphs_from_nodes(c.nodes, cfg["status_vocab"]["satisfied"],
                                     cfg["status_vocab"]["blocking"])
    return c, graphs


def model_corpus(cfg):
    from tools.campaign_optimizer import corpus as corpus_mod

    return corpus_mod.load_snapshot(
        str(SNAPSHOT),
        float(cfg["schedule"]["default_duration_s"]),
        float(cfg["schedule"]["min_duration_s"]),
    )


@pytest.fixture(scope="module")
def plan233(loaded, cfg):
    c, graphs = loaded
    g = graphs[FLAGSHIP]
    dur = {n.node_id: c.effective_duration(n) for n in g.nodes.values()}
    return g, dur


# --------------------------------------------------------------------------- #
# AC1 瓶颈检测
# --------------------------------------------------------------------------- #
def test_ac1_flagship_has_ten_nodes_and_four_blocked(plan233, cfg):
    g, _ = plan233
    assert len(g.nodes) == 10
    blocked = sorted(nid for nid in g.nodes if g.is_blocking(nid))
    assert blocked == [1351, 1354, 1356, 1357]


def test_ac1_recall_is_100_percent(plan233, cfg):
    g, dur = plan233
    res = bt.detect_recall(g, dur, cfg)
    assert res["recall"] == pytest.approx(1.0), res
    assert res["precision"] == pytest.approx(1.0), res
    assert len(res["true_positives"]) == 4


def test_ac1_ranking_puts_downstream_gating_blockers_first(plan233, cfg):
    g, dur = plan233
    rep = dt.detect(g, dur, cfg, capacity=int(cfg["schedule"]["capacity"]))
    sev = {b.node_id: b.severity for b in rep.bottlenecks}
    # 1354（卡住 1355→1356）与 1356（卡住 1367）比孤立阻塞 1351/1357 更严重
    assert min(sev[1354], sev[1356]) > max(sev[1351], sev[1357])


# --------------------------------------------------------------------------- #
# AC2 根因分类
# --------------------------------------------------------------------------- #
def test_ac2_accuracy_against_manual_labels(loaded, cfg):
    c, graphs = loaded
    allc = []
    for pid, g in graphs.items():
        dur = {n.node_id: c.effective_duration(n) for n in g.nodes.values()}
        allc.extend(rc.classify_graph(g, dur, cfg))
    ev = bt.label_eval(bt.load_labels(str(LABELS)), allc, cfg)
    assert ev["n_cases"] >= 40, ev["n_cases"]
    assert ev["accuracy"] >= float(cfg["backtest"]["rootcause_accuracy_threshold"]), ev
    assert ev["heldout"]["accuracy"] >= float(cfg["backtest"]["rootcause_accuracy_threshold"])
    assert ev["passed"] is True


def test_ac2_no_case_carries_text_into_features(loaded, cfg):
    c, graphs = loaded
    g = graphs[FLAGSHIP]
    dur = {n.node_id: c.effective_duration(n) for n in g.nodes.values()}
    for cl in rc.classify_graph(g, dur, cfg):
        assert set(cl.features) == set(rc.ALLOWED_FEATURES)


# --------------------------------------------------------------------------- #
# AC3 吞吐回测
# --------------------------------------------------------------------------- #
def test_ac3_flagship_makespan_reduction_at_least_20_percent(loaded, cfg):
    c, graphs = loaded
    tb = bt.throughput_backtest(c, graphs, cfg)
    assert tb["plan233_gain"] is not None
    assert tb["plan233_gain"] >= float(cfg["backtest"]["throughput_gain_threshold"]), tb["plan233_gain"]
    assert tb["passed_on_plan233"] is True


def test_ac3_median_gain_across_plans(loaded, cfg):
    c, graphs = loaded
    tb = bt.throughput_backtest(c, graphs, cfg)
    assert tb["n_plans_selected"] >= 10
    assert tb["median_gain"] >= float(cfg["backtest"]["throughput_gain_threshold"])


def test_ac3_sensitivity_is_robust(loaded, cfg):
    c, graphs = loaded
    tb = bt.throughput_backtest(c, graphs, cfg)
    thr = float(cfg["backtest"]["throughput_gain_threshold"])
    assert tb["sensitivity"], "sensitivity grid empty"
    for row in tb["sensitivity"]:
        assert row["median_gain"] >= thr, row


def test_ac3_advice_carries_computable_savings(loaded, cfg):
    c, graphs = loaded
    from tools.campaign_optimizer import reallocate as realloc

    g = graphs[FLAGSHIP]
    dur = {n.node_id: c.effective_duration(n) for n in g.nodes.values()}
    out = realloc.advice_for_plan(g, dur, cfg)
    assert out["aggregate"]["n_advice"] >= 1
    for a in out["advice"]:
        assert a["expected_saving_s"] > 0
        assert a["improved_attempts"] < a["baseline_attempts"]
        assert a["action"] in cfg["advice"]["actions"]


# --------------------------------------------------------------------------- #
# AC5 负控
# --------------------------------------------------------------------------- #
def test_ac5_random_dag_false_positive_rate(cfg):
    nc = bt.negative_control(cfg)
    assert nc.fpr is not None
    assert nc.fpr <= float(cfg["backtest"]["fpr_threshold"]), nc.to_dict()
    assert nc.passed is True


def test_ac5_negative_control_has_discriminating_power(cfg):
    """纯结构启发式在同一批随机图上必须被负控打红（否则负控没鉴别力）。"""
    nc = bt.negative_control(cfg)
    assert nc.heuristic_fpr is not None
    assert nc.heuristic_fpr > float(cfg["backtest"]["fpr_threshold"])


def test_ac5_negative_control_is_deterministic(cfg):
    a = bt.negative_control(cfg).to_dict()
    b = bt.negative_control(cfg).to_dict()
    assert a == b


# --------------------------------------------------------------------------- #
# 报告一致性（审计：报告里的数字必须等于复算值）
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not REPORT.exists(), reason="report.json not generated")
def test_report_numbers_match_recomputation(loaded, cfg):
    c, graphs = loaded
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    recompute = bt.detect_recall(graphs[FLAGSHIP],
                                 {n.node_id: c.effective_duration(n) for n in graphs[FLAGSHIP].nodes.values()},
                                 cfg)
    assert report["plan233"]["detect"]["recall"] == recompute["recall"]
    assert report["plan233"]["detect"]["flagged"] == recompute["flagged"]
    assert report["corpus"]["content_hash"] == c.content_hash
    assert report["acceptance"] == {"AC1": True, "AC2": True, "AC3": True, "AC4": True, "AC5": True}


@pytest.mark.skipif(not REPORT.exists(), reason="report.json not generated")
def test_report_figures_are_hashed_and_present():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    import hashlib

    fig_dir = REPO / "artifacts" / "campaign_optimizer"
    assert report["figures_sha256"], "figures not hashed"
    for name, digest in report["figures_sha256"].items():
        path = fig_dir / name
        assert path.exists(), name
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        assert h == digest, name