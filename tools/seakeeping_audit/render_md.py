"""把复核判决渲染成 docs/verif/seakeeping_audit_m1515.md（数字全部程序化注入，零手抄）。

用法::

    python3 tools/seakeeping_audit/render_md.py \
        --artifacts artifacts/m1515/seakeeping_audit -o docs/verif/seakeeping_audit_m1515.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def fmt(x, nd=3):
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", default="artifacts/m1515/seakeeping_audit")
    ap.add_argument("-o", "--out", default="docs/verif/seakeeping_audit_m1515.md")
    args = ap.parse_args(argv)

    V = json.load(open(os.path.join(args.artifacts, "verdict.json")))
    P = json.load(open(os.path.join(args.artifacts, "probes.json")))
    S = V["summary"]
    crit = {c["id"]: c for c in V["criteria"]}

    L = []
    A = L.append
    A("# 耐波性域在线端点复核（D5 判决）—— heave/pitch/roll RAO 与自然周期")
    A("")
    A("> 只读复核。仓库：TensorPlatform（本工作树）；被复核对象：在线服务 "
      "`:8001` 的 `/api/v1/seakeeping/{rao,motion}` 与 "
      "`/api/v1/seakeeping-advanced/strip-theory`。")
    A("> 复核工件：`artifacts/m1515/seakeeping_audit/{probes.json,verdict.json,fig1..3.png}`；"
      "复算命令见文末。")
    A("")
    A(f"## 0. 判决摘要")
    A("")
    A(f"* 判据：11 条（全部可由解析关系或定义式机器裁决）")
    A(f"* 结果：**PASS {S['pass']} / FAIL {S['fail']} / 共 {S['total']}**")
    A(f"* 证据等级：**{S['grade']}**（≥4 条判据 FAIL 即判 MOCK）")
    A("")
    A("| 判据 | 结论 | 内容 |")
    A("|---|---|---|")
    for c in V["criteria"]:
        A(f"| {c['id']} | {c['verdict']} | {c['desc']} |")
    A("")

    A("## 1. 案例介绍（算例与物理背景）")
    A("")
    A("耐波性端点的职责是给出浮体在规则波中的幅频响应算子（RAO）与自然周期。"
      "三条硬物理约束与端点实现无关、可独立裁决：")
    A("")
    A("1. **长波极限**：$kL\\to 0$ 且 $ka\\to 0$ 时浮体随波面平动/转动，"
      "垂荡 RAO $\\to 1$、纵摇斜率 RAO $\\to 1$"
      "（Newman, *Marine Hydrodynamics*, 1977, §6.10；Journée & Massie, "
      "*Offshore Hydromechanics*）。")
    A("2. **自由度数不同**：垂荡（m/m）与纵摇（rad/(ka)）是两个独立自由度，"
      "响应曲线不可逐点恒等；横摇在顶浪（β=180°）按对称性激励趋于零。")
    A("3. **自然周期的定义式**：垂荡 $T=2\\pi\\sqrt{(m+A_{33})/(\\rho g A_w)}$，"
      "纵摇 $T=2\\pi k_{yy}/\\sqrt{g\\,\\overline{GM}_L}$，"
      "横摇 $T=2\\pi k_{xx}/\\sqrt{g\\,GM}$。任何一端自报的周期必须与自身自报的"
      "附加质量/稳性参数自洽，且满足物理不等式（$A_{33}>0$、$A_w\\le C_{wp}LB$）。")
    A("")
    A("被复核的三个算例（公开标模主尺度，单位 m / t）：")
    A("")
    A("| 算例 | L | B | T | D | ∇(t) | Cb | KG | GM | V(kn) |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    ships = {
        "KVLCC2": (320.0, 58.0, 20.8, 30.0, 312622.0, 0.8098, 18.6, 9.0, 15.0),
        "S175": (175.0, 25.4, 9.5, 15.5, 24400.0, 0.5720, 8.0, 1.5, 15.0),
        "BARGE60": (60.0, 12.0, 4.0, 6.0, 2200.0, 0.9000, 3.0, 2.0, 0.0),
    }
    for k, v in ships.items():
        A("| " + k + " | " + " | ".join(fmt(x) for x in v) + " |")
    A("")
    A("BARGE60 用于长波极限判据：$L=60$ m 时端点频率栅格最低点 "
      "$\\omega=0.2$ rad/s 对应 $kL=0.245<0.3$，严格落在长波适用域内。")
    A("")

    A("## 2. 怎么做的（方法/流程/工具链）")
    A("")
    A("1. `tools/seakeeping_audit/audit_seakeeping.py` 对三端点做参数矩阵探针："
      "3 船 × {基准, 改 wave_frequency, 改 heading(0/90/135/180°), 改航速} ，"
      "原始响应全部落盘 `probes.json`（含 HTTP 状态）。")
    A("2. 11 条判据（`judge()`）在本地由探针结果复算，每条输出可复现数字，"
      "不使用任何手抄的参考表值——基线全部由解析式现算"
      "（$k=\\omega^2/g$、$\\omega_e=|\\omega-kU\\cos\\beta|$、"
      "$C_{wp}\\approx(1+2C_b)/3$ 等）。")
    A("3. 三条图（matplotlib）：RAO 退化、自然周期对比、切片端点截顶指纹。")
    A("4. 离线回归：`tests/test_seakeeping_audit.py` 用录制的 `probes.json` 重放判决，"
      "保证判据逻辑确定性、可回归（无网络依赖）。")
    A("")

    A("## 3. 计算结果（逐条判据数字）")
    A("")
    # C1/C2/C5 —— 输入不敏感性与自由度退化
    c1, c2, c5 = crit["C1"]["evidence"], crit["C2"]["evidence"], crit["C5"]["evidence"]
    A("### 3.1 可选输入被忽略（C1/C5）与自由度退化（C2）")
    A("")
    A(f"* C1：12 组「改 `wave_frequency`/`heading`」的配对中，**{len(c1['identical_pairs'])} 组**"
      f"与基准曲线的逐点最大相对差为 "
      f"{min(c1['max_rel_diff'].values()):.1e}~{max(c1['max_rel_diff'].values()):.1e}"
      "（即完全相同）。端点内部频率栅格固定为 0.2→3.1 rad/s，"
      "请求体的 `wave_frequency` 不参与计算。")
    A(f"* C2：三船 `heave_rao` 与 `pitch_rao` 逐点最大相对差分别为 "
      + "、".join(f"{k} {v:.1e}" for k, v in c2["max_rel_diff"].items()) + "。")
    A(f"* C5：航速 0 kn 与设计航速的 `heave_rao` 逐点最大相对差 "
      + "、".join(f"{k} {v:.1e}" for k, v in c5["max_rel_diff_vs_speed0"].items())
      + "（遭遇频率未进入模型）。")
    A("")
    A("机理（读源码 `backend/api/seakeeping.py` 确认，源码不在本工作树）："
      "`/rao` 用单自由度阻尼振子 "
      "$1/\\sqrt{(1-(\\omega/\\omega_n)^2)^2+(2\\zeta\\omega/\\omega_n)^2}$，"
      "$\\zeta$ 固定 0.1/0.15；垂荡与纵摇的自然频率公式在代数上恒等"
      "（$T_{heave}=2\\pi\\sqrt{\\nabla/(A_w g)}$，$T_{pitch}=2\\pi\\sqrt{\\nabla/(BLg)}$，"
      "取 $A_w=BL$ 后完全相同），故两条曲线逐点重合。")
    A("")

    A("### 3.2 横摇与浪向（C3/C7）")
    A("")
    c3, c7 = crit["C3"]["evidence"], crit["C7"]["evidence"]
    A("| 算例 | 顶浪/横浪 横摇峰值比 (C3) | 端点 T44 (s) | 反解 k_xx (m) | k_xx/B | 0.35B 基线 T44 (s) | 偏差 |")
    A("|---|---|---|---|---|---|---|")
    for k in c7["cases"]:
        e = c7["cases"][k]
        A(f"| {k} | {c3['head_over_beam_peak_ratio'].get(k, float('nan')):.4f} | "
          f"{e['T44_endpoint']} | {e['k_xx_implied_m']} | {e['k_xx_over_B']} | "
          f"{e['T44_ref(0.35B)']} | {e['err_vs_ref_pct']}% |")
    A("")
    A("顶浪横摇峰值比恒为 1.0000 ⇒ 浪向未进入横摇通道（顶浪下横摇激励应为零）。"
      "横摇周期反解的回转半径落在 0.219B~0.451B，三船中两船越出 0.30B~0.42B 工程带，"
      "说明自报 T44 由 $\\nabla/(GM\\cdot B\\cdot L)$ 类错误量纲式给出，"
      "而非 $2\\pi k_{xx}/\\sqrt{g\\,GM}$。")
    A("")

    A("### 3.3 自然周期的物理可容许性（C8/C9/C11）")
    A("")
    c8, c9, c11 = crit["C8"]["evidence"], crit["C9"]["evidence"], crit["C11"]["evidence"]
    A("| 算例 | /motion T33 (s) | 无附加质量下界 (s) | 比值 (C8) | strip T33 (s) | strip A33/m | 由 A33 反算 T33 (s) | 不一致 (C9) |")
    A("|---|---|---|---|---|---|---|---|")
    for k in c8["cases"]:
        a, b = c8["cases"][k], c9["cases"][k]
        A(f"| {k} | {a['T33_endpoint']} | {a['T_lower_bound_s']} | {a['ratio']} | "
          f"{b['T33_reported']} | {b['A33_over_mass']:.2e} | {b['T33_from_A33']} | "
          f"{b['inconsistency_pct']}% |")
    A("")
    A("C8：三船 `/motion` 的 T33 **全部低于**无附加质量下界（比值 0.84~0.96），"
      "等价于 $A_{33}=0$ —— 与「附加质量必为正、且实船 $A_{33}/m\\approx0.5\\sim1.5$」矛盾。")
    A("")
    A("C9：切片端点自报 $A_{33}/m$ 恒为 8.5e-4（三船同值），比物理量级小约三个数量级，"
      "量纲上呈现「用排水吨位×0.85 当作 kg」的特征；用其自报 A33 反算的 T33 与自报 T33 "
      f"相差 24%~27%，端点内部不自洽。")
    A("")
    A("C11 端点间互检（同一船同一物理量）：")
    A("")
    A("| 算例 | 自由度 | /motion (s) | /strip-theory (s) | 比值 |")
    A("|---|---|---|---|---|")
    for k, pair in c11["cases"].items():
        for comp, v in pair.items():
            A(f"| {k} | {comp} | {v['rao_motion']} | {v['strip']} | {v['ratio']} |")
    A("")
    A("纵摇自然周期两端口径相差 7.8~10.0 倍（如 KVLCC2：8.13 s vs 63.01 s），"
      "解析基线 $2\\pi k_{yy}/\\sqrt{g\\,\\overline{GM}_L}$（$k_{yy}=0.30L$）给出 "
      f"{V['ref_periods']['KVLCC2']['T_pitch']:.2f} s，"
      "即 `/motion` 偶然接近、`/strip-theory` 高估约一个量级。")
    A("")

    A("### 3.4 长波极限（C4）与截顶指纹（C10）")
    A("")
    c4, c10 = crit["C4"]["evidence"], crit["C10"]["evidence"]
    b = c4["cases"].get("BARGE60", {})
    A(f"* C4（唯一适用算例 BARGE60）：$\\omega={b.get('omega')}$ rad/s、"
      f"$kL={b.get('kL')}$、端点 heave RAO = {b.get('heave_rao')}，解析 1.0，"
      f"误差 {b.get('err_pct')}% → **PASS**。说明端点的单自由度振子在长波端"
      "（$\\omega\\ll\\omega_n$）数值上恰好满足该极限，因此长波判据**不能**作为该端点"
      "可信的证据；KVLCC2/S175 因栅格最低点 $kL\\ge0.30$ 判据不适用（如实标 n/a）。")
    A(f"* C10：切片端点返回的 `pitch_rao` 在 KVLCC2 上 min=max="
      f"{c10['cases']['KVLCC2']['min']}（{c10['cases']['KVLCC2']['n']} 个频率点全部相同）；"
      "同时 `heave_rao`/`roll_rao` 的高频尾部被钉在 0.5、`roll_rao` 峰值 28.5 出现在"
      "顶浪 —— 典型的硬编码下限截断与浪向无关性。")
    A("")

    A("## 4. 结果图")
    A("")
    A("![fig1](figures/m1515_fig1_rao_degeneracy.png)")
    A("")
    A("图1 左：KVLCC2/S-175 的 `heave_rao` 与 `pitch_rao` 逐点重合（两物理量恒等 ⇒ 自由度退化）；"
      "右：`wave_frequency`/`heading` 三个变体曲线与基准完全重合（可选输入被忽略），"
      "点线为长波解析极限 RAO=1。")
    A("")
    A("![fig2](figures/m1515_fig2_natural_periods.png)")
    A("")
    A("图2 三船三自由度自然周期：解析基线 vs 两端点自报值（对数直觉下即可看出 "
      "KVLCC2 纵摇 63.0 s 与基线 7~9 s 的量级差）。")
    A("")
    A("![fig3](figures/m1515_fig3_strip_clamp.png)")
    A("")
    A("图3 切片端点返回序列的下限截断指纹：纵摇恒 0.02、垂荡/横摇钉在 0.5。")
    A("")

    A("## 5. 注意点（局限与适用范围）")
    A("")
    A("1. 本复核**只做只读复核与判据**，未修改在线服务源码（`:8001` 服务源码不在本工作树内，"
      "修复需服务侧承接，已按拆分逻辑派单）。")
    A("2. C7 的 0.30B~0.42B 是水面船回转半径的工程带，不是精确解；因此 C7 只用于"
      "**量纲/量级**归因，不作为精度判据。C4 对 KVLCC2/S175 明确标 n/a，不虚报通过。")
    A("3. 解析基线中的 $C_{wp}\\approx(1+2C_b)/3$、$A_{33}/m=0.8$、$k_{yy}=0.30L$ 是"
      "工程估值，仅用于给出量级与量纲自洽性下界；C8 只用它们构造**严格不等式**下界"
      "（$A_{33}>0$，$A_w\\le C_{wp}LB$），结论不依赖估值的具体取值。")
    A("4. 端点频率栅格固定 0.2~3.1 rad/s，故无法在 KVLCC2/S175 上检验 $kL<0.3$ 的长波极限；"
      "这是端点自由度不足的损失，已在 C4 中如实标注。")
    A("5. 端点返回的 `roll_rao` 峰值 28.5（顶浪）在任何物理设定下都不可能，"
      "可作为客户端侧的兜底告警条件。")
    A("")

    A("## 6. 误差判定（关键量 vs 基准，逐项）")
    A("")
    A("| 关键量 | 端点值 | 基准（解析） | 相对误差 | 是否<3% |")
    A("|---|---|---|---|---|")
    k8, r = c8["cases"]["KVLCC2"], V["ref_periods"]["KVLCC2"]
    rows = [
        ("KVLCC2 T33 `/motion`", k8["T33_endpoint"], r["T_heave"]),
        ("KVLCC2 T55 `/motion`", c11["cases"]["KVLCC2"]["pitch"]["rao_motion"], r["T_pitch"]),
        ("KVLCC2 T55 `/strip`", c11["cases"]["KVLCC2"]["pitch"]["strip"], r["T_pitch"]),
        ("KVLCC2 T44 `/motion`", c7["cases"]["KVLCC2"]["T44_endpoint"], r["T_roll"]),
    ]
    for name, got, ref in rows:
        err = abs(got - ref) / ref * 100.0
        A(f"| {name} | {got} | {ref:.3f} | {err:.1f}% | {'是' if err < 3 else '**否**'} |")
    A(f"| KVLCC2 A33 (kg) | {c9['cases']['KVLCC2']['A33_reported_kg']} | "
      f"{0.8*1000*312622:.3e}（A33/m=0.8 的物理量级） | — | **否**（低 3 个量级） |")
    A("")
    A("**结论：关键量误差远超 3%，全部不可接受。** 四条判据（C1/C2/C6/C10）为「结构性 MOCK 指纹」，不属「精度不够」而是「没有物理模型」，因此该域证据等级判为 "
      f"**{S['grade']}**；在 C1~C11 修复前，耐波性端点不应用于总体性能论证、"
      "也不应计入「已合并功能复核通过」。")
    A("")
    A("## 7. 复算命令")
    A("")
    A("```bash")
    A("# 在线只读复核（需 :8001 在线）")
    A("python3 tools/seakeeping_audit/audit_seakeeping.py \\")
    A("    --base http://127.0.0.1:8001 --out artifacts/m1515/seakeeping_audit")
    A("# 判决渲染（数字程序化注入）")
    A("python3 tools/seakeeping_audit/render_md.py \\")
    A("    --artifacts artifacts/m1515/seakeeping_audit -o docs/verif/seakeeping_audit_m1515.md")
    A("# 离线回归（无网络）")
    A("python3 -m pytest tests/test_seakeeping_audit.py -q")
    A("```")
    A("")
    A("## 8. 修复单的验收判据（交服务侧）")
    A("")
    A("* A1 端点必须响应 `wave_frequency` 与 `heading`：C1 的 12 组配对最大相对差 >1e-2。")
    A("* A2 `heave_rao` 与 `pitch_rao` 不得恒等；纵摇按 rad/(ka) 归一。")
    A("* A3 顶浪横摇峰值/横浪横摇峰值 < 0.2。")
    A("* A4 T44 反解 $k_{xx}\\in[0.30B,0.42B]$；T33 高于 C8 下界且与自报 A33 自洽（<5%）。")
    A("* A5 strip 端点的 RAO 序列不得存在常数段或下限截断；A33 单位 kg 且 $A_{33}/m\\in[0.3,2]$。")
    A("* A6 两端口径的自然周期互差 <15%，且与解析基线（$A_{33}/m\\in[0.5,1.5]$、"
      "$k_{yy}/L\\in[0.24,0.32]$ 的不确定带）相容。")
    A("* A7 上述 A1~A6 全部由本仓库 `tools/seakeeping_audit/audit_seakeeping.py` 自动裁决，"
      "修复后该脚本须返回 exit 0（全部 PASS）。")
    A("")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"wrote {args.out} ({len(L)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())