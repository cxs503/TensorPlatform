# 否定性知识台账（Negative Knowledge Registry）

> P155W1-N03 实现文档

## 概述

否定性知识台账是 TensorPlatform 的跨域否定性知识登记、存储与检索系统。其核心目标是把已证伪假设炼成可查询知识库，避免重复踩坑。

### 设计原则

1. **零领域语义**：schema 通用，域数据经配置/工件注入
2. **幂等登记**：同一 (mission_id, node_id) 不重复登记
3. **语义检索**：基于字符 n-gram Jaccard + 包含度的文本相似度
4. **派单门**：新提案入池前强制检索相似否定记录

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        API Layer                                 │
│  /tensor/negative-knowledge/{register,search,gate,harvest,...}  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Search Engine                                │
│  • 文本归一化 (NFKC → 去排版记号 → 标点折叠 → casefold)         │
│  • 字符 n-gram 切分                                              │
│  • Jaccard + Containment 相似度计算                              │
│  • 派单门判决 (pass/review/reject)                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Store Layer                                 │
│  • SQLAlchemy Core (SQLite/PostgreSQL 双向兼容)                  │
│  • 幂等唯一键 (mission_id, node_id)                              │
│  • 状态管理 (active/superseded/retracted)                        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    negative_knowledge_ledger                     │
│  entry_id | mission_id | node_id | title | hypothesis | ...     │
└─────────────────────────────────────────────────────────────────┘
```

## Schema

### 否定性知识条目

| 字段 | 类型 | 说明 |
|------|------|------|
| entry_id | UUID | 业务唯一 ID |
| mission_id | int | 来源 mission（可空） |
| plan_id | int | 来源战役计划（可空） |
| node_id | string | 来源 DAG 节点（可空） |
| title | string | 假设/失败标题 |
| hypothesis | text | 被证伪的假设描述 |
| falsification_evidence | text | 证伪证据 |
| failure_reason | text | 失败根因 |
| failure_kind | enum | hypothesis_falsified / method_failed / blocked / infeasible / regression |
| applicable_conditions | JSON | 适用条件边界 |
| keywords | JSON[] | 关键词 |
| domain_tags | JSON[] | 域标签（零领域语义） |
| differentiation_required | JSON[] | 后续提案需声明的差异点 |
| source_artifacts | JSON[] | 来源工件路径 |
| confidence | float | 证伪置信度 [0,1] |
| status | enum | active / superseded / retracted |

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /tensor/negative-knowledge | 台账条目列表 |
| GET | /tensor/negative-knowledge/stats | 统计信息 |
| GET | /tensor/negative-knowledge/search | 语义检索 |
| GET | /tensor/negative-knowledge/{entry_id} | 单条详情 |
| POST | /tensor/negative-knowledge/register | 登记条目 |
| POST | /tensor/negative-knowledge/gate | 派单前检索门 |
| POST | /tensor/negative-knowledge/harvest | 从 mission 收割 |
| PATCH | /tensor/negative-knowledge/{entry_id}/status | 更新状态 |
| GET | /tensor/negative-knowledge/health | 健康检查 |

## 派单门判决逻辑

```
score = max(jaccard, containment_weight * containment)

if score >= gate_reject_threshold (0.6):
    → reject: 必须声明差异化
elif score >= gate_review_threshold (0.4):
    → review: 建议人工审查
else:
    → pass: 可入池
```

## 配置

配置文件：`config/negative_knowledge.json`

```json
{
  "table_name": "negative_knowledge_ledger",
  "schema_version": "1.0",
  "failure_kinds": ["hypothesis_falsified", "method_failed", "blocked", "infeasible", "regression"],
  "search": {
    "shingle_size": 4,
    "min_score_threshold": 0.25,
    "gate_reject_threshold": 0.6,
    "gate_review_threshold": 0.4,
    "containment_enabled": true,
    "containment_weight": 0.8,
    "min_length_ratio": 0.3,
    "top_k": 5
  }
}
```

## 使用示例

### 登记否定性知识

```python
POST /api/v1/tensor/negative-knowledge/register
{
  "title": "Lindqvist 冰阻力公式不适用于薄冰",
  "hypothesis": "Lindqvist 公式在冰厚 < 0.3m 时仍然准确",
  "failure_kind": "hypothesis_falsified",
  "falsification_evidence": "模型试验显示薄冰区误差 > 30%",
  "failure_reason": "公式假设冰为无限大平板，薄冰时边界效应显著",
  "keywords": ["Lindqvist", "冰阻力", "薄冰"],
  "domain_tags": ["ice_navigation"],
  "confidence": 0.95
}
```

### 派单前检索门

```python
POST /api/v1/tensor/negative-knowledge/gate
{
  "proposal_title": "Lindqvist 冰阻力公式在薄冰区的改进",
  "proposal_description": "改进 Lindqvist 公式在薄冰条件下的精度",
  "proposal_keywords": ["Lindqvist", "薄冰", "冰阻力"]
}

# 响应
{
  "verdict": {
    "passed": false,
    "action": "reject",
    "max_score": 0.72,
    "reason": "与已证伪假设高度相似（score=0.720 >= 0.6），必须声明差异化",
    "hits": [...]
  }
}
```

## 测试

```bash
pytest tests/test_negative_knowledge.py -v
```

覆盖：
- 配置装载（默认值 + 文件覆盖）
- 存储层 CRUD（登记/查询/状态更新/幂等）
- 检索引擎（相似度计算/检索/派单门判决）
- 集成测试（完整工作流）

## 文件清单

| 文件 | 说明 |
|------|------|
| tools/negative_knowledge/__init__.py | 模块入口 |
| tools/negative_knowledge/config.py | 配置装载 |
| tools/negative_knowledge/store.py | 存储层 |
| tools/negative_knowledge/search.py | 检索引擎 |
| backend/api/negative_knowledge.py | API 端点 |
| backend/api/domain/tensor.py | 路由注册（已更新） |
| config/negative_knowledge.json | 配置文件 |
| tests/test_negative_knowledge.py | 单元测试 |
