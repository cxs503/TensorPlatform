# IN25-T2 · 多样性引擎：氏族矩阵 / 盲预测锁 / 同族查重

> 战役节点 **IN25-T2**（锦标赛军 T2，仓 TensorPlatform）。全部数字由
> `tools/diversity/gen_doc.py` 从 `artifacts/in25_t2/` 程序化注入，无一手抄；
> 工件快照 sha256（前 16 位）：coverage `fddd78dbd6e61b17` · seats
> `d8ee5d316df9aa40` · prereg_lock `cc97b74b46f65d44` · calibration
> `694b8fe358e207c8` · dedup_ledger `9ece8a74de549404` ·
> blind `0439b915d213c62f`。
> 代码：`tools/diversity/{roster,prereg,dedup,run_node}.py`；测试：`tests/test_diversity_engine.py`。

## ① 案例介绍（问题 / 输入 / 参数）

**问题**：把同一目标派给 N 个席位，若它们共用同一模型、同一表述、同一先验、同一角色，
N 次尝试在统计上近似 1 次尝试的重复抽样——"一百颗同骰子"。预算被消费，失败空间却没被展开，
锦标赛的"数量→质变"机制退化成排队重试。本节点交付的不是"多派几个"，而是**四条轴同时展开
且每题可机裁**的席位发生器，外加两道防作弊门（盲预测、同族查重）。

| 项 | 值 |
|---|---|
| 目标（域无关） | 为目标量 y*(θ) 设计一条可复算的求解/估计路径，并给出可被下一代检验的预期收益（目标文本本身与学科无关：θ 是待定参数，y* 是唯一靶值） |
| 模型族（现场对账） | deepseek, qwen, minimax, glm（要求 ≥3） |
| 框架变体（攻击角） | direct, inverse, limits, failure, orthogonal（要求 ≥4） |
| 对抗角色 | attacker（攻坚者）/ breaker（破坏者） |
| 席位数（族×框架×角色） | **40** = 4×5×2 |
| 矩阵格数 | 20（每格 2 席：一攻一破） |
| 生成时刻（UTC） | 2026-11-07T07:49:03+00:00 |

模型族的"存在性"由现场 `/v1/models` 实测决定，不看配置文本（历史判例：配置曾写过从未存在的
模型名）。本轮实测：

| 族 | 现场可用模型 |
|---|---|
| deepseek | ✅ deepseek-v4-flash, DeepSeek-V4-Flash-0731 |
| glm | ✅ glm-5.2, glm-5.1 |
| minimax | ✅ MiniMax-M2.5-AWQ |
| qwen | ✅ qwen3.7-plus, qwen3.6-plus, Qwen3.6-35B-A3B, Qwen3.8-27B |

## ② 怎么做的（方法 / 流程 / 关键设置）

1. **氏族矩阵**（`roster.py`）：`build_seats()` 取 族×框架×角色 的**完整乘积**，构造完立即
   `coverage_report()` 硬判满格；任一族/框架拼写漂移、族数 <3、框架 <4、角色缺一，一律
   `RosterError` 抛出（不静默降级——降级的矩阵仍会"看起来有席位"）。
2. **框架变体不是换说法，是换证据面**：五种攻击角各带 `deliverable` 与 `stress_axis`——
   正向构造（流程完整性）/ 反演（可辨识性）/ 极限与界（渐近）/ 失效面（最坏情形）/
   换基（表示与良态性）。同一目标由此得到 5 种不同表述，且要求交出的证据互不重叠。
3. **先验种子**：`seed = sha256("IN25-T2|族|框架|角色|prior-seed-v1")[:8]`，截到 63 位。
   判据是**比特级**的：两两汉明距离（不是"数值不等"——低位同构仍会退化成同一骰子）。
4. **盲预测锁**（`prereg.py`）：三条时序硬门——①结果已存在则拒锁（先跑后锁=抄）；
   ②锁内 `predictions_sha256` + `lock_hash` 双哈希，任一处改动即验伪并指出改的是哪个字段；
   ③计分要求每条结果 `recorded_utc > locked_utc`，时间倒挂即拒评。另加两条多样性约束：
   预测必须覆盖全部席位（缺格即拒锁）、不同族逐字相同的预测在锁阶段就拒（同骰子指纹）。
5. **同族查重**（`dedup.py`）：三层表征 —— 表层令牌（拉丁词 + CJK 二元组 + 数字指纹）、
   **骨架**（近义词归一 + 裸数字抹平后的令牌集合，抹平"换词不换招"）、**人口约束**
   （任一族占已收方案 ≥60% 即拒新来者）。
   判定多路或门：骨架 ≥0.72 / 包含度 ≥0.85 /
   表层 ≥0.8 / 同族骨架 ≥0.45 /
   数字集合全同且骨架 ≥0.5（只调参的克隆）。
6. **fail-closed 复用**：门的"看不清"分支一律拒（摘要 <40 字、缺交付物、缺 clade、
   令牌为空），与 `ops/taskplane/campaign_dispatcher.py::_hollow()` 同一打法——
   台账查不了就不放行。门是机器门，下游无法用一句"这是新方案"绕过。

## ③ 计算结果（判据数字）

| 判据（战役 metadata） | 实测 | 阈值 | 通过 | 工件 |
|---|---|---|---|---|
| 氏族覆盖矩阵全格有席位 | 20/20 格 = 100%，每格 2 席 | 1.00 | ✅ | `artifacts/in25_t2/coverage.json` |
| 模型族数（现场） | 4 族 | ≥3 | ✅ | `artifacts/in25_t2/roster_check.json` |
| 框架变体数 | 5 种 | ≥4 | ✅ | `artifacts/in25_t2/seats.json` |
| 角色齐备 | attacker 20 / breaker 20 | 两者 >0 | ✅ | `artifacts/in25_t2/coverage.json` |
| 先验种子差异 | 40/40 各异；汉明 min/中位/max = 21/31/42；碰撞 0 | 碰撞 0 且 min ≥16 bit | ✅ | `artifacts/in25_t2/seed_divergence.json` |
| 盲预测锁定工件 | lock_hash `1b96a171b6000fb9…`，40 条预测，复算 ✅，篡改检出 ✅，先跑后锁被拒 ✅ | 三项全真 | ✅ | `artifacts/in25_t2/prereg_lock.json` |
| 克隆方案 MUST 拒 | 语料 TPR 1.00 / FPR 0.00；端到端 24/24 拒；fail-closed 负例 3/3 拒 | TPR=1.00 且端到端 100% | ✅ | `artifacts/in25_t2/dedup_ledger.json` |
| pytest | **22 passed**（`tests/test_diversity_engine.py`） | ≥8 | ✅ | `artifacts/in25_t2/pytest_summary.json` |

**氏族覆盖矩阵**（每格 `攻/破` 席位数）：

| 族 \ 框架 | direct | inverse | limits | failure | orthogonal |
|---|---|---|---|---|---|
| deepseek | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| qwen | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| minimax | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| glm | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |

边际：族各 10 席、框架各 8 席，
格内席位 2–2，无缺格、无缺角色格。

**相似度门分离度**（标注语料 24 个克隆对 / 15 个独立对，
独立性由语料构造决定=同方法改写 vs 不同方法）：

| 量 | 值 |
|---|---|
| 克隆对骨架相似度 min / 中位 | 0.6308 / 0.9123 |
| 独立对骨架相似度 max | 0.0412 |
| 独立且同族的骨架相似度 max | 0.0111 |
| 判定阈值 | 0.72 |

各机制独立命中数（克隆对 n=24）：

| 机制 | 命中 |
|---|---|
| CLONE_CONTAINMENT | 24 |
| CLONE_LEXICAL | 15 |
| CLONE_SKELETON | 18 |
| RETUNE_ONLY | 9 |
| SAME_CLADE_REPEAT | 24 |

逐条门账本（前 24 条，按提交顺序；`-c1…-c4` 分别是逐字 / 改写 / 只调参 / 嵌长文克隆）：

| 候选 | 真值 | 判定 | 理由 |
|---|---|---|---|
| `M1` | independent | 收 | — |
| `M1-c1` | clone:verbatim | **拒** | CLONE_SKELETON(1.00>=0.72); CLONE_CONTAINMENT(1.00>=0.85); CLONE_LEXICAL(1.00>=0.8); SAME_CLADE_REPEAT(1.00>=0.45); RETUNE_ONLY(numbers=1.0,skeleton=1.00) |
| `M1-c2` | clone:paraphrase | **拒** | CLONE_SKELETON(0.91>=0.72); CLONE_CONTAINMENT(0.88>=0.85); SAME_CLADE_REPEAT(0.91>=0.45); RETUNE_ONLY(numbers=1.0,skeleton=0.91) |
| `M1-c3` | clone:retune | **拒** | CLONE_SKELETON(0.86>=0.72); CLONE_CONTAINMENT(0.91>=0.85); CLONE_LEXICAL(0.84>=0.8); SAME_CLADE_REPEAT(0.86>=0.45) |
| `M1-c4` | clone:embedded | **拒** | CLONE_CONTAINMENT(1.00>=0.85); SAME_CLADE_REPEAT(0.69>=0.45); RETUNE_ONLY(numbers=1.0,skeleton=0.69) |
| `M2` | independent | 收 | — |
| `M2-c1` | clone:verbatim | **拒** | CLONE_SKELETON(1.00>=0.72); CLONE_CONTAINMENT(1.00>=0.85); CLONE_LEXICAL(1.00>=0.8); SAME_CLADE_REPEAT(1.00>=0.45); RETUNE_ONLY(numbers=1.0,skeleton=1.00) |
| `M2-c2` | clone:paraphrase | **拒** | CLONE_SKELETON(0.81>=0.72); CLONE_CONTAINMENT(0.91>=0.85); CLONE_LEXICAL(0.82>=0.8); SAME_CLADE_REPEAT(0.81>=0.45); RETUNE_ONLY(numbers=1.0,skeleton=0.81) |
| `M2-c3` | clone:retune | **拒** | CLONE_SKELETON(1.00>=0.72); CLONE_CONTAINMENT(0.96>=0.85); CLONE_LEXICAL(0.91>=0.8); SAME_CLADE_REPEAT(1.00>=0.45) |
| `M2-c4` | clone:embedded | **拒** | CLONE_CONTAINMENT(1.00>=0.85); SAME_CLADE_REPEAT(0.63>=0.45); RETUNE_ONLY(numbers=1.0,skeleton=0.63) |
| `M3` | independent | 收 | — |
| `M3-c1` | clone:verbatim | **拒** | CLONE_SKELETON(1.00>=0.72); CLONE_CONTAINMENT(1.00>=0.85); CLONE_LEXICAL(1.00>=0.8); SAME_CLADE_REPEAT(1.00>=0.45) |
| `M3-c2` | clone:paraphrase | **拒** | CLONE_SKELETON(0.83>=0.72); CLONE_CONTAINMENT(0.85>=0.85); SAME_CLADE_REPEAT(0.83>=0.45) |
| `M3-c3` | clone:retune | **拒** | CLONE_SKELETON(1.00>=0.72); CLONE_CONTAINMENT(1.00>=0.85); CLONE_LEXICAL(1.00>=0.8); SAME_CLADE_REPEAT(1.00>=0.45) |
| `M3-c4` | clone:embedded | **拒** | CLONE_CONTAINMENT(1.00>=0.85); SAME_CLADE_REPEAT(0.65>=0.45) |
| `M4` | independent | 收 | — |
| `M4-c1` | clone:verbatim | **拒** | CLONE_SKELETON(1.00>=0.72); CLONE_CONTAINMENT(1.00>=0.85); CLONE_LEXICAL(1.00>=0.8); SAME_CLADE_REPEAT(1.00>=0.45) |
| `M4-c2` | clone:paraphrase | **拒** | CLONE_SKELETON(0.84>=0.72); CLONE_CONTAINMENT(0.92>=0.85); CLONE_LEXICAL(0.84>=0.8); SAME_CLADE_REPEAT(0.84>=0.45) |
| `M4-c3` | clone:retune | **拒** | CLONE_SKELETON(1.00>=0.72); CLONE_CONTAINMENT(1.00>=0.85); CLONE_LEXICAL(1.00>=0.8); SAME_CLADE_REPEAT(1.00>=0.45) |
| `M4-c4` | clone:embedded | **拒** | CLONE_CONTAINMENT(1.00>=0.85); SAME_CLADE_REPEAT(0.67>=0.45) |
| `M5` | independent | 收 | — |
| `M5-c1` | clone:verbatim | **拒** | CLONE_SKELETON(1.00>=0.72); CLONE_CONTAINMENT(1.00>=0.85); CLONE_LEXICAL(1.00>=0.8); SAME_CLADE_REPEAT(1.00>=0.45); RETUNE_ONLY(numbers=1.0,skeleton=1.00) |
| `M5-c2` | clone:paraphrase | **拒** | CLONE_SKELETON(0.77>=0.72); CLONE_CONTAINMENT(0.88>=0.85); SAME_CLADE_REPEAT(0.77>=0.45); RETUNE_ONLY(numbers=1.0,skeleton=0.77) |
| `M5-c3` | clone:retune | **拒** | CLONE_SKELETON(1.00>=0.72); CLONE_CONTAINMENT(0.95>=0.85); CLONE_LEXICAL(0.91>=0.8); SAME_CLADE_REPEAT(1.00>=0.45) |

**人口结构**（反同族，n=6）：

| 族 | 方案数 | 占比 |
|---|---|---|
| deepseek | 2 | 0.3333 |
| glm | 1 | 0.1667 |
| minimax | 1 | 0.1667 |
| qwen | 2 | 0.3333 |

前两族占比 0.6666，已收方案最大两两骨架相似度 0.0412
（独立方法卡之间应当很低；同值偏高就说明这批"独立"其实是改写）。

**盲预测计分（合成 dry-run）**：40 条结果，区间命中 7 条
（hit_rate 0.175），区间宽度中位 0.13705000000000003，时间序检查
✅，锁校验 ✅。逐席：

| 席位 | 实测 | 预测区间 | 结果 |
|---|---|---|---|
| IN25-T2/deepseek/direct/attacker | 0.6116 | [0.39, 0.49] | 未中 |
| IN25-T2/deepseek/direct/breaker | 0.6346 | [0.77, 0.99] | 未中 |
| IN25-T2/deepseek/failure/attacker | 0.6410 | [0.16, 0.26] | 未中 |
| IN25-T2/deepseek/failure/breaker | 0.6038 | [0.33, 0.55] | 未中 |
| IN25-T2/deepseek/inverse/attacker | 0.7183 | [0.45, 0.55] | 未中 |
| IN25-T2/deepseek/inverse/breaker | 0.6398 | [0.36, 0.58] | 未中 |
| IN25-T2/deepseek/limits/attacker | 0.7137 | [0.14, 0.24] | 未中 |
| IN25-T2/deepseek/limits/breaker | 0.6938 | [0.76, 0.98] | 未中 |
| IN25-T2/deepseek/orthogonal/attacker | 0.6534 | [0.20, 0.30] | 未中 |
| IN25-T2/deepseek/orthogonal/breaker | 0.6622 | [0.56, 0.78] | 命中 |
| IN25-T2/glm/direct/attacker | 0.6404 | [0.84, 0.94] | 未中 |
| IN25-T2/glm/direct/breaker | 0.5609 | [0.63, 0.85] | 未中 |
| IN25-T2/glm/failure/attacker | 0.6286 | [0.48, 0.58] | 未中 |
| IN25-T2/glm/failure/breaker | 0.6337 | [0.79, 1.00] | 未中 |
| IN25-T2/glm/inverse/attacker | 0.5502 | [0.45, 0.55] | 未中 |
| IN25-T2/glm/inverse/breaker | 0.5313 | [0.62, 0.84] | 未中 |
| IN25-T2/glm/limits/attacker | 0.5658 | [0.16, 0.26] | 未中 |
| IN25-T2/glm/limits/breaker | 0.6708 | [0.68, 0.90] | 未中 |
| IN25-T2/glm/orthogonal/attacker | 0.6870 | [0.79, 0.89] | 未中 |
| IN25-T2/glm/orthogonal/breaker | 0.6158 | [0.08, 0.30] | 未中 |
| IN25-T2/minimax/direct/attacker | 0.6538 | [0.63, 0.73] | 命中 |
| IN25-T2/minimax/direct/breaker | 0.5891 | [0.61, 0.83] | 未中 |
| IN25-T2/minimax/failure/attacker | 0.5987 | [0.32, 0.42] | 未中 |
| IN25-T2/minimax/failure/breaker | 0.6715 | [0.83, 1.00] | 未中 |
| IN25-T2/minimax/inverse/attacker | 0.6413 | [0.34, 0.44] | 未中 |
| IN25-T2/minimax/inverse/breaker | 0.6317 | [0.73, 0.95] | 未中 |
| IN25-T2/minimax/limits/attacker | 0.6643 | [0.41, 0.51] | 未中 |
| IN25-T2/minimax/limits/breaker | 0.5986 | [0.71, 0.93] | 未中 |
| IN25-T2/minimax/orthogonal/attacker | 0.6514 | [0.20, 0.30] | 未中 |
| IN25-T2/minimax/orthogonal/breaker | 0.5571 | [0.35, 0.57] | 命中 |
| IN25-T2/qwen/direct/attacker | 0.6482 | [0.88, 0.98] | 未中 |
| IN25-T2/qwen/direct/breaker | 0.6095 | [0.39, 0.61] | 未中 |
| IN25-T2/qwen/failure/attacker | 0.5658 | [0.66, 0.76] | 未中 |
| IN25-T2/qwen/failure/breaker | 0.6750 | [0.82, 1.00] | 未中 |
| IN25-T2/qwen/inverse/attacker | 0.6063 | [0.13, 0.23] | 未中 |
| IN25-T2/qwen/inverse/breaker | 0.6591 | [0.45, 0.67] | 命中 |
| IN25-T2/qwen/limits/attacker | 0.6727 | [0.60, 0.70] | 命中 |
| IN25-T2/qwen/limits/breaker | 0.5642 | [0.52, 0.74] | 命中 |
| IN25-T2/qwen/orthogonal/attacker | 0.6945 | [0.71, 0.81] | 未中 |
| IN25-T2/qwen/orthogonal/breaker | 0.5860 | [0.48, 0.70] | 命中 |

## ④ 图

![fig1](artifacts/in25_t2/fig1_coverage_matrix.png)

**图 1** 氏族覆盖矩阵：左攻坚者、右破坏者，格子里的数字是该 (族,框架) 格内席位数。
所有格子均 ≥1，说明"多样"不是靠多派几席堆出来的，而是矩阵的每一格都有负责人。

![fig2](artifacts/in25_t2/fig2_similarity_separation.png)

**图 2** 相似度门的分离度：蓝=独立方案对，橙=克隆对，黑虚线=拒绝阈值
（骨架 ≥0.72）。两族分布被阈值分开，间距
（独立 max 0.0412 → 克隆中位 0.9123）约两个数量级；
"嵌长文"类克隆骨架分低（0.6308），靠包含度 0.85
这一路兜住——**单靠一种相似度会漏**，这是多路或门存在的理由。

## ⑤ 注意点 / 局限（踩过的坑）

1. **"种子互异"是最弱的判据**：`len(set(seeds))==n` 也能通过低位同构的一组种子。实现因此
   直接报**两两汉明距离**（实测 min 21 bit、中位 31 bit），
   并把它当判据（碰撞 0 且 min ≥16 bit）；只看"不相等"会自欺。
2. **"换词不换招"是主干攻击面**：表层 Jaccard 对同义改写只有 0.77（<0.80 阈值），
   骨架表征把它抬到 0.9123。只做词面查重会放过大部分克隆。
3. **只看重摘要不看数字**：把 0.5→0.35、三次→五次这类"只调参"克隆，数字集合虽变，
   骨架仍 ≥0.5 且数字集合被判为"只调参"路径；反之若只比数字，独立方法常撞同样的常数。
4. **族上限会误伤**（已知局限）：`CLADE_SHARE_CAP=0.6`
   在多族并存时无害，但若某族确实显著更强，人口约束会压它——这是**刻意的**多样性税，
   调参口径由战役维护者决定，不隐瞒。
5. **合成 dry-run 不是能力证据**：本节点的 seat"结果"由种子确定性生成（`blind_scores.json`
   里 `synthetic: true`），只用来打通"锁→跑→计分"的时序门与计分器。真实锦标赛（真实模型 +
   真实物理判分）由 IN25-T3 在集群上跑；那时本节点提供的是 `build_seats()` / `seat_prompt()`
   接口与两道门。
6. **阈值是在自造语料上定的**：语料由 6 张方法卡 + 4 类克隆变体 + 3 个 fail-closed 负例构成，
   标签由构造已知。真实方案的分布会更宽，误判率需要在 T3/T4 的真实产出上重估（预计需要下调
   `T_CLONE_SKELETON` 之外再引入"方法关键词白名单"）。
7. **中文分词不是真分词**：CJK 用二元组，长句改写（>50% 词序变化）会让表层分虚高；
   骨架靠近义词表兜，表外近义词仍可能漏判 —— fail-open 的方向必须靠"同族更严"阈值平衡。
8. **`roster.py` 的族清单会过期**：中继模型会增减，`verify_roster()` 每轮实测，不缓存。

## ⑥ 误差判定（关键量 vs 基准，逐项回答"是否 <3%"）

| 量 | 基准 | 实测 | 误差 | <3%？ |
|---|---|---|---|---|
| 矩阵覆盖率 | 1.000 | 1.0 | 0 | **是**（0.0%） |
| 克隆拒绝率（端到端） | 1.000 | 1.0 | 0 | **是**（0.0%） |
| 独立方案误拒率（FPR） | 0.000 | 0.0 | 0 | **是**（0.0%） |
| 预测锁复算一致 | 一致（True） | True | — | **是**（布尔一致） |
| 种子碰撞 | 0 | 0 | 0 | **是**（0.0%） |
| 盲预测命中率 | 无信息基线=平均区间宽度 0.1576 | 0.175 | 0.017（相对基线 11.0%） | **不适用**——合成 dry-run 无物理基准，命中率与无信息基线同量级属预期（恰好说明流程无泄漏）；真实命中率待 T3 |

**物理基准声明**：本节点是**进程/纪律机器**，不产生任何物理量（零平台域语义），因此
"对标实验值误差 <15%/3%" 这类物理判据**不适用**；这里可判的只有决策误差（覆盖率、
拒绝率、碰撞数、哈希一致），其误差均为 0（数值判据见上表）。合成 dry-run 的命中率
不作能力结论。

## 审计链

```
目标文本 ──► build_seats() ──► 40 席 (族×框架×角色) ──► plan_digest
                        │                                   │
                        ├──► coverage.json / seed_divergence.json
                        └──► seal(predictions) ──► prereg_lock.json (lock_hash 1b96a171b6000fb9…)
                                                   │
                         结果 recorded_utc ──────────┴──► score_blind() ──► blind_scores.json
方案文本 ──► normalize/skeleton/number ──► 三层判据 ──► dedup_ledger.json ──► population.json
```

复算命令：`python3 -m tools.diversity.run_node && python3 -m tools.diversity.gen_doc && python3 -m pytest tests/test_diversity_engine.py -q`
