# Planner Agent - 任务规划师

> **定位**：机器门（m597 必要性三查）之上的**判断层**——机器查事实（已覆盖/机械可修/在途去重），规划师判意图与价值（挂靠谁/成本值不值）并产出任务书。

## 背景

2026-10-31 全案即黄金标注集：
- 12 个 openhands 僵尸返工单（该拒：目标已被覆盖）
- 报告 34/35/36 三份全同重复（该机械修不派 LLM）
- 报告 8 MD5 typo（不值 140 次工具调用）
- EX-1 m541-550 系（好单范本）
- m533（任务书判据好但交付造假→教训：判据必须配复算）
- m583 治理假声明（自查全绿外查全假）

**问题**：机器判不了，agent 应该判的了，要培养出能分析目标布置任务的 agent。

## 架构

```
候选任务 → [机器门 m597] → [规划师 Planner] → 决策
                ↓                    ↓
          事实检查             意图与价值判断
          - 已覆盖?            - 挂靠谁?
          - 机械可修?          - 成本值不值?
          - 在途去重?          - 产出任务书
```

## 核心组件

### 1. 决策 Schema (`__init__.py`)

输入：候选任务 + 上下文包（guide_tracker KPI 快照/在飞单清单/近 7d 成果与失败谱/预算价目）

输出：结构化决策 JSON
```json
{
  "verdict": "dispatch|reject|mechanical|merge",
  "挂靠目标": "指令/KPI/战役线 id",
  "成本收益论证": {...},
  "任务书": {
    "goal": "...",
    "机器判据": [...],
    "预算建议": "calls,tokens,max_time",
    "禁止项": [...]
  },
  "confidence": 0.85
}
```

### 2. 决策引擎 (`engine.py`)

基于规则 + 模式的决策逻辑：
- 僵尸单模式匹配（openhands 返工）
- 机械修复模式（报告重复、typo）
- 好单模式（EX-1 m541-550 系）
- 上下文分析（在飞单去重、失败谱规避）

### 3. 黄金标注集 (`annotation_set.py`)

≥30 条带 ground truth：
- 2026-10-31 案例 ≥15 条直录
- 历史 succeeded/failed/僵尸单采样
- Held-out 留 1/3 不许训练调参偷看

### 4. 影子模式 (`shadow_mode.py`)

对进自动派单队列的候选并行出决策**不实际派单**：
- 决策入 `tensor_planner_decisions`（append-only 触发器同 events 纪律）
- 输入快照/输出决策/对照方(机器门+人工)裁决/后续结果回填位

### 5. 培养回路 (`training_loop.py`)

决策→后续结果回填→credit_ledger→playbooks 规划分册

**权限阶梯**：
1. **advisory**（纯建议）
2. **shadow**（并行对照跑分）
3. **低风险自动派**（仅机械修+返工类）
4. **战役自动拆单**

每级升权留 owner 批。

### 6. 红队跑分 (`scoring.py`)

标注集上出跑分报告：
- 必要性判断准确率（dispatch/reject/mechanical 三类混淆矩阵）
- 任务书判据可复算率
- 成本预估校准（预估 calls vs 实耗散点）
- 错例逐条列出（不许只报成功）

## 机器判据

1. ✅ 决策 schema 程序可解析
2. ✅ held-out 跑分数字脚本可复算
3. ✅ 影子决策链路端到端演示 1 例
4. ✅ tensor_planner_decisions 的 UPDATE 被 append-only 触发器拒

## 数据库 Schema (`schema.sql`)

```sql
CREATE TABLE tensor_planner_decisions (
    id              BIGSERIAL PRIMARY KEY,
    decision_id     VARCHAR(64) NOT NULL UNIQUE,
    verdict         VARCHAR(16) NOT NULL,
    confidence      FLOAT NOT NULL,
    -- ... 其他字段
    mode            VARCHAR(16) DEFAULT 'shadow'
);

-- Append-only 触发器
CREATE TRIGGER trg_prevent_planner_decision_update
    BEFORE UPDATE ON tensor_planner_decisions
    FOR EACH ROW
    EXECUTE FUNCTION prevent_planner_decision_update();
```

## 使用示例

### 影子模式演示

```python
from tools.planner.shadow_mode import demo_shadow_mode

record = demo_shadow_mode()
print(f"Verdict: {record.decision.verdict.value}")
print(f"Confidence: {record.decision.confidence}")
```

### 跑分

```python
from tools.planner.scoring import run_scoring

report = run_scoring()
print(f"Accuracy: {report['held_out']['accuracy']:.2%}")
```

### 权限升级检查

```python
from tools.planner.training_loop import PermissionLadder, PermissionLevel

ladder = PermissionLadder()
eligibility = ladder.check_upgrade_eligibility(PermissionLevel.SHADOW)
print(f"Eligible: {eligibility['eligible']}")
```

## 测试

```bash
cd tools/planner
python tests/test_planner.py
```

机器判据验证：
- ✅ Schema 可解析
- ✅ Held-out 可复算
- ✅ 影子模式演示
- ✅ Append-only 触发器
- ✅ 标注集完整性
- ✅ 信用账本
- ✅ 权限阶梯

## 约束

- ✅ 纯新增 `tools/planner/` + 新表 + docs
- ✅ 不改 `tensor_missions.py` / approvals 热区
- ✅ 影子模式不真派单
- ✅ 标注集 held-out 不泄漏
- ✅ 跑分不只挑好例

## 交付

- ✅ 代码：`tools/planner/`
- ✅ 标注集：`golden_annotation_set.json`
- ✅ 跑分报告：`scoring_report.json`
- ✅ 权限阶梯文档：`docs/planner/permission_ladder.md`
- ✅ 论文级进展报告：`docs/reports/PLANNER_PROGRESS_REPORT.md`

## Owner

2026-10-31 全案即黄金标注集——定位：机器门之上的判断层。
