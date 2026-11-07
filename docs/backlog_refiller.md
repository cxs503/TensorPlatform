# 常设补给器（backlog_refiller）成熟化

## 概述

常设补给器是 TensorPlatform 的渐退令核心件，用于替代人工播种任务。
它是一个容器 daemon，在队列深度低于阈值时自动从已文档化缺口账本生成 mission。

## 架构

```
src/backlog_refiller/
├── __init__.py           # 模块入口
├── daemon.py             # 主循环 daemon
├── daemon_entry.py       # 命令行入口
├── audit.py              # 审计日志（可回滚）
├── gap_ledger.py         # 缺口账本聚合器
└── mission_generator.py  # Mission 生成器
```

## 触发条件

- `pending_missions == 0`（无排队任务）
- `idle_workers >= 6`（至少 6 个空闲 worker）

## 缺口源

1. **能力台账 WITHHELD 项**：`capability_ledger` 表中证据等级为 WITHHELD/NOT_SUPPORTED 的条目
2. **战役收尾 GAP 账本**：`campaign_nodes` 中标记为 gap 的节点
3. **m605/G 账本条目**：已完成 mission 的 result 中包含 gap 标记的条目

## Mission 生成规范

- **goal ≥ 80 字**：包含任务目标、验收判据、纪律要求
- **判据**：可机器验证（pytest）、误差 <15%、几何验证
- **honest FAIL 纪律**：不可覆盖，如实报告
- **零域语义**：平台基础设施任务，不涉及具体领域知识
- **ext_id 去重**：SHA256 哈希前 16 字符 + goal 模糊匹配
- **仓白名单**：仅允许预定义的 12 个仓库

## 审计与回滚

每个动作记录到 `backlog_refiller_audit` 表：
- `action`: refill / rollback / liveness
- `mission_id`: 生成的 mission ID
- `ext_id`: 外部去重标识
- `details`: JSON 详情
- `rollback_sql`: 回滚 SQL

回滚操作：`AuditLogger.rollback_latest()` 撤销最近一次 refill。

## 运行

```bash
# 直接运行
python -m backlog_refiller.daemon_entry

# 带参数
python -m backlog_refiller.daemon_entry \
  --api-base http://localhost:8001 \
  --loop-interval 60 \
  --max-cycles 0

# 环境变量
export DATABASE_URL="postgresql://..."
export API_BASE="http://localhost:8001"
export LOOP_INTERVAL=60
python -m backlog_refiller.daemon_entry
```

## 测试

```bash
pytest tests/test_backlog_refiller.py -v
```

22 个测试用例覆盖：
- 队列深度判定（正例/负例）
- 2026-11-07 03:18 停摆案例回放
- 正常饱和态
- Goal 生成验证（长度/判据/honest FAIL/零域语义）
- ext_id 去重
- 仓白名单
- 审计日志与回滚
- 缺口账本聚合
- Daemon 存活证明
- Mission 参数生成

## 设计约束

- 独立于任何人类会话与 AI 协调者会话运行
- 带审计日志 + 回滚路径
- 假设明天没有人类来

## IN20-G21 现场修复（真库跑不通的两处）

2026-11-07 验收门实测发现：R1 的 22 条单测全过，但在真库上**一轮都跑不完**。两处根因，均已修并加回归用例
（`tests/test_autonomy_acceptance_gate.py::TestRefillerWiringRegression`）：

1. **第一缺口源的表不存在**。`_collect_withheld_capabilities()` 查 `capability_ledger`，
   而 `tensorplatform` / `foundation` 两库都没有这张表 → `UndefinedTable` 抛穿 `collect_gaps()`，
   daemon 直接失败。修法：
   - `gap_ledger.ensure_capability_ledger(session)`：幂等建表（`daemon.ensure_schema()` 启动时调用）；
   - 源级容错：`collect_gaps()` 逐源 `try/except`，不可用源记 `source_status[name]="unavailable:<Exc>"`
     后跳过，不再拖死整轮；状态经 `run_once()["gap_sources"]` 输出，可审计。
2. **`origin` 撞平台 CHECK**。`tensor_dev_missions_origin_check` 只允许 `owner|agent`，
   而生成器写 `origin='backlog_refiller'` → 每次补给 INSERT 必然失败。修法：
   - 写 `origin='agent'`（补给器是无人值守 agent 侧机制），生成者身份记
     `result.generated_by='backlog_refiller'`；
   - 去重查询改按 `result->>'generated_by'` + `ext_id_hash`，不再依赖 `origin`。

回放实证（`artifacts/autonomy_acceptance/refill_stall_replay.json`）：停摆态（pending=0、idle=8、
6 条 WITHHELD 缺口）下 `triggered=true`，生成 3 条 mission、3 条 refill 审计行，
`rollback_latest()` 撤销后回库核验该 mission 已不存在。
