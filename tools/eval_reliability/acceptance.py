"""机器裁决：把恢复实验 / 覆盖率 / 功效 / 决策门折成可审计判据 J1..J6。

==========================  ====================================================  ==========================
判据                         含义                                                  阈值
==========================  ====================================================  ==========================
J1 α 真值恢复                3 档 α_true × 200 重复，|α̂−α_true| 的偏差与平均绝对误差   max|bias| ≤ 0.05
                                                                                  且 max MAE ≤ 0.05
J2 bootstrap CI 覆盖率        95% CI 是否罩住 α_true（同 3 档 × 200 重复）           覆盖率 ≥ 0.90
J3 ICC 功效 + 负控             n=100、3 判级者下检出 ICC=0.3 的功效；ICC=0 时假阳性率   功效 ≥ 0.80
                                                                                  且假阳性 ≤ 0.10
J4 真实数据可判性            n_pairable / UNKEYED 不足时**必须**报 WITHHELD，          结论与规则逐位一致
                             并给出「CI 半宽 ≤0.1 所需最小评审判数」表
J5 注入恢复                  注入 lenient/harsh（±0.5 级）后 Δα 与理论预测的偏差       相对误差 ≤ 0.10
J6 决策门工作特性            已知真值的世界里门是否给出正确动作（不误放也不误拦）      PASS 率 ≥ 0.90（α=0.8）
                                                                                  与 拦下率 ≥ 0.90（α=0.5）
==========================  ====================================================  ==========================

**状态语义**（与 ``tools.eval_discriminability.acceptance`` 同纪律）：
- ``PASS`` 判据达标；
- ``FAIL`` 仪器坏了（负控不成立、注入效应与理论不符）——需修实现；
- ``IMPROVE`` 仪器正常但数据/规模不够（样本不足导致的精度不足）；
- ``WITHHELD`` 无法判定（真实数据不具备可判性时 J4 就是这种状态，且这是**正确**
  的结论，不算失败）。
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np

from tools.eval_reliability import gate, stats, synthetic

PASS, FAIL, IMPROVE, WITHHELD = "PASS", "FAIL", "IMPROVE", "WITHHELD"

#: 判据阈值（单一契约源）
THRESHOLDS: dict[str, float] = {
    # 门参数与 gate.THRESHOLDS 必须逐值一致（单测 test_thresholds_single_source 钉住）
    "alpha_gate": 0.667,
    "min_pairable_units": 50,
    "max_unkeyed_share": 0.50,
    "alpha_bias_max": 0.05,
    "alpha_mae_max": 0.05,
    "ci_coverage_min": 0.90,
    "icc_power_min": 0.80,
    "icc_false_positive_max": 0.10,
    "injection_rel_err_max": 0.10,
    "gate_pass_rate_min": 0.90,
    "gate_block_rate_min": 0.90,
    "target_ci_halfwidth": 0.10,
}


def _row(cid: str, name: str, value: Any, threshold: str, status: str,
         note: str = "", evidence: Any = None) -> dict[str, Any]:
    return {"id": cid, "name": name, "value": value, "threshold": threshold,
            "status": status, "note": note, "evidence": evidence}


# ─────────────────── J1/J2：恢复 + 覆盖率（同一次实验出两判据） ───────────────────

def j1_alpha_recovery(recovery: Mapping[str, Any],
                      th: Mapping[str, float] | None = None) -> dict[str, Any]:
    t = {**THRESHOLDS, **(th or {})}
    rows = [r for r in recovery.get("rows", []) if r.get("status") == "ok"]
    if len(rows) < 3:
        return _row("J1", "α 真值恢复", None, f"max|bias|≤{t['alpha_bias_max']}",
                    WITHHELD, "恢复实验不足三档，不能判")
    bias = max(abs(r["bias"]) for r in rows)
    mae = max(r["mae"] for r in rows)
    status = PASS if (bias <= t["alpha_bias_max"] and mae <= t["alpha_mae_max"]) else FAIL
    return _row("J1", "α 真值恢复（3 档×N 重复）",
                {"max_abs_bias": bias, "max_mae": mae,
                 "per_level": [{k: r[k] for k in ("alpha_target", "alpha_true", "bias",
                                                  "mae", "sd_alpha_hat", "reps_used")}
                               for r in rows]},
                f"max|bias|≤{t['alpha_bias_max']} 且 max MAE≤{t['alpha_mae_max']}",
                status,
                "偏差是判定「系数实现是否无偏」的核心量；MAE 才是单次判级的典型误差。")


def j2_ci_coverage(recovery: Mapping[str, Any],
                   th: Mapping[str, float] | None = None) -> dict[str, Any]:
    t = {**THRESHOLDS, **(th or {})}
    rows = [r for r in recovery.get("rows", []) if r.get("status") == "ok"]
    if len(rows) < 3:
        return _row("J2", "bootstrap CI 覆盖率", None,
                    f"≥{t['ci_coverage_min']}", WITHHELD, "恢复实验不足三档")
    cov = min(r["ci_coverage"] for r in rows)
    cov_pct = min((r.get("ci_coverage_percentile", float("nan")) for r in rows),
                  default=float("nan"))
    status = PASS if cov >= t["ci_coverage_min"] else FAIL
    return _row("J2", "95% CI 覆盖率（按单元 bootstrap；BC 主口径 + 百分位敏感性）",
                {"min_coverage_bc": cov, "min_coverage_percentile": cov_pct,
                 "max_halfwidth": max(r["mean_ci_halfwidth"] for r in rows),
                 "per_level": [{"alpha_target": r["alpha_target"],
                                "coverage_bc": r["ci_coverage"],
                                "coverage_percentile": r.get("ci_coverage_percentile"),
                                "mean_halfwidth": r["mean_ci_halfwidth"]} for r in rows]},
                f"BC 覆盖率 ≥{t['ci_coverage_min']}（百分位法并列报出）", status,
                "覆盖率低于名义值说明 CI 是假的自信；两种方法同批重采样对比，方法选择靠数字不靠声明。")


# ─────────────────────────────── J3：ICC 功效 ───────────────────────────────

def j3_icc_power(power: Mapping[str, Any], null_control: Mapping[str, Any],
                 th: Mapping[str, float] | None = None) -> dict[str, Any]:
    t = {**THRESHOLDS, **(th or {})}
    p = power.get("power", float("nan"))
    fp = null_control.get("false_positive_rate", float("nan"))
    if not math.isfinite(p) or not math.isfinite(fp):
        return _row("J3", "ICC 功效/负控", {"power": p, "fp": fp},
                    f"power≥{t['icc_power_min']} 且 FP≤{t['icc_false_positive_max']}",
                    WITHHELD, "scipy 不可用或无有效重复")
    status = PASS if (p >= t["icc_power_min"] and fp <= t["icc_false_positive_max"]) else FAIL
    return _row("J3", "ICC 功效（n=100,k=3,ICC=0.3）+ ICC=0 负控",
                {"power": p, "false_positive_rate": fp,
                 "n_units": power.get("n_units"), "n_raters": power.get("n_raters"),
                 "reps_used": power.get("reps_used")},
                f"power≥{t['icc_power_min']} 且 FP≤{t['icc_false_positive_max']}",
                status, "功效不足=仪器钝；假阳性超 α=仪器会自欺。")


# ───────────────────── J4：真实数据可判性 + 最小审数表 ─────────────────────

def j4_real_admissibility(coverage: Mapping[str, Any], verdict: Mapping[str, Any],
                          min_table: Mapping[str, Any],
                          th: Mapping[str, float] | None = None) -> dict[str, Any]:
    """真实数据判据：**规则一致性**比「数值好看」重要。

    期望行为逐位校验：``n_pairable < 50`` 或 ``UNKEYED > 50%`` → 结论必须
    ``WITHHELD`` 且给出最小审数表；否则结论必须是 ``PASS``/``REQUIRE_THIRD``。
    """
    t = {**THRESHOLDS, **(th or {})}
    n_pair = int(coverage.get("n_pairable_missions", 0))
    unkeyed = float(coverage.get("unkeyed_share", float("nan")))
    should_withhold = (n_pair < t["min_pairable_units"]
                       or (math.isfinite(unkeyed) and unkeyed > t["max_unkeyed_share"]))
    expected = WITHHELD if should_withhold else "DECIDABLE"
    got = verdict.get("status")
    ok = (got == WITHHELD) if should_withhold else (got in (gate.PASS, gate.REQUIRE_THIRD))
    has_table = bool(min_table.get("rows"))
    status = PASS if (ok and (has_table or not should_withhold)) else FAIL
    return _row("J4", "真实台账可判性（WITHHELD 语义 + 最小审数表）",
                {"n_pairable": n_pair, "unkeyed_share": unkeyed,
                 "expected": expected, "verdict": got,
                 "min_rating_table_present": has_table,
                 "min_table_examples": (min_table.get("rows") or [])[:4]},
                f"n_pairable≥{int(t['min_pairable_units'])} 且 UNKEYED≤{t['max_unkeyed_share']} 才可判",
                status,
                "不可判时必须如实 WITHHELD，并用最小审数表把「要补多少」变成可派工的数。")


# ─────────────────────────────── J5：注入恢复 ───────────────────────────────

def j5_injection(injection: Mapping[str, Any],
                 th: Mapping[str, float] | None = None) -> dict[str, Any]:
    t = {**THRESHOLDS, **(th or {})}
    if injection.get("status") != "ok":
        return _row("J5", "注入 lenient/harsh 的 Δα 一致性", None,
                    f"相对误差≤{t['injection_rel_err_max']}", WITHHELD,
                    "注入实验未完成")
    rel = injection.get("relative_error", float("nan"))
    status = PASS if (math.isfinite(rel) and rel <= t["injection_rel_err_max"]) else FAIL
    return _row("J5", "注入 lenient(+0.5)/harsh(−0.5) 判级者的 Δα",
                {"delta_alpha_theory": injection["delta_alpha_theory"],
                 "delta_alpha_hat_mean": injection["delta_alpha_hat_mean"],
                 "delta_alpha_hat_sd": injection["delta_alpha_hat_sd"],
                 "relative_error": rel,
                 "sigma": injection["sigma"],
                 "reps_used": injection["reps_used"]},
                f"相对误差≤{t['injection_rel_err_max']}", status,
                "注入效应与理论一致 ⇒ 系数对「判级者系统性偏移」敏感且敏感度可预测。")


# ─────────────────────────── J6：决策门工作特性 ───────────────────────────

def j6_gate_operating_characteristic(
    *,
    good_alpha: float = 0.8,
    bad_alpha: float = 0.5,
    n_units: int = 100,
    n_raters: int = 4,
    reps: int = 100,
    seed: int = 20261201,
    th: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """已知真值世界里，门是否给出正确动作（不误放、不误拦）。

    α_true=0.8（够格）→ 期望 ``PASS``；α_true=0.5（不够格）→ 期望
    ``REQUIRE_THIRD``。判据把「门的错误率」量化，因为一个只在纸上成立的
    门槛等于没有门槛。
    """
    t = {**THRESHOLDS, **(th or {})}
    rng = np.random.default_rng(seed)
    out: dict[str, Any] = {}
    for tag, target, expect in (("good", good_alpha, gate.PASS),
                                ("bad", bad_alpha, gate.REQUIRE_THIRD)):
        cal = synthetic.calibrate_sigma(target, biases=(0.0,) * n_raters)
        if not cal.get("reached"):
            return _row("J6", "决策门工作特性", None,
                        f"PASS率≥{t['gate_pass_rate_min']}", WITHHELD,
                        f"目标 α={target} 不可达")
        hits = 0
        used = 0
        for _ in range(int(reps)):
            x = synthetic.simulate_sample(rng, n_units, sigma=cal["sigma"],
                                          biases=(0.0,) * n_raters)
            a = stats.krippendorff_alpha(x, "ordinal")
            if not math.isfinite(a["alpha"]):
                continue
            ci = stats.bootstrap_alpha_ci(x, n_boot=300, seed=int(rng.integers(1, 10**9)))
            v = gate.alpha_verdict(a["alpha"], n_pairable=n_units, unkeyed_share=0.0,
                                  ci=ci, thresholds=t)
            used += 1
            hits += int(v["status"] == expect)
        out[tag] = {"alpha_true": cal["achieved_alpha"], "expected": expect,
                    "rate": hits / used if used else float("nan"), "reps_used": used}
    good_rate = out["good"]["rate"]
    bad_rate = out["bad"]["rate"]
    ok = (math.isfinite(good_rate) and math.isfinite(bad_rate)
          and good_rate >= t["gate_pass_rate_min"]
          and bad_rate >= t["gate_block_rate_min"])
    return _row("J6", "决策门工作特性（α=0.8 应放行 / α=0.5 应拦下）", out,
                f"放行率≥{t['gate_pass_rate_min']} 且拦下率≥{t['gate_block_rate_min']}",
                PASS if ok else FAIL,
                "门自身的操作特性：只报系数不报门的行为等于没装门。")


# ─────────────────────────── 最小审数表（J4 附件） ───────────────────────────

def min_rating_table(
    *,
    target_halfwidth: float = 0.10,
    alphas: Sequence[float] = (0.5, 0.8),
    raters: Sequence[int] = (2, 3),
    measured_n: Sequence[int] = (25, 50, 100, 200),
    reps: int = 80,
    seed: int = 20261202,
) -> dict[str, Any]:
    """「CI 半宽 ≤ target 所需最小审数」表（按判级者数 × α 档）。

    做法：在 ``measured_n`` 各点**实测** α̂ 的抽样 SD（合成生成模型，与夹具同
    口径），再用 ``SD ∝ 1/√n`` 外推达标 n：

        n* = ceil( n_meas · (z·SD(n_meas)/target)² )

    并把「实测点」与「由更小 n 外推的预测」并列，直接给出外推可信度（相对偏差）。
    """
    rows: list[dict[str, Any]] = []
    for alpha_target in alphas:
        for m in raters:
            cal = synthetic.calibrate_sigma(alpha_target, biases=(0.0,) * m)
            if not cal.get("reached"):
                rows.append({"alpha_true": alpha_target, "n_raters": m,
                             "status": "unreachable"})
                continue
            sigma = cal["sigma"]
            rng = np.random.default_rng(seed + int(1000 * alpha_target) + m)
            meas: list[dict[str, Any]] = []
            for n in measured_n:
                vals = []
                for _ in range(int(reps)):
                    x = synthetic.simulate_sample(rng, int(n), sigma=sigma,
                                                  biases=(0.0,) * m)
                    a = stats.krippendorff_alpha(x, "ordinal")["alpha"]
                    if math.isfinite(a):
                        vals.append(a)
                sd = float(np.std(vals, ddof=1)) if len(vals) > 1 else float("nan")
                meas.append({"n_units": int(n), "sd_alpha": sd,
                             "ci_halfwidth": 1.959963985 * sd})
            best = None
            for rec_ in meas:
                if math.isfinite(rec_["ci_halfwidth"]) and \
                        rec_["ci_halfwidth"] <= target_halfwidth:
                    best = rec_["n_units"]
                    break
            pred = None
            if best is None:
                ref = max((r for r in meas if math.isfinite(r["sd_alpha"])),
                          key=lambda r: r["n_units"], default=None)
                if ref:
                    scale = (1.959963985 * ref["sd_alpha"] / target_halfwidth) ** 2
                    pred = int(math.ceil(ref["n_units"] * scale))
            rows.append({
                "alpha_true": cal["achieved_alpha"], "alpha_target": float(alpha_target),
                "sigma": sigma, "n_raters": int(m),
                "measured": meas,
                "min_n_units_measured": best,
                "min_n_units_extrapolated": pred,
                "predicted_n": pred if best is None else None,
            })
    return {"target_halfwidth": target_halfwidth, "rows": rows,
            "note": "实测点 SD 由合成生成模型抽样获得；外推按 SD∝1/√n。"}


# ─────────────────────────────── 汇总裁决 ───────────────────────────────

def adjudicate(criteria: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """把 J1..J6 折成单一裁决（任何 FAIL 即整体 FAIL；WITHHELD 不掩盖 FAIL）。"""
    by_id = {c["id"]: c["status"] for c in criteria}
    fails = [cid for cid, s in by_id.items() if s == FAIL]
    withheld = [cid for cid, s in by_id.items() if s == WITHHELD]
    improve = [cid for cid, s in by_id.items() if s == IMPROVE]
    if fails:
        overall = FAIL
    elif withheld:
        overall = WITHHELD
    elif improve:
        overall = IMPROVE
    else:
        overall = PASS
    return {"overall": overall, "by_id": by_id,
            "fails": fails, "withheld": withheld, "improve": improve,
            "n_criteria": len(criteria)}


__all__ = ["PASS", "FAIL", "IMPROVE", "WITHHELD", "THRESHOLDS",
           "j1_alpha_recovery", "j2_ci_coverage", "j3_icc_power",
           "j4_real_admissibility", "j5_injection", "j6_gate_operating_characteristic",
           "min_rating_table", "adjudicate"]