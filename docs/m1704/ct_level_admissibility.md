# m1704 · Ct 级判据门：把 12 条阻力参考行从「无判据」恢复成「可对标」

**任务** tp-doc-2 自找活（data 席）· **分支** agent/m1704 · **模型** ITTC-1957 friction + empirical residual (axisymmetric-body fit)
**平台报告** id 1261 · **数据集** 正典 v3.0：船舶阻力基准参考数据集（唯一正典 v3.0）。来源：Roddy 1990 DTRC-90/012（SUBOFF）、ITTC V&V Workshop 2010（DTMB-5415）、SIMMAN 2008（KVLCC2）。每个量由 backend/services/benchmark_canon.py 的解析恒等式复算，含密度/黏度溯源。

## 1. 案例介绍（物理背景与数据）

船舶总体性能的第一性指标是阻力：模型给出无量纲总阻力系数

    Ct = Cf(ITTC-1957) + Cr(经验剩余阻力拟合)          （平台内置模型，SUBOFF 标定）

对标判据是文献/试验算例的实测 Ct。平台正典参考数据集
`backend/data/benchmark_reference.json` 收录 12 条算例（3 船型 × 4 航速）：

| 船型 | 主尺度 | 来源 |
|---|---|---|
| SUBOFF（回转体） | L=4.356 m, D=0.508 m, 淡水 20 °C | Roddy 1990, DTRC-90/012 |
| DTMB-5415（水面战斗舰） | L=5.72 m, B=0.759 m, T=0.246 m, Cb=0.508 | ITTC V&V Workshop 2010 |
| KVLCC2（VLCC 油轮） | L=320 m, B=58 m, T=20.8 m, Cb=0.8098 | SIMMAN 2008 |

**被复核的缺陷（验证欠账）**：m1388/m1614 的数据集闸门一行只出一个三值状态，
其中 `resistance_closure`（C3：`Rt == Ct·½ρV²S`）需要参考行的湿面积 S。DTMB-5415 与
KVLCC2 的 8 行**没有登记湿面积**，于是整行被判 `PARTIAL / withheld`，8/12 条文献
算例对任何精度口径都不可用——平台对全船型阻力实际上处于「无判据」状态：唯一被
允许说话的 4 行全是回转体（SUBOFF）。

## 2. 怎么做的（方法与工具链）

关键物理观察：**C3 判的是牛顿值 Rt，精度对标用的是无量纲 Ct，而 S 在两侧约去。**

    Ct = Rt / (½ ρ V² S)   ⇒   比较 Ct_calc 与 Ct_ref 时 S 不需要出现

所以"缺 S"只能封锁 Rt 级声明（牛顿值、KPI 功率链），不构成封锁 Ct 级结论的理由。
但闸门也不能空转：Ct 归属到哪个航速、哪条摩擦线、哪种船型必须自洽。故新增一条与
三值状态**正交**的判据门：

```
ct_admissible =  froude_closure == PASS
             and friction_floor == PASS
             and form_bands     == PASS          （缺项 N.A. 不算阻塞）
ct_level = CT+RT  if resistance_closure == PASS     ← Rt 级也可声明
         = CT     if resistance_closure == WITHHELD ← 仅无量纲 Ct 级
         = NONE   otherwise
```

实现（最小改动，三处 + 一份测试 + 一份验证脚本）：

| 文件 | 改动 |
|---|---|
| `backend/services/benchmark_integrity.py` | 新增 `ct_admissibility()`、`CT_GATE_CHECKS`、`CT_LEVEL_*` |
| `backend/services/benchmark_dataset_audit.py` | facade 每行附加 `ct_admissible / ct_level / ct_gate_blockers`，三值 `status` 口径不变 |
| `backend/api/domain/ship_benchmark.py` | `benchmark_validate` 每行新增 `ct_admissible / ct_evidence_level / ct_level_pass`，summary 新增 `ct_admissible_cases / ct_admissible_passed / pass_rate_ct / rt_claimable_cases`；`/calculations/benchmark_cases` 输出 `ct_evidence_level` |
| `backend/tests/test_benchmark_ct_admissibility.py` | 15 项回归测试 |
| `tools/verif/verify_ct_admissibility_m1704.py` | 生成下表数字与图（零手抄） |

**刻意不做的事**：不给 DTMB-5415 / KVLCC2 补湿面积。补一个未溯源的 S 会把
`PARTIAL` 洗成 `CONSISTENT`，等于用估计值伪造实验数据；测试
`test_no_wetted_surface_invented_in_dataset` 与 `test_ct_level_never_upgrades_rt_claim`
把这条红线锁死。

## 3. 计算结果（由 `artifacts/m1704/ct_admissibility.json` 程序化注入）

**逐算例对标表（12 行全部进入 Ct 口径）**

| 算例 | 船型 | Fn | Re | Cf(ITTC-57) | Ct_calc | Ct_ref | 误差% | Ct/Cf 参考 | Ct/Cf 模型 | 证据等级 | ≤15% |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SUBOFF-01 | SUBOFF | 0.4589 | 1.302e+07 | 0.002867 | 0.003462 | 0.003800 | 8.90 | 1.32 | 1.21 | CT+RT | PASS |
| SUBOFF-02 | SUBOFF | 0.6119 | 1.735e+07 | 0.002732 | 0.003953 | 0.004000 | 1.18 | 1.46 | 1.45 | CT+RT | PASS |
| SUBOFF-03 | SUBOFF | 0.7649 | 2.169e+07 | 0.002634 | 0.004766 | 0.004200 | 13.48 | 1.59 | 1.81 | CT+RT | PASS |
| SUBOFF-04 | SUBOFF | 0.9179 | 2.603e+07 | 0.002557 | 0.005921 | 0.004400 | 34.58 | 1.72 | 2.31 | CT+RT | FAIL |
| DTMB5415-01 | DTMB-5415 | 0.0610 | 2.603e+06 | 0.003847 | 0.003851 | 0.004500 | 14.43 | 1.17 | 1.00 | CT | PASS |
| DTMB5415-02 | DTMB-5415 | 0.1220 | 5.207e+06 | 0.003371 | 0.003392 | 0.003800 | 10.73 | 1.13 | 1.01 | CT | PASS |
| DTMB5415-03 | DTMB-5415 | 0.2440 | 1.041e+07 | 0.002979 | 0.003098 | 0.003500 | 11.50 | 1.18 | 1.04 | CT | PASS |
| DTMB5415-04 | DTMB-5415 | 0.3660 | 1.562e+07 | 0.002780 | 0.003107 | 0.003800 | 18.23 | 1.37 | 1.12 | CT | FAIL |
| KVLCC2-01 | KVLCC2 | 0.1285 | 2.295e+09 | 0.001384 | 0.001415 | 0.002800 | 49.46 | 2.02 | 1.02 | CT | FAIL |
| KVLCC2-02 | KVLCC2 | 0.1377 | 2.460e+09 | 0.001373 | 0.001410 | 0.003000 | 53.00 | 2.19 | 1.03 | CT | FAIL |
| KVLCC2-03 | KVLCC2 | 0.1423 | 2.542e+09 | 0.001368 | 0.001408 | 0.003100 | 54.59 | 2.27 | 1.03 | CT | FAIL |
| KVLCC2-04 | KVLCC2 | 0.1469 | 2.623e+09 | 0.001363 | 0.001406 | 0.003200 | 56.06 | 2.35 | 1.03 | CT | FAIL |

**船型汇总：可用参考行从 4 行扩到 12 行，Rt 级声明仍只有 4 行**

| 船型 | 可用参考行 (m1704 前 → 后) | Ct 级通过 | Ct 通过率 | Rt 级可声明行 |
|---|---|---|---|---|
| SUBOFF | 4 → 4 | 3 | 75.0% | 4 |
| DTMB-5415 | 0 → 4 | 3 | 75.0% | 0 |
| KVLCC2 | 0 → 4 | 0 | 0.0% | 0 |

总体：`ct_admissible=12, ct_pass=6, pass_rate_ct=50.0%, rt_claimable=4`。

**缺口分解（模型缺失的形状因子 + 波形阻力）**

| 算例 | Ct_ref/Cf | Ct_calc/Cf | 缺口 Ct_ref−Ct_calc | 缺口占 Ct_ref |
|---|---|---|---|---|
| SUBOFF-01 | 1.325 | 1.207 | 0.000338 | 8.9% |
| SUBOFF-02 | 1.464 | 1.447 | 0.000047 | 1.2% |
| SUBOFF-03 | 1.595 | 1.810 | -0.000566 | -13.5% |
| SUBOFF-04 | 1.721 | 2.315 | -0.001521 | -34.6% |
| DTMB5415-01 | 1.170 | 1.001 | 0.000649 | 14.4% |
| DTMB5415-02 | 1.127 | 1.006 | 0.000408 | 10.7% |
| DTMB5415-03 | 1.175 | 1.040 | 0.000402 | 11.5% |
| DTMB5415-04 | 1.367 | 1.117 | 0.000693 | 18.2% |
| KVLCC2-01 | 2.023 | 1.022 | 0.001385 | 49.5% |
| KVLCC2-02 | 2.185 | 1.027 | 0.001590 | 53.0% |
| KVLCC2-03 | 2.267 | 1.029 | 0.001692 | 54.6% |
| KVLCC2-04 | 2.348 | 1.032 | 0.001794 | 56.1% |

## 4. 结果图

![ct_admissibility](artifacts/m1704/ct_admissibility_m1704.png)

(a) Ct–Fn 双对数对标：点为文献值，虚线为平台模型，标注为逐算例 |误差|。
(b) 逐算例相对误差与 15% 判据线，颜色区分船型。

## 5. 注意点（坑 / 局限 / 适用范围）

1. **Ct 级 ≠ Rt 级**：`ct_level=CT` 的行只支持"阻力系数对不对"的结论；功率/油耗/
   有效马力一类 KPI 仍需要湿面积，8 行照旧封锁。summary 里 `rt_claimable_cases` 与
   `withheld` 字段保守原样，没有因为本改动放宽。
2. **闸门是保守闭合型**：Fr 闭合、摩擦下限、形体带任一项出现 WARN 也一律关门
   （不是只挡 FAIL）——航速配对出 2% 以上的偏差时，把 Ct 归到错误航速上得到的
   "精度"毫无意义。合成反例见 `test_gate_closes_when_any_pillar_not_pass`。
3. **KVLCC2 的 49–56% 不是数据问题**：该行 Fr 闭合误差 0.03%、Ct/Cf=1.98（远离
   摩擦下限），缺口是真模型缺口——平台模型的 Ct/Cf≈1.00，即隐含 (1+k)=1，没有形状
   因子也没有波形项；实船 Fr≈0.13–0.15 恰好是波形阻力占比最高的区间。故 KVLCC2 的
   失败结论可信，且与 m1414 用湿面积反解独立得到的平均误差 ~53% 一致（本文
   53.3%，两条互不依赖的路径互证）。
4. **SUBOFF-04（Fr=0.918）34.6%** 是既有已知局限（Cr 拟合式在 Fr>0.9 外推），本轮
   不改模型，只如实保留 FAIL；m1704 未改变 SUBOFF 口径（前 3 行通过、第 4 行失败，
   与改动前逐位一致，见 `test_endpoint_suboff_unchanged`）。
5. **DTMB-5415 34.58%→18.23% 一类的"部分通过"不等于模型可用**：DTMB 4 行里
   3 行 ≤15% 是低 Fr 摩擦主导区间，模型此时几乎只剩 Cf，属"巧合式正确"；一旦进入
   Fr≥0.37 波形项抬头就超判据。不要把 Ct 口径的 75% 当作全船型精度背书。

## 6. 误差判定（含 15% 判据）（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 结果 | 是否 <3% |
|---|---|---|---|
| Ct（SUBOFF 4 行，回转体标定域） | DTRC-90/012 实测 Ct | 14.5% 平均 / 最小 1.18% | **否**（标定域内最好 1.18% <3%，但平均 14.5% 不达标） |
| Ct（DTMB-5415 4 行） | ITTC-2010 试验 Ct | 13.7% 平均（范围 10.7–18.2%） | **否** |
| Ct（KVLCC2 4 行） | SIMMAN 2008 实测 Ct | 53.3% 平均（范围 49.5–56.1%） | **否**（判据亦远超 15%） |
| 判据本身的自洽性 | 12 行 C1/C2/C4/C5 恒等式 | 12/12 PASS，恒等式容差 1% | **是**（0.00–0.03% 闭合） |
| 闸门不空转（反例可控性） | 15 项回归测试 | 15/15 通过；WARN/FAIL/WITHHELD 三种阻塞均关门 | **是** |

诚实结论：本单**没有**提升阻力模型本身的精度（那是模型层工作），提升的是"能不能
被证明"——把 8 条此前无法引用的文献算例纳入可对标范围，并把模型缺口量化成
49–56%（KVLCC2）与 10.7–18.2%（DTMB-5415）。按 15% 判据，平台现有阻力模型对
油轮型船体**不合格**，必须换船型适配方法（Holtrop-Mennen 波形 + 形状因子 (1+k)），
该升级已按本单量化目标另派单 **mission 1721（runner=native）**，验收判据即本文数字：KVLCC2 4 行 ≤15%、DTMB-5415 4 行 ≤15%、SUBOFF 不回退。
