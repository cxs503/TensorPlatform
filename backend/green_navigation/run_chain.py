#!/usr/bin/env python3
"""跑绿色航行能效链并把成果落盘：``artifacts/green_navigation/``。

产出
----
* ``results.json``          —— 全部算例 + E1..E8 判据数字 + 证据等级 + 产物路径
* ``figures/fig1_sfoc.png`` —— SFOC 标定 vs 转录参考表（残差）
* ``figures/fig2_eedi.png`` —— EEDI_attained vs 各阶段 required + IMO 分组均值锚
* ``figures/fig3_power_speed.png`` —— 阻力-功率-自航点（设计点 / 75% MCR 点）
* ``figures/fig4_cii_trajectory.png`` —— 5 年 CII 退化轨迹 + 评级漂移 + 两场景
* ``figures/fig5_rating_bands.png`` —— CII 比值与 dd 评级带（三船型）

用法::

    PYTHONPATH=backend python3 backend/green_navigation/run_chain.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]          # worktree 根
_BACKEND = _ROOT / "backend"
for p in (str(_ROOT), str(_BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

import matplotlib                                      # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                        # noqa: E402

from green_navigation import chain, cii as cii_mod, matching as matching_mod  # noqa: E402

OUT_DIR = _ROOT / "artifacts" / "green_navigation"
FIG_DIR = OUT_DIR / "figures"


def _fig_sfoc(result, path: Path) -> None:
    model = result["sfoc"]["model"]
    pts_tbl = result["sfoc"]["validation"]["points"]
    loads = [p["load"] for p in pts_tbl]
    sfocs = [p["sfoc_ref"] for p in pts_tbl]
    from green_navigation.engine import SfocMap
    m = SfocMap(sfoc_min_g_kwh=model["sfoc_min_g_kwh"], a=model["a"], b=model["b"],
                p_opt=model["p_opt"], n_opt=model["n_opt"])
    grid = [0.2 + 0.01 * i for i in range(81)]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    ax.plot(grid, [m.sfoc(x) for x in grid], "-", lw=2,
            label=f"model: {model['sfoc_min_g_kwh']:.2f} + {model['a']:.2f}(P-0.8)$^2$")
    ax.plot(loads, sfocs, "o", ms=6, label="transcribed part-load table (CONF_MED)")
    ax.axvspan(0.75, 0.90, color="grey", alpha=0.15, label="criterion window 0.75-0.90")
    ax.set_xlabel("P / P$_{MCR}$ [-]")
    ax.set_ylabel("SFOC [g/kWh]")
    ax.set_title("E1: SFOC map calibration")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    pts = result["sfoc"]["validation"]["points"]
    ax2.bar([f"{p['load']:.2f}" for p in pts], [p["rel_err_pct"] for p in pts])
    ax2.axhline(5.0, color="r", ls="--", label="5% criterion")
    ax2.set_xlabel("P / P$_{MCR}$ [-]")
    ax2.set_ylabel("|model - table| / table  [%]")
    ax2.set_title(f"residuals (max {result['sfoc']['validation']['max_rel_err_pct']:.2f}%)")
    ax2.legend(fontsize=8)
    ax2.tick_params(axis="x", labelsize=7)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def _fig_eedi(result, path: Path) -> None:
    cases = result["cases"]
    labels = [c["case_id"] for c in cases]
    x = range(len(cases))
    phases = ("phase1", "phase2", "phase3")
    width = 0.2
    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    ax.bar([i - 1.5 * width for i in x], [c["eedi"]["attained"]["attained_g_per_t_nm"] for c in cases],
           width, label="EEDI attained (REAL)", color="#1f77b4")
    for k, ph in enumerate(phases):
        ax.bar([i + (k - 0.5) * width for i in x],
               [c["eedi"]["verdicts"][ph]["required_g_per_t_nm"] for c in cases],
               width, label=f"required {ph} (WITHHELD coeffs)",
               color=["#bbbbbb", "#888888", "#444444"][k])
    for i, c in enumerate(cases):
        anc = c["eedi"]["external_anchor"]
        if anc and anc.get("use_for_e3"):
            ax.plot(i, anc["reference_g_per_t_nm"], "r*", ms=14,
                    label="IMO DB class mean" if i == 0 else None)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("gCO$_2$/(t$\\cdot$nm)")
    ax.set_title("EEDI attained vs required phases (+ class-mean anchor)")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def _fig_power_speed(case_data, case_spec, path: Path) -> None:
    geo = case_spec["geometry"]
    hull = case_spec.get("hull_coeffs", {})
    ship = matching_mod.ShipGeometry(
        length_m=geo["L_m"], beam_m=geo["B_m"], draft_m=geo["T_m"], cb=geo["cb"],
        displacement_t=case_spec.get("displacement_t"),
        cm=hull.get("cm"), cwp=hull.get("cwp"),
        lcb_pct=hull.get("lcb_pct", 0.0), i_e_deg=hull.get("i_e_deg", 25.0),
    )
    p = case_spec["propeller"]
    prop = matching_mod.PropellerSpec(
        diameter_m=p["diameter_m"], pitch_ratio=p["pitch_ratio"], ae_ao=p["ae_ao"],
        blades=p["blades"], wake_fraction=p["wake_fraction"],
        thrust_deduction=p["thrust_deduction"],
        relative_rotative_efficiency=p.get("relative_rotative_efficiency", 1.0))
    pt = case_spec["powertrain"]
    train = matching_mod.PowertrainSpec(eta_shaft=pt["eta_shaft"], eta_gear=pt["eta_gear"])

    speeds = [8 + 0.5 * i for i in range(21)]
    pbs, pes = [], []
    for v in speeds:
        mp = matching_mod.match_at_speed(ship, prop, train, v)
        pbs.append(mp.brake_power_kw)
        pes.append(mp.effective_power_kw)
    fig, ax = plt.subplots(figsize=(8, 4.6))
    ax.plot(speeds, pbs, "-o", ms=3, label="P$_B$ = P$_E$/(η$_O$η$_R$η$_H$η$_{shaft}$η$_{gear}$)")
    ax.plot(speeds, pes, "-s", ms=3, label="P$_E$ = R$_T$V")
    ax.axhline(case_data["engine"]["mcr_kw"], color="k", ls=":", label="engine MCR")
    ax.axhline(0.75 * case_data["engine"]["mcr_kw"], color="r", ls="--", label="75% MCR (EEDI point)")
    ax.plot(case_data["design_point"]["speed_kn"], case_data["design_point"]["brake_power_kw"], "r*", ms=15,
            label=f"design point {case_data['power_fraction_at_design_speed']:.3f} MCR")
    ax.plot(case_data["eedi"]["v_ref_kn"], case_data["eedi"]["match_point_75pct"]["brake_power_kw"],
            "bx", ms=9, label="matched V$_{ref}$ at 75% MCR")
    ax.set_xlabel("V [kn]")
    ax.set_ylabel("power [kW]")
    ax.set_title(f"{case_data['case_id']}: resistance-propeller matching (E2)")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def _fig_cii_trajectory(result, path: Path) -> None:
    fig, axes = plt.subplots(1, len(result["cases"]), figsize=(13.5, 4.4), sharey=False)
    for ax, c in zip(axes, result["cases"]):
        for key, color, label in (("maintained_15um_per_year", "#1f77b4", "maintained 15 µm/yr"),
                                  ("uncleaned_30um_per_year", "#d62728", "uncleaned 30 µm/yr")):
            tr = c["cii"]["sensitivity_scenarios"][key]
            yrs = [y["year"] for y in tr["years"]]
            ax.plot(yrs, [y["attained_g_per_t_nm"] for y in tr["years"]], "-o", color=color, label=label)
        base = c["cii"]["trajectory"]
        yrs = [y["year"] for y in base["years"]]
        ax.plot(yrs, [y["required_g_per_t_nm"] for y in base["years"]], "k--", label="required CII (Z tightening)")
        for y in base["years"]:
            ax.annotate(y["rating"], (y["year"], y["attained_g_per_t_nm"]),
                        textcoords="offset points", xytext=(0, 6), fontsize=7, ha="center")
        ax.set_title(f"{c['case_id']}  dCII {base['degradation_pct_per_year']:.2f}%/yr", fontsize=9)
        ax.set_xlabel("year")
        ax.set_ylabel("CII [gCO$_2$/(t$\\cdot$nm)]")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=6)
    fig.suptitle("E5: 5-year hull-roughness degradation trajectory + CII rating drift", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def _fig_rating_bands(result, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for row, c in enumerate(result["cases"]):
        bnd = c["cii"]["boundaries"]
        dd = bnd["dd"]
        edges = [0.5] + dd + [max(dd[-1] * 1.25, 3.0)]
        colors = ["#2ca02c", "#98df8a", "#ffd27f", "#ff9f9f", "#d62728"]
        for i, grade in enumerate("ABCDE"):
            ax.barh(row, edges[i + 1] - edges[i], left=edges[i], height=0.5,
                    color=colors[i], alpha=0.75, edgecolor="w")
            ax.text(0.5 * (edges[i] + edges[i + 1]), row, grade, ha="center", va="center", fontsize=9)
        ratio = c["cii"]["trajectory"]["years"][0]["ratio_to_required"]
        ax.plot(ratio, row, "k|", ms=22, label="year-1 ratio" if row == 0 else None)
    ax.set_yticks(range(len(result["cases"])))
    ax.set_yticklabels([c["case_id"] for c in result["cases"]], fontsize=8)
    ax.set_xlabel("attained CII / required CII  (dd vector boundaries, MEPC.354(78) G4, CONF_MED)")
    ax.set_title("E4: CII rating bands (logic REAL, boundaries WITHHELD_PROVENANCE)")
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(alpha=0.3, axis="x")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    result = chain.run_all()
    figures = {
        "fig1_sfoc_calibration": "SFOC 标定曲线与转录部分负荷参考表逐点残差（E1，最大 0.63%）",
        "fig2_eedi_attained_vs_required": "三算例 EEDI 达到值 vs 各阶段要求值 + IMO 分组均值锚（E3）",
        "fig3_power_speed_matching": "KVLCC2 阻力-功率-自航点曲线（设计点 0.861 MCR、75% MCR 反解 V_ref）",
        "fig4_cii_degradation": "5 年船体粗糙度退化轨迹 + 评级漂移（E5，两曝露场景）",
        "fig5_cii_rating_bands": "CII 比值落在 dd 评级带的位置（E4）",
    }
    _fig_sfoc(result, FIG_DIR / "fig1_sfoc_calibration.png")
    _fig_eedi(result, FIG_DIR / "fig2_eedi_attained_vs_required.png")
    _fig_power_speed(result["cases"][0], chain.load_cases()[0],
                     FIG_DIR / "fig3_power_speed_matching.png")
    _fig_cii_trajectory(result, FIG_DIR / "fig4_cii_degradation.png")
    _fig_rating_bands(result, FIG_DIR / "fig5_cii_rating_bands.png")

    result["artifacts"] = {
        "results_json": "artifacts/green_navigation/results.json",
        "figures_dir": "artifacts/green_navigation/figures",
        "figures": {k: f"artifacts/green_navigation/figures/{k}.png" for k in figures},
        "module": "backend/green_navigation/",
        "tests": "backend/green_navigation/tests/",
    }
    result["figures_captions"] = figures
    out = OUT_DIR / "results.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
                   encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    acc = result["acceptance"]
    print(f"criteria passed {acc['passed_count']}/{acc['total']}; "
          f"withheld/honest: {acc['honest_fail_or_withheld']}")
    for k, v in acc["criteria"].items():
        print(f"  {k}: passed={v.get('passed')} status={v.get('status', '-')} "
              f"value={v.get('value_pct', v.get('value'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())