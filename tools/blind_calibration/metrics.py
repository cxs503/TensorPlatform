#!/usr/bin/env python3
"""概率预测计分原语：可靠性曲线 / Brier 分数 / Murphy 三分分解。

零领域语义：本模块只认识 ``(p, y)`` 两个数列——``p`` 是承诺时给出的概率
（0..1 的有限实数），``y`` 是事后观测到的二元结局（0/1）。谁给的概率、结局
是什么物理量，一律由调用方以工件/配置注入，模块内不出现任何域词条或域阈值。

三个判据（全部机器可裁）
========================
A. **可靠性曲线**（reliability curve）：把 ``p`` 按等宽分箱（默认 10 箱，
   ``[0,0.1),[0.1,0.2),…,[0.9,1.0]``，``p=1.0`` 归末箱）后，逐箱给出
   平均预测概率 ``p_k`` 与实际频率 ``f_k``；``max_deviation = max_k |p_k - f_k|``
   只在非空箱上取（空箱如实计数并列出，不当 0 处理）。
B. **Brier 分数**：``BS = mean((p - y)^2)``，随机预测基线 = 0.25（p≡0.5）。
C. **Murphy 三分分解**：``BS_binned = REL - RES + UNC``，其中

   * ``UNC = ȳ(1 - ȳ)``                       —— 不确定性（结局本身的难度，与预测无关）
   * ``RES = Σ_k n_k (f_k - ȳ)^2 / N``        —— 分辨力（能把组间频率拉开多少）
   * ``REL = Σ_k n_k (p_k - f_k)^2 / N``      —— 可靠性（组内偏差，越小越好）
   * ``BS_binned = Σ_k n_k (p_k - f_k)^2 / N + Σ_k n_k f_k(1 - f_k) / N``

   恒等式在**分箱后**的 Brier 上精确成立（代数恒等，残差应 ≤1e-12）。逐样本的
   ``BS_raw`` 与 ``BS_binned`` 之差 = ``within_bin_term``（箱内概率离散带来的项），
   本模块把三项一并报告，避免用「分箱版」冒充「逐样本版」。

诚实边界
========
* 分箱是**粗化**：``REL``/``RES`` 依赖分箱方案，换箱数会改动数值（BS 不变，
  分箱版 BS 会变）。报告里必须同时写 ``n_bins`` 与 ``bin_edges``。
* 小样本的可靠性曲线**逐箱只有 1~2 个点**，``max_deviation`` 天然容易「通过」。
  模块因此同时输出每箱样本数与二项置信区间，判据通过与否必须连同这些读数一起看。
* 本模块只做**计分**，不产生预测、不判断预测从哪来；预测来源的盲性由
  ``tools.abm.blind``（承诺-揭示）与哈希链账本负责，本模块不背书。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

DEFAULT_N_BINS = 10
#: Brier 的随机预测基线（p ≡ 0.5 时 BS = mean((0.5 - y)^2) = 0.25）
RANDOM_BASELINE_BRIER = 0.25
#: 三分分解恒等式的机器判据阈值
IDENTITY_TOL = 1e-10


# --------------------------------------------------------------------------- #
# 输入校验
# --------------------------------------------------------------------------- #
def _finite(x: Any, name: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError(f"{name} 必须是实数: {x!r}")
    v = float(x)
    if not math.isfinite(v):
        raise ValueError(f"{name} 必须是有限实数: {x!r}")
    return v


def validate_pairs(p: Sequence[float], y: Sequence[int]) -> tuple[list[float], list[int]]:
    """校验并归一化 ``(p, y)``；不合格一律抛错（不静默丢弃样本）。"""
    ps = [_finite(v, "p") for v in p]
    ys = [_finite(v, "y") for v in y]
    if len(ps) != len(ys):
        raise ValueError(f"长度不一致: len(p)={len(ps)} len(y)={len(ys)}")
    if not ps:
        raise ValueError("样本为空")
    for v in ps:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"概率必须在 [0,1]: {v}")
    out_y: list[int] = []
    for v in ys:
        if v not in (0.0, 1.0):
            raise ValueError(f"结局必须是 0 或 1: {v}")
        out_y.append(int(v))
    return ps, out_y


# --------------------------------------------------------------------------- #
# 分箱
# --------------------------------------------------------------------------- #
def bin_edges(n_bins: int = DEFAULT_N_BINS) -> list[float]:
    if not isinstance(n_bins, int) or n_bins < 1:
        raise ValueError(f"n_bins 必须是正整数: {n_bins!r}")
    return [i / n_bins for i in range(n_bins + 1)]


def bin_index(prob: float, n_bins: int = DEFAULT_N_BINS) -> int:
    """等宽分箱的下标；左闭右开，``prob == 1.0`` 归末箱。"""
    v = _finite(prob, "prob")
    if not (0.0 <= v <= 1.0):
        raise ValueError(f"概率必须在 [0,1]: {prob!r}")
    idx = int(math.floor(v * n_bins))
    return n_bins - 1 if idx >= n_bins else idx


def _bins(ps: Sequence[float], ys: Sequence[int], n_bins: int) -> list[dict[str, Any]]:
    idxs = [bin_index(v, n_bins) for v in ps]
    edges = bin_edges(n_bins)
    out: list[dict[str, Any]] = []
    for k in range(n_bins):
        members = [i for i, b in enumerate(idxs) if b == k]
        n_k = len(members)
        row: dict[str, Any] = {
            "index": k,
            "lo": edges[k],
            "hi": edges[k + 1],
            "n": n_k,
            "mean_prob": None,
            "observed_freq": None,
            "deviation": None,
        }
        if n_k:
            mp = sum(ps[i] for i in members) / n_k
            of = sum(ys[i] for i in members) / n_k
            row["mean_prob"] = mp
            row["observed_freq"] = of
            row["deviation"] = mp - of
        out.append(row)
    return out


# --------------------------------------------------------------------------- #
# 判据 A：可靠性曲线
# --------------------------------------------------------------------------- #
def reliability_curve(p: Sequence[float], y: Sequence[int],
                      n_bins: int = DEFAULT_N_BINS) -> dict[str, Any]:
    """逐箱「平均预测概率 vs 实际频率」；返回最大偏差与 ECE。"""
    ps, ys = validate_pairs(p, y)
    rows = _bins(ps, ys, n_bins)
    n = len(ps)
    occupied = [r for r in rows if r["n"]]
    deviations = [abs(r["deviation"]) for r in occupied]
    ece = sum(r["n"] * abs(r["deviation"]) for r in occupied) / n
    max_dev = max(deviations) if deviations else None
    max_row = None
    if occupied:
        worst = max(occupied, key=lambda r: abs(r["deviation"]))
        max_row = {"index": worst["index"], "lo": worst["lo"], "hi": worst["hi"],
                   "n": worst["n"], "mean_prob": worst["mean_prob"],
                   "observed_freq": worst["observed_freq"],
                   "deviation": worst["deviation"]}
    return {
        "n": n,
        "n_bins": n_bins,
        "bin_edges": bin_edges(n_bins),
        "bins": rows,
        "n_occupied_bins": len(occupied),
        "n_empty_bins": n_bins - len(occupied),
        "empty_bin_indices": [r["index"] for r in rows if not r["n"]],
        "max_deviation": max_dev,
        "max_deviation_bin": max_row,
        "ece_unweighted_by_n": (sum(deviations) / len(deviations)) if deviations else None,
        "ece": ece,
    }


# --------------------------------------------------------------------------- #
# 判据 B：Brier 分数
# --------------------------------------------------------------------------- #
def brier_score(p: Sequence[float], y: Sequence[int]) -> float:
    """逐样本 Brier：``mean((p - y)^2)``（0 完美，1 全错向，0.5 恒错）。"""
    ps, ys = validate_pairs(p, y)
    return sum((a - b) ** 2 for a, b in zip(ps, ys)) / len(ps)


def binned_brier(p: Sequence[float], y: Sequence[int],
                 n_bins: int = DEFAULT_N_BINS) -> float:
    """分箱版 Brier：箱内一律用该箱的平均概率 ``p_k`` 计分（三分分解的对象）。"""
    ps, ys = validate_pairs(p, y)
    idxs = [bin_index(v, n_bins) for v in ps]
    means: dict[int, float] = {}
    counts: dict[int, int] = {}
    for b in idxs:
        counts[b] = counts.get(b, 0) + 1
    for i, b in enumerate(idxs):
        means[b] = means.get(b, 0.0) + ps[i]
    for b in means:
        means[b] /= counts[b]
    return sum((means[b] - yv) ** 2 for b, yv in zip(idxs, ys)) / len(ps)


def climatology_brier(y: Sequence[int]) -> float:
    """气候基准（只用结局基率、不看任何预测）的 Brier。"""
    _, ys = validate_pairs([0.5] * len(y), y)
    base = sum(ys) / len(ys)
    return sum((base - v) ** 2 for v in ys) / len(ys)


def brier_skill_score(p: Sequence[float], y: Sequence[int]) -> dict[str, Any]:
    """Brier skill score：``1 - BS / BS_climatology``（>0 优于气候基准）。"""
    ps, ys = validate_pairs(p, y)
    bs = brier_score(ps, ys)
    bs_ref = climatology_brier(ys)
    if bs_ref == 0.0:  # 结局全同：气候基准即完美，skill 无定义
        return {"brier": bs, "brier_climatology": bs_ref, "skill": None,
                "note": "结局无变异（ȳ∈{0,1}），气候基准 BS=0，skill 无定义"}
    return {"brier": bs, "brier_climatology": bs_ref, "skill": 1.0 - bs / bs_ref}


def log_loss(p: Sequence[float], y: Sequence[int], eps: float = 1e-12) -> float:
    ps, ys = validate_pairs(p, y)
    total = 0.0
    for a, b in zip(ps, ys):
        q = min(max(a, eps), 1.0 - eps)
        total += -(b * math.log(q) + (1 - b) * math.log(1 - q))
    return total / len(ps)


# --------------------------------------------------------------------------- #
# 判据 C：Murphy 三分分解
# --------------------------------------------------------------------------- #
def murphy_decomposition(p: Sequence[float], y: Sequence[int],
                         n_bins: int = DEFAULT_N_BINS) -> dict[str, Any]:
    """``BS_binned = REL - RES + UNC``，逐项给出并复算恒等残差。"""
    ps, ys = validate_pairs(p, y)
    n = len(ps)
    rows = _bins(ps, ys, n_bins)
    y_bar = sum(ys) / n
    uncertainty = y_bar * (1.0 - y_bar)
    reliability = 0.0
    resolution = 0.0
    for r in rows:
        if not r["n"]:
            continue
        w = r["n"] / n
        reliability += w * (r["mean_prob"] - r["observed_freq"]) ** 2
        resolution += w * (r["observed_freq"] - y_bar) ** 2
    bs_binned = binned_brier(ps, ys, n_bins)
    bs_raw = brier_score(ps, ys)
    residual = (reliability - resolution + uncertainty) - bs_binned
    return {
        "n": n,
        "n_bins": n_bins,
        "bin_edges": bin_edges(n_bins),
        "base_rate": y_bar,
        "reliability": reliability,
        "resolution": resolution,
        "uncertainty": uncertainty,
        "brier_binned": bs_binned,
        "brier_raw": bs_raw,
        "within_bin_term": bs_raw - bs_binned,
        "identity_residual": residual,
        "identity_holds": abs(residual) <= IDENTITY_TOL,
        "identity_tol": IDENTITY_TOL,
        "bins": rows,
    }


# --------------------------------------------------------------------------- #
# 组合计分（一键判据）
# --------------------------------------------------------------------------- #
def score(p: Sequence[float], y: Sequence[int], n_bins: int = DEFAULT_N_BINS,
          *, max_deviation_max: float = 0.1,
          brier_max: float = RANDOM_BASELINE_BRIER) -> dict[str, Any]:
    """把 A/B/C 三判据折成机器可裁的 JSON（判据阈值可注入，不写死域语义）。"""
    curve = reliability_curve(p, y, n_bins)
    dec = murphy_decomposition(p, y, n_bins)
    skill = brier_skill_score(p, y)
    checks = {
        "reliability_max_deviation_lt": {
            "threshold": max_deviation_max,
            "value": curve["max_deviation"],
            "pass": curve["max_deviation"] is not None
            and curve["max_deviation"] < max_deviation_max,
        },
        "brier_lt_random_baseline": {
            "threshold": brier_max,
            "value": dec["brier_raw"],
            "pass": dec["brier_raw"] < brier_max,
        },
        "murphy_identity": {
            "threshold": dec["identity_tol"],
            "value": abs(dec["identity_residual"]),
            "pass": dec["identity_holds"],
        },
    }
    return {
        "schema": "blind_calibration/score/v1",
        "n": dec["n"],
        "n_bins": n_bins,
        "checks": checks,
        "all_pass": all(c["pass"] for c in checks.values()),
        "reliability_curve": curve,
        "murphy": dec,
        "brier": {"raw": dec["brier_raw"], "binned": dec["brier_binned"],
                  "random_baseline": RANDOM_BASELINE_BRIER,
                  "climatology": skill["brier_climatology"],
                  "skill_vs_climatology": skill["skill"],
                  "log_loss": log_loss(p, y)},
        "notes": [
            "max_deviation 只在非空箱上取；空箱数见 reliability_curve.n_empty_bins",
            "三分分解恒等式对象是分箱版 Brier；逐样本版与分箱版之差见 within_bin_term",
        ],
    }


def bootstrap_ci(p: Sequence[float], y: Sequence[int], *,
                 statistic: str = "brier", n_boot: int = 2000,
                 alpha: float = 0.05, seed: int = 0) -> dict[str, Any]:
    """百分位自助法置信区间：把「点估计」变成「点估计 ± 抽样噪声」。

    小样本的 ``max_deviation`` 与 ``Brier`` 都该看区间，否则容易把噪声当结论。
    """
    import random

    ps, ys = validate_pairs(p, y)
    n = len(ps)
    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        bp = [ps[i] for i in idx]
        by = [ys[i] for i in idx]
        if statistic == "brier":
            stats.append(brier_score(bp, by))
        elif statistic == "max_deviation":
            md = reliability_curve(bp, by)["max_deviation"]
            stats.append(float(md) if md is not None else 0.0)
        else:
            raise ValueError(f"未知统计量: {statistic}")
    stats.sort()
    lo = stats[int(math.floor((alpha / 2) * n_boot))]
    hi = stats[min(n_boot - 1, int(math.ceil((1 - alpha / 2) * n_boot)) - 1)]
    point = brier_score(ps, ys) if statistic == "brier" else reliability_curve(ps, ys)["max_deviation"]
    return {"statistic": statistic, "point": point, "lo": lo, "hi": hi,
            "alpha": alpha, "n_boot": n_boot, "seed": seed,
            "method": "percentile bootstrap (resample cases with replacement)"}


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """单箱频率的 Wilson 区间（小样本箱里频率的不确定性有多大）。"""
    if n <= 0:
        return (0.0, 1.0)
    phat = k / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def curve_with_intervals(p: Sequence[float], y: Sequence[int],
                         n_bins: int = DEFAULT_N_BINS) -> dict[str, Any]:
    """可靠性曲线 + 每箱 Wilson 区间（避免把 1 样本箱的「0 偏差」当校准良好）。"""
    curve = reliability_curve(p, y, n_bins)
    ps, ys = validate_pairs(p, y)
    idxs = [bin_index(v, n_bins) for v in ps]
    for row in curve["bins"]:
        members = [i for i, b in enumerate(idxs) if b == row["index"]]
        k = sum(ys[i] for i in members)
        lo, hi = wilson_interval(k, len(members))
        row["observed_freq_ci"] = [lo, hi]
        row["n_positive"] = k
    return curve


def groupwise(p: Sequence[float], y: Sequence[int], groups: Iterable[str],
              n_bins: int = DEFAULT_N_BINS) -> dict[str, Any]:
    """按组（如按波次/来源）分别计分，暴露「整体校准好、局部校准坏」。"""
    gl = [str(g) for g in groups]
    ps, ys = validate_pairs(p, y)
    if len(gl) != len(ps):
        raise ValueError("groups 长度与样本不一致")
    out: dict[str, Any] = {}
    for g in sorted(set(gl)):
        sel = [i for i, x in enumerate(gl) if x == g]
        if len(sel) < 2:
            out[g] = {"n": len(sel), "skipped": "样本量 <2，不单独计分"}
            continue
        out[g] = {"n": len(sel),
                  "brier": brier_score([ps[i] for i in sel], [ys[i] for i in sel]),
                  "max_deviation": reliability_curve(
                      [ps[i] for i in sel], [ys[i] for i in sel], n_bins)["max_deviation"]}
    return out