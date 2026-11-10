"""图算法与调度器纯单元测试（合成小图，不依赖线上语料）。"""

from __future__ import annotations

import pytest

from tools.campaign_optimizer.model import Graph, Node, graphs_from_nodes
from tools.campaign_optimizer import schedule as sched


def _node(nid, deps=(), status="pending", dur_h=1.0, **kw):
    return Node(node_id=nid, plan_id=1, status=status, dependencies=tuple(deps),
                duration_s=dur_h * 3600.0, mission_id=100 + nid, **kw)


SAT = ["done"]
BLK = ["blocked", "failed"]


def _diamond():
    nodes = [
        _node(1, (), "done", 1),
        _node(2, (1,), "pending", 5),
        _node(3, (1,), "pending", 1),
        _node(4, (2, 3), "pending", 1),
    ]
    return Graph.build(nodes, SAT, BLK)


def test_topo_order_and_cycle_detection():
    g = _diamond()
    assert g.topo_order() == [1, 2, 3, 4]
    cyclic = [
        _node(1, (2,)),
        _node(2, (1,)),
    ]
    with pytest.raises(ValueError, match="cycle"):
        Graph.build(cyclic, SAT, BLK).topo_order()


def test_critical_path_and_total_float():
    g = _diamond()
    w = {nid: g.nodes[nid].duration_s for nid in g.nodes}
    length, _ = g.longest_paths(w)
    assert length == pytest.approx(7 * 3600)
    assert g.critical_path(w) == [1, 2, 4]
    flt = g.total_float(w)
    assert flt[2] == pytest.approx(0.0)
    # 非关键分支 3 的浮动 = 关键路径长度 - 经过 3 的最长链 = 7 - 3 = 4 h
    assert flt[3] == pytest.approx(4 * 3600)


def test_downstream_closure():
    g = _diamond()
    assert g.downstream_closure(1) == [2, 3, 4]
    assert g.downstream_closure(4) == []
    assert g.upstream_closure(4) == [1, 2, 3]


def test_dangling_dependencies_are_dropped_and_recorded():
    nodes = [_node(1, (99,)), _node(2, (1,))]
    g = Graph.build(nodes, SAT, BLK)
    assert g.nodes[1].dependencies == ()
    assert g.dangling_deps == {1: (99,)}
    with pytest.raises(ValueError):
        Graph.build(nodes, SAT, BLK, dangling_policy="raise")


def test_graphs_from_nodes_groups_by_plan():
    nodes = [_node(1), _node(2)]
    nodes[1] = Node(node_id=2, plan_id=2, status="pending", dependencies=(), duration_s=3600.0)
    graphs = graphs_from_nodes(nodes, SAT, BLK)
    assert sorted(graphs) == [1, 2]


def test_scheduler_chain_makespan_is_serial_sum():
    nodes = [_node(1, (), "blocked", 2), _node(2, (1,), "blocked", 3)]
    g = Graph.build(nodes, SAT, BLK)
    w = {nid: g.nodes[nid].duration_s for nid in g.nodes}
    rc = {1: "unknown", 2: "unknown"}
    policy = {"priority": "dispatch_order", "retry_by_rootcause": {"unknown": 0},
              "attempt_divisor": 1}
    res = sched.simulate(g, w, rc, policy, capacity=4)
    # 每节点 1 次尝试（0 次失败）+ 依赖串行
    assert res.makespan_s == pytest.approx(5 * 3600)


def test_scheduler_retry_budget_scales_work():
    nodes = [_node(1, (), "blocked", 3)]
    g = Graph.build(nodes, SAT, BLK)
    w = {1: 3 * 3600.0}
    rc = {1: "resource_shortage"}
    base = sched.simulate(g, w, rc, {"priority": "dispatch_order", "retry_max": 2,
                                     "attempt_divisor": 1}, capacity=1)
    improved = sched.simulate(g, w, rc, {"priority": "dispatch_order",
                                         "retry_by_rootcause": {"resource_shortage": 1},
                                         "attempt_divisor": 1}, capacity=1)
    assert base.makespan_s == pytest.approx(3 * 3 * 3600.0)   # (2 次失败 + 1 次成功)
    assert improved.makespan_s == pytest.approx(2 * 3 * 3600.0)
    assert improved.makespan_s < base.makespan_s


def test_scheduler_respects_capacity():
    nodes = [_node(i, (), "pending", 1) for i in range(1, 5)]
    g = Graph.build(nodes, SAT, BLK)
    w = {nid: 3600.0 for nid in g.nodes}
    rc = {nid: "unknown" for nid in g.nodes}
    policy = {"priority": "dispatch_order", "retry_by_rootcause": {"unknown": 0},
              "attempt_divisor": 1}
    assert sched.simulate(g, w, rc, policy, capacity=4).makespan_s == pytest.approx(3600.0)
    assert sched.simulate(g, w, rc, policy, capacity=2).makespan_s == pytest.approx(7200.0)
    assert sched.simulate(g, w, rc, policy, capacity=1).makespan_s == pytest.approx(4 * 3600.0)


def test_improved_schedule_guard_never_regresses():
    """守卫：候选优先级里取最优，绝不劣于任一候选（含原始派单顺序）。"""
    nodes = [_node(1, (), "blocked", 10), _node(2, (), "pending", 1), _node(3, (1,), "pending", 1)]
    g = Graph.build(nodes, SAT, BLK)
    w = {nid: g.nodes[nid].duration_s for nid in g.nodes}
    rc = {1: "resource_shortage", 2: "unknown", 3: "unknown"}
    cfg = {
        "schedule": {
            "capacity": 1,
            "baseline": {"priority": "dispatch_order", "retry_max": 2, "attempt_divisor": 1},
            "improved": {"priority_candidates": ["critical_first", "dispatch_order"],
                         "retry_by_rootcause": {"resource_shortage": 1, "unknown": 0},
                         "attempt_divisor": 1},
        }
    }
    res = sched.improved_schedule(g, w, rc, cfg, capacity=1)
    plain = sched.simulate(g, w, rc, cfg["schedule"]["improved"], capacity=1)
    assert res.makespan_s <= plain.makespan_s + 1e-9


def test_scheduler_is_deterministic():
    g = _diamond()
    w = {nid: g.nodes[nid].duration_s for nid in g.nodes}
    rc = {nid: "unknown" for nid in g.nodes}
    policy = {"priority": "critical_first", "retry_by_rootcause": {"unknown": 1},
              "attempt_divisor": 2}
    a = sched.simulate(g, w, rc, policy, capacity=2).to_dict()
    b = sched.simulate(g, w, rc, policy, capacity=2).to_dict()
    assert a == b