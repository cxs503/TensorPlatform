# 振动 / 噪声域物理内核验证（m1681）

> 域：振动噪声（船体梁垂向振动 · 叶频激励 · 板格局部振动 · 舱室噪声）
> 内核：`backend/services/digital_twin/models/vibration.py`（新增，补齐该域零实现）
> 验收套件：`backend/tests/test_vibration_noise_kernel.py`（35 项 pytest，全绿）
> 复算脚本：`tools/verify/vibration_noise_kernel_check.py` → 本文件表格由其程序化生成

## 1. 背景：为什么这是验证欠账

报告 #1186（m1600）与本单扫描结论一致：振动/噪声是唯一没有物理内核的性能域。
服务端点 `/api/v1/extended-calculations/vibration` 使用
`f_hull = 1.5 (L/100)^-0.5`（与排水量无关）与手调占位式
`vertical_vib = 2.0 + 0.5 (rpm/100) * (1 或 0.3)`（无单位），噪声链零实现。
#1186 的 19 条判据给出 5 PASS / 14 FAIL，两域判 MOCK，并留下明确待办：
(a) 以 EI/Δ 分布替换占位固有频率并给出带单位的谱量；(b) 噪声域功能补全。

本单做的正是 (a)+(b) 的内核侧，且把每条结论钉在闭式解/文献值上。

## 2. 方法与工具链

| 环节 | 数学模型 | 基准（独立锚点） |
|---|---|---|
| 船体梁垂向振动 | 自由-自由 Euler-Bernoulli 梁，精确特征值 cos(βL)cosh(βL)=1 | 超越方程根 4.7300407 / 7.8532046 / 10.9956078… |
| 剖面/质量 | 薄壁箱型剖面 I（含纵骨系数 1.5）+ μ=Δ(1+k_added)/L | 手算闭式与 `I/(D/2)` 一致性 |
| 叶频激励 | f_BF = n·rpm/60，谐波 k·f_BF，轴承力 k_F·T/k | 运动学恒等式（精确） |
| 甲板速度响应 | 自由-自由模态叠加（∫φ²dx=L，模态质量 μL） | 共振处 |v|=F φ_f φ_o/(2ζM ω_n)（精确） |
| 局部板格 | 简支=Navier 精确；固支=Rayleigh-Ritz（clamped-clamped 梁基） | Navier 闭式；Leissa 固支方板 35.985 |
| 舱室噪声 | 单层质量定律 TL=10log10(1+(m''ω/2ρ0c)²) −5dB，Sabine T60，Lp=Lw−TL+10log10(4/R) | +6 dB/倍频程、0.1611V/A、自由场 r⁻² |

参考船：L=180.0 m, B=32.0 m, T=11.5 m, Cb=0.8，
Δ=54317 t（几何先用 `Cb·L·B·T·ρ` 闭式校核，误差 <1e-12）。
工况：T=800000 N, rpm=110,
叶数 4, ζ=0.01。

## 3. 计算结果（关键量，全部由脚本写出）

| 量 | 数值 | 单位 |
|---|---|---|
| 船体梁 2 节点垂向固有频率 f₁ | 0.8188 | Hz |
| 3 节点 f₂ | 2.2572 | Hz |
| 叶频 f_BF | 7.3333 | Hz |
| 甲板响应（1–80 Hz 带内 RMS，船体梁模态贡献） | 0.14714 | mm/s RMS |
| 叶频与 f₁ 相对裕度 | 7.956（低风险） | — |
| 参考舱室 SPL（m''=40, ᾱ=0.15, 100 Hz） | 63.12 | dB |
| 同上隔声量 TL | 24.63 | dB |
| 旧占位式 f_hull（对照） | 1.1180 | Hz |

判定总账：**30 条判据，26 PASS / 4 FAIL**；
其中闭式/文献锚点 21 条**全部 PASS**，最坏相对误差 6.055e-04（V20）；
evidence（对旧占位式的反证）3 条、observability 3 条、capability 3 条。

| 判据 | 类型 | 域 | 内容 | 本次计算值 | 基准/参考 | 相对误差 | 判定 |
|---|---|---|---|---|---|---|---|
| V1 | anchor | hull girder | beta_1L solves cos(x)cosh(x)=1 | 4.730040744863 | exact root | 2.706e-16 | PASS |
| V2 | anchor | hull girder | beta_2L solves cos(x)cosh(x)=1 | 7.853204624096 | exact root | 4.144e-16 | PASS |
| V3 | anchor | hull girder | beta_3L solves cos(x)cosh(x)=1 | 10.995607838002 | exact root | 3.903e-16 | PASS |
| V4 | anchor | hull girder | beta_4L solves cos(x)cosh(x)=1 | 14.137165491257 | exact root | 2.898e-16 | PASS |
| V5 | anchor | hull girder | beta_5L solves cos(x)cosh(x)=1 | 17.278759657399 | exact root | 1.717e-15 | PASS |
| V9n | anchor | hull girder | Newton eigenvalue n=9 vs (2n+1)pi/2 | 29.845130209 | 29.845130209 | 7.380e-15 | PASS |
| V10 | anchor | hull girder | mode shapes normalised, |int phi^2 dx / L - 1| (n=1..3) | 3.665e-08 | 0 (i.e. int phi^2 dx = L) | 3.665e-08 | PASS |
| V11 | anchor | hull girder | 2-node vertical frequency inside closed-form beam band | 0.8188 Hz | [0.6154, 1.0769] Hz | — | PASS |
| V12 | anchor | hull girder | 3-node/2-node frequency ratio | 2.756539 | 2.756539 (exact, (beta_2/beta_1)^2) | 1.611e-16 | PASS |
| V13 | anchor | hull girder | geometric-similarity exponent d ln f / d ln lambda | -1.000000 | -1.000000 (Euler-Bernoulli + Schlick both give -1) | — | PASS |
| V13x | evidence | hull girder | legacy placeholder exponent (defect evidence) | -0.5000 | -1.0 | 5.000e-01 | FAIL |
| V14 | anchor | hull girder | displacement doubled -> frequency change (%) | -29.2893 % | -29.2893 % | 3.791e-16 | PASS |
| V14x | evidence | hull girder | legacy placeholder change on displacement doubling (%) | 0.0000 % | -29.2893 % | 1.000e+00 | FAIL |
| V15 | anchor | response | modal velocity at resonance vs analytic F phi_f phi_o/(2 zeta M omega_n) | 1.008423e-07 m/s | 1.008423e-07 m/s | — | PASS |
| V16 | anchor | response | modal convergence 10 vs 20 modes (band RMS) | 8.981e-05 | < 2e-2 | 8.981e-05 | PASS |
| V16x | evidence | response | 3-mode truncation error (why convergence must be checked) | 325.2 x | 1.0 | 3.242e+02 | FAIL |
| V17 | anchor | response | RMS from harmonic sum vs time-domain record (Parseval) | 0.147144 mm/s | 0.147204 mm/s | 4.086e-04 | PASS |
| V18 | anchor | excitation | blade-rate identity f_BF = n_blades rpm/60 | 7.333333 Hz | 7.333333 Hz | — | PASS |
| V19 | anchor | plate | simply-supported plate vs Navier exact (4 modes/aspect ratios) | 0.000e+00 | 0 | — | PASS |
| V20 | anchor | plate | clamped square plate eigenvalue omega a^2 sqrt(rho h/D) | 36.0068 | 35.985 (Leissa, Vibration of Plates) | 6.055e-04 | PASS |
| V21 | anchor | plate | Ritz reduction on the simply-supported limit (= 2 pi^2) | 19.739208802 | 19.739208802 | — | PASS |
| V22 | anchor | noise | mass law slope per mass doubling (m''=400, f=1000 Hz) | 6.0206 dB | 6.0206 dB | 2.361e-07 | PASS |
| V23 | anchor | noise | Sabine constant 24 ln10 / c (V=A=1) | 0.161114 | 0.1611 | 8.582e-05 | PASS |
| V24 | anchor | noise | diffuse-field chain Lp = Lw - TL + 10 log10(4/R) | 63.1244 dB | 63.1244 dB | — | PASS |
| V25 | capability | noise | reference deck-house cabin level vs IMO MSC.337(91) 60 dB(A) | 63.12 dB | 60 dB(A) | 5.207e-02 | FAIL |
| V26 | observability | served API | vertical_vibration carries a unit | mm/s RMS (1-80 Hz) | mm/s RMS (1-80 Hz) | — | PASS |
| V27 | observability | served API | two different hulls give different f_hull (legacy: constant) | 0.819 vs 0.626 Hz | must differ (legacy: identical) | — | PASS |
| V28 | observability | served API | deck panel frequency is an independent quantity (a,b doubled -> 4x) | 4.0000 | 4.0000 | — | PASS |
| V29 | capability | served API | resonance screen: blade rate vs 2-node mode | 低风险 (closest harmonic k=1, relative margin 7.956) | risk flag consistent with the reported frequencies | — | PASS |
| V30 | capability | served API | comfort grade from a band-RMS velocity (units explicit) | 良好 / 舒适 | ISO 6954-style band | — | PASS |

合计：30 条判据，26 PASS / 4 FAIL；其中闭式/文献锚点 21 条全部 PASS，最坏相对误差 6.055e-04（V20）；evidence（已记录缺陷反证）3 条、observability（端点可观测性）3 条、capability（工程判断）3 条。

## 4. 误差判定（逐项，回答「是否 <3%」）

* 船体梁特征值/频率恒等式、模态归一化、叶频恒等式、Navier 板频、共振幅值、
  质量定律斜率、Sabine 常数、漫射场链：相对误差 1e-16 ~ 1e-7 量级 → **<3% ✅**
* 固支方板 Ritz 特征值 vs Leissa 35.985：0.061% → **<3% ✅**（这也是锚点组最坏项）
* 2 节点频率绝对量级：0.8188 Hz 落在 #1186 判据 V4 的闭式带 [0.6154, 1.0769] Hz 内 → **在带内 ✅**
  （该带本身是量级判据，非精确实测值；绝对精度仍受船体梁理想化限制，见 §5）
* 舱室 SPL：与 IMO MSC.337(91) 起居舱室 60 dB(A) 限值比较，参考配置 63.12 dB → **超限 5.2%（不达标，如实记录）**
  ——这是被预测对象不达标，不是内核误差；内核该链路的代数一致性误差为 0（V24 精确）。

## 5. 注意点（坑与局限）

1. **模态截断必须做收敛判定**：叶频 7.33 Hz 高于 3 节点频率，取 3 阶模态给出的 RMS 与收敛值差 325 倍
   （V16x）。内核默认 10 阶并用 20 阶复算，`modal_converged` 字段显式返回；任何下游用 3 阶近似都会错到量级。
2. **响应量是船体梁（整体）贡献，属下界**：局部板格/结构声路径未建模，`level_basis` 字段已写明。
   因此 `comfort_grade` 只宜作筛选，不宜作舒适性合规的终裁；合规判定需 SEA/舱段 FEM（仍 WITHHELD）。
3. **ISO 6954 计权曲线未逐字实现**：1–80 Hz 采用平窗，低频段偏保守/高频段偏差方向已知；
   阈值作为平台可配置门值（`ComfortThresholds`），未冒充标准逐字转录。
4. **固支板边界是理想化**：实船板格介于简支/固支之间，固支偏刚（同板厚频率高约 1.82 倍），应按连接刚度取值。
5. **未覆盖**：三维舱段 FEM、桨-轴-船体耦合（轴系纵/扭振）、水下辐射噪声（空泡谱）、
   冰致振动 —— 这些维持 WITHHELD，不因本单得分而升级。
6. **服务面接线**：本内核在 TensorPlatform 工作树内（`backend/services/digital_twin/models/`），
   `vibration_summary()` 已按旧端点字段（f_blade/f_hull/vertical_vibration/level/resonance_risk）
   做好 drop-in 形状并附单位；但 `:8001` 的 `extended_calculations.calculate_vibration` 位于
   另一仓库（部署面），本次未改，属待接线项（DEPLOY_DRIFT 风险已知）。

## 6. 图

* `artifacts/vibration_noise/fig1_girder_frequency.png`：f₁(L) 内核 vs 旧占位式 vs 闭式带
* `artifacts/vibration_noise/fig2_resonance_sweep.png`：叶频谱 + rpm 扫描共振穿越
* `artifacts/vibration_noise/fig3_plate_anchors.png`：固支 Ritz 收敛到 Leissa；板格频率族
* `artifacts/vibration_noise/fig4_noise_chain.png`：质量定律与舱室噪声链 / IMO 限值
