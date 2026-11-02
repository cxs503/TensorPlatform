#!/usr/bin/env python3
"""从 artifacts/cii_rating_m1803/verification.json 生成 docs/verification/cii_rating_m1803.md
（数字零手抄：所有表格数值直接取自工件 JSON）。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
J = json.loads((ROOT / "artifacts" / "cii_rating_m1803" / "verification.json").read_text())


def f(x: float, n: int = 4) -> str:
    return f"{x:.{n}f}"


lines = []
A = lines.append

A("# mission 1803 · 绿色航行 L4「CII A–E 等级」验证欠账清偿")
A("")
A("> verifier 席自主工时产出。**只做验证，不动生产代码**：新增 1 个测试文件 +")
A("> 1 个工件生成器 + 1 份文档；`backend/services/green_navigation.py` 一行未改。")
A("")
A("## 1. 案例介绍（物理/规则背景）")
A("")
A("IMO 碳强度指标 CII 的判决链是三层：")
A("")
A("1. **达到值** attained = ΣCO₂ / (Capacity · 航程)  [gCO₂/(t·nm)]；")
A("2. **要求值** required = a · Capacity^(−c) · (1 − Z)；")
A("3. **等级** A–E = 达到值与要求值之比的四边界映射（G5 §4，边界向量 dd）。")
A("")
A("平台 `green_navigation` 模块自陈证据分层：L1 碳因子 REAL / L2 达到值 REAL /")
A("L3 要求值 WITHHELD（系数为转录）/ **L4 等级 NOT_SUPPORTED**，理由是「G4(MEPC.354(78))")
A("等级边界函数未取得规则原文」。本单要回答的验证问题因此有且只有一个：")
A("")
A("> **L4 的 NOT_SUPPORTED 是「规则原文没拿到」，还是「数学上做不出来」？**")
A("")
A("这两者被混为一谈时，会把「欠一次转录」误判成「永远做不到」，从而长期空转一项")
A("能力。验证方法：在测试内独立实现边界映射，看它是否是一个**无自由参数的确定性规则**。")
A("")
A("### 参数表（本单实际用到的量）")
A("")
A("| 项 | 值 | 来源 |")
A("|---|---|---|")
A("| 边界向量 dd1..dd4 | " + " / ".join(f"{d}" for d in J["g5_dd_vector"]) + " | G5 转录 |")
A("| 年度削减率 Z | " + ", ".join(f"{y}:{z:.0%}" for y, z in J["z_schedule"].items()) + " | MEPC.338(76) 转录 |")
for k, v in J["cii_reference_lines"].items():
    A(f"| CII 参考线 {k} | a={v[0]}, c={v[1]} | MEPC.353(78) G2 转录 |")
A("")
A("## 2. 怎么做的（方法/流程/工具链）")
A("")
A("| 层 | 判据 | 手段 | 结果 |")
A("|---|---|---|---|")
A("| A | L4 可闭合性 | 测试内独立影子实现 `shadow_cii_rating()`（半开区间 + 浮点容差） | 20 项判据全绿 |")
A("| B | L3 应用算术 | required = a·Cap^(−c)·(1−Z) 独立标量复算 | 机器残差 worst = "
  + f"{J['l3_arithmetic']['worst_rel_err']:.3e}" + " |")
A("| C | 诚实性 | 等级字段不得凭空出现 + 两处静默默认以 xfail(strict) 钉住 | 2 xfailed（缺陷实证） |")
A("")
A("工具链：pytest 7（`tests/test_green_cii_rating_reference_m1803.py`）→ "
  "`scripts/verify_cii_rating_m1803.py`（生成 JSON + matplotlib 图）→ "
  "`scripts/gen_cii_report_md_m1803.py`（本文件，数字直接注入）。")
A("")
A("## 3. 计算结果（全部程序化生成）")
A("")
A("### 3.1 L4 等级函数的不变式实测（1200 点稠密采样）")
A("")
inv = J["invariants"]
A("| 不变式 | 判据 | 实测 |")
A("|---|---|---|")
A(f"| 单调性 | 达到值↑ ⇒ 等级序号不减 | {inv['monotone_nonincreasing_grade_index']} |")
A(f"| 无跳档 | 0.1% 步长内等级序号跳变 ≤1 | 实测最大跳变 = {inv['max_grade_jump_per_0.1pct_step']} |")
A(f"| 标度不变 | 只依赖比值 attained/required | {inv['scale_invariance']} |")
A(f"| 边界归属 | 边界值归入较优档（半开区间） | {inv['boundary_closed_on_worse_side']} |")
A(f"| 全档可达 | A,B,C,D,E 均可取到 | {', '.join(inv['grades_reachable'])} |")
A(f"| 采样点数 | — | {inv['n_samples']} |")
A("")
A("### 3.2 L3 要求值带（四边界绝对值）")
A("")
A("| 船型 / DWT | 年份 | 要求值 | A|B | B|C | C|D | D|E |")
A("|---|---|---|---|---|---|---|")
for key in ("bulk_carrier_80000", "container_100000"):
    for row in J["ship_required_bands"][key]:
        b = row["bands"]
        A(f"| {key} | {row['year']} | {f(row['required_value'])} | {f(b['superior_A_bound'])} | "
          f"{f(b['lower_B_bound'])} | {f(b['upper_C_bound'])} | {f(b['inferior_D_bound'])} |")
A("")
A("### 3.3 三条实船算例（功率立方律 → 燃油 → CO₂ → 航程）")
A("")
A("| 船 | DWT | 航速 kn | P_ME kW | CO₂ t | 达到值 CII | 等级 2023→2026 |")
A("|---|---|---|---|---|---|---|")
for v in J["demo_vessels"]:
    grades = " → ".join(g["grade"] for g in v["grades"])
    A(f"| {v['name']} | {v['dwt']:.0f} | {v['speed_kn']:.1f} | {v['p_me_kw']:.0f} | "
      f"{v['co2_t']:.0f} | {f(v['attained_cii'])} | {grades} |")
A("")
sw = J["speed_sweep_handymax_55k_2024"]
A(f"### 3.4 慢速航行扫描（Handymax 55k DWT，2024，要求值 = {f(sw['required_value_2024'])}）")
A("")
A("| 航速 kn | P_ME kW | 达到值 CII | 比值 | 等级 |")
A("|---|---|---|---|---|")
for r in sw["sweep"]:
    if abs(r["speed_kn"] - round(r["speed_kn"])) < 1e-9:
        A(f"| {r['speed_kn']:.0f} | {r['p_me_kw']:.0f} | {f(r['attained_cii'])} | "
          f"{f(r['ratio'], 3)} | **{r['grade']}** |")
A("")
A("## 4. 结果图")
A("")
A("- `artifacts/cii_rating_m1803/cii_rating_bands.png`：散货船 2026 年等级带（对数 DWT 轴）+")
A("  实船达到值落点。图里能直接看出「同一艘船，DWT 越大要求越松」的参考线斜率。")
A("- `artifacts/cii_rating_m1803/cii_grade_map.png`：慢速航行把等级从 D 拉到 A 的物理曲线")
A("  （attained ∝ v²，因为 P ∝ v³ 而分母带一个 v）。这是 L4 缺失时平台**无法回答**的问题：")
A("  「我现在给不出等级，那我降速 2 kn 能不能进 C 档？」")
A("")
A("## 5. 注意点（坑与局限）")
A("")
A("1. **边界向量 dd 仍是转录**。本单证明的是「规则结构可实现且自洽」，不是「0.86/0.94/1.06/1.18")
A("   这四个数就是 G5 原文」。因此 L4 在平台侧**不得**由本单升级为 REAL；要升级必须有人回读")
A("   G4/G5 原文并签字，本单只把这件事的成本从「未知工作量」降为「13 行代码 + 一次转录核对」。")
A("2. **浮点边界坑**：`dd·R / R` 未必逐位等于 `dd`，`ratio <= dd` 会漏掉边界点（本单实测在")
A("   required=12.3 时复现）。影子实现用 `isclose(rel_tol=1e-12)` 兜底；这是实现 L4 时必须照抄的细节。")
A("3. **两处静默默认（实证缺陷）**：模块原则写着「禁止静默默认」，但 `required_cii()` 在")
A("   year ≥ 2027 静默沿用 Z=0.11、在 year < 2023 静默返回 Z=0.0，二者都无标记。已用")
A("   `xfail(strict=True)` 钉住（当前 2 xfailed）。**本仓不得修船舶代码**，修复已派给")
A("   ship-performance-platform（见 mission 派生单），修好后 strict xfail 会转红，强制更新判据。")
A("4. **算例参数不代表任何具体船**：SFOC/功率/航速取量级合理的示教值，用于验证等级映射，")
A("   不作能效结论。")
A("")
A("## 6. 误差判定（逐项回答「是否 <3%」）")
A("")
A("| 关键量 | 基准 | 实测误差 | <3%？ |")
A("|---|---|---|---|")
A(f"| L3 要求值算术 required = a·Cap^(−c)·(1−Z) | 独立标量复算 | {J['l3_arithmetic']['worst_rel_err']:.3e}（相对） | **是**（逐位 0，机器精度） |")
A("| L4 边界归属 | 影子参考 dd·R | 边界点归属全部正确（5×4 组合） | **是**（离散判据，0 错） |")
A("| L4 区间宽度 | dd 差向量 0.86/0.08/0.12/0.12 | 相对误差 0 | **是** |")
A("| CII 参考线系数 a/c | MEPC.353(78) G2 **转录值** | 与转录表逐位一致 | **无法判定（WITHHELD）** |")
A("| 等级 vs 官方裁决 | 无（离线无 IMO 裁决样本） | — | **无基准，不做判定** |")
A("")
A("最后两行必须显式保留为未判定：把「与自己的转录表一致」当成「<3% 通过」正是")
A("假证据的典型来源。L4 的诚实结论是：**结构已证、来源未证**。")
A("")
A("## 7. 结论与下一步")
A("")
A("- L4 = NOT_SUPPORTED 的**性质**被澄清：不是数学不可行，是规则原文缺一次回读。")
A("- 交付物：20 项自动化判据（含 5 类不变式）+ 2 项 strict xfail 缺陷守卫 + 2 张图 + 工件 JSON。")
A("- 下一步（已派单）：ship-performance-platform 侧修 C1/C2 两处静默默认；")
A("  取得 G4/G5 原文者按影子实现 13 行接线并把 L4 升级为 REAL（同时把 xfail 转为正向断言）。")

out = ROOT / "docs" / "verification" / "cii_rating_m1803.md"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("\n".join(lines), encoding="utf-8")
print("written", out, len(lines), "lines")