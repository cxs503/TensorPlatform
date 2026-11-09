"""IN99-N10(b) 战役 DAG：依赖归一 / 本 plan 约束 / 拓扑排序 / 环检出（判据 C2）。"""
from __future__ import annotations

import random

import pytest

from ops.taskplane.governance import dag


def _nodes(*spec):
    """spec: (node_id, dependencies) 元组序列 → 节点 dict 列表。"""
    return [{"node_id": nid, "dependencies": list(deps)} for nid, deps in spec]


# ─────────────────────────── 依赖归一 ───────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (None, []), ([], []), ("{}", []), ("{1,2}", [1, 2]), ("[3]", [3]),
    ("4", [4]), (5, [5]), ([1, "2"], [1, 2]), (["{7}"], [7]),
])
def test_dependency_normalization(raw, expected):
    assert dag._as_dep_list(raw) == expected


def test_pg_text_dependencies_accepted():
    """PG 的 dependencies::text 形如 {1,2}，必须与 list 语义一致。"""
    a = dag.check_dag([{"node_id": 1, "dependencies": "{}"},
                       {"node_id": 2, "dependencies": "{1}"}])
    b = dag.check_dag([{"node_id": 1, "dependencies": []},
                       {"node_id": 2, "dependencies": [1]}])
    assert a.ok and b.ok and a.order == b.order == [1, 2]


def test_duplicate_and_malformed_nodes_reported():
    rep = dag.check_dag([{"node_id": 1}, {"node_id": 1}, {"dependencies": []}])
    assert not rep.ok
    joined = " ".join(rep.problems)
    assert "重复" in joined and "不可解析" in joined


# ─────────────────────────── 拓扑与环 ───────────────────────────

def test_topological_order_deterministic_and_respects_deps():
    nodes = _nodes((3, [1]), (1, []), (2, [1]), (4, [2, 3]))
    order = dag.topological_order(nodes)
    pos = {n: i for i, n in enumerate(order)}
    assert order == dag.topological_order(list(reversed(nodes)))     # 输入序无关
    assert pos[1] < pos[2] < pos[4] and pos[1] < pos[3] < pos[4]


def test_three_cycle_detected_with_readable_path():
    rep = dag.check_dag(_nodes((1, [3]), (2, [1]), (3, [2]), (4, [1])))
    assert not rep.ok
    assert rep.cycles == [[1, 3, 2, 1]] or rep.cycles[0][0] == 1
    assert rep.to_dict()["cycle_paths"] and "→" in rep.to_dict()["cycle_paths"][0]
    assert any("DAG 有环" in r for r in rep.reasons())


def test_self_loop_and_out_of_plan_reported():
    loop = dag.check_dag(_nodes((1, [1])))
    assert loop.self_loops == [1] and not loop.ok
    ext = dag.check_dag(_nodes((1, []), (2, [1, 99])))
    assert ext.out_of_plan == {2: [99]} and not ext.ok
    assert "plan 外" in " ".join(ext.reasons())


def test_acyclic_graph_not_flagged():
    rep = dag.check_dag(_nodes((1, []), (2, [1]), (3, [1, 2])))
    assert rep.ok and rep.order == [1, 2, 3] and not rep.cycles


# ─────────────────────────── C2：100% 检出的对照 ───────────────────────────

def _has_cycle_matrix(n, edges):
    """独立实现（Warshall 可达矩阵），与 dag 的 Kahn/DFS 实现不同路。"""
    reach = [[False] * n for _ in range(n)]
    for u, v in edges:
        reach[u][v] = True
    for k in range(n):
        for i in range(n):
            if reach[i][k]:
                for j in range(n):
                    reach[i][j] = reach[i][j] or reach[k][j]
    return any(reach[i][i] for i in range(n))


@pytest.mark.parametrize("n", [2, 3, 4])
def test_c2_exhaustive_small_graphs(n):
    possible = [(i, j) for i in range(n) for j in range(n) if i != j]
    for mask in range(1 << len(possible)):
        edges = [possible[k] for k in range(len(possible)) if mask >> k & 1]
        nodes = [{"node_id": i + 1, "dependencies": [j + 1 for (u, j) in edges if u == i]}
                 for i in range(n)]
        assert dag.check_dag(nodes).ok == (not _has_cycle_matrix(n, edges)), \
            f"n={n} mask={mask} edges={edges}"


def test_c2_random_up_to_12_nodes():
    rng = random.Random(20261108)
    for n in (5, 8, 12):
        for _ in range(200):
            order = list(range(1, n + 1))
            rng.shuffle(order)
            nodes = [{"node_id": i, "dependencies": []} for i in range(1, n + 1)]
            for a in range(n):
                for b in range(a + 1, n):
                    if rng.random() < 0.25:
                        nodes[order[a] - 1]["dependencies"].append(order[b])
            if rng.random() < 0.5:
                u, v = rng.sample(range(1, n + 1), 2)
                nodes[v - 1]["dependencies"].append(u)
            edges = [(nd["node_id"] - 1, d - 1) for nd in nodes for d in nd["dependencies"]]
            assert dag.check_dag(nodes).ok == (not _has_cycle_matrix(n, edges))


def test_frontier_unlocks_downstream():
    nodes, _ = dag.normalize_nodes(_nodes((1, []), (2, [1]), (3, [1]), (4, [2, 3])))
    assert dag.frontier(nodes, done=set()) == [1]
    assert dag.frontier(nodes, done={1}) == [2, 3]
    assert dag.frontier(nodes, done={1, 2, 3}) == [4]