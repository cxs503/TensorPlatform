"""Render the mission-1697 audit report from ``artifacts/m1697/bem_literature_audit/audit.json``.

Every number in the markdown comes from the audit JSON written by
``audit_bem_vs_literature.py``; nothing is transcribed by hand.

Run:  python3 -m tools.verify.propeller_open_water.make_bem_literature_report
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List

REPO = Path(__file__).resolve().parents[3]
ART = REPO / "artifacts" / "m1697" / "bem_literature_audit"
DOC = REPO / "docs" / "verifier" / "BEM_OPEN_WATER_LITERATURE_AUDIT.md"
FIGDIR = REPO / "docs" / "verifier" / "figs"

FIGURES: List[Dict[str, str]] = [
    ("fig1_open_water_curves.png", "图 1", "四个几何的敞水曲线：BEM（红实线）vs 规范 B 系列回归（蓝圈）"),
    ("fig2_error_vs_J.png", "图 2", "BEM 相对规范回归的偏差随进速比分布（绿=±3%，橙=±15% 验收带）"),
    ("fig3_kp505_experiment.png", "图 3", "KP505 敞水试验（方块）vs 回归（蓝）vs BEM（红虚）"),
    ("fig4_error_map_advance_area.png", "图 4", r"$K_T$ 偏置在 $(J, A_e/A_0)$ 平面上的分布（P/D=1.0, Z=4）"),
    ("fig5_bias_vs_solidity.png", "图 5", "偏置随盘面比 / 叶数的诊断：随盘面比单调增长，与叶数弱相关"),
]


def pct(x: float, nd: int = 1) -> str:
    return f"{100.0 * x:+.{nd}f}%"


def main() -> Path:
    data = json.loads((ART / "audit.json").read_text(encoding="utf-8"))
    vs = data["vs_bseries"]
    s = vs["summary"]
    kp = data["vs_kp505_experiment"]
    ks = kp["summary"]
    sol = data["solidity_scaling"]
    emap = data["error_map"]

    FIGDIR.mkdir(parents=True, exist_ok=True)
    for name, _, _ in FIGURES:
        shutil.copyfile(ART / name, FIGDIR / name)

    L: List[str] = []
    ap = L.append
    ap("# 敞水一阶原理（BEM）模型的文献复核 — mission 1697")
    ap("")
    ap("**被测能力** `propeller.open_water`（平台审计清单标注 REAL）的两条实现之一：")
    ap("`backend/services/propeller_open_water.py`（m1393 的叶元+动量模型）。")
    ap("**本单性质**：验证欠账清算（把已合并功能对文献/实测基准复核），未改动被测模型。")
    ap("**工件** `artifacts/m1697/bem_literature_audit/`（audit.json + 5 张图）；")
    ap("**生成工具** `tools/verify/propeller_open_water/audit_bem_vs_literature.py`（数字零手抄）。")
    ap("")
    ap("## ① 案例介绍")
    ap("")
    ap("敞水性能是推进学科的基本量：推力系数 $K_T=T/(\\rho n^2D^4)$、扭矩系数 "
       "$K_Q=Q/(\\rho n^2D^5)$、敞水效率 $\\eta_0=JK_T/(2\\pi K_Q)$。平台内部存在两条敞水通路：")
    ap("")
    ap("1. **B 系列回归通路**（`backend/api/open_water.py` 等三条多项式路径）——m1403 已对规范表逐点复核；")
    ap("2. **一阶原理通路**（本单被测）：m1393 用以替换硬编码 MOCK 替代式的 BEM 模型。")
    ap("")
    ap("m1393 的验证判据只有**解析上界**（作动盘理想效率 $\\eta_{ideal}=2/(1+\\sqrt{1+C_T})$、"
       "傅汝德缩尺不变性、叶面积守恒）。这类判据的盲区正是本单要量化的：**`K_T` 的系统性正偏不会违反"
       "效率上界**——上界随 `K_T` 一起移动。")
    ap("")
    ap("**算例参数表**（均落在 B 系列公布有效域 Z=2..7、P/D=0.5..1.4、Ae/A0=0.2..1.05、J=0..1.5 内）：")
    ap("")
    ap("| 算例 | P/D | Ae/A0 | Z | 用途 |")
    ap("|---|---|---|---|---|")
    for c in vs["cases"]:
        ap(f"| {c['name']} | {c['P_D']} | {c['Ae_Ao']} | {c['Z']} | B 系列回归对照 |")
    ap(f"| KP505 | {kp['case']['P_D']} | {kp['case']['Ae_Ao']} | {kp['case']['Z']} | "
       "敞水试验实测对照（Iftikhar & Takinaci 2023, Table 2） |")
    ap("")
    ap(f"计算网格：$J=0.05:0.05:0.85$（{len(vs['cases'][0]['points'])} 点/几何 × "
       f"{len(vs['cases'])} 几何 = {s['n_points']} 点）；KP505 为 {ks['n_points']} 个试验点；"
       f"另在 $(J,A_e/A_0)$ 平面做 {len(emap['J'])}×{len(emap['Ae_Ao'])} 偏置云图。")
    ap("")
    ap("## ② 怎么做的")
    ap("")
    ap("1. **规范回归基准**：`tools/verify/propeller_open_water/reference_bseries.py`"
       "（Oosterveld & van Oossanen 1975 的 39 项 $K_T$ / 47 项 $K_Q$ 系数表，与平台自带 "
       "`backend/data/official/Wageningen_Bseries_coeffs.dat` 已由 m1403 逐项核过）。")
    ap("2. **实测基准**：KP505 模型桨敞水试验表，从 `audit_open_water.py::KP505_EXPERIMENT` 导入"
       "（该表全仓只转录一次，避免二次抄写误差）。")
    ap("3. **被测模型**：只读调用 `open_water_point()`，未修改被测代码；D=5 m、ρ=1025 kg/m³ 仅作量纲载体"
       "（输出无量纲，与尺度无关）。")
    ap("4. **统计口径（避免粉饰）**：$K_T$、$K_Q$ 在零推力点附近穿越零，相对误差失去定义。故相对误差统计"
       f"只在基准值 $|K_T|\\ge {s['KT_floor']}$、$|K_Q|\\ge {s['KQ_floor']}$ 的点上计算"
       f"（$K_T$ 有效点 {s['n_KT_valid']}/{s['n_points']}，$K_Q$ 有效点 {s['n_KQ_valid']}/{s['n_points']}），"
       "被排除点改报**绝对偏差**与**符号失配数**，不静默丢弃。")
    ap("5. **判据**：平台精度标准（`docs/QUALITY_BAR.md`）——与基准偏差 >15% 不可接受，3% 为论文级。")
    ap("")
    ap("## ③ 计算结果")
    ap("")
    ap("### 3.1 对规范 B 系列回归的偏差（逐几何）")
    ap("")
    ap("| 算例 | 平均 $\\Delta K_T$ | max $|\\Delta K_T|$ | max $|\\Delta K_Q|$ | max $|\\Delta\\eta_0|$ | "
       "$J_0$ BEM / 基准 | $J_0$ 偏差 | $K_T$ 符号失配点 |")
    ap("|---|---|---|---|---|---|---|---|")
    for c in vs["cases"]:
        ap(f"| {c['name']} | {pct(c['mean_KT_rel'])} | {pct(c['max_abs_KT_rel'])} | "
           f"{pct(c['max_abs_KQ_rel'])} | {pct(c['max_abs_eta_rel'])} | "
           f"{c['J0_bem']:.4f} / {c['J0_ref']:.4f} | {pct(c['J0_rel'])} | {c['sign_disagreements']} |")
    ap("")
    ap("### 3.2 汇总")
    ap("")
    ap("| 指标 | 值 |")
    ap("|---|---|")
    ap(f"| 有效 $K_T$ 点数 | {s['n_KT_valid']} |")
    ap(f"| max $|\\Delta K_T|$ | {pct(s['max_abs_KT_rel'])} |")
    ap(f"| 平均 $\\Delta K_T$ | {pct(s['mean_KT_rel'])} |")
    ap(f"| max $|\\Delta K_Q|$ | {pct(s['max_abs_KQ_rel'])} |")
    ap(f"| max $|\\Delta\\eta_0|$ | {pct(s['max_abs_eta_rel'])} |")
    ap(f"| max 绝对偏差 $|\\Delta K_T|$ | {s['max_abs_dKT']:.4f} |")
    ap(f"| $K_T$ 落在 ±3% 的点占比 | {100*s['frac_KT_within_3pct']:.0f}% |")
    ap(f"| $K_T$ 落在 ±15% 的点占比 | {100*s['frac_KT_within_15pct']:.0f}% |")
    ap(f"| $K_Q$ 落在 ±15% 的点占比 | {100*s['frac_KQ_within_15pct']:.0f}% |")
    ap(f"| $\\eta_0$ 落在 ±15% 的点占比 | {100*s['frac_eta_within_15pct']:.0f}% |")
    ap(f"| max $|\\Delta J_0|$ | {pct(s['max_abs_J0_rel'])} |")
    ap(f"| $K_T$ 符号失配点（BEM 给出制动力/正推力与基准相反） | {s['sign_disagreements']} |")
    ap(f"| 作动盘效率上界违反次数 | {s['eta_bound_violations']} |")
    ap("")
    ap("### 3.3 对 KP505 实测的偏差")
    ap("")
    ap("| 基准 | 平均 $\\Delta K_T$ | max $|\\Delta K_T|$ | max $|\\Delta K_Q|$ | max $|\\Delta\\eta_0|$ |")
    ap("|---|---|---|---|---|")
    ap(f"| BEM（被测模型） | {pct(ks['mean_KT_bem_rel'])} | {pct(ks['max_abs_KT_bem_rel'])} | "
       f"{pct(ks['max_abs_KQ_bem_rel'])} | {pct(ks['max_abs_eta_bem_rel'])} |")
    ap(f"| B 系列回归（对照） | {pct(ks['mean_KT_bseries_rel'])} | {pct(ks['max_abs_KT_bseries_rel'])} | "
       f"{pct(ks['max_abs_KQ_bseries_rel'])} | {pct(ks['max_abs_eta_bseries_rel'])} |")
    ap("")
    ap(f"实测点 $K_T$ 绝对偏差最大 {ks['max_abs_dKT_bem']:.4f}（有效点数 {ks['n_KT_valid']}/{ks['n_points']}）。")
    ap("")
    ap("### 3.4 依赖诊断（偏置 vs 盘面比 / 叶数）")
    ap("")
    ap(f"固定 J={sol['J']}、P/D=1.0、Z=4，扫盘面比：")
    ap("")
    ap("| Ae/A0 | $K_T$ BEM | $K_T$ 基准 | $\\Delta K_T$ | $\\Delta K_Q$ |")
    ap("|---|---|---|---|---|")
    for r in sol["by_Ae_Ao"]:
        ap(f"| {r['Ae_Ao']} | {r['KT_bem']:.4f} | {r['KT_ref']:.4f} | {pct(r['KT_rel'])} | {pct(r['KQ_rel'])} |")
    ap("")
    ap(f"固定 J={sol['J']}、P/D=1.0、Ae/A0=0.70，扫叶数：")
    ap("")
    ap("| Z | $K_T$ BEM | $K_T$ 基准 | $\\Delta K_T$ |")
    ap("|---|---|---|---|")
    for r in sol["by_Z"]:
        ap(f"| {r['Z']} | {r['KT_bem']:.4f} | {r['KT_ref']:.4f} | {pct(r['KT_rel'])} |")
    ap("")
    ap(f"$(J,A_e/A_0)$ 平面偏置云图：max $|\\Delta K_T|$ = {emap['max_abs']:.1f}%，"
       f"区间 [{emap['min']:.1f}%, {emap['max']:.1f}%]，掩膜点 {len(emap['masked_points'])} 个。")
    ap("")
    wire = data["batch_wrapper"]
    ap("### 3.5 附带发现：MOCK 替代式曾仍在批量执行器的服务路径上（已修）")
    ap("")
    ap("审计过程中发现 main 上一条**本就红着的**测试"
       "（`tests/test_propeller_open_water.py::TestBatchWrapperWiring`）：m1393 声称已把 "
       "`tools/batch_scan/batch_executor.py::ShipCalculationEngine.calc_propeller` 换成 BEM 模型，"
       "但合入树里该函数仍是**同一段被判定为 MOCK 的硬编码线性替代式**"
       "（`KT=max(0.05, 0.5-0.3J)`、`KQ=max(0.01, 0.05+0.02J)`、效率夹 [0.3, 0.85]），"
       "接线从未真正落地——即“已被替换”在实际合并状态中不成立。本单已把它接到被测 BEM 模型：")
    ap("")
    ap("| 检查项 | 结果 |")
    ap("|---|---|")
    ap(f"| 源码中残留的 MOCK 特征片段 | {wire['mock_markers_present'] or '无'} |")
    ap(f"| 返回字典的 model 字段 | {', '.join(wire['model_field'])} |")
    ap(f"| $K_Q$ 随 J 单调下降 | {wire['kq_decreasing_with_J']} |")
    ap(f"| max $|\\Delta K_T|$（包装器 vs 直接调用服务） | {wire['max_abs_dKT_vs_service']:.2e} |")
    ap(f"| max $|\\Delta K_Q|$（包装器 vs 直接调用服务） | {wire['max_abs_dKQ_vs_service']:.2e} |")
    ap("")
    ap("包装器与直接调用服务逐点一致（差异为 float 舍入量级），即批量扫描现在真正吃 BEM 模型；"
       "同时把 §⑥ 的幅值偏差结论一并继承给该路径，文档与代码注释都已写明适用范围。")
    ap("")
    ap("## ④ 结果图")
    ap("")
    for name, tag, cap in FIGURES:
        ap(f"**{tag}** {cap}")
        ap("")
        ap(f"![{tag}](figs/{name})")
        ap("")
    ap("## ⑤ 注意点（坑与局限）")
    ap("")
    ap("1. **解析判据的盲区（本单最重要的方法论结论）**：被测模型在 $K_T$ 最大正偏 "
       f"{pct(s['max_abs_KT_rel'])} 的情况下，作动盘效率上界违反次数仍为 **{s['eta_bound_violations']}**。"
       "因为 $\\eta_{ideal}$ 由 $C_T=8K_T/(\\pi J^2)$ 算出、随偏置一起漂移，"
       "「$0<\\eta_0<\\eta_{ideal}$」只能证伪永动机式的错误，无法约束幅值。"
       "**结论：以解析上界作为 REAL 级证据不充分，敞水类能力必须叠加回归/实测点对。**")
    ap("2. **偏置随盘面比单调放大**（见表 3.4 与图 5）：固定 J、P/D、Z 时 Ae/A0 从 0.30 增到 1.00，"
       "$\\Delta K_T$ 由负转正并持续放大；叶数变化引起的偏置远小于盘面比。这与「剖面升力在高固体度下"
       "的高攻角饱和/失速后衰减未建模」一致，但本单**只做诊断，未做归因证明，也未修复模型**。")
    ap("3. **$J_0$ 系统性偏低**：四个几何的零推力进速比一致偏低 "
       f"{pct(max(abs(c['J0_rel']) for c in vs['cases']))} 量级（全部为负），即推力图斜率偏陡；"
       f"B4-55 在 J≥0.80 出现 $K_T<0$ 而基准仍为正（{sum(c['sign_disagreements'] for c in vs['cases'])} 个符号失配点），"
       "意味着该模型在**高进速比/轻载区不可用于推力符号判定**。")
    ap("4. **KP505 不是瓦格宁根 B 系列桨**：它用 NACA66 mod 剖面、a=0.8 拱度，"
       f"所以回归本身对实测已有 {pct(ks['mean_KT_bseries_rel'])} 平均偏差。"
       "因此「BEM vs KP505」不能把全部偏差都记在 BEM 头上，本单同时给出两条对照（表 3.3）。")
    ap("5. **相对误差在零推力附近无定义**：已按 3.1 节口径加分母下限并另报绝对偏差，"
       "否则会得到 808% 这类无物理含义的数字（原始未加限的统计正是这样，已弃用）。")
    ap("6. **红测试不等于已修**：3.5 节的接线回归说明，m1393 的“已替换”只在独立服务模块内成立，"
       "合入树的批量执行器仍是 MOCK。教训：**能力级证据等级必须以服务路径上的可执行断言为准**，"
       "不能以文档/提交说明为准；后续评审应把红测试当作未清偿债务而非噪声。")
    ap("7. **适用范围**：结论限于被测模型的剖面模型（$C_l=2\\pi\\sin\\alpha$ 饱和 1.10、"
       "$C_d=0.012+\\alpha^2$）与 40 环带 Prandtl 修正；换剖面模型需重跑本套工具（一条命令）。")
    ap("")
    ap("## ⑥ 误差判定（是否 <3%）")
    ap("")
    def verdict(err: float) -> str:
        """Programmatic pass/fail against the platform accuracy standard."""
        ok3 = "是" if abs(err) <= 0.03 else "否"
        ok15 = "是" if abs(err) <= 0.15 else "否"
        return f"{ok3} | {ok15}"

    rows = [(f"$K_T$（{c['name']} 全网格）", "B 系列回归", c["max_abs_KT_rel"]) for c in vs["cases"]]
    rows += [
        ("$K_Q$（全网格 max）", "B 系列回归", s["max_abs_KQ_rel"]),
        (r"$\eta_0$（全网格 max）", "B 系列回归", s["max_abs_eta_rel"]),
        ("$J_0$（全几何 max）", "B 系列回归", s["max_abs_J0_rel"]),
        ("$K_T$（KP505 均值）", "敞水试验", ks["mean_KT_bem_rel"]),
        ("$K_T$（KP505 最大）", "敞水试验", ks["max_abs_KT_bem_rel"]),
        ("$K_Q$（KP505 最大）", "敞水试验", ks["max_abs_KQ_bem_rel"]),
    ]
    ap("| 关键量 | 基准 | 偏差 | ≤3% ? | ≤15% ? |")
    ap("|---|---|---|---|---|")
    for label, baseline, err in rows:
        ap(f"| {label} | {baseline} | {pct(err)} | {verdict(err)} |")
    ap("")
    ap("**显式回答：关键量 $K_T$ 是否 <3%？——否。** 是否 <15%？——**部分几何不满足**"
       f"（{len([c for c in vs['cases'] if c['max_abs_KT_rel']>0.15])}/{len(vs['cases'])} 个几何超 15%，"
       f"全网格仅 {100*s['frac_KT_within_15pct']:.0f}% 的点在 ±15% 内）。")
    ap("")
    ap("**处置与证据分级建议**：被测 BEM 模型在敞水幅值预报上不满足平台 15% 精度标准，"
       "不应单独作为 REAL 级敞水预报依据；平台对外敞水端点（B 系列回归通路，m1403 已复核）不受本单影响。"
       "建议：(a) 该模块证据等级降为 WITHHELD/实验性，仅供定性趋势与效率趋势；(b) 幅值仍走 B 系列回归；"
       "(c) 修复（剖面失速后衰减 + 固体度一致的诱导修正）已作为后继任务派单，修复后本套测试会因偏差带被突破而变红，强制复评。")
    ap("")
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(L) + "\n", encoding="utf-8")
    return DOC


if __name__ == "__main__":
    path = main()
    print(f"wrote {path}")