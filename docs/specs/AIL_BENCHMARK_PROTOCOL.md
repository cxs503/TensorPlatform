# AIL 基准协议 v1.0（四臂对比：token 成本 × 任务恢复准确率）

- 状态：**v1.0，隔离实验路径内生效**
- 日期：2026-10-23
- 关联：[ADR-001](../adr/AIL_ADR_001.md)、[数据规范](AIL_DATA_SPEC.md)、
  [阶段 1-2 报告](../architecture/AIL_STAGE12_REPORT.md)、[Issue #74](https://github.com/cxs503/TensorPlatform/issues/74)

## 1. 要回答的问题

> 在**同等 token 预算**下，哪种记忆表示能更准确地恢复智能体工作状态？

判据是二元组 **（token 成本，任务恢复准确率）**，不是单纯压缩比。
单独报压缩比是**禁止**的（Issue #74 明文），因为把上下文压成空串能拿到 ∞ 压缩比。

## 2. 判据：任务恢复四元组

每条探针（probe）问一个问题，答案必须能落到下面四类之一：

| 维度（quad） | 定义 | 例 |
|---|---|---|
| `state` | 工作状态：现在在哪个阶段、关键计数/门状态如何 | "守护进程实测多少"、'mission 状态是什么' |
| `decision` | 历史决策：做过什么选择、为什么 | "为什么先做语义层而非新语法" |
| `evidence` | 知识依据：判据来自哪个工件/哪次真跑 | "logger 16 这个数字出自哪个文件" |
| `next_action` | 下一步动作：待办、阻塞、验收条件 | "阶段二第一件事做什么" |

### 2.1 打分（逐项、机械、可复算）

每个探针在数据集里带一份 `gold`：

```json
{"probe_id": "p-state-guardian",
 "quad": "state",
 "question": "m192 班次实测守护进程数量、基线数与池化槽数分别是多少？",
 "gold": {"topic": "platform.guardian.count_identity", "value": 16,
          "must_contain": ["16"], "must_not_contain": ["14"],
          "evidence_locator": "operator_log/2026-10-23.m192.readings.json#/2_guardian/pgrep_total"}}
```

判分（`scorer.py`，确定性，无 LLM 参与）：

1. **可答性**（answerable，0/1）：给定该臂产出的上下文，`gold.topic` 对应的条目是否**在场**？
   - "在场"的机械定义：上下文中存在一个条目，其 `value` canonical 形式等于 `gold.value`
     **或**文本命中 `must_contain` 全部项且不命中任一 `must_not_contain` 项。
   - 这一层**不依赖模型**，因此完全可复现。
2. **逐字正确**（exact，0/1）：在场条目的 `value` 与 `gold.value` 严格相等
   （`16 != "16"`、`16 != 16.0`）。用于抓"看起来对但口径错"（如把 16 写成 6+10 没给总数）。
3. **依据可追**（traceable，0/1）：若探针指定 `evidence_locator`，该 locator 是否能在
   上下文的引用中出现（允许前缀/指针形式）？用于抓"答对了但不知道依据在哪"。
4. **无自信错误**（no_confident_error，0/1）：在场条目不与 `must_not_contain` 冲突，
   且若存在冲突条目，上下文必须同时带 `!CONFLICT` 标记。

**探针判分 = (answerable + exact + traceable + no_confident_error) / 4**，其中不适用于该探针的
子项（如 `evidence_locator` 为 null）按 **1.0 计但记账为 `n/a`**，并在报告里分别列出
"计入 n/a 的探针数"，不允许用 n/a 拉高分而不披露。

**臂的恢复准确率** = 全部探针判分的算术平均；同时**分维度**（state/decision/evidence/next_action）
各报一个平均。四个维度各报一个是硬要求——总分相同但维度分布不同，正是要看见的信息。

### 2.2 token 计量

- 分词器：`tiktoken` `cl100k_base`（GPT 系口径），**实测编码**，不是字符数估算。
- 若运行环境缺 tiktoken：**必须**在报告里标 `tokenizer: char_estimate` 并说明结论不可与
  tiktoken 口径比较（本仓库把 tiktoken 装在实验用 `.pydeps` 里，脚本会打印实际口径）。
- 计的是**喂给模型的上下文文本**的 token 数（不含 system prompt 与问题，问题两侧相同）。
- 同时报 `chars` 与 `tokens`，并给出 `tokens/char` 比值（用于让读者判断中文口径）。

## 3. 四臂（arms）

| 臂 | 定义 | 本阶段的实现 |
|---|---|---|
| **A0 原始历史**（raw） | 把记录原样拼进上下文（现状做法的直接形态） | 按时间顺序 dump 记录的关键字段 + 摘要行，无压缩 |
| **A1 自然语言摘要**（nl_summary） | 每条记录压成一句自然语言，**抽取式** | 确定性抽取：取每条记录首句/关键句 + 数字，拼成段落 |
| **A2 结构化 JSON**（json_struct） | 同样信息量，但 organised 成 JSON 数组（无 topic/value 语义、无 intent 检索） | `[{id,kind,text,ref}]` 全量给（按预算截断） |
| **A3 AIL** | 本文档定义的 MemoryItem + intent-aware 检索 + 分层装配 | `tools/ail/` |

A1 与 A2 是**下界**基线（它们比 A0 更省 token，但没有语义检索）；A3 要与它们比
**同预算下的准确率**，与 A0 比 **同等准确率下的 token**。

### 3.1 为什么没有直接跑真实 LLMLingua / SimpleMem

- 两者都需要模型权重/GPU 或外部服务；本任务明确**不碰 GPU**。
- 纪律（ADR-001 §5）：外部论文数字不得搬作本平台预期。
- 因此 LLMLingua 的定位是 **A1 的方法族参照**：本协议保留 `compact` 臂插入点
  （`tools/ail/arms.py::ARM_REGISTRY`），接入真实实现时判分协议不变。
  **报告中必须把这一点写成"未测"而不是"更好"。**

## 4. 预算档位

同一探针集在三档 token 预算下各跑一遍：

| 档 | 名 | 预算 | 目的 |
|---|---|---|---|
| T1 | tight | 400 tokens | 极紧：看谁在信息饥荒下仍能恢复核心状态 |
| T2 | normal | 1200 tokens | 常规：默认工作预算 |
| T3 | loose | 3000 tokens | 宽松：接近"全都给"的副本 |

A0 是**无预算**臂（它的成本是被测量对象），因此在预算档位下 A0 只记录其自然成本，
其准确率作为"上限参考"（理论上 A0 信息最全）。若 A3 在 T3 下已接近 A0 上限而成本远低，
即为本阶段的目标结论形态。

## 5. 数据来源（真实记录，三库混合）

| 语料集 | 来源（真实路径） | 内容 |
|---|---|---|
| `tensorplatform` | 本仓库 `operator_log/*.readings.json`、`*.validation_report.json`、`agent_memory/platform-operator/memory.jsonl`（只读） | 班次巡检读数、判据报告、终态事实记录 |
| `tensorphysics` | `/srv/tensor/shared/tensor_eco/TensorPhysics/outputs/wedge-icebreaking-s0/{research_goal,s0_report}.json` + `docs/SYSTEM_OVERVIEW.md` | 四腿求解器编排的 goal/report/门状态 |
| `tensordem` | `/srv/tensor/shared/tensor_eco/TensorDEM/{benchmarks/dem3d/*.json,docs/dem3d_*.md}` | 基准 case 定义、验收阈值、验证limitations |

每份语料在 `tools/ail/corpus/sources.json` 里登记：路径、sha256、抽取方式、只读声明。
抽取器**只读**这些路径；任何写操作都必须报错（`tests/ail/test_ail_corpus.py` 校验）。

## 6. 执行与取证

```bash
PYTHONPATH=<pydeps> /srv/tensor/shared/venvs/tensor_eco/bin/python scripts/run_ail_benchmark.py \
    --out artifacts/ail_benchmark.json
```

产物：

| 文件 | 内容 |
|---|---|
| `artifacts/ail_benchmark.json` | 机器判据工件：逐探针逐臂逐预算档的判分、token 数、总表 |
| `artifacts/ail_bundles/*.jsonl` | 各语料集转换出的 AIL bundle（canonical JSONL，带 checksum） |

报告里的每个数字都必须能在这个 json 里找到对应路径（`probes[i].arms[a].tiers[t]`）。

## 7. 禁止事项（写进验收）

1. 不得只报压缩比而不报准确率。
2. 不得把未执行的臂标成通过；未执行一律 `NOT RUN`。
3. 不得声称"下游任务成功率提升"——本阶段没有真实下游任务执行（属阶段三）。
4. 不得用 LLM 生成"标准答案"再对比：gold 全部来自真实工件的人工/机械抽取，且 `evidence_locator` 必须可核对。
5. 不得跨 scope 取数造出"更全"的上下文。

## 8. 结论的有效性边界（写进报告）

- 判分是**信息在场性**判分，不是 LLM 阅读理解判分：它回答"上下文里有没有正确且可追的依据"，
  不回答"某个模型能不能用它答对"。后者需真实模型调用，属阶段三。
- A1/A2 是确定性抽取的基线，**弱于**真实 LLMLingua/SimpleMem；因此"优于 A1/A2"不能外推成
  "优于 LLMLingua"。报告必须原样保留这句限定。
- 语料规模有限（三个仓库的真实工件快照），结论是**方向性的**，不是统计显著性的。
## 9. 延迟判据（阶段 2 增补，实测口径）

Issue #74 的验收判据含"延迟"。本阶段落地口径：

- **测什么**：`run_arm()` 的墙钟耗时（毫秒），即"从记录/ bundle 到最终上下文字符串"的纯本地耗时。
- **怎么测**：每个 (探针 × 臂 × 预算档) 单元格取样 **3 次**，取**中位数**写入 `latency_ms_median`，
  同时保留 `latency_ms_median`/`min`/`samples`，工件里可审计、可复算。
- **不含什么**：不含模型推理、不含网络、不含 DB 查询。因此本数字衡量的是**装配开销**，
  不是端到端延迟——不得当作端到端性能对外表述。
- **可比性**：四臂在同一进程、同一语料、同一探针下顺序执行，口径一致。
- **防造假**：`tests/ail/test_ail_benchmark.py::test_latency_measured_for_every_cell`
  断言每个单元格都有正数延迟且取样数=3；
  `test_ail_latency_same_order_as_raw` 断言 AIL 延迟不超 raw 的 10 倍。
