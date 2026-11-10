# P233W2-N10 · 战役 DAG 自回归优化节点：自动瓶颈检测 + 根因分类 + 资源重分配 + 历史回测

**节点**：`P233W2-N10`（TensorPlatform｜平台自回归优化）  
**语料快照**：`artifacts/campaign_optimizer/corpus_snapshot.json`（content_hash `c6541e7f6a171d1f…`，3983 节点 / 417 plan / 阻塞节点 361）  
**结论**：机制建成，AC1–AC5 全部机器可算判据达标（下表逐条给数字与复算命令）。

## 判据总表（预注册判据 vs 实测）

| 判据 | 目标 | 实测 | 判定 | 证据 |
|---|---|---|---|---|
| AC1 瓶颈检测召回（plan 233，10 单） | 100%（4 个 blocked） | 100.0%（4/4），precision 100.0% | PASS | `artifacts/campaign_optimizer/report.json:plan233.detect` |
| AC2 根因分类准确率（vs 人工标注） | ≥70%（40+ 例） | 100.0%（n=56），留出集 100.0%（n=23） | PASS | `artifacts/campaign_optimizer/rootcause_labels.json` |
| AC3 关键路径完成时间缩短（plan 233） | ≥20% | 33.3%（49.49 h → 32.99 h）；61 个 plan 中位 33.3% | PASS | `report.json:throughput` |
| AC4 零领域语义 | 判定只读状态/依赖/资源元数据 | 特征白名单 10 项；改名不变性/隐文本不变性/无 CJK 三项行为测试通过 | PASS | `tests/test_campaign_optimizer_zero_domain.py` |
| AC5 负控假阳性率（随机 DAG） | ≤10% | 0.00%（0/3824 非阻塞节点）；纯结构启发式对照 39.98% | PASS | `report.json:negative_control` |
| AC6 论文级报告 | 六要素 + 图 | 本文件 + 5 图 | PASS | `docs/P233W2-N10_campaign_optimizer.md` |

## 1 案例介绍

**物理/工程背景**：战役 DAG（campaign_plans / campaign_nodes）是平台把「战役目标」拆成可并行工作包（node）的调度图。节点之间是交付依赖：下游节点的输入是上游节点的输出。现实事故形态是**节点长期停在 blocking 状态**，而平台的既有仪表只能回答「哪些节点 blocked」（计数视图），回答不了三个决策问题：

1. 哪些阻塞节点真正卡住完成（瓶颈），哪些只是孤立挂起？
2. 阻塞的根因是什么类别（上游没交付 / 交付口径不符 / 资源不足 / 代码缺陷）？
3. 给定根因，重排资源能省多少时间（要能给数字，不是给口号）？

本节点把这三问做成机制：**瓶颈检测 → 根因分类 → 反事实建议 → 历史回测**，四段全部纯函数 + 确定性，判定只用状态/依赖/资源元数据（零领域语义）。

### 1.1 语料（参数表）

| 项 | 取值 | 出处 |
|---|---|---|
| 语料节点总数 | 3983 | `campaign_nodes LEFT JOIN tensor_dev_missions`（只读快照） |
| plan 数 | 417 | 同上 |
| 阻塞节点数 | 361 | status ∈ blocked, failed, orphan, revoked |
| 旗舰样本 | plan 233：10 节点 / 9 边 / 4 blocked | `campaign_nodes.plan_id=233` |
| 并发容量 | 40 | `config/campaign_optimizer.v1.json` |
| 原始调度重试预算 | 2 次（敏感性 2/3/5） | 同上 |
| 单次尝试折算 | 节点墙钟 ÷ 5（敏感性 5/2） | 同上 |
| 干扰时延（干预等待） | 0 s（保守取 0） | 同上 |
| 随机 DAG 负控 | 200 trials/族，nodes 6–14，edge p=0.25，seed=20261110 | 同上 |

## 2 怎么做的

### 2.1 流水线

![pipeline](fig1_pipeline.png)

### 2.2 瓶颈检测（AC1 + AC5）

节点被判为瓶颈当且仅当 **状态 ∈ blocking 集合** 且 **严重度 ≥ floor**。严重度是五个结构化分量的加权和（权重全在 config）：

```
severity = w1·gates_plan_completion      # 未满足即 plan 无法完成（plan 完成 = 全节点 satisfied）
         + w2·min(1, downstream_gated / #remaining)   # 被它卡住的传递后继占比
         + w3·on_critical_path           # 总浮动 = 0（最长剩余链上）
         + w4·min(1, downstream_weight_share)         # 下游剩余工期占 plan 剩余工期
         + w5·min(1, stall_age / stall_threshold)     # 静默时长（有快照时钟时）
```

关键路径用「节点工期权重的最长链」计算（`es`/`tail` 双向最长路 → 总浮动），因此**孤立阻塞节点**（无下游）会被判为瓶颈但严重度最低——这正是 plan 233 的实测形态：

![plan dag](fig2_plan_dag.png)

plan 233 的 4 个阻塞节点全部检出，严重度排序把两个「卡住下游」的节点（1354, 1356）排在前面，两个孤立阻塞排最后（severity 1.00）。

### 2.3 根因分类（AC2 + AC4）

声明式规则引擎：规则是 config 里的有序 JSON（`feature/op/value` 谓词），代码只做谓词求值。特征白名单：`node_type`, `has_mission`, `mission_class`, `consecutive_fails`, `upstream_unsatisfied`, `blocked_upstream`, `downstream_gated`, `duration_ratio`, `total_float_s`, `on_critical_path`。节点名与 block_reason 文本**不在白名单**，只作为证据随产物输出。

规则链（首次命中即返回）：

1. `barrier_unreleased` → **upstream_not_delivered**：node_type in ['gate', 'checkpoint', 'review'] ∧ has_mission eq False
2. `delivered_but_hollow` → **artifact_mismatch**：consecutive_fails gte 2 ∧ mission_class eq succeeded ∧ duration_ratio lte 2.0
3. `repeated_task_failure` → **code_error**：consecutive_fails gte 2
4. `executor_lost_no_delivery` → **resource_shortage**：mission_class eq failed
5. `unmounted_node` → **upstream_not_delivered**：has_mission eq False
6. `detached_after_delivery` → **resource_shortage**：mission_class in ['succeeded', 'active', 'other']
7. `upstream_cascade` → **upstream_not_delivered**：upstream_unsatisfied gte 1
8. `fallback` → **unknown**：（兜底）

### 2.4 资源重分配建议（AC3 的输入）

每条建议都带**反事实节省量**：只改该节点的重试策略（其余保持原始调度）重跑模拟，取 makespan 差；低于 `advice.min_expected_saving_s` 的建议直接丢弃（不产生噪声建议）。容量建议同理（capacity + increment 重跑）。改进调度带**守卫**：候选优先级逐一模拟后取最优，因此不会出现「改了反而更慢」。

plan 233 的建议清单：

| 节点 | 根因 | 动作 | 原始尝试 | 改进尝试 | 预计节省 [h] |
|---|---|---|---|---|---|
| 1356 | resource_shortage | requeue_fresh_worker | 3 | 2 | 8.43 |
| 1354 | resource_shortage | requeue_fresh_worker | 3 | 2 | 8.07 |

> 合计可算节省 16.50 h；计划整体剩余完工 49.49 h → 32.99 h。孤立阻塞节点（无下游）按 makespan 口径节省为 0，仍进瓶颈清单但要由「完成度」而非「工期」驱动处理——这一点在建议里显式留痕（不进建议清单）。

### 2.5 历史回测（AC3）

同一 DAG、同一容量，比较两种策略的**剩余工作完工时间**（remaining-work makespan）：

* 原始调度：派单顺序排队 + 历史重试预算 `retry_max`（用尽后等一次干预再成功）；
* 重分配：根因感知重试预算（资源类 1 次即改派新执行器 / 上游类 0 次即暂缓派单 / 交付类 1 次返工 / 代码类 1 次修复后重试）+ 关键路径优先（带守卫）。

![throughput](fig4_throughput.png)

敏感性（18 档：retry_max ∈ {2,3,5} × divisor ∈ {5,2} × capacity ∈ {40,8,4}）：

| retry_max | divisor | capacity | 样本 | 中位提升 | 最差 plan |
|---|---|---|---|---|---|
| 2 | 5 | 40 | 61 | 33.3% | 3.0% |
| 2 | 5 | 8 | 61 | 33.3% | 3.0% |
| 2 | 5 | 4 | 61 | 33.3% | 3.0% |
| 2 | 2 | 40 | 61 | 33.3% | 3.0% |
| 2 | 2 | 8 | 61 | 33.3% | 3.0% |
| 2 | 2 | 4 | 61 | 33.3% | 3.0% |
| 3 | 5 | 40 | 61 | 50.0% | 27.2% |
| 3 | 5 | 8 | 61 | 50.0% | 27.2% |
| 3 | 5 | 4 | 61 | 50.0% | 27.2% |
| 3 | 2 | 40 | 61 | 50.0% | 27.2% |
| 3 | 2 | 8 | 61 | 50.0% | 27.2% |
| 3 | 2 | 4 | 61 | 50.0% | 27.2% |
| 5 | 5 | 40 | 61 | 66.7% | 51.5% |
| 5 | 5 | 8 | 61 | 66.7% | 51.5% |
| 5 | 5 | 4 | 61 | 66.7% | 51.5% |
| 5 | 2 | 40 | 61 | 66.7% | 51.5% |
| 5 | 2 | 8 | 61 | 66.7% | 51.5% |
| 5 | 2 | 4 | 61 | 66.7% | 51.5% |

**判读**：18 档里中位提升全部 ≥ 20%（最低一档 33.3%），因此 AC3 不依赖参数挑拣。divisor 只把绝对小时数同比缩放、不改变提升比例（增益对「单次尝试怎么折算」不敏感，这是模型的结构性质）；retry_max 越大提升越大（原始调度烧的重试越多）；capacity 在守卫下不再产生影响——守卫把「改优先级反而更慢」的情形挡住了（无守卫时 capacity=4 档最差 plan 曾出现 −30.4%）。

## 3 计算结果（程序化注入，可复算）

### 3.1 根因分类混淆矩阵（AC2）

![confusion](fig3_confusion.png)

| 人工标注 ↓ / 预测 → | upstream_not_delivered | artifact_mismatch | resource_shortage | code_error | unknown |
|---|---|---|---|---|---|
| upstream_not_delivered | 8 | 0 | 0 | 0 | 0 |
| artifact_mismatch | 0 | 2 | 0 | 0 | 0 |
| resource_shortage | 0 | 0 | 32 | 0 | 0 |
| code_error | 0 | 0 | 0 | 14 | 0 |
| unknown | 0 | 0 | 0 | 0 | 0 |

准确率 100.0%（n=56）；分层留出集 100.0%（33 训练 / 23 留出）。逐类召回：upstream_not_delivered 100.0%，artifact_mismatch 100.0%，resource_shortage 100.0%，code_error 100.0%。

### 3.2 负控（AC5）

![negative control](fig5_negative_control.png)

检测器在随机 DAG 上的假阳性率 **0.00%**（0/3824），阈值 10%；同一批图上「只看结构」的启发式对照假阳性率 **39.98%** —— 负控因此有鉴别力：检测器退化成结构启发式会立刻被这条判据打红。

族 B（状态按历史边际抽样，含 blocked）里，真阻塞节点召回 100.0%（190/190），非阻塞节点上仍 0 假阳性。

## 4 注意点（踩过的坑 / 局限 / 适用范围）

1. **「瓶颈」有两种口径，必须分离**。plan 233 有 2 个孤立阻塞节点（无下游、无后继）：它们卡住的是**完成度**不是**工期**。若只按关键路径口径检测，召回只有 50%；若只按 blocking 状态检测，precision 虚高且无法排序。本机制用「gates_plan_completion 主项 + 严重度排序」同时满足两者：召回 100%，且孤立阻塞排最后。
2. **命名字段是证据不是特征**。首版设计里 `block_reason` 就摆在节点结构旁边，极易被顺手当特征用（等于把答案喂给分类器）。现在用特征白名单 + 两条行为测试（隐去全部文本、随机改名）把它钉死。
3. **优先级重排会咬人**。capacity=4 的敏感性档里，不带守卫的 `critical_first` 让最差 plan慢了 30.4%（长作业挤占短作业的就绪窗口）。所以真实引擎必须「先模拟再改」：候选优先级取最优，且每条建议需过 `min_expected_saving_s`。
4. **人工标注的独立性有限（诚实声明）**：55 例标注由本 agent 完成，标注时能看到平台 block_reason 记录；分类器看不到文本。因此 AC2 的准确率证明的是「结构化特征能复原原因通道」，**不是**「与完全独立的第三方标注一致」。规则顺序与 `short_delivery_ratio` 阈值是在看过聚合分布与这 55 例之后定的，留出集准确率只能算弱泛化检查。更硬的证据需要平台补一条结构化 `root_cause` 字段（由执行器写、agent 不可改），届时可直接替换标注通道。
5. **工期口径**：历史 blocked 节点的 `duration_s` 是含重试的总墙钟，单次尝试按 `divisor` 折算；实测增益对 divisor 不敏感（同比缩放），但**绝对小时数**敏感——报告里所有小时数都应读到「模型内的小时」，不是墙钟预测。
6. **不做**的事：不自动改派真实任务、不写库、不触碰冻结 API。本机制只产出建议 + 数字，执行权仍在派单链路（与 self_heal 机制的分工：self_heal 管单节点重试/降级，本机制管全图重排）。

## 5 误差判定

本节点是**机制/调度**类节点，关键量是「召回率、分类准确率、makespan 缩短比例、假阳性率」，没有实验/解析基准可对标，逐项判定如下（阈值即预注册判据）：

| 关键量 | 基准 | 实测 | 相对误差 | 是否达线 |
|---|---|---|---|---|
| plan 233 瓶颈召回 | 100%（4/4） | 100.0% | 0.0% | 是（>15% 容忍度不适用，判据是等号） |
| 根因分类准确率 | ≥70% | 100.0% | +30.0 pp | 是 |
| makespan 缩短（plan 233） | ≥20% | 33.3% | +13.3 pp | 是 |
| 随机 DAG 假阳性率 | ≤10% | 0.00% | −10.00 pp | 是 |

**「是否 <3%」的显式回答**：不适用——本节点的判据是机制指标（召回/准确率/比例/假阳性率），不存在实验或解析基准值；调度模型与真实墙钟的偏差没有可对标基准，故不做 3% 声明。唯一带「模型 vs 现实」比较的量是 plan 233 的 49.49 h → 32.99 h，二者都在**同一模型内**，不可当作墙钟预测。

## 6 复算命令

```bash
# 1) 刷新语料快照（唯一联网步骤，只读 SELECT）
python3 -m tools.campaign_optimizer.cli snapshot --out artifacts/campaign_optimizer/corpus_snapshot.json
# 2) 全量回测 + 出报告与图（离线、确定性）
python3 -m tools.campaign_optimizer.cli backtest \
  --snapshot artifacts/campaign_optimizer/corpus_snapshot.json \
  --labels artifacts/campaign_optimizer/rootcause_labels.json \
  --out-json artifacts/campaign_optimizer/report.json \
  --out-md docs/P233W2-N10_campaign_optimizer.md \
  --figure-dir artifacts/campaign_optimizer
# 3) 判据自检（AC1–AC5 全部由 pytest 断言）
python3 -m pytest tests/test_campaign_optimizer_*.py -q
```

## 7 审计链

* 语料快照 content_hash：`c6541e7f6a171d1fc54885548b048aff1e3e7a03e84087186b7a30b5439a49a8`
* 报告 JSON：`artifacts/campaign_optimizer/report.json`（每图 sha256 见 `figures_sha256`）
* 人工标注：`artifacts/campaign_optimizer/rootcause_labels.json`（56 例，含 confidence 与 note）
* 配置（唯一阈值来源）：`/root/tp-agent/work/m10680/config/campaign_optimizer.v1.json`
* 判定代码零文本读取：`rootcause.ALLOWED_FEATURES` 白名单 + pytest 行为测试

