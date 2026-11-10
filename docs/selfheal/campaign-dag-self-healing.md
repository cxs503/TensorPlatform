# 战役 DAG 自愈与节点弹性调度（P162W1-N08）

> 模块：`backend/services/selfheal/`（纯逻辑引擎）+ `backend/api/campaign_self_heal.py`（HTTP 面）
> 数据库迁移：`backend/alembic/versions/111_campaign_selfheal.py`
> 验收产物：`artifacts/selfheal/acceptance.json`（本报告全部数字由其程序化注入）
> 随机种子：20261110；生成器：`tools/selfheal/run_acceptance.py`

## ① 案例介绍

本节点属于平台共性机制（不是船舶物理算例）：战役 DAG 由 `campaign_nodes` 承载，
节点可能因瞬态错误（超时、抢占、外部服务抖动）失败，也可能在集群高负载时被迫
排队。目标是在**不引入领域语义**的前提下提供七项自愈能力：

1. 失败节点按指数退避自动重试（`consecutive_fails` 计数，`delay = base × factor^n`）；
2. 重试耗尽后旁路（标记 `skipped`），下游依赖视其为"可用但已跳过"，不级联失败；
3. 支持降级执行（降低精度/减少迭代），结果标记 `DEGRADED`，并与最近一次正常结果
   比较，偏差超过容差则拒绝降级；
4. 资源饱和感知（CPU/内存/队列长度取最大比值，> 80% 视为饱和）；
5. 弹性调度：饱和时优先关键路径节点，非关键节点延迟但延迟有界（< 30 s）；
6. 自愈事件审计链：每条事件含时间/节点/动作/结果，并以 SHA-256 前序哈希链接；
7. 无环不变量：自愈操作不改变依赖边集合，环依赖在执行前即被拒绝。

由于是平台机制，本节点不涉及船体型线/几何，故无 Cb/L/B 几何前置校验；参数表为
策略默认值（下表），全部可经 `PUT /tensor/campaigns/plans/{id}/selfheal/policy`
按战役覆写。

| 参数 | 默认值 | 含义 |
|---|---|---|
| `retry.max_retries` | 3 | 首次尝试之后允许的重试次数 |
| `retry.base_delay_s` / `factor` | 2.0 / 2.0 | 退避延迟 `min(base·factor^n, max_delay)`，即 4/8/16 s |
| `retry.max_delay_s` | 300 | 退避封顶 |
| `resources.saturation_threshold` | 0.80 | 触发弹性调度的负载阈值 |
| `resources.max_defer_s` | 30 | 非关键节点最大延迟（严格小于，C5） |
| `degradation.max_deviation` | 0.15 | 降级结果相对正常结果的最大偏差 |
| `bypass_on_exhaust` | True | 重试+降级均不可用时旁路而非整支失败 |

## ② 方法与流程

**状态机**（`services/selfheal/engine.py`）：
`pending → in-flight → done | failed`，失败分支进入
`retry_pending`（退避等待）→ 到点 `in-flight`；重试耗尽后按策略进入
`degraded`（降级结果通过偏差门）或 `skipped`（旁路）；上游 `failed/blocked`
时下游转 `blocked`，上游 `skipped/degraded` 视作依赖满足。

**依赖语义**（`services/selfheal/dag.py`）：Kahn 算法做拓扑校验（C7），
`ready_nodes()` 只返回依赖全部满足的 pending 节点；`priority_nodes()` 优先使用
显式 `critical` 标记，未标记的历史计划回退到最长依赖链，保证饱和时仍有确定优先级。

**弹性调度**（`schedule`）：取 `max(cpu, mem, queue/capacity)` 与阈值比较；
饱和时关键路径节点立即派发（延迟 0），非关键节点
`delay = clamp((ratio - thr)/(1 - thr), 0, 1-ε) × max_defer_s`，到点后下一 tick 派发。
每 tick 顺序：`resolve_blocked → advance(到点重试) → schedule`。

**审计链**（`services/selfheal/audit.py`）：`hash = SHA256(canonical(seq, ts,
node_id, action, outcome, detail, prev_hash))`，逐条落库为
`campaign_events.event_type = selfheal_<action>`；`verify()` 可检测篡改、删除与重排。

**持久化**（`services/selfheal/store.py`）：不新增列——重试/延迟/降级簿记写入
`campaign_nodes.metadata` JSON；PostgreSQL 上 `ensure_selfheal_schema()` 与迁移 111
幂等扩宽 `status` / `event_type` 的 CHECK 白名单（只增不删）。

**HTTP 面**：`report`（上报结果→自愈决策）、`step`（一个自愈 tick）、`state`、
`audit`、`policy GET/PUT`、`validate`（无环/关键路径），已挂入
`api/domain/tensor.py` 的 `/tensor/campaigns` 路由。
API 不执行任何战役负载：降级结果由上报方或进程内执行器提供，平台保持零领域语义。

## ③ 计算结果（机器验收判据）

| 判据 | 名称 | 实测 | 阈值 | 结果 |
|---|---|---|---|---|
| C1 | automatic_retry_recovery_rate | 0.985 fraction | 0.8 | PASS |
| C2 | bypass_routing_no_cascade | 2 nodes_done | 2 | PASS |
| C3 | degraded_result_deviation | 0.006375 relative_deviation | 0.15 | PASS |
| C4 | no_fault_no_heal | 0 heal_events | 0 | PASS |
| C5 | saturated_elastic_scheduling | 20.625 seconds | 30 | PASS |
| C6 | selfheal_audit_chain | 1 completion_fraction | 1 | PASS |
| C7 | acyclic_invariant | 1 boolean_invariant | 1 | PASS |

全量判据布尔：**True**。

关键数字说明：

- **C1**：200 次故障注入（每次尝试独立失败概率 0.4），
  3 次重试内恢复 **197/200 = 98.50%**，
  理论值 97.44%，门槛 80%。
- **C2**：头节点 4 次失败后状态 `skipped`，下游
  `{'2': 'done', '3': 'done'}` 全部 `done`，级联失败
  0 个。
- **C3**：正常 200 次迭代 = 3.136593，降级 40 次迭代 =
  3.116597，相对偏差 **0.6375%**
  （容差 15%，且 0.1% 严格档下同一结果被 `degrade_rejected` 拒绝）。
- **C4**：健康战役 4 节点全部成功 → 自愈事件
  **0**，零误报；审计链完整性 True。
- **C5**：负载 94%（cpu 92%）
  时关键节点延迟 0.0 s，非关键节点延迟
  **20.625 s < 30 s**，且负载回落后该节点正常派发。
- **C6**：混合场景产生 5 条自愈事件，完整率
  100%，链完整性 True，
  篡改检测 True。
- **C7**：自愈前后依赖边集合一致 True，注入环
  ['1', '3', '2', '1'] 被拒绝 True。

## ④ 结果图

![fig1_retry_recovery.png](../../artifacts/selfheal/figures/fig1_retry_recovery.png)

*图 1 C1 自动重试：故障注入（每次尝试失败概率 0.40）下，恢复率随允许重试次数的变化；绿色虚线为 200 次注入试验的实测恢复率，红色虚线为 80% 验收门。*

![fig2_elastic_scheduling.png](../../artifacts/selfheal/figures/fig2_elastic_scheduling.png)

*图 2 C5 弹性调度：非关键节点延迟随集群负载的变化（80% 阈值左侧不延迟），关键路径节点延迟恒为 0 s，灰色点线为 30 s 上限。*

![fig3_degradation_tradeoff.png](../../artifacts/selfheal/figures/fig3_degradation_tradeoff.png)

*图 3 C3 降级执行：降级模式迭代次数与相对正常结果偏差的关系，红色虚线为 15% 容差，绿色竖线为选用的 40 次迭代降级档位。*

![fig4_audit_chain_actions.png](../../artifacts/selfheal/figures/fig4_audit_chain_actions.png)

*图 4 C6 自愈审计链：混合场景（重试→恢复、重试耗尽→降级、饱和→延迟）中每个自愈动作的审计事件计数。*

## ⑤ 注意点（踩坑 / 局限 / 适用面）

1. **降级偏差门依赖基线**：C3 只在存在最近一次正常结果（`baseline_result`）时
   才做数值比较；无基线时降级结果会被接受但不做数值校验。API 上基线随
   `campaign_nodes.metadata` 持久化。
2. **饱和输入来自调用方**：本模块只消费 `LoadSample(cpu/mem/queue)`，未绑定具体
   监控后端（Prometheus/节点探针在平台其他模块）。调用方需在 tick 时注入真实负载。
3. **PostgreSQL 分支未在本工作树实测**：迁移 111 与 `ensure_selfheal_schema` 走
   与迁移 110 相同的"解析既有字面量→DROP/ADD CHECK"路径，工作树无 PG 实例，只在
   SQLite 上验证了幂等空操作；上线时需在 PG 上执行一次迁移冒烟。
4. **全量 FastAPI app 导入受限**：测试环境缺 `boto3`/`cryptography`（工程既有
   依赖缺口，与本模块无关），故 API 测试采用隔离 router + 依赖覆盖，未跑整机 app。
5. **关键路径语义**：显式 `critical` 标记优先；完全未标记的旧计划回退为最长依赖链，
   线性链在饱和时不会产生非关键延迟——这是刻意的保守行为（不会饿死主线）。
6. **重试退避默认无抖动**：为保证审计链可复现，`jitter_ratio=0`；大规模并发时可
   调大以避免同刻重试风暴。
7. **自愈事件与普通事件同表**：全部落在 `campaign_events`（`selfheal_*` 前缀），
   便于 SQL 溯源；审计链校验需按 `id` 顺序读取全部自愈事件。

## ⑥ 误差判定

| 关键量 | 基准 | 误差 | 是否 < 3% |
|---|---|---|---|
| C3 降级结果 3.116597 | 正常结果 3.136593 | 0.6375% | **是**（0.64% < 3%） |
| C1 恢复率 98.50% | 理论 97.44% | 1.09% | 是（统计涨落，200 次试验） |
| C5 非关键延迟 20.625 s | 上限 30 s | 余量 9.375 s | 不适用（阈值型判据） |

C3 的基准是同一确定性级数的正常精度结果，属于**解析可复现基准**而非实验/文献数据；
本节点为平台机制，无实验测量或文献参考曲线可对标，故不声明与外部基准的偏差，
此处"误差"指降级相对正常精度的损失，已显式给出且 < 3%。其余判据为布尔/阈值型，
无连续量误差概念。

## 复现

```bash
cd backend && python -m pytest tests/test_campaign_selfheal.py     tests/test_campaign_selfheal_api.py -q          # 25 项引擎 + API 验收测试
python tools/selfheal/run_acceptance.py             # 生成 artifacts/selfheal/*
python tools/selfheal/render_report.py              # 生成本文（数字化注入）
```
