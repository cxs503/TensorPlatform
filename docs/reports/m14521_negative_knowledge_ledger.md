# 负知识账本建设 · m14521 进展报告

## 1. 案例介绍

### 背景
owner 2026-11-11 质量审计令配套任务：QA pending 8699 篇的负知识沉淀通道。前作 m14355 无声失败，本单接力 refill。

### 目标
交付 `negative_knowledge_ledger` 真实入账——把 11-04 以来判伪/FAIL/honest-FAIL 案例逐条结构化落表（场景/根因/防再犯规则/证据指针），≥30 条真材实料非凑数；配套消费端=负知识注入 prompt 的机制接入点说明。

### 判据
- `SELECT count(*) FROM negative_knowledge_ledger ≥ 30`
- 每条有证据指针（`falsification_evidence` 非空）
- `failure_kind` 在允许值内
- `status` 在允许值内

---

## 2. 怎么做的

### 数据来源
从以下证据源提取负知识条目：

| 来源 | 条目数 | 说明 |
|------|--------|------|
| agent_memory/m7297 | 4 | UQ 框架 TRAPS（Sobol'/闭合性/Jacobian/区间宽度） |
| agent_memory/m9049 | 7 | 先验校准器 TRAPS（Platt/先验漂移/BLAS/psql/右删失/字体/nohup/BSS/确定性） |
| agent_memory/m9356 | 7 | 跨架构门 TRAPS（零值扰动/抵消型/NaN/符号零/统计vs严格/FMA/排队） |
| agent_memory/m200 | 1 | Soul 迁移 checksum |
| agent_memory/m311 | 3 | 质量门 R3/R4/fail-open |
| runs/m591/verdict.json | 1 | DTMB 5415 稳性 82% 误差 |
| validation_report.json | 1 | Pydantic v2 500 错误 |
| quality/delivery_semantics | 2 | 分类器 F1 低/漂移衰减 |
| quality/criteria_lint | 6 | 判据 lint 9 类中 6 类 |
| quality/scorer_robustness | 1 | 模型评估加速比失真 |
| quality/maturity | 1 | 物理开关覆盖不全 |
| **合计** | **35** | |

### 处理流程
1. 读取各证据源（agent_memory context.md、validation reports、quality evidence）
2. 提取 TRAPS/FAIL 案例，结构化为本体字段
3. 按 `NegativeKnowledgeEntry` schema 组装
4. 写入 JSON 台账文件
5. 用加载器脚本入库（SQLite/PostgreSQL 兼容）
6. 验证脚本确认 ≥30 条且每条有证据指针

### 工具链
- `scripts/load_negative_knowledge_ledger.py`：批量加载器（SQLite/PostgreSQL 兼容）
- `scripts/verify_negative_knowledge_ledger.py`：验证脚本
- `tests/test_negative_knowledge.py`：15 条单元测试（全绿）
- `backend/migrations/120_negative_knowledge_ledger.sql`：PostgreSQL 迁移
- `docs/negative_knowledge_consumption.md`：消费端机制说明

---

## 3. 计算结果

### 台账统计

| 指标 | 值 |
|------|-----|
| 总条目数 | 35 |
| 有证据指针 | 35/35 (100%) |
| 有 source_artifacts | 35/35 (100%) |
| 有 keywords | 35/35 (100%) |
| 有 differentiation_required | 35/35 (100%) |

### 失败类型分布

| failure_kind | 数量 | 占比 |
|--------------|------|------|
| hypothesis_falsified | 25 | 71.4% |
| method_failed | 6 | 17.1% |
| regression | 2 | 5.7% |
| blocked | 2 | 5.7% |

### 来源 mission 分布

| mission_id | 条目数 | 来源 |
|------------|--------|------|
| 7297 | 4 | UQ 框架 |
| 9049 | 7 | 先验校准器 |
| 9356 | 7 | 跨架构门 |
| 200 | 1 | Soul 迁移 |
| 311 | 3 | 质量门 |
| 591 | 1 | 稳性复现 |
| 56 | 1 | Pydantic 500 |
| null | 11 | 质量/criteria |

---

## 4. 结果图

本任务为纯文档/后端任务，无数值计算结果图。理由：负知识台账是结构化数据登记，不涉及数值计算或场量可视化。所有数字均来自证据源直接提取，零手抄。

---

## 5. 注意点

### 踩过的坑
1. **SQL 方言差异**：PostgreSQL 的 `SERIAL`/`TIMESTAMPTZ`/`tsvector` 在 SQLite 不兼容，加载器需分方言建表
2. **检索 shingle 匹配**：短查询（如 "sobol" 5 字符）在 shingle_size=4 时只产生 2 个 shingle，与中文混合文本匹配率低——测试需用足够长查询
3. **NegativeKnowledgeConfig 构造**：是 dataclass 无默认值，必须用 `load_config()` 而非直接实例化

### 局限
- 当前 35 条主要来自 agent_memory 和 quality 证据，未覆盖全部 8699 篇 QA pending
- 数据库加载需 PostgreSQL 运行（当前环境无 PG，用 SQLite 验证）
- 消费端 prompt 注入的 `render_prompt_block()` 函数待实现

### 适用范围
- 本台账为零领域语义设计，域数据经配置/工件注入
- 检索基于字符 n-gram Jaccard + 包含度，不依赖域词表

---

## 6. 误差判定

本任务为数据登记任务，无数值计算误差。关键量验证：

| 关键量 | 基准 | 实测 | 误差 | 是否<3% |
|--------|------|------|------|---------|
| 条目数 | ≥30 | 35 | N/A | ✅ 满足 |
| 有证据指针比例 | 100% | 100% | 0% | ✅ |
| 测试通过率 | 100% | 15/15 | 0% | ✅ |
| 加载成功率 | 100% | 35/35 | 0% | ✅ |

---

## 7. 交付物清单

| 文件 | 说明 |
|------|------|
| `artifacts/negative_knowledge_ledger/negative_knowledge_ledger.json` | 35 条台账 JSON |
| `backend/migrations/120_negative_knowledge_ledger.sql` | PostgreSQL 迁移 |
| `scripts/load_negative_knowledge_ledger.py` | 批量加载器 |
| `scripts/verify_negative_knowledge_ledger.py` | 验证脚本 |
| `tests/test_negative_knowledge.py` | 15 条单元测试 |
| `docs/negative_knowledge_consumption.md` | 消费端机制说明 |
| `config/negative_knowledge.json` | 配置文件 |

---

## 8. 验证命令

```bash
# 验证台账
python3 scripts/verify_negative_knowledge_ledger.py \
    --input artifacts/negative_knowledge_ledger/negative_knowledge_ledger.json

# 加载到数据库
python3 scripts/load_negative_knowledge_ledger.py \
    --input artifacts/negative_knowledge_ledger/negative_knowledge_ledger.json \
    --db-url "sqlite:///backend/foundation_ai.db"

# 运行测试
python3 -m pytest tests/test_negative_knowledge.py -v
```
