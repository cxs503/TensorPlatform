"""锦标赛编排器验收测试（IN25-T1）。

判据先于实现（机器可裁）：
1. 席位生成：每席 mission 的 goal 必带四轴多样性清单，mission.diversity 四轴齐全；
   同代 ``(model_family, framework)`` 唯一；近同族候选必被拒并留痕。
2. 干跑链路：受控干跑走满三代；G1/G2 无 PASS，G3 出冠军；冠军谱系可回溯到 G1。
3. PASS 早停：任一代 PASS 后不再播后续代席位，预算回收；分数略低于门限时**不得**误触发
   （反向负例）。
4. 预算门：总席位上限硬顶，实播席位数 <= 上限，截断留痕。
5. 物理锚：anchor_ok=False 一票否决（分数再高也不能夺冠）；锚指标缺失 → 席位无效。
6. 负知识轨：全席产物入 S-N 轨（含冠军），篡改任一环可检出。
7. 审计：哈希链全留痕且可验；篡改可检出；同 seed + 固定时钟逐字节可复现。
8. Goodhart 门：``scorer_basis="model"`` 的模型自评打分器在构造期被拒。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.tournament import dry_run
from tools.tournament.ledger import AuditLog, NegativeLedger, verify_chain
from tools.tournament.model import (
    Gene,
    ProblemSpec,
    Seat,
    admissibility,
    build_goal,
    candidate_pool,
    gene_similarity,
    promote_count,
    select_fresh,
)
from tools.tournament.orchestrator import TournamentOrchestrator
from tools.tournament.report import build_report, render_markdown, report_digest

FIXED_CLOCK = lambda: "2026-11-06T00:00:00+00:00"  # noqa: E731  (确定性时钟)


# ----------------------------------------------------------------------------- 夹具
def toy_problem(**overrides) -> ProblemSpec:
    kwargs = dict(
        problem_id="toy",
        title="玩具问题（测试用）",
        families=("famA", "famB", "famC"),
        frameworks=("fwA", "fwB"),
        anchors=("anchorX",),
        anchor_metrics=("rel_error",),
        score_threshold=0.9,
        max_rel_error=0.1,
        seats_per_generation=3,
        max_generations=3,
        max_total_seats=9,
        seed=3,
    )
    kwargs.update(overrides)
    return ProblemSpec(**kwargs)


def table_scorer(score_by_generation=None, default=0.5, anchor_ok=True, metrics_extra=None):
    """按代给分（用于编排器机制测试；物理正确性由干跑装置的解析参考负责）。"""
    score_by_generation = score_by_generation or {}

    def scorer(mission, artifact):
        score = score_by_generation.get(mission["generation"], default)
        return {
            "score": score,
            "passed": score >= 0.9,
            "anchor_ok": anchor_ok,
            "metrics": {"rel_error": round(1.0 - score, 6), **(metrics_extra or {})},
        }

    return scorer


def echo_executor(mission):
    return {
        "estimate": {"zeta": 0.08, "omega": 2.4, "x0": 0.05},
        "mission_id": mission["mission_id"],
        "self_reported_score": 0.999,  # 永远虚高的自评分：编排器必须丢弃
    }


def run_tournament(tmp_path, problem, scorer, executor=echo_executor, name="run"):
    orch = TournamentOrchestrator(
        problem=problem,
        scorer=scorer,
        executor=executor,
        workdir=tmp_path / name,
        clock=FIXED_CLOCK,
    )
    return orch, orch.run()


# ----------------------------------------------------------------------------- 1 席位生成
def test_every_mission_carries_diversity_clause(tmp_path):
    problem = toy_problem()
    orch, report = run_tournament(tmp_path, problem, table_scorer())
    assert report["criteria"]["diversity_fields_in_every_goal"] is True
    missions = sorted((tmp_path / "run" / "missions").rglob("*.json"))
    assert len(missions) == report["budget"]["seats_spent"] == len(orch.records)
    for path in missions:
        mission = json.loads(path.read_text("utf-8"))
        div = mission["diversity"]
        assert set(div) >= {"model_family", "framework", "blind_prediction", "physics_anchor"}
        goal = mission["goal"]
        for axis in ("model_family =", "framework =", "blind_prediction =", "physics_anchor ="):
            assert axis in goal
        assert "多样性清单" in goal and "禁止自报分数" in goal


def test_family_signature_unique_within_generation(tmp_path):
    problem = toy_problem(seats_per_generation=4, max_generations=3, max_total_seats=12)
    orch, report = run_tournament(tmp_path, problem, table_scorer(default=0.1))
    for gen in report["generations"]:
        sigs = [row["gene"]["family_signature"] for row in gen["ranked"]]
        assert len(sigs) == len(set(sigs)), f"G{gen['generation']} 出现同族重复席位"
        assert gen["diversity"]["distinct_family_signatures"] == len(gen["ranked"])


def test_near_duplicate_candidate_is_rejected(tmp_path):
    problem = toy_problem(sibling_similarity_max=0.8)
    chosen = [
        Seat("T01-S01-aaaa", 1, "C-1", Gene("famA", "fwA", True, "anchorX"), "famA 路线在 fwA 框架内推进估计")
    ]
    near = Gene("famA", "fwB", True, "anchorX")  # 只换框架：轴相似度 0.75
    ok, reason = admissibility(near, "famA 路线在 fwA 框架内推进估计", chosen, problem)
    assert ok is False and reason.startswith("sibling_similarity=")

    same = Gene("famA", "fwA", False, "anchorX")
    ok2, reason2 = admissibility(same, "别的假设", [], problem)
    assert ok2 is True and reason2 == ""
    ok3, reason3 = admissibility(same, "别的假设", chosen, problem)
    assert ok3 is False and reason3 == "family_signature_duplicate"

    picks, rejections = select_fresh(problem, __import__("random").Random(0), 3, chosen, {"famA|fwA"})
    assert picks and all(p[0].family_signature != "famA|fwA" for p in picks)
    assert rejections, "抽样必须把被拒候选的原因留下"


def test_candidate_pool_is_full_cartesian_product():
    problem = toy_problem()
    pool = candidate_pool(problem)
    assert len(pool) == len(problem.families) * len(problem.frameworks) * len(problem.anchors) * len(problem.blind_options)
    assert len({g.family_signature for g in pool}) == len(problem.families) * len(problem.frameworks)


def test_promotion_ratio_and_elimination(tmp_path):
    problem = toy_problem(
        seats_per_generation=5,
        max_generations=1,
        max_total_seats=5,
        promote_ratio=0.6,
        min_promote=2,
        families=("famA", "famB", "famC", "famD", "famE"),
        frameworks=("fwA", "fwB", "fwC"),
    )
    assert promote_count(problem, 5) == 3
    orch, report = run_tournament(tmp_path, problem, table_scorer(default=0.2))
    gen = report["generations"][0]
    assert len(gen["promoted"]) == 3 and len(gen["eliminated"]) == 2


# ----------------------------------------------------------------------------- 2 干跑链路
def test_dryrun_three_generation_chain(tmp_path):
    report = dry_run.run_scenario("three_generations", tmp_path, clock=FIXED_CLOCK)
    verdict = report["verdict"]
    assert report["generations"][0]["passed"] == []
    assert report["generations"][1]["passed"] == []
    assert verdict["pass_generation"] == 3
    assert verdict["verdict"] == "champion"
    champion = verdict["champion"]
    assert champion["metrics"]["rel_error"] <= report["problem"]["max_rel_error"]
    assert champion["score"] >= report["problem"]["score_threshold"]
    generations_in_lineage = {node["generation"] for node in verdict["champion_lineage"]}
    assert generations_in_lineage >= {1, 2, 3}, "冠军谱系必须能回溯到 G1（三代接力）"


def test_dryrun_hybrid_mission_reflows_parent_estimates(tmp_path):
    report = dry_run.run_scenario("three_generations", tmp_path, clock=FIXED_CLOCK)
    hybrid_missions = [
        json.loads(p.read_text("utf-8"))
        for p in sorted((tmp_path / "runs" / "three_generations" / "missions").rglob("*.json"))
        if json.loads(p.read_text("utf-8"))["origin"] in ("hybrid", "mutation")
    ]
    assert hybrid_missions, "三代链路必须出现杂交/变异后代"
    hybrid = [m for m in hybrid_missions if m["origin"] == "hybrid"]
    assert hybrid, "需要至少一个杂交后代"
    for mission in hybrid:
        assert len(mission["parents"]) == 2
        assert mission["parent_estimates"], "父代实测量必须回流给子代"
        assert {"zeta", "omega", "x0"} <= set(mission["parent_estimates"][0])


def test_dryrun_all_eliminated_is_honest_fail(tmp_path):
    report = dry_run.run_scenario("all_eliminated", tmp_path, clock=FIXED_CLOCK)
    assert report["verdict"]["verdict"] == "all_eliminated"
    assert report["verdict"]["champion"] is None
    assert report["verdict"]["generations_run"] == 3
    assert report["generations"][-1]["passed"] == []
    assert report["criteria"]["champion_rel_error"] is None
    # 全灭也必须留痕：全席入负知识轨且全部未夺冠
    assert report["negative_knowledge"]["covers_all_seats"] is True
    assert all(rec["verdict"] != "champion" for rec in report["negative_knowledge"]["records"])


# ----------------------------------------------------------------------------- 3 PASS 早停
def test_pass_early_stop_blocks_following_generations(tmp_path):
    report = dry_run.run_scenario("pass_early", tmp_path, clock=FIXED_CLOCK)
    assert report["verdict"]["pass_generation"] == 1
    assert report["verdict"]["stopped_early"] is True
    assert report["verdict"]["generations_run"] == 1
    assert report["budget"]["early_stop_seats_saved"] > 0
    assert report["budget"]["seats_unspent"] > 0
    g2_dir = tmp_path / "runs" / "pass_early" / "missions" / "G02"
    assert not g2_dir.exists() or not list(g2_dir.iterdir()), "PASS 之后不得再播后续代席位"
    assert report["audit"]["by_type"].get("early_stop") == 1


def test_no_early_stop_when_score_just_below_gate(tmp_path):
    """负例：分数差一点点（0.89 < 0.9）→ 不得早停，必须跑满代数并如实全灭。"""
    problem = toy_problem(max_generations=3, seats_per_generation=2, max_total_seats=6)
    orch, report = run_tournament(tmp_path, problem, table_scorer(default=0.89))
    assert report["verdict"]["verdict"] == "all_eliminated"
    assert report["verdict"]["stopped_early"] is False
    assert report["verdict"]["generations_run"] == 3
    assert "early_stop" not in report["audit"]["by_type"]
    assert report["budget"]["early_stop_seats_saved"] == 0


# ----------------------------------------------------------------------------- 4 预算门
def test_total_seat_cap_is_hard(tmp_path):
    problem = toy_problem(seats_per_generation=3, max_generations=3, max_total_seats=4)
    orch, report = run_tournament(tmp_path, problem, table_scorer(default=0.1))
    assert report["budget"]["seats_spent"] == 4 == len(orch.records)
    assert report["budget"]["seats_spent"] <= problem.max_total_seats
    assert report["criteria"]["seat_cap_respected"] is True
    assert report["audit"]["by_type"].get("budget_capped") == 1
    assert report["budget"]["seats_unspent"] == 0


# ----------------------------------------------------------------------------- 5 物理锚
def test_anchor_violation_vetoes_champion(tmp_path):
    problem = toy_problem()
    orch, report = run_tournament(
        tmp_path, problem, table_scorer(score_by_generation={1: 0.99, 2: 0.99, 3: 0.99}, anchor_ok=False)
    )
    assert report["verdict"]["verdict"] == "all_eliminated", "anchor_ok=False 时高分也不得夺冠"
    assert report["audit"]["by_type"].get("anchor_violation") > 0
    assert all(row["anchor_ok"] is False for gen in report["generations"] for row in gen["ranked"])
    assert {row["failure_mode"] for gen in report["generations"] for row in gen["ranked"]} == {"anchor_violation"}


def test_missing_anchor_metric_invalidates_seat(tmp_path):
    problem = toy_problem(anchor_metrics=("rel_error", "must_have"))

    def scorer(mission, artifact):
        return {"score": 0.99, "passed": True, "anchor_ok": True, "metrics": {"rel_error": 0.01}}

    orch, report = run_tournament(tmp_path, problem, scorer)
    assert report["verdict"]["verdict"] == "all_eliminated"
    assert report["audit"]["by_type"].get("anchor_metric_missing") == len(orch.records)
    assert {row["status"] for gen in report["generations"] for row in gen["ranked"]} == {"invalid"}


def test_executor_crash_recorded_as_missing_artifact(tmp_path):
    def boom(mission):
        raise RuntimeError("执行器挂了")

    problem = toy_problem(max_generations=1, seats_per_generation=2, max_total_seats=2)
    orch, report = run_tournament(tmp_path, problem, table_scorer(default=0.99), executor=boom)
    assert report["audit"]["by_type"].get("artifact_missing") == 2
    assert all(rec["failure_mode"] == "no_artifact" for rec in report["negative_knowledge"]["records"])
    assert report["verdict"]["verdict"] == "all_eliminated"


# ----------------------------------------------------------------------------- 6 负知识轨
def test_negative_ledger_covers_every_seat_and_is_tamper_evident(tmp_path):
    problem = toy_problem()
    orch, report = run_tournament(tmp_path, problem, table_scorer(default=0.1))
    neg = report["negative_knowledge"]
    assert neg["covers_all_seats"] is True
    assert neg["count"] == report["budget"]["seats_spent"]
    assert [r["record_id"] for r in neg["records"]] == [f"S-N-{i:04d}" for i in range(1, neg["count"] + 1)]
    assert neg["chain_valid"] is True
    for record in neg["records"]:
        assert record["do_not_repeat"] and record["failure_mode"]
        assert record["gene"]["family_signature"]
    ledger = NegativeLedger()
    for record in neg["records"]:
        ledger.records.append(json.loads(json.dumps(record)))
    assert ledger.verify() is True
    ledger.records[1]["score"] = 0.99  # 事后美化历史
    assert ledger.verify() is False


def test_audit_chain_fully_traced_and_tamper_evident(tmp_path):
    problem = toy_problem()
    orch, report = run_tournament(tmp_path, problem, table_scorer(default=0.1))
    audit = report["audit"]
    assert audit["chain_valid"] is True
    for event_type in (
        "run_started",
        "generation_started",
        "seat_spawned",
        "artifact_collected",
        "seat_scored",
        "seat_promoted",
        "seat_eliminated",
        "generation_no_pass",
        "negative_knowledge_recorded",
        "run_finished",
    ):
        assert audit["by_type"].get(event_type, 0) >= 1, f"审计缺事件 {event_type}"
    assert verify_chain(orch.audit.events) is True
    tampered = [dict(e) for e in orch.audit.events]
    tampered[3]["payload"] = {**tampered[3]["payload"], "score": 1.0}
    assert verify_chain(tampered) is False


def test_self_reported_score_is_ignored(tmp_path):
    problem = toy_problem(max_generations=1, seats_per_generation=1, max_total_seats=1)
    orch, report = run_tournament(tmp_path, problem, table_scorer(default=0.1))
    row = report["generations"][0]["ranked"][0]
    assert row["score"] == pytest.approx(0.1), "工件里的 self_reported_score=0.999 必须被丢弃"
    assert report["audit"]["by_type"].get("score_spoof_ignored") == 1
    assert report["criteria"]["self_reported_scores_ignored"] == 1


# ----------------------------------------------------------------------------- 7 可复现 / 不变量
def test_same_seed_and_clock_is_byte_identical(tmp_path):
    problem = toy_problem()
    orch_a, report_a = run_tournament(tmp_path, problem, table_scorer(default=0.1), name="a")
    orch_b, report_b = run_tournament(tmp_path, problem, table_scorer(default=0.1), name="b")
    assert report_digest(report_a) == report_digest(report_b)
    assert orch_a.audit.head == orch_b.audit.head


def test_invariants_hold_across_seeds(tmp_path):
    for seed in (1, 2, 5, 8, 13):
        problem = ProblemSpec(**{**toy_problem().__dict__, "seed": seed, "max_total_seats": 7})
        orch, report = run_tournament(tmp_path, problem, table_scorer(default=0.2), name=f"seed{seed}")
        assert report["budget"]["seats_spent"] <= problem.max_total_seats
        assert report["verdict"]["generations_run"] <= problem.max_generations
        assert report["negative_knowledge"]["covers_all_seats"] is True
        assert report["audit"]["chain_valid"] is True
        championed = report["verdict"]["verdict"] == "champion"
        assert championed == (report["verdict"]["champion"] is not None)
        if championed:
            assert report["verdict"]["champion"]["passed"] is True
        for gen in report["generations"]:
            sigs = [row["gene"]["family_signature"] for row in gen["ranked"]]
            assert len(sigs) == len(set(sigs))


def test_dryrun_scenarios_are_replayable(tmp_path):
    a = dry_run.run_scenario("three_generations", tmp_path, clock=FIXED_CLOCK)
    b = dry_run.run_scenario("three_generations", tmp_path, clock=FIXED_CLOCK)
    assert report_digest(a) == report_digest(b)


# ----------------------------------------------------------------------------- 8 纪律门 / 报告
def test_model_based_scorer_is_rejected_at_construction(tmp_path):
    problem = toy_problem(scorer_basis="model")
    with pytest.raises(ValueError, match="Goodhart"):
        TournamentOrchestrator(problem, table_scorer(), echo_executor, tmp_path / "x")


def test_report_criteria_and_markdown_render(tmp_path):
    report = dry_run.run_scenario("three_generations", tmp_path, clock=FIXED_CLOCK)
    criteria = report["criteria"]
    assert criteria["seat_cap_respected"] is True
    assert criteria["family_signature_uniqueness_per_generation"] is True
    assert criteria["diversity_fields_in_every_goal"] is True
    assert criteria["audit_chain_valid"] is True
    assert criteria["negative_knowledge_count"] == criteria["seats_spawned"]
    assert criteria["pass_generation"] == 3
    md = render_markdown(report)
    assert "锦标赛报告" in md and "冠军" in md and "S-N" in md
    json.dumps(report)  # 必须可 JSON 序列化


def test_build_goal_injects_lineage_and_gate(tmp_path):
    problem = toy_problem()
    gene = Gene("famA", "fwA", True, "anchorX")
    goal = build_goal(problem, gene, "假设X", lineage_note="hybrid of S1, S2")
    assert "hybrid of S1, S2" in goal
    assert f"score >= {problem.score_threshold:.4f}" in goal
    assert "anchorX" in goal
    assert gene_similarity(gene, gene) == 1.0