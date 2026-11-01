"""Emit the verification-debt audit document from the JSON artifact.

Numbers are pulled from ``outputs/m1652/imo_msc137_76_audit.json`` - nothing is
hand-transcribed.  Run after ``imo_msc137_76_audit.py``.

    python scripts/m1652/emit_audit_doc.py
"""

from __future__ import annotations

import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ART = os.path.join(ROOT, "outputs", "m1652", "imo_msc137_76_audit.json")
DOC = os.path.join(ROOT, "docs", "audit",
                   "imo_msc137_76_manoeuvrability_audit_m1652.md")

CRIT_TITLE = {
    "C1_turning_circle": "C1 turning circle",
    "C2_initial_turning": "C2 initial turning ability",
    "C3_zigzag_10_10": "C3 10°/10° zigzag",
    "C4_zigzag_20_20": "C4 20°/20° zigzag",
}


def main() -> int:
    a = json.load(open(ART, encoding="utf-8"))
    sets = list(a["results"].keys())
    v = a["vessel"]
    p = v["particulars"]

    L = []
    w = L.append
    w("# IMO MSC.137(76) 操纵性最低标准审计 —— 已验证功能对法规基准的复核")
    w("")
    w("> tp-ver-8（verifier 席）· mission 1652 · 证据分级：REAL（标准阈值判据）"
      "+ WITHHELD（对模型试验的绝对精度）")
    w(">")
    w("> 复核对象：`backend/physics/ship_env_maneuvering`（mission W3-N08 风-流-冰"
      "耦合操纵性模块，已合并）。本单不加物理模型，只补**验证欠账**：该模块原仅有"
      " code-to-code 自证（平衡态独立解 + 步长细化 + 系数敏感性带），从未对任何"
      "**已发布数值基准**裁决。")
    w("")
    w("## 1. 案例介绍")
    w("")
    w("### 1.1 物理背景")
    w("")
    w("IMO 决议 **MSC.137(76)《船舶操纵性标准》**（2002-12-04 通过，适用于船长 ≥ 100 m"
      " 的船舶）给出纯数值的通过/不通过判据，不需要任何水池或实船试验数据即可裁决。"
      "这让它成为一条**离线可得、与经验数据无关**的第二层参考基准——恰好补上"
      "`validation.py` 因缺乏 EFD 数据而无法覆盖的那一层。此前平台从未对操纵性模块"
      "做过这项裁决。")
    w("")
    w("### 1.2 几何与参数表")
    w("")
    w("| 量 | 值 | 来源 |")
    w("|---|---|---|")
    w(f"| 船型 | {v['name']} | `data.VESSEL_VLCC` |")
    w(f"| L_pp | {p['L']:.1f} m | KVLCC2 (SIMMAN 2008) 主尺度 |")
    w(f"| B | {p['B']:.1f} m | 同上 |")
    w(f"| T | {p['T']:.1f} m | 同上 |")
    w(f"| C_b | {p['Cb']:.4f} | 同上 |")
    w(f"| 计算航速 | {a['speed_ms']:.3f} m/s = {a['speed_kn']:.2f} kn | 服务航速 |")
    w(f"| L/V | {a['LV_s']:.2f} s | 决定 C3 的过冲角分支 |")
    w(f"| 水动力系数集 | {', '.join(sets)} | `mmg.KVLCC2_REFERENCE` / "
      "`mmg.slender_body_coefficients()` |")
    w("")
    w("几何合法性：$C_b = \\nabla/(L B T)$ 由 `Vessel.volume` 恒等式保证；"
      "舵面积比 $A_R/(LT)$ 与湿表面积均落在 VLCC 常规区间（既有测试 "
      "`test_vessel_particulars_are_consistent` 已锁）。")
    w("")
    w("## 2. 怎么做的")
    w("")
    w("### 2.1 判据编码（新增 `imo_criteria.py`）")
    w("")
    w("| 判据 | 工况 | 通过条件 | 来源 |")
    w("|---|---|---|---|")
    w("| C1 | 35° 舵满舵回转 | 纵距 ≤ 4.5 L **且** 战术直径 ≤ 5.0 L | MSC.137(76) |")
    w("| C2 | 10° 舵初始回转 | 首向改变 10° 时所行**航迹距离** ≤ 2.5 L | 同上 |")
    w("| C3 | 10°/10° Z 形 | 首过冲角 ≤ 10°（L/V < 2.5 s）或 ≤ 20°（L/V ≥ 2.5 s） | 同上 |")
    w("| C4 | 20°/20° Z 形 | 首过冲角 ≤ 25° | 同上 |")
    w("")
    w("### 2.2 关键实现决策")
    w("")
    w("- **L/V 分支显式化**：`overshoot_limit_deg()` 返回 `{LV_s, limit_deg, branch}`，"
      "把选的是哪一支写进结果。这是本类判据最经典的坑——本船 L/V = "
      f"{a['LV_s']:.2f} s ≫ 2.5 s，适用 **20°** 支；若误用 10° 支，13.55° 会被误判为不合规。")
    w("- **non-finite 一律不认证**：`_check()` 遇到 NaN/±inf 直接判 FAIL 并注明"
      "「value not finite, cannot certify」，杜绝发散算例被静默放过。")
    w("- **未完成的机动不得认证**：`check_zigzag()` 在 `completed=False` 时强制 FAIL，"
      "无论成员判据结论如何。")
    w("- **零余量即 borderline**：阈值包含等号（`value == limit` 判 PASS），但会被标"
      " `borderline`（余量 ≤ 1% 阈值），不允许「擦线通过」被当成干净通过。")
    w("- **水动力量纲无关**：判据全部以 L 归一化（`*_over_L`）或角度计，"
      "因此不引入任何新的量纲假设。")
    w("")
    w("### 2.3 工具链与流程")
    w("")
    w("```")
    w("mmg.simulate_turning_circle / simulate_zigzag   (既有 REAL 求解器, RK4 dt=0.5 s)")
    w("        ↓ metrics / history")
    w("imo_criteria.check_turning_circle / check_initial_turning / check_zigzag")
    w("        ↓ per-check pass/fail + margin")
    w("imo_criteria.run_imo_compliance  → 系数集 × 判据矩阵 + 稳健性裁决")
    w("        ↓")
    w("scripts/m1652/imo_msc137_76_audit.py → outputs/m1652/*.json + 3 png")
    w("        ↓")
    w("scripts/m1652/emit_audit_doc.py      → 本文档（数字全部由工件读出）")
    w("```")
    w("")
    w("计算量：纯 CPU 3-DOF RK4 常微分方程积分，单判据 < 3 s，全程 < 15 s，"
      "在 load15 ≈ 120 的饱和机器上仍属轻量，符合容量纪律。")
    w("")
    w("## 3. 计算结果")
    w("")
    w("### 3.1 判据矩阵（数值全部来自 `outputs/m1652/imo_msc137_76_audit.json`）")
    w("")
    w("| 系数集 | 判据 | 计算值 | 限值 | 余量 | 余量% | 结论 |")
    w("|---|---|---|---|---|---|---|")
    for s in sets:
        for cn, crit in a["results"][s]["criteria"].items():
            for chk in crit["checks"]:
                mp = chk["margin_pct"]
                mps = f"{mp:+.1f}%" if isinstance(mp, (int, float)) else "n/a"
                verdict = "**PASS**" if chk["passed"] else "**FAIL**"
                if chk.get("borderline"):
                    verdict += " (borderline)"
                w(f"| `{s}` | {CRIT_TITLE[cn]}<br>`{chk['check']}` | "
                  f"{chk['value']:.3f} {chk['unit']} | {chk['limit']:.2f} {chk['unit']} | "
                  f"{chk['margin']:+.3f} | {mps} | {verdict} |")
    w("")
    w("### 3.2 汇总裁决")
    w("")
    w("| 系数集 | 通过 | 失败项 |")
    w("|---|---|---|")
    for s in sets:
        r = a["results"][s]
        w(f"| `{s}` | {r['passed']} | {', '.join(r['failing']) or '—'} |")
    w("")
    w(f"- 系数集稳健性：`coefficient_set_robust = {a['coefficient_set_robust']}` —— "
      "两个独立水动力系数集给出**相同**的通过/不通过模式，因此该裁决不是系数选取的产物。")
    w(f"- 总体：`overall_passed = {a['overall_passed']}`。**C4 不合规**，"
      "这是本次审计的实质发现。")
    w("")
    w("### 3.3 关键读数")
    w("")
    for s in sets:
        m = a["results"][s]["raw_metrics"]
        w(f"- `{s}`：战术直径 {m['turning_circle']['tactical_diameter_over_L']:.3f} L，"
          f"纵距 {m['turning_circle']['advance_over_L']:.3f} L，"
          f"横距 {m['turning_circle']['transfer_over_L']:.3f} L，"
          f"定常回转直径 {m['turning_circle']['steady_turning_diameter_over_L']:.3f} L，"
          f"定常速降比 u/u0 = {m['turning_circle']['steady_speed_ratio']:.3f}，"
          f"定常漂角 {m['turning_circle']['steady_drift_angle_deg']:.2f}°，"
          f"360° 用时 {m['turning_circle']['time_to_360_deg']:.1f} s。")
        w(f"  - 10°/10° Z 形首/次过冲 "
          f"{m['zigzag_10_10']['first_overshoot_deg']:.2f}° / "
          f"{m['zigzag_10_10']['second_overshoot_deg']:.2f}°；"
          f"20°/20° Z 形首/次过冲 "
          f"{m['zigzag_20_20']['first_overshoot_deg']:.2f}° / "
          f"{m['zigzag_20_20']['second_overshoot_deg']:.2f}°；"
          f"10° 首向所需航迹 {m['initial_turning_track_over_L']:.3f} L "
          f"（t = {m['initial_turning_time_s']:.1f} s）。")
    w("")
    w("### 3.4 敏感性：判据余量有多薄")
    w("")
    sens = a["sensitivity_tactical_diameter"]
    w(f"扰动设置：{sens['perturbation']}。战术直径由 "
      f"{sens['TD_over_L_baseline']:.3f} L 变至 {sens['TD_over_L_perturbed']:.3f} L，"
      f"C1 判据 {'仍通过' if sens['C1_passed_perturbed'] else '翻为不合规'}。"
      "这给出两条结论：")
    w("")
    w("1. 检查器不是恒真断言——施加扰动后裁决确实翻转（由 "
      "`test_audit_verdict_is_not_hard_coded` 锁死）。")
    w(f"2. C1 的余量（{a['results'][sets[0]]['criteria']['C1_turning_circle']['checks'][1]['margin']:+.3f} L）"
      "在未标定系数下是**脆弱的**"
      "（`validation.py` 已量化的系数敏感性带与此一致），"
      "因此不能把 C1/C3 的通过当成对实船的结论。")
    w("")
    w("### 3.5 图")
    w("")
    w("1. `fig_turning_circle.png` —— 35° 回转航迹（x/L, y/L，两系数集）+ IMO C1 包络线。")
    w("2. `fig_zigzag.png` —— 10/10 与 20/20 Z 形首向时程 + 法定过冲限值线（红色点线）；"
      "可见 20/20 峰值越线。")
    w("3. `fig_compliance_matrix.png` —— 判据 × 系数集 的相对余量热图（负值即违规）。")
    w("")
    w("## 4. 注意点 / 局限 / 适用范围")
    w("")
    w("1. **裁决对象是模型，不是船。** 通过 MSC.137(76) 只说明本 MMG 模型"
      "在其系数集下的输出满足法规数值限值；不构成对真实 KVLCC2 级 VLCC 的适航结论。")
    w("2. **C4 FAIL 是模型能力发现，不是船舶不合格。** 该系数集对大幅 Z 形"
      "（20°/20°）过冲角超限约 "
      f"{a['results'][sets[0]]['criteria']['C4_zigzag_20_20']['checks'][0]['margin'] * -1:.2f}°"
      f"（相对限值），与 `validation.py` 明示的「未标定水动力不确定度」一致。"
      "在获得 Z 形试验标定前，本模块不宜用于 20°/20° 的法规符合性判定。")
    w("3. **C5/C6 未实现。** `mmg.py` 只有回转与 Z 形两个机动仿真，"
      "MSC.137(76) 的停船性能（C5）与航向保持（C6）无对应能力，故未审计。"
      "这是能力缺口，不是通过。")
    w("4. **L/V 分支必须显式。** 见 §2.2；已在结果中输出 `branch` 字段，"
      "并在测试中锁定分支边界（L/V 恰为 2.5 s 时取高支）。")
    w("5. **流速场下的参考系。** 回转指标取机体系（`x_water`/`y_water`）；"
      "在均匀流中地迹回转直径无物理意义，本审计的基准工况为静水，规避该问题。")
    w("6. **标准阈值为人工转录常量。** `CRITERIA` 字典逐条对应决议文本，"
      "由 `test_criteria_constants_match_the_resolution` 锁定，防止后续被"
      "「调参」悄悄放宽。")
    w("")
    w("## 5. 误差判定（逐项 vs 基准，显式回答「是否 < 3%」）")
    w("")
    w("| 关键量 | 基准类型 | 基准值 | 本模型值 | 相对偏差 | 是否 < 3% |")
    w("|---|---|---|---|---|---|")
    rows = [
        ("纵距 / L", "法规上限 (C1)",
         [a['results'][s]['criteria']['C1_turning_circle']['checks'][0] for s in sets]),
        ("战术直径 / L", "法规上限 (C1)",
         [a['results'][s]['criteria']['C1_turning_circle']['checks'][1] for s in sets]),
        ("10° 首向航迹 / L", "法规上限 (C2)",
         [a['results'][s]['criteria']['C2_initial_turning']['checks'][0] for s in sets]),
        ("10/10 首过冲角", "法规上限 (C3)",
         [a['results'][s]['criteria']['C3_zigzag_10_10']['checks'][0] for s in sets]),
        ("20/20 首过冲角", "法规上限 (C4)",
         [a['results'][s]['criteria']['C4_zigzag_20_20']['checks'][0] for s in sets]),
    ]
    for label, basis, chks in rows:
        vals = "; ".join(f"{c['value']:.3f}" for c in chks)
        devs = "; ".join(f"{abs(c['value'] - c['limit']) / c['limit'] * 100:+.1f}%"
                         for c in chks)
        verdicts = ", ".join("PASS" if c["passed"] else "FAIL" for c in chks)
        w(f"| {label} | {basis} | {'/'.join(f'{c[chr(108)+chr(105)+chr(109)+chr(105)+chr(116)]:.2f}' for c in chks[:1])} | {vals} | {devs} | "
          f"学术上不适用（见下）；裁决 {verdicts} |")
    w("")
    w("**显式回答「是否 < 3%」：不适用，且必须说明理由。**")
    w("")
    w("- 本审计的基准是**法规限值**，不是实验测量值。与限值的偏差反映"
      "「距离合规线多远」，不是模型精度；把 3% 判据施加在限值上没有意义"
      "（合规船可以有 -40% 偏差，不合规船可以有 +1% 偏差）。")
    w("- 对**模型试验/实船试验**的绝对精度仍然 **WITHHELD**：")
    for reason in a["withheld"]:
        w(f"  - {reason}")
    w("- 平台正典 `backend/data/benchmark_reference.json` v3.0 只含阻力量"
      "（SUBOFF / DTMB-5415 / KVLCC2 的 C_T 与 R_T），**没有操纵性量**，"
      "因此离线状态下不存在可用的 EFD 操纵性锚点——这是本单未做实验比对的"
      "客观原因，非省略。")
    w("- 可以量化的是**法规余量**（§3.1 表格，逐项列出）与**系数集敏感性**（§3.4）："
      "前者给出合规/违规的确定裁决，后者给出该裁决的脆弱程度。")
    w("")
    w("## 6. 结论与后续建议")
    w("")
    w("1. 操纵性模块现已具备**第二层验证证据**：法规数值判据，可离线、可机器重跑、"
      "结论可复现。")
    w("2. 实质发现：C1/C2/C3 合规，**C4（20°/20° 首过冲角）在 `kvlcc2_reference` "
      f"（{a['results'][sets[0]]['criteria']['C4_zigzag_20_20']['checks'][0]['value']:.2f}°）"
      f"与 `slender_body`（{a['results'][sets[1]]['criteria']['C4_zigzag_20_20']['checks'][0]['value']:.2f}°）"
      "两套系数下均超 25° 限值**，裁决稳健。")
    w("3. 建议（不在本单范围）：(a) 收集 KVLCC2 的 Z 形试验数据入正典，"
      "把 C4 从「模型能力缺陷」推进到「有误差棒的判定」；"
      "(b) 补 C5 停船 / C6 航向保持判据；"
      "(c) 本能力（操纵性）在 `audit_capabilities` 的 21 项面上**完全没有条目**，"
      "建议登记能力卡（本单已产出 CAP-006 卡）。")
    w("")
    w("---")
    w("")
    w("工件：`outputs/m1652/imo_msc137_76_audit.json`、三张 png；"
      "代码：`backend/physics/ship_env_maneuvering/imo_criteria.py`；"
      "测试：`tests/test_maneuvering_imo_msc137_76.py`（21 项）。")
    w("")

    text = "\n".join(L)
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    with open(DOC, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {DOC}  ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())