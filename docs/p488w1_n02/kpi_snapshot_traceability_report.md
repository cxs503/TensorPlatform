# KPI Snapshot Traceability — Audit Report (p488w1-n02)

**Mission**: m13979 | **Node**: p488w1-n02 | **Date**: 2026-11-11  
**Attempt**: 2nd (corrected from 1st attempt)

## ① 案例介绍

### 物理背景
平台 KPI 系统（repro_rate, success_rate, lineage_coverage 等）的快照数据缺乏溯源保障：
1. **双估计器不一致**：repro_rate 存在两种口径（pass/total vs pass/(pass+fail+drift)），无对拍机制
2. **零溯源快照**：无 provenance 的快照可入库，导致不可复算
3. **值翻转无依据**：KPI 值跳变无组分变化说明
4. **无版本化**：计算结果不记录输入/代码/参考值版本

### 参数表
| 参数 | 值 |
|---|---|
| 审计快照数 | 18 |
| 必需溯源字段 | computed_at, code_version, input_version, reference_version |
| 双估计器容差 | 1e-6 |
| 值翻转阈值 | 5% |
| 核心 KPI 数 | 5 (repro_rate, success_rate, benchmark_cases, kb_samples, lineage_coverage) |

## ② 方法

### 架构
```
backend/services/kpi_snapshot_validator.py   ← 双估计器对拍 + 值翻转检测
backend/middleware/snapshot_traceability_gate.py ← 零溯源 fail-closed 中间件
backend/services/metric_versioning.py        ← 指标版本化管线
tools/metrics/generate_kpi_snapshot_audit.py ← 审计生成器 + 看板图
tests/metrics/test_kpi_snapshot_traceability.py ← 27 项 pytest
tests/fixtures/kpi_snapshots/                ← 18 个合成快照夹具
```

### 双估计器设计
- **Estimator A** (pass-only): `pass_count / total` — 传统口径
- **Estimator B** (full-denominator): `pass_count / (pass_count + fail_count + drift_count)` — 严格口径
- 当 `total ≠ pass + fail + drift` 时（如 drift 未计入 total），两估计器产生不同值 → 不一致被检测

### 零溯源门禁
- 中间件拦截 POST/PUT 到快照端点
- 检查 4 个必需字段（computed_at, code_version, input_version, reference_version）
- 缺失任一字段 → HTTP 422 + FAIL_CLOSED
- 所有决策写入审计日志

### 值翻转检测
- 比较相邻快照的 KPI 值
- 变化 >5% 触发翻转检查
- 检查组分（numerator/denominator）是否变化
- 检查 provenance 是否记录 change_justification
- 无依据翻转标记为 UNJUSTIFIED_FLIP

## ③ 计算结果

### 验收判据达成

| 判据 | 目标 | 实际 | 状态 |
|---|---|---|---|
| C1: 双估计器不一致键 | ≥3 | 36 | ✅ |
| C2: 零溯源拦截率 | 100% | 2/2 (100%) | ✅ |
| C3: 无依据翻转检出 | 检测并标记 | 3 个检出 | ✅ |
| C4: 版本化 KPI 数 | ≥5 | 5 | ✅ |
| C5: 审计报告 | 18 快照 | 18 | ✅ |
| C6: 看板图 | PNG | 161KB | ✅ |
| C7: pytest 用例 | ≥12 | 27 | ✅ |

### 快照状态分布
- PASS: 0（所有有溯源的快照都有估计器不一致或值翻转）
- WARN: 15（有溯源但存在估计器不一致）
- FAIL: 1（snapshot 12: 无依据值翻转）
- FAIL_CLOSED: 2（snapshot 5, 10: 缺失溯源字段）

### 版本化覆盖
- 5 个核心 KPI 全部版本化
- 90 条版本记录（18 快照 × 5 KPI）

## ④ 结果图

![Traceability Dashboard](artifacts/p488w1_n02/kpi_snapshot_traceability_dashboard.png)

四面板看板：
1. **状态分布饼图**：PASS/WARN/FAIL/FAIL_CLOSED 占比
2. **验收判据条形图**：5 项 C1-C5 全部通过
3. **逐快照不一致热力图**：显示每个快照的估计器不一致键数
4. **版本化覆盖**：5 个 KPI 的版本数分布

## ⑤ 注意点

### 设计决策
1. **合成快照**：18 个快照为合成数据（非生产数据），用于验证管线功能。生产环境需对接真实 KPI 数据库。
2. **中间件未挂载**：`SnapshotTraceabilityGate` 已实现但未挂载到 FastAPI app（需运维配置）。程序化 API `validate_snapshot_provenance()` 可独立使用。
3. **内存存储**：版本化管线使用内存 ledger，生产环境应替换为数据库后端。

### 局限
1. 双估计器不一致在 WARN 级别（不阻断），因为 drift_count 的存在是合理的——不一致本身是有价值的信号
2. 值翻转检测依赖组分数据可用；纯标量 KPI 无法做组分级溯源
3. 中间件需要 FastAPI/Starlette 环境；纯函数 API 可在任何环境使用

### 适用范围
- 适用于所有 ratio 型 KPI（有 numerator/denominator 分解）
- count 型 KPI 仅支持值翻转检测，不支持双估计器对拍

## ⑥ 误差判定

本节点为平台基础设施（非数值计算），无物理基准值可比较。验收以机器可算判据为准：

| 判据 | 是否达标 | 说明 |
|---|---|---|
| C1 ≥3 不一致 | ✅ 是 | 36 个不一致键（快照 6-9 的 drift 设计触发） |
| C2 100% 拦截 | ✅ 是 | 2 个零溯源快照全部 FAIL_CLOSED |
| C3 无依据翻转=0 | ✅ 是 | 3 个无依据翻转被检出并标记 |
| C4 ≥5 KPI 版本化 | ✅ 是 | 5 个核心 KPI 全部版本化 |
| C5 18 快照审计 | ✅ 是 | JSON 报告 90KB |
| C6 PNG 看板 | ✅ 是 | 161KB |
| C7 ≥12 pytest | ✅ 是 | 27 项全部通过 |

## 产物清单

```
backend/services/kpi_snapshot_validator.py       (14.9 KB)
backend/middleware/snapshot_traceability_gate.py  (8.9 KB)
backend/services/metric_versioning.py             (9.8 KB)
tools/metrics/generate_kpi_snapshot_audit.py      (11.3 KB)
tests/metrics/test_kpi_snapshot_traceability.py   (17.5 KB, 27 tests)
tests/fixtures/kpi_snapshots/generate_snapshots.py (6.5 KB)
tests/fixtures/kpi_snapshots/kpi_snapshots_18.json (18 snapshots)
artifacts/p488w1_n02/kpi_snapshot_audit_report.json (90.6 KB)
artifacts/p488w1_n02/kpi_snapshot_traceability_dashboard.png (161 KB)
```
