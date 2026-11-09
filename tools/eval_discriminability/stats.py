"""纯统计核：序贯趋势检验（Mann-Kendall / Theil-Sen）与判别力不确定性。

无 IO、无 DB、无第三方统计库依赖（仅 numpy + math）——可离线单测、可审计。

为什么自带一份 Mann-Kendall 而不引 scipy：本仓运行环境不保证有 scipy，
且通胀检测的**结（tie）修正**必须显式可查（判级是 5 档离散序，tie 极多，
不修正会把方差算大、p 值算小 → 假报警）。
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import numpy as np


# ───────────────────────────────── 正态尾概率 ─────────────────────────────────

def norm_sf(z: float) -> float:
    """标准正态上尾 P(Z > z)（用 erfc，避免 erf 精度损失）。"""
    return 0.5 * math.erfc(float(z) / math.sqrt(2.0))


def norm_isf(p: float) -> float:
    """标准正态上尾分位点：解 ``P(Z > x) = p``（双侧 CI 用 ``norm_isf(alpha/2)``）。

    实现选择：直接对 :func:`norm_sf` 做**二分**（上尾概率对 x 严格单调递减），
    区间 ``[-40, 40]``、60 次迭代 → 分辨率 < 1e-15。相比有理逼近/牛顿法，
    二分没有初值分支问题（不会出现负根/除零），可审计性更好——这里只为置信
    区间服务，性能无关。
    """
    p = float(p)
    if not 0.0 < p < 1.0:
        raise ValueError(f"p 越界：{p}")
    lo, hi = -40.0, 40.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if norm_sf(mid) > p:      # 上尾概率过大 → 需要更大的 x
            lo = mid
        else:
            hi = mid
    return float(0.5 * (lo + hi))


# ─────────────────────────────── Mann-Kendall ───────────────────────────────

def mann_kendall(series: Sequence[float]) -> dict[str, Any]:
    """Mann-Kendall 趋势检验（非参数，逐对比较；含结修正与连续性修正）。

    ``S = Σ_{i<j} sign(x_j - x_i)``

    无趋势时 ``E[S] = 0``，``Var[S] = [n(n-1)(2n+5) - Σ_t t(t-1)(2t+5)] / 18``
    （``t`` = 各结组的长度）。连续性修正 ``Z = (S - sign(S)) / √Var``，
    双侧 ``p = 2·P(Z > |Z|)``。

    返回 ``S`` / ``var`` / ``z`` / ``p`` / ``tau``（Kendall tau-b 口径 =
    ``S / (n(n-1)/2)``，因为结被显式计入方差）与 ``n`` / ``n_ties``。
    单点或常数序列返回 ``p=nan`` 并显式标注——绝不用 p=1 伪装「无趋势」。
    """
    x = np.asarray([v for v in series if v is not None], dtype=float)
    x = x[~np.isnan(x)]
    n = int(x.size)
    if n < 3:
        return {"n": n, "S": 0, "var": float("nan"), "z": float("nan"),
                "p": float("nan"), "tau": float("nan"), "n_tie_groups": 0,
                "status": "insufficient_n"}

    s = 0
    for i in range(n - 1):
        d = x[i + 1:] - x[i]
        s += int((d > 0).sum()) - int((d < 0).sum())

    # 结修正
    values, counts = np.unique(x, return_counts=True)
    tie_groups = counts[counts > 1]
    tie_term = float(np.sum(tie_groups * (tie_groups - 1) * (2 * tie_groups + 5)))
    var = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0

    if var <= 0:
        return {"n": n, "S": int(s), "var": 0.0, "z": float("nan"),
                "p": float("nan"), "tau": 0.0,
                "n_tie_groups": int(tie_groups.size), "status": "degenerate"}

    z = (s - (1 if s > 0 else (-1 if s < 0 else 0))) / math.sqrt(var)
    p = 2.0 * norm_sf(abs(z))
    return {"n": n, "S": int(s), "var": float(var), "z": float(z),
            "p": float(min(1.0, p)), "tau": float(s / (n * (n - 1) / 2.0)),
            "n_tie_groups": int(tie_groups.size), "status": "ok"}


# ─────────────────────────────── Theil-Sen ───────────────────────────────

def theil_sen_slope(t: Sequence[float], y: Sequence[float]) -> float:
    """Theil-Sen 斜率 = 所有点对斜率的中位数（对离群点稳健）。"""
    tt = np.asarray(t, dtype=float)
    yy = np.asarray(y, dtype=float)
    if tt.size < 2:
        return float("nan")
    slopes = [(yy[j] - yy[i]) / (tt[j] - tt[i])
              for i in range(tt.size - 1) for j in range(i + 1, tt.size)
              if tt[j] != tt[i]]
    return float(np.median(slopes)) if slopes else float("nan")


def theil_sen_with_ci(t: Sequence[float], y: Sequence[float], *,
                      alpha: float = 0.05) -> dict[str, Any]:
    """Theil-Sen 斜率 + Kendall 型分布无关置信区间。

    构造：把全部 ``N = n(n-1)/2`` 个斜率排序，置信带由
    ``C = Z_{1-alpha/2}·√Var(S)`` 决定，取第 ``(N-C)/2`` 与 ``(N+C)/2`` 个斜率。
    ``Var(S)`` 与 :func:`mann_kendall` 同一结修正口径，故两者自洽。
    """
    tt = np.asarray(t, dtype=float)
    yy = np.asarray(y, dtype=float)
    n = tt.size
    mk = mann_kendall(yy)
    if n < 3 or mk["status"] != "ok":
        return {"slope": theil_sen_slope(t, y), "lo": float("nan"),
                "hi": float("nan"), "n": int(n), "alpha": alpha,
                "status": "insufficient_n"}
    slopes = np.array(sorted((yy[j] - yy[i]) / (tt[j] - tt[i])
                             for i in range(n - 1) for j in range(i + 1, n)
                             if tt[j] != tt[i]), dtype=float)
    N = slopes.size
    C = norm_isf(alpha / 2.0) * math.sqrt(mk["var"])
    lo_idx = int(max(0, min(N - 1, math.floor((N - C) / 2.0))))
    hi_idx = int(max(0, min(N - 1, math.ceil((N + C) / 2.0) - 1)))
    return {
        "slope": float(np.median(slopes)),
        "lo": float(slopes[lo_idx]),
        "hi": float(slopes[hi_idx]),
        "n": int(n), "n_pairs": int(N), "alpha": alpha,
        "z_crit": float(norm_isf(alpha / 2.0)),
        "status": "ok",
    }


# ─────────────────────────── AUC 的不确定性（Hanley-McNeil） ───────────────────────────

def hanley_mcneil_se(auc: float, n_pos: int, n_neg: int) -> float:
    """AUC 的 Hanley-McNeil 标准误（无结近似，用于「样本够不够」的规划）。

    ``Q1 = A/(2-A)``，``Q2 = 2A²/(1+A)``，
    ``SE = √[(A(1-A) + (n1-1)(Q1-A²) + (n0-1)(Q2-A²)) / (n1·n0)]``
    """
    a = float(auc)
    n1, n0 = int(n_pos), int(n_neg)
    if n1 <= 0 or n0 <= 0 or math.isnan(a):
        return float("nan")
    q1 = a / (2.0 - a)
    q2 = 2.0 * a * a / (1.0 + a)
    num = a * (1 - a) + (n1 - 1) * (q1 - a * a) + (n0 - 1) * (q2 - a * a)
    return float(math.sqrt(max(num, 0.0) / (n1 * n0)))


def n_for_se(auc: float, se_target: float, *, ratio_neg_per_pos: float = 1.0) -> int:
    """达到目标标准误 ``se_target`` 所需的**阳性样本数**（负类按比例配）。

    由 ``SE ≈ √[(A(1-A) + (n1-1)(Q1-A²) + (n0-1)(Q2-A²))/(n1 n0)]``
    取 ``n0 = r·n1``，解出使 SE 单调下降的 n1（二分；上限 1e7）。
    """
    lo, hi = 2, 10_000_000
    r = float(ratio_neg_per_pos)

    def se_of(n1: int) -> float:
        n0 = max(2, int(round(r * n1)))
        return hanley_mcneil_se(auc, n1, n0)

    if se_of(hi) > se_target:
        return hi
    while lo < hi:
        mid = (lo + hi) // 2
        if se_of(mid) > se_target:
            lo = mid + 1
        else:
            hi = mid
    return int(lo)


# ───────────────── 判别力分解：同日同层的条件一致性（去构成假象） ─────────────────

def conditional_pair_concordance(scores: Iterable[float], labels: Iterable[int],
                                 strata: Iterable[str]) -> dict[str, Any]:
    """只在**同一层内**配对的正负对一致性（条件 AUC 的配对计数口径）。

    与 :func:`tools.external_validity.metrics.stratified_auc` 的区别：本函数返回
    逐层的**配对数**（``concordant`` / ``discordant`` / ``tie``），使多维度分解
    （repo × day、agent × day）可以自底向上归并，且能给出总配对权重。

    ``AUC_c = (concordant + 0.5·tie) / pairs``；某层缺正或缺负则跳过并计入
    ``skipped``（不补 0.5，不伪装）。
    """
    sc = np.asarray(list(scores), dtype=float)
    y = np.asarray(list(labels), dtype=int)
    st = np.asarray(list(strata), dtype=object)
    if not (sc.size == y.size == st.size):
        raise ValueError("scores/labels/strata 长度不一致")

    total_c = total_d = total_t = 0
    strata_rows: list[dict[str, Any]] = []
    for s in sorted(set(st.tolist())):
        m = st == s
        pos = sc[m & (y == 1)]
        neg = sc[m & (y == 0)]
        if pos.size == 0 or neg.size == 0:
            strata_rows.append({"stratum": s, "n": int(m.sum()),
                                "n_pos": int(pos.size), "n_neg": int(neg.size),
                                "pairs": 0, "auc": float("nan")})
            continue
        diff = pos[:, None] - neg[None, :]
        c = int((diff > 0).sum())
        d = int((diff < 0).sum())
        t = int((diff == 0).sum())
        total_c += c
        total_d += d
        total_t += t
        strata_rows.append({
            "stratum": s, "n": int(m.sum()), "n_pos": int(pos.size),
            "n_neg": int(neg.size), "pairs": int(c + d + t),
            "concordant": c, "discordant": d, "tie": t,
            "auc": float((c + 0.5 * t) / (c + d + t)),
        })
    pairs = total_c + total_d + total_t
    return {
        "auc": float((total_c + 0.5 * total_t) / pairs) if pairs else float("nan"),
        "pairs": int(pairs), "concordant": int(total_c),
        "discordant": int(total_d), "tie": int(total_t),
        "n_strata": int(len(strata_rows)),
        "n_strata_usable": int(sum(1 for r in strata_rows if r["pairs"] > 0)),
        "per_stratum": strata_rows,
    }


def wilson_interval(k: int, n: int, *, alpha: float = 0.05) -> tuple[float, float]:
    """二项比例的 Wilson 区间（小样本比正态近似稳，用于逐层通过率）。"""
    if n <= 0:
        return (float("nan"), float("nan"))
    z = norm_isf(alpha / 2.0)
    phat = k / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (float(max(0.0, centre - half)), float(min(1.0, centre + half)))