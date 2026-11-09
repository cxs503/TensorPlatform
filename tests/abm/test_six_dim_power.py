"""tools/abm/six_dim_power.py 的 pytest 用例（零领域语义：全部用合成数据）。

覆盖：
* 配对键/单位/source 纪律（结构）
* 效应量方向（accuracy/efficiency/reproducibility 是"越小越好"）
* 符号翻转置换检验：大效应可检出、零效应 p 近似均匀、小 n 的离散化事实
* Holm 与文献算例一致；矩阵版与标量版一致
* 六维族判决端到端（2 维真有优势 → pass）
* MDE 解析值 = 2.802σ/√n；数值反解与解析同量级且单调
* A/A 空对照误报率不超名义值太多；功效正控在 d=0.5/n=20 下可检出
* 回溯性功效三态（可判 / 需n=N / 不可判）
* CLI 端到端落 JSON

全部断言只用合成数字，不出现任何学科词条。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.abm import six_dim_power as S  # noqa: E402


# --------------------------------------------------------------------- 结构/编码


def test_dimensions_and_directions_are_complete():
    assert set(S.DIMENSIONS) == set(S.DIMENSION_DIRECTION) == set(S.DIMENSION_NOTE)
    assert len(S.DIMENSIONS) == 6
    # 三个"越小越好"的度量必须编码为 -1，否则效应方向会整体反号
    for dim in ("accuracy", "efficiency", "reproducibility"):
        assert S.DIMENSION_DIRECTION[dim] == -1
    for dim in ("correctness", "stability", "applicability"):
        assert S.DIMENSION_DIRECTION[dim] == +1


def _rec(**kw):
    base = {"lineage": "L1", "seat": "S1", "arm": "control", "dimension": "accuracy",
            "value": 1.0, "unit": "u", "source": "artifact.json#row1"}
    base.update(kw)
    return base


def test_build_pairs_requires_sources_units_and_both_arms():
    records = [
        _rec(arm="control", value=1.0),
        _rec(arm="treatment", value=0.5),
        _rec(seat="S2", arm="control", value=1.0),                       # 缺 treatment → 不成对
        _rec(seat="S3", arm="control", value=1.0, source=""),            # 缺 source → 不入账
        _rec(seat="S3", arm="treatment", value=0.5),
        _rec(seat="S4", arm="control", value=1.0, unit="u"),
        _rec(seat="S4", arm="treatment", value=0.5, unit="other_unit"),  # 单位不一致
        _rec(seat="S5", arm="control", dimension="bogus", value=1.0),    # 未知维度
    ]
    folded = S.build_pairs(records)
    assert folded["n_pairs_by_dim"]["accuracy"] == 1
    pairs = folded["pairs"]["accuracy"]
    assert pairs[0]["seat"] == "S1" and pairs[0]["v_control"] == 1.0 and pairs[0]["v_treatment"] == 0.5
    assert any("缺 source" in e for e in folded["errors"])
    assert any("单位不一致" in e for e in folded["errors"])
    assert any("未知维度" in e for e in folded["errors"])


def test_oriented_deltas_respect_direction():
    pairs = [{"lineage": "L", "seat": "S", "v_control": 6.0, "v_treatment": 3.0,
              "unit": "s", "sources": {}}]
    # efficiency：越小越好 → treatment 3s 优于 control 6s → 正的定向差
    assert S.oriented_deltas(pairs, "efficiency")[0] == pytest.approx(+3.0)
    # correctness：越大越好 → 同一对数给负的定向差
    assert S.oriented_deltas(pairs, "correctness")[0] == pytest.approx(-3.0)


# ---------------------------------------------------------------- 效应量与检验


def test_paired_effect_matches_hand_computation():
    d = np.array([0.5, 1.0, 1.5, 2.0])
    eff = S.paired_effect(d)
    assert eff["n"] == 4
    assert eff["mean_diff"] == pytest.approx(1.25)
    assert eff["median_diff"] == pytest.approx(1.25)
    assert eff["sd"] == pytest.approx(float(np.std(d, ddof=1)))
    assert eff["cohen_dz"] == pytest.approx(1.25 / float(np.std(d, ddof=1)))
    assert eff["ci95_mean"][0] < 1.25 < eff["ci95_mean"][1]
    # 退化输入
    assert S.paired_effect([])["n"] == 0
    assert S.paired_effect([1.0])["cohen_dz"] is None


def test_sign_flip_detects_large_effect():
    rng = np.random.default_rng(3)
    d = 2.0 + rng.standard_normal(20)
    res = S.sign_flip_permutation_test(d, n_perm=4000, seed=0, alternative="greater")
    assert res["p_value"] < 0.01
    assert 0.0 < res["p_value"] <= 1.0
    # 方向反了就不该显著
    res2 = S.sign_flip_permutation_test(-d, n_perm=4000, seed=0, alternative="greater")
    assert res2["p_value"] > 0.5


def test_sign_flip_null_p_values_are_roughly_uniform():
    rng = np.random.default_rng(11)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(2000, 20))
    D = rng.standard_normal((400, 20))  # 真实效应恒 0
    p = S._perm_pvalues_matrix(D, signs, "two-sided")
    assert 0.4 < p.mean() < 0.6            # 零假设下 p 的均值≈0.5
    assert 0.01 < np.mean(p <= 0.05) < 0.12


def test_sign_flip_small_n_discreteness_is_real():
    """等幅配对下 n=5 双侧可达最小 p=2/32=6.25%>5%（拒绝不了）；n=6 才到 3.1%。"""
    p5 = S.sign_flip_permutation_test(np.ones(5), n_perm=20000, seed=1)["p_value"]
    p6 = S.sign_flip_permutation_test(np.ones(6), n_perm=20000, seed=1)["p_value"]
    assert p5 > 0.05
    assert p6 <= 0.05
    assert S.MIN_TESTABLE_PAIRS >= 6


def test_sign_flip_requires_two_pairs():
    with pytest.raises(ValueError):
        S.sign_flip_permutation_test([1.0], n_perm=10)


# ---------------------------------------------------------------------- Holm


def test_holm_matches_textbook_example():
    # Holm(1979) m=5, α=.05 逐步下降：p=.01 → 5*.01=.05 <= α 拒；p=.02 → 4*.02=.08 > α 停
    res = S.holm_adjust({"a": 0.01, "b": 0.02, "c": 0.03, "d": 0.04, "e": 0.05}, alpha=0.05)
    assert res["reject"] == {"a": True, "b": False, "c": False, "d": False, "e": False}
    assert res["adjusted"]["a"] == pytest.approx(0.05)
    assert res["adjusted"]["b"] == pytest.approx(0.08)
    assert res["n_reject"] == 1
    # 逐步下降：首个失败之后的全部不拒绝
    # a: 3*1e-3=3e-3 拒；c(0.03): 2*0.03=0.06 > 0.05 → 停 → 之后的 b 也不拒
    res2 = S.holm_adjust({"a": 0.001, "b": 0.5, "c": 0.03}, alpha=0.05)
    assert res2["reject"] == {"a": True, "b": False, "c": False}


def test_holm_matrix_agrees_with_scalar():
    rng = np.random.default_rng(5)
    P = rng.uniform(0, 1, size=(6, len(S.DIMENSIONS)))
    adj, rej = S.holm_matrix(P, alpha=0.05)
    for i in range(P.shape[0]):
        scalar = S.holm_adjust(dict(zip(S.DIMENSIONS, P[i])), alpha=0.05)
        for j, dim in enumerate(S.DIMENSIONS):
            assert rej[i, j] == scalar["reject"][dim]
            assert adj[i, j] == pytest.approx(scalar["adjusted"][dim])


def test_holm_controls_family_error_under_null():
    """零假设下 6 维全无效应：FWER 应在 α 附近（200 族分辨率下不至于爆掉）。"""
    rng = np.random.default_rng(17)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(999, 20))
    P = np.empty((200, 6))
    for j in range(6):
        D = rng.standard_normal((200, 20))
        P[:, j] = S._perm_pvalues_matrix(D, signs, "two-sided")
    _, rej = S.holm_matrix(P, alpha=0.05)
    fwer = np.mean(rej.any(axis=1))
    assert fwer <= 0.10  # 名义 0.05，200 族抽样噪声 ~1.5%


# --------------------------------------------------------------- 六维族判决


def _synthetic_records(effects, n_pairs=20, seed=0, sigma=1.0):
    """按 {dim: 真实均值差} 生成配对记录（arm 间差 = effect*sigma）。"""
    rng = np.random.default_rng(seed)
    records = []
    for dim in S.DIMENSIONS:
        mu = effects.get(dim, 0.0) * sigma
        for i in range(n_pairs):
            base = rng.standard_normal() * sigma
            c = base
            t = base + mu + rng.standard_normal() * sigma * 0.5
            records.append({"lineage": "L", "seat": f"S{i}", "arm": "control",
                            "dimension": dim, "value": c, "unit": "u", "source": "synth"})
            records.append({"lineage": "L", "seat": f"S{i}", "arm": "treatment",
                            "dimension": dim, "value": t, "unit": "u", "source": "synth"})
    return records


def test_six_dim_advantage_flags_two_better_dims():
    recs = _synthetic_records({"correctness": 2.0, "efficiency": -2.0}, n_pairs=20, seed=1)
    res = S.six_dim_advantage(recs, n_perm=4000, seed=0, min_dims=2)
    assert res["n_significant_better"] >= 2
    assert res["decision_pass"] is True
    assert res["dimensions"]["correctness"]["direction"] == "treatment_better"
    assert res["dimensions"]["efficiency"]["direction"] == "treatment_better"
    # 零效应维的符号可能偶然为正（点估计不显著即可），但 Holm 后必须不显著
    assert res["dimensions"]["stability"]["significant_better"] is False
    assert res["dimensions"]["accuracy"]["significant_better"] is False


def test_six_dim_advantage_rejects_when_only_one_dim_better():
    recs = _synthetic_records({"correctness": 2.0}, n_pairs=20, seed=2)
    res = S.six_dim_advantage(recs, n_perm=4000, seed=0, min_dims=2)
    assert res["decision_pass"] is False
    assert res["n_significant_better"] < 2


def test_six_dim_advantage_reports_missing_data_instead_of_guessing():
    recs = [_rec(dimension="accuracy", arm="control", value=1.0),
            _rec(dimension="accuracy", arm="treatment", value=0.5)]
    res = S.six_dim_advantage(recs, n_perm=100)
    assert res["decision_pass"] is False
    assert res["dimensions"]["accuracy"]["p_perm"] is None       # n=1 → 不可检验
    assert res["n_pairs_by_dim"]["stability"] == 0


# ------------------------------------------------------------------- MDE(n)


def test_mde_analytic_matches_the_2_8_over_sqrt_n_rule():
    assert S.mde_coefficient(0.05, 0.8, two_sided=True) == pytest.approx(2.8016, abs=1e-3)
    coef = S.mde_coefficient(0.05, 0.8, two_sided=True)
    assert S.mde_analytic(20, 1.0) == pytest.approx(coef / np.sqrt(20), rel=1e-9)
    assert S.mde_analytic(4, 2.0) == pytest.approx(2.0 * coef / 2.0, rel=1e-9)
    assert S.mde_coefficient(0.05, 0.8, two_sided=False) == pytest.approx(2.4865, abs=1e-3)
    with pytest.raises(ValueError):
        S.mde_analytic(0, 1.0)


def test_mde_numeric_close_to_analytic_and_monotone():
    a = S.mde_numeric(20, 1.0, n_rep=200, n_perm=299, seed=0)
    b = S.mde_numeric(40, 1.0, n_rep=200, n_perm=299, seed=0)
    assert a["bracket_exhausted"] is False
    # 数值反解与解析近似同量级（小 n 处解析偏乐观，容许 25% 的单侧偏差）
    assert a["mde_numeric"] == pytest.approx(a["mde_analytic"], rel=0.25)
    assert b["mde_numeric"] < a["mde_numeric"]            # 单调下降
    assert b["achieved_power"] >= 0.6                     # 反解处功效确实到量级


def test_required_n_analytic_inverse():
    # d=0.5, 双侧 80% → n = (2.8016/0.5)^2 = 31.4 → 32
    assert S.required_n_analytic(0.5, 1.0) == 32
    assert S.required_n_analytic(0.0) is None


# ------------------------------------------------- 空对照 / 正控 / 族判决所需 n


def test_aa_null_control_false_positive_rate_near_nominal():
    res = S.aa_null_control(n_sims=60, n_pairs=20, n_perm=499, seed=0, min_dims=2,
                            stability_seeds=(0, 1))
    assert res["primary"]["n_families"] == 60
    assert res["pass_family_rate"] is True            # 族判决误报率 <= 0.05
    assert res["primary"]["family_ge_min_dims"]["hi"] <= 0.20   # 附 CI 上界
    assert res["pooled"]["n_families"] == 120
    assert set(res["pooled"]["per_dim"]) == set(S.DIMENSIONS)


def test_power_positive_control_detects_known_effect():
    res = S.power_positive_control(d=0.5, n_pairs=20, rho=0.7, n_sims=300, n_perm=499,
                                   alternative="greater", seed=0)
    assert 0.5 < res["rho_realized"] < 0.9
    assert res["dz_realized"] > 0.5
    assert res["detection_rate"] >= 0.60              # 正控应能检出（严格线在验收里 >=0.70）
    # rho=0（放弃配对收益）时功效更低 —— 方向必须如此
    res0 = S.power_positive_control(d=0.5, n_pairs=20, rho=0.0, n_sims=300, n_perm=499,
                                    alternative="greater", seed=0)
    assert res0["detection_rate"] < res["detection_rate"]


def test_family_decision_power_increases_with_n():
    res = S.family_decision_power_curve(effect_dz=1.0, n_grid=(10, 30), n_rep=120,
                                       n_perm=299, seed=0)
    powers = [r["power_ge_min_dims"] for r in res["rows"]]
    assert powers[1] > powers[0]
    req = S.required_n_for_family_decision(effect_dz=1.0, n_grid=(10, 30), n_rep=120,
                                          n_perm=299, seed=0)
    assert req["required_n_grid_hit"] in (10, 30)


# --------------------------------------------------------------- 回溯性功效


def _retro_spec():
    entries = []
    # (1) n=1 且控制臂非零 → 有 σ 代理时给"需n=N"
    entries += [
        {"lineage": "C1", "seat": "a", "arm": "control", "dimension": "efficiency",
         "value": 10.0, "unit": "s", "source": "x#1"},
        {"lineage": "C1", "seat": "a", "arm": "treatment", "dimension": "efficiency",
         "value": 5.0, "unit": "s", "source": "x#2"},
    ]
    # (2) 控制臂 = 0 → 无相对尺度 → 不可判
    entries += [
        {"lineage": "C1", "seat": "b", "arm": "control", "dimension": "correctness",
         "value": 0.0, "unit": "count", "source": "x#3"},
        {"lineage": "C1", "seat": "b", "arm": "treatment", "dimension": "correctness",
         "value": 1.0, "unit": "count", "source": "x#4"},
    ]
    # (3) n=12 ≥ 下限 → 可判
    rng = np.random.default_rng(4)
    for i in range(12):
        base = rng.standard_normal()
        entries += [
            {"lineage": "C1", "seat": f"c{i}", "arm": "control", "dimension": "accuracy",
             "value": base, "unit": "rel", "source": "x#5"},
            {"lineage": "C1", "seat": f"c{i}", "arm": "treatment", "dimension": "accuracy",
             "value": base - 1.0, "unit": "rel", "source": "x#6"},
        ]
    return {"campaigns": [{"id": "SYN", "control_arm": "control", "treatment_arm": "treatment",
                           "sigma_rel_proxy": {"value": 0.10, "source": "synth"},
                           "min_dims": 2, "entries": entries}]}


def test_retrospective_power_three_states():
    out = S.retrospective_bundle(_retro_spec(), n_perm=2000, seed=0)
    assert out["n_campaigns"] == 1
    camp = out["campaigns"]["SYN"]
    dims = camp["dimensions"]
    # accuracy：n=12 可判，且实验组（误差更小）更优
    assert dims["accuracy"]["state_code"] == "DECIDABLE"
    assert dims["accuracy"]["state"] == "可判"
    assert dims["accuracy"]["favorable"] is True
    assert dims["accuracy"]["p_perm"] < 0.05
    # efficiency：n=1 + σ 代理 → 需 n=N（明确数字）
    assert dims["efficiency"]["state_code"] == "NEEDS_N"
    assert dims["efficiency"]["state"].startswith("需n=")
    assert dims["efficiency"]["required_n"] >= S.MIN_TESTABLE_PAIRS
    assert dims["efficiency"]["required_n"] == 9   # 相对效应 0.5 巨大 → 落到设计下限
    # correctness：控制臂 0 → 不可判
    assert dims["correctness"]["state"] == "不可判"
    assert dims["correctness"]["required_n"] is None
    # stability/applicability：无数据
    assert dims["stability"]["state"] == "不可判"
    # σ 敏感性表必须给（跨量转移假设不能单独背结论）
    assert len(dims["accuracy"]["sigma_sensitivity"]) == 4
    # 族层：accuracy 已可判且有利，但只 1 维 → 需 n
    assert camp["family"]["state_code"] in ("NEEDS_N", "DECIDABLE")
    assert camp["family"]["n_decidable"] == 1
    assert out["signature"]


def test_retrospective_without_sigma_proxy_is_undecidable():
    spec = _retro_spec()
    spec["campaigns"][0]["sigma_rel_proxy"] = None
    out = S.retrospective_bundle(spec, n_perm=500, seed=0)
    camp = out["campaigns"]["SYN"]
    assert camp["dimensions"]["efficiency"]["state"] == "不可判"
    assert camp["dimensions"]["efficiency"]["required_n"] is None
    assert camp["family"]["state"] in ("不可判", "需n=9")  # 顶层三态之一，不得含糊


def test_binomial_ci_reports_bounds():
    ci = S.binom_ci(3, 100)
    assert ci["p_hat"] == pytest.approx(0.03)
    assert ci["lo"] < 0.03 < ci["hi"]
    assert ci["method"] in ("clopper-pearson", "wilson")
    assert S.binom_ci(0, 0)["method"] == "n/a"


# -------------------------------------------------------------------- CLI 端到端


def test_cli_simulate_and_retro_write_json(tmp_path):
    cfg = {"aa": {"n_sims": 10, "n_pairs": 10, "n_perm": 199, "stability_seeds": [0]},
           "fwer": {"n_families": 10, "n_pairs": 10, "n_perm": 199, "extended_families": 20},
           "power_positive": {"d": 0.5, "n_pairs": 20, "rho": 0.7, "n_sims": 40, "n_perm": 199},
           "power_vs_rho": {"d": 0.5, "n_pairs": 20, "rho_grid": [0.0, 0.7], "n_sims": 40,
                            "n_perm": 199},
           "mde": {"n_grid": [20, 40], "n_rep": 60, "n_perm": 199, "deviation_check_from": 20},
           "family_required_n": {"effect_dz": 1.0, "n_grid": [10, 30], "n_rep": 60,
                                 "n_perm": 199}}
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_retro_spec()), encoding="utf-8")
    outdir = tmp_path / "out"
    rc = S.main(["all", "--outdir", str(outdir), "--spec", str(spec_path),
                 "--config", str(cfg_path), "--no-figures"])
    assert rc == 0
    acc = json.loads((outdir / "acceptance.json").read_text(encoding="utf-8"))
    assert {c["id"] for c in acc["criteria"]} == {
        "a_aa_null_control", "b_power_positive_control", "c_fwer_null_families", "d_mde_curve"}
    retro = json.loads((outdir / "retro_p12.json").read_text(encoding="utf-8"))
    assert retro["n_campaigns"] == 1
    assert retro["spec_path"] == str(spec_path)

# ------------------------------------------------- 与 acceptance.evaluate 的接线


def test_acceptance_wires_power_gate_as_optional_criterion(tmp_path):
    from tools.abm import acceptance as A

    recs = _synthetic_records({"correctness": 2.0, "efficiency": -2.0}, n_pairs=20, seed=1)
    gate = S.six_dim_advantage(recs, n_perm=3000, seed=0, min_dims=2)

    ledger = A.TraceLedger(str(tmp_path / "acc.jsonl"))
    seq = A.GateSequence()
    blind = A.BlindRegistry(ledger=ledger, ts="T")
    dim_table = {"arms": {}, "cells": [], "dimensions": list(S.DIMENSIONS),
                 "n_numeric_by_dim": {d: 0 for d in S.DIMENSIONS}, "errors": []}
    base = A.evaluate(ledger=ledger, gate_sequence=seq, blind=blind, dim_table=dim_table)
    assert all(c["id"] != "E1_six_dim_advantage_statistical" for c in base["criteria"])
    with_power = A.evaluate(ledger=ledger, gate_sequence=seq, blind=blind, dim_table=dim_table,
                            power_gate=gate)
    e1 = [c for c in with_power["criteria"] if c["id"] == "E1_six_dim_advantage_statistical"]
    assert len(e1) == 1
    assert e1[0]["pass"] is True
    assert e1[0]["observed"]["n_significant_better"] >= 2
    assert "correctness" in e1[0]["observed"]["per_dim"]


def test_analytic_power_is_finite_for_huge_effects():
    """大效应（ncp 远超双精度可表示范围）不得返回 nan —— nct 下溢要退化为正态近似。"""
    for dz in (0.5, 5.0, 15.0, 100.0):
        for two_sided in (True, False):
            p = S._analytic_power(dz, 9, 0.05, two_sided)
            assert p is not None and 0.0 <= p <= 1.0
    assert S._analytic_power(15.0, 9, 0.05, True) == pytest.approx(1.0, abs=1e-6)
    assert S._analytic_power(0.0, 9, 0.05, True) == pytest.approx(0.05, abs=0.02)


def test_mde_numeric_reproduces_exact_t_reference_within_3pct():
    """数值反解应当贴近精确非中心 t 的 MDE（这是"引擎对不对"的基准，不是 2.8 近似）。"""
    ref20 = S.mde_noncentral_t(20, 1.0)
    assert ref20 is not None and ref20 > S.mde_analytic(20, 1.0)   # 精确解 > 正态近似
    num = S.mde_numeric(20, 1.0, n_rep=400, n_perm=499, seed=0, max_iter=14)
    assert abs(num["mde_numeric"] - ref20) / ref20 <= 0.06
    # 精确 t 参考在小 n 处与 2.802 近似的差距 >10%（这就是判据只声明 n>=20 的原因）
    assert abs(S.mde_noncentral_t(10, 1.0) - S.mde_analytic(10, 1.0)) / S.mde_analytic(10, 1.0) > 0.10
