# IN59-A6 动态升级机制与能力评价七指标

## 1. 案例介绍

### 物理背景
本模块属于 TensorPlatform 智能体运行底座的**战役调度与能力评价**层，不直接涉及船舶学科计算，
而是为上层智能体席位提供**任务失败自动升级**与**能力度量**的基础设施。

### 核心问题
- 当某任务谱系连续失败 ≥ 3 次，或某席位心跳正常但无进展 ≥ 2 轮时，系统需自动生成升级菜单
- 同一技术假设被证伪 2 次后不得第三次重跑（强制换路线）
- 七项能力指标需入 chronicle clock s9，按席位分层归一，防挑软柿子

### 参数表
| 参数 | 默认值 | 说明 |
|---|---|---|
| consecutive_failures_threshold | 3 | 连续失败触发阈值 |
| no_progress_threshold | 2 | 无进展轮数触发阈值 |
| hypothesis_max_falsifications | 2 | 假设最大证伪次数 |
| metrics_window | 30 | 指标计算窗口（mission 数） |
| difficulty_levels | low/medium/high | 分层归一维度 |

## 2. 方法与设计

### 2.1 动态升级机制（`src/campaign_upgrade/`）

**触发条件**：
1. `check_lineage_failures()`: 查询 `tensor_dev_missions` 表，按 `task_key` 排序，
   统计最近连续 failed 记录数，≥ threshold 则触发
2. `check_seat_no_progress()`: 查询 `seat_heartbeat_log` 表，按 `round_number` 倒序，
   统计连续 `progress_flag=0` 的轮数，≥ threshold 则触发

**升级菜单**（4 选项，按优先级排列）：
1. `request_stronger_agent` — 请求更强Agent（模型升级）
2. `request_expert_knowledge` — 请求领域专家知识包
3. `reallocate_seat` — 重分配席位
4. `change_technical_route` — 换技术路线（联 hypothesis_registry 立新假设）

**假设注册与证伪**：
- `register_hypothesis()`: 注册技术假设到 `hypothesis_registry` 表
- `record_hypothesis_falsification()`: 证伪计数 +1，≥ 2 次自动标记 `blocked`
- `is_hypothesis_blocked()`: 查询假设是否被阻止

### 2.2 能力评价七指标（`src/capability_metrics/`）

| 指标 | 定义 | 分母 | 分子 |
|---|---|---|---|
| first_pass_rate | 首次解决率 | succeeded 任务数 | 无 failed 历史的 succeeded 数 |
| result_effectiveness | 结果有效率 | succeeded 任务数 | succeeded + accepted 数 |
| hypothesis_hit_rate | 假设命中率 | 关联假设总数 | active 且 falsification_count=0 数 |
| defect_discovery_rate | 缺陷发现率 | 审查任务数 | 发现缺陷数 |
| reproduction_success | 复现成功率 | 尝试复现数 | 成功复现数 |
| knowledge_contribution | 知识贡献率 | 完成任务数 | 产出知识工件数 |
| breakthrough_degree | 攻关贡献度 | 高难度任务数 | 解决高难度任务数 |

**分层归一**：按 low/medium/high 三层分别计算七指标，防止席位只挑简单任务刷分。

### 2.3 落库形状
- `upgrade_menus`: 升级菜单记录（menu_id, lineage_key, trigger_reason, options_json, status, disposition）
- `hypothesis_registry`: 假设注册表（hypothesis_id, lineage_key, falsification_count, status）
- `capability_metrics`: 能力快照表（snapshot_id, seat_id, clock_key='s9', metrics_json, stratified_json）
- `seat_heartbeat_log`: 席位心跳日志
- `defect_registry`: 缺陷登记
- `reproduction_log`: 复现日志
- `knowledge_artifacts`: 知识工件

## 3. 计算结果

### 3.1 测试覆盖
31 项测试全部通过，覆盖：
- 连续失败触发/不触发/打断场景（4 测试）
- 席位无进展触发/不触发/重置场景（3 测试）
- 升级菜单构建/处置/驳回（4 测试）
- 假设注册/证伪/阻止/不存在（5 测试）
- 七指标计算（含无数据退化）（10 测试）
- 快照写入与分层（2 测试）
- 零领域语义（2 测试）
- 端到端工作流（1 测试）

### 3.2 关键数字
| 判据 | 期望 | 实际 | 状态 |
|---|---|---|---|
| 连续失败 ≥ 3 触发 | triggered=True | triggered=True | ✅ |
| 连续失败 = 2 不触发 | triggered=False | triggered=False | ✅ |
| 升级菜单选项数 | 4 | 4 | ✅ |
| 假设证伪 2 次 blocked | blocked=True | blocked=True | ✅ |
| 七指标全部可计算 | 7 个 metric | 7 个 metric | ✅ |
| 快照含 stratified | 3 层 × 7 指标 | 3 层 × 7 指标 | ✅ |
| 测试通过率 | 100% | 31/31 = 100% | ✅ |

## 4. 结果图

```
┌─────────────────────────────────────────────────────────────┐
│                    IN59-A6 架构概览                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  tensor_dev_missions ──┐                                    │
│                        ├──► check_lineage_failures()        │
│  seat_heartbeat_log ───┤                                    │
│                        ├──► check_seat_no_progress()        │
│                        │                                    │
│                        ▼                                    │
│              ┌──────────────────┐                           │
│              │  build_upgrade_  │                           │
│              │     menu()       │──► upgrade_menus 表       │
│              └────────┬─────────┘                           │
│                       │                                     │
│          ┌────────────┼────────────┐                        │
│          ▼            ▼            ▼                        │
│   request_     request_     reallocate   change_            │
│   stronger     expert       seat         technical          │
│   agent        knowledge                 route              │
│          │            │            │           │            │
│          └────────────┴─────┬──────┴───────────┘            │
│                             │                               │
│              ┌──────────────▼──────────────┐                │
│              │  hypothesis_registry        │                │
│              │  falsification_count ≥ 2    │                │
│              │  → status = 'blocked'       │                │
│              └──────────────┬──────────────┘                │
│                             │                               │
│              ┌──────────────▼──────────────┐                │
│              │  capture_capability_        │                │
│              │  snapshot() → clock s9      │                │
│              │  7 metrics × 3 levels       │                │
│              └─────────────────────────────┘                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 5. 注意点

### 踩过的坑
1. **SQLite 多语句 DDL**：SQLAlchemy `execute(text(...))` 不支持多语句，需用 `raw_connection().cursor()` 逐条执行
2. **API 一致性**：所有 compute_fn 必须接受相同签名（含 `window` 参数），否则循环调用会 TypeError
3. **零领域语义**：模块内不含学科词，被观测主体由 `seat_id`/`lineage_key` 注入

### 局限
- `breakthrough_degree` 用 `fail_stage` 作为难度代理，实际系统应有独立难度标签
- 分层归一当前为简化实现（三层计算相同指标），实际应按任务难度标签过滤
- `hypothesis_hit_rate` 的 JOIN 逻辑假设 `task_key = lineage_key`，实际可能需要更复杂的关联

### 适用范围
- 适用于任何基于任务谱系的智能体调度系统
- 七指标定义可适配不同领域，通过 `seat_id` 注入主体

## 6. 误差判定

本模块为**平台基础设施**，不涉及数值计算误差。验收判据为功能正确性：
- 触发逻辑：布尔判定，无误差
- 指标计算：精确计数，无浮点误差（除法结果 round 到 6 位小数）
- 状态转换：确定性状态机，无随机性

所有 31 项测试通过，功能正确性已验证。
