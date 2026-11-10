"""m8716 验证脚本：六维优势判决（G25）与设计期功效 —— 全部数字程序化注入。

产物（m9713 重开时迁址：平台推送门禁滤除 artifacts/ 与 runs/，证据须落 docs/m<本单>/ 才随主线走）
----
docs/m9713/six_dim_power_validation.json   原始测量（下游报告/文档只许引用这里）
docs/m9713/six_dim_power_fig_g25_power.png 两联图：族判决功效 vs n / 检出率 vs 臂间相关 rho

运行
----
python docs/m9713/build_six_dim_power_validation.py

确定性：全部模拟走固定 seed（20261109 / 11 / 31 / 5 / 9 / 3 / 7），跨进程逐位可复现。
"""

from __future__ import annotations

import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from tools.abm import six_dim_power as S  # noqa: E402

OUT_DIR = os.path.join(ROOT, "docs", "m9713")


def _synthetic_records(deltas_by_dim, *, lineage="L1", base=1.0, unit="u"):
    recs = []
    for dim, deltas in deltas_by_dim.items():
        sgn = S.DIMENSION_DIRECTION[dim]
        for i, delta in enumerate(deltas):
            recs.append({"lineage": lineage, "seat": f"S{i}", "arm": "control",
                         "dimension": dim, "value": base, "unit": unit,
                         "source": f"synthetic://{lineage}/control"})
            recs.append({"lineage": lineage, "seat": f"S{i}", "arm": "treatment",
                         "dimension": dim, "value": base + sgn * delta, "unit": unit,
                         "source": f"synthetic://{lineage}/treatment"})
    return recs


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    result = {}

    # 1) G25 族判决：2 维真实改进（accuracy/efficiency）vs 全零对照
    rng_syn = random.Random(20261109)
    deltas = {d: [0.0] * 12 for d in S.DIMENSIONS}
    deltas["accuracy"] = [0.030 + rng_syn.gauss(0.0, 0.015) for _ in range(12)]
    deltas["efficiency"] = [0.050 + rng_syn.gauss(0.0, 0.020) for _ in range(12)]
    res = S.six_dim_advantage(_synthetic_records(deltas), n_perm=9999, seed=20261109)
    result["g25_two_dim_improvement"] = {
        "n_pairs_by_dim": res["n_pairs_by_dim"],
        "n_significant_better": res["n_significant_better"],
        "decision_pass": res["decision_pass"],
        "min_perm_for_holm": res["min_perm_for_holm"],
        "n_perm_resolution_ok": res["n_perm_resolution_ok"],
        "per_dim": {
            d: {"p_perm": v["p_perm"], "p_holm": v["p_holm"],
                "cohen_dz": (v["effect"] or {}).get("cohen_dz"),
                "direction": v["direction"]}
            for d, v in res["dimensions"].items()
        },
        "errors": res["errors"],
    }
    null_recs = _synthetic_records({d: [0.0] * 12 for d in S.DIMENSIONS})
    res0 = S.six_dim_advantage(null_recs, n_perm=9999, seed=20261109)
    result["g25_null"] = {
        "n_significant_better": res0["n_significant_better"],
        "decision_pass": res0["decision_pass"],
        "max_p_perm": max(v["p_perm"] for v in res0["dimensions"].values()),
    }

    # 2) 零效应族 FWER / A/A 空对照
    fam = S.null_family_sim(n_families=200, n_pairs=20, n_perm=499, min_dims=2,
                            seed=11, alternative="greater")
    result["null_family_fwer"] = {
        "n_families": fam["n_families"],
        "n_pairs": fam["n_pairs"],
        "n_perm": fam["n_perm"],
        "family_any": fam["family_any"],
        "family_ge_min_dims": fam["family_ge_min_dims"],
        "family_all": fam["family_all"],
        "per_dim_max_rate": fam["per_dim_max_rate"],
        "per_dim": {d: fam["per_dim"][d] for d in S.DIMENSIONS},
        "nominal_alpha": fam["alpha"],
        "n_perm_resolution_ok": fam["n_perm_resolution_ok"],
    }
    aa = S.aa_null_control(n_sims=60, n_pairs=20, n_perm=499, min_dims=2,
                           seed=31, stability_seeds=(0, 1, 2))
    result["aa_null_control"] = {
        "pass": aa["pass"],
        "family_ge_min_dims": aa["primary"]["family_ge_min_dims"],
        "two_sided_family_ge_min_dims": aa["primary_two_sided"]["family_ge_min_dims"],
        "pooled_per_dim": aa["pooled"]["per_dim"],
        "n_pooled_families": aa["pooled"]["n_families"],
        "ci_upper_family": aa["ci_upper_family"],
    }

    # 3) 功效正控（d=0.5, n=20）与解析值对照
    pos = S.power_positive_control(d=0.5, n_pairs=20, rho=0.7, n_sims=400,
                                   n_perm=999, seed=5, alternative="greater")
    pos0 = S.power_positive_control(d=0.5, n_pairs=20, rho=0.0, n_sims=400,
                                    n_perm=999, seed=5, alternative="greater")
    result["power_positive_control"] = {
        "rho_0.7": {k: pos[k] for k in ("d", "n_pairs", "rho_assumed", "rho_realized",
                                       "dz_realized", "detection_rate", "detection_ci",
                                       "analytic_power_normal_t", "n_sims", "n_perm")},
        "rho_0.0": {k: pos0[k] for k in ("d", "n_pairs", "rho_assumed", "rho_realized",
                                        "dz_realized", "detection_rate", "detection_ci",
                                        "analytic_power_normal_t", "n_sims", "n_perm")},
        "abs_error_rho_0.7": abs(pos["detection_rate"] - pos["analytic_power_normal_t"]),
        "rel_error_rho_0.7": abs(pos["detection_rate"] - pos["analytic_power_normal_t"])
        / pos["analytic_power_normal_t"],
    }

    # 4) 配对收益：检出率 vs rho
    rho = S.power_vs_rho(n_pairs=20, n_sims=300, n_perm=499,
                         rho_grid=(0.0, 0.3, 0.5, 0.7, 0.85), seed=9)
    result["power_vs_rho"] = rho

    # 5) 族判决功效曲线与所需 n
    req = S.required_n_for_family_decision(target_power=0.8, effect_dz=0.8,
                                           n_affected_dims=2, min_dims=2,
                                           n_grid=(9, 12, 16, 20, 26, 32, 40, 60),
                                           n_rep=120, n_perm=499, seed=3)
    curve = req["curve"]
    result["family_decision_power"] = {
        "required_n_grid_hit": req["required_n_grid_hit"],
        "target_power": req["target_power"],
        "n_rep": curve["n_rep"],
        "n_perm": curve["n_perm"],
        "n_perm_resolution_ok": curve["n_perm_resolution_ok"],
        "rows": curve["rows"],
    }
    # 内部一致性：族功效 ≈ 各维独立功效之积。六维在模拟里独立生成，
    # 故 P(≥2 维显著) 应≈P(单维在 Holm 阈值下显著)^2；单维功效在同一置换机制下实测。
    row20 = next(r for r in curve["rows"] if r["n_pairs"] == 20)
    signs20 = S._sign_matrix(499, 20, S._derive_seed("family-power-signs", 3, 20, 499))
    rng_dbg = random.Random(2026)
    singles = [S._perm_pvalue([0.8 + rng_dbg.gauss(0.0, 1.0) for _ in range(20)],
                              signs20, "greater") for _ in range(400)]
    alpha_holm = 0.05 / len(S.DIMENSIONS)
    single_power_holm = sum(1 for p in singles if p <= alpha_holm) / len(singles)
    result["family_decision_power"].update({
        "single_dim_power_at_holm_alpha_measured": single_power_holm,
        "holm_alpha_per_test": alpha_holm,
        "predicted_both_significant": single_power_holm ** 2,
        "sim_n20_power_all_affected": row20["power_all_affected"],
        "abs_error_n20": abs(row20["power_all_affected"] - single_power_holm ** 2),
        "analytic_normal_single_n20": S._analytic_power_general(
            0.8, 20, alpha_holm, two_sided=False),
        "note": ("解析正态近似在 n=20 系统性高估置换功效（离散 p + t 修正口径差异），"
                 "故判据用同一置换机制的实测分解，不用正态解析"),
    })

    # 6) 解析 MDE 与既有引擎的对照（1000 次置换，n=20/40）
    mde = S.mde_curve(n_range=[20, 40], n_perm=1000, seed=7)
    result["mde_curve_check"] = {
        "points": mde,
        "analytic": {20: S.analytic_mde(20), 40: S.analytic_mde(40)},
    }

    with open(os.path.join(OUT_DIR, "six_dim_power_validation.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps({"out": os.path.join(OUT_DIR, "six_dim_power_validation.json")}, ensure_ascii=False))

    # 7) 图：两联（族判决功效 vs n；检出率 vs rho）
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - 无 matplotlib 环境
        print(f"matplotlib 不可用，跳过作图：{exc}")
        return 0

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), dpi=140)
    ns = [r["n_pairs"] for r in curve["rows"]]
    powers = [r["power_ge_min_dims"] for r in curve["rows"]]
    axes[0].plot(ns, powers, "o-", label="simulated: P(>=2 dims significant, Holm)")
    holm_alpha = 0.05 / len(S.DIMENSIONS)
    axes[0].plot(ns, [S._analytic_power_general(0.8, n, holm_alpha, two_sided=False) ** 2
                      for n in ns], "--",
                 label="analytic: [power(dz=0.8, alpha/6)]^2")
    axes[0].axhline(req["target_power"], color="gray", ls=":", label="target 0.8")
    axes[0].set_xlabel("paired samples n")
    axes[0].set_ylabel("family-decision power")
    axes[0].set_title("G25 family decision power (2/6 dims affected, dz=0.8)")
    axes[0].legend(fontsize=7)
    axes[0].grid(alpha=0.3)

    rhos = [r["rho"] for r in rho["rows"]]
    axes[1].plot(rhos, [r["power_greater"] for r in rho["rows"]], "o-",
                 label="simulated (one-sided, alpha=0.05)")
    axes[1].plot(rhos, [r["analytic_greater"] for r in rho["rows"]], "--",
                 label="analytic (normal+t)")
    axes[1].set_xlabel("inter-arm correlation rho (design assumption)")
    axes[1].set_ylabel("detection rate (d=0.5, n=20)")
    axes[1].set_title("Pairing benefit: power vs rho")
    axes[1].legend(fontsize=7)
    axes[1].grid(alpha=0.3)
    fig.tight_layout()
    fig_path = os.path.join(OUT_DIR, "six_dim_power_fig_g25_power.png")
    fig.savefig(fig_path)
    print(json.dumps({"figure": fig_path}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())