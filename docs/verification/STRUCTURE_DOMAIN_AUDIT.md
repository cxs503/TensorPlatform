# 结构域 4 端点独立复核：全判 MOCK（22 条判据 / 9 条决定性失败）

本文件由 `tools/structure_audit/audit.py` 的报告 JSON 渲染（数字零手抄）。原始数据：
`artifacts/structure_audit/structure_audit_report.json`、`live_records.json`、`structure_audit_report.md`；
图 `fig1_plate_buckling_vs_aspect.png` / `fig2_fatigue_life_vs_stress_range.png` / `fig3_utilization_magnitude.png`。

## 0. 结论先行

用**与平台实现完全无关**的闭式解与规范公式，对结构域 4 个此前从未被审计的端点做独立复核：

| `/fea/plate-buckling` | **MOCK** | 4 | 4 | 0 | PB1, PB2, PB7 |
| `/structure/analyze` | **MOCK** | 2 | 4 | 0 | SA1, SA2, SA6 |
| `/structure/plate` | **MOCK** | 1 | 2 | 0 | SP1 |
| `/structure/fatigue` | **MOCK** | 3 | 2 | 0 | SF2, SF5 |

判据统计（共 22 条）：PASS 10 / FAIL 12 /
WITHHELD 0，其中**决定性失败 9 条**——一条失败即可定级 MOCK。
阳性对照（同一探针计划、同一套判据、解析实现 `reference_candidate`）四端点全 REAL、非循环判据 0 失败，
证明判据不过严（第 3 节）。

**误差判定（逐项，显式回答"是否 <3%"）**：9 条决定性失败项误差跨度 11.9 % – 1×10⁶ 倍，逐项见第 2 节；
**没有一项 <3 %，也仅 1 项 <15 %**（SP3=11.9 %，溯源为 `safety_factor` 被四舍五入到两位小数），
其余 8 项远大于平台 15 % 的不可接受门。故本域精度判据整体不满足。

## 1. 方法与工具链

- 探针 `tools/structure_audit/probe.py::PROBE_PLAN`：4 端点 27 次调用（9+7+4+7），含 α=a/b 扫掠、
  t / b / E 缩放律、三档船长（150/230/320 m）+ 散货船型、两档屈服应力。
- 基准 `tools/structure_audit/baselines.py`：Timoshenko & Gere (1961) Eq.9-3 薄板屈曲；
  IACS UR S11 §5.2.1 波浪弯矩 + UR S7.1.1 最小中剖面模数；DNV-RP-C203 (2016) Table 2-1 空气 S-N 双折线；
  Palmgren-Miner 线性损伤。
- 判据 `tools/structure_audit/criteria.py`：**纯函数**，只吃 (输入, 输出) 记录、不触网，同一代码同时打线上端点与解析对照。
  `decisive=True` = 精确代数恒等式 / 精确缩放律 / 量纲一致性，正确实现必须复现到舍入误差。
- 端点时延（本机 load15≈125，重活端点排队；遵容量纪律，探针点到为止）：

| endpoint | calls | min s | max s | total s |
|---|---|---|---|---|
| `/fea/plate-buckling` | 9 | 0.004 | 0.014 | 0.1 |
| `/structure/analyze` | 7 | 0.004 | 0.005 | 0.0 |
| `/structure/plate` | 4 | 0.004 | 0.005 | 0.0 |
| `/structure/fatigue` | 7 | 0.004 | 0.005 | 0.0 |

## 2. 判据全表（程序化注入）

| id | endpoint | verdict | decisive | measured | criterion | reference |
|---|---|---|---|---|---|---|
| PB1 | `/fea/plate-buckling` | **FAIL** | yes | 0.524376 rel | sigma_cr equals the Timoshenko thin-plate solution k(a/b) pi^2 D /(b^2 t) | Timoshenko & Gere 1961 Eq.9-3 |
| PB2 | `/fea/plate-buckling` | **FAIL** | yes | 0.524376 rel | the a/b dependence survives: sigma(0.4)/sigma(4.0) = k(0.4)/k(4.0) = 2.10 | Timoshenko & Gere 1961 Eq.9-3 |
| PB3 | `/fea/plate-buckling` | PASS | yes | 0 rel | sigma_cr scales as t^2 when the plating thickness doubles | D ~ t^3 in Eq.9-3 => sigma_cr ~ t^2 |
| PB4 | `/fea/plate-buckling` | PASS | yes | 0 rel | sigma_cr scales as b^-2 when plate and span are both doubled | sigma_cr = k pi^2 D/(b^2 t) |
| PB5 | `/fea/plate-buckling` | PASS | yes | 0 rel | sigma_cr scales linearly with E | D = E t^3/(12(1-nu^2)) |
| PB6 | `/fea/plate-buckling` | PASS | - | 0 count | the reported half-wave count is the argmin m of the k(m) family | Timoshenko & Gere 1961 Eq.9-3 |
| PB7 | `/fea/plate-buckling` | **FAIL** | yes | 8 count_zero | critical_load is a positive edge force proportional to sigma_cr * b * t | dimensional analysis (stress x width x thickness) |
| PB8 | `/fea/plate-buckling` | **FAIL** | - | 0.84585 rel | safety_margin = sigma_y / sigma_cr - 1 (classical structural margin) | Timoshenko & Gere 1961 (margin convention) |
| SA1 | `/structure/analyze` | **FAIL** | yes | 999999 rel | utilization = sigma/sigma_allow evaluated in one common unit | dimensional analysis |
| SA2 | `/structure/analyze` | **FAIL** | yes | 1.3882 log10_ratio | hull girder stress within a factor 3 of the IACS S11+S7 rule baseline | IACS UR S11 5.2.1 / S7.1.1 |
| SA3 | `/structure/analyze` | PASS | yes | 0.543752 rel_spread | max_stress responds to the section properties in the request | physics of hull girder bending (sigma = M/W) |
| SA4 | `/structure/analyze` | **FAIL** | - | 7 count | fatigue_life is finite and strictly positive for a loaded structure | Palmgren-Miner damage accumulation |
| SA6 | `/structure/analyze` | **FAIL** | yes | 0 rel_change | max_stress responds to plate_thickness 12 mm -> 30 mm (sigma = M/W) | hull girder beam theory |
| SA5 | `/structure/analyze` | PASS | - | 0 count | the status label follows the reported utilization (>1 => needs check) | internal consistency |
| SP1 | `/structure/plate` | **FAIL** | yes | 0.891992 rel | panel critical stress matches k=4 plate buckling (steelmanned by a yield cap) | Timoshenko & Gere 1961 Eq.9-3 + yield criterion |
| SP2 | `/structure/plate` | PASS | yes | 0.0238095 rel | critical stress scales as t^2 when plating goes 6 mm -> 12 mm (span fixed) | sigma_cr ~ t^2 |
| SP3 | `/structure/plate` | **FAIL** | - | 0.119048 rel | safety_factor is a consistent ratio of the reported pair and agrees with status | internal consistency |
| SF1 | `/structure/fatigue` | PASS | yes | 0.338028 rel_spread | the fatigue stress range is derived from hull geometry, not a constant | stress-transfer physics |
| SF2 | `/structure/fatigue` | **FAIL** | yes | 420930 cycles_per_year | life reproducible from the endpoint's own range and its named DNV S-N curve | DNV-RP-C203 (2016) Table 2-1 + Miner |
| SF3 | `/structure/fatigue` | PASS | - | 0.073888 rel | life ratio between two stress ranges follows the named DNV S-N curve | DNV-RP-C203 (2016) Table 2-1 |
| SF5 | `/structure/fatigue` | **FAIL** | yes | 0 rel_spread | at fixed yield stress the fatigue range still follows hull geometry | stress-transfer physics |
| SF4 | `/structure/fatigue` | PASS | - | 0 count | status is 合格 iff calculated_life >= design_life | internal consistency |

### 关键取证

- **PB1/PB2 板格屈曲**：`critical_stress` 在 α∈[0.4,4.0] 恒为 75.92 MPa；α=1、α=4 恰合理论（误差 0.00 %），
  α=0.4 应为 159.62 MPa → 误差 **52.44 %**；σ(0.4)/σ(4.0) 实测 1.0000 vs 理论 2.1025
  → 误差 **52.44 %**。即只实现了 k≡4，丢掉 k(α)=min_m(m b/a+a/(m b))² 的极值化。
- **PB3/PB4/PB5 缩放律**：t²、b⁻²、E 三项**精确通过**（误差 0.00 %）→ 公式骨架正确、缺陷单点，
  "修复成本极低"是可证判断而非猜测。
- **PB7 边载**：9 样本中 8 个 `critical_load`=0，最小非零 0.01 kN，而该量级应为 σ_cr·b·t≈1.09×10⁻³ kN
  → 字段按 kN 四舍五入两位小数后被抹平。
- **PB8 裕度**：`safety_margin` 恒 0.323 = σ_cr/σ_y（实为应力比），经典裕度 σ_y/σ_cr−1=2.095 → 误差 **84.59 %**。
- **SA1 量纲**：`utilization` 与"Pa/MPa 混算"完全吻合，与同量纲计算相差**恰好 10⁶ 倍**（rel_err=999999）；
  由物理可容许性反解：`max_stress` 只能按 Pa 读（6.99 MPa）、`allowable_stress` 只能按 MPa 读（141 MPa）。
- **SA2 船体梁应力**：反解 σ=6.989 MPa；IACS S11/S7 基准 σ_ref=110.0 MPa
  （W_min=23.391 m³）→ 比值 0.0635，**偏低 93.65 %**，是 3 倍可行带下界的 1/5.2。
- **SA6 剖面响应**：板厚 12→30 mm（2.5×）`max_stress` 零变化（要求 ≥20 %）；同批样本该量对 L 有响应（4 个不同值），
  缺的是"剖面模数/板厚"通道而非整体硬编码。
- **SP1/SP2 板格强度**：端点 51.5 MPa vs k=4 闭式解 474.5 MPa（屈服封顶的最有利读法 235 MPa）
  → 误差 **78.09 %**（对未封顶解为 9.2 倍，最坏样本 89.20 %）；
  但 t 6→12 mm 强度比 3.90 vs 理论 4.00（误差 2.38 %）→ t² 指数正确、系数偏低 9.2 倍。
- **SF5 疲劳应力幅**：固定 σ_y 组内 `stress_range` 恒定，range/σ_y 恒为 0.3（235→70.5、315→94.5、355→106.5）
  → 该幅值不是波浪诱导应力幅，而是屈服应力的 30 %。
- **SF2 寿命标定**：用端点**自己命名**的 DNV-C 曲线反解年循环数 420930 次/年，
  5–12 s 遭遇周期对应 [2.63e+06, 6.31e+06] 次/年 → 寿命高估 6.2–15.0 倍。
- **SF3 正面证据**：两档应力幅寿命比 2.415 vs DNV-C 曲线 2.589，误差 7.39 %（容差 10 %）→
  疲劳端点的 S-N 内核真实，缺陷在输入端（SF5）与循环率标定（SF2）。**并非整条链路都是假的**。

## 3. 阳性对照（判据不败的证明）

同一探针计划、同一 22 条判据，跑解析实现 `tools/structure_audit/reference_candidate.py`
（Timoshenko-Gere 屈曲 + 箱形梁剖面模数 + DNV-C + Miner；几何/厚度/屈服应力全部参与计算）：

| PB1 | PASS | independent |
| PB2 | PASS | independent |
| PB3 | PASS | independent |
| PB4 | PASS | independent |
| PB5 | PASS | independent |
| PB6 | PASS | independent |
| PB7 | PASS | independent |
| PB8 | PASS | independent |
| SA1 | PASS | independent |
| SA2 | PASS | independent |
| SA3 | PASS | independent |
| SA4 | PASS | independent |
| SA6 | PASS | independent |
| SA5 | PASS | independent |
| SP1 | PASS | independent |
| SP2 | PASS | independent |
| SP3 | PASS | independent |
| SF1 | PASS | independent |
| SF2 | PASS | independent |
| SF3 | PASS | independent |
| SF5 | PASS | independent |
| SF4 | PASS | independent |

**非循环判据 0 失败**（`unexpected_failures = []`），四端点 REAL。
故线上失败可归因于端点实现。SA2 的基准（IACS 公式）在对照侧由**箱形梁力学**独立给出，对照只通过"应力量级合理"，非同源。

## 4. 局限与适用范围

- 断网无 CSR/B 系列试验数据：全部基准为闭式解析或规范公式。S11/S7 是**规范最小值**，与真实设计船实测有 ±30 % 量级差异，
  故 SA2 取 3 倍可行带（宽松），不放 3 %。
- 板格判据只覆盖 `boundary=simply_supported, load_type=compression`（端点默认）；其它边界/载荷未判定、不声称。
- `/structure/plate` 单次 23 s，探针只取 4 点（受负载约束）；t² 关系与 9.2 倍系数偏差已足以定级。
- 端点源码在 `ship-performance-platform-incoming`，不在本单白名单仓：本单只出**判定 + 可复现判据 + 修单**，不改端点。
- SF3 容差 10 %：端点输出仅 3 位有效数字（28.5 / 11.8），量化本身已占约 1 %。
- SP3 的 11.9 % 失败很可能只是两位小数舍入（0.02 vs 0.0179），故列为非决定性。

## 5. 复现

```
python3 -m tools.structure_audit.audit --live    # 线上探针 + 判级 + 图（约 2 min，重活端点排队）
python3 -m tools.structure_audit.audit --cached  # 离线确定性复算（CI 用）
python3 -m tools.structure_audit.audit --control # 只跑阳性对照，应 0 意外失败
python3 -m pytest tests/test_structure_audit.py -q   # 14 passed
```
