# m1720 — 平冰阻力（Lindqvist 1989）验证欠账独立审计与速度因子修复

**任务**：tp-paper-4 自主工时单（本轮 load15 ≈ 108 ≥ 100 → 只做轻量「验证欠账」活：纯公式求值 + 测试 + 分析，未起 CFD/DEM 重算）
**仓库**：TensorPlatform ｜ **分支**：agent/m1720
**产出**：`artifacts/m1720/audit_ice_resistance.{py,json}`、`artifacts/m1720/fig*.png`、
`tests/test_m1720_ice_resistance_validation_audit.py`（14 例）、本文档、
`backend/services/ice_resistance_uq.py`（速度因子修复 + 诚实验证状态面）

---

## 1. 案例介绍（物理背景与对象）

船舶在连续层冰中以速度 V 航行时的**平冰阻力** R_ice 是冰区航行战役（p8/p9 波次）的核心物理量，
也直接喂给 `api/domain/ice_uq.py`、`services/digital_twin/models/resistance.py` 与抗冰结构工况。

平台内**同时存在两套同名 "Lindqvist (1989) level ice resistance" 实现**：

| 实现 | 位置 | 特点 |
|---|---|---|
| A | `services/ice_resistance_uq.py::_lindqvist_raw` | 每船一个标量 `calibration_factor`，标定到 3 艘实船试验均值；下游 PCE/Sobol UQ 链全依赖它 |
| B | `services/digital_twin/models/resistance.py::lindqvist_ice_resistance` | 无每船标定，含 `SUBMERSION_COEFF = 0.55` 单点工程修正；数字孪生代理模型依赖它 |

参考数据（硬编码在 `SHIP_TRIAL_DATA`，来源标注 Riska et al. 1997 / Kühnlein 1997 / Spencer & Jones 2001）：

| 船 | L [m] | B [m] | T [m] | Cb | 首柱角 [°] | 名义冰厚 h [m] | V [m/s] | 试验均值 R [kN] |
|---|---|---|---|---|---|---|---|---|
| MV Arctic | 222.5 | 32.2 | 13.5 | 0.75 | 30 | 1.0 | 3.0 | 4800 |
| Polarstern | 118.0 | 25.0 | 10.5 | 0.68 | 35 | 0.8 | 2.5 | 1800 |
| Terry Fox | 75.0 | 18.0 | 7.5 | 0.60 | 28 | 0.6 | 2.0 | 850 |

## 2. 怎么做的（方法与工具链）

单干、可机器裁决的四项判据（`artifacts/m1720/audit_ice_resistance.py`，纯解析求值，无重算）：

- **J1 标定真空性**：`calibration_factor = R_ref / R_raw` 是在**该船自己那个试验点上**拟合的，
  故 in-sample 误差恒为浮点零。做**篡改实验**（把参考值 ×0.2…×5 后重标定）检验该判据的判别力。
- **J2 留一交叉验证（LOO）**：3 艘船 × 每船 1 个名义点，最强的可用检验是用**其余 2 船**拟合的单一
  全局常数预测被留出的船（对数空间几何平均），报 out-of-sample 相对误差，对照平台 15% 验收带。
- **J3 同名两实现分歧**：同船同冰况下 A/B 的 R_ice 比值。
- **J4 物理标度**：用 σ_c=0、V=0 解析提取浸没项后，检验结构项 ∝h²、σ_c 线性、速度因子存在性。
- 顺带核对语义：模块注释把浸没项标为 "Lindqvist Eq. 12"，实际实现是 `F_sub_per_l × 0.3 L`（引入船长标度，
  与 Lindqvist 的 `ρ g h B T (B+T)/(B+2T)` 不同构）——已就地改正注释而非静默改名。

工具链：Python 3.11 + NumPy + Matplotlib(Agg)；pytest。全部数字由脚本写出 JSON，文档零手抄。

## 3. 计算结果

### 3.1 J1：现有"标定即验证"链是恒真式

| 船 | R_raw [kN]（修复后） | 试验均值 [kN] | calibration_factor | in-sample 相对误差 |
|---|---|---|---|---|
| MV Arctic | 6374.9 | 4800 | 0.7530 | 0.0 |
| Polarstern | 2686.2 | 1800 | 0.6701 | 0.0 |
| Terry Fox | 943.2 | 850 | 0.9012 | 0.0 |

篡改实验（参考值任意缩放后重标定）：

| 参考值缩放 k | 0.2 | 0.5 | 1.0 | 2.0 | 5.0 |
|---|---|---|---|---|---|
| 模型-参考最大相对误差 | 1.2e-16 | 0 | 0 | 0 | 0 |

→ **`tests/test_pce_ice_resistance_uq.py::test_calibration_matches_trial_mean` 对模型正确性零判别力**：
把参考值改成任意荒谬值它都全绿。该测试此前被当作"对实船试验验证通过"的证据使用。

### 3.2 J2：out-of-sample 误差超验收带

| 被留出船 | 全局常数 k（其余 2 船拟合） | 预测 [kN] | 试验均值 [kN] | 相对误差 |
|---|---|---|---|---|
| MV Arctic | 0.7771 | 4953.4 | 4800 | **+3.20%** |
| Polarstern | 0.8238 | 2212.9 | 1800 | **+22.94%** |
| Terry Fox | 0.7103 | 670.0 | 850 | **−21.18%** |

最大 out-of-sample 误差 **22.94% > 15%**（`verdict_le_15pct = false`）。
参考：用全部 3 船拟合单个常数（乐观口径、非验证）最大误差 14.76%——**这正是"看起来过了 15%"的来源**。

### 3.3 J3：同名两实现给出不同答案

| 船 | A 标定后 [kN] | B 数字孪生 [kN] | B/A | B/试验均值 |
|---|---|---|---|---|
| MV Arctic | 4800.0 | 2195.1 | 0.457 | 0.457 |
| Polarstern | 1800.0 | 1202.0 | 0.668 | 0.668 |
| Terry Fox | 850.0 | 532.7 | 0.627 | 0.627 |

同船同冰况下两套"Lindqvist"相差 **1.5–2.2 倍**；B 系统性低估试验均值 33–54%。
平台对"冰阻力"这一物理量内部没有一致答案。

### 3.4 J4：标度检验 → 抓到真实缺陷（速度因子缺失）

| 船 | 浸没项占比 | σ_c 加倍后结构项比（应 =2） | 结构项 h 指数 | 总阻力 h 指数 | 修复前 0→5 m/s 阻力变化 | 修复后 |
|---|---|---|---|---|---|---|
| MV Arctic | 0.658 | 2.0000 | 1.765 | 1.267 | +0.95% | **+77.4%** |
| Polarstern | 0.477 | 2.0000 | 1.843 | 1.448 | +1.39% | **+118.2%** |
| Terry Fox | 0.404 | 2.0000 | 1.865 | 1.522 | +1.83% | **+135.0%** |

修复前阻力对航速几乎不敏感（0→5 m/s 只涨 ~1%），与任何冰区实船试验（速度效应显著）矛盾。
根因：**Lindqvist (1989) 的速度乘子 `(1 + 1.4 V/√(gh))` 在 A 实现里整条丢失**，只剩一个经验二次拖曳项
（MV Arctic 在 3 m/s 时该经验项仅 14.9 kN，占总阻力 0.34%）；而 B 实现正好相反——有速度乘子、没有拖曳项、
还有 0.55 的浸没修正。两套实现各是半个模型。

**最小修复**（`services/ice_resistance_uq.py::_lindqvist_raw`）：
`R = (R_c + R_b)(1 + 1.4 V/√(gh)) + R_sub + R_drag`，速度乘子按 Lindqvist 原文作用在结构项上。
修复后标定因子由 1.097/1.068/1.410 变为 0.753/0.670/0.901，LOO 误差 MV Arctic **+11.82% → +3.20%**。

### 3.5 图

- `fig1_resistance_vs_thickness.png` — R_ice(h) 三分支（A 未标定 / A 标定 / B 数字孪生）+ 试验均值水平线
- `fig2_calibration_vs_loo_error.png` — in-sample（恒 0，symlog）vs LOO vs 单常数误差，15% 验收带
- `fig3_implementation_divergence.png` — A/B 相对试验均值的比值柱状 + ±15% 带 + 倍数标注
- `fig4_scaling.png` — h 标度与 h² 参照线；速度依赖性 A（修复后线性乘子）vs B

## 4. 注意点（坑、局限、适用范围）

1. **标定点 = 验证点 ⇒ 验证恒真**。这是本平台多处以"标定后与参考值吻合"充当验证的通病形态；
   判别的机器特征是 in-sample 残差 ~1e-16，正常验证不会出现这种数字。
2. **参考值来源离线不可核**：`SHIP_TRIAL_DATA` 的 3 个试验均值是硬编码，原始论文/数字化数据集不在工作树内，
   本审计**无法独立确认**这些数字本身，属 WITHHELD 的一部分。所有结论只依赖平台内部一致性 + 标度自洽。
3. **3 船 × 1 点**意味着任何形如"两参数、3 点拟合"的模型都能拟合得很好，不可作为验证；
   LOO 是此处唯一有信息量的检验，且样本量小，22.9% 与 14.8% 的差别本身也不稳定。
4. 浸没项 `0.3 L` 的船长标度偏离 Lindqvist 原文闭式；本单只改注释与状态说明，**未改该项数值**，
   因为 0.55/0.3 这类工程系数没有独立数据可支撑替换，改动会使既有 UQ 结论不可比。
5. 本次未做：B 实现的 0.55 修正未动（数字孪生侧调参牵动 PCE 代理与既有报告，需另立单）；
   结构项弯曲部分 `l_c ∝ h^0.75` 使 h 指数 1.77–1.87 而非常见的 2，属公式本身性质，非缺陷。
6. 本轮机器 load15 ≈ 108，刻意避开 CFD/DEM 复算，因此无场量云图；图为标定/误差/标度诊断图。

## 5. 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 误差 | <3%？ |
|---|---|---|---|
| 平冰阻力 R_ice（MV Arctic，LOO out-of-sample） | Riska et al. 1997 试验均值 4800 kN | **+3.20%** | ✗（勉强超线） |
| 平冰阻力 R_ice（Polarstern，LOO） | Kühnlein 1997 试验均值 1800 kN | **+22.94%** | ✗ |
| 平冰阻力 R_ice（Terry Fox，LOO） | Spencer & Jones 2001 试验均值 850 kN | **−21.18%** | ✗ |
| in-sample "验证"误差 | 同一试验点 | 1.9e-16（恒真式，无判别力） | 不可计入 |
| 数字孪生侧 B vs 试验均值 | 同上 | −54.3% / −33.2% / −37.3% | ✗ |
| σ_c 线性标度 | 解析（结构项 ∝σ_c） | 2.0000 vs 2（<1e-12） | ✓ |
| h 指数（结构项） | 解析 h²（含 l_c∝h^0.75 修正） | 1.765–1.865 vs 2（−6.8%…−11.8%） | ✗（公式性质，已在 §4.5 说明） |
| Gauss-Hermite / PCE 等 UQ 数值链 | 大样本 MC 参照 | 既有测试全绿（45 passed） | ✓（本次未改） |

**结论（honest）**：平冰阻力物理模型在 out-of-sample 口径下**未通过 15% 验收带**，
证据等级裁定为 **WITHHELD**（不是 REAL）。本次唯一的实质性改进是补回缺失的
Lindqvist 速度乘子——它把证据最充分的 MV Arctic 算例从 +11.8% 拉到 +3.2%，
并把"阻力对航速几乎不敏感"这一硬物理矛盾消除；但仍不足以支撑绝对量级宣称。
平台的 UQ 数值链（PCE/Smolyak/Sobol/CI 覆盖）本身有效，缺陷只在被它包装的物理模型上。

## 6. 复现

```bash
cd /srv/tensor/agent/work/m1720
python3 artifacts/m1720/audit_ice_resistance.py        # 生成 JSON + 4 图
cd backend && python3 -m pytest ../tests/test_m1720_ice_resistance_validation_audit.py -q   # 14 passed
python3 -m pytest ../tests/test_pce_ice_resistance_uq.py -q                                 # 45 passed（回归）
```

机器可读状态面：`services/ice_resistance_uq.py::VALIDATION_STATUS` 与
`level_ice_validation_report()`（重算 in-sample / LOO / 两实现分歧，供 API 诊断端点直接调用）。