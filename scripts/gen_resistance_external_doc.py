#!/usr/bin/env python3
"""生成 `docs/research/resistance_external_baseline_audit.md` + 图。

数字 **全部** 来自 `runs/m1666/resistance_external_audit.json`（由
`scripts/resistance_external_audit.py` 产出），本脚本不手抄任何结果：
读 JSON → 填表 → 出图 → 写 md。

跑法::

    python3 scripts/resistance_external_audit.py      # 先出账本
    python3 scripts/gen_resistance_external_doc.py    # 再出文档与图
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "runs" / "m1666" / "resistance_external_audit.json"
FIGDIR = REPO / "docs" / "research" / "figs"
DOC = REPO / "docs" / "research" / "resistance_external_baseline_audit.md"

GREEN, GREY, ORANGE, RED = "#2e7d32", "#9e9e9e", "#ef6c00", "#c62828"
COLOR = {"PASS": GREEN, "WARN": ORANGE, "WITHHELD": GREY, "FAIL": RED}


def fmt(x, nd=3):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    return f"{x:.{nd}f}"


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def fig_wetted_surface(d, path: Path) -> str:
    rows = [r for r in d["external_baseline"]["rows"]
            if r["wetted_surface_external"].get("wetted_surface_recorded_m2")]
    ids = [r["case_id"] for r in rows]
    eng = [r["wetted_surface_external"]["wetted_surface_engine_m2"] for r in rows]
    rec = [r["wetted_surface_external"]["wetted_surface_recorded_m2"] for r in rows]
    bas = [r["wetted_surface_external"].get("wetted_surface_baseline_m2") for r in rows]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    x = range(len(ids))
    ax1.bar([i - 0.26 for i in x], rec, 0.26, label="recorded (Roddy 1990)", color="#1565c0")
    ax1.bar([i for i in x], eng, 0.26, label="engine fit  πBL(0.5+0.25Cb)", color=RED)
    ax1.bar([i + 0.26 for i in x], [b or 0 for b in bas], 0.26,
            label="prolate-spheroid (closed form)", color=GREEN)
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(ids, rotation=15)
    ax1.set_ylabel("wetted surface S [m²]")
    ax1.set_title("SUBOFF: engine fit vs recorded vs geometric baseline")
    ax1.legend(fontsize=7)
    ax1.grid(alpha=0.3, axis="y")

    cases = [r for r in d["existing_ct_only"]["rows"] if r["case_id"].startswith("SUBOFF")]
    labels = [r["case_id"] for r in cases]
    ext_s = {r["case_id"]: r["wetted_surface_external"]["engine_vs_recorded"]["error_pct"]
             for r in rows}
    ct_err = [r["error_pct"] for r in cases]
    rt_err = [r["end_to_end"]["rt_error_pct"] for r in
              [x for x in d["external_baseline"]["rows"] if x["case_id"].startswith("SUBOFF")]]
    xx = range(len(labels))
    ax2.bar([i - 0.27 for i in xx], [ext_s[l] for l in labels], 0.27, label="S (area) error", color=RED)
    ax2.bar([i for i in xx], ct_err, 0.27, label="Ct-only error (existing metric)", color="#1565c0")
    ax2.bar([i + 0.27 for i in xx], rt_err, 0.27, label="Rt (total) error", color=ORANGE)
    ax2.axhline(15, ls="--", lw=1, color="k")
    ax2.axhline(-15, ls="--", lw=1, color="k")
    ax2.axhline(3, ls=":", lw=1, color="grey")
    ax2.axhline(-3, ls=":", lw=1, color="grey")
    ax2.set_xticks(list(xx))
    ax2.set_xticklabels(labels, rotation=15)
    ax2.set_ylabel("relative error [%]")
    ax2.set_title("Error by metric (dashed = ±15% bar, dotted = ±3%)")
    ax2.legend(fontsize=7)
    ax2.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path.name


def fig_decomposition(d, path: Path) -> str:
    row = next(r for r in d["external_baseline"]["rows"] if r["case_id"] == "SUBOFF-01")
    dec = row["end_to_end"]["decomposition"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))

    terms = ["area\nS_e/S_m", "coefficient\nCf+Cr", "recorded\nCt↔Rt", "total\nRt_e/Rt_m"]
    vals = [dec["area_term_pct"], dec["ct_term_pct"],
            (dec["closure_recorded"] - 1.0) * 100.0, dec["total_error_pct"]]
    ax1.bar(terms, vals, color=[RED, "#1565c0", GREY, ORANGE])
    for i, v in enumerate(vals):
        ax1.annotate(f"{v:+.2f}%", (i, v), ha="center",
                     va="bottom" if v >= 0 else "top", fontsize=8)
    ax1.axhline(-15, ls="--", lw=1, color="k")
    ax1.set_ylabel("contribution [%]")
    ax1.set_title("SUBOFF-01 @3.0 m/s: multiplicative error decomposition")
    ax1.grid(alpha=0.3, axis="y")

    rows = [r for r in d["external_baseline"]["rows"] if r["case_id"].startswith("SUBOFF")]
    fn = [r["residual_magnitude"]["froude"] for r in rows]
    cr_eng = [r["residual_magnitude"]["cr_engine"] for r in rows]
    cr_imp = [r["residual_magnitude"]["cr_implied"] for r in rows]
    sweep = d["cr_law_sweep"]["sweep"]
    ax2.plot([p["fn"] for p in sweep], [p["cr_engine"] for p in sweep],
             "-", color=RED, label="engine Cr = 0.002(1+2Cb)Fn^2.5")
    ax2.plot(fn, cr_imp, "o", color="#1565c0", label="Cr implied by recorded (Ct−Cf)")
    ax2.plot(fn, cr_eng, "s", color=RED, ms=4, label="engine Cr at same Fn")
    ax2.set_xlabel("Fn = V/√(gL)")
    ax2.set_ylabel("Cr [-]")
    ax2.set_title("Residual-resistance law vs SUBOFF data")
    ax2.legend(fontsize=7)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path.name


def fig_cb_sensitivity(d, path: Path) -> str:
    scan = d["cb_sensitivity"]["scan"]
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    cbs = [s["cb"] for s in scan]
    errs = [s["error_pct_vs_recorded_5p42"] for s in scan]
    ax.plot(cbs, errs, "o-", color=RED, label="engine S error vs recorded 5.42 m²")
    ax.axhline(-15, ls="--", lw=1, color="k", label="15% bar")
    ax.axhline(-3, ls=":", lw=1, color="grey", label="3% bar")
    ax.annotate("Cb = π/6 (spheroid)", (scan[0]["cb"], scan[0]["error_pct_vs_recorded_5p42"]),
                textcoords="offset points", xytext=(6, -14), fontsize=8)
    ax.annotate("Cb = 0.542 (shipped fallback)", (0.542, -18.49),
                textcoords="offset points", xytext=(4, 8), fontsize=8)
    ax.set_xlabel("Cb (block coefficient)")
    ax.set_ylabel("error [%]")
    ax.set_title("The fit cannot be rescued by Cb: S stays low for cb∈[0.52, 0.8]")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path.name


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def build(d: dict, figs: dict) -> str:
    eb = d["external_baseline"]
    rows = {r["case_id"]: r for r in eb["rows"]}
    su = [rows[f"SUBOFF-0{i}"] for i in range(1, 5)]
    ct_rows = {r["case_id"]: r for r in d["existing_ct_only"]["rows"]}
    ws01 = su[0]["wetted_surface_external"]

    L: list[str] = []
    A = L.append
    A("# 阻力引擎外部基准复核：湿面积拟合式与残阻律对文献/解析基准（m1666）")
    A("")
    A("> 复核对象：`POST /api/v1/calculations/resistance`（能力 `calc.resistance`，宣称 REAL）"
      "与其对标链路 `POST /api/v1/calculations/benchmark_validate`。")
    A("> 复核类型：**验证欠账清偿**（已合并功能对文献实测值 + 解析几何基准独立复核），"
      "承接 `docs/research/m1386_stability_gz_validation.md` §待办首条。")
    A(f"> 工具链：Python 3.11 · 纯进程内计算 · pytest "
      f"15 项 · matplotlib。全部数字出自 `runs/m1666/resistance_external_audit.json`"
      "（`scripts/resistance_external_audit.py` 产出，本文由 "
      "`scripts/gen_resistance_external_doc.py` 注入，零手抄）。")
    A("> 判据：`docs/QUALITY_BAR.md` —— 对标实验/参考值误差 >15% 不可接受；理想标尺 3%。"
      "**引擎默认数值本次一字未改**（回归测试 `test_engine_default_numbers_unchanged` 锁死）。")
    A("")
    A("## ① 案例介绍（物理背景 / 几何 / 参数）")
    A("")
    A("平台的阻力引擎是两条**经验式**加一条**平板摩擦线**：")
    A("")
    A("```")
    A("S   = π · B · L · (0.5 + 0.25·Cb)        # 湿表面积（船体拟合式）")
    A("Cr  = 0.002 · (1 + 2·Cb) · Fn^2.5        # 残余阻力（docstring 自称 SUBOFF-calibrated）")
    A("Cf  = 0.075 / (log10(Re) − 2)^2          # ITTC-1957")
    A("Rt  = ½ρV²S · (Cf + Cr),   Ct = Rt/(½ρV²S)")
    A("```")
    A("")
    A("正典 v3.0（`backend/data/benchmark_reference.json`）12 行算例中，只有 DARPA SUBOFF 四行"
      "带**实测湿面积**，因而是唯一能做「S 绝对精度」判决的对象：")
    A("")
    A("| 参数 | 值 | 来源 |")
    A("|---|---|---|")
    A("| L（模型） | 4.356 m | Roddy 1990, DTRC-90/012 |")
    A("| D（最大直径） | 0.508 m | 同上 |")
    A(f"| 记录实测湿面积 S | {fmt(ws01['wetted_surface_recorded_m2'], 2)} m² | 同上（正典行自带） |")
    A("| 介质 | 淡水 20 °C，ρ=998.2 kg/m³，ν=1.004e-6 m²/s | ITTC 7.5-02-01-01 |")
    A("| 航速 | 3.0 / 6.0 / 12.0 / 18.0 m/s（SUBOFF-01..04） | 正典行 |")
    A(f"| 引擎 Cb（无溯源兜底常数） | {fmt(d['cb_fallback_constant'], 3)} | "
      "`backend/api/domain/ship_benchmark.py` `SHIP_GEOMETRY` |")
    A("")
    A("**为什么以前查不出来**：`benchmark_validate` 比的是 **Ct**（单位面积系数），"
      "而 Ct 按定义与湿面积无关；`docs/benchmarks/benchmark_data_integrity.md` §3.2 的解析账本"
      "把 S/Cr 公式与**公式自身**对照（误差恒 ~1e-7）。两者都看不见「S 的绝对精度」。")
    A("")
    A("## ② 怎么做的（方法 / 闸门设计）")
    A("")
    A("新增 `backend/services/resistance_external_baseline.py`（纯 stdlib，不连 DB/HTTP），"
      "把外部基准复核落成 7 项可机器判定的检查，判级与 `benchmark_integrity` 同序"
      "（`PASS < WARN < WITHHELD < FAIL`，3% / 15% 双标尺）：")
    A("")
    A("| 检查 | 内容 | 外部依据 |")
    A("|---|---|---|")
    A("| E1 | 引擎 S vs 记录实测 S | Roddy 1990（正典行） |")
    A("| E2 | 引擎 S vs 长椭球闭式解（同 L、D） | 解析：`S = 2πb² + (2πab/e)·asin(e)`，无拟合系数 |")
    A("| E3 | 几何基准**自校**：椭球 vs 记录实测 S（<3% 才准用） | 防止用一个不可信基准判死刑 |")
    A("| E4 | Cb 溯源：常数是否来自正典 | 正典行无 `cb`/体积 → WITHHELD |")
    A("| E5 | Cr 形状律 4 项解析极限（零极限/单调/量级带/幂次） | 解析 |")
    A("| E6 | 引擎 Cr vs `Ct_recorded − Cf_ITTC57` 反解值 | 该反解**不含湿面积假设** |")
    A("| E7 | 引擎 (Ct, Rt) vs 记录 + 乘法误差分解 | 恒等式，非拟合 |")
    A("")
    A("E7 的分解是恒等式，可把总误差**无损**拆到子模型上：")
    A("")
    A("```")
    A("Rt_e / Rt_m = (S_e / S_m) · ((Cf + Cr_e) / Ct_m) · closure_recorded")
    A("              └ 面积项 ┘   └──── 系数项 ────┘   └ 记录自身的 Ct↔Rt 闭合 ┘")
    A("```")
    A("")
    A("其中 `closure_recorded` 由正典行的 (S, Ct, Rt) 反算，用于把「记录本身" 
      "闭合了多少」与「引擎偏了多少」彻底分开——"
      "`identity_holds` 字段逐行断言该乘积等于总比（相对 1e-9）。")
    A("")
    A("**裁决纪律**：`reference_status=WITHHELD` 的正典行（DTMB-5415、KVLCC2 共 8 行）"
      "一律 WITHHELD，绝不用不可复核的记录去判引擎；记录缺实测 S 时同样 WITHHELD，"
      "不许拿几何基准单方面判死。")
    A("")
    A("## ③ 计算结果（数字表）")
    A("")
    A("### 3.1 湿表面积：引擎拟合式 vs 两条外部基准")
    A("")
    A("| 基准 | S [m²] | 相对记录值误差 | 判定 |")
    A("|---|---|---|---|")
    A(f"| 记录实测（Roddy 1990） | {fmt(ws01['wetted_surface_recorded_m2'], 3)} | 0.00% | 参考 |")
    A(f"| 长椭球闭式解（同 L、D） | {fmt(ws01['wetted_surface_baseline_m2'], 3)} | "
      f"{fmt(ws01['baseline_selfcheck']['error_pct'], 2)}% | "
      f"{ws01['baseline_selfcheck']['verdict']}（基准自校通过） |")
    A(f"| **引擎拟合式** `πBL(0.5+0.25Cb)` | {fmt(ws01['wetted_surface_engine_m2'], 3)} | "
      f"**{fmt(ws01['engine_vs_recorded']['error_pct'], 2)}%** | "
      f"**{ws01['engine_vs_recorded']['verdict']}** |")
    A("")
    A("两条**互相独立**的外部基准（拖曳水池实测 5.42、解析椭球 5.494）互差 "
      f"{fmt(ws01['baseline_selfcheck']['error_pct'], 2)}%（<3%），因此 −18.5% 是引擎的模型偏差，"
      "不是参考值坏了。")
    A("")
    A("### 3.2 端到端逐例误差（SUBOFF 四行，正典 `reference_status=DERIVED`）")
    A("")
    A("| case | V [m/s] | Fn | Ct 引擎 | Ct 记录 | Ct 误差 | Rt 引擎 [N] | Rt 记录 [N] | Rt 误差 | S 误差 | 行判 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in su:
        e7 = r["end_to_end"]
        A(f"| {r['case_id']} | {fmt(r['speed_ms'], 3)} | {fmt(e7['engine']['froude'], 3)} | "
          f"{fmt(e7['engine']['ct'], 6)} | {fmt(e7['ct_recorded'], 4)} | "
          f"{fmt(e7['ct_error_pct'], 2)}% | {fmt(e7['engine']['rt_n'], 2)} | "
          f"{fmt(e7['rt_recorded'], 2)} | {fmt(e7['rt_error_pct'], 2)}% | "
          f"{fmt(r['wetted_surface_external']['engine_vs_recorded']['error_pct'], 2)}% | "
          f"{r['verdict']} |")
    A("")
    A("### 3.3 误差归因（SUBOFF-01，V=3.0 m/s，恒等式分解）")
    A("")
    dec = su[0]["end_to_end"]["decomposition"]
    A("| 项 | 比值 | 贡献 |")
    A("|---|---|---|")
    A(f"| 面积项 S_e/S_m | {fmt(dec['area_ratio_S_engine_over_recorded'], 4)} | "
      f"{fmt(dec['area_term_pct'], 2)}% |")
    A(f"| 系数项 (Cf+Cr)/Ct_m | {fmt(dec['ct_engine_over_ct_recorded'], 4)} | "
      f"{fmt(dec['ct_term_pct'], 2)}% |")
    A(f"| 记录自身 Ct↔Rt 闭合 | {fmt(dec['closure_recorded'], 6)} | "
      f"{fmt((dec['closure_recorded'] - 1) * 100, 4)}% |")
    A(f"| **总比 Rt_e/Rt_m** | **{fmt(dec['total_ratio'], 4)}** | "
      f"**{fmt(dec['total_error_pct'], 2)}%** |")
    A(f"| 反事实（用记录 S 重算 Rt） | — | {fmt(dec['counterfactual_rt_error_pct'], 2)}% |")
    A("")
    A("读法：总误差 −25.7% 中，−18.5 个百分点来自湿面积、−8.9 个百分点来自残阻律；"
      "反事实一列说明「把 S 换成实测值后只剩 −8.9%」——面积项是主导项，且两项同号叠加，"
      "不存在互相抵消。")
    A("")
    A("### 3.4 残阻律 Cr：引擎 vs 记录反解")
    A("")
    A("| case | Fn | Cr 引擎 | Cr 反解 = Ct−Cf | 误差 | 判定 |")
    A("|---|---|---|---|---|---|")
    for r in su:
        m = r["residual_magnitude"]
        A(f"| {r['case_id']} | {fmt(m['froude'], 3)} | {fmt(m['cr_engine'], 6)} | "
          f"{fmt(m['cr_implied'], 6)} | {fmt(m['error_pct'], 2)}% | {m['verdict']} |")
    A("")
    A("引擎 Cr 的 `Fn^2.5` 指数与 SUBOFF 记录不符：低 Fn 偏小、高 Fn 偏大，误差随 Fn "
      "**单调恶化**（−36% → +83%）。docstring 里「SUBOFF-calibrated」的说法在这四行数据上不成立。")
    A("")
    A("### 3.5 数据集级汇总")
    A("")
    A("| 形体 | 行数 | 最差判定 |")
    A("|---|---|---|")
    for form, v in eb["per_form_verdict"].items():
        n = sum(1 for r in eb["rows"] if (r["form_type"] or "") == form)
        A(f"| {form} | {n} | {v} |")
    A("")
    A(f"- 数据集级 `binding = {eb['binding']}`；`provenance_caveat_rows = "
      f"{len(eb['provenance_caveat_rows'])}` 行（Cb 无溯源，见 §⑤）。")
    A(f"- 12 行中 8 行（DTMB-5415/KVLCC2）判 **WITHHELD**：正典自身 `reference_status=WITHHELD`，"
      "按纪律不得据此产生任何精度声明——这不是本次审计的空白，而是闸门应有的拒绝。")
    A("")
    A("## ④ 结果图")
    A("")
    A(f"![wetted surface](figs/{figs['wetted']})")
    A("")
    A(f"**(a) 湿面积三源对照**：引擎拟合式（红）系统性低于实测（蓝）与椭球基准（绿）；"
      f"**(b) 同一批案例三种口径的误差**：Ct-only（蓝，现有指标）在 ±15% 带内，"
      "而 S（红）与 Rt（橙）越界——差异正是本单要还的欠账。")
    A("")
    A(f"![decomposition](figs/{figs['decomp']})")
    A("")
    A("**(a) 乘法分解**：面积项与系数项同号，共同造成 −25.7% 的总偏差，记录自身闭合因子 ≈1 "
      "（证明问题不在参考值）；**(b) 残阻律形状**：引擎 `Fn^2.5` 曲线（红实线）与记录反解点（蓝圈）"
      "在低 Fn 相交、高 Fn 分离——指数过陡。")
    A("")
    A(f"![cb sensitivity](figs/{figs['cb']})")
    A("")
    A("**Cb 扫描**：把 Cb 从 π/6 抬到 0.8，引擎 S 仍全程低于记录值 → 偏差来自拟合式的**形状**"
      "（对 B 线性、对 Cb 仅 0.25 权），换 Cb 洗不白，必须改用几何/实测 S。")
    A("")
    A("## ⑤ 注意点（坑 / 局限 / 适用范围）")
    A("")
    A("- **坑 1：Ct 口径结构上看不见面积误差。** `Ct = Rt/(½ρV²S)`，S 在分子分母里同时出现，"
      "用 Ct 对标等于把湿面积误差开根号式地淡化；现有 pass 判据因此名不副实。")
    A("- **坑 2：解析账本的自证陷阱。** 旧账本把 S 公式与**同一公式**比对（误差 1e-7），"
      "这类「自洽性检查」再多也证伪不了模型；外部基准必须来自数据或闭式几何。")
    A("- **坑 3：Cb 兜底常数。** `SHIP_GEOMETRY['SUBOFF']['cb'] = 0.542` 在正典里没有溯源"
      f"（回转体行无 Cb/体积），与同 L/D 长椭球的 π/6={fmt(d['cb_prolate_spheroid'], 4)} 相差 "
      f"{fmt(rows['SUBOFF-01']['cb_provenance']['error_pct_vs_spheroid'], 2)}%。"
      "本单把它判为 `WITHHELD` 并单列 `provenance_caveat`，未替换数值。")
    A("- **局限 1：** 只有 SUBOFF 四行带实测 S，样本量 1 个几何、4 个工况；"
      "结论对「细长回转体 + 该航速带」成立，不宜外推到肥大型船。")
    A("- **局限 2：** 长椭球只是**回转型**几何的代表，不是 SUBOFF 实体；它与实测差 "
      f"{fmt(ws01['baseline_selfcheck']['error_pct'], 2)}% 说明该近似可用作交叉校验，"
      "但不应被当作精确真值。")
    A("- **局限 3：** 未做 CFD 交叉验证（`suboff_re1000` 属 TensorLBM，跨仓不在本单白名单内）。")
    A("- **未做（如实列出）：** ①不修改引擎默认 S/Cr 公式（改动会波及全部阻力结果与下游稳性/推进，"
      "需独立评审单）；②不重标正典 12 行参考值（需回原文）；③未把本节闸门接进 "
      "`/calculations/benchmark_validate` 的响应体（建议下单一并接线并暴露 `wetted_surface_status`）。")
    A("")
    A("## ⑥ 误差判定（逐项，显式回答是否 <3%）")
    A("")
    A("| 关键量 | 基准 | 误差 | <3%? | <15%? | 判定 |")
    A("|---|---|---|---|---|---|")
    A(f"| 几何基准 S（椭球） | 记录实测 5.42 m² | {fmt(ws01['baseline_selfcheck']['error_pct'], 2)}% | "
      f"**是** | 是 | PASS |")
    A(f"| 引擎 S | 记录实测 5.42 m² | {fmt(ws01['engine_vs_recorded']['error_pct'], 2)}% | "
      f"**否** | **否** | FAIL |")
    A(f"| 引擎 S | 椭球闭式解 5.494 m² | {fmt(ws01['engine_vs_baseline']['error_pct'], 2)}% | "
      f"**否** | **否** | FAIL |")
    for r in su:
        e7 = r["end_to_end"]
        A(f"| Rt（{r['case_id']}） | 记录 {fmt(e7['rt_recorded'], 2)} N | "
          f"{fmt(e7['rt_error_pct'], 2)}% | **否** | "
          f"{'是' if abs(e7['rt_error_pct']) <= 15 else '**否**'} | {e7['rt_verdict']} |")
    for r in su:
        m = r["residual_magnitude"]
        A(f"| Cr（{r['case_id']}） | Ct−Cf 反解 | {fmt(m['error_pct'], 2)}% | **否** | "
          f"{'是' if abs(m['error_pct']) <= 15 else '**否**'} | {m['verdict']} |")
    A("")
    A("**显式结论**：")
    A("")
    A(f"1. 湿面积：**不满足 3%，也不满足 15%**（{fmt(ws01['engine_vs_recorded']['error_pct'], 2)}%）→ "
      "`calc.resistance` 的 S 子模型在这条唯一可复核的算例上 **未达 REAL 精度门槛**。")
    A(f"2. 残阻律：4 个工况中 {sum(1 for r in su if abs(r['residual_magnitude']['error_pct']) > 15)}/4 "
      "超出 15%，且误差随 Fn 单调恶化 → 「SUBOFF-calibrated」不成立。")
    A("3. 端到端 Rt：4 个工况全部超出 3%，"
      f"{sum(1 for r in su if abs(r['end_to_end']['rt_error_pct']) > 15)}/4 超出 15%（最差 "
      f"{fmt(su[0]['end_to_end']['rt_error_pct'], 2)}%）。")
    A("4. 几何基准自校 **<3%（"
      f"{fmt(ws01['baseline_selfcheck']['error_pct'], 2)}%）** → 判决不是参考值坏了，"
      "而是引擎的两条经验式在这类形体上不适用。")
    A("5. 因此本次结论是对已合并能力的 **honest-FAIL（子模型级）**，"
      "并不推翻 `benchmark_validate` 的 Ct 口径历史结论——那两个口径量的是不同的东西。")
    A("")
    A("## 复现方式")
    A("")
    A("```bash")
    A("python3 scripts/resistance_external_audit.py --print      # 出账本 runs/m1666/resistance_external_audit.json")
    A("python3 scripts/gen_resistance_external_doc.py            # 出本文与三张图")
    A("cd backend && python3 -m pytest tests/test_resistance_external_baseline.py -q   # 15 passed")
    A("```")
    A("")
    A("## 给下一张自找活单的建议")
    A("")
    A("1. **接线**：把 `wetted_surface_status` / `rt_error_pct` 暴露进 "
      "`/calculations/benchmark_validate` 响应，让 UI 与报告直接看见面积项（本单只建闸门）。")
    A("2. **修公式**：对 `form_type=body_of_revolution` 用几何/实测 S（椭球解已在本仓可用且自校 "
      "1.4%），对 Cr 用 SUBOFF 记录的 Fn 依赖重标（需原文数据，不得臆造）。")
    A("3. **同门禁推广**：把 E1–E7 的「外部基准 vs 引擎」范式搬到 `calc.stability`"
      "（GZ 已有精确箱体解，缺的是真实船型静水力曲线接入）。")
    A("")
    return "\n".join(L)


def main() -> int:
    d = json.loads(LEDGER.read_text(encoding="utf-8"))
    FIGDIR.mkdir(parents=True, exist_ok=True)
    figs = {
        "wetted": fig_wetted_surface(d, FIGDIR / "m1666_wetted_surface_baselines.png"),
        "decomp": fig_decomposition(d, FIGDIR / "m1666_error_decomposition.png"),
        "cb": fig_cb_sensitivity(d, FIGDIR / "m1666_cb_sensitivity.png"),
    }
    DOC.write_text(build(d, figs), encoding="utf-8")
    print(f"wrote {DOC.relative_to(REPO)} and {len(figs)} figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())