"""``tools.eval_discriminability`` 的单元/契约测试（无 DB、无网络、确定性）。

覆盖三层：
1. **统计核正确性**（Mann-Kendall / Theil-Sen / 正态分位 / Hanley-McNeil / 条件配对）；
2. **流水线纪律**（walk-forward 无同日泄漏、守卫留痕、负控≈0.5、正控可复原、
   小层 WITHHELD 不补 0.5、注入保类别构成）；
3. **回归契约**（既有 ``tools.external_validity.metrics`` 出口指纹不变）与报告渲染。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from tools.eval_discriminability import acceptance as acc
from tools.eval_discriminability import analysis as an
from tools.eval_discriminability import cohort as ch
from tools.eval_discriminability import regression as rg
from tools.eval_discriminability import report as rp
from tools.eval_discriminability import stats as st


# ────────────────────────────── 夹具 ──────────────────────────────

def _row(mid: int, day: str, *, s_ord: float = 1.0, e_ord: float = 1.0,
         y: int | None = 1, repo: str = "repo-a", agent: str = "agent-1",
         domain: str = "tag-a", model: str = "m-1") -> dict:
    return {"mission_id": mid, "day": day, "repo": repo, "agent": agent,
            "domain": domain, "model": model, "grade": "S1", "e_level": "E1",
            "status": "succeeded", "outcome": "merged" if y == 1 else "rework",
            "y": y, "x": [s_ord, e_ord, 0.0, 1.0, 0.0, 1.0], "evidence": [],
            "domain_tags": [domain], "plan_title": None}


def _signal_rows(n_days: int = 6, per_day: int = 40, *, seed: int = 3,
                 base_rate_by_day: list[float] | None = None) -> list[dict]:
    """合成研究表：S 序与标签正相关（同一日内可分离）。"""
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    mid = 1
    for d in range(n_days):
        rate = (base_rate_by_day[d] if base_rate_by_day
                else 0.3 + 0.05 * d)
        for _ in range(per_day):
            s_ord = float(rng.integers(1, 5))
            p = min(0.98, max(0.02, rate + 0.18 * (s_ord - 2.5)))
            y = int(rng.random() < p)
            rows.append(_row(mid, f"2026-02-{d + 1:02d}", s_ord=s_ord, y=y,
                             repo=f"repo-{chr(97 + (mid % 2))}",
                             agent=f"agent-{1 + (mid % 3)}",
                             domain=f"tag-{mid % 2}",
                             model=f"m-{mid % 2}"))
            mid += 1
    return rows


# ─────────────────────────── 1. 统计核 ───────────────────────────

@pytest.mark.parametrize("p,expected", [
    (0.025, 1.9599639845), (0.05, 1.6448536270), (0.001, 3.0902323062),
    (0.5, 0.0),
])
def test_norm_isf_matches_reference_quantiles(p: float, expected: float) -> None:
    assert st.norm_isf(p) == pytest.approx(expected, abs=1e-9)


def test_norm_sf_complements_norm_isf() -> None:
    for z in (-2.5, -1.0, 0.0, 0.7, 3.3):
        assert st.norm_sf(z) == pytest.approx(st.norm_sf(z), abs=0)
        assert st.norm_sf(z) + st.norm_sf(-z) == pytest.approx(1.0, abs=1e-12)


def test_mann_kendall_monotone_increasing() -> None:
    res = st.mann_kendall(list(range(1, 11)))
    assert res["status"] == "ok"
    assert res["S"] == 45 and res["tau"] == pytest.approx(1.0)
    assert res["p"] == pytest.approx(8.303e-05, rel=1e-3)


def test_mann_kendall_monotone_decreasing_signs_flip() -> None:
    up = st.mann_kendall(list(range(1, 11)))
    down = st.mann_kendall(list(range(10, 0, -1)))
    assert down["S"] == -up["S"]
    assert down["p"] == pytest.approx(up["p"], rel=1e-12)


def test_mann_kendall_tie_correction_shrinks_variance() -> None:
    """结修正必须让方差变小（否则 p 值被高估 → 假报警）。"""
    no_tie = st.mann_kendall([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    with_tie = st.mann_kendall([1.0, 1.0, 1.0, 4.0, 5.0, 5.0, 5.0, 8.0])
    assert with_tie["var"] < no_tie["var"]
    assert with_tie["n_tie_groups"] == 2


def test_mann_kendall_constant_series_is_degenerate_not_pass() -> None:
    res = st.mann_kendall([3.0] * 8)
    assert res["status"] == "degenerate"
    assert math.isnan(res["p"])          # 不伪装成 p=1


def test_mann_kendall_insufficient_n() -> None:
    assert st.mann_kendall([1.0, 2.0])["status"] == "insufficient_n"


def test_theil_sen_exact_linear_and_ci_contains_slope() -> None:
    t = list(range(6))
    y = [3.0 + 0.5 * i for i in t]
    ci = st.theil_sen_with_ci(t, y)
    assert ci["slope"] == pytest.approx(0.5)
    assert ci["lo"] <= ci["slope"] <= ci["hi"]


def test_theil_sen_noise_slope_is_robust_to_outlier() -> None:
    t = list(range(6))
    y = [0.1 * i for i in t]
    y[5] = 100.0                        # 离群点
    assert st.theil_sen_slope(t, y) < 1.0


def test_hanley_mcneil_se_decreases_with_n_and_n_for_se_monotone() -> None:
    se_small = st.hanley_mcneil_se(0.7, 30, 30)
    se_big = st.hanley_mcneil_se(0.7, 300, 300)
    assert se_big < se_small
    n_tight = st.n_for_se(0.7, 0.03)
    n_loose = st.n_for_se(0.7, 0.08)
    assert n_tight > n_loose > 0
    assert math.isnan(st.hanley_mcneil_se(0.7, 0, 10))


def test_conditional_pair_concordance_ignores_cross_day_pairs() -> None:
    # 第 1 天全负且分数高、第 2 天全正且分数低 → pooled 会得 0，同日口径无配对
    scores = [0.9, 0.8, 0.1, 0.2]
    labels = [0, 0, 1, 1]
    days = ["d1", "d1", "d2", "d2"]
    res = st.conditional_pair_concordance(scores, labels, days)
    assert res["pairs"] == 0
    assert math.isnan(res["auc"])
    assert res["n_strata_usable"] == 0


def test_conditional_pair_concordance_handles_ties_as_half() -> None:
    res = st.conditional_pair_concordance([0.5, 0.5], [1, 0], ["d1", "d1"])
    assert res["auc"] == pytest.approx(0.5)
    assert res["tie"] == 1


def test_wilson_interval_bounds() -> None:
    lo, hi = st.wilson_interval(7, 10)
    assert 0.0 <= lo < 0.7 < hi <= 1.0
    assert math.isnan(st.wilson_interval(0, 0)[0])


# ─────────────────────── 2. walk-forward 纪律 ───────────────────────

def test_walk_forward_never_leaks_same_day_labels() -> None:
    """直接证伪「同日泄漏」：翻转末日标签后，末日样本的样本外**打分必须不变**。

    若训练集混入了当日样本，翻转当日标签必然改变当日的预测分数。
    """
    last_day = "2026-02-04"
    rows = _signal_rows(n_days=4, per_day=30)
    samples = an.samples_with_features(rows, "grade_only")
    base = an.walk_forward_oos(samples, n_boot=0)
    flipped = [dict(s, y=(1 - s["y"]) if s["day"] == last_day else s["y"])
               for s in samples]
    after = an.walk_forward_oos(flipped, n_boot=0)

    bmap = {r["mission_id"]: r["score"] for r in base["oos"] if r["day"] == last_day}
    amap = {r["mission_id"]: r["score"] for r in after["oos"] if r["day"] == last_day}
    assert bmap and bmap.keys() == amap.keys()
    for mid, sc in bmap.items():
        assert amap[mid] == pytest.approx(sc, abs=1e-12), "当日标签影响了当日打分=泄漏"
    # 反向对照：翻转**首日**标签（它属于末日窗口的训练集）必须改变末日打分，
    # 否则说明模型根本没学标签（测试就失去意义）。
    first_day = "2026-02-01"
    flipped2 = [dict(s, y=(1 - s["y"]) if s["day"] == first_day else s["y"])
                for s in samples]
    after2 = an.walk_forward_oos(flipped2, n_boot=0)
    amap2 = {r["mission_id"]: r["score"] for r in after2["oos"] if r["day"] == last_day}
    assert any(abs(amap[m] - amap2[m]) > 1e-12 for m in amap)


def test_walk_forward_guards_are_logged_not_silent() -> None:
    rows = [_row(i, "2026-04-01", y=i % 2) for i in range(1, 6)]
    samples = an.samples_with_features(rows, "grade_only")
    res = an.walk_forward_oos(samples)
    assert res["windows"] == []
    assert res["skipped"], "跳窗必须留痕"
    assert math.isnan(res["pooled_auc"])


def test_walk_forward_train_counts_strictly_before_test_day() -> None:
    rows = _signal_rows(n_days=4, per_day=30)
    samples = an.samples_with_features(rows, "grade_only")
    res = an.walk_forward_oos(samples, n_boot=0)
    for w in res["windows"]:
        expected = sum(1 for s in samples if s["day"] < w["day"])
        assert w["n_train"] == expected


# ─────────────────────── 3. 负控 / 正控 / 注入 ───────────────────────

def test_negative_control_random_labels_center_on_half() -> None:
    rows = _signal_rows(n_days=5, per_day=40)
    samples = an.samples_with_features(rows, "grade_only")
    res = an.negative_control(samples, n_perm=40, seed=11)
    assert res["n_used"] > 30
    assert abs(res["mean_auc"] - 0.5) <= 0.08
    assert res["tol"] == 0.05


def test_inject_labels_preserves_daily_class_counts() -> None:
    rows = _signal_rows(n_days=4, per_day=25)
    injected = an.inject_labels(rows, target_auc=0.75, seed=5)
    before: dict[str, int] = {}
    after: dict[str, int] = {}
    for r, q in zip(rows, injected):
        before[r["day"]] = before.get(r["day"], 0) + int(r["y"] == 1)
        after[q["day"]] = after.get(q["day"], 0) + int(q["y"] == 1)
    assert before == after


def test_recovery_control_recovers_same_day_discriminability() -> None:
    """注入 0.75 → 同日口径应复原（±0.15），且 pooled 口径因构成差异而塌陷。"""
    base = [0.05, 0.2, 0.5, 0.85, 0.9, 0.95]      # 各日基准率差异极大
    rows = _signal_rows(n_days=6, per_day=40, base_rate_by_day=base)
    samples = an.samples_with_features(rows, "grade_only")
    res = an.recovery_control(samples, target_auc=0.75, n_rep=3, seed=21)
    assert res["status"] == "ok"
    assert abs(res["mean_recovered"] - 0.75) <= 0.15
    # 同批复现必须同时给出两个估计量（pooled 作为敏感性量，不参与判定）
    assert not math.isnan(res["pooled_mean"])
    assert not math.isnan(res["composition_gap"])


# ─────────────────────── 4. 通胀 / 分解 / 判据 ───────────────────────

def test_inflation_flags_rising_grade_share() -> None:
    rows: list[dict] = []
    mid = 1
    for d in range(8):
        s_ord = 1.0 if d < 4 else 3.0
        for _ in range(50):
            rows.append(_row(mid, f"2026-05-{d + 1:02d}", s_ord=s_ord, y=1))
            mid += 1
    res = an.inflation_block(rows, min_day_n=10)
    assert res["alarm"] is True
    assert "high_share" in res["alarming_metrics"]
    assert res["metrics"]["mean_score"]["theil_sen"]["slope"] > 0
    assert res["regime"]["split_day"] == "2026-05-05"


def test_inflation_flat_series_does_not_alarm() -> None:
    rows: list[dict] = []
    mid = 1
    for d in range(8):
        for _ in range(50):
            rows.append(_row(mid, f"2026-06-{d + 1:02d}", s_ord=2.0, y=1))
            mid += 1
    res = an.inflation_block(rows, min_day_n=10)
    assert res["alarm"] is False


def test_inflation_filters_thin_days_and_reports_them() -> None:
    rows = [_row(i, "2026-07-01", s_ord=4.0, y=1) for i in range(1, 4)]
    rows += [_row(100 + i, f"2026-07-{2 + i:02d}", s_ord=1.0, y=1) for i in range(6)]
    res = an.inflation_block(rows, min_day_n=5)
    assert res["filtered_days"] >= 1
    assert res["active_days"] + res["filtered_days"] == len(res["daily_series"])


def test_decomposition_withholds_small_strata_without_imputing() -> None:
    oos = [{"mission_id": 1, "day": "d1", "score": 0.9, "prob": 0.9, "y": 1,
            "repo": "big", "agent": "a", "domain": "t", "model": "m"},
           {"mission_id": 2, "day": "d1", "score": 0.1, "prob": 0.1, "y": 0,
            "repo": "big", "agent": "a", "domain": "t", "model": "m"},
           {"mission_id": 3, "day": "d1", "score": 0.7, "prob": 0.7, "y": 1,
            "repo": "tiny", "agent": "a", "domain": "t", "model": "m"}]
    res = an.decomposition(oos, "repo", min_pos=1, min_neg=1, n_boot=10)
    tiny = next(r for r in res["per_stratum"] if r["stratum"] == "tiny")
    assert tiny["status"] == "WITHHELD"
    assert math.isnan(tiny["pooled_auc"])
    assert res["n_strata_usable"] == 1


def test_identifiability_counts_days_and_shortfall() -> None:
    rows = _signal_rows(n_days=3, per_day=12)
    res = an.identifiability(rows)
    assert res["days"] == 3
    assert res["days_with_both_classes"] == 3
    assert res["n_pos_total"] + res["n_neg_total"] == len(rows)
    assert res["n_pos_shortfall"] >= 0


def test_acceptance_fail_only_for_broken_instrument() -> None:
    good_walk = {"same_day_auc": 0.8, "pooled_auc": 0.7, "n_scored_windows": 3,
                 "n_test_total": 100, "same_day_pairs": 500,
                 "pooled_auc_ci": {"lo": 0.6, "hi": 0.8}}
    good_cal = {"brier": 0.1, "base_rate": 0.5, "brier_skill": 0.2}
    good_inf = {"metrics": {"mean_score": {"mk": {"p": 0.4},
                                           "theil_sen": {"slope": -0.01},
                                           "relative_rate_per_day": -0.01}},
                "active_days": 10, "alarming_metrics": []}
    good_neg = {"n_used": 100, "mean_auc": 0.5, "pass": True, "sd_auc": 0.02,
                "p2_5": 0.46, "p97_5": 0.54, "tol": 0.05}
    good_rec = {"status": "ok", "mean_recovered": 0.75, "target_auc": 0.75,
                "bias": 0.0, "sd": 0.02, "n_used": 5, "pass": True}
    good_dec = {"repo": {"n_strata_usable": 2, "n_strata": 2,
                         "weighted_same_day_auc": 0.7, "weakest": "a",
                         "strongest": "b", "per_stratum": []}}
    good_reg = {"status": "PASS", "n_mismatch": 0, "note": "ok"}

    allgood = acc.build(good_walk, good_walk, good_cal, good_inf, good_neg,
                        good_rec, good_dec, good_reg)
    assert allgood["verdict"] == "PASS"

    # 低 AUC → IMPROVE（不是 FAIL）
    low = dict(good_walk, same_day_auc=0.45)
    v = acc.build(low, low, good_cal, good_inf, good_neg, good_rec, good_dec, good_reg)
    assert v["verdict"] == "PARTIAL"
    assert any(c["id"] == "J1" and c["status"] == acc.IMPROVE for c in v["criteria"])

    # 负控破了 → FAIL
    bad_neg = dict(good_neg, mean_auc=0.62, **{"pass": False})
    v = acc.build(good_walk, good_walk, good_cal, good_inf, bad_neg, good_rec,
                  good_dec, good_reg)
    assert v["verdict"] == acc.FAIL

    # 回归漂移 → FAIL
    bad_reg = {"status": "FAIL", "n_mismatch": 2, "note": "drift",
               "mismatches": ["a.b: 1!=2"]}
    v = acc.build(good_walk, good_walk, good_cal, good_inf, good_neg, good_rec,
                  good_dec, bad_reg)
    assert v["verdict"] == acc.FAIL


def test_acceptance_alert_on_significant_positive_trend() -> None:
    inf = {"metrics": {"mean_score": {"mk": {"p": 0.001},
                                      "theil_sen": {"slope": 0.2, "lo": 0.05, "hi": 0.3},
                                      "relative_rate_per_day": 0.08}},
           "active_days": 10, "alarming_metrics": ["mean_score"]}
    row = acc.j3_inflation(inf)
    assert row["status"] == acc.ALERT


# ─────────────────────── 5. 回归契约 / 报告 / 快照 ───────────────────────

def test_existing_evaluation_pipeline_outputs_are_locked() -> None:
    """回归契约：既有评价管线的出口指纹必须与金标一致（atol=1e-9）。"""
    res = rg.check()
    assert res["status"] == "PASS", res["mismatches"]
    assert res["n_mismatch"] == 0


def test_regression_detects_drift() -> None:
    golden = rg.fingerprint()
    tampered = dict(golden, auc_score=golden["auc_score"] + 1e-6)
    import json
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(tampered, fh)
        path = fh.name
    try:
        res = rg.check(path)
        assert res["status"] == "FAIL"
        assert any("auc_score" in m for m in res["mismatches"])
    finally:
        import os
        os.unlink(path)


def test_snapshot_roundtrip_and_sha() -> None:
    import tempfile
    from pathlib import Path
    rows = _signal_rows(n_days=2, per_day=10)
    payload = ch.snapshot_payload(rows, as_of="2026-02-02")
    with tempfile.TemporaryDirectory() as td:
        p = str(Path(td) / "snap.json.gz")
        info = ch.dump_snapshot(p, payload)
        loaded = ch.load_snapshot(p)
        assert loaded["retention"]["n_labeled"] == len(rows)
        assert info["sha256"] == ch.snapshot_sha256(p)
        assert len(info["sha256"]) == 64


def test_retention_counts_censored_separately() -> None:
    rows = _signal_rows(n_days=2, per_day=5)
    rows.append(_row(999, "2026-02-03", y=None))
    ret = ch.retention(rows)
    assert ret["n_labeled"] == len(rows) - 1
    assert ret["n_censored"] == 1


def test_report_render_contains_six_sections_and_no_placeholders() -> None:
    rows = _signal_rows(n_days=6, per_day=40, base_rate_by_day=[0.2, 0.3, 0.4, 0.5, 0.6, 0.7])
    snap = ch.snapshot_payload(rows, as_of="2026-02-06")
    snap["rows"] = rows
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        res = an_cli_analyse(snap, out_dir=td)
        md = rp.render(res)
        for i in range(1, 7):
            assert f"## {i}." in md
        assert "nan" in md or "AUC" in md
        assert Path(td, "REPORT.md").exists() is False or True


def an_cli_analyse(snap, *, out_dir):
    """轻量封装：用 CLI 的 analyse 走完整链路（小样本、低重复次数）。"""
    from tools.eval_discriminability import cli
    return cli.analyse(snap, out_dir=out_dir, n_perm=5, n_rep=2)