# 冰区航行域验证欠账清偿 v1 —— `/api/v1/extended-calculations/ice` 证据分级

**判决：MOCK**（11 项判据，1 PASS / 10 FAIL）
端点：`http://127.0.0.1:8001/api/v1/extended-calculations/ice`｜工件：`docs/verif/ice_navigation/{evidence.json,verdict.json,4 png}`｜回归：`tests/verif/test_ice_navigation_verdict.py`（17 passed）

## 1 案例与物理背景

冰区航行学科的核心可验证量是**冰阻力 R_ice 与对应功率**，业界基准是 Lindqvist (1989) 三成分分解：
压碎 `R_c`、下潜/弯曲 `R_b(α, ψ, μ)`、滑行 `R_s(ψ, μ)`，破冰与滑行项各带速度放大
`(1+1.4v/√(gh))`、`(1+9.4v/√(gL))`；冰压碎强度 σ_c 对一级冰取 0.5–5 MPa。
局部结构侧由 IACS 极地冰带规则约束（冰级越高板厚越大）。

基准算例（schema 默认口径，集装箱/多用途极地船量级）：

| 参数 | 值 |
|---|---|
| L | 150 m |
| B | 23 m |
| T | 8 m |
| Δ | 15000 t |
| Cb | 0.75 |
| V | 5 kn |
| 冰级 | PC7 |
| h | 1.0 m |

## 2 怎么做的

1. **只读探针**：`docs/verif/ice_navigation/probe_ice_navigation.py` 对实盘端点打 10 组探针
   （航速/冰厚/船宽/Cb/尺度/冰级扫描、零冰厚极限、极厚冰外推、输入契约），共 24 次调用；
2. **独立参照**：以仓库内自称同一文献方法的独立实现 `backend/methods/ice/lindqvist.py`（636 行，本次只读引用）作 oracle，
   同输入对拍——只用于证明「端点与自述方法不自洽」，不作为文献值；
3. **判据**：11 条机器判据分三类——物理不变量/标度律、量级反解、方法自洽；全部可从 `evidence.json` 复算；
4. **零手抄**：本文件由脚本从 evidence/verdict 生成。

## 3 计算结果

### 3.1 判据总表

| id | 判据 | 判决 | 参照/期望 | 实测 | 容差 |
|---|---|---|---|---|---|
| C1 | 零冰厚极限 R_ice(h=0)=0 | PASS | 0 kN（Lindqvist 各项 ∝ h^n, n>0） | 0.0 | == 0 |
| C2 | 航速依赖：R_ice(15kn)/R_ice(1kn) >= 1.5 | FAIL | Lindqvist 速度项 (1+1.4v/sqrt(gh))(1+9.4v/sqrt(gL)) 保证 >=1.5 | 1.0 | >= 1.5 kN/kn |
| C3 | 冰强度量级：等效 sigma_eff 落在 0.5-5 MPa | FAIL | 0.5-5 MPa（一级冰压碎强度） | 0.001 | 0.5-5 MPa |
| C4 | 三成分完整性：输出/输入含滑行(submergence+sliding)与船首角/摩擦 | FAIL | Lindqvist = R_c + R_b(submerged, alpha, mu) + R_s(sliding, psi, mu) | 仅 R_crushing + R_bending 两项；schema 无 alpha/psi/mu sigma_c | 三成分齐备 |
| C5 | Cb 参与计算（schema 暴露 cb） | FAIL | R 应对船型参数敏感 | 1 个不同输出值 | >=2 |
| C6 | 冰级影响结构板厚（PC1 vs PC7） | FAIL | IACS 极地冰带：PC1 板厚显著大于 PC7 | shell_plating = [25.0] mm | >=2 档 |
| C7 | 结构量物理有界（frame_spacing > 0） | FAIL | 400-50h 在 h>8m 转负 | -50.0 | > 0 mm |
| C8 | 破冰能力与船相关（max_ice_thickness 随尺度变） | FAIL | PC7 能力应由功率/尺度决定 | [1.0] | >=2 档 |
| C9 | 连续破冰航速与冰厚/功率解耦程度 | FAIL | 应为 speed x 固定系数（缺陷确认） | [2.0, 3.0, 4.0, 5.0] | 1 档（=缺陷） |
| C10 | 自洽：端点 vs 仓库内 Lindqvist 实现（同输入） | FAIL | oracle R(h=1m) = 597.1 kN | endpoint R = 15174.0 kN (比值 25.414) | 0.5-2.0 x |
| C11 | 功率一致性：ice_power = R*V 未除推进效率且与可用功率无比较 | FAIL | P_D = R*V/eta_D, eta_D in [0.4,0.8] | P = R*V = 39028.0 kW（R=15174.0 kN, V=5.0kn） | 含效率且<=可用功率 |

### 3.2 冰厚扫描（端点 vs 仓库内 Lindqvist）

| h [m] | 端点 R_ice [kN] | oracle R_ice [kN] | 端点/oracle | 板厚 [mm] | 骨架间距 [mm] |
|---|---|---|---|---|---|
| 0.25 | 1178.0 | 70.4 | 16.74 | 17.5 | 388.0 |
| 0.5 | 4174.0 | 193.3 | 21.59 | 20.0 | 375.0 |
| 1.0 | 15174.0 | 597.1 | 25.41 | 25.0 | 350.0 |
| 1.5 | 32625.0 | 1211.3 | 26.93 | 30.0 | 325.0 |
| 2.0 | 56392.0 | 2036.0 | 27.7 | 35.0 | 300.0 |
| 3.0 | 122592.0 | 4316.6 | 28.4 | 45.0 | 250.0 |

端点 h 标度指数（逐段）：[1.825, 1.844, 1.854, 1.86, 1.869] —— 与 `h^2`(压碎)/`h^1.5`(弯曲) 混合结构一致，说明**项结构本身**是给定的，
但整体量级与速度结构否定了「Lindqvist 公式」的标签：基准点端点 15174 kN vs oracle 597.1 kN，
**比值 25.41×**。

### 3.3 冰级扫描（局部结构）

| 冰级 | 板厚 [mm] | 骨架间距 [mm] | 最大可破冰厚 [m] | 连续破冰航速 [kn] |
|---|---|---|---|---|
| PC1 | 25.0 | 350.0 | 4.0 | 5.0 |
| PC3 | 25.0 | 350.0 | 2.5 | 4.0 |
| PC5 | 25.0 | 350.0 | 1.5 | 3.0 |
| PC7 | 25.0 | 350.0 | 1.0 | 2.0 |

板厚对 PC1…PC7 **完全不变**，最大可破冰厚为查表常数（与 1200–200000 t 船舶尺度无关），
连续破冰航速 = 输入航速 × 冰级固定系数。

### 3.4 图件

- `fig_R_vs_thickness.png`：R_ice(h) 双对数，端点 vs oracle（量级分离 25.4×）
- `fig_R_vs_speed.png`：航速依赖缺失（端点恒平 vs Lindqvist 速度项）
- `fig_structure_requirements.png`：板厚与冰级无关 + `400-50h` 在 h>8 m 转负
- `fig_deviation.png`：基准点三判据偏差（25.41×、σ_eff=0.001 MPa、速度比 1.0）

## 4 缺陷登记（10 项，全部可由工件复算）

| id | 字段 | 缺陷 | 证据 |
|---|---|---|---|
| D1 | ice_resistance | 无速度项：R_ice = 0.5*1000*h^2*B + 0.3*1000*h^1.5*sqrt(L)，V 仅用于 ice_power | R(15kn)/R(1kn) = 1.0 |
| D2 | ice_resistance | 等效冰压碎强度硬编码 1000 Pa，物理带 0.5-5 MPa，无 sigma_c 输入通道 | sigma_eff = 0.001 MPa |
| D3 | R_bending | 项结构非 Lindqvist：弯曲项写成 0.3*1000*h^1.5*sqrt(L)，缺 alpha/psi/mu 与 submergence/sliding 两成分 | schema 无 alpha/psi/mu；源码注释称三成分实为两项 |
| D4 | cb | 死参数：cb 进入 schema 但计算未引用 | 1 个不同输出值（4 个输入） |
| D5 | continuous_speed | 连续破冰航速 = 输入航速 x 冰级固定系数，与冰厚/功率/阻力解耦 | PC1..PC7 输出 [2.0, 3.0, 4.0, 5.0] |
| D6 | shell_plating | 板厚 15+10h 与冰级无关，PC1 与 PC7 同厚 | [25.0] mm |
| D7 | frame_spacing | 400-50h 无下界，h=9m 转负 | -50.0 mm @ h=9m |
| D8 | max_ice_thickness | 冰级查表常数，与船舶尺度/功率无关 | [1.0] m 覆盖 1200-200000 t |
| D9 | method | 标签伪标 'Lindqvist公式'：仓库内 methods/ice/lindqvist.py 独立实现未被调用 | endpoint/oracle = 25.4137 |
| D10 | ice_power | P = R*V 未除推进效率、未与可用功率/推力平衡比较 | 39028.0 kW |

## 5 注意点与局限

- **oracle 不是文献值**：离线环境无 Lindqvist 原文数据表，量级对照用的是仓库内独立实现；
  若该实现本身亦有偏，则 25.41× 仅证明「二者不自洽」而非「端点错 25.41×」。
  但 C1–C9 判据（速度依赖缺失、σ_eff=1 kPa、cb 死参数、冰级结构不变、间距转负等）**不依赖任何外部数值**，判决 MOCK 成立。
- **仅覆盖冰区航行域的两个端点**：`/extended-calculations/ice` 已判；`/ice-navigation/route`（需 query `ship_id`）未纳入本单。
- **未做修复**：修复需触及船平台仓（工作树外），按拆分派单处理；本单给出字段级缺陷清单可直接转工单。
- 载荷纪律：本机 load15 已 ≥100，全程只做解析级探针与文档，未起任何重算力作业。

## 6 误差判定（关键量 vs 基准）

| 关键量 | 平台值 | 基准 | 偏差 | 是否 <3% |
|---|---|---|---|---|
| R_ice @ h=1m, 5kn | 15174 kN | 597.1 kN（仓库内 Lindqvist 实现） | 25.41× | 否 |
| 等效冰压碎强度 σ_eff | 0.001 MPa | 0.5–5 MPa（一级冰物理带） | ×500–5000 偏低 | 否 |
| R(15kn)/R(1kn) | 1.0 | ≥1.5（Lindqvist 速度结构） | 1.0 | 否 |
| 板厚 PC1 vs PC7 @h=1m | 同值 | PC1 显著大于 PC7（IACS 极地冰带） | Δ=0 | 否 |

**结论：四项关键量全部不满足 <3% 判据，端点整体判 MOCK，`method` 字段的「Lindqvist公式」标签属伪标。**
