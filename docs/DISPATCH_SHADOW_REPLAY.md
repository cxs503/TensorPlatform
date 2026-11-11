# 派单决策日志 v1 · 确定性回放 · 影子策略回放门

> 模块：`tools/dispatch_shadow/`（平台共性机制，**零领域语义**）
> 工件：`artifacts/dispatch_shadow/{report.json,report.md,figures/,log_manifest.json,log_sample.jsonl}`
> 测试：`tests/test_dispatch_shadow.py`（35 用例）
> 复跑：`python -m tools.dispatch_shadow.cli --out artifacts/dispatch_shadow`
> 本文件只讲**设计与边界**；所有数字在 `report.json`，`report.md` 由 JSON 程序化生成（零手抄）。

## 1 问题

上一环（`p485w1-n10`，见 `docs/DISPATCH_EVOI_WALKFORWARD.md`）把派单排序做成了可离线评估的
EVOI 策略问题，但结论有一个结构性弱点：**评估不可回放**。判分脚本换一次迭代、RNG 换一个
种子、特征口径悄悄改一列，"新策略更好"的结论就无法被复核，也无法被证伪。本节点把
"每一次派单决策"变成可回放的事实，并给出一个能对"策略变好了"说不的门：

1. **决策日志 schema v1**：content-addressed 行（`record_id = sha256(body)`）、
   策略版本 `policy_sha256`（内含实现源码摘要）、派单时刻 feature 快照（带可用时间戳）、
   决策输出（score/ranking/chosen/propensity）、outcome **分区存放**（迟到标签不碰决策字节）；
2. **确定性回放引擎**：给定记录与钉住的策略版本，重算 score/ranking/propensity 并与日志
   **逐位**（IEEE-754 bit pattern）比对；
3. **影子策略评估**：用 IPS/SNIPS + 自助置信区间（回合重抽样，SHA-256 计数器 RNG 定种）
   估计候选策略在历史日志上的价值，ESS 判定正性；
4. **泄漏负控**：outcome 混进 feature 必须报警——名字级、时间戳级、统计级三层，并给出
   检出率/误报率曲线。

## 2 关键设计决定（含被否定的做法）

| 决定 | 为什么 | 位置 |
|---|---|---|
| 决策记录与 outcome **物理分区**，outcome 行只带 `record_id` 外键 | 标签迟到数小时是常态；合写会让"补标签"变成"改决策字节"，content address 失效 | `store.py` |
| `record_id` 只覆盖 body（不含 `prev_record_id`/`chain_digest`） | 身份=内容；链位置是日志元数据。两者混在一起时，任何插入都会改变历史行身份 | `schema.BODY_FIELDS` |
| 链摘要 `sha256(prev_chain || record_id)` | 与"链上 id 拼接"不同：只改一行会同时破坏本行摘要与下一行 prev，删除/重复/重寻址都留下不同指纹 | `store.verify` |
| 行序以 `decision_seq` 为准，**分区文件内的物理顺序无语义** | 分片存储必然打乱写入序；若"换序"也算篡改，就得为磁盘顺序付审计成本。代价是：换序不可检（E2 记录了这点） | `store._record_rows` |
| 策略版本 = 语义 spec + **实现源码摘要** `kernel_sha256` | 只钉 spec 挡不住"改了 kernel 但沿用版本号"；源码摘要把 replay 变成真的"同一算法" | `policy.kernel_digest` |
| 所有求和用 `math.fsum`，排序 tie-break 用 `(-score, candidate_id)` | `sum()` 顺序敏感、`max` 平票取首元素，都会让"重放一致"变成环境巧合 | `policy.linear_score` |
| score 用**位串**比对（`struct.pack(">d")`），不用 `==` | `0.0 == -0.0` 为真但位不同；1-ulp 差异 `==` 为假但排序常常不变——两种方向都会漏检 | `canonical.bit_equal` |
| 回放**不要求** `chosen == argmax`，只要求 chosen 被策略律支持（`p ≥ ε/n`） | 线上派单是从探索律抽样；把抽样差异当回放失败会把真实日志全判红。argmax 与实派偏离比例单独汇报 | `replay.replay_record` |
| 重要性权重用**候选策略的随机律**（softmax+探索底），不是确定性 top-1 | 确定性目标策略下 IPS 只有少数回合有权重，ESS 崩到 <30%；探索底同时提供 positivity | `policy.softmax_propensities` |
| 自助重抽样用 SHA-256 计数器模式 RNG，种子由 `record_id` 列表摘要派生 | `numpy.random`/`random` 的流不是契约；区间要能从日志复现，种子必须落盘且可重算 | `offpolicy.bootstrap_ci` |
| 泄漏三层：**名字**（配置注入的词表）/ **时间戳**（`field_ts > dispatch_ts`）/ **统计**（单变量 AUC≥0.95） | 名字检查抓不住"名字合法但当事后刷新"，时间戳检查抓不住"名字与时间都合法但值就是标签" | `leakage.py` |
| 统计泄漏检测只用 **单变量 AUC**，不做多变量漂移检测 | 每回合候选少、样本几百，多变量检测器在噪声上频繁报警，最终会被所有人忽略；AUC≥0.95 的语义是"这列几乎就是标签" | `leakage.LeakageDetector` |
| 域无关：候选/特征/outcome 名全部由注入 `LogConfig` 提供 | 平台铁律=零领域语义；域内容经 API/配置注入 | `schema.LogConfig` |

**被否定/否决的做法（记录在案）**

- *把 outcome 直接写在决策行里、用"状态列"表示是否已评分*：需要就地改历史行，链必断；
  且"未评分"与"拒绝"在语义上混淆 ⇒ 分区存放。
- *回放只比 `chosen`/`ranking`*：1-ulp 分数改动在多数回合不改变排序（E2 里 1024-ulp 才改变
  名次），只比排序等于对数值扰动失明 ⇒ 全列位串比对。
- *用 `==` 比分数*：`-0.0` 与 `1-ulp` 两侧都漏 ⇒ 位串。
- *用 `random.Random(0)` 做自助*：跨 Python 版本不保证同流，报告里的种子无法复现区间 ⇒
  计数器模式 SHA-256。
- *把"换序"当篡改形状*：见上，物理顺序无语义 ⇒ 换成"重寻址"（改分并重算 `record_id`）
  与"重复行"两种真正可检的形状。
- *首次尝试教训*：本 worktree 内没有上一轮（第 1 次）任务的报告可读（`artifacts/`、`docs/`
  与报告服务 100 条最近记录均无 `p495w4-n01` 条目）。因此把相邻 EVOI 节点的踩坑直接继承下来
  （见 §9），而不是"原样重跑"：特别是**时间戳级泄漏守卫**、**探索底保证 positivity**、
  **多次重复的零假设负控**、以及**把"同种子可复现"与"换数据集波动"分开汇报**。

## 3 schema v1 布局与不变量

```
<root>/manifest.json            # counts + head digests + config digest
<root>/config.json              # 注入的 LogConfig（feature/outcome 名、候选 id 字段）
<root>/records/part-<k>.jsonl   # 决策分区（k = decision_seq % parts）
<root>/outcomes/part-<k>.jsonl  # outcome 分区（k = outcome_index % parts）
```

决策 body：`schema_version, log_id, decision_seq, dispatch_ts, policy{policy_id,
policy_sha256, params}, config_sha256, slate{candidate_id_field, candidates[{id, features,
field_ts}]}, decision{scores, ranking, chosen, propensities, exploration_floor}, writer`；
根上再加 `record_id, prev_record_id, chain_digest`。outcome 行：
`schema_version, outcome_index, record_id, outcome_ts, source, outcome{...}, outcome_id,
prev_outcome_id, chain_digest`。

不变量（全部有测试）：

1. 未注册特征列 → **拒绝写**（不是忽略）：多一列要么是笔误丢信号，要么是泄漏加信号；
2. 特征缺 `field_ts` 或 `field_ts > dispatch_ts` → `LeakageError`；
3. propensity 严格正且归一（容差 `propensity_tolerance`）；
4. `record_id` 可复算（canonical JSON：`sort_keys`、无空格、禁 NaN/Inf）；
5. 链可复算，四种篡改形状各有不同问题码；
6. outcome 不得指向不存在的 `record_id`（`ORPHAN_OUTCOME`）。

## 4 "确定性回放"到底声称什么

- **声称**：同一记录 + 同一钉住版本 ⇒ score/propensity 的 IEEE-754 位串、ranking 列表一致；
  过程不读时钟、不读进程 RNG、不读文件系统状态；
- **不声称**：跨平台 libm 的 `exp()` 末位一致（softmax 分母）。可控的是操作数顺序与舍入；
  超验函数末位是平台属性。因此 propensity 在本机做位比对，跨机以区间一致性判定，并在
  `report.json.limitations` 里如实写明。
- **拒绝**：策略版本未注册（`POLICY_VERSION_MISMATCH`）、spec 复算不出记录里的 sha256、
  kernel 源码摘要变化（`UnpinnedPolicyError`）——**拒绝回放比回放出"差不多"的结果更安全**。

## 5 影子评估与门的判据

估计量：`w_t = π₁(a_t|s_t)/π₀(a_t|s_t)`，`IPS = mean(w r)`，`SNIPS = Σw r / Σw`，
`ESS = (Σw)²/Σw²`；`naive = mean(r)` 即现状策略（记录策略）的自我评估。
区间：回合级 percentile bootstrap（B、α、种子全部落盘）。

门（`shadow.evaluate_shadow_gate`）五项 + 一项确定性：

| id | 判据 | 失败时挡住的事故 |
|---|---|---|
| A | schema 校验 + 链校验通过 | 手改/删行/重寻址的日志 |
| B | 回放逐位保真 1.0 且 1-ulp 扰动 100% 检出 | "重放"其实是重跑另一份代码 |
| C | 干净日志 0 报警、注入 outcome 必报警、纯噪声不报警 | 把标签偷渡进特征 |
| D | SNIPS−Π₀ 的自助 CI 下界 > 0 | 用自选择偏差冒充"策略变好" |
| E | ESS/n ≥ 0.10 | 正性崩塌（权重集中在少数回合） |
| F | 两次完整评估字节一致 | 宿主 RNG/字典序/时钟泄漏进结论 |

`WITHHELD` 是一等结果：无标签回合或 ESS 不足时拒绝回答，而不是给一个弱的 PASS。

## 6 泄漏的三层守卫与负控

- **L1 名字**：`LogConfig.forbidden_feature_substrings` + outcome 字段名；命中即拒写/报警；
- **L2 时间戳**：每个特征带 `field_ts`，"名字合法但当事后刷新"被抓；
- **L3 统计**：逐特征对 outcome 的单变量 AUC，阈值 0.95；
- **负控**：把 outcome 按概率 p 混入一个**已注册**的特征（绕过 L1/L2），p 从 0.5（纯噪声）
  扫到 1.0（标签副本），给出 AUC 曲线与报警/静默判定；另用 100 次标签置换估误报率。

## 7 结果指针

见 `artifacts/dispatch_shadow/report.{json,md}`：判据 A–I 的观测值/阈值/证据路径、
E2 回放灵敏度、E3 统计功效（门在 N=200/400 时诚实 FAIL，N≥800 才 PASS）、
E4 泄漏检出曲线、E5 探索下限 × ESS/区间宽度/误差。图：`figures/fig1..fig5.png`。

## 8 局限（不粉饰）

1. 日志是**合成世界**：策略价值可枚举，因此能给出真实的相对误差；迁移到生产日志属
   **WITHHELD**（生产可能有夹具里不存在的隐藏混淆）。
2. IPS/SNIPS 只对"日志出现过的 slate 分布"无偏；从未出现的 slate 不在估计量内。
3. 跨平台 `exp()` 末位不保证一致（§4）。
4. 统计泄漏检测是单变量阈值法：少数回合泄漏（p≲0.8）会落在阈值下（E4 曲线如实给出）。
5. 本节点的战役 metadata 在工作树内不可机读，判据 A–I 系按 goal 文本自定并全部落 JSON。

## 9 知识蒸馏（可复用规则卡）

- **Title / 规则**：任何"策略更好"的离线结论，必须能从一个 content-addressed、带链的
  决策日志上**逐位重放**并给出**带区间的对照**；否则结论只有叙事价值。
- **适用范围**：平台内一切"排序/派单/选择"类策略评估（EVOI、triage、优先级、调度）；
  **不适用**：单次执行的数值求解器验证（那是收敛性/守恒律问题，不是策略问题）。
- **证据与出处**：本节点 `artifacts/dispatch_shadow/report.json`（`environment.git_commit` /
  `module_sha256` / `manifest.head_*`）+ `tests/test_dispatch_shadow.py`；
  相邻节点教训出处 `docs/DISPATCH_EVOI_WALKFORWARD.md` §2。
- **手顺**：①先定日志 schema 与不变量（未注册列拒绝）→ ②写 store + 链校验 + 篡改形状测试
  → ③写 kernel 源码摘要与版本注册 → ④回放逐位比对 + k-ulp 灵敏度探针 → ⑤off-policy 估计
  + 落盘种子自助 → ⑥三层泄漏守卫 + 负控曲线 → ⑦门判据表 + honest FAIL 路径。
- **反例/失效条件**：日志行内物理顺序被当作语义（则换序成假警报）；用确定性 top-1 做 IPS
  （ESS 崩塌）；只比 ranking（对 1-ulp 失明）；把探索抽样当回放失败（真实日志全红）。
- **回归护栏**：`test_replay_detects_one_ulp_perturbation`、
  `test_replay_refuses_changed_kernel`、`test_chain...`/`test_deleted_record_is_detected`、
  `test_statistical_detector_alarms_on_smuggled_outcome_and_silent_on_noise`、
  `test_module_is_domain_free`（领域词表全词匹配）——全部进 pytest，改坏即红。
- **评审状态**：本文件随分支提交，待平台独立席位按 S×E 复判；**last-verified**：见
  `report.json.environment.git_commit` 与 `generated_at_utc`。