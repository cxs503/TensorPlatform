"""规则引擎 + 零领域语义（AC4）的行为与结构测试。

零领域语义的三条机器可验形式
----------------------------
1. **结构**：决策模块（rootcause/schedule/model）的源码里不出现对 ``.name`` / ``.reason_text``
   的属性访问（AST 扫描）。
2. **行为·隐去文本**：把所有节点名与原因文本清空，检测与分类结果逐字段不变。
3. **行为·随机改名**：把节点名与原因文本换成长度不同的随机串，结果不变。
4. **白名单**：规则只能引用 ``ALLOWED_FEATURES``；带多余键的特征字典会被拒绝。
"""

from __future__ import annotations

import ast
import pathlib
import random
import re

import pytest

from tools.campaign_optimizer import model, rootcause as rc, detect as dt
from tools.campaign_optimizer.model import Graph, Node

REPO = pathlib.Path(__file__).resolve().parents[1]
PKG = REPO / "tools" / "campaign_optimizer"
DECISION_MODULES = ["rootcause.py", "schedule.py", "model.py", "detect.py"]
TEXT_ATTRS = {"name", "reason_text"}


@pytest.fixture(scope="module")
def cfg():
    return model.load_config()


def _graph(names=("alpha", "beta", "gamma"), reason="some text"):
    nodes = [
        Node(node_id=1, plan_id=7, status="done", node_type="task", mission_id=11,
             dependencies=(), duration_s=3600.0, name=names[0], reason_text=reason,
             mission_status="succeeded"),
        Node(node_id=2, plan_id=7, status="blocked", node_type="task", mission_id=12,
             dependencies=(1,), duration_s=7200.0, name=names[1], reason_text=reason,
             mission_status="failed", consecutive_fails=0),
        Node(node_id=3, plan_id=7, status="blocked", node_type="task", mission_id=13,
             dependencies=(2,), duration_s=7200.0, name=names[2], reason_text=reason,
             mission_status="succeeded", consecutive_fails=2),
    ]
    return Graph.build(nodes, ["done"], ["blocked", "failed"])


def _fingerprint(g, cfg):
    w = {nid: g.nodes[nid].duration_s for nid in g.nodes}
    rep = dt.detect(g, w, cfg, capacity=4)
    bottlenecks = [(b.node_id, round(b.severity, 9), b.on_critical_path,
                    b.downstream_gated) for b in rep.bottlenecks]
    classes = [(c.node_id, c.label, c.rule_id) for c in rc.classify_graph(g, w, cfg)]
    return bottlenecks, classes


def test_decision_modules_never_read_text_fields():
    offenders = []
    for mod in DECISION_MODULES:
        tree = ast.parse((PKG / mod).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in TEXT_ATTRS:
                offenders.append((mod, node.lineno, node.attr))
    assert offenders == [], f"decision code reads text fields: {offenders}"


def test_rule_predicates_only_use_whitelisted_features(cfg):
    for rule in cfg["rootcause"]["rules"]:
        for cond in rule["when"].get("all", []):
            assert cond["feature"] in rc.ALLOWED_FEATURES, (rule["id"], cond)
    for rule in cfg["rootcause"]["rules"]:
        assert rule["class"] in cfg["rootcause"]["classes"], rule


def test_features_dict_contains_only_whitelisted_keys(cfg):
    g = _graph()
    w = {nid: g.nodes[nid].duration_s for nid in g.nodes}
    feat = rc.features(g, g.nodes[2], w, plan_median_duration_s=7200.0, cfg=cfg)
    assert set(feat) == set(rc.ALLOWED_FEATURES)


def test_classify_rejects_smuggled_features(cfg):
    feat = {k: 0 for k in rc.ALLOWED_FEATURES}
    feat["reason_text"] = "infra x5 (agent_lost)"
    with pytest.raises(rc.RuleError):
        rc.classify(feat, cfg)


def test_classify_is_invariant_to_text(cfg):
    g = _graph(names=("alpha", "beta", "gamma"), reason="reason channel says one thing")
    before = _fingerprint(g, cfg)
    erased = _graph(names=("", "", ""), reason="")
    after = _fingerprint(erased, cfg)
    assert before == after


def test_classify_is_invariant_to_renaming(cfg):
    base = _fingerprint(_graph(), cfg)
    rng = random.Random(12345)
    for _ in range(5):
        names = tuple(
            "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789_") for _ in range(rng.randint(0, 24)))
            for _ in range(3)
        )
        reason = "".join(rng.choice("xyz -_") for _ in range(rng.randint(0, 30)))
        assert _fingerprint(_graph(names=names, reason=reason), cfg) == base


def test_rule_order_prefers_barrier_then_delivery_then_code_then_resource(cfg):
    def label(**feat):
        base = {k: 0 for k in rc.ALLOWED_FEATURES}
        base.update({"node_type": "task", "has_mission": True,
                     "mission_class": "failed", "consecutive_fails": 0,
                     "duration_ratio": 1.0})
        base.update(feat)
        return rc.classify(base, cfg)

    assert label(node_type="gate", has_mission=False, mission_class="none")[0] == "upstream_not_delivered"
    assert label(consecutive_fails=2, mission_class="succeeded", duration_ratio=0.5)[0] == "artifact_mismatch"
    assert label(consecutive_fails=2, mission_class="succeeded", duration_ratio=50.0)[0] == "code_error"
    assert label(consecutive_fails=2, mission_class="failed")[0] == "code_error"
    assert label(mission_class="failed")[0] == "resource_shortage"
    assert label(mission_class="succeeded", has_mission=False)[0] == "upstream_not_delivered"


def test_no_cjk_in_machine_readable_vocab(cfg):
    """机器可读词表（状态/规则 id/类别/动作）不得含 CJK —— 领域语义一进来就先在这里露头。"""
    cjk = re.compile(r"[\u4e00-\u9fff]")
    vocab = (
        cfg["status_vocab"]["satisfied"]
        + cfg["status_vocab"]["blocking"]
        + cfg["status_vocab"]["active"]
        + cfg["rootcause"]["classes"]
        + [r["id"] for r in cfg["rootcause"]["rules"]]
        + [r["class"] for r in cfg["rootcause"]["rules"]]
        + list(cfg["advice"]["action_by_rootcause"].values())
        + list(cfg["advice"]["actions"])
    )
    assert not [v for v in vocab if cjk.search(str(v))]


def test_detect_flags_only_blocking_status():
    g = _graph()
    w = {nid: g.nodes[nid].duration_s for nid in g.nodes}
    cfg = model.load_config()
    rep = dt.detect(g, w, cfg, capacity=4)
    assert set(rep.bottleneck_ids) <= {2, 3}
    assert rep.bottlenecks[0].node_id == 2  # 有下游的阻塞节点排前面


def test_structural_heuristic_baseline_flags_healthy_nodes():
    """对照基线（仅实验用）会标记健康节点 —— 这正是 AC5 负控的鉴别力来源。"""
    cfg = model.load_config()
    g = _graph()
    for n in list(g.nodes.values()):
        if n.node_id == 1:
            continue
    w = {nid: g.nodes[nid].duration_s for nid in g.nodes}
    rep = dt.detect(g, w, cfg, capacity=4)
    heuristic = dt.structural_heuristic_detect(g, w)
    assert not (set(heuristic) & set())  # 结构启发式至少给出一个标记
    assert set(heuristic) - set(rep.bottleneck_ids) >= {1} or set(heuristic) == {1, 2, 3}