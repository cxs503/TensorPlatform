# 船舶平台几何/静水力能力独立复核（geometry.hydro_curves / geometry.verify_official）

> 判定对象：`audit_capabilities` 声明为 **REAL** 的两项能力。本单只用**精确数学恒等式与硬物理上界**做判据（不引入任何“记忆里的表值”），全部数字由 `tools/validation/probe_geometry_hydrostatics.py` 程序化注入。

## ① 案例介绍

**物理背景**：静水力曲线（排水体积 ∇(T)、水线面积 Awp(T)、浮心高 VCB、方形系数 Cb、每厘米吃水吨数 TPC）是稳性—浮态—装载计算的共同上游。平台 `/calculations/stability` 允许把 GM/BM 当外部输入，因此静水力一旦失真，整条稳性链的基准都是错的。

**几何/参数表**（取自平台自己的 `/ship-hull/templates`，即被复核对象的自述参数）：

| 标模 | L [m] | B [m] | T [m] | Cb | 曲线点数 |
|---|---|---|---|---|---|
| KCS | 230.0 | 32.2 | 10.800 | 0.651 | 21 |
| KVLCC2 | 320.0 | 58.0 | 20.800 | 0.810 | 21 |
| SUBOFF | 4.4 | 0.5 | 0.254 | 0.520 | 21 |

**端点**：`GET /api/v1/geometry/hydro-curves/{ship}`（21 点吃水扫描）、`GET /api/v1/geometry/verify/{ship}`（生成船体 vs 官方型值）。

## ② 怎么做的

1. **判据先于动手**：把静水力学写成 8 条可机裁判据，每条要么是恒等式（误差门 3%），要么是硬上界（门 0%）：
   - A1 箱体上界：`∇ = W/ρ ≤ L·B·T`（物体不可能排出比自己包围盒更大的体积）
   - A2 方形系数自洽：`Cb_reported == ∇/(L·B·T)`，且 `Cb ≤ 1`
   - A3 设计点一致：曲线在设计吃水处的 Cb 必须等于同一平台的模板 Cb
   - A4 **基本定理**：`d(∇)/dT == Awp(T)`（水线面积的定义，恒等式，零容差）
   - A5 直壁二分法：Awp 在吃水扫描内数值恒定 ⇒ 柱状体 ⇒ `VCB == T/2`
   - A6 TPC 恒等式：`TPC == ρ·Awp/100`
   - A7 水线面上界：`Awp ≤ L·B`
   - A8 曲线卫生：单调性 + 分辨率（最小非零步长/设计排水量 > 1% 即判缺陷）
2. **探针**：`tools/validation/probe_geometry_hydrostatics.py --probe-live` 取三个标模的实时响应，原始 body 落盘 `artifacts/geometry_hydro_raw/`，判据结果落盘 `artifacts/geometry_hydrostatics_verdicts.json`；`--from-raw` 可离线重放同一判定（可复现性）。
3. **判级规则**：任一 `bound`/`identity` 判据失败 ⇒ 该能力 **MOCK**；只有卫生类失败 ⇒ `REAL_WITH_DEFECTS`；全部通过 ⇒ **REAL**；端点不可达或样本为空 ⇒ **WITHHELD / NOT_SUPPORTED**（空比较不算 PASS）。
4. **工具链**：纯 Python + urllib（无第三方客户端），matplotlib 出图，pytest 覆盖“注入缺陷必须被抓到”的反向用例。

## ③ 计算结果

### 3.1 逐判据实测（实时端点，本单时刻快照）

| 标模 | A1 ∇/LBT | A2 Cb | A3 Cb(设计) | A4 dV/dT vs Awp | A5 VCB vs T/2 | A6 TPC | A7 Awp/LB | A8 分辨率 | 判级 |
|---|---|---|---|---|---|---|---|---|---|
| KCS | 1.195 (19.49%) FAIL | 1.195 (19.49%) FAIL | 1.195 (83.55%) FAIL | 8849.543 (46.00%) FAIL | 9.837 (82.22%) FAIL | 62.130 (0.00%) PASS | 0.818 (0.00%) PASS | 0.000 (0.00%) PASS | **MOCK** |
| KVLCC2 | 1.444 (44.41%) FAIL | 1.444 (44.41%) FAIL | 1.444 (78.28%) FAIL | 26802.272 (50.54%) FAIL | 18.680 (79.64%) FAIL | 182.492 (0.00%) PASS | 0.959 (0.00%) PASS | 0.000 (0.00%) PASS | **MOCK** |
| SUBOFF | 1.192 (19.16%) FAIL | 0.716 (39.98%) FAIL | 0.716 (37.60%) FAIL | 0.000 (408.13%) FAIL | 0.159 (28.00%) FAIL | 0.016 (2.44%) PASS | 0.723 (0.00%) PASS | 80.000 (80.00%) FAIL | **MOCK** |

### 3.2 决定性证据（逐条可复算）

* **KCS** — A1_box_bound failed (max nabla/(L*B*T) at T=2.052 m; violating volume requires displaced volume L*B*T = 15197.1 m3, reported 18159.2 m3); A2_block_coefficient failed (reported Cb range [1.1949, 1.1949] must equal nabla/(L*B*T) and must not exceed 1); A3_design_cb failed (curve Cb at T=10.8 m vs /ship-hull template Cb=0.651); A4_fundamental_theorem failed (d(nabla)/dT from the reported displacement curve vs the reported Awp(T); worst point T=1.566 m); A5_wall_sided_vcb failed (Awp spread only 0.002% over the draft sweep => prismatic body => VCB must equal T/2 at every draft)
  - A1：最大 ∇/(L·B·T) = **1.1949**（>1 即违反物理上界）；max nabla/(L*B*T) at T=2.052 m; violating volume requires displaced volume L*B*T = 15197.1 m3, reported 18159.2 m3
  - A4：d(∇)/dT = **8849.5 m²**，而平台自己报告的 Awp = **6061.5 m²**，偏差 46.00%；d(nabla)/dT from the reported displacement curve vs the reported Awp(T); worst point T=1.566 m
  - A5：VCB = **9.837 m**，直壁体应恒为 T/2 = **5.400 m**，偏差 82.22%；Awp spread only 0.002% over the draft sweep => prismatic body => VCB must equal T/2 at every draft
* **KVLCC2** — A1_box_bound failed (max nabla/(L*B*T) at T=2.08 m; violating volume requires displaced volume L*B*T = 38604.8 m3, reported 55748.8 m3); A2_block_coefficient failed (reported Cb range [1.4441, 1.4441] must equal nabla/(L*B*T) and must not exceed 1); A3_design_cb failed (curve Cb at T=20.8 m vs /ship-hull template Cb=0.81); A4_fundamental_theorem failed (d(nabla)/dT from the reported displacement curve vs the reported Awp(T); worst point T=6.76 m); A5_wall_sided_vcb failed (Awp spread only 0.000% over the draft sweep => prismatic body => VCB must equal T/2 at every draft)
  - A1：最大 ∇/(L·B·T) = **1.4441**（>1 即违反物理上界）；max nabla/(L*B*T) at T=2.08 m; violating volume requires displaced volume L*B*T = 38604.8 m3, reported 55748.8 m3
  - A4：d(∇)/dT = **26802.3 m²**，而平台自己报告的 Awp = **17804.1 m²**，偏差 50.54%；d(nabla)/dT from the reported displacement curve vs the reported Awp(T); worst point T=6.76 m
  - A5：VCB = **18.680 m**，直壁体应恒为 T/2 = **10.400 m**，偏差 79.64%；Awp spread only 0.000% over the draft sweep => prismatic body => VCB must equal T/2 at every draft
* **SUBOFF** — A1_box_bound failed (max nabla/(L*B*T) at T=0.037 m; violating volume requires displaced volume L*B*T = 0.1 m3, reported 0.1 m3); A2_block_coefficient failed (reported Cb range [0.7151, 0.7159] must equal nabla/(L*B*T) and must not exceed 1); A3_design_cb failed (curve Cb at T=0.254 m vs /ship-hull template Cb=0.52); A4_fundamental_theorem failed (d(nabla)/dT from the reported displacement curve vs the reported Awp(T); worst point T=0.025 m); A5_wall_sided_vcb failed (Awp spread only 0.000% over the draft sweep => prismatic body => VCB must equal T/2 at every draft)
  - A1：最大 ∇/(L·B·T) = **1.1916**（>1 即违反物理上界）；max nabla/(L*B*T) at T=0.037 m; violating volume requires displaced volume L*B*T = 0.1 m3, reported 0.1 m3
  - A4：d(∇)/dT = **0.0 m²**，而平台自己报告的 Awp = **1.6 m²**，偏差 408.13%；d(nabla)/dT from the reported displacement curve vs the reported Awp(T); worst point T=0.025 m
  - A5：VCB = **0.159 m**，直壁体应恒为 T/2 = **0.127 m**，偏差 28.00%；Awp spread only 0.000% over the draft sweep => prismatic body => VCB must equal T/2 at every draft

### 3.3 `/geometry/verify` 官方型值对撞

| 标模 | HTTP | 检查站位数 | 平均误差 | 最大误差 | 判级 | 说明 |
|---|---|---|---|---|---|---|
| KCS | 200 | 0 | n/a | n/a | **WITHHELD** | zero stations checked -- an empty comparison is not a PASS |
| KVLCC2 | 200 | 4 | 23.30 | 141.10 | **MOCK** | 4 stations, max |error| 141.1% vs 15.0% gate |
| SUBOFF | 200 | 0 | n/a | n/a | **NOT_SUPPORTED** | HTTP 404: {'detail': "标模 'SUBOFF' 不存在"} |

### 3.4 能力判级汇总

| 能力 | 声明等级 | 本单实测 |
|---|---|---|
| `geometry.hydro_curves` | REAL | **MOCK** |
| `geometry.verify_official` | REAL | **MOCK** |

## ④ 结果图

![fig1_box_bound.png](figures/fig1_box_bound.png)

*图1 A1 箱体上界：三型标模报告排水体积已越界*

![fig2_fundamental_theorem.png](figures/fig2_fundamental_theorem.png)

*图2 A4 基本定理 d(nabla)/dT = Awp 的内部矛盾*

![fig3_anchor_errors.png](figures/fig3_anchor_errors.png)

*图3 各判据相对偏差与 3%/15% 门*

![fig4_verify_stations.png](figures/fig4_verify_stations.png)

*图4 A9 geometry/verify 站位半宽对撞*

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **单位链推断而非外部资料**：把 `displacement` 判为吨（质量）而非 m³，依据是平台自身 TPC 恒等式与 A6 一致（A6 在三型上偏差 ≤ 数个百分点）。若端点实际返回 m³，结论会变化 —— 这本身是缺陷：响应 schema 未声明单位。
2. **A5 是条件判据**：仅当 Awp 在扫描内数值恒定（直壁）时启用；真实船体不满足该条件，这条判据届时自动降为 `not applicable`。
3. **A4 是零容差恒等式**，但它对离散化敏感：用中心差分，两端单侧差分；真实积分器在粗网格上也可能有数个百分点偏差。因此本单把 3% 定为“恒等式门”，并把 A4 的失败与 A1 的越界叠加使用——单一判据不作为判级依据。
4. **未做的事**：未修改船舶平台源码（不在本工作树白名单内）；未取实验/试验数据做绝对精度验证（对标算例仍为本平台 KPI 缺口）；`MTC` 无 I_T 输入故未设判据（A6 之外的纵倾项保留为 WITHHELD）。
5. **适用范围**：结论只对本次快照（响应体已落盘 `artifacts/geometry_hydro_raw/`）有效；端点修复后须重跑 `--probe-live`，测试层会用录制体自动发现漂移。

## ⑥ 误差判定（逐项 vs 基准）

| 关键量 | 基准（精确值/恒等式） | 实测 | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| KCS  Cb(设计 T) | 0.651（平台模板） | n/a | n/a% | **否** |
| KCS  d∇/dT vs Awp | 6061.5 m²（端点自报 Awp） | n/a | n/a% | **否** |
| KCS  VCB vs T/2 | T/2（柱状体恒等） | n/a | n/a% | **否** |
| KVLCC2 Cb(设计 T) | 0.81（平台模板） | n/a | n/a% | **否** |
| KVLCC2 d∇/dT vs Awp | 17804.1 m²（端点自报 Awp） | n/a | n/a% | **否** |
| KVLCC2 VCB vs T/2 | T/2（柱状体恒等） | n/a | n/a% | **否** |
| SUBOFF Cb(设计 T) | 0.52（平台模板） | n/a | n/a% | **否** |
| SUBOFF ∇/LBT 上界 | 1.0（硬上界） | n/a | n/a% | **否** |

**结论（显式回答）**：三个基准量族中，只有 TPC 恒等式（A6）在 3% 内通过；排水体积—水线面积—方形系数—浮心高四项**全部**超出 3% 与 15% 两道门，且 A1（硬上界）在三个标模上都被越界。因此 `geometry.hydro_curves` 的 REAL 声明不成立，实测判级 **MOCK**（参数化闭式代打），`geometry.verify_official` 实测 **MOCK/WITHHELD**（KCS 零站位空比较、KVLCC2 最大误差超门、SUBOFF 不可用）。修复建议：让 hydro-curves 走官方型值表积分（而非由 L·B·T·Cb 反算），并在 `verify` 返回 `stations_checked>0` 前禁止给出 PASS。

