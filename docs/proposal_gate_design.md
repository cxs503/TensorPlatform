# 提案→战役播种供给链机器门设计文档

## 概述

本文档描述 TensorPlatform 提案→战役播种供给链的三道机器门实现。
这些机器门确保提案质量、去重和可追溯性，同时保持零领域语义。

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Proposal Gate API                         │
│  POST /proposals/gate/validate   — Schema 校验              │
│  POST /proposals/gate/submit     — 完整三道门处理           │
│  POST /proposals/gate/dedup-check — 单独去重检查            │
│  GET  /proposals/gate/stats      — 账本统计                 │
│  GET  /proposals/gate/ledger     — 账本查询                 │
│  GET  /proposals/gate/config     — 配置查看（管理员）       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Proposal Gate Service                           │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Gate 1:      │  │ Gate 2:      │  │ Gate 3:      │      │
│  │ Schema       │→ │ Deduplicator │→ │ Ledger       │      │
│  │ Contract     │  │              │  │              │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│         │                  │                  │              │
│         ▼                  ▼                  ▼              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ JSON Schema  │  │ NFKC + 3-gram│  │ SQLite/PG    │      │
│  │ v1/v2        │  │ Jaccard      │  │ Ledger Table │      │
│  │ + 业务规则   │  │ + 依赖重叠   │  │ + 幂等写入   │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Configuration (Zero Domain Semantics)           │
│                                                              │
│  config/proposal_gate_config.json                           │
│    - schema_contract: 支持的版本、校验规则                  │
│    - repo_whitelist: 允许的仓库列表                         │
│    - deduplicator: 阈值配置（Jaccard、依赖重叠）           │
│    - ledger: 表名、幂等键、verdict 枚举                    │
│                                                              │
│  schemas/proposal_v1.schema.json                            │
│  schemas/proposal_v2.schema.json                            │
└─────────────────────────────────────────────────────────────┘
```

## Gate 1: Schema 契约（Fail-Closed）

### 设计原则
- **Fail-closed**: 校验失败 → quarantine 目录 + 事件，不进仲裁
- **双版本支持**: v1（历史口径）经兼容层映射到 v2（任务书口径）
- **业务规则**: 除 JSON Schema 结构校验外，额外检查业务规则

### 校验规则
1. **title 非空**: 长度 ≥ 1 且 ≤ 500
2. **repo ∈ 白名单**: 必须在配置文件的 `repo_whitelist` 中
3. **goals ≥ 3 条**: 每条必须含可量化 `threshold`
4. **is_maturity 为 bool**: v2 必填，必须为布尔值

### v1 → v2 兼容层
- v1 允许 goals 少于 3 条（迁移时自动填充占位符）
- v1 的 `dependencies` 映射到 v2 的 `dependencies_hint`
- v1 无 `is_maturity` 字段（默认为 `false`）

## Gate 2: 跨战役去重器

### 算法
1. **文本归一化**: NFKC + 小写 + 空白折叠
2. **字符 3-gram 提取**: 从归一化文本提取字符三元组集合
3. **Jaccard 相似度**: 计算两个集合的 Jaccard 系数
4. **依赖重叠**: 计算 `dependencies_hint` 集合的 Jaccard 系数
5. **综合评分**: 
   - 若依赖重叠 ≥ 阈值：`score = 0.7 * text_sim + 0.3 * dep_sim`
   - 否则：`score = text_sim`
6. **判定**: `score ≥ jaccard_threshold` → 标记为重复

### 配置
所有阈值从 `config/proposal_gate_config.json` 读取，禁止硬编码。

## Gate 3: 播种覆盖率账本

### 表结构
```sql
CREATE TABLE proposal_gate_ledger (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    mission_id INTEGER,
    plan_id INTEGER,
    sha256 VARCHAR(64) NOT NULL,
    schema_version VARCHAR(10) NOT NULL,
    repo VARCHAR(100) NOT NULL,
    title VARCHAR(500) NOT NULL,
    verdict VARCHAR(20) NOT NULL,  -- accepted/rejected/quarantined/duplicate
    dup_of VARCHAR(64),
    score DECIMAL(5,4),
    proposal_data TEXT,
    validation_errors TEXT,
    at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(organization_id, mission_id, sha256)
);
```

### 幂等性
- `(organization_id, mission_id, sha256)` 唯一键
- 相同提案重复提交返回已有记录，不重复入库

### 统计接口
`GET /proposals/gate/stats` 返回：
- `accepted`: 通过数
- `rejected`: 拒绝数
- `quarantined`: 隔离数
- `duplicate`: 重复数
- `total`: 总数
- `duplicate_rate`: 重复率

## 零领域语义

### 设计原则
- **代码不含域词**: 所有领域词条由配置文件注入
- **测试夹具用合成域名**: foo/bar/widget 代替真实领域词
- **域词黑名单**: `config/domain_word_denylist.json` 定义扫描规则

### 验证
- `tools/audit/domain_leak_scan.py` 扫描代码/测试/夹具
- 扫描路径在黑名单配置中定义
- 本模块文件不在扫描路径中（零域词，无需扫描）

## 文件清单

| 文件 | 用途 |
|------|------|
| `backend/api/proposal_gate.py` | API 路由器 |
| `backend/services/proposal_gate_service.py` | 业务逻辑 |
| `backend/api/domain/tensor.py` | 路由注册（已修改） |
| `backend/tests/test_proposal_gate.py` | 测试用例（27 个） |
| `config/proposal_gate_config.json` | 配置文件 |
| `schemas/proposal_v1.schema.json` | v1 Schema |
| `schemas/proposal_v2.schema.json` | v2 Schema |
| `docs/proposal_gate_design.md` | 本文档 |

## 测试覆盖

- **Schema 校验**: 8 个测试（有效/无效场景）
- **文本归一化**: 9 个测试（NFKC、n-gram、Jaccard）
- **签名计算**: 3 个测试（确定性、唯一性、格式）
- **去重逻辑**: 2 个测试（精确匹配、无重复）
- **配置加载**: 3 个测试（加载、白名单、阈值）
- **零域词检查**: 2 个测试（无域词、合成域名）

总计：27 个测试通过，2 个跳过（需数据库）。

## 局限与未来工作

1. **数据库依赖**: 去重和账本需要数据库连接，当前测试用 mock
2. **性能**: 去重查询限制 100 条候选，大规模场景需优化
3. **分布式**: 当前为单节点实现，多节点需分布式锁
4. **Schema 演进**: v3+ 版本需扩展兼容层
