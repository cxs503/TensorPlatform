# 提案门（Proposal Gate）架构设计

## 概述

提案门是 TensorPlatform 的**提案→战役播种供给链机器门**，实现三道自动化仲裁：

1. **契约门**：校验提案 schema 合规性（v1/v2 双版本），失败 → 隔离区（quarantined）
2. **去重门**：n-gram 文本相似度检测，同义改写自动拦截（duplicate / near_duplicate）
3. **覆盖率账本**：采纳提案 ↔ 战役节点的播种覆盖率追踪

## 核心原则

- **零领域语义**：所有阈值/白名单/词表来自 `config/proposal_gate.json`，代码内无散落常量
- **fail-closed**：配置缺失/契约失败 → 隔离区，不静默降级
- **幂等保证**：`(mission_id, sha256)` 唯一键，重复提交不新增行
- **SQL 溯源**：所有统计/账本查询可 SQL 复算，零手抄

## 架构分层

```
┌─────────────────────────────────────────────────────────┐
│                    API 层 (FastAPI)                      │
│  GET  /proposals/gate/stats                             │
│  GET  /proposals/gate/ledger                            │
│  POST /proposals/gate/evaluate                          │
│  POST /proposals/gate/adopt                             │
│  GET  /proposals/gate/coverage                          │
│  GET  /proposals/gate/config                            │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                  服务层 (Service)                        │
│  ProposalGateService.evaluate()                         │
│  ProposalGateService.adopt()                            │
│  ProposalGateService.stats() / ledger() / coverage()    │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                  核心判定层 (Gate)                       │
│  ProposalGate.evaluate()                                │
│    1. 契约门 → validate_proposal()                      │
│    2. 幂等门 → ledger.get(mission_id, sha256)           │
│    3. 去重门 → index.best_text_match(doc)               │
│    4. 孪生门 → dependency overlap check                 │
│    5. 落账 → ledger.record()                            │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              基础设施层 (Config/Index/Ledger)            │
│  GateConfig: 配置装载（点号取值，fail-closed）           │
│  ProposalIndex: n-gram 索引 + 文本相似度                │
│  LedgerStore: SQLite/PostgreSQL 账本持久化              │
└─────────────────────────────────────────────────────────┘
```

## 判定流程

```
提案载荷
    │
    ▼
┌─────────────┐
│  契约门     │ ← schema 校验（v1/v2 双版本）
│  (fail-     │
│   closed)   │
└──────┬──────┘
       │ OK
       ▼
┌─────────────┐
│  幂等门     │ ← (mission_id, sha256) 查重
└──────┬──────┘
       │ 新内容
       ▼
┌─────────────┐
│  去重门     │ ← n-gram 相似度 ≥ dup_threshold → duplicate
│             │ ← [review_threshold, dup_threshold) → near_duplicate
└──────┬──────┘
       │ 通过
       ▼
┌─────────────┐
│  孪生门     │ ← 同 repo + 依赖重合 ≥ twin_dependency_overlap → twin
└──────┬──────┘
       │ 通过
       ▼
┌─────────────┐
│  accepted   │ → 落账 + 入索引
└─────────────┘
```

## 四态模型

| 判定结果 (verdict) | 账本状态 (state) | 说明 |
|---|---|---|
| accepted | proposal | 通过所有门，进入提案池 |
| adopted | adopted | 被战役播种采纳 |
| duplicate | dedup_rejected | 重复提案（同义改写/跨 mission 重复） |
| twin | dedup_rejected | 结构化孪生（同 repo + 依赖重合） |
| near_duplicate | quarantined | 复核带（待人工复核） |
| quarantined | quarantined | 契约失败（隔离区） |

## 配置参数

全部参数在 `config/proposal_gate.json`：

| 路径 | 默认值 | 说明 |
|---|---|---|
| dedup.dup_threshold | 0.85 | 重复判定阈值 |
| dedup.review_threshold | 0.55 | 复核带下界 |
| dedup.shingle_size | 3 | n-gram 大小 |
| dedup.twin_dependency_overlap | 0.8 | 孪生依赖重合阈值 |
| dedup.coverage_match_threshold | 0.85 | 覆盖率匹配阈值 |
| contract.min_criteria | 3 | 最少判据条数 |
| ledger.table | proposal_gate_ledger | 账本表名 |

## 测试策略

- **合成域名**：foo/bar/widget，零学科域词
- **内存账本**：SQLite :memory:，无需真实 DB
- **边界用例**：缺失字段/类型错误/同义改写/不同主题/孪生依赖
- **反作弊**：同义改写族 (b) 检出 + 不同主题不误判

## 文件清单

| 文件 | 职责 |
|---|---|
| backend/api/proposal_gate.py | FastAPI 路由 |
| backend/services/proposal_gate_service.py | 服务层封装 |
| backend/tests/test_proposal_gate.py | API 端到端测试 |
| tools/proposal_gate/gate.py | 核心判定逻辑 |
| tools/proposal_gate/contract.py | 契约校验 |
| tools/proposal_gate/index.py | n-gram 索引 |
| tools/proposal_gate/ledger.py | 账本持久化 |
| tools/proposal_gate/config.py | 配置装载 |
| tools/proposal_gate/textsim.py | 文本相似度 |
| config/proposal_gate.json | 全部参数 |
| tests/proposal_gate_support.py | 测试夹具 |
