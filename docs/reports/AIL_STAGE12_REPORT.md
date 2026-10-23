# AIL 阶段 1-2 交付报告（Issue #74）

> AI 原生上下文语言与记忆内核：规范 + 基准 + 可运行原型
> 分支 `agent/m204` · 基线 main `1b01550` · 本报告全部数字由
> `artifacts/ail_benchmark.json` + `ail_stage12_validation.json` 当场直读抄录。

本报告只写**实测**数字，每个数字都可用 §6 的命令现场复算。
未执行的项一律标 **NOT RUN** 并写明原因（§7）。

---

## 1. Commit 清单

| SHA | 内容 |
|---|---|
| `1b01550` | 合并基点（origin/main，`Merge pull request #78`）——含 Tensor Soul 第一阶段 |
| `fee8ddc` | 阶段一（ADR / DATA_SPEC / 基准协议）+ 阶段二原型（`tools/ail/**`、`tests/ail/**`、语料快照、两个脚本、基准工件） |
| 收尾提交 | 语料可复现性修复（`Record.to_dict()` 补 `valid_from`）+ 基准钉快照 + 交付报告 + `tokens_per_char` 机读判据（`head_commit` 见 `ail_stage12_validation.json`） |

本分支已 `git merge --ff-only origin/main`，与 main 无分叉；全程未 push。
逐位精确的 HEAD SHA 以 `ail_stage12_validation.json` 的 `head_commit` 字段为准
（报告本身在提交内，无法自指其自身 SHA）。

**重要事实**：Tensor Soul 第一阶段在本次任务期间并入 main
（`backend/services/soul_runtime.py`、`backend/migrations/094_factory_soul.sql`、
`tests/factory/test_soul_runtime.py`、`soul_phase1_validation.json`）。
因此 ADR-001 §4 的边界表是**对照真实代码**写成，不是对提案的推测。

---

## 2. 交付物

### 2.1 三份主文档（阶段一）

| 文件 | 内容 |
|---|---|
| `docs/adr/AIL_ADR_001.md` | 决策记录：为什么先做语义记忆层而非新符号语法；与 Soul 的接口边界（9 个关注点逐行对照真实代码 + 2 处实质差异）；SimpleMem/LLMLingua 的**基线定位**；阶段三闸门；附录 A 复用清单（对照 Soul AUDIT）、附录 B 机械核对 |
| `docs/specs/AIL_DATA_SPEC.md` | 版本化 schema：`MemoryItem`/`Relation`/`EvidenceRef`/`SessionState`/`MemoryUpdate`；确定性 ID；版本规则；canonical JSON；状态冲突处理；intent-aware 检索；预算装配；与 Soul 的字段对照表 |
| `docs/specs/AIL_BENCHMARK_PROTOCOL.md` | 判据（四元组）、四臂定义、三预算档、数据来源、执行取证、禁止事项、有效性边界、延迟实测口径 |

### 2.2 原型（阶段二）

| 文件 | 职责 |
|---|---|
| `tools/ail/model.py` | 五个实体 + 校验 + canonical JSON + 确定性 ID |
| `tools/ail/convert.py` | 转换器：真实记录 → `MemoryItem` + `EvidenceRef`（覆盖率记账，跳过项显式上报） |
| `tools/ail/retrieval.py` | intent-aware 检索：意图加权 × 结构权重 × 时效 × 取代关系 × 冲突成组 |
| `tools/ail/assembly.py` | 上下文构建器：L0..L4 分层 + token 预算 + 降级/丢弃记账 |
| `tools/ail/tokens.py` | token 统计（tiktoken cl100k_base；缺失时降级并在工件标注口径） |
| `tools/ail/bundle.py` | canonical JSONL 落盘/回读 + sha256 checksum 自校验 |
| `tools/ail/corpus.py` | 语料抽取 + 快照（含来源 sha256）+ **漂移检测** |
| `tools/ail/probes.py` | 25 个探针（四元组全覆盖）+ 四元组判分 |
| `tools/ail/state.py` | 会话状态推进与冲突处理 |
| `tools/ail/arms.py` / `runner.py` | 四臂实现 / 基准运行器（含延迟取样与语料来源记账） |
| `scripts/run_ail_benchmark.py` | 基准入口 |
| `scripts/verify_ail_stage12.py` | 18 项机读判据 → `ail_stage12_validation.json` |

### 2.3 语料（真实记录，路径与来源已写明）

语料一律由 `tools/ail/corpus.py` 的抽取器从三个仓库的**真实工件**只读抽取，
来源清单（相对生态根 `/srv/tensor/shared/tensor_eco/`）与每个文件的 sha256
记录在 `tools/ail/corpus/records.snapshot.json` 的 `sources[]`。

| 语料 | 来源 | 记录数 | 探针 | 覆盖率 |
|---|---|---|---|---|
| tensorplatform | `agent_work/m204/TensorPlatform/operator_log/*.readings.json`、`*.validation_report.json`、`agent_memory/platform-operator/{context.md,memory.jsonl}` | 295 | 13 | 1.0 |
| tensorphysics | `tensor_physics` 交付报告与决策记录 | 26 | 6 | 1.0 |
| tensordem | `tensor_dem` 交付报告与决策记录 | 150 | 6 | 1.0 |
| **合计** | 17 个来源文件 | **471** | **25** | **1.0** |

覆盖率 = 成功转换的源记录 / 全部源记录 = **1.0**（`corpora[*].coverage.coverage`）；
跳过项逐个记在 `corpora[*].skipped`（本次为 0）。

---

## 3. 四臂对比（主结果）

口径：tiktoken `cl100k_base` **真实 BPE** token；T2_normal 预算 **1200 tokens**；
25 探针 × 4 臂 × 3 档全部在同一进程、同一语料（快照，`mode=snapshot`、`drift=[]`）下执行。

| 臂 | 说明 | T2 token 均值 | **四元组恢复准确率** |
|---|---|---|---|
| A0 `raw` | 原始历史（现状做法，贪心截断到预算） | 1032.9 | **0.440** |
| A1 `nl_summary` | 自然语言摘要（确定性抽取式） | 1149.6 | **0.400** |
| A2 `json_struct` | 结构化 JSON（扁平数组，无 topic/intent） | 1143.8 | **0.450** |
| A3 `ail` | **AIL**：`MemoryItem` + topic/value + intent-aware 检索 + 分层预算 | 1188.8 | **0.900** |

**核心结论（本阶段要证明的那一条）**：在**同一 token 预算**（1200）下，
AIL 的四元组恢复准确率 **0.900**，比最好的基线（`json_struct` 0.450）高 **+0.45 绝对值**，
比原始历史（0.440）高 **+0.46**，而 token 用量与三个基线持平（1188.8 vs 1032.9–1149.6）。

### 3.1 四元组逐项（T2_normal）

| 臂 | 状态 | 决策 | 依据 | 下一步 |
|---|---|---|---|---|
| raw | 0.583 | 0.350 | 0.357 | 0.375 |
| nl_summary | 0.417 | 0.400 | 0.393 | 0.375 |
| json_struct | 0.500 | 0.500 | 0.357 | 0.438 |
| **ail** | **0.861** | **0.800** | **0.964** | **1.000** |

`decision` 是四元组里最难的一项（AIL 0.800），因为决策常以"否决/采纳 + 理由"的形式
散落在多个工件里、需要跨条聚合。`evidence`（0.964）与 `next_action`（1.000）接近满分，
说明 topic/value 拆解 + `EvidenceRef` 确实解决了"依据无法回溯"和"下一步查不到"两个问题。

### 3.2 预算档位扫描（成本—精度曲线）

| 臂 | T1 (400) tokens / acc | T2 (1200) / acc | T3 (3000) / acc |
|---|---|---|---|
| raw | 369.2 / **0.300** | 1032.9 / **0.440** | 2901.4 / **0.540** |
| nl_summary | 387.9 / 0.340 | 1149.6 / 0.400 | 2510.6 / 0.590 |
| json_struct | 357.1 / 0.300 | 1143.8 / 0.450 | 2714.3 / 0.580 |
| **ail** | 393.6 / **0.710** | 1188.8 / **0.900** | 2212.7 / **0.940** |

读法：
- **紧预算（400）差距最大**：AIL 0.710 vs 基线 0.30–0.34。原始历史在 400 token 里只能塞进
  少量记录开头，关键数字几乎必然被截掉——`answerable_rate` 仅 **0.08**（AIL 为 0.64）。
- **基线要到 3000 token 才追到 0.54–0.59**，而 AIL 在 **400 token 时就已 0.71**：
  AIL 用 393.6 token 超过了 raw 用 2901.4 token 的水平。
- AIL 在 T2→T3 趋于饱和（0.900→0.940），剩余 6% 主要来自 `decision` 与 `state`（见 §5.4）。

### 3.3 无预算时的自然成本（"压缩率"的可复现依据）

| 臂 | 自然 token 均值 | 自然 token 最大 | 自然准确率 |
|---|---|---|---|
| raw | 26946.7 | 43140 | **0.960** |
| nl_summary | 11741.8 | 19920 | 0.640 |
| json_struct | 18048.8 | 28225 | 0.850 |
| **ail** | **2467.4** | **4445** | **0.970** |

- AIL 的自然上下文成本是原始历史的 **9.16%**，即约 **10.9× 压缩**。
- 这个数字**有复现测量支撑**（`run_ail_benchmark.py` 对同一批记录现场分词得出），
  但只在"同语料、同 tokenizer、同装配策略"范围内成立，不得外推为通用压缩率。
- **最有说服力的一行**：AIL 把上下文压到 1/10.9，自然准确率 **0.970 反而不低于 raw 的 0.960**
  （差 0.01 ≈ 25 个探针里半个项，**在噪声范围内，不应宣称"优于"**）。
  这说明关键在于**表示方式**而不只是长度——raw 塞得再多，数字散落在散文里仍然取不出来。
- `nl_summary` 自然准确率只有 0.640：摘要压到 43.6% 的成本，但信息损失最大
  （数字被"约"掉、来源丢失），是"看起来省了其实废了"的典型。

### 3.4 延迟（Issue #74 判据之一，实测）

口径：`run_arm()` 纯本地墙钟耗时，每单元格取样 **3 次取中位数**；
**不含模型推理 / 网络 / DB**，因此衡量的是"装配开销"，不是端到端延迟。

| 臂 | T1 ms | T2 ms | T3 ms |
|---|---|---|---|
| raw | 16.25 | 17.18 | 19.29 |
| nl_summary | 8.76 | 9.58 | 11.87 |
| json_struct | 12.00 | 12.98 | 14.63 |
| **ail** | 13.04 | **18.05** | 17.00 |

AIL 装配开销与"原样拼接"同一数量级（T2：18.05ms vs 17.18ms，**+5.1%**），
远低于任何一次模型调用（通常数百毫秒到数秒）。
**结论：分层装配不是延迟瓶颈，瓶颈永远在模型侧。**
（`nl_summary` 最快是因为它输出字符最少，不是因为它更优。）

### 3.5 关键错误率（无自信错误，`no_confident_error_rate`）

"无自信错误" = 上下文中**没有出现与 gold 矛盾的错误数值**。

| 臂 | T1 | T2 | T3 |
|---|---|---|---|
| raw | 0.960 | 0.920 | 0.840 |
| nl_summary | 0.920 | 0.880 | 0.880 |
| json_struct | 0.960 | 0.920 | 0.880 |
| **ail** | **0.960** | **0.920** | **0.920** |

AIL 与 raw 持平（T2 均 0.92），且**预算放宽时不退化**（0.92→0.92），
而 raw 从 0.92 退化到 0.84（塞进越多旧记录，越容易撞上过期数值）。
这说明**AIL 的压缩没有引入新的错误信息**——这是压缩类方案最容易翻车的地方。

### 3.6 可追溯率（`traceable_rate`）

| 臂 | T1 | T2 | T3 |
|---|---|---|---|
| raw | 0.080 | 0.240 | 0.240 |
| nl_summary | **0.000** | **0.000** | **0.000** |
| json_struct | 0.080 | 0.240 | 0.280 |
| **ail** | **0.640** | **0.840** | **0.920** |

自然语言摘要的可追溯率**恒为 0**——摘要一写出来，来源就丢了。
这是 AIL 相对摘要类方案最硬的结构性优势：`EvidenceRef` 是 schema 的组成部分，不是可选装饰。
raw 的可追溯率低（0.24）是因为截断后来源行大多被切掉。

---

## 4. 一个口径发现：字符预算 ≠ token 预算（已写进 ADR §4.1）

Soul 的 `recall()`/`prepare()` 按**字符**卡预算（`DEFAULT_RECALL_CHARS=2400`、
`DEFAULT_PREPARE_CHARS=6000`），AIL 按 **token**。本阶段实测二者偏差：

| 臂 | token/char 比（T2，25 探针均值） | tokens 均值 | chars 均值 |
|---|---|---|---|
| raw | 0.314 | 1032.9 | 3310.2 |
| json_struct | 0.309 | 1143.8 | 3714.5 |
| nl_summary | 0.384 | 1149.6 | 2997.8 |
| **ail** | **0.384** | 1188.8 | 3384.4 |

（该表由 `run_arm()` 的 `tokens`/`chars` 字段实测；tokens 均值与 §3 完全一致，
可互相校验。`ail` 与 `nl_summary` 同处 0.384，因为二者都以中文散文句为主。）

中英混排语料约 **0.31–0.38 token/char**。含义：
- 用字符数卡 6000，实际约等于 **1900–2300 token**，对 8k 模型安全、对严格预算偏松；
- 反过来，若按"6000 token"设计却实现成 6000 **字符**，就只用掉预期的约 1/3 预算——
  看起来"省了"，其实是信息没塞进去。

AIL 用真实 token 计数消除了这层猜测。**这是 Soul 与 AIL 可以互补而非重复的地方。**

---

## 5. 局限与已修正的缺陷（必须读，否则会误读上面的数字）

### 5.0 本阶段发现并修复的两个真实缺陷（详见 §6.2）

1. **`Record.to_dict()` 漏 `valid_from`** → 快照往返丢真实时间戳（退化成 `retrieved_at`），
   导致 `--from-snapshot` 与现场抽取给出**不同的 item_digest**，基准不可复现。
   已修 + 加回归测试（`test_to_dict_covers_every_dataclass_field` 等 3 条）。
2. **语料钉在会增长的工件上** → `agent_memory/platform-operator/memory.jsonl` 是持续追加的账本，
   现场抽取每次给出不同语料（观测到 141→143 行）。
   已修：基准一律以 `mode=snapshot` 运行，`corpus_source.drift` 显式报告漂移；
   新增 `snapshot_drift()` 与 `load_snapshot()` 的未知字段硬拒绝。

> 这两个缺陷是本人自查发现的。**修复前的数字与修复后不同**
> （如 raw 自然成本 24036→26947 tokens），
> 本报告中的全部数字均为**修复后、快照钉定**的结果。
> 这正说明"可复现性契约"必须先立起来，否则任何数字都不可信。

### 5.1 判分是"信息在场性"判分，不是 LLM 阅读理解判分

四元组判分的做法是：gold 值（数字/字符串/布尔）是否**逐字出现**在上下文中、
来源定位串是否在场、是否出现矛盾值。它证明的是"必需信息在不在、可不可追"，
**不证明**"模型读了就能答对"。

→ 因此 **0.900 不能表述为"任务成功率 90%"**。下游任务成功率 **NOT RUN**（阶段三）。

### 5.2 A1/A2 是确定性抽取的弱基线，弱于真实 LLMLingua / SimpleMem

本任务禁止 GPU，且纪律要求"外部参考数字不得搬运为本平台预期"，故未跑真实压缩模型。

→ 结论**只能**表述为"AIL 优于原始历史与朴素结构化表示"，
**不能**表述为"AIL 优于 LLMLingua"。真实压缩臂见 §7。

### 5.3 语料规模有限（方向性结论）

471 条记录 / 25 探针 / 3 个仓库。**没有做置信区间**，不是统计显著性结论。
单个探针值 1/25 = 0.04，所以准确率差异小于 0.04 时视为噪声
（例：§3.3 的 0.970 vs 0.960 不应宣称"AIL 更准"）。

### 5.4 四元组分布不均

state 9 / decision 5 / evidence 7 / next_action 4。
`decision` 与 `next_action` 探针偏少，其准确率（0.800 / 1.000）波动区间较宽。

### 5.5 topic/value 拆解依赖规则化抽取

对"散文式"记录（大段自然语言周报）覆盖率会下降。本次语料结构化程度较高，
覆盖率才达 1.0。**散文语料的表现未测**。

### 5.6 延迟只测装配开销

不含模型推理、网络、DB。

### 5.7 未做隔离 A/B

没有在同一任务上对比"喂 raw"与"喂 AIL"时模型的实际表现差异（阶段三 A）。

---

## 6. 取证明细（命令 + 退出码，全部真实执行）

一键复算：`scripts/verify_ail_stage12.py` → `ail_stage12_validation.json`
（**19 项 checks，19/19 PASS**，approver 可直接读该工件。
工件 `not_run` 字段列出的 6 条与本报告 §7 一一对应）。

解释器：`/srv/tensor/shared/venvs/tensor_eco/bin/python`（Python 3.12）。
`PYTHONPATH` 注入 `agent_work/m204/.pydeps`（tiktoken / fastapi / pgserver 的**本地副本**）
+ 仓库根。**不引用 m200 目录、不修改 m200 任何文件**
（`git -C .../m200/TensorPlatform status --short` 为空，已核对）。

| # | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 1 | `python -m pytest -q tests/ail -p no:warnings` | **0** | **115 passed** |
| 2 | `python -m pytest -q tests/ --ignore=tests/ail --ignore=tests/test_public_wall.py -p no:warnings` | **0** | **113 passed** |
| 3 | `python -m pytest -q tests/test_public_wall.py tests/test_single_tenant_fallback.py -p no:warnings` | **0** | **9 passed** |
| 4 | `python scripts/run_ail_benchmark.py --out artifacts/ail_benchmark.json --from-snapshot` | **0** | 25 探针 × 4 臂 × 3 档 |
| 5 | `git diff --stat $(git merge-base HEAD origin/main)..HEAD -- backend tools/agent tools/factory` | **0** | 无输出（既有执行链零改动） |
| 6 | `verify_ail_stage12.py` 内 `tokens_per_char` 子进程（真实 `run_arm()` 复算） | **0** | raw 0.314 / nl 0.384 / json 0.309 / ail 0.384（§4 表格即此输出） |

### 6.1 确定性验证

- 同进程重跑两次：除墙钟延迟字段外，`probes` 逐字节一致（已核验）。
- 跨进程（不同 `PYTHONHASHSEED`）：AIL 结果一致。
- `corpus_source.mode = "snapshot"`、`drift = []`、`snapshot_sha256` 已写入工件，
  任何人可用同一快照复算出同一组数字。

### 6.2 一条需要说明的既有问题（不是本次引入）

`python -m pytest -q tests/ --ignore=tests/ail` **整目录一次性跑**会 collection error：
`tests/test_public_wall.py` 与 `tests/factory` 共享 module-level engine，
混跑触发 `psycopg2 DuplicateTable: relation "organizations" already exists`。

- **已在 main 基线复现**（`git worktree add /tmp/ail_base origin/main` 后同一命令同样报错）。
- 与本次改动无关（本次只新增 `tools/ail/**`、`tests/ail/**`、文档、脚本）。
- 因此拆成上表第 2、3 条命令分别执行，**两者均 0 退出**。
- 该事实同时写入 `ail_stage12_validation.json` 的 `known_pre_existing_issues`。

---

## 7. 明确 NOT RUN（不掩盖）

| 项 | 状态 | 原因 |
|---|---|---|
| 真实 LLMLingua / SimpleMem 压缩臂（`compact`） | **NOT RUN** | 需模型权重或外部服务；禁止 GPU；且纪律禁止搬运外部论文数字作预期。工件 `arm_notes.compact` 已显式标注 |
| 下游任务成功率（接模型的 A/B） | **NOT RUN** | 属阶段三隔离实验；本阶段只做信息在场性判分 |
| 部署 / 接入正式工作流 | **NOT RUN** | 只在隔离路径 `tools/ail/`；feature flag 与接入留待阶段三 |
| 新符号语法 / 二进制编码 / 学习型编码 | **不做** | owner 方向裁定：先证明"更少上下文准确恢复状态"，再决定是否需要专门语法。ADR-001 §3.4 记录否决理由 |
| 真实语料上的隔离 A/B | **NOT RUN** | 阶段三 |
| 散文式语料的表现 | **NOT RUN** | 本次语料结构化程度高，未覆盖（§5.5） |

---

## 8. 阶段三建议（接入常驻 Agent 的隔离实验）

前置闸门（ADR-001 §6）：本阶段五项判据（恢复准确率 / token 成本 / 延迟 / 可追溯率 /
关键错误率）均达标，**可以进闸门**。

### 阶段三 A：离线隔离 A/B（最先做，风险最低）
- 探针从 25 扩到 **50–80 个**，优先补 `decision`（5→20）与 `next_action`（4→15）短板。
- 同一道题分别喂 `raw` 与 `ail` 上下文给**同一模型、同一温度、同 token 预算**，
  让模型输出结构化答案（JSON：状态/决策/依据/下一步），自动比对 gold。
- 判据换成**真实下游任务成功率**（不再是信息在场性）。
- 关键控制：预算必须等 **token**（不是字符），否则对比无效（见 §4）。
- 实现路径：`tools/ail/arms.py` 已支持 `token_budget`，只需加一个"调用模型"的执行器。
- **预估**：必须先用 §5.5 的散文语料探针校准，否则 A/B 结果会被"语料太结构化"抬高。

### 阶段三 B：接入常驻 Agent（feature flag，影子先行）
- 接入点：`backend/services/soul_runtime.py:prepare()` 返回值旁路，
  新增 `AIL_CONTEXT_ENABLED` flag（默认 **off**）。
- **只做 AIL 侧适配层**：`MemoryItem.source.kind='event'` → `factory_memory_events.event_id`，
  字段对照已按 Soul 命名设计（DATA_SPEC §13），切换时**零改结构、零迁移历史**。
- **禁止**：AIL 写任何 Soul 表；AIL 结果不得作为 `LearningCandidate` 晋级证据。
- 先跑**影子模式**：AIL 上下文只生成、只记账、不进模型输入，
  验证 3–7 天装配稳定性、覆盖率不退化、延迟不劣化，再灰度开启。

### 阶段三 C：符号语法 / 二进制编码的决策点
- **只有**在 A 阶段证明"下游成功率仍受上下文长度限制"**且**"token 成本仍是瓶颈"时，
  才启动语法/编码设计。
- 届时判据应为：新编码相比当前 canonical JSON 在**同信息量**下再降 token ≥ 30%，
  且不损失可追溯率。**本阶段不预设**这个结论。

### 现在就该补的两件事（低成本、高价值）
1. **补散文语料探针**：当前 topic/value 依赖规则化字段映射，覆盖率 1.0 得益于语料结构化。
   应加入大段自然语言周报/评审意见，测覆盖率退化曲线（§5.5 的空白）。
2. **修掉 `tests/` 全目录混跑冲突**（§6.2），否则 CI 会长期靠"拆开跑"绕过。

---

## 9. 纪律自查

| 要求 | 状态 |
|---|---|
| 只在 mission worktree 分支工作 | ✅ `agent/m204` |
| 不 push | ✅ 全程未 push（无远端写入） |
| 不碰 GPU | ✅ 纯 CPU、纯文本处理 |
| 不修改 m200 目录 | ✅ 只读；`git -C .../m200/TensorPlatform status --short` 为空 |
| 不复制改造 Soul / 既有执行链代码 | ✅ `merge-base..HEAD -- backend tools/agent tools/factory` 零差异（机读判据 `existing_chain_untouched`） |
| 不写 Soul 表 | ✅ 本阶段不连数据库 |
| 不做新符号语法/二进制编码/学习型编码 | ✅ 显式否决并记录理由（ADR §3.4） |
| 未执行项标 NOT RUN | ✅ §7 |
| 无复现测量不得声称压缩率 | ✅ §3.3 的 10.9× 由现场分词得出，且限定适用范围 |
| 判据数字当场读工件抄录 | ✅ 全部来自 `ail_stage12_validation.json` / `artifacts/ail_benchmark.json` |
| 放在隔离实验路径 + feature flag | ✅ `tools/ail/` 独立包，不 import 任何 backend 模块；flag 留待阶段三 B |