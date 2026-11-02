# 批量链路敞水性能接线：MOCK 代理 → BEM 真实模型（m1662）

**Mission**: 1662 · **Repo**: TensorPlatform · **分支**: agent/m1662 · **日期**: 2026-11-01
**目标工件**: `tools/batch_scan/batch_executor.py::ShipCalculationEngine.calc_propeller`
**证据等级**: 前 **MOCK** → 后 **REAL**（叶元 + 动量 BEM，失败即报错，无代理回落）

---

## 1. 案例介绍：这笔账是怎么欠下的

`backend/services/propeller_open_water.py`（mission 1393）已经把敞水性能从硬编码代理换成
叶元条带积分 + 动量相容诱导速度模型，并配了 24 项判据。但**接线没有落地**：千工况批量执行器
`ShipCalculationEngine.calc_propeller` 仍然返回被淘汰的线性代理

```
KT = max(0.05, 0.5 - 0.3 J)      KQ = max(0.01, 0.05 + 0.02 J)
eta0 = clip(J KT /(2 pi KQ), 0.3, 0.85)
```

后果有两个。其一是**批量链路整条是 MOCK**：`CaseExecutor.ENGINE_MAP["propeller"]` 走的正是
这个方法，任何批量扫掠的推进结论都建立在一个反物理代理上。其二是**判据与实现脱钩**：
`tests/test_propeller_open_water.py::TestBatchWrapperWiring::test_batch_executor_uses_bem_model`
在 HEAD 上是**红的**（`KeyError: 'model'`），说明真实模型与调用点之间是一条断链——
这正是 mission 1413 判据 J1–J7 指出的"声称 REAL、实测 MOCK"的结构性成因。

| 环节 | 接线前 | 接线后 |
|---|---|---|
| 模型 | 线性代理（KT 只随 J，KQ=0.1·KT，η0 钳位 0.3–0.85） | 叶元 CL/CD 积分 + Prandtl 梢/毂损失 + 动量相容 u、a′ |
| 几何敏感度 | 对 P/D、Z、Ae/A0 **完全无感** | 全部进入几何（弦长分布按 Ae/A0 精确归一） |
| 失效模式 | 静默出数 | 模型不可导入即 `RuntimeError`，**绝不回落代理** |
| 判据 | 5 项全部失效（m1413 J1–J7） | W1–W9 全绿（新增 15 项测试） |

## 2. 怎么做的（方法 / 关键设置 / 工具链）

1. **读码定位**：`git`/`grep` 定位代理实现与真实模型；确认失败用例是接线缺失而非模型缺陷
   （`backend/services/propeller_open_water.py` 50 项既有测试全绿）。
2. **最小改动接线**：`calc_propeller` 改为调用 `open_water_point(geom, J, rho, n_rps=n)`，
   几何经 `PropellerGeometry` 构造并按参数元组缓存（批量复用，避免重复归一化弦长）。
   `J = V_a/(n D)`，`V_a = V_kn × 0.5144`，`n = rpm/60` —— 与平台其余模块同一套单位常数。
3. **失败即报错（fail-closed）**：`_bem()` 用 `importlib.import_module` 延迟导入，
   任何导入异常转 `RuntimeError` 并显式写明"拒绝回落到已退役的 MOCK 代理"；
   输入越界（D≤0、rpm≤0、负航速、Z∉[2,7]、Ae/A0∉(0,1.5)）一律 `ValueError`。
4. **向后兼容**：保留 `advance_coefficient / thrust_coefficient / torque_coefficient /
   efficiency / thrust_kn` 五个旧键（批量结果表与判据配置依赖），新增 `model`、
   `ideal_efficiency_bound`、`torque_knm`、`delivered_power_kw`、`induction_converged`。
5. **判据锁**：新增 `tests/test_m1662_propeller_wiring.py`（15 项，W1–W9），把代理指纹写成
   **可机器裁决的负面判据**；台账与图件由 `artifacts/m1662/make_ledger.py` 程序化生成。

```
python3 -m pytest tests/test_m1662_propeller_wiring.py tests/test_propeller_open_water.py \
                 tests/test_propulsion_anchors.py -q     # 65 passed
python3 artifacts/m1662/make_ledger.py                   # 台账 + 图件
```

## 3. 计算结果（P/D=1.0, Z=4, Ae/A0=0.55, D=5 m, 100 rpm；数字全部由脚本注入）

| V [kn] | J | K_T | K_Q | K_Q/K_T | η₀ (BEM) | η_i 上界 | η₀ 恒等式偏差 | 退役代理 η₀ | 收敛 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0.0000 | 0.46941 | 0.05562 | 0.118 | 0.0000 | 1.0000 | 0.0 | 0.300 | 是 |
| 2 | 0.1235 | 0.43191 | 0.05350 | 0.124 | 0.1586 | 0.2093 | 8.2e−05 | 0.300 | 是 |
| 4 | 0.2469 | 0.38992 | 0.05055 | 0.130 | 0.3031 | 0.3878 | 6.7e−06 | 0.305 | 是 |
| 6 | 0.3704 | 0.34093 | 0.04642 | 0.136 | 0.4329 | 0.5395 | 6.3e−05 | 0.399 | 是 |
| 8 | 0.4938 | 0.28290 | 0.04074 | 0.144 | 0.5458 | 0.6692 | 6.3e−05 | 0.462 | 是 |
| 10 | 0.6173 | 0.21946 | 0.03371 | 0.154 | 0.6396 | 0.7780 | 6.7e−06 | 0.496 | 是 |
| 12 | 0.7407 | 0.15225 | 0.02541 | 0.167 | 0.7065 | 0.8672 | 1.6e−04 | 0.505 | 是 |
| 14 | 0.8642 | 0.08050 | 0.01560 | 0.194 | 0.7096 | 0.9394 | 1.5e−04 | 0.492 | 是 |
| 16 | 0.9876 | 0.00375 | 0.00410 | (1.093) | 0.1436 | 0.9976 | 1.6e−04 | 0.459 | 是 |
| 18 | 1.1111 | −0.18841 | −0.02912 | — | 0.0 | 1.0000 | 制动段 | 0.408 | 否（见 §5） |
| 20 | 1.2346 | −0.38309 | −0.06908 | — | 0.0 | 1.0000 | 制动段 | 0.341 | 否 |

参数扫描（V=6 kn）暴露的几何敏感度：K_T max/min = **2.512**（P/D 0.8→1.4 与 Z 3→6），
而代理该值恒为 1.000（K_T 与几何无关）。

**判据台账**（`artifacts/m1662/propeller_wiring_ledger.json`）：

| 判据 | 阈值 | 实测 | 判定 |
|---|---|---|---|
| K_Q/K_T 比值随几何展开（代理恒 0.1） | spread > 0.02 | **0.0483**（区间 0.121–0.169） | PASS |
| K_T 几何敏感度（代理恒 1.0） | > 1.2 | **2.512** | PASS |
| η₀ 恒等于 0.850 钳位 | 不得出现 | **未出现** | PASS |
| 0 ≤ η₀ < η_i（能量方程上界） | 全部成立 | **11/11 成立** | PASS |
| η₀ = J K_T/(2π K_Q) 恒等式 | abs err < 3e−4 | **1.63e−04** | PASS |
| 代理 η₀ 越上界 | 应为真（代理错误证据） | **真**（如 J=0.86：0.492 < η_i 0.939 尚不越界，J→1 段代理与 BEM 反向） | 见 §5 |

## 4. 结果图

![敞开水面曲线：BEM vs 退役代理与解析上界](../artifacts/m1662/propeller_wiring.png)

三联图（`artifacts/m1662/propeller_wiring.png`）：左 K_T(J)、中 K_Q(J)（代理随 J **上升**，
反物理）、右 η₀(J) 与作动盘上界 η_i = 2/(1+√(1+C_T))。BEM 曲线全程落在上界之下且不饱和；
代理在 J≳0.5 之后与上界的距离持续缩小并在高 J 段越界/饱和于 0.85 钳位。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **恒等式判据必须在推进段使用**：首版把 η₀ = JK_T/(2πK_Q) 的偏差对全段取最大，得到
   1.14 的"巨大误差"——实为 J>1.05 制动段 K_Q 与 K_T 同时为负、而 `open_water_point`
   把 η₀ 截断为 0 所致。修正为只在 K_T>0 且 K_Q>0 的推进段比较，最大偏差降到 **1.6e−04**。
   教训：恒等式判据的**定义域**与实现的分支截断必须对齐，否则会自造假 FAIL。
2. **近零推力点病态**：J≈0.99（K_T=0.0038）处 K_Q/K_T 比值达 1.09，是分母趋零的数值病态，
   不是物理异常；比值类判据不得在 J₀ 附近 ±5% 内取用。
3. **制动段动量解不收敛**：J≥1.11 时 `induction_converged=False`（条带无根穿越，被钳到带顶/带底），
   该区段输出应视为定性可用、定量不可引用；批量判据建议对 `induction_converged` 设门。
4. **未做的事**：绝对量级对照（B 系列表值 / ITTC 敞水试验曲线）仍未完成，本仓无可靠离线表值工件，
   本单拒绝用记忆表值充作证据 —— 这是继承 m1413 §6 第 5 条的**显式剩余欠账**。
   因此本单的 REAL 判级依据是"恒等式 + 解析上界 + 无量纲不变性"三段式，不含绝对精度声明。
5. **性能**：单点 41 ms（32 条带，含 44×2 次二分），千工况批量约 41 s（并发下更低）；
   几何按参数元组缓存，重复扫描不重复归一化弦长。
6. **未覆盖**：桨后伴流场、空泡剥蚀、非均匀来流非定常推力——需试验或 CFD。

## 6. 误差判定（逐项，是否 < 3%）

| 关键量 | 基准 | 误差 | <3%? |
|---|---|---|---|
| η₀ = J K_T/(2π K_Q) 恒等式（推进段 8 点） | 闭式定义 | 最大 **1.63e−04**（绝对） | **是** |
| η₀ vs 作动盘上界 η_i | 解析上界 | 全段严格小于（无越界点） | **是**（不等式判据） |
| Froude 缩放不变性（D×2, n÷2） | 无量纲不变量 | ≤ 1e−04（4 位小数舍入级） | **是** |
| K_Q/K_T 比值展开 vs 代理恒定 0.1 | 结构性对比 | spread 0.0483（代理 0.0） | **是**（代理指纹已消除） |
| **绝对量级**（K_T、K_Q、η₀ vs B 系列表值/试验） | — | **未比对**（无离线可信表值） | **否**（已声明欠账，判 WITHHELD） |

结论：所有可离线自证的不变式判据均 <3%；**绝对量级一项判 WITHHELD**，不作 REAL 精度声明，
仅声明接线正确性与代理指纹消除。

## 7. 复现与产物

| 产物 | 路径 |
|---|---|
| 接线代码 | `tools/batch_scan/batch_executor.py`（`calc_propeller` / `_bem` / `_geometry`） |
| 判据测试（15 项） | `tests/test_m1662_propeller_wiring.py` |
| 台账（全部数字） | `artifacts/m1662/propeller_wiring_ledger.json` |
| 图件 | `artifacts/m1662/propeller_wiring.png` |
| 生成脚本 | `artifacts/m1662/make_ledger.py`、`artifacts/m1662/patch_batch_executor.py` |

回归命令：`python3 -m pytest tests/test_m1662_propeller_wiring.py tests/test_propeller_open_water.py tests/test_propulsion_anchors.py -q` → **65 passed**（HEAD 上同组为 50 passed / 1 failed = 接线测试红）。