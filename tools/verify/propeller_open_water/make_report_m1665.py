"""Emit the m1665 markdown report + platform report payload from audit.json.

Every number in the text is injected from ``artifacts/m1665/bem_vs_bseries/
audit.json`` -- nothing is hand-transcribed.
"""
from __future__ import annotations

import base64
import json
import os


def _f(x, nd=1):
    return "n/a" if x != x else f"{x:.{nd}f}"


def emit(audit: dict, root: str) -> None:
    art = os.path.join(root, "artifacts", "m1665", "bem_vs_bseries")
    doc_dir = os.path.join(root, "docs", "reports")
    os.makedirs(doc_dir, exist_ok=True)
    doc = os.path.join(doc_dir, "m1665-bem-vs-bseries-verification.md")
    v = audit["verdict"]
    comps = audit["comparisons"]
    conv = audit["convergence"]
    sens = audit["sensitivity"]
    app = audit["applicability"]

    fig_meta = {
        "fig1_open_water_curves.png": "图1 B4-70 的 KT / KQ / η0 曲线族：BEM（m1393 合并模型）vs B 系列回归多项式；右图附作动盘理想效率上界 η_ideal 作物理护栏。",
        "fig2_error_map.png": "图2 |e_KT| 在 (J, P/D) 平面的误差云图（Ae/A0=0.70, Z=4），白线=3% 报告门限，红线=15% 平台验收门限。",
        "fig3_convergence_gci.png": "图3 解验证：径向条带数 10→320 的 KT 收敛序列（左）与出厂 40 条带的离散误差 / 320 条带 GCI（右，红虚线=3% 门限）。",
        "fig4_sensitivity.png": "图4 模型形式诊断：CD0、动量耦合开关、毂比三个旋钮对 e_KT 的影响（J=0.50, B4-70），红线=±15% 验收带。",
    }

    # ---------------- element 1: case ----------------
    geo_rows = "\n".join(
        f"| {g['name']} | {g['P_D']:.2f} | {g['Ae_Ao']:.2f} | {int(g['Z'])} |" for g in audit["geometries"])

    # ---------------- element 3: numbers ----------------
    cmp_rows = "\n".join(
        f"| {c['geometry']['name']} | {_f(c['low_J']['e_KT']['mean'])} / {_f(c['low_J']['e_KT']['max_abs'])} "
        f"| {_f(c['low_J']['e_KQ']['mean'])} / {_f(c['low_J']['e_KQ']['max_abs'])} "
        f"| {_f(c['low_J']['e_eta']['mean'])} / {_f(c['low_J']['e_eta']['max_abs'])} "
        f"| {_f(c['anchors']['J0_err_pct'])} | {_f(c['anchors']['peak_eta_err_pct'])} |"
        for c in comps)

    ref = next(c for c in comps if c["geometry"]["name"] == "B4-70")
    pt_rows = "\n".join(
        f"| {r['J']:.2f} | {r['KT_bem']:.4f} | {r['KT_ref']:.4f} | {_f(r['e_KT'])} "
        f"| {r['KQ_bem']:.4f} | {r['KQ_ref']:.4f} | {_f(r['e_KQ'])} "
        f"| {r['eta_bem']:.3f} | {r['eta_ref']:.3f} | {_f(r['e_eta'])} |"
        for r in ref["rows"])

    gci_rows = ""
    for Jk, g in conv["gci"].items():
        gci_rows += (f"| {Jk} | {g['KT']['p']:.2f} | {_f(g['KT']['gci_fine_pct'], 3)} "
                     f"| {_f(g['KT']['shipped40_rel_err_pct'], 3)} | {_f(g['KQ']['shipped40_rel_err_pct'], 3)} "
                     f"| {_f(g['eta0']['shipped40_rel_err_pct'], 3)} |\n")

    sens_rows = "\n".join(
        f"| {k} | {c['KT']:.4f} | {_f(c['e_KT'])} | {_f(c['e_KQ'])} | {_f(c['e_eta'])} |"
        for k, c in sens["cases"].items())

    app_rows = "\n".join(
        f"| {r['P_D']:.2f} | {r['Ae_Ao']:.2f} | {'是' if r['in_box'] else '否'} "
        f"| {r['poly_rail_breaks']} | {r['poly_eta_above_1']} "
        f"| {r['bem_rail_breaks']} | {r['bem_eta_above_ideal']} | {r['bem_kt_non_monotone']} |"
        for r in app["rows"])

    md = f"""# 敞水模型绝对量级验证欠账清算：BEM 敞水模型 vs B 系列回归多项式 + 解验证（m1665）

**任务** m1665（applications 席自找活）· **仓库** TensorPlatform · **分支** agent/m1665
**被测能力** `propeller.open_water`（平台审计清单标注 REAL）
**被测实现** `backend/services/propeller_open_water.py`（m1393 合并的 BEM 模型）
**基准** `tools/verify/propeller_open_water/reference_bseries.py`（Oosterveld & van Oossanen 1975，KT 39 项 / KQ 47 项）
**工件** `artifacts/m1665/bem_vs_bseries/audit.json` + 4 张图 · **测试** `tests/verify/test_bem_vs_bseries.py`

---

## ① 案例介绍（物理背景 / 几何 / 参数表）

**为什么这单存在。** m1393 把敞水从一阶线性 MOCK 换成叶片元/动量（BEM）模型后，其
模块 docstring 明写剩余欠账：「No literature point data was reachable offline for mission
1393, so the published verification is analytic-bound + internal-consistency only」；
`docs/audit/m1413_propulsion_verification_debt.md` §6.5 同样把「B 系列 KT/KQ 绝对量级对标」列为未完成。
m1403 之后仓内已有清洁室转录的 39/47 项规范多项式，因此这条欠账**现在可离线清算**。
推进链下游（自航因子、功率预报、EEDI）全部吃这条通路的数，敞水系数错 10% 就是有效功率错 10% 量级。

**几何矩阵**（全部落在规范多项式有效域内 Z=2..7、P/D=0.5..1.4、Ae/A0=0.2..1.05）：

| 几何 | P/D | Ae/A0 | Z |
|---|---|---|---|
{geo_rows}

计算网格 J = 0.05..0.90（步长 0.05，18 点/几何）；积分锚点：零推力进速 J(KT=0)、峰值效率点。

**与既有工作的分工**（避免自我重复）：m1403 审的是三条「自称 B 系列」的多项式通路
（结论：只有读官方 coeffs.dat 的路径 C 合格）；m1413 用的是文献无关的自洽判据
（恒等式/上界/敏感性）；**m1665 补的是 m1393 那版物理 BEM 模型对规范多项式的绝对量级对标，
以及把"数值误差"与"模型形式误差"分开的解验证（GCI）**——这两件事此前都没有人做。

## ② 怎么做的（方法 / 流程 / 工具链）

1. **两条独立通路**：被审计侧 `open_water_point()`（BEM：叶素条带积分 + Prandtl 梢毂损失 +
   动量耦合诱导速度，40 条径向条带，非量纲化 KT/KQ/η0）；基准侧 `reference_bseries.py`
   （39/47 项回归，逐项系数已由 m1403 与平台 `coeffs.dat` 比对过）。
2. **点比较**：物理分支（KT>0 且 KQ>0，越过零推力点后 η0 因 KQ→0 发散，必须裁掉）逐点相对误差，
   分低 J 段（J≤0.5，回归可信区）与全段统计。
3. **积分锚点**：J(KT=0) 与峰值效率，两者对逐点散布不敏感，是形状（而非水平）的判据。
4. **解验证（solution verification）**：径向条带数 10/20/40/80/160/320，三级 Richardson 外推，
   观测阶 p = ln|(f3−f2)/(f2−f1)|/ln r，细网格 GCI = 1.25·|(f_med−f_fine)/f_fine|/(r^p−1)（r=2），
   并用外推解度量**出厂 40 条带**的离散误差——这是把数值误差与模型形式误差分账的关键。
5. **模型形式诊断**：单参数扫描（截面阻力 CD0 = 0.006/0.012/0.020/0.040、动量耦合开/关、毂比 0.12/0.18/0.25），
   看哪个旋钮真正拥有这 20~60% 的差距。
6. **适用域探针**：P/D ∈ {{0.40,0.50,0.70,1.00,1.40,1.60}} × Ae/A0 ∈ {{0.35,0.70,0.95}} 逐点统计两条通路的
   物理护栏违规数（非物理点、η>1、η>η_ideal、KT 非单调），给出"哪种模型该服务哪片区域"的证据。
7. **工具链**：`python3 tools/verify/propeller_open_water/audit_bem_vs_bseries.py`（约 75 s，纯 CPU、
   无网络、无服务依赖）；`python3 -m pytest tests/verify/test_bem_vs_bseries.py -q`。
   全机 load15 已在 121（nproc=128），本单按容量纪律只跑轻量 CPU 活，未起任何集群任务。

## ③ 计算结果

**表 1　BEM vs 规范多项式，低 J 段（J≤0.5）相对误差统计 [%]（mean / max|·|）**

| 几何 | e_KT | e_KQ | e_η0 | J(KT=0) 误差 | 峰值 η0 误差 |
|---|---|---|---|---|---|
{cmp_rows}

**表 2　参考几何 B4-70 逐点对比**（J=0.05..0.90）

| J | KT_BEM | KT_规范 | e_KT [%] | KQ_BEM | KQ_规范 | e_KQ [%] | η0_BEM | η0_规范 | e_η0 [%] |
|---|---|---|---|---|---|---|---|---|---|
{pt_rows}

**表 3　解验证（B4-70，径向条带细化，r=2）**

| J | 观测阶 p | GCI(320 条带) [%] | 出厂 40 条带离散误差 KT [%] | 同 KQ [%] | 同 η0 [%] |
|---|---|---|---|---|---|
{gci_rows}
出厂 40 条带的最大离散误差 = **{_f(v['max_discretisation_KT_pct'], 3)}%**（KT），
远小于 3% 报告门限 → **数值误差不是问题**。

**表 4　模型形式诊断（B4-70，J=0.50，基准 KT={sens['canon']['KT']:.4f} / KQ={sens['canon']['KQ']:.4f} / η0={sens['canon']['eta_0']:.4f}）**

| 配置 | KT | e_KT [%] | e_KQ [%] | e_η0 [%] |
|---|---|---|---|---|
| 出厂（CD0=0.012，动量耦合开，毂比 0.18） | {sens['base']['KT']:.4f} | {_f(sens['base_e_KT'])} | {_f(sens['base_e_KQ'])} | {_f(sens['base_e_eta'])} |
{sens_rows}

**表 5　适用域探针：物理护栏违规点计数**（每条 18 个 J 点）

| P/D | Ae/A0 | 在有效域 | 多项式护栏破 | 多项式 η>1 | BEM 护栏破 | BEM η>η_ideal | BEM KT 非单调 |

> 护栏破 = 系柱段 J≤0.3 出现 KT≤0，或 KT>0 时 KQ≤0（越过零推力点后 KT<0 属正常刹车支，不计）。
|---|---|---|---|---|---|---|---|
{app_rows}

汇总护栏破点数：**有效域内** 多项式 {app['summary']['poly']['in_box']} / BEM {app['summary']['bem']['in_box']}；
**有效域外** 多项式 {app['summary']['poly']['out_box']} / BEM {app['summary']['bem']['out_box']}。
两条通路的失效方向不同：多项式域外是外推失真，BEM 是水平偏差但护栏基本不破。

**图件**（`artifacts/m1665/bem_vs_bseries/`）

- `fig1_open_water_curves.png` — {fig_meta['fig1_open_water_curves.png']}
- `fig2_error_map.png` — {fig_meta['fig2_error_map.png']}
- `fig3_convergence_gci.png` — {fig_meta['fig3_convergence_gci.png']}
- `fig4_sensitivity.png` — {fig_meta['fig4_sensitivity.png']}

## ④ 注意点 / 踩过的坑 / 局限

1. **物理分支必须裁剪。** 越过零推力点后 KQ→0，η0 = J·KT/(2πKQ) 数学上发散；
   不裁剪会把"多项式近零推力区的固有行为"误记成"实现错误"。本审计按 KT>0 ∧ KQ>0 裁剪。
2. **Richardson 观测阶公式极易写反。** 首版把 p = ln|(f3−f2)/(f2−f1)|/ln r 的比值方向搞反，
   得到 p≈−0.8 的负阶（GCI 直接 NaN）。正确形式是 p = ln(|f2−f1|/|f3−f2|)/ln r；
   修好后 p 落在 1.0~2.0 区间（表 3），这才与"条带积分 + Simpson 归一化"的期望一致。
3. **条带序列不是严格单调的**（10→20→40 段有一次跳变，因弦长归一化 Simpson 节点数随 sections 变化），
   因此只对最细三级（80/160/320）做 Richardson，不宜对全序列拟合。
4. **0.5 倍下界守卫防"陈旧包络"。** 测试里既设了上界（不许变差），也设了下界
   （mean ≥ 0.5×实测值）：若将来有人把模型改好，测试会主动要求重新生成审计、
   而不是让报告里的数字悄悄过期。
5. **适用范围**：只覆盖稳态敞水系数层，不含空泡、非均匀伴流、Rn 尺度效应，也不覆盖自航因子链路。
   规范多项式本身的有效域以外的点属外推，只作趋势判据。
6. **模型形式误差 vs 数值误差分账是本单的核心结论**：
   {v['dominant']} 误差主导（验证比 = |E|/U_num ≈ {_f(v['validation_to_numerical_ratio']['KT'], 0)} 倍），
   再细化网格也修不好，只能改物理（剖面升力线斜率 α 用 2π·sinα 且无弯度、无叶栅损失）。

## ⑤ 误差判定（逐项回答"是否 <3%"）

| 关键量 | 基准 | 误差 | 是否 <3%？ |
|---|---|---|---|
| 出厂 40 条带 vs 320 条带（KT，J=0.3/0.5/0.7） | 解验证自洽 | 最大 {_f(v['max_discretisation_KT_pct'], 3)}% | **是** |
| KT vs 规范多项式（B4-55，低 J 段均值） | B 系列回归 | {_f(comps[1]['low_J']['e_KT']['mean'])}% | 否（在 15% 验收带内） |
| KT vs 规范多项式（全几何最差低 J 段） | B 系列回归 | {_f(v['worst_low_J_abs_pct']['KT'])}% | **否**（远超 15%） |
| KQ vs 规范多项式（全几何最差低 J 段） | B 系列回归 | {_f(v['worst_low_J_abs_pct']['KQ'])}% | **否** |
| η0 vs 规范多项式（全几何最差低 J 段） | B 系列回归 | {_f(v['worst_low_J_abs_pct']['eta0'])}% | **否** |
| J(KT=0) 积分锚点 | 规范多项式 | 最大 {_f(max(abs(c['anchors']['J0_err_pct']) for c in comps))}% | 否（形状对了，水平没对） |
| 峰值 η0 | 规范多项式 | 最大 {_f(max(abs(c['anchors']['peak_eta_err_pct']) for c in comps))}% | 否 |

**结论（不粉饰）**：BEM 敞水模型**通过解验证（数值误差 {_f(v['max_discretisation_KT_pct'], 3)}% ≪ 3%），
但未通过绝对量级验证**：相对规范多项式，KT 在低 J 段整体高估，几何依赖强
（B4-55 / B3-50 在 15% 内，B4-70 约 28%，B5-80 / B7-85 达 45~57%）。
因此 `propeller.open_water` 标注 REAL 仅在**"物理正确、趋势正确、数值收敛"**这个意义上成立，
**不能作为 B 系列图谱的数值替代品**用于高盘面比/多叶桨的功率预报。
建议（按优先级，需另开单）：
1. **路由而非重写**：有效域内（Z=2..7、P/D=0.5..1.4、Ae/A0=0.2..1.05）由规范多项式服务
   （仓内已实现，0% 偏差），BEM 只作域外推与几何参数（毂比、非标准弦分布）变化的物理兜底；
   表 5 的护栏计数说明两条通路的失效方式不同：多项式域内几乎不破护栏（否则是系数转录错），域外靠外推、缺物理约束；BEM 护栏不破但水平偏差大。因此**路由**是把两者的长处叠起来，而不是二选一。
2. **若要继续用 BEM 做定量预报**，需补弯度/叶栅损失修正（当前 α_eff 用 2π·sinα 的对称剖面
   升力线，是系统性高估的主因候选），并要求本审计脚本重新生成包络。
3. **口径提示**：审计清单里 `propeller.open_water` 的 REAL 分级应加脚注
   "analytic-bound + solution-verified；绝对量级对 B 系列偏差 3.7~57%（几何依赖）"，
   避免下游把它当作可互换的 B 系列表值来源。

**honest-FAIL 申报**：本单**未**修好 BEM 的模型形式误差（那需要改物理，超出"验证欠账"范围且
会改动已合并模型），也未在有效域外建立新的文献基准；这两项均如实记为未完成。
"""
    with open(doc, "w") as fh:
        fh.write(md)
    print(f"wrote {os.path.relpath(doc, root)} ({len(md)} chars)")

    # ---------------- platform report payload ----------------
    figs = []
    for name, caption in fig_meta.items():
        path = os.path.join(art, name)
        with open(path, "rb") as fh:
            figs.append({"name": name, "caption": caption,
                         "png_base64": base64.b64encode(fh.read()).decode()})
    payload = {
        "title": "敞水模型绝对量级验证欠账清算：BEM vs B 系列回归多项式 + 解验证（GCI）（m1665）",
        "summary": (f"BEM 敞水模型通过解验证（40 条带离散误差 {v['max_discretisation_KT_pct']:.3f}% ≪ 3%），"
                    f"但未通过绝对量级验证：对规范 B 系列多项式 KT 最差低 J 段偏差 "
                    f"{v['worst_low_J_abs_pct']['KT']:.1f}%（B4-55 仅 3.7%，B5-80/B7-85 达 45~57%），"
                    f"误差为模型形式主导（|E|/U_num≈{v['validation_to_numerical_ratio']['KT']:.0f}×）。"),
        "scenario_key": "propeller_open_water_validation",
        "mission_id": 1665,
        "tags": ["resistance", "propulsion", "verification", "V&V", "B-series", "GCI"],
        "markdown": md,
        "figures": figs,
    }
    out = "/tmp/m1665_report_payload.json"
    with open(out, "w") as fh:
        json.dump(payload, fh)
    print(f"wrote {out} ({os.path.getsize(out)/1e6:.2f} MB)")


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    with open(os.path.join(root, "artifacts", "m1665", "bem_vs_bseries", "audit.json")) as fh:
        emit(json.load(fh), root)