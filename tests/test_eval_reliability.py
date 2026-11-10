"""判级可靠性模块（IN55W3-N07）单测。

证伪优先：每个系数先钉**解析可验证性质**（与 Scott π / Fleiss κ / AC1 的退化
关系、界限、缺格口径），再钉生成模型（总体极限 vs 大样本）、决策门语义
（WITHHELD ≠ PASS、单审不可救）、以及端到端工件（JSON + 论文级 Markdown）。
不用真实台账的不确定性去掩盖管线 bug——真实数据只在 CLI 里跑。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eval_reliability import (  # noqa: E402
    acceptance, dataset, gate, report, run as cli, stats, synthetic,
)


# ─────────────────────────────── 夹具 ───────────────────────────────

def pairwise_matrix(n=600, flip=0.2, seed=0, k=3):
    """两判级者，按 ``flip`` 概率随机改判（类别均匀）。"""
    rng = np.random.default_rng(seed)
    a = rng.integers(1, k + 1, size=n)
    b = a.copy()
    m = rng.random(n) < flip
    b[m] = rng.integers(1, k + 1, size=int(m.sum()))
    return np.stack([a, b], axis=1).astype(float)


def adjacent_disagreement_matrix(n=400, seed=1):
    """只在**相邻等级**上分歧（序数度量应比名义度量更宽松）。"""
    rng = np.random.default_rng(seed)
    a = rng.integers(2, 5, size=n)          # 2..4，保证 ±1 仍在 1..5 内
    step = rng.choice([-1, 0, 1], size=n)
    return np.stack([a, a + step], axis=1).astype(float)


# ─────────────────────── Krippendorff α 的解析性质 ───────────────────────

def test_alpha_perfect_agreement_is_one():
    m = np.array([[1, 1], [2, 2], [3, 3], [1, 1], [2, 2]], dtype=float)
    res = stats.krippendorff_alpha(m, "ordinal")
    assert res["status"] == "ok"
    assert res["alpha"] == pytest.approx(1.0, abs=1e-12)
    assert res["n"] == 10  # 5 单元 × 2 判级者


def test_alpha_nominal_converges_to_scott_pi():
    """两判级者名义 α 在大样本下 → Scott 的 π（差别只来自 n/(n−1) 修正）。"""
    m = pairwise_matrix(n=4000, flip=0.25, seed=3)
    a = stats.krippendorff_alpha(m, "nominal")["alpha"]
    pi = stats.scott_pi(m)
    assert abs(a - pi) < 0.01
    # α 的 D_e 分母是 n(n−1)，故 α 严格不小于 π（(n−1)/n 修正使扣除项变小）；
    # 差别随 n 消失，这就是「大样本下 α = Scott π」的精确含义。
    assert a >= pi - 1e-12


def test_alpha_single_rater_units_do_not_inflate_n():
    """只有 1 个判级值的单元不进巧合矩阵（n 不被算大）。"""
    m = np.array([[1, np.nan], [2, np.nan], [1, 1], [2, 2]], dtype=float)
    res = stats.krippendorff_alpha(m, "ordinal")
    assert res["n"] == 4                    # 只有后两个单元可配对
    assert res["n_units"] == 2
    assert res["alpha"] == pytest.approx(1.0)


def test_alpha_insufficient_returns_nan_not_zero():
    """"样本不足"必须给 nan（0.0 会被误读成「一致性实测为零」）。"""
    m = np.array([[1, np.nan], [2, np.nan]], dtype=float)
    res = stats.krippendorff_alpha(m)
    assert res["status"] == "insufficient"
    assert math.isnan(res["alpha"])


def test_ordinal_alpha_more_lenient_than_nominal_on_adjacent_disagreement():
    """分歧只发生在相邻档时，序数 α > 名义 α（部分学分生效）。"""
    m = adjacent_disagreement_matrix()
    a_ord = stats.krippendorff_alpha(m, "ordinal")["alpha"]
    a_nom = stats.krippendorff_alpha(m, "nominal")["alpha"]
    assert a_ord > a_nom


def test_alpha_permutation_invariance_of_raters():
    """判级者列交换不改变 α（对称性）。"""
    m = pairwise_matrix(n=200, flip=0.3, seed=5)
    a1 = stats.krippendorff_alpha(m, "ordinal")["alpha"]
    a2 = stats.krippendorff_alpha(m[:, ::-1], "ordinal")["alpha"]
    assert a1 == pytest.approx(a2, abs=1e-12)


# ───────────────────────── 名义 κ / Gwet AC 退化关系 ─────────────────────────

def test_fleiss_kappa_equals_scott_pi_for_two_raters():
    m = pairwise_matrix(n=800, flip=0.2, seed=7)
    k = stats.fleiss_kappa(m)["kappa"]
    assert k == pytest.approx(stats.scott_pi(m), abs=1e-12)


def test_ac2_equals_ac1_for_binary_scale():
    """K=2 时线性权重退化为 0/1 → AC2 恒等于 AC1。"""
    rng = np.random.default_rng(11)
    a = rng.integers(1, 3, size=120)
    b = a.copy()
    flip = rng.random(120) < 0.3
    b[flip] = rng.integers(1, 3, size=int(flip.sum()))
    m = np.stack([a, b], axis=1).astype(float)
    assert stats.gwet_ac2(m)["ac2"] == pytest.approx(stats.gwet_ac1(m)["ac1"], abs=1e-12)


def test_gwet_ac_beats_kappa_under_skewed_marginals():
    """偏斜边际 + 高一致率是 κ 的经典病态；AC1 不应塌到 0 附近。"""
    m = np.array([[1, 1]] * 40 + [[2, 2]] * 6 + [[1, 2]] * 4, dtype=float)
    kap = stats.fleiss_kappa(m)["kappa"]
    ac1 = stats.gwet_ac1(m)["ac1"]
    assert ac1 > kap          # 同一数据下 AC1 更贴近「几乎全一致」的直觉
    assert ac1 > 0.5


def test_coefficients_reject_degenerate_input():
    with pytest.raises(ValueError):
        stats.fleiss_kappa(np.array([[1.0, np.nan]]))
    with pytest.raises(ValueError):
        stats.gwet_ac1(np.array([[1.0]]))


# ─────────────────────────────── ICC ───────────────────────────────

def test_icc_ordering_and_bounds():
    rng = np.random.default_rng(13)
    eff = rng.normal(0, 1.0, size=80)
    m = eff[:, None] + rng.normal(0, 0.5, size=(80, 4))
    res = stats.icc(m)
    assert res["status"] == "ok"
    assert 0.0 < res["icc_2_1"] <= res["icc_2_k"] <= 1.0
    assert res["icc_3_1"] >= res["icc_2_1"] - 1e-12   # 绝对一致 ≤ 一致性口径


def test_icc_insufficient_and_null_pvalue():
    assert stats.icc(np.array([[1.0, np.nan], [2.0, np.nan]]))["status"] == "insufficient"
    rng = np.random.default_rng(17)
    noise = rng.normal(0, 1.0, size=(40, 3))       # 无组间效应 → p 应偏大
    p = stats.icc(noise)["p_value"]
    assert (math.isnan(p)) or p > 0.001


# ───────────────────────── bootstrap CI 的性质 ─────────────────────────

def test_bootstrap_ci_brackets_point_and_scales_with_sd():
    rng = np.random.default_rng(19)
    x = synthetic.simulate_sample(rng, 150, sigma=0.6)
    ci = stats.bootstrap_alpha_ci(x, n_boot=800)
    assert ci["status"] == "ok"
    assert ci["lo"] <= ci["point"] <= ci["hi"]
    # 95% 正态区间半宽 ≈ 1.96·SD（BC 只平移不缩放）
    assert 1.4 <= ci["half_width"] / max(ci["sd"], 1e-9) <= 3.0
    assert ci["n_valid"] > 700


def test_bootstrap_ci_is_reproducible_with_seed():
    rng = np.random.default_rng(23)
    x = synthetic.simulate_sample(rng, 80, sigma=0.8)
    a = stats.bootstrap_alpha_ci(x, n_boot=300, seed=99)
    b = stats.bootstrap_alpha_ci(x, n_boot=300, seed=99)
    assert (a["lo"], a["hi"]) == (b["lo"], b["hi"])


# ─────────────────── 生成模型：总体极限与样本一致 ───────────────────

@pytest.mark.parametrize("target", [0.2, 0.5, 0.8])
def test_population_alpha_calibration_hits_target(target):
    cal = synthetic.calibrate_sigma(target, biases=(0.0,) * 4)
    assert cal["reached"]
    assert cal["achieved_alpha"] == pytest.approx(target, abs=1e-6)


def test_population_alpha_matches_large_sample():
    """总体极限 α 必须与 N=5000 的样本 α 对齐（生成模型自洽）。"""
    rng = np.random.default_rng(29)
    cal = synthetic.calibrate_sigma(0.5, biases=(0.0,) * 4)
    big = synthetic.simulate_sample(rng, 5000, sigma=cal["sigma"])
    assert stats.krippendorff_alpha(big, "ordinal")["alpha"] == pytest.approx(0.5, abs=0.02)


def test_rating_probs_sums_to_one_and_respects_bias_direction():
    p_neutral = synthetic.rating_probs(3, 0.0, 0.8)
    p_lenient = synthetic.rating_probs(3, 0.5, 0.8)
    assert p_neutral.sum() == pytest.approx(1.0)
    assert p_lenient.sum() == pytest.approx(1.0)
    assert p_lenient[3] > p_neutral[3]      # 宽判级者更容易给高一档（索引 3 = 等级 4）


def test_injection_shift_matches_theory_within_10pct():
    res = synthetic.injection_shift(0.8, reps=60, n_units=150)
    assert res["status"] == "ok"
    assert res["delta_alpha_theory"] > 0            # 注入必然拉低 α
    assert res["relative_error"] <= 0.10


# ─────────────────────── 方差分解 φ_g 的恢复 ───────────────────────

def test_variance_components_recovers_phi_g():
    """已知 σ²_g 的合成世界里，φ_g 的平均估计应贴近真值。"""
    rng = np.random.default_rng(31)
    sig_g, sig_m, sig_e = 0.6, 1.0, 1.0
    truth = sig_g ** 2 / (sig_g ** 2 + sig_m ** 2 + sig_e ** 2)
    est = []
    for _ in range(6):
        g_eff = {f"g{j}": rng.normal(0, sig_g) for j in range(6)}
        rows = []
        for i in range(1800):
            g = f"g{rng.integers(0, 6)}"
            rows.append((i, g, g_eff[g] + rng.normal(0, sig_m) + rng.normal(0, sig_e)))
        est.append(stats.variance_components(rows)["phi_g"])
    assert abs(float(np.mean(est)) - truth) < 0.06
    assert all(0.0 <= v <= 1.0 for v in est)


def test_variance_components_identifiability_flag():
    """单评审级设计下 mission 分量不可识别，必须显式标注。"""
    rows = [(i, f"g{i % 5}", 3.0 + (i % 7) * 0.01) for i in range(200)]
    res = stats.variance_components(rows)
    assert res["status"] == "ok"
    assert res["n_multi_graded_missions"] == 0
    assert res["mission_component_identifiable"] is False


# ─────────────────────────── 决策门语义 ───────────────────────────

def test_thresholds_single_source():
    """门槛数字只允许一处定义（报告/门/判据必须同源）。"""
    assert gate.THRESHOLDS["alpha_gate"] == acceptance.THRESHOLDS["alpha_gate"]
    assert gate.THRESHOLDS["min_pairable_units"] == acceptance.THRESHOLDS["min_pairable_units"]
    assert gate.THRESHOLDS["max_unkeyed_share"] == acceptance.THRESHOLDS["max_unkeyed_share"]


def test_verdict_withheld_when_sample_insufficient():
    v = gate.alpha_verdict(0.9, n_pairable=12, unkeyed_share=0.02)
    assert v["status"] == gate.WITHHELD
    assert v["enforcement"] == "SUSPENDED"
    assert v["ledger_upgrade_allowed"] is False


def test_verdict_withheld_when_unkeyed_dominates():
    v = gate.alpha_verdict(0.9, n_pairable=200, unkeyed_share=0.8)
    assert v["status"] == gate.WITHHELD


def test_verdict_require_third_below_gate():
    v = gate.alpha_verdict(0.5, n_pairable=120, unkeyed_share=0.0)
    assert v["status"] == gate.REQUIRE_THIRD
    assert v["third_judge_required"] is True
    assert v["ledger_upgrade_allowed"] is False


def test_verdict_pass_above_gate():
    v = gate.alpha_verdict(0.8, n_pairable=120, unkeyed_share=0.0)
    assert v["status"] == gate.PASS
    assert v["ledger_upgrade_allowed"] is True


def test_single_rater_is_unreliable_even_when_alpha_passes():
    """α 再高也不能把「一个成果只有一个判级者」变成可复核（设计性缺陷）。"""
    v = gate.alpha_verdict(0.95, n_pairable=200, unkeyed_share=0.0)
    lab = gate.mission_label(1, v)
    assert lab["label"] == gate.UNRELIABLE
    assert lab["ledger_upgrade_allowed"] is False


def test_mission_labels_follow_gate_status():
    v_third = gate.alpha_verdict(0.4, n_pairable=100, unkeyed_share=0.0)
    assert gate.mission_label(2, v_third)["label"] == gate.NEEDS_THIRD
    assert gate.mission_label(3, v_third)["label"] == gate.ADMISSIBLE
    v_withheld = gate.alpha_verdict(float("nan"), n_pairable=3, unkeyed_share=0.0)
    assert gate.mission_label(2, v_withheld)["label"] == gate.PROVISIONAL


def test_gate_apply_counts_and_required_extra_evaluations():
    recs = [dataset.ReliabilityRecord(mission_id=str(i), grader=f"g{i % 3}",
                                      grade="S2", e_level="E2", status="confirmed")
            for i in range(60)]
    # 一个双评 mission：两个不同判级者 → bucket "pair"
    recs += [dataset.ReliabilityRecord(mission_id="m1", grader="g9", grade="S3",
                                       e_level="E2", status="confirmed"),
             dataset.ReliabilityRecord(mission_id="m1", grader="g8", grade="S4",
                                       e_level="E2", status="confirmed")]
    v = gate.alpha_verdict(0.5, n_pairable=1, unkeyed_share=0.0)
    applied = gate.apply(recs, v)
    assert applied["bucket_counts"]["pair"] == 1
    assert applied["to_reach_min_pairable"]["needed_extra_second_evaluations"] == 49
    text = gate.render_policy(v, applied)
    assert "禁作台账升账依据" in text


# ─────────────────────── 数据抽取 / 覆盖率（无 DB） ───────────────────────

def test_coverage_does_not_impute_unkeyed():
    rows = [
        {"evaluation_id": "1", "mission_id": "10", "reviewer": "a", "grade": "S2",
         "e_level": "E2", "status": "confirmed"},
        {"evaluation_id": "2", "mission_id": "10", "reviewer": "b", "grade": "S3",
         "e_level": "E1", "status": "confirmed"},
        {"evaluation_id": "3", "mission_id": "11", "reviewer": None, "grade": "S1",
         "e_level": "E2", "status": "confirmed"},
        {"evaluation_id": "4", "mission_id": "12", "reviewer": "a", "grade": "S-N",
         "e_level": "E2", "status": "confirmed"},
    ]
    recs = dataset.records_from_rows(rows)
    cov = dataset.coverage(recs)
    assert cov["n_records"] == 4
    assert cov["n_unkeyed"] == 1
    assert cov["unkeyed_share"] == pytest.approx(0.25)
    assert cov["n_pairable_missions"] == 1          # 只有 mission 10
    assert cov["n_off_scale"] == 1                  # S-N 不入序数尺度


def test_to_matrix_takes_latest_rating_per_cell():
    rows = [
        {"mission_id": "1", "reviewer": "a", "grade": "S5", "status": "confirmed",
         "decided_at": "2026-01-01"},
        {"mission_id": "1", "reviewer": "a", "grade": "S2", "status": "confirmed",
         "decided_at": "2026-02-01"},
    ]
    mat, graders, units = dataset.to_matrix(dataset.records_from_rows(rows))
    assert mat[0][graders.index("a")] == 2.0        # 取最后一次（时间更晚）


def test_psql_guard_rejects_writes_and_multi_statements():
    with pytest.raises(ValueError):
        dataset.psql_rows("DELETE FROM outcome_evaluations")
    with pytest.raises(ValueError):
        dataset.psql_rows("SELECT 1; SELECT 2")


# ─────────────────────────── 端到端（离线回放） ───────────────────────────

def _synthetic_rows(n_missions=140, seed=41):
    """造一份有双评结构的台账行（离线回放，不依赖 DB）。"""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_missions):
        base = int(rng.integers(1, 6))
        for g in ("g1", "g2"):
            grade = int(np.clip(base + rng.choice([0, 0, 1, -1]), 1, 5))
            rows.append({"evaluation_id": f"{i}-{g}", "mission_id": str(i),
                         "reviewer": g, "grade": f"S{grade}", "e_level": "E2",
                         "status": "confirmed", "decided_at": "2026-11-01"})
    return rows


def test_end_to_end_artifact_and_markdown(tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(_synthetic_rows()))
    out = tmp_path / "artifact.json"
    doc = tmp_path / "report.md"
    result = cli.run(from_json=str(rows_file), reps=4, n_units=40, n_boot=120,
                     make_figures=False, out=out, fig_dir=tmp_path / "figs",
                     doc=doc)
    assert out.exists() and doc.exists()
    art = json.loads(out.read_text())
    assert art["coverage"]["n_pairable_missions"] == 140
    assert {c["id"] for c in art["criteria"]} == {"J1", "J2", "J3", "J4", "J5", "J6"}
    assert art["adjudication"]["overall"] in ("PASS", "FAIL", "IMPROVE", "WITHHELD")
    md = doc.read_text()
    for section in ("## ①", "## ②", "## ③", "## ④", "## ⑤", "## ⑥"):
        assert section in md
    assert "是否 <3%" in md
    # 报告里的数字必须来自工件（同一来源，不是手抄）
    assert f"{art['coverage']['n_pairable_missions']}" in md


def test_report_payload_shape(tmp_path):
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(_synthetic_rows(n_missions=60)))
    result = cli.run(from_json=str(rows_file), reps=2, n_units=30, n_boot=80,
                     make_figures=False, out=tmp_path / "a.json",
                     fig_dir=tmp_path / "figs", doc=tmp_path / "r.md")
    result["_markdown"] = report.build_markdown(result)
    payload = cli.report_payload(result)
    assert payload["mission_id"] == 10363
    assert payload["title"] and payload["summary"]
    assert len(payload["criteria"]) == 6
    assert isinstance(payload["figures"], list)