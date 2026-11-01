"""SUBOFF 阻力验证（正典 v3.0 供给版）—— m1709 验证欠账清偿。

背景（为什么重写）
------------------
`tools/validate_suboff_resistance.py`（m1444，已合并）把 SUBOFF 参考值**硬编码**在脚本里：

    {"case_id": "SUBOFF-01", "speed_ms": 3.0, "fn": 0.236, "ct_exp": 0.0038, "rt_exp_n": 12.50}
    ...
    SUBOFF_GEOM = {..., "displacement": 0.508}   # 把 beam 抄进了 displacement

三处缺陷（均为可机器裁决的不一致，不是"精度不够"）：

D1 **引用已作废的参考值**：`backend/data/benchmark_reference.json` v3.0 的
   `adjudication_reason` 明确写「v1 的 12.5 N 反解湿面积仅 0.71 m²，与 R1 差 7.6 倍，直接作废」，
   并把它保留在 `legacy_values.v1_experimental_rt_n`。旧脚本正文用的正是这个作废值
   （SUBOFF-01..04 的 12.50/21.00/31.50/45.00 N = 1/7.4 比例的一组旧表），
   而同一行的 `ct_exp=0.0038` 却是正典有效值 → **参考侧自相矛盾**。
D2 **参考侧不自洽**：正典 R1 恒等式 Rt = Ct·½ρV²S。以 SUBOFF-01 计
   0.0038·0.5·998.2·3²·5.42 = 92.52 N ≠ 12.50 N（差 7.4 倍）。
   Ct 与 Rt 不可能同时为真，旧脚本却把两者并列成"实验值"。
D3 **Froude 数列错**：旧脚本 fn=0.236/0.315/0.393/0.472，而 L=4.356 m、V=3 m/s 时
   Fr = V/√(gL) = 0.4589（正典 `froude_basis` 一致）。旧数列恰为真值的一半量级，
   使 Ct–Fn 对比图的横坐标整体错误。

因此 m1444 报告的结论「Rt 误差 ~99%、HONEST-FAIL」是**数据侧缺陷造成**的，
不是模型的真实精度。本工具做的是把参考侧换成单一真源（正典 v3.0）后重算误差。

方法
----
1. 参考侧：只从 `backend/data/benchmark_reference.json`（正典 v3.0）读 SUBOFF 四行，
   不存任何字面量；顺带跑两条供给自检：
     R1 = Rt_ref vs Ct_ref·½ρV²S_wet（正典恒等式）
     R3 = Fr vs V/√(gL)（正典派生量）
2. 引擎侧：直接调用**仓库内实现** `backend/services/ship_resistance.py`
   （与端点 `POST /calculations/resistance` 同一组公式：ITTC-57 摩擦线 +
   Cr = 0.002(1+2Cb)Fn^2.5），离线运行，不依赖服务可达性。
   两种湿面积口径并列：`S_canon`（几何/实测 5.42 m²）与 `S_empirical`（引擎自带拟合式），
   因为"面积项"本身是另一条已知欠账（m1666）。
3. 输出：`outputs/suboff_canon_validation/ledger.json` + `fig1_ct_fn.png` + `fig2_error.png`

判定纪律
--------
两档精度线：3%（严格）/ 15%（不可接受线）。结果无论好坏都如实打印，脚本不"修"误差。

复现
----
    python3 tools/validate_suboff_resistance_canon.py
    python3 -m pytest tests/test_suboff_canon_audit.py -q
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
CANON_PATH = BACKEND / "data" / "benchmark_reference.json"
OUT_DIR = ROOT / "outputs" / "suboff_canon_validation"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import services.ship_resistance as sr  # noqa: E402  (需在 sys.path 注入后导入)

G = 9.81


# --------------------------------------------------------------------------- #
# 参考侧：唯一真源 = 正典 v3.0
# --------------------------------------------------------------------------- #
def load_canon() -> dict:
    with CANON_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def suboff_cases(canon: dict | None = None) -> list[dict]:
    """返回正典 v3.0 的 SUBOFF 四行，字段直取，禁止字面量。"""
    canon = canon or load_canon()
    out = []
    for case in canon["cases"]:
        if case["ship_type"] != "SUBOFF":
            continue
        geom = case["geometry"]
        out.append({
            "case_id": case["case_id"],
            "speed_ms": case["speed_ms"],
            "length_m": geom["length_m"],
            "diameter_m": geom["diameter_m"],
            "wetted_surface_m2": geom["wetted_surface_m2"],
            "rho": case["reference_density_kg_m3"],
            "nu": case["reference_kinematic_viscosity_m2_s"],
            "ct_ref": case["experimental_ct"],
            "rt_ref_n": case["experimental_rt_n"],
            "fr_canon": case["froude_number"],
            "legacy_rt_n": case.get("legacy_values", {}).get("v1_experimental_rt_n"),
            "source_citation": case["source_citation"],
        })
    return sorted(out, key=lambda c: c["speed_ms"])


def reference_closure(case: dict) -> dict:
    """正典 R1/R3 供给自检：参考值自身必须闭合，否则一切误差判定无意义。"""
    q = 0.5 * case["rho"] * case["speed_ms"] ** 2 * case["wetted_surface_m2"]
    rt_from_ct = case["ct_ref"] * q
    fr = case["speed_ms"] / math.sqrt(G * case["length_m"])
    return {
        "rt_from_identity_n": rt_from_ct,
        "rt_ref_n": case["rt_ref_n"],
        "closure_rel_err": abs(rt_from_ct - case["rt_ref_n"]) / case["rt_ref_n"],
        "fr_derived": fr,
        "fr_canon": case["fr_canon"],
        "fr_rel_err": abs(fr - case["fr_canon"]) / case["fr_canon"],
    }


# --------------------------------------------------------------------------- #
# 引擎侧：仓库内实现（与端点同一组公式）
# --------------------------------------------------------------------------- #
def engine_resistance(case: dict, use_canon_surface: bool) -> dict:
    re = sr.reynolds_number(case["speed_ms"], case["length_m"], case["nu"])
    fr = sr.froude_number(case["speed_ms"], case["length_m"], G)
    cf = sr.ittc57_cf(re)
    if use_canon_surface:
        s_wet = case["wetted_surface_m2"]
        s_src = "canon_geometry"
    else:
        s_wet = sr.wetted_surface_axisym(case["diameter_m"], case["length_m"], 0.542)
        s_src = "engine_empirical"
    cr = sr.residual_coefficient(fr, 0.542)
    q = 0.5 * case["rho"] * case["speed_ms"] ** 2 * s_wet
    rt = (cf + cr) * q
    return {
        "re": re, "fr": fr, "cf": cf, "cr": cr, "s_wet_m2": s_wet,
        "s_source": s_src, "rt_n": rt, "ct": sr.ct_from_rt(rt, case["rho"], s_wet, case["speed_ms"]),
    }


def build_ledger() -> dict:
    canon = load_canon()
    cases = suboff_cases(canon)
    rows = []
    for case in cases:
        ref = reference_closure(case)
        eng_canon = engine_resistance(case, True)
        eng_emp = engine_resistance(case, False)
        row = {
            "case_id": case["case_id"],
            "speed_ms": case["speed_ms"],
            "fr_canon": case["fr_canon"],
            "ct_ref": case["ct_ref"],
            "rt_ref_n": case["rt_ref_n"],
            "legacy_rt_n": case["legacy_rt_n"],
            "reference_closure": ref,
            "engine_s_canon": {**eng_canon,
                               "ct_err_pct": 100.0 * (eng_canon["ct"] - case["ct_ref"]) / case["ct_ref"],
                               "rt_err_pct": 100.0 * (eng_canon["rt_n"] - case["rt_ref_n"]) / case["rt_ref_n"]},
            "engine_s_empirical": {**eng_emp,
                                   "ct_err_pct": 100.0 * (eng_emp["ct"] - case["ct_ref"]) / case["ct_ref"],
                                   "rt_err_pct": 100.0 * (eng_emp["rt_n"] - case["rt_ref_n"]) / case["rt_ref_n"]},
        }
        rows.append(row)

    def summarize(key: str) -> dict:
        errs = [abs(r[key]["rt_err_pct"]) for r in rows]
        cerrs = [abs(r[key]["ct_err_pct"]) for r in rows]
        return {
            "rt_abs_err_pct_max": max(errs),
            "rt_abs_err_pct_mean": sum(errs) / len(errs),
            "ct_abs_err_pct_max": max(cerrs),
            "ct_abs_err_pct_mean": sum(cerrs) / len(cerrs),
            "all_rt_within_3pct": all(e < 3.0 for e in errs),
            "all_rt_within_15pct": all(e < 15.0 for e in errs),
            "all_ct_within_3pct": all(e < 3.0 for e in cerrs),
            "all_ct_within_15pct": all(e < 15.0 for e in cerrs),
        }

    return {
        "mission": 1709,
        "canon_version": canon.get("version"),
        "canon_description": canon.get("description"),
        "reference_citation": cases[0]["source_citation"] if cases else None,
        "rows": rows,
        "summary_s_canon": summarize("engine_s_canon"),
        "summary_s_empirical": summarize("engine_s_empirical"),
        "defect_found": {
            "superseded_reference_rt_n": sorted({r["legacy_rt_n"] for r in rows if r["legacy_rt_n"]}),
            "note": "m1444 工具把正典 v3.0 已作废的 legacy Rt 当作实验值，且 Ct 用有效值，参考侧自相矛盾",
        },
    }


# --------------------------------------------------------------------------- #
# 出图
# --------------------------------------------------------------------------- #
def make_figures(ledger: dict) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = ledger["rows"]
    fr = [r["fr_canon"] for r in rows]
    figs = []

    fig, ax = plt.subplots(figsize=(7, 4.6))
    ax.plot(fr, [r["ct_ref"] for r in rows], "ko-", label="canon v3.0 reference Ct")
    ax.plot(fr, [r["engine_s_canon"]["ct"] for r in rows], "s--",
            label="engine Ct (S = canon 5.42 m$^2$)")
    ax.plot(fr, [r["engine_s_empirical"]["ct"] for r in rows], "^:",
            label="engine Ct (S = engine empirical)")
    ax.set_xlabel("Froude number $F_n$")
    ax.set_ylabel("$C_T$")
    ax.set_ylim(bottom=0)
    ax.set_title("DARPA SUBOFF: resistance coefficient vs $F_n$ (canon v3.0 supply)")
    ax.grid(alpha=0.3)
    ax.legend()
    p1 = OUT_DIR / "fig1_ct_fn.png"
    fig.savefig(p1, dpi=150, bbox_inches="tight")
    plt.close(fig)
    figs.append(p1)

    fig, ax = plt.subplots(figsize=(7, 4.6))
    ax.plot(fr, [r["engine_s_canon"]["rt_err_pct"] for r in rows], "s--",
            label="Rt error, S = canon")
    ax.plot(fr, [r["engine_s_empirical"]["rt_err_pct"] for r in rows], "^:",
            label="Rt error, S = empirical")
    ax.axhline(0, color="k", linewidth=0.8)
    for band, style in ((3.0, ":"), (15.0, "--")):
        ax.axhline(band, color="r", linestyle=style, linewidth=1.0)
        ax.axhline(-band, color="r", linestyle=style, linewidth=1.0)
    ax.set_xlabel("Froude number $F_n$")
    ax.set_ylabel("$R_t$ relative error [%]")
    ax.set_title("SUBOFF $R_t$ error vs acceptance bands ($\\pm$3%, $\\pm$15%)")
    ax.grid(alpha=0.3)
    ax.legend()
    p2 = OUT_DIR / "fig2_error.png"
    fig.savefig(p2, dpi=150, bbox_inches="tight")
    plt.close(fig)
    figs.append(p2)
    return figs


def main() -> int:
    ledger = build_ledger()
    figs = make_figures(ledger)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger["figures"] = [str(p.relative_to(ROOT)) for p in figs]
    (OUT_DIR / "ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"canon v{ledger['canon_version']}  ({ledger['reference_citation'][:60]}...)")
    print(f"{'case':12s} {'V':>5s} {'Fr':>7s} {'Rt_ref':>9s} {'Rt(S_canon)':>12s} "
          f"{'err%':>7s} {'Rt(S_emp)':>10s} {'err%':>7s}")
    for r in ledger["rows"]:
        print(f"{r['case_id']:12s} {r['speed_ms']:5.1f} {r['fr_canon']:7.4f} "
              f"{r['rt_ref_n']:9.2f} {r['engine_s_canon']['rt_n']:12.2f} "
              f"{r['engine_s_canon']['rt_err_pct']:+7.2f} "
              f"{r['engine_s_empirical']['rt_n']:10.2f} {r['engine_s_empirical']['rt_err_pct']:+7.2f}")
    print()
    for key, label in (("summary_s_canon", "S = canon 5.42 m2"),
                       ("summary_s_empirical", "S = engine empirical fit")):
        s = ledger[key]
        print(f"[{label}] Rt |err| mean {s['rt_abs_err_pct_mean']:.2f}% / max {s['rt_abs_err_pct_max']:.2f}%  "
              f"<3%? {s['all_rt_within_3pct']}  <15%? {s['all_rt_within_15pct']}")
        print(f"{'':>18}  Ct |err| mean {s['ct_abs_err_pct_mean']:.2f}% / max {s['ct_abs_err_pct_max']:.2f}%  "
              f"<3%? {s['all_ct_within_3pct']}  <15%? {s['all_ct_within_15pct']}")
    print(f"\nledger -> {OUT_DIR / 'ledger.json'}")

    # 供给自检必须闭合，否则拒绝出账（fail-closed）
    worst = max(r["reference_closure"]["closure_rel_err"] for r in ledger["rows"])
    worst_fr = max(r["reference_closure"]["fr_rel_err"] for r in ledger["rows"])
    print(f"reference closure worst rel err = {worst:.2e} ; Froude worst rel err = {worst_fr:.2e}")
    if worst > 1e-3 or worst_fr > 1e-3:
        print("REFERENCE SUPPLY NOT CLOSED -> refuse to certify")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())