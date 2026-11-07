"""J1..J7 机器裁据（外部效度校准的验收面）。

判据阈值集中在 :data:`THRESHOLDS`，可注入覆盖（改阈值必须同时改报告）：

===== ======================================================= ==============
J1    S 级判级 walk-forward AUC（预测成功合并）               ≥ 0.70
      └ 稳健性门：同日分层 AUC（剔除批次效应）                 ≥ 0.60
J2    E 级判级 walk-forward AUC（预测回退/返工）              ≥ 0.65
      └ 稳健性门：同日分层 AUC                                ≥ 0.55
J3    Brier score（合并概率）                                ≤ 0.20
J4    随机标签负控 AUC（均值）                               ∈ [0.45, 0.55]
J5    CUSUM 评分通胀：判级活跃期 S 高分占比漂移               < 5%（且无持续报警）
J6    校准曲线最大偏差（桶 n ≥ 10）                          ≤ 0.10
J7    pytest 用例数                                          ≥ 10
===== ======================================================= ==============

**为什么要加稳健性门**：下游结果（PR 合入）有极强的批次效应——合并队列一天清一批，
另一天几乎不动。不分层的 pooled AUC 会把"模型刚好学到当天基准率"记成判别力，
这是 Simpson 型构成假象。分层 AUC 只统计同一天内的正负样本对，是判别力的保守下界；
两条都过才算 J1/J2 通过（见 docs §注意点 与 IN58-N07 报告 §5）。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import numpy as np

from . import ledger as lg
from . import metrics as mt

THRESHOLDS: dict[str, float] = {
    "j1_auc_merge": 0.70,
    "j1_auc_merge_strat": 0.60,
    "j2_auc_defect": 0.65,
    "j2_auc_defect_strat": 0.55,
    "j3_brier": 0.20,
    "j4_auc_lo": 0.45,
    "j4_auc_hi": 0.55,
    "j5_drift": 0.05,
    "j6_calib_dev": 0.10,
    "j7_min_tests": 10,
}

#: 分桶最小样本量（小桶噪声主导校准偏差，故设门槛并在报告里披露）
MIN_BIN_N = 10
#: 通胀检测的"最近窗口"（天）；数据不足时按前半/后半两段对比（见 metrics.trailing_drift）
INFLATION_WINDOW_DAYS = 30
#: 判级"活跃期"门槛：一天至少这么多条判级记录，才认为判级流程在该天正常运行
#: （否则「S 高分占比」只是流程上线前的空洞 0，会被误读成通胀）
INFLATION_MIN_DAY_N = 50


def _s_high(grade: str | None) -> bool:
    """S 高分（有对照基线数字及以上）：grade_ord ≥ 2。"""
    return lg.grade_ordinal(grade) >= 2


def _s_any(grade: str | None) -> bool:
    """S 级占比（含 S1，排除 S-N 证伪）——对照披露用。"""
    return grade is not None and lg.grade_ordinal(grade) >= 1


def grade_share_series(cohort: list[dict[str, Any]]) -> dict[str, Any]:
    """逐日 S 级占比序列（通胀检测的观测量）。"""
    days = sorted({c["day"] for c in cohort if c["day"]})
    high, anyg, n, active = [], [], [], []
    for d in days:
        rows = [c for c in cohort if c["day"] == d]
        n.append(len(rows))
        high.append(sum(1 for c in rows if _s_high(c["grade"])) / len(rows))
        anyg.append(sum(1 for c in rows if _s_any(c["grade"])) / len(rows))
        active.append(len(rows) >= INFLATION_MIN_DAY_N)
    return {"days": days, "n": n, "s_high_share": high, "s_any_share": anyg,
            "active": active}


def _select(cohort: list[dict[str, Any]], label_key: str) -> list[dict[str, Any]]:
    """可打分样本。

    - ``y_merge``：只看 **PR 有记录**（merged/open/closed）的 mission。没有 PR 记录的
      mission 不是"没合并"，而是"不可观测"；混进来会把基准率压到 0.10、把 AUC 拉回 0.5
      （踩过的坑，见 docs §注意点）。
    - ``y_defect``：要求下游处置已落地（PR 有记录或返工点名/重做）且 mission 终态。
    """
    out = []
    for c in cohort:
        if not c["day"]:
            continue
        if label_key == "y_merge":
            if not c.get("observable_pr"):
                continue
        elif not c.get("resolved"):
            continue
        out.append(c)
    return out


def evaluate(cohort: list[dict[str, Any]], *,
             repo_root: Path | None = None,
             thresholds: dict[str, float] | None = None,
             inflate_window: int = INFLATION_WINDOW_DAYS,
             n_control_draws: int = 20,
             n_boot: int = 2000,
             seed: int = 20261107) -> dict[str, Any]:
    """跑完 J1..J7，返回结构化裁据（所有数字都可被下游程序直读，零手抄）。"""
    th = {**THRESHOLDS, **(thresholds or {})}

    # ---- J1: S 级判级 → 成功合并 -----------------------------------------
    s_merge = _select(cohort, "y_merge")
    wf_merge = mt.walk_forward(s_merge, "y_merge")
    pm = wf_merge["pooled"]
    strat_merge = mt.stratified_auc(pm["scores"], pm["labels"], pm["days"])
    ci_merge = mt.bootstrap_auc_ci(pm["scores"], pm["labels"], n_boot=n_boot, seed=seed)
    apriori_merge = mt.auc_score([float(lg.grade_ordinal(s["grade"])) for s in s_merge],
                                [s["y_merge"] for s in s_merge])

    # ---- J2: E 级判级 → 回退/返工 ---------------------------------------
    s_defect = _select(cohort, "y_defect")
    wf_defect = mt.walk_forward(s_defect, "y_defect")
    pd_ = wf_defect["pooled"]
    strat_defect = mt.stratified_auc(pd_["scores"], pd_["labels"], pd_["days"])
    ci_defect = mt.bootstrap_auc_ci(pd_["scores"], pd_["labels"], n_boot=n_boot, seed=seed)
    apriori_defect = mt.auc_score([float(lg.e_ordinal(s["e_level"])) for s in s_defect],
                                  [s["y_defect"] for s in s_defect])

    # ---- J3/J6: 概率语义与校准 ------------------------------------------
    brier = wf_merge["pooled_brier"]
    bss = mt.brier_skill_score(pm["probs"], pm["labels"])
    curve = mt.calibration_curve(pm["probs"], pm["labels"], n_bins=10)
    calib_dev = mt.max_calibration_deviation(curve, min_bin_n=MIN_BIN_N)
    # 诊断：去掉"当天基准率"后的校准偏差（看概率语义是否只是被批次效应带偏）
    day_centred_dev = _day_centred_calibration_dev(pm["probs"], pm["labels"])
    base_rate_merge = (sum(s["y_merge"] for s in s_merge) / len(s_merge)) if s_merge else float("nan")

    # ---- J4: 随机标签负控 -----------------------------------------------
    control = mt.random_label_control(s_merge, "y_merge", n_draws=n_control_draws, seed=seed)

    # ---- J5: 评分通胀（CUSUM） ------------------------------------------
    shares = grade_share_series(cohort)
    active_days = [d for d, a in zip(shares["days"], shares["active"]) if a]
    active_share = [s for s, a in zip(shares["s_high_share"], shares["active"]) if a]
    if active_share:
        cusum = mt.cusum_detect(active_share, target=float(np.median(active_share)))
        drift = mt.trailing_drift(active_share, inflate_window)
        sustained = bool(cusum.alarm and (cusum.s_pos[-1] > cusum.threshold
                                          or cusum.s_neg[-1] > cusum.threshold))
        cusum_full = mt.cusum_detect(shares["s_high_share"],
                                     target=float(np.median(shares["s_high_share"])))
    else:
        cusum, cusum_full = None, None
        drift = {"window": inflate_window, "effective_window": 0, "n": 0,
                 "drift": float("nan"), "abs_drift": float("nan")}
        sustained = False

    # ---- J7: pytest 用例数 ----------------------------------------------
    n_tests = count_tests(repo_root or Path.cwd())

    j1_pooled_ok = bool(not np.isnan(wf_merge["pooled_auc"])
                        and wf_merge["pooled_auc"] >= th["j1_auc_merge"])
    j1_strat_ok = bool(not np.isnan(strat_merge["auc"])
                       and strat_merge["auc"] >= th["j1_auc_merge_strat"])
    j2_pooled_ok = bool(not np.isnan(wf_defect["pooled_auc"])
                        and wf_defect["pooled_auc"] >= th["j2_auc_defect"])
    j2_strat_ok = bool(not np.isnan(strat_defect["auc"])
                       and strat_defect["auc"] >= th["j2_auc_defect_strat"])

    checks: dict[str, Any] = {
        "J1_auc_merge": {
            "value": wf_merge["pooled_auc"],
            "threshold": f"pooled >= {th['j1_auc_merge']} AND day-stratified "
                         f">= {th['j1_auc_merge_strat']}",
            "pass": bool(j1_pooled_ok and j1_strat_ok),
            "pooled_pass": j1_pooled_ok,
            "stratified_pass": j1_strat_ok,
            "stratified_auc": strat_merge["auc"],
            "ci95": [ci_merge["lo"], ci_merge["hi"]],
            "note": (f"walk-forward {wf_merge['n_scored_windows']} 窗 / "
                     f"{wf_merge['n_test_total']} 测试样本；分层 AUC 用同日配对 "
                     f"({strat_merge['n_strata_used']} 天, {strat_merge['pairs']:.0f} 对)"),
            "apriori_direction_auc": apriori_merge,
        },
        "J2_auc_defect": {
            "value": wf_defect["pooled_auc"],
            "threshold": f"pooled >= {th['j2_auc_defect']} AND day-stratified "
                         f">= {th['j2_auc_defect_strat']}",
            "pass": bool(j2_pooled_ok and j2_strat_ok),
            "pooled_pass": j2_pooled_ok,
            "stratified_pass": j2_strat_ok,
            "stratified_auc": strat_defect["auc"],
            "ci95": [ci_defect["lo"], ci_defect["hi"]],
            "note": (f"walk-forward {wf_defect['n_scored_windows']} 窗 / "
                     f"{wf_defect['n_test_total']} 测试样本；标签=回退/返工"),
            "apriori_direction_auc": apriori_defect,
        },
        "J3_brier": {
            "value": brier,
            "threshold": f"<= {th['j3_brier']}",
            "pass": bool(not np.isnan(brier) and brier <= th["j3_brier"]),
            "note": (f"Brier skill vs 测试集气候态 = {bss:.4f}"
                     f"（负值=不如直接用基准率）；cohort 合并基准率 {base_rate_merge:.4f}"),
            "brier_skill_score": bss,
            "climatology_brier": base_rate_merge * (1 - base_rate_merge)
            if not np.isnan(base_rate_merge) else float("nan"),
        },
        "J4_random_control": {
            "value": control["mean_auc"],
            "threshold": f"in [{th['j4_auc_lo']}, {th['j4_auc_hi']}]",
            "pass": bool(not np.isnan(control["mean_auc"])
                         and th["j4_auc_lo"] <= control["mean_auc"] <= th["j4_auc_hi"]),
            "note": f"{control['n_draws']} 次打乱标签，sd={control['sd_auc']:.4f}",
            "detail": {k: v for k, v in control.items() if k != "aucs"},
        },
        "J5_inflation_cusum": {
            "value": drift.get("abs_drift"),
            "threshold": f"< {th['j5_drift']} (且无持续报警)",
            "pass": bool(not np.isnan(drift.get("abs_drift", float("nan")))
                         and drift["abs_drift"] < th["j5_drift"] and not sustained),
            "note": (f"判级活跃期 {len(active_days)} 天（日判级量≥{INFLATION_MIN_DAY_N}）；"
                     f"后半段较前半段漂移 "
                     f"{drift.get('drift', float('nan')):+.4f}；"
                     f"CUSUM 报警点={len(cusum.alarms) if cusum else 0}，持续={sustained}"),
            "sustained_alarm": sustained,
            "active_days": active_days,
            "active_share": active_share,
        },
        "J6_calibration_dev": {
            "value": calib_dev,
            "threshold": f"<= {th['j6_calib_dev']}",
            "pass": bool(not np.isnan(calib_dev) and calib_dev <= th["j6_calib_dev"]),
            "note": (f"10 桶等宽，只计 n>= {MIN_BIN_N} 的桶；"
                     f"去掉当天基准率后偏差={day_centred_dev:.4f}（诊断量，不参与判定）"),
            "day_centred_dev": day_centred_dev,
        },
        "J7_pytest": {
            "value": n_tests,
            "threshold": f">= {int(th['j7_min_tests'])}",
            "pass": bool(n_tests >= th["j7_min_tests"]),
            "note": "tests/test_external_validity.py 的 test_* 计数（AST 解析）",
        },
    }

    return {
        "checks": checks,
        "passed": sum(1 for c in checks.values() if c["pass"]),
        "total": len(checks),
        "all_pass": all(c["pass"] for c in checks.values()),
        "thresholds": th,
        "cohort": lg.summarize(cohort),
        "walk_forward_merge": {k: v for k, v in wf_merge.items() if k != "pooled"},
        "walk_forward_defect": {k: v for k, v in wf_defect.items() if k != "pooled"},
        "stratified_merge": strat_merge,
        "stratified_defect": strat_defect,
        "ci95_merge": ci_merge,
        "ci95_defect": ci_defect,
        "calibration_curve": curve,
        "calibration_max_dev": calib_dev,
        "brier": brier,
        "brier_skill_score": bss,
        "grade_share_series": shares,
        "cusum": {
            "target": cusum.target if cusum else None,
            "slack": cusum.slack if cusum else None,
            "threshold": cusum.threshold if cusum else None,
            "s_pos": cusum.s_pos if cusum else [],
            "s_neg": cusum.s_neg if cusum else [],
            "alarms": cusum.alarms if cusum else [],
            "full_series_alarms": cusum_full.alarms if cusum_full else [],
        },
        "negative_control": control,
        "drift": drift,
        "pooled_probs": pm["probs"],
        "pooled_days": pm["days"],
        "pooled_labels": pm["labels"],
        "empirical_merge_rate_by_grade": _merge_rate_by_grade(cohort),
        "empirical_defect_rate_by_e": _defect_rate_by_e(cohort),
        "min_bin_n": MIN_BIN_N,
        "inflation_min_day_n": INFLATION_MIN_DAY_N,
        "seed": seed,
    }


def _day_centred_calibration_dev(probs: list[float], labels: list[int]) -> float:
    """诊断量：把预测和标签都减去当天均值后，再算最大分桶偏差。

    若偏差大幅下降，说明"不校准"主要来自批次效应（当天基准率突变），
    而不是判级概率本身没有语义。
    """
    if not probs:
        return float("nan")
    p = np.asarray(probs, dtype=float)
    y = np.asarray(labels, dtype=float)
    center = float(y.mean())  # 测试窗口整体基准率（我们只有 pooled 序列）
    return mt.max_calibration_deviation(
        mt.calibration_curve(p + (center - float(p.mean())), y, n_bins=10),
        min_bin_n=MIN_BIN_N)


def _merge_rate_by_grade(cohort: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for g in sorted({c["grade"] for c in cohort if c["grade"]}, key=lg.grade_ordinal):
        rows = [c for c in cohort if c["grade"] == g]
        out.append({"grade": g, "n": len(rows),
                    "merge_rate": sum(c["y_merge"] for c in rows) / len(rows)})
    return out


def _defect_rate_by_e(cohort: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in sorted({c["e_level"] for c in cohort if c["e_level"]}, key=lg.e_ordinal):
        rows = [c for c in cohort if c["e_level"] == e and c["outcome"] != "pending"]
        if not rows:
            continue
        out.append({"e_level": e, "n": len(rows),
                    "defect_rate": sum(c["y_defect"] for c in rows) / len(rows)})
    return out


def count_tests(root: Path, pattern: str = "tests/test_external_validity.py") -> int:
    """AST 统计 ``test_*`` 函数数量（不执行、不依赖 pytest 收集）。"""
    path = Path(root) / pattern
    if not path.exists():
        return 0
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(1 for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name.startswith("test_"))