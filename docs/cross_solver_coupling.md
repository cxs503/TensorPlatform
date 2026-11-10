# 跨求解器耦合基础设施：保守场映射 + 时钟同步 + 能量守恒耦合（P148W2-N02）

生成时间（UTC）：`2026-11-10T18:28:35.507660+00:00` ｜ git commit：`3f05b56b60da36e8f149aac9bb3f5d1c6f8606ad` ｜ python 3.11.6 / numpy 2.4.6

**总结论**：10/10 条验收判据通过（全部通过）。

## 1. 案例介绍

本节点验证的是**跨求解器耦合的基础设施**，不是任何一个物理模型，因此算例是合成算例：
单位正方形 `[0,1]^2`、两套均匀直角网格、三个解析场（一个守恒标量密度、一个守恒矢量、一个能量型密度）。
场量函数为

* 密度 `ρ(x,y) = 2 + sin(πx)·cos(πy)`（严格为正）
* 速度 `u = (0.5 + cos(πx)·sin(πy/2), 1 + sin(πx)·cos(πy))`，动量密度 `m = ρ·u`
* 能量密度 `e = ρ·(½|u|² + 1 + 0.25·cos(2πxy))`

| 参数 | 取值 |
|---|---|
| 定义域 | unit square [0,1]^2 (synthetic, no physical model) |
| 源网格 | uniform 16x16 |
| 源:目标网格比 | 1:1, 1:2, 1:4, 1:8, 1:10 |
| 交换场量 | density, momentum(2), energy |
| 随机性 | fully deterministic; no RNG is used anywhere in this benchmark |
| 时钟耦合间隔 Δ | 0.05 s |
| 快/慢子循环比 | slow=1, fast=0.248 |

## 2. 怎么做的

**① 保守场映射**（`tools/field_mapper/`）：两级几何搜索——先在一条轴上排序后做 `searchsorted` 得到候选对，再用包围盒整体判据剔除；随后用**精确交叠测度**构造算子 `f_dst[j] = Σ_i S[i,j] f_src[i] / |Ω_j|`，其中 `S[i,j] = |Ω_i ∩ Ω_j|`：直角网格用解析交叠（1/2/3 维同一条代码），凸多边形用 Sutherland–Hodgman 裁剪 + 鞋带公式。守恒律必须**显式声明**（`density` / `extensive` / `scalar`），校正项（`none` / `global_scale` / `bounded`）只对非保守算子有意义且全部记账。

**② 时钟同步**（`tensorphysics/clock_sync/clock.py`）：耦合点用乘法 `T_k = k·Δ` 生成（不做累加，避免漂移）；每个求解器用自己的步长演化到 `T_k`，**窗口最后一个子步被截断**以保证精确落点；每次截断都记账。
`align_last_substep=False` 是负控：不截断就按满步长向前走，落点超出耦合点。
实测：对齐时最大偏差 0 s，不对齐时 0.003 s。

**③ 能量守恒耦合**（`energy.py` + `subcycling.py`）：接口账本累计每个参与方从界面获得的净能量与耦合自身储能，闭合判据 `ΣE_in = ΣE_out + ΔE_stored`；并把**接口数据插值方式**作为可变量：零阶保持（`hold`）与线性外推（`linear`）。

**④ 稳定性**（`lyapunov.py`）：Benettin 算法 + Gram–Schmidt 重正交，切映射用有限差分从**映射本身**得到（不需要解析 Jacobian），并用线性映射的精确谱 `log|eig(M)|/dt` 校验估计器本身。

## 3. 计算结果

| 判据 | 指标 | 实测值 | 阈值 | 判定 |
|---|---|---|---|---|
| B1 | `mass_rel_error` | 0 | <= 0.005 | PASS |
| B2 | `momentum_rel_error` | 0 | <= 0.01 | PASS |
| B3 | `energy_rel_error` | 0 | <= 0.01 | PASS |
| B4 | `max_coupling_point_skew_s` | 0 | <= 1.000e-10 | PASS |
| B5 | `subcycle_state_rel_error` | 1.483e-05 | <= 0.05 | PASS |
| B6 | `max_lyapunov_exponent` | -0.03888 | <= 0 | PASS |
| B7 | `identity_l2_rel` | 0 | <= 1.000e-12 | PASS |
| B8 | `zero_coupling_exchange_energy` | 0 | <= 1.000e-12 | PASS |
| B9 | `max_conservation_rel_error_over_ratios` | 2.144e-16 | <= 0.02 | PASS |
| B10 | `min(error_modes/3, manifest_fields/4)` | 1 | >= 1 | PASS |

### 3.1 网格比敏感性（B9）与负控对照

| 源:目标 | 单元数(源→目标) | 质量误差 | 动量误差 | 能量误差 | 密度 L2 vs 解析 | 最近单元对照:质量误差 | 守恒校正后对照:能量误差 | 覆盖最小值 |
|---|---|---|---|---|---|---|---|---|
| 1:1 | 256→256 | 0 | 0 | 0 | 7.784e-04 | 0.03966 | 0 | 1 |
| 1:2 | 256→1024 | 0 | 0 | 2.144e-16 | 0.01683 | 0.01983 | 0 | 1 |
| 1:4 | 256→4096 | 0 | 0 | 0 | 0.01881 | 0.009915 | 0 | 1 |
| 1:8 | 256→16384 | 0 | 0 | 0 | 0.01928 | 0.004958 | 0 | 1 |
| 1:10 | 256→25600 | 0 | 0 | 0 | 0.01933 | 0.003966 | 2.144e-16 | 1 |

保守算子的守恒误差在全部网格比下处于舍入量级（最大 2.144e-16），因为目标网格完整铺满源域（覆盖比例最小值恒为 1），交叠测度构成精确的重新分配。**最近单元注入**（非保守对照）在同一算例下的质量误差最大 0.03966，超过 B1 的 0.5% 门限约 7.9 倍——守恒门确实区分了“重新分配”与“重采样”。给对照臂加上全局守恒校正后，总误差降到 2.144e-16，但其密度 L2 误差仍有 0.06472（保守算子为 0.01881）——**守恒 ≠ 精度**，两者本报告始终分开报告。

### 3.2 时钟与子循环（B4 / B5）

| 量 | 值 |
|---|---|
| 耦合点数 | 20 |
| 对齐时最大偏差 | 0 s |
| 不对齐时最大偏差 | 0.003 s |
| 截断子步总数（对齐） | 20 |
| 10:1 子循环 vs 细化参考解 误差 | 1.483e-05 |
| 更严参考（耦合间隔同时细化 4 倍）误差 | 0.001072 |
| 接口能量闭合残差（linear 接口数据） | 0.005701 |
| 接口能量闭合残差（零阶保持 hold） | 0.07719 |

### 3.3 耦合系统稳定性（B6）

| 系统 | 最大 Lyapunov 指数 (1/s) | 判定 |
|---|---|---|
| damped | -0.03888 | ≤0（严格稳定） |
| conservative | 0.004246 | 边缘：|λ|≤0.01（理论 0，估计器噪声） |
| unstable_control | 0.08326 | >0（发散） |

阻尼系统的谱严格为负；保守系统理论上为 0，实测落在估计器噪声带内（报告为边缘，不作为严格 ≤0 的证据）；负阻尼对照给出正指数，证明检测器不是“永远通过”。

估计器自校验：对一个特征值已知的 4×4 线性映射，Benettin 估计与精确谱 `log|eig(M)|/dt` 的最大绝对差为 5.839e-09（估计 [0.0, -0.512933, -1.053605, -2.231436] vs 精确 [0.0, -0.512933, -1.053605, -2.231436]），远小于边缘带 0.01，因此“边缘/稳定/发散”的判别不是估计器伪影。

## 4. 结果图

![conservation_errors.png](benchmarks/verified/cross_solver_coupling/figures/conservation_errors.png)

*B1/B2/B3/B9: mass, momentum and energy conservation error of the intersection-based operator across grid ratios 1:1..1:10, against the nearest-cell control; dashed lines are the 0.5% / 1% / 2% thresholds.*

![coupling_sync_error.png](benchmarks/verified/cross_solver_coupling/figures/coupling_sync_error.png)

*B4/B5: left — |skew| between each solver's clock and the coupling point, aligned vs unaligned; right — error of the 10:1 sub-cycled run against the step-refined reference.*

![lyapunov_convergence.png](benchmarks/verified/cross_solver_coupling/figures/lyapunov_convergence.png)

*B6: running estimate of the leading Lyapunov exponent for the damped coupled map (stable), the conservative map (marginal, |lambda| ~ estimator noise) and the negative-damping control (unstable).*

![energy_budget_closure.png](benchmarks/verified/cross_solver_coupling/figures/energy_budget_closure.png)

*B3 coupled budget: |Σ E_in − Σ E_out − ΔE_stored| for zero-order-hold and linear interface data as the coupling interval is refined (first vs second order).*

## 5. 注意点（踩坑与局限）

1. **零阶保持是 O(Δ) 缺陷**：最初直接在慢求解器上用粒子/伙伴状态保持，接口能量闭合残差高达 7.7e-2，拒绝把它当“能量守恒耦合”。改为线性接口数据（用最近两个交换值外推）后残差降到 5.7e-3，图 4 显示两条曲线分别是 O(Δ) 与 O(Δ²)——这既是修复证据，也是适用范围：耦合间隔必须足够小到二次项可忽略。
2. **commensurate 掩盖对齐问题**：只要 `dt_fast = Δ/(n·m)`，满步长本来就正好落在耦合点上，此时“不对齐”负控也是零偏差、无法判别。B4 因此改用**非公度**步长（0.0031 s vs Δ=0.05 s，16.13 步/窗口）才暴露出 3e-3 s 的偏差。
3. **有限差分谱估计器自身的误差**：保守系统理论指数为 0，实测 0.004246，因此 B6 的严格判定只用阻尼系统，边缘情形只作报告；估计器本身已用线性映射精确谱校验，最大绝对差 5.839e-09。
4. **守恒不等于精度**：交叠投影是守恒的，但源网格分辨率决定其点值精度（本算例密度 L2 误差约 1.8e-2）。
5. **分场独立映射的内部一致性**：ρ、ρu、e 分别映射各自的总量都严格守恒，但由 ρ、ρu 重建的能量与独立映射的 e 存在差异，本算例为 0.01329（未做为门，但如实报告）。
6. **范围**：只覆盖直角网格与 2D 凸多边形；三维凸多面体裁剪未实现（三维直角盒解析交叠已覆盖）。时间积分器只实现了一个二阶显式格式；耦合数据只实现 hold / linear 两种。

## 6. 误差判定（逐项 vs 基准）

基准取法：守恒量（质量/动量/能量）以**映射前总积分量为基准**；精度量以**解析场的高斯-勒让德单元平均**为基准；时钟以**解析耦合点 k·Δ** 为基准；子循环以**同步细化 8 倍的同格式解**为基准；稳定性以**线性映射精确谱**校验估计器、以阻尼符号为物理基准。

| 关键量 | 基准 | 误差 | 是否 < 3% |
|---|---|---|---|
| 质量总量 | 映射前 ΣρV | 0 | 是 |
| 动量总量 | 映射前 Σ(ρu)V | 0 | 是 |
| 能量总量 | 映射前 ΣeV | 0 | 是 |
| 最大网格比 1:10 守恒误差 | 映射前总量 | 2.144e-16 | 是 |
| 同网格恒等映射 L2 | 源场本身 | 0 | 是 |
| 10:1 子循环状态误差 | 细化参考解 | 1.483e-05 | 是 |
| 接口能量闭合（linear） | 界面净能量尺度 | 0.005701 | 是 |
| 密度投影 L2（精度，非守恒） | 解析单元平均 | 0.01881 | 是 |

**显式回答“是否 < 3%”**：所有守恒量、时钟同步、子循环与恒等性判据的误差均远小于 3%（多数在 1e-12 以下或等于 0，见表 3 与上表）；唯一超过 3% 的是**零阶保持接口数据方式的能量闭合残差**（0.07719），它被明确列为负控并给出修复路径（线性接口数据 → 0.005701）。密度投影的点值精度误差（非守恒量）为 0.01881，其量级由源网格分辨率决定，本报告不作为守恒判据。

## 7. 契约与产物

* `benchmarks/verified/cross_solver_coupling/failure_faces.json`：schema `failure_faces/v2`，4 条面记录 + 8 条故障记录，错误模式 5 种：`kind_law_mismatch`, `moment_drift`, `overshoot`, `total_mismatch`, `uncovered_destination`；结构自校验 通过，声明模式与记录模式一致：是
* `benchmarks/verified/cross_solver_coupling/run_manifest.json`：`coupling_clock` 块含 10 个字段（必需 4 个：`coupling_interval_s`, `master_dt_s`, `sync_tolerance_s`, `max_skew_s`），Draft-2020-12 契约校验 通过。
* 复现命令：`python -m benchmarks.verified.cross_solver_coupling.run_benchmark`（确定性，无随机数）。
* 测试面（pytest）：`tests/test_field_mapper_geometry.py`、`tests/test_field_mapper_conservation.py`、`tests/test_field_mapper_errors.py`、`tests/test_clock_sync_clock.py`、`tests/test_clock_sync_subcycling.py`、`tests/test_clock_sync_lyapunov.py`、`tests/test_clock_sync_manifest.py`、`tests/test_cross_solver_coupling_benchmark.py`、`tests/test_cross_solver_coupling_neutrality.py`；其中 `test_cross_solver_coupling_benchmark.py` 直接重跑本 benchmark 要求 10 条判据全通过，并用负控臂（最近单元注入 / 不对齐时钟 / 负阻尼 / 零阶保持）证明每道门都可能失败。

## 8. 审计链

* 环境：python 3.11.6，numpy 2.4.6，Linux-6.6.52-1.7.lns23.loongarch64-loongarch64-with-glibc2.38
* commit：`3f05b56b60da36e8f149aac9bb3f5d1c6f8606ad`；结果文件 sha256：`e9ecdf84ce90610c39cde9c0f364c80e5a59f75444515f28a66ac0f1a1e06f8c`
* 映射算子矩阵摘要（sha256）：`d0663c8f223c54f8d9ad3009179b3db204c6bd9500aada55f7872f8fed76fc5d`
* 源/目标网格摘要：`c4a4498b972683d2d7fa247adcefbee494e877e1a49808386c092679ad48bda9` / `e00192c413fd937662baa46c7d949890a28d45565e31eb0f96f5e1582e695f7e`
* 覆盖最小比例：1；未覆盖目标单元数：0

## 9. 局限与后续

* 合成算例不含任何物理模型：本节点只证明基础设施正确，物理一致性需由各领域求解器仓在各自算例上复验。
* 三维一般多面体裁剪、隐式/迭代式耦合、以及多场强耦合的稳定性理论界未覆盖。
* `partner_model="linear"` 依赖最近两个交换值，本质上仍是显式耦合；强附加质量比场景需要隐式或准牛顿界面（不在本节点范围）。
