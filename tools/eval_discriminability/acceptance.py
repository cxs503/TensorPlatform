"""机器裁决：把分析结果折成可审计的判据表（J1..J7）。

判据与阈值（战役 metadata 口径，全部落 ``acceptance.json``）：

======  ======================================================  =========================================
判据    含义                                                     阈值
======  ======================================================  =========================================
J1      walk-forward AUC（判级特征集）                             >= 0.70 → PASS；否则 IMPROVE（须登记）
J2      校准 Brier（样本外概率）                                    <= 0.15
J3      判级通胀 Mann-Kendall                                          p >= 0.05 且无显著上行斜率 → PASS；
                                                                      p < 0.05 且上行 → ALERT（报警并报通胀率）
J4      负控（随机标签）                                            |mean-0.5| <= 0.05 → PASS，否则 FAIL
J5      正控（注入已知 AUC 的标签能否复原）                           |bias| <= 0.15 → PASS
J6      判别力分解覆盖（可用层数 ≥ 1 且最弱层已识别）                   PASS/WITHHELD
J7      既有评价管线回归（金标值不变）                                PASS 由 pytest 提供，见 tests
======  ======================================================  =========================================

**判据语义纪律**：``ALERT`` / ``IMPROVE`` / ``WITHHELD`` 与 ``FAIL`` 严格区分——
- ``FAIL`` 只给「仪器坏了」（负控不成立、回归漂移）；
- ``IMPROVE`` 给「仪器正常但数据/体系不够好」（AUC 不足、Brier 超阈）；
- ``WITHHELD`` 给「样本不足无法判定」，绝不补默认值。
"""
from __future__ import annotations

import math
from typing import Any, Mapping

#: 判据阈值（单一契约源：报告、测试、CLI 都读这里）
THRESHOLDS: dict[str, float] = {
    "auc_min": 0.70,
    "brier_max": 0.15,
    "neg_control_tol": 0.05,
    "alpha": 0.05,
    "recovery_tol": 0.15,
}

PASS, FAIL, IMPROVE, ALERT, WITHHELD = "PASS", "FAIL", "IMPROVE", "ALERT", "WITHHELD"


def _row(cid: str, name: str, value: Any, threshold: str, status: str,
         note: str = "") -> dict[str, Any]:
    return {"id": cid, "name": name, "value": value, "threshold": threshold,
            "status": status, "note": note}


def j1_auc(walk: Mapping[str, Any], *, threshold: float | None = None) -> dict[str, Any]:
    """J1：walk-forward 判别力是否达到 0.70。

    **判据量 = 同日条件 AUC**（只在同一测试日内配对），pooled AUC 作为敏感性量
    一并报出。理由不是放宽而是纠偏：正控实验（注入同日判别力 0.75 的标签）显示
    pooled AUC 会塌到 ~0.50，因为它把「各日基准率差异」当成了排序信号——
    pooled 的那份偏置已实测并登记在 ``recovery_control.composition_gap``。
    """
    thr = THRESHOLDS["auc_min"] if threshold is None else threshold
    same = float(walk.get("same_day_auc", float("nan")))
    pooled = float(walk.get("pooled_auc", float("nan")))
    if math.isnan(same) and math.isnan(pooled):
        return _row("J1", "walk-forward AUC", None, f">= {thr}", WITHHELD,
                    "样本不足/无可用窗口，AUC 无定义")
    estimand = same if not math.isnan(same) else pooled
    status = PASS if estimand >= thr else IMPROVE
    ci = walk.get("pooled_auc_ci") or {}
    return _row("J1", "walk-forward AUC（同日口径）", round(estimand, 4), f">= {thr}",
                status,
                f"{walk.get('n_scored_windows', 0)} 窗 / {walk.get('n_test_total', 0)} 测试样本；"
                f"配对 {walk.get('same_day_pairs', 0)}；pooled AUC={pooled:.3f}"
                f"（95%CI [{ci.get('lo', float('nan')):.3f}, {ci.get('hi', float('nan')):.3f}]，"
                f"构成敏感，见 J5）")


def j2_brier(cal: Mapping[str, Any], *, threshold: float | None = None) -> dict[str, Any]:
    """J2：样本外 Brier 是否 <= 0.15。"""
    thr = THRESHOLDS["brier_max"] if threshold is None else threshold
    b = float(cal.get("brier", float("nan")))
    if math.isnan(b):
        return _row("J2", "校准 Brier", None, f"<= {thr}", WITHHELD, "无样本外概率")
    return _row("J2", "校准 Brier", round(b, 4), f"<= {thr}",
                PASS if b <= thr else IMPROVE,
                f"climate={cal.get('base_rate', float('nan')):.3f}；"
                f"Brier skill={cal.get('brier_skill', float('nan')):.3f}")


def j3_inflation(inf: Mapping[str, Any], *, alpha: float | None = None) -> dict[str, Any]:
    """J3：判级通胀——Mann-Kendall p<alpha 且斜率为正 → ALERT。"""
    a = THRESHOLDS["alpha"] if alpha is None else alpha
    metrics = inf.get("metrics") or {}
    primary = metrics.get("mean_score") or {}
    mk = primary.get("mk") or {}
    ts = primary.get("theil_sen") or {}
    p = float(mk.get("p", float("nan")))
    slope = float(ts.get("slope", float("nan")))
    if math.isnan(p):
        return _row("J3", "判级通胀 (Mann-Kendall)", None, f"p >= {a} 且斜率<=0", WITHHELD,
                    f"活跃日 {inf.get('active_days', 0)} 天，样本不足以做趋势检验")
    alarm = (p < a) and (slope > 0)
    rate = primary.get("relative_rate_per_day", float("nan"))
    return _row("J3", "判级通胀 (Mann-Kendall)", round(p, 6), f"p >= {a} 且斜率<=0",
                ALERT if alarm else PASS,
                f"复合分 Sen 斜率={slope:.4f}/日（相对 {rate * 100:.2f}%/日，"
                f"95%CI [{ts.get('lo', float('nan')):.4f}, {ts.get('hi', float('nan')):.4f}]）；"
                f"显著项={inf.get('alarming_metrics', [])}")


def j4_negative_control(neg: Mapping[str, Any], *,
                        tol: float | None = None) -> dict[str, Any]:
    """J4：负控（随机标签 → AUC≈0.5）。不通过 = 仪器有泄漏/偏置 = FAIL。"""
    t = THRESHOLDS["neg_control_tol"] if tol is None else tol
    if neg.get("status") == "WITHHELD" or not neg.get("n_used"):
        return _row("J4", "负控（随机标签）", None, f"|mean-0.5| <= {t}", WITHHELD,
                    "无可用置换结果")
    mean = float(neg["mean_auc"])
    return _row("J4", "负控（随机标签）", round(mean, 4), f"|mean-0.5| <= {t}",
                PASS if neg.get("pass") else FAIL,
                f"{neg.get('n_used')} 次置换；sd={neg.get('sd_auc', float('nan')):.3f}；"
                f"2.5–97.5%=[{neg.get('p2_5', float('nan')):.3f}, {neg.get('p97_5', float('nan')):.3f}]")


def j5_recovery(rec: Mapping[str, Any], *,
                tol: float | None = None) -> dict[str, Any]:
    """J5：正控（注入已知 AUC 能否复原）。偏差过大 = 流水线有偏 = FAIL。"""
    t = THRESHOLDS["recovery_tol"] if tol is None else tol
    if rec.get("status") != "ok":
        return _row("J5", "正控（注入已知 AUC）", None, f"|bias| <= {t}", WITHHELD,
                    "注入/复现失败")
    return _row("J5", "正控（注入已知 AUC）", round(float(rec["mean_recovered"]), 4),
                f"|bias| <= {t}", PASS if rec.get("pass") else FAIL,
                f"目标 {rec.get('target_auc')}（同日口径）；bias={rec.get('bias', float('nan')):+.3f}；"
                f"sd={rec.get('sd', float('nan')):.3f}；n={rec.get('n_used')}；"
                f"pooled 口径 bias={rec.get('pooled_bias', float('nan')):+.3f}"
                f"（构成偏置实测 {rec.get('composition_gap', float('nan')):+.3f}）")


def j6_decomposition(decomp: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """J6：分解覆盖度——至少一个维度有可用层，且报告最弱/最强层。"""
    usable = {d: v.get("n_strata_usable", 0) for d, v in decomp.items()}
    if not any(usable.values()):
        return _row("J6", "判别力分解", 0, "可用层 >= 1", WITHHELD,
                    "所有维度层样本不足（正/负各 < 10）")
    best_dim = max(usable, key=lambda d: usable[d])
    v = decomp[best_dim]
    return _row("J6", "判别力分解", int(sum(usable.values())), "可用层 >= 1", PASS,
                f"维度 {usable}；{best_dim} 同日加权 AUC="
                f"{v.get('weighted_same_day_auc', float('nan')):.3f}；"
                f"最弱层={v.get('weakest')} 最强层={v.get('strongest')}")


def j7_regression(reg: Mapping[str, Any] | None) -> dict[str, Any]:
    """J7：既有评价管线回归——参考实现出口指纹与金标逐项比对（atol=1e-9）。"""
    info = dict(reg or {})
    status = info.get("status", WITHHELD)
    if status not in (PASS, FAIL):
        status = WITHHELD
    return _row("J7", "既有评价管线回归（金标指纹）",
                info.get("n_mismatch", None), "0 处漂移",
                status, str(info.get("note", "")) + (
                    "" if not info.get("mismatches") else
                    "；漂移项：" + ", ".join(info["mismatches"][:5])))


def verdict(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """总裁决：任一 FAIL → FAIL；任一无 FAIL 但有 IMPROVE/ALERT → 需改进；否则 PASS。"""
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    if counts.get(FAIL):
        v = FAIL
    elif counts.get(IMPROVE) or counts.get(ALERT) or counts.get(WITHHELD):
        v = "PARTIAL"
    else:
        v = PASS
    return {"verdict": v, "counts": counts,
            "n_pass": counts.get(PASS, 0), "n_rows": len(rows),
            "criteria": rows,
            "pass_ratio": round(counts.get(PASS, 0) / len(rows), 4) if rows else 0.0}


def build(walk_full: Mapping[str, Any], walk_grade: Mapping[str, Any],
          cal: Mapping[str, Any], inf: Mapping[str, Any], neg: Mapping[str, Any],
          rec: Mapping[str, Any], decomp: Mapping[str, Mapping[str, Any]],
          regression_check: Mapping[str, Any] | None = None,
          *, j1_source: str = "grade_only") -> dict[str, Any]:
    """组装判据总表。``j1_source`` 指明 J1 用哪个特征集（默认只用判级特征）。"""
    src = walk_grade if j1_source == "grade_only" else walk_full
    rows = [
        j1_auc(src),
        j2_brier(cal),
        j3_inflation(inf),
        j4_negative_control(neg),
        j5_recovery(rec),
        j6_decomposition(decomp),
        j7_regression(regression_check),
    ]
    out = verdict(rows)
    out["j1_feature_set"] = j1_source
    out["j1_full_feature_auc"] = float(walk_full.get("pooled_auc", float("nan")))
    return out