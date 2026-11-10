# CAMPAIGN_OPTIMIZER —— 战役 DAG 自回归优化机制（瓶颈检测 / 根因分类 / 资源重分配 / 历史回测）

**代码**：`tools/campaign_optimizer/`  **配置**：`config/campaign_optimizer.v1.json`
**报告**：`docs/P233W2-N10_campaign_optimizer.md`  **产物**：`artifacts/campaign_optimizer/`

## 0 一句话

把「战役 DAG 为什么推不动、该先救哪个节点、重排资源能省多少」从人工判断变成
**状态 + 依赖 + 资源元数据驱动的确定性机制**，每条结论都带可复算的数字。

## 1 分工（与既有机制的关系）

| 机制 | 管什么 | 交互 |
|---|---|---|
| `backend/services/selfheal/` | 单节点治理：重试 / 降级 / 旁路 / 阻断传播 | 本机制消费其状态语义（satisfied / blocking），输出「这张图该先救谁」 |
| `tools/campaign_throughput.py` | 逐 plan 吞吐报表（成功率 / 墙钟 / 领取时延） | 本机制用节点工期做调度成本，吞吐口径可交叉对照 |
| `tools/dispatch_contract/` | 派单前 fail-closed 契约门（上游交付/口径） | 根因 `upstream_not_delivered` / `artifact_mismatch` 的判定与之同源，但本机制**看已发生的事**（事后归因），契约门看**派发前**（事前拦截） |
| **本机制** | 全图级：瓶颈排序 + 根因 + 反事实建议 + 回测 | 只出建议与数字，**不执行**改派（执行权仍在派单链） |

## 2 流水线

```
campaign_nodes (status, dependencies, node_type, consecutive_fails, mission.*)
        │
        ├─ model.py     纯图算法：拓扑序 / 最长链关键路径 / 总浮动 / 下游闭包
        │
        ├─ detect.py    瓶颈 = blocking 状态 ∧ 严重度 ≥ floor
        │               严重度 = w·{gates_completion, downstream_gated, on_critical_path,
        │                           downstream_weight_share, stall_age}
        │               另出 risk（上游含阻塞的活动节点）与 queue_bound（容量约束）
        │
        ├─ rootcause.py 声明式规则链（config JSON 里的 feature/op/value 谓词）
        │               类别：upstream_not_delivered / artifact_mismatch /
        │                     resource_shortage / code_error / unknown
        │
        ├─ schedule.py  确定性列表调度：容量 + 重试预算 + 优先级；改进调度带守卫（先模拟再改）
        │
        ├─ reallocate.py 每条建议 = 单节点反事实仿真（只改该节点策略）→ 可算节省秒数
        │
        └─ backtest.py  AC1 召回 / AC2 混淆矩阵 / AC3 makespan 对比 + 敏感性 /
                        AC5 随机 DAG 负控（含纯结构启发式对照）
```

## 3 三条硬约束（改代码前先读）

1. **零领域语义**：判定链（`detect/rootcause/schedule/model`）对 `Node.name`、
   `Node.reason_text` **零引用**——由 `tests/test_campaign_optimizer_zero_domain.py` 的 AST 扫描断言。
   审计需要的文本由产物层 `cli.build_payload → payload["evidence_text"]` 补。
2. **阈值唯一来源**：状态词表、严重度权重、规则链、调度参数、判据阈值全部在
   `config/campaign_optimizer.v1.json`；代码里没有魔法数。
3. **确定性**：无墙钟、无未播种随机；`content_clock` 取快照内最大 `updated_at`。
   同输入连跑逐字节相同（`test_report_*` / `test_ac5_negative_control_is_deterministic`）。

## 4 语义要点

* **plan 完成 = 全节点 satisfied**，因此「未满足节点」都 `gates_plan_completion`；
  但只有**在最长剩余链上**的阻塞节点会拉长工期。于是：
  - 孤立阻塞（无下游）→ 严重度最低、进建议清单但**makespan 节省为 0**（要按完成度处理）；
  - 有下游的阻塞 → 严重度高、节省可算（优先处理）。
* **工时口径**：节点 `duration_s` 是含重试的总墙钟；单次尝试 = `duration_s / attempt_divisor`。
  回测显示增益对 divisor 不敏感（同比缩放），对 `baseline retry_max` 敏感（原始调度烧几次）。
* **守卫**：`schedule.improved_schedule` 在候选优先级里取最优，绝不劣于原始派单顺序
  （实测无守卫时 tight capacity 下有 plan 慢 30.4%）。

## 5 用法

```bash
# 1) 导出只读快照（唯一联网步骤）
python3 -m tools.campaign_optimizer.cli snapshot --out artifacts/campaign_optimizer/corpus_snapshot.json
# 2) 单 plan 体检（打印 JSON：检测 + 建议）
python3 -m tools.campaign_optimizer.cli detect --snapshot artifacts/campaign_optimizer/corpus_snapshot.json --plan 233
# 3) 全量回测 + 报告 + 图
python3 -m tools.campaign_optimizer.cli backtest \
  --snapshot artifacts/campaign_optimizer/corpus_snapshot.json \
  --labels artifacts/campaign_optimizer/rootcause_labels.json \
  --out-json artifacts/campaign_optimizer/report.json \
  --out-md docs/P233W2-N10_campaign_optimizer.md \
  --figure-dir artifacts/campaign_optimizer
# 4) 判据自检
python3 -m pytest tests/test_campaign_optimizer_model.py \
                 tests/test_campaign_optimizer_zero_domain.py \
                 tests/test_campaign_optimizer_acceptance.py -q
```

## 6 已知局限（诚实清单）

1. **标注独立性有限**：55+ 例人工标注由同一 agent 完成，能看到平台 `block_reason` 记录；
   分类器看不到文本。AC2 证明的是「结构化特征可复原原因通道」，不是第三方一致性。
   根治办法：平台补结构化 `root_cause` 字段（执行器写入、agent 不可改），直接替换标注通道。
2. **负控的鉴别力来自对照基线**：状态门控式检测器在「无阻塞状态」的随机图上天然 0 假阳性，
   所以 AC5 同时报告纯结构启发式的假阳性率（≈40%）作为鉴别力证据。
3. **模型内小时数**：所有 makespan 都是模型量（同一模型内可比），不是墙钟预测。
4. **只读**：本机制不写库、不改派任务；建议落地需要派单链路接入（下一步）。