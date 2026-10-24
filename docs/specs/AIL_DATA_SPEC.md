# AIL 数据规范 v1.0（AI-native context Language — memory representation）

- 状态：**v1.0，隔离实验路径内生效**（`tools/ail/`）；未接入正式工作流
- 日期：2026-10-23
- 关联：[ADR-001](../adr/AIL_ADR_001.md)、[基准协议](AIL_BENCHMARK_PROTOCOL.md)、
  [阶段 1-2 报告](../architecture/AIL_STAGE12_REPORT.md)
- 上位契约：[Tensor Soul Contract v0.1](../architecture/TENSOR_SOUL_CONTRACT_v0.1.md)（Soul 拥有事实，本文档只定义**视图**）

## 0. 本文档的边界

- 定义：**MemoryItem / Relation / EvidenceRef / SessionState / MemoryUpdate** 五个实体的字段、
  **意图感知检索**的输入输出、**来源追踪**、**状态冲突处理**、**版本规则**、**canonical JSON** 序列化。
- 不定义：新符号语法、二进制编码、学习型编码、tokenizer 替换（见 ADR-001 §3.4）。
- 不产生事实：本层的任何结构都是**派生视图**。删除全部 AIL 工件不损失平台事实；
  权威来源仍是 mission / event / 工件文件（加上 Soul 落地后的 `factory_memory_events`）。

## 1. 术语与不变量

| 术语 | 定义 |
|---|---|
| 记录（record） | 平台已有的事实载体：mission 行、session 事件、JSON 工件、commit。**AIL 不拥有它** |
| 条目（MemoryItem） | 从记录中**抽取**的一条可判分断言（键 `topic` + 值 `value` + 单行 `text`） |
| 证据（EvidenceRef） | 条目的来源锚点（locator + sha256 + 可选摘录） |
| 束（AILBundle） | 一个 agent 会话在某一时刻的完整 AIL 视图（条目 + 关系 + 会话状态 + 更新） |

四条不变量（实现与测试都必须守住）：

1. **I1 可重建**：任意 AILBundle 可由记录集重新派生，且同输入产生**逐字节相同**的 canonical 字节
   （因此 `bundle_checksum` 可复算）。
2. **I2 零回写**：AIL 组件不得写、改、删任何记录；不得写 Soul 事件表。
3. **I3 不越权验证**：AIL 不得把 `verification.status` 上调（`unknown`→`passed` 或 `observed`→`passed`）。
   它只能**降权/过滤**。
4. **I4 不静默择一**：同一 `topic` 存在互相矛盾的 active 条目时，检索结果必须**同时**返回它们并置
   `conflict=true`；装配器不得静默丢弃其一。

## 2. 通用约定

- 编码：UTF-8，无 BOM。
- 时间：ISO-8601 UTC，秒精度，必须以 `Z` 结尾（例：`2026-10-23T06:20:49Z`）。禁止本地时区偏移。
- 数字：整数字段不得用浮点表示（`16` 而非 `16.0`）；禁 `NaN`/`Infinity`。
- ID：见 §7 的确定性 ID 规则（`mi_`/`ev_`/`rel_` 前缀 + 小写十六进制）。
- 长度上限一律按 **Unicode 码点**计，超限即 **拒绝**（不是截断）——截断会静默改变语义。
- 未列出的字段：**禁止**（`additionalProperties: false` 口径）。向前兼容靠 §8 的版本规则。

## 3. EvidenceRef

```json
{
  "evidence_id": "ev_9f2c1a3b4d5e6f70",
  "kind": "git_commit",
  "locator": "git:cxs503/TensorPlatform@495f976",
  "sha256": null,
  "excerpt": "Merge pull request #70 from cxs503/agent/m196",
  "retrieved_at": "2026-10-23T06:40:00Z"
}
```

| 字段 | 类型 | 约束 |
|---|---|---|
| `evidence_id` | string | `^ev_[0-9a-f]{16}$`，由 §7 派生 |
| `kind` | enum | `git_commit` \| `file` \| `json_pointer` \| `artifact` \| `mission` \| `test_run` \| `url` |
| `locator` | string | 1..300 码点，**单行**；`file` 用 `path`，`json_pointer` 用 `path#/a/b` |
| `sha256` | string \| null | 64 位小写十六进制；`file`/`artifact` **必须**给（可核验），`git_commit`/`mission` 可空 |
| `excerpt` | string \| null | ≤ 300 码点**单行**，不得含换行（含换行视为拒绝） |
| `retrieved_at` | string | ISO-8601 UTC |

`sha256` 的语义是"本层读取该 locator 时内容的哈希"。读取后若文件变化，哈希不匹配即视为**陈旧**，
必须重新抽取（不允许沿用旧哈希"补签"）。

## 4. MemoryItem

```json
{
  "item_id": "mi_1b7f0a5c9d2e3f41",
  "kind": "state",
  "topic": "platform.guardian.count_identity",
  "value": 16,
  "text": "守护进程实测 16 = 基线 6 + 池化槽 10，6 模型全覆盖",
  "status": "active",
  "verification": {"status": "passed", "validator": "operator_log/verify_2026-10-23.py", "checked_at": "2026-10-23T06:40:00Z"},
  "source": {"kind": "json_pointer", "locator": "operator_log/2026-10-23.m192.readings.json#/2_guardian/pgrep_total", "sha256": "<64hex>", "retrieved_at": "2026-10-23T06:40:00Z"},
  "evidence_refs": ["ev_9f2c1a3b4d5e6f70"],
  "scope": {"agent_id": null, "session_id": null, "mission_id": 192},
  "sensitivity": "organization",
  "valid_from": "2026-10-22T17:16:48Z",
  "valid_to": null,
  "recorded_at": "2026-10-23T06:40:00Z",
  "revision": 1,
  "supersedes": [],
  "salience": 80,
  "idempotency_key": "operator_log/2026-10-23.m192.readings.json#/2_guardian/pgrep_total",
  "tags": ["guardian", "infra"]
}
```

### 4.1 `kind`（断言类型，决定检索权重与装配位次）

| kind | 含义 | 装配位次 |
|---|---|---|
| `state` | 当前状态事实（数值/状态机） | L3 |
| `next_action` | 下一步动作（含未完成项） | L1 |
| `decision` | 显式做出的选择及其理由 | L2 |
| `evidence` | 证据/判据数字 | L3 |
| `constraint` | 约束与禁止项（边界、纪律） | L1 |
| `fact` | 一般事实（非状态、非决策） | L3 |
| `lesson` | 教训（驳回原因、坑） | L2 |
| `open_question` | 未解问题 | L1 |

扩展：允许 `x-` 前缀自定义 kind；未知且非 `x-` 前缀的 kind 必须**拒绝**（防拼写漂移）。

### 4.2 `topic`

- 形如 `<namespace>.<segment>(.<segment>)*`，全小写 ASCII + `_` + `.`，段内禁止空白；
  正则 `^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$`，总长 ≤ 120。
- `topic` 是**判分与冲突检测的主键维度**："同一主题只能有一个当前有效值"，冲突处理见 §10。
- 中文内容不进 topic（进 `text`），避免分词不稳定。

### 4.3 `value`

- 类型：`string` \| `number` \| `boolean` \| `null` \| 上述标量的**同质数组**（≤ 16 项）。
- `string` ≤ 120 码点**单行**。禁止嵌套对象（嵌套=应该拆成多条或放 `text`）。
- 用途：让 "16"、"passed"、"0.213707" 这类**必须逐字精确**的内容脱离叙述文本。

### 4.4 `text`

- 1..400 码点，**单行**（禁止 `\n`；多句用 `；` 分隔）。
- 必须**自足**：脱离上下文读这一行也能判断真假（不允许"同上"、"见上文"）。

### 4.5 `status`

`active` \| `superseded` \| `retired`。

- `superseded` 只能由**显式** `MemoryUpdate{op:"supersede"}` 置位，且必须指出后继条目（§6.2）。
- `retired` 表示主观作废（例如结论被推翻但未必有后继）。
- 检索默认只返回 `active`；`superseded`/`retired` 仅在被显式要求历史视图时返回（用于回答
  "当时为什么那样做"）。

### 4.6 `verification`

```json
{"status": "passed", "validator": "operator_log/verify_2026-10-23.py", "checked_at": "2026-10-23T06:40:00Z"}
```

- 三态（Issue #74 要求）：`passed` / `failed` / `unknown` —— **`unknown` 绝不等于 `failed`**，
  渲染时必须带不确定标记（`?`）。
- `observed` 表示"模型或工具的原始输出，尚未独立校验"（对齐 Soul 契约不变量 4：模型输出是观察不是事实）。
  渲染标记为 `~`；`failed` 标记为 `!`；`passed` 无标记。
- `passed`/`failed` **必须**带 `validator` 与至少 1 条 `evidence_refs`；`observed`/`unknown` 不强制
  （否则等于"没有验证器却声称已判定"）。
- 只有 `passed` 且 `validator` 非空才允许 `sensitivity="shared"`（对齐 Soul 的 CHECK 约束口径）。
- AIL 侧**不得**将其上调（I3）。

### 4.7 `source` 与 `evidence_refs`

- `source`：**抽取来源**（哪个文件/接口的哪个指针）。一条条目恰好一个 `source`。
- `evidence_refs`：**支撑证据**（0..8 个 `evidence_id`）。可为空（如纯推断）。
  但 `verification.status ∈ {passed, failed}` 时**必须**至少 1 条，且 `verification.validator`
  必须非空（"判定"必须有验证器；"观察"`observed` 允许没有独立验证器）。
- 二者都要写入；`source` 保证"这条是从哪生成的"，`evidence_refs` 保证"怎么核对的"。

### 4.8 `scope` / `sensitivity`

- `scope`：`agent_id`/`session_id`/`mission_id` 可为 `null`（= 不限）。检索按 `scope` 做硬过滤，
  **不允许**跨 scope 泄漏（对齐 Soul 契约验收项 5）。
- `sensitivity`：`private` \| `organization` \| `shared`，语义与 Soul 一致。

### 4.9 `salience`

整数 0..100，表示"该条目对恢复工作状态的相对重要性"，由转换器按 kind 与判据重要性给出
**确定性**取值（同输入同输出）。仅用于预算不足时的**降级顺序**，不得用来在冲突中择一（I4）。

### 4.10 `idempotency_key`

≤ 200 码点单行。同 `idempotency_key` 重复入场：
- 内容 canonical 字节一致 → 幂等（返回既有条目，`revision` 不变）；
- 内容不同 → **冲突**（拒绝并要求用 `MemoryUpdate{op:"add"}` 显式新增版本）。

## 5. Relation

```json
{"relation_id": "rel_2c9d8e7f6a5b4c3d", "type": "supports", "from_id": "mi_1b7f0a5c9d2e3f41", "to_id": "ev_9f2c1a3b4d5e6f70", "note": "", "created_at": "2026-10-23T06:40:00Z"}
```

| 字段 | 约束 |
|---|---|
| `relation_id` | `^rel_[0-9a-f]{16}$` |
| `type` | `supports` \| `contradicts` \| `supersedes` \| `derived_from` \| `refers_to` \| `blocks` |
| `from_id` / `to_id` | 必须指向同一 bundle 内存在的 `item_id` 或 `evidence_id`；`from_id != to_id` |
| `note` | ≤ 200 码点单行 |

- `supersedes` 关系与 `MemoryItem.supersedes` 必须**双向一致**（转换器负责，测试校验）。
- `contradicts` 是**保留**信息：矛盾不是错误，是有价值的信号；检索据此置 `conflict=true`。

## 6. SessionState 与 MemoryUpdate

### 6.1 SessionState

```json
{
  "session_id": "s-m204",
  "agent_id": null,
  "mission_ref": {"mission_id": 204, "task_key": "AIL:stage1-2"},
  "phase": "executing",
  "status_summary": "阶段一文档在写；原型未开始",
  "active_plan": [{"step_id": "p1", "text": "提交 ADR/SPEC", "status": "active"}],
  "next_actions": [{"action_id": "n1", "text": "实现转换器", "preconditions": ["SPEC 冻结"], "owner": "agent"}],
  "blockers": [],
  "open_questions": [{"question_id": "q1", "text": "是否需要测真实 LLMLingua？"}],
  "budget": {"tool_calls_used": 40, "wall_seconds_used": 1800, "token_budget": 2000},
  "checkpoint_ref": null,
  "revision": 3,
  "updated_at": "2026-10-23T06:40:00Z"
}
```

- `phase`：`planning` \| `executing` \| `blocked` \| `awaiting_acceptance` \| `done`。
- `checkpoint_ref`：`{session_id, version, checksum}` 或 `null`——**引用** Soul 的
  `factory_soul_checkpoints`，AIL 不复制其内容。
- 各数组上限：`active_plan` ≤ 12、`next_actions` ≤ 8、`blockers` ≤ 8、`open_questions` ≤ 8。
- `status_summary` ≤ 400 码点单行。
- `plan.status` / `action`**无** `status` 字段（动作的存在即"未完成"；完成即从列表移除并记一条
  `MemoryUpdate`）。

### 6.2 MemoryUpdate

```json
{"op": "supersede", "target_id": "mi_1b7f0a5c9d2e3f41", "successor_id": "mi_aa11bb22cc33dd44", "reason": "m203 实测 16 覆盖 m200 的 14", "actor": "m204", "at": "2026-10-23T06:40:00Z", "idempotency_key": "upd-2026-10-23-1"}
```

| `op` | 语义 | 必填 |
|---|---|---|
| `add` | 新增条目（含新版本） | `item` |
| `supersede` | 旧条目标 `superseded`，指向后继 | `target_id`, `successor_id`, `reason` |
| `retire` | 旧条目标 `retired`（无后继） | `target_id`, `reason` |
| `attest` | 补充证据（只增 `evidence_refs`） | `target_id`, `evidence_id` |
| `reverify` | 重新校验后更新 `verification`（只允许带 `validator` 的保守方向） | `target_id`, `verification` |
| `noop` | 显式声明"本次无更新"（有意义的空操作，便于审计） | — |

- 任何 `op` 都不得删除条目（append-only 视图语义）。
- `reverify` 的"不许上调"由代码强制：`unknown|observed|failed → passed` 一律拒绝，
  除非 `verification.validator` 非空**且**把 `evidence_refs` 从空变为非空。

## 7. 确定性 ID 规则

```
item_id    = "mi_"  + sha256(canonical({"kind":kind,"topic":topic,"value":value,"scope":scope,"idempotency_key":key}))[:16]
evidence_id= "ev_"  + sha256(canonical({"kind":kind,"locator":locator,"sha256":sha256}))[:16]
relation_id= "rel_" + sha256(canonical({"type":type,"from_id":from,"to_id":to}))[:16]
bundle_checksum = sha256(canonical(bundle 去掉 checksum 字段))       # 64 hex
```

- 确定性 ID 让"同记录重跑"逐字幂等（不变量 I1 的实现基础），也让基准可复现。
- 冲突处理：若两条内容不同的条目产生同一 `item_id`，说明 canonical 输入相同而其他字段不同
  （例如 `text` 不同）——此时**首见者胜**，后者必须改 `idempotency_key` 或走 `add`。

## 8. 版本规则

| 规则 | 内容 |
|---|---|
| R1 | 包级 `schema_version` 为**单整数**，当前 `1`。字段级语义变更/删除/类型收紧 → `+1`（major，破坏性） |
| R2 | 新增**可选**字段 → 不升 `schema_version`，但必须在本文档登记为 "added in v1.x"；旧读者必须忽略未知字段（而非报错） |
| R3 | 枚举新增值 → 视同破坏性变更（旧读者无法正确处理），必须升 major；例外：`x-` 前缀扩展值不需升版 |
| R4 | 读者遇到**大于自身**的 `schema_version` → **拒绝处理**并给出明确错误（不猜） |
| R5 | 读者遇到**小于自身**的 `schema_version` → 必须尝试读取；缺字段用默认值（`null`/`[]`/`0`），并在 `MigrationReport` 记录补了哪些默认 |
| R6 | 迁移不得改写历史条目；只能新增条目或用 `MemoryUpdate` 表达变更 |
| R7 | 每条 AIL 产物必须同时记录生成它的 `ail_version`（如 `"1.0"`）、转换器版本与输入记录的 sha256 清单，缺一即视为不可复现 |

## 9. Canonical JSON 序列化规范

对齐 Soul 的 `canonical()`（`backend/services/soul_runtime.py`，m200），保证两侧哈希可互认：

```python
canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False)
```

强制项：

1. **键序**：`sort_keys=True`（按 Unicode 码点序）。
2. **分隔符**：`","` 与 `":"`，**无空格**。
3. **非 ASCII**：原样输出（`ensure_ascii=False`），不转 `\uXXXX`。UTF-8 编码落盘。
4. **数值**：整数不带小数点；浮点用最短往返表示；禁 `NaN`/`Infinity`（`allow_nan=False`）。
5. **空值**：可选字段一律**显式写 `null`**，不得省略（省略会让 checksum 随实现漂移）。
   数组为空写 `[]`。
6. **行序**：JSONL 中条目按 `(kind_order, -salience, item_id)` 排序，`kind_order` 取 §4.1 表序。
   这样"同一集合"必然产生同一字节流，与输入文件顺序无关。
7. **落盘**：UTF-8、无 BOM、`\n` 换行（含最后一行）、无行尾空白。
8. **校验和**：`checksum = sha256(canonical(bundle_without_checksum))`；
   校验不符必须**拒绝**（`AILChecksumError`），**不允许**静默重建。

## 10. 状态冲突处理（正式规则）

| 情形 | 判定 | 处置 |
|---|---|---|
| C1 同 `topic`、同 `scope`、不同 `value`，两条都 `active` | **矛盾** | 两条都返回，结果置 `conflict=true`，并给出各自 `evidence_refs`；装配器把二者相邻渲染，标注 `!CONFLICT` |
| C2 时间上不重叠（旧 `valid_to` ≤ 新 `valid_from`） | **演进**，非矛盾 | 旧条目自动视为历史，不需要 `supersede`；"当前视图"只给新条目 |
| C3 同 `topic` 同值但证据不同 | **重复** | 合并 `evidence_refs`（去重），保留较早 `item_id` |
| C4 `verification.status` 相互矛盾（一条 passed 一条 failed） | **未决** | C1 的强化形态：`conflict=true` **且** `unresolved=true`；不给"当前值"结论 |
| C5 新条目声称取代旧条目但没有 `MemoryUpdate{op:supersede}` | **不取代** | 旧条目继续 `active`，并生成 C1 冲突（禁止隐式取代） |
| C6 引用不存在的 `target_id` | **错误** | 拒绝该 `MemoryUpdate`，整个提交回滚（不部分应用） |
| C7 `source.sha256` 与当前文件不符 | **陈旧** | 条目保留但标 `stale=true`，检索降权；必须重抽取才能恢复满权重 |

## 11. 意图感知检索（intent-aware retrieval）

### 11.1 输入

```json
{"intent": "state", "query": "guardian 槽位",
 "budget": {"max_items": 12, "max_tokens": 600},
 "include_superseded": false, "include_failed": false,
 "scope_filter": {"mission_id": 192}}
```

`intent` ∈ `state` \| `decision` \| `evidence` \| `next_action` \| `recall`。

### 11.2 打分（确定性，无随机、无外部模型）

```
score = lex(query, item) * 0.55
      + kind_weight(intent, item.kind) * 0.25
      + recency(item.valid_from) * 0.10
      + verification_weight(item) * 0.10
      - stale_penalty - superseded_penalty

# 必要条件（非加权项）：query 非空且词法重叠为 0 时，score *= 0.5
# 理由：结构分（kind/recency/verification）是"先验"，不能把与本次意图无关的历史条目
# 抬进预算，否则唯一命中会被大量同构条目挤掉。实测依据见报告 §4（改动前后对比）。
```

- `lex`：ASCII 按 `[a-z0-9_]+` 切词 + CJK 二元组，去停用词后算覆盖度（∈[0,1]）。
- `kind_weight`：按意图给 kind 期望分布（如 `intent=next_action` 时 `next_action`=1.0、
  `constraint`=0.8、`state`=0.6、`evidence`=0.2）。
- `recency`：以会话 `now` 为基准的半衰期衰减（半衰期 7 天，确定性计算）。
- `verification_weight`：`passed`=1.0、`observed`=0.6、`unknown`=0.4、`failed`=0.1、`stale`×0.5。
- `superseded_penalty`：仅在 `include_superseded=true` 时生效（-0.3）。
- 平局用 `item_id` 升序**稳定**打破（保证可复现）。

### 11.3 输出

```json
{"intent": "state", "items": ["…"], "conflicts": [{"topic": "…", "item_ids": ["…", "…"]}],
 "unresolved": false, "dropped": [{"item_id": "…", "reason": "budget"}],
 "stats": {"scanned": 128, "returned": 9}}
```

- `dropped` 必须报告被预算挤掉的条目及原因（`budget`/`filtered_failed`/`filtered_superseded`），
  便于审计"到底丢了什么"。

### 11.4 硬约束

- 不做跨 `scope` 合并（契约验收项 5）。
- 不返回 `status="superseded"` 的条目，除非 `include_superseded=true`。
- 矛盾条目**一起**返回或**一起**不返回——I4。

## 12. 上下文装配（预算控制）

分层优先级（预算不足时从低层开始降级/丢弃）：

| 层 | 内容 | 降级方式 |
|---|---|---|
| L0 | `SessionState` 压缩行（phase / status_summary / budget） | 全文 → 仅 phase+status_summary |
| L1 | `next_action` + `constraint` + `open_question` | 全文 → 单行 → 仅 `item_id` 引用 |
| L2 | `decision` + `lesson` | 同上 |
| L3 | `evidence`（判据数字）+ `state` 具体值 | 单行（值+引用） → 仅引用 |
| L4 | 原文摘录（`evidence_refs[].excerpt`） | 整层可丢 |

- 装配**绝不超预算**：先按层装，装不下就降级本级，本级降到"仅引用"仍放不下则丢弃整级，
  并在 `AssemblyReport` 记录 `dropped_tiers` 与 `degraded`。
- 输出必须自带 `tokens_used` / `token_budget` / `tokenizer` 字段（口径透明）。

## 13. 与 Soul 的字段对照（实现切换时零改结构）

| AIL 字段 | Soul 对应 | 说明 |
|---|---|---|
| `verification.status` | `factory_memory_events.verification_status` | AIL 多一个 `unknown`（Soul 的 NULL 语义） |
| `sensitivity` | `factory_memory_events.sensitivity` | 逐字一致 |
| `source.kind='mission'` | `MemoryEvent.task_refs` | 指向 `tensor_dev_missions` |
| `scope.agent_id` | `factory_agent_identities.agent_id` | 只引用，不定义 |
| `checkpoint_ref` | `factory_soul_checkpoints(session_id,version,checksum)` | 只引用，不复制 |
| `idempotency_key` | `UNIQUE(organization_id,idempotency_key)` | 幂等口径一致 |

## 14. 附：最小合法 bundle 示例

见 `tools/ail/corpus/bundles/*.jsonl`（由 `scripts/run_ail_benchmark.py` 实测生成）
与 `tests/ail/test_ail_schema.py` 的 `MINIMAL_BUNDLE` 常量（用于 schema 自检）。