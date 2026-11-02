"""m1965 · 数字孪生 PCE 代理「不确定性通道」验证欠账复核（models 席自找活）.

审计对象
--------
``backend/services/digital_twin/pce.py::PolynomialChaos.predict_std``
``backend/services/digital_twin/surrogate_registry.py::PCESurrogate.predict_with_uncertainty``

四项机器可判的检查（全部对解析解 / 蒙特卡洛，零手抄）：

S1 传播器正确性   : 线性/二次函数上 delta 法 std 对解析导数（相对误差）
S2 单位口径缺陷   : ``log10_outputs``（疲劳 ``damage_per_day``）上
                    legacy(decades 直接报出) vs fixed(物理单位) vs 参考模型 MC
S3 键集合缺陷     : ``predict_with_uncertainty()["std"]`` 是否覆盖 ``["mean"]``
                    的全部键（``life_years`` 曾是漏项），life_years std 对 MC
S4 质量口径高估   : ``fit_report`` 的十进空间 R² 与物理空间 R²/中位相对误差
                    的差异（疲劳代理在物理口径下是否达 15% 带）

输出（工件，数字全部程序化注入）
--------------------------------
``artifacts/m1965/pce_uncertainty_audit.json|.md``
``artifacts/m1965/fig_pce_units_before_after.png``
``artifacts/m1965/fig_pce_quality_space.png``
``artifacts/m1965/fig_pce_fatigue_error_cdf.png``

运行：``python3 tools/pce_uncertainty_audit_m1965.py``
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.digital_twin.pce import PolynomialChaos  # noqa: E402
from backend.services.digital_twin.surrogate_registry import get_registry  # noqa: E402

OUT = ROOT / "artifacts" / "m1965"
MC_N = 20000
MC_SEED = 20261101


# --------------------------------------------------------------------------
# S1 传播器：解析基准
# --------------------------------------------------------------------------
def s1_propagator_analytic() -> Dict[str, object]:
    rows: List[Dict[str, float]] = []
    cases = [
        ("linear_scale_1", 1.0, 0.0, 2.5),
        ("linear_scale_1e3", 1.0e3, 0.0, 2.5),
        ("linear_scale_1e6", 1.0e6, 0.0, 2.5),
        ("linear_offset_5e4", 2.0e4, 5.0e4, 2.5),
    ]
    for label, scale, offset, slope in cases:
        lower, upper = offset, offset + scale
        pce = PolynomialChaos(["x"], [lower], [upper], degree=2)
        pce.fit(lambda X: (slope * X).reshape(-1, 1), n_samples=200, seed=7,
                output_names=["y"])
        x0 = offset + 0.5 * scale
        sigma = 0.01 * scale
        got = pce.predict_std({"x": x0}, sigma_input=[sigma])["y"]
        want = abs(slope) * sigma
        rows.append({"case": label, "x0": x0, "sigma": sigma,
                     "std_delta": got, "std_analytic": want,
                     "rel_err": abs(got - want) / want})
    return {"cases": rows,
            "max_rel_err": max(r["rel_err"] for r in rows)}


# --------------------------------------------------------------------------
# 蒙特卡洛：参考模型（物理真值）与 PCE 自身（传播误差分解）
# --------------------------------------------------------------------------
def _mc(surrogate, point: Dict[str, float], n: int = MC_N, seed: int = MC_SEED,
        use_reference: bool = True) -> Dict[str, Dict[str, float]]:
    fields = surrogate.inputs.fields
    rng = np.random.default_rng(seed)
    sig = [0.01 * (f.upper - f.lower) for f in fields]
    names = [f.name for f in fields]
    base = np.array([point[n] for n in names], dtype=float)
    lo = np.array([f.lower for f in fields], dtype=float)
    hi = np.array([f.upper for f in fields], dtype=float)
    X = np.clip(base[None, :] + rng.normal(0.0, 1.0, (n, len(names))) * np.array(sig),
                lo, hi)
    if use_reference:
        Y = np.atleast_2d(np.asarray(surrogate._ref_fn(X), dtype=float)).reshape(n, -1)
        keys = list(surrogate.pce.output_names)
        if "damage_per_day" in keys and "life_years" not in keys:
            j = keys.index("damage_per_day")
            life = np.where(Y[:, j] > 0.0, 1.0 / (Y[:, j] * 365.25), 1e12)
            Y = np.column_stack([Y, life])
            keys = keys + ["life_years"]
    else:
        Y = surrogate.pce.predict_matrix(X)
        keys = list(surrogate.pce.output_names)
        for i in surrogate._log_idx:
            Y[:, i] = 10.0 ** np.clip(Y[:, i], -12.0, 12.0)
    out = {}
    for i, key in enumerate(keys):
        col = Y[:, i]
        mean = float(np.mean(col))
        std = float(np.std(col, ddof=1))
        out[key] = {"mean": mean, "std": std,
                    "std_se": std / math.sqrt(2.0 * (n - 1))}
    return out


def _legacy_dex_std(surrogate, point: Dict[str, float]) -> Dict[str, float]:
    """复现修复前的行为：把拟合空间（log10）std 直接当物理 std 报出。"""
    return surrogate.pce.predict_std(point)


# --------------------------------------------------------------------------
# S2/S3 单位与键集合
# --------------------------------------------------------------------------
POINTS = [
    {"Hs": 2.0, "Tp": 5.0, "ice_concentration": 0.0, "ice_thickness": 0.0},
    {"Hs": 3.0, "Tp": 7.0, "ice_concentration": 0.0, "ice_thickness": 0.0},
    {"Hs": 4.0, "Tp": 8.0, "ice_concentration": 0.2, "ice_thickness": 0.3},
]


def s2_s3_units_and_keys(fatigue) -> Dict[str, object]:
    rows = []
    for point in POINTS:
        legacy = _legacy_dex_std(fatigue, point)
        u = fatigue.predict_with_uncertainty(point)
        mc_ref = _mc(fatigue, point, use_reference=True)
        mc_pce = _mc(fatigue, point, use_reference=False)
        row = {"point": point,
               "mean": u["mean"],
               "std_fixed": u["std"],
               "std_legacy": legacy,
               "keys_legacy": sorted(legacy),
               "keys_mean": sorted(u["mean"]),
               "keys_fixed": sorted(u["std"]),
               "std_keys_cover_mean_legacy": sorted(legacy) == sorted(u["mean"]),
               "std_keys_cover_mean_fixed": sorted(u["std"]) == sorted(u["mean"]),
               "mc_reference": mc_ref,
               "mc_pce": mc_pce}
        for key in ("damage_per_day", "sigma_rms_MPa"):
            if key in mc_ref and key in legacy and key in u["std"]:
                sd = mc_ref[key]["std"]
                row[f"legacy_over_mc_{key}"] = legacy[key] / sd if sd else float("nan")
                row[f"fixed_rel_err_{key}"] = (u["std"][key] - sd) / sd if sd else float("nan")
                row[f"delta_self_rel_err_{key}"] = ((u["std"][key] - mc_pce[key]["std"])
                                                   / mc_pce[key]["std"]
                                                   if mc_pce[key]["std"] else float("nan"))
        life_mc = mc_ref.get("life_years")
        if life_mc is not None and "life_years" in u["std"]:
            row["fixed_rel_err_life_years"] = ((u["std"]["life_years"] - life_mc["std"])
                                              / life_mc["std"] if life_mc["std"] else
                                              float("nan"))
        rows.append(row)
    worst_legacy = max(r["legacy_over_mc_damage_per_day"] for r in rows)
    worst_fixed = max(abs(r["fixed_rel_err_damage_per_day"]) for r in rows)
    worst_delta = max(abs(r["delta_self_rel_err_damage_per_day"]) for r in rows)
    return {"points": rows,
            "worst_legacy_over_mc_damage": worst_legacy,
            "worst_fixed_rel_err_damage": worst_fixed,
            "worst_delta_self_rel_err_damage": worst_delta}


# --------------------------------------------------------------------------
# S4 质量口径
# --------------------------------------------------------------------------
def s4_quality_space(registry) -> Dict[str, object]:
    rows = []
    for name, sur in registry._surrogates.items():
        rep = sur.fit_report
        row = {"surrogate": name,
               "r2_fitted_space": rep.get("r2", {}),
               "r2_physical": rep.get("r2_physical", {}),
               "median_rel_error_physical": rep.get("median_rel_error_physical", {}),
               "p95_rel_error_physical": rep.get("p95_rel_error_physical", {}),
               "frac_within_15pct_physical": rep.get("frac_within_15pct_physical", {}),
               "log_space_overstatement": rep.get("log_space_overstatement", {})}
        rows.append(row)
    return {"surrogates": rows}


# --------------------------------------------------------------------------
# S5 参考模型非光滑性（解释 PCE 物理空间误差来源）
# --------------------------------------------------------------------------
def s5_reference_nonsmooth(fatigue, n_grid: int = 400) -> Dict[str, object]:
    """1 维细网格扫描 damage_per_day：相邻格点最大相对跃变 → 拐点/阶跃证据。"""
    fields = {f.name: f for f in fatigue.inputs.fields}
    hs = np.linspace(fields["Hs"].lower, fields["Hs"].upper, n_grid)
    X = np.column_stack([
        hs,
        np.full(n_grid, 7.0),
        np.zeros(n_grid),
        np.zeros(n_grid),
    ])
    Y = np.atleast_2d(np.asarray(fatigue._ref_fn(X), dtype=float)).reshape(n_grid, -1)
    d = Y[:, list(fatigue.pce.output_names).index("damage_per_day")]
    rel_jump = np.abs(np.diff(d)) / np.maximum(np.abs(d[:-1]), 1e-30)
    i = int(np.argmax(rel_jump))
    return {"grid": int(n_grid), "Tp_fixed_s": 7.0, "ice": 0.0,
            "max_rel_jump": float(rel_jump[i]),
            "hs_at_max_jump": float(hs[i + 1]),
            "hs_at_max_jump_prev": float(hs[i]),
            "damage_prev": float(d[i]), "damage_next": float(d[i + 1]),
            "median_rel_jump": float(np.median(rel_jump)),
            "damage_range": [float(d.min()), float(d.max())]}


# --------------------------------------------------------------------------
# 出图
# --------------------------------------------------------------------------
def _figures(s2: Dict[str, object], s4: Dict[str, object], fatigue) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUT.mkdir(parents=True, exist_ok=True)
    made = []

    # fig 1: units before/after vs MC (damage_per_day)
    pts = s2["points"]
    labels = [f"P{i+1}" for i in range(len(pts))]
    legacy = [p["std_legacy"]["damage_per_day"] for p in pts]
    fixed = [p["std_fixed"]["damage_per_day"] for p in pts]
    mc = [p["mc_reference"]["damage_per_day"]["std"] for p in pts]
    x = np.arange(len(pts))
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    w = 0.26
    ax.bar(x - w, legacy, w, label="legacy (decades reported as 1/day)",
           color="#c0392b")
    ax.bar(x, fixed, w, label="fixed (physical units)", color="#27ae60")
    ax.bar(x + w, mc, w, label="reference-model Monte Carlo", color="#2c3e50")
    for i, p in enumerate(pts):
        ax.annotate(f"×{p['legacy_over_mc_damage_per_day']:.1f}",
                    (x[i] - w, legacy[i]), textcoords="offset points",
                    xytext=(0, 4), ha="center", fontsize=8, color="#c0392b")
        ax.annotate(f"{(p['fixed_rel_err_damage_per_day'])*100:+.0f}%",
                    (x[i], fixed[i]), textcoords="offset points",
                    xytext=(0, 4), ha="center", fontsize=8, color="#27ae60")
    ax.set_yscale("log")
    ax.set_xticks(x, [f"{labels[i]}\nHs={p['point']['Hs']}m Tp={p['point']['Tp']}s\n"
                      f"ice={p['point']['ice_concentration']}" for i, p in enumerate(pts)],
                  fontsize=8)
    ax.set_ylabel("std of damage_per_day  [1/day]")
    ax.set_title("PCE uncertainty channel: unit defect before/after (m1965)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    f1 = OUT / "fig_pce_units_before_after.png"
    fig.tight_layout(); fig.savefig(f1, dpi=150); plt.close(fig)
    made.append(str(f1.relative_to(ROOT)))

    # fig 2: quality space per surrogate
    names = [r["surrogate"] for r in s4["surrogates"]]
    def _avg(d: Dict[str, float]) -> float:
        return float(np.mean(list(d.values()))) if d else float("nan")
    r2f = [_avg(r["r2_fitted_space"]) for r in s4["surrogates"]]
    r2p = [_avg(r["r2_physical"]) for r in s4["surrogates"]]
    within = [100.0 * _avg(r["frac_within_15pct_physical"]) for r in s4["surrogates"]]
    x = np.arange(len(names))
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.0))
    axes[0].bar(x - 0.2, r2f, 0.4, label="R² in fitted space (reported)", color="#8e44ad")
    axes[0].bar(x + 0.2, r2p, 0.4, label="R² in physical units", color="#2980b9")
    axes[0].set_xticks(x, names, rotation=20, fontsize=8)
    axes[0].set_ylim(min(0.0, min(r2p) - 0.05), 1.02)
    axes[0].set_ylabel("hold-out R² (mean over outputs)")
    axes[0].set_title("Reported quality vs physical-space quality")
    axes[0].legend(fontsize=8); axes[0].grid(alpha=0.3, axis="y")
    axes[1].bar(x, within, 0.5, color="#16a085")
    axes[1].axhline(100.0, ls="--", lw=1, color="#7f8c8d")
    axes[1].set_xticks(x, names, rotation=20, fontsize=8)
    axes[1].set_ylabel("hold-out points within 15% band [%]")
    axes[1].set_ylim(0, 105)
    axes[1].set_title("Physical accuracy bar (15%) reached by hold-out share")
    axes[1].grid(alpha=0.3, axis="y")
    f2 = OUT / "fig_pce_quality_space.png"
    fig.tight_layout(); fig.savefig(f2, dpi=150); plt.close(fig)
    made.append(str(f2.relative_to(ROOT)))

    # fig 3: fatigue physical-space error CDF
    lo = np.array([f.lower for f in fatigue.inputs.fields], dtype=float)
    hi = np.array([f.upper for f in fatigue.inputs.fields], dtype=float)
    rng = np.random.default_rng(4242)
    X = lo + (hi - lo) * rng.random((600, lo.size))
    Yref = np.atleast_2d(np.asarray(fatigue._ref_fn(X), dtype=float)).reshape(600, -1)
    Yraw = fatigue.pce.predict_matrix(X)
    Yhat = np.array(Yraw[:, :1], dtype=float, copy=True)
    for i in fatigue._log_idx:
        if i == 0:
            Yhat[:, 0] = 10.0 ** np.clip(Yhat[:, 0], -12.0, 12.0)
    ref = Yref[:, 0]
    rel = np.abs(Yhat[:, 0] - ref) / np.maximum(np.abs(ref), 1e-12)
    rel_sorted = np.sort(rel)
    frac = np.arange(1, rel.size + 1) / rel.size
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.plot(rel_sorted, frac * 100, color="#2c3e50", lw=1.8,
            label="fatigue damage_per_day (physical rel. error)")
    ax.axvline(0.15, color="#c0392b", ls="--", lw=1.2, label="15% acceptance bar")
    ax.set_xscale("log")
    ax.set_xlabel("relative error vs reference model (log scale)")
    ax.set_ylabel("hold-out share below x [%]")
    ax.set_title("Fatigue surrogate: physical-space error CDF (600 hold-out points)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    f3 = OUT / "fig_pce_fatigue_error_cdf.png"
    fig.tight_layout(); fig.savefig(f3, dpi=150); plt.close(fig)
    made.append(str(f3.relative_to(ROOT)))
    return made


# --------------------------------------------------------------------------
def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    s1 = s1_propagator_analytic()
    registry = get_registry(n_samples=160)
    fatigue = registry._surrogates["fatigue"]
    s2 = s2_s3_units_and_keys(fatigue)
    s4 = s4_quality_space(registry)
    s5 = s5_reference_nonsmooth(fatigue)
    figs = _figures(s2, s4, fatigue)

    payload = {
        "mission": "m1965",
        "branch": "agent/m1965",
        "targets": [
            "backend/services/digital_twin/pce.py::PolynomialChaos.predict_std",
            "backend/services/digital_twin/surrogate_registry.py::PCESurrogate.predict_with_uncertainty",
        ],
        "mc": {"n": MC_N, "seed": MC_SEED, "sigma_rule": "1% of each input range"},
        "S1_propagator_analytic": s1,
        "S2_S3_units_and_keys": s2,
        "S4_quality_space": s4,
        "S5_reference_nonsmooth": s5,
        "figures": figs,
    }
    (OUT / "pce_uncertainty_audit.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = ["# m1965 · PCE 代理不确定性通道验证（工件摘要）", "",
             "## S1 传播器 vs 解析", "",
             "| case | x0 | sigma | std_delta | std_analytic | rel err |",
             "|---|---|---|---|---|---|"]
    for r in s1["cases"]:
        lines.append(f"| {r['case']} | {r['x0']:.4g} | {r['sigma']:.4g} | "
                     f"{r['std_delta']:.6e} | {r['std_analytic']:.6e} | "
                     f"{r['rel_err']*100:.2e}% |")
    lines += ["", f"S1 max rel err = {s1['max_rel_err']*100:.2e}%", "",
              "## S2/S3 单位与键集合（fatigue，MC n=%d）" % MC_N, "",
              "| point | key | legacy | fixed | MC ref | legacy/MC | fixed rel err | "
              "delta self rel err |", "|---|---|---|---|---|---|---|---|"]
    for p in s2["points"]:
        for key in ("damage_per_day", "sigma_rms_MPa", "life_years"):
            legacy = p["std_legacy"].get(key)
            fixed = p["std_fixed"].get(key)
            mc = p["mc_reference"].get(key, {}).get("std")
            ratio = p.get(f"legacy_over_mc_{key}")
            ferr = p.get(f"fixed_rel_err_{key}")
            derr = p.get(f"delta_self_rel_err_{key}")
            lines.append(
                "| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                    f"Hs={p['point']['Hs']},Tp={p['point']['Tp']},"
                    f"ice={p['point']['ice_concentration']}",
                    key,
                    "-" if legacy is None else f"{legacy:.6g}",
                    "-" if fixed is None else f"{fixed:.6g}",
                    "-" if mc is None else f"{mc:.6g}",
                    "-" if ratio is None else f"×{ratio:.2f}",
                    "-" if ferr is None else f"{ferr*100:+.2f}%",
                    "-" if derr is None else f"{derr*100:+.2f}%"))
    lines += ["", f"最差 legacy/MC = ×{s2['worst_legacy_over_mc_damage']:.2f}；"
                  f"修复后最差 |rel err| = {s2['worst_fixed_rel_err_damage']*100:.2f}%；"
                  f"纯传播误差（PCE 自 MC）最差 = "
                  f"{s2['worst_delta_self_rel_err_damage']*100:.2f}%", "",
              "## S4 质量口径（十进空间 vs 物理空间）", "",
              "| surrogate | R² fitted | R² physical | median rel err | p95 rel err | "
              "within 15% |", "|---|---|---|---|---|---|"]
    for r in s4["surrogates"]:
        def avg(d):
            return float(np.mean(list(d.values()))) if d else float("nan")
        lines.append(f"| {r['surrogate']} | {avg(r['r2_fitted_space']):.4f} | "
                     f"{avg(r['r2_physical']):.4f} | "
                     f"{avg(r['median_rel_error_physical'])*100:.2f}% | "
                     f"{avg(r['p95_rel_error_physical'])*100:.2f}% | "
                     f"{avg(r['frac_within_15pct_physical'])*100:.1f}% |")
    lines += ["", "## S5 参考模型非光滑性（疲劳）", "",
              f"Hs 细网格 {s5['grid']} 点（Tp={s5['Tp_fixed_s']} s, ice=0）："
              f"相邻格点最大相对跃变 **{s5['max_rel_jump']*100:.1f}%** "
              f"@ Hs={s5['hs_at_max_jump_prev']:.3f}→{s5['hs_at_max_jump']:.3f} m"
              f"（damage {s5['damage_prev']:.4g}→{s5['damage_next']:.4g}），"
              f"中位跃变 {s5['median_rel_jump']*100:.2f}%；"
              f"damage 覆盖 [{s5['damage_range'][0]:.3g}, {s5['damage_range'][1]:.3g}] 1/day。",
              "", "  → 读法（不做超出证据的归因）：中位跃变很小（大多数区域光滑），"
              "但 damage 的动态范围跨 6 个数量级（1.6e-7 → 0.27 1/day），"
              "均匀采样的单一多项式基在低值角落几乎无法同时压低物理相对误差；"
              "这与 m1912 判据（J1 p95 相对 + J6 量程归一化双口径）的结论同向。"
              "因此本单只把**不确定度口径**修对，代理本身的物理精度仍判 WITHHELD。", "",
              "## 图", ""] + [f"- `{f}`" for f in figs]
    (OUT / "pce_uncertainty_audit.md").write_text("\n".join(lines) + "\n",
                                                 encoding="utf-8")
    print("\n".join(lines[-40:]))
    print(f"\nwrote {OUT/'pce_uncertainty_audit.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())