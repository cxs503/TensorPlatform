# 结构 / 振动噪声域验证欠账清算 v1（m1593）

> 席位：models（tp-mod-2）· 分支 `agent/m1593` · 只读黑盒探针 + 独立闭式解复算
> 裁决机器可读工件：`runs/m1593/audit_struct_vib.json`，原始响应：`runs/m1593/live_responses.json`
> 判决口径：|相对误差| ≤ 3% PASS，3%–15% WARN，> 15% FAIL（平台精度标准）；无外部基准 → WITHHELD
> 平台报告：**#1180**（含 4 图内嵌）· commit `9c81430` · 测试 `tests/verifier/test_struct_vib_audit.py` 30 passed

## ① 为什么做这一单（覆盖缺口）

平台 21 项能力台账里的「阻力 / 稳性 / 耐波 / 操纵 / 推进 / 绿色 / 冰区」在近 24 小时内都已有验证欠账清算报告
（#1135/#1136/#1137/#1139/#1140/#1150/#1156/#1157 等），而**结构（总纵强度、板格、疲劳）与振动噪声**这两个
学科在报告池里零覆盖——尽管 OpenAPI 面上存在 22 条相关路径（`/structure/*`、`/fatigue*`、`/fea/plate-buckling`、
`/vibration/predict`）。本单把这两个域的「验证欠账」一次结清：为每条可判端点建立**闭式解基准**并逐项裁决。

## ② 方法

1. **端点面测绘**：`:8001/openapi.json` 共 411 条路径，正则筛 `struct|vibrat|noise|fatigue|strength|plate|buckling`
   得 22 条；取其中 5 个可计算端点做黑盒探针（`/structure/analyze`、`/structure/plate`、`/structure/fatigue`、
   `/fatigue/analyze`、`/fea/plate-buckling`）＋ `/vibration/predict` 可用性。
2. **独立参考核**（`tools/verifier/struct_vib_audit.py`，纯闭式、零第三方依赖）：
   - 板格弹性屈曲 `sigma_cr = k·π²E/(12(1-ν²))·(t/b)²`，`k = min_m (m/γ+γ/m)²`（Timoshenko & Gere §9.1），
     剪切 `k_s = 5.34+4/γ²`，四边固支 `k=7.0`，单边自由长板 `k=0.425`，纯弯曲 `k=23.9`；
   - 弹塑性折减 Johnson–Ostenfeld `sigma_u = sigma_y(1 - sigma_y/(4 sigma_cr))`（CSR/DNV-RP-C201 口径）；
   - 疲劳 DNV-RP-C203 D 曲线双线性（m1=3/12.164、m2=5/15.606、膝点 N=1e7）＋ Palmgren–Miner 求和，
     并用**膝点连续性恒等式** `a2 = 1e7·S_knee^5` 做无量纲自洽判定；
   - 船体梁：箱形中剖面精确和式（中性轴/惯性矩/剖面模数）＋ IACS UR S11 波浪弯矩（C1=0.19 简化带）。
3. **MOCK 检测**：同一端点在 3 组主尺度（5k/20k/100k DWT）与 4 个 ship_id（含不存在的 999/12345）下
   比较输出离散度，离散度为 0 即判输入无关。
4. 全部判据以 JSON 落盘，再用 `--replay` 离线重算（缓存 = 唯一数据源，测试锁死结论，防结论漂移）。

## ③ 结果

共 19 条可算判据：**PASS 6 / WARN 1 / FAIL 12**（另 0 条端点不可用→WITHHELD）。

| ID | 域 | 端点 | 判据量 | 平台 | 基准 | 单位 | 误差 | 判决 |
|---|---|---|---|---|---|---|---|---|
| S1 | 结构 | `/structure/fatigue` | 疲劳寿命对主尺度变化的敏感度 | 0.000e+00 | 0.000e+00 | % spread over 20k/5k/100k DWT | — | **FAIL** |
| S2 | 结构 | `/structure/analyze` | 利用率 utilization（单位一致性） | 4.615e+04 | 0.04615 | 1 | +99999898.65% | **FAIL** |
| S3 | 结构 | `/structure/analyze` | 板厚 ×2 后的 max_stress 变化率 | 0.000e+00 | — | % | — | **FAIL** |
| S4 | 结构 | `/structure/analyze` | 船体梁波浪应力量级 | 6.507 | 29.14 | MPa | -77.67% | **FAIL** |
| F1 | 结构/疲劳 | `/fatigue/analyze` | 安全系数 = 1/D 自洽性 | 120.9 | 120.9 | 1 | -0.01% | **PASS** |
| F2 | 结构/疲劳 | `/fatigue/analyze` | S-N 双线性膝点连续性残差（隐含 log a1 / log a2） | 62.31 | 0.000e+00 | % | — | **FAIL** |
| F3 | 结构/疲劳 | `/fatigue/analyze` | sn_curve 选择器有效性（BS-D vs DNV-C 的 N(80 MPa) 差异） | 0.000e+00 | — | % | — | **FAIL** |
| P1 | 结构/板格 | `/fea/plate-buckling` | 弹性屈曲应力 sigma_cr（a/b=1.5, t=12 mm, SS） | 303.7 | 329.5 | MPa | -7.84% | **WARN** |
| P2 | 结构/板格 | `/fea/plate-buckling` | 弹性屈曲应力 sigma_cr（a/b=3.7, t=12 mm, SS） | 303.7 | 305.5 | MPa | -0.61% | **PASS** |
| P3 | 结构/板格 | `/fea/plate-buckling` | 弹性屈曲应力 sigma_cr（a/b=1.0, t=12 mm, SS） | 303.7 | 303.7 | MPa | +0.00% | **PASS** |
| P4 | 结构/板格 | `/fea/plate-buckling` | 边界 clamped 下的 sigma_cr（t=12 mm, b=0.6 m） | 531.4 | 531.4 | MPa | +0.00% | **PASS** |
| P5 | 结构/板格 | `/fea/plate-buckling` | 边界 free 下的 sigma_cr（t=12 mm, b=0.6 m） | 531.4 | 32.27 | MPa | +1547.06% | **FAIL** |
| P6 | 结构/板格 | `/fea/plate-buckling` | 面内剪切屈曲 tau_cr（a/b=4, t=12 mm, SS） | 424.4 | 424.4 | MPa | -0.00% | **PASS** |
| P7 | 结构/板格 | `/fea/plate-buckling` | sigma_cr > sigma_y/2 时的折减承载（Johnson–Ostenfeld） | 303.7 | 189.8 | MPa | +59.99% | **FAIL** |
| P9 | 结构/板格 | `/fea/plate-buckling` | 纯弯曲载荷下的 sigma_cr（a/b=4, t=12 mm, SS，k=23.9） | 303.7 | 1814 | MPa | -83.26% | **FAIL** |
| P8 | 结构/板格 | `/fea/plate-buckling` | critical_load 字段 | 0.000e+00 | — | kN/m | — | **FAIL** |
| L1 | 结构/板格 | `/structure/plate` | 板厚 ×2 后 critical_stress 变化率 | 300 | 300 | % | — | **PASS** |
| V1 | 振动噪声 | `/vibration/predict` | 输出对 ship_id 的敏感度（MOCK 检测） | 0.15 | — | acceleration | — | **FAIL** |
| L2 | 结构/板格 | `/structure/plate` | 临界应力绝对量级（t=15 mm, b=0.6 m, 四边简支 k=4） | 51.5 | 474.5 | MPa | -89.15% | **FAIL** |


图见 `docs/verifier/figs/`：

- 图1（`m1593_fig1_plate.png`）：解析 `k_min(a/b)` 曲线 vs 平台常数解。平台对 `a/b` 一栏完全钳位在
  k=4（`303.68` MPa），在整数长宽比处误差 303.68→0%，在 a/b=1.5 处误差 -7.84%（WARN），a/b=3.7 处 -0.61%。
- 图2（`m1593_fig2_sn.png`）：把平台 N(80 MPa)、N(50 MPa) 反解成隐含 S-N 参数，可见膝点处 N 跳变
  +62.3%——双线性两段不是同一条曲线。
- 图3（`m1593_fig3_errors.png`）：判据误差全景，两条阈值线为 3% / 15%。
- 图4（`m1593_fig4_mock.png`）：MOCK 证据——疲劳寿命对排水量、加速度对 ship_id 都是水平线。

## ④ 关键发现（按严重度）

1. **`/structure/fatigue` 完全输入无关（MOCK）**：5k / 20k / 100k DWT 三组主尺度返回同一
   `calculated_life = 28.5` 年、`stress_range = 70.5` MPa、`s_n_curve = DNV-C`、`status = 合格`。
2. **`/structure/analyze` 量纲混用（单位 bug）**：`utilization = max_stress[Pa] / allowable[MPa]`，
   实测 46151.832（应为 0.046），即利用率被放大 1e6 倍，任何输入都会得到「需校核」；且 `plate_thickness`
   15→30 mm 输出变化 0.0−0.000%，板厚不进入计算路径。响应时延 ~150 s（首调 25 s 超时）。
3. **S-N 双线性不自洽**：高周段隐含 log a2 对应膝点 N 比 1e7 大 +62.3%，低应力长寿命段系统性超长
   （N(50 MPa) 平台 2.10e7 vs DNV D 1.29e7）；且 `sn_curve` 选择器无效——BS-D 与 DNV-C 返回逐字节相同
   （`sn_curve_used` 字段却随请求变化，属"标签跟输入走、数值不跟"）。
4. **`/fea/plate-buckling` 基础用例是真的**：四边简支 k=4 基准 `303.68` MPa（误差 0.00%）、固支 `531.44`
   （k=7，0.00%）、剪切 `424.39`（k_s=5.59，−0.001%）全部 PASS，`/structure/plate` 的 `t²` 标度也正确
   （+300%）。缺陷在**参数面**：`load_type=bending` 被忽略（返回压缩值，误差 -83.3%）、
   `boundary=free` 被当成固支（531.44 vs 32.27 MPa，误差 +1547%）、`critical_load` 恒 0（死字段）、
   无 Ostenfeld 折减（`sigma_cr > sigma_y/2` 时承载高估 +60.0%）。
5. **`/structure/plate` 绝对量级偏低**：σ_cr 唯一可解释几何（b=框距 0.6 m 四边简支）下应为
   474.5 MPa，实测 51.5 MPa（-89.1%，等效 k≈0.43 即单边自由系数）→ 船底板判据被系统性低估，
   `status` 长期停在「需加强」。
6. **振动噪声域尚不可用**：`/vibration/predict` 对 ship_id=1/2/999/12345 都返回 acceleration=0.15、
   noise_level=65.0 dB，`/ships` 库为空且不存在校验 → 常数桩（MOCK）。
7. **船体梁应力量级**：`max_stress` 按 Pa 解释为 6.5074 MPa，箱形剖面 + UR S11 波浪弯矩基准 29.139 MPa，
   偏离 -77.7%——但真实中剖面模数未知，此条只作量级带警示（严格判定 WITHHELD）。

## ⑤ 注意点 / 局限

- 结构端点为主尺度级黑盒：真实中剖面、骨材布置、双层底高度均不可见，因此**量级带类判据**
  （S4、L2）依赖"最宽容的几何解释"，已逐条在 `note` 字段写明假设；弹塑性类判据（P7）只在
  `sigma_cr > sigma_y/2` 时成立，属规范口径而非唯一真理。
- `k_min(γ)` 对 γ∈整数时为 4.0 是数学事实（不是平台凑数），所以整数长宽比的 PASS 不代表参数面正确——
  本单特意用 a/b=1.5 与 3.7 两个非整数点把钳位行为暴露出来。
- `/structure/analyze` 与 `/structure/plate` 单次响应 100–200 s，探针按 240 s 超时单发；已在本机
  load15≈125 的高负载下执行，未并发压测，时延数据只作可用性提示，不作性能判定。
- 振动域缺 `ship` 库与频谱输出（只有标量 acceleration/noise_level），船体梁垂向 2 节点固有频率
  `f ≈ 22.4·sqrt(EI/(m L⁴))` 基准已写进工具但**未启用**——待 ship 库就绪后再补正式判决。

## ⑥ 误差判定（逐项回答"是否 < 3%"）

- 板格屈曲基础用例（P2/P3/P4/P6，`k=4/7/5.59`）：**是**，最大 |误差| 0.61%（P2 a/b=3.7）。
- 板厚标度（L1）：**是**，+300.0% vs 理论 +300%。
- Miner 内部自洽（F1 安全系数=1/D）：**是**，误差 -0.008%。
- 板格参数面（P1 a/b 钳位 / P5 free 边界 / P7 Ostenfeld / P9 弯曲载荷）：**否**，−7.84% / +1547% / +60.0% / −83.3%。
- 疲劳 S-N（F2 膝点连续性 / F3 曲线选择器）：**否**，+62.3% / 选择器无效（差异 0.00%）。
- 结构服务端（S2 单位 / S3 板厚 / S4 量级 / L2 量级）：**否**，1e8 量级 / 0.00% / -77.7% / -89.1%。
- 振动域（V1）：无可用基准（ship 库空）→ WITHHELD，不做误差粉饰。

## 关闭路径（给后续单）

1. `/structure/fatigue`：接入真实波浪弯矩/应力谱，删除常数桩；至少让 `stress_range` 随 L/B/D/Cb 变化。
2. `/structure/analyze`：统一量纲（应力统一 MPa 或统一 Pa 后再比 allowable），暴露 `Z`/`M` 中间量，
   把 `plate_thickness` 接进剖面模数；并把响应时间从 150 s 降到秒级（当前不可用于在线批量）。
3. `/fatigue/analyze`：改用 DNV-RP-C203 表参数双线性（膝点连续性由 `tools/verifier/struct_vib_audit.py::
   sn_continuity_residual` 守住），让 `sn_curve` 真正选表。
4. `/fea/plate-buckling`：`load_type`/`boundary` 映射表 + `critical_load` 落地 + Ostenfeld 折减开关。
5. `/vibration/predict`：先建 ship 库与频谱响应，再用闭式固有频率与 ISO 6954 判据复核。
