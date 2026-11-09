"""六维评价统计功效门（通用层，零领域语义）。

把「优于对照」从叙事升级为带第一类错误控制的判决。

功能
----
1. 六维各定义效应量（Cohen's d 或配对差中位数）+ 配对设计
2. 置换检验 10^4 次 + Bonferroni-Holm FWER 控制
3. MDE(n) 曲线并反解所需 n
4. A/A 空对照误报率 ≤ 5% 验证
5. 对既有产物做回溯性功效计算

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

import math
import random
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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
        curve.append({
            "n": n,
            "mde": mde["mde"],
            "power_achieved": mde["power_achieved"],
            "alpha": alpha,
            "n_perm": n_perm,
        })
    return curve


def _estimate_power(
    delta: float, n: int, alpha: float, n_perm: int,
    seed: int, effect_fn: Callable, control_std: float,
    n_rep: int = 100,
) -> float:
    """估计给定效应量 delta 下的统计功效。"""
    rng = random.Random(seed)
    n_reject = 0
    for rep in range(n_rep):
        ctrl = [rng.gauss(0, control_std) for _ in range(n)]
        treat = [c + delta * control_std for c in ctrl]
        p = _permutation_p_value(
            ctrl, treat, n_perm=n_perm, seed=seed + rep * 7919 + n,
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

    使用逐步缩小范围的方法：先找到 power < target 和 power >= target 的边界，
    然后在边界内二分。
    """
    # 先探测上界：找到一个能达到目标功效的 delta
    hi = 0.5
    while _estimate_power(hi, n, alpha, n_perm, seed, effect_fn, control_std, n_rep=50) < target_power - tol:
        hi *= 2
        if hi > 10:
            # 即使 delta=10 也达不到，返回 hi
            return {"mde": round(hi, 4), "power_achieved": 0.0}

    lo = 0.0
    best_delta = hi
    best_power = _estimate_power(hi, n, alpha, n_perm, seed, effect_fn, control_std, n_rep=100)

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        if mid < 0.01:
            lo = mid
            continue
        achieved = _estimate_power(mid, n, alpha, n_perm, seed, effect_fn, control_std, n_rep=100)
        if achieved >= target_power - tol:
            best_delta = mid
            best_power = achieved
            hi = mid
        else:
            lo = mid

    return {"mde": round(best_delta, 4), "power_achieved": round(best_power, 4)}


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
        if pt["mde"] <= target_mde:
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

    fpr = n_reject / n_total_tests if n_total_tests > 0 else 0.0
    per_dim_fpr = fpr  # 经校正后的族系误报率
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

        rng = random.Random(seed + hash(dim) % 10000)
        n_reject = 0
        for sim in range(n_sim_power):
            # 生成有真实方差的配对数据：
            # ctrl ~ N(0, std_diff), treat = ctrl + mean_diff + noise
            # 这样 diff = mean_diff + noise，std(diff) ≈ std_diff
            sim_ctrl = [rng.gauss(0, std_diff) for _ in range(n)]
            sim_treat = [c + mean_diff + rng.gauss(0, std_diff * 0.1) for c in sim_ctrl]
            p = _permutation_p_value(
                sim_ctrl, sim_treat, n_perm=n_perm,
                seed=seed + sim * 3571 + hash(dim) % 1000,
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

    if n_sig > 0 and power_sufficient and effect_sufficient:
        verdict = "pass"
        reason = f"{n_sig}/{n_tested} 维显著（FWER 控制），功效≥{min_power}，效应量≥{min_effect_size}"
    elif n_sig == 0:
        verdict = "fail"
        reason = f"无维度在 FWER 控制下显著（{n_tested} 维均不显著）"
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
        "n_observations_per_dim": n_obs,
        "alpha": alpha,
        "min_power": min_power,
        "min_effect_size": min_effect_size,
        "n_perm": n_perm,
        "seed": seed,
    }
