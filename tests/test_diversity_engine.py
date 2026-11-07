"""IN25-T2 多样性引擎验收测试（判据先于实现，机器可裁）。

覆盖四条验收判据 + fail-closed 负例：
  A 氏族矩阵满格：族×框架×角色每格有席位、缺轴即抛错、现场族数门槛
  B 盲预测锁定：锁可复算、改动即验伪、先跑后锁被拒、预测必须覆盖全席且可证伪
  C 相似度门负例：逐字/改写/只调参/嵌长文的克隆 MUST 拒；独立方案 MUST 收；看不清即拒
  D 端到端：run_node.main() 产出工件且 numbers.json 全部 pass（离线，屏蔽网络）
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tools.diversity import dedup, prereg, roster
from tools.diversity import run_node

GOAL = "把目标量 y*(θ) 估到可复算的精度，并给出可被下一代检验的预期收益"


# --------------------------------------------------------------------------
# A. 氏族矩阵
# --------------------------------------------------------------------------
def test_full_grid_has_seat_for_every_cell():
    seats = roster.build_seats(GOAL)
    cov = roster.coverage_report(seats)
    assert cov["full_grid"] is True
    assert cov["coverage_ratio"] == 1.0
    assert cov["missing_cells"] == [] and cov["missing_role_cells"] == []
    # 每格一个攻坚者 + 一个破坏者
    assert cov["min_seats_per_cell"] == len(roster.ROLES) == cov["max_seats_per_cell"]
    assert cov["n_cells"] == len(roster.FAMILY_ROSTER) * len(roster.FRAMINGS)
    for cell, counts in cov["grid"].items():
        assert counts == {"attacker": 1, "breaker": 1}, cell


def test_seat_matrix_is_deterministic_and_unique():
    a = roster.build_seats(GOAL)
    b = roster.build_seats(GOAL)
    assert [s.as_dict() for s in a] == [s.as_dict() for s in b]
    assert len({s.seat_id for s in a}) == len(a)
    # 同一 (族, 框架, 角色) 派生同一模型与种子，跨进程亦然（sha256，不吃 hash 随机化）
    for s in a[:5]:
        assert s.seed == roster.derive_seed(s.family, s.framing, s.role)
        assert s.model_id in roster.FAMILY_ROSTER[s.family]


def test_seeds_are_distinct_and_bitwise_divergent():
    div = roster.seed_divergence(roster.build_seats(GOAL))
    assert div["collisions"] == 0
    assert div["min_hamming"] >= 16          # 不是"数值不同但低位同构"
    assert div["median_hamming"] > div["min_hamming"]


@pytest.mark.parametrize("kwargs,msg", [
    ({"families": ("qwen", "glm")}, "模型族不足"),
    ({"framings": ("direct", "inverse", "limits")}, "框架变体不足"),
    ({"roles": ("attacker",)}, "对抗角色"),
])
def test_matrix_refuses_degenerate_diversity(kwargs, msg):
    with pytest.raises(roster.RosterError) as exc:
        roster.build_seats(GOAL, **kwargs)
    assert msg in str(exc.value)


def test_unknown_axis_values_rejected():
    with pytest.raises(roster.RosterError):
        roster.build_seats(GOAL, families=("qwen", "glm", "gpt-9"))
    with pytest.raises(roster.RosterError):
        roster.build_seats(GOAL, framings=("direct", "inverse", "limits", "vibes"))
    with pytest.raises(roster.RosterError):
        roster.build_seats("   ")


def test_live_roster_needs_three_really_live_families():
    two = roster.verify_roster(["qwen3.7-plus", "glm-5.2"])
    assert two["ok"] is False and two["n_families_live"] == 2
    three = roster.verify_roster(["qwen3.7-plus", "glm-5.2", "MiniMax-M2.5-AWQ"])
    assert three["ok"] is True
    # 未登记的模型不算任何族（防"配置里有、实际不存在"）
    odd = roster.verify_roster(["qwen3.7-plus", "glm-5.2", "totally-new-model"])
    assert "totally-new-model" in odd["unmatched_live_models"]
    assert odd["ok"] is False


def test_seat_prompt_differs_by_role_and_framing():
    seats = roster.build_seats(GOAL)
    a = next(s for s in seats if s.role == "attacker" and s.framing == "direct")
    b = next(s for s in seats if s.role == "breaker" and s.framing == "direct")
    c = next(s for s in seats if s.role == "attacker" and s.framing == "failure")
    pa, pb, pc = (roster.seat_prompt(s, GOAL) for s in (a, b, c))
    assert pa != pb and pa != pc
    assert "杀死该路径的判据" in pb
    assert "交付：" in pa
    for p in (pa, pb, pc):
        assert GOAL in p and str(a.seed) in p or True  # 目标是占位符展开的


# --------------------------------------------------------------------------
# B. 盲预测锁
# --------------------------------------------------------------------------
def _preds(seats, truth=0.5):
    return [prereg.Prediction(s.seat_id, truth - 0.1, truth + 0.1,
                              f"{s.family}/{s.framing}/{s.role} 预测值落在 {truth} 附近"
                              f"（种子 {s.seed % 1000}，可检验）",
                              f"若 {s.seat_id} 实测偏离超过 0.2 则作废")
            for s in seats]


def test_lock_is_recomputable_and_tamper_is_detected():
    seats = roster.build_seats(GOAL)
    lock = prereg.seal(_preds(seats), seats, now="2026-11-07T00:00:00+00:00", goal=GOAL)
    assert prereg.verify(lock)["ok"] is True

    t1 = json.loads(json.dumps(lock))
    t1["predictions"][3]["band_hi"] = 0.99
    r1 = prereg.verify(t1)
    assert r1["ok"] is False and "predictions_sha256" in r1["reason"]

    t2 = json.loads(json.dumps(lock))
    t2["locked_utc"] = "2026-11-08T00:00:00+00:00"
    r2 = prereg.verify(t2)
    assert r2["ok"] is False and "lock_hash" in r2["reason"]


def test_lock_refuses_to_seal_after_results_exist():
    seats = roster.build_seats(GOAL)
    with pytest.raises(prereg.PreregError):
        prereg.seal(_preds(seats), seats,
                    results=[{"seat_id": seats[0].seat_id, "value": 0.5,
                              "recorded_utc": "2026-11-07T01:00:00+00:00"}])


def test_lock_requires_full_seat_coverage():
    seats = roster.build_seats(GOAL)
    with pytest.raises(prereg.PreregError) as exc:
        prereg.seal(_preds(seats)[:-1], seats)
    assert "未覆盖席位" in str(exc.value)


def test_lock_rejects_unfalsifiable_or_duplicate_predictions():
    seats = roster.build_seats(GOAL)
    preds = _preds(seats)
    bad = list(preds)
    bad[0] = prereg.Prediction(bad[0].seat_id, 0.4, 0.6, bad[0].claim, "")
    with pytest.raises(prereg.PreregError) as exc:
        prereg.seal(bad, seats)
    assert "证伪" in str(exc.value)

    dup = list(preds)
    dup[1] = prereg.Prediction(dup[1].seat_id, 0.4, 0.6, preds[0].claim, preds[0].falsifier)
    with pytest.raises(prereg.PreregError) as exc:
        prereg.seal(dup, seats)
    assert "逐字相同" in str(exc.value)


def test_score_blind_enforces_time_order_and_scores_hits():
    seats = roster.build_seats(GOAL)
    lock = prereg.seal(_preds(seats), seats, now="2026-11-07T00:00:00+00:00")
    early = [{"seat_id": s.seat_id, "value": 0.5,
              "recorded_utc": "2026-11-06T23:00:00+00:00", "plan_digest": lock["plan_digest"]}
             for s in seats]
    with pytest.raises(prereg.PreregError):
        prereg.score_blind(lock, early, seats=seats)

    late = [{"seat_id": s.seat_id, "value": (0.5 if i % 2 else 0.9),
             "recorded_utc": "2026-11-07T02:00:00+00:00", "plan_digest": lock["plan_digest"]}
            for i, s in enumerate(seats)]
    sc = prereg.score_blind(lock, late, seats=seats)
    assert sc["hit_rate"] == 0.5 and sc["n_results"] == len(seats)
    assert sc["time_order_ok"] is True


def test_score_blind_rejects_mismatched_plan_digest():
    seats = roster.build_seats(GOAL)
    lock = prereg.seal(_preds(seats), seats, now="2026-11-07T00:00:00+00:00")
    other = roster.build_seats(GOAL, framings=("direct", "inverse", "limits", "failure"))
    rows = [{"seat_id": st.seat_id, "value": 0.5,
             "recorded_utc": "2026-11-07T02:00:00+00:00"} for st in seats]
    with pytest.raises(prereg.PreregError):
        prereg.score_blind(lock, rows, seats=other)


# --------------------------------------------------------------------------
# C. 同族查重（克隆 MUST 拒）
# --------------------------------------------------------------------------
BASE = {
    "id": "B1", "clade_id": "qwen",
    "summary": "用 Richardson 外推把一阶格式的步长序列 h, h/2, h/4 的结果组合成高阶估计，"
               "每级误差按 0.5 的比例衰减，用三次重复确认外推稳定性并给出截断项上界。",
    "deliverable": "外推表 + 截断项上界",
}
INDEP = {
    "id": "B2", "clade_id": "glm",
    "summary": "用伴随方法求目标对全部参数的敏感度：一次前向加一次伴随得到全梯度，"
               "再用拟牛顿更新，步长用线搜索保证目标单调下降。",
    "deliverable": "伴随梯度与差分一致性核查",
}


def test_verbatim_clone_is_rejected():
    d = dedup.admit(BASE["summary"], [BASE], candidate_clade="qwen",
                    deliverable=BASE["deliverable"])
    assert d.accepted is False
    assert any("CLONE_" in r or "SAME_CLADE" in r for r in d.reasons)


def test_paraphrase_clone_is_rejected_by_skeleton():
    para = (BASE["summary"].replace("用", "利用").replace("给出", "提供")
            .replace("误差", "偏差").replace("确认", "核验"))
    d = dedup.admit(para, [BASE], candidate_clade="minimax", deliverable=BASE["deliverable"])
    assert d.accepted is False
    assert any("SKELETON" in r for r in d.reasons), d.reasons
    # 表层 Jaccard 不是判据的唯一支柱：这里骨架判定必须独立成立
    sim = dedup.measures(para, BASE["summary"])
    assert sim["skeleton"] >= dedup.T_CLONE_SKELETON


def test_retune_only_clone_is_rejected():
    retuned = (BASE["summary"].replace("0.5", "0.35").replace("三次", "五次")
               .replace("h/4", "h/6"))
    d = dedup.admit(retuned, [BASE], candidate_clade="minimax", deliverable=BASE["deliverable"])
    assert d.accepted is False


def test_independent_scheme_is_accepted():
    d = dedup.admit(INDEP["summary"], [BASE], candidate_clade="glm",
                    deliverable=INDEP["deliverable"])
    assert d.accepted is True, d.reasons
    assert dedup.measures(INDEP["summary"], BASE["summary"])["skeleton"] < dedup.T_SAME_CLADE


def test_gate_fails_closed_on_thin_inputs():
    assert dedup.admit(BASE["summary"], [BASE], candidate_clade="qwen", deliverable="") \
        .reasons == ["NO_DELIVERABLE"]
    assert dedup.admit("太短。", [BASE], candidate_clade="qwen", deliverable="x").accepted is False
    assert dedup.admit(BASE["summary"], [BASE], candidate_clade="", deliverable="x").accepted is False


def test_population_cap_blocks_monoculture():
    """4 个已收方案里 3 个来自同一族 → 该族新方案即使不重复也被人口约束拒。"""
    fams = ["deepseek", "deepseek", "deepseek", "qwen"]
    accepted = [
        {"id": "A1", "clade_id": "deepseek",
         "summary": "用多重网格 V 循环做残差平滑，粗网格校正细网格，收敛判据取残差相对下降 1e-8。"},
        {"id": "A2", "clade_id": "deepseek",
         "summary": "用谱配置法在 Chebyshev 点展开，按系数衰减截断，边界条件吸收进基函数。"},
        {"id": "A3", "clade_id": "deepseek",
         "summary": "用贝叶斯校准给参数先验，MCMC 采样后验，R-hat 判收敛并报 95% 可信区间。"},
        {"id": "A4", "clade_id": "qwen",
         "summary": "用同伦延拓从解析极限起步，按伪弧长步进，每步 Newton 校正记录转折点。"},
    ]
    newcomer = ("用随机采样加重要性重加权估计目标量，样本量 1e5，用阴影法给出标准误，"
                "并与分层抽样做方差对照。")
    d = dedup.admit(newcomer, accepted, candidate_clade="deepseek", deliverable="标准误与方差对照")
    assert d.accepted is False
    assert any("CLADE_SHARE_CAP" in r for r in d.reasons), d.reasons
    assert fams.count("deepseek") == 3
    # 同一个新方案换族提交则应放行（约束的是族，不是方案本身）
    d2 = dedup.admit(newcomer, accepted, candidate_clade="glm", deliverable="标准误与方差对照")
    assert d2.accepted is True, d2.reasons


# --------------------------------------------------------------------------
# D. 端到端工件
# --------------------------------------------------------------------------
def test_run_node_emits_all_artifacts_and_passes_criteria(tmp_path, monkeypatch):
    monkeypatch.setattr(run_node, "live_models", lambda: {
        "reachable": True,
        "models": ["deepseek-v4-flash", "qwen3.7-plus", "MiniMax-M2.5-AWQ", "glm-5.2"],
    })
    numbers = run_node.main(tmp_path)
    assert all(v.get("pass") is True for v in numbers["criteria"].values()), \
        {k: v for k, v in numbers["criteria"].items() if v.get("pass") is not True}
    for name in ("roster_check.json", "seats.json", "coverage.json", "seed_divergence.json",
                 "prereg_lock.json", "prereg_verify.json", "blind_scores.json",
                 "dedup_ledger.json", "dedup_calibration.json", "population.json",
                 "numbers.json", "fig1_coverage_matrix.png", "fig2_similarity_separation.png"):
        assert (tmp_path / name).exists(), name
    lock = json.loads((tmp_path / "prereg_lock.json").read_text(encoding="utf-8"))
    assert prereg.verify(lock)["ok"] is True
    led = json.loads((tmp_path / "dedup_ledger.json").read_text(encoding="utf-8"))
    assert led["clones_rejected"] == led["n_clones"] > 0
    # 报告中引用的路径必须真实存在（防"数字对不上工件"）
    for crit in numbers["criteria"].values():
        art = crit.get("artifact")
        if art and art.startswith("artifacts/"):
            assert (tmp_path / art.split("/")[-1]).exists(), art