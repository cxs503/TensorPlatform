# ADR-001：AI 原生上下文语言（AIL）——先做语义记忆层，不先造新语法

- 状态：**已接受（Accepted）**，实现范围限定在隔离实验路径
- 日期：2026-10-23
- 决策者：TensorPlatform owner（方向裁定）+ mission m204 执行体
- 关联：[Issue #74](https://github.com/cxs503/TensorPlatform/issues/74)（AIL 原型与基准）、
  [Issue #72](https://github.com/cxs503/TensorPlatform/issues/72)（Tensor Soul）、
  [契约 v0.1](../architecture/TENSOR_SOUL_CONTRACT_v0.1.md)、
  [数据规范](../specs/AIL_DATA_SPEC.md)、[阶段 1-2 报告](../architecture/AIL_STAGE12_REPORT.md)

## 1. 背景

平台上的常驻 Agent 每个班次开工时，是靠**把历史重新喂回模型**来"记起自己在干什么"的。现行机制
（详细盘点见附录 A 的复用清单，基线为 Soul 审计 `TENSOR_SOUL_AUDIT.md`）有三个可直接指认的事实：

1. 上下文由**原始记录拼接**而成：`tools/factory/resident_harness.py:187` 每步把全量人格档案
   `persona.read()` + 近期记忆 `persona.search()` + 工具契约 + 目标 schema + 任务证据一次性塞进 prompt；
   `tools/agent/tensor_agent.py:1055` 的 `build_memory_block()` 把同仓库最近 3 个终态 mission 的
   终报摘要注入 goal。这些块是**无预算上限的自然语言**，其 token 成本随历史线性增长。
2. 记忆的**容量**被硬截断而非压缩：`backend/api/factory_residents.py:116` 是
   `memory = (memory + [entry])[-12:]` 的滑动窗口（旧记录直接丢弃）；
   `tools/factory/persona.py:44` 的 `search()` 是子串匹配 + 滑动 `limit`（默认 6、上限 12）。
3. 记录的**语义结构没有被表达**：`persona.py:41` 写入的条目只有
   `id/agent_session_id/kind/text/evidence_refs/created_at`——"这是哪条决策、依据哪个工件、
   被谁取代、当前是否仍然有效"这些关系全部隐含在自由文本里。

于是每班次要花的 token 越来越多，而**信息的可恢复性并没有随之提高**：滑动窗口丢掉的正是早期决策，
自由文本里的关键数字（判据 X/Y、SHA、容器数）与叙述性文字混在一起，模型要靠猜。

Issue #74 给的直觉解法是"造一门 AI 原生上下文语言"（专门符号语法、二进制编码、学习型编码）。
本 ADR 要决定的是**先做哪一层**。

## 2. 决策

**先实现语义记忆层（AIL memory kernel），把"新语法/二进制编码/学习型编码"推迟到内核验证之后，
并以其测量结果作为是否值得做的依据。**

具体含义：

1. 本阶段交付的是**结构化记忆表示 + intent-aware 检索 + 有预算的上下文装配 + 可复现基准**，
   落盘面是规范化 JSON（Canonical JSON），不是新语法、不是二进制、不引入任何训练。
2. 全部实现放在**隔离实验路径**（`tools/ail/`、`scripts/run_ail_benchmark.py`、`tests/ail/`），
   不接入常驻 Agent 的正式执行链（`tensor_agent.py` / `resident_harness.py` 一行不改）。
3. 阶段 1-2 的产出是**判据数据**：在同等 token 预算下，四臂（原始历史 / 自然语言摘要 /
   结构化 JSON / AIL）的任务恢复准确率。阶段三是否上专有语法、是否上二进制、是否上学习型编码，
   由这份数据决定，不由偏好决定。

## 3. 理由

### 3.1 瓶颈定价：token 预算贵在"拓扑"，不在"编码"

一次 LLM 调用的 token 成本 ≈ 上下文字节数 / 每 token 字节数。改编码（更短的分隔符、二进制打包）
最多在这一项上拿一个**常数因子**；而改记忆拓扑（只送当前 intent 需要的那几条、把失效的决策标出来、
把证据折叠成引用）拿的是**与历史长度的斜率**：历史越长收益越大。后者是先做的那一层。

反过来，先做语法会在没有度量时锁死格式：语法一旦被写进 prompt 与训练/微调数据，迁移成本远高于
内部 JSON schema 的版本号。**先证明信息密度问题值得解决，再决定用什么符号解决它。**

### 3.2 收益可度量、可归因

语义层的效果可以用"同等 token 预算下的四元组恢复率"直接测（状态 / 决策 / 依据 / 下一步），
每个数字都能追到具体记录。而新语法的效果必须先有一个**参照系**才能评估——否则"新语法比原来好"
这句话无意义。也就是说：AIL 记忆层是语法实验的**前置测量装置**，不是它的竞争对手。

### 3.3 与 Soul 分层：AIL 是派生视图，不是第二份真相

Tensor Soul（Issue #72）已经把**身份 / 事件 / 信任**这一层落到了代码（`factory_agent_identities`、
`factory_memory_events` 触发器、checkpoint 校验和、ModelBinding 历史、LearningCandidate 晋级门）。
如果 AIL 再造一套"记忆事件账本"，就会出现两份互相漂移的真相——这正是要避免的失败模式。

因此本 ADR 把边界定成一条硬规则：

> **Soul 拥有事实（append-only、带来源与验证状态）；AIL 拥有视图（可重建、可丢弃、永远不写 Soul 的账本）。**

由此推出 AIL 的三条自约束：

1. **可重建性**：任何 AIL 结构都能从 Soul 事件（或本阶段的记录快照）重新派生；删除 AIL 存储不损失事实。
2. **零回写**：AIL 不修改、不删除 Soul 的记忆事件；它只读，并把自己产出的上下文标注为"派生视图"。
3. **不越权验证**：AIL 不改变 `verification_status`。检索器可以**降权**未验证/被取代的条目，
   但不能把 `observed` 提升为 `validated`。

### 3.4 不做的清单（明确排除，避免范围漂移）

- 新符号语法 / DSL 编译器：**不做**（阶段三之后，且需先有本阶段的测量）。
- 二进制编码 / 自定义 tokenizer：**不做**。
- 学习型编码模型（embedding 微调、压缩器训练）：**不做**。检索用词法 + 结构权重，可解释、可离线复现。
- 接入正式工作流（让常驻 Agent 真的读 AIL 上下文）：**不做**。属阶段三隔离实验。

## 4. 与 Soul 层的接口边界

**事实核定（2026-10-23，`1b01550`）**：Soul 第一阶段已并入 main
（`backend/services/soul_runtime.py`、`backend/migrations/094_factory_soul.sql`、
`tests/factory/test_soul_runtime.py`、`soul_phase1_validation.json` 均在 main 树内）。
因此下表逐行对照**真实代码**，不是对提案的推测。

| 关注点 | Tensor Soul（Issue #72，main 已落地） | AIL（本 ADR） | 边界规则 |
|---|---|---|---|
| 身份 | `factory_agent_identities`（无 model 列，`UNIQUE(org,worker_id)`）；`soul_runtime.create_agent` | 无身份概念 | AIL 只携带 `agent_id`/`session_id` **引用**，不定义、不改写 |
| 事件账本 | `factory_memory_events`（`094:71-99`）+ 触发器 `094:102-123` 硬禁 DELETE 与内容 UPDATE | 只读消费 | AIL **永不**写事件表；不新增事件表 |
| 验证状态 | `observed/validated/rejected/superseded`；`CHECK (sensitivity<>'shared' OR verification_status='validated')`（`094:91`，DB 兜底） | 消费并降权 | AIL 不产生新状态，只读；本层的 `shared` 规则与之同构（SPEC §4.6） |
| 检查点 | `factory_soul_checkpoints`（`PK(session_id,version)` + checksum）；`commit_checkpoint`/`restore_checkpoint`，不符抛 `CheckpointCorruptError` 拒绝恢复 | 可选引用 | AIL 不替代 checkpoint；`SessionState.checkpoint_ref` 只存版本号+校验和 |
| 记忆表示 | `payload` 为自由 JSON（`canonical()` 序列化后落库） | **MemoryItem / Relation / EvidenceRef** | 结构属于 AIL，事实来源属于 Soul |
| canonical 序列化 | `soul_runtime.canonical(payload)`：`sort_keys/ensure_ascii=False/separators` | 逐字相同（SPEC §9） | **两侧哈希可互认**：同输入同 sha256，`tests/ail/test_ail_schema.py` 有交叉校验用例 |
| 检索 | `recall(task_context, limit, max_chars)`：词法重合 + verified + 本人 排序，**字符**预算硬截断 | `retrieve()`：意图加权 + 结构权重，**token** 预算 | Soul 保证"能取回且可信"，AIL 保证"取回的信息密度" |
| 装配 | `prepare(task_context, max_chars, recall_limit)`：identity/session/recall 分节，**字符**预算 | `assemble()`：分层（L0..L4）+ **token** 预算 + 降级/丢弃记账 | 互补：Soul 的 prepare 是"有界回放"，AIL 是"分层压缩+意图选择" |
| 晋升 | `factory_soul_learning_candidates` 晋级门（`094:175` 要求 validator） | 无 | AIL 不得成为晋级判据的证据来源 |

一句话：**Soul 是账本与信任边界，AIL 是索引、摘要与预算调度器。**
Soul 的数据（`factory_memory_events.payload`）就是 AIL 未来的**首选输入源**。

### 4.1 AIL 与 Soul 的两处实质差异（必须说清，避免被误读为重复建设）

1. **预算口径不同**：Soul 按**字符**（`DEFAULT_RECALL_CHARS=2400`、`DEFAULT_PREPARE_CHARS=6000`），
   AIL 按**token**（tiktoken 实测）。字符预算是模型无关的近似；token 预算才是"模型的上下文成本"。
   本阶段实测了二者偏差：见报告 §4 的 `tokens/char` 比（中英文混排约 0.31–0.38 token/char），
   说明用字符卡预算会**低估**中文语料的真实成本。
2. **表示层不同**：Soul 的 `payload` 是自由结构，检索只能做 payload 全文的词法重合；
   AIL 把断言拆成 `(topic, value)`，因此能做"同主题冲突检测"（SPEC §10）与"按意图选层"。
   Soul 侧 `recall` 也做了 `overlap==0 and selected` 的噪音拦截（`soul_runtime.py:466`），
   与本层"零词法重叠减半"是同一个工程直觉，但缺少 topic 维度，做不到冲突上报。

### 4.2 阶段 3 的接入路径（现在不做）

AIL 若要接 Soul，只做**一个方向**的适配：`MemoryItem.source.kind='mission'|'event'` 指向
`factory_memory_events.event_id`，其余字段按 SPEC §13 的对照表映射（已按 Soul 字段命名设计，
切换时不需要改数据结构、不需要迁移历史）。**本阶段不写这层适配**，见 §3.4 与 §6 的闸门。

本阶段的可运行性约束：m204 的语料以**记录快照**（真实工件抽取 + sha256 来源追踪）为输入，
不依赖数据库；这样基准可在纯离线环境复现（无 pgserver、无网络），
且不触碰任何 Soul 表。

## 5. SimpleMem / LLMLingua 的定位

两者在本工作中**只作对比基线与设计参考，不作预期值来源**。

| 基线 | 出处 | 在本工作的定位 |
|---|---|---|
| SimpleMem | arXiv [2601.02553](https://arxiv.org/abs/2601.02553)（语义结构化压缩 + 在线语义合成 + intent-aware 检索规划） | 设计参考（我们的 intent-aware 检索与"多视图索引"受其启发）。它自报在 LoCoMo 上 F1 提升约 26.4%、推理期 token 消耗最多降 30×——**该数字属其论文设定，不得搬运为本平台预期**。 |
| LLMLingua | arXiv [2310.05736](https://arxiv.org/abs/2310.05736)（粗到细 prompt 压缩、预算控制器） | 对比基线的**方法族**（token 级压缩）。其自报最高约 20× 压缩**不是**本平台可继承的结论。 |

纪律（写进报告即判据）：

1. **不搬运**：任何外部论文的分母（数据集）、分词器、预算、任务定义都与本平台不同，
   外部数字只可作为"该方法在其设定下的自报结果"引用，**不得**出现在本平台的"预期/达标"栏。
2. **不声称压缩比**：本平台只报**实测** token 数与实测恢复率，且必须给出命令、退出码与工件路径。
3. **可替换**：`arms.py` 里"自然语言摘要"与"token 级压缩"两臂是可插拔的基线实现，
   若后续要接入真实 LLMLingua 权重，只需替换 arm 实现，判据协议不变。

## 6. 后果与阶段三闸门

### 正面后果

- 每班次的上下文成本变为**可预算的**（按 token 给上限，装配器绝不超）。
- 关键事实（判据数字、SHA、门状态、未完成事项）与叙述文本在结构上分离，**可逐项判分**。
- 决策的取代关系（superseded）显式化，模型不再同时看到互相矛盾的旧决策而无从判断。

### 负面后果 / 代价

- 需要一层"真实记录 → AIL 条目"的转换器，转换规则本身可能引入偏差（本阶段用**可用性/逐字
  可恢复性**度量把这些偏差暴露出来，而不是假设它没有）。
- 结构化字段会**鼓励**模型把内容塞进 `value` 里当自由文本用；用 SPEC 的 `value` 长度上限与
  `text` 单行约束抑制。
- 抽取式基线（自然语言摘要、token 级压缩）在真实 LLM 上可能比我们的确定性实现更强，
  因此本阶段的对比结论**只在"确定性、可复现、离线"这一口径下成立**（见 SPEC §8 与报告局限章）。

### 阶段三闸门（进入条件，缺一不进）

1. 四臂对比在至少 3 个预算档位上稳定给出可解释的结论，且 AIL 在**同预算**下不劣于原始历史
   （劣于则不进入阶段三，先修内核）。
2. 探针集包含反例（时间冲突、被取代的决策、未验证证据），AIL 在这些反例上不产生"自信的错误"。
3. 隔离实验设计（A/B 同一任务、同模型、同预算，双盲判分）与回滚开关就绪。

## 7. 备选方案与否决理由

| 备选 | 否决理由 |
|---|---|
| A. 直接设计新符号语法 / DSL | 在无测量时锁死格式；工具链与模型侧支持成本高；对"历史线性增长"这一主因无直接作用 |
| B. 二进制 / 自定义 tokenizer | 只拿常数因子，且破坏可审计性（人无法直读）；与"durable facts 保持可检查"的契约冲突 |
| C. 学习型压缩器（先训再测） | 训练需要标签与算力；无基线时无法归因；把"是否有效"和"是否训成"混成一件事 |
| D. 只扩容上下文（更大窗口 / RAG 检索原文档） | 不动记忆拓扑，成本随历史线性增长；检索到的仍是自由文本，无法逐项判分 |
| E. 复用 Soul 的 recall 不做新层 | Soul 的 recall 是**字符**预算 + 自由 payload，没有 representation 与 intent 概念；本阶段需要 token 预算与四元组判分（但复用其来源/验证语义） |
| **F. 语义记忆层（选中）** | 直接作用于主因（拓扑），可度量，可回滚，且是语法实验的前置测量装置 |

## 附录 A：与既有实现的复用清单（对照 Soul AUDIT）

基线：`docs/architecture/TENSOR_SOUL_AUDIT.md`（m200，只读参考） + main 现有代码。
分类：**直接复用** / **需改造** / **需新建**。

| # | 既有能力 | 位置（main） | AIL 处置 | 说明 |
|---|---|---|---|---|
| A1 | 会话/任务引用与幂等 `task_key` | `backend/migrations/091_factory_residents.sql:19-25`、`backend/api/tensor_missions.py:113` | 直接复用 | 作为 `SessionState.mission_ref` 的外部键，AIL 不重造任务模型 |
| A2 | 事件追加（审计面） | `backend/api/tensor_sessions.py:149` `append_session_events_batch` | 直接复用（只读消费） | AIL 从事件流派生条目；不新增事件表 |
| A3 | 人格档案与 append-only 记忆文件 | `tools/factory/persona.py:36-52` | 需改造（作为**输入源**） | `memory.jsonl` 是阶段 2 的转换输入之一；其缺 `provenance`/`sensitivity`/`verification_status`（Soul 审计已判定），AIL 条目补上 `source`+`verification` 字段而不改该文件格式 |
| A4 | 会话 12 条滑动窗口 | `backend/api/factory_residents.py:116` | **不复用**（反例） | 该行为即"丢弃式记忆"，是基准里"原始历史臂"的极端形态；AIL 不继承 |
| A5 | 有界 prompt 装配（超长截断） | `tools/factory/resident_harness.py:178-187` | 需改造 | 现有是**字符**截断 + "读全文工具"兜底；AIL 改为 **token 预算 + 分层降级**（full→compact→引用） |
| A6 | 往次任务记忆注入 | `tools/agent/tensor_agent.py:1055-1088` | 需改造 | 现为最近 3 条终报的固定模板；AIL 按 intent 选条目，同样输入可产生更小上下文（阶段三接入才生效，本阶段不接线） |
| A7 | native harness 状态检查点（version+checksum） | `tools/agent/native_harness.py:426-456` | 直接复用（口径） | 采用"版本+校验和、不符则**拒绝**而非静默丢弃"的准则（Soul 已把它升级为 `CheckpointCorruptError`）；AIL 的 canonical JSON 校验和与之同算法 |
| A8 | 稳定的 canonical 序列化 | `backend/services/soul_runtime.py:53`（main 已落地） | 直接复用（口径） | `sort_keys=True, ensure_ascii=False, separators=(',',':')` —— 与 Soul 的 checksum 口径逐字一致，保证两边哈希可互认（`tests/ail/test_ail_schema.py::test_checksum_matches_soul_algorithm` 机械校验） |
| A9 | 来源追踪 `evidence_refs` | `tools/factory/persona.py:39-41` | 需新建 | 现状是自由字符串列表；AIL 定义结构化 `EvidenceRef{kind,locator,sha256,excerpt}` |
| A10 | 意图/相关性打分 | `backend/services/soul_runtime.py:432` `recall()` 有词法重合排序（无意图维度、无 topic 冲突检测）；`Persona.search` 仅子串匹配 | **需新建** | intent-aware 检索器（意图加权 + 结构权重 + 时效 + 取代关系 + 冲突成组返回），Soul 侧保留其自身实现不动 |
| A11 | token 计量 | 无（全平台按字符数） | **需新建** | `tools/ail/tokens.py`：优先 tiktoken `cl100k_base`，缺失时回退并**显式标注**回退口径 |
| A12 | 记忆条目/关系/更新/会话状态 schema | Soul 有 `MemoryEvent`/`SessionState`/`LearningCandidate`（自由 payload，无 topic/value）；AIL 的**表示层**仍是新建 | **需新建** | 见 `docs/specs/AIL_DATA_SPEC.md`；与 Soul 字段的对照表见 SPEC §13 |
| A13 | 基准协议与判分 | 无 | **需新建** | 四臂 × 预算档位 × 四元组探针 |

## 附录 B：证明"不复制改造 Soul / 既有执行链"的机械核对

本 ADR 的实现（阶段 2）只**新增**文件：`tools/ail/**`、`scripts/run_ail_benchmark.py`、
`scripts/verify_ail_stage12.py`、`tests/ail/**`、`artifacts/**`，以及三份文档 +
机读判据工件 `ail_stage12_validation.json`。核对命令（结果见报告 §6、由
`scripts/verify_ail_stage12.py` 的 `existing_chain_untouched` 判据机械生成）：

```
# 相对本分支与 main 的合并基点，既有执行链必须零差异
git diff --stat $(git merge-base HEAD origin/main)..HEAD -- backend tools/agent tools/factory
```

对 m200 目录的只读核对（本任务全程只读）：

```
git -C /srv/tensor/shared/tensor_eco/agent_work/m200/TensorPlatform status --short   # 应为空
```

> 注：`git diff --stat origin/main -- backend` 在**分叉**时会显示"删除 Soul 文件"的假象
> （因为 origin/main 已 ahead 而本分支未合入）。正确口径是上面的 `merge-base..HEAD`。
> 本分支已 `--ff-only` 合入 origin/main（见报告 §1），故两种口径现在一致。