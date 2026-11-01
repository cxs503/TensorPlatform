# m1645 耐波谱矩频率窗截断偏差：验证欠账清偿（解析尾项修正）

**域**：船舶总体性能 · 耐波性（seakeeping）｜**单元**：`backend/services/seakeeping.py`
**证据等级**：修复前 = REAL 模型但**谱矩导出量 REAL-with-bias（Tz 偏差 3.7%–15%）**；修复后 = REAL（对解析闭式 ≤1e-3%）
**判据**：平台精度标准（关键量 vs 基准 >3% 不可接受）；本次关键量 = 谱矩 m0/m1/m2 及导出量 Tz、Tm01（ITTC/PM/JONSWAP 三种谱）

## 1. 案例介绍（物理背景与参数）

频域耐波评估的三条链：**波浪谱 → 谱矩 → 响应统计**。平台 `/seakeeping/spectrum` 端点输出
`m0/m1/m2/Tz/Tm01`，其中 `Tz`（平均跨零周期）不仅自身是耐波性指标，还是疲劳/上浪等下游链的输入量。
谱的理论定义域是 (0, ∞)，但实现只能在有限频率窗 `[ω_lo, ω_hi]` 上做梯形积分。

历史实现：窗内积分后把谱整体缩放到 `4√m0 = Hs`。这个"窗内归一化"让 **Hs 恰好正确**，却把被丢弃的
高频尾部的方差折算进缩放因子，于是 **m2 被系统性低估 → Tz = 2π√(m0/m2) 被系统性高估**，且偏差随
频率窗与网格变化（不可复现）。

计算参数（验证算例）：Hs = 5.0 m、谱峰周期 T1 = Tp = 10.0 s（ωp = 0.628 rad/s），
三种谱：ITTC/ISSC 双参数谱、Pierson–Moskowitz、JONSWAP(γ=3.3)；
频率窗 4 组：`[0.2,2.0]/101`、`[0.2,2.0]/1001`、`[0.3,1.6]/81`、`[0.1,3.0]/2001`（rad/s 点数）。
RAO 侧算例：S-175 级集装箱船，L=175 m、B=25.4 m、T=9.5 m、Cb=0.56、Cwp=0.66、GM_T=1.2 m（几何门先行闭合 Cb/L/B/T/∇）。

## 2. 怎么做的（方法 / 流程）

1. **基准先立**：三种谱（PM / JONSWAP（γ=1 退化为 PM）/ ITTC）共用同一指数骨架
   `S(ω) = A·ω⁻⁵·exp(−B·ω⁻⁴)`，其矩有闭式：
   - `m0 = A/(4B)`，`m2 = A√π/(4√B)`（模块内已有 `ittc_m0_closed` / `ittc_m2_closed`）
   - 尾部（ω ≥ W）同样闭式：`m0_up(W) = (A/4B)(1 − e^{−B/W⁴})`，
     `m1_up(W) = (A/4)·B^{−3/4}·γ(3/4, B/W⁴)`，`m2_up(W) = (A/4)·B^{−1/2}·√π·erf(√(B/W⁴))`
   JONSWAP 的峰升因子 `γ^r` 在 ω ≤ ωp/2 与 ω ≥ 2ωp 之外趋于 1（r→0），因此骨架在"离开峰区"的
   窗口上就是真实尾部；窗口切进峰区时**不做修正并显式标注**。
2. **量化欠账**（修复前）：对 3 谱 × 4 窗，比较"窗内原始矩"与闭式/高分辨参考的 Tz、m2 相对误差。
3. **最小修复**：新增 `_lower_incomplete_gamma`（级数分支 x≤60 + 渐近补分支 x>60，避免 x≫100 时
   O(x) 项数与 `x^a·e^{−x}` 的 inf×0）、`exp_skeleton_moments`、`exp_skeleton_upper_moments`、
   `skeleton_tail_moments`；`_scale_to_hs` 改为按 **(窗内 + 解析尾项)** 归一到 `Hs²/16`；
   `spectrum()` 返回 `m0/m1/m2` 含尾项，并新增 `m0_in_range/m1_in_range/m2_in_range` 与
   `tail_correction{applied,note,m0,m1,m2}` 供审计（`S` 仍是窗内采样密度，响应积分口径不变）。
4. **判据固化**：新增 `backend/tests/feat/test_seakeeping_spectral_truncation_m1645.py`（25 项）：
   闭式尾项 vs 数值积分、m0 精确、Tz 窗无关（<5e-4）、**把"窗内原始矩偏差 >3%"写成回归判据**、
   JONSWAP 窗口切峰时必须 `applied=False` 且标注 truncation-biased。
5. **工具链**：pytest（`PYTHONPATH=backend ... --noconftest`，沙箱缺 psycopg2/cryptography）、
   matplotlib（Agg）出图、JSON 工件程序化注入报告（零手抄）。

## 3. 计算结果（数字表，全部由 `artifacts/m1645/spectral_tail_verification.json` 程序化导出）

ITTC 谱 Hs=5 m、T1=10 s，闭式 `Tz = 9.204981 s`：

| 频率窗 [rad/s] / 点数 | 窗内 m2 误差 | 窗内 Tz [s] | Tz 误差（修复前） | 修复后 Tz [s] | Tz 误差（修复后） |
|---|---|---|---|---|---|
| [0.2,2.0] / 101  | −7.40%  | 9.5453 | **+3.70%** | 9.20499 | +1.2e−4% |
| [0.2,2.0] / 1001 | −7.40%  | 9.5453 | **+3.70%** | 9.20498 | +1.2e−6% |
| [0.3,1.6] / 81   | −11.55% | 9.7349 | **+5.76%** | 9.20483 | −1.7e−3% |
| [0.1,3.0] / 2001 | −3.29%  | 9.3565 | **+1.65%** | 9.20498 | +1.8e−7% |

PM 谱（Tz=7.103707 s）与 JONSWAP γ=3.3（Tz=7.773992 s）同窗表现：

| 谱 | 窗 [0.2,2.0]/101 修复前 | 修复后 | 窗 [0.3,1.6]/81 修复前 | 修复后 |
|---|---|---|---|---|
| PM      | +6.20% (m2 −12.40%) | +1.6e−4% | **+9.65%** (m2 −19.26%) | +2.3e−4% |
| JONSWAP | +4.84% (m2 −9.74%)  | −1.0e−3% | **+7.50%** (m2 −15.13%) | +1.1e−3% |

尾项占 m2 的比例（即"被丢掉的物理"）：ITTC 7.4%（[0.2,2.0]）→ 11.5%（[0.3,1.6]）；
PM 12.4%→19.3%；JONSWAP 9.7%→15.1%。对 m0 而言尾项仅 1e−3…2e−2 量级，所以**问题只暴露在 m2 及其导出量**。

响应侧（S-175 级、首斜浪/迎浪 U=0、Hs=4 m、T1=9 s）：`sigma_heave / significant_heave / sigma_pitch /
significant_pitch / sigma_vertical_acc` 在修复前后**完全相同**（Δ=0.00%）。原因：`response_statistics`
积的是窗内 `RAO²·S`，尾项只进入谱矩而非该积分。因此本修复**不改变**响应幅值结论，
只修正谱矩导出量（Tz、Tm01）——这一点如实记录，不做粉饰。

图：`artifacts/m1645/fig1_spectrum_tail.png`（谱与窗口、被丢弃尾部）、
`fig2_tz_error_vs_window.png`（Tz 误差随窗口上界，修复前跨 3% 门）、
`fig3_rao_and_tz_gate.png`（迎浪 RAO + 4 窗 Tz 误差对比）。

## 4. 注意点（踩过的坑 / 局限 / 适用范围）

1. **m0 尾项闭式易写反**：`m0_up(W) = (A/4B)(1−e^{−B/W⁴})`，写成 `(A/4B)e^{−B/W⁴}` 会得到"尾部占 99%"的荒谬结果（首次实现即踩此坑，m2 误差 −94.6% 立刻暴露）。
2. **`ittc_shape` 实际幅值是 Hs-free 的 `173/T1⁴`**（docstring 说"A 携带 Hs²"但代码不乘 Hs），尾项分数必须用同一幅值，否则归一化与尾项不自洽。
3. **不完全伽马函数**：小 x 用级数 `γ(a,x)=x^a e^{−x}Γ(a)Σxⁿ/Γ(a+n+1)`（缺 `Γ(a)` 因子会整体偏小 18%）；x>60 必须走 `Γ(a)−Γ(a,x)` 渐近补，否则出现 nan（本单第二次踩坑）。
4. **适用范围**：尾项只在"窗口离开谱峰"时成立（JONSWAP 需 ω_lo ≤ ωp/2 且 ω_hi ≥ 2ωp）。窗口切进峰区时 `tail_correction.applied=False` 并给出 truncation-biased 提示——此时 Tz 仍带偏，**不得**当成精确值使用。
5. **未清偿的相邻欠账（如实列出）**：①`response_statistics` 内的响应矩（`Tz_heave_s` 等）与纵/横摇响应仍为窗内积分，同源截断偏差未修（无闭式可用，需响应谱的峰形假设）；②`backend/services/digital_twin/models/fatigue.py` 的 `m4`（带宽参数 ε 用）截断偏差比 m2 更重，未在本单范围内；③本模块 κ/ζ 仍是经验输入，绝对 RAO 精度对模型试验仍为 WITHHELD（无试验数据入册）。

## 5. 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 修复前误差 | 修复后误差 | <3%？ |
|---|---|---|---|---|
| ITTC m2（窗 [0.2,2.0]） | 闭式 A√π/(4√B) | −7.40% | −2.4e−4% | 修复后 是 |
| ITTC Tz（窗 [0.2,2.0]） | 闭式 9.204981 s | **+3.70%（否）** | +1.2e−4% | 修复后 是 |
| ITTC Tz（窗 [0.3,1.6]） | 同上 | **+5.76%（否）** | −1.7e−3% | 修复后 是 |
| PM Tz（窗 [0.2,2.0]） | 高分辨参考 7.103707 s | **+6.20%（否）** | +1.6e−4% | 修复后 是 |
| JONSWAP Tz（窗 [0.3,1.6]） | 高分辨参考 7.773992 s | **+7.50%（否）** | +1.1e−3% | 修复后 是 |
| 尾项闭式 vs 数值积分（m0/m1/m2, W=1,2,3） | 梯形积分 [W,400] | — | ≤2e−5（m2 ≤1e−3，受参考截断限制） | 是 |
| 响应统计 sigma/Hsig（迎浪） | 本模块同口径 | — | 0.00%（不变量） | 是（且未受影响） |

**结论**：修复前谱矩导出量 Tz 的偏差 1.65%–9.65%（视窗口/谱型），多组工况 **>3%，判不可接受**；
修复后全部 ≤1.1e−3%，判 **<3%** 达标，且结果与频率窗无关（可复现）。

## 6. 复现

```bash
cd <worktree>
PYTHONPATH=backend python3 -m pytest backend/tests/feat/test_seakeeping_spectral_truncation_m1645.py -q --noconftest
# 25 passed
PYTHONPATH=backend python3 -m pytest backend/tests/feat/test_ship_seakeeping.py -q --noconftest
# 24 passed, 1 env-red（test_endpoints_contract：沙箱缺 psycopg2，基线同样红）
```