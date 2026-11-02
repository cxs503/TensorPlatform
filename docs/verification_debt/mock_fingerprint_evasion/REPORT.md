# 证据闸门的规避阈值审计：MOCK 指纹探测器对"带噪声的伪造"的失明点

**任务** m2036（自找活·验证欠账类，platform 席）｜**仓** TensorPlatform
**编辑白名单** EDIT_WHITELIST: `tools/mock_fingerprint/`, `tests/`, `docs/verification_debt/mock_fingerprint_evasion/`, `artifacts/mock_fingerprint/`
**产物** `tools/mock_fingerprint/adversary_sweep.py`、`tests/test_mock_fingerprint_adversary.py`(10 项全绿)、
`artifacts/mock_fingerprint/detector_evasion_sweep.{json,png}`

## ① 案例介绍（物理/工程背景与对象）

`tools/mock_fingerprint` 是平台的**结构性信任闸门**：端点自称 REAL，闸门用"无需实验基线的物理不变量"
（比值锁定 / 参数失明 / 硬物理界 / 钳位平台 / 参考偏差）裁决它是否其实在伪造数字。敞水螺旋桨端点的
历史 MOCK 判决（报告 1173/1218、`docs/verification_debt/propeller_endpoint/mock_gate.md`）就是它给出的。

该闸门此前的自证是**两点验证**：一个合成缺陷响应（`K_Q = K_T/10`）必须触发，一个健康响应（Wageningen
B 系列 39/47 项回归）必须静默。两点验证能证明探测器"点得着、也能不点"，**但不能回答运营上最要紧的那个问题**：
一个伪造实现要扰动多少，才能从闸门里走过去？

被测对象与参数（无需实验基线，全部由仓内回归公式合成）：

| 项 | 设置 |
|---|---|
| 进速比扫描 | J = 0.00 : 0.05 : 1.25（26 点） |
| 设计点 | (P/D, A_E/A_0, Z) = (1.0, 0.70, 4)、(1.4, 0.75, 5)、(0.8, 0.70, 4) |
| 扰动网格 ε | 0, 1e-12, 1e-9, 1e-6, 1e-4, 1e-2（相对扰动，确定性 ±1 序列，无 RNG） |
| 被测探测器 | `ratio_lock`×2、`parameter_blindness`×3、`physical_bound`×2、`clamp_plateau`×2（**未改动一行源码**） |

伪造族：F1 `K_Q=K_T/10`（历史指纹）· F3 `K_T=c J^k` 且对 P/D、A_E/A_0、Z 全失明 · F4 η₀ 在 J>0.6 被钳到 0.85（报告 1218 记录的钳位指纹）。

## ② 怎么做的（方法/流程/工具链）

1. 用仓内 B 系列回归合成"健康端点"作为无误报基线；再用四个族构造同一缺陷的不同扰动版本。
2. 以 `run_detectors()` 一次性推过**现有**探测器（签名原样调用，不改阈值），逐 ε 记录 `MOCK/REAL` 与命中项。
3. 规避阈值 = 该族首次被判 REAL 的最小 ε；并测"健康spread / 触发容差"的余量作为可安全放宽的量化依据。
4. 复跑：`python3 -m tools.mock_fingerprint.adversary_sweep`；判据：`python3 -m pytest tests/test_mock_fingerprint_adversary.py -q`（10 passed）。

## ③ 计算结果（程序化注入，零手抄）

### 3.1 健康基线与余量
健康 B 系列响应在全部探测器上静默，最小相对散布 **0.2824**；
Z 由 4 改 5 使 K_T 最大变化 **26.23%**（参数灵敏度远高于容差）。

### 3.2 F4（η₀ 钳位到 0.85）—— 规避阈值 1e-12
| ε | 判决 | 命中探测器 |
|---|---|---|
| 0 | MOCK | clamp_plateau/eta0 |
| 1e-12 | REAL | — |
| 1e-09 | REAL | — |
| 1e-06 | REAL | — |
| 0.0001 | REAL | — |
| 0.01 | REAL | — |

### 3.3 F1（K_Q = K_T/10）
| ε | 判决 | 命中探测器 |
|---|---|---|
| 0 | MOCK | ratio_lock/KQ/KT, parameter_blindness/KT, parameter_blindness/eta0, parameter_blindness/v, physical_bound/eta0 |
| 1e-12 | MOCK | parameter_blindness/KT, parameter_blindness/v, physical_bound/eta0 |
| 1e-09 | MOCK | parameter_blindness/KT, parameter_blindness/v, physical_bound/eta0 |
| 1e-06 | MOCK | parameter_blindness/KT, parameter_blindness/v, physical_bound/eta0 |
| 0.0001 | MOCK | parameter_blindness/KT, parameter_blindness/v, physical_bound/eta0 |
| 0.01 | MOCK | parameter_blindness/KT, parameter_blindness/v, physical_bound/eta0 |

### 3.4 F3（幂律、参数全失明）
| ε | 判决 | 命中探测器 |
|---|---|---|
| 0 | MOCK | parameter_blindness/KT, parameter_blindness/eta0, parameter_blindness/v, physical_bound/eta0 |
| 1e-12 | MOCK | parameter_blindness/KT, parameter_blindness/eta0, parameter_blindness/v, physical_bound/eta0 |
| 1e-09 | MOCK | parameter_blindness/eta0, physical_bound/eta0 |
| 1e-06 | MOCK | parameter_blindness/eta0, physical_bound/eta0 |
| 0.0001 | MOCK | parameter_blindness/eta0, physical_bound/eta0 |
| 0.01 | MOCK | parameter_blindness/eta0, physical_bound/eta0 |

## ④ 结果图

`artifacts/mock_fingerprint/detector_evasion_sweep.png`：左图为三族的"命中阶跃曲线"对扰动 ε，
右图为健康响应在诸探测器上的 log₁₀ 相对散布与触发容差线（余量条带）。

## ⑤ 注意点（坑与局限）

* **要害**：`rel_tol = 1e-12` 让结构性探测器只判"逐位相等"。F4 在 ε=0 被 `clamp_plateau` 抓住，
  但 ε=1e-12 就整体翻成 REAL —— 一个伪造实现只要在钳位值上加 1 ulp 噪声即可通过。
  而健康响应的最小散布是 0.28，容差有 **2.8e+11 倍**未被使用的余量。
* **好消息**：F1/F3 族在每一个 ε 上仍被判 MOCK —— 但不是靠比值锁定，而是靠 `parameter_blindness` 与
  `physical_bound`（ε≥1e-12 后 `ratio_lock` 已静默）。即闸门的可靠性目前由"物理界 + 参数失明"承担，
  比值/钳位两类纯等式探测器对噪声零容忍度。
* `clamp_plateau` 还要求钳位窗口被完全采样（窗口内只要有一个未钳位点就静默），窗口边界是操作参数，需在探针里固定。
* 本审计只覆盖敞水端点族的响应形状，未覆盖其它域（阻力/耐波/稳性）探测器的具体判据；
  未改动探测器阈值本身（改阈值会改变历史判决，需 owner 裁定），只给出量化建议：**容差提到 1e-6～1e-3 量级仍零误报**。
* 端点 :8001 本轮不可达，故未复跑在线探针，全部判决基于合成响应（这本身也是可控变量的优点）。

## ⑥ 误差判定（判据 vs 平台标准）

| 关键量 | 基准 | 结果 | 是否达标 |
|---|---|---|---|
| 健康响应误报数 | 0（不得把 REAL 判成 MOCK） | **0**（19 组扫描中 healthy 全静默） | 是 |
| 健康最小散布 / 容差余量 | ≥1e8（至少 8 个数量级余量） | **2.8e+11** | 是 |
| F1 族检出率 | 100%（任一 ε） | **6/6** | 是 |
| F3 族检出率 | 100%（任一 ε） | **6/6** | 是 |
| F4 族检出率（ε=0） | 100% | **1/1** | 是 |
| F4 族检出率（ε≥1e-12） | 期望 100% | **0/5（失明）** | **否 —— 已如实登记为闸门缺陷** |

「是否 <3%」：本审计的关键量是**计数与数量级**（误报 0、余量 1e11 倍、检出 N/6），不是与实验值比的物理量，
故不适用 3% 判据；可比的量化误差是"规避阈值 = 1e-12 相对扰动"，它是精确记录值而非估计值。缺陷项（F4 噪声失明）已如实登记，不粉饰。
