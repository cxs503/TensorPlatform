"""IN99-N10(c) 机器门：18 类违规变异逐条拒收并给出具体违规项（判据 C3/C5）。"""
from __future__ import annotations

import copy
import json

import pytest

from ops.taskplane.governance import bench as bench_mod
from ops.taskplane.governance import machine_gate as mg

MUTANTS = {mid: (desc, expected) for mid, desc, expected in bench_mod._mutants()}


def _seed():
    return bench_mod.compliant_seed()


# ─────────────────────────── 基线与判据 ───────────────────────────

def test_compliant_seed_accepted():
    rep = mg.evaluate_seed(_seed())
    assert rep.ok, rep.to_dict()["reasons"]
    assert rep.stats["n_nodes"] == 8 and rep.stats["n_milestones"] == 3 \
        and rep.stats["n_maturation"] == 2
    assert rep.checks["NODE_COUNT"] == 1 and rep.checks["DAG_ACYCLIC"] == 1


def test_report_serializable_and_deterministic():
    a = mg.evaluate_seed(_seed()).to_dict()
    b = mg.evaluate_seed(_seed()).to_dict()
    assert a == b and json.dumps(a, ensure_ascii=False)
    assert set(a["checks"]) >= {"NODE_COUNT", "EXT_ID_GLOBAL_UNIQUE", "REPO_ALLOWED",
                                "GOAL_THICK", "MILESTONE_COUNT", "MATURATION_ENOUGH"}


# ─────────────────────────── C3：逐条变异 ───────────────────────────

@pytest.mark.parametrize("mid", sorted(MUTANTS))
def test_mutant_rejected_with_specific_codes(mid):
    desc, expected = MUTANTS[mid]
    seed = bench_mod.apply_mutant(_seed(), mid)
    rep = mg.evaluate_seed(seed, ext_id_registry={"IN98-OLD-1"})
    assert not rep.ok, f"{mid}（{desc}）本应被拒收却通过了"
    codes = rep.codes()
    missing = [c for c in expected if c not in codes]
    assert not missing, f"{mid} 缺少预期违规码 {missing}，实际 {codes}"
    # 拒收理由必须具体（带节点/码/数值），不能是空话
    for v in rep.violations:
        assert v.code and v.detail and len(v.detail) >= 4


def test_all_mutants_and_only_one_baseline():
    accepted, rejected = 0, 0
    for mid in MUTANTS:
        rep = mg.evaluate_seed(bench_mod.apply_mutant(_seed(), mid), ext_id_registry={"IN98-OLD-1"})
        accepted += int(rep.ok)
        rejected += int(not rep.ok)
    assert rejected == len(MUTANTS) and accepted == 0
    assert mg.evaluate_seed(_seed()).ok          # 负控：合规 seed 不被误拒


# ─────────────────────────── C5：注册表负控 ───────────────────────────

def test_registry_collision_is_the_only_violation():
    seed = _seed()
    seed["nodes"][0]["metadata"]["ext_id"] = "IN98-OLD-1"
    rep = mg.evaluate_seed(seed, ext_id_registry={"IN98-OLD-1", "IN98-OLD-2"})
    assert not rep.ok and rep.codes() == ["EXT_ID_COLLISION"]
    assert rep.violations[0].node_id == seed["nodes"][0]["node_id"]


def test_registry_is_injected_not_hardcoded():
    seed = _seed()
    assert mg.evaluate_seed(seed, ext_id_registry=set()).ok
    assert mg.evaluate_seed(seed, ext_id_registry={seed["nodes"][0]["metadata"]["ext_id"]}).ok is False


def test_thresholds_tunable_by_config():
    seed = _seed()
    loose = mg.GateConfig(min_nodes=1, max_nodes=99, min_goal_chars=1, min_milestones=1,
                          min_maturation=0, require_criteria=False,
                          repo_whitelist=("TensorPlatform",))
    assert mg.evaluate_seed(seed, loose).ok
    strict = mg.GateConfig(min_nodes=9, max_nodes=12)
    rep = mg.evaluate_seed(seed, strict)
    assert not rep.ok and rep.codes() == ["NODE_COUNT"]
    assert "8" in rep.violations[0].detail and "9" in rep.violations[0].detail


def test_config_from_dict_rejects_unknown():
    with pytest.raises(ValueError):
        mg.GateConfig.from_dict({"min_nodes": 5, "typo": True})
    cfg = mg.GateConfig(min_nodes=6, min_goal_chars=100)
    assert mg.GateConfig.from_dict(cfg.to_dict()).min_nodes == 6


def test_max_nodes_boundary_is_inclusive():
    seed = _seed()
    nodes = seed["nodes"]
    for i in range(4):                                  # 8 → 12
        nodes.append({"node_id": 301 + i, "milestone_id": "M3", "dependencies": [101],
                      "metadata": copy.deepcopy(nodes[0]["metadata"])})
        nodes[-1]["metadata"]["ext_id"] = f"IN99-N{301 + i}"
    rep = mg.evaluate_seed(seed)
    assert rep.ok and rep.stats["n_nodes"] == 12
    nodes.append({"node_id": 401, "milestone_id": "M3", "dependencies": [101],
                  "metadata": dict(copy.deepcopy(nodes[0]["metadata"]), ext_id="IN99-N401")})
    rep13 = mg.evaluate_seed(seed)
    assert not rep13.ok and "NODE_COUNT" in rep13.codes()