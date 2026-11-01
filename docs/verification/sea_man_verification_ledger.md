# 耐波 / 操纵域验证欠账清偿（m1523）

> EDIT_WHITELIST: tools/verifier/, tests/, docs/verification/, artifacts/
>
> 判决台账工件：`artifacts/verification/sea_man_verdicts.json` / `.md`；图：`artifacts/verification/fig_sea_man.png`
> 可复算：`python3 tools/verifier/tp_ver_sea_man.py --replay`（快照 `tools/verifier/fixtures/sea_man_probe_snapshot.json`）

## ① 案例介绍（物理背景 / 几何 / 参数）

平台「船舶总体性能」六域中，阻力、推进、破舱稳性、板架稳定已由前序单清偿验证欠账；
**耐波（seakeeping）与操纵（manoeuvring）两域此前没有任何独立判决**——本单即补这一块。
两域均以公开基准船 **KRISO KCS** 为算例（几何先验：Cb 必须等于 ∇/(L·B·T)，见 GEO-0，实测偏差 2.2e-6，通过）。

| 参数 | 值 | 参数 | 值 |
|---|---|---|---|
| Lpp | 230.0 m | ∇ | 52030.0 m³ |
| B | 32.2 m | Cb（公开值） | 0.6505 |
| T | 10.8 m | Cb = ∇/(LBT)（反算） | 0.65050 |
| D | 19.0 m | Cm / Cwp | 0.9849 / 0.868 |
| 航速 U | 7.7 m/s (15 kn) | KG / GM（设计假定） | 11.0 / 1.2 m |
| 螺旋桨 Dp | 7.0 m | 舵面积 Ar | 55.0 m² |

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

工具链：`tools/verifier/tp_ver_sea_man.py`（19 条判据 + 探针记录）→
`tools/verifier/gen_sea_man_artifacts.py`（图 + 本台账）→ `tests/test_tp_ver_sea_man.py`（13 项离线回归）。

方法（与 `tools/verifier/verifier.py` 同源，NEVER trust self-reported result）：

1. **探针记录**：对 16 个端点/工况（回转 35°/20°/5°/低速、Z 形 10°/35°、K-T、MMG、RAO×2、motion×2、
   strip、wave-spectrum、roll-motion）打实时服务，落盘 `fixtures/sea_man_probe_snapshot.json`（含 500 错误体）。
2. **闭式判据**：每条判据只用①解析恒等式（R=U/r、Nomoto r=Kδ、ω²=gk、T_φ=2πk/√(gGM)、
   m0=Hs²/16、ω_n²=g·Cwp/(Cb·T)）②规范限值（IMO Res. MSC.137(76)）③物理不等式（附加质量 ⇒ T_n≥T_box）。
   **不使用任何黑箱模型作参考**，故判决本身不需要被判决。
3. **快照回放**：判决函数只吃快照 dict，不碰网络 ⇒ pytest 可离线复跑全部 19 条判决（防判决漂移）。
4. **规范性检验**：对每条判据强制 `basis`+`tol` 非空（台账完整性测试）。

## ③ 计算结果（判决台账，数字程序化注入）

- 判据总数 **19**：PASS **3** / FAIL **16** / ERROR 0。
- 分域：geometry PASS 1；manoeuvring FAIL 9/9；
  seakeeping PASS 2 / FAIL 7/9。

### 操纵域（9/9 FAIL，证据分级 MOCK）

| 编号 | 判据 | 容差 | 参考值 | 实测值 | 相对误差 | 判决 | 分级 |
|---|---|---|---|---|---|---|---|
| MAN-1 | 稳态回转直径 vs 回转角速度（D = 2U/r） | 5% | {"D_from_rate [m]": 1764.7, "D_reported [m]":  | 805.0 | 54.4% | **FAIL** | MOCK |
| MAN-2 | 回转直径对舵角的敏感性（dD/dδ < 0） | 35° 比 20° 至少收缩 5% | {"D_35deg [m]": 1035.0, "D_20deg [m]": 1035.0, | 0.0 | - | **FAIL** | MOCK |
| MAN-3 | 回转角速度航速标度律 r ∝ U | 15% | {"expected_ratio(U=7.7/5.0)": 1.54, "r_7.7 [de | 1.0 | 35.1% | **FAIL** | MOCK |
| MAN-4 | 小舵角渐近（δ→0 => D→∞） | D(5°)/D(35°) >= 3 | {"D_35deg [m]": 1035.0, "D_5deg [m]": 1035.0,  | 1.0 | - | **FAIL** | MOCK |
| MAN-5 | IMO MSC.137(76) 回转性限值 + 硬编码指纹 | 限值 + 栅格指纹 | {"advance/L": 3.5, "transfer/L": 2.0, "TD/L":  | {"advance/L": 3.5, "TD/L": 4.5} | - | **FAIL** | MOCK |
| MAN-6 | K-T 模型与回转输出互锁（r_ss = K·δ） | 20% | {"K": 2.987, "r_ss_if_K_per_s [deg/s]": 104.54 | 0.5 | 85.7% | **FAIL** | MOCK |
| MAN-7 | K-T 无量纲量级合理性（K', T'） | 工程域 | {"K [1/s]": 2.987, "T [s]": 580.68, "K'=KL/U": | {"Kp": 89.222, "Tp": 19.44} | - | **FAIL** | MOCK |
| MAN-8 | Z 形试验超越角依赖 + IMO 限值 | 限值 + 单调 | {"overshoot_1(10deg) [deg]": 1.5, "overshoot_1 | {"overshoot_1(10deg)": 1.5} | - | **FAIL** | MOCK |
| MAN-9 | /maneuvering-mmg/turning 可用性（MMG 3-DOF 真解算） | HTTP 200 + 数值 | "HTTP 200 含回转要素" | "http=500" | - | **FAIL** | MOCK |

关键原始回执（KCS，δ=35°）：advance=805.0 m、transfer=460.0 m、
tactical_diameter=1035.0 m、steady_diameter=805.0 m、
turning_rate=0.5 deg/s、final_speed=2.77 m/s。
即 advance/L=3.50、transfer/L=2.00、TD/L=4.50
——三个要素同时落在 0.5L 整数栅格上，且对舵角（35°/20°/5°）、航速（7.7/5.0 m/s）、
Cb、舵面积、桨径全部不敏感 ⇒ 常量表输出。
K-T 端点：K=2.987 1/s、T=580.68 s ⇒ K'=KL/U=89.2、
T'=TU/L=19.44（回归适用域 K'∈[0.2,5]、T'∈[0.3,5]）。
Z 形：δ=10° 首超越角 1.5°、δ=35° 为 5.2°，周期恒为 1161.4 s。

### 耐波域（2 PASS / 7 FAIL）

| 编号 | 判据 | 容差 | 参考值 | 实测值 | 相对误差 | 判决 | 分级 |
|---|---|---|---|---|---|---|---|
| SEA-1 | 垂荡 RAO 长波渐近 RAO(ω→0)=1 | 10% | {"RAO_ref": 1.0, "omega_min [rad/s]": 0.2, "la | 1.028 | 2.8% | **PASS** | REAL |
| SEA-2 | 纵摇 RAO 长波渐近 RAO(ω→0)=0 | |RAO| <= 0.05 rad/m | {"RAO_ref": 0.0, "omega_min [rad/s]": 0.2} | 1.028 | 2.8% | **FAIL** | MOCK |
| SEA-3 | 纵摇/垂荡 RAO 的物理区分度 | 不得恒等 | {"pitch_over_heave_at_omega_min": 1.0} | "identical" | - | **FAIL** | MOCK |
| SEA-4 | 横摇固有周期 GM 标度律 T ∝ 1/√GM | 10% | {"expected_ratio T(GM=1.2)/T(GM=2.4)": 1.4142, | 1.4143 | 0.0% | **PASS** | REAL |
| SEA-5 | 垂荡固有周期的附加质量下界 | T_n >= T_box（比值 >= 1.0） | {"T_box [s]": 5.708, "T_n_reported [s]": 5.25, | 0.9197 | 8.0% | **FAIL** | MOCK |
| SEA-6 | 垂荡/纵摇固有周期不得相等 | 差异 >5% | {"T_heave [s]": 5.25, "T_pitch [s]": 5.25} | 0.0 | 0.0% | **FAIL** | MOCK |
| SEA-7 | Strip theory 端点：纵摇须有动力学、垂荡低频须→1 | 见证据 | {"heave_rao(omega_min)": 0.5, "pitch_rao_mean  | {"pitch_rao_cv": 0.06442, "heave_lo": 0.5} | 50.0% | **FAIL** | MOCK |
| SEA-8 | /seakeeping-advanced/wave-spectrum 可用性（m0=Hs²/16 自洽） | HTTP 200 + 5% | {"m0_analytic [m²]": 1.5584, "Hs²/16": 1.5625} | "http=500" | - | **FAIL** | MOCK |
| SEA-9 | /dynamic-stability/roll-motion 可用性 | HTTP 200 + 数值 | "HTTP 200 含横摇要素" | "http=500" | - | **FAIL** | MOCK |

关键原始回执：RAO 30 个频点中 heave_rao 与 pitch_rao **逐点完全相同**
（ω=0.2 处同为 1.028）；natural_periods = heave 5.25 s / pitch 5.25 s / roll 15.02 s（heave 与 pitch 恒等）。

## ④ 结果图

![fig_sea_man.png](fig_sea_man.png)

四联图：(a) 回转要素 vs 运动学预期 D=2U/r；(b) 战术直径对舵角的响应（平台为水平线，违背 ∂D/∂δ<0）；
(c) 垂荡/纵摇 RAO vs λ/L（两条曲线完全重合，与解析渐近 heave→1、pitch→0 不符）；
(d) 固有周期 vs 垂荡附加质量下界（T_n<T_box 等价于负附加质量）。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **源在禁改路径**：:8001 服务由 `/srv/tensor/ship-workspace/...` 启动，不在本仓工作树白名单内，
   故本单只做「验证欠账清偿 + 判决台账」，不修其实现（实测修复需另派单到该仓）。
2. **端点请求模型与直觉不同**：`/seakeeping/rao` 的 `wave_frequency` 是标量（传数组 422）；
   返回值恒为 30 个频点数组——这也是一种「参数被忽略」的旁证。
3. **K-T 单位二义性**：无法确知 K 是 1/s 还是无量纲 K'，故 MAN-6 取**对报告方最有利**的读法仍 FAIL（偏 86%），避免因单位假设误判。
4. **附加质量下界判据的强度**：T_n≥T_box 是严格不等式（a33≥0），FAIL 只说明「等价负附加质量」，
   不给出真实 T_n 应是多少；真实船 T_n/T_box 工程带为 [1.1,1.6]。
5. **SEA-7 纵摇变异系数 6.44%** 仅略高于 5% 门槛（首个频点 0.026 后恒为 0.02），
   该条 FAIL 主要由垂荡低频 RAO=0.5（应→1）支撑。
6. **三个端点 500**（MMG 回转、波浪谱、横摇运动）在标准输入下必现，属可用性缺陷而非精度缺陷。

## ⑥ 误差判定（关键量 vs 基准，逐项回答「是否 <3%」）

| 编号 | 判据 | 是否 <3% | 相对误差 |
|---|---|---|---|
| GEO-0 | 参考船几何自洽 Cb = ∇/(L·B·T) | <3% | 0.00% |
| MAN-1 | 稳态回转直径 vs 回转角速度（D = 2U/r） | 否 | 54.38% |
| MAN-2 | 回转直径对舵角的敏感性（dD/dδ < 0） | 否 | 不适用（结构性恒等 / 可用性判据） |
| MAN-3 | 回转角速度航速标度律 r ∝ U | 否 | 35.06% |
| MAN-4 | 小舵角渐近（δ→0 => D→∞） | 否 | 不适用（结构性恒等 / 可用性判据） |
| MAN-5 | IMO MSC.137(76) 回转性限值 + 硬编码指纹 | 否 | 不适用（结构性恒等 / 可用性判据） |
| MAN-6 | K-T 模型与回转输出互锁（r_ss = K·δ） | 否 | 85.71% |
| MAN-7 | K-T 无量纲量级合理性（K', T'） | 否 | 不适用（结构性恒等 / 可用性判据） |
| MAN-8 | Z 形试验超越角依赖 + IMO 限值 | 否 | 不适用（结构性恒等 / 可用性判据） |
| MAN-9 | /maneuvering-mmg/turning 可用性（MMG 3 | 否 | 不适用（结构性恒等 / 可用性判据） |
| SEA-1 | 垂荡 RAO 长波渐近 RAO(ω→0)=1 | <3% | 2.80% |
| SEA-2 | 纵摇 RAO 长波渐近 RAO(ω→0)=0 | <3% | 2.80% |
| SEA-3 | 纵摇/垂荡 RAO 的物理区分度 | 否 | 不适用（结构性恒等 / 可用性判据） |
| SEA-4 | 横摇固有周期 GM 标度律 T ∝ 1/√GM | <3% | 0.01% |
| SEA-5 | 垂荡固有周期的附加质量下界 | 否 | 8.03% |
| SEA-6 | 垂荡/纵摇固有周期不得相等 | <3% | 0.00% |
| SEA-7 | Strip theory 端点：纵摇须有动力学、垂荡低频须→1 | 否 | 50.00% |
| SEA-8 | /seakeeping-advanced/wave-spectrum | 否 | 不适用（结构性恒等 / 可用性判据） |
| SEA-9 | /dynamic-stability/roll-motion 可用性 | 否 | 不适用（结构性恒等 / 可用性判据） |

**结论**：3 条判据达标（GEO-0 几何恒等 0.0002%、
SEA-1 2.80%、SEA-4 0.0070% —— 这三条**均 <3%**）；
其余 16 条**均不满足 <3%**，其中 MAN-1 偏差 54%、
MAN-6 偏差 86%、SEA-5 偏差 8.0%，
已远超平台「>15% 不可接受」红线。按证据分级：耐波域 SEA-1/SEA-4 = REAL，
操纵域 9/9 与耐波域 7/9 = MOCK，3 个端点 = STUB（500）。
