# 百人锦标赛编排器（IN25-T1）：数量 → 质变的引擎件

> 领域无关的锦标赛编排器：输入（问题规格 / 机器打分器 / 席位数 / 代数）→ 自动生成氏族席位
> mission（多样性清单注入 goal）→ 收工件 → 物理锚打分 → 排名淘汰 → 杂交重组 → 冠军或全灭裁决。
> 本文件所有数字由 `tools/tournament/make_artifacts.py` 从报告 JSON 程序化写入，无手抄。
> 复现：`python3 tools/tournament/make_artifacts.py --out-dir artifacts/tournament`。

## 1. 案例介绍（受控干跑问题）

编排器本身**不做物理**：它只认 `ProblemSpec`、注入的 `scorer`（必须是参考/实验锚定）与席位基因组。
为了让这条链路在没有外部依赖时被真实跑通并被真实测量，本报告用一个解析可解的单自由度阻尼
自由振动作为地面测试台：

- 方程 `x'' + 2ζωx' + ω²x = 0`，`x(0)=x0, ẋ(0)=0`，
  解析解 `x(t)=x0·e^{-ζωt}[cos(ω_d t)+(ζω/ω_d)sin(ω_d t)]`，`ω_d=ω√(1-ζ²)`；
- 参考参数 `ζ=0.08`、`ω=2.4 rad/s`、`x0=0.05 m`，窗口 `t∈[0,12] s`，`Δt=0.05 s`（241 点）；
- 两个**物理锚**（打分器独立计算，席位无法改写）：
  `rel_error` = 与解析解的相对 L2 误差（通过门 ≤ 3.0%）；
  `decay_consistency_residual` = 对数减量自洽残差 `|λ̂+2ζ̂ω̂|/|2ζ̂ω̂|`（门 ≤ 2.0%），
  只考核「曲线是否遵守自己宣称的耗散律」，对参数偏差不可见、对数值格式的非物理耗散敏感；
- 三个演练场景（同一编排器，只改门限/代数）：

| 场景 | 裁决 | PASS 所在代 | 跑了几代 | 实播席位 | 未用席位 | 冠军 rel_error | S-N 记录 | 审计事件 | 链有效 |
|---|---|---|---|---|---|---|---|---|---|
| three_generations | champion | 3 | 3 | 15 | 5 | 0.654% | 15 | 101 | True |
| pass_early | champion | 1 | 1 | 5 | 15 | 4.286% | 5 | 33 | True |
| all_eliminated | all_eliminated | — | 3 | 15 | 5 | — | 15 | 100 | True |

## 2. 怎么做的（方法 / 流程 / 工具链）

管线（`tools/tournament/orchestrator.py`，纯标准库、零网络）：

1. **生成席位**：从 `families × frameworks × blind_prediction × physics_anchor` 的笛卡尔积里做
   确定性抽样，逐候选过反同族查重（同代 `(model_family, framework)` 唯一 + 相似度上限
   0.8），把四轴多样性清单写进 mission 的 `goal`，落盘
   `runs/<scenario>/missions/G0x/<seat>.json`（可直接 POST 到任务池的形态）。
2. **收集工件**：`executor(mission) -> artifact` 写出后**从磁盘读回**再打分，sha256 入审计。
3. **物理锚打分**：`scorer(mission, artifact) -> {score, passed, anchor_ok, metrics}`。
   `anchor_ok=False` 一票否决；席位自报分数（`self_reported_score`）一律丢弃并留痕。
4. **排名淘汰**：`(score desc, seat_id)` 定序，前 `max(min_promote, ⌈n·promote_ratio⌉)` 晋级。
5. **门禁**：本代任一座位 PASS（精度门 ∧ 锚门 ∧ 打分器判定）→ 停播后续代，缺口记未花预算。
6. **杂交重组**：晋级席位的基因组交叉（胜者 `model_family` × 次席 `framework`），父代**实测量**
   随 mission 回流（`parent_estimates`），子代在赢家的数上继续做工；每代再补 ≥2 席新族
   （没有新血，锦标赛必然退化为近亲繁殖）。
7. **裁决**：冠军（带可回溯谱系）或全灭（honest FAIL，不粉饰）。全席产物入 S-N 负知识轨。

工具链：Python 3.11 标准库 + matplotlib（仅出图）+ pytest（23 项验收）。

### 三条锦标赛基因如何落成机器门

| 基因 | 机器门 | 反例会怎么死 |
|---|---|---|
| 多样性 | 同代同族键唯一 + 相似度 0.8 上限 + 每代 ≥2 族 | 同族堆人拿不到新信息，G1 就重复投放同一族 |
| 物理锚淘汰 | 打分器 `scorer_basis ∈ {reference, experiment}`（构造期硬门），`anchor_ok=False` 一票否决，自报分数丢弃 | 模型自评选自己第一 → 构造期直接 ValueError |
| 赢家杂交回流 | 基因组交叉 + `parent_estimates` 实测量回流 + 谱系入报告 | 只筛不交配 → 每代原地重抽，永远拿不到异号偏差相消 |

## 3. 计算结果（三代链路逐席明细）

| 代 | 名次 | 席位 | 模型族 | 框架 | 来源 | score% | rel_error | 锚残差 | 结果 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | `T01-S04-019a6b91` | analytic-inversion | rk4 | fresh | 95.71 | 4.286% | 0.29% | promoted |
| 1 | 2 | `T01-S01-7012ed0b` | nn-surrogate | closed-form | fresh | 95.49 | 4.508% | 0.31% | promoted |
| 1 | 3 | `T01-S02-9ccfeeb2` | analytic-inversion | symplectic-euler | fresh | 95.43 | 4.575% | 0.38% | promoted |
| 1 | 4 | `T01-S03-8be549f4` | gp-regression | symplectic-euler | fresh | 94.85 | 5.153% | 0.39% | eliminated |
| 1 | 5 | `T01-S05-04f39175` | mimetic-operator | rk4 | fresh | 94.12 | 5.879% | 0.32% | eliminated |
| 2 | 1 | `T02-S06-7dce9368` | analytic-inversion | closed-form | hybrid | 95.70 | 4.295% | 0.30% | promoted |
| 2 | 2 | `T02-S07-8796f0a2` | nn-surrogate | rk4 | hybrid | 95.70 | 4.295% | 0.30% | promoted |
| 2 | 3 | `T02-S10-fd0c3372` | gp-regression | rk4 | fresh | 95.03 | 4.965% | 0.29% | promoted |
| 2 | 4 | `T02-S08-7abfa55b` | analytic-inversion | symplectic-euler | hybrid | 94.98 | 5.022% | 0.46% | eliminated |
| 2 | 5 | `T02-S09-fa5ddb06` | pinn-residual | rk4 | fresh | 94.84 | 5.161% | 0.28% | eliminated |
| 3 | 1 | `T03-S13-7bd91846` | gp-regression | closed-form | hybrid | 99.35 | 0.654% | 0.30% | PASS |
| 3 | 2 | `T03-S14-0f7662cc` | poly-fit | forward-euler | fresh | 96.72 | 3.277% | 12.98% | promoted |
| 3 | 3 | `T03-S12-3ff033f3` | nn-surrogate | closed-form | hybrid | 95.70 | 4.295% | 0.30% | promoted |
| 3 | 4 | `T03-S11-e830b0c6` | analytic-inversion | rk4 | hybrid | 95.70 | 4.295% | 0.30% | promoted |
| 3 | 5 | `T03-S15-c89ee72a` | gp-regression | forward-euler | fresh | 95.06 | 4.939% | 13.51% | promoted |

冠军谱系（父代 → 子代的接力，跨三代）：

| 代 | 席位 | 来源 | 模型族 | 框架 | score% |
|---|---|---|---|---|---|
| 3 | `T03-S13-7bd91846` | hybrid | gp-regression | closed-form | 99.35 |
| 2 | `T02-S06-7dce9368` | hybrid | analytic-inversion | closed-form | 95.70 |
| 1 | `T01-S04-019a6b91` | fresh | analytic-inversion | rk4 | 95.71 |
| 1 | `T01-S01-7012ed0b` | fresh | nn-surrogate | closed-form | 95.49 |
| 2 | `T02-S10-fd0c3372` | fresh | gp-regression | rk4 | 95.03 |

### 跨 seed 稳健性（编排器不依赖某一个 seed）

对 40 个 seed 各跑一次同一问题（门限 0.97）：

| 结局 | seed 数 |
|---|---|
| all_eliminated | 7 |
| pass_G2 | 30 |
| pass_G3 | 3 |

全灭的 seed：[9, 19, 22, 23, 31, 35, 36]。所有 run 的审计链均有效、
席位均未超硬顶、负知识轨覆盖全席。主线场景用 seed=12 只是**让三代链路被走满**
的受控环境参数（冠军多数在 G2 出现），编排器代码里没有任何与 seed 相关的分支。

## 4. 结果图

- `fig1_scores_by_generation.png`：三场景每席得分与门限（绿=通过）。
- `fig2_champion_vs_reference.png`：冠军/落选席位预测 vs 解析解 + 逐点绝对误差（对数）。
- `fig3_diversity_and_dedup.png`：族×代数得分分布（○新族 / △杂交变异）+ 每代族/框架覆盖与查重拒绝数。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **反同族查重的尺度**：早期实现把 `(model_family, framework)` 的唯一性强制到**整轮**，
   结果 8 族 × 4 框架的键空间被 15 席耗尽，第 3 代无法再交叉（后代基因必撞历史键）——
   锦标赛退化成「变异重播」。修正：整轮唯一只约束**新席位**，杂交后代只需与同代兄弟不同、
   不等于亲本基因组；复用历史键时写 `recombination_reused_signature` 审计事件。
2. **能量投影不一定帮忙**：最初让「承诺能量单调」的席位对轨迹做运行时能量投影，实测把衰减
   包络钉在 `E0`（过阻尼假象），误差从 5% 涨到 50%。已删除该做法，改为「承诺耗散律 ⇒ 步长
   细分」（约束驱动离散化），并用对数减量自洽残差机验。
3. **锚不能替代外部参考**：`decay_consistency_residual` 拿席位**自己申报**的 `ζ̂, ω̂` 当基准，
   一个足够聪明的席位可以把申报值反向拟合自己的曲线来蒙混过关。锚只拦「结构性违约」
   （数值格式违背自己宣称的物理律）；真正的判据必须是与解析解/实验比对的 `rel_error`。
   两者互补，缺一不可——报告里如实写明，不夸大成「物理锚免疫一切」。
4. **干跑的偏差表是设计参数**：`FAMILY_BIAS` 里「单族 |Δζ/ζ| ≥ 6.2% 故单族必不达标」是
   **受控实验设定**（为了让「集成抵消」这一机制被检验），不是物理定律。编排器不读取该表，
   换任一打分器即可复用到真实领域；但**不能**把这里的数字当作船舶/任何物理量结论。
5. **全灭是合法结局**：门限不可达时编排器不硬凑冠军（`all_eliminated`），全席入负知识轨。
   这比「选一个最好的凑数」更有用：它告诉下一轮别再原样投放这批同族组合。

## 6. 误差判定（关键量 vs 基准，逐项回答「是否 < 3%」）

| 量 | 实测 | 门限 | 占门限 | < 3%？ |
|---|---|---|---|---|
| 冠军响应相对 L2 误差 rel_error | 0.6541% | ≤ 3.0% | 0.218× | 是 |
| 冠军耗散律自洽残差 | 0.2956% | ≤ 2.0% | 0.148× | 是 |
| 最佳落选席位 rel_error（离门限最近的失败者） | 3.277% | > 3.0%（未过门） | — | 否 |

结论：冠军 `rel_error = 0.6541%`，
相对解析参考解**小于 3%（且小于 1%）**，耗散律自洽残差
0.2956% 也在门内。
需要强调：这个 3% 是**受控干跑装置**里冠军与解析解的一致性，证明的是「编排器能把达标席位选出来
并验证它」，**不是**对任何船舶物理量的精度声明。

## 7. 机器验收对表

| 验收项 | 判据 | 实测 |
|---|---|---|
| 干跑演练走满三代（G1/G2 无 PASS，G3 出冠军） | pass_generation == 3 | pass_generation=3，generations_run=3 |
| PASS 早停（后续席位不播 + 预算回收） | stopped_early 且 seats_unspent > 0 | True / 省 10 席 |
| 早停反向负例（差 0.01 不得误触发） | test_no_early_stop_when_score_just_below_gate | tests/test_tournament_orchestrator.py 通过 |
| 席位带多样性字段 | diversity_fields_in_every_goal | True |
| 同代反同族查重 | family_signature_uniqueness_per_generation | True（查重拒绝 9 次） |
| 总席位硬顶 | seats_spawned <= 20 | 15 <= 20（True） |
| 审计全留痕 | audit_chain_valid | 101 事件，链有效=True |
| 全席入 S-N 负知识轨 | negative_knowledge_count == seats_spawned | 15 == 15 |
| 测试 | pytest >= 10 | 23 passed（tests/test_tournament_orchestrator.py） |

## 8. 局限与未完成

- 编排器只管**编排**：不产生科学假设、不解释物理。席位 mission 的内容质量取决于上游提案质量。
- 打分器必须由调用方注入且为参考/实验锚定；若上游只能提供模型自评，本编排器会拒绝运行
  （这是设计意图，不是缺陷）。
- 未接入真实任务池：`missions/*.json` 是可直接 POST 的形态，但本单没有向任务池派发（干跑纪律）。
- 杂交只用「交叉 + 实测量等权回流」，未做多父代、加权堆叠、种群多样性维护（NSGA 类）。
  这些是下一代引擎件的自然扩展，不在本单范围。
