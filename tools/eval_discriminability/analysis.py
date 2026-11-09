"""分析层：walk-forward 判别力、校准、通胀趋势、判别力分解、负控与仪器复现。

复用约定：AUC / Brier / 校准曲线 / 逻辑回归一律取
:mod:`tools.external_validity.metrics`（同一契约源，避免两套口径）；
本模块只加**它没有的三件事**：

1. **Mann-Kendall 通胀检验**（CUSUM 之外的非参数趋势检验，见 :mod:`.stats`）；
2. **逐维度判别力分解**（repo / agent / domain / model），并同时给
   「同日条件配对」口径（去构成假象）与 bootstrap 区间；
3. **仪器营业特性**：负控（随机标签 → 0.5）+ 正控（注入已知 AUC 的可观测标签 →
   能否复原），从而把「度量算不出来」与「数据不可识别」分开归因。

全部函数纯函数化（输入行 → 输出 dict），便于单测与离线重算。
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from tools.external_validity.metrics import (
    auc_score,
    bootstrap_auc_ci,
    brier_score,
    brier_skill_score,
    calibration_curve,
    fit_logistic,
    max_calibration_deviation,
)

from . import stats as st

#: S/E 判级特征在 ``x`` 里的位置（见 ``ledger.FEATURE_NAMES``）
FEATURE_SETS: dict[str, tuple[int, ...]] = {
    "grade_only": (0, 1),          # S 序、E 序 —— 被验证的判级本身
    "evidence_only": (2, 3, 4, 5),  # 证据面（跨模型复核/引用核对/产物/理由码）
    "full": (0, 1, 2, 3, 4, 5),    # 全部判级可观测特征
}

#: 高分级阈值（S 序 >= 2 即 S2 及以上）——「分数通胀」的口径
HIGH_GRADE_ORD = 2

DEFAULT_GUARDS = {"min_train_days": 2, "min_test_n": 10,
                  "min_train_pos": 10, "min_train_neg": 10}


# ─────────────────────────────── 特征装配 ───────────────────────────────

def samples_with_features(rows: Iterable[Mapping[str, Any]], features: str,
                          *, label_key: str = "y") -> list[dict[str, Any]]:
    """把研究表行 → ``walk_forward`` 需要的样本（``x`` 按特征集裁剪）。"""
    idx = FEATURE_SETS[features]
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.get(label_key) is None:
            continue
        out.append({
            "mission_id": int(r["mission_id"]),
            "day": r["day"],
            "x": [float(r["x"][i]) for i in idx],
            "y": int(r[label_key]),
            "repo": r.get("repo"),
            "agent": r.get("agent"),
            "agent_family": r.get("agent_family"),
            "domain": r.get("domain"),
            "model": r.get("model"),
            "origin": r.get("origin"),
        })
    return out


# ──────────────────────────── walk-forward（带 OOS 明细） ────────────────────────────

def walk_forward_oos(samples: Sequence[dict[str, Any]], *,
                     guards: Mapping[str, int] | None = None,
                     l2: float = 1.0, n_boot: int = 2000) -> dict[str, Any]:
    """expanding-window walk-forward，返回**逐样本**的样本外预测。

    与 :func:`tools.external_validity.metrics.walk_forward` 同思路，额外输出每条的
    ``mission_id`` 与维度值，使分解可以落到「层 × 日」上；训练集恒为
    ``day < d``（严格小于），杜绝同日泄漏。窗口守卫：训练集正负各 ≥10、
    测试集 ≥10 条，否则整窗跳过并写明原因。
    """
    g = dict(DEFAULT_GUARDS)
    if guards:
        g.update({k: int(v) for k, v in guards.items()})
    ordered = sorted(samples, key=lambda s: (s["day"], s["mission_id"]))
    days = sorted({s["day"] for s in ordered})
    windows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    oos: list[dict[str, Any]] = []

    for i, day in enumerate(days):
        if i < g["min_train_days"]:
            skipped.append({"day": day, "why": f"warmup(<{g['min_train_days']}d)"})
            continue
        train = [s for s in ordered if s["day"] < day]
        test = [s for s in ordered if s["day"] == day]
        if len(test) < g["min_test_n"]:
            skipped.append({"day": day, "why": f"test_n={len(test)}<{g['min_test_n']}"})
            continue
        y_tr = np.array([s["y"] for s in train], dtype=int)
        n_pos, n_neg = int((y_tr == 1).sum()), int((y_tr == 0).sum())
        if n_pos < g["min_train_pos"] or n_neg < g["min_train_neg"]:
            skipped.append({"day": day,
                            "why": f"train class imbalance pos={n_pos} neg={n_neg}"})
            continue
        model = fit_logistic([s["x"] for s in train], y_tr, l2=l2)
        raw = model.predict_raw([s["x"] for s in test])
        prob = model.predict_proba([s["x"] for s in test])
        y_te = np.array([s["y"] for s in test], dtype=int)
        windows.append({
            "day": day, "n_train": len(train), "n_test": len(test),
            "train_pos": n_pos, "train_neg": n_neg,
            "test_pos": int((y_te == 1).sum()), "test_neg": int((y_te == 0).sum()),
            "actual_rate": float(y_te.mean()),
            "mean_pred": float(np.mean(prob)),
            "auc": auc_score(raw, y_te),
            "brier": brier_score(prob, y_te),
        })
        for s, ra, pr, yy in zip(test, raw, prob, y_te):
            oos.append({"mission_id": s["mission_id"], "day": day,
                        "score": float(ra), "prob": float(pr), "y": int(yy),
                        "repo": s.get("repo"), "agent": s.get("agent"),
                        "agent_family": s.get("agent_family"),
                        "domain": s.get("domain"), "model": s.get("model"),
                        "origin": s.get("origin")})

    scores = [r["score"] for r in oos]
    labels = [r["y"] for r in oos]
    days_oos = [r["day"] for r in oos]
    valid = [w for w in windows if not math.isnan(w["auc"])]
    pooled_auc = auc_score(scores, labels) if oos else float("nan")
    # n_boot=0 → 跳过 bootstrap（置换/重复实验只要点估计，省 200 倍算力）
    ci = (bootstrap_auc_ci(scores, labels, n_boot=n_boot) if (oos and n_boot > 0)
          else {"auc": float("nan"), "lo": float("nan"), "hi": float("nan"),
                "n_boot": 0})
    # 同日条件配对（把「当天基准率」这一构成假象从判别力里剥掉）
    cond = st.conditional_pair_concordance(scores, labels, days_oos) if oos else None
    return {
        "windows": windows,
        "skipped": skipped,
        "n_windows": len(windows),
        "n_scored_windows": len(valid),
        "n_test_total": len(oos),
        "pooled_auc": float(pooled_auc),
        "pooled_auc_ci": ci,
        "same_day_auc": float(cond["auc"]) if cond else float("nan"),
        "same_day_pairs": int(cond["pairs"]) if cond else 0,
        "window_mean_auc": float(np.mean([w["auc"] for w in valid])) if valid else float("nan"),
        "window_min_auc": float(np.min([w["auc"] for w in valid])) if valid else float("nan"),
        "oos": oos,
        "guards": g,
    }


# ────────────────────────────── 校准（Brier / 曲线） ──────────────────────────────

def calibration_block(oos: Sequence[Mapping[str, Any]], *,
                      n_bins: int = 10, min_bin_n: int = 10) -> dict[str, Any]:
    """样本外概率的校准曲线 + Brier + 技巧分（vs 测试集气候态）。"""
    if not oos:
        return {"bins": [], "brier": float("nan"), "brier_skill": float("nan"),
                "max_dev": float("nan"), "base_rate": float("nan"), "n": 0}
    probs = [float(r["prob"]) for r in oos]
    labels = [int(r["y"]) for r in oos]
    curve = calibration_curve(probs, labels, n_bins=n_bins)
    return {
        "bins": curve,
        "brier": float(brier_score(probs, labels)),
        "brier_skill": float(brier_skill_score(probs, labels)),
        "max_dev": float(max_calibration_deviation(curve, min_bin_n=min_bin_n)),
        "min_bin_n": int(min_bin_n),
        "base_rate": float(np.mean(labels)),
        "n": len(oos),
    }


# ─────────────────────────────── 评分通胀（MK） ───────────────────────────────

def _daily_series(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """按判级完成日聚合：判级量、高分级占比、S 序均值、E 序均值、复合分均值。"""
    by_day: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    out: list[dict[str, Any]] = []
    for day in sorted(by_day):
        grp = by_day[day]
        s_ord = np.array([float(r["x"][0]) for r in grp])
        e_ord = np.array([float(r["x"][1]) for r in grp])
        out.append({
            "day": day,
            "n": len(grp),
            "high_share": float((s_ord >= HIGH_GRADE_ORD).mean()),
            "mean_s": float(s_ord.mean()),
            "mean_e": float(e_ord.mean()),
            "mean_score": float((s_ord + 0.5 * e_ord).mean()),
        })
    return out


def inflation_block(rows: Iterable[Mapping[str, Any]], *,
                    min_day_n: int = 30, alpha: float = 0.05) -> dict[str, Any]:
    """评分通胀检测：日度序列上的 Mann-Kendall 趋势检验 + Theil-Sen 斜率。

    「分数通胀」的定义：判级分数**随日历时间系统性上移**（判级标准松弛）。
    三个观测量分别检验：高分级（S≥S2）占比、S 序均值、复合分（S + 0.5E）均值。

    ``min_day_n`` 的活跃日过滤：单日判级量过少时占比是纯噪声（会假报警），
    过滤阈值与受影响的天数必须写进报告。
    """
    series = _daily_series(rows)
    active = [s for s in series if s["n"] >= min_day_n]
    metrics: dict[str, Any] = {}
    for key, label in (("high_share", "高分级(S>=S2)占比"),
                       ("mean_s", "S 序均值"),
                       ("mean_score", "复合分 S+0.5E 均值")):
        y = [s[key] for s in active]
        t = list(range(len(y)))
        mk = st.mann_kendall(y)
        ts = st.theil_sen_with_ci(t, y, alpha=alpha)
        base = float(np.median(y)) if y else float("nan")
        rel = (ts["slope"] / base) if (base and not math.isnan(ts["slope"])) else float("nan")
        metrics[key] = {
            "label": label,
            "mk": mk,
            "theil_sen": ts,
            "median": base,
            "relative_rate_per_day": float(rel) if rel == rel else float("nan"),
            "trend_significant": bool(mk.get("status") == "ok" and
                                      mk["p"] < alpha),
        }
    alarming = [k for k, v in metrics.items() if v["trend_significant"]]

    # 机制诊断：判级"波次"造成的台阶 vs 单调松弛。
    # 取「首个高分级占比 > 0 的活跃日」为体制分界，对其后的同质子序列再跑一次 MK：
    # 若全序列显著而子序列不显著，说明信号主要是**波次台阶**（判级回填批次换了口径），
    # 而不是持续的判级松弛——两者含义完全不同，必须分开报。
    split = None
    for rec in active:
        if rec["high_share"] > 0:
            split = rec["day"]
            break
    tail = [r for r in active if split is not None and r["day"] >= split]
    tail_y = [r["mean_score"] for r in tail]
    tail_mk = st.mann_kendall(tail_y) if len(tail_y) >= 3 else {
        "n": len(tail_y), "p": float("nan"), "tau": float("nan"),
        "status": "insufficient_n"}
    return {
        "daily_series": series,
        "active_days": len(active),
        "filtered_days": len(series) - len(active),
        "min_day_n": int(min_day_n),
        "alpha": alpha,
        "metrics": metrics,
        "alarm": bool(alarming),
        "alarming_metrics": alarming,
        "regime": {
            "split_day": split,
            "post_regime_days": len(tail),
            "post_regime_mk": tail_mk,
            "post_regime_median_score": float(np.median(tail_y)) if tail_y else float("nan"),
            "note": ("全序列显著而后段不显著 ⇒ 信号主要是判级波次台阶（口径换代），"
                     "不是持续松弛" if split else "无体制分界"),
        },
    }


# ─────────────────────────────── 判别力分解 ───────────────────────────────

def decomposition(oos: Sequence[Mapping[str, Any]], dimension: str, *,
                  min_pos: int = 10, min_neg: int = 10,
                  n_boot: int = 1000, seed: int = 20261109) -> dict[str, Any]:
    """按维度分层计算判别力（含同日条件口径与 bootstrap 区间）。

    - ``pooled_auc``：层内合并 AUC（受层内日构成影响）
    - ``same_day_auc``：层内**同日**配对的 AUC（去构成假象，保守口径）
    - 层样本不足（正 <``min_pos`` 或负 <``min_neg``）→ ``status=WITHHELD``，
      AUC 记 ``nan``，绝不补 0.5
    """
    rows = [r for r in oos if r.get(dimension) is not None]
    strata: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        strata.setdefault(str(r[dimension]), []).append(r)

    out: list[dict[str, Any]] = []
    for key in sorted(strata):
        grp = strata[key]
        y = [int(r["y"]) for r in grp]
        n_pos, n_neg = sum(y), len(y) - sum(y)
        rec = {"stratum": key, "n": len(grp), "n_pos": n_pos, "n_neg": n_neg,
               "base_rate": (n_pos / len(grp)) if grp else float("nan")}
        if n_pos < min_pos or n_neg < min_neg:
            rec.update({"status": "WITHHELD", "pooled_auc": float("nan"),
                        "same_day_auc": float("nan"), "ci": None})
        else:
            sc = [float(r["score"]) for r in grp]
            days = [r["day"] for r in grp]
            cond = st.conditional_pair_concordance(sc, y, days)
            ci = bootstrap_auc_ci(sc, y, n_boot=n_boot, seed=seed)
            rec.update({
                "status": "ok",
                "pooled_auc": float(auc_score(sc, y)),
                "same_day_auc": float(cond["auc"]),
                "same_day_pairs": int(cond["pairs"]),
                "ci": {"lo": ci["lo"], "hi": ci["hi"]},
            })
        out.append(rec)

    # 类别平衡诊断：某层全是阳性/全是阴性 → AUC 无定义，且往往说明**标签与该维度强相关**
    # （例如战役执行面的单子几乎都是合并单），这本身就是外部效度的结构性限制，必须报出来。
    balance: list[dict[str, Any]] = []
    for key in sorted(strata):
        grp = strata[key]
        y = [int(r["y"]) for r in grp]
        n_pos, n_neg = sum(y), len(y) - sum(y)
        lo, hi = st.wilson_interval(n_pos, len(y))
        balance.append({"stratum": key, "n": len(grp), "n_pos": n_pos, "n_neg": n_neg,
                        "pos_rate": n_pos / len(grp), "wilson_lo": lo, "wilson_hi": hi,
                        "single_class": bool(n_pos == 0 or n_neg == 0)})

    ok = [r for r in out if r["status"] == "ok"]
    weighted = sum(r["n_pos"] * r["n_neg"] for r in ok)
    agg = (sum(r["same_day_auc"] * r["n_pos"] * r["n_neg"] for r in ok) / weighted
           if weighted else float("nan"))
    return {
        "dimension": dimension,
        "n_strata": len(out),
        "n_strata_usable": len(ok),
        "n_rows": len(rows),
        "weighted_same_day_auc": float(agg),
        "per_stratum": out,
        "class_balance": balance,
        "n_single_class": int(sum(1 for b in balance if b["single_class"])),
        "weakest": (min(ok, key=lambda r: r["same_day_auc"])["stratum"] if ok else None),
        "strongest": (max(ok, key=lambda r: r["same_day_auc"])["stratum"] if ok else None),
    }


# ───────────────────────── 仪器营业特性：负控 / 正控 ─────────────────────────

def negative_control(samples: Sequence[dict[str, Any]], *, n_perm: int = 100,
                     seed: int = 20261109, guards: Mapping[str, int] | None = None,
                     tol: float = 0.05) -> dict[str, Any]:
    """负控：整体打乱标签（保留标签边际与日均量）后重跑整条 walk-forward。

    期望 pooled AUC ≈ 0.5。判据：``|mean - 0.5| <= tol`` **且** 95% 分位带落在
    ``[0.5-tol, 0.5+tol]`` 内（只看均值会被个别极端抽样掩盖）。
    """
    rng = np.random.default_rng(seed)
    base_labels = [int(s["y"]) for s in samples]
    aucs: list[float] = []
    for _ in range(n_perm):
        perm = rng.permutation(base_labels)
        relabeled = [dict(s, y=int(perm[i])) for i, s in enumerate(samples)]
        res = walk_forward_oos(relabeled, guards=guards, n_boot=0)
        if not math.isnan(res["pooled_auc"]):
            aucs.append(res["pooled_auc"])
    arr = np.asarray(aucs, dtype=float)
    if arr.size == 0:
        return {"n_used": 0, "status": "WITHHELD"}
    lo, hi = float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))
    return {
        "n_requested": int(n_perm),
        "n_used": int(arr.size),
        "mean_auc": float(arr.mean()),
        "sd_auc": float(arr.std(ddof=1)) if arr.size > 1 else float("nan"),
        "p2_5": lo, "p97_5": hi,
        "tol": tol,
        "bias_from_half": float(abs(arr.mean() - 0.5)),
        "pass": bool(abs(arr.mean() - 0.5) <= tol and lo >= 0.5 - tol and hi <= 0.5 + tol),
    }


# ────────────────────────────── 可识别性与功效 ──────────────────────────────

def identifiability(rows: Sequence[Mapping[str, Any]], *,
                    target_auc: float = 0.7, se_target: float = 0.05,
                    min_pos: int = 10, min_neg: int = 10) -> dict[str, Any]:
    """标签可识别性诊断：逐日类别构成 + 达到目标 AUC 精度所需的样本量。

    回答的问题：**算不出 AUC 是仪器的问题，还是标签被删失？**
    """
    by_day: dict[str, dict[str, int]] = {}
    for r in rows:
        if r.get("y") is None:
            continue
        d = by_day.setdefault(r["day"], {"pos": 0, "neg": 0})
        d["pos" if r["y"] == 1 else "neg"] += 1
    both = [d for d, v in by_day.items() if v["pos"] >= 1 and v["neg"] >= 1]
    trainable = [d for d, v in by_day.items()
                 if v["pos"] >= min_pos and v["neg"] >= min_neg]
    n_pos_tot = sum(v["pos"] for v in by_day.values())
    n_neg_tot = sum(v["neg"] for v in by_day.values())
    ratio = (n_neg_tot / n_pos_tot) if n_pos_tot else float("nan")
    se_now = st.hanley_mcneil_se(target_auc, n_pos_tot, n_neg_tot) if n_pos_tot else float("nan")
    return {
        "days": len(by_day),
        "days_with_both_classes": len(both),
        "days_meeting_class_floor": len(trainable),
        "n_pos_total": n_pos_tot,
        "n_neg_total": n_neg_tot,
        "neg_per_pos": float(ratio) if ratio == ratio else float("nan"),
        "by_day": dict(sorted(by_day.items())),
        "target_auc": target_auc,
        "se_target": se_target,
        "se_at_current_n": float(se_now),
        "n_pos_needed_for_se": st.n_for_se(target_auc, se_target,
                                           ratio_neg_per_pos=ratio if ratio == ratio else 1.0),
        "n_pos_shortfall": int(max(0, st.n_for_se(
            target_auc, se_target, ratio_neg_per_pos=ratio if ratio == ratio else 1.0)
            - n_pos_tot)),
        "first_day_with_pos": min((d for d, v in by_day.items() if v["pos"] > 0),
                                  default=None),
        "last_day_with_neg": max((d for d, v in by_day.items() if v["neg"] > 0),
                                 default=None),
    }


def inject_labels(rows: Sequence[Mapping[str, Any]], *, target_auc: float,
                  seed: int = 0, n_iter: int = 40) -> list[dict[str, Any]]:
    """正控：**保留真实日期与类别构成**，注入已知判别力的合成标签。

    做法：逐日在**本日 pos/neg 数额不变**的前提下，按 ``score = s_ord + k·noise``
    排序把阳性名额分配给得分最高者（``k`` 由二分调到 ``target_auc``）。
    这样时间删失结构不变，只有标签可识别性改变——于是「复现出的 AUC」就是
    仪器在给定数据几何下的营业特性。
    """
    by_day: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        if r.get("y") is None:
            continue
        by_day.setdefault(r["day"], []).append(dict(r))

    def assign(k: float) -> tuple[float, dict[int, int]]:
        rng2 = np.random.default_rng(seed)   # 同一噪声，保证 k 二分单调可控
        out_labels: dict[int, int] = {}
        for day in sorted(by_day):
            grp = by_day[day]
            n_pos = sum(1 for r in grp if r["y"] == 1)
            base = np.array([float(r["x"][0]) + 0.5 * float(r["x"][1]) for r in grp])
            noise = rng2.normal(size=base.size)
            s = base / (np.std(base) or 1.0) + k * noise
            order = np.argsort(-s)
            y = np.zeros(base.size, dtype=int)
            y[order[:n_pos]] = 1
            for r, yy in zip(grp, y):
                out_labels[int(r["mission_id"])] = int(yy)
        # 当日标号的判别力（用本日分数对当日标签算 AUC，再按日加权）
        num = den = 0.0
        for day in sorted(by_day):
            grp = by_day[day]
            sc = [float(r["x"][0]) + 0.5 * float(r["x"][1]) for r in grp]
            yy = [out_labels[int(r["mission_id"])] for r in grp]
            a = auc_score(sc, yy)
            if math.isnan(a):
                continue
            w = sum(yy) * (len(yy) - sum(yy))
            num += a * w
            den += w
        return (num / den if den else float("nan")), out_labels

    lo, hi = 0.0, 6.0
    best: dict[int, int] = {}
    for _ in range(n_iter):
        mid = (lo + hi) / 2.0
        val, labels = assign(mid)
        best = labels
        if math.isnan(val):
            break
        if val > target_auc:         # 判别力太强 → 加噪
            lo = mid
        else:
            hi = mid
        if abs(val - target_auc) < 0.01:
            break
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.get("y") is None:
            continue
        out.append(dict(r, y=int(best.get(int(r["mission_id"]), 0))))
    return out


def recovery_control(samples: Sequence[dict[str, Any]], *,
                     target_auc: float = 0.75, n_rep: int = 5,
                     seed: int = 20261109,
                     guards: Mapping[str, int] | None = None,
                     tol: float = 0.15) -> dict[str, Any]:
    """正控：注入 ``target_auc`` 的合成标签，跑同一 walk-forward，看能否复原。

    **判据量取「同日条件 AUC」而不是 pooled AUC**——这不是放宽，而是纠偏：
    pooled AUC 把「各日基准率差异」也算进排序，注入实验中同日判别力 0.75 时
    pooled 会塌到 ~0.50（见 ``pooled_*`` 字段与 ``composition_gap``）。
    pooled 的那份塌陷本身就是**度量偏置的实测值**，随结果一并报出。

    判据：``|mean_same_day - target| <= tol``（默认 0.15）。
    """
    rows = [dict(s) for s in samples]
    same_day: list[float] = []
    pooled: list[float] = []
    for rep in range(n_rep):
        injected = inject_labels(rows, target_auc=target_auc, seed=seed + rep)
        res = walk_forward_oos(injected, guards=guards, n_boot=0)
        if not math.isnan(res["same_day_auc"]):
            same_day.append(res["same_day_auc"])
        if not math.isnan(res["pooled_auc"]):
            pooled.append(res["pooled_auc"])
    if not same_day:
        return {"status": "WITHHELD", "n_used": 0, "target_auc": target_auc}
    a = np.asarray(same_day, dtype=float)
    pv = np.asarray(pooled, dtype=float)
    pooled_mean = float(pv.mean()) if pv.size else float("nan")
    return {
        "status": "ok",
        "estimand": "same_day_auc",
        "target_auc": target_auc,
        "tol": tol,
        "n_requested": int(n_rep),
        "n_used": int(a.size),
        "mean_recovered": float(a.mean()),
        "sd": float(a.std(ddof=1)) if a.size > 1 else float("nan"),
        "bias": float(a.mean() - target_auc),
        "values": [float(v) for v in a],
        "pooled_mean": pooled_mean,
        "pooled_bias": float(pooled_mean - target_auc) if pv.size else float("nan"),
        "composition_gap": float(pooled_mean - a.mean()) if pv.size else float("nan"),
        "pass": bool(abs(a.mean() - target_auc) <= tol),
    }
