"""判级者间一致性系数与方差分解——纯统计核（无 IO / 无 DB）。

为什么自带实现而不引第三方库：本仓运行环境不保证有 ``krippendorff`` /
``irr`` / ``pingouin``，而一致性系数的**每一个分母约定**（结修正、边际口径、
小数单元除数）都会改变数值，引不到库就不能装作有。这里把每个公式显式写出，
并用解析可验证的性质（与 Scott 的 π / Fleiss κ 的退化关系、界限、完美一致
性、注入单调性）在单测里钉住。

符号约定
--------
- **单元** = 一个被评对象（mission）；**判级者** = 给出等级的独立身份（grader）。
- 可靠性数据矩阵 ``X``：``n_units × n_raters``，``NaN`` = 该判级者没评该单元
  （Krippendorff α 允许缺格；Fleiss κ / Gwet AC / ICC 走**完整格**子样本）。
- 等级编码为 ``1..K`` 的整数序（S1..S5），``K`` = 观测到的类别数。

系数一览（都能回答「重复判级是否稳定」，但口径不同）
----------------------------------------------------
====================================  ============================================
系数                                   口径 / 何时用
====================================  ============================================
Krippendorff α (ordinal)               主指标。允许缺格与不等判级者数；序数度量给
                                       「差一级」部分学分。
Krippendorff α (nominal)               敏感性指标；两判级者时在大样本下 → Scott π。
Gwet AC1 / AC2                         **高一致 + 偏斜边际**悖论（κ 病态）下的稳健
                                       替代；AC2 = 序数版（线性权重）。
Fleiss κ                               经典多判级者名义 κ，仅完整格；作对照。
ICC(1,1) / ICC(2,1) / ICC(2,k)         把等级当连续量做方差分解：ICC(2,k) 是
                                       「k 个判级者**均值**的可靠性」，作决策参考。
====================================  ============================================

**纪律**：样本不足时返回 ``status="insufficient"`` 并给 ``nan``，绝不返回值域
内的安慰数（0.0 会被误读成「一致性实测为零」）。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Sequence

import numpy as np

try:  # scipy 仅在 F 分布尾概率时用；没有就退化为 nan（不假装有 p 值）
    from scipy.stats import f as _f_dist  # type: ignore

    _HAVE_SCIPY = True
except Exception:  # pragma: no cover - 环境相关
    _f_dist = None  # type: ignore
    _HAVE_SCIPY = False


# ─────────────────────────────── 基础工具 ───────────────────────────────

def norm_cdf(x: float) -> float:
    """标准正态 CDF（erfc 版，避免 erf 在尾部丢精度）。"""
    return 0.5 * math.erfc(-float(x) / math.sqrt(2.0))


def _norm_ppf(p: float) -> float:
    """标准正态分位点（二分反解 ``norm_cdf``，无外部依赖、可审计）。"""
    p = float(p)
    lo, hi = -40.0, 40.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _z_from_prop(prop: float) -> float:
    return _norm_ppf(min(max(float(prop), 1e-6), 1.0 - 1e-6))


def as_matrix(matrix: Any) -> np.ndarray:
    """把输入转成 ``float`` 矩阵（``None`` → ``nan``），保持形状。"""
    m = np.asarray(matrix, dtype=object)
    if m.ndim != 2:
        raise ValueError(f"可靠性矩阵必须二维，得到 ndim={m.ndim}")
    out = np.empty(m.shape, dtype=float)
    for i in range(m.shape[0]):
        for j in range(m.shape[1]):
            v = m[i, j]
            out[i, j] = np.nan if v is None else float(v)
    return out


def _categories(m: np.ndarray) -> list[float]:
    vals = m[~np.isnan(m)]
    return sorted({float(v) for v in vals})


def complete_rows(m: np.ndarray) -> np.ndarray:
    """取「每个判级者都评了」的完整单元（Fleiss / Gwet / ICC 的前提）。"""
    return m[~np.isnan(m).any(axis=1)]


# ───────────────────── 编码 / 巧合矩阵（快路径） ─────────────────────

def encode(matrix: Any) -> dict[str, Any]:
    """把可靠性矩阵编码成类别计数，供 α 与 bootstrap 批量复用。

    返回 ``enc``（``n×r`` 整数编码，``-1``=缺格）、``counts``（``n×K``）、
    ``mu``（每单元有效判级数）、``cats``（有序类别）。
    """
    m = as_matrix(matrix)
    cats = _categories(m)
    n, r = m.shape
    K = len(cats)
    idx = {c: i for i, c in enumerate(cats)}
    enc = np.full((n, r), -1, dtype=int)
    for i in range(n):
        for j in range(r):
            v = m[i, j]
            if not np.isnan(v):
                enc[i, j] = idx[float(v)]
    out: dict[str, Any] = {"enc": enc, "cats": cats, "K": K,
                           "n_units": n, "n_raters": r}
    if K == 0:
        out.update({"counts": np.zeros((n, 0)), "mu": np.zeros(n)})
        return out
    flat = enc.ravel()
    rows = np.repeat(np.arange(n), r)
    ok = flat >= 0
    key = rows[ok] * K + flat[ok]
    counts = np.bincount(key, minlength=n * K).reshape(n, K).astype(float)
    mu = np.bincount(rows[ok], minlength=n).astype(float)
    out.update({"counts": counts, "mu": mu})
    return out


def _delta2_ordinal(n_c: np.ndarray) -> np.ndarray:
    """Krippendorff 序数差函数 δ²(c,k) = ( Σ_{g=c..k} n_g − (n_c+n_k)/2 )²。

    直观含义：两类别之间「隔着多少个观测」的平方——相隔越远的等级分歧罚越重，
    而尺度由**实际边际分布**决定（不是人为等差数列），因此对偏斜等级分布稳健。
    ``c = k`` 时 δ² = 0。
    """
    k = len(n_c)
    d2 = np.zeros((k, k), dtype=float)
    for c in range(k):
        for kk in range(k):
            if c == kk:
                continue
            lo, hi = (c, kk) if c < kk else (kk, c)
            span = float(n_c[lo:hi + 1].sum()) - 0.5 * float(n_c[lo] + n_c[hi])
            d2[c, kk] = span * span
    return d2


def _delta2_nominal(n_c: np.ndarray) -> np.ndarray:
    k = len(n_c)
    return 1.0 - np.eye(k)


def _delta2_batch(n_c: np.ndarray) -> np.ndarray:
    """批量序数差函数：``n_c`` 形状 ``(B, K)`` → ``(B, K, K)``。"""
    B, K = n_c.shape
    cum = np.cumsum(n_c, axis=1)
    before = np.concatenate([np.zeros((B, 1)), cum[:, :-1]], axis=1)
    span = cum[:, None, :] - before[:, :, None]                     # [b,c,k]
    span = span - 0.5 * (n_c[:, :, None] + n_c[:, None, :])
    d2 = span * span
    d2[:, np.arange(K), np.arange(K)] = 0.0
    return d2


def _coincidence_batch(counts: np.ndarray, mu: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """批量巧合矩阵：``counts (B,n,K)``、``mu (B,n)`` → ``o (B,K,K)``, ``n_c``, ``n``。

    ``o[b,c,k] = Σ_u w[b,u,c]·c[b,u,k] − δ_ck·Σ_u w[b,u,c]``，其中
    ``w = counts/(m_u−1)``。**必须逐单元做外积**：先对单元求和再外积会凭空造出
    单元间交叉项（这一步写错会把完美一致的数据算成 α<0，单测钉住）。
    """
    denom = np.where(mu > 1, mu - 1.0, np.inf)
    w = counts / denom[:, :, None]
    o = np.einsum("buc,buk->bck", w, counts)
    o = o - np.einsum("buc,bck->bck", w, np.eye(counts.shape[-1])[None, :, :])
    n_c = o.sum(axis=1)
    return o, n_c, n_c.sum(axis=1)


def _alpha_batch(counts: np.ndarray, mu: np.ndarray, metric: str) -> np.ndarray:
    """批量 Krippendorff α（样本口径：``D_e`` 分母 ``n(n−1)``）。

    ``counts`` 形状 ``(B, n, K)``、``mu`` 形状 ``(B, n)``；退化批给 ``nan``。
    """
    B, n, K = counts.shape
    out = np.full(B, np.nan)
    if K < 2:
        return out
    o, n_c, n_tot = _coincidence_batch(counts, mu)
    if metric == "ordinal":
        d2 = _delta2_batch(n_c)
    elif metric == "nominal":
        d2 = np.broadcast_to(1.0 - np.eye(K), (B, K, K)).copy()
    else:
        raise ValueError(f"未实现的一致度量：{metric}")
    with np.errstate(invalid="ignore", divide="ignore"):
        do = (o * d2).sum(axis=(1, 2)) / n_tot
        de = np.einsum("bc,bk,bck->b", n_c, n_c, d2) / (n_tot * (n_tot - 1.0))
        alpha = 1.0 - do / de
    bad = ~np.isfinite(alpha) | ~(n_tot > 1) | ~(de > 0)
    alpha[bad] = np.nan
    return alpha


def coincidence_matrix(matrix: Any) -> dict[str, Any]:
    """Krippendorff 巧合矩阵（coincidence matrix）。

    单元 ``u`` 有 ``m_u ≥ 2`` 个值时按 ``1/(m_u−1)`` 加权，逐单元累加有序对：

        o[c,k] += ( n_uc·n_uk − δ_ck·n_uc ) / (m_u − 1)

    于是 ``n = Σ_c n_c = Σ_{m_u≥2} m_u``（有效值总数）。``m_u = 1`` 的单元
    **不进入**矩阵——单评不提供一致性信息，也绝不因此把 n 算大。
    """
    e = encode(matrix)
    K, n = e["K"], e["n_units"]
    if K == 0:
        return {"o": np.zeros((0, 0)), "n_c": np.zeros(0), "n": 0.0,
                "categories": [], "n_units": n, "n_pairable_units": 0}
    o, n_c, n_tot = _coincidence_batch(e["counts"][None], e["mu"][None])
    return {"o": o[0], "n_c": n_c[0], "n": float(n_tot[0]),
            "categories": e["cats"], "n_units": n,
            "n_pairable_units": int((e["mu"] >= 2).sum())}


def krippendorff_alpha(matrix: Any, metric: str = "ordinal") -> dict[str, Any]:
    """Krippendorff α = 1 − D_o/D_e（允许缺格 / 判级者数不等）。

    ``D_o = (1/n)·Σ_ck o_ck δ²_ck``，``D_e = 1/(n(n−1))·Σ_ck n_c n_k δ²_ck``。

    小样本性质（单测钉住）：两判级者 + 名义度量在大样本下 → Scott 的 π
    （差别只来自 ``D_e`` 的 ``n/(n−1)`` 修正）。
    """
    e = encode(matrix)
    K = e["K"]
    cm = coincidence_matrix(matrix)
    # n 只能是**可配对**单元的有效值总数（单评单元不进巧合矩阵，不得把 n 算大）
    n_tot = float(cm["n"])
    base = {"n": n_tot, "n_units": int((e["mu"] >= 2).sum()), "k": K,
            "metric": metric, "n_raters": e["n_raters"]}
    if K < 2 or n_tot <= 1:
        return {"alpha": float("nan"), "status": "insufficient", **base}
    alpha = float(_alpha_batch(e["counts"][None], e["mu"][None], metric)[0])
    n_c = cm["n_c"]
    d2 = _delta2_ordinal(n_c) if metric == "ordinal" else _delta2_nominal(n_c)
    do = float((cm["o"] * d2).sum() / n_tot)
    de = float((np.outer(n_c, n_c) * d2).sum() / (n_tot * (n_tot - 1.0)))
    if not math.isfinite(alpha) or de <= 0:
        return {"alpha": float("nan"), "status": "degenerate_zero_expected",
                "do": do, "de": de, **base}
    return {"alpha": alpha, "status": "ok", "do": do, "de": de,
            "n_c": n_c.tolist(), "categories": e["cats"], **base}


def scott_pi(matrix: Any) -> float:
    """Scott 的 π（两判级者名义一致性；仅作 α 的解析参照，不作决策）。"""
    m = as_matrix(matrix)
    if m.shape[1] != 2:
        raise ValueError("scott_pi 只对两判级者定义")
    m = complete_rows(m)
    n = m.shape[0]
    if n == 0:
        return float("nan")
    po = float((m[:, 0] == m[:, 1]).mean())
    cats = _categories(m)
    p = np.array([float((m == c).sum()) / (2.0 * n) for c in cats])
    pe = float((p * p).sum())
    return 1.0 - (1.0 - po) / (1.0 - pe) if pe < 1 else float("nan")


# ────────────────────────── 名义 κ / Gwet AC ──────────────────────────

def binomial_ci(k: int, n: int, conf: float = 0.95) -> dict[str, Any]:
    """逐对一致的 **Clopper-Pearson 精确二项区间**（稀疏样本的保守界）。

    为什么需要它：当 n 很小且几乎全是完美配对时（例如 12/13），bootstrap 重采样
    无法生成**未观察到的分歧模式**，α̂ 的 bootstrap CI 会异常窄（半宽 0.01 量级），
    属于反保守的假自信。精确二项区间给的是「按逐对一致率」的保守替代界，
    两者并列才诚实。
    """
    if n <= 0:
        return {"status": "insufficient", "n": 0}
    k = int(min(max(k, 0), n))
    a = (1.0 - conf) / 2.0
    try:
        from scipy.stats import beta as _beta  # type: ignore

        lo = 0.0 if k == 0 else float(_beta.ppf(a, k, n - k + 1))
        hi = 1.0 if k == n else float(_beta.ppf(1.0 - a, k + 1, n - k))
    except Exception:  # pragma: no cover
        lo = hi = float("nan")
    return {"status": "ok", "k": k, "n": n, "rate": k / n, "lo": lo, "hi": hi,
            "conf": conf, "method": "clopper_pearson"}


def _require_balanced(matrix: Any, name: str) -> tuple[np.ndarray, int, int]:
    m = complete_rows(as_matrix(matrix))
    if m.shape[0] == 0 or m.shape[1] < 2:
        raise ValueError(f"{name} 需要至少一个完整单元且判级者数 ≥2")
    return m, m.shape[0], m.shape[1]


def fleiss_kappa(matrix: Any) -> dict[str, Any]:
    """Fleiss κ（固定判级者数、名义类别、完整格）。

    ``P̄ = (1/n)Σ_i Σ_q n_iq(n_iq−1)/(r(r−1))``，``P̄_e = Σ_q p_q²``，
    ``κ = (P̄ − P̄_e)/(1 − P̄_e)``。两判级者时与 Scott 的 π 相同（单测钉住）。
    """
    m, n, r = _require_balanced(matrix, "fleiss_kappa")
    cats = _categories(m)
    k = len(cats)
    counts = np.stack([(m == c).sum(axis=1) for c in cats], axis=1).astype(float)
    p_i = (counts * (counts - 1)).sum(axis=1) / (r * (r - 1))
    pbar = float(p_i.mean())
    p_q = counts.sum(axis=0) / (n * r)
    pe = float((p_q * p_q).sum())
    if pe >= 1.0:
        return {"kappa": float("nan"), "status": "degenerate", "n_units": n,
                "n_raters": r, "k": k, "pa": pbar, "pe": pe}
    return {"kappa": 1.0 - (1.0 - pbar) / (1.0 - pe), "status": "ok",
            "n_units": n, "n_raters": r, "k": k, "pa": pbar, "pe": pe}


def gwet_ac1(matrix: Any) -> dict[str, Any]:
    """Gwet AC1：``(p_a − p_e)/(1 − p_e)``，``p_e = (1/(k−1))·Σ_q π_q(1−π_q)``。

    κ 的经典病态是「边际极度偏斜 + 一致率很高」时 κ 反常趋零；AC1 用
    「随机判级者以 (1/(k−1)) 概率命中同一类别」的机会一致率，从而在该场景
    保持可解释。``p_a`` 为名义观测一致率。
    """
    m, n, r = _require_balanced(matrix, "gwet_ac1")
    cats = _categories(m)
    k = len(cats)
    if k < 2:
        return {"ac1": float("nan"), "status": "single_category", "n_units": n,
                "n_raters": r, "k": k}
    counts = np.stack([(m == c).sum(axis=1) for c in cats], axis=1).astype(float)
    pa = float((counts * (counts - 1)).sum(axis=1).sum() / (n * r * (r - 1)))
    pi = counts.sum(axis=0) / (n * r)
    pe = float((pi * (1.0 - pi)).sum() / (k - 1))
    if pe >= 1.0:
        return {"ac1": float("nan"), "status": "degenerate", "n_units": n,
                "n_raters": r, "k": k, "pa": pa, "pe": pe}
    return {"ac1": (pa - pe) / (1.0 - pe), "status": "ok", "n_units": n,
            "n_raters": r, "k": k, "pa": pa, "pe": pe}


def gwet_ac2(matrix: Any) -> dict[str, Any]:
    """Gwet AC2（序数版，线性权重 ``w(c,k) = 1 − |c−k|/(K−1)``）。

    观测一致率换成加权一致率 ``p_a = (1/n)Σ_i Σ_{j≠j'} w(x_ij,x_ij')/(r(r−1))``；
    机会一致率沿用 Gwet 的边际口径 ``p_e = (1/(K−1))·Σ_q π_q(1−π_q)``，
    由此 **K=2 时 AC2 恒等于 AC1**（线性权重退化为 0/1，单测钉住）。

    ⚠ 口径声明：AC2 的机会一致率在文献里有多种写法，本实现采用上式并把 AC2
    定位为**敏感性指标**——决策门只用 Krippendorff α(ordinal)。
    """
    m, n, r = _require_balanced(matrix, "gwet_ac2")
    cats = _categories(m)
    k = len(cats)
    if k < 2:
        return {"ac2": float("nan"), "status": "single_category", "n_units": n,
                "n_raters": r, "k": k}
    idx = {c: i for i, c in enumerate(cats)}
    enc = np.vectorize(idx.get)(m).astype(float)
    denom = float(k - 1)
    s = np.zeros(n, dtype=float)
    for a in range(r):
        for b in range(r):
            if a == b:
                continue
            s += 1.0 - np.abs(enc[:, a] - enc[:, b]) / denom
    pa = float(s.sum() / (n * r * (r - 1)))
    counts = np.stack([(m == c).sum(axis=1) for c in cats], axis=1).astype(float)
    pi = counts.sum(axis=0) / (n * r)
    pe = float((pi * (1.0 - pi)).sum() / denom)
    if pe >= 1.0:
        return {"ac2": float("nan"), "status": "degenerate", "n_units": n,
                "n_raters": r, "k": k, "pa": pa, "pe": pe}
    return {"ac2": (pa - pe) / (1.0 - pe), "status": "ok", "n_units": n,
            "n_raters": r, "k": k, "pa": pa, "pe": pe}


# ─────────────────────────────── ICC ───────────────────────────────

def _anova_two_way(m: np.ndarray) -> dict[str, float]:
    n, k = m.shape
    grand = float(m.mean())
    row_means = m.mean(axis=1)
    col_means = m.mean(axis=0)
    ss_total = float(((m - grand) ** 2).sum())
    ss_rows = float(k * ((row_means - grand) ** 2).sum())
    ss_cols = float(n * ((col_means - grand) ** 2).sum())
    ss_err = ss_total - ss_rows - ss_cols
    return {
        "n": n, "k": k, "grand": grand,
        "ss_total": ss_total, "ss_rows": ss_rows, "ss_cols": ss_cols,
        "ss_err": max(ss_err, 0.0),
        "df_rows": n - 1, "df_cols": k - 1, "df_err": (n - 1) * (k - 1),
        "ms_rows": ss_rows / (n - 1),
        "ms_cols": ss_cols / (k - 1),
        "ms_err": max(ss_err, 0.0) / ((n - 1) * (k - 1)),
    }


def _f_sf(f: float, df1: int, df2: int) -> float:
    if not _HAVE_SCIPY or not math.isfinite(f) or df1 <= 0 or df2 <= 0:
        return float("nan")
    return float(_f_dist.sf(f, df1, df2))


def icc(matrix: Any) -> dict[str, Any]:
    """ICC 族（Shrout & Fleiss 1979 / McGraw & Wong 1996 口径，完整格）。

    - ``ICC(1,1)``：单向随机效应，判级者差异**视为误差**。
    - ``ICC(2,1)``：双向随机、绝对一致（判级者差异算系统偏差）。
    - ``ICC(2,k)``：**k 个判级者均值**的可靠性（单次判级噪声被 √k 压低，但
      不能替代「每个成果都双评」这一设计要求）。
    - ``ICC(3,1)``/``ICC(3,k)``：双向混合（一致性口径，不算系统偏差），参照值。

    另给 ``F = ms_rows/ms_err`` 与 p 值（H0: ICC=0）——ICC 功效仿真的统计量。
    """
    m = complete_rows(as_matrix(matrix))
    if m.shape[0] < 2 or m.shape[1] < 2:
        return {"status": "insufficient",
                "n_units": int(m.shape[0]) if m.size else 0}
    a = _anova_two_way(m)
    msr, msc, mse = a["ms_rows"], a["ms_cols"], a["ms_err"]
    n, k = a["n"], a["k"]
    num = msr - mse
    den1 = msr + (k - 1) * mse
    den21 = msr + (k - 1) * mse + k * (msc - mse) / n
    den2k = msr + (msc - mse) / n
    out = {
        "status": "ok", "n_units": n, "n_raters": k,
        "icc_1_1": num / den1 if den1 > 0 else float("nan"),
        "icc_2_1": num / den21 if den21 > 0 else float("nan"),
        "icc_2_k": num / den2k if den2k > 0 else float("nan"),
        "icc_3_1": num / den1 if den1 > 0 else float("nan"),
        "icc_3_k": num / msr if msr > 0 else float("nan"),
        "ms_rows": msr, "ms_cols": msc, "ms_err": mse,
        "f": msr / mse if mse > 0 else float("inf"),
        "df1": a["df_rows"], "df2": a["df_err"],
    }
    out["p_value"] = _f_sf(out["f"], a["df_rows"], a["df_err"])
    return out


# ───────────────────────────── bootstrap CI ─────────────────────────────

def bootstrap_alpha_ci(
    matrix: Any,
    metric: str = "ordinal",
    *,
    n_boot: int = 1000,
    conf: float = 0.95,
    method: str = "bc",
    seed: int = 20261110,
) -> dict[str, Any]:
    """α 的 bootstrap 置信区间（按**单元**重采样，向量化实现）。

    为什么按单元而不是按判级值：可靠性抽样的独立单位是「被评对象」，同一单元
    内的多个判级值不独立；按值重采样会把 n 算大、CI 算窄（典型假自信）。

    ``method="percentile"`` 百分位区间；``method="bc"`` 偏差校正百分位区间
    （Efron BC：``z0 = Φ⁻¹(#{α*<α̂}/B)``，端点 ``Φ(2z0 ± z_{α/2})``）。默认 BC
    的理由：α 在小样本上有中位偏差，BC 用重采样偏差因子 ``z0`` 做平移、更保守
    （``z0`` 一并返回，便于审计）。**两种方法用同一批重采样同时算出**并放在
    ``intervals`` 里，覆盖率由 ``acceptance.J2`` 对两者分别实测——不靠声明，
    靠数字选方法。
    

    无效重采样（退化批次）**丢弃而非填 0**，并报 ``n_valid``。
    """
    e = encode(matrix)
    counts, mu, K = e["counts"], e["mu"], e["K"]
    n = e["n_units"]
    point = krippendorff_alpha(matrix, metric)
    ah = point["alpha"]
    if K < 2 or n == 0:
        return {"status": "insufficient", "n_boot": 0, "n_units": n, "point": ah}
    rng = np.random.default_rng(seed)
    B = int(n_boot)
    idx = rng.integers(0, n, size=(B, n))
    o, n_c, n_tot = _coincidence_batch(counts[idx], mu[idx])
    d2 = _delta2_batch(n_c) if metric == "ordinal" else \
        np.broadcast_to(1.0 - np.eye(K), (B, K, K)).copy()
    with np.errstate(invalid="ignore", divide="ignore"):
        do = (o * d2).sum(axis=(1, 2)) / n_tot
        de = np.einsum("bc,bk,bck->b", n_c, n_c, d2) / (n_tot * (n_tot - 1.0))
        boots = 1.0 - do / de
    valid = boots[np.isfinite(boots) & (de > 0)]
    if valid.size < 10 or not math.isfinite(ah):
        return {"status": "insufficient_replicates", "n_valid": int(valid.size),
                "point": ah, "n_units": n, "n_boot": B}
    a = (1.0 - conf) / 2.0
    z0 = _z_from_prop(float((valid < ah).mean()))
    pct = np.quantile(valid, [a, 1.0 - a])
    lo_p = norm_cdf(2.0 * z0 + _norm_ppf(a))
    hi_p = norm_cdf(2.0 * z0 + _norm_ppf(1.0 - a))
    bc = np.quantile(valid, [min(max(lo_p, 0.0), 1.0), min(max(hi_p, 0.0), 1.0)])
    intervals = {
        "percentile": {"lo": float(pct[0]), "hi": float(pct[1]),
                       "half_width": float((pct[1] - pct[0]) / 2.0)},
        "bc": {"lo": float(bc[0]), "hi": float(bc[1]),
               "half_width": float((bc[1] - bc[0]) / 2.0)},
    }
    if method not in intervals:
        raise ValueError(f"未实现的 bootstrap 方法：{method}")
    chosen = intervals[method]
    lo, hi = chosen["lo"], chosen["hi"]
    return {
        "status": "ok", "point": float(ah), "lo": float(lo), "hi": float(hi),
        "half_width": float((hi - lo) / 2.0), "sd": float(valid.std(ddof=1)),
        "n_boot": B, "n_valid": int(valid.size), "n_units": n,
        "conf": conf, "method": method, "z0": float(z0),
        "intervals": intervals,
    }


def bootstrap_ci(
    matrix: Any,
    statistic: Callable[[np.ndarray], float] | None = None,
    *,
    n_boot: int = 1000,
    level: float = 0.95,
    seed: int = 20261110,
) -> dict[str, Any]:
    """通用（非 α）统计量的按单元 bootstrap 百分位区间。

    α 请用 :func:`bootstrap_alpha_ci`（向量化 + BC）；本函数用于 ICC / κ 等
    需要自定义统计量的场合，慢但通用。
    """
    m = as_matrix(matrix)
    n = m.shape[0]
    if n == 0 or statistic is None:
        return {"sd": float("nan"), "lo": float("nan"), "hi": float("nan"),
                "n_boot": 0, "status": "insufficient"}
    rng = np.random.default_rng(seed)
    vals: list[float] = []
    for _ in range(int(n_boot)):
        idx = rng.integers(0, n, size=n)
        v = statistic(m[idx])
        if v is not None and np.isfinite(v):
            vals.append(float(v))
    if not vals:
        return {"sd": float("nan"), "lo": float("nan"), "hi": float("nan"),
                "n_boot": int(n_boot), "status": "all_nan"}
    arr = np.asarray(vals)
    a = (1.0 - level) / 2.0
    return {
        "sd": float(arr.std(ddof=1)),
        "lo": float(np.quantile(arr, a)),
        "hi": float(np.quantile(arr, 1.0 - a)),
        "half_width": float((np.quantile(arr, 1.0 - a) - np.quantile(arr, a)) / 2.0),
        "n_boot": int(n_boot), "n_units": int(n), "level": level,
        "status": "ok",
    }


# ─────────────────────── 判级者方差占比 φ_g ───────────────────────

def variance_components(rows: Sequence[tuple[Any, Any, float]]) -> dict[str, Any]:
    """把等级方差拆成「判级者（grader）」与其他（mission+噪声）。

    模型：``y_ij = μ + g_j + ε_ij``，``g_j ~ (0, σ²_g)``，``ε ~ (0, σ²_ε)``。

    **为什么把 mission 并进 ε**：本平台的现实设计是「一个 mission 基本只被一个
    判级者评」，mission 与判级者效应**不可分离识别**（经典混杂）。硬拆会得到
    一个由设计假象决定的数。因此这里只识别可识别的量：
    ``φ_g = σ²_g/(σ²_g + σ²_ε)``（判级者方差占比 = 单向随机效应 ICC 口径）；
    mission 分量单独由「同一 mission 被 ≥2 个判级者评」的子样本估纯误差后反解，
    并在不可识别（<30 个多评单元）时显式 ``mission_component_identifiable=False``。

    估计量（不等组单向随机效应矩估计，Searle 口径）：
    ``σ̂²_g = max(0, (MSB − MSW)/n̄_h)``，``n̄_h = (1/(J−1))(n − Σ n_j²/n)``。
    矩估计在 σ²_g→0 时被截断在 0（单侧偏差），故 φ_g 下侧偏保守。
    """
    recs = [(m, g, float(y)) for (m, g, y) in rows if g is not None and y is not None]
    n = len(recs)
    graders = sorted({g for _, g, _ in recs}, key=str)
    J = len(graders)
    missions = {m for m, _, _ in recs}
    if n < 3 or J < 2:
        return {"status": "insufficient", "n": n, "n_graders": J,
                "n_missions": len(missions)}

    ys = np.array([y for _, _, y in recs], dtype=float)
    grand = float(ys.mean())
    ssb = 0.0
    ssw = 0.0
    # 一次分组（O(n)），避免每个判级者都全表扫一遍（bootstrap 里差 5 个数量级的耗时）
    grouped: dict[Any, list[float]] = {}
    for _, g, y in recs:
        grouped.setdefault(g, []).append(y)
    group_sizes: dict[Any, int] = {}
    for g in graders:
        vals = np.asarray(grouped.get(g, []), dtype=float)
        group_sizes[g] = int(vals.size)
        ssb += float(vals.size) * (float(vals.mean()) - grand) ** 2
        ssw += float(((vals - vals.mean()) ** 2).sum())
    df_b, df_w = J - 1, n - J
    if df_b <= 0 or df_w <= 0:
        return {"status": "insufficient_design", "n": n, "n_graders": J}
    msb, msw = ssb / df_b, ssw / df_w
    n_bar_h = (n - sum(v * v for v in group_sizes.values()) / n) / df_b
    sigma_g2 = max(0.0, (msb - msw) / n_bar_h) if n_bar_h > 0 else float("nan")
    sigma_eps2 = msw
    total = sigma_g2 + sigma_eps2
    phi_g = sigma_g2 / total if total > 0 else float("nan")

    by_mission: dict[Any, list[float]] = {}
    for m_, _, y in recs:
        by_mission.setdefault(m_, []).append(y)
    multi = [v for v in by_mission.values() if len(v) >= 2]
    within_ms = []
    for v in multi:
        arr = np.asarray(v, dtype=float)
        within_ms.append(float(((arr - arr.mean()) ** 2).sum() / (arr.size - 1)))
    sigma_e2 = float(np.mean(within_ms)) if within_ms else float("nan")
    sigma_m2 = (max(0.0, sigma_eps2 - sigma_e2)
                if math.isfinite(sigma_e2) else float("nan"))

    return {
        "status": "ok", "n": n, "n_graders": J, "n_missions": len(missions),
        "grand_mean": grand, "ms_between_graders": msb, "ms_within": msw,
        "n_bar_harmonic": n_bar_h,
        "sigma2_grader": sigma_g2, "sigma2_eps": sigma_eps2,
        "sigma2_mission": sigma_m2, "sigma2_pure_error": sigma_e2,
        "phi_g": phi_g,
        "n_multi_graded_missions": len(multi),
        "max_graders_per_mission": max((len(v) for v in by_mission.values()), default=1),
        "mission_component_identifiable": len(multi) >= 30,
        "grader_sizes": {str(k): v for k, v in group_sizes.items()},
    }


def bootstrap_phi_g(
    rows: Sequence[tuple[Any, Any, float]],
    *,
    n_boot: int = 1000,
    level: float = 0.95,
    seed: int = 20261110,
) -> dict[str, Any]:
    """φ_g 的置信区间——按 mission 聚类重采样（判级者身份固定，不重采样人）。"""
    recs = list(rows)
    units = sorted({m for m, _, _ in recs}, key=str)
    if len(units) < 5:
        return {"status": "insufficient", "n_units": len(units)}
    by_unit: dict[Any, list[tuple[Any, Any, float]]] = {}
    for r in recs:
        by_unit.setdefault(r[0], []).append(r)
    rng = np.random.default_rng(seed)
    vals: list[float] = []
    for _ in range(int(n_boot)):
        pick = rng.integers(0, len(units), size=len(units))
        sample: list[tuple[Any, Any, float]] = []
        for u in pick:
            sample.extend(by_unit[units[u]])
        res = variance_components(sample)
        if res.get("status") == "ok" and math.isfinite(res.get("phi_g", float("nan"))):
            vals.append(float(res["phi_g"]))
    if not vals:
        return {"status": "all_nan", "n_boot": int(n_boot)}
    arr = np.asarray(vals)
    a = (1.0 - level) / 2.0
    return {"status": "ok", "sd": float(arr.std(ddof=1)),
            "lo": float(np.quantile(arr, a)), "hi": float(np.quantile(arr, 1.0 - a)),
            "n_boot": int(n_boot), "n_units": len(units), "level": level}


# ───────────────────────── ICC 功效（判据 J3） ─────────────────────────

def _sim_continuous(rng: np.random.Generator, n_units: int, n_raters: int,
                    icc_true: float) -> np.ndarray:
    m_var, e_var = float(icc_true), float(1.0 - icc_true)
    m_eff = rng.normal(0.0, math.sqrt(max(m_var, 0.0)), size=n_units)
    return m_eff[:, None] + rng.normal(0.0, math.sqrt(max(e_var, 0.0)),
                                       size=(n_units, n_raters))


def icc_power(
    *,
    n_units: int = 100,
    n_raters: int = 3,
    icc_true: float = 0.3,
    reps: int = 400,
    alpha: float = 0.05,
    seed: int = 20261110,
) -> dict[str, Any]:
    """H0: ICC=0 的 F 检验功效（单向随机效应，单次判级口径 ICC(1,1)）。

    生成模型：``y_ij = m_i + e_ij``，``σ²_m = icc_true``、``σ²_e = 1 − icc_true``；
    统计量 ``F = MSB/MSW ~ F(n−1, n(k−1))``，单侧（ICC≥0 方向）用上尾 p。
    """
    rng = np.random.default_rng(seed)
    hit = 0
    ps: list[float] = []
    for _ in range(int(reps)):
        y = _sim_continuous(rng, n_units, n_raters, icc_true)
        a = _anova_two_way(y)
        f = a["ms_rows"] / a["ms_err"] if a["ms_err"] > 0 else float("inf")
        p = _f_sf(f, a["df_rows"], a["df_err"])
        if math.isfinite(p):
            ps.append(p)
            hit += int(p < alpha)
    used = len(ps)
    return {
        "status": "ok" if used else "scipy_unavailable",
        "power": hit / used if used else float("nan"),
        "reps_used": used, "n_units": n_units, "n_raters": n_raters,
        "icc_true": icc_true, "alpha_level": alpha,
        "median_p": float(np.median(ps)) if ps else float("nan"),
    }


def icc_null_false_positive(
    *, n_units: int = 100, n_raters: int = 3, reps: int = 400,
    alpha: float = 0.05, seed: int = 20261111,
) -> dict[str, Any]:
    """负控：ICC=0 时 F 检验假阳性率应 ≈ α（仪器不自欺的证明）。"""
    res = icc_power(n_units=n_units, n_raters=n_raters, icc_true=0.0,
                    reps=reps, alpha=alpha, seed=seed)
    res["false_positive_rate"] = res.get("power")
    return res


def min_units_for_halfwidth(
    *,
    target_halfwidth: float = 0.10,
    n_raters: int = 3,
    alpha_true: float = 0.5,
    sigma: float = 1.0,
    prior: Sequence[float] | None = None,
    grid: Iterable[int] = (10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 130, 160,
                           200, 260, 320, 400),
    reps: int = 120,
    seed: int = 20261112,
) -> dict[str, Any]:
    """「CI 半宽 ≤ target 需要多少评审判数」——用**抽样 SD** 反推。

    半宽 ≈ z_{0.975}·SD(α̂)，SD 由合成重抽样**实测**（不引解析近似）。传
    ``prior``/``sigma``（合成夹具口径）时用生成模型抽样；否则退化为连续潜
    变量量化（仅作对照）。
    """
    rows: list[dict[str, Any]] = []
    best: int | None = None
    for n_units in grid:
        rng = np.random.default_rng(seed + int(n_units))
        alphas: list[float] = []
        for _ in range(int(reps)):
            if prior is None:
                y = np.clip(np.floor(_sim_continuous(rng, n_units, n_raters, 0.5) * 1.5 + 3.5), 1, 5)
            else:
                from tools.eval_reliability import synthetic as _syn
                y = _syn.simulate_sample(rng, n_units, prior=prior, sigma=sigma,
                                         biases=(0.0,) * n_raters)
            a = krippendorff_alpha(y, "ordinal")["alpha"]
            if math.isfinite(a):
                alphas.append(a)
        sd = float(np.std(alphas, ddof=1)) if len(alphas) > 1 else float("nan")
        hw = 1.959963985 * sd
        rows.append({"n_units": int(n_units), "n_raters": int(n_raters),
                     "sd_alpha": sd, "ci_halfwidth": hw,
                     "mean_alpha": float(np.mean(alphas)) if alphas else float("nan")})
        if best is None and math.isfinite(hw) and hw <= target_halfwidth:
            best = int(n_units)
    return {"target_halfwidth": target_halfwidth, "n_raters": int(n_raters),
            "alpha_true": alpha_true, "curve": rows, "min_n_units": best}


__all__ = [
    "norm_cdf", "as_matrix", "encode", "complete_rows", "coincidence_matrix",
    "krippendorff_alpha", "scott_pi", "fleiss_kappa", "gwet_ac1", "gwet_ac2",
    "icc", "binomial_ci", "bootstrap_alpha_ci", "bootstrap_ci", "variance_components",
    "bootstrap_phi_g", "icc_power", "icc_null_false_positive",
    "min_units_for_halfwidth",
]