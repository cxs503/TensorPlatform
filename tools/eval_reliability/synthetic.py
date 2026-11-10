"""合成恢复夹具：α 真值可控、Δα 有理论预测的判级生成模型。

为什么必须有这一层：真实台账里几乎没有同一对象被两个判级者评的记录（见
``dataset.coverage``），因此**无法**从真实数据证明「一致性系数实现是对的」。
唯一诚实的出路是可控真值：

1. **生成模型**（等级尺度上逐层可解释，所有量都以「级」为单位）::

        ℓ_i ~ p(ℓ)                                 真实等级，ℓ ∈ {1..5}
        r_ig = clip( round( ℓ_i + b_g + e_ig ), 1, 5 )   判级者 g 的判级
        e_ig ~ N(0, σ²)                             判级者随机噪声（级）

   ``b_g`` 是**判级者偏置**（级）：``+0.5`` = lenient（宽一个半档），
   ``−0.5`` = harsh。σ 单调控制一致性：σ→0 时 α→1。

2. **总体极限 α**（不是抽样估计）：给定 ``p``、``b``、``σ``，单元级巧合矩阵
   的条件期望可精确枚举——

       P(r_g = c | ℓ) = Φ((c+0.5 − ℓ − b_g)/σ) − Φ((c−0.5 − ℓ − b_g)/σ)

   （两端 ``c=1``/``c=5`` 用 ±∞ 截断，正好复现 ``clip``），于是

       o_ck = Σ_ℓ p(ℓ) · (1/(m−1)) · Σ_{g≠g'} P_g(c|ℓ) P_g'(k|ℓ)

   总质量 = m（与样本口径一致），用与样本**同一个**序数 δ² 算出 α_pop。
   ``α_true`` 就是 α_pop：它是生成模型的解析性质，不含抽样噪声。

3. **σ 的校准**：对目标 α_true 二分求解 σ（α 关于 σ 单调递减）。

4. **注入对照**：把 ``[0, +0.5, 0, −0.5]`` 四个判级者的 α_pop 与全零偏置的
   α_pop 相减得**理论 Δα**；再在同随机数（paired）样本上实测 Δα̂，两者相对
   误差 ≤10% 才算「注入效应与理论一致」（判据 J5）。

**局限（写在报告里，不藏着）**：α 的序数度量依赖边际分布 p，因此 α_true 与
p 绑定。夹具用对称单峰先验为主口径，另给均匀/偏斜先验作为敏感性行。
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import numpy as np

from tools.eval_reliability import stats

#: 主口径等级先验（对称单峰，sd≈1.1 的离散正态截断到 1..5）
LEVEL_PRIOR: tuple[float, ...] = (0.15, 0.25, 0.32, 0.20, 0.08)
#: 敏感性口径：均匀先验（边际最不偏斜）
LEVEL_PRIOR_UNIFORM: tuple[float, ...] = (0.2, 0.2, 0.2, 0.2, 0.2)
#: 敏感性口径：偏斜先验（模拟真实台账 S1 占多数的形态）
LEVEL_PRIOR_SKEWED: tuple[float, ...] = (0.45, 0.30, 0.15, 0.07, 0.03)

LEVELS: tuple[int, ...] = (1, 2, 3, 4, 5)


# ───────────────────── 单判级者的等级条件分布 ─────────────────────

def rating_probs(level: int, bias: float, sigma: float) -> np.ndarray:
    """``P(r = c | ℓ)``，``c = 1..5``（正态噪声 + 四舍五入 + 截断）。"""
    if sigma <= 0:
        out = np.zeros(5)
        out[int(min(max(round(level + bias), 1), 5)) - 1] = 1.0
        return out
    out = []
    for c in LEVELS:
        # 截断后的类别边界：round 半档处切开，端点类别的边界在 ±∞。
        # 端点若写成 0.5 / 5.5 会把被 clip 掉的概率质量漏算/多算，
        # 使总体 α 与样本生成模型不一致（低 α 档偏 0.1 量级）。
        hi = math.inf if c == 5 else c + 0.5
        lo = -math.inf if c == 1 else c - 0.5
        out.append(stats.norm_cdf((hi - level - bias) / sigma)
                   - stats.norm_cdf((lo - level - bias) / sigma))
    arr = np.clip(np.asarray(out, dtype=float), 0.0, None)
    s = arr.sum()
    return arr / s if s > 0 else arr


def population_coincidence(
    prior: Sequence[float] = LEVEL_PRIOR,
    biases: Sequence[float] = (0.0, 0.0),
    sigma: float = 0.8,
) -> np.ndarray:
    """总体极限巧合矩阵（总质量 = 判级者数 ``m``，见模块文档）。"""
    p = np.asarray(prior, dtype=float)
    p = p / p.sum()
    m = len(biases)
    o = np.zeros((5, 5), dtype=float)
    for li, level in enumerate(LEVELS):
        if p[li] <= 0:
            continue
        probs = [rating_probs(level, float(b), sigma) for b in biases]
        acc = np.zeros((5, 5), dtype=float)
        for g in range(m):
            for gg in range(m):
                if g == gg:
                    continue
                acc += np.outer(probs[g], probs[gg])
        o += p[li] * acc / (m - 1)
    return o


def alpha_from_coincidence(o: np.ndarray, level: str = "ordinal") -> float:
    """由巧合矩阵算 Krippendorff α（与样本口径同一公式，``n`` = 总质量）。"""
    n = float(o.sum())
    n_c = o.sum(axis=0)
    if n <= 1 or (n_c > 0).sum() < 2:
        return float("nan")
    d2 = (stats._delta2_ordinal(n_c) if level == "ordinal"
          else stats._delta2_nominal(n_c))
    do = float((o * d2).sum() / n)
    # 总体极限：样本公式里的 D_e 分母 n(n−1) 在 N→∞ 下 → n²（这里 n 是单元级
    # 总质量 m，不是样本观测数）。写成 n(n−1) 会把小样本修正搬到总体真值上，
    # 使「完全随机判级」的 α 地板值假抬高到 1/m（m=4 时 0.25）。
    de = float((np.outer(n_c, n_c) * d2).sum() / (n * n))
    if de <= 0:
        return float("nan")
    return 1.0 - do / de


def population_alpha(prior: Sequence[float] = LEVEL_PRIOR,
                     biases: Sequence[float] = (0.0, 0.0),
                     sigma: float = 0.8,
                     level: str = "ordinal") -> float:
    """总体极限 α（真值）——生成模型的解析性质，无抽样噪声。"""
    return alpha_from_coincidence(population_coincidence(prior, biases, sigma), level)


# ─────────────────────────── σ 校准 ───────────────────────────

def calibrate_sigma(
    target_alpha: float,
    *,
    prior: Sequence[float] = LEVEL_PRIOR,
    biases: Sequence[float] = (0.0, 0.0, 0.0, 0.0),
    lo: float = 1e-3,
    hi: float = 400.0,
    tol: float = 1e-9,
    max_iter: int = 200,
) -> dict[str, Any]:
    """二分求 σ 使总体 α 命中 ``target_alpha``（α 关于 σ 单调递减）。

    ``α`` 在 σ→0 时 →1、σ→∞ 时 →0（边际驱动的期望分歧等于观测分歧）。
    目标不可达时返回 ``reached=False``（不返回一个凑数的 σ）。
    """
    f = lambda s: population_alpha(prior, biases, s) - float(target_alpha)
    f_lo, f_hi = f(lo), f(hi)
    if f_lo < 0 or f_hi > 0:
        return {"reached": False, "sigma": float("nan"), "target": target_alpha,
                "alpha_at_lo": f_lo + target_alpha, "alpha_at_hi": f_hi + target_alpha}
    a, b = lo, hi
    for _ in range(max_iter):
        mid = 0.5 * (a + b)
        if f(mid) > 0:
            a = mid               # α 仍偏高 → 需更大 σ
        else:
            b = mid
        if b - a < tol * max(1.0, b):
            break
    sigma = 0.5 * (a + b)
    achieved = population_alpha(prior, biases, sigma)
    return {"reached": True, "sigma": float(sigma), "target": float(target_alpha),
            "achieved_alpha": float(achieved), "iterations": int(max_iter)}


# ─────────────────────────── 样本模拟 ───────────────────────────

def simulate_sample(
    rng: np.random.Generator,
    n_units: int,
    *,
    prior: Sequence[float] = LEVEL_PRIOR,
    biases: Sequence[float] = (0.0, 0.0, 0.0, 0.0),
    sigma: float = 0.8,
    latent: np.ndarray | None = None,
    noise: np.ndarray | None = None,
) -> np.ndarray:
    """按生成模型抽样，返回 ``n_units × m`` 等级矩阵（float，无缺格）。

    ``latent`` / ``noise`` 可外部注入以做 **paired（同随机数）对照**：注入偏置
    前后用同一批潜在等级与同一批噪声，Δα 的抽样噪声被消掉，只剩偏置效应本身。
    """
    m = len(biases)
    p = np.asarray(prior, dtype=float)
    p = p / p.sum()
    if latent is None:
        latent = rng.choice(np.arange(1, 6), size=n_units, p=p).astype(float)
    if noise is None:
        noise = rng.normal(0.0, sigma, size=(n_units, m))
    b = np.asarray(biases, dtype=float)[None, :]
    cont = latent[:, None] + b + noise
    return np.clip(np.floor(cont + 0.5), 1, 5).astype(float)


# ───────────────────── 判据 J1：α 真值恢复 ─────────────────────

def alpha_recovery(
    targets: Iterable[float] = (0.2, 0.5, 0.8),
    *,
    reps: int = 200,
    n_units: int = 60,
    n_raters: int = 4,
    prior: Sequence[float] = LEVEL_PRIOR,
    n_boot: int = 400,
    level: float = 0.95,
    seed: int = 20261110,
) -> dict[str, Any]:
    """判据 J1/J2 的恢复实验：每个 α_true 档跑 ``reps`` 次，同时测 CI 覆盖率。

    输出每档：``alpha_true``（总体极限）、``mean_alpha_hat``、``bias``、
    ``mae``、``sd``、``ci_coverage``（总体真值是否落在 bootstrap CI 内）、
    ``mean_ci_halfwidth``；以及跨档汇总 ``max_abs_bias`` / ``min_coverage``。
    """
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    for target in targets:
        cal = calibrate_sigma(float(target), prior=prior,
                              biases=(0.0,) * n_raters)
        if not cal["reached"]:
            rows.append({"alpha_target": float(target), "status": "unreachable",
                         "detail": cal})
            continue
        sigma = cal["sigma"]
        true_alpha = float(cal["achieved_alpha"])
        est: list[float] = []
        cover = 0
        cover_pct = 0
        hws: list[float] = []
        hws_pct: list[float] = []
        for _ in range(int(reps)):
            x = simulate_sample(rng, n_units, prior=prior,
                                biases=(0.0,) * n_raters, sigma=sigma)
            a = stats.krippendorff_alpha(x, "ordinal")
            if not math.isfinite(a["alpha"]):
                continue
            est.append(a["alpha"])
            ci = stats.bootstrap_alpha_ci(
                x, "ordinal", n_boot=n_boot, conf=level,
                seed=int(rng.integers(1, 10**9)))
            if math.isfinite(ci.get("lo", float("nan"))):
                hws.append(ci["half_width"])
                cover += int(ci["lo"] <= true_alpha <= ci["hi"])
                # 同一批重采样上的百分位区间（方法敏感性，不额外花算力）
                pct = (ci.get("intervals") or {}).get("percentile")
                if pct:
                    hws_pct.append(pct["half_width"])
                    cover_pct += int(pct["lo"] <= true_alpha <= pct["hi"])
        arr = np.asarray(est)
        used = arr.size
        rows.append({
            "alpha_target": float(target), "alpha_true": true_alpha,
            "sigma": sigma, "mean_alpha_hat": float(arr.mean()) if used else float("nan"),
            "bias": float(arr.mean() - true_alpha) if used else float("nan"),
            "mae": float(np.abs(arr - true_alpha).mean()) if used else float("nan"),
            "sd_alpha_hat": float(arr.std(ddof=1)) if used > 1 else float("nan"),
            "ci_coverage": cover / used if used else float("nan"),
            "ci_coverage_percentile": cover_pct / used if used else float("nan"),
            "mean_ci_halfwidth_percentile": float(np.mean(hws_pct)) if hws_pct else float("nan"),
            "mean_ci_halfwidth": float(np.mean(hws)) if hws else float("nan"),
            "reps_used": used, "n_units": n_units, "n_raters": n_raters,
            "status": "ok",
        })
    ok = [r for r in rows if r.get("status") == "ok"]
    return {
        "rows": rows, "reps": int(reps), "n_units": n_units,
        "n_raters": n_raters, "n_boot": n_boot,
        "max_abs_bias": max((abs(r["bias"]) for r in ok), default=float("nan")),
        "min_coverage": min((r["ci_coverage"] for r in ok), default=float("nan")),
        "min_coverage_percentile": min((r.get("ci_coverage_percentile", float("nan")) for r in ok),
                                       default=float("nan")),
        "max_abs_mae": max((r["mae"] for r in ok), default=float("nan")),
        "status": "ok" if ok else "all_unreachable",
    }


# ─────────────── 判据 J5：注入 lenient/harsh 的 Δα 一致性 ───────────────

def injection_shift(
    base_alpha: float = 0.8,
    *,
    shift: float = 0.5,
    reps: int = 200,
    n_units: int = 120,
    prior: Sequence[float] = LEVEL_PRIOR,
    seed: int = 20261120,
) -> dict[str, Any]:
    """注入 lenient(+shift) / harsh(−shift) 判级者：实测 Δα vs 理论 Δα。

    设计：4 个判级者，注入后偏置 ``[0, +0.5, 0, −0.5]``（两个中性 + 一宽一严）。
    理论 Δα 用总体极限算（同一模型、同一 σ），实测 Δα 用**同随机数配对**样本
    （潜在等级与噪声都不变，只加偏置）→ Δα̂ 的抽样噪声大幅降低。
    """
    biases_base = (0.0, 0.0, 0.0, 0.0)
    biases_inj = (0.0, +shift, 0.0, -shift)
    cal = calibrate_sigma(float(base_alpha), prior=prior, biases=biases_base)
    if not cal["reached"]:
        return {"status": "unreachable", "detail": cal}
    sigma = cal["sigma"]
    a_base_pop = float(cal["achieved_alpha"])
    a_inj_pop = population_alpha(prior, biases_inj, sigma)
    d_theory = a_base_pop - a_inj_pop

    rng = np.random.default_rng(seed)
    d_hat: list[float] = []
    a_base_hat: list[float] = []
    a_inj_hat: list[float] = []
    for _ in range(int(reps)):
        latent = rng.choice(np.arange(1, 6), size=n_units,
                            p=np.asarray(prior, dtype=float) / float(np.sum(prior))).astype(float)
        noise = rng.normal(0.0, sigma, size=(n_units, 4))
        xb = simulate_sample(rng, n_units, prior=prior, biases=biases_base,
                             sigma=sigma, latent=latent, noise=noise)
        xi = simulate_sample(rng, n_units, prior=prior, biases=biases_inj,
                             sigma=sigma, latent=latent, noise=noise)
        ab = stats.krippendorff_alpha(xb, "ordinal")["alpha"]
        ai = stats.krippendorff_alpha(xi, "ordinal")["alpha"]
        if math.isfinite(ab) and math.isfinite(ai):
            a_base_hat.append(ab)
            a_inj_hat.append(ai)
            d_hat.append(ab - ai)
    arr = np.asarray(d_hat)
    used = arr.size
    mean_d = float(arr.mean()) if used else float("nan")
    rel_err = (abs(mean_d - d_theory) / abs(d_theory)) if d_theory else float("nan")
    return {
        "status": "ok" if used else "insufficient",
        "base_alpha_target": float(base_alpha), "sigma": sigma,
        "alpha_base_population": a_base_pop,
        "alpha_injected_population": a_inj_pop,
        "delta_alpha_theory": float(d_theory),
        "delta_alpha_hat_mean": mean_d,
        "delta_alpha_hat_sd": float(arr.std(ddof=1)) if used > 1 else float("nan"),
        "alpha_base_hat_mean": float(np.mean(a_base_hat)) if used else float("nan"),
        "alpha_injected_hat_mean": float(np.mean(a_inj_hat)) if used else float("nan"),
        "relative_error": float(rel_err),
        "biases": list(biases_inj), "shift_levels": float(shift),
        "reps_used": used, "n_units": n_units, "paired": True,
    }


def prior_sensitivity(
    *,
    n_units: int = 120,
    n_raters: int = 4,
    reps: int = 120,
    seed: int = 20261130,
) -> list[dict[str, Any]]:
    """α_true 的边际依赖：同一 σ 下换先验，总体 α 与样本恢复随之变化。

    这是本夹具的**已知局限**：序数 α 是可分解度量，其期望分歧由边际决定。
    报告里用它量化「换一批真实数据、边际变了，α 的可比性」。
    """
    out: list[dict[str, Any]] = []
    rng = np.random.default_rng(seed)
    for name, prior in (("symmetric", LEVEL_PRIOR),
                        ("uniform", LEVEL_PRIOR_UNIFORM),
                        ("skewed", LEVEL_PRIOR_SKEWED)):
        sigma = 0.8
        pop = population_alpha(prior, (0.0,) * n_raters, sigma)
        est = [stats.krippendorff_alpha(
            simulate_sample(rng, n_units, prior=prior, biases=(0.0,) * n_raters, sigma=sigma),
            "ordinal")["alpha"] for _ in range(int(reps))]
        arr = np.asarray([v for v in est if math.isfinite(v)])
        out.append({"prior": name, "sigma": sigma, "alpha_population": pop,
                    "mean_alpha_hat": float(arr.mean()) if arr.size else float("nan"),
                    "bias": float(arr.mean() - pop) if arr.size else float("nan")})
    return out


__all__ = [
    "LEVEL_PRIOR", "LEVEL_PRIOR_UNIFORM", "LEVEL_PRIOR_SKEWED", "LEVELS",
    "rating_probs", "population_coincidence", "alpha_from_coincidence",
    "population_alpha", "calibrate_sigma", "simulate_sample",
    "alpha_recovery", "injection_shift", "prior_sensitivity",
]