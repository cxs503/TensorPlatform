"""提案聚合播种管线回归测试（零领域语义：夹具全部用合成域名与合成文本）。

覆盖：异构 goal 归一 / 相似度口径 / 语义合并与吸收 / 互补保留 / 仓冲突留证 /
ext_id 幂等 / 机器门七项硬校验（节点数、计划内与全库 ext_id 唯一、仓白名单、DAG 无环、
依赖闭合、里程碑覆盖、成熟化单数）/ 自回归注入幂等 / 溢出反哺下一波。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.proposal_seeding.aggregate import aggregate, load_proposals, similarity_measure
from tools.proposal_seeding.autoregress import (advance_wave, ensure_platform_node,
                                                pipeline_telemetry, platform_proposal)
from tools.proposal_seeding.config import load_config
from tools.proposal_seeding.gate import check_plan, topological_order
from tools.proposal_seeding.model import PlanNode, digest
from tools.proposal_seeding.seed import seed_plan

REPO_A = "TensorPlatform"
REPO_B = "TensorLBM"

DENSE = ("在这个合成案例里我们只用无意义占位串验证管线行为：widget flux measurement "
         "synthetic placeholder text for aggregation behaviour checks only, "
         "no physics domain vocabulary is involved anywhere in this fixture.")


def distinct_text(i: int) -> str:
    """互不相交的合成文本：保证任意两条的 3-gram Jaccard 接近 0（互补项）。

    注意：模板化改写（``alpha{i}beta{k}``）在字符 3-gram 下仍有 0.65 的相似度——
    这正是「模板句式会被相似度判成重复」的现场教训，故此处改用互不相交的随机串。
    """
    import hashlib
    body = " ".join(hashlib.sha256(f"{i}-{k}".encode()).hexdigest()[:10]
                    for k in range(24))
    return f"capability variant {i} widget subassembly " + body


@pytest.fixture()
def cfg():
    return load_config()


def write_props(tmp_path: Path, name: str, proposals: list[dict]) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({"proposals": proposals}, ensure_ascii=False), encoding="utf-8")
    return p


def prop(title: str, goal, *, repo: str = REPO_A, is_maturity: bool = False,
         deps: list[str] | None = None, criteria: list[str] | None = None,
         pid: str | None = None) -> dict:
    raw = {"title": title, "repo": repo, "goal": goal, "is_maturity": is_maturity,
           "dependencies_hint": deps or []}
    if criteria:
        raw["criteria"] = criteria
    if pid:
        raw["id"] = pid
    return raw


def make_plan(n: int, *, milestones: int = 3, repo: str = REPO_A,
              maturity: int = 2, stage: str = "w") -> dict:
    blocks = [i % milestones for i in range(n)]
    nodes = [{
        "index": i + 1,
        "ext_id": f"{stage}-n{i + 1:02d}-node",
        "name": f"node {i + 1}" + (" 成熟化" if i < maturity else ""),
        "repo": repo,
        "milestone": blocks[i],
        "goal": f"goal of node {i + 1}",
        "dependencies": [i] if i else [],
        "is_maturity": i < maturity,
        "metadata": {},
    } for i in range(n)]
    return {
        "plan_id": "test",
        "wave": stage,
        "milestones": [{"index": m, "name": f"M{m + 1}",
                        "node_indices": [i + 1 for i in range(n) if blocks[i] == m]}
                       for m in range(milestones)],
        "nodes": nodes,
        "overflow": [],
        "meta": {"capacity": 11},
    }


# ---------------------------------------------------------------------------
# 1. 输入归一
# ---------------------------------------------------------------------------
def test_load_heterogeneous_goal_shapes_and_rejections(cfg, tmp_path):
    a = write_props(tmp_path, "prop-1.json", [
        prop("alpha widget", "【objective】do a thing\n(1) first step"),
        prop("beta widget", {"objective": "do b", "approach": ["1) step b1", "2) step b2"]}),
        prop("gamma widget", ["1) only a list", "2) second line"]),
        {"title": "", "repo": REPO_A, "goal": "missing title"},  # 应被拒
    ])
    proposals, rejected, files = load_proposals([a], cfg)
    assert len(proposals) == 3 and len(rejected) == 1
    assert files[0]["raw_count"] == 4 and files[0]["accepted"] == 3
    assert "title" in rejected[0]["reason"]
    by_title = {p.title: p for p in proposals}
    assert by_title["alpha widget"].sections["objective"] == ["do a thing", "first step"]
    assert by_title["beta widget"].sections["approach"] == ["step b1", "step b2"]
    assert by_title["gamma widget"].sections["body"] == ["only a list", "second line"]


def test_digest_is_content_addressed_not_source_addressed(cfg, tmp_path):
    same = prop("same widget", "identical body text")
    f1 = write_props(tmp_path, "prop-11.json", [same])
    f2 = write_props(tmp_path, "prop-22.json", [dict(same)])
    p1, _, _ = load_proposals([f1], cfg)
    p2, _, _ = load_proposals([f2], cfg)
    assert p1[0].digest == p2[0].digest
    assert p1[0].source == "11" and p2[0].source == "22"


# ---------------------------------------------------------------------------
# 2. 相似度
# ---------------------------------------------------------------------------
def test_similarity_bounds_and_symmetry(cfg):
    m1 = similarity_measure(DENSE, DENSE + " extra tail words", cfg)
    assert 0.0 <= m1["jaccard"] <= 1.0
    assert m1["jaccard"] == similarity_measure(DENSE + " extra tail words", DENSE,
                                               cfg)["jaccard"]
    assert similarity_measure(DENSE, "completely other words zzz", cfg)["jaccard"] < 0.1
    assert similarity_measure(DENSE, DENSE, cfg)["jaccard"] == 1.0


# ---------------------------------------------------------------------------
# 3. 语义合并 + 吸收细节
# ---------------------------------------------------------------------------
def test_overlapping_proposals_merge_and_absorb_details(cfg, tmp_path):
    f1 = write_props(tmp_path, "prop-1.json", [
        prop("widget flux measure", {"objective": DENSE, "approach": ["1) base approach"]},
             is_maturity=False, criteria=["误差 < 1%"]),
    ])
    f2 = write_props(tmp_path, "prop-2.json", [
        prop("widget flux measure",
             {"objective": DENSE,
              "approach": ["1) base approach", "2) extra detail from b"]},
             criteria=["误差 < 1%", "≥3 阶收敛"], deps=["hint from b"]),
    ])
    agg = aggregate([f1, f2], cfg)
    assert agg["counts"]["proposals_in"] == 2
    assert agg["counts"]["items_out"] == 1
    assert agg["counts"]["merged_semantic"] == 1
    assert agg["counts"]["cross_source_merges"] == 1
    item = agg["items"][0]
    assert {q["source"] for q in item["quotes"]} == {"1", "2"}
    assert "extra detail from b" in item["sections"]["approach"]
    assert "≥3 阶收敛" in item["criteria"]
    assert "hint from b" in item["deps"]
    assert item["max_similarity"] >= cfg.similarity.merge_threshold


def test_complementary_proposals_are_kept_not_merged(cfg, tmp_path):
    f1 = write_props(tmp_path, "prop-1.json", [prop("widget flux", DENSE, repo=REPO_A)])
    f2 = write_props(tmp_path, "prop-2.json", [
        prop("gadget torque audit", "完全不相干的另一种能力描述 zzz qqq", repo=REPO_B)])
    agg = aggregate([f1, f2], cfg)
    assert agg["counts"]["items_out"] == 2
    assert agg["counts"]["merged_semantic"] == 0
    assert agg["counts"]["distinct_kept"] == 2
    assert agg["similarity"]["max_jaccard"] < cfg.similarity.merge_threshold


def test_repo_conflict_is_recorded_not_silently_overwritten(cfg, tmp_path):
    f1 = write_props(tmp_path, "prop-1.json", [prop("widget flux", DENSE, repo=REPO_A)])
    f2 = write_props(tmp_path, "prop-2.json", [prop("widget flux", DENSE, repo=REPO_B)])
    agg = aggregate([f1, f2], cfg)
    item = agg["items"][0]
    assert item["repo_conflict"] is True
    assert set(item["repos"]) == {REPO_A, REPO_B}


# ---------------------------------------------------------------------------
# 4. 播种：ext_id 幂等 / 结构
# ---------------------------------------------------------------------------
def _five_inputs(tmp_path) -> list[Path]:
    files = []
    for i in range(5):
        files.append(write_props(tmp_path, f"prop-{i + 1}.json", [
            prop(f"capability {i} widget", distinct_text(i), repo=REPO_A,
                 is_maturity=(i < 1)),
        ]))
    return files


def test_ext_id_sequence_is_idempotent(cfg, tmp_path):
    files = _five_inputs(tmp_path)
    plans = [seed_plan(aggregate(files, cfg), cfg, wave="pTestw1", plan_id="t")
             for _ in range(2)]
    seq1 = [n["ext_id"] for n in plans[0]["nodes"]]
    seq2 = [n["ext_id"] for n in plans[1]["nodes"]]
    assert seq1 == seq2
    assert len(set(seq1)) == len(seq1)
    assert seq1[0].startswith("pTestw1-n01-")


def test_plan_structure_milestones_and_dag(cfg, tmp_path):
    files = _five_inputs(tmp_path)
    plan = seed_plan(aggregate(files, cfg), cfg, wave="pTestw1", plan_id="t")
    assert len(plan["nodes"]) == 5
    assert len(plan["milestones"]) == cfg.plan.milestone_count
    assert all(m["node_indices"] for m in plan["milestones"])
    order, cycle = topological_order([PlanNode.from_dict(n) for n in plan["nodes"]])
    assert not cycle and len(order) == 5
    assert plan["nodes"][1]["dependencies"] == [1]  # 里程碑内串联


# ---------------------------------------------------------------------------
# 5. 机器门
# ---------------------------------------------------------------------------
def test_gate_passes_on_valid_plan(cfg):
    gate = check_plan(make_plan(6), cfg)
    assert gate["all_passed"], gate["failed"]


@pytest.mark.parametrize("n", [4, 13])
def test_gate_node_count_bounds(cfg, n):
    gate = check_plan(make_plan(n, milestones=3), cfg)
    assert "node_count_range" in gate["failed"]


def test_gate_detects_cycle(cfg):
    plan = make_plan(6)
    plan["nodes"][0]["dependencies"] = [6]   # 1 → 6
    plan["nodes"][5]["dependencies"] = [1]   # 6 → 1 成环
    gate = check_plan(plan, cfg)
    assert "dag_acyclic" in gate["failed"]
    assert gate["checks"][4]["cycle"]


def test_gate_detects_dependency_outside_plan_and_self_dep(cfg):
    plan = make_plan(6)
    plan["nodes"][3]["dependencies"] = [99]
    plan["nodes"][4]["dependencies"] = [5]
    gate = check_plan(plan, cfg)
    assert "deps_within_plan" in gate["failed"]
    rec = [c for c in gate["checks"] if c["name"] == "deps_within_plan"][0]
    assert rec["outside"] == [{"node": 4, "dep": 99}]
    assert rec["self_dep"] == [5]


def test_gate_repo_whitelist(cfg):
    plan = make_plan(6, repo="NotARealRepo")
    gate = check_plan(plan, cfg)
    assert "repo_whitelist" in gate["failed"]


def test_gate_ext_id_global_collision_and_exclusion(cfg):
    plan = make_plan(6)
    taken = plan["nodes"][0]["ext_id"]
    assert "ext_id_unique_global" in check_plan(plan, cfg, global_ext_ids=[taken])["failed"]
    # 原地重播种：排除自身既有 ext_id 后应通过
    gate = check_plan(plan, cfg, global_ext_ids=[taken], exclude_ext_ids=[taken])
    assert "ext_id_unique_global" not in gate["failed"]


def test_gate_intra_duplicate_ext_id(cfg):
    plan = make_plan(6)
    plan["nodes"][1]["ext_id"] = plan["nodes"][0]["ext_id"]
    assert "ext_id_unique_intra" in check_plan(plan, cfg)["failed"]


def test_gate_maturity_and_milestone_coverage(cfg):
    thin = check_plan(make_plan(6, maturity=1), cfg)
    assert "maturity_count" in thin["failed"]
    plan = make_plan(6)
    plan["milestones"][2]["node_indices"] = []       # 里程碑 3 变空
    assert "milestone_coverage" in check_plan(plan, cfg)["failed"]


# ---------------------------------------------------------------------------
# 6. 自回归增长
# ---------------------------------------------------------------------------
def test_platform_node_injected_once_and_idempotent(cfg, tmp_path):
    files = _five_inputs(tmp_path)
    agg = aggregate(files, cfg)
    plan = seed_plan(agg, cfg, wave="pTestw1", plan_id="t")
    telemetry = pipeline_telemetry(agg, plan)
    plan2, inj1 = ensure_platform_node(plan, cfg, telemetry=telemetry)
    assert inj1["injected"] is True
    assert len(plan2["nodes"]) == 6
    plan3, inj2 = ensure_platform_node(plan2, cfg, telemetry=telemetry)
    assert inj2["injected"] is False
    assert len(plan3["nodes"]) == 6
    ext_ids = [n["ext_id"] for n in plan3["nodes"]]
    assert ext_ids == [n["ext_id"] for n in plan2["nodes"]]
    assert check_plan(plan3, cfg)["all_passed"]


def test_platform_proposal_is_deterministic_and_points_at_deficit(cfg):
    agg = {"counts": {"proposals_in": 4, "merged_semantic": 0, "cross_source_merges": 0},
           "similarity": {"max_jaccard": 0.11, "threshold": 0.55}}
    t1 = pipeline_telemetry(agg, {"nodes": [], "overflow": []})
    t2 = pipeline_telemetry(agg, {"nodes": [], "overflow": []})
    p1 = platform_proposal(t1, cfg, wave="w2")
    p2 = platform_proposal(t2, cfg, wave="w2")
    assert p1["title"] == p2["title"]
    assert digest(p1) == digest(p2)
    assert cfg.maturity_marker in p1["title"]
    assert p1["repo"] == cfg.platform_repo
    assert p1["source_telemetry"]["primary"]["signal"] == "no_semantic_overlap"


def test_overflow_carried_into_next_wave(cfg, tmp_path):
    files = []
    for i in range(cfg.plan.node_count_max + 1):  # 13 条 > 容量 11
        files.append(write_props(tmp_path, f"prop-{i + 1}.json", [
            prop(f"capability {i} widget", distinct_text(i), repo=REPO_A,
                 is_maturity=(i < 2)),
        ]))
    agg = aggregate(files, cfg)
    plan = seed_plan(agg, cfg, wave="pTestw1", plan_id="t")
    assert len(plan["nodes"]) == cfg.capacity
    assert len(plan["overflow"]) == 13 - cfg.capacity
    assert all("item" in entry for entry in plan["overflow"])
    plan, _ = ensure_platform_node(plan, cfg,
                                   telemetry=pipeline_telemetry(agg, plan))
    assert len(plan["nodes"]) == cfg.plan.node_count_max
    nxt = advance_wave(plan, cfg, wave_next="pTestw2")
    carried = {n["metadata"]["item_id"] for n in nxt["plan"]["nodes"]}
    assert {e["item_id"] for e in plan["overflow"]} <= carried
    assert nxt["backpressure"]["demand_new_proposals"] is True  # 2 条 < 下限 5 → 背压
    assert nxt["backpressure"]["demanded"] == cfg.plan.node_count_min - len(nxt["plan"]["nodes"])

# ---------------------------------------------------------------------------
# 7. 复现匹配通道（前缀通道的归一化顺序回归）
# ---------------------------------------------------------------------------
def test_prefix_channel_splits_before_normalisation(cfg):
    """回归：``normalize`` 会把冒号折成空格，前缀必须先切分后归一化。"""
    from tools.proposal_seeding.match import match_channels, prefix
    assert prefix("前缀标题：后面的细节") == "前缀标题"
    ch = match_channels("统一前缀标题：甲种细节写法", "统一前缀标题：乙种另类说法", cfg)
    assert ch["prefix_3gram"] == 1.0
    assert ch["cjk_3gram"] < 1.0          # 后缀不同 → 全文/中文通道都被尾部拉低
    assert ch["channel"] == "prefix_3gram"
    assert ch["score"] == 1.0


def test_cjk_channel_recovers_bilingual_paraphrase(cfg):
    """回归：长英文译文会稀释全文 3-gram，中文通道应恢复匹配。"""
    from tools.proposal_seeding.match import match_channels
    a = "同一能力描述：合成占位细节 (Identical Capability Description: synthetic placeholder)"
    b = "同一能力描述：合成占位细节"
    ch = match_channels(a, b, cfg)
    assert ch["full_3gram"] < cfg.repro_match_threshold
    assert ch["cjk_3gram"] >= 0.55
    assert ch["channel"] in ("cjk_3gram", "prefix_3gram")


def test_platform_role_channel_ignores_wording(cfg):
    from tools.proposal_seeding.match import is_platform_role
    assert is_platform_role(f"任意措辞{cfg.maturity_marker}单", cfg.platform_repo, cfg)
    assert not is_platform_role(f"任意措辞{cfg.maturity_marker}单", REPO_B, cfg)
    assert not is_platform_role("无标记节点", cfg.platform_repo, cfg)


def test_best_match_reports_channel_evidence(cfg):
    from tools.proposal_seeding.match import CHANNELS, best_match
    cands = [{"item_id": "a", "title": "完全不相干的另一件事"},
             {"item_id": "b", "title": "目标能力：合成细节"}]
    best = best_match("目标能力：另一种细节写法", cands, cfg)
    assert best["item_id"] == "b"
    assert set(CHANNELS) <= set(best["channels"])
    assert best["score"] == max(best["channels"].values())
