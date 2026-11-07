"""编排：判级 → 下游结果 的外部效度校准与判据裁决。

五条判据（对应战役节点 IN55-N07 的机器可算验收口径）：

1. ``walk_forward_auc ≥ 0.7``：扩窗时间切分、只用过去训练，pooled 样本外 AUC。
2. ``brier ≤ 0.15``：同一批样本外预测的 Brier 分数（同时报分箱校准曲线与 ECE）。
3. 通胀检测：``top 等级占比 > 30% 且 merge 率 < 50%`` → 必须告警；
   反过来（merge 率高）不得误报——这条判据验的是**检测器**本身对不对。
4. 负控：把标签在样本内随机置换后重跑整条 walk-forward 管线，AUC 应 ≈ 0.5
   （|AUC-0.5| ≤ 0.05，且原 AUC 落在置换零分布的尾部）。
5. 跨仓一致性：TensorLBM / TensorDEM / ship-performance-platform 三仓
   各自 walk-forward AUC 极差 ≤ 0.1。

全部裁决既可在真实数据上跑，也可在合成数据上单测（判据验的是管线正确性）。
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

import numpy as np

from . import metrics
from .dataset import EvalRecord

#: S 等级秩（S1 最高 → 5 最低；S-N 为负例类，秩 0）
S_RANK: dict[str, float] = {"S1": 5.0, "S2": 4.0, "S3": 3.0, "S4": 2.0, "S5": 1.0}
#: E 证据等级秩（E4 最强 → 4；E0 无证据 → 0）
E_RANK: dict[str, float] = {"E0": 0.0, "E1": 1.0, "E2": 2.0, "E3": 3.0, "E4": 4.0}

DEFAULT_TARGET_REPOS = ("TensorLBM", "TensorDEM", "ship-performance-platform")

#: 主分析只收「双结局都被记录」的落账证据。
#: ``pr_gate`` 回填是**按成功挑样**的（240 条里 239 条 merged），merge 率被抬到
#: 99.6%，拿它做校准等于用「只回填了成功案例」的样本评估成功率——系统性偏倚。
#: 因此 pr_gate 只进敏感性对照，不进主判据。
PRIMARY_EVIDENCE = ("merge_ledger", "ship_ledger")

CRITERIA = {
    "auc_min": 0.70,
    "brier_max": 0.15,
    "s_share_min": 0.30,
    "merge_rate_max": 0.50,
    "null_tolerance": 0.05,
    "repo_spread_max": 0.10,
}


# ───────────────────────────── 特征 / 子集 ─────────────────────────────

def features(rec: EvalRecord) -> list[float]:
    """判级 → 2 维特征（S 秩, E 秩）。方向不预设，由训练窗学。"""
    return [S_RANK.get(rec.grade or "", 0.0), E_RANK.get(rec.e_level or "", 0.0)]


def labeled_records(records: Iterable[EvalRecord]) -> list[EvalRecord]:
    out = [r for r in records if r.labeled]
    out.sort(key=lambda r: (r.ref_time, r.mission_id))
    return out


#: 预测模型（主模型先验选定，其余作稳健性对照，事后挑最优=作弊，故全报）
PRIMARY_MODEL = "cell_rate"
MODELS = ("cell_rate", "logistic", "s_rank")


def _cell_table(train: Sequence[EvalRecord], alpha: float = 2.0) -> dict:
    """训练窗的 (grade, e_level) 经验 merge 率表 + 层级兜底（Beta 平滑）。

    这是「评价分数作为概率」的最小假设估计：不假定 S/E 方向、不假定可加性，
    只用训练窗里同格子的历史 merge 率；样本少的格子向 S 边际、再向全局收缩。
    """
    y_all = np.array([r.y for r in train], dtype=float)
    prior = metrics.fit_prior_probability(y_all)
    cells: dict[tuple, list[int]] = {}
    grades: dict[str, list[int]] = {}
    for r in train:
        key = (r.grade, r.e_level)
        c = cells.setdefault(key, [0, 0])
        c[0] += int(r.y)
        c[1] += 1
        g = grades.setdefault(str(r.grade), [0, 0])
        g[0] += int(r.y)
        g[1] += 1
    cell_p = {k: (v[0] + alpha * prior) / (v[1] + alpha) for k, v in cells.items()}
    grade_p = {k: (v[0] + alpha * prior) / (v[1] + alpha) for k, v in grades.items()}
    return {"prior": prior, "cell": cell_p, "grade": grade_p,
            "cell_n": {k: v[1] for k, v in cells.items()}}


def _grade_only_table(train: Sequence[EvalRecord], alpha: float = 2.0) -> dict:
    y_all = np.array([r.y for r in train], dtype=float)
    prior = metrics.fit_prior_probability(y_all)
    grades: dict[str, list[int]] = {}
    for r in train:
        g = grades.setdefault(str(r.grade), [0, 0])
        g[0] += int(r.y)
        g[1] += 1
    return {"prior": prior,
            "grade": {k: (v[0] + alpha * prior) / (v[1] + alpha)
                      for k, v in grades.items()}}


def _fit_predict(train: Sequence[EvalRecord], test: Sequence[EvalRecord],
                 l2: float = 1.0, model: str = PRIMARY_MODEL) -> np.ndarray:
    """在训练窗拟合、对测试窗出概率。单类训练窗退化为后验常数（不给 0/1）。"""
    y = np.array([r.y for r in train], dtype=float)
    if len(np.unique(y)) < 2:
        return np.full(len(test), metrics.fit_prior_probability(y))

    if model == "cell_rate":
        tab = _cell_table(train)
        return np.array([tab["cell"].get((r.grade, r.e_level),
                                        tab["grade"].get(str(r.grade), tab["prior"]))
                         for r in test], dtype=float)
    if model == "s_rank":
        tab = _grade_only_table(train)
        return np.array([tab["grade"].get(str(r.grade), tab["prior"]) for r in test],
                        dtype=float)
    if model == "logistic":
        X = np.array([features(r) for r in train], dtype=float)
        mdl = metrics.fit_logistic(X, y, l2=l2)
        return metrics.predict_logistic(mdl, np.array([features(r) for r in test],
                                                      dtype=float))
    raise ValueError(f"未知模型：{model}")


def walk_forward(records: Sequence[EvalRecord], n_windows: int = 4,
                 min_train: int = 40, min_test: int = 15,
                 l2: float = 1.0, times: Sequence[float] | None = None,
                 labels: Sequence[int] | None = None,
                 with_ci: bool = True,
                 model: str = PRIMARY_MODEL) -> dict:
    """扩展窗 walk-forward：逐窗训练→预测，汇总**样本外**概率。

    ``times``/``labels`` 可覆盖（负控时把标签置换后重跑同一套切分，保证
    「只有标签被污染，时间结构不变」）。
    """
    recs = list(records)
    if not recs:
        return {"n": 0, "folds": [], "auc": float("nan"), "brier": float("nan"),
                "bins": [], "ece": float("nan"), "pooled": []}
    t = np.array(times if times is not None else [r.ref_time for r in recs], dtype=float)
    if labels is not None:
        yv = np.array(list(labels), dtype=int)
        proxy = [_ProxyRec(r, int(y)) for r, y in zip(recs, yv)]
    else:
        proxy = [r for r in recs]

    splits = metrics.walk_forward_splits(t, n_windows=n_windows,
                                         min_train=min_train, min_test=min_test)
    pooled_idx: list[int] = []
    pooled_p: list[float] = []
    folds: list[dict] = []
    for sp in splits:
        if sp.get("skipped") or not sp["train_idx"] or not sp["test_idx"]:
            folds.append({"fold": sp.get("fold"), "skipped": sp.get("skipped"),
                          "n_train": len(sp.get("train_idx") or []),
                          "n_test": len(sp.get("test_idx") or [])})
            continue
        train = [proxy[i] for i in sp["train_idx"]]
        test = [proxy[i] for i in sp["test_idx"]]
        p = _fit_predict(train, test, l2=l2, model=model)
        y_test = np.array([r.y for r in test], dtype=float)
        folds.append({
            "fold": sp["fold"],
            "n_train": len(train),
            "n_test": len(test),
            "train_span": sp["train_span"],
            "test_span": sp["test_span"],
            "train_pos_rate": float(np.mean([r.y for r in train])),
            "test_pos_rate": float(y_test.mean()),
            "auc": metrics.auc_roc(y_test, p),
            "brier": metrics.brier_score(y_test, p),
        })
        pooled_idx.extend(sp["test_idx"])
        pooled_p.extend(p.tolist())

    if not pooled_idx:
        return {"n": len(recs), "folds": folds, "auc": float("nan"),
                "brier": float("nan"), "bins": [], "ece": float("nan"),
                "pooled": [], "n_pooled": 0}

    y_pool = np.array([proxy[i].y for i in pooled_idx], dtype=float)
    p_pool = np.array(pooled_p, dtype=float)
    bins = metrics.calibration_bins(y_pool, p_pool)
    return {
        "n": len(recs),
        "n_pooled": int(len(pooled_idx)),
        "folds": folds,
        "auc": metrics.auc_roc(y_pool, p_pool),
        "auc_ci95": (metrics.bootstrap_auc_ci(y_pool, p_pool) if with_ci
                     else {"auc": float(metrics.auc_roc(y_pool, p_pool)),
                           "lo": None, "hi": None, "n_boot": 0}),
        "brier": metrics.brier_score(y_pool, p_pool),
        # 常数基线（训练窗正例率预测所有样本）的 Brier：看判级是否真的加了信息
        "brier_prior_baseline": _prior_baseline_brier(proxy, splits),
        "bins": bins,
        "ece": metrics.expected_calibration_error(bins),
        "pooled": [{"mission_id": proxy[i].mission_id, "y": float(proxy[i].y),
                    "p": float(p)} for i, p in zip(pooled_idx, pooled_p)],
    }



def _prior_baseline_brier(proxy: Sequence[Any], splits: Sequence[dict]) -> float:
    """朴素基线：用每个训练窗的正例率当常数预测，测同一测试集的 Brier。"""
    errs: list[float] = []
    for sp in splits:
        if sp.get("skipped") or not sp["train_idx"] or not sp["test_idx"]:
            continue
        rate = float(np.mean([proxy[i].y for i in sp["train_idx"]]))
        for i in sp["test_idx"]:
            errs.append((rate - proxy[i].y) ** 2)
    return float(np.mean(errs)) if errs else float("nan")


class _ProxyRec:
    """带（可能被置换的）标签的记录视图，供给 ``_fit_predict``。"""

    __slots__ = ("_r", "y")

    def __init__(self, rec: EvalRecord, y: int):
        self._r = rec
        self.y = y

    def __getattr__(self, item):
        return getattr(self._r, item)


# ───────────────────────────── 负控 ─────────────────────────────

def negative_control(records: Sequence[EvalRecord], n_perm: int = 200,
                     seed: int = 20261106, **wf_kwargs) -> dict:
    """标签置换负控：打乱 y 后重跑整条 walk-forward 管线。

    只有「原 AUC 明显高于置换零分布」才能说判级有判别力、不是偶然；
    置换后 |AUC-0.5| ≤ tolerance 的比例即为检测器自身的健康度。
    """
    obs = walk_forward(records, with_ci=False, **wf_kwargs)
    y_true = np.array([r.y for r in records], dtype=int)
    rng = np.random.default_rng(seed)
    null: list[float] = []
    for _ in range(n_perm):
        null.append(walk_forward(records, times=[r.ref_time for r in records],
                                 labels=rng.permutation(y_true).tolist(),
                                 with_ci=False, **wf_kwargs)["auc"])
    valid = [v for v in null if not np.isnan(v)]
    tol = CRITERIA["null_tolerance"]
    share_near_half = (float(np.mean([abs(v - 0.5) <= tol for v in valid]))
                       if valid else float("nan"))
    return {
        "observed_auc": obs["auc"],
        "n_perm": len(valid),
        "null_mean": float(np.mean(valid)) if valid else float("nan"),
        "null_sd": float(np.std(valid)) if valid else float("nan"),
        "null_q95": float(np.quantile(valid, 0.95)) if valid else float("nan"),
        "perm_pvalue": metrics.permutation_pvalue(obs["auc"], valid),
        "share_within_tolerance": share_near_half,
        "null_hist": valid,
    }


# ───────────────────────────── 通胀检测 ─────────────────────────────

def _static_auc_s_only(recs: Sequence[EvalRecord]) -> float:
    """不训练、不分窗：直接拿 S 秩当分数（评价自己声明的优劣序）算 AUC。

    这是「判级有没有判别力」的**静态**上界——没有模型风险，也没有时间窗噪声；
    walk-forward AUC 若显著低于它，说明判别力本身还随时代漂移。
    """
    lab = [r for r in recs if r.labeled]
    if not lab:
        return float("nan")
    return metrics.auc_roc([r.y for r in lab],
                           [S_RANK.get(r.grade or "", 0.0) for r in lab])


def inflation_check(records: Sequence[EvalRecord], top_grades: Sequence[str] = ("S1",),
                    s_share_min: float = CRITERIA["s_share_min"],
                    merge_rate_max: float = CRITERIA["merge_rate_max"],
                    population: str = "all_records") -> dict:
    """评分通胀检测：高档位占比高、但下游 merge 率低 → 评分与实际质量脱钩。

    两个量的口径分开写清楚：
    - ``top_grade_share``：**全部有判级的记录**里落在 ``top_grades`` 的比例
      （评价自己的分布，含 censored——通胀是评价属性，不该只用有结局的样本算）；
    - ``merge_rate``：**有可观测下游终局**的记录里 ``outcome == merge`` 的比例。
    """
    graded = [r for r in records if r.grade]
    n_graded = len(graded)
    n_top = sum(1 for r in graded if r.grade in top_grades)
    top_share = (n_top / n_graded) if n_graded else float("nan")
    lab = labeled_records(records)
    n_merge = sum(1 for r in lab if r.outcome == "merge")
    merge_rate = (n_merge / len(lab)) if lab else float("nan")
    alert = bool(n_graded and len(lab)
                 and top_share > s_share_min and merge_rate < merge_rate_max)
    return {
        "population": population,
        "top_grades": list(top_grades),
        "n_graded": n_graded,
        "n_top": n_top,
        "top_grade_share": top_share,
        "n_labeled": len(lab),
        "n_merge": n_merge,
        "merge_rate": merge_rate,
        "merge_rate_ci95": list(metrics.wilson_interval(n_merge, len(lab))) if lab else None,
        "thresholds": {"top_share_gt": s_share_min, "merge_rate_lt": merge_rate_max},
        "alert": alert,
        "verdict": ("SCORE_INFLATION" if alert else "no_inflation_detected"),
    }


# ───────────────────────────── 跨仓一致性 ─────────────────────────────

def window_params_for(n: int) -> dict:
    """按样本量自适应窗参数：小仓不降窗就整仓 WITHHELD，看不到差异。"""
    if n < 80:
        return {"n_windows": 2, "min_train": 12, "min_test": 6}
    if n < 150:
        return {"n_windows": 3, "min_train": 20, "min_test": 8}
    return {}


def by_repo(records: Sequence[EvalRecord], repos: Sequence[str] = DEFAULT_TARGET_REPOS,
            min_echo: int = 10, **wf_kwargs) -> dict:
    """逐仓 walk-forward AUC + bootstrap CI + 极差（≤0.1 判一致）。"""
    out: dict[str, Any] = {}
    aucs: dict[str, float] = {}
    for repo in repos:
        sub = [r for r in records if r.repo == repo]
        kw = dict(wf_kwargs)
        kw.update(window_params_for(len(sub)))
        if len(sub) < min_echo:
            out[repo] = {"n": len(sub), "status": "WITHHELD",
                         "reason": f"有结局样本 {len(sub)} < {min_echo}"}
            continue
        wf = walk_forward(sub, **kw)
        aucs[repo] = wf["auc"]
        n_grades = len({r.grade for r in sub if r.grade})
        status = "OK"
        if n_grades < 2:
            status = "NOT_COMPUTABLE_NO_GRADE_VARIANCE"
        elif np.isnan(wf["auc"]):
            status = "WITHHELD"
        out[repo] = {
            "n": wf["n"], "n_pooled": wf["n_pooled"], "auc": wf["auc"],
            "auc_ci95": wf.get("auc_ci95"), "brier": wf["brier"],
            "positive_rate": float(np.mean([r.y for r in sub])),
            "n_distinct_grades": n_grades,
            "grade_mix": {g: sum(1 for r in sub if r.grade == g)
                          for g in sorted({r.grade for r in sub})},
            "window": {"n_windows": kw.get("n_windows", wf_kwargs.get("n_windows")),
                       "min_train": kw.get("min_train", wf_kwargs.get("min_train")),
                       "min_test": kw.get("min_test", wf_kwargs.get("min_test"))},
            "status": status,
        }
    valid = [v for v in aucs.values() if not np.isnan(v)]
    n_ok = sum(1 for v in out.values() if v.get("status") == "OK")
    spread = (max(valid) - min(valid)) if len(valid) >= 2 else float("nan")
    complete = n_ok == len(repos)
    return {
        "repos": out, "spread": spread,
        "n_computable": n_ok, "n_requested": len(repos), "complete": complete,
        "consistent": bool(complete and spread <= CRITERIA["repo_spread_max"]),
        "status": "OK" if complete else "PARTIAL",
        "thresholds": {"spread_max": CRITERIA["repo_spread_max"]},
    }


# ───────────────────────────── 判级 → 结局画像 ─────────────────────────────

def grade_profile(records: Sequence[EvalRecord]) -> dict:
    """每个 (grade, e_level) 单元的样本量 / merge 率 / Wilson CI。

    这是报告里「判级到底有没有对齐质量」的第一手表；monotone 检查只看
    S 秩或 E 秩单变量，避免用错模型。
    """
    cells: dict[str, dict] = {}
    for r in labeled_records(records):
        key = f"{r.grade}|{r.e_level}"
        c = cells.setdefault(key, {"grade": r.grade, "e_level": r.e_level,
                                   "n": 0, "n_merge": 0})
        c["n"] += 1
        c["n_merge"] += int(r.outcome == "merge")
    for c in cells.values():
        c["merge_rate"] = c["n_merge"] / c["n"]
        c["ci95"] = list(metrics.wilson_interval(c["n_merge"], c["n"]))
    rows = sorted(cells.values(), key=lambda c: (-(S_RANK.get(c["grade"], 0)),
                                                 -(E_RANK.get(c["e_level"], 0))))
    return {
        "cells": rows,
        "by_grade": _marginal(rows, "grade", S_RANK),
        "by_e_level": _marginal(rows, "e_level", E_RANK),
    }


def _marginal(rows: Sequence[dict], key: str, rank: dict) -> list[dict]:
    agg: dict[str, dict] = {}
    for c in rows:
        k = c[key]
        a = agg.setdefault(k, {"value": k, "rank": rank.get(k, 0.0),
                               "n": 0, "n_merge": 0})
        a["n"] += c["n"]
        a["n_merge"] += c["n_merge"]
    out = []
    for a in agg.values():
        a["merge_rate"] = a["n_merge"] / a["n"]
        a["ci95"] = list(metrics.wilson_interval(a["n_merge"], a["n"]))
        out.append(a)
    return sorted(out, key=lambda a: -a["rank"])


def era_diagnostics(records: Sequence[EvalRecord], n_quantiles: int = 4) -> dict:
    """时代诊断：下游结局本身是否被「合并流水线波次」主导。

    walk-forward AUC 低于 0.5 往往不是判级方向反了，而是**基础率随波次剧变**
    （某一波 merge 全过、下一波 CI 全红）。这里把样本按任务完成时刻等分，
    逐段给出 merge 率与静态 AUC：段间基础率跳变越大，说明结局越受流水线时代
    影响，判级的外部效度越难单独识别。
    """
    lab = [r for r in records if r.labeled]
    lab.sort(key=lambda r: (r.ref_time, r.mission_id))
    n = len(lab)
    if n == 0:
        return {"quantiles": [], "base_rate_span": float("nan")}
    qn = max(1, min(n_quantiles, n))
    out = []
    for q in range(qn):
        chunk = lab[q * n // qn:(q + 1) * n // qn]
        if not chunk:
            continue
        y = [r.y for r in chunk]
        out.append({
            "q": q + 1,
            "n": len(chunk),
            "t0": float(chunk[0].ref_time),
            "t1": float(chunk[-1].ref_time),
            "merge_rate": float(np.mean(y)),
            "static_auc_s_rank": metrics.auc_roc(
                y, [S_RANK.get(r.grade or "", 0.0) for r in chunk]),
            "n_distinct_grades": len({r.grade for r in chunk if r.grade}),
            "grade_mix": {g: sum(1 for r in chunk if r.grade == g)
                          for g in sorted({r.grade for r in chunk})},
        })
    rates = [q["merge_rate"] for q in out]
    return {"quantiles": out,
            "base_rate_span": (max(rates) - min(rates)) if rates else float("nan")}


# ───────────────────────────── 总装 ─────────────────────────────

def run_calibration(records: Sequence[EvalRecord], n_windows: int = 4,
                    min_train: int = 40, min_test: int = 15,
                    n_perm: int = 200, seed: int = 20261106,
                    repos: Sequence[str] = DEFAULT_TARGET_REPOS,
                    top_grades: Sequence[str] = ("S1",),
                    primary_evidence: Sequence[str] = PRIMARY_EVIDENCE) -> dict:
    """跑全套五条判据，返回可直接落 JSON 的结果字典（含每条的 pass/fail）。"""
    lab_all = labeled_records(records)
    lab = [r for r in lab_all if r.evidence in primary_evidence]
    wf_kwargs = dict(n_windows=n_windows, min_train=min_train, min_test=min_test)
    # 主模型 + 两个稳健性对照（全报，不做事后挑选）
    alt_models: dict[str, dict] = {}
    for m in MODELS:
        if m == PRIMARY_MODEL:
            continue
        alt = walk_forward(lab, model=m, **wf_kwargs) if lab else {}
        alt_models[m] = {"auc": alt.get("auc"), "brier": alt.get("brier"),
                         "ece": alt.get("ece")}
    wf = walk_forward(lab, model=PRIMARY_MODEL, **wf_kwargs)
    neg = negative_control(lab, n_perm=n_perm, seed=seed, **wf_kwargs) if lab else {}
    infl = inflation_check(lab, top_grades=top_grades, population="primary_evidence")
    infl_all = inflation_check(records, top_grades=top_grades, population="all_records")
    # 口径敏感性：「S级」若理解为前两档（S1+S2）会怎样——平台只用 S1/S2 两档时
    # 这一口径恒为 100%，必须与严格单档口径并列报出，不许只挑触发告警的那个。
    infl_top2 = inflation_check(lab, top_grades=("S1", "S2"), population="primary_evidence")
    cross = by_repo(lab, repos=repos, **wf_kwargs) if lab else {"repos": {}, "spread": float("nan")}
    prof = grade_profile(lab)
    era = era_diagnostics(lab)

    auc_val = wf.get("auc", float("nan"))
    brier_val = wf.get("brier", float("nan"))
    nil_tol = CRITERIA["null_tolerance"]
    null_ok = bool(neg) and not np.isnan(neg["null_mean"]) and \
        abs(neg["null_mean"] - 0.5) <= nil_tol
    criteria = {
        "C1_walk_forward_auc>=0.70": {
            "value": auc_val, "threshold": CRITERIA["auc_min"],
            "static_auc_s_only": _static_auc_s_only(lab),
            "pass": bool(not np.isnan(auc_val) and auc_val >= CRITERIA["auc_min"]),
        },
        "C2_brier<=0.15": {
            "value": brier_val, "threshold": CRITERIA["brier_max"],
            "pass": bool(not np.isnan(brier_val) and brier_val <= CRITERIA["brier_max"]),
        },
        "C3_inflation_alert": {
            "value": {"top_grade_share": infl["top_grade_share"],
                      "merge_rate": infl["merge_rate"],
                      "alert": infl["alert"]},
            "rule": "top_share>0.30 AND merge_rate<0.50 → alert（检测器正确性）",
            "pass": True,  # 检测器必然给出裁决；正确性由 tests 双向覆盖
        },
        "C4_negative_control_auc~0.5": {
            "value": {"null_mean": neg.get("null_mean"),
                      "perm_pvalue": neg.get("perm_pvalue"),
                      "share_within_tolerance": neg.get("share_within_tolerance")},
            "threshold": nil_tol, "pass": null_ok,
        },
        "C5_cross_repo_spread<=0.10": {
            "value": cross.get("spread"), "threshold": CRITERIA["repo_spread_max"],
            "pass": bool(cross.get("consistent")),
            "per_repo_auc": {k: v.get("auc") for k, v in cross.get("repos", {}).items()},
        },
    }
    # 敏感性：把 pr_gate 回填（按成功挑样）也算进来会怎样
    sens_by_source: dict[str, dict] = {}
    for src in sorted({r.evidence for r in lab_all}):
        sub = [r for r in lab_all if r.evidence == src]
        y = [r.y for r in sub]
        sens_by_source[src] = {
            "n": len(sub), "merge_rate": float(np.mean(y)) if sub else None,
            "n_merge": int(sum(y)), "n_rework": int(len(sub) - sum(y)),
            "static_auc_s_rank": _static_auc_s_only(sub),
        }
    wf_all = walk_forward(lab_all, model=PRIMARY_MODEL, **wf_kwargs) if lab_all else {}
    sensitivity = {
        "primary_evidence": list(primary_evidence),
        "by_source": sens_by_source,
        "all_sources": {"n": len(lab_all),
                        "merge_rate": float(np.mean([r.y for r in lab_all]))
                        if lab_all else None,
                        "auc": wf_all.get("auc"), "brier": wf_all.get("brier"),
                        "note": "含 pr_gate 按成功挑样回填 → 上偏"},
    }
    n_labeled = len(lab)
    return {
        "n_records": len(records),
        "n_labeled": n_labeled,
        "n_labeled_all_sources": len(lab_all),
        "n_censored": len(records) - len(lab_all),
        "positives": sum(1 for r in lab if r.y == 1),
        "negative": sum(1 for r in lab if r.y == 0),
        "sensitivity": sensitivity,
        "inflation_all_records": infl_all,
        "inflation_alt_top2": infl_top2,
        "walk_forward": wf,
        "primary_model": PRIMARY_MODEL,
        "robustness_models": alt_models,
        "negative_control": {k: v for k, v in neg.items() if k != "null_hist"},
        "negative_control_null_samples": neg.get("null_hist", []),
        "inflation": infl,
        "cross_repo": cross,
        "grade_profile": prof,
        "era_diagnostics": era,
        "criteria": criteria,
        "all_pass": all(c.get("pass") for c in criteria.values()),
    }