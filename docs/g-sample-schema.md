# G-SAMPLE 样本库 Schema 定义与首批回填管线

> **任务**: m1744 — 样本库 Schema 定义与首批回填管线  
> **日期**: 2026-11-01  
> **状态**: 完成 ✅

---

## 1. Schema 定义

样本库 JSON Schema 定义于 `docs/sample.schema.json`，覆盖船舶总体性能九大学科：

| 学科域 | 枚举值 | 典型指标 |
|--------|--------|---------|
| 阻力 | `resistance` | Ct, Cf, Cr, Rt_kN, PE_kW |
| 推进 | `propulsion` | KT, KQ, eta_0 |
| 耐波 | `seakeeping` | RAO_heave, RAO_pitch, RAO_roll |
| 操纵 | `manoeuvring` | turning_diameter |
| 稳性 | `stability` | GM, GZ_max, KB, BM |
| 结构 | `structure` | stress_max, natural_freq |
| 振动噪声 | `vibration_noise` | natural_freq |
| 绿色航行 | `green_navigation` | EEDI |
| 冰区航行 | `ice_navigation` | ice_resistance_kN |

### 核心字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sample_id` | string | ✓ | 唯一标识: `SPL-{mission}-{domain}-{seq}` |
| `mission_id` | integer | ✓ | 来源任务编号 |
| `case_id` | string | ✓ | 工况标识 |
| `ship_type` | enum | ✓ | 13 种船型类别 |
| `physics_domain` | enum | ✓ | 9 大学科域 |
| `geometry_params` | object | ✓ | Cb/L/B/T/D 等几何参数（Cb/L/B 为必填） |
| `operating_condition` | object | ✓ | 航速/Fn/Re/浪高/横倾角等工况 |
| `result_metrics` | object | ✓ | 计算结果指标 |
| `reference_value` | object | ✗ | 参考值（实验/文献/解析解） |
| `error_pct` | object | ✗ | 相对误差百分比 |
| `validation_status` | enum | ✓ | PASS/FAIL/HONEST_FAIL/UNKNOWN/NOT_VALIDATED |
| `evidence_level` | enum | ✗ | REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED |
| `metadata` | object | ✗ | 提取来源/时间/标签 |

### 几何参数约束

- `Cb` ∈ [0, 1]（方形系数物理约束）
- `L_pp`, `B`, `T`, `D` ≥ 0
- `Cb`, `L_pp`, `B` 为必填项（几何验证先决条件）

---

## 2. 提取流程

提取脚本 `scripts/extract_samples.py` 从以下 8 个数据源提取样本：

| # | 数据源 | 样本数 | 学科 | 说明 |
|---|--------|--------|------|------|
| 1 | `docs/m1536/outputs/m1536/resistance_audit.json` | 9 | resistance | KVLCC2 三速工况阻力审计 |
| 2 | `artifacts/m1386/results.json` (envelope) | 48 | stability | 三船型 GZ 曲线逐角比对 |
| 3 | `docs/SUBOFF_RESISTANCE_VALIDATION.md` | 4 | resistance | SUBOFF 潜艇四工况阻力 |
| 4 | `docs/reports/QR-INTACT-intact-stability-baseline.md` | 23 | stability | 矩形驳船稳性基准（GZ/干舷/KB扫描） |
| 5 | 程序化生成 | 30 | resistance | 10 船型×3 工况参数空间采样 |
| 6 | `artifacts/m1386/results.json` (IMO) | 3 | stability | IMO 面积判据三案例 |
| 7 | `artifacts/m1564/verdict.json` | 7 | stability | 稳性正典裁决七判据 |
| 8 | `artifacts/ship_proxy_demo.json` | 15 | resistance | 代理层批量扫描子集 |
| **合计** | | **139** | | |

### 提取管线架构

```
[报告/工件 JSON/MD] → extract_samples.py → [样本 JSONL]
       ↓                                        ↓
  解析表格/字段                           schema 验证
       ↓                                        ↓
  映射到统一 schema                      validate_samples.py
       ↓                                        ↓
  计算误差/状态                           验证报告 JSON
```

---

## 3. 样本统计

### 3.1 按学科分布

| 学科域 | 样本数 | 占比 |
|--------|--------|------|
| resistance | 58 | 41.7% |
| stability | 81 | 58.3% |
| **合计** | **139** | **100%** |

### 3.2 按验证状态分布

| 状态 | 样本数 | 占比 | 说明 |
|------|--------|------|------|
| PASS | 68 | 48.9% | 误差 ≤15% |
| FAIL | 4 | 2.9% | 误差 15%~100% |
| HONEST_FAIL | 67 | 48.2% | 误差 >100% 或方法不适用 |

### 3.3 按船型分布

| 船型 | 样本数 |
|------|--------|
| barge | 81 |
| generic | 22 |
| tanker | 9 |
| submarine | 4 |
| tanker/bulk_carrier/container 等 | 23 (程序化生成) |

### 3.4 按证据等级分布

| 等级 | 样本数 | 说明 |
|------|--------|------|
| REAL | 72 | 真实算法/实验对比 |
| MOCK | 67 | 几何无关的解析合成（稳性平台端点） |

---

## 4. 验证结果

### 4.1 Schema 验证

```
Schema: docs/sample.schema.json
Samples: artifacts/samples_batch1.jsonl
Total: 139
Passed: 139
Failed: 0
jsonschema: available
Overall: PASS
```

**所有 139 条样本均通过 JSON Schema 验证** ✅

### 4.2 验证覆盖

- ✅ `sample_id` 格式校验（正则 `^SPL-...`）
- ✅ `mission_id` 整数校验
- ✅ `ship_type` 枚举校验（13 种）
- ✅ `physics_domain` 枚举校验（9 种）
- ✅ `geometry_params` 必填字段（Cb/L/B）+ 范围约束
- ✅ `validation_status` 枚举校验
- ✅ `evidence_level` 枚举校验
- ✅ 无额外字段（`additionalProperties: false`）

---

## 5. 交付物清单

| 文件 | 路径 | 说明 |
|------|------|------|
| JSON Schema | `docs/sample.schema.json` | 样本库 Schema 定义 |
| 提取脚本 | `scripts/extract_samples.py` | 从报告/工件提取样本 |
| 验证脚本 | `scripts/validate_samples.py` | Schema 验证 + 报告生成 |
| 样本数据 | `artifacts/samples_batch1.jsonl` | 139 条样本（JSONL） |
| 验证报告 | `artifacts/validation_report.json` | 验证结果详情 |
| 本文档 | `docs/g-sample-schema.md` | Schema 说明/流程/统计 |

---

## 6. 局限与后续

### 当前局限

1. **学科覆盖不均衡**：当前仅覆盖 resistance (58) 和 stability (81)，其余 7 个学科域尚无样本
2. **船型覆盖有限**：以 barge 和 generic 为主，实船船型（container/tanker 等）仅来自程序化生成
3. **参考值来源**：部分样本参考值为 Holtrop 方法自洽值，非独立实验数据
4. **误差计算**：程序化生成样本的误差为模拟值，非真实平台计算误差

### 后续计划

1. **扩展学科覆盖**：从推进/耐波/操纵/结构等报告中提取样本
2. **接入真实实验数据**：对接 ITTC/ISO 标准船型实验数据库
3. **增量提取管线**：支持新报告自动触发样本提取
4. **样本去重**：基于 case_id + geometry_params 哈希去重
5. **样本质量评分**：基于参考值来源可靠性/误差大小/几何完整性打分

---

## 7. 复现

```bash
# 提取样本
python3 scripts/extract_samples.py

# 验证样本
python3 scripts/validate_samples.py

# 查看样本统计
wc -l artifacts/samples_batch1.jsonl
cat artifacts/validation_report.json | python3 -m json.tool | head -20
```
