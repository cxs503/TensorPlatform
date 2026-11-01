# 优化器目标函数物理保真度复核：KCS 球鼻艏 CMA-ES 的 Ct 目标实为裸摩擦线（证据等级 MOCK）

## ① 案例介绍

**对象**：`optimizer/interfaces/resistance_eval.py::evaluate_resistance` —— 平台 KCS 球鼻艏
形优化（CMA-ES）**唯一目标函数**：`optimization_loop.objective` 直接返回 `evaluate_resistance(...)["Ct"]`，
8 个艏部变形参数的全部适应度都由它给出；`optimizer/REPORT.md` 的“阻力下降 xx%”即建立其上。
该模块文件头自称 `m547 interface mock`，但**从未被任何独立参考复核**，属于典型验证欠账。

**几何与工况（KCS 模型尺度，取自 `optimizer/interfaces/kcs_bow_param.py`）**

| 量 | 值 | 判据 |
|---|---|---|
| L / B / T (m) | 7.278 / 1.019 / 0.342 | L/B=7.142，B/T=2.98 |
| Cb | 0.651 | ∇=Cb·L·B·T=1.6512 m³（@1025 kg/m³ → 1692.46 kg） |
| 设计 Fn / V | 0.26 / 2.1969 m/s | V=Fn·√(gL) |
| 流体 | ρ=1025 kg/m³，ν=1.190e-06 m²/s | 与 mock 同流体，只比方法 |

## ② 怎么做的（方法/流程/关键设置/工具链）

1. **异实现交叉**（而非自洽复算）：正典取 Holtrop-Mennen (1984) 全系数链内核（`backend/api/holtrop1984.py`，provenance: `git:agent/m1553:a5c35436916d2bd40935201836e657db81907ce9`，sha256=7449b33090984ef9）。本单**不重复实现第三次**——报告 1144 已裁决过多分支数值漂移的代价。
2. **七条独立判据**（C1~C7，见下表）：几何恒等式、湿面积夹逼+异实现、量纲一致性、残余阻力量级、
   总阻力误差、物理下界、速度极限/单调性。每条都可被变异体单独判负。
3. **复算工具**：`python3 tools/audit/optimizer_objective_audit.py --write`；漂移闸 `--check`
   （复算结果必须等于入库 JSON，否则非零退出）。
4. 输入全部由程序读取（`KCS_BASELINE` / `generate_geometry` / 正典模块），文中数字由工具在写文档时注入，零手抄。

## ③ 计算结果（程序化注入）

**证据等级判决：MOCK**（7/7 条可判，FAIL=['C3', 'C4', 'C5', 'C6']，WITHHELD=无）

| 判据 | 名称 | 判决 | 关键数值 |
|---|---|---|---|
| C1 | 几何恒等式 Cb·L·B·T / 标定域 | PASS | {"L_B": 7.142296, "B_T": 2.979532, "Cb": 0.651, "nabla_m3": 1.651176, "displacement_kg_at_1025": 1692.455253} |
| C2 | 湿表面积 S_wet（夹逼 + 异实现） | PASS | {"S_mock_m2": 10.012762, "S_canonical_m2": 9.009053, "squeeze_lo_2LT": 4.978152, "squeeze_hi_L_B_2T": 12.394434, "rel_err_pct": 11.141117} |
| C3 | 相关补贴 Ca 量纲一致性 | FAIL | {"Ca_dimensionless": 0.00284, "Ca_force_recorded_N": 0.028433, "implied_coefficient": 1e-06, "Ra_correct_N": 70.330723, "rel_err_pct_of_coefficient": 99.959572, "underestimate_N": 70.30229} |
| C4 | 残余阻力显著性 Cr/Cf（量级仲裁） | FAIL | {"Cr_Cf_mock": 3.3e-05, "Cr_Cf_canonical": 0.334535, "ratio_mock_over_canonical": 9.8e-05} |
| C5 | 总阻力 Ct / Rt vs 正典 Holtrop-1984 | FAIL | {"Ct_mock": 0.002853, "Ct_canonical": 0.005125, "err_pct_Ct": 44.329833, "Rt_mock_N": 70.661455, "Rt_canonical_N": 114.205046, "err_pct_Rt": 38.127555, "gate_pct": 15.0, "tight_pct": 3.0, "under_3pct": false} |
| C6 | 物理下界 Ct ≥ Cf 且 Ct ≥ (1+k)Cf | FAIL | {"Ct_mock": 0.002853, "Cf": 0.002852, "Cf_times_1pk": 0.003381, "Ct_over_Cf": 1.000435, "deficit_pct_vs_1pk": 15.620223} |
| C7 | 速度极限 Rt(V)↑ / Ct(V)↓ | PASS | {"Rt_monotonic_increasing": true, "Ct_monotonic_decreasing": true, "samples": 8} |

关键量对照（KCS 模型，设计点 Fn=0.26）：

| 量 | mock 目标函数 | 正典 Holtrop-1984 | 相对误差 |
|---|---|---|---|
| Ct [-] | 0.00285303 | 0.00512489 | 44.33% |
| Rt [N] | 70.661 | 114.21 | 38.13% |
| S_wet [m²] | 10.013 | 9.0091 | 11.14% |
| Cr/Cf [-] | 3.285e-05 | 0.334535 | 比值 9.821e-05 |
| Ca（无量纲） | 记录为力项 0.02843 N → 隐含系数 1.148e-06 | 0.00283968 | 99.96% |

## ④ 图

见 `docs/verification/figures/optobj_fig1_ct_vs_v.png`（Ct(V) 与摩擦下界）与
`optobj_fig2_components.png`（分量分解，symlog）。

## ⑤ 注意点（坑/局限/适用范围）

- **mock 的 Ct 恒等于 ITTC-57 Cf**：残余阻力项 `0.2·exp(-1.5/Fn^1.2)` 在 Fn=0.26 处给出 Cr≈1e-7，
  兴波项 `exp(-2/Fn²)` 给出 Rw≈1e-11 N，二者被指数形式压在机器意义下的零 —— 目标函数等价于“只有摩擦”。
- **量纲错误**：相关补贴 `Ca=0.006(L+100)^-0.16·S_wet` 把 [m²] 量直接加到 [N] 上，
  使该作用项被低估 70.3 N（系数低 2 个数量级）。
- **影响面**：优化器的每条适应度都偏低约 44.3%，且**对艏部参数的灵敏度完全来自人工 `params` 乘子**
  （`bow_entrance_angle_delta` / `bulb_volume_scale` / `stem_rake_delta` 的线性折扣），
  所以“优化改善 x%”是灵敏度旋钮的读数，不是水动力收益；不得作为 REAL 证据引用。
- **局限**：正典本身也不适用于细长轴对称体（见其模块 docstring 第 4 条），本复核只覆盖 KCS 位移船工况。
- 本单**不修物理**：正典 Holtrop-1984 内核已由 agent/m1553 落地（`backend/api/holtrop1984.py`），
  在优化器侧再写一份必然产生第三次版本漂移。正确修法是把目标函数改为委托正典（已在本单留下工单建议）。

## ⑥ 误差判定

- Ct 相对误差 **44.33%** vs 正典 Holtrop-1984 → **是否 <3%：否**；是否 <15%（平台不可接受门）：**否**。
- Rt 相对误差 **38.13%**（同一判据的力形式）。
- 相关补贴系数误差 **99.96%**（量纲错误，远超门限）。
- Cr/Cf 量级比 **9.821e-05**（要求 0.5~2）。
- 结论：上述四项全部不满足 3%/15% 判据，目标函数证据等级维持 **MOCK**；优化器输出不得进入能力台账的 REAL 行。

## ⑦ 复现

```
python3 tools/audit/optimizer_objective_audit.py --write   # 生成 JSON+MD+图
python3 tools/audit/optimizer_objective_audit.py --check   # 漂移闸（复算==入库）
python3 -m pytest tests/test_optimizer_objective_audit.py -q
```
