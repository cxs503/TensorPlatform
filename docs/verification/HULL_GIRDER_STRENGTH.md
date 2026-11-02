# 船体梁极限强度（Smith 递增塌陷）截面层实现与解析基准验证：25/25 判据 PASS

**仓库** TensorPlatform · **分支** `agent/m1592` · **mission** 1592（strategy 席自找活）
**代码** `tools/verify/hull_girder/`（section / smith / loads / benchmarks）·
**测试** `tests/test_hull_girder.py`（19 passed）·
**工件** `artifacts/hull_girder/benchmark_results.json` + 5 图 ·
**文档** `docs/verification/HULL_GIRDER_STRENGTH.md`

## ① 案例介绍（物理背景 / 几何 / 参数表）

船体梁总纵强度分两层：**弹性层**（剖面模数 + 静水/波浪弯矩 → 正应力 vs 规范许用值）与
**极限层**（板格屈曲后承载力 → 递增塌陷 → 极限弯矩 M_u）。平台侧弹性层刚由并行席落地
（报告 1185 / mission 1598），而**极限层此前为空**：任何"结构域已覆盖"的结论都只覆盖到屈服前。
本单补极限层，并把"先造判据再实现"贯彻为 25 条机器可判据的解析基准。

算例几何（理想化双壳/双层底中剖面，钢 σ_y = 235 MPa，E = 206 GPa，ν = 0.3）：

| 量 | 值（程序化注入） |
|---|---|
| 剖面积 A | 6.499 m² |
| 弹性中性轴 z_NA | 12.220 m |
| 惯性矩 I | 714.3 m⁴ |
| 剖面模数 Z_bottom / Z_deck | 58.46 / 55.89 m³ |
| 刚塑性模数 Z_p | 64.54 m³ |
| 一阶屈服弯矩 M_y | 13.135 GN·m |
| 刚塑性极限 M_p | 15.167 GN·m |
| **极限弯矩 M_u（中拱）** | **12.789 GN·m** |
| **极限弯矩 M_u（中垂）** | **12.345 GN·m** |
| M_u/M_p（Euler 平台 / von Karman 平台） | 0.843 / 0.921 |

板厚 30–35 mm 级板格在 0.85 m 肋距下的屈曲应力（Euler 板屈曲 σ_cr = π²E/(12(1-ν²))·(t/b)²）
低于屈服，因此本剖面处在**屈曲控制**区：M_u/M_p = 0.843，即弹性校核通过
并不等于极限校核通过。

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

1. **剖面特性三条独立路径**（互为判据）：矩形构件解析积分、Green 定理多边形轮廓积分、
   教科书同心箱型闭式解；三者在同一箱型算例上必须一致到浮点精度（B1）。
2. **刚塑性恒等式**：Z_p = Σ∫|z − z_eq|dA，z_eq 由等面积轴二分得到；同心箱型须复现
   Z_p = (B D² − b h²)/4（B1/刚塑性测试）。这条恒等式是**手推**的，不共享求解器代码。
3. **Smith 递增塌陷**：板格离散为水平条带，给定曲率求瞬时中性轴（轴向力平衡二分），
   单元加载-缩短关系为弹性-理想塑性 + 屈曲平台（`euler` 保守 / `von_karman` 有效宽度）；
   曲率几何扫描取弯矩极值。中拱（甲板受拉）与中垂（甲板受压）**分别求解**——板格只在压缩侧屈曲，
   两侧承载不同。
4. **载荷层**：IACS UR S11 波浪弯矩闭式（0.19 / 0.11 C L² B Cb），校核 C(L) 两支接缝、
   平台段与端点值；正文只用闭式，不引入无法核验的实测载荷。
5. 工具链：纯 numpy + matplotlib(Agg) + pytest；`benchmarks --write` 产出 JSON + 图，
   `publish` 从 JSON 程序化注入本报告（零手抄）。

## ③ 计算结果（判据逐条，程序化产出）

| 判据 | 名称 | 实现值 | 基准值 | 相对误差 | 阈值 | 判决 |
|---|---|---|---|---|---|---|
| `B1a` | rectangle integration vs closed-form area | 1.2784 | 1.2784 | 3.82e-15 | 0.03 | PASS |
| `B1b` | rectangle integration vs closed-form neutral axis | 6.0000 | 6.0000 | 2.40e-14 | 0.03 | PASS |
| `B1c` | rectangle integration vs closed-form inertia | 34.4067 | 34.4067 | 5.00e-14 | 0.03 | PASS |
| `B1d` | Green-theorem polygon vs rectangle integration (area) | 1.2784 | 1.2784 | 9.38e-15 | 1.00e-09 | PASS |
| `B1e` | Green-theorem polygon vs rectangle integration (inertia) | 34.4067 | 34.4067 | 9.89e-14 | 1.00e-09 | PASS |
| `B2-10` | strip model vs exact, n_strips=10 | 713.5392 | 714.3303 | 0.0011 | 0.03 | PASS |
| `B2-40` | strip model vs exact, n_strips=40 | 714.2809 | 714.3303 | 6.92e-05 | 0.03 | PASS |
| `B2-100` | strip model vs exact, n_strips=100 | 714.3224 | 714.3303 | 1.11e-05 | 0.03 | PASS |
| `B3-box-B20` | Smith ultimate moment vs rigid-plastic M_p (box-B20) | 2190306690.0000 | 2190306690.0000 | 2.18e-16 | 0.03 | PASS |
| `B3-box-B30` | Smith ultimate moment vs rigid-plastic M_p (box-B30) | 6574014079.9999 | 6574014079.9999 | 1.45e-16 | 0.03 | PASS |
| `B3-double-hull` | Smith ultimate moment vs rigid-plastic M_p (double-hull) | 15166274527.0363 | 15166875255.3931 | 3.96e-05 | 0.03 | PASS |
| `B3-double-hull-S235-panel065` | Smith ultimate moment vs rigid-plastic M_p (double-hull-S235-panel065) | 7744480609.5504 | 7744787364.4560 | 3.96e-05 | 0.03 | PASS |
| `B4a` | upper bound M_u <= M_p = sigma_y Z_p over 8 slenderness levels | 14.825 GNm (max) | 15.167 GNm (min M_p) | 0 | 判据式 | PASS |
| `B4b` | M_u monotone decreasing in plate slenderness (b/t) | M_u/M_p 0.977 -> 0.080 | monotone sequence | 0 | 判据式 | PASS |
| `B4c-tdeck20-tbot30-b1200-sagging` | Smith vs hand-derived two-flange collapse moment (sagging) | 248192490.0930 | 248246459.9053 | 2.17e-04 | 0.03 | PASS |
| `B4c-tdeck20-tbot30-b1200-hogging` | Smith vs hand-derived two-flange collapse moment (hogging) | 836204585.7963 | 837831802.1804 | 0.0019 | 0.03 | PASS |
| `B4c-tdeck25-tbot40-b1500-sagging` | Smith vs hand-derived two-flange collapse moment (sagging) | 310531522.1704 | 310308074.8816 | 7.20e-04 | 0.03 | PASS |
| `B4c-tdeck25-tbot40-b1500-hogging` | Smith vs hand-derived two-flange collapse moment (hogging) | 1267683285.4420 | 1271021874.7151 | 0.0026 | 0.03 | PASS |
| `B5a` | C(L) continuity at L = 300 m | 10.7500 | 10.7500 | 0 | 0.03 | PASS |
| `B5b` | C(L) continuity at L = 350 m | 10.7500 | 10.7500 | 0 | 0.03 | PASS |
| `B5c` | flat plateau 10.75 on 300 < L <= 350 | 10.750000..10.750000 | 10.7500 | 0 | 0.03 | PASS |
| `B5d` | C(L) rises on 90..350 m and falls on 350..500 m | up=True, down=True | up and down | 0 | 判据式 | PASS |
| `B5e` | hogging/sagging moment ratio | 1.7273 | 1.7273 | 2.57e-16 | 1.00e-09 | PASS |
| `B6a` | wave-only girder stress vs S11 allowable 175/k MPa (necessary condition) | 106.9315 | 175.0000 | 0 | 判据式 | PASS |
| `B6b` | S11 hogging moment vs hand-carried formula | 9823534796.8000 | 9823534796.8000 | 0 | 1.00e-09 | PASS |

## ④ 结果图

见内嵌 5 图：极限曲率下的应力剖面（图 1）、弯矩–曲率曲线与 M_y/M_p 界（图 2）、
极限强度随板格柔度衰减（图 3）、S11 载荷面（图 4）、中剖面模型（图 5）。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

- **判据自己先被推翻两次**（honest-FAIL 记录在案）：
  1. 曾断言 "M_y ≤ M_u ≤ M_p"。该断言**物理不成立**：板格在屈服前屈曲时，全剖面一阶屈服弯矩
     M_y 可以高于塌陷弯矩（本剖面随板格加宽 M_u/M_y 1.129 -> 0.092，实测下探 −91%）。
     现改为只保留严格上界 M_u ≤ M_p，屈曲区改用两手板闭式基准。
  2. 两手板闭式首版写成 "σ_u A_d = σ_y A_b 定轴 + 力臂 (D − z_NA)"，隐含了
     σ_u A_comp ≥ σ_y A_ten，与本算例相反，误差 98–290%。正解为力偶式
     **M_u = min(σ_u A_comp, σ_y A_ten) · D**。
  3. 中性轴二分把 "F 随 z_NA 递减" 写死，只在 κ>0 成立；中垂侧守卫误判并静默回退到中点，
     产出**不平衡**的"极限弯矩"。现改为方向无关夹逼 + `na_failures` 计数（本次全部 0）。
- **适用范围**：结构理想化（无纵骨/肘板/开孔）→ 剖面模数与 M_u 是实船**上界**；
  无残余应力、无应变硬化、无剪力滞、无疲劳/屈曲交互；Smith 用简化加载-缩短曲线而非 CSR 规范曲线。
- **平台能力证据**：`:8001` 五个候选路径（calculations/structure、/strength、/hull_girder、
  structure/strength、calculations/section_modulus）实测
  `codes={'calculations/structure': 404, 'calculations/strength': 404, 'calculations/hull_girder': 404, 'structure/strength': 404, 'calculations/section_modulus': 404}` → 结构极限层 **NOT_SUPPORTED**（本模块是 worktree 内的独立验证实现，不是平台端点）。
- 极限层与并行席弹性层**互补而不重复**：报告 1185 给弹性应力判决，本单给屈服后承载力。

## ⑥ 误差判定（逐项，显式回答"是否 <3%"）

- 数值型判据 20 项（其中精确算法互校 4 项，阈值 1e-9；数值-闭式 16 项，阈值 3%）：**20/20 PASS**。
- 最大数值误差出现在 `B4c-tdeck25-tbot40-b1500-hogging`（Smith vs hand-derived two-flange collapse moment (hogging)）：**0.0026** → <3%。
- 布尔型判据 5 项（守恒/界/单调性/连续性/规范式）：**5/5 PASS**。
- **是否 <3%？是**——所有数值判据均 <3%，且极大部分 <1e-3（结构积分与刚塑性恒等式）。但须显式声明：本 `<3%` 是**对闭式/恒等式基准**成立的，不是对模型试验塌陷弯矩成立；后者本机无数据源，证据等级 **WITHHELD**（见注意点）。

## ⑦ 复现

```
python3 -m tools.verify.hull_girder.benchmarks --write   # 25/25 PASS + 5 图 + JSON
python3 -m pytest tests/test_hull_girder.py -q           # 19 passed
python3 -m tools.verify.hull_girder.publish             # 生成本文档与报告载荷
```
