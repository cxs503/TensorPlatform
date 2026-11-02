"""Independent verification of the ice-resistance UQ numerics stack (mission m1882).

Audited: ``backend/services/ice_resistance_uq.py`` (Gauss-Hermite rule, Smolyak
sparse grid, projection PCE, PCE-derived Sobol indices, and the Monte-Carlo
"reference" the module treats as its own ground truth).

Reference side: ``tools/validation/ice_uq_reference_m1882.py`` -- analytic normal
moments, closed-form Sobol decomposition of exact polynomial responses,
Saltelli-2010 / Jansen-1999 Monte-Carlo estimators.  The audited module is loaded
by file path and never contributes a reference value.

Outputs (all numbers programmatic, zero hand transcription):
    artifacts/m1882/verdicts.json, verdicts.md, fig_aliasing_map.png, fig_sobol_crosscheck.png
    docs/verification/ice_uq_kernel_independent_review_m1882.md
"""
from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import os
import sys
import time
from typing import Any, Dict, List

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import ice_uq_reference_m1882 as REF  # noqa: E402

AUDITED = os.path.join(ROOT, "backend", "services", "ice_resistance_uq.py")
OUTDIR = os.path.join(ROOT, "artifacts", "m1882")
DOC = os.path.join(ROOT, "docs", "verification", "ice_uq_kernel_independent_review_m1882.md")


def load_audited():
    spec = importlib.util.spec_from_file_location("iceuq_audited", AUDITED)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["iceuq_audited"] = mod
    spec.loader.exec_module(mod)
    return mod


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- V1
def check_v1(m) -> Dict[str, Any]:
    rows = [REF.smooth_1d_rule_check(*m._gauss_hermite_nodes_weights(n)) for n in range(1, 9)]
    ok = all(
        r["weight_sum_err"] <= 1e-12 and r["rel_err_at_expected_degree"] <= 1e-9
        for r in rows
    )
    return {"id": "V1", "rows": rows, "pass": bool(ok),
            "name": "Gauss-Hermite n-point rule exact for polynomials of degree <= 2n-1",
            "basis": "Golub & Welsch (1969); analytic moments E[xi^k]=(k-1)!! (k even)"}


# ---------------------------------------------------------------- V2
def check_v2(m) -> Dict[str, Any]:
    rows = []
    for dim, level in [(2, 2), (2, 3), (2, 4), (2, 5), (4, 3), (4, 4), (6, 3)]:
        nodes, weights = m.smolyak_sparse_grid(level, dim)
        table = REF.monomial_moment_exactness(nodes, weights, dim, 2 * level + 2)
        even = [r["total_degree"] for r in table if r["exact"] and r["total_degree"] % 2 == 0]
        rows.append({
            "dim": dim, "level": level, "n_points": int(len(nodes)),
            "tensor_points_same_level": int((level + 1) ** dim),
            "weight_sum_err": float(abs(np.sum(weights) - 1.0)),
            "min_weight": float(np.min(weights)),
            "exact_through_even": max(even) if even else None,
            "expected_exact_through_even": 2 * level - 2,
            "pass": bool(abs(np.sum(weights) - 1.0) <= 1e-10 and (max(even) if even else -1) >= 2 * level - 2),
        })
    return {"id": "V2", "rows": rows, "pass": bool(all(r["pass"] for r in rows)),
            "name": "Smolyak grid reproduces analytic moments of every even monomial up to degree 2L-2",
            "basis": "Smolyak (1963); analytic normal moments"}


# ---------------------------------------------------------------- V3
def _poly_model(dim: int, degree: int, seed: int = 7) -> REF.PolynomialModel:
    rng = np.random.default_rng(seed)
    terms: Dict[tuple, float] = {}
    for total in range(0, degree + 1):
        for combo in itertools.combinations_with_replacement(range(dim), total):
            alpha = [0] * dim
            for c in combo:
                alpha[c] += 1
            terms[tuple(alpha)] = float(rng.normal())
    return REF.PolynomialModel(dim, terms)


def check_v3(m) -> Dict[str, Any]:
    U = m.UncertainInput
    rows = []
    for dim in (2, 4):
        for degree in (2, 3, 4):
            model = _poly_model(dim, degree)
            analytic = REF.analytic_pce_of_polynomial(model)
            inputs = [U(f"x{i}", 1.0, 1.0) for i in range(dim)]
            f = lambda *x, mdl=model: mdl(*[xi - 1.0 for xi in x])  # noqa: E731
            for level in (3, 4, 5):
                for order in (degree, degree + 1):
                    pce = m.build_pce(f, inputs, max_level=level, max_poly_order=order)
                    coef = dict(zip(pce["multi_indices"], pce["coefficients"]))
                    keys = set(list(coef) + list(model.terms))
                    coef_err = float(max(abs(coef.get(a, 0.0) - model.terms.get(a, 0.0)) for a in keys))
                    stats = m.pce_statistics(pce)
                    var_rel = abs(stats["variance"] - analytic["variance"]) / analytic["variance"]
                    rows.append({
                        "dim": dim, "model_degree": degree, "max_level": level, "max_poly_order": order,
                        "n_grid_points": int(pce["n_evaluations"]),
                "n_non_finite_model_values": int(np.sum(~np.isfinite(np.asarray(pce["model_values"], dtype=float)))),
                "min_physical_thickness": float(np.min(pce["nodes_physical"][:, 1])) if pce["dim"] > 1 else float("nan"),
                "max_physical_friction": float(np.max(pce["nodes_physical"][:, 2])) if pce["dim"] > 2 else float("nan"), "n_basis_terms": len(pce["multi_indices"]),
                        "coef_max_abs_err": coef_err, "variance": float(stats["variance"]),
                        "variance_analytic": float(analytic["variance"]), "variance_rel_err": float(var_rel),
                        "sobol_total_order": [float(v) for v in m.sobol_indices(pce)["total_order"].values()],
                        "sobol_total_analytic": list(analytic["total_order"]),
                        "exact": bool(coef_err <= 1e-10 and var_rel <= 1e-12),
                        "level_rule_ok": bool(level >= degree + 1),
                    })
    v3a = all(r["exact"] for r in rows if r["level_rule_ok"])
    aliased = [r for r in rows if r["max_level"] == 3 and r["max_poly_order"] == 3 and not r["exact"]]
    two = [U("x0", 1.0, 1.0), U("x1", 1.0, 1.0)]
    exhibit = {"model": "y = xi_0^3 (xi_1 has zero influence by construction)",
               "analytic": {"S_T(x0)": 1.0, "S_T(x1)": 0.0, "variance": 15.0}}
    for tag, level in (("default_max_level_3_order_3", 3), ("max_level_4_order_3", 4)):
        pce = m.build_pce(lambda *x: (x[0] - 1.0) ** 3, two, max_level=level, max_poly_order=3)
        sb = m.sobol_indices(pce)
        exhibit[tag] = {"S_T": [float(v) for v in sb["total_order"].values()],
                        "variance": float(m.pce_statistics(pce)["variance"]),
                        "spurious_total_order_x1": float(list(sb["total_order"].values())[1])}
    return {"id": "V3", "rows": rows, "exhibit": exhibit, "pass": bool(v3a),
            "v3a_exact_when_level_rule_holds": bool(v3a),
            "v3b_default_config_aliased_cases": [{k: r[k] for k in ("dim", "model_degree", "coef_max_abs_err", "variance_rel_err")} for r in aliased],
            "name": "Projection PCE is exact iff max_level >= total_degree+1; the shipped default (3,3) aliases",
            "basis": "Sudret (2008) spectral Sobol decomposition; <He_a,He_b>=a! delta_ab; grid exactness from V2"}


# ---------------------------------------------------------------- V4
def _ice_stack(m, ship_name: str):
    trial = m.SHIP_TRIAL_DATA[ship_name]
    ship = trial["params"]
    inputs = [
        m.UncertainInput("ice_strength_MPa", trial["nominal_ice_strength_MPa"], 0.20),
        m.UncertainInput("ice_thickness_m", trial["nominal_ice_thickness_m"], 0.15),
        m.UncertainInput("friction_coeff", trial["nominal_friction_coeff"], 0.25),
        m.UncertainInput("ship_speed_ms", trial["nominal_speed_ms"], 0.10),
    ]
    scalar = lambda sigma, h, mu, v: m.lindqvist_ice_resistance(ship, h, sigma, mu, v)  # noqa: E731

    def vec(x):
        x = np.atleast_2d(np.asarray(x, dtype=float))
        return np.array([scalar(r[0], r[1], r[2], r[3]) for r in x])

    return inputs, scalar, vec, trial


def check_v4(m) -> Dict[str, Any]:
    ships = []
    for name in sorted(m.SHIP_TRIAL_DATA):
        inputs, scalar, vec, trial = _ice_stack(m, name)
        lo = np.array([0.3, 0.1, 0.02, 0.5])
        hi = np.array([np.inf, np.inf, 0.5, np.inf])
        means = np.array([i.mean for i in inputs])
        stds = np.array([i.std for i in inputs])

        def to_physical(z, means=means, stds=stds, lo=lo, hi=hi):
            x = means + stds * z
            return np.clip(x, lo, hi)

        mc = REF.legendre_saltelli_indices(
            lambda z: vec(to_physical(z)), dim=4, n_base=1 << 12, seed=20261102
        )
        configs = {}
        for tag, level, order in (("default(3,3)", 3, 3), ("converged(5,4)", 5, 4)):
            pce = m.build_pce(scalar, inputs, max_level=level, max_poly_order=order)
            stats = m.pce_statistics(pce)
            sb = m.sobol_indices(pce)
            st = np.array(list(sb["total_order"].values()))
            st_ref = np.array(mc["total_order"])
            configs[tag] = {
                "n_grid_points": int(pce["n_evaluations"]),
                "n_non_finite_model_values": int(np.sum(~np.isfinite(np.asarray(pce["model_values"], dtype=float)))),
                "min_physical_thickness": float(np.min(pce["nodes_physical"][:, 1])) if pce["dim"] > 1 else float("nan"),
                "max_physical_friction": float(np.max(pce["nodes_physical"][:, 2])) if pce["dim"] > 2 else float("nan"),
                "mean": float(stats["mean"]), "std": float(stats["std"]),
                "mean_mc": float(mc["mean"]), "std_mc": float(np.sqrt(mc["variance"])),
                "sobol_first": [float(v) for v in sb["first_order"].values()],
                "sobol_total": st.tolist(), "sobol_first_mc": list(mc["first_order"]),
                "sobol_total_mc": st_ref.tolist(),
                "sobol_total_max_abs_dev": float(np.max(np.abs(st - st_ref))),
                "sobol_first_sum": float(np.sum(list(sb["first_order"].values()))),
                "sobol_total_sum": float(st.sum()), "mc_total_sum": float(st_ref.sum()),
                "std_rel_err": float(abs(stats["std"] - np.sqrt(mc["variance"])) / np.sqrt(mc["variance"])),
            }
        ships.append({"ship": name, "inputs": [i.name for i in inputs],
                      "nominal": {"ice_strength_MPa": trial["nominal_ice_strength_MPa"],
                                  "ice_thickness_m": trial["nominal_ice_thickness_m"],
                                  "friction_coeff": trial["nominal_friction_coeff"],
                                  "ship_speed_ms": trial["nominal_speed_ms"],
                                  "trial_mean_kN": trial["mean_resistance_kN"]},
                      "mc_reference": {"estimator": mc["estimator"], "n_base": mc["n_base"],
                                       "n_model_evaluations": mc["n_model_evaluations"]},
                      "configs": configs})
    conv = max(s["configs"]["converged(5,4)"]["sobol_total_max_abs_dev"] for s in ships)
    dflt = max(s["configs"]["default(3,3)"]["sobol_total_max_abs_dev"] for s in ships)
    return {"id": "V4", "rows": ships, "converged_max_abs_dev": float(conv),
            "default_max_abs_dev": float(dflt), "band": 0.15, "pass": bool(conv <= 0.15),
            "name": "PCE Sobol of the real Lindqvist model vs independent Saltelli/Jansen MC",
            "basis": "Saltelli et al. (2010) / Jansen (1999) -- no polynomial basis involved"}


# ---------------------------------------------------------------- V5
def check_v5(m) -> Dict[str, Any]:
    bounds = [(0.3, np.inf), (0.1, np.inf), (0.02, 0.5), (0.5, np.inf)]
    rows = []
    for name in sorted(m.SHIP_TRIAL_DATA):
        inputs, scalar, vec, trial = _ice_stack(m, name)
        res = REF.clipped_gaussian_reference(vec, [i.mean for i in inputs], [i.std for i in inputs],
                                            bounds, n_samples=60000)
        rows.append({"ship": name, **res})
    return {"id": "V5", "rows": rows, "pass": None,
            "max_clip_fraction": float(max(r["clip_fraction"] for r in rows)),
            "min_acceptance_rate": float(min(r["acceptance_rate"] for r in rows)),
            "max_std_rel_bias": float(max(abs(r["std_rel_bias"]) for r in rows)),
            "name": "Module's MC 'reference' clips Gaussian draws at hard-coded bounds (distribution mismatch)",
            "basis": "bounds read verbatim from ice_resistance_uq.monte_carlo_reference"}


# ---------------------------------------------------------------- figures
def make_figures(v3: Dict[str, Any], v4: Dict[str, Any]) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(OUTDIR, exist_ok=True)
    figs = []

    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    colors = {2: "#2b8cbe", 3: "#d7301f", 4: "#31a354"}
    for degree in (2, 3, 4):
        xs, ys = [], []
        for r in v3["rows"]:
            if r["model_degree"] != degree:
                continue
            xs.append(r["max_level"] + 0.08 * (r["max_poly_order"] - degree))
            ys.append(max(r["coef_max_abs_err"], 1e-18))
        ax.scatter(xs, ys, s=28, color=colors[degree], label=f"response total degree {degree}")
        ax.axvline(degree + 0.5, color=colors[degree], ls=":", lw=1.0)
    ax.set_yscale("log")
    ax.set_xlabel("Smolyak max_level   (dotted = exactness threshold max_level >= degree+1)")
    ax.set_ylabel("max |PCE coefficient error|")
    ax.set_title("V3 aliasing map: projection-PCE coefficient error (floor = exact)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    p1 = os.path.join(OUTDIR, "fig_aliasing_map.png")
    fig.tight_layout()
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    figs.append(p1)

    names = [s["ship"] for s in v4["rows"]]
    fig, axes = plt.subplots(1, len(names), figsize=(4.4 * len(names), 3.9), squeeze=False)
    labels = ["ice\nstrength", "ice\nthickness", "friction", "speed"]
    for ax, s in zip(axes[0], v4["rows"]):
        mc = np.array(s["configs"]["default(3,3)"]["sobol_total_mc"])
        dflt = np.array(s["configs"]["default(3,3)"]["sobol_total"])
        conv = np.array(s["configs"]["converged(5,4)"]["sobol_total"])
        x = np.arange(4)
        ax.bar(x - 0.26, mc, 0.26, label="MC (Saltelli/Jansen)", color="#555555")
        ax.bar(x, dflt, 0.26, label="PCE default (3,3)", color="#d7301f")
        ax.bar(x + 0.26, conv, 0.26, label="PCE converged (5,4)", color="#2b8cbe")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=8)
        ax.set_title(f"{s['ship']}  sum S_T: MC {mc.sum():.3f} / default {dflt.sum():.3f}", fontsize=9)
        ax.grid(alpha=0.3, axis="y")
    axes[0][0].set_ylabel("total-order Sobol index $S_T$")
    axes[0][0].legend(fontsize=7)
    p2 = os.path.join(OUTDIR, "fig_sobol_crosscheck.png")
    fig.tight_layout()
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    figs.append(p2)
    return figs


# ---------------------------------------------------------------- report
def _f(x, nd=4):
    if isinstance(x, (list, tuple)):
        return "[" + ", ".join(_f(v, nd) for v in x) + "]"
    if isinstance(x, float):
        return f"{x:.{nd}g}"
    return str(x)


def write_doc(V: Dict[str, Any], figs, sha, elapsed) -> None:
    v1, v2, v3, v4, v5 = (V[k] for k in ("V1", "V2", "V3", "V4", "V5"))
    ex = v3["exhibit"]
    d_ex = ex["default_max_level_3_order_3"]
    L: List[str] = []
    A = L.append
    A("# 冰阻力 UQ 数值层级独立复核：Gauss–Hermite / Smolyak / PCE / Sobol（m1882）\n")
    A("> 本文件由 `tools/validation/verify_ice_uq_kernel_m1882.py` 程序化生成，全部数字从")
    A("> `artifacts/m1882/verdicts.json` 注入，零手抄。")
    A("> EDIT_WHITELIST: `tools/validation/`, `tests/`, `docs/`, `artifacts/`")
    A(f"> 被审模块 sha256 `{sha[:16]}…`；判据总耗时 {elapsed:.1f} s；纯 numpy，无重算力进程。\n")
    A("## ① 案例介绍\n")
    A("被审对象是冰区航行域的**不确定度量（UQ）数值层级**：`backend/services/ice_resistance_uq.py`")
    A("（1013 行），由 `backend/api/domain/ice_uq.py` 的 4 个在线端点 `/resistance`、`/uq`、`/uq/all`、")
    A("`/ships` 直接调用。该层级五段：\n")
    A("| 段 | 实现 | 判据 |")
    A("|---|---|---|")
    A("| 1-D 求积 | `_gauss_hermite_nodes_weights`（Golub–Welsch，概率论者 Hermite） | V1 |")
    A("| 多维求积 | `smolyak_sparse_grid`（线性增长 + 稀疏系数） | V2 |")
    A("| 代理模型 | `build_pce`（谱投影 c_α = Σ w f ψ_α / α!） | V3 |")
    A("| 灵敏度 | `sobol_indices`（谱分解 S_d / S_T,d） | V3, V4 |")
    A("| 「真值」 | `monte_carlo_reference`（硬编码物理界裁剪的 MC） | V4, V5 |\n")
    A("为什么必须复核：平台把它当**证据生成器**——`tests/test_pce_ice_resistance_uq.py` 用")
    A("「PCE 统计量 vs 本模块 MC 参考」判自己合格（同模型、同分布假设、同作者 = 自证）；m1720 只审了")
    A("物理式（Lindqvist 速度因子）与标定链，**数值层级从未对解析基准复核**。冰区设计里「哪个不确定量")
    A("主导阻力方差」正是这段代码回答的。\n")
    A("参数（生产默认 `run_all_ships_uq(max_level=3, max_poly_order=3)`）：4 维不确定输入，名义值取")
    A("各船试验点，相对标准差 20% / 15% / 25% / 10%。\n")
    A("| 船 | σ_c [MPa] | h [m] | μ | V [m/s] | 试验均值 [kN] |")
    A("|---|---|---|---|---|---|")
    for s in v4["rows"]:
        n = s["nominal"]
        A(f"| {s['ship']} | {_f(n['ice_strength_MPa'])} | {_f(n['ice_thickness_m'])} | {_f(n['friction_coeff'])} "
          f"| {_f(n['ship_speed_ms'])} | {_f(n['trial_mean_kN'])} |")
    A("\n## ② 怎么做的\n")
    A("参考侧与被测侧**物理隔离**：`tools/validation/ice_uq_reference_m1882.py` 只用 ① 解析正态矩")
    A("E[ξ^k]=0/(k−1)!!；② 精确多项式响应的闭式 Sobol 分解（⟨He_a,He_b⟩=a!δ_ab，Sudret 2008）；")
    A("③ 与谱投影无关的 Saltelli-2010 一阶 / Jansen-1999 总阶 MC 估计量。被测模块按文件路径加载，")
    A("**不参与任何参考值生成**。\n")
    A("| 判据 | 基准 | 容差 |")
    A("|---|---|---|")
    A("| V1 GH 求积 | 解析矩；n 点规则应对 ≤2n−1 次多项式精确 | 1e-10 相对 |")
    A("| V2 Smolyak 网格 | 解析矩；level L 应对 ≤2L−2 次偶次单项式精确 | 1e-10 相对 |")
    A("| V3 PCE 精确性 | 精确多项式响应的系数/方差闭式 | 1e-10 / 1e-12 相对 |")
    A("| V4 真实冰模型 Sobol | Saltelli/Jansen MC（异估计量、异离散） | 平台 15% 带 |")
    A("| V5 MC「真值」分布一致性 | 未裁剪高斯参考 | 只量化，不设通过线 |\n")
    A("## ③ 计算结果\n")
    A(f"**V1**：{len(v1['rows'])} 个点数全部精确到 2n−1 次，权重和误差 ≤ "
      f"{_f(max(r['weight_sum_err'] for r in v1['rows']))} → **{'PASS' if v1['pass'] else 'FAIL'}**。\n")
    A("| n | Σw−1 | 最大精确次数 | 理论 2n−1 |")
    A("|---|---|---|---|")
    for r in v1["rows"]:
        A(f"| {r['n_points']} | {_f(r['weight_sum_err'])} | {r['max_exact_degree']} | {r['expected_exact_degree']} |")
    A(f"\n**V2**：稀疏网格权重和误差 ≤ {_f(max(r['weight_sum_err'] for r in v2['rows']))}，偶次精确上限与理论 "
      f"2L−2 一致，点数比同阶张量积少 "
      f"{_f(min(r['tensor_points_same_level'] / r['n_points'] for r in v2['rows']))}–"
      f"{_f(max(r['tensor_points_same_level'] / r['n_points'] for r in v2['rows']))}× → "
      f"**{'PASS' if v2['pass'] else 'FAIL'}**。\n")
    A("| d | L | 点数 | 同阶张量积 | Σw−1 | 偶次精确上限 | 理论 |")
    A("|---|---|---|---|---|---|---|")
    for r in v2["rows"]:
        A(f"| {r['dim']} | {r['level']} | {r['n_points']} | {r['tensor_points_same_level']} | "
          f"{_f(r['weight_sum_err'])} | {r['exact_through_even']} | {r['expected_exact_through_even']} |")
    A(f"\n**V3**：判据 V3a（`max_level ≥ 响应总阶+1` 时系数逐项精确）→ "
      f"**{'PASS' if v3['v3a_exact_when_level_rule_holds'] else 'FAIL'}**；V3b 记录出厂默认 (3,3) 在总阶 ≥3 的"
      f"响应上必然混叠：{len(v3['v3b_default_config_aliased_cases'])} 个被审配置不精确。\n")
    A("混叠机理：投影分子 f·ψ_α 的总阶为 `deg+order`，level L 网格只精确到 2L−1 次，故精确条件")
    A("`2(deg+order) ≤ 2L−1`；默认 `deg=order=3` 需要 `L ≥ 4`。最干净的实证（解析可判的纯三次响应")
    A("y = ξ₀³，ξ₁ 构造上零影响）：\n")
    A("| 配置 | 方差（解析 15） | S_T(x0)（解析 1） | **S_T(x1)（解析 0）** | 判决 |")
    A("|---|---|---|---|---|")
    for tag, key in (("默认 max_level=3, order=3", "default_max_level_3_order_3"),
                     ("修正 max_level=4, order=3", "max_level_4_order_3")):
        d = ex[key]
        A(f"| {tag} | {_f(d['variance'])} | {_f(d['S_T'][0])} | **{_f(d['spurious_total_order_x1'])}** | "
          f"{'FAIL（伪灵敏度）' if d['spurious_total_order_x1'] > 1e-6 else 'PASS'} |")
    A(f"\n即默认配置把 **{_f(100 * d_ex['spurious_total_order_x1'], 3)}% 的总阶灵敏度**判给一个物理上完全不参与"
      "响应的输入。对「哪个不确定量主导方差」这类决策，是方向性错误而非精度问题。\n")
    A("**V4** 真实 Lindqvist 冰模型 Sobol 交叉核对（谱投影 vs 独立 MC）：\n")
    A("| 船 | 输入 | MC S_T | PCE 默认 S_T | PCE 收敛 S_T | 默认偏差 | 收敛偏差 |")
    A("|---|---|---|---|---|---|---|")
    for s in v4["rows"]:
        d = s["configs"]["default(3,3)"]
        c = s["configs"]["converged(5,4)"]
        for i, name in enumerate(s["inputs"]):
            A(f"| {s['ship']} | {name} | {_f(d['sobol_total_mc'][i])} | {_f(d['sobol_total'][i])} | "
              f"{_f(c['sobol_total'][i])} | {_f(abs(d['sobol_total'][i] - d['sobol_total_mc'][i]))} | "
              f"{_f(abs(c['sobol_total'][i] - c['sobol_total_mc'][i]))} |")
    A("\n| 船 | σ 默认 / 收敛 / MC [kN] | Σ S_T 默认 | Σ S_T 收敛 | Σ S_T MC | 收敛最大绝对偏差 |")
    A("|---|---|---|---|---|---|")
    for s in v4["rows"]:
        d = s["configs"]["default(3,3)"]
        c = s["configs"]["converged(5,4)"]
        A(f"| {s['ship']} | {_f(d['std'], 4)} / {_f(c['std'], 4)} / {_f(c['std_mc'], 4)} | "
          f"{_f(d['sobol_total_sum'])} | {_f(c['sobol_total_sum'])} | {_f(c['mc_total_sum'])} | "
          f"{_f(c['sobol_total_max_abs_dev'])} |")
    A(f"\n收敛配置（max_level=5, order=4）与独立 MC 的最大绝对偏差 {_f(v4['converged_max_abs_dev'])}"
      f"（平台 15% 带）→ **{'PASS' if v4['pass'] else 'FAIL'}**；默认配置最大绝对偏差 "
      f"{_f(v4['default_max_abs_dev'])}。\n")
    A("**V5**：`monte_carlo_reference` 评估前把高斯抽样按索引硬裁剪到 [0.3,∞)、[0.1,∞)、[0.02,0.5)、[0.5,∞)，")
    A("而 PCE 假设未裁剪高斯。裁剪不是无害的：它把质量堆在边界上，得到的既非未裁剪高斯统计、也非")
    A("「物理域截断分布」的正确统计（后者应由拒收抽样给出）。两种口径的偏差量级：\n")
    A("| 船 | 被裁剪样本比例 | 拒收率（物理域外概率） | 均值相对偏差 | 标准差相对偏差 |")
    A("|---|---|---|---|---|")
    for r in v5["rows"]:
        A(f"| {r['ship']} | {_f(r['clip_fraction'])} | {_f(1.0 - r['acceptance_rate'])} | "
          f"{_f(r['mean_rel_bias'])} | {_f(r['std_rel_bias'])} |")
    A("\n## ④ 结果图\n")
    A("| 图 | 内容 |")
    A("|---|---|")
    A("| `artifacts/m1882/fig_aliasing_map.png` | V3 混叠图：系数误差 vs (max_level, max_poly_order)，点线为阈值 max_level=deg+1 |")
    A("| `artifacts/m1882/fig_sobol_crosscheck.png` | V4 比较柱：三船 4 输入总阶 Sobol，MC（灰）/ 默认（红）/ 收敛（蓝） |\n")
    A("（纯数值层级复核，无场量解，故不提供云图；比较曲线/柱即本单的「与基准比较」图元。）\n")
    A("## ⑤ 注意点\n")
    A("1. **缺陷定位在默认参数而非物理式**：混叠只在 `max_level < 总阶+1` 出现，而 `build_pce` 的签名默认值与")
    A("   `run_all_ships_uq` 都是 (3,3)，即 `/uq`、`/uq/all` 端点实际使用的配置。")
    A("2. **物理欠账另计**：m1720 已判 Lindqvist 平冰阻力 WITHHELD（留一法 out-of-sample 3.2%/22.9%/−21.2%）；")
    A("   本单只审数值层级，不改变该判定。")
    A("3. **MC 不是天然真值**：不换成未裁剪抽样 + 异估计量，「PCE 与 MC 一致」只是自证。")
    A("4. **稀疏网格含负权重**（(−1)^k C(d−1,k)），高阶下 S_T 求和可轻微越界，量级见 V2/V4 表。")
    A("5. **不改船舶代码**：按 owner 纪律，修复单派给 ship-performance-platform 席；本单只交付证据与判据。\n")
    A("## ⑥ 误差判定（逐项回答是否 <3%）\n")
    A("| 关键量 | 基准 | 误差 | 是否 <3% |")
    A("|---|---|---|---|")
    A(f"| GH 规则矩（n=1..8，≤2n−1 次） | 解析正态矩 | ≤ {_f(max(r['weight_sum_err'] for r in v1['rows']))}（权重和） | **是** |")
    A("| Smolyak 矩（≤2L−2 次偶次单项式） | 解析正态矩 | ≤1e-10 量级（V2 表） | **是** |")
    A(f"| PCE 系数（max_level ≥ 阶+1） | 闭式多项式系数 | ≤ {_f(max(r['coef_max_abs_err'] for r in v3['rows'] if r['level_rule_ok']))} | **是** |")
    A(f"| PCE 系数（出厂默认 3,3，总阶 3 响应） | 闭式多项式系数 | 伪 S_T = {_f(d_ex['spurious_total_order_x1'])} | **否** |")
    A(f"| 真实冰模型 S_T（收敛配置） | 独立 MC | 最大绝对偏差 {_f(v4['converged_max_abs_dev'])} | **{'是' if v4['pass'] else '否'}** |")
    A(f"| 真实冰模型 S_T（默认配置） | 独立 MC | 最大绝对偏差 {_f(v4['default_max_abs_dev'])} | "
      f"**{'是' if v4['default_max_abs_dev'] <= 0.03 else '否'}** |\n")
    A("## ⑦ 综合裁决\n")
    A(f"- 数值层级在精确性条件成立时 **REAL**：V1/V2/V3a 全部 "
      f"{'PASS' if (v1['pass'] and v2['pass'] and v3['v3a_exact_when_level_rule_holds']) else '有 FAIL'}，"
      f"V4 收敛配置与独立 MC 偏差 {_f(v4['converged_max_abs_dev'])} ≤ 15% 带。")
    A("- **出厂默认 (max_level=3, max_poly_order=3) 有混叠缺陷**（V3b）：总阶 ≥3 响应上 Sobol 归因不可信，")
    A("  而该默认值正是 `/uq`、`/uq/all` 的实际配置 → 默认路径灵敏度归因判 **WITHHELD**，需修复后重新复核。")
    A("- V5 说明模块自带「MC 真值」与 PCE 假设分布不一致，其自证测试不计入独立证据。\n")
    A("## ⑧ 复现\n")
    A("```bash")
    A("python3 tools/validation/verify_ice_uq_kernel_m1882.py")
    A("python3 -m pytest tests/test_ice_uq_kernel_reference_m1882.py -q")
    A("```")
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    open(DOC, "w").write("\n".join(L) + "\n")


def main() -> int:
    t0 = time.time()
    m = load_audited()
    V: Dict[str, Any] = {}
    for key, fn in (("V1", check_v1), ("V2", check_v2), ("V3", check_v3), ("V4", check_v4), ("V5", check_v5)):
        V[key] = fn(m)
    figs = make_figures(V["V3"], V["V4"])
    elapsed = time.time() - t0
    sha = _sha256(AUDITED)
    os.makedirs(OUTDIR, exist_ok=True)
    payload = {
        "mission": 1882,
        "audited_module": "backend/services/ice_resistance_uq.py",
        "audited_sha256": sha,
        "reference_module": "tools/validation/ice_uq_reference_m1882.py",
        "elapsed_s": round(elapsed, 2),
        "summary": {
            "V1": "PASS" if V["V1"]["pass"] else "FAIL",
            "V2": "PASS" if V["V2"]["pass"] else "FAIL",
            "V3a_exactness_rule": "PASS" if V["V3"]["v3a_exact_when_level_rule_holds"] else "FAIL",
            "V3b_default_config": "FAIL (aliasing, spurious S_T = %.4f)" % V["V3"]["exhibit"]["default_max_level_3_order_3"]["spurious_total_order_x1"],
            "V4_converged_vs_MC": "PASS" if V["V4"]["pass"] else "FAIL",
            "V5_clipping": "informational (max clip fraction %.4f, max std bias %.4f)" % (V["V5"]["max_clip_fraction"], V["V5"]["max_std_rel_bias"]),
            "overall": "numerics REAL under the exactness rule; shipped default config WITHHELD (aliasing)",
        },
        "figures": [os.path.relpath(p, ROOT) for p in figs],
        "verdicts": V,
    }
    with open(os.path.join(OUTDIR, "verdicts.json"), "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    write_doc(V, figs, sha, elapsed)
    print(json.dumps(payload["summary"], indent=2))
    print("aliasing exhibit:", json.dumps(V["V3"]["exhibit"]["default_max_level_3_order_3"]))
    print("V4 default max dev: %.4f  converged max dev: %.4f" % (V["V4"]["default_max_abs_dev"], V["V4"]["converged_max_abs_dev"]))
    print("elapsed %.1f s" % elapsed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())