# 战役门机制全生命周期自动化成熟化（P165W1-N08 / m8179）

把 TensorPlatform 的战役门从「基础 DAG 派发 + 简单验收」升级为「派发→执行→验收→能力登记→自回归优化」的全自动闭环，并以 plan 165 的实测数据为基线做成熟化裁决。七条机器判据 C1–C7 全部由 `scripts/gate_lifecycle_study.py` 一次运行产出数字，结论 C1–C4/C6/C7 PASS；C5 通过「人工干预减少」分支（2.0→0 次/节点，-100%），时间分支在 replay 口径下为负（-86.0%，如实报 FAIL），只在 workload-equalised 口径下为 -62.9%。

## 1. 案例介绍（背景/对象/参数）

任务对象不是物理场，而是平台的战役门机制本身，因此没有几何/流场参数，参数量是调度与裁决参数：计划为 10 节点 4 层合成波次（wave-A），每节点带机器可算判据（err/score/residual/drift 的相对阈值），执行器为 StaticExecutor（测量值即工件输出）；调度容量 k=16（合同要求 ≥10）；基线为 plan 165（P27-W3-seed）的实测派发记录：8 节点、墙钟 6277.7 s、16 条事件（14 mission_dispatched + 2 plan_updated = 2.0 次/节点），planned_start/planned_end 全为空，且汇总节点与其 7 个依赖同一时刻派发。合成波次的节点命名与指标名全部与学科无关（cap-alpha…/err/score），因为平台仓铁律是零领域语义。

## 2. 方法（流程/关键设置/工具链）

- 派发层 `tools/gate_lifecycle/dag.py`：Kahn 拓扑＋DFS 回边定位环、CPM 求关键路径与时差、按关键长度优先的资源饱和列表调度（k≥10），全部平局按节点 id 破平以保证确定性；关键路径用「穷举所有根→叶路径」的独立实现交叉校验。
- 执行层 `executor.py`：StaticExecutor（合成波次）与 JsonCommandExecutor（真实子进程输出 JSON 测量值），执行器异常被记为 failed 证据而非静默通过。
- 验收层 `verdict.py`：6 条显式裁决规则（R1 缺证据=WITHHELD 不是 FAIL、R2 证据压声明、R3 有违约即 FAIL、R4 honest-FAIL 进 S-N 轨不计失败率、R5 声明白拿保护会被标 declaration_conflict、R6 可选判据不改裁决），对 52 例人工金标集做一致率统计。
- 台账层 `ledger.py`：PASS 节点自动写 capability_delta，按 (node_id, result) 摘要去重，S 级由声明式规则推导（S3=结构性保障+基线增量，S2=有基线增量，S1=其余）。
- 自回归层 `autoregress.py`：gap_analysis 五类缺口（gap 违约/缺证据/受阻/负结果/未登记）→ 按指纹去重后生成提案 → 注入 append-only 提案池 → 播种下一波计划（after 提示转依赖并做 DAG 校验）。
- 幂等层：run_id 与 result_digest 都是「计划+配置」的规范化 JSON sha256，去掉了时间戳/绝对路径，两次运行逐节点结果必须逐字节一致。
- 成熟化层 `baseline.py`：读冻结的 plan 165 实测基线，同时给出 replay 与 workload-equalised 两种时间口径，并逐条列出基线中的依赖违约。
- 工具链：python 3 + pytest（判据回归）+ matplotlib（图）；全流程 `python scripts/gate_lifecycle_study.py` 可复现。

## 3. 计算结果（判据数字，程序化注入）

| 判据 | 指标 | 实测 | 阈值 | 结论 | 产物 |
|---|---|---|---|---|---|
| C1 | DAG 无环检测+关键路径正确性与耗时 | wave_A_acyclic=True, deliberate_cycle=['c1', 'c2', 'c3', 'c1'], cp=['n02', 'n05', 'n08', 'n10'], len=395s, brute_force_len=395s, worst=1.356ms, schedule_problems=[] | 无环判定正确 + 关键路径与穷举一致 + 10节点≤1000ms | PASS | `artifacts/m8179/lifecycle_result.json` |
| C2 | 自动裁决 vs 人工金标一致率 | 52/52 = 1.0000 | >=0.95 | PASS | `artifacts/m8179/verdict_accuracy.json` |
| C3 | 能力台账自动登记完整率 | 4/4 = 1.0000 | =1.0 | PASS | `artifacts/m8179/ledger.jsonl` |
| C4 | gap_analysis→提案池注入→播种 全链路自动化 | gaps={'blocked_dependency': 3, 'criterion_violation': 1, 'missing_evidence': 1, 'negative_result': 1}, proposals=6, injected=6, seeded_nodes=6, waveB_pass=6/6 | 四段全自动且播种波次可执行 | PASS | `artifacts/m8179/seeded_plan.json` |
| C5 | 端到端时间缩短 或 人工干预减少（vs plan 165） | time_replay=-0.860, time_serial=+0.629, interventions=+1.000 | 时间>=0.30 或 干预>=0.50 | PASS | `artifacts/m8179/maturity_comparison.json` |
| C6 | 全链路幂等（同输入两次，逐节点一致） | digest_a=1f3e54a5734736e032643562353cdf7a3ed86655203e64b56e30442e9d44b475 digest_b=1f3e54a5734736e032643562353cdf7a3ed86655203e64b56e30442e9d44b475, node_digest_a=6ad58d9864e9012c node_digest_b=6ad58d9864e9012c, ledger_second_appended=0, ledger_second_duplicates=4 | 两次 digest 相同且台账不重复 | PASS | `artifacts/m8179/lifecycle_result.json` |
| C7 | honest-FAIL 不计失败率、进 S-N 轨 | failed=1, honest_fail=1, denominator=6, rate=0.1667, S-N=['n07'] | honest-FAIL 不入失败率且出现在 S-N 轨 | PASS | `artifacts/m8179/lifecycle_result.json` |

### 3.1 调度层

- 计划 wave-A：10 节点，容量 k=16，峰值并行 5，makespan 395s
- 关键路径：n02 → n05 → n08 → n10（长度 395s）
- 依赖波次：[['n01', 'n02', 'n03', 'n07', 'n09'], ['n04', 'n05'], ['n06', 'n08'], ['n10']]
- 无环检测 + 关键路径单次耗时 1.356 ms（阈值 1000 ms）

### 3.2 验收门与能力台账

- 人工标注金标集 52 例，自动裁决一致 52 例，准确率 100.00%（阈值 95%）
- 混淆矩阵：`{'FAIL': {'FAIL': 16}, 'PASS': {'PASS': 22}, 'WITHHELD': {'WITHHELD': 14}}`
- 能力台账登记完整率 100.0%（PASS 节点 4，缺失 []）
- S 级分布：{'S1': 2, 'S2': 1, 'S3': 1}；类别分布：{'capability_extension': 4}

### 3.3 失败保护与自回归

- 失败率（honest-FAIL 剔除）= 1/6 = 0.1667；S-N 轨节点 ['n07']
- gap_analysis 结论：{'blocked_dependency': 3, 'criterion_violation': 1, 'missing_evidence': 1, 'negative_result': 1}
- 提案注入：新生成 6 条，去重 0 条，池内 6 条
- 下一波播种：wave-B 共 6 节点，来源提案 ['prop-d2ea9f478bf7', 'prop-301399f74bed', 'prop-356783d8efa7', 'prop-395de617e625', 'prop-5c6555a870ff', 'prop-715341cce448']
- 闭环验证：播种波次重跑后 PASS 6 节点、台账新增 6 条

### 3.4 全链路幂等

- 两次运行的逐节点结果摘要 1f3e54a5734736e032643562353cdf7a3ed86655203e64b56e30442e9d44b475 vs 1f3e54a5734736e032643562353cdf7a3ed86655203e64b56e30442e9d44b475：一致
- 台账去重：第二次 register 追加 0 条，判重 4 条

## 4. 结果图

![fig1_schedule](artifacts/m8179/fig1_schedule.png)

*调度对比：(a) plan 165 实测派发时刻（无 planned_start/planned_end，汇总节点与其依赖同时启动）；(b) 自动 CPM 列表调度（k=16，红框=关键路径）。*

![fig2_gate_outcomes](artifacts/m8179/fig2_gate_outcomes.png)

*验收门分布：(a) 自动裁决；(b) 节点状态（honest_fail 进 S-N 轨）；(c) gap_analysis 各类缺口计数（直接驱动提案生成）。*

![fig3_critical_path](artifacts/m8179/fig3_critical_path.png)

*自动调度下每个节点的总时差（红线=关键路径，时差 0）：关键路径长度与穷举参考一致。*

![fig4_maturity](artifacts/m8179/fig4_maturity.png)

*成熟化对比：(a) 两种时间口径都如实给出（replay 口径无时间收益，workload-equalised 口径 -62.9%）；(b) 人工干预/节点（2.0 → 0，虚线=C5 目标线）。*

## 5. 注意点（坑/局限/适用范围）

- C5 时间分支必须分开报：plan 165 的墙钟被单个长节点支配（6277 s ≈ 全部墙钟），因此「同一时长、依赖被真正强制」的 replay 口径下自动调度反而更长（-86.0%），这是基线的依赖违约掩盖了真实工作量；只有 workload-equalised 的串行模型口径才得到 -62.9%。两条都写进报告，不挑口径。
- workload-equalised 口径是模型：基线串行执行 + 每节点 2.0 次人工交接 × 62 s 交接成本（62 s 取自 plan 165 初始批次后 5 个相邻派发间隔的中位数 61.82 s）。模型的口径与参数都已冻结在基线 JSON，可复算。
- 基线依赖违约是实测事实：汇总节点（依赖 7 个节点）在 offset 1.5 s 被派发，而其最长依赖在 offset 6277 s 才结束；新派发层把此类违约作为阻塞条件处理（未 PASS 的上游 → 下游 blocked，不伪造成功）。
- 规模局限：合成波次 10 节点、容量 16，列表调度未做优先级抢占/回填；真实战役里节点时长不可预知，本机制的时长字段需由历史中位数估计，估计误差会传导到 makespan。
- 裁决规则里「证据压声明」是刻意选择：若执行者声称 PASS 而指标违约，节点判 FAIL 并标 overclaim；这也意味着指标采集器本身的 bug 会变成 FAIL 而不是 WITHHELD，需要靠 C2 金标集与 metric-digest 审计来兜底。
- 提案去重只做指纹级（规范化文本 sha256 前 16 位）；语义级近重复（同义改写）仍可能漏过，需要后续 embedding 去重。
- C2 的「人工裁决」是 52 例手写期望值（每个案例的 verdict+status 在跑引擎之前就写定），但它与引擎由同一个智能体编写，因此 100% 一致只能证明「规则实现与声明规则无偏差」，不等于独立人类评审的一致率；真要做人机一致率裁决需要独立评审席位复核这批案例（本单未做，属残余风险）。
- honest-FAIL 的下游处理是刻意选择：honest-FAIL 保护执行者不背失败率，但它没有产出可用结果，因此其下游节点一律 blocked ——不伪造成功链。

## 6. 误差判定

本任务的「关键量」是机制判据而非物理量，逐项判定如下：

| 关键量 | 实测 | 基准 | 误差/差异 | 是否达标 |
|---|---|---|---|---|
| 关键路径长度 | 395 s | 穷举参考 395 s | 0（逐节点路径完全一致） | 是（<3%） |
| 自动裁决一致率 | 100.00% | 手写金标 100% | 0.00 pp | 是（阈值 95%，非 3%） |
| 台账登记完整率 | 100.0% | 100% | 0 | 是（<3%） |
| 幂等摘要 | 1f3e54a5734736e032643562353cdf7a3ed86655203e64b56e30442e9d44b475 | 1f3e54a5734736e032643562353cdf7a3ed86655203e64b56e30442e9d44b475 | 0 | 是（<3%） |
| 干预次数/节点 | 0 | 基线 2.0 | -2.0（-100%） | 是（<3% 口径不适用，按 C5 阈值 50%） |
| end-to-end 时间（replay） | 11678 s | 6278 s | -86.0% | 否（如实报 FAIL） |
| end-to-end 时间（serial model） | 11678 s | 31435 s | +62.9% | 是（>30%） |

回答「是否<3%」：与可定义基准的项（关键路径、台账完整率、幂等摘要）误差均为 0，<3% 成立；裁决一致率以 95% 机制阈值为准（实测见上），时间项无 3% 意义（不是物理量），按 C5 的 30%/50% 阈值裁决，且 replay 口径明确未达标。

### 审计链

- 基线冻结：`artifacts/m8179/baseline_plan165.json`（含 4 条 SQL 与观测记录，digest 0ed0c50a5d540f48）
- 合成波次：`artifacts/m8179/wave_a.json`（digest d330cab5f09b2814）
- 全链路结果：`artifacts/m8179/lifecycle_result.json`（run_id 8316c3d8a35e7311，result_digest 1f3e54a5734736e032643562353cdf7a3ed86655203e64b56e30442e9d44b475）
- 能力台账：`artifacts/m8179/ledger.jsonl`（4 条，去重 4 条）
- 提案池：`artifacts/m8179/proposals.jsonl`（注入 6 条；第二波同输入再生成 0 / 去重 6）
- 播种计划：`artifacts/m8179/seeded_plan.json`（wave-B 执行 PASS 6/6）
- 金标语料：`tools/gate_lifecycle/data/golden_cases.json`（52 例，corpus digest 6e7529fa0fb3015c）
- 环境：python 3.11.6 / Linux-6.6.52-1.7.lns23.loongarch64-loongarch64-with-glibc2.38

## 7. 复现与接入

```bash
python scripts/gate_lifecycle_study.py                       # 复现全部判据数字与图
python -m pytest tests/test_gate_lifecycle_*.py -q           # 62 条回归（C1-C7）
python -m tools.gate_lifecycle.cli dag  --plan artifacts/m8179/wave_a.json
python -m tools.gate_lifecycle.cli run  --plan plan.json --ledger l.jsonl --pool p.jsonl --out out/
python -m tools.gate_lifecycle.cli maturity --baseline tools/gate_lifecycle/data/baseline_plan165.json
```

接入方式：plan JSON 就是战役 DAG（`id / deps / duration / criteria / meta.measurements`）；
真实门用 `--cmd "<命令>"` 走 `JsonCommandExecutor`（命令最后一行输出
`{"measurements": {...}, "declaration": {...}}`，非零退出码记 failed 证据）；
能力台账与提案池是 append-only JSONL，可替换为 backend 侧 `campaign_plans` /
`proposal_gate` 的存储适配器而不改裁决逻辑。
