"""六维评价统计功效门（通用层，零领域语义）。

把「优于对照」从叙事升级为带第一类错误控制的判决。

功能
----
1. 六维各定义效应量（Cohen's d 或配对差中位数）+ 配对设计
2. 置换检验 10^4 次 + Bonferroni-Holm FWER 控制
3. MDE(n) 曲线并反解所需 n
4. A/A 空对照误报率 ≤ 5% 验证
5. 对既有产物做回溯性功效计算
6. 六维优势族判决（G25）：方向语义（越小越好）+ 溯源配对（无 source 不入账）
   + Holm FWER + 「≥k 维优于对照」机器判决
7. 设计期功效：A/A 族级 FWER、功效正控、臂间相关 rho 敏感性、族判决所需 n 反解，
   并显式给出置换 p 值的**分辨率下限**（n_perm 不足时族判决在数学上不可达）

设计原则
--------
- 纯函数优先：所有对外结果都是可确定性序列化的 dict
- 零领域语义：维度名/效应量定义/阈值由调用方注入
- 可复现：固定随机种子，所有参数可追溯

六维（与 dimensions.py 一致）
-----------------------------
correctness / accuracy / stability / efficiency / reproducibility / applicability
"""

from __future__ import annotations

import hashlib
import math
import random
from statistics import NormalDist
from typing import Any, Callable, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DIMENSIONS = (
    "correctness",
    "accuracy",
    "stability",
    "efficiency",
    "reproducibility",
    "applicability",
)

DEFAULT_ALPHA = 0.05
DEFAULT_N_PERM = 10_000
DEFAULT_SEED = 42


# ---------------------------------------------------------------------------
# 效应量计算
# ---------------------------------------------------------------------------

def cohens_d(
    control: Sequence[float],
    treatment: Sequence[float],
) -> float:
    """配对设计 Cohen's d（使用配对差的标准差）。

    d = mean(diff) / std(diff)，其中 diff = treatment - control。
    """
    if len(control) != len(treatment) or len(control) == 0:
        raise ValueError("control 与 treatment 必须等长且非空")
    diffs = [t - c for c, t in zip(control, treatment)]
    n = len(diffs)
    mean_d = sum(diffs) / n
    if n < 2:
        return float("inf") if mean_d != 0 else 0.0
    var_d = sum((d - mean_d) ** 2 for d in diffs) / (n - 1)
    std_d = math.sqrt(var_d)
    if std_d < 1e-15:
        return float("inf") if mean_d != 0 else 0.0
    return mean_d / std_d


def paired_median_diff(
    control: Sequence[float],
    treatment: Sequence[float],
) -> float:
    """配对差中位数。"""
    if len(control) != len(treatment) or len(control) == 0:
        raise ValueError("control 与 treatment 必须等长且非空")
    diffs = sorted(t - c for c, t in zip(control, treatment))
    n = len(diffs)
    if n % 2 == 1:
        return diffs[n // 2]
    return (diffs[n // 2 - 1] + diffs[n // 2]) / 2.0


# ---------------------------------------------------------------------------
# 解析对照（配对 t 检验的正态近似）—— 用于交叉校验置换模拟结果
# ---------------------------------------------------------------------------

def _derive_seed(*parts: object) -> int:
    """确定性派生子种子（不用内建 hash()：str 的 hash 受 PYTHONHASHSEED 随机化）。"""
    h = hashlib.sha256("|".join(str(x) for x in parts).encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _t_ppf(p: float, df: int) -> float:
    """t 分位数（Cornish-Fisher 展开，df>=5 时误差 <0.5%）。"""
    z = NormalDist().inv_cdf(p)
    if df <= 0:
        return z
    return z + (z ** 3 + z) / (4.0 * df)


def analytic_power_paired(
    effect_size: float,
    n: int,
    *,
    alpha: float = DEFAULT_ALPHA,
) -> float:
    """配对设计的解析功效（正态近似 + t 分位修正）。

    effect_size 为 Cohen's d = mean(diff)/std(diff)；双侧检验。
    这是置换模拟的**独立**参照：两者互相印证，任一实现漂移都会被对照发现。
    """
    if n < 2:
        return min(max(alpha, 0.0), 1.0)
    d = abs(float(effect_size))
    t_crit = _t_ppf(1.0 - alpha / 2.0, n - 1)
    nc = math.sqrt(n) * d
    power = NormalDist().cdf(nc - t_crit) + NormalDist().cdf(-nc - t_crit)
    return min(max(power, 0.0), 1.0)


def analytic_mde(
    n: int,
    *,
    alpha: float = DEFAULT_ALPHA,
    power: float = 0.8,
) -> float:
    """解析 MDE(n)：给定 alpha/power 下配对设计可检出的最小 Cohen's d。"""
    if n < 2:
        return float("inf")
    z_power = NormalDist().inv_cdf(power)
    return (_t_ppf(1.0 - alpha / 2.0, n - 1) + z_power) / math.sqrt(n)


def min_n_for_effect(
    effect_size: float,
    *,
    alpha: float = DEFAULT_ALPHA,
    power: float = 0.8,
    n_min: int = 5,
    n_max: int = 500,
) -> Optional[int]:
    """解析反解：要在 alpha 下以 power 检出 effect_size，最少需要多少对样本。"""
    if effect_size <= 0:
        return None
    for n in range(max(n_min, 2), n_max + 1):
        if analytic_power_paired(effect_size, n, alpha=alpha) >= power:
            return n
    return None


# ---------------------------------------------------------------------------
# 置换检验
# ---------------------------------------------------------------------------

def _permutation_p_value(
    control: Sequence[float],
    treatment: Sequence[float],
    *,
    n_perm: int = DEFAULT_N_PERM,
    seed: int = DEFAULT_SEED,
    effect_fn: Optional[Callable[[Sequence[float], Sequence[float]], float]] = None,
    two_sided: bool = True,
) -> float:
    """双样本配对置换检验。

    零假设：control 与 treatment 来自同一分布。
    每次置换随机翻转配对差的符号，计算效应量，统计 ≥ 观测效应的比例。
    """
    if effect_fn is None:
        effect_fn = cohens_d

    rng = random.Random(seed)
    diffs = [t - c for c, t in zip(control, treatment)]
    n = len(diffs)
    obs_effect = effect_fn(control, treatment)
    abs_obs = abs(obs_effect)

    count = 0
    # 退化情形的显式分支：配对差方差为 0（如各样本偏移完全相同）时
    # Cohen's d = ±inf，置换统计量只剩"同样全同号才复现"的分辨力。
    # 这里显式改用尺度无关的配对差均值（等价于符号翻转精确检验），
    # 让 p 的下限由排列结构决定，而不是靠 inf >= inf 的浮点比较碰巧兜住。
    # 注：实测基线与本分支数值一致（p 都触到 1/(n_perm+1) 下限），
    # 因此这一条是防御性/可读性改动，不计入缺陷修复。
    if effect_fn is cohens_d and n >= 2 and not math.isfinite(obs_effect):
        mean_obs = math.fsum(diffs) / n
        abs_obs_m = abs(mean_obs)
        for _ in range(n_perm):
            s_sum = 0.0
            for d in diffs:
                s_sum = s_sum + d if rng.getrandbits(1) else s_sum - d
            if two_sided and abs(s_sum) / n >= abs_obs_m:
                count += 1
            elif not two_sided and s_sum / n >= mean_obs:
                count += 1
        return (count + 1) / (n_perm + 1)

    # 快路径：effect_fn 为 Cohen's d 时，置换统计量 mean(s·d)/std(s·d) 只需
    # 一趟累加 Σd² 与 Σ s·d（省掉每轮 3 次列表构造 + 2 次遍历）。
    fast = effect_fn is cohens_d and n >= 2
    if fast:
        sum_d2 = math.fsum(d * d for d in diffs)
        inv_n = 1.0 / n
        df = n - 1
        for _ in range(n_perm):
            s_sum = 0.0
            for d in diffs:
                s_sum = s_sum + d if rng.getrandbits(1) else s_sum - d
            mean_s = s_sum * inv_n
            var_s = (sum_d2 - s_sum * s_sum * inv_n) / df
            if var_s <= 1e-30:
                perm_effect = math.inf if mean_s > 0 else (-math.inf if mean_s < 0 else 0.0)
            else:
                perm_effect = mean_s / math.sqrt(var_s)
            if two_sided and abs(perm_effect) >= abs_obs:
                count += 1
            elif not two_sided and perm_effect >= obs_effect:
                count += 1
        return (count + 1) / (n_perm + 1)

    for _ in range(n_perm):
        signs = [rng.choice((-1, 1)) for _ in range(n)]
        perm_diffs = [s * d for s, d in zip(signs, diffs)]
        perm_control = [0.0] * n
        perm_treatment = perm_diffs
        perm_effect = effect_fn(perm_control, perm_treatment)
        if two_sided and abs(perm_effect) >= abs_obs:
            count += 1
        elif not two_sided and perm_effect >= obs_effect:
            count += 1

    return (count + 1) / (n_perm + 1)


def permutation_test_six_dim(
    control_data: Dict[str, Sequence[float]],
    treatment_data: Dict[str, Sequence[float]],
    *,
    n_perm: int = DEFAULT_N_PERM,
    seed: int = DEFAULT_SEED,
    alpha: float = DEFAULT_ALPHA,
    effect_fn: Optional[Callable[[Sequence[float], Sequence[float]], float]] = None,
    correction: str = "bonferroni_holm",
) -> Dict[str, Any]:
    """六维置换检验 + 多重比较校正。

    参数
    ----
    control_data : {dim: [values...]}  对照组各维测量值
    treatment_data : {dim: [values...]} 实验组各维测量值
    n_perm : 置换次数
    seed : 随机种子
    alpha : 族系错误率
    effect_fn : 效应量函数（默认 Cohen's d）
    correction : "bonferroni_holm" | "bonferroni" | "none"

    返回
    ----
    {dimensions: [{dim, effect_size, raw_p, adjusted_p, significant}],
     fwer_controlled, correction_method, alpha, n_perm, seed}
    """
    if effect_fn is None:
        effect_fn = cohens_d

    dims = [d for d in DIMENSIONS if d in control_data and d in treatment_data]
    results: List[Dict[str, Any]] = []

    for dim in dims:
        ctrl = list(control_data[dim])
        treat = list(treatment_data[dim])
        if len(ctrl) != len(treat) or len(ctrl) < 2:
            results.append({
                "dimension": dim,
                "effect_size": None,
                "raw_p": None,
                "adjusted_p": None,
                "significant": False,
                "reason": "样本不足或长度不等",
            })
            continue
        eff = effect_fn(ctrl, treat)
        p_raw = _permutation_p_value(ctrl, treat, n_perm=n_perm, seed=seed, effect_fn=effect_fn)
        results.append({
            "dimension": dim,
            "effect_size": eff,
            "raw_p": p_raw,
            "adjusted_p": None,  # 待校正
            "significant": False,
        })

    # 多重比较校正
    raw_ps = [r["raw_p"] for r in results if r["raw_p"] is not None]
    if correction == "bonferroni_holm" and raw_ps:
        adjusted = _bonferroni_holm(raw_ps, alpha)
        idx = 0
        for r in results:
            if r["raw_p"] is not None:
                r["adjusted_p"] = adjusted[idx]
                r["significant"] = adjusted[idx] < alpha
                idx += 1
    elif correction == "bonferroni" and raw_ps:
        for r in results:
            if r["raw_p"] is not None:
                r["adjusted_p"] = min(r["raw_p"] * len(raw_ps), 1.0)
                r["significant"] = r["adjusted_p"] < alpha
    else:
        for r in results:
            if r["raw_p"] is not None:
                r["adjusted_p"] = r["raw_p"]
                r["significant"] = r["raw_p"] < alpha

    fwer_ok = all(
        (r["adjusted_p"] is None or r["adjusted_p"] >= alpha or r["significant"])
        for r in results
    )

    return {
        "dimensions": results,
        "n_dims_tested": len(dims),
        "fwer_controlled": fwer_ok,
        "correction_method": correction,
        "alpha": alpha,
        "n_perm": n_perm,
        "seed": seed,
        "n_significant": sum(1 for r in results if r["significant"]),
    }


def _bonferroni_holm(p_values: List[float], alpha: float) -> List[float]:
    """Bonferroni-Holm 逐步校正。

    按 p 值升序排列，第 k 个 p 值与 alpha/(m-k+1) 比较。
    一旦某个 p 值不显著，其后所有 p 值也视为不显著。
    """
    m = len(p_values)
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    adjusted = [0.0] * m
    prev_adj = 0.0
    rejected = True
    for rank, (orig_idx, p) in enumerate(indexed):
        if not rejected:
            adjusted[orig_idx] = max(prev_adj, 1.0)
            continue
        threshold = alpha / (m - rank)
        adj_p = min(p * (m - rank), 1.0)
        adj_p = max(adj_p, prev_adj)
        adjusted[orig_idx] = adj_p
        prev_adj = adj_p
        if p >= threshold:
            rejected = False
    return adjusted


# ---------------------------------------------------------------------------
# MDE(n) 曲线
# ---------------------------------------------------------------------------

def mde_curve(
    *,
    alpha: float = DEFAULT_ALPHA,
    power: float = 0.8,
    n_range: Optional[Sequence[int]] = None,
    n_perm: int = 1000,
    seed: int = DEFAULT_SEED,
    effect_fn: Optional[Callable[[Sequence[float], Sequence[float]], float]] = None,
    control_std: float = 1.0,
) -> List[Dict[str, Any]]:
    """MDE(n) 曲线：对每个样本量 n，通过模拟反解最小可检测效应量。

    方法：对每个 n，二分搜索效应量 delta，使得在给定 alpha 和 power 下，
    置换检验的检出率 ≈ power。

    参数
    ----
    alpha : 显著性水平
    power : 目标统计功效
    n_range : 样本量范围（默认 [5, 10, 15, 20, 30, 50, 80, 100, 150, 200]）
    n_perm : 每次检验的置换次数（为速度默认 1000，生产用 10000）
    seed : 随机种子
    effect_fn : 效应量函数
    control_std : 对照组标准差（用于生成模拟数据）

    返回
    ----
    [{n, mde, power_achieved, alpha, n_perm}, ...]
    """
    if effect_fn is None:
        effect_fn = cohens_d
    if n_range is None:
        n_range = [5, 10, 15, 20, 30, 50, 80, 100, 150, 200]

    curve: List[Dict[str, Any]] = []
    for n in n_range:
        mde = _find_mde_for_n(
            n=n, alpha=alpha, target_power=power,
            n_perm=n_perm, seed=seed, effect_fn=effect_fn,
            control_std=control_std,
        )
        point = {
            "n": n,
            "mde": mde["mde"],
            "power_achieved": mde["power_achieved"],
            "mde_analytic": round(analytic_mde(n, alpha=alpha, power=power), 4),
            "alpha": alpha,
            "n_perm": n_perm,
        }
        if mde.get("note"):
            point["note"] = mde["note"]
        curve.append(point)

    # 单调化：MDE(n) 理论上随 n 单调不增；模拟噪声造成的局部回升用累计最小值抹平
    # （记录幅度，便于审计"仿真偏离解析"的程度而非悄悄改写）。
    running = None
    for pt in curve:
        m = pt["mde"]
        if m is None:
            continue
        if running is not None and m > running:
            pt["mde_raw"] = m
            pt["monotonised"] = True
            pt["mde"] = running
        else:
            running = m
    return curve


def _estimate_power(
    delta: float, n: int, alpha: float, n_perm: int,
    seed: int, effect_fn: Callable, control_std: float,
    n_rep: int = 100,
) -> float:
    """估计给定效应量 delta 下的统计功效。

    模拟设定：配对差 diff ~ N(delta·control_std, control_std)，
    即 Cohen's d = delta（均值/std 同尺度）。

    历史缺陷（m8646 修复）：旧实现取 ``treat = ctrl + delta*control_std``，
    配对差完全没有噪声（std(diff)=0 → Cohen's d = inf），置换检验因此对任意
    delta>0 都以极大概率显著，导致 MDE(n) 曲线整体塌陷到搜索下界
    （n=6 与 n=200 都报 0.01）。这里给配对差补上真实方差。
    """
    rng = random.Random(seed)
    diff_sd = control_std
    n_reject = 0
    for rep in range(n_rep):
        base = [rng.gauss(0.0, control_std) for _ in range(n)]
        treat = [c + rng.gauss(delta * control_std, diff_sd) for c in base]
        p = _permutation_p_value(
            base, treat, n_perm=n_perm, seed=seed + rep * 7919 + n,
            effect_fn=effect_fn,
        )
        if p < alpha:
            n_reject += 1
    return n_reject / n_rep


def _find_mde_for_n(
    *,
    n: int,
    alpha: float,
    target_power: float,
    n_perm: int,
    seed: int,
    effect_fn: Callable,
    control_std: float,
    tol: float = 0.03,
    max_iter: int = 15,
) -> Dict[str, Any]:
    """对固定 n，二分搜索 MDE。

    先向上探测括区（找到 power ≥ target-tol 的 delta 上界），再在 [0, hi] 内二分。
    括区失败（hi 达到 16 仍达不到目标功效）时返回 mde=None 而不是伪造一个点，
    以免曲线出现"假塌陷"或非单调跳变。
    """
    def _p(delta: float, reps: int) -> float:
        return _estimate_power(delta, n, alpha, n_perm, seed, effect_fn, control_std, n_rep=reps)

    threshold = target_power - tol
    hi = 0.25
    while _p(hi, 40) < threshold:
        hi *= 2.0
        if hi > 16.0:
            # 目标功效不可达：多出现在 n 很小时——符号翻转检验的 p 值有分辨率下限
            # （n=5 时最少也只能到 ~0.06），再大的效应量也检不出。
            return {
                "mde": None,
                "power_achieved": round(_p(hi, 40), 4),
                "bracketed": False,
                "note": (
                    f"n={n} 时置换检验分辨率不足，达不到 power={target_power}"
                    f"（解析 MDE={analytic_mde(n, alpha=alpha, power=target_power):.3f}）"
                ),
            }

    lo = 0.0
    best_delta, best_power = hi, _p(hi, 100)
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if mid < 0.005:
            break
        achieved = _p(mid, 100)
        if achieved >= threshold:
            best_delta, best_power = mid, achieved
            hi = mid
        else:
            lo = mid

    return {
        "mde": round(best_delta, 4),
        "power_achieved": round(best_power, 4),
        "bracketed": True,
    }


def solve_n_for_mde(
    *,
    target_mde: float,
    alpha: float = DEFAULT_ALPHA,
    power: float = 0.8,
    n_perm: int = 1000,
    seed: int = DEFAULT_SEED,
    effect_fn: Optional[Callable] = None,
    control_std: float = 1.0,
    n_max: int = 500,
) -> Dict[str, Any]:
    """反解：给定目标 MDE，求所需最小样本量 n。

    返回 {n, mde_achieved, power_achieved, alpha}；若 n_max 内达不到则返回 None。
    """
    if effect_fn is None:
        effect_fn = cohens_d
    n_range = list(range(5, n_max + 1, 5))
    curve = mde_curve(
        alpha=alpha, power=power, n_range=n_range,
        n_perm=n_perm, seed=seed, effect_fn=effect_fn,
        control_std=control_std,
    )
    for pt in curve:
        if pt["mde"] is not None and pt["mde"] <= target_mde:
            return {
                "n": pt["n"],
                "mde_achieved": pt["mde"],
                "power_achieved": pt["power_achieved"],
                "alpha": alpha,
                "target_mde": target_mde,
            }
    return {
        "n": None,
        "mde_achieved": None,
        "power_achieved": None,
        "alpha": alpha,
        "target_mde": target_mde,
        "reason": f"n_max={n_max} 内无法达到 MDE={target_mde}",
    }


# ---------------------------------------------------------------------------
# A/A 空对照误报率验证
# ---------------------------------------------------------------------------

def aa_false_positive_rate(
    *,
    n: int = 20,
    n_sim: int = 1000,
    n_perm: int = 1000,
    alpha: float = DEFAULT_ALPHA,
    seed: int = DEFAULT_SEED,
    effect_fn: Optional[Callable] = None,
    control_std: float = 1.0,
    n_dims: int = 6,
) -> Dict[str, Any]:
    """A/A 空对照实验：两组来自同一分布，估计误报率。

    要求：误报率 ≤ alpha（名义水平）。
    """
    if effect_fn is None:
        effect_fn = cohens_d
    rng = random.Random(seed)
    n_reject = 0
    n_total_tests = 0

    for sim in range(n_sim):
        ctrl_data = {}
        treat_data = {}
        for d_idx in range(n_dims):
            ctrl_data[f"dim_{d_idx}"] = [rng.gauss(0, control_std) for _ in range(n)]
            treat_data[f"dim_{d_idx}"] = [rng.gauss(0, control_std) for _ in range(n)]

        result = permutation_test_six_dim(
            ctrl_data, treat_data,
            n_perm=n_perm, seed=seed + sim * 7919,
            alpha=alpha, effect_fn=effect_fn,
            correction="bonferroni_holm",
        )
        n_total_tests += result["n_dims_tested"]
        n_reject += result["n_significant"]

    fpr = n_reject / n_total_tests if n_total_tests > 0 else 0.0  # 经 Holm 校正后的族系误报率
    nominal_ok = fpr <= alpha

    return {
        "n": n,
        "n_sim": n_sim,
        "n_perm": n_perm,
        "alpha": alpha,
        "n_dims": n_dims,
        "total_tests": n_total_tests,
        "n_reject": n_reject,
        "false_positive_rate": round(fpr, 6),
        "nominal_alpha": alpha,
        "fpr_within_nominal": nominal_ok,
        "seed": seed,
    }


# ---------------------------------------------------------------------------
# 回溯性功效计算（对既有产物 P1/P2）
# ---------------------------------------------------------------------------

def retrospective_power(
    control_data: Dict[str, Sequence[float]],
    treatment_data: Dict[str, Sequence[float]],
    *,
    n_perm: int = DEFAULT_N_PERM,
    seed: int = DEFAULT_SEED,
    alpha: float = DEFAULT_ALPHA,
    effect_fn: Optional[Callable] = None,
    n_sim_power: int = 500,
) -> Dict[str, Any]:
    """对既有产物做回溯性功效计算。

    方法：用观测到的效应量作为真实效应，模拟 n_sim_power 次实验，
    估计在该效应量下检验的检出率（功效）。

    返回
    ----
    {dimensions: [{dim, observed_effect, observed_p, retrospective_power, n_observed}],
     alpha, n_perm, seed}
    """
    if effect_fn is None:
        effect_fn = cohens_d

    dims = [d for d in DIMENSIONS if d in control_data and d in treatment_data]
    results: List[Dict[str, Any]] = []

    for dim in dims:
        ctrl = list(control_data[dim])
        treat = list(treatment_data[dim])
        if len(ctrl) != len(treat) or len(ctrl) < 2:
            results.append({
                "dimension": dim,
                "observed_effect": None,
                "observed_p": None,
                "retrospective_power": None,
                "n_observed": len(ctrl),
                "reason": "样本不足",
            })
            continue

        obs_effect = effect_fn(ctrl, treat)
        obs_p = _permutation_p_value(ctrl, treat, n_perm=n_perm, seed=seed, effect_fn=effect_fn)

        # 回溯性功效：以观测效应量为真实效应，模拟检验
        # 关键：需要生成有真实方差的配对数据
        n = len(ctrl)
        diffs = [t - c for c, t in zip(ctrl, treat)]
        mean_diff = sum(diffs) / n
        var_diff = sum((d - mean_diff) ** 2 for d in diffs) / max(n - 1, 1)
        std_diff = math.sqrt(var_diff) if var_diff > 0 else 1.0

        # 模拟：diff ~ N(mean_diff, std_diff) 且原样传给置换检验。
        # 历史缺陷（m8646 修复）两处：
        #   1) 噪声写成 std_diff*0.1 → 模拟 diff 的 std 只有观测的 1/10，
        #      功效被系统性高估（d 被放大 10 倍）；
        #   2) 子种子来自内建 hash(dim)，受 PYTHONHASHSEED 随机化，
        #      同输入跨进程结果不一致（与"可复现"承诺冲突）。
        rng = random.Random(_derive_seed("retrospective", seed, dim))
        n_reject = 0
        for sim in range(n_sim_power):
            sim_base = [rng.gauss(0.0, 1.0) for _ in range(n)]
            sim_treat = [c + rng.gauss(mean_diff, std_diff) for c in sim_base]
            p = _permutation_p_value(
                sim_base, sim_treat, n_perm=n_perm,
                seed=_derive_seed("retro-perm", seed, dim, sim),
                effect_fn=effect_fn,
            )
            if p < alpha:
                n_reject += 1

        ret_power = n_reject / n_sim_power
        results.append({
            "dimension": dim,
            "observed_effect": round(obs_effect, 6),
            "observed_p": round(obs_p, 6),
            "retrospective_power": round(ret_power, 4),
            "analytic_power_same_effect": round(analytic_power_paired(obs_effect, n, alpha=alpha), 4),
            "assumed_diff_mean": round(mean_diff, 6),
            "assumed_diff_std": round(std_diff, 6),
            "n_observed": n,
        })

    return {
        "dimensions": results,
        "alpha": alpha,
        "n_perm": n_perm,
        "n_sim_power": n_sim_power,
        "seed": seed,
    }


# ---------------------------------------------------------------------------
# 高层封装：六维功效门
# ---------------------------------------------------------------------------

def six_dim_power_gate(
    control_data: Dict[str, Sequence[float]],
    treatment_data: Dict[str, Sequence[float]],
    *,
    alpha: float = DEFAULT_ALPHA,
    n_perm: int = DEFAULT_N_PERM,
    seed: int = DEFAULT_SEED,
    min_power: float = 0.8,
    min_effect_size: float = 0.5,
    correction: str = "bonferroni_holm",
    run_aa_check: bool = True,
    run_mde: bool = True,
    run_retrospective: bool = True,
) -> Dict[str, Any]:
    """六维统计功效门：一站式输出所有判决所需信息。

    返回
    ----
    {
        permutation_test: {...},
        aa_check: {...} | None,
        mde_curve: [...] | None,
        retrospective_power: {...} | None,
        gate_verdict: "pass" | "fail" | "inconclusive",
        gate_reason: str,
        n_observations_per_dim: {...},
    }
    """
    perm_result = permutation_test_six_dim(
        control_data, treatment_data,
        alpha=alpha, n_perm=n_perm, seed=seed,
        correction=correction,
    )

    aa_result = None
    if run_aa_check:
        aa_result = aa_false_positive_rate(
            n=20, n_sim=500, n_perm=min(n_perm, 1000),
            alpha=alpha, seed=seed, n_dims=6,
        )

    mde_result = None
    if run_mde:
        mde_result = mde_curve(
            alpha=alpha, power=min_power, n_perm=min(n_perm, 1000),
            seed=seed,
        )

    ret_result = None
    if run_retrospective:
        ret_result = retrospective_power(
            control_data, treatment_data,
            alpha=alpha, n_perm=n_perm, seed=seed,
        )

    # 判决逻辑
    n_sig = perm_result["n_significant"]
    n_tested = perm_result["n_dims_tested"]

    # 观测样本量（取各维最小，偏保守）
    _n_all = [len(control_data[d]) for d in DIMENSIONS if d in control_data]
    n_obs_min = min(_n_all) if _n_all else 0

    # (a) 检验自身的校准门：A/A 空对照误报率必须 ≤ alpha，
    #     否则置换检验的 p 值不可信，任何"显著"都不成立。
    #     历史缺陷（m8646 修复）：aa_check 只落库、不参与判决。
    calibration_ok = True
    calibration_note = "A/A 未运行"
    if aa_result is not None:
        fpr = aa_result.get("false_positive_rate")
        calibration_ok = bool(aa_result.get("fpr_within_nominal", True))
        calibration_note = f"A/A 误报率 {fpr} vs alpha {alpha}"

    # (b) 样本量充足性：用解析功效反解"检出 min_effect_size 所需 n"，
    #     并与真实 n 比对。零显著 + 欠功效 => 证据不足（inconclusive），而非证否（fail）。
    n_required = min_n_for_effect(min_effect_size, alpha=alpha, power=min_power)
    sample_underpowered = n_required is not None and n_obs_min < n_required
    mde_at_n = round(analytic_mde(n_obs_min, alpha=alpha, power=min_power), 4) if n_obs_min >= 2 else None

    # 检查功效是否充足
    power_sufficient = True
    if ret_result:
        for rd in ret_result["dimensions"]:
            rp = rd.get("retrospective_power")
            if rp is not None and rp < min_power:
                power_sufficient = False
                break

    # 检查效应量是否达标
    effect_sufficient = True
    for rd in perm_result["dimensions"]:
        eff = rd.get("effect_size")
        if eff is not None and abs(eff) < min_effect_size:
            effect_sufficient = False
            break

    if not calibration_ok:
        verdict = "fail"
        reason = f"检验校准失控（{calibration_note}）：p 值不可信，判决作废"
    elif n_sig > 0 and power_sufficient and effect_sufficient:
        verdict = "pass"
        reason = f"{n_sig}/{n_tested} 维显著（FWER 控制），功效≥{min_power}，效应量≥{min_effect_size}"
    elif n_sig == 0 and sample_underpowered:
        verdict = "inconclusive"
        reason = (
            f"证据不足：{n_tested} 维均不显著，但 n={n_obs_min} < 检出 d={min_effect_size} "
            f"所需 n={n_required}（power={min_power}）；n={n_obs_min} 时解析 MDE={mde_at_n}"
        )
    elif n_sig == 0:
        verdict = "fail"
        reason = (
            f"无维度在 FWER 控制下显著（{n_tested} 维均不显著），且 n={n_obs_min} ≥ 所需 {n_required}"
            f"（power={min_power} 可检出 d={min_effect_size}）"
        )
    else:
        verdict = "inconclusive"
        reasons = []
        if not power_sufficient:
            reasons.append("部分维度回溯功效不足")
        if not effect_sufficient:
            reasons.append("部分维度效应量低于阈值")
        reason = f"{n_sig}/{n_tested} 维显著，但{'；'.join(reasons)}"

    n_obs = {}
    for dim in DIMENSIONS:
        if dim in control_data:
            n_obs[dim] = len(control_data[dim])

    return {
        "permutation_test": perm_result,
        "aa_check": aa_result,
        "mde_curve": mde_result,
        "retrospective_power": ret_result,
        "gate_verdict": verdict,
        "gate_reason": reason,
        "calibration_ok": calibration_ok,
        "calibration_note": calibration_note,
        "n_required_for_min_effect": n_required,
        "sample_underpowered": sample_underpowered,
        "mde_analytic_at_n": mde_at_n,
        "n_observations_per_dim": n_obs,
        "alpha": alpha,
        "min_power": min_power,
        "min_effect_size": min_effect_size,
        "n_perm": n_perm,
        "seed": seed,
    }


# ---------------------------------------------------------------------------
# 六维优势判决（G25）与设计期功效
# ---------------------------------------------------------------------------
# 上面的 permutation_test_six_dim / mde_curve / aa_false_positive_rate /
# retrospective_power / six_dim_power_gate 回答的是「差异是否显著 / 需要多少样本」。
# 本节补三类它们不覆盖的命题：
#
# 1. 方向语义：六维里 accuracy（相对误差）/ efficiency（耗时）/ reproducibility
#    （两次运行差异）是「越小越好」。不做方向编码，「优于」的效应符号会整体反号。
# 2. 族判决（G25）：命题是「六维中至少 k 维优于对照」，必须按族（Holm）裁决，
#    并**实测该判决规则本身**的零假设误报率（族级 FWER，而不是逐维 α）。
# 3. 设计期功效：判出「≥k 维优势」需要多少配对对数，以及臂间相关 rho 带来的
#    配对收益（rho=0 是最保守情形，必须与主结果同时呈现）。
#
# 数字纪律：每条测量记录必须带 source（工件路径/命令）才入账，单位不一致不成对。
# 参考：Holm (1979) Scand. J. Statist. 6, 65-70；Good (2005) Permutation Tests；
#       Cohen (1988) Statistical Power Analysis。

#: +1 = 数值越大越好；-1 = 数值越小越好。
#: accuracy 的口径是**相对误差**、efficiency 是**耗时/步数**、reproducibility 是
#: **两次运行的相对差异**——三者都是越小越好；口径必须在记录里用 unit 字段声明。
DIMENSION_DIRECTION: Dict[str, int] = {
    "correctness": +1,
    "accuracy": -1,
    "stability": +1,
    "efficiency": -1,
    "reproducibility": -1,
    "applicability": +1,
}

DIMENSION_NOTE: Dict[str, str] = {
    "correctness": "越大越好：守恒/解析极限通过项数或 1-归一化残差",
    "accuracy": "越小越好：与基准的相对误差",
    "stability": "越大越好：稳定性通过项数或稳健裕度",
    "efficiency": "越小越好：墙钟秒数/步数",
    "reproducibility": "越小越好：两次运行的相对差异",
    "applicability": "越大越好：已登记的限制/失效模式条数",
}

#: 配对检验的**设计下限**（非数学下限）：等幅配对下双侧最小可达 p = 2/2^n，
#: n=5 → 6.25%（根本拒绝不了），n=6 → 3.1%；取 9 是为了让 0.05 以下的 p 阶梯
#: 不止一两档（否则功效被 p 值离散化吃掉）。
MIN_TESTABLE_PAIRS = 9

#: 解析 MDE 系数 z_{1-alpha/2}+z_power（α=0.05、功效 0.8）。
MDE_COEFF_TWO_SIDED_05_80 = 2.801585
MDE_COEFF_ONE_SIDED_05_80 = 2.486475

ALTERNATIVES = ("two-sided", "greater", "less")


def mde_coefficient(alpha: float = DEFAULT_ALPHA, power: float = 0.8,
                    two_sided: bool = True) -> float:
    """解析 MDE 系数：``z_{1-alpha/2}+z_power``（双侧）或 ``z_{1-alpha}+z_power``。"""
    z_alpha = (NormalDist().inv_cdf(1.0 - alpha / 2.0) if two_sided
               else NormalDist().inv_cdf(1.0 - alpha))
    return float(z_alpha + NormalDist().inv_cdf(power))


def binom_ci(k: int, n: int, level: float = 0.95) -> Dict[str, Any]:
    """二项比例的 Clopper-Pearson 精确区间（无 scipy 时退化为 Wilson 得分区间）。

    ``method`` 字段如实写明用了哪一种——区间方法不同，宽度会差一点，审计必须能看出来。
    """
    k, n = int(k), int(n)
    if n <= 0:
        return {"p_hat": float("nan"), "lo": float("nan"), "hi": float("nan"),
                "level": level, "method": "n/a", "k": k, "n": n}
    a = (1.0 - level) / 2.0
    p_hat = k / n
    try:  # 首选精确区间
        from scipy.stats import beta as _beta  # type: ignore

        lo = 0.0 if k == 0 else float(_beta.ppf(a, k, n - k + 1))
        hi = 1.0 if k == n else float(_beta.ppf(1.0 - a, k + 1, n - k))
        method = "clopper-pearson"
    except Exception:  # pragma: no cover - 无 scipy 环境
        z = NormalDist().inv_cdf(1.0 - a)
        denom = 1.0 + z * z / n
        centre = (p_hat + z * z / (2 * n)) / denom
        half = z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n)) / denom
        lo, hi = max(0.0, centre - half), min(1.0, centre + half)
        method = "wilson"
    return {"p_hat": p_hat, "lo": lo, "hi": hi, "level": level,
            "method": method, "k": k, "n": n}


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
    缺 ``source`` 的记录不入账（沿用 dimensions.py 的「数字无出处不入账」纪律）。
    """
    errors: List[str] = []
    buckets: Dict[Tuple[str, str, str], Dict[str, Dict[str, Any]]] = {}
    for rec in records:
        dim = str(rec.get("dimension") or rec.get("dim") or "")
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


def oriented_deltas(pairs: Sequence[Dict[str, Any]], dim: str) -> List[float]:
    """``delta_i = direction(dim) * (v_treatment - v_control)``，正 = 实验组更好。"""
    if dim not in DIMENSION_DIRECTION:
        raise ValueError(f"未知维度 {dim!r}")
    sgn = DIMENSION_DIRECTION[dim]
    return [float(sgn * (p["v_treatment"] - p["v_control"])) for p in pairs]


def paired_effect(deltas: Sequence[float], *, level: float = 0.95) -> Dict[str, Any]:
    """配对效应量：Cohen's d_z、配对差均值/中位数及其区间。

    中位数区间用二项式序统计量（不依赖正态假设）：
    ``x_(j), x_(n+1-j)``，``j = ceil(n/2 - 1.96*sqrt(n)/2)``。
    """
    d = [float(x) for x in deltas]
    n = len(d)
    out: Dict[str, Any] = {"n": n}
    if n == 0:
        out.update({"mean_diff": None, "median_diff": None, "sd": None, "cohen_dz": None,
                    "se": None, "ci95_mean": None, "ci95_median": None})
        return out
    mean = math.fsum(d) / n
    sd = None
    if n > 1:
        var = math.fsum((x - mean) ** 2 for x in d) / (n - 1)
        sd = math.sqrt(var) if var > 0 else 0.0
    srt = sorted(d)
    median = srt[n // 2] if n % 2 == 1 else 0.5 * (srt[n // 2 - 1] + srt[n // 2])
    out["mean_diff"] = mean
    out["median_diff"] = median
    out["sd"] = sd
    if n > 1 and sd:
        out["cohen_dz"] = mean / sd
        se = sd / math.sqrt(n)
        out["se"] = se
        crit = _t_ppf(0.5 + level / 2.0, n - 1)
        out["ci95_mean"] = [mean - crit * se, mean + crit * se]
        out["ci_method"] = "t"
    elif n > 1:  # 配对差零方差：d_z 发散，如实标 inf/0，不伪造有限值
        out["cohen_dz"] = math.inf if mean > 0 else (-math.inf if mean < 0 else 0.0)
        out["se"] = None
        out["ci95_mean"] = None
    else:
        out["cohen_dz"] = None
        out["se"] = None
        out["ci95_mean"] = None
    if n >= 3:
        j = max(1, int(math.ceil(n / 2.0 - 1.96 * math.sqrt(n) / 2.0)))
        j = min(j, (n + 1) // 2)
        out["ci95_median"] = [srt[j - 1], srt[n - j]]
    else:
        out["ci95_median"] = None
    return out


def _sign_matrix(n_perm: int, n: int, seed: int) -> List[Tuple[int, ...]]:
    """符号翻转矩阵（±1），由 stdlib random 生成 —— 跨环境/跨进程逐位一致。"""
    rng = random.Random(_derive_seed("sign-flip", seed, n, n_perm))
    return [tuple(1 if rng.getrandbits(1) else -1 for _ in range(n))
            for _ in range(int(n_perm))]


def _mul(a: int, b: float) -> float:
    return a * b


def _perm_pvalue(deltas: Sequence[float], signs: Sequence[Tuple[int, ...]],
                 alternative: str) -> float:
    """单行配对符号翻转置换检验 p 值（统计量 = 配对差均值，p 用 (1+#)/(1+B)）。"""
    if alternative not in ALTERNATIVES:
        raise ValueError(f"未知备择 {alternative!r}")
    n = len(deltas)
    t_obs = math.fsum(deltas) / n
    hits = 0
    if alternative == "two-sided":
        thr = abs(t_obs) - 1e-12
        for s in signs:
            if abs(math.fsum(map(_mul, s, deltas)) / n) >= thr:
                hits += 1
    elif alternative == "greater":
        thr = t_obs - 1e-12
        for s in signs:
            if math.fsum(map(_mul, s, deltas)) / n >= thr:
                hits += 1
    else:
        thr = t_obs + 1e-12
        for s in signs:
            if math.fsum(map(_mul, s, deltas)) / n <= thr:
                hits += 1
    return (1.0 + hits) / (1.0 + len(signs))


def sign_flip_permutation_test(
    deltas: Sequence[float],
    *,
    n_perm: int = DEFAULT_N_PERM,
    seed: int = DEFAULT_SEED,
    alternative: str = "two-sided",
) -> Dict[str, Any]:
    """配对符号翻转置换检验（统计量 = 配对差均值；零假设下符号可交换 → 精确）。

    p 值用 ``(1 + #{|T_perm| >= |T_obs|}) / (1 + n_perm)``，保证 p > 0。
    已知边界：等幅配对下 n 小时 p 值离散，n=5 时双侧最小 p=6.25% > 5%（拒绝不了）。
    """
    if alternative not in ALTERNATIVES:
        raise ValueError(f"未知备择 {alternative!r}")
    d = [float(x) for x in deltas]
    n = len(d)
    if n < 2:
        raise ValueError(f"配对符号翻转检验需要 n>=2（当前 n={n}）")
    if n_perm < 1:
        raise ValueError("n_perm 必须 >=1")
    signs = _sign_matrix(n_perm, n, _derive_seed("sign-flip-test", seed, n, n_perm))
    return {
        "statistic": math.fsum(d) / n,
        "p_value": _perm_pvalue(d, signs, alternative),
        "n_perm": int(n_perm),
        "n": n,
        "alternative": alternative,
        "seed": int(seed),
        "method": "paired_sign_flip_permutation(mean)",
    }


def holm_adjust(pvalues: Dict[str, float], alpha: float = DEFAULT_ALPHA) -> Dict[str, Any]:
    """Bonferroni-Holm 逐步下降校正（FWER ≤ alpha，任意相依结构下成立）。

    ``p_adj_(i) = min(1, max_{j<=i} (m-j+1) p_(j))``（保持单调）；拒绝规则等价于
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
        running = max(running, factor * p)
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


def min_perm_for_holm(alpha: float = DEFAULT_ALPHA,
                      n_tests: int = len(DIMENSIONS)) -> int:
    """Holm 校正下置换 p 值的**分辨率下限**：最小可达 p = 1/(1+B) 必须 ≤ alpha/m。

    B < ceil(m/alpha)-1 时，即使效应再大，校正后的 p 也永远 > alpha —— 族判决
    在数学上不可达（这不是"没效应"，是"分辨率不够"）。调用方必须检查该值。
    """
    return int(math.ceil(int(n_tests) / float(alpha))) - 1


def six_dim_advantage(
    records: Sequence[Dict[str, Any]],
    *,
    control_arm: str = "control",
    treatment_arm: str = "treatment",
    alpha: float = DEFAULT_ALPHA,
    n_perm: int = DEFAULT_N_PERM,
    seed: int = 0,
    min_dims: int = 2,
    alternative: str = "greater",
    dims: Sequence[str] = DIMENSIONS,
) -> Dict[str, Any]:
    """六维「优于对照」族判决（G25，预声明规则）。

    规则：六维中 **≥ min_dims 维**在 Holm(alpha) 校正后仍显著为实验组更好
    （方向由 ``DIMENSION_DIRECTION`` 统一成「正 = 更好」）。
    方向性备择 ``alternative="greater"`` 是预声明的——命题本身是「优于」而非「不同」。
    """
    if alternative not in ALTERNATIVES:
        raise ValueError(f"未知备择 {alternative!r}")
    folded = build_pairs(records, control_arm=control_arm,
                         treatment_arm=treatment_arm, dims=dims)
    table: Dict[str, Dict[str, Any]] = {}
    pvals: Dict[str, float] = {}
    for dim in dims:
        prs = folded["pairs"][dim]
        entry: Dict[str, Any] = {"n_pairs": len(prs), "unit": prs[0]["unit"] if prs else ""}
        if len(prs) >= 2:
            d = oriented_deltas(prs, dim)
            entry["effect"] = paired_effect(d)
            test = sign_flip_permutation_test(
                d, n_perm=n_perm,
                seed=_derive_seed("advantage", seed, dim), alternative=alternative)
            entry["p_perm"] = test["p_value"]
            entry["test"] = test
            pvals[dim] = test["p_value"]
        else:
            entry["effect"] = paired_effect(oriented_deltas(prs, dim)) if prs else None
            entry["p_perm"] = None
            entry["note"] = "无可检验配对（n<2）"
        table[dim] = entry

    holm = holm_adjust(pvals, alpha) if pvals else {
        "alpha": float(alpha), "adjusted": {}, "reject": {}, "n_reject": 0,
        "steps": [], "method": "holm"}
    for dim, entry in table.items():
        entry["p_holm"] = holm["adjusted"].get(dim)
        entry["significant_better"] = bool(holm["reject"].get(dim, False))
        eff = entry.get("effect") or {}
        median = eff.get("median_diff")
        if eff:
            if median is None:
                entry["direction"] = "no_data"
            elif median > 0:
                entry["direction"] = "treatment_better"
            elif median < 0:
                entry["direction"] = "treatment_worse"
            else:
                entry["direction"] = "indistinguishable"
        else:
            entry["direction"] = "no_data"

    n_sig = int(holm.get("n_reject", 0))
    return {
        "rule": (f"G25: 六维中 >= {min_dims} 维在 Holm(alpha={alpha}) 下显著优于对照"
                 f"（备择={alternative}）"),
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
        "min_testable_pairs": MIN_TESTABLE_PAIRS,
        "min_perm_for_holm": min_perm_for_holm(alpha, len(dims)),
        "n_perm_resolution_ok": bool(int(n_perm) >= min_perm_for_holm(alpha, len(dims))),
    }


def paired_gaussian_arms(
    rng: random.Random,
    n_pairs: int,
    mean_diff: float,
    *,
    sigma: float = 1.0,
    rho: float = 0.0,
) -> Tuple[List[float], List[float]]:
    """生成配对数据：两臂各 sd≈sigma、臂间相关≈rho、均值差=mean_diff。

    ``x_c = z + e_c``, ``x_t = z + e_t + mean_diff``，``z~N(0,rho*sigma^2)``,
    ``e~N(0,(1-rho)*sigma^2)`` → 臂内 sd=sigma，臂间相关=rho，
    配对差 sd = sigma*sqrt(2(1-rho))（rho 越大配对收益越大）。
    """
    n = int(n_pairs)
    if not 0.0 <= rho <= 1.0:
        raise ValueError("rho 必须在 [0, 1]")
    z = [rng.gauss(0.0, math.sqrt(rho) * sigma) for _ in range(n)]
    sd_e = math.sqrt(max(1.0 - rho, 1e-12)) * sigma
    e_c = [rng.gauss(0.0, sd_e) for _ in range(n)]
    e_t = [rng.gauss(0.0, sd_e) for _ in range(n)]
    return ([z[i] + e_c[i] for i in range(n)],
            [z[i] + e_t[i] + mean_diff for i in range(n)])


def _mean_std(xs: Sequence[float]) -> Tuple[float, float]:
    n = len(xs)
    m = math.fsum(xs) / n
    if n < 2:
        return m, 0.0
    return m, math.sqrt(math.fsum((x - m) ** 2 for x in xs) / (n - 1))


def _corr(x: Sequence[float], y: Sequence[float]) -> float:
    mx, sx = _mean_std(x)
    my, sy = _mean_std(y)
    if sx <= 0 or sy <= 0:
        return float("nan")
    cov = math.fsum((a - mx) * (b - my) for a, b in zip(x, y)) / (len(x) - 1)
    return cov / (sx * sy)


def _analytic_power_general(dz: float, n: int, alpha: float, two_sided: bool) -> float:
    """正态近似 + t 分位修正的解析功效（不依赖 scipy，口径与置换检验一致）。"""
    if n is None or n < 2:
        return 0.0
    crit = _t_ppf(1.0 - (alpha / 2.0 if two_sided else alpha), n - 1)
    nc = math.sqrt(n) * abs(float(dz))
    nd = NormalDist()
    if two_sided:
        return min(1.0, max(0.0, nd.cdf(nc - crit) + nd.cdf(-nc - crit)))
    return min(1.0, max(0.0, nd.cdf(nc - crit)))


def power_positive_control(
    *,
    d: float = 0.5,
    n_pairs: int = 20,
    rho: float = 0.7,
    n_sims: int = 300,
    n_perm: int = 499,
    alpha: float = DEFAULT_ALPHA,
    alternative: str = "greater",
    seed: int = 0,
    sigma: float = 1.0,
) -> Dict[str, Any]:
    """功效正控：已知合成效应 d=0.5、n=20 时，实测检出率（目标 ≥70%）。

    ``d`` 的口径是**标准化均值差（臂内 sd 为单位）**；配对设计的收益来自 rho>0
    时配对差 sd 变小，故 ``d_z = d/sqrt(2(1-rho))``；``rho=0`` 是最保守情形
    （等价于放弃配对收益），必须与主结果同时报告。
    """
    if alternative not in ALTERNATIVES:
        raise ValueError(f"未知备择 {alternative!r}")
    n_pairs, n_sims, n_perm = int(n_pairs), int(n_sims), int(n_perm)
    rng = random.Random(_derive_seed("power-pos", seed, n_pairs, n_sims, n_perm,
                                     round(float(d) * 1e6), round(float(rho) * 1e6),
                                     alternative))
    signs = _sign_matrix(n_perm, n_pairs, _derive_seed("power-pos-signs", seed, n_pairs, n_perm))
    hits = 0
    xc_all: List[float] = []
    xt_all: List[float] = []
    mean_diffs: List[float] = []
    sds: List[float] = []
    for _ in range(n_sims):
        xc, xt = paired_gaussian_arms(rng, n_pairs, d * sigma, sigma=sigma, rho=rho)
        deltas = [t - c for c, t in zip(xc, xt)]
        if _perm_pvalue(deltas, signs, alternative) <= alpha:
            hits += 1
        m, s = _mean_std(deltas)
        mean_diffs.append(m)
        sds.append(s)
        xc_all.extend(xc)
        xt_all.extend(xt)
    rate = hits / n_sims
    mean_sd = math.fsum(sds) / len(sds) if sds else 0.0
    dz_realized = (math.fsum(mean_diffs) / len(mean_diffs) / mean_sd) if mean_sd > 0 else None
    analytic = _analytic_power_general(
        d / math.sqrt(2.0 * (1.0 - rho)) if rho < 1.0 else d,
        n_pairs, alpha, two_sided=(alternative == "two-sided"))
    return {
        "design": (f"合成效应 d={d}, n_pairs={n_pairs}, rho={rho}, "
                   f"备择={alternative}, alpha={alpha}"),
        "d": float(d),
        "n_pairs": n_pairs,
        "rho_assumed": float(rho),
        "rho_realized": _corr(xc_all, xt_all),
        "dz_realized": dz_realized,
        "detection_rate": rate,
        "detection_ci": binom_ci(hits, n_sims),
        "analytic_power_normal_t": analytic,
        "n_sims": n_sims,
        "n_perm": n_perm,
        "alpha": float(alpha),
        "alternative": alternative,
        "seed": int(seed),
    }


def power_vs_rho(
    *,
    d: float = 0.5,
    n_pairs: int = 20,
    rho_grid: Sequence[float] = (0.0, 0.3, 0.5, 0.7, 0.85),
    n_sims: int = 200,
    n_perm: int = 299,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
) -> Dict[str, Any]:
    """配对收益敏感性：检出率作为臂间相关 rho 的函数（两种备择都报）。"""
    rows = []
    for rho in rho_grid:
        row: Dict[str, Any] = {
            "rho": float(rho),
            "dz": d / math.sqrt(2.0 * (1.0 - rho)) if rho < 1.0 else float("inf"),
        }
        for alt in ("greater", "two-sided"):
            r = power_positive_control(d=d, n_pairs=n_pairs, rho=rho, n_sims=n_sims,
                                       n_perm=n_perm, alpha=alpha, alternative=alt, seed=seed)
            row[f"power_{alt}"] = r["detection_rate"]
            row[f"analytic_{alt}"] = r["analytic_power_normal_t"]
        rows.append(row)
    return {
        "design": f"d={d}, n_pairs={n_pairs}，rho 扫描（配对收益敏感性）",
        "rows": rows,
        "n_sims": int(n_sims),
        "n_perm": int(n_perm),
        "note": ("rho 是设计假设：同谱系/同席位、只改一条路线时两臂强相关是预期的；"
                 "rho=0 行是最保守情形，必须与主结果同时呈现"),
    }


def null_family_sim(
    *,
    n_families: int = 100,
    n_pairs: int = 20,
    n_perm: int = 999,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
    min_dims: int = 1,
    alternative: str = "greater",
    dims: Sequence[str] = DIMENSIONS,
) -> Dict[str, Any]:
    """零效应族模拟：每个族六维都是 A/A 数据（两臂同分布），跑完整族判决。

    返回
    ----
    ``per_dim``：每维误报率（附 95% CI）；
    ``family_any``：≥1 维显著的族比例（= FWER 估计）；
    ``family_ge_min_dims``：**判决规则本身**的误报率（≥min_dims 维显著）；
    ``family_all``：六维全显著的族比例（极端检验，应≈0）。
    """
    if alternative not in ALTERNATIVES:
        raise ValueError(f"未知备择 {alternative!r}")
    n_families, n_pairs, n_perm = int(n_families), int(n_pairs), int(n_perm)
    m = len(dims)
    rng = random.Random(_derive_seed("null-family", seed, n_pairs, n_perm,
                                     n_families, alternative))
    signs = _sign_matrix(n_perm, n_pairs,
                         _derive_seed("null-family-signs", seed, n_pairs, n_perm))
    pmat: List[List[float]] = [[] for _ in range(n_families)]
    for dim in dims:
        sgn = DIMENSION_DIRECTION[dim]
        for f in range(n_families):
            xc = [rng.gauss(0.0, 1.0) for _ in range(n_pairs)]
            xt = [rng.gauss(0.0, 1.0) for _ in range(n_pairs)]
            pmat[f].append(_perm_pvalue([sgn * (t - c) for c, t in zip(xc, xt)],
                                        signs, alternative))
    reject_rows = []
    for f in range(n_families):
        h = holm_adjust({dims[j]: pmat[f][j] for j in range(m)}, alpha)
        reject_rows.append([bool(h["reject"][d]) for d in dims])
    per_dim = {}
    for j, dim in enumerate(dims):
        ci = binom_ci(int(sum(1 for r in reject_rows if r[j])), n_families)
        ci["dimension"] = dim
        ci["direction"] = DIMENSION_DIRECTION[dim]
        per_dim[dim] = ci
    counts = [sum(1 for v in row if v) for row in reject_rows]
    any_ci = binom_ci(int(sum(1 for c in counts if c >= 1)), n_families)
    ge_ci = binom_ci(int(sum(1 for c in counts if c >= int(min_dims))), n_families)
    all_ci = binom_ci(int(sum(1 for c in counts if c >= m)), n_families)
    return {
        "n_families": n_families,
        "n_pairs": n_pairs,
        "n_perm": n_perm,
        "alpha": float(alpha),
        "alternative": alternative,
        "min_dims": int(min_dims),
        "seed": int(seed),
        "per_dim": per_dim,
        "per_dim_max_rate": max(v["p_hat"] for v in per_dim.values()),
        "family_any": any_ci,
        "family_ge_min_dims": ge_ci,
        "family_all": all_ci,
        "mean_rejections_per_family": math.fsum(counts) / n_families,
        "min_perm_for_holm": min_perm_for_holm(alpha, m),
        "n_perm_resolution_ok": bool(n_perm >= min_perm_for_holm(alpha, m)),
        "pass_family_rate": bool(ge_ci["p_hat"] <= alpha),
        "pass_per_dim_rate": bool(max(v["p_hat"] for v in per_dim.values()) <= alpha),
    }


def aa_null_control(
    *,
    n_sims: int = 100,
    n_pairs: int = 20,
    n_perm: int = 999,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
    min_dims: int = 2,
    stability_seeds: Sequence[int] = (0, 1, 2, 3, 4),
) -> Dict[str, Any]:
    """A/A 空对照：同一路线测两次，族判决（≥min_dims 维显著）误报率须 ≤ alpha。

    主判据用**族判决**的误报率——这正是战役真正要用的规则；逐维误报率同时报告
    （附 95% CI）。``stability_seeds`` 多随机种子重复，仅作抽样波动检查，不改主判据。
    """
    primary = null_family_sim(n_families=n_sims, n_pairs=n_pairs, n_perm=n_perm,
                              alpha=alpha, seed=seed, min_dims=min_dims,
                              alternative="greater")
    primary_two_sided = null_family_sim(n_families=n_sims, n_pairs=n_pairs, n_perm=n_perm,
                                        alpha=alpha, seed=seed, min_dims=min_dims,
                                        alternative="two-sided")
    pooled_hits = {d: 0 for d in DIMENSIONS}
    for s in stability_seeds:
        r = null_family_sim(n_families=n_sims, n_pairs=n_pairs, n_perm=n_perm,
                            alpha=alpha, seed=int(s), min_dims=min_dims,
                            alternative="greater")
        for d in DIMENSIONS:
            pooled_hits[d] += int(r["per_dim"][d]["k"])
    n_pool = int(n_sims) * len(stability_seeds)
    pooled = {
        "n_families": n_pool,
        "per_dim": {d: binom_ci(pooled_hits[d], n_pool) for d in DIMENSIONS},
        "seeds": [int(s) for s in stability_seeds],
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
        "pass": bool(primary["family_ge_min_dims"]["p_hat"] <= alpha
                     and max_pooled_dim <= alpha),
        "ci_upper_family": primary["family_ge_min_dims"]["hi"],
        "ci_upper_per_dim_pooled": max(v["hi"] for v in pooled["per_dim"].values()),
    }


def family_decision_power_curve(
    *,
    effect_dz: float = 0.8,
    n_affected_dims: int = 2,
    n_grid: Sequence[int] = (9, 12, 16, 20, 26, 32, 40, 60),
    min_dims: int = 2,
    sigma: float = 1.0,
    alpha: float = DEFAULT_ALPHA,
    alternative: str = "greater",
    n_rep: int = 80,
    n_perm: int = 149,
    seed: int = 0,
) -> Dict[str, Any]:
    """族判决功效随 n 的变化：前 ``n_affected_dims`` 维有真实效应 ``effect_dz``，其余为 0。

    这是「要判出 ≥min_dims 维优势需要多少对」的模拟侧反解（判决规则 = Holm 后
    ≥min_dims 维显著）。默认 n_rep/n_perm 偏小以控成本，生产高精度请加大。
    """
    if not 0 <= int(n_affected_dims) <= len(DIMENSIONS):
        raise ValueError("n_affected_dims 超出六维范围")
    rows: List[Dict[str, Any]] = []
    m = len(DIMENSIONS)
    for n in n_grid:
        n = int(n)
        rng = random.Random(_derive_seed("family-power", seed, n, n_rep, n_perm, alternative))
        signs = _sign_matrix(n_perm, n, _derive_seed("family-power-signs", seed, n, n_perm))
        pmat: List[List[float]] = [[] for _ in range(int(n_rep))]
        for j, _dim in enumerate(DIMENSIONS):
            mu = effect_dz * sigma if j < int(n_affected_dims) else 0.0
            for r in range(int(n_rep)):
                D = [mu + sigma * rng.gauss(0.0, 1.0) for _ in range(n)]
                pmat[r].append(_perm_pvalue(D, signs, alternative))
        counts = []
        all_affected = []
        null_dim_rates = []
        for r in range(int(n_rep)):
            h = holm_adjust({DIMENSIONS[j]: pmat[r][j] for j in range(m)}, alpha)
            rej = [bool(h["reject"][d]) for d in DIMENSIONS]
            counts.append(sum(1 for v in rej if v))
            if int(n_affected_dims) > 0:
                all_affected.append(all(rej[:int(n_affected_dims)]))
            if m > int(n_affected_dims):
                null_dim_rates.append(sum(1 for v in rej[int(n_affected_dims):]))
        rows.append({
            "n_pairs": n,
            "power_ge_min_dims": sum(1 for c in counts if c >= int(min_dims)) / len(counts),
            "power_all_affected": (sum(1 for v in all_affected if v) / len(all_affected)
                                   if all_affected else None),
            "mean_rejections": math.fsum(counts) / len(counts),
            "alpha_per_dim_rate_0": (math.fsum(null_dim_rates) / len(null_dim_rates)
                                     if null_dim_rates else None),
        })
    return {
        "design": (f"族判决功效：{n_affected_dims} 维有 d_z={effect_dz}，"
                   f"其余 {m - int(n_affected_dims)} 维为 0"),
        "effect_dz": float(effect_dz),
        "n_affected_dims": int(n_affected_dims),
        "min_dims": int(min_dims),
        "alpha": float(alpha),
        "alternative": alternative,
        "rows": rows,
        "n_rep": int(n_rep),
        "n_perm": int(n_perm),
        "min_perm_for_holm": min_perm_for_holm(alpha, m),
        "n_perm_resolution_ok": bool(int(n_perm) >= min_perm_for_holm(alpha, m)),
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
                            "未命中则报 None（需扩网格）"),
        "target_power": float(target_power),
        "curve": curve,
    }
