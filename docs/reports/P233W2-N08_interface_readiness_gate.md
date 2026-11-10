# P233W2-N08 · 派单前置『接口-就绪』机器门成熟化（interface readiness gate）

**节点**：P233W2-N08（战役 DAG 节点，仓库 TensorPlatform）  
**报告工件**：`artifacts/dispatch_readiness/backtest_report.json`（sha256 `4f885adf9dc0445a…`）、
`artifacts/dispatch_readiness/history_snapshot.json`（sha256 `90beefd7d123c58d…`）、
SQL（sha256 `bab0e6eb04c4548d…`）

## ① 案例介绍

**物理/工程背景**。战役 DAG 派单失败有一类典型根因：下游节点被 blocked，不是因为它自己难，
而是因为上游节点没交付、或交付的口径（字段/单位/版本）与消费方契约不符。平台此前靠人肉巡检
发现这类 blocked（如 §① 末尾的 plan 233 实例）。本节点把这件事做成**派单前 fail-closed 判定**。
机制本身零领域语义：它只认『artefact 类别 + 必需字段 + 单位 + 版本 + 需求强度』，域数据全部
经配置/API 注入。

**数据总体（可 SQL 溯源）**。历史终态节点取自平台库 `campaign_nodes`（status ∈ {done, blocked}）
与 `tensor_dev_missions`（节点任务终态、时间戳、result 字段名），一次性只读导出：
终态节点 **2802** 个（done 2440 / blocked 362），
关联 mission **2822** 条。标注集（带契约、可判标签）**2509** 个，
其中正例（blocked 且停摆时上游工件仍缺）**17** 个、负例（done）**2492** 个、
出范围（记录原因为确证基建故障 infra）**293** 个。

**plan 233 复核（本节点自身所在战役）**。战役播种说明称『10 单里 4 单 blocked』，本节点实测：
4 单 blocked 原因为 `infra x5 (agent_lost)`（worker 丢失），契约门在这 4 单上全部判 READY——
见 `artifacts/dispatch_readiness/attribution_examples.md`。**上游缺失不是 plan 233 的 blocked 根因**，这是本单的核心诚实结论之一（见 §⑤）。

## ② 怎么做的

**契约声明**。每个节点可声明 `input_contract`（`requires[]`：upstream 节点键、artefact 类别、
需求强度 mode ∈ {declared, delivered, terminal_success}、必需字段、单位、最低版本）与 `output_contract`
（`provides[]` + 字段/单位/版本）。无声明时按**结构**推断：gate/checkpoint 节点要求上游 `terminal_success`，
task 节点只要求上游被 `declared`（等价于平台派单器现役策略 `READY={done,in-flight}`，契约并行派单）。

**就绪判定（三态）**。`resolver.evaluate_readiness(contract, upstream_states)` 是纯函数：
READY（全部需求满足且口径一致）/ NOT_READY（上游未派单或未完成）/ CONFLICT（已交付但字段缺失、
单位不符或版本过旧）。每条判定输出人可读归因（哪条需求、缺什么、上游是谁）。

**fail-closed 派单门**。`gate.DispatchGate.preflight()` 只给派单器一个决策：`dispatch` 或 `defer`；
节点未登记、契约求值抛异常等一切异常路径都返回 `defer`（绝不放行、也绝不崩）。
`DeliveryLedger` 记录拦截与后续放行结果，直接产出『拦截后补上游再派 → 一次通过率』。

**历史回测**。对每个历史节点重建**给定时刻可见的工件集**：mission `created_at ≤ t` ⇒ 上游已派单；
`finished_at ≤ t` ⇒ 上游已交付；`status='succeeded'` ⇒ 终态成功；交付记录的字段集取 mission `result` 的键名。
三个时刻分别考核：`@dispatch`（该节点被派出的瞬间，门的真实使用点）、
`@consult`（没被派出的节点用其出生时刻，即最后一次可咨询时刻，主判据）、`@stop`（任务结束/落定时刻）。

**标签（配置注入，SQL 可溯源）**。规则在 `config/dispatch_readiness_labels.json`，代码零硬编码：
`delivery_gap`（主判据，客观）= blocked 且停摆时至少一个已声明上游工件仍缺；
`upstream_cause`（审计用）= block_reason 文本显式指向上游/放行门；
`infra_cause`（出范围）= 记录原因为确证基建故障（agent_lost / llm_error / hollow success），
这类故障与上游交付无关，混进来只会污染标签，故整层剔除并单独报告。

**工具链**。`backend/tools/dispatch_readiness/`（models / contracts / resolver / gate / backtest /
history_export / audit_zero_domain / cli）、`backend/tests/test_dispatch_readiness*.py`（33 例 pytest，
全部合成夹具、零领域词）、`scripts/dispatch_readiness_figures.py`（图）、
`scripts/dispatch_readiness_report.py`（本报告的机器数字源）。全流程可用
`python -m tools.dispatch_readiness.cli {export,backtest,audit,check}` 复现（`check` 阈值不过则退出码 1）。

## ③ 计算结果

### 3.1 主判据混淆矩阵（`@consult`，mode=declared，stop anchor=birth）

| | 门判 defer | 门判 dispatch |
|---|---|---|
| 实际 blocked 且有上游缺口（n=17） | TP=11 | FN=6 |
| 实际 done（n=2492） | FP=113 | TN=2379 |

| 指标 | 值 | 95% CI | 冻结阈值 | 判定 |
|---|---|---|---|---|
| recall | 64.71% | [41.30%, 82.69%] | ≥60% | PASS |
| FPR | 4.53% | [3.79%, 5.42%] | ≤5% | PASS |
| AUC | 0.806 | — | 参考 | — |
| 置换零控 AUC 95% 区间 | [0.475, 0.564] | 200 次 | 覆盖 0.5 | PASS |

### 3.2 三个评估时刻的对照（同一批标签、同一预测器）

| 时刻 | 正例 | TP/FN | FP/TN | recall | FPR | AUC |
|---|---|---|---|---|---|---|
| `@consult`（主） | 17 | 11/6 | 113/2379 | 64.71% | 4.53% | 0.806 |
| `@dispatch`（仅被派出的节点） | 9 | 3/6 | 112/2378 | 33.33% | 4.50% | 0.645 |
| `@stop` | 17 | 17/0 | 536/1956 | 100.00% | 21.51% | 0.892 |

### 3.3 需求强度（契约口径）政策前沿

| mode | done 节点被误拦（FPR） | 真缺口被拦（coverage） |
|---|---|---|
| declared | 4.30% (105/2440) | 64.71% (11/17) |
| delivered | 20.86% (509/2440) | 70.59% (12/17) |
| terminal_success | 20.86% (509/2440) | 70.59% (12/17) |

`declared` 是平台现役派单策略（契约并行，上游 in-flight 即放行），FPR 落在 5% 预算内；
`delivered`/`terminal_success` 是严格口径，能多抓 1 个真缺口，但会把 **1/5 的健康节点** 拦下来——
这是严格契约的真实代价，也是本报告给出的政策取舍数字。

### 3.4 标签锚点敏感性（结论不能被定义挑出来）

| stop 锚点 | 标注集 | 正例 | recall | FPR | AUC |
|---|---|---|---|---|---|
| birth | 2509 | 17 | 64.71% | 4.53% | 0.806 |
| update | 2509 | 17 | 64.71% | 4.53% | 0.806 |
| auto | 2509 | 11 | 100.00% | 4.52% | 0.985 |

### 3.5 拦截后修复 → 一次通过率（AC5）

* 门会拦下的节点：**128**
* 拦下后补齐上游并完成交付：**1**，其中一次通过（无重试）：**1**
* 一次通过率：**100.00%**（样本极小，n=1，不作强结论）
* 拦截后仍卡着、缺口未闭：**15**

### 3.6 标注层分布

| stratum | 节点数 |
|---|---|
| `negative_done|label=0` | 2440 |
| `excluded_infra|label=None`（出范围，不计入主判据） | 293 |
| `blocked_no_gap|label=0` | 52 |
| `positive_delivery_gap|label=1` | 17 |

## ④ 结果图

**confusion_matrix** — 主判据混淆矩阵（@consult，全部标注节点）。

![confusion_matrix](../../artifacts/dispatch_readiness/figures/confusion_matrix.png)

**label_strata** — 历史终态总体按标注层分布（log 轴）。

![label_strata](../../artifacts/dispatch_readiness/figures/label_strata.png)

**mode_sensitivity** — 契约口径（需求强度）政策前沿：误拦率 vs 真缺口覆盖率。

![mode_sensitivity](../../artifacts/dispatch_readiness/figures/mode_sensitivity.png)

**permutation_null** — AC3 负控：标签随机置换 200 次 AUC 分布，95% 区间覆盖 0.5。

![permutation_null](../../artifacts/dispatch_readiness/figures/permutation_null.png)

**roc_curves** — 三个评估时刻的 ROC；竖点线为 FPR 5% 预算。主判据 AUC 见 ③。

![roc_curves](../../artifacts/dispatch_readiness/figures/roc_curves.png)

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **前提被数据否证**。战役说明称 plan 233『10 单 4 单 blocked』源于上游未交付；实测 4 单原因全为
   `infra x5 (agent_lost)`，契约门在它们身上全部判 READY（应判 READY——它们上游齐备）。
   即：**『上游缺失』不是这批 blocked 的根因**，本机制不解决 agent_lost，也不该被记作它的功劳。
2. **正例池小而敏感**。全库 362 个 blocked 里，剔除确证基建故障后仅 17 个带真实上游缺口（4.7%）；
   换标签锚点（`update`/`auto`）正例变 17/17/11（见 ③3.4）。recall 的 95% CI 宽到 [41%, 83%]，
   n 小是硬约束，冻结阈值下 PASS 但**不应外推**为普适判别力。
3. **recall 与 FPR 的机制性权衡已量化**。严格口径下 recall 提到 70.6%，FPR 立刻从 4.3% 跳到 20.9%：
   因为平台本来就是契约并行派单，1/5 的健康节点在上游还在跑时就被派出。想要严格门就必须改派单策略，
   不是改判定代码。
4. **@consult 对『从未被派出』节点的时刻近似**。这类节点（放行门、出生即等待）没有派单时刻，
   回测用其出生时刻近似；`update`/`auto` 锚点的敏感性表就是为了让这个近似不藏起来。
5. **能力未覆盖**：CONFLICT（口径不符）路径在历史库中**零正例**（`result` 文本里 11 条『口径/units/schema』
   命中全部是基建故障节点的无关提及），该路径只有合成夹具验证（`test_conflict_on_unit_mismatch` 等），
   因此本报告不对『口径不符』给任何实测判别力。
6. **NOT_SUPPORTED**：agent_lost（worker 丢失）/ LLM 报错 / hollow success 类 blocked 本机制明确不支持。

## ⑥ 误差判定（逐项对照基准）

| 关键量 | 本单值 | 基准/判据 | 误差 | 是否 <3% |
|---|---|---|---|---|
| recall | 64.71% | 预注册下限 60% | 超线 4.71% | 不适用（阈值判据，非连续量基准） |
| FPR | 4.53% | 预注册上限 5% | 余量 0.47% | 不适用（同上） |
| 置换零控区间中心 | 0.500 | 0.5 | 0.03% | **否**（0.06%，但见注） |
| 标签覆盖率复算 | 2509 | 快照终态数 2802 中带契约者 | 逐行 | 是（SQL 逐行可溯源，零手抄） |

**是否 <3% 的显式回答**：本单是**判别力/政策**类工作，没有物理量的解析解或实验基准可对标，
因此不存在『关键物理量误差 <3%』这一项——这是声明而非含糊。可对照的只有预注册阈值与置换零控：
置换零控的均值与 0.5 的偏差为 0.03%（<3%），说明置换管道无泄漏；
反过来，**观测 AUC 与零控分布完全分离**（观测值远在 95% 区间之外），这才是判别力的证据。

## ⑦ 可复现与审计链

* SQL：`backend/tools/dispatch_readiness/sql/dump_history.sql`（只读，sha256 `bab0e6eb04c4548dde445b2a773b9c9c2ac61f664bd628ccae3190bcd56af78a`）
* 快照：`artifacts/dispatch_readiness/history_snapshot.json`（sha256 `90beefd7d123c58de7d650b8f53350000ae8ffe9edb85da972e27ce195d99cf6`）
* 回测报告：`artifacts/dispatch_readiness/backtest_report.json`（sha256 `4f885adf9dc0445ac8de8e0b52eb9a8ef62ebe1c6243439753d2f82357c4457f`）
* 归因样例：`artifacts/dispatch_readiness/attribution_examples.md`
* 汇总：`artifacts/dispatch_readiness/summary.json`
* 复现命令：`cd backend && python3 -m tools.dispatch_readiness.cli export && python3 -m tools.dispatch_readiness.cli backtest && python3 -m tools.dispatch_readiness.cli check`
* pytest：`python3 -m pytest backend/tests/test_dispatch_readiness.py backend/tests/test_dispatch_readiness_zero_domain.py -q`（33 passed）

## ⑧ 验收判据逐条判定（预注册，数字与路径见上）

| 判据 | 要求 | 实测 | 判定 |
|---|---|---|---|
| AC1 标注集 | ≥40 节点、≥10 正例、SQL 可溯源 | 2509 节点 / 17 正例 | PASS |
| AC2 判别力 | recall ≥60%、done FPR ≤5%（阈值冻结） | recall 64.71%、FPR 4.53% | PASS |
| AC3 置换负控 | 200 次置换 AUC 95% 区间覆盖 0.5 | [0.475, 0.564] | PASS |
| AC4 零领域语义 | backend/tools/tests 无域词条；缺配置降级不崩 | 扫描 `backend/tools/dispatch_readiness` + 两个测试文件 = clean（22 条词表在 `config/dispatch_readiness_domain_terms.json`）；缺配置走 `degraded_identity`（`test_missing_terms_config_degrades_to_identity`）、缺契约/缺标签配置不崩 | PASS |
| AC5 可归因 | 每次拦截有人可读归因 + 拦截后一次通过率 | 归因样例见 `attribution_examples.md`；一次通过率 100.00%（n=1，样本不足，部分达成） | 部分 PASS |
| AC6 文档 | 论文级六要素 + 混淆矩阵 + ROC + 归因示例 | 本文件（③ 表 / ④ 图 / ⑤ 局限 / ⑥ 误差） | PASS |

> 诚实声明：AC5 的『一次通过率』只有 n=1 的可观测样本，数值 100% 不具备统计意义；
> AC2 为小样本 PASS（recall CI 下界 41%），结论按 §⑤.2 限定适用范围。

