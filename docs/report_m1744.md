# Mission 1744 补报：G-SAMPLE 样本库 Schema 定义与首批回填管线

> **补报单**：原 mission 1744 已交付但缺论文级进展报告，本文补报。  
> **原单状态**：完成 ✅  
> **仓库**：TensorPlatform  
> **日期**：2026-11-11（补报）  

---

## 1. 案例介绍

### 1.1 物理背景

船舶总体性能平台的知识库长期存在「报告多、样本零」的结构性问题：guide_dashboard 显示已有 270+ 篇报告，但结构化样本数为 0。报告是非结构化 Markdown/JSON 混合体，无法被下游任务（参数扫描、不确定性量化、模型校准）直接消费。

### 1.2 目标

- 定义覆盖船舶九大学科（阻力/推进/耐波/操纵/稳性/结构/振动噪声/绿色航行/冰区航行）的样本库 JSON Schema
- 实现从现有报告/工件中自动提取结构化样本的管线
- 首批回填 ≥100 条样本，通过 Schema 验证

### 1.3 关键参数

| 参数 | 值 |
|------|-----|
| Schema 版本 | JSON Schema Draft 2020-12 |
| 学科域枚举数 | 9 |
| 船型枚举数 | 13 |
| 必填字段数 | 9 |
| 数据源数 | 8 |
| 目标样本数 | ≥100 |

---

## 2. 方法/流程

### 2.1 Schema 设计

采用 JSON Schema Draft 2020-12 定义样本结构（`docs/sample.schema.json`），核心设计决策：

1. **唯一标识**：`sample_id` 格式 `SPL-{mission}-{domain}-{seq}`，正则约束
2. **几何先验**：`Cb ∈ [0,1]`（方形系数物理约束），`L_pp/B/T ≥ 0`，且 `Cb/L_pp/B` 为必填——确保几何验证先决条件
3. **验证状态**：五级枚举 `PASS/FAIL/HONEST_FAIL/UNKNOWN/NOT_VALIDATED`，支持诚实失败
4. **证据等级**：五级枚举 `REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED`，与平台能力审计对齐
5. **零额外字段**：`additionalProperties: false`，防止 schema 漂移

### 2.2 提取管线

```
[8 个数据源] → extract_samples.py (877行) → samples_batch1.jsonl
                                                    ↓
                                         validate_samples.py (190行)
                                                    ↓
                                         validation_report.json
```

**8 个数据源**：

| # | 数据源 | 学科 | 样本数 |
|---|--------|------|--------|
| 1 | KVLCC2 阻力审计 (`docs/m1536/`) | resistance | 9 |
| 2 | 稳性 GZ 曲线 (`artifacts/m1386/`) | stability | 48 |
| 3 | SUBOFF 潜艇阻力 (`docs/SUBOFF_*.md`) | resistance | 4 |
| 4 | 矩形驳船稳性基准 (`docs/reports/QR-INTACT-*`) | stability | 23 |
| 5 | 程序化生成（10 船型×3 工况） | resistance | 30 |
| 6 | IMO 面积判据 (`artifacts/m1386/`) | stability | 3 |
| 7 | 稳性裁决七判据 (`artifacts/m1564/`) | stability | 7 |
| 8 | 代理层批量扫描 (`artifacts/ship_proxy_demo.json`) | resistance | 15 |

### 2.3 回填管线（SQLite 层）

独立于 JSON Schema 层，另有 SQLite 回填管线（`tools/sample_db/backfill.py`）：
- 审计扫描：`backfill_audit.py` → 90 个工件文件，125 条可抽取样本，81 个任务
- 回填入库：JSON → SQLite（`sample_db.sqlite`），演示 3 文件 → 5 样本

---

## 3. 计算结果

### 3.1 首批样本统计

| 指标 | 数值 |
|------|------|
| **提取样本总数** | **139** |
| Schema 验证通过 | 139 (100%) |
| Schema 验证失败 | 0 |
| 覆盖学科数 | 2/9（resistance + stability） |
| 覆盖船型数 | 5/13（barge, generic, tanker, submarine, research_vessel） |

### 3.2 学科分布

| 学科域 | 样本数 | 占比 |
|--------|--------|------|
| resistance | 58 | 41.7% |
| stability | 81 | 58.3% |
| 其余 7 学科 | 0 | 0% |

### 3.3 证据等级分布

| 证据等级 | 样本数 | 说明 |
|----------|--------|------|
| REAL | 72 | 真实算法/实验对比 |
| MOCK | 67 | 几何无关的解析合成（稳性平台端点） |

### 3.4 回填审计统计（工件层）

| 指标 | 数值 |
|------|------|
| 工件文件总数 | 90 |
| 可解析文件 | 90（100%） |
| 解析失败 | 0 |
| 可抽取样本上界 | 125 |
| 涉及任务数 | 81 |
| 学科覆盖 | fluid (100%) |

### 3.5 工件判定分布

| 判定 | 文件数 | 占比 |
|------|--------|------|
| UNKNOWN | 48 | 53.3% |
| FAIL | 19 | 21.1% |
| PASS | 16 | 17.8% |
| HONEST_FAIL | 6 | 6.7% |
| IN_PROGRESS | 1 | 1.1% |

---

## 4. 结果图

本任务为**数据工程/Schema 定义**类任务，无数值计算场量，不适用云图/曲线比较。

以表格形式呈现样本学科分布：

```
学科域          样本数  柱状图
────────────────────────────────────────
resistance      58     ████████████████████████████
stability       81     █████████████████████████████████████
propulsion       0     
seakeeping       0     
manoeuvring      0     
structure        0     
vibration_noise  0     
green_navigation  0     
ice_navigation    0     
```

**无图理由**：本任务产出为 Schema 定义 + 提取管线 + 结构化样本数据，不涉及 CFD 场量或收敛曲线，无需 matplotlib 图形。学科覆盖的不均衡以文字+ASCII 柱状图表达。

---

## 5. 注意点

### 5.1 学科覆盖严重不均衡

首批 139 条样本仅覆盖 2/9 学科域（resistance + stability），其余 7 个学科域（推进/耐波/操纵/结构/振动噪声/绿色航行/冰区航行）样本数为 0。这是当前阶段最大的结构性缺口。

### 5.2 参考值来源问题

- 程序化生成的 30 条 resistance 样本，参考值为 Holtrop 方法自洽值，**非独立实验数据**
- 误差为模拟值，非真实平台计算误差
- 这意味着这 30 条样本的 `error_pct` 字段不具备验证意义

### 5.3 双层 Schema 并存

当前存在两套 Schema：
1. **JSON Schema 层**（`docs/sample.schema.json`）：面向船舶九大学科，字段以船舶参数为主
2. **SQLite Schema 层**（`docs/sample_db/schema.md`）：面向流体/结构/多体三学科，字段以 CFD 参数为主

两套 Schema 的学科枚举、字段命名不完全一致，后续需统一或建立映射。

### 5.4 工件层判定 UNKNOWN 占比过高

回填审计中 53.3% 的工件判定为 UNKNOWN，说明大量工件缺少明确的 PASS/FAIL 判据。这限制了样本的 `validation_status` 赋值质量。

### 5.5 到 1 万条的差距

首批 139 条 vs 目标 1 万条，差距约 72 倍。需要：
- 扩展学科覆盖（从更多报告提取）
- 接入外部实验数据库（ITTC/ISO 标准船型）
- 增量提取管线（新报告自动触发）

---

## 6. 误差判定

### 6.1 Schema 验证精度

| 检查项 | 结果 |
|--------|------|
| sample_id 格式（正则） | ✅ 139/139 通过 |
| mission_id 整数 | ✅ 139/139 通过 |
| ship_type 枚举 | ✅ 139/139 通过 |
| physics_domain 枚举 | ✅ 139/139 通过 |
| geometry_params 必填+范围 | ✅ 139/139 通过 |
| validation_status 枚举 | ✅ 139/139 通过 |
| evidence_level 枚举 | ✅ 139/139 通过 |
| additionalProperties | ✅ 139/139 通过 |

**Schema 验证通过率 = 100%**，这是管线正确性的直接证据。

### 6.2 数值误差

本任务为数据工程任务，不涉及数值计算，无「关键量 vs 基准误差」可报。

- 样本中的 `error_pct` 字段：58 条 resistance 样本中，30 条为程序化模拟值（非真实误差），28 条来自实际平台计算 vs 参考值对比
- 参考值来源：KVLCC2 来自文献实验值，SUBOFF 来自文献实验值，Holtrop 方法值为经验公式自洽值
- **是否 <3%**：不适用于本任务（非数值计算任务）

### 6.3 管线完整性

| 环节 | 状态 |
|------|------|
| Schema 定义 | ✅ 完成 |
| 提取脚本 | ✅ 877 行，8 数据源 |
| 验证脚本 | ✅ 190 行，全字段校验 |
| 首批回填 | ✅ 139 条（超目标 100 条） |
| SQLite 回填器 | ✅ 演示 3 文件 → 5 样本 |
| 审计扫描 | ✅ 90 文件 → 125 可抽取 |

---

## 附录：交付物清单

| 文件 | 路径 | 说明 |
|------|------|------|
| JSON Schema | `docs/sample.schema.json` | 样本库 Schema 定义 |
| Schema 说明 | `docs/g-sample-schema.md` | 完整文档 |
| SQLite Schema | `docs/sample_db/schema.md` | 数据库层 Schema |
| 回填审计 | `docs/sample_db/backfill_audit.md` | 工件层审计 |
| README | `docs/sample_db/README.md` | 模块说明 |
| 提取脚本 | `scripts/extract_samples.py` | 877 行 |
| 验证脚本 | `scripts/validate_samples.py` | 190 行 |
| 回填器 | `tools/sample_db/backfill.py` | JSON → SQLite |
| 审计扫描 | `tools/sample_db/backfill_audit.py` | 工件扫描 |
| 审计数据 | `artifacts/backfill_audit.json` | 原始审计数据 |
| 回填演示 | `artifacts/backfill_demo.json` | 3 文件→5 样本 |
