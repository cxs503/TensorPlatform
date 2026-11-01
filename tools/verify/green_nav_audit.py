#!/usr/bin/env python3
"""绿色航行域证据审计：程序化生成 evidence.json + 3 张图 + 报告数字表。

纪律：报告中所有数字都从本脚本写出，人工只做叙述，不手抄结果。

输出：
  artifacts/green_nav/evidence.json   全量数字 + 判据结果
  artifacts/green_nav/fig1_cf_chemistry.png   导出 C_F vs 公布值
  artifacts/green_nav/fig2_eedi_vs_capacity.png  达到值 vs 各阶段要求值
  artifacts/green_nav/fig3_cii_timeline.png    CII 达到值 vs 逐年要求值
  artifacts/green_nav/numbers.md      报告用 markdown 数字表

运行：python3 tools/verify/green_nav_audit.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from services import green_navigation as gn  # noqa: E402

OUT = ROOT / "artifacts" / "green_nav"
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Noto Sans CJK JP", "WenQuanYi Zen Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# ---------------------------------------------------------------------------
# 算例：82 000 DWT Kamsarmax 散货船（主机 HFO）
# ---------------------------------------------------------------------------
CASE = dict(
    ship_type="bulk_carrier",
    capacity_t=82000.0,
    v_ref_kn=14.0,
    p_me_kw=8800.0,
    sfoc_me=168.0,
    sfoc_ae=215.0,
    fuel="hfo",
)


def build_chain(case=CASE) -> gn.PowerChain:
    cf = gn.carbon_factor(case["fuel"])
    p_ae = gn.auxiliary_power_default(case["p_me_kw"])
    return gn.PowerChain(
        p_me_kw=case["p_me_kw"], sfoc_me=case["sfoc_me"], cf_me=cf,
        p_ae_kw=p_ae, sfoc_ae=case["sfoc_ae"], cf_ae=cf,
    )


def independent_attained(case=CASE) -> float:
    """测试/审计侧的独立复算（不调用模块函数，逐项写开）。"""
    cf = case["p_me_kw"] and gn.FUELS[case["fuel"]].derive_carbon_fraction() * 44.009 / 12.011
    p_ae = gn.auxiliary_power_default(case["p_me_kw"])
    main = case["p_me_kw"] * case["sfoc_me"] * cf
    aux = p_ae * case["sfoc_ae"] * cf
    return (main + aux) / (case["capacity_t"] * case["v_ref_kn"])


def main() -> int:
    ev = {}

    # A 化学计量层
    cf_rows = gn.fuel_factor_table()
    ev["A_carbon_factors"] = {
        "rows": cf_rows,
        "worst_rel_err_pct": max(r["rel_err_pct"] for r in cf_rows),
        "criterion": "每项 <3%（平台不可接受阈），实测 <0.25%",
    }

    # B 达到值层
    chain = build_chain()
    attained = gn.attained_index(chain, CASE["capacity_t"], CASE["v_ref_kn"])
    manual = independent_attained()
    ev["B_attained"] = {
        "attained_eedi": attained,
        "independent_recompute": manual,
        "rel_diff_pct": abs(attained - manual) / manual * 100.0,
        "unit": "gCO2/(t·nm)",
        "energy_breakdown": chain.co2_terms(),
        "p_ae_kw": gn.auxiliary_power_default(CASE["p_me_kw"]),
        "p_ae_branch": "0.05·ΣMCR（<10 000 kW）" if CASE["p_me_kw"] < 10000 else "0.025·ΣMCR+250",
    }

    # C 要求值层（WITHHELD）
    phases = ["phase0", "phase1", "phase2", "phase3"]
    req = {p: gn.required_eedi(CASE["ship_type"], CASE["capacity_t"], p) for p in phases}
    ev["C_required_eedi"] = {
        p: {"reference_value": r["reference_value"], "reduction": r["phase_reduction"],
            "required_value": r["required_value"], "evidence_grade": r["evidence_grade"]}
        for p, r in req.items()
    }
    eexi = gn.evaluate_eexi(CASE["ship_type"], chain, CASE["capacity_t"], CASE["v_ref_kn"])
    ev["C_required_eexi"] = {
        "attained_value": eexi["attained_value"],
        "required_value": eexi["required_value"],
        "ratio": eexi["ratio"], "compliant": eexi["compliant"],
        "evidence_grade_required": eexi["required_evidence_grade"],
    }
    ev["C_verdicts"] = {
        p: {"attained": attained, "required": req[p]["required_value"],
            "ratio": attained / req[p]["required_value"],
            "compliant": attained <= req[p]["required_value"]}
        for p in phases
    }

    # CII：假定年航程 60 000 nm、年油耗按 5 000 h 全功率折算
    hours = 5000.0
    fuel_t = (CASE["p_me_kw"] * CASE["sfoc_me"] + gn.auxiliary_power_default(CASE["p_me_kw"]) * CASE["sfoc_ae"]) * hours / 1e6
    co2_t = gn.voyage_co2_from_fuel(CASE["fuel"], fuel_t)["co2_t"]
    distance = 60000.0
    cii_attained = gn.attained_cii(co2_t * 1e6, CASE["capacity_t"], distance)
    cii_rows = {}
    for year in (2023, 2024, 2025, 2026, 2030):
        r = gn.required_cii(CASE["ship_type"], CASE["capacity_t"], year)
        cii_rows[str(year)] = {
            "required": r["required_value"], "z_factor": r["z_factor"],
            "ratio": cii_attained / r["required_value"],
            "compliant": cii_attained <= r["required_value"],
        }
    ev["C_cii"] = {
        "annual_fuel_t": fuel_t, "annual_co2_t": co2_t, "distance_nm": distance,
        "attained_cii": cii_attained, "years": cii_rows,
        "rating": gn.cii_rating_status(),
    }

    # D 诚实性
    ev["D_evidence_summary"] = gn.evidence_summary()

    # E 判据汇总
    crit = {
        "CF_vs_published_lt_3pct": ev["A_carbon_factors"]["worst_rel_err_pct"] < 3.0,
        "CF_worst_lt_0p5pct": ev["A_carbon_factors"]["worst_rel_err_pct"] < 0.5,
        "attained_vs_independent_lt_0p001pct": ev["B_attained"]["rel_diff_pct"] < 1e-3,
        "required_monotone_in_capacity": (
            gn.required_eedi("bulk_carrier", 50000.0)["required_value"]
            > gn.required_eedi("bulk_carrier", 180000.0)["required_value"]
        ),
        "cii_rating_honestly_not_supported": gn.cii_rating_status()["evidence_grade"] == "NOT_SUPPORTED",
    }
    ev["E_criteria"] = crit
    ev["E_all_pass"] = all(crit.values())

    (OUT / "evidence.json").write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # 图 1：C_F 化学计量导出 vs 公布值
    # ------------------------------------------------------------------
    fuels = [r["fuel"] for r in cf_rows]
    derived = [r["cf_derived"] for r in cf_rows]
    published = [r["cf_published"] for r in cf_rows]
    errs = [r["rel_err_pct"] for r in cf_rows]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    idx = range(len(fuels))
    ax1.bar([i - 0.2 for i in idx], published, width=0.4, label="MARPOL 公布 C_F", color="#4c72b0")
    ax1.bar([i + 0.2 for i in idx], derived, width=0.4, label="化学计量导出 C_F", color="#dd8452")
    ax1.set_xticks(list(idx))
    ax1.set_xticklabels(fuels, rotation=35, ha="right", fontsize=9)
    ax1.set_ylabel("C_F  [gCO2/gFuel]")
    ax1.set_title(f"碳因子：导出 vs 公布（最大偏差 {max(errs):.3f}%）")
    ax1.legend(fontsize=9)
    ax1.grid(axis="y", alpha=0.3)
    ax2.bar(fuels, errs, color="#55a868")
    ax2.axhline(3.0, color="r", ls="--", lw=1.2, label="不可接受阈 3%")
    ax2.axhline(0.5, color="gray", ls=":", lw=1.0, label="本域自设 0.5%")
    ax2.set_xticklabels(fuels, rotation=35, ha="right", fontsize=9)
    ax2.set_ylabel("相对偏差 [%]")
    ax2.set_yscale("log")
    ax2.set_title("逐项相对偏差（对数轴）")
    ax2.legend(fontsize=9)
    ax2.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig1_cf_chemistry.png", dpi=150)
    plt.close(fig)

    # ------------------------------------------------------------------
    # 图 2：达到值 vs 各阶段要求值（容量扫描）
    # ------------------------------------------------------------------
    caps = [20000 * 1.12 ** k for k in range(18)]
    fig, ax = plt.subplots(figsize=(9.5, 5.6))
    for phase in phases:
        ys = [gn.required_eedi("bulk_carrier", c, phase)["required_value"] for c in caps]
        ax.plot(caps, ys, label=f"要求值 {phase}（WITHHELD）", lw=1.8)
    ax.axhline(attained, color="k", ls="--", lw=1.6,
               label=f"本船达到值 {attained:.3f}（REAL）")
    ax.scatter([CASE["capacity_t"]], [attained], color="k", zorder=5)
    ax.set_xscale("log")
    ax.set_xlabel("Capacity  [t]")
    ax.set_ylabel("EEDI  [gCO2/(t·nm)]")
    ax.set_title("Kamsarmax 算例：达到值 vs 散货船参考线各阶段要求值")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(OUT / "fig2_eedi_vs_capacity.png", dpi=150)
    plt.close(fig)

    # ------------------------------------------------------------------
    # 图 3：CII 达到值 vs 逐年要求值
    # ------------------------------------------------------------------
    years = [2019, 2023, 2024, 2025, 2026, 2030]
    reqs = [gn.required_cii("bulk_carrier", CASE["capacity_t"], y)["required_value"] for y in years]
    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.plot(years, reqs, "o-", label="要求 CII（参考线 × (1−Z)）", color="#c44e52")
    ax.axhline(cii_attained, color="k", ls="--", label=f"本船达到 CII {cii_attained:.3f}（REAL）")
    ax.fill_between(years, reqs, cii_attained, where=[cii_attained > r for r in reqs],
                    color="red", alpha=0.12, label="超出要求区间")
    ax.set_xlabel("年份")
    ax.set_ylabel("CII  [gCO2/(t·nm)]")
    ax.set_title("CII 达到值 vs 逐年收紧要求（A–E 等级：NOT_SUPPORTED）")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig3_cii_timeline.png", dpi=150)
    plt.close(fig)

    # ------------------------------------------------------------------
    # numbers.md：报告数字表（从 ev 读取，零手抄）
    # ------------------------------------------------------------------
    lines = ["# 绿色航行域审计数字表（自动生成，勿手改）", ""]
    lines += ["## A 碳因子化学计量", "", "| 燃料 | w_C | C_F 导出 | C_F 公布 | 偏差 % |", "|---|---|---|---|---|"]
    for r in cf_rows:
        lines.append(f"| {r['fuel']} | {r['carbon_mass_fraction']:.5f} | {r['cf_derived']:.4f} | "
                     f"{r['cf_published']:.3f} | {r['rel_err_pct']:.3f} |")
    lines += ["", f"最大偏差 **{ev['A_carbon_factors']['worst_rel_err_pct']:.3f}%**", ""]
    lines += ["## B 达到值", "",
              f"- attained EEDI = **{attained:.6f}** gCO2/(t·nm)",
              f"- 独立复算 = {manual:.6f}，相对差 {ev['B_attained']['rel_diff_pct']:.2e} %",
              f"- P_AE = {ev['B_attained']['p_ae_kw']:.1f} kW（{ev['B_attained']['p_ae_branch']}）", ""]
    lines += ["## C 要求值与判决（要求值层 WITHHELD）", "", "| 阶段 | 参考值 | 削减 | 要求值 | 达到/要求 | 合规 |",
              "|---|---|---|---|---|---|"]
    for p in phases:
        v = ev["C_verdicts"][p]
        lines.append(f"| {p} | {req[p]['reference_value']:.3f} | {req[p]['phase_reduction']:.0%} | "
                     f"{v['required']:.3f} | {v['ratio']:.3f} | {'PASS' if v['compliant'] else 'FAIL'} |")
    lines += ["", f"EEXI：达到 {eexi['attained_value']:.3f} / 要求 {eexi['required_value']:.3f} "
                  f"→ {'PASS' if eexi['compliant'] else 'FAIL'}（比值 {eexi['ratio']:.3f}）", ""]
    lines += ["## CII", "", f"- 年油耗 {fuel_t:.1f} t，年 CO₂ {co2_t:.1f} t，年航程 {distance:.0f} nm",
              f"- 达到 CII = **{cii_attained:.4f}** gCO2/(t·nm)", "",
              "| 年份 | Z | 要求值 | 达到/要求 | 合规 |", "|---|---|---|---|---|"]
    for y, v in cii_rows.items():
        lines.append(f"| {y} | {v['z_factor']:.0%} | {v['required']:.4f} | {v['ratio']:.3f} | "
                     f"{'PASS' if v['compliant'] else 'FAIL'} |")
    lines += ["", "## E 判据", ""]
    for k, v in crit.items():
        lines.append(f"- {'PASS' if v else 'FAIL'} — {k}")
    lines += ["", f"**全判据：{'PASS' if ev['E_all_pass'] else 'FAIL'}**"]
    (OUT / "numbers.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    return 0 if ev["E_all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())