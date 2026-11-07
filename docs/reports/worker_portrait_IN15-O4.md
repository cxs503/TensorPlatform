# worker 画像最小面（IN15-O4 · 自回归燃料④：识产能）

**快照时间**：2026-11-07T00:45:11.781668+00:00　**schema**：`worker_portrait/v1` / **口径版本**：`wp-v1`　**性质**：只读观察，不参与调度

## ① 案例介绍：观察对象与问题

TensorPlatform 的派发面已经能持续产出工单，但「谁在干活、干得怎么样」只以事件流形式散落在`tensor_dev_missions` 与 `tensor_agent_session_events` 两张表里，没有任何一处能回答**产能问题**：某个 worker 领取工单要等多久？成功率多少？7200 s 预算内能不能收工？主要在哪几个仓干活？本节点交付的是这个问题的**最小面**——一份可复算、逐条可溯、只读的 worker 画像表，为后续波「按产能调度」提供燃料，自身**不改任何调度策略**。

**数据面**（只读两表，无 schema 改动、无新表）：

| 表 | 用途 | 关键列 |
|---|---|---|
| `tensor_dev_missions` | 工单主档 | id / claimed_by(worker) / repo / status / created_at / finished_at / model / runner |
| `tensor_agent_session_events` | 领取时序证据 | session_id, kind='note', payload->>'subtype'='mission_claimed', created_at |

**样本**：4591 张已领取工单 / 62 个 worker / 其中 55 个活跃 worker（≥3 单）出画像，7 个低于阈值仅登记。

### 指标定义（definition_version `wp-v1`，与 artifact JSON 逐字同源）

| 指标 | 定义 |
|---|---|
| `pickup_s` | claim_at - created_at（领取延迟，秒）；claim_at=最早 mission_claimed 事件时间 |
| `wall_s` | finished_at - claim_at（在岗执行时长，秒）；claim_at 缺失时退化为 finished_at - created_at |
| `success_rate` | delivered/(delivered+failed)，delivered={succeeded,awaiting_pr_merge}，failed={failed,revoked,stopped}；在飞态不进分母 |
| `completion_7200` | #(wall_s<=7200)/#(已完结)；7200s=budget.max_wall_seconds 默认值 |
| `repo_distribution` | 该 worker 领取工单按 repo 计数与占比 |
| `invalid_interval` | pickup_s<0、wall_s<0 或 claim_at>finished_at 的行，剔出时长统计并单独计数 |

## ② 怎么做的：方法、流程与工具链

```
psql 只读导出（单条 SELECT，不开写事务）
   → tools/worker_portrait/portrait.py  aggregate()   # 纯函数聚合，零第三方依赖
   → artifacts/worker_portraits/report.json + index.md + <worker>.md   # 55 份画像
   → tools/worker_portrait/crosscheck.py              # 独立 SQL 复算，逐项相对误差
   → tools/worker_portrait/figures.py                 # 4 张判据图
   → tools/worker_portrait/report.py                  # 本报告（数字程序化注入）
```

实现要点：

1. **领取速度的唯一时序证据**：mission 表没有 `claimed_at` 列。领取时刻取 `tensor_agent_session_events` 中 `mission_claimed` 事件的**最早**时间戳，按 `session_id` 与工单关联；因此该指标有覆盖度问题——只有 2425/4591 = 52.8% 的工单可算，画像里逐 worker 明示 `n_claim_events`，不做插补、不外推。
2. **成功率不把在飞态算成失败**：`delivered` = {succeeded, awaiting_pr_merge}，`failed` = {failed, revoked, stopped}；running/queued/suspended 不进分母、单独计数。
3. **7200 s 履约**：`finished_at − claim_at ≤ 7200`（claim 缺失时退化为 `finished_at − created_at`），分母是已完结单——这才是「预算内收工率」的正确口径。
4. **逐条可溯**：每份画像内嵌 `per_mission` 明细（mission_id/repo/status/claim_at/pickup_s/wall_s）并对 mission_id 集合取 sha256 指纹；`--verify` 用同一 SQL 复查在线表，逐单比对 id/worker/repo/status 四项并检查无漏单。
5. **负例与 fail-closed**：空 worker（无工单）、空归属（`claimed_by` 为空）、活跃 worker 数 < 3、快照为空，全部拒收（抛异常 / 退出码 2），宁报 FAIL 不出空画像。
6. **只读纪律**：全部 SQL 均以只读单条 SELECT 形式下发（`psql -tA`），SQL 文本带 sha256 指纹 `dbcf154c98b4a1d0…` 固化在产物中；未创建/修改任何表，未触碰冻结 API 面。

## ③ 计算结果

### 3.1 覆盖度

| 项 | 值 |
|---|---|
| 工单样本 | 4591 |
| 可算领取速度（含 claim 事件） | 2425 （52.8%） |
| worker 总数 / 活跃 / 低于阈值 | 62 / 55 / 7 |
| 时长样本（pickup / wall） | 2425 / 4546 |
| mission_id 指纹 sha256 | `11125fa1396a9f2ca01147e168536ef7b36623e362327fda5ca28a9ef69425dc` |

### 3.2 平台整体（pooled）

| 指标 | 值 | 样本 n |
|---|---|---|
| 领取延迟 中位 / p90 / 均值 / 最大 | 1.62 s / 19.6 s / 1099.1 s / 183651 s | 2425 |
| 在岗时长 中位 / p90 / 均值 / 最大 | 970.6 s / 3280.9 s / 3626.3 s / 122969 s | 4546 |
| 成功率（delivered/settled） | 83.32% （3792/4551） | 4551 |
| 7200 s 内完成率 | 94.43% （4293/4546） | 4546 |

### 3.3 画像分布特征（按 worker 离散度）

| 指标 | min | 中位 | max | 说明 |
|---|---|---|---|---|
| 工单数 n_missions | 3 | 95 | 157 | 44 个 worker ≥50 单，长尾在个位数 |
| 成功率 | 14.3% | 85.0% | 100.0% | 离散度大，最低者 tp-ds31-1 = 14.3%（6/42） |
| 7200s 内完成率 | 81.0% | 94.5% | 100.0% | 整体贴近预算线 |

产能与质量的秩相关很弱：`n_missions` vs 成功率 ρ = 0.083，vs 7200s 完成率 ρ = 0.159——**高产不等于低质**，这条对后续调度波很关键：简单按工单数排序不会系统性引入失败率。

### 3.4 top-12 画像（完整 55 份见 `artifacts/worker_portraits/`）

| worker | n | 成功率 | 领取中位(s) | 领取p90(s) | 在岗中位(s) | 7200s内 | 主仓 | 模型 |
|---|---|---|---|---|---|---|---|---|
| tp-qa-1 | 157 | 91.0% | 11.9 | 2157 | 409 | 97.4% | TensorLBM | qwen3.6-plus |
| tp-gen-1 | 120 | 90.8% | 12.1 | 820 | 462 | 96.6% | TensorPlatform | qwen3.7-plus |
| tp-qa-2 | 118 | 87.2% | 4.5 | 125 | 690 | 94.9% | TensorLBM | qwen3.6-plus |
| tp-impl-1 | 116 | 82.8% | 2.2 | 16 | 920 | 94.8% | TensorLBM | deepseek-v4-flash |
| tp-impl-2 | 112 | 83.8% | 1.1 | 10 | 911 | 94.6% | TensorLBM | deepseek-v4-flash |
| tp-impl-3 | 112 | 85.6% | 1.6 | 7 | 775 | 94.5% | TensorPlatform | deepseek-v4-flash |
| tp-impl-5 | 111 | 84.5% | 2.1 | 14 | 869 | 95.5% | TensorLBM | deepseek-v4-flash |
| tp-impl-6 | 110 | 87.2% | 2.0 | 8 | 1004 | 94.4% | TensorLBM | deepseek-v4-flash |
| tp-impl-4 | 109 | 87.0% | 1.6 | 14 | 1040 | 96.3% | TensorLBM | deepseek-v4-flash |
| tp-ver-1 | 109 | 87.0% | 1.2 | 7 | 1022 | 95.4% | TensorLBM | deepseek-v4-flash |
| tp-arch-1 | 105 | 77.1% | 1.7 | 15 | 922 | 94.3% | TensorPlatform | deepseek-v4-flash |
| tp-impl-8 | 105 | 86.5% | 2.2 | 8 | 801 | 95.2% | TensorLBM | deepseek-v4-flash |

### 3.5 模型族产能（按各 worker 主用模型归并，暴露别名/版本混杂）

| 主用模型 | 工单数 | 成功率 |
|---|---|---|
| `deepseek-v4-flash` | 3553 | 85.51% |
| `qwen3.6-plus` | 476 | 87.08% |
| `qwen3.7-plus` | 179 | 90.96% |
| `Qwen3.8-27B` | 145 | 78.17% |
| `Qwen3.6-35B-A3B` | 100 | 58.16% |
| `DeepSeek-V4-Flash-0731` | 93 | 27.47% |
| `MiniMax-M2.5-AWQ` | 38 | 26.32% |

### 3.6 仓分布（活跃 worker 合计）

| repo | 工单数 |
|---|---|
| TensorLBM | 889 |
| TensorPlatform | 840 |
| TensorDEM | 408 |
| TensorSPH | 377 |
| TensorBEM | 356 |
| TensorFEM | 346 |
| TensorFVM | 310 |
| TensorCAD | 277 |
| TensorPapers | 234 |
| TensorDG | 218 |
| ship-performance-platform | 193 |
| TensorPhysics | 76 |
| ship-platform | 58 |
| Ghost-Repo-XYZ | 2 |

### 3.7 异常面（后续波的靶子）

- **领取慢**（pickup p90 > 600 s）：`tp-qa-1` 2157s；`tp-gen-2` 1596s；`tp-gen-1` 820s。这 3 个 worker 的 p90 明显脱离平台 19.6 s 水平，是排队饥饿的直接证据。
- **7200 s 履约偏低**（<90%）：`tp-merge-5` 81.0%(n=22)；`tp-merge-3` 85.7%(n=21)；`tp-merge-4` 89.5%(n=19)。
- **成功率洼地**：`tp-ds31-1` 成功率 14.3%（6/42），n=43，主用模型 `DeepSeek-V4-Flash-0731`；该模型族在 3.5 表里整体成功率同样垫底，说明这是**模型/席别层面的系统性失败**，不是单 worker 抖动。

## ④ 结果图

![fig1_worker_throughput.png](artifacts/worker_portraits/figs/fig1_worker_throughput.png)

*图1 产能排序：领取工单数 top-20 与成功率（右侧标注 n / 成功率）*

![fig2_pickup_wall_dist.png](artifacts/worker_portraits/figs/fig2_pickup_wall_dist.png)

*图2 时序分布：领取延迟（左，log-log）与在岗时长（右，含 7200s 预算线）*

![fig3_completion_7200.png](artifacts/worker_portraits/figs/fig3_completion_7200.png)

*图3 各 worker 7200s 预算内完成率（升序），红线为平台 pooled 值*

![fig4_repo_distribution.png](artifacts/worker_portraits/figs/fig4_repo_distribution.png)

*图4 活跃 worker top-20 的仓分布（堆叠占比）*

## ⑤ 注意点：坑、局限与适用范围

1. **领取速度只覆盖一半工单**（52.8%）。`tensor_agent_session_events` 只保存了有 session 的工单事件，早期（10-29 至 11-02 部分班次）与部分 runner=openhands 的工单没有 claim 事件。因此 worker 间领取速度可比性受样本量影响：n 小的 worker 中位数噪声大，报告里 `n_claim_events` 与 n 并列展示，不做跨 worker 显著性断言。
2. **pickup 分布极端右偏**：中位 1.6 s、均值 1099 s、最大 183651 s。派发器在工单创建后毫秒级领单是常态，长尾来自「已建单但席别空/冻结」的滞留单。**均值不可用于画像排序**，本工具统一用中位+p90。
3. **在岗时长是墙上时间**，含排队等心跳、等外部依赖、等审批；它不是算力成本，更不等价于「努力程度」。7200 s 完成率只回答「预算窗口够不够」。
4. **仓名有别名污染**：`ship-performance-platform` 与 `ship-platform` 并存（同一领域仓的不同写法），本表按原值统计、未做归并，避免擅自改写事实；下游若需归并请在消费侧映射。
5. **模型名大小写/版本混杂**（`deepseek-v4-flash` 与 `DeepSeek-V4-Flash-0731` 并存），3.5 表按原值展示——这本身就是产能画像的一个发现（别名分裂会掩盖真实质量差异）。
6. **快照会漂移**：表是活的，报告内所有数字绑定 `generated_at` 与 mission_id 指纹；复算一律重跑 CLI，不要引用旧数字。
7. **不做因果**：本画像只报关联（如模型族与成功率的关联），不推断「换模型就一定提升」，那需要 A/B（后续波）。

## ⑥ 误差判定（关键量 vs 基准，逐项显式回答是否 <3%）

本任务无物理基准（不是 CFD/经验公式对标），基准取**独立实现路径的第二次计算**：画像侧的时长由 Python `datetime` 计算、群体统计由纯函数聚合；复算侧由一条独立 SQL 用 `extract(epoch …)` 与 `FILTER`/`avg` 重算同一批量。两条路径若口径一致，相对误差应只剩舍入残差。

| 指标 | 画像值 | 独立复算值 | 相对误差 | <3%? |
|---|---|---|---|---|
| `missions_total` | 4591.0 | 4591.0 | 0.00e+00 | 是 |
| `workers_total` | 62.0 | 62.0 | 0.00e+00 | 是 |
| `delivered` | 3792.0 | 3792.0 | 0.00e+00 | 是 |
| `settled` | 4551.0 | 4551.0 | 0.00e+00 | 是 |
| `pickup_n` | 2425.0 | 2425.0 | 0.00e+00 | 是 |
| `pickup_mean_s` | 1099.059 | 1099.059456 | 0.00e+00 | 是 |
| `wall_n` | 4546.0 | 4546.0 | 0.00e+00 | 是 |
| `within_7200` | 4293.0 | 4293.0 | 0.00e+00 | 是 |
| `success_rate` | 0.8332 | 0.833223 | 2.80e-05 | 是 |
| `completion_7200_rate` | 0.9443 | 0.944347 | 4.90e-05 | 是 |
| `n_missions[tp-qa-1]` | 157.0 | 157.0 | 0.00e+00 | 是 |
| `n_missions[tp-gen-1]` | 120.0 | 120.0 | 0.00e+00 | 是 |
| `n_missions[tp-qa-2]` | 118.0 | 118.0 | 0.00e+00 | 是 |
| `n_missions[tp-impl-1]` | 116.0 | 116.0 | 0.00e+00 | 是 |
| `n_missions[tp-impl-2]` | 112.0 | 112.0 | 0.00e+00 | 是 |
| `n_missions[tp-impl-3]` | 112.0 | 112.0 | 0.00e+00 | 是 |
| `n_missions[tp-impl-5]` | 111.0 | 111.0 | 0.00e+00 | 是 |
| `n_missions[tp-impl-6]` | 110.0 | 110.0 | 0.00e+00 | 是 |

最大相对误差 **4.90e-05**（completion_7200_rate），判据 `rel < 0.03`：**全部通过**。

另有两项非数值判据：

- **逐条可溯**：`--verify` 逐单比对 —— 检查 4584 单，不一致 0 项，结论 **通过**。
- **pytest**：`21 passed`（含 6 个负例：空 worker / 空归属 / 活跃数不足 / 空快照 fail-closed / 篡改检出 / mission 缺失检出）。

## ⑦ 判据对照（机器可验证项）

| 判据 | 要求 | 实测 | 结论 |
|---|---|---|---|
| 活跃 worker 画像数 | ≥3 | 55（另 7 个低于阈值仅登记） | 通过 |
| 与 mission 表逐条可溯 | 每单可核对、无漏单 | checked=4584，不一致=0 | 通过 |
| pytest 用例数 | ≥5 | 21 | 通过 |
| 含负例（空 worker 拒收） | ≥1 | 6 个负例（空 worker / 空归属 / 活跃数不足 / 空快照 / 篡改检出 / 漏单检出） | 通过 |
| 独立复算误差 | <3% | 最大 4.9e-05（18 项） | 通过 |
| 只读不干预 | 无调度改动、无 schema 变更 | 全部 SQL 为单条 SELECT；仅新增文件，未改任何既有文件 | 通过 |
| dry-run 模式 | 每工具具备 | portrait / crosscheck / figures / report 均支持 `--dry-run` 且不落盘 | 通过 |

复现命令：

```bash
python3 -m tools.worker_portrait --source psql --verify --out-dir artifacts/worker_portraits
python3 -m tools.worker_portrait.crosscheck
python3 -m tools.worker_portrait.figures
python3 -m tools.worker_portrait.report
python3 -m pytest tests/test_worker_portrait.py -q
```

## 结论与后续

- 交付 55 份 worker 画像（判据要求 ≥3 个活跃 worker，超出 52 个），每份含四项指标 + 逐单溯源表；索引 `artifacts/worker_portraits/index.md`。
- 平台 pooled：成功率 83.32%、7200 s 内完成率 94.43%、领取延迟中位 1.6 s。
- 后续波可直接消费 `report.json`：按 (成功率, 7200s 完成率, 领取 p90) 三轴做产能分层，并把「模型族别名分裂」与「领取长尾」列为两个独立靶子。本节点不改调度——按题设只观察。

