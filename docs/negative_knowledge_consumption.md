# 负知识注入消费端机制说明

## 概述

否定性知识台账（`negative_knowledge_ledger`）的消费端有两个主要接入点：

1. **派单前检索门（Gate）**：在提案进入任务池前，自动检索相似否定知识
2. **Prompt 注入（Prompt Injection）**：在 agent 执行任务时，将相关否定知识注入系统 prompt

---

## 1. 派单前检索门（Gate）

### API 端点

```
POST /tensor/negative-knowledge/gate
```

### 请求体

```json
{
  "proposal_title": "提案标题",
  "proposal_description": "提案详细描述",
  "proposal_keywords": ["关键词1", "关键词2"],
  "mission_id": 12345
}
```

### 响应

```json
{
  "verdict": {
    "action": "pass|review|reject",
    "score": 0.75,
    "hits": [
      {
        "entry_id": "nk-001",
        "title": "Sobol' 低差异点集不可用于 pick-freeze A/B 基底",
        "score": 0.82,
        "differentiation_required": ["必须使用随机/伪随机序列而非低差异序列作为 A/B 基底"]
      }
    ]
  },
  "thresholds": {
    "reject_threshold": 0.8,
    "review_threshold": 0.5
  }
}
```

### 判决逻辑

| action | 条件 | 含义 |
|--------|------|------|
| `pass` | 最高相似度 < review_threshold | 无相似否定知识，可入池 |
| `review` | review_threshold ≤ 最高相似度 < reject_threshold | 中度相似，建议人工审查 |
| `reject` | 最高相似度 ≥ reject_threshold | 高度相似，必须声明差异化 |

### 代码入口

- `backend/api/negative_knowledge.py` → `gate_check()` 端点
- `tools/negative_knowledge/search.py` → `NegativeKnowledgeSearcher.gate_check()`

---

## 2. Prompt 注入机制

### 注入点

否定知识通过以下路径注入 agent 的 system prompt：

```
提案文本 → 语义检索 → 命中条目 → 渲染为 PLAYBOOK 块 → 注入 prompt
```

### 渲染格式

命中的否定知识条目被渲染为以下格式注入 prompt：

```markdown
## ⚠️ 相关否定性知识（来自台账）

### [nk-001] Sobol' 低差异点集不可用于 pick-freeze A/B 基底
- **假设**：使用两条独立的置乱 Sobol' 序列作为 pick-freeze Sobol' 估计的 A/B 样本基底可以得到准确的闭合性估计
- **证伪证据**：两条置乱 Sobol' 网之间存在相关结构，闭合性样本间散度从 ±24% 恶化到 ±500%
- **失败根因**：低差异序列的确定性结构导致 A/B 样本间存在隐藏相关性
- **后续提案需声明的差异点**：
  - 必须使用随机/伪随机序列而非低差异序列作为 A/B 基底
- **来源**：mission 7297
- **置信度**：0.95
```

### 注入规则

1. **检索**：用提案标题+描述+关键词做语义检索（TF-IDF + 关键词匹配）
2. **过滤**：只注入 `status=active` 且 `score ≥ 0.3` 的条目
3. **排序**：按相似度降序，最多注入 5 条
4. **格式**：每条包含标题、假设、证伪证据、失败根因、差异化要求、来源、置信度
5. **位置**：注入到 system prompt 的「已知陷阱」章节

### 代码入口

- `tools/negative_knowledge/search.py` → `NegativeKnowledgeSearcher.search()`
- `tools/negative_knowledge/search.py` → `render_prompt_block()`（待实现）

---

## 3. 与 Failure Postmortems 的关系

否定性知识台账与失败复盘（`failure_postmortems`）是互补机制：

| 维度 | negative_knowledge_ledger | failure_postmortems |
|------|--------------------------|---------------------|
| 粒度 | 单条假设/方法/路径 | 完整 mission 失败链 |
| 结构 | 假设+证伪证据+根因 | 七环+五类+规则 |
| 消费 | 派单前 gate + prompt 注入 | PLAYBOOK append + 规则检索 |
| 定稿 | 登记即生效 | 需 challenger 双闸定稿 |

两者共享同一套检索基础设施，但台账更轻量、更即时。

---

## 4. 检索算法

### 语义检索（当前实现）

```python
def search(self, query: str, top_k: int = 5, min_score: float = 0.2):
    # 1. TF-IDF 向量化
    # 2. 余弦相似度计算
    # 3. 关键词加权（精确匹配关键词得分 ×1.5）
    # 4. 按 score 降序返回 top_k
```

### PostgreSQL 全文检索（生产环境）

```sql
SELECT entry_id, title, failure_kind,
       ts_rank(search_vector, plainto_tsquery('simple', :query)) AS score
FROM negative_knowledge_ledger
WHERE status = 'active'
  AND search_vector @@ plainto_tsquery('simple', :query)
ORDER BY score DESC
LIMIT :top_k;
```

---

## 5. 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `gate_reject_threshold` | 0.8 | 拒绝阈值 |
| `gate_review_threshold` | 0.5 | 审查阈值 |
| `search_min_score` | 0.2 | 检索最低得分 |
| `prompt_inject_max_entries` | 5 | prompt 注入最大条目数 |
| `prompt_inject_min_score` | 0.3 | prompt 注入最低得分 |

配置文件：`config/negative_knowledge.json`（待创建）

---

## 6. 审计与追溯

每条注入到 prompt 的否定知识都记录：

- `entry_id`：台账条目 ID
- `referencing_mission_id`：引用该条目的 mission
- `injection_timestamp`：注入时间
- `score`：检索得分

这些记录可用于分析哪些否定知识被频繁引用，哪些已被淘汰。

---

## 7. 数据流图

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  提案文本    │────▶│  语义检索     │────▶│  判决 (gate)     │
│  (title+    │     │  (TF-IDF +   │     │  pass/review/   │
│   desc+kws) │     │   关键词)     │     │  reject)         │
└─────────────┘     └──────┬───────┘     └────────┬────────┘
                           │                      │
                           ▼                      ▼
                  ┌─────────────────┐    ┌─────────────────┐
                  │  negative_      │    │  任务池/人工     │
                  │  knowledge_     │    │  审查            │
                  │  ledger         │    └─────────────────┘
                  └─────────────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │  Prompt 注入     │
                  │  (system prompt  │
                  │   已知陷阱章节)   │
                  └─────────────────┘
```
