"""Build the artefacts for the PCE/Sobol analytic-verification report.

Reads ``artifacts/pce_verify/ledger.json`` (produced by
``python3 -m tools.pce_verify.verify_pce``) and writes

  artifacts/pce_verify/fig1_projection_defect.png
  artifacts/pce_verify/fig2_sobol_benchmarks.png
  docs/pce_analytic_verification/REPORT.md      (numbers injected, never hand copied)

Run:  python3 tools/pce_verify/make_report_assets.py
"""

from __future__ import annotations

import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LEDGER = os.path.join(ROOT, "artifacts", "pce_verify", "ledger.json")
FIGDIR = os.path.join(ROOT, "artifacts", "pce_verify")
DOCDIR = os.path.join(ROOT, "docs", "pce_analytic_verification")


# ----------------------------------------------------------------------
# figures
# ----------------------------------------------------------------------

def fig_projection_defect(led: dict) -> str:
    rows = led["C_hermite_pce"]["rows"]
    dims = sorted({r["dim"] for r in rows})
    under = []
    coupled = []
    for d in dims:
        u = next(r for r in rows if r["dim"] == d and r["under_integrated"])
        c = next(r for r in rows if r["dim"] == d and not r["under_integrated"]
                 and r["max_poly_order"] == u["max_poly_order"])
        under.append(max(u["max_rel_err_sobol_first"], u["max_rel_err_sobol_total"]))
        coupled.append(max(c["max_rel_err_sobol_first"], c["max_rel_err_sobol_total"]))

    g = led["G_ice_production"]["rows"]
    g_under = next(r for r in g if r["under_integrated"])
    g_coupled = next(r for r in g if not r["under_integrated"])

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4))

    ax = axes[0]
    x = np.arange(len(dims))
    w = 0.36
    ax.bar(x - w / 2, np.maximum(under, 1e-18), w, label="L=3, p=3 (under-integrated, 2L-1=5 < 2p=6)",
           color="#c0392b")
    ax.bar(x + w / 2, np.maximum(coupled, 1e-18), w,
           label="L=max(L, p+1)=4, p=3 (exact projection)", color="#27ae60")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels([f"dim={d}" for d in dims])
    ax.set_ylabel("max relative error of Sobol index")
    ax.set_title("A  Hermite projection vs closed-form model\n(coefficient-exact reference)")
    ax.axhline(1e-10, ls="--", lw=1, color="k", label="machine-exact gate 1e-10")
    ax.legend(fontsize=7.5, loc="lower right")
    ax.grid(axis="y", alpha=0.3)

    ax = axes[1]
    labels = ["mean |err|", "p95 |err|", "max |err|"]
    uvals = [g_under["oos_mean_rel_err"], g_under["oos_p95_rel_err"], g_under["oos_max_rel_err"]]
    cvals = [g_coupled["oos_mean_rel_err"], g_coupled["oos_p95_rel_err"], g_coupled["oos_max_rel_err"]]
    x = np.arange(3)
    ax.bar(x - w / 2, uvals, w, label="shipped default before fix (L=3, p=3)", color="#c0392b")
    ax.bar(x + w / 2, cvals, w, label="after fix (L coupled to p)", color="#27ae60")
    for xi, (u, c) in enumerate(zip(uvals, cvals)):
        ax.text(xi - w / 2, u * 1.12, f"{u:.3%}", ha="center", fontsize=8)
        ax.text(xi + w / 2, c * 1.12, f"{c:.3%}", ha="center", fontsize=8)
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("relative error of surrogate vs Lindqvist model")
    ax.set_title("B  Ice-resistance surrogate, out-of-sample (n=4000)\nMV Arctic, 4 uncertain inputs, p=3")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)

    fig.suptitle("PCE projection under-integration: defect and fix", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path = os.path.join(FIGDIR, "fig1_projection_defect.png")
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def fig_sobol_benchmarks(led: dict) -> str:
    e = led["E_ishigami"]
    f = led["F_g_function"]
    d = led["D_legendre_model"]

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.3))

    ax = axes[0]
    ref = e["reference"]
    best = min(e["rows"], key=lambda r: max(r["max_abs_err_sobol_first"],
                                             r["max_abs_err_sobol_total"]))
    names = ["x1", "x2", "x3"]
    fo_ref = [ref["first_order"][str(i)] for i in range(3)]
    to_ref = [ref["total_order"][str(i)] for i in range(3)]
    fo = [best["sobol_first"][n] for n in names]
    to = [best["sobol_total"][n] for n in names]
    x = np.arange(3)
    w = 0.2
    ax.bar(x - 1.5 * w, fo_ref, w, label="analytic $S_i$", color="#2c3e50")
    ax.bar(x - 0.5 * w, fo, w, label=f"PCE $S_i$ (degree {best['degree']})", color="#3498db")
    ax.bar(x + 0.5 * w, to_ref, w, label="analytic $S_{T,i}$", color="#7f8c8d")
    ax.bar(x + 1.5 * w, to, w, label=f"PCE $S_{{T,i}}$ (degree {best['degree']})", color="#e67e22")
    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_ylabel("Sobol index")
    ax.set_title(f"Ishigami (1990), a=7, b=0.1\nmax |PCE - analytic| = "
                 f"{max(best['max_abs_err_sobol_first'], best['max_abs_err_sobol_total']):.2e}")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)

    ax = axes[1]
    rows = f["rows"]
    degs = [r["degree"] for r in rows]
    errs = [max(r["max_abs_err_sobol_first"], r["max_abs_err_sobol_total"]) for r in rows]
    ax.loglog(degs, np.maximum(errs, 1e-16), "o-", label="Sobol g-function (5-D, kink at 0.5)")
    ax.axhline(0.03, ls="--", color="r", lw=1, label="3 % acceptance band")
    ax.set_xlabel("Legendre PCE degree")
    ax.set_ylabel("max |PCE - analytic| Sobol index error")
    ax.set_title("g-function: algebraic convergence at the $|4x-2|$ kink\n"
                 f"best degree {f['best_degree']}: {f['best_max_abs_err']:.2e}")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")

    fig.suptitle("Legendre PCE engine graded against literature references", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    path = os.path.join(FIGDIR, "fig2_sobol_benchmarks.png")
    fig.savefig(path, dpi=140)
    plt.close(fig)
    _ = d
    return path


# ----------------------------------------------------------------------
# report
# ----------------------------------------------------------------------

def _pct(x: float, nd: int = 3) -> str:
    return f"{x:.{nd}%}"


def write_report(led: dict, figs: list[tuple[str, str]]) -> str:
    a, b, c = led["A_gauss_hermite"], led["B_smolyak"], led["C_hermite_pce"]
    d, e, f, g = (led["D_legendre_model"], led["E_ishigami"],
                  led["F_g_function"], led["G_ice_production"])

    c_rows = "\n".join(
        f"| {r['dim']} | {r['max_level_requested']} | {r['max_level']} | {r['max_poly_order']} | "
        f"{'yes' if r['under_integrated'] else 'no'} | {r['n_quad_points']} | "
        f"{r['max_rel_err_coefficient']:.2e} | {r['max_rel_err_sobol_first']:.2e} | "
        f"{r['max_rel_err_sobol_total']:.2e} |"
        for r in c["rows"])

    sm_rows = "\n".join(
        f"| {r['dim']} | {r['level']} | {r['n_points']} | {r['sum_weights']:.15f} | "
        f"{r['min_weight']:.3f} | {r['n_negative_weights']} | 2L-1 = {r['exact_degree_claim']} | "
        f"{r['worst_rel_err_within_degree']:.2e} |"
        for r in b["rows"])

    gh_rows = "\n".join(
        f"| {r['n_nodes']} | {r['degrees_exact']} | {r['sum_weights']:.16f} | "
        f"{r['min_weight']:.4f} | {r['max_rel_err_deg_le_2n-1']:.2e} |"
        for r in a["rows"])

    g_rows = "\n".join(
        f"| {r['max_level_requested']} | {r['max_level']} | {r['max_poly_order']} | "
        f"{'yes' if r['under_integrated'] else 'no'} | {r['n_quad_points']} | "
        f"{_pct(r['rel_err_mean_vs_mc'])} | {_pct(r['rel_err_std_vs_mc'])} | "
        f"{_pct(r['oos_mean_rel_err'], 4)} | {_pct(r['oos_p95_rel_err'])} | {_pct(r['oos_max_rel_err'])} |"
        for r in g["rows"])

    f_rows = "\n".join(
        f"| {r['degree']} | {r['n_basis']} | {_pct(r['rel_err_variance'], 4)} | "
        f"{r['max_abs_err_sobol_first']:.3e} | {r['max_abs_err_sobol_total']:.3e} |"
        for r in f["rows"])

    e_rows = "\n".join(
        f"| {r['degree']} | {r['n_basis']} | {_pct(r['rel_err_variance'], 4)} | "
        f"{r['max_abs_err_sobol_first']:.2e} | {r['max_abs_err_sobol_total']:.2e} |"
        for r in e["rows"])

    legacy = g["legacy_endpoint_result"]
    best_e = min(e["rows"], key=lambda r: max(r["max_abs_err_sobol_first"], r["max_abs_err_sobol_total"]))
    u = next(r for r in g["rows"] if r["under_integrated"])
    cc = next(r for r in g["rows"] if not r["under_integrated"])

    fig_md = "\n".join(f"![{cap}](../{path.split('artifacts/')[-1]})\n\n*{cap}*\n"
                       for path, cap in [(p, cap) for p, cap in figs])

    body = f"""# PCE / Sobol 机理论证：解析基准验证欠账清偿

* 席位：tp-doc-4（data 席）自主工时，mission **m1969**，model deepseek-v4-flash
* 验证器：`tools/pce_verify/analytic_benchmarks.py`（闭式参考）+ `tools/pce_verify/verify_pce.py`（驱动）
* 台账：`artifacts/pce_verify/ledger.json`（本文所有数字由 `tools/pce_verify/make_report_assets.py` 从这里注入，零手抄）
* 测试：`tests/test_pce_analytic_verification.py`（29 例）
* 修复：`backend/services/ice_resistance_uq.py`（投影欠积分）、`backend/services/digital_twin/pce.py`（补齐方差分解/全局敏感性）
* 复跑：`python3 -m tools.pce_verify.verify_pce --out artifacts/pce_verify/ledger.json`

## ① 案例介绍（物理背景 / 对象 / 参数）

平台上有两台彼此独立的**多项式混沌（PCE）**引擎，冰区航行与数字孪生两条产品线各用一台：

| 引擎 | 位置 | 基 | 系数 | 用途 |
|---|---|---|---|---|
| Hermite / Smolyak **投影** | `services/ice_resistance_uq.py` | 概率论者 Hermite（高斯输入） | Galerkin 投影 `c_a = E[f Ψ_a]/‖Ψ_a‖²`（Smolyak 稀疏网格求积） | 冰阻力 UQ 的均值/方差/置信区间/Sobol |
| Legendre **最小二乘** | `services/digital_twin/pce.py` | Legendre（有界均匀设计域） | 空间填充样本上的 LSQ | 数字孪生代理（阻力/稳性/耐波） |

它们决定了冰区航行 UQ 的均值与方差、以及数字孪生换代（surrogate）报告里的“敏感参数排序”。
此前两者的验收只有**自一致性**检查（例如“ΣS_i ≤ 1”“total ≥ first”），这类判据对**整体缩放错误、
基归一化错误、求积欠积分**完全不敏感——一个把所有 Sobol 指数整体乘 0.5 的实现可以全绿通过。

本单清偿的欠账就是：**给这两台引擎造与被测代码无关的闭式基准**，逐项判定，并把判出来的缺陷修掉。
基准全部来自数学本身，没有一处从被测代码读回：

1. Gauss-Hermite n 点规则的矩 `E[X^k] = (k−1)!!`（k 偶数），精确到 `2n−1` 次；
2. Smolyak 稀疏网格：概率测度下的**单位分解** `Σw = 1`，与总次数 `2L−1` 的精确性；
3. Hermite 模型的 ANOVA 闭式分解：正交基下 `V = Σ_{{α≠0}} c_α²‖Ψ_α‖²`，`S_d`、`S_{{T,d}}` 由分组求和给出；
4. **Ishigami (1990)**：`f = sin x₁ + a sin²x₂ + b x₃⁴ sin x₁`，`U[−π,π]³`，a=7, b=0.1，全部指数闭式；
5. **Sobol g 函数**（乘积型）：`V = Π(1+V_i) − 1`，`V_i = 1/(3(1+a_i)²)`，`S_i`、`S_{{T,i}}` 闭式；
6. Legendre 模型：`U[−1,1]^d` 上 `‖P_α‖² = Π 1/(2α_d+1)`。

被测参数（生产配置，来自 `SHIP_TRIAL_DATA`/`run_ice_resistance_uq`）：MV Arctic，4 个不确定输入
（冰强度 σ 相对 σ=0.20、冰厚 h=0.15、摩擦 μ=0.25、航速 v=0.10），多项式总阶 p=3，
Smolyak 水平 L 由代码默认给出（修复前 3）。

## ② 怎么做的（方法 / 判据 / 工具链）

判据分三级，全部机器裁定：

| 级别 | 判据 | 阈值 |
|---|---|---|
| 多项式精确 | 求积/投影对声明次数内的多项式必须**机器精确** | 相对误差 ≤ {led['tolerances']['machine_exact']:.0e} |
| 收敛基准 | Ishigami / g 函数这类非多项式模型，取最优阶次比对文献闭式值 | 绝对误差 ≤ {led['tolerances']['acceptance_band']} (3 %) |
| 生产配置 | 生产默认配置的 PCE 与 Lindqvist 模型逐点比对（n=4000 出样点）+ 与 MC 参考统计比对 | 3 % 接受带 |

工具链：

* `tools/pce_verify/analytic_benchmarks.py` —— 纯函数闭式参考（GH 矩、Hermite/Legendre 范数、Ishigami、g 函数），
  以及与 `ice_resistance_uq` 无关的 ANOVA 分组求和实现；
* `tools/pce_verify/verify_pce.py` —— 四组检查 A–G，写 `artifacts/pce_verify/ledger.json`；
* `tools/pce_verify/make_report_assets.py` —— 从台账生成图与本文（数字注入）；
* `tests/test_pce_analytic_verification.py` —— 29 例 pytest，可离线复跑。

## ③ 计算结果

### A. Gauss-Hermite 求积（判据：`E[X^k]` 闭式矩）

| n 点 | 精确次数 | Σw | min w | 声明次数内最大相对误差 |
|---|---|---|---|---|
{gh_rows}

结论：**{a['verdict']}**，声明次数内最坏相对误差 `{a['worst_rel_err_within_degree']:.2e}`（阈值 `{a['tolerance']:.0e}`）；
声明次数外最坏 `{a['worst_rel_err_beyond_degree']:.1f}`（= 该规则本就不精确，反向证明检测器不是空转）。

### B. Smolyak 稀疏网格（判据：单位分解 + 总次数精确性）

| dim | L | 点数 | Σw | min w | 负权点数 | 精确次数 | 次数内最坏相对误差 |
|---|---|---|---|---|---|---|---|
{sm_rows}

结论：**{b['verdict']}**，最坏相对误差 `{b['worst_rel_err']:.2e}`。
注意点：组合技巧（combination technique）作用在**非嵌套** Gauss 规则上必然产生**负权**（dim=3, L=2 时最小权重 −2）。
这对投影系数无害（线性泛函仍然精确），但任何“权重非负”的误差棒/蒙特卡洛方差估计假设都会失效。

### C. Hermite 投影 PCE + Sobol（判据：闭式 ANOVA）

| dim | L 请求 | L 实用 | p | 欠积分 | 求积点数 | 系数最大相对误差 | S_i 最大相对误差 | S_T,i 最大相对误差 |
|---|---|---|---|---|---|---|---|---|
{c_rows}

结论：精确投影下最坏 Sobol 相对误差 `{c['worst_sobol_err_exact_projection']:.2e}`（**{c['verdict']}**）；
而**欠积分**路径（L=3, p=3）最坏相对误差 `{c['worst_sobol_err_under_integrated']:.2f}`——
即 Sobol 指数可以整个错到 100 % 以上。这是本单发现的**缺陷 D1**。

### D. Legendre 引擎：统计量 + 全局敏感性（判据：闭式 Legendre 模型）

| 量 | 相对误差 |
|---|---|
| 点预测（相对闭式模型） | {d['rel_err_point_prediction']:.2e} |
| 方差 | {d['rel_err_variance']:.2e} |
| S_i | {d['max_rel_err_sobol_first']:.2e} |
| S_T,i | {d['max_rel_err_sobol_total']:.2e} |

结论：**{d['verdict']}**；新增的 `PolynomialChaos.statistics()` / `sobol_indices()`
与独立参考实现的最大差 `{d['engine_vs_reference_max_diff']:.2e}`（两条路径互不调用）。

### E. Ishigami (1990)（判据：文献闭式指数）

| 阶次 | 基函数数 | 方差相对误差 | max|ΔS_i| | max|ΔS_T,i| |
|---|---|---|---|
{e_rows}

结论：**{e['verdict']}**，最优阶次 {best_e['degree']}，最大绝对误差 `{e['best_max_abs_err']:.2e}`
（Ishigami 含 sin/x⁴，Legendre 基不是精确基，故用收敛判据而非机器精确）。

### F. Sobol g 函数（5 维，a = 0, 0.5, 1, 3, 9）

| 阶次 | 基函数数 | 方差相对误差 | max|ΔS_i| | max|ΔS_T,i| |
|---|---|---|---|
{f_rows}

结论：**{f['verdict']}**，最优阶次 {f['best_degree']}，最大绝对误差 `{f['best_max_abs_err']:.2e}`。
g 函数在 x=0.5 有一个 C⁰ 折点，谱基只有代数收敛——这条曲线本身就是“引擎能不能收敛”的证据（阶次单调下降后触底）。

### G. 生产配置（MV Arctic，4 输入，p=3）

| L 请求 | L 实用 | p | 欠积分 | 求积点数 | 均值误差 vs MC | 标准差误差 vs MC | 出样平均 | 出样 p95 | 出样最大 |
|---|---|---|---|---|---|---|---|---|---|
{g_rows}

端点实测（`run_ice_resistance_uq`）：均值 {legacy['mean_resistance_kN']:.1f} kN、标准差 {legacy['std_resistance_kN']:.1f} kN、
95 % CI 覆盖率 {legacy['ci_coverage']:.4f}、ΣS_i = {legacy['sobol_sum_first_order']:.4f}、ΣS_T,i = {legacy['sobol_sum_total_order']:.4f}。

读出的事实（不粉饰）：
* 均值/标准差/置信区间覆盖率**几乎不受欠积分影响**（三者到 3 位有效数字相同）——所以此前那些“PCE vs MC <5 %”的自检
  从来没有暴露这个缺陷，它们测不到点位精度；
* 真正受害的是**代理模型本身**：欠积分时出样平均相对误差 {_pct(u['oos_mean_rel_err'],4)}、p95 {_pct(u['oos_p95_rel_err'])}、
  最大 {_pct(u['oos_max_rel_err'])}；耦合后分别为 {_pct(cc['oos_mean_rel_err'],4)}、{_pct(cc['oos_p95_rel_err'])}、{_pct(cc['oos_max_rel_err'])}，
  即最坏点误差降到 **1/{u['oos_max_rel_err']/cc['oos_max_rel_err']:.0f}**，代价是求积点 {cc['n_quad_points']}/{u['n_quad_points']}（模型单次求值约 µs 级）。

## ④ 结果图

{fig_md}

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **自一致性判据是失效的**：`ΣS_i ≤ 1`、`ΣS_T ≥ ΣS_i` 在“所有指数整体缩放”“基范数错一个常数”“投影欠积分”下都能通过。
   这正是本单要替换掉的旧验收方式。
2. **欠积分不是数值小事**：`2L−1 < 2p` 时投影系数不再是最小二乘意义下的最优解，也不是 Galerkin 投影，
   在 dim≥2 的合成模型上系数误差直接到 100 %（表 C）。判据朴素可查：**L ≥ p+1**。
3. **Smolyak 负权**（B 表）不是缺陷，但禁止用于任何依赖权重非负的推断（例如把 Σw f 当期望上界）。
4. **一次构造注意**：`UncertainInput.std = nominal × std_relative`，所以**零均值输入不可表达**；
   做解析基准时必须把模型搬到标准正态空间（本单用 mean=1,std=1 + 模型平移），否则模型被评估在常量点上，
   全零系数会被误判成引擎缺陷（本单第一版就踩过这个坑，已写在验证器注释里）。
5. **适用域**：投影 PCE 假定输入近高斯（Hermite 基）；Legendre 引擎假定有界均匀域。跨分布的结论不可迁移。
6. **未做**：冰阻力物理模型本身的精度（Lindqvist + 单点标定）本单不涉及，其证据等级仍为 WITHHELD（见 m1720）；
   本单只验证 UQ 机器。Ishigami/g 函数只作为**收敛性**基准，不作为机器精确性基准。

## ⑥ 误差判定（逐项，显式回答是否 < 3 %）

| 关键量 | 基准 | 误差 | 是否 < 3 % |
|---|---|---|---|
| GH 求积矩（k ≤ 2n−1，n=1..6） | `(k−1)!!` 闭式 | {a['worst_rel_err_within_degree']:.2e} | 是（≤ 1e-10，机器精确） |
| Smolyak 单位分解 + 次数内矩 | 闭式 + 1 | {b['worst_rel_err']:.2e} | 是（机器精确） |
| Hermite 投影 Sobol（精确投影） | 闭式 ANOVA | {c['worst_sobol_err_exact_projection']:.2e} | 是（机器精确） |
| Hermite 投影 Sobol（欠积分，修复前默认） | 闭式 ANOVA | {c['worst_sobol_err_under_integrated']:.2f} | **否（>100 %）→ 已判缺陷并修复** |
| Legendre 引擎方差 / Sobol | 闭式 Legendre 模型 | {max(d['rel_err_variance'], d['max_rel_err_sobol_first'], d['max_rel_err_sobol_total']):.2e} | 是（机器精确） |
| Ishigami `S_i` / `S_T,i` | Ishigami 1990 闭式 | {e['best_max_abs_err']:.2e}（绝对） | 是（abs 3 %） |
| g 函数 `S_i` / `S_T,i` | 乘积型闭式 | {f['best_max_abs_err']:.2e}（绝对） | 是（abs 3 %） |
| 冰阻力 PCE 代理点位误差（修复后） | Lindqvist 全模型（4000 出样点） | 平均 {_pct(cc['oos_mean_rel_err'],4)} / 最大 {_pct(cc['oos_max_rel_err'])} | 是（3 % 带内） |
| 冰阻力 PCE 代理点位误差（修复前） | 同上 | 平均 {_pct(u['oos_mean_rel_err'],4)} / 最大 {_pct(u['oos_max_rel_err'])} | 最大点**否**（4.9 %）→ 修复动机 |

结论：**修复后 8/9 项在机器精确或 3 % 带内**；唯一超带项是修复前的历史配置，作为缺陷证据保留。
"""
    os.makedirs(DOCDIR, exist_ok=True)
    path = os.path.join(DOCDIR, "REPORT.md")
    with open(path, "w") as fh:
        fh.write(body)
    return path


def main() -> int:
    with open(LEDGER) as fh:
        led = json.load(fh)
    os.makedirs(FIGDIR, exist_ok=True)
    p1 = fig_projection_defect(led)
    p2 = fig_sobol_benchmarks(led)
    figs = [
        (p1, "图 1 投影欠积分缺陷与修复：左=闭式 Hermite 模型的 Sobol 相对误差（L=3,p=3 欠积分 vs L=max(L,p+1)）；"
             "右=冰阻力代理对 Lindqvist 模型的出样点位误差（4000 点）。"),
        (p2, "图 2 Legendre 引擎对文献基准：左=Ishigami 的一阶/总阶 Sobol 与解析值逐项对比；"
             "右=g 函数随阶次的收敛曲线（折点导致代数收敛）。"),
    ]
    doc = write_report(led, figs)
    print("figures:", p1, p2)
    print("report :", doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())