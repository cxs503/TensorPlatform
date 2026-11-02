# m1664 · 敞水桨物理模型在生产批量通道的接线修复 + B 系列绝对量级基准

**Mission** m1664 · **Repo** TensorPlatform · **分支** agent/m1664 · 2026-11-01

## 1. 案例介绍

平台把敞水螺旋桨从替代式升级为叶元-动量（BEM）模型（`backend/services/propeller_open_water.py`，
mission m1393/m1403），但**生产千工况通道 `tools/batch_scan/batch_executor.py::calc_propeller`
从未接线**——它仍执行

```
KT = max(0.05, 0.5 - 0.3J);  KQ = max(0.01, 0.05 + 0.02J);  eta0 = clip(J·KT/(2πKQ), 0.3, 0.85)
```

该式 KQ 随 J **上升**（真实桨扭矩必降）、效率被钳成 0.85 平台、与 P/D、Ae/A0、Z 完全无关。
证据：`tests/test_propeller_open_water.py::TestBatchWrapperWiring` 在本单开工时 **红**（`KeyError: 'model'`）。
即"已合并能力的物理模型在批量通道中不可见"，属接线缺失型验证欠账。

| 参数 | 取值 |
|---|---|
| 几何 | D=5 m, P/D∈{0.8,1.0,1.2}, Ae/A0∈{0.4,0.55,0.7}, Z∈{4,5}, r_hub/R=0.18 |
| 工况 | J∈[0.05,1.10]（22 点 × 18 组几何 = 396 点），ρ=1025 kg/m³ |
| 基准 | `tools/verify/propeller_open_water/reference_bseries.py`（Oosterveld & van Oossanen 1975 的 39/47 项回归表，仓内 clean-room 实现） |

## 2. 怎么做的

1. **接线**：`calc_propeller` 改为装载 `services.propeller_open_water`（包导入失败则按文件路径回退），
   构建 `PropellerGeometry` 后调用 `open_water_point`；历史输出键全部保留，新增
   `model / eta_ideal / torque_knm / induction_converged`。
2. **工况生成器去假域**：旧 `advance_speed_kn~U(8,18)` 与 `D∈[3,8]`、`n∈[1,2.5]rps` 独立采样可生成 J≈3
   （远超零推力进速与 B 系列域 J≤1.5）。改为**由目标 J∈[0.15,1.10] 反推航速**；
   判据 `KT/KQ min=0.0`（为 clamp 量身定制）改为允许负值（风车状态真实存在）。
3. **绝对量级基准**：`tools/verify/propeller_open_water/benchmark_bem_vs_bseries.py` 逐点比对 BEM 与 B 系列；
   统计口径显式剔除零推力点邻域（|KT_ref|<0.03 时相对误差数学发散 = 除零伪影）。
4. 工具链：pytest（26 项）、matplotlib（2 图）、git（单次成果提交）。

## 3. 计算结果

**接线后批量通道**（30 个生成工况）：判据通过 30/30（修复前若跑真实物理会 5/15 失败，
因为旧判据把风车状态判为失败）；J 覆盖 0.30–1.09；样例
`J=0.237, KT=0.2498, KQ=0.0273, η0=0.344, η_ideal=0.443, T=1928 kN`。

**BEM vs B 系列（396 点，装载带 J≤0.9 且 |KT_ref|≥0.03，n=311）**

| 量 | 中位 | 均值 | p95 | 最大 | >15% 占比 |
|---|---|---|---|---|---|
| K_T | 10.6% | 17.2% | 39.9% | 139.0% | **42.1%** |
| K_Q | 13.6% | 15.1% | 36.9% | 83.9% | **39.2%** |
| η_0 | 11.4% | 13.4% | 23.4% | 100.0% | **28.9%** |

分组中位（e_KT）：Ae/A0=0.4 → 6.1%、0.55 → 9.0%、**0.7 → 24.9%**；
J=0.25–0.35 → ~8.8%（最好），J=0.8 → 26.6%（最差）；Z=4 → 10.1%、Z=5 → 11.4%。
平均有符号偏差 +2.0%（近无偏，误差是散布型而非系统性偏移）。
设计带 J∈{0.5,0.7,0.9} 中位：KT 16.8%、KQ 16.2%、η0 8.1%。
全 396 点 η0 ≤ 动盘上界 η_ideal **全部成立**。

## 4. 图

`artifacts/m1664/fig_bem_vs_bseries.png`（KT/KQ/η0 曲线：BEM vs 回归）
`artifacts/m1664/fig_bem_error_heatmap.png`（误差热图 + 分布，含 15% 判据线）

## 5. 注意点

* J 逼近 J0 时 |KT_ref|→0，相对误差发散（原均值 218%、最大 2.8e4%）纯属除零伪影，必须按装载带统计。
* BEM 的偏差主因是**剖面极曲线过简**：单一线化 CL 斜率 + 固定 CD0=0.012，无弯度均值线、
  无 Re 相关阻力极、柱面剖面假设 —— 因此大 Ae/A0（叶宽、剖面失速）与高 J（小攻角、CD0 主导）
  处误差最大，与实测分组完全一致。
* 部分点 `induction_converged=False`（高 J 逆压/负推力区），批量通道原样透出该字段，不静默取近似。
* 本单未修 BEM 物理本身（需 NACA a=0.8 均值线 + Re 极曲线，属独立单）。B 系列回归仅在其有效域内有效。

## 6. 误差判定（是否 <3%）

| 关键量 | 基准 | 误差 | <3%? |
|---|---|---|---|
| 批量通道 vs 平台服务 KT/KQ | 同一实现 | ≤5e-5（round 级） | **是** |
| 判据通过率（30 工况） | 100% | 0 失败 | **是** |
| KT 中位（J≤0.9 装载带） | B 系列回归 | 10.6% | 否 |
| KQ 中位 | 同上 | 13.6% | 否 |
| η0 中位 | 同上 | 11.4% | 否 |
| KT 全点 15% 内比例 | 100% | 57.9% | 否 |
| η0 ≤ η_ideal | 第一性原理上界 | 396/396 成立 | **是** |

**结论（honest）**：接线缺陷已修并锁死（测试绿、双通道零偏差）；绝对精度**未达 15% 全线判据**，
中位在判据内、尾部明显越界（42%）。故 BEM 只能判为"物理自洽 + 中位可用"，
不得判 REAL 精度合格；升级路径见 §5。

## 7. 复用

```bash
python3 -m pytest tests/verify/test_propeller_batch_bem.py tests/test_propeller_open_water.py -q
python3 tools/verify/propeller_open_water/benchmark_bem_vs_bseries.py
```
