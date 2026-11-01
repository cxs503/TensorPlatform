# m1590 — 结构域验证欠账清偿 v1：板格屈曲 / 疲劳 S-N / 总强度端点对闭式与标准基准复核

**席位** strategy（tp-arch-2）· **日期** 2026-11-01 · **仓库** TensorPlatform（分支 `agent/m1590`）
**EDIT_WHITELIST**: `docs/`, `tools/validation/`, `tests/`, `artifacts/`
**平台报告**: `/api/v1/tensor/reports` id=1182 · **commit**: `c85f718`

---

## 1. 为什么选这件事（选题与去重）

按任务要求先做只读扫描：

| 扫描面 | 结果 |
|---|---|
| 平台报告池（`/tensor/reports?limit=20`，id 1134–1157） | 近 20 篇覆盖 稳性 GZ、操纵 MMG、耐波 STF、阻力 Holtrop、绿色航行 EEDI、冰区、基准数据集、能力台账 |
| 上述 20 篇按关键词（结构/屈曲/疲劳/振动/螺旋桨/空泡）过滤 | **命中 0 篇** |
| `guide_tracker_state.json` 缺口 | 仅列 LBM 类算例（cavity_3d / dam_break / forchheimer / kelvin_helmholtz），与结构域无关 |
| 在飞任务 | 1589–1609 共 21 张自找活单在跑，全部为"自主工时"型，未声明结构域 |
| 本席上一张 selfstart 单产出 | 无同域重复 |

结论：**结构域（板格屈曲 / 疲劳 / 板厚屈曲 / 总强度利用率）是当前唯一"端点在跑但从未被外部基准复算"的整域欠账**，且全部为解析公式计算，属轻量任务——符合本机 load15≈125（nproc=128）下的容量纪律。

## 2. 怎么做的（方法 / 工具链 / 关键设置）

不改平台代码，只做**外部复核**（审计模式 A）：

1. **探针**（`tools/validation/probe_struct_fatigue.py`、`probe_structure_endpoints.py`）
   对 `POST /api/v1/plate-stability/plate`、`/api/v1/fatigue/analyze`、`/api/v1/structure/plate`、`/api/v1/structure/analyze` 做单因子（OAT）+ 扫描设计，共 29 + 70 + 11 + 20 = 130 次调用，原始响应逐行落盘 `artifacts/probe_*.json`（边跑边 flush，抗超时）。
   关键设计：疲劳端点用**单应力幅 + 固定 1000 周**反解 N，从响应里**反推** S-N 参数（$C=N\Delta\sigma^m$ 是否恒定），而不是相信自述的 "BS-D / m=3"。
2. **独立参考实现**（`tools/validation/structure_reference.py`，不 import 任何平台代码）
   - 板格屈曲：Timoshenko–Gere 精确能解，对半波数 $m,n$ 取整数极小；夹支取 DNV-RP-C201 Table 6-1 的 $k=6.97$；
   - 极限强度：Faulkner (1975) $\sigma_u/\sigma_y = 2/\beta - 1/\beta^2$；
   - S-N：DNV-RP-C203 曲线 D（空气，$\log a=12.164$，$m_3=3$/$m_5=5$，拐点 $10^7$）与 BS 7608:2014 class D（$2\times10^6$ 周处 $\Delta\sigma=53.4$ MPa）；
   - 损伤：Palmgren-Miner。
3. **判定器**（`tools/validation/validate_structure.py`）逐项输出 served/reference/相对误差/判决，21 项检查、5 项 PASS；同时生成图与表（表内数字全部程序化注入，零手抄）。
4. **回归测试**（`tests/test_structure_validation_m1590.py`，33 条）：一层钉住参考公式本身（无条件成立），一层钉住"被审计端点在 2026-11-01 的实际偏差"——将来修好了测试会失败，强制更新台账。

**复现命令**（约 2 分钟，纯解析计算）：
```bash
python3 tools/validation/probe_struct_fatigue.py artifacts/probe_struct_fatigue.json
python3 tools/validation/probe_structure_endpoints.py artifacts/probe_structure_endpoints.json
python3 tools/validation/validate_structure.py
python3 -m pytest tests/test_structure_validation_m1590.py -q   # 33 passed
```

## 3. 计算结果（vs 基准，逐项）

**全量检查寄存器见 §6 表 1**（21 项，机器生成）。要点：

### 3.1 板格屈曲 `/plate-stability/plate`

| 项 | 服务值 | 基准 | 误差 | 判定 |
|---|---|---|---|---|
| 正方板 SSSS 临界应力（Bryan，$k=4$） | 29.09 MPa | 29.091 MPa | −0.00% | **PASS（真公式）** |
| 纯剪屈曲系数 $k_s$ | 5.794 | 5.784 | +0.17% | **PASS** |
| $a/b=0.5$ 压力方向系数 $k$ | 4.00 | 6.25（精确解） | **−36%** | FAIL |
| 同一块板，token `SSSS` vs `simply_supported` | 4.00 vs 6.25 | 应相同 | **−36%** | FAIL（契约缺陷） |
| 夹支方板 $k$ | 4.00 | 6.97（DNV-RP-C201） | **−42.6%** | FAIL |
| token `CCCC` | 4.00（=SSSS） | 6.97 | −42.6% | FAIL（未知 token 静默降级） |
| 横向压缩系数 $k_b$（$a/b=3$） | 0.444 | 1.235（且**下界 1.0**） | **−64%** | FAIL（低于变分下界，物理不可能） |
| `ultimate_strength` vs Faulkner（18 mm, $\beta=4.50$） | 41.89 MPa | 92.78 MPa | **−54.9%** | FAIL |

诊断：屈曲**容量**用的是真公式（Bryan），误差 0.00%；但 (a) $k$ 被 `max(k,4.0)` 截断，短板/夹支非保守；(b) `edge_condition` 是自由字符串，`CCCC` 未被识别却无报错，`clamped` 与 `simply_supported` 对同一几何给出不同答案；(c) `ultimate_strength` 恒等于 `critical_stress_a`（29/29 行），**完全没有后屈曲储备**，嵌入船体板格时低估 18%–63%（随 $\beta$）。

### 3.2 疲劳 `/fatigue/analyze`

从响应反推的 S-N 参数（表 3）：m=3 段 $C=N\Delta\sigma^3=1.4588\times10^{12}$，即 $\log a=12.164$ —— **这正是 DNV-RP-C203 曲线 D 的常数**；而端点公布的参考应力 `Sc=53.4 MPa` 是 **BS 7608 class D 在 $2\times10^6$ 周**的值（后者对应 $C=3.05\times10^{11}$）。

| 项 | 服务值 | 基准 | 误差 | 判定 |
|---|---|---|---|---|
| 由服务曲线反解 $\Delta\sigma@2\times10^6$ | 90.02 MPa | 53.4 MPa（自己公布的 Sc） | **+68.6%** | FAIL（自相矛盾） |
| 公布参考应力处的寿命 | 9.58e6 周 | 2e6 周 | **+379%** | FAIL |
| $\Delta\sigma=70$ MPa 寿命 vs BS 7608 D | 4.25e6 | 8.88e5 | **+379%**（乐观 4.79×） | FAIL |
| $\Delta\sigma=50$ MPa 寿命 vs DNV D（m=5 段） | 2.10e7 | 1.29e7 | **+62.6%** | FAIL |
| 拐点位置 | 4.84e6 周 / 67.1 MPa | 1e7 周 / 52.65 MPa | −51.6% | FAIL |
| 拐点处寿命跳变 | ×1.62 | ×1.00（必须连续） | +62% | FAIL |
| Miner 求和（示例工况） | 0.291661 | 0.291661 | 0.00% | **PASS** |

诊断：**算法真（Miner + 双折线 S-N），参数串标**——m=3 段取 DNV D 的 $\log a$，拐点锚点却按 BS 7608 的 Sc 折算（$67.07 \approx 53.4\times2^{1/3}$，即把它当成 1e6 周参考值），两者组合后拐点落在 $4.84\times10^6$ 周/67.1 MPa，**既不满足 DNV（1e7/52.65）也不满足 BS 7608（1e7/31.2）**，且曲线在拐点不连续（寿命跳 +62%）。净效应：相对 BS 7608 D 寿命乐观 4.79×，相对 DNV D 在低应力段乐观 1.63×——**非保守方向**，用于舱口围/舷侧等疲劳热点会低估损伤。
另：BS-B / BS-C / BS-F 三个等级的 m=3 段 $C$ 不恒定（局部值分歧，见 `anomalous_classes`），参数出处无法从公开表核对 → 记 **WITHHELD**。

### 3.3 `/structure/plate` 与 `/structure/analyze`

| 项 | 观察 | 判定 |
|---|---|---|
| `/structure/plate` 屈曲容量（$b_{eff}=3\times$ 肋距，由 $(t/s)^2$ 标定反推） | 51.50 vs 51.72 MPa | **PASS（−0.43%）** |
| 同上，施加应力 | `actual_stress ≡ 0.5 σ_y`（3 种屈服强度全部如此） | **MOCK**：与载荷无关，SF 只反映板格细长比 |
| `/structure/analyze` 利用率 | `utilization ≡ max_stress/allowable`（4.72e6 Pa ÷ 141.0 MPa = 33490） | **单位串接**，量纲差 $10^6$，利用率虚高 6 个数量级 |
| 同上，对吃水 $T$ 的敏感性 | $T=8/10.5/13/16$ m → `max_stress` 完全相同 | **FAIL**：该路径的剖面模数不分 $T$，总强度物理不成立 |
| 同上，`fatigue_life` | 恒为 0.0 | **STUB**：疲劳未接入强度链 |

## 4. 注意点（坑 / 局限 / 适用范围）

- **先算量纲再判错**：我第一版参考实现把 $\sigma_{cr}$ 写成 $k\pi^2D/b^2$，漏了除 $t$（该式量纲是 N/m 不是 Pa），导致 21 项全 FAIL、误差 6500%。是 `test_sigma_cr_matches_bryan_closed_form` 把它揪住的——**参考实现本身必须先被测试钉住**，否则审计就是自证。
- `edge_condition` 是自由字符串，服务端不做校验，所以"同一物理条件两个 token 两个答案"这类缺陷只能靠**交叉扫描**发现，单点调用看不出来。
- 结构端点的**内部几何假设**（`/structure/plate` 的板格宽度 = 3×肋距）是从 $(t/s)^2$ 标定关系反推的，未读到源码确认；结论对该假设不敏感（因为容量项在 $t,s$ 全部 4 档上都一致到 0.43%）。
- 疲劳只对 class D 做定量判定（BS 7608 与 DNV-RP-C203 的 D 级值均为公开确证）；B/C/F 等级的 $C$ 与公布 Sc 无法用同一张表核对，记为 WITHHELD，不做误差断言。
- 未覆盖：`/structure/analyze` 的 `max_stress` 具体公式（只做了 OAT 敏感性，未反解）、`/vibration/predict`（OpenAPI 无 requestBody schema，无法构造合法请求）、`/fea/*`、`/potential-*`。
- 本机 load15≈125，全程只跑解析式与绘图，未起任何重算力进程。

## 5. 误差判定（显式回答"是否 <3%"）

| 关键量 | 基准 | 误差 | <3%？ |
|---|---|---|---|
| 板格屈曲临界应力（SSSS 方板） | Bryan/Timoshenko 闭式 | −0.00% | **是** |
| 剪屈曲系数 $k_s$ | $5.34+4/\alpha^2$ | +0.17% | **是** |
| Miner 累积损伤 | 由服务 N 独立复算 | 0.00% | **是** |
| `/structure/plate` 屈曲容量 | Bryan（$b_{eff}=3s$） | −0.43% | **是** |
| `/structure/analyze` 利用率 | `max_stress/allowable`（量纲对齐后） | 0.00%（说明就是原始比值） | 否（量纲本身错 $10^6$） |
| 短板屈曲系数 $k(a/b=0.5)$ | 精确解 6.25 | −36.0% | **否** |
| 夹支方板 $k$ | 6.97 | −42.6% | **否** |
| 横向压缩 $k_b$ | 1.235（下界 1.0） | −64.0% | **否** |
| 极限强度 | Faulkner | −54.9% | **否** |
| 疲劳寿命（vs BS 7608 D） | 53.4 MPa@2e6 | +379%（4.79×） | **否** |
| 疲劳寿命（vs DNV D，低应力段） | 1e7/52.65 MPa | +62.6% | **否** |
| S-N 拐点位置 | 1e7 周 | −51.6% | **否** |
| S-N 拐点连续性 | 必须连续 | 寿命跳 +62% | **否** |

**总结论**：结构域 4 个端点中，**屈曲容量公式与剪屈曲、Miner 求和是真实现（PASS）**；但 16/21 项判定 FAIL，失效集中在 **(i) 系数/边界的硬截断与非保守取值、(ii) 疲劳参数跨标准串标（BS 参考应力 + DNV 斜率常数）、(iii) 单位/量纲串接、(iv) `ultimate_strength` 与 `fatigue_life` 属 MOCK/STUB**。按平台标准（>15% 不可接受），结构域当前**不可用于疲劳寿命与极限强度的定量决策**，可用于板格弹性屈曲容量（SSSS、$a/b\ge1$）。

### 建议的修复顺序（供后续任务领）
1. 疲劳：统一到单一标准（建议 DNV-RP-C203 D，$\log a=12.164$，拐点 1e7/52.65，m=5 锚点 $10^{15.606}$），并在响应里返回 `sn_standard` 与拐点；同时补 BS 7608 参数表——**最高优先级，非保守且影响疲劳寿命 4.79×**。
2. `plate-stability`：$k$ 去掉 4.0 地板，`edge_condition` 改枚举并校验（`SSSS/CCCC`），夹支取 6.97，$k_b$ 用 $[1+(b/a)^2]^2$，`ultimate_strength` 接 Faulkner。
3. `structure/analyze`：应力统一 Pa→MPa 再算利用率；剖面模数接 $T$；`fatigue_life` 要么接入疲劳模块要么返回 `null` 而非 0.0。
4. 契约层：`/structure/plate` 的 `actual_stress` 应由载荷输入算出，缺输入时返回 `WITHHELD` 而不是 0.5σ_y。

## 6. 数据表与图（程序化注入）

图：`artifacts/figures/`
- `fig1_plate_buckling_coefficients.png` — 屈曲系数 vs 长宽比：服务值 vs 精确解（左：压力方向；右：横向，含 $k\ge1$ 变分下界）
- `fig2_ultimate_strength.png` — 极限强度 vs 板格细长比 $\beta$：服务值（=$\sigma_{cr}$）vs Faulkner
- `fig3_sn_curves.png` — 四条服务 S-N 曲线 vs BS 7608 D / DNV-RP-C203 D
- `fig4_life_ratio_and_utilization.png` — 疲劳寿命比（服务/标准）与 `/structure/analyze` 利用率量纲

原始证据：`artifacts/probe_struct_fatigue.json`、`artifacts/probe_structure_endpoints.json`、`artifacts/structure_validation.json`（21 项检查全字段）。

### Table 1 — closed-form check register

| ID | group | quantity | served | reference | unit | err % | tol % | verdict |
|---|---|---|---|---|---|---|---|---|
| P1 | plate-stability | sigma_cr,a (SSSS, a/b=1, t=15mm) | 29.09 | 29.09 | MPa | -0 | 3 | PASS |
| P2 | plate-stability | k(alpha) simply supported (token 'SSSS'), worst of sweep | 4 | 6.25 | - | -36 | 3 | FAIL |
| P2b | plate-stability | same panel, token 'SSSS' vs 'simply_supported' | 4 | 6.251 | - | -36.01 | 3 | FAIL |
| P3 | plate-stability | k clamped edges, a/b=1 | 4 | 6.97 | - | -42.61 | 3 | FAIL |
| P4 | plate-stability | edge_condition='CCCC' recognised (k should be ~6.97) | 4 | 6.97 | - | -42.61 | 3 | FAIL |
| P5 | plate-stability | k_b (transverse compression), worst of sweep | 0.4441 | 1.235 | - | -64.03 | 3 | FAIL |
| P6 | plate-stability | k_s shear, worst of sweep | 5.794 | 5.784 | - | 0.17 | 3 | PASS |
| P7 | plate-stability | ultimate strength vs Faulkner (worst of sweep) | 41.89 | 92.78 | MPa | -54.85 | 3 | FAIL |
| P8 | plate-stability | safety factor at zero load (should be inf, capped?) | 10 |  | - |  | 0 | FAIL |
| F1 | fatigue | stress range at 2e6 cycles implied by served curve, BS-D | 90.02 | 53.4 | MPa | 68.58 | 3 | FAIL |
| F2 | fatigue | life at published Sc (BS-D), cycles | 9580231 | 2.000e+06 | cycles | 379 | 3 | FAIL |
| F3 | fatigue | life at dsigma=70 MPa vs BS 7608 class D | 4253100 | 887891 | cycles | 379 | 3 | FAIL |
| F4 | fatigue | life at dsigma=50 MPa vs DNV-RP-C203 D (m=5 branch) | 20996648 | 12916653 | cycles | 62.55 | 3 | FAIL |
| F5 | fatigue | knee position, cycles (served curve) | 4836145 | 1.000e+07 | cycles | -51.64 | 3 | FAIL |
| F5b | fatigue | life jump at the served S-N knee | 1.623 | 1 | x | 62.3 | 0.03 | FAIL |
| F6 | fatigue | Miner sum recomputed from served lives | 0.2917 | 0.2917 | - | 0 | 0.001 | PASS |
| S1 | structure/plate | buckling capacity of the panel (b_eff=3*frame_spacing deduced) | 51.5 | 51.72 | MPa | -0.43 | 3 | PASS |
| S2 | structure/plate | applied stress / yield stress (should vary with load, not be constant) | 0.5 |  | - |  | 0 | FAIL |
| S3 | structure/analyze | utilization vs max_stress/allowable_stress (Pa vs MPa) | 2.495e+04 | 2.495e+04 | - | -0 | 3 | PASS |
| S4 | structure/analyze | max_stress sensitivity to depth T (T=8..16 m) | 1 | 4 | distinct | -75 | 0 | FAIL |
| S5 | structure/analyze | fatigue_life returned to the caller | 0 |  | years |  | 0 | FAIL |


### Table 2 — plate-stability sweep (served vs exact)

| a/b | t mm | edge token | k served | k ref(SSSS) | k_b served | k_b ref | k_s served | k_s ref | sigma_cr,a | tau_cr | ult served | ult Faulkner | beta |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.5 | 15 | SSSS | 4 | 6.25 | 16 | 25 | 21.35 | 6.34 | 29.09 | 155.3 | 29.09 | 78.92 | 5.404 |
| 0.5 | 15 | CCCC | 4 | 6.25 | 16 | 25 | 21.35 | 6.34 | 29.09 | 155.3 | 29.09 | 78.92 | 5.404 |
| 0.5 | 15 | clamped | 6.251 | 6.25 | 25 | 25 | 21.35 | 6.34 | 45.46 | 155.3 | 45.46 | 78.92 | 5.404 |
| 0.5 | 15 | simply_supported | 6.251 | 6.25 | 16 | 25 | 21.35 | 6.34 | 45.46 | 155.3 | 45.46 | 78.92 | 5.404 |
| 1 | 15 | SSSS | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 29.09 | 68 | 29.09 | 78.92 | 5.404 |
| 1 | 15 | CCCC | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 29.09 | 68 | 29.09 | 78.92 | 5.404 |
| 1 | 15 | clamped | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 29.09 | 68 | 29.09 | 78.92 | 5.404 |
| 1 | 15 | simply_supported | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 29.09 | 68 | 29.09 | 78.92 | 5.404 |
| 2 | 15 | SSSS | 4 | 4 | 0.9996 | 1.562 | 6.35 | 6.34 | 29.09 | 46.18 | 29.09 | 78.92 | 5.404 |
| 2 | 15 | CCCC | 4 | 4 | 0.9996 | 1.562 | 6.35 | 6.34 | 29.09 | 46.18 | 29.09 | 78.92 | 5.404 |
| 2 | 15 | clamped | 6.251 | 4 | 1.562 | 1.562 | 6.35 | 6.34 | 45.46 | 46.18 | 45.46 | 78.92 | 5.404 |
| 2 | 15 | simply_supported | 4 | 4 | 1.562 | 1.562 | 6.35 | 6.34 | 29.09 | 46.18 | 29.09 | 78.92 | 5.404 |
| 3 | 15 | SSSS | 4 | 4 | 0.4441 | 1.235 | 5.794 | 5.784 | 29.09 | 42.14 | 29.09 | 78.92 | 5.404 |
| 3 | 15 | CCCC | 4 | 4 | 0.4441 | 1.235 | 5.794 | 5.784 | 29.09 | 42.14 | 29.09 | 78.92 | 5.404 |
| 3 | 15 | clamped | 11.11 | 4 | 1.235 | 1.235 | 5.794 | 5.784 | 80.81 | 42.14 | 80.81 | 78.92 | 5.404 |
| 3 | 15 | simply_supported | 4 | 4 | 1.235 | 1.235 | 5.794 | 5.784 | 29.09 | 42.14 | 29.09 | 78.92 | 5.404 |
| 1 | 10 |  | 4 | 4 | 4 | 4 | 9.349 | 9.34 | 12.93 | 30.22 | 12.93 | 54.4 | 8.106 |
| 1 | 12 |  | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 18.62 | 43.52 | 18.62 | 64.43 | 6.755 |
| 1 | 15 |  | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 29.09 | 68 | 29.09 | 78.92 | 5.404 |
| 1 | 18 |  | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 41.89 | 97.92 | 41.89 | 92.78 | 4.503 |
| 1 | 20 |  | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 51.72 | 120.9 | 51.72 | 101.7 | 4.053 |
| 1 | 25 |  | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 80.81 | 188.9 | 80.81 | 122.6 | 3.242 |
| 1 | 30 |  | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 116.4 | 272 | 116.4 | 141.8 | 2.702 |
| 1 | 15 |  | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 29.09 | 68 | 29.09 | 78.92 | 5.404 |
| 1 | 15 |  | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 29.09 | 68 | 29.09 | 78.92 | 5.404 |
| 1 | 15 |  | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 29.09 | 68 | 29.09 | 78.92 | 5.404 |
| 1 | 15 |  | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 29.09 | 68 | 29.09 | 78.92 | 5.404 |
| 1 | 15 |  | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 29.09 | 68 | 29.09 | 78.92 | 5.404 |
| 1 | 15 |  | 4 | 4 | 4 | 4 | 9.35 | 9.34 | 29.09 | 68 | 29.09 | 78.92 | 5.404 |


### Table 3 — S-N parameters extracted from the served response

| curve | Sc (pub) | C = N·S^3 | log10 C | sigma@2e6 implied | N at published Sc | N knee | sigma knee |
|---|---|---|---|---|---|---|---|
| BS-B | 101.2 | 3908408625000 | 12.59 | 125 | 3.771e+06 | 2.283e+07 | 55.53 |
| BS-C | 78.2 | 2811898125000 | 12.45 | 112 | 5.880e+06 | 1.104e+07 | 63.39 |
| BS-D | 53.4 | 1458813375000 | 12.16 | 90.02 | 9.580e+06 | 4.836e+06 | 67.07 |
| BS-F | 33.6 | 576766000000 | 11.76 | 66.07 | 1.520e+07 | 2.385e+06 | 62.3 |


### Table 4 — /structure/plate OAT sweep

| varied | value | t mm | s m | sigma_y | sigma_cr served | sigma_cr ref | applied | applied/yield | SF |
|---|---|---|---|---|---|---|---|---|---|
| plate_thickness | 0.01 | 10 | 0.6 | 235 | 22.9 | 22.99 | 117.5 | 0.5 | 0.19 |
| plate_thickness | 0.015 | 15 | 0.6 | 235 | 51.5 | 51.72 | 117.5 | 0.5 | 0.44 |
| plate_thickness | 0.02 | 20 | 0.6 | 235 | 91.6 | 91.94 | 117.5 | 0.5 | 0.78 |
| plate_thickness | 0.03 | 30 | 0.6 | 235 | 206 | 206.9 | 117.5 | 0.5 | 1.75 |
| frame_spacing | 0.6 | 15 | 0.6 | 235 | 51.5 | 51.72 | 117.5 | 0.5 | 0.44 |
| frame_spacing | 0.9 | 15 | 0.9 | 235 | 22.9 | 22.99 | 117.5 | 0.5 | 0.19 |
| frame_spacing | 1.2 | 15 | 1.2 | 235 | 12.9 | 12.93 | 117.5 | 0.5 | 0.11 |
| frame_spacing | 2.4 | 15 | 2.4 | 235 | 3.2 | 3.23 | 117.5 | 0.5 | 0.03 |
| yield_stress | 235 | 15 | 0.6 | 235 | 51.5 | 51.72 | 117.5 | 0.5 | 0.44 |
| yield_stress | 315 | 15 | 0.6 | 315 | 51.5 | 51.72 | 157.5 | 0.5 | 0.33 |
| yield_stress | 355 | 15 | 0.6 | 355 | 51.5 | 51.72 | 177.5 | 0.5 | 0.29 |


### Table 5 — /structure/analyze OAT sweep

| varied | value | max_stress | allowable | utilization | ratio max/allow | fatigue_life | status |
|---|---|---|---|---|---|---|---|
| L | 90 | 3.518e+06 | 141 | 2.495e+04 | 2.495e+04 | 0 | 需校核 |
| L | 120 | 4.722e+06 | 141 | 3.349e+04 | 3.349e+04 | 0 | 需校核 |
| L | 180 | 7.176e+06 | 141 | 5.089e+04 | 5.089e+04 | 0 | 需校核 |
| L | 240 | 9.692e+06 | 141 | 6.873e+04 | 6.873e+04 | 0 | 需校核 |
| L | 360 | 1.491e+07 | 141 | 1.057e+05 | 1.057e+05 | 0 | 需校核 |
| B | 24 | 8.900e+06 | 141 | 6.312e+04 | 6.312e+04 | 0 | 需校核 |
| B | 30 | 7.176e+06 | 141 | 5.089e+04 | 5.089e+04 | 0 | 需校核 |
| B | 40 | 5.452e+06 | 141 | 3.866e+04 | 3.866e+04 | 0 | 需校核 |
| B | 50 | 4.417e+06 | 141 | 3.133e+04 | 3.133e+04 | 0 | 需校核 |
| T | 8 | 7.176e+06 | 141 | 5.089e+04 | 5.089e+04 | 0 | 需校核 |
| T | 10.5 | 7.176e+06 | 141 | 5.089e+04 | 5.089e+04 | 0 | 需校核 |
| T | 13 | 7.176e+06 | 141 | 5.089e+04 | 5.089e+04 | 0 | 需校核 |
| T | 16 | 7.176e+06 | 141 | 5.089e+04 | 5.089e+04 | 0 | 需校核 |
| D | 12 | 1.276e+07 | 141 | 9.048e+04 | 9.048e+04 | 0 | 需校核 |
| D | 16 | 7.176e+06 | 141 | 5.089e+04 | 5.089e+04 | 0 | 需校核 |
| D | 20 | 4.593e+06 | 141 | 3.257e+04 | 3.257e+04 | 0 | 需校核 |
| D | 24 | 3.189e+06 | 141 | 2.262e+04 | 2.262e+04 | 0 | 需校核 |
| displacement | 2.000e+04 | 3.344e+06 | 141 | 2.372e+04 | 2.372e+04 | 0 | 需校核 |
| displacement | 4.500e+04 | 7.176e+06 | 141 | 5.089e+04 | 5.089e+04 | 0 | 需校核 |
| displacement | 9.000e+04 | 1.407e+07 | 141 | 9.981e+04 | 9.981e+04 | 0 | 需校核 |
