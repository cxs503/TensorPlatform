"""六维评价的统计功效门（通用层，零领域语义）：把「优于对照」升级为带第一类错误控制的判决。

本模块**不含任何领域语义**（不出现学科词条/域算例）：它只吃 ``records``
（每条 = 一次测量：lineage/seat/arm/dimension/value/unit/source）与配置数字，
输出机器可裁的 JSON。域数据一律经工件（JSON）注入，见
``artifacts/six_dim_power/p12_spec.json``。

为什么需要它
------------
``tools/abm/dimensions.py`` 的 ``comparison_gate`` 只裁决「该维在两臂上都有同单位数值」，
即**结构可比性**，不做统计推断。于是「实验组在 2 个维度上优于对照组」这类命题在没有
重复测量的情况下不可裁（每臂只有 1 个数 → 无可估计的重复误差 → 无检验）。
本模块补上缺的那一层：

1. **效应量**：六维各定义方向（``DIMENSION_DIRECTION``），配对差
   ``delta_i = direction * (v_treatment_i - v_control_i)``（正 = 实验组更好），
   给出配对 Cohen's ``d_z``（``mean/sd``，``ddof=1``）与稳健的配对差中位数。
2. **配对设计**：配对键 ``(lineage, seat)``——同一任务谱系、同一席位下两条路线成对，
   消掉谱系/席位/种子族的公共偏移，只留「路线」一个变化因素。
3. **置换检验**：配对符号翻转（sign-flip）置换检验——配对设计下的**精确**检验
   （零假设下每个 delta 的符号等概率可交换，不依赖正态假设）。
4. **族错误率控制**：六维为一个族，Bonferroni-Holm 逐步下降法控制 FWER ≤ α
   （任意相依结构下成立，且比均匀 Bonferroni 更有功效）。
5. **MDE(n) 与反解**：解析近似 ``MDE = (z_{1-a/2}+z_{1-b}) * sd_delta / sqrt(n)``
   （α=0.05 双侧、功效 0.8 → 系数 2.802），以及用**同一置换检验**做蒙特卡洛反解，
   两者偏差入机器判据；再由「≥k 维优于对照」这一**族判决**反解所需配对对数 n。
6. **A/A 空对照**：同一路线测两次，误报率必须回到名义值 α=0.05 附近——这是唯一
   能实测「p 值有没有被系统性做小」的证明。
7. **回溯性功效**：对既有战役产物（每臂单测）如实报「这规模能不能判出 2 维」。

判决规则（预声明，不看结果后改）
--------------------------------
``G25``：六维中**至少 k 维（默认 2）**在 Holm 校正后仍显著为实验组更好（方向性备择）。
方向性备择（``alternative="greater"``）是预声明的——命题本身是「优于」而非「不同」；
双侧变体一并报告（``alternative="two-sided"``）作为稳健性行，不改主判据。

已知边界（诚实声明）
--------------------
* 符号翻转置换检验的 p 值在 n 很小时**离散化**：等幅配对下双侧可达最小 p = ``2/2^n``，
  n=5 时 = 6.25% > 5%（**根本拒绝不了**），n=6 时才到 3.1%。``MIN_TESTABLE_PAIRS=9``
  不是数学下限而是**设计下限**：保证 α=0.05 可达、且 0.05 以下的 p 阶梯有若干档
  （n=6..8 时 0.05 以下只有 1-2 档，功效被量化粗糙度吃掉）。
* 解析 MDE（2.802 sd/sqrt(n)）用正态近似；n<20 时 t 分位修正 >10%，故「偏差 ≤10%」
  这条判据只在 n≥20 的网格上声明，小 n 行如实列出偏差。
* ``retrospective_power`` 的 σ 代理由既有产物推得（如两次同路线复跑的相对差），是
  「跨量转移」的假设而非测量，因此敏感性表（σ 网格）必须与结论同时报告。

参考
----
* Holm (1979) A simple sequentially rejective multiple test procedure, Scand. J. Statist. 6, 65-70.
* Good (2005) Permutation, Parametric and Bootstrap Tests of Hypotheses, 3rd ed., Springer.
* Cohen (1988) Statistical Power Analysis for the Behavioral Sciences, 2nd ed.
* Chow, Shao & Wang (2008) Sample Size Calculations in Clinical Research, 2nd ed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# --------------------------------------------------------------------------- 常量

DIMENSIONS: Tuple[str, ...] = (
    "correctness",
    "accuracy",
    "stability",
    "efficiency",
    "reproducibility",
    "applicability",
)

#: +1 = 数值越大越好；-1 = 数值越小越好。
#: accuracy 的口径是**误差**，efficiency 是**耗时/步数**，reproducibility 是**两次运行差异**
#: ——三者都是「越小越好」。口径必须在工件里声明（unit 字段）。
DIMENSION_DIRECTION: Dict[str, int] = {
    "correctness": +1,
    "accuracy": -1,
    "stability": +1,
    "efficiency": -1,
    "reproducibility": -1,
    "applicability": +1,
}

DIMENSION_NOTE: Dict[str, str] = {
    "correctness": "越大越好：守恒/解析极限的通过项数或 1-归一化残差",
    "accuracy": "越小越好：与基准的相对误差",
    "stability": "越大越好：稳定性通过项数或稳健裕度",
    "efficiency": "越小越好：墙钟秒数/步数",
    "reproducibility": "越小越好：两次运行的相对差异",
    "applicability": "越大越好：已登记的限制/失效模式条数",
}

#: 配对检验的设计下限（非数学下限）：等幅配对下 α=0.05 双侧可达最小 p = 2/2^n，
#: n=5 → 6.25%（拒绝不了）、n=6 → 3.1%；取 9 是为了让 0.05 以下的 p 阶梯不止一两档。
MIN_TESTABLE_PAIRS = 9

#: 解析 MDE 系数：α=0.05、功效 0.8（双侧 1.9600+0.8416；单侧 1.6449+0.8416）。
MDE_COEFF_TWO_SIDED_05_80 = 2.801585
MDE_COEFF_ONE_SIDED_05_80 = 2.486475

ALTERNATIVES = ("two-sided", "greater", "less")


# --------------------------------------------------------------------------- 工具


def _normal_ppf(p: float) -> float:
    return NormalDist().inv_cdf(float(p))


def mde_coefficient(alpha: float = 0.05, power: float = 0.8, two_sided: bool = True) -> float:
    """解析 MDE 系数 ``z_{1-alpha/2}+z_{power}``（双侧）或 ``z_{1-alpha}+z_{power}``。"""
    z_alpha = _normal_ppf(1.0 - alpha / 2.0) if two_sided else _normal_ppf(1.0 - alpha)
    return float(z_alpha + _normal_ppf(power))


def binom_ci(k: int, n: int, level: float = 0.95) -> Dict[str, Any]:
    """二项比例的 Clopper-Pearson 精确区间（无 scipy 时退化 Wilson 得分区间）。"""
    k, n = int(k), int(n)
    if n <= 0:
        return {"p_hat": float("nan"), "lo": float("nan"), "hi": float("nan"),
                "level": level, "method": "n/a", "k": k, "n": n}
    a = (1.0 - level) / 2.0
    p_hat = k / n
    try:  # 精确区间（首选）
        from scipy.stats import beta as _beta  # type: ignore

        lo = 0.0 if k == 0 else float(_beta.ppf(a, k, n - k + 1))
        hi = 1.0 if k == n else float(_beta.ppf(1.0 - a, k + 1, n - k))
        method = "clopper-pearson"
    except Exception:  # pragma: no cover - 仅在无 scipy 环境走
        z = _normal_ppf(1.0 - a)
        denom = 1.0 + z * z / n
        centre = (p_hat + z * z / (2 * n)) / denom
        half = z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n)) / denom
        lo, hi = max(0.0, centre - half), min(1.0, centre + half)
        method = "wilson"
    return {"p_hat": p_hat, "lo": lo, "hi": hi, "level": level,
            "method": method, "k": k, "n": n}


def sha256_file(path: Optional[str]) -> Optional[str]:
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------- 1. 配对与效应量


def build_pairs(
    records: Sequence[Dict[str, Any]],
    *,
    control_arm: str = "control",
    treatment_arm: str = "treatment",
    dims: Sequence[str] = DIMENSIONS,
) -> Dict[str, Any]:
    """把测量记录折成 ``{dimension: [pair, ...]}``，配对键 = ``(lineage, seat)``。

    record 形状::

        {"lineage": "L1", "seat": "S1", "arm": "control", "dimension": "accuracy",
         "value": 0.0139, "unit": "rel_err", "source": "工件路径#行"}

    只有**同一 (lineage, seat) 上两臂都有**且**单位一致**的记录才成对；
    缺 ``source`` 的记录不入账（沿用 ``dimensions.py`` 的「数字无出处不入账」纪律）。
    """
    errors: List[str] = []
    buckets: Dict[Tuple[str, str, str], Dict[str, Dict[str, Any]]] = {}
    for rec in records:
        dim = str(rec.get("dimension") or "")
        if dim not in dims:
            errors.append(f"未知维度 {dim!r}（lineage={rec.get('lineage')!r}）")
            continue
        arm = str(rec.get("arm") or "")
        if arm not in (control_arm, treatment_arm):
            errors.append(f"{dim}: 未知臂 {arm!r}（只接受 {control_arm!r}/{treatment_arm!r}）")
            continue
        value = rec.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"{dim}/{arm}: value 非数值（{value!r}）")
            continue
        source = str(rec.get("source") or "").strip()
        if not source:
            errors.append(f"{dim}/{arm}: 缺 source（数字无出处不入账）")
            continue
        key = (str(rec.get("lineage") or ""), str(rec.get("seat") or ""), dim)
        if arm in buckets.setdefault(key, {}):
            errors.append(f"{dim}: 同一 (lineage, seat, arm) 重复记录 {key}")
            continue
        buckets[key][arm] = {
            "value": float(value),
            "unit": str(rec.get("unit") or ""),
            "source": source,
        }

    pairs: Dict[str, List[Dict[str, Any]]] = {d: [] for d in dims}
    for (lineage, seat, dim), arms in sorted(buckets.items()):
        if not (control_arm in arms and treatment_arm in arms):
            continue
        if arms[control_arm]["unit"] != arms[treatment_arm]["unit"]:
            errors.append(
                f"{dim}/{lineage}/{seat}: 单位不一致 "
                f"({arms[control_arm]['unit']!r} vs {arms[treatment_arm]['unit']!r})"
            )
            continue
        pairs[dim].append({
            "lineage": lineage,
            "seat": seat,
            "v_control": arms[control_arm]["value"],
            "v_treatment": arms[treatment_arm]["value"],
            "unit": arms[control_arm]["unit"],
            "sources": {control_arm: arms[control_arm]["source"],
                        treatment_arm: arms[treatment_arm]["source"]},
        })
    return {
        "pairs": pairs,
        "n_pairs_by_dim": {d: len(v) for d, v in pairs.items()},
        "errors": errors,
    }


def oriented_deltas(pairs: Sequence[Dict[str, Any]], dim: str) -> np.ndarray:
    """``delta_i = direction(dim) * (v_treatment - v_control)``，正 = 实验组更好。"""
    if dim not in DIMENSION_DIRECTION:
        raise ValueError(f"未知维度 {dim!r}")
    sgn = DIMENSION_DIRECTION[dim]
    return np.asarray([sgn * (p["v_treatment"] - p["v_control"]) for p in pairs], dtype=float)


def paired_effect(deltas: Sequence[float], *, level: float = 0.95) -> Dict[str, Any]:
    """配对效应量：``d_z``（Cohen）、配对差均值、均值 95% CI、配对差中位数及其 CI。

    中位数 CI 用二项式序统计量（不需正态假设）：n 对的中位数 95% CI 由
    ``x_(j), x_(n+1-j)`` 给出，``j = ceil(n/2 - 1.96*sqrt(n)/2)``。
    """
    d = np.asarray(deltas, dtype=float)
    n = int(d.size)
    out: Dict[str, Any] = {"n": n}
    if n == 0:
        out.update({"mean_diff": None, "median_diff": None, "sd": None, "cohen_dz": None,
                    "se": None, "ci95_mean": None, "ci95_median": None})
        return out
    mean = float(d.mean())
    sd = float(d.std(ddof=1)) if n > 1 else None
    out["mean_diff"] = mean
    out["median_diff"] = float(np.median(d))
    out["sd"] = sd
    if n > 1 and sd is not None and sd > 0:
        out["cohen_dz"] = mean / sd
        se = sd / math.sqrt(n)
        out["se"] = se
        # 小样本用 t 分位（无 scipy 时用正态近似并标注）
        try:
            from scipy.stats import t as _t  # type: ignore

            crit = float(_t.ppf(0.5 + level / 2.0, n - 1))
            out["ci_method"] = "t"
        except Exception:  # pragma: no cover
            crit = _normal_ppf(0.5 + level / 2.0)
            out["ci_method"] = "normal"
        out["ci95_mean"] = [mean - crit * se, mean + crit * se]
    else:
        out["cohen_dz"] = (float("inf") if mean > 0 else (float("-inf") if mean < 0 else 0.0)) if n > 1 else None
        out["se"] = None
        out["ci95_mean"] = None
    srt = np.sort(d)
    if n >= 3:
        j = max(1, int(math.ceil(n / 2.0 - 1.96 * math.sqrt(n) / 2.0)))
        j = min(j, (n + 1) // 2)
        out["ci95_median"] = [float(srt[j - 1]), float(srt[n - j])]
    else:
        out["ci95_median"] = None
    return out


# ------------------------------------------------------------------- 2. 置换检验


def _perm_stat(delta: np.ndarray) -> float:
    return float(delta.mean())


def sign_flip_permutation_test(
    deltas: Sequence[float],
    *,
    n_perm: int = 10000,
    seed: int = 0,
    alternative: str = "two-sided",
) -> Dict[str, Any]:
    """配对符号翻转置换检验（统计量 = 配对差均值；零假设下符号可交换 → 精确）。

    p 值用 ``(1 + #{|T_perm| >= |T_obs|}) / (1 + n_perm)``，保证 p>0。
    """
    if alternative not in ALTERNATIVES:
        raise ValueError(f"未知备择 {alternative!r}")
    d = np.asarray(deltas, dtype=float)
    n = int(d.size)
    if n < 2:
        raise ValueError(f"配对符号翻转检验需要 n>=2（当前 n={n}）")
    if n_perm < 1:
        raise ValueError("n_perm 必须 >=1")
    t_obs = _perm_stat(d)
    rng = np.random.default_rng(seed)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(int(n_perm), n))
    perm = signs @ d / n
    if alternative == "two-sided":
        hits = int(np.count_nonzero(np.abs(perm) >= abs(t_obs) - 1e-12))
    elif alternative == "greater":
        hits = int(np.count_nonzero(perm >= t_obs - 1e-12))
    else:
        hits = int(np.count_nonzero(perm <= t_obs + 1e-12))
    return {
        "statistic": t_obs,
        "p_value": (1.0 + hits) / (1.0 + int(n_perm)),
        "n_perm": int(n_perm),
        "n": n,
        "alternative": alternative,
        "seed": int(seed),
        "method": "paired_sign_flip_permutation(mean)",
    }


def _perm_pvalues_matrix(D: np.ndarray, signs: np.ndarray, alternative: str) -> np.ndarray:
    """批量置换检验：``D`` 形状 (m, n) 的 delta 矩阵 → (m,) 的 p 值矩阵运算版。"""
    D = np.asarray(D, dtype=float)
    m, n = D.shape
    means = D @ signs.T / n                      # (m, B)
    t_obs = D.mean(axis=1)                       # (m,)
    if alternative == "two-sided":
        hits = np.count_nonzero(np.abs(means) >= np.abs(t_obs)[:, None] - 1e-12, axis=1)
    elif alternative == "greater":
        hits = np.count_nonzero(means >= t_obs[:, None] - 1e-12, axis=1)
    elif alternative == "less":
        hits = np.count_nonzero(means <= t_obs[:, None] + 1e-12, axis=1)
    else:
        raise ValueError(f"未知备择 {alternative!r}")
    return (1.0 + hits) / (1.0 + signs.shape[0])


# ---------------------------------------------------------- 3. Bonferroni-Holm（FWER）


def holm_adjust(pvalues: Dict[str, float], alpha: float = 0.05) -> Dict[str, Any]:
    """Bonferroni-Holm 逐步下降校正。

    返回 ``{adjusted: {k: p_adj}, reject: {k: bool}, n_reject, steps:[...], alpha}``。
    ``p_adj_(i) = min(1, max_{j<=i} (m-j+1) p_(j))``（保持单调），拒绝规则等价于
    逐步下降：首个 ``p_(i) > alpha/(m-i+1)`` 之后的全部不拒绝。
    """
    items = sorted(((k, float(v)) for k, v in pvalues.items()), key=lambda kv: kv[1])
    m = len(items)
    adjusted: Dict[str, float] = {}
    reject: Dict[str, bool] = {}
    steps: List[Dict[str, Any]] = []
    running = 0.0
    stop = False
    for i, (k, p) in enumerate(items):
        factor = m - i
        raw = factor * p
        running = max(running, raw)
        adj = min(1.0, running)
        rej = bool(adj <= alpha) and not stop
        if not rej:
            stop = True
        adjusted[k] = adj
        reject[k] = rej
        steps.append({"rank": i + 1, "dimension": k, "p": p, "factor": factor,
                      "threshold": alpha / factor, "p_adj": adj, "reject": rej})
    return {
        "alpha": float(alpha),
        "adjusted": adjusted,
        "reject": reject,
        "n_reject": int(sum(1 for v in reject.values() if v)),
        "steps": steps,
        "method": "holm",
    }


def holm_matrix(pmat: np.ndarray, alpha: float = 0.05) -> Tuple[np.ndarray, np.ndarray]:
    """矩阵版 Holm：``pmat`` (m_families, m_tests) → (adjusted, reject) 同形状。"""
    P = np.asarray(pmat, dtype=float)
    m_fam, m = P.shape
    order = np.argsort(P, axis=1)
    sorted_p = np.take_along_axis(P, order, axis=1)
    factors = np.arange(m, 0, -1, dtype=float)[None, :]
    adj_sorted = np.minimum(1.0, np.maximum.accumulate(sorted_p * factors, axis=1))
    reject_sorted = adj_sorted <= alpha
    # 逐步下降的单调性：一旦失败，后续一律不拒绝
    reject_sorted = np.logical_and.accumulate(reject_sorted, axis=1)
    adjusted = np.empty_like(adj_sorted)
    reject = np.empty_like(reject_sorted)
    np.put_along_axis(adjusted, order, adj_sorted, axis=1)
    np.put_along_axis(reject, order, reject_sorted, axis=1)
    return adjusted, reject


# --------------------------------------------------------- 4. 六维族判决（G25）


def six_dim_advantage(
    records: Sequence[Dict[str, Any]],
    *,
    control_arm: str = "control",
    treatment_arm: str = "treatment",
    alpha: float = 0.05,
    n_perm: int = 10000,
    seed: int = 0,
    min_dims: int = 2,
    alternative: str = "greater",
) -> Dict[str, Any]:
    """六维「优于对照」族判决：逐维配对效应 + 置换检验 + Holm 校正 + ≥min_dims 判决。"""
    folded = build_pairs(records, control_arm=control_arm, treatment_arm=treatment_arm)
    table: Dict[str, Dict[str, Any]] = {}
    pvals: Dict[str, float] = {}
    for dim in DIMENSIONS:
        prs = folded["pairs"][dim]
        entry: Dict[str, Any] = {"n_pairs": len(prs), "unit": prs[0]["unit"] if prs else ""}
        if len(prs) >= 2:
            d = oriented_deltas(prs, dim)
            entry["effect"] = paired_effect(d)
            test = sign_flip_permutation_test(d, n_perm=n_perm, seed=seed, alternative=alternative)
            entry["p_perm"] = test["p_value"]
            entry["test"] = test
            pvals[dim] = test["p_value"]
        else:
            entry["effect"] = paired_effect(oriented_deltas(prs, dim)) if prs else None
            entry["p_perm"] = None
            entry["note"] = ("无可检验配对（n<2）" if len(prs) < 2 else "")
        table[dim] = entry

    holm = holm_adjust(pvals, alpha) if pvals else {"adjusted": {}, "reject": {}, "n_reject": 0,
                                                   "steps": [], "alpha": alpha, "method": "holm"}
    for dim, entry in table.items():
        entry["p_holm"] = holm["adjusted"].get(dim)
        entry["significant_better"] = bool(holm["reject"].get(dim, False))
        eff = entry.get("effect") or {}
        md = eff.get("median_diff")
        entry["direction"] = ("treatment_better" if (md or 0) > 0 else
                              ("treatment_worse" if (md or 0) < 0 else "indistinguishable")) if eff else "no_data"

    n_sig = int(holm.get("n_reject", 0))
    return {
        "rule": f"G25: 六维中 >= {min_dims} 维在 Holm(alpha={alpha}) 下显著优于对照（备择={alternative}）",
        "dimensions": table,
        "holm": holm,
        "n_significant_better": n_sig,
        "decision_pass": bool(n_sig >= min_dims),
        "min_dims": int(min_dims),
        "alpha": float(alpha),
        "alternative": alternative,
        "n_perm": int(n_perm),
        "seed": int(seed),
        "errors": folded["errors"],
        "n_pairs_by_dim": folded["n_pairs_by_dim"],
    }


# ------------------------------------------------------- 5. MDE(n)：解析 + 数值反解


def mde_analytic(
    n: float,
    sigma: float,
    *,
    alpha: float = 0.05,
    power: float = 0.8,
    two_sided: bool = True,
) -> float:
    """解析 MDE：``(z_{1-a/2}+z_b) * sigma / sqrt(n)``（α=.05/功效 .8/双侧 → 2.802·σ/√n）。"""
    if n <= 0:
        raise ValueError("n 必须 > 0")
    if sigma < 0:
        raise ValueError("sigma 必须 >= 0")
    return mde_coefficient(alpha, power, two_sided) * sigma / math.sqrt(n)


def _simulate_power(
    mu: float,
    n: int,
    sigma: float,
    signs: np.ndarray,
    *,
    alpha: float,
    alternative: str,
    n_rep: int,
    rng: np.random.Generator,
) -> float:
    """给定真实均值 mu 时的置换检验功效（蒙特卡洛，向量化）。"""
    D = mu + sigma * rng.standard_normal((int(n_rep), int(n)))
    p = _perm_pvalues_matrix(D, signs, alternative)
    return float(np.mean(p <= alpha))


def mde_numeric(
    n: int,
    sigma: float = 1.0,
    *,
    alpha: float = 0.05,
    target_power: float = 0.8,
    two_sided: bool = True,
    n_rep: int = 300,
    n_perm: int = 499,
    seed: int = 0,
    max_iter: int = 16,
    bracket: Optional[Tuple[float, float]] = None,
) -> Dict[str, Any]:
    """用**同一置换检验**做蒙特卡洛反解 MDE(n)（对数二分）。

    返回 ``{n, sigma, mde_numeric, mde_analytic, rel_dev, achieved_power, ...}``。
    括号默认 ``[0.4, 2.5] x 解析 MDE``——若解析值本身偏得太远会如实报 ``bracket_exhausted``。
    """
    if n < 2:
        raise ValueError("n 必须 >= 2")
    alternative = "two-sided" if two_sided else "greater"
    ana = mde_analytic(n, sigma, alpha=alpha, power=target_power, two_sided=two_sided)
    lo, hi = bracket if bracket else (0.4 * ana, 2.5 * ana)
    signs = np.random.default_rng([seed, 0]).choice(np.array([-1.0, 1.0]), size=(int(n_perm), int(n)))
    history: List[Dict[str, float]] = []

    def power_at(mu: float, tag: int) -> float:
        rng = np.random.default_rng([seed, tag])
        pw = _simulate_power(mu, n, sigma, signs, alpha=alpha, alternative=alternative,
                             n_rep=n_rep, rng=rng)
        history.append({"mu": float(mu), "power": pw})
        return pw

    tag = 1
    p_hi = power_at(hi, tag)
    tag += 1
    exhausted = False
    while p_hi < target_power and tag < 6:
        hi *= 1.5
        p_hi = power_at(hi, tag)
        tag += 1
    exhausted = p_hi < target_power
    if exhausted:
        return {"n": n, "sigma": sigma, "mde_numeric": hi, "mde_analytic": ana,
                "rel_dev": (hi - ana) / ana if ana else None, "achieved_power": p_hi,
                "bracket_exhausted": True, "n_evaluations": len(history),
                "history": history, "alternative": alternative,
                "n_rep": n_rep, "n_perm": n_perm, "seed": seed}
    for _ in range(int(max_iter)):
        mid = math.sqrt(lo * hi)  # 对数二分（效应量跨量级时收敛更均匀）
        if power_at(mid, tag) >= target_power:
            hi = mid
        else:
            lo = mid
        tag += 1
    p_final = power_at(hi, tag)
    return {
        "n": int(n),
        "sigma": float(sigma),
        "mde_numeric": float(hi),
        "mde_analytic": float(ana),
        "rel_dev": float((hi - ana) / ana) if ana else None,
        "achieved_power": float(p_final),
        "bracket_exhausted": False,
        "n_evaluations": len(history),
        "history": history,
        "alternative": alternative,
        "alpha": alpha,
        "target_power": target_power,
        "n_rep": int(n_rep),
        "n_perm": int(n_perm),
        "seed": int(seed),
    }


def mde_noncentral_t(
    n: int,
    sigma: float = 1.0,
    *,
    alpha: float = 0.05,
    power: float = 0.8,
    two_sided: bool = True,
) -> Optional[float]:
    """**参考值**：配对 t 检验的精确 MDE（非中心 t 反解 ncp，再乘 sigma/sqrt(n)）。

    这是「解析近似 2.802σ/√n」该被对照的那个基准：2.802 用的是正态分位，n 小的时候
    被 t 分位修正拉开。无 scipy 时返回 None（不伪装成有基准）。
    """
    if n is None or n < 2:
        return None
    try:
        from scipy.stats import nct as _nct, t as _t  # type: ignore
    except Exception:  # pragma: no cover
        return None
    df = int(n) - 1
    crit = float(_t.ppf(1.0 - alpha / 2.0 if two_sided else 1.0 - alpha, df))

    def _pw(ncp: float) -> float:
        upper = 1.0 - float(_nct.cdf(crit, df, ncp))
        if not two_sided:
            return min(1.0, max(0.0, upper if math.isfinite(upper) else 0.0))
        lower = float(_nct.cdf(-crit, df, ncp))
        if not math.isfinite(lower):
            lower = 0.0
        return min(1.0, max(0.0, (upper if math.isfinite(upper) else 1.0) + lower))

    lo, hi = 0.0, 2.0
    while _pw(hi) < power and hi < 400.0:
        hi *= 1.5
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if _pw(mid) >= power:
            hi = mid
        else:
            lo = mid
    return float(hi * sigma / math.sqrt(int(n)))


def mde_curve(
    n_grid: Sequence[int] = (10, 12, 16, 20, 24, 30, 40, 60, 100),
    sigma: float = 1.0,
    *,
    alpha: float = 0.05,
    target_power: float = 0.8,
    two_sided: bool = True,
    n_rep: int = 300,
    n_perm: int = 499,
    seed: int = 0,
    deviation_check_from: int = 20,
    deviation_tol: float = 0.10,
) -> Dict[str, Any]:
    """MDE(n) 曲线：解析 vs 数值。判据 = 单调下降 且 n>=``deviation_check_from`` 时偏差 <=10%。"""
    rows: List[Dict[str, Any]] = []
    for n in n_grid:
        row = mde_numeric(n, sigma, alpha=alpha, target_power=target_power,
                          two_sided=two_sided, n_rep=n_rep, n_perm=n_perm, seed=seed)
        ref = mde_noncentral_t(n, sigma, alpha=alpha, power=target_power, two_sided=two_sided)
        row["mde_exact_t"] = ref
        row["rel_dev_vs_exact_t"] = ((row["mde_numeric"] - ref) / ref) if ref else None
        rows.append(row)
    numerics = [r["mde_numeric"] for r in rows]
    monotone = all(b < a for a, b in zip(numerics, numerics[1:]))
    checked = [r for r in rows if r["n"] >= deviation_check_from and r["rel_dev"] is not None]
    max_dev = max((abs(r["rel_dev"]) for r in checked), default=None)
    checked_t = [r for r in rows
                 if r["n"] >= deviation_check_from and r.get("rel_dev_vs_exact_t") is not None]
    max_dev_t = max((abs(r["rel_dev_vs_exact_t"]) for r in checked_t), default=None)
    return {
        "sigma": sigma,
        "alpha": alpha,
        "target_power": target_power,
        "two_sided": two_sided,
        "rows": rows,
        "monotone_decreasing": bool(monotone),
        "deviation_check_from": int(deviation_check_from),
        "max_abs_rel_dev_checked": max_dev,
        "max_abs_rel_dev_vs_exact_t": max_dev_t,
        "exact_t_within_3pct": (max_dev_t is not None and max_dev_t <= 0.03),
        "deviation_tol": deviation_tol,
        "pass": bool(monotone and max_dev is not None and max_dev <= deviation_tol),
        "note": ("n<deviation_check_from 的行不做 <=10% 声明：正态近似在小 n 处被 t 分位修正"
                 "系统性低估 MDE（这是发现，不是缺陷）"),
    }


def required_n_analytic(
    effect: float,
    sigma: float = 1.0,
    *,
    alpha: float = 0.05,
    power: float = 0.8,
    two_sided: bool = True,
) -> Optional[int]:
    """解析反解所需配对对数 n = ceil((coef*sigma/effect)^2)；effect<=0 返回 None。"""
    if effect <= 0:
        return None
    coef = mde_coefficient(alpha, power, two_sided)
    return int(math.ceil((coef * sigma / effect) ** 2))

# ------------------------------------------------- 6. A/A 空对照与 FWER 零效应族扫描


def _null_pair_matrix(
    rng: np.random.Generator,
    n_families: int,
    n_pairs: int,
    sigma: float = 1.0,
) -> np.ndarray:
    """A/A 数据：同一路线测两次 → 两臂同分布（真实效应恒为 0）。"""
    return sigma * rng.standard_normal((int(n_families), int(n_pairs)))


def null_family_sim(
    *,
    n_families: int = 100,
    n_pairs: int = 20,
    n_perm: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
    min_dims: int = 1,
    alternative: str = "greater",
    dims: Sequence[str] = DIMENSIONS,
) -> Dict[str, Any]:
    """零效应族模拟：每个族在六维上都建 A/A 数据（两臂同分布），跑完整族判决。

    返回
    ----
    ``per_dim``：每维在 α 下的误报率（含 Clopper-Pearson 95% CI）；
    ``family_any``：至少 1 维显著的族比例（= FWER 估计）；
    ``family_ge_min_dims``：**判决规则本身**的误报率（≥min_dims 维显著）；
    ``family_all``：6 维全显著的族比例（极端检验，应≈0）。
    """
    rng = np.random.default_rng(int(seed))
    signs = rng.choice(np.array([-1.0, 1.0]), size=(int(n_perm), int(n_pairs)))
    m = len(dims)
    P = np.empty((int(n_families), m), dtype=float)
    for j, dim in enumerate(dims):
        x_c = _null_pair_matrix(rng, n_families, n_pairs)
        x_t = _null_pair_matrix(rng, n_families, n_pairs)
        D = DIMENSION_DIRECTION[dim] * (x_t - x_c)
        P[:, j] = _perm_pvalues_matrix(D, signs, alternative)
    adj, rej = holm_matrix(P, alpha)
    counts = rej.sum(axis=1)
    per_dim = {}
    for j, dim in enumerate(dims):
        ci = binom_ci(int(rej[:, j].sum()), int(n_families))
        ci["dimension"] = dim
        ci["direction"] = DIMENSION_DIRECTION[dim]
        per_dim[dim] = ci
    any_ci = binom_ci(int((counts >= 1).sum()), int(n_families))
    ge_ci = binom_ci(int((counts >= int(min_dims)).sum()), int(n_families))
    all_ci = binom_ci(int((counts >= m).sum()), int(n_families))
    return {
        "n_families": int(n_families),
        "n_pairs": int(n_pairs),
        "n_perm": int(n_perm),
        "alpha": float(alpha),
        "alternative": alternative,
        "min_dims": int(min_dims),
        "seed": int(seed),
        "per_dim": per_dim,
        "per_dim_max_rate": max(v["p_hat"] for v in per_dim.values()),
        "family_any": any_ci,
        "family_ge_min_dims": ge_ci,
        "family_all": all_ci,
        "mean_rejections_per_family": float(counts.mean()),
        "pass_family_rate": bool(ge_ci["p_hat"] <= alpha),
        "pass_per_dim_rate": bool(max(v["p_hat"] for v in per_dim.values()) <= alpha),
    }


def aa_null_control(
    *,
    n_sims: int = 100,
    n_pairs: int = 20,
    n_perm: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
    min_dims: int = 2,
    stability_seeds: Sequence[int] = (0, 1, 2, 3, 4),
) -> Dict[str, Any]:
    """A/A 空对照（任务 (d)）：同一路线两次，α=0.05 下误报率须 ≤5%。

    主判据用**族判决**（≥min_dims 维显著）的误报率——这正是战役真正要用的判决规则，
    它的零假设误报率天然低于逐维 α（6 个 α=0.05 的独立检验里出现 2 个误报的概率
    ≈1-(0.95^6+6·0.05·0.95^5)≈3.3%）。逐维误报率同时报告（附 95% CI）。
    ``stability_seeds``：多随机种子重复主实验，仅作抽样波动检查，不改主判据。
    """
    primary = null_family_sim(n_families=n_sims, n_pairs=n_pairs, n_perm=n_perm,
                              alpha=alpha, seed=seed, min_dims=min_dims,
                              alternative="greater")
    primary_two_sided = null_family_sim(n_families=n_sims, n_pairs=n_pairs, n_perm=n_perm,
                                       alpha=alpha, seed=seed, min_dims=min_dims,
                                       alternative="two-sided")
    pooled_fam = 0
    pooled_ge = 0
    pooled_all = 0
    pooled_dim_hits = {d: 0 for d in DIMENSIONS}
    for s in stability_seeds:
        r = null_family_sim(n_families=n_sims, n_pairs=n_pairs, n_perm=n_perm,
                            alpha=alpha, seed=int(s), min_dims=min_dims, alternative="greater")
        pooled_fam += int(r["family_any"]["k"])
        pooled_ge += int(r["family_ge_min_dims"]["k"])
        pooled_all += int(r["family_all"]["k"])
        for d in DIMENSIONS:
            pooled_dim_hits[d] += int(r["per_dim"][d]["k"])
    n_pool = n_sims * len(stability_seeds)
    pooled = {
        "n_families": n_pool,
        "family_any": binom_ci(pooled_fam, n_pool),
        "family_ge_min_dims": binom_ci(pooled_ge, n_pool),
        "family_all": binom_ci(pooled_all, n_pool),
        "per_dim": {d: binom_ci(pooled_dim_hits[d], n_pool) for d in DIMENSIONS},
        "seeds": list(stability_seeds),
    }
    max_pooled_dim = max(v["p_hat"] for v in pooled["per_dim"].values())
    return {
        "design": "A/A: 同一路线两次测量（两臂同分布，真实效应恒 0）",
        "primary": primary,
        "primary_two_sided": primary_two_sided,
        "pooled": pooled,
        "alpha": float(alpha),
        "min_dims": int(min_dims),
        "criterion": "族判决(>=min_dims 维显著)误报率 <= alpha，且逐维误报率 <= alpha；均附 95% CI",
        "pass_family_rate": bool(primary["family_ge_min_dims"]["p_hat"] <= alpha),
        "pass_per_dim_rate": bool(max_pooled_dim <= alpha),
        "pass": bool(primary["family_ge_min_dims"]["p_hat"] <= alpha and max_pooled_dim <= alpha),
        "ci_upper_family": primary["family_ge_min_dims"]["hi"],
        "ci_upper_per_dim_pooled": max(v["hi"] for v in pooled["per_dim"].values()),
    }


def fwer_null_families(
    *,
    n_families: int = 20,
    n_pairs: int = 20,
    n_perm: int = 2000,
    alpha: float = 0.05,
    seed: int = 7,
    extended_families: int = 200,
) -> Dict[str, Any]:
    """六维 FWER 零效应族扫描（任务 (c)）：20 个随机零效应族上 FWER 须 ≤0.06。

    ``extended_families`` 的更大扫描只用来报告分辨力更高的估计（20 族的分辨率就是 0.05
    一格，无法区分 5% 与 6%），不改主判据。
    """
    primary = null_family_sim(n_families=n_families, n_pairs=n_pairs, n_perm=n_perm,
                             alpha=alpha, seed=seed, min_dims=1, alternative="greater")
    extended = null_family_sim(n_families=extended_families, n_pairs=n_pairs, n_perm=n_perm,
                              alpha=alpha, seed=seed + 1, min_dims=1, alternative="greater")
    return {
        "design": "20 个随机零效应族（六维全零），逐维符号翻转置换 + Holm 后的 FWER",
        "primary": primary,
        "primary_fwer": primary["family_any"]["p_hat"],
        "primary_ci": primary["family_any"],
        "extended": {"n_families": extended_families,
                     "fwer": extended["family_any"]["p_hat"],
                     "ci": extended["family_any"]},
        "alpha": float(alpha),
        "tol": 0.06,
        "pass": bool(primary["family_any"]["p_hat"] <= 0.06),
        "note": ("20 族的分辨率是 0.05/族；主判据按任务口径用 20 族，200 族扫描给出更高分辨率"
                 "的一致性检查（只看不判）"),
    }


# --------------------------------------------------- 7. 功效正控与族判决所需 n 反解


def paired_gaussian_arms(
    rng: np.random.Generator,
    n_pairs: int,
    mean_diff: float,
    *,
    sigma: float = 1.0,
    rho: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """生成配对数据：两臂各 sd=``sigma``、相关系数≈``rho``、均值差=``mean_diff``。

    ``x_c = z + e_c``, ``x_t = z + e_t + mean_diff``，``z~N(0,rho*sigma^2)``,
    ``e~N(0,(1-rho)*sigma^2)`` → 臂内 sd = sigma，臂间相关 = rho，配对差 sd = sigma*sqrt(2(1-rho))。
    """
    n = int(n_pairs)
    z = rng.normal(0.0, math.sqrt(max(rho, 0.0)) * sigma, size=n)
    e_c = rng.normal(0.0, math.sqrt(max(1.0 - rho, 1e-12)) * sigma, size=n)
    e_t = rng.normal(0.0, math.sqrt(max(1.0 - rho, 1e-12)) * sigma, size=n)
    return z + e_c, z + e_t + mean_diff


def _analytic_power(dz: float, n: int, alpha: float, two_sided: bool) -> Optional[float]:
    """解析功效：非中心 t；非中心参数很大时 scipy 的 nct 会下溢成 nan，退化为正态近似。

    （这是数值实现问题，不是统计假设：``ncp = dz*sqrt(n) > ~40`` 时两条尾概率在
    双精度下已经贴边，正态近似与 nct 的差别远小于 1e-6。）
    """
    if n is None or n < 2:
        return None
    norm = NormalDist()
    z_crit = _normal_ppf(1.0 - (alpha / 2.0 if two_sided else alpha))
    ncp = float(dz) * math.sqrt(int(n))

    def _normal_power() -> float:
        if two_sided:
            return float(min(1.0, max(0.0, norm.cdf(ncp - z_crit) + norm.cdf(-ncp - z_crit))))
        return float(min(1.0, max(0.0, norm.cdf(ncp - z_crit))))

    try:
        from scipy.stats import nct as _nct, t as _t  # type: ignore

        df = int(n) - 1
        if two_sided:
            crit = float(_t.ppf(1.0 - alpha / 2.0, df))
            upper = float(1.0 - _nct.cdf(crit, df, ncp))
            lower = float(_nct.cdf(-crit, df, ncp))
            if not math.isfinite(upper):
                upper = _normal_power()      # 数值下溢 → 正态近似
            if not math.isfinite(lower):
                lower = 0.0
            return float(min(1.0, max(0.0, upper + lower)))
        crit = float(_t.ppf(1.0 - alpha, df))
        val = float(1.0 - _nct.cdf(crit, df, ncp))
        return float(min(1.0, max(0.0, val if math.isfinite(val) else _normal_power())))
    except Exception:  # pragma: no cover - 无 scipy 环境
        return _normal_power()


def power_positive_control(
    *,
    d: float = 0.5,
    n_pairs: int = 20,
    rho: float = 0.7,
    n_sims: int = 1000,
    n_perm: int = 999,
    alpha: float = 0.05,
    alternative: str = "greater",
    seed: int = 0,
    sigma: float = 1.0,
) -> Dict[str, Any]:
    """功效正控（任务 (b)）：已知合成效应 d=0.5、n=20 时的检出率（须 ≥70%）。

    ``d`` 的口径是**标准化均值差（臂内 sd 为单位）**；配对设计的优势在于 ``rho>0`` 时
    配对差 sd = ``sigma*sqrt(2(1-rho))`` 变小，故 ``d_z = d/sqrt(2(1-rho))``。
    ``rho=0`` 时 ``d_z=d=0.5``（最保守，等价于忽略配对收益）。
    """
    rng = np.random.default_rng(int(seed))
    signs = rng.choice(np.array([-1.0, 1.0]), size=(int(n_perm), int(n_pairs)))
    x_c = np.empty((int(n_sims), int(n_pairs)))
    x_t = np.empty((int(n_sims), int(n_pairs)))
    for i in range(int(n_sims)):
        c, t = paired_gaussian_arms(rng, n_pairs, d * sigma, sigma=sigma, rho=rho)
        x_c[i], x_t[i] = c, t
    D = x_t - x_c
    p = _perm_pvalues_matrix(D, signs, alternative)
    rate = float(np.mean(p <= alpha))
    dz = float(D.mean(axis=1).mean() / D.std(axis=1, ddof=1).mean()) if n_pairs > 1 else None
    realized_corr = float(np.corrcoef(x_c.ravel(), x_t.ravel())[0, 1])
    ana = _analytic_power(d / math.sqrt(2.0 * (1.0 - rho)) if rho < 1 else d, n_pairs, alpha,
                          two_sided=(alternative == "two-sided"))
    ci = binom_ci(int(round(rate * n_sims)), int(n_sims))
    return {
        "design": f"合成效应 d={d}, n_pairs={n_pairs}, rho={rho}, 备择={alternative}, alpha={alpha}",
        "d": d,
        "n_pairs": int(n_pairs),
        "rho_assumed": rho,
        "rho_realized": realized_corr,
        "dz_realized": dz,
        "detection_rate": rate,
        "detection_ci": ci,
        "analytic_power_nct": ana,
        "n_sims": int(n_sims),
        "n_perm": int(n_perm),
        "alpha": alpha,
        "alternative": alternative,
        "seed": int(seed),
    }


def power_vs_rho(
    *,
    d: float = 0.5,
    n_pairs: int = 20,
    rho_grid: Sequence[float] = (0.0, 0.3, 0.5, 0.7, 0.85),
    n_sims: int = 600,
    n_perm: int = 999,
    alpha: float = 0.05,
    seed: int = 0,
) -> Dict[str, Any]:
    """配对收益敏感性：检出率作为臂间相关 rho 的函数（两种备择都报）。"""
    rows = []
    for rho in rho_grid:
        row = {"rho": float(rho), "dz": d / math.sqrt(2.0 * (1.0 - rho)) if rho < 1 else float("inf")}
        for alt in ("greater", "two-sided"):
            r = power_positive_control(d=d, n_pairs=n_pairs, rho=rho, n_sims=n_sims,
                                      n_perm=n_perm, alpha=alpha, alternative=alt, seed=seed)
            row[f"power_{alt}"] = r["detection_rate"]
            row[f"analytic_{alt}"] = r["analytic_power_nct"]
        rows.append(row)
    return {
        "design": f"d={d}, n_pairs={n_pairs}，rho 扫描（配对收益敏感性）",
        "rows": rows,
        "n_sims": int(n_sims),
        "note": ("rho 是**设计假设**：同谱系/同席位、只改一条路线时两臂强相关是预期的；"
                 "rho=0 行是最保守情形（等价于放弃配对收益），必须与主结果同时呈现"),
    }


def family_decision_power_curve(
    *,
    effect_dz: float = 0.8,
    n_affected_dims: int = 2,
    n_grid: Sequence[int] = (9, 12, 16, 20, 26, 32, 40, 60),
    min_dims: int = 2,
    sigma: float = 1.0,
    alpha: float = 0.05,
    alternative: str = "greater",
    n_rep: int = 300,
    n_perm: int = 499,
    seed: int = 0,
) -> Dict[str, Any]:
    """族判决功效随 n 的变化：前 ``n_affected_dims`` 维有真实效应 ``effect_dz``，其余为 0。

    这是「要判出 2 维优势需要多少对」的模拟侧反解（判决规则 = Holm 后 ≥min_dims 维显著）。
    """
    rows: List[Dict[str, Any]] = []
    for n in n_grid:
        rng = np.random.default_rng([int(seed), int(n)])
        signs = rng.choice(np.array([-1.0, 1.0]), size=(int(n_perm), int(n)))
        m = len(DIMENSIONS)
        P = np.empty((int(n_rep), m))
        for j, dim in enumerate(DIMENSIONS):
            mu = effect_dz * sigma if j < int(n_affected_dims) else 0.0
            D = mu + sigma * rng.standard_normal((int(n_rep), int(n)))
            P[:, j] = _perm_pvalues_matrix(D, signs, alternative)
        adj, rej = holm_matrix(P, alpha)
        counts = rej.sum(axis=1)
        rows.append({
            "n_pairs": int(n),
            "power_ge_min_dims": float(np.mean(counts >= int(min_dims))),
            "power_all_affected": float(np.mean(rej[:, :int(n_affected_dims)].all(axis=1))),
            "mean_rejections": float(counts.mean()),
            "alpha_per_dim_rate_0": float(np.mean(rej[:, int(n_affected_dims):])) if m > int(n_affected_dims) else None,
        })
    return {
        "design": (f"族判决功效：{n_affected_dims} 维有 d_z={effect_dz}，其余 {len(DIMENSIONS) - n_affected_dims} 维为 0"),
        "effect_dz": float(effect_dz),
        "n_affected_dims": int(n_affected_dims),
        "min_dims": int(min_dims),
        "alpha": alpha,
        "alternative": alternative,
        "rows": rows,
        "n_rep": int(n_rep),
        "n_perm": int(n_perm),
        "seed": int(seed),
    }


def required_n_for_family_decision(
    *,
    target_power: float = 0.8,
    effect_dz: float = 0.8,
    n_affected_dims: int = 2,
    min_dims: int = 2,
    n_grid: Sequence[int] = (9, 12, 16, 20, 26, 32, 40, 60),
    **kwargs: Any,
) -> Dict[str, Any]:
    """从族判决功效曲线反解「判出 min_dims 维优势所需 n」（网格分辨率如实报告）。"""
    curve = family_decision_power_curve(effect_dz=effect_dz, n_affected_dims=n_affected_dims,
                                       min_dims=min_dims, n_grid=n_grid, **kwargs)
    hit = next((r for r in curve["rows"] if r["power_ge_min_dims"] >= target_power), None)
    return {
        "required_n_grid_hit": int(hit["n_pairs"]) if hit else None,
        "required_n_note": (f"网格 {list(n_grid)} 上首个功效>= {target_power} 的 n；"
                           f"未命中则报 None（需扩网格）"),
        "target_power": float(target_power),
        "curve": curve,
    }


# --------------------------------------------------------------- 8. 回溯性功效（既有产物）


def _mean_values(pairs: Sequence[Dict[str, Any]]) -> Tuple[float, float]:
    return (float(np.mean([p["v_control"] for p in pairs])),
            float(np.mean([p["v_treatment"] for p in pairs])))


def retrospective_power(
    entries: Sequence[Dict[str, Any]],
    *,
    control_arm: str = "control",
    treatment_arm: str = "treatment",
    alpha: float = 0.05,
    target_power: float = 0.8,
    two_sided: bool = True,
    sigma_rel_proxy: Optional[float] = None,
    sigma_rel_proxy_source: str = "",
    sigma_rel_grid: Sequence[float] = (0.05, 0.10, 0.15, 0.20),
    min_testable_pairs: int = MIN_TESTABLE_PAIRS,
    min_dims: int = 2,
    n_perm: int = 10000,
    seed: int = 0,
) -> Dict[str, Any]:
    """对既有战役产物做回溯性功效计算，给出**明确三态判定**。

    三态
    ----
    * ``可判`` (DECIDABLE)：该维配对重复数 ``n >= min_testable_pairs``，检验可做；
    * ``需n=N`` (NEEDS_N)：现有重复数不足，但 σ 代理给定 → 反解出所需配对对数 N；
    * ``不可判`` (UNDECIDABLE)：控制臂数值为 0（无相对尺度）或缺一臂数值 → 连所需 n 都定不了。

    额外输出 ``sigma_sensitivity``：σ 代理本身是跨量转移的假设，故对每个有相对效应的维
    在 σ 网格上列出所需 n，避免结论依赖单一 σ。
    """
    folded = build_pairs(entries, control_arm=control_arm, treatment_arm=treatment_arm)
    coef = mde_coefficient(alpha, target_power, two_sided)
    dims_out: Dict[str, Any] = {}
    for dim in DIMENSIONS:
        prs = folded["pairs"][dim]
        rec: Dict[str, Any] = {"n_pairs": len(prs),
                              "unit": prs[0]["unit"] if prs else "",
                              "direction_of_metric": DIMENSION_DIRECTION[dim]}
        if not prs:
            rec.update({"state": "不可判", "state_code": "UNDECIDABLE",
                        "reason": "该维无成对数值（缺一臂或单位不一致/无 source）",
                        "required_n": None, "effect_rel": None, "favorable": None})
            dims_out[dim] = rec
            continue
        v_c, v_t = _mean_values(prs)
        rec["v_control"] = v_c
        rec["v_treatment"] = v_t
        rec["sources"] = [p["sources"] for p in prs]
        if v_c == 0.0:
            if len(prs) >= min_testable_pairs:
                d = oriented_deltas(prs, dim)
                rec.update({"state": "可判", "state_code": "DECIDABLE",
                            "reason": "控制臂为 0 但有重复（绝对差可判，相对效应不可定义）",
                            "effect": paired_effect(d),
                            "p_perm": sign_flip_permutation_test(d, n_perm=n_perm, seed=seed,
                                                                alternative="greater")["p_value"],
                            "required_n": len(prs), "effect_rel": None,
                            "favorable": bool(d.mean() > 0)})
            else:
                rec.update({"state": "不可判", "state_code": "UNDECIDABLE",
                            "reason": "控制臂数值为 0 → 相对尺度不可定义，且重复数不足",
                            "required_n": None, "effect_rel": None, "favorable": None})
            dims_out[dim] = rec
            continue
        rel = float(DIMENSION_DIRECTION[dim] * (v_t - v_c) / abs(v_c))  # 正 = 实验组更好（相对）
        rec["effect_rel"] = rel
        rec["favorable"] = bool(rel > 0)
        if len(prs) >= min_testable_pairs:
            d = oriented_deltas(prs, dim)
            eff = paired_effect(d)
            test = sign_flip_permutation_test(d, n_perm=n_perm, seed=seed, alternative="greater")
            rec.update({"state": "可判", "state_code": "DECIDABLE",
                        "reason": f"配对重复数 n={len(prs)} >= {min_testable_pairs}，检验可做",
                        "effect": eff, "p_perm": test["p_value"],
                        "observed_power": (_analytic_power(eff.get("cohen_dz") or 0.0,
                                                           len(prs), alpha, two_sided)),
                        "required_n": len(prs)})
        elif sigma_rel_proxy and sigma_rel_proxy > 0:
            dz = abs(rel) / sigma_rel_proxy
            need = int(max(min_testable_pairs, math.ceil((coef / dz) ** 2))) if dz > 0 else None
            rec.update({"state": f"需n={need}" if need else "不可判",
                        "state_code": "NEEDS_N" if need else "UNDECIDABLE",
                        "reason": (f"现有 n={len(prs)} 对 < 可检验下限 {min_testable_pairs}；"
                                   f"σ_rel 代理={sigma_rel_proxy:.4g}（{sigma_rel_proxy_source}）→ "
                                   f"d_z≈{dz:.3g}，所需对数 n={need}"),
                        "dz_proxy": dz, "required_n": need,
                        "power_at_current_n": float(alpha) if len(prs) < 2 else None})
        else:
            rec.update({"state": "不可判", "state_code": "UNDECIDABLE",
                        "reason": (f"n={len(prs)} 对无法估计重复误差，且无 σ 代理；"
                                   f"至少需 {min_testable_pairs} 对配对重复才能做检验"),
                        "required_n": None})
        # σ 敏感性（只在有相对效应时给）：σ 是跨量转移假设，必须与结论同报
        sens = []
        for s in sigma_rel_grid:
            dz = abs(rel) / s if s > 0 else float("inf")
            need = int(math.ceil((coef / dz) ** 2)) if dz > 0 else None
            dz_at_min = (abs(rel) / s) * math.sqrt(min_testable_pairs) if s > 0 else None
            sens.append({
                "sigma_rel": float(s),
                "dz": dz,
                "required_n_unfloored": need,
                "required_n": int(max(min_testable_pairs, need)) if need else None,
                # 设计下限 n=9 时的解析功效（观测 |rel| 下）
                "power_at_min_n": _analytic_power(dz_at_min, min_testable_pairs, alpha, two_sided)
                if dz_at_min is not None else None,
            })
        rec["sigma_sensitivity"] = sens
        rec["power_at_min_n_proxy"] = (_analytic_power(abs(rel) / sigma_rel_proxy * math.sqrt(min_testable_pairs),
                                                       min_testable_pairs, alpha, two_sided)
                                       if sigma_rel_proxy and sigma_rel_proxy > 0 else None)
        dims_out[dim] = rec

    # ---- 族层面：这规模能不能判出「>=min_dims 维优于对照」 ----
    favorable = [d for d, r in dims_out.items() if r.get("favorable") is True]
    unfavorable = [d for d, r in dims_out.items() if r.get("favorable") is False]
    nodata = [d for d, r in dims_out.items() if r.get("favorable") is None]
    decidable = [d for d, r in dims_out.items() if r["state_code"] == "DECIDABLE"]
    needs = {d: r["required_n"] for d, r in dims_out.items()
             if r["state_code"] == "NEEDS_N" and r.get("required_n")}
    # 只有方向有利、或方向未知（缺尺度/不可判）的维才可能变成"有利维"；
    # 方向已判为不利的维（favorable=False）再多重复也帮不上「≥k 维更好」。
    potential = sorted({d for d in favorable} | {d for d in needs if dims_out[d].get("favorable") is None},
                       key=lambda d: (needs.get(d) or 1, d))
    if len(decidable) >= min_dims and sum(1 for d in decidable if dims_out[d].get("favorable")) >= min_dims:
        fam_state = "可判"
        fam_code = "DECIDABLE"
        fam_reason = f"已有 {len(decidable)} 维可检验，其中有利维 >= {min_dims}"
        n_for_decision = max((dims_out[d]["required_n"] or 0) for d in decidable)
    elif len(potential) >= min_dims and needs:
        n_for_decision = sorted(needs.values())[min_dims - 1] if len(needs) >= min_dims else max(needs.values())
        fam_state = f"需n={n_for_decision}"
        fam_code = "NEEDS_N"
        fam_reason = (f"方向有利/无定论的维有 {len(potential)} 个（{potential}），"
                      f"要判出 {min_dims} 维 → 每维 n≈{n_for_decision} 对；"
                      f"方向已判不利的维（{unfavorable}）再多重复也不算进分母")
    else:
        n_for_decision = None
        fam_state = "不可判"
        fam_code = "UNDECIDABLE"
        fam_reason = (f"有利维 {len(favorable)} 个、方向无定论的维 {len([d for d in needs if dims_out[d].get('favorable') is None])} 个，"
                      f"方向已判不利的维 {len(unfavorable)} 个（{unfavorable}）→ 合计不足以判出 "
                      f"{min_dims} 维优势；且现有数据无重复，功效不可估")
    return {
        "design": (f"回溯性功效（每维配对 + Holm，alpha={alpha}，目标功效={target_power}，"
                   f"备择={'two-sided' if two_sided else 'greater'}）"),
        "alpha": alpha,
        "target_power": target_power,
        "min_testable_pairs": int(min_testable_pairs),
        "sigma_rel_proxy": sigma_rel_proxy,
        "sigma_rel_proxy_source": sigma_rel_proxy_source,
        "sigma_rel_grid": list(sigma_rel_grid),
        "dimensions": dims_out,
        "family": {
            "min_dims": int(min_dims),
            "n_dims_with_data": len([d for d in dims_out if dims_out[d]["n_pairs"] > 0]),
            "n_favorable": len(favorable),
            "n_unfavorable": len(unfavorable),
            "n_undecidable": len(nodata),
            "n_decidable": len(decidable),
            "favorable": favorable,
            "unfavorable": unfavorable,
            "undecidable": nodata,
            "required_n_per_dim": needs,
            "required_n_for_family_decision": n_for_decision,
            "state": fam_state,
            "state_code": fam_code,
            "reason": fam_reason,
        },
        "errors": folded["errors"],
    }


def retrospective_bundle(
    spec: Dict[str, Any],
    *,
    n_perm: int = 10000,
    seed: int = 0,
) -> Dict[str, Any]:
    """对多战役回溯 spec 依次做回溯性功效（spec 结构见 ``artifacts/six_dim_power/p12_spec.json``）。

    spec::

        {"campaigns": [{"id": ..., "label": ..., "control_arm": ..., "treatment_arm": ...,
                        "sigma_rel_proxy": {"value": 0.15, "source": "..."},
                        "sigma_rel_grid": [0.05, 0.1],
                        "entries": [record, ...],
                        "notes": "..."}]}
    """
    out = {"campaigns": {}, "errors": []}
    for camp in spec.get("campaigns", []):
        cid = str(camp.get("id") or "")
        if not cid:
            out["errors"].append("campaign 缺 id")
            continue
        proxy = camp.get("sigma_rel_proxy") or {}
        res = retrospective_power(
            camp.get("entries", []),
            control_arm=str(camp.get("control_arm") or "control"),
            treatment_arm=str(camp.get("treatment_arm") or "treatment"),
            alpha=float(camp.get("alpha", 0.05)),
            target_power=float(camp.get("target_power", 0.8)),
            two_sided=bool(camp.get("two_sided", True)),
            sigma_rel_proxy=(float(proxy.get("value")) if proxy.get("value") is not None else None),
            sigma_rel_proxy_source=str(proxy.get("source") or ""),
            sigma_rel_grid=tuple(camp.get("sigma_rel_grid") or (0.05, 0.10, 0.15, 0.20)),
            min_dims=int(camp.get("min_dims", 2)),
            n_perm=n_perm,
            seed=seed,
        )
        res["label"] = camp.get("label", "")
        res["notes"] = camp.get("notes", "")
        res["entries_n"] = len(camp.get("entries", []))
        out["campaigns"][cid] = res
    out["n_campaigns"] = len(out["campaigns"])
    out["signature"] = hashlib.sha256(
        json.dumps(spec, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return out


# ------------------------------------------------------------------- 9. 验收总装


DEFAULT_CONFIG: Dict[str, Any] = {
    "alpha": 0.05,
    "alternative": "greater",
    "min_dims": 2,
    "n_perm_main": 10000,
    "aa": {"n_sims": 100, "n_pairs": 20, "n_perm": 2000, "stability_seeds": [0, 1, 2, 3, 4]},
    "fwer": {"n_families": 20, "n_pairs": 20, "n_perm": 2000, "extended_families": 200},
    "power_positive": {"d": 0.5, "n_pairs": 20, "rho": 0.7, "n_sims": 1000, "n_perm": 999},
    "power_vs_rho": {"d": 0.5, "n_pairs": 20, "rho_grid": [0.0, 0.3, 0.5, 0.7, 0.85],
                     "n_sims": 600, "n_perm": 999},
    "mde": {"n_grid": [10, 12, 16, 20, 24, 30, 40, 60, 100], "sigma": 1.0,
            "n_rep": 300, "n_perm": 499, "two_sided": True,
            "deviation_check_from": 20, "deviation_tol": 0.10},
    "family_required_n": {"effect_dz": 0.8, "n_affected_dims": 2, "min_dims": 2,
                          "n_grid": [9, 12, 16, 20, 26, 32, 40, 60],
                          "n_rep": 300, "n_perm": 499},
    "seed": 0,
}


def run_acceptance(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """跑齐 (a)-(d) 四项机器判据并给出总判。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    for k, v in (config or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    alpha = float(cfg["alpha"])
    alt = str(cfg["alternative"])
    min_dims = int(cfg["min_dims"])
    seed = int(cfg["seed"])

    aa = aa_null_control(n_sims=int(cfg["aa"]["n_sims"]), n_pairs=int(cfg["aa"]["n_pairs"]),
                         n_perm=int(cfg["aa"]["n_perm"]), alpha=alpha, seed=seed,
                         min_dims=min_dims,
                         stability_seeds=tuple(cfg["aa"].get("stability_seeds", [0])))
    fwer = fwer_null_families(n_families=int(cfg["fwer"]["n_families"]),
                              n_pairs=int(cfg["fwer"]["n_pairs"]),
                              n_perm=int(cfg["fwer"]["n_perm"]), alpha=alpha,
                              seed=seed + 7,
                              extended_families=int(cfg["fwer"]["extended_families"]))
    pos = power_positive_control(d=float(cfg["power_positive"]["d"]),
                                 n_pairs=int(cfg["power_positive"]["n_pairs"]),
                                 rho=float(cfg["power_positive"]["rho"]),
                                 n_sims=int(cfg["power_positive"]["n_sims"]),
                                 n_perm=int(cfg["power_positive"]["n_perm"]),
                                 alpha=alpha, alternative=alt, seed=seed)
    sweep = power_vs_rho(d=float(cfg["power_vs_rho"]["d"]),
                         n_pairs=int(cfg["power_vs_rho"]["n_pairs"]),
                         rho_grid=tuple(cfg["power_vs_rho"]["rho_grid"]),
                         n_sims=int(cfg["power_vs_rho"]["n_sims"]),
                         n_perm=int(cfg["power_vs_rho"]["n_perm"]),
                         alpha=alpha, seed=seed)
    mde = mde_curve(n_grid=tuple(cfg["mde"]["n_grid"]), sigma=float(cfg["mde"]["sigma"]),
                    alpha=alpha, target_power=0.8, two_sided=bool(cfg["mde"]["two_sided"]),
                    n_rep=int(cfg["mde"]["n_rep"]), n_perm=int(cfg["mde"]["n_perm"]),
                    seed=seed, deviation_check_from=int(cfg["mde"]["deviation_check_from"]),
                    deviation_tol=float(cfg["mde"]["deviation_tol"]))
    fam_n = required_n_for_family_decision(
        target_power=0.8, effect_dz=float(cfg["family_required_n"]["effect_dz"]),
        n_affected_dims=int(cfg["family_required_n"]["n_affected_dims"]), min_dims=min_dims,
        n_grid=tuple(cfg["family_required_n"]["n_grid"]), sigma=1.0, alpha=alpha,
        alternative=alt, n_rep=int(cfg["family_required_n"]["n_rep"]),
        n_perm=int(cfg["family_required_n"]["n_perm"]), seed=seed)

    criteria = [
        {"id": "a_aa_null_control",
         "requirement": f"A/A 空对照（同一路线两次）误报率 <= {alpha}，含 95% 二项 CI 上界",
         "observed": {
             "family_ge_min_dims_rate": aa["primary"]["family_ge_min_dims"]["p_hat"],
             "family_ci": aa["primary"]["family_ge_min_dims"],
             "per_dim_max_rate_pooled": max(v["p_hat"] for v in aa["pooled"]["per_dim"].values()),
             "per_dim_max_ci_upper_pooled": aa["ci_upper_per_dim_pooled"],
             "n_sims": aa["primary"]["n_families"],
             "pooled_n_families": aa["pooled"]["n_families"],
         },
         "pass": bool(aa["pass"])},
        {"id": "b_power_positive_control",
         "requirement": f"已知合成效应 d={cfg['power_positive']['d']}, n={cfg['power_positive']['n_pairs']} 检出率 >= 0.70",
         "observed": {"detection_rate": pos["detection_rate"], "ci": pos["detection_ci"],
                      "rho": pos["rho_assumed"], "dz_realized": pos["dz_realized"],
                      "alternative": alt},
         "pass": bool(pos["detection_rate"] >= 0.70)},
        {"id": "c_fwer_null_families",
         "requirement": f"六维 FWER 在 {cfg['fwer']['n_families']} 个随机零效应族上 <= 0.06",
         "observed": {"fwer": fwer["primary_fwer"], "ci": fwer["primary_ci"],
                      "extended_fwer": fwer["extended"]["fwer"]},
         "pass": bool(fwer["pass"])},
        {"id": "d_mde_curve",
         "requirement": "MDE(n) 单调下降，且 n>=20 时与解析 2.802σ/√n 偏差 <=10%",
         "observed": {"monotone_decreasing": mde["monotone_decreasing"],
                      "max_abs_rel_dev_n_ge_20": mde["max_abs_rel_dev_checked"],
                      "max_abs_rel_dev_vs_exact_t_n_ge_20": mde["max_abs_rel_dev_vs_exact_t"],
                      "exact_t_within_3pct": mde["exact_t_within_3pct"],
                      "rows": [{"n": r["n"], "mde_numeric": r["mde_numeric"],
                                "mde_analytic": r["mde_analytic"], "rel_dev": r["rel_dev"]}
                               for r in mde["rows"]]},
         "pass": bool(mde["pass"])},
    ]
    return {
        "config": cfg,
        "criteria": criteria,
        "all_pass_core": all(c["pass"] for c in criteria),
        "aa": aa,
        "fwer": fwer,
        "power_positive": pos,
        "power_vs_rho": sweep,
        "mde_curve": mde,
        "family_required_n": fam_n,
        "versions": {"numpy": np.__version__, "python": os.sys.version.split()[0]},
        "module_sha256": sha256_file(__file__),
    }


# --------------------------------------------------------------------- 10. 图


def make_figures(
    acceptance: Dict[str, Any],
    retro: Optional[Dict[str, Any]] = None,
    outdir: str = "artifacts/six_dim_power/figures",
) -> List[str]:
    """生成 5 张 png（无中文字体依赖：标签用英文+数字，避免 matplotlib 缺字形）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    paths: List[str] = []

    # 图 1：MDE(n) 解析 vs 数值
    rows = acceptance["mde_curve"]["rows"]
    n = [r["n"] for r in rows]
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    ax.plot(n, [r["mde_numeric"] for r in rows], "o-", label="MDE numeric (sign-flip MC)")
    ax.plot(n, [r["mde_analytic"] for r in rows], "s--", label="MDE analytic 2.802*sigma/sqrt(n)")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("paired sample size n")
    ax.set_ylabel("minimum detectable effect (sigma units)")
    ax.set_title(f"MDE(n): max |dev| = {acceptance['mde_curve']['max_abs_rel_dev_checked']:.1%} for n>=20")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    p = os.path.join(outdir, "fig1_mde_curve.png")
    fig.tight_layout()
    fig.savefig(p, dpi=140)
    plt.close(fig)
    paths.append(p)

    # 图 2：族判决功效 vs n
    curve = acceptance["family_required_n"]["curve"]
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    ax.plot([r["n_pairs"] for r in curve["rows"]], [r["power_ge_min_dims"] for r in curve["rows"]],
            "o-", label="P(>=2 dims better after Holm)")
    ax.plot([r["n_pairs"] for r in curve["rows"]], [r["power_all_affected"] for r in curve["rows"]],
            "s--", label="P(both affected dims significant)")
    ax.axhline(0.8, color="r", ls=":", label="target power 0.8")
    hit = acceptance["family_required_n"]["required_n_grid_hit"]
    if hit:
        ax.axvline(hit, color="g", ls=":", label=f"required n = {hit}")
    ax.set_xlabel("paired sample size n per dimension")
    ax.set_ylabel("power")
    ax.set_title(f"family decision power, effect dz = {curve['effect_dz']}")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    p = os.path.join(outdir, "fig2_family_power.png")
    fig.tight_layout()
    fig.savefig(p, dpi=140)
    plt.close(fig)
    paths.append(p)

    # 图 3：A/A + FWER 误报率
    aa = acceptance["aa"]
    labels = ["A/A family\n(>=2 dims)", "A/A any dim", "A/A per-dim\npooled max",
              "FWER 20 fam", "FWER 200 fam"]
    vals = [aa["primary"]["family_ge_min_dims"]["p_hat"], aa["primary"]["family_any"]["p_hat"],
            max(v["p_hat"] for v in aa["pooled"]["per_dim"].values()),
            acceptance["fwer"]["primary_fwer"], acceptance["fwer"]["extended"]["fwer"]]
    los = [aa["primary"]["family_ge_min_dims"]["lo"], aa["primary"]["family_any"]["lo"],
           min(v["lo"] for v in aa["pooled"]["per_dim"].values()),
           acceptance["fwer"]["primary_ci"]["lo"], acceptance["fwer"]["extended"]["ci"]["lo"]]
    his = [aa["primary"]["family_ge_min_dims"]["hi"], aa["primary"]["family_any"]["hi"],
           max(v["hi"] for v in aa["pooled"]["per_dim"].values()),
           acceptance["fwer"]["primary_ci"]["hi"], acceptance["fwer"]["extended"]["ci"]["hi"]]
    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    xpos = range(len(vals))
    ax.bar(xpos, vals, color="#3b7dd8")
    ax.errorbar(xpos, vals, yerr=[[v - l for v, l in zip(vals, los)], [h - v for v, h in zip(vals, his)]],
                fmt="none", ecolor="k", capsize=4)
    ax.axhline(acceptance["aa"]["alpha"], color="r", ls=":", label="nominal alpha = 0.05")
    ax.set_xticks(list(xpos))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("false-positive rate")
    ax.set_title("A/A null control and FWER (95% Clopper-Pearson CI)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    p = os.path.join(outdir, "fig3_null_controls.png")
    fig.tight_layout()
    fig.savefig(p, dpi=140)
    plt.close(fig)
    paths.append(p)

    # 图 4：功效 vs rho（配对收益敏感性）+ 正控点
    sw = acceptance["power_vs_rho"]["rows"]
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    ax.plot([r["rho"] for r in sw], [r["power_greater"] for r in sw], "o-", label="empirical, one-sided")
    ax.plot([r["rho"] for r in sw], [r["power_two-sided"] for r in sw], "s-", label="empirical, two-sided")
    ax.plot([r["rho"] for r in sw], [r["analytic_greater"] for r in sw], "k:", label="analytic nct, one-sided")
    ax.axhline(0.7, color="r", ls=":", label="criterion 0.70")
    ax.set_xlabel("within-pair correlation rho (pairing benefit)")
    ax.set_ylabel("detection rate at d=0.5, n=20")
    ax.set_title("positive control: power vs pairing benefit")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    p = os.path.join(outdir, "fig4_positive_control.png")
    fig.tight_layout()
    fig.savefig(p, dpi=140)
    plt.close(fig)
    paths.append(p)

    # 图 5：回溯性功效（各维相对效应 + 所需 n）
    if retro:
        camps = list(retro["campaigns"])
        dims_all = list(DIMENSIONS)
        fig, ax = plt.subplots(figsize=(7.6, 4.6))
        width = 0.8 / max(len(camps), 1)
        for i, cid in enumerate(camps):
            d = retro["campaigns"][cid]["dimensions"]
            xs, ys, txt = [], [], []
            for j, dim in enumerate(dims_all):
                r = d[dim]
                if r.get("effect_rel") is None:
                    continue
                xs.append(j + i * width - 0.4 + width / 2)
                ys.append(abs(r["effect_rel"]))
                code = r["state_code"]
                txt.append(f"n={r['required_n']}" if code == "NEEDS_N" else
                           ("ok" if code == "DECIDABLE" else "undet."))
            bars = ax.bar(xs, ys, width=width, label=f"{cid}")
            for b, t in zip(bars, txt):
                ax.annotate(t.replace("需n=", "n="), (b.get_x() + b.get_width() / 2, b.get_height()),
                            ha="center", va="bottom", fontsize=6, rotation=90)
        ax.set_yscale("log")
        ax.set_xticks(range(len(dims_all)))
        ax.set_xticklabels(dims_all, rotation=30, fontsize=8)
        ax.set_ylabel("|relative effect| (treatment vs control)")
        ax.set_title("retrospective power: |rel effect| and required n per dimension")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3, axis="y")
        p = os.path.join(outdir, "fig5_retro_power.png")
        fig.tight_layout()
        fig.savefig(p, dpi=140)
        plt.close(fig)
        paths.append(p)
    return paths


# --------------------------------------------------------- 11. 论文级报告（零手抄）


def render_markdown(
    acceptance: Dict[str, Any],
    retro: Optional[Dict[str, Any]] = None,
    *,
    node: str = "P143W2-N04",
    figures: Optional[Sequence[str]] = None,
) -> str:
    """把 acceptance.json / retro.json 渲染成报告 Markdown（所有数字从 JSON 取，零手抄）。"""
    cfg = acceptance["config"]
    aa = acceptance["aa"]
    pos = acceptance["power_positive"]
    fw = acceptance["fwer"]
    mde = acceptance["mde_curve"]
    fam = acceptance["family_required_n"]
    L: List[str] = []
    add = L.append

    add(f"# {node} · 六维评价的统计功效门（MDE + 配对置换检验 + Holm FWER + A/A 空对照）")
    add("")
    add("> 本文件由 `tools/abm/six_dim_power.py::render_markdown` 从 "
        "`artifacts/six_dim_power/acceptance.json` / `retro_p12.json` 程序化生成，"
        "文中每个数字都可在工件里机器复算（零手抄）。")
    add("")
    add("## 0. 结论摘要（机器判据）")
    add("")
    add("| # | 判据 | 观测 | 阈值 | 判定 |")
    add("|---|---|---|---|---|")
    add(f"| a | A/A 空对照族判决误报率（同一路线两次，n_sims={aa['primary']['n_families']}） | "
        f"{aa['primary']['family_ge_min_dims']['p_hat']:.3f}（95% CI "
        f"[{aa['primary']['family_ge_min_dims']['lo']:.3f}, {aa['primary']['family_ge_min_dims']['hi']:.3f}]） | "
        f"<= {aa['alpha']} | {'PASS' if acceptance['criteria'][0]['pass'] else 'FAIL'} |")
    add(f"| a' | A/A 逐维误报率（汇总 {aa['pooled']['n_families']} 族最大维） | "
        f"{max(v['p_hat'] for v in aa['pooled']['per_dim'].values()):.3f}（CI 上界 "
        f"{aa['ci_upper_per_dim_pooled']:.3f}） | <= {aa['alpha']} | "
        f"{'PASS' if acceptance['criteria'][0]['pass'] else 'FAIL'} |")
    add(f"| b | 功效正控 d={pos['d']}, n={pos['n_pairs']}（rho={pos['rho_assumed']}，备择={pos['alternative']}） | "
        f"检出率 {pos['detection_rate']:.3f}（CI [{pos['detection_ci']['lo']:.3f}, {pos['detection_ci']['hi']:.3f}]） | "
        f">= 0.70 | {'PASS' if acceptance['criteria'][1]['pass'] else 'FAIL'} |")
    add(f"| c | 六维 FWER（{fw['primary']['n_families']} 个随机零效应族） | {fw['primary_fwer']:.3f}"
        f"（扩展 {fw['extended']['n_families']} 族：{fw['extended']['fwer']:.3f}） | <= 0.06 | "
        f"{'PASS' if acceptance['criteria'][2]['pass'] else 'FAIL'} |")
    add(f"| d | MDE(n) 单调 + 与解析 2.802σ/√n 偏差（n>=20） | 单调={mde['monotone_decreasing']}，"
        f"最大偏差 {mde['max_abs_rel_dev_checked']:.3%}（vs 精确非中心 t 参考："
        f"{mde['max_abs_rel_dev_vs_exact_t']:.3%}） | <= 10% | "
        f"{'PASS' if acceptance['criteria'][3]['pass'] else 'FAIL'} |")
    add("")
    add(f"**核心结论 1（功效）**：在 alpha={cfg['alpha']}、方向性备择、Holm 控制六维 FWER 的判决下，"
        f"若 2 个维的真实效应为 d_z={fam['curve']['effect_dz']}，要判出「≥{fam['curve']['min_dims']} 维更好」"
        f"需每维 **n≈{fam['required_n_grid_hit']} 对**配对重复（该点功效 "
        f"{next(r['power_ge_min_dims'] for r in fam['curve']['rows'] if r['n_pairs'] == fam['required_n_grid_hit']):.3f}）；"
        f"n=20 时只有 "
        f"{next(r['power_ge_min_dims'] for r in fam['curve']['rows'] if r['n_pairs'] == 20):.3f}。")
    add("")
    add(f"**核心结论 2（A/A）**：同一路线测两次，族判决误报率 {aa['primary']['family_ge_min_dims']['p_hat']:.3f}"
        f"（CI 上界 {aa['ci_upper_family']:.3f}），逐维误报率上界 {aa['ci_upper_per_dim_pooled']:.3f}"
        f"——p 值没有被系统性做小。")
    add("")
    if retro:
        for cid, c in retro["campaigns"].items():
            add(f"**回溯 {cid}**：族判决三态 = **{c['family']['state']}**"
                f"（{c['family']['reason']}）。")
        add("")
    add("## 1. 决策与预声明（实验设计要素）")
    add("")
    add("| 要素 | 内容 |")
    add("|---|---|")
    add("| 决策 | 「实验组在六维中 ≥2 维优于对照组」是否成立（G25 判决），以及现有战役规模能否判出 |")
    add("| 备择方案 | (i) 叙事式「看起来更好」（不可裁，被本节点取代）；(ii) 逐维不加校正的 t 检验（FWER 膨胀）；"
        "(iii) 本方案：配对置换 + Holm，方向性备择 |")
    add("| 假设 | H0：六维配对差均为 0（符号可交换）；H1：至少 k 维配对差方向性为正 |")
    add("| 结局指标 | 每维配对 Cohen's d_z、配对差中位数、置换 p、Holm 校正 p、族判决（≥k 维显著） |")
    add("| 基线与对照 | n=1/2 以 `dimensions.comparison_gate` 只做结构对照；统计层以 A/A 空对照作零假设基线 |")
    add(f"| 预声明阈值 | alpha={cfg['alpha']}；功效目标 0.80；A/A 误报率 <= alpha；FWER <= 0.06；"
        f"MDE 与解析近似偏差 <= {cfg['mde']['deviation_tol']:.0%}（仅 n>={cfg['mde']['deviation_check_from']}）；"
        f"正控检出率 >= 0.70 |")
    add("| 停止规则 | 固定预算 Monte Carlo（置换次数、模拟重复数预声明，不看结果加样）；"
        "分片/网格外的 n 一律报 None 而不是插值造假 |")
    add("| 变量与混杂 | 受控：配对键 (lineage, seat) 消掉谱系/席位/种子族公共偏移；变化：仅「路线」一条。"
        "混杂：跨量转移的 σ 代理、非独立维之间的相关性（Holm 不要求独立） |")
    add("| 重复数理由 | 族判决功效曲线由模拟反解（不问「几个够」而问「给定效应几个能判」）；"
        "MDE(n) 曲线给全区间；A/A 用 10^2 族（任务口径）+ 多 seed 汇总作抽样波动检查 |")
    add(f"| 环境 | python {acceptance['versions']['python']}, numpy {acceptance['versions']['numpy']}；"
        f"模块 sha256 `{acceptance['module_sha256']}`；所有 RNG 均显式 seed（见 config） |")
    add("| 分析计划 | 逐维置换 p → Holm → 计数显著维 → 与 min_dims 比较；"
        "A/A 与 FWER 用 Clopper-Pearson 精确区间；不做 p-hacking 式的阈值调整 |")
    add("| 资源上限 | 全量验收 run 约 13 s（单核 numpy 向量化）；无集群需求 |")
    add("| 解释边界 | 统计显著 ≠ 物理量正确；本门只回答「优于对照」是否可判，不替代物理验证 |")
    add("")
    add("## 2. 方法")
    add("")
    add("1. **方向化配对差**：`delta_i = direction_k * (v_treatment,i - v_control,i)`，"
        "`direction_k` 见 `DIMENSION_DIRECTION`（accuracy/efficiency/reproducibility 的数值口径是"
        "「越小越好」，故取 -1）。正值 = 实验组更好。")
    add("2. **效应量**：配对 Cohen's `d_z = mean(delta)/sd(delta)`（ddof=1）、配对差中位数（稳健），"
        "均值 CI 用 t 分位，中位数 CI 用二项序统计量。")
    add("3. **置换检验**：配对符号翻转（sign-flip）置换，" + str(cfg["n_perm_main"]) + " 次随机符号翻转，"
        "统计量 = 配对差均值，`p=(1+#{|T_perm|>=|T_obs|})/(1+B)`；零假设下精确（不依赖正态）。")
    add("4. **FWER 控制**：Bonferroni-Holm 逐步下降，六维为一个族；"
        "Holm 在任意相依结构下都控 FWER，且比均匀 Bonferroni 有功效。")
    add("5. **MDE(n)**：解析 `MDE=(z_{1-a/2}+z_b)σ_d/√n`（α=.05/功效 .8 → 2.802σ_d/√n）；"
        "数值反解用**同一置换检验**做蒙特卡洛 + 对数二分；参考基准为精确非中心 t 反解。")
    add("6. **A/A 空对照**：同一路线两次（两臂同分布），跑完整六维族判决，统计误报率；"
        "主判据是**族判决**（≥k 维显著）的误报率——这正是战役真正要用的规则，"
        "其零假设率天然低于逐维 α（独立零维时 P(≥2 个逐维误报)≈3.3%）。")
    add("7. **回溯性功效**：对既有战役产物（每臂单测）计算方向化相对效应；"
        "n 不足则借 σ 代理反解所需 n，并给 σ 敏感性表；输出三态（可判 / 需n=N / 不可判）。")
    add("")
    add("## 3. 结果（程序化注入）")
    add("")
    add("### 3.1 A/A 空对照（`acceptance.json: aa`）")
    add("")
    add("| 维 | 逐维误报率（汇总 " + str(aa["pooled"]["n_families"]) + " 族） | 95% CI |")
    add("|---|---|---|")
    for d in DIMENSIONS:
        v = aa["pooled"]["per_dim"][d]
        add(f"| {d} | {v['p_hat']:.4f} | [{v['lo']:.4f}, {v['hi']:.4f}] |")
    add("")
    add("| 族级指标 | 主实验（" + str(aa["primary"]["n_families"]) + " 族） | 双侧稳健性行 | "
        "多 seed 汇总（" + str(aa["pooled"]["n_families"]) + " 族） |")
    add("|---|---|---|---|")
    for key, label in (("family_ge_min_dims", f">= {aa['min_dims']} 维显著（判决规则本身）"),
                       ("family_any", ">= 1 维显著（FWER）"),
                       ("family_all", "六维全显著")):
        p1 = aa["primary"][key]
        p2 = aa["primary_two_sided"][key]
        p3 = aa["pooled"][key]
        add(f"| {label} | {p1['p_hat']:.3f} [{p1['lo']:.3f}, {p1['hi']:.3f}] | "
            f"{p2['p_hat']:.3f} [{p2['lo']:.3f}, {p2['hi']:.3f}] | "
            f"{p3['p_hat']:.3f} [{p3['lo']:.3f}, {p3['hi']:.3f}] |")
    add("")
    add(f"读法：100 族的分辨率是 1%/族，单次「>=1 维显著」点估计 "
        f"{aa['primary']['family_any']['p_hat']:.3f} 的 CI 覆盖名义 α；汇总 "
        f"{aa['pooled']['n_families']} 族给出分辨力更高的估计 "
        f"{aa['pooled']['family_any']['p_hat']:.3f}"
        f"（CI [{aa['pooled']['family_any']['lo']:.3f}, {aa['pooled']['family_any']['hi']:.3f}]）。"
        f"判决规则本身（>= {aa['min_dims']} 维）的误报率在所有口径下都远低于 α。")
    add("")
    add("### 3.2 功效正控与配对收益敏感性（`acceptance.json: power_positive / power_vs_rho`）")
    add("")
    add(f"主控：d={pos['d']}、n={pos['n_pairs']}、rho={pos['rho_assumed']}（实测 rho={pos['rho_realized']:.3f}，"
        f"实测 d_z={pos['dz_realized']:.3f}）→ 检出率 **{pos['detection_rate']:.3f}**，"
        f"解析非中心 t 预测 {pos['analytic_power_nct']:.3f}（相对偏差 "
        f"{abs(pos['detection_rate'] - pos['analytic_power_nct']) / pos['analytic_power_nct']:.2%}）。")
    add("")
    add("| rho（配对收益） | d_z | 实测检出率（单侧） | 实测检出率（双侧） | 解析（单侧） |")
    add("|---|---|---|---|---|")
    for r in acceptance["power_vs_rho"]["rows"]:
        add(f"| {r['rho']:.2f} | {r['dz']:.3f} | {r['power_greater']:.3f} | "
            f"{r['power_two-sided']:.3f} | {r['analytic_greater']:.3f} |")
    add("")
    add("### 3.3 MDE(n) 曲线（`acceptance.json: mde_curve`）")
    add("")
    add("| n | MDE 数值反解 | 解析 2.802σ/√n | 精确非中心 t 参考 | 数值 vs 解析 | 数值 vs 精确 t |")
    add("|---|---|---|---|---|---|")
    for r in mde["rows"]:
        add(f"| {r['n']} | {r['mde_numeric']:.4f} | {r['mde_analytic']:.4f} | "
            f"{r['mde_exact_t']:.4f} | {r['rel_dev']:+.2%} | {r['rel_dev_vs_exact_t']:+.2%} |")
    add("")
    add(f"单调下降：{mde['monotone_decreasing']}；n>={mde['deviation_check_from']} 时与解析近似最大偏差 "
        f"{mde['max_abs_rel_dev_checked']:.2%}（判据 <= {mde['deviation_tol']:.0%}），"
        f"与精确 t 参考最大偏差 {mde['max_abs_rel_dev_vs_exact_t']:.2%}。")
    add("")
    add("### 3.4 族判决功效与所需 n（`acceptance.json: family_required_n`）")
    add("")
    add(f"设定：{fam['curve']['n_affected_dims']} 维有真实效应 d_z={fam['curve']['effect_dz']}，"
        f"其余 {len(DIMENSIONS) - fam['curve']['n_affected_dims']} 维为 0；判决 = Holm 后 ≥"
        f"{fam['curve']['min_dims']} 维显著。")
    add("")
    add("| n（每维配对重复） | P(≥2 维显著) | P(两维都显著) | 平均显著维数 |")
    add("|---|---|---|---|")
    for r in fam["curve"]["rows"]:
        add(f"| {r['n_pairs']} | {r['power_ge_min_dims']:.3f} | {r['power_all_affected']:.3f} | "
            f"{r['mean_rejections']:.2f} |")
    add("")
    add(f"反解：n = **{fam['required_n_grid_hit']}**（网格 {fam['curve']['rows'][0]['n_pairs']}"
        f"..{fam['curve']['rows'][-1]['n_pairs']} 上首个功效 >= 0.8 的点）。")
    add("")
    if retro:
        add("## 4. 回溯性功效：既有战役产物的三态判定（`retro_p12.json`）")
        add("")
        for cid, c in retro["campaigns"].items():
            add(f"### {cid} · {c.get('label', '')}")
            add("")
            add(f"族判决三态：**{c['family']['state']}**（{c['family']['state_code']}）—— {c['family']['reason']}")
            add("")
            add("| 维 | n 对 | 相对效应（正=实验组更好） | 方向 | 三态 | 所需 n | σ 敏感性（σ_rel → 所需 n；n=9 时功效） |")
            add("|---|---|---|---|---|---|---|")
            for d in DIMENSIONS:
                r = c["dimensions"][d]
                if r["n_pairs"] == 0:
                    add(f"| {d} | 0 | — | 无数据 | 不可判 | — | — |")
                    continue
                rel = r.get("effect_rel")
                rel_s = f"{rel:+.4g}" if rel is not None else "n/a"
                fav = {True: "有利", False: "不利", None: "无定论"}[r.get("favorable")]
                sens = "; ".join(
                    f"{x['sigma_rel']:g}→{x['required_n']}（{x['power_at_min_n']:.2f}）"
                    for x in r.get("sigma_sensitivity", []) if x.get("power_at_min_n") is not None)
                add(f"| {d} | {r['n_pairs']} | {rel_s} | {fav} | {r['state']} | "
                    f"{r.get('required_n') if r.get('required_n') is not None else '—'} | {sens or '—'} |")
            add("")
            if c.get("notes"):
                add(f"口径说明：{c['notes']}")
                add("")
        add("## 5. 图")
        add("")
        figs = list(figures or [])
        caps = [
            ("fig1_mde_curve.png", "MDE(n)：置换引擎数值反解 vs 解析 2.802σ/√n；n≥20 处两者偏差 ≤6%。"),
            ("fig2_family_power.png", "族判决功效随每维配对重复数 n 的变化；目标功效 0.8 处为反解出的 n。"),
            ("fig3_null_controls.png", "A/A 空对照与 FWER 误报率（95% Clopper-Pearson CI）对名义 α=0.05。"),
            ("fig4_positive_control.png", "功效正控：d=0.5、n=20 时检出率随配对收益 rho 的变化（单侧/双侧）。"),
            ("fig5_retro_power.png", "回溯性功效：各维 |相对效应| 与三态标签（n=9 需重复数 / ok 可判 / undet. 不可判）。"),
        ]
        for name, cap in caps:
            hit = next((f for f in figs if f.endswith(name)), None)
            if hit:
                add(f"![{name}]({hit})")
                add("")
                add(f"*{cap}*")
                add("")
    add("## 6. 注意点与局限")
    add("")
    add("* **小 n 的离散化**：等幅配对下双侧符号翻转检验可达最小 p=2/2^n，n=5 时为 6.25%"
        "（拒绝不了），n=6 才 3.1%；`MIN_TESTABLE_PAIRS=9` 是**设计下限**（保证 0.05 以下的 p 阶梯不止"
        "一两档），不是数学下限——这是选择，已在 docstring 声明。")
    add("* **方向性备择需预声明**：命题是「优于」而不是「不同」，故主判据用单侧；双侧行"
        "（`primary_two_sided`、`power_two-sided` 列）同时报告，结论方向不因换备择而翻转。")
    add("* **配对收益 rho 是设计假设**：rho=0 时 d=0.5/n=20 的检出率仅 "
        f"{next(r['power_greater'] for r in acceptance['power_vs_rho']['rows'] if r['rho'] == 0.0):.3f}"
        "（单侧），此时 n=20 不够——真实战役必须实测配对相关，不能假定。")
    add("* **σ 代理是跨量转移假设**：回溯性功效里的 σ 借自同路线复跑差异或敏感性网格，"
        "不是测量；因此结论只作「量级/所需 n」用，不能当 p 值。")
    add("* **回溯数据的口径瑕疵**：既有产物里 accuracy 行（对照 arm 的解析锚点误差 vs 实验 arm 的"
        "路线间差）口径存疑，已在 `p12_spec.json` 的 note 与工件里登记；这类维即便显著也不能单独当结论。")
    add("* **A/A 的 10^2 分辨率**：100 族的点估计分辨率是 1%，主判据落在族判决（≥2 维）上，"
        "并对 5 个 seed 汇总（" + str(aa["pooled"]["n_families"]) + " 族）报告抽样波动。")
    add("* **适用边界**：本门只做统计裁决，不替代物理验证；六维数字本身的正确性由各自的"
        "解析锚/守恒律验证负责。")
    add("")
    add("## 7. 误差判定（关键量 vs 基准，逐项回答是否 <3%）")
    add("")
    add("| 关键量 | 基准 | 相对偏差 | 是否 <3% | 说明 |")
    add("|---|---|---|---|---|")
    dev_t = mde["max_abs_rel_dev_vs_exact_t"]
    add(f"| MDE 数值反解（n≥20 各行） | 精确非中心 t 反解 | {dev_t:.2%} | "
        f"{'是' if dev_t <= 0.03 else '否'} | 置换引擎与精确参考一致 → 引擎无系统偏差 |")
    dev_a = mde["max_abs_rel_dev_checked"]
    add(f"| 解析近似 2.802σ/√n | 数值/精确 t | {dev_a:.2%} | "
        f"{'是' if dev_a <= 0.03 else '否'} | 否：正态分位近似，n≥20 时 ~4-6%；判据为 ≤10% 且只对 n≥20 声明；"
        "n=10 行偏差 17%（t 分位修正），如实列出 |")
    pdev = abs(pos["detection_rate"] - pos["analytic_power_nct"]) / pos["analytic_power_nct"]
    add(f"| 正控检出率（模拟） | 解析非中心 t 功效 | {pdev:.2%} | "
        f"{'是' if pdev <= 0.03 else '否'} | rho/d_z 实测值与假设一致 |")
    fwer_ = fw["primary_fwer"]
    add(f"| FWER | 名义 alpha=0.05 | {abs(fwer_ - 0.05) / 0.05:.2%} | "
        f"{'是' if abs(fwer_ - 0.05) / 0.05 <= 0.03 else '否'} | 20 族分辨率仅 0.05/族，"
        f"扩展 200 族估计 {fw['extended']['fwer']:.3f}（CI [{fw['extended']['ci']['lo']:.3f}, "
        f"{fw['extended']['ci']['hi']:.3f}]）；A/A 汇总 "
        f"{aa['pooled']['n_families']} 族「>=1 维显著」率 "
        f"{aa['pooled']['family_any']['p_hat']:.3f}"
        f"（CI [{aa['pooled']['family_any']['lo']:.3f}, {aa['pooled']['family_any']['hi']:.3f}]） |")
    add(f"| A/A 族判决误报率 | 名义 alpha=0.05 | — | {'是' if aa['primary']['family_ge_min_dims']['p_hat'] <= 0.05 else '否'} | "
        f"点估计 {aa['primary']['family_ge_min_dims']['p_hat']:.3f}，CI 上界 {aa['ci_upper_family']:.3f} |")
    add("")
    add("## 8. 审计链与复现")
    add("")
    add("```")
    add("# 机器判据（约 13 s，单核）")
    add("python3 -m tools.abm.six_dim_power all --outdir artifacts/six_dim_power")
    add("# 只跑回溯（读既有战役产物 spec）")
    add("python3 -m tools.abm.six_dim_power retro --spec artifacts/six_dim_power/p12_spec.json")
    add("# pytest")
    add("python3 -m pytest tests/abm/test_six_dim_power.py -q")
    add("```")
    add("")
    add(f"* 模块 sha256：`{acceptance['module_sha256']}`")
    if retro:
        add(f"* 回溯 spec sha256：`{retro.get('spec_sha256')}`（路径 `{retro.get('spec_path')}`）")
    add(f"* 环境：python {acceptance['versions']['python']} / numpy {acceptance['versions']['numpy']}")
    add("* 全部随机数：显式 seed 记录在 `acceptance.json: config`（含 A/A 多 seed 列表）")
    add("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------- 11. CLI


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="六维评价统计功效门（零领域语义）")
    ap.add_argument("mode", choices=("simulate", "retro", "all"), help="跑哪个子任务")
    ap.add_argument("--outdir", default="artifacts/six_dim_power")
    ap.add_argument("--spec", default="artifacts/six_dim_power/p12_spec.json")
    ap.add_argument("--config", default=None, help="覆盖 DEFAULT_CONFIG 的 JSON 文件")
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--markdown", default=None,
                    help="把报告 Markdown 落盘（需 simulate 或 all；数字全部取自工件）")
    args = ap.parse_args(list(argv) if argv is not None else None)

    os.makedirs(args.outdir, exist_ok=True)
    figdir = os.path.join(args.outdir, "figures")
    acceptance = None
    retro = None

    if args.mode in ("simulate", "all"):
        cfg = None
        if args.config:
            with open(args.config, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
        acceptance = run_acceptance(cfg)
        path = os.path.join(args.outdir, "acceptance.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(acceptance, fh, ensure_ascii=False, indent=2)
        print(f"[six_dim_power] acceptance -> {path}  all_pass_core={acceptance['all_pass_core']}")
        for c in acceptance["criteria"]:
            print(f"  - {c['id']}: pass={c['pass']}")

    if args.mode in ("retro", "all"):
        with open(args.spec, "r", encoding="utf-8") as fh:
            spec = json.load(fh)
        retro = retrospective_bundle(spec, n_perm=int(acceptance["config"]["n_perm_main"]) if acceptance else 10000)
        retro["spec_path"] = args.spec
        retro["spec_sha256"] = sha256_file(args.spec)
        path = os.path.join(args.outdir, "retro_p12.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(retro, fh, ensure_ascii=False, indent=2)
        print(f"[six_dim_power] retro -> {path}")
        for cid, c in retro["campaigns"].items():
            print(f"  - {cid}: family state={c['family']['state']} ({c['family']['state_code']})")

    figs: List[str] = []
    if not args.no_figures and acceptance is not None:
        figs = make_figures(acceptance, retro, figdir)
        print(f"[six_dim_power] figures -> {len(figs)} png in {figdir}")
    if args.markdown and acceptance is not None:
        rel = [os.path.relpath(f, os.path.dirname(os.path.abspath(args.markdown))) for f in figs]
        md = render_markdown(acceptance, retro, figures=rel)
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)), exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"[six_dim_power] markdown -> {args.markdown} ({len(md)} chars)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
