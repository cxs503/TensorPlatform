"""m1872 · PCE/Sobol 解析基准工件生成器（数字全部程序化注入报告，零手抄）。

产出：
  artifacts/ice_uq_sobol_m1872/bench.json      —— 全部判据数值
  artifacts/ice_uq_sobol_m1872/fig1_sobol_analytic_vs_computed.png
  artifacts/ice_uq_sobol_m1872/fig2_convergence.png
  artifacts/ice_uq_sobol_m1872/fig3_quadrature_exactness.png
  artifacts/ice_uq_sobol_m1872/fig4_mc_noise_floor.png

用法：python3 tools/verify/pce_sobol_bench_m1872.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from services.ice_resistance_uq import (  # noqa: E402
    SHIP_TRIAL_DATA,
    UncertainInput,
    _gauss_hermite_nodes_weights,
    build_pce,
    lindqvist_ice_resistance,
    pce_statistics,
    smolyak_sparse_grid,
    sobol_indices,
)

OUT = ROOT / "artifacts" / "ice_uq_sobol_m1872"
OUT.mkdir(parents=True, exist_ok=True)
SEED = 20261101
plt.rcParams.update({"figure.dpi": 130, "font.size": 9})


def gauss_moment(k: int) -> float:
    if k % 2:
        return 0.0
    return float(math.prod(range(1, k, 2))) if k > 0 else 1.0


def compositions(total: int, dim: int):
    if dim == 1:
        yield (total,)
        return
    for i in range(total + 1):
        for rest in compositions(total - i, dim - 1):
            yield (i,) + rest


def mk(name, mean, std):
    return UncertainInput(name=name, nominal=mean, std_relative=std / abs(mean))


def real_case(ship_name):
    trial = SHIP_TRIAL_DATA[ship_name]
    ship = trial["params"]
    inputs = [
        UncertainInput("ice_strength_MPa", trial["nominal_ice_strength_MPa"], 0.20),
        UncertainInput("ice_thickness_m", trial["nominal_ice_thickness_m"], 0.15),
        UncertainInput("friction_coeff", trial["nominal_friction_coeff"], 0.25),
        UncertainInput("ship_speed_ms", trial["nominal_speed_ms"], 0.10),
    ]
    model = lambda s, t, f, v: lindqvist_ice_resistance(ship, t, s, f, v)
    return inputs, model


# ---------------------------------------------------------------------------
# A. Gauss-Hermite 与 Smolyak 的精确阶（解析矩）
# ---------------------------------------------------------------------------
quad = {}
for n in (3, 5, 7, 9, 13, 20):
    nodes, weights = _gauss_hermite_nodes_weights(n)
    worst = 0.0
    for k in range(0, 2 * n):
        scale = float(np.sum(np.abs(weights * nodes ** k)))
        worst = max(worst, abs(float(np.sum(weights * nodes ** k)) - gauss_moment(k))
                    / max(1.0, abs(gauss_moment(k)), scale))
    k_fail = 2 * n
    rel_fail = abs(float(np.sum(weights * nodes ** k_fail)) - gauss_moment(k_fail)) / gauss_moment(k_fail)
    quad[f"gh_n{n}"] = {"worst_rel_err_deg<=2n-1": worst, f"rel_err_deg{2*n}": rel_fail}

smolyak = {}
for level, dim in ((2, 3), (3, 3), (4, 3), (3, 5), (4, 5), (5, 5)):
    nodes, weights = smolyak_sparse_grid(level, dim)
    errs = {}
    for total in range(0, 2 * level + 2):
        worst = 0.0
        for alpha in compositions(total, dim):
            col = np.ones(len(nodes))
            for k, a in enumerate(alpha):
                col = col * nodes[:, k] ** a
            exact = float(np.prod([gauss_moment(a) for a in alpha]))
            worst = max(worst, abs(float(np.sum(weights * col)) - exact)
                        / max(1.0, abs(exact)))
        errs[total] = worst
    smolyak[f"L{level}_d{dim}"] = {"n_nodes": len(nodes), "err_by_total_degree": errs,
                                   "exact_upto_2L_minus_1": max(errs[t] for t in range(2 * level))}

# ---------------------------------------------------------------------------
# B. 解析 Sobol 基准（多项式测试函数家族）
# ---------------------------------------------------------------------------
analytic = []

# B1 可加线性
ins = [mk("x1", 5.0, 0.5), mk("x2", 2.0, 0.4), mk("x3", -1.0, 0.3)]
a = [3.0, -2.0, 1.0]
D = sum(a[i] ** 2 * ins[i].std ** 2 for i in range(3))
S = {ins[i].name: a[i] ** 2 * ins[i].std ** 2 / D for i in range(3)}
pce = build_pce(lambda x1, x2, x3: a[0] * x1 + a[1] * x2 + a[2] * x3, ins, 3, 1)
sb = sobol_indices(pce)
analytic.append({"case": "additive_linear_deg1", "inputs": {i.name: [i.mean, i.std] for i in ins},
                 "exact_S": S, "pce_S": {k: float(v) for k, v in sb["first_order"].items()},
                 "max_abs_err": max(abs(sb["first_order"][k] - S[k]) for k in S)})

# B2 交互（非零均值）
ins2 = [mk("x1", 2.0, 0.3), mk("x2", 1.0, 0.5), mk("x3", 1.0, 0.2)]
s1, s2, s3 = (i.std for i in ins2)
m1, m2, m3 = (i.mean for i in ins2)
D2 = s1 ** 2 + s2 ** 2 * s3 ** 2 + m2 ** 2 * s3 ** 2 + m3 ** 2 * s2 ** 2
S2 = {"x1": s1 ** 2 / D2, "x2": m3 ** 2 * s2 ** 2 / D2, "x3": m2 ** 2 * s3 ** 2 / D2}
ST2 = {"x1": S2["x1"], "x2": (s2 ** 2 * s3 ** 2 + m3 ** 2 * s2 ** 2) / D2,
       "x3": (s2 ** 2 * s3 ** 2 + m2 ** 2 * s3 ** 2) / D2}
pce2 = build_pce(lambda x1, x2, x3: x1 + x2 * x3, ins2, 3, 2)
sb2 = sobol_indices(pce2)
analytic.append({"case": "interaction_deg2", "exact_S": S2, "pce_S": {k: float(v) for k, v in sb2["first_order"].items()},
                 "exact_ST": ST2, "pce_ST": {k: float(v) for k, v in sb2["total_order"].items()},
                 "max_abs_err": max(max(abs(sb2["first_order"][k] - S2[k]) for k in S2),
                                    max(abs(sb2["total_order"][k] - ST2[k]) for k in ST2))})

# B3 4 输入尺度混合（二次）
ins3 = [mk("a", 2.0, 0.3), mk("b", 1.0, 0.4), mk("c", 1.5, 0.2), mk("d", 3.0, 0.6)]
sa, sb_, sc, sd = (i.std for i in ins3)
mb, mc_, md = ins3[1].mean, ins3[2].mean, ins3[3].mean
var_bc = sb_ ** 2 * sc ** 2 + mc_ ** 2 * sb_ ** 2 + mb ** 2 * sc ** 2
var_d2 = 2 * sd ** 4 + 4 * md ** 2 * sd ** 2
D3 = 1.5 ** 2 * sa ** 2 + 0.8 ** 2 * var_bc + 0.3 ** 2 * var_d2
V_b, V_c = 0.8 ** 2 * mc_ ** 2 * sb_ ** 2, 0.8 ** 2 * mb ** 2 * sc ** 2
base = 1.5 ** 2 * sa ** 2 + 0.3 ** 2 * var_d2
S3 = {"a": 1.5 ** 2 * sa ** 2 / D3, "b": V_b / D3, "c": V_c / D3, "d": 0.3 ** 2 * var_d2 / D3}
ST3 = {"a": S3["a"], "b": 1.0 - (base + V_c) / D3, "c": 1.0 - (base + V_b) / D3, "d": S3["d"]}
pce3 = build_pce(lambda a_, b_, c_, d_: 1.5 * a_ + 0.8 * b_ * c_ - 0.3 * d_ ** 2, ins3, 3, 2)
sb3 = sobol_indices(pce3)
st3 = pce_statistics(pce3)
analytic.append({"case": "scaled_mixed_quadratic_4d", "exact_var": D3, "pce_var": float(st3["variance"]),
                 "exact_S": S3, "pce_S": {k: float(v) for k, v in sb3["first_order"].items()},
                 "exact_ST": ST3, "pce_ST": {k: float(v) for k, v in sb3["total_order"].items()},
                 "max_abs_err": max(max(abs(sb3["first_order"][k] - S3[k]) for k in S3),
                                    max(abs(sb3["total_order"][k] - ST3[k]) for k in ST3))})

# B4 立方 + 求积充要判据 p+P <= 2L-1
s_, mu_ = 0.4, 1.0
cub_in = [mk("x1", mu_, s_), mk("x2", 1.0, 0.5)]
var_cub = (mu_ ** 6 + 15 * mu_ ** 4 * s_ ** 2 + 45 * mu_ ** 2 * s_ ** 4 + 15 * s_ ** 6) \
    - (mu_ ** 3 + 3 * mu_ * s_ ** 2) ** 2
cubic = {}
for L, P in ((3, 3), (4, 3), (5, 3), (3, 4), (4, 4)):
    st_ = pce_statistics(build_pce(lambda x1, x2: x1 ** 3, cub_in, L, P))
    cubic[f"L{L}_P{P}"] = {"exact_var": var_cub, "pce_var": float(st_["variance"]),
                           "rel_err": abs(st_["variance"] - var_cub) / var_cub,
                           "exact_rule_satisfied": 3 + P <= 2 * L - 1}

# ---------------------------------------------------------------------------
# C. 真实模型：谱收敛 + MC 噪声底 + 截断不一致
# ---------------------------------------------------------------------------
bounds = [(0.3, None), (0.1, None), (0.02, 0.5), (0.5, None)]
conv, noise = {}, {}
for ship_name in sorted(SHIP_TRIAL_DATA):
    inputs, model = real_case(ship_name)
    rows = {}
    for L, P in ((2, 3), (3, 3), (4, 3), (5, 3), (6, 3), (5, 4)):
        st_ = pce_statistics(build_pce(model, inputs, L, P))
        sb_ = sobol_indices(build_pce(model, inputs, L, P))
        rows[f"L{L}_P{P}"] = {"n_eval": build_pce(model, inputs, L, P)["n_evaluations"],
                              "mean": float(st_["mean"]), "std": float(st_["std"]),
                              "S": {k: float(v) for k, v in sb_["first_order"].items()},
                              "ST": {k: float(v) for k, v in sb_["total_order"].items()}}
    ref = rows["L6_P3"]
    for key, row in rows.items():
        row["rel_dmean"] = abs(row["mean"] - ref["mean"]) / ref["mean"]
        row["rel_dstd"] = abs(row["std"] - ref["std"]) / ref["std"]
        row["max_dS"] = max(abs(row["S"][k] - ref["S"][k]) for k in ref["S"])
        row["max_dST"] = max(abs(row["ST"][k] - ref["ST"][k]) for k in ref["ST"])
    conv[ship_name] = rows

    rng = np.random.default_rng(SEED)
    n = 200_000
    z = rng.standard_normal((n, 4))
    x = np.column_stack([inp.mean + inp.std * z[:, d] for d, inp in enumerate(inputs)])
    xc = x.copy()
    clamped = 0
    for d, (lo, hi) in enumerate(bounds):
        col = xc[:, d].copy()
        if lo is not None:
            col = np.maximum(col, lo)
        if hi is not None:
            col = np.minimum(col, hi)
        clamped += int(np.sum(col != xc[:, d]))
        xc[:, d] = col
    y = np.array([model(*row) for row in x])
    yc = np.array([model(*row) for row in xc])
    pce = build_pce(model, inputs, 3, 3)
    st_ = pce_statistics(pce)
    noise[ship_name] = {
        "mc_mean": float(y.mean()), "mc_std": float(y.std(ddof=1)),
        "se_mean": float(y.std(ddof=1) / math.sqrt(n)),
        "se_std": float(y.std(ddof=1) / math.sqrt(2 * n)),
        "pce_mean": float(st_["mean"]), "pce_std": float(st_["std"]),
        "gap_over_se_mean": abs(st_["mean"] - y.mean()) / (y.std(ddof=1) / math.sqrt(n)),
        "gap_over_se_std": abs(st_["std"] - y.std(ddof=1)) / (y.std(ddof=1) / math.sqrt(2 * n)),
        "clamped_frac": clamped / n,
        "clamp_rel_dmean": abs(y.mean() - yc.mean()) / yc.mean(),
        "clamp_rel_dstd": abs(y.std(ddof=1) - yc.std(ddof=1)) / yc.std(ddof=1),
    }

bench = {"quadrature_gauss_hermite": quad, "quadrature_smolyak": smolyak,
         "analytic_sobol": analytic, "cubic_exactness_rule": cubic,
         "real_model_convergence": conv, "mc_noise_floor": noise}
(OUT / "bench.json").write_text(json.dumps(bench, indent=1, ensure_ascii=False))

# ---------------------------------------------------------------------------
# 图
# ---------------------------------------------------------------------------
# fig1: 解析 vs 计算 Sobol
fig, axes = plt.subplots(1, 3, figsize=(12, 3.6))
for ax, item, title in zip(axes, analytic[:3],
                           ["(a) additive deg-1", "(b) interaction deg-2", "(c) mixed quadratic 4-D"]):
    names = list(item["exact_S"].keys())
    ex = [item["exact_S"][k] for k in names]
    got = [item["pce_S"][k] for k in names]
    xx = np.arange(len(names))
    ax.bar(xx - 0.2, ex, 0.4, label="analytic", color="#3b6ea5")
    ax.bar(xx + 0.2, got, 0.4, label="PCE", color="#d9822b")
    ax.set_xticks(xx)
    ax.set_xticklabels(names, rotation=20)
    ax.set_ylabel("first-order Sobol $S_i$")
    ax.set_title(f"{title}\nmax|err|={item['max_abs_err']:.1e}")
    ax.legend(fontsize=7)
axes[0].set_ylim(0, 1)
fig.suptitle("PCE Sobol indices vs closed-form analytic values (m1872)", fontsize=10)
fig.tight_layout()
fig.savefig(OUT / "fig1_sobol_analytic_vs_computed.png")
plt.close(fig)

# fig2: 真实模型谱收敛
fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
levels = [2, 3, 4, 5, 6]
for ship in conv:
    axes[0].plot(levels, [conv[ship][f"L{L}_P3"]["std"] for L in levels], "o-", label=ship)
    axes[1].semilogy(levels, [max(conv[ship][f"L{L}_P3"]["rel_dstd"], 1e-16) for L in levels],
                     "o-", label=ship)
axes[0].set_xlabel("Smolyak level L (P=3)")
axes[0].set_ylabel("PCE std of ice resistance [kN]")
axes[0].set_title("(a) std convergence (3 ships)")
axes[0].legend(fontsize=7)
axes[1].axhline(0.03, color="r", ls="--", lw=1, label="3% criterion")
axes[1].set_xlabel("Smolyak level L")
axes[1].set_ylabel("rel. diff vs L=6")
axes[1].set_title("(b) convergence error vs converged reference")
axes[1].legend(fontsize=7)
fig.tight_layout()
fig.savefig(OUT / "fig2_convergence.png")
plt.close(fig)

# fig3: 求积精确域 2L-1
fig, ax = plt.subplots(figsize=(6.2, 3.6))
keys = [k for k in smolyak if k.endswith("d5")]
tmax = max(2 * int(k[1]) + 2 for k in keys)
mat = np.full((len(keys), tmax), np.nan)
for i, k in enumerate(keys):
    for t in range(0, 2 * int(k[1]) + 2):
        mat[i, t] = max(smolyak[k]["err_by_total_degree"][t], 1e-17)
mat = np.ma.masked_invalid(np.log10(mat))
im = ax.imshow(mat, aspect="auto", cmap="viridis", origin="lower")
ax.set_yticks(range(len(keys)))
ax.set_yticklabels([f"L={k[1]} (exact≤{2*int(k[1])-1})" for k in keys])
ax.set_xlabel("total polynomial degree of monomial")
ax.set_title("Smolyak quadrature: log10 relative moment error (d=5)")
for i, k in enumerate(keys):
    L = int(k[1])
    ax.plot([2 * L - 1, 2 * L - 1], [i - 0.4, i + 0.4], "r-", lw=2)
fig.colorbar(im, ax=ax, label="log10 rel err")
fig.tight_layout()
fig.savefig(OUT / "fig3_quadrature_exactness.png")
plt.close(fig)

# fig4: MC 噪声底
fig, ax = plt.subplots(figsize=(6.4, 3.6))
ships = list(noise)
g1 = [noise[s]["gap_over_se_mean"] for s in ships]
g2 = [noise[s]["gap_over_se_std"] for s in ships]
xx = np.arange(len(ships))
ax.bar(xx - 0.2, g1, 0.4, label="|PCE−MC|/se_mean", color="#3b6ea5")
ax.bar(xx + 0.2, g2, 0.4, label="|PCE−MC|/se_std", color="#d9822b")
ax.axhline(4, color="r", ls="--", lw=1, label="4σ noise band")
ax.set_xticks(xx)
ax.set_xticklabels(ships, rotation=15)
ax.set_ylabel("gap / analytic MC standard error")
ax.set_title("PCE vs MC gap is sampling noise (N=2e5)")
ax.legend(fontsize=7)
fig.tight_layout()
fig.savefig(OUT / "fig4_mc_noise_floor.png")
plt.close(fig)

print(json.dumps({
    "analytic_max_err": [a["max_abs_err"] for a in analytic],
    "cubic": cubic,
    "convergence_rel_dstd_L3": {s: conv[s]["L3_P3"]["rel_dstd"] for s in conv},
    "noise": {s: [noise[s]["gap_over_se_mean"], noise[s]["gap_over_se_std"], noise[s]["clamped_frac"]] for s in noise},
}, indent=1))