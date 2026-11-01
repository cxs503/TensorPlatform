# 阻力引擎外部基准复核：湿面积拟合式与残阻律对文献/解析基准（m1666）

> 复核对象：`POST /api/v1/calculations/resistance`（能力 `calc.resistance`，宣称 REAL）与其对标链路 `POST /api/v1/calculations/benchmark_validate`。
> 复核类型：**验证欠账清偿**（已合并功能对文献实测值 + 解析几何基准独立复核），承接 `docs/research/m1386_stability_gz_validation.md` §待办首条。
> 工具链：Python 3.11 · 纯进程内计算 · pytest 15 项 · matplotlib。全部数字出自 `runs/m1666/resistance_external_audit.json`（`scripts/resistance_external_audit.py` 产出，本文由 `scripts/gen_resistance_external_doc.py` 注入，零手抄）。
> 判据：`docs/QUALITY_BAR.md` —— 对标实验/参考值误差 >15% 不可接受；理想标尺 3%。**引擎默认数值本次一字未改**（回归测试 `test_engine_default_numbers_unchanged` 锁死）。

## ① 案例介绍（物理背景 / 几何 / 参数）

平台的阻力引擎是两条**经验式**加一条**平板摩擦线**：

```
S   = π · B · L · (0.5 + 0.25·Cb)        # 湿表面积（船体拟合式）
Cr  = 0.002 · (1 + 2·Cb) · Fn^2.5        # 残余阻力（docstring 自称 SUBOFF-calibrated）
Cf  = 0.075 / (log10(Re) − 2)^2          # ITTC-1957
Rt  = ½ρV²S · (Cf + Cr),   Ct = Rt/(½ρV²S)
```

正典 v3.0（`backend/data/benchmark_reference.json`）12 行算例中，只有 DARPA SUBOFF 四行带**实测湿面积**，因而是唯一能做「S 绝对精度」判决的对象：

| 参数 | 值 | 来源 |
|---|---|---|
| L（模型） | 4.356 m | Roddy 1990, DTRC-90/012 |
| D（最大直径） | 0.508 m | 同上 |
| 记录实测湿面积 S | 5.42 m² | 同上（正典行自带） |
| 介质 | 淡水 20 °C，ρ=998.2 kg/m³，ν=1.004e-6 m²/s | ITTC 7.5-02-01-01 |
| 航速 | 3.0 / 6.0 / 12.0 / 18.0 m/s（SUBOFF-01..04） | 正典行 |
| 引擎 Cb（无溯源兜底常数） | 0.542 | `backend/api/domain/ship_benchmark.py` `SHIP_GEOMETRY` |

**为什么以前查不出来**：`benchmark_validate` 比的是 **Ct**（单位面积系数），而 Ct 按定义与湿面积无关；`docs/benchmarks/benchmark_data_integrity.md` §3.2 的解析账本把 S/Cr 公式与**公式自身**对照（误差恒 ~1e-7）。两者都看不见「S 的绝对精度」。

## ② 怎么做的（方法 / 闸门设计）

新增 `backend/services/resistance_external_baseline.py`（纯 stdlib，不连 DB/HTTP），把外部基准复核落成 7 项可机器判定的检查，判级与 `benchmark_integrity` 同序（`PASS < WARN < WITHHELD < FAIL`，3% / 15% 双标尺）：

| 检查 | 内容 | 外部依据 |
|---|---|---|
| E1 | 引擎 S vs 记录实测 S | Roddy 1990（正典行） |
| E2 | 引擎 S vs 长椭球闭式解（同 L、D） | 解析：`S = 2πb² + (2πab/e)·asin(e)`，无拟合系数 |
| E3 | 几何基准**自校**：椭球 vs 记录实测 S（<3% 才准用） | 防止用一个不可信基准判死刑 |
| E4 | Cb 溯源：常数是否来自正典 | 正典行无 `cb`/体积 → WITHHELD |
| E5 | Cr 形状律 4 项解析极限（零极限/单调/量级带/幂次） | 解析 |
| E6 | 引擎 Cr vs `Ct_recorded − Cf_ITTC57` 反解值 | 该反解**不含湿面积假设** |
| E7 | 引擎 (Ct, Rt) vs 记录 + 乘法误差分解 | 恒等式，非拟合 |

E7 的分解是恒等式，可把总误差**无损**拆到子模型上：

```
Rt_e / Rt_m = (S_e / S_m) · ((Cf + Cr_e) / Ct_m) · closure_recorded
              └ 面积项 ┘   └──── 系数项 ────┘   └ 记录自身的 Ct↔Rt 闭合 ┘
```

其中 `closure_recorded` 由正典行的 (S, Ct, Rt) 反算，用于把「记录本身闭合了多少」与「引擎偏了多少」彻底分开——`identity_holds` 字段逐行断言该乘积等于总比（相对 1e-9）。

**裁决纪律**：`reference_status=WITHHELD` 的正典行（DTMB-5415、KVLCC2 共 8 行）一律 WITHHELD，绝不用不可复核的记录去判引擎；记录缺实测 S 时同样 WITHHELD，不许拿几何基准单方面判死。

## ③ 计算结果（数字表）

### 3.1 湿表面积：引擎拟合式 vs 两条外部基准

| 基准 | S [m²] | 相对记录值误差 | 判定 |
|---|---|---|---|
| 记录实测（Roddy 1990） | 5.420 | 0.00% | 参考 |
| 长椭球闭式解（同 L、D） | 5.494 | 1.36% | PASS（基准自校通过） |
| **引擎拟合式** `πBL(0.5+0.25Cb)` | 4.418 | **-18.49%** | **FAIL** |

两条**互相独立**的外部基准（拖曳水池实测 5.42、解析椭球 5.494）互差 1.36%（<3%），因此 −18.5% 是引擎的模型偏差，不是参考值坏了。

### 3.2 端到端逐例误差（SUBOFF 四行，正典 `reference_status=DERIVED`）

| case | V [m/s] | Fn | Ct 引擎 | Ct 记录 | Ct 误差 | Rt 引擎 [N] | Rt 记录 [N] | Rt 误差 | S 误差 | 行判 |
|---|---|---|---|---|---|---|---|---|---|---|
| SUBOFF-01 | 3.000 | 0.459 | 0.003462 | 0.0038 | -8.90% | 68.70 | 92.52 | -25.75% | -18.49% | FAIL |
| SUBOFF-02 | 4.000 | 0.612 | 0.003953 | 0.0040 | -1.18% | 139.46 | 173.13 | -19.45% | -18.49% | FAIL |
| SUBOFF-03 | 5.000 | 0.765 | 0.004766 | 0.0042 | 13.48% | 262.74 | 284.04 | -7.50% | -18.49% | FAIL |
| SUBOFF-04 | 6.000 | 0.918 | 0.005921 | 0.0044 | 34.58% | 470.03 | 428.49 | 9.69% | -18.49% | FAIL |

### 3.3 误差归因（SUBOFF-01，V=3.0 m/s，恒等式分解）

| 项 | 比值 | 贡献 |
|---|---|---|
| 面积项 S_e/S_m | 0.8151 | -18.49% |
| 系数项 (Cf+Cr)/Ct_m | 0.9110 | -8.90% |
| 记录自身 Ct↔Rt 闭合 | 0.999948 | -0.0052% |
| **总比 Rt_e/Rt_m** | **0.7425** | **-25.75%** |
| 反事实（用记录 S 重算 Rt） | — | -8.90% |

读法：总误差 −25.7% 中，−18.5 个百分点来自湿面积、−8.9 个百分点来自残阻律；反事实一列说明「把 S 换成实测值后只剩 −8.9%」——面积项是主导项，且两项同号叠加，不存在互相抵消。

### 3.4 残阻律 Cr：引擎 vs 记录反解

| case | Fn | Cr 引擎 | Cr 反解 = Ct−Cf | 误差 | 判定 |
|---|---|---|---|---|---|
| SUBOFF-01 | 0.459 | 0.000595 | 0.000933 | -36.25% | FAIL |
| SUBOFF-02 | 0.612 | 0.001221 | 0.001268 | -3.72% | WARN |
| SUBOFF-03 | 0.765 | 0.002133 | 0.001566 | 36.16% | FAIL |
| SUBOFF-04 | 0.918 | 0.003364 | 0.001843 | 82.56% | FAIL |

引擎 Cr 的 `Fn^2.5` 指数与 SUBOFF 记录不符：低 Fn 偏小、高 Fn 偏大，误差随 Fn **单调恶化**（−36% → +83%）。docstring 里「SUBOFF-calibrated」的说法在这四行数据上不成立。

### 3.5 数据集级汇总

| 形体 | 行数 | 最差判定 |
|---|---|---|
| displacement_hull | 8 | WITHHELD |
| body_of_revolution | 4 | FAIL |

- 数据集级 `binding = FAIL`；`provenance_caveat_rows = 4` 行（Cb 无溯源，见 §⑤）。
- 12 行中 8 行（DTMB-5415/KVLCC2）判 **WITHHELD**：正典自身 `reference_status=WITHHELD`，按纪律不得据此产生任何精度声明——这不是本次审计的空白，而是闸门应有的拒绝。

## ④ 结果图

![wetted surface](figs/m1666_wetted_surface_baselines.png)

**(a) 湿面积三源对照**：引擎拟合式（红）系统性低于实测（蓝）与椭球基准（绿）；**(b) 同一批案例三种口径的误差**：Ct-only（蓝，现有指标）在 ±15% 带内，而 S（红）与 Rt（橙）越界——差异正是本单要还的欠账。

![decomposition](figs/m1666_error_decomposition.png)

**(a) 乘法分解**：面积项与系数项同号，共同造成 −25.7% 的总偏差，记录自身闭合因子 ≈1 （证明问题不在参考值）；**(b) 残阻律形状**：引擎 `Fn^2.5` 曲线（红实线）与记录反解点（蓝圈）在低 Fn 相交、高 Fn 分离——指数过陡。

![cb sensitivity](figs/m1666_cb_sensitivity.png)

**Cb 扫描**：把 Cb 从 π/6 抬到 0.8，引擎 S 仍全程低于记录值 → 偏差来自拟合式的**形状**（对 B 线性、对 Cb 仅 0.25 权），换 Cb 洗不白，必须改用几何/实测 S。

## ⑤ 注意点（坑 / 局限 / 适用范围）

- **坑 1：Ct 口径结构上看不见面积误差。** `Ct = Rt/(½ρV²S)`，S 在分子分母里同时出现，用 Ct 对标等于把湿面积误差开根号式地淡化；现有 pass 判据因此名不副实。
- **坑 2：解析账本的自证陷阱。** 旧账本把 S 公式与**同一公式**比对（误差 1e-7），这类「自洽性检查」再多也证伪不了模型；外部基准必须来自数据或闭式几何。
- **坑 3：Cb 兜底常数。** `SHIP_GEOMETRY['SUBOFF']['cb'] = 0.542` 在正典里没有溯源（回转体行无 Cb/体积），与同 L/D 长椭球的 π/6=0.5236 相差 3.51%。本单把它判为 `WITHHELD` 并单列 `provenance_caveat`，未替换数值。
- **局限 1：** 只有 SUBOFF 四行带实测 S，样本量 1 个几何、4 个工况；结论对「细长回转体 + 该航速带」成立，不宜外推到肥大型船。
- **局限 2：** 长椭球只是**回转型**几何的代表，不是 SUBOFF 实体；它与实测差 1.36% 说明该近似可用作交叉校验，但不应被当作精确真值。
- **局限 3：** 未做 CFD 交叉验证（`suboff_re1000` 属 TensorLBM，跨仓不在本单白名单内）。
- **未做（如实列出）：** ①不修改引擎默认 S/Cr 公式（改动会波及全部阻力结果与下游稳性/推进，需独立评审单）；②不重标正典 12 行参考值（需回原文）；③未把本节闸门接进 `/calculations/benchmark_validate` 的响应体（建议下单一并接线并暴露 `wetted_surface_status`）。

## ⑥ 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 误差 | <3%? | <15%? | 判定 |
|---|---|---|---|---|---|
| 几何基准 S（椭球） | 记录实测 5.42 m² | 1.36% | **是** | 是 | PASS |
| 引擎 S | 记录实测 5.42 m² | -18.49% | **否** | **否** | FAIL |
| 引擎 S | 椭球闭式解 5.494 m² | -19.58% | **否** | **否** | FAIL |
| Rt（SUBOFF-01） | 记录 92.52 N | -25.75% | **否** | **否** | FAIL |
| Rt（SUBOFF-02） | 记录 173.13 N | -19.45% | **否** | **否** | FAIL |
| Rt（SUBOFF-03） | 记录 284.04 N | -7.50% | **否** | 是 | WARN |
| Rt（SUBOFF-04） | 记录 428.49 N | 9.69% | **否** | 是 | WARN |
| Cr（SUBOFF-01） | Ct−Cf 反解 | -36.25% | **否** | **否** | FAIL |
| Cr（SUBOFF-02） | Ct−Cf 反解 | -3.72% | **否** | 是 | WARN |
| Cr（SUBOFF-03） | Ct−Cf 反解 | 36.16% | **否** | **否** | FAIL |
| Cr（SUBOFF-04） | Ct−Cf 反解 | 82.56% | **否** | **否** | FAIL |

**显式结论**：

1. 湿面积：**不满足 3%，也不满足 15%**（-18.49%）→ `calc.resistance` 的 S 子模型在这条唯一可复核的算例上 **未达 REAL 精度门槛**。
2. 残阻律：4 个工况中 3/4 超出 15%，且误差随 Fn 单调恶化 → 「SUBOFF-calibrated」不成立。
3. 端到端 Rt：4 个工况全部超出 3%，2/4 超出 15%（最差 -25.75%）。
4. 几何基准自校 **<3%（1.36%）** → 判决不是参考值坏了，而是引擎的两条经验式在这类形体上不适用。
5. 因此本次结论是对已合并能力的 **honest-FAIL（子模型级）**，并不推翻 `benchmark_validate` 的 Ct 口径历史结论——那两个口径量的是不同的东西。

## 复现方式

```bash
python3 scripts/resistance_external_audit.py --print      # 出账本 runs/m1666/resistance_external_audit.json
python3 scripts/gen_resistance_external_doc.py            # 出本文与三张图
cd backend && python3 -m pytest tests/test_resistance_external_baseline.py -q   # 15 passed
```

## 给下一张自找活单的建议

1. **接线**：把 `wetted_surface_status` / `rt_error_pct` 暴露进 `/calculations/benchmark_validate` 响应，让 UI 与报告直接看见面积项（本单只建闸门）。
2. **修公式**：对 `form_type=body_of_revolution` 用几何/实测 S（椭球解已在本仓可用且自校 1.4%），对 Cr 用 SUBOFF 记录的 Fn 依赖重标（需原文数据，不得臆造）。
3. **同门禁推广**：把 E1–E7 的「外部基准 vs 引擎」范式搬到 `calc.stability`（GZ 已有精确箱体解，缺的是真实船型静水力曲线接入）。
