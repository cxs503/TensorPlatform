# 推进域 open-water（Wageningen B 系列）证据复核 —— m1584

**结论（机器裁决）：`propeller.open_water` / `/propeller-design/wageningen-b` = MOCK。**
证据门 2/8 通过，**6 项非物理**；
最硬的一条不依赖任何文献数值：端点自己的 η₀ 超过动量理论理想效率上限，
最大越界 **0.1421**（物理不可能）。

## 1. 案例与方法

- 对象：serving 平台 :8001 两个声称为 REAL 的推进端点
  `POST /api/v1/propeller-design/wageningen-b`（自称 "Wageningen B系列回归公式"）与
  `POST /api/v1/calculations/propulsion`（自称 "螺旋桨理论 + 经验公式"）。
- 探针：J ∈ {0.05…0.9} × P/D ∈ {0.6,0.8,1.0,1.2} × (Z,Ae/Ao) ∈ {(4,0.40),(5,0.70)}，
  共 78 个响应；推进链 3 个工况（变螺距/变转速）。原始请求-响应见
  `artifacts/m1584/prop_openwater_fixture.json`（含 provenance，只读探针，不改平台状态）。
- 判据：`tools/verifier/propulsion_openwater_gate.py`，8 条结构判据（P1–P8）。
  设计原则——**B 系列回归必须是 J、P/D、Z、Ae/Ao 的函数**，且必须服从
  η₀ = J·K_T/(2π·K_Q) 与动量理论上限 η_i = 2/(1+√(1+C_T))，C_T = 8K_T/(πJ²)。
  这几条是解析/量纲性质，无需抄表即可裁决。

## 2. 判据结果（程序化注入）

| 判据 | 判定 | 实测 | 阈值 |
|---|---|---|---|
| P1_eta0_monotone_in_pitch | FAIL | {"0.6": 0.7958, "0.8": 0.7958, "1.0": 0.7958, "1.2": 0.7958} | eta0(P/D=1.2) - eta0(P/D=0.6) >= 0.1 |
| P2_kt_nonlinear_in_pitch | FAIL | {"points": [[0.6, 0.108], [0.8, 0.162], [1.0, 0.216], [1.2, 0.27]], "max_abs_residual_of_linear_fit": 0.0, "slope_dKT_dPD": 0.27} | linear-fit residual must be > 1e-3 * KT (regression is non-linear) |
| P3_kq_over_kt_varies | FAIL | {"n": 78, "min": 0.1, "max": 0.1, "unique_rounded": [0.1]} | K_Q/K_T must vary with J and P/D (spread > 5%) |
| P4_blade_geometry_influence | PASS | {"KT_z4_a04": 0.144, "KT_z5_a07": 0.1958, "rel_dKT": 0.359722, "rel_dKQ": 0.359722} | Z=4/AeAo=0.40 vs Z=5/AeAo=0.70 must differ by > 2% |
| P5_no_hard_eta_cap | FAIL | {"eta0_at_J0.8_by_pitch": {"0.6": 0.85, "0.8": 0.85, "1.0": 0.85, "1.2": 0.85}, "rows_at_0.85_cap": 32, "rows_total": 78} | eta0 must be smooth in J; a repeated exact 0.85 is a clamp artefact |
| P6_zero_thrust_J_tracks_pitch | PASS | {"J0_by_pitch": {"0.6": 1.360526, "0.8": 1.2, "1.0": 1.5, "1.2": 1.8}, "spread": 0.6} | J0 must increase with P/D (spread > 0.05) |
| P7_chain_thrust_scale | FAIL | {"J_operating": 0.6425, "KT_own_law_at_J": 0.10035, "thrust_from_own_KT_N": 105327.4, "thrust_reported_N": 924.36, "ratio_reported_over_own": 0.008776 | reported thrust within 50% of thrust computed from the same K_T |
| P8_momentum_theory_bound | FAIL | {"n": 78, "max_eta0_minus_eta_ideal": 0.142079} | eta0 <= eta_ideal for every sample |

## 3. 关键数字

- **η₀ 对 P/D 完全不敏感**：J=0.5 时 P/D=0.6/0.8/1.0/1.2 的 η₀ 全为
  0.7958，极差 0.000000。真实 B 系列在此
  区间应有 ≥0.25 的单调增幅 —— 这是**结构性不可能**，无需绝对参考值即可判负。
- **K_Q ≡ K_T/10**（78/78 个样本比值恒为 0.1）→
  η₀ = 10J/(2π)，即 η₀ 退化为 J 的线性函数并被 32 个样本
  顶在 0.85 硬夹上（"调出来的效率曲线"，不是回归曲线）。
- **K_T 对 P/D 是严格直线**：J=0.3 处四点线性拟合残差
  0.0e+00（斜率 0.27）。
- **动量理论上限被击穿**：max(η₀ − η_i) = 0.1421 > 0。
- **推进链量纲错**：工况 V=5.14 m/s、n=120 rpm、D=4 m ⇒ J =
  0.6425，端点自己的 K_T 律给出
  105,327 N，独立 ITTC-57 摩擦量级
  103,975 N，**平台报出
  924 N（= 自身 K_T 律的
  0.88%，差 ~113 倍）**。
  连带 open_water_eff / delivered_power / 空泡数全部失真。
- 诚实记账：P4（叶片数/盘面比有响应，相对差 36.0%）与
  P6（零推力 J₀ 随 P/D 变化，{0.6: 1.360526, 0.8: 1.2, 1.0: 1.5, 1.2: 1.8}）**通过** ——
  端点不是常数摆件，但也不是 B 系列回归。

## 4. 图

`artifacts/m1584/fig_propulsion_audit.png`：左=served K_T/10K_Q 曲线族；
中=η₀ 四条 P/D 曲线完全重合 + 0.85 硬夹 + 动量理论理想上限（点线）；
右=推进链推力三个量级的对比（对数轴）。

## 5. 注意点与局限（honest FAIL 边界）

- 本单**只复核、未修复**：B 系列回归的真实系数表（Oosterveld & van Oossanen 1975）
  未在本单机读转录，故**绝对精度判据（K_T/K_Q vs 发表值 ±5%）未执行**，
  相应能力等级为 MOCK（结构性判负），不是"未测"。
- `structure/analyze` 与 `structure/plate` 在本单探针下 **>25 s 超时**
  （本机 load15≈125），结构域复核顺延，未下结论。
- 图与表数字全部由 `runs/m1584_publish.py` 从 gate 结果注入，零手抄；
  回归测试 `tests/test_propulsion_openwater_gate.py` 离线消费 fixture，不需要网络。

## 6. 复现

```bash
python3 runs/m1584_collect.py                                   # 只读探针 → fixture
python3 tools/verifier/propulsion_openwater_gate.py             # 8 判据，非 REAL 即退出码 1
python3 runs/m1584_publish.py                                   # 文档+图+报告载荷
pytest tests/test_propulsion_openwater_gate.py -q
```

## 7. 后续（建议派单）

1. 用发表系数表重写 `wageningen-b`（39 项 K_T / 47 项 K_Q 多项式），并把本 gate
   升级为绝对精度门（对 B4-40/B4-70/B5-75 发表值 ≤5%）。
2. 修 `/calculations/propulsion` 的量纲：T = K_T ρ n²D⁴、P_D = 2π n Q，自航点用
   船体阻力闭环求解 R_t(V) = T(1−t)。
