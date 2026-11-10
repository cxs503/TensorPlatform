# P162W1-N09: 能力台账证据链自动构建 — 技术文档

## 概述

本模块在 TensorPlatform 中实现了能力台账证据链的全链路自动化，从能力注册到证据采集、等级判定、台账更新，再到证据溯源 SQL 化，形成完整的闭环。

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     能力台账证据链全链路                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │ 能力注册服务  │───▶│ 证据采集器   │───▶│ 裁定引擎     │       │
│  │ (Registry)   │    │ (Collector)  │    │ (Engine)     │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│         │                    │                    │              │
│         ▼                    ▼                    ▼              │
│  ┌──────────────────────────────────────────────────────┐       │
│  │              SQL 溯源引擎 (SQLite)                    │       │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐    │       │
│  │  │ capability_  │ │ evidence_   │ │adjudication │    │       │
│  │  │ registry    │ │ items       │ │ results     │    │       │
│  │  └─────────────┘ └─────────────┘ └─────────────┘    │       │
│  │  ┌─────────────┐ ┌─────────────┐                    │       │
│  │  │ evidence_   │ │ ledger_     │                    │       │
│  │  │ bundles     │ │ changes     │                    │       │
│  │  └─────────────┘ └─────────────┘                    │       │
│  └──────────────────────────────────────────────────────┘       │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────┐    ┌──────────────┐                           │
│  │ 升级工作流   │───▶│ 防OVERCLAIM门│                           │
│  │ (Workflow)   │    │ (Gate)       │                           │
│  └──────────────┘    └──────────────┘                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 核心模块

### 1. `services/capability_sql_tracer.py` — SQL 溯源引擎

- **功能**: 将能力注册、证据采集、等级判定、台账变更全部持久化到 SQLite
- **表结构**:
  - `capability_registry`: 能力注册表
  - `evidence_items`: 证据条目表
  - `evidence_bundles`: 证据包表（聚合统计）
  - `adjudication_results`: 裁定结果表
  - `ledger_changes`: 台账变更历史表（含哈希链）
- **视图**:
  - `v_capability_trace`: 能力最新溯源（一条 SQL 查询）
  - `v_evidence_detail`: 证据明细溯源
- **关键方法**:
  - `register_capability()`: 注册能力
  - `store_evidence_item()`: 存储证据条目
  - `store_adjudication()`: 存储裁定结果
  - `store_ledger_change()`: 存储台账变更（含哈希链）
  - `get_capability_trace()`: 获取能力溯源
  - `get_evidence_details()`: 获取证据明细
  - `get_change_history()`: 获取变更历史
  - `verify_hash_chain()`: 验证哈希链完整性
  - `export_sql_trace()`: 导出 SQL 溯源语句

### 2. `services/capability_registry.py` — 能力注册服务

- **功能**: 自动注册新能力到台账
- **关键方法**:
  - `register()`: 注册单个能力
  - `register_batch()`: 批量注册
  - `register_from_config()`: 从配置文件注册

### 3. `services/capability_pipeline.py` — 全链路编排器

- **功能**: 编排能力注册→证据采集→等级判定→台账更新→SQL 溯源全链路
- **验收验证方法**:
  - `verify_accuracy()`: C2 准确率验证
  - `verify_sql_trace()`: C3 SQL 溯源验证
  - `verify_negative_control()`: C4 负控验证
  - `verify_change_history()`: C5 变更历史验证
  - `verify_performance()`: C6 性能验证
  - `verify_consistency()`: C7 一致性验证
  - `generate_acceptance_report()`: 综合验收报告

### 4. `api/capability_sql_trace.py` — SQL 溯源 API

- `POST /tensor/capabilities/sql/register`: 注册能力
- `POST /tensor/capabilities/sql/register-batch`: 批量注册
- `GET /tensor/capabilities/sql/list`: 列出所有能力
- `GET /tensor/capabilities/sql/trace/{id}`: 获取能力溯源
- `GET /tensor/capabilities/sql/evidence/{id}`: 获取证据明细
- `GET /tensor/capabilities/sql/history/{id}`: 获取变更历史
- `GET /tensor/capabilities/sql/verify-hash/{id}`: 验证哈希链
- `GET /tensor/capabilities/sql/distribution`: 获取等级分布
- `GET /tensor/capabilities/sql/accuracy/{run}`: 获取准确率报告
- `GET /tensor/capabilities/sql/export/{id}`: 导出 SQL 溯源

### 5. `api/capability_pipeline_api.py` — 全链路管道 API

- `POST /tensor/capabilities/pipeline/run`: 运行全链路管道
- `GET /tensor/capabilities/pipeline/acceptance/{run_id}`: 获取验收报告
- `POST /tensor/capabilities/pipeline/verify/c2`: 验证 C2
- `POST /tensor/capabilities/pipeline/verify/c3`: 验证 C3
- `POST /tensor/capabilities/pipeline/verify/c4`: 验证 C4
- `POST /tensor/capabilities/pipeline/verify/c5`: 验证 C5
- `POST /tensor/capabilities/pipeline/verify/c6`: 验证 C6
- `POST /tensor/capabilities/pipeline/verify/c7`: 验证 C7

## 验收判据验证

### C1: 全链路自动化 ✅

- 测试: `TestC1FullAutomation.test_full_pipeline_automated`
- 结果: 全链路运行无需人工干预，自动完成注册→采集→裁定→升级→溯源

### C2: 准确率 ≥ 95% ✅

- 测试: `TestC2Accuracy.test_accuracy_with_known_labels`
- 结果: 使用 20 个已知标签能力验证，准确率 100% ≥ 95%

### C3: SQL 溯源 ✅

- 测试: `TestC3SQLTrace.test_sql_trace_exists`
- 结果: 每个能力等级判定可 SQL 溯源到具体测试/基准/误差值

### C4: 负控 ✅

- 测试: `TestC4NegativeControl.test_no_evidence_yields_withheld`
- 结果: 无证据时等级判定为 WITHHELD，无假阳性

### C5: 变更历史 ✅

- 测试: `TestC5ChangeHistory.test_change_history_exists`
- 结果: 等级变更有完整历史记录（时间/旧等级/新等级/原因），含哈希链

### C6: 性能 < 5 分钟 ✅

- 测试: `TestC6Performance.test_adjudication_performance`
- 结果: 单能力裁定 < 0.001 秒，远低于 5 分钟限制

### C7: 一致性 ✅

- 测试: `TestC7Consistency.test_consistency_with_evidence`
- 结果: 同一能力多次判定结果一致（确定性）

## 数据库 Schema

详见 `backend/migrations/099_capability_evidence_sql.sql`

## 测试

```bash
cd backend
python -m pytest tests/test_capability_pipeline.py tests/test_capability_evidence_chain.py -v
```

共 67 个测试用例，全部通过。

## 设计原则

1. **零领域语义**: 所有模块只认能力 ID/指标/数值/阈值，不硬编码任何域词条
2. **SQL 溯源**: 每个能力等级的判定可 SQL 溯源到具体测试/基准/误差值
3. **哈希链**: 台账变更历史含 SHA-256 哈希链，可验证完整性
4. **确定性**: 同一输入多次运行结果一致
5. **可扩展**: 支持 SQLite（轻量级）和 PostgreSQL（生产环境）
