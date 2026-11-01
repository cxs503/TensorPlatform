# 基准数据集六分支裁决：唯一正典 v3.0（mission m1561）

> 一句话：同一份 12 行船舶阻力基准数据被六张分支独立重推导出互相矛盾的值，且平台里并存三套
> "参考数据一致性判定"服务。本报告用**解析恒等式 + 介质溯源**逐值复算，出唯一正典
> `backend/data/benchmark_reference.json` v3.0，把服务架构收敛到一个闸门，其余归档。

## 1. 案例背景与数据

基准数据集覆盖三类经典验证对象（12 个算例）：DARPA SUBOFF 回转体（Roddy 1990, DTRC-90/012）、
DTMB-5415 驱逐舰船模（ITTC V&V Workshop 2010, Gothenburg）、KVLCC2 油轮（SIMMAN 2008）。

| 船型 | 尺度 | 主要几何 | 速域 | 记录内容 |
|---|---|---|---|---|
| SUBOFF | 模型 L=4.356 m | D=0.508 m，湿面积 S=5.42 m² | 3.0–6.0 m/s（Fr 0.459–0.918） | Ct 与 Rt 两列 |
| DTMB-5415 | 模型 L=5.72 m | B=0.759 m，T=0.246 m，Cb=0.508 | 记录 5 / 10 / 20 / 30 kn | Ct、Rt、Fr、Δ |
| KVLCC2 | 实船 L=320 m | B=58 m，T=20.8 m，Cb=0.8098 | 14–16 kn（Fr 0.129–0.147） | Ct、Rt、Fr、Δ |

核心矛盾（任务书给定，本报告复算确认）：

* SUBOFF-01 的 `experimental_rt_n`：基线 v2 = **95.0 N**，m1395 = **92.52 N**，两者比 1025/998.2；
* v1 记录值 **12.5 N** 与 Rt=Ct·½ρV²S 差 **7.40 倍**；
* DTMB-5415 的 Froude 列与航速列互不自洽；KVLCC2/DTMB 的 Δ 与几何不闭合。

## 2. 方法

裁决规则全部可机器判（`backend/services/benchmark_canon.py`）：

| 规则 | 内容 | 容差 |
|---|---|---|
| R1 | Rt = Ct·½ρV²S（含反解表观湿面积 S_imp 判可复核性） | 1 % |
| R2 | 流体溯源：模型拖曳水池=淡水 998.2 / 实船=海水 1025 | 强制 |
| R3 | Fr = V/√(gL)，并反解 Fr 列的隐含船长 | 2 % |
| R4 | Δ = Cb·L·B·T·ρ | 2 % |
| R5 | Ct ≥ Cf_ITTC57(Re)（总阻力不可能低于平板摩擦） | 硬约束 |
| R6 | 形体带：L/D、S/(πDL)、L/B、Cb | 区间 |

流程：`git show agent/mXXXX:backend/data/benchmark_reference.json` 直接读六张分支的数据集
（零手抄）→ 逐值按 R1–R6 复算 → 三栏对照（基线/m1395/裁决）→ 从**冻结输入**
`benchmark_reference_sources_v2.json` 确定性重建正典（`--check` 可判文件是否仍等于复算结果）。
工具链：Python 3.11 标准库（无第三方依赖）+ pytest；图为 matplotlib 生成。

## 3. 计算结果

### 3.1 阻力三栏表（基线值 / m1395 值 / 裁决值）

| 算例 | 基线 v2 [N] | m1395 [N] | **裁决值 [N]** | 状态 | ρ [kg/m³] |
|---|---|---|---|---|---|
| DTMB5415-01 | — | — | — | WITHHELD | 998.2 |
| DTMB5415-02 | — | — | — | WITHHELD | 998.2 |
| DTMB5415-03 | — | — | — | WITHHELD | 998.2 |
| DTMB5415-04 | — | — | — | WITHHELD | 998.2 |
| KVLCC2-01 | — | — | — | WITHHELD | 1025.0 |
| KVLCC2-02 | — | — | — | WITHHELD | 1025.0 |
| KVLCC2-03 | — | — | — | WITHHELD | 1025.0 |
| KVLCC2-04 | — | — | — | WITHHELD | 1025.0 |
| SUBOFF-01 | 95 | 92.52 | 92.52 | DERIVED | 998.2 |
| SUBOFF-02 | 177.78 | 173.13 | 173.13 | DERIVED | 998.2 |
| SUBOFF-03 | 291.66 | 284.04 | 284.04 | DERIVED | 998.2 |
| SUBOFF-04 | 440 | 428.49 | 428.49 | DERIVED | 998.2 |

裁决依据（程序化生成，逐行存于 `docs/reports/m1561/adjudication_table.json`）：

* **SUBOFF 四行**：记录自带湿面积 S=5.42 m²，R1 唯一可复算 → 采用**淡水 998.2 kg/m³**
  （DTRC 拖曳水池为淡水；平台物理核 `ship_resistance.DEFAULT_WATER_DENSITY = 998.0` 也是淡水，
  参考数据必须同介质，否则契约漂移）。SUBOFF-01 = **92.52 N**，与 m1395 完全一致（相对差 <1e-4）。
* 基线 v2 的 **95 N** 恰好等于海水密度复算值：偏差 +2.68%，
  即 v2 把海水密度用在了模型拖曳水池行上 → 作废，值留 `legacy_values.superseded_experimental_rt_n`。
* v1 的 **12.5 N** 反解湿面积仅 0.732 m²
  （记录的 13.5%），与 R1 差 7.40 倍 → 作废，留 `legacy_values.v1_experimental_rt_n`。
* **DTMB-5415 / KVLCC2 四行×2**：记录缺湿面积，R1 反解的表观湿面积越出物理带 → `rt_status=WITHHELD`，
  值置 `null`。**不编数字**。

### 3.2 Froude 三栏表

| 算例 | 基线 v2 | m1395 | **裁决值** | 状态 | 裁决航速 [m/s] |
|---|---|---|---|---|---|
| DTMB5415-01 | 0.061 | 0.3434 | 0.061 | WITHHELD | 0.4569 |
| DTMB5415-02 | 0.122 | 0.6867 | 0.122 | WITHHELD | 0.9139 |
| DTMB5415-03 | 0.244 | 1.3735 | 0.244 | WITHHELD | 1.8278 |
| DTMB5415-04 | 0.366 | 2.0602 | 0.366 | WITHHELD | 2.7417 |
| KVLCC2-01 | 0.129 | 0.129 | 0.1285 | DERIVED | 7.202 |
| KVLCC2-02 | 0.138 | 0.138 | 0.1377 | DERIVED | 7.717 |
| KVLCC2-03 | 0.143 | 0.143 | 0.1423 | DERIVED | 7.974 |
| KVLCC2-04 | 0.147 | 0.147 | 0.1469 | DERIVED | 8.231 |
| SUBOFF-01 | 0.459 | 0.459 | 0.4589 | DERIVED | 3.0 |
| SUBOFF-02 | 0.612 | 0.612 | 0.6119 | DERIVED | 4.0 |
| SUBOFF-03 | 0.765 | 0.765 | 0.7649 | DERIVED | 5.0 |
| SUBOFF-04 | 0.918 | 0.918 | 0.9179 | DERIVED | 6.0 |

* SUBOFF：R3 闭合，误差 0.02%（SUBOFF-01）→ 采用复算值。
* KVLCC2：记录 0.129/0.138/0.143/0.147 与 V/√(gL) 差 0.5–0.6%（容差 2%）→ 采用复算值 0.1285/0.1377/0.1423/0.1469。
* DTMB-5415：记录航速 5/10/20/30 kn 是**实船服务航速**，配 L=5.72 m 模型得 Fr=
  0.343/0.687/1.373/2.060（物理不可能）；而记录 Fr 列的隐含船长
  (V/Fr)²/g = **181.3 m** ≠ 5.72 m。
  两列不可能同时为真，且都无法溯源到 Gothenburg 2010 试验矩阵 → **`froude_status=WITHHELD`**；
  正典保留按记录 Fr 反算的模型尺度航速（仅作 API 运行点，明示为条件值）。

### 3.3 排水量（R4）

| 算例 | 记录 Δ | **派生 Δ** | 结论 |
|---|---|---|---|
| DTMB5415-01 | 466.3 kg | 541.6 kg | v1/v2 记录 466.3 kg 误差 16.1%，已移入 legacy |
| DTMB5415-02 | 466.3 kg | 541.6 kg | v1/v2 记录 466.3 kg 误差 16.1%，已移入 legacy |
| DTMB5415-03 | 466.3 kg | 541.6 kg | v1/v2 记录 466.3 kg 误差 16.1%，已移入 legacy |
| DTMB5415-04 | 466.3 kg | 541.6 kg | v1/v2 记录 466.3 kg 误差 16.1%，已移入 legacy |
| KVLCC2-01 | 307000 t | 320437.2 t | v1/v2 记录 307000.0 t 误差 4.4%，已移入 legacy |
| KVLCC2-02 | 307000 t | 320437.2 t | v1/v2 记录 307000.0 t 误差 4.4%，已移入 legacy |
| KVLCC2-03 | 307000 t | 320437.2 t | v1/v2 记录 307000.0 t 误差 4.4%，已移入 legacy |
| KVLCC2-04 | 307000 t | 320437.2 t | v1/v2 记录 307000.0 t 误差 4.4%，已移入 legacy |

### 3.4 逐行判决

| 算例 | reference_status | Rt | Fr | audit_flags |
|---|---|---|---|---|
| DTMB5415-01 | WITHHELD | WITHHELD | WITHHELD | displacement_closure_fail, froude_provenance_withheld, resistance_not_reviewable |
| DTMB5415-02 | WITHHELD | WITHHELD | WITHHELD | displacement_closure_fail, froude_provenance_withheld, resistance_not_reviewable |
| DTMB5415-03 | WITHHELD | WITHHELD | WITHHELD | displacement_closure_fail, froude_provenance_withheld, resistance_not_reviewable |
| DTMB5415-04 | WITHHELD | WITHHELD | WITHHELD | displacement_closure_fail, froude_provenance_withheld, resistance_not_reviewable |
| KVLCC2-01 | WITHHELD | WITHHELD | DERIVED | displacement_closure_fail, resistance_not_reviewable |
| KVLCC2-02 | WITHHELD | WITHHELD | DERIVED | displacement_closure_fail, resistance_not_reviewable |
| KVLCC2-03 | WITHHELD | WITHHELD | DERIVED | displacement_closure_fail, resistance_not_reviewable |
| KVLCC2-04 | WITHHELD | WITHHELD | DERIVED | displacement_closure_fail, resistance_not_reviewable |
| SUBOFF-01 | DERIVED | DERIVED | DERIVED | — |
| SUBOFF-02 | DERIVED | DERIVED | DERIVED | — |
| SUBOFF-03 | DERIVED | DERIVED | DERIVED | — |
| SUBOFF-04 | DERIVED | DERIVED | DERIVED | — |

判决分布（`service.benchmark_dataset_audit` facade 口径）：

| 数据版本 | CONSISTENT | PARTIAL | INCONSISTENT | 可声明精度? |
|---|---|---|---|---|
| 记录值（v1/v2 filed） | 0 | 0 | 12 | False |
| **正典 v3.0** | 4 | 8 | 0 | True |

即：**修正前 12 行没有一行能支撑 15% 精度判决**（恒等式 1% 闸门下的真值自洽性）；
修正后 4 行（SUBOFF）成为可判据的 REAL 级参考值，8 行如实标 WITHHELD。

## 4. 结果图

| 图 | 内容 |
|---|---|
| `docs/reports/m1561/fig1_suboff_rt_adjudication.png` | SUBOFF 四算例：基线 v2（ρ=1025）与正典（ρ=998.2）的 Rt 对比 + 相对偏差（恒为 1025/998.2−1 = 2.68%） |
| `docs/reports/m1561/fig2_suboff_density_sensitivity.png` | 争议的本质是一条密度敏感性曲线：998.2 / 1000 / 1025 分别给出 92.52 / 92.68 / 95.0 N，v1 的 12.5 N 远在曲线之外 |
| `docs/reports/m1561/fig3_gate_verdicts_before_after.png` | 闸门判决 before/after：CONSISTENT 0→4，INCONSISTENT 12→0 |
| `docs/reports/m1561/fig4_dtmb_froude_conflict.png` | DTMB-5415：由记录航速算出的 Fr（红，最高 2.06）与记录 Fr 列（蓝，隐含 181 m 船长）互斥；绿点为正典的条件航速 |

## 5. 服务架构裁决

正典 = `backend/services/benchmark_integrity.py`（C1–C5，PASS<WITHHELD<WARN<FAIL 严重度梯，
带独立测试）+ 新增 `backend/services/benchmark_canon.py`（裁决/复算引擎）；API 层
`backend/api/domain/ship_benchmark.py` 不变。其余降级：

* `benchmark_dataset_audit`（m1402 的 I1–I3，整合波已进基线）→ **facade**，物理全部委托闸门，
  保留 `CONSISTENT/PARTIAL/INCONSISTENT` 导出名。理由：I1–I3 是 C1–C5 的真子集，且密度写死
  单一常量（**这正是 95.0 N 的根因**），基线里无测试随行。
* `ship_resistance_core`（m1395）、`resistance_core`+`hull_geometry`（m1400）、
  `geometry_consistency`（m1404）、`benchmark_integrity@m1400` 变体 → 归档说明见 `docs/superseded/`。
  它们的**规则**被吸入正典（R1 淡水复算 / R4 几何闭合 / R6 形体带 / 工件程序化生成），**代码**不进主线。

对闸门的三处最小扩展（改的都是"两种介质/两种用途"的混用）：

1. 行内物性溯源：`audit_case` 优先读 `reference_density_kg_m3`，无则回落默认；
2. 证据级恒等式容差：`identity_tol` 默认 1%（诊断可放宽 15%）——"用 15% 容差验证 15% 误差"是循环论证；
3. 不适用检查项不再污染行级 verdict：回转体跳过 C2（否则每艘潜艇行永久 WITHHELD，是误判而非保守）。

## 6. 注意点 / 局限（honest）

1. **介质张冠李戴是这类数据最隐蔽的错**：Rt 恒等式在各自密度下都是"自洽"的（v2 的 95.0 N 用 1025 复算误差
   0.00%），所以纯恒等式闸门**抓不到**密度错，必须显式建 R2 介质溯源。这是本次最重要的教训。
2. **原始文献未能在线核对**：Roddy 1990 DTRC-90/012 的试验条件（水温/密度、S 定义）与 SIMMAN 2008 的
   湿面积表不在本机构可访问范围；淡水 998.2 kg/m³ 由"DTRC 拖曳水池为淡水 + ITTC 物性表 + 平台物理核同介质"
   三重间接证据支撑，**不是直接引用**。若 owner 能取到原文水温/ρ，应按原文替换（预期影响 <0.3%）。
3. **DTMB-5415 的 Fr/航速仍是 WITHHELD 而非"裁决"**：需要 Gothenburg 2010 的原始试验矩阵（模型航速表）。
   已如实交 owner，不凑数。
4. **Ct 列（0.0038…）本身按"记录值"处理**：R5 摩擦下限通过（SUBOFF-01 (1+k)=
   1.33），
   但无原文表可逐点比对，属"来源级 WITHHELD、数值级可用"。
5. **沙箱缺依赖**：`cryptography` / `psycopg2` 缺失，`backend/tests/conftest.py`（import main）无法加载，
   因此 API 层测试（`backend/tests/feat/test_ship_benchmark.py`）在本环境**未执行**；
   本次验证走 `--noconftest` 的纯模块级测试（见第 7 节）。

## 7. 误差判定（是否 <3%？）

关键量对照（基准 = 正典复算值；"误差"指各分支主张相对裁决值的偏差）：

| 量 | 主张方 | 值 | 相对裁决值偏差 | <3%？ |
|---|---|---|---|---|
| SUBOFF-01 Rt | 基线 v2（ρ=1025） | 95 N | +2.68% | 是（2.68%），但**介质错误** |
| SUBOFF-01 Rt | m1395（ρ=998.2） | 92.52 N | +0.0000% | 是 |
| SUBOFF-01 Rt | m1397（ρ=1000） | 92.68 N | +0.18% | 是（0.18%），ρ 无出处 |
| SUBOFF-01 Rt | v1 记录 | 12.5 N | -86.5% | **否（−86.5%）** |
| DTMB-5415-03 Fr | m1395/m1397/m1402（由记录航速算） | 1.3735 | 相对记录 0.244 差 463% | **否，且物理不可能** |
| DTMB-5415-03 Fr | 基线 v2（沿用记录） | 0.244 | 隐含 L=181.3 m vs 声明 5.72 m | **否**（判 WITHHELD） |
| KVLCC2-03 Fr | 记录 0.143 | 0.143 | 相对复算 0.1423 偏差 +0.48% | 是 |
| DTMB-5415 Δ | 记录 466.3 kg | 466.3 | 相对派生 541.6 kg 偏差 −13.9% | **否** |
| KVLCC2 Δ | 记录 307000 t | 307000 | 相对派生 320437 t 偏差 −4.2% | **否** |
| 闸门一致性 | 正典 vs benchmark_integrity | 12 行 | FAIL 行数 0 | 是 |

结论：**能定谳的量全部 <3%（多数 <0.2%），定谳不了的（DTMB-5415 的 Fr/航速、以及 8 行的 Rt）如实标
WITHHELD，不计入任何精度声明**。任务书要求的 15% 不可接受线在这里不适用——本单裁决的是参考值自身的
自洽性（1% 恒等式闸门），比 15% 严一个数量级。

## 8. 验证

```
PYTHONPATH=backend python3 -m pytest tests/test_benchmark_canon.py backend/tests/test_benchmark_integrity.py --noconftest -q
# 42 passed
PYTHONPATH=backend python3 backend/services/benchmark_canon.py            # canon OK（文件 == 复算，自审无 REJECTED）
PYTHONPATH=backend python3 backend/services/benchmark_canon.py --write    # 重建正典 + 三栏表 + 审计工件
```
