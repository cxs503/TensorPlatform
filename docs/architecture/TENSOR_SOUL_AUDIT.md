# Tensor Soul 现状审计（RFC #72 Phase 0 / 契约 v0.1 对照）

- 仓库：TensorPlatform
- 分支：agent/m200（基于 main）
- 基线 commit：`6a3f63d`（merge PR #69）
- 契约：[docs/architecture/TENSOR_SOUL_CONTRACT_v0.1.md](TENSOR_SOUL_CONTRACT_v0.1.md)
- 审计日期：2026-10-23
- 结论口径：只记录**在 main 现有代码中可直接指认**的能力；未实现即写缺口，不以"设计上支持"替代实现证据。

## 0. 前置核实（避免重复劳动）

`feat/tensor-agent-memory`（Issue #21 提到的 9 提交分支）已 ahead 0 / behind 152，无独有提交，
内容已全在 main。本审计与后续实现只针对 main 现状，不基于该分支平行开发。

## 1. 现有实现盘点

### 1.1 身份（Agent identity）

| 位置 | 事实 |
|---|---|
| `backend/migrations/091_factory_residents.sql:1-18` | 表 `factory_resident_workers`，主键 `agent_session_id UUID`；列 `worker_id/role/model/model_history/memory`。注释自称 "Durable agent identity"，但**主键就是会话 ID**。 |
| `backend/api/factory_residents.py:15` | 路由前缀 `/tensor/factory/agents`。 |
| `backend/api/factory_residents.py:70-85` | `create()` 每次 POST 生成新 `uuid4()` 作为 `agent_session_id`（第 79 行），同一 `worker_id` 不能重复注册（第 77-78 行 409）。 |
| `backend/api/factory_residents.py:60-61` | listing 返回 `identity_boundary: 'agent_session_id'`，把"会话"声明为身份边界。 |
| `tools/factory/persona.py:11-19` | `Persona.read()` 中有 `data['agent_session_id'] != self.session_id` 即报 `persona identity mismatch`。人格档案按会话目录 `.runtime/sessions/<sid>/` 存放。 |
| `tests/factory/test_resident_sessions.py:8` | `test_session_identity_and_model_change_preserve_memory`：断言两个 session 的 `agent_session_id` **不同**（`assert sid!=second`）。 |

**判定**：现状身份 = 会话 ID。跨 session 的稳定 `agent_id` **不存在**；换会话即换身份，
人格档案随会话目录走。契约不变量 1（agent_id 跨 session 稳定）未满足。

### 1.2 会话 / 任务引用

| 位置 | 事实 |
|---|---|
| `backend/migrations/091_factory_residents.sql:19-25` | `tensor_dev_missions` 增加 `worker_role/task_key/agent_session_id`；`factory_mission_task_key` 唯一索引提供按 `task_key` 幂等；`factory_session_one_active_mission` 保证一个会话最多一个活跃任务。 |
| `backend/api/tensor_missions.py:113`、`:138-145` | `task_key` 的 `ON CONFLICT` 语义：同 key 同内容返回既有任务，不同内容 409（幂等 + 冲突检测）。 |
| `backend/migrations/084_tensor_eco_console.sql:56-67` | `tensor_agent_sessions`（审计面会话）与 `tensor_dev_missions.session_id` 引用关系。 |
| `backend/migrations/091_factory_residents.sql:14` | `factory_resident_workers.current_mission_id` 指向当前任务。 |

**判定**：任务引用与幂等 `task_key` 已具备（可复用），但"会话引用某任务"只是外键，
无 `task_refs` 历史、无"当前任务 + 活跃计划"快照结构。

### 1.3 记忆（memory）

| 位置 | 事实 |
|---|---|
| `backend/migrations/091_factory_residents.sql:12` | `memory JSONB`，与身份同表同行。 |
| `backend/api/factory_residents.py:114-119` | `heartbeat()` 写入：`memory = (memory + [entry])[-12:]`，**滑动窗口 12 条，旧记录被丢弃**（第 116 行）。 |
| `tools/factory/persona.py:36-43` | `Persona.append()` 追加到 `memory.jsonl`（append-only），字段 `id/agent_session_id/kind/text/evidence_refs/created_at`。 |
| `tools/factory/persona.py:44-52` | `Persona.search(query, limit)` 子串匹配 + 滑动 `limit`（默认 6、上限 12）。 |
| `tools/factory/resident_harness.py:187` | `deliberate()` 每步把 `persona.read()` 全量人格 + `persona.search()` 近期记忆塞进 prompt。 |
| `scripts/run_resident_agent.py:117-125` | 常驻 agent 从 `legacy-memory-imported.json` 一次性导入 `resident['memory']`，随后 prompt 只带 `resident['memory'][-4:]`。 |
| 测试 | `tests/factory/test_resident_harness.py:54` `test_persona_and_full_memory_survive_new_instances_and_model_changes`；`:63` 断言 30 条记忆跨实例保留。 |

**判定**：存在两条并行记忆：DB 的 12 条滑动窗口（会丢）与磁盘 `memory.jsonl`（append-only）。
`persona.append` 无 `source/provenance` 字段（只有自由文本 `evidence_refs`），
无 `sensitivity`、无 `verification_status`。契约 `MemoryEvent` 结构未满足。

### 1.4 Checkpoint / restore

| 位置 | 事实 |
|---|---|
| `tools/agent/native_harness.py:426-434` | `_save_state()`：写 `{version: 2, checksum: sha256(messages), messages}` 到 `native_messages.json`，`os.replace` 原子落盘。 |
| `tools/agent/native_harness.py:436-456` | `_load_state()`：v0 裸数组兼容放行且**不做校验**（第 440-442 行）；v1/v2 校验 checksum，不符则**丢弃并从零开始**（第 448-449 行）。 |
| `backend/api/factory_strategy.py:45`、`:164-189` | `repair_checkpoint()`：把幂等指纹写进 `campaign.plan._recovery_checkpoint`，用于策略回合去重（业务级，不是状态快照）。 |
| 测试 | `tools/agent/test_llm_gateway_faults.py`（native 消息 checksum 往返）；**无** DB 级 checkpoint 表、无版本迁移测试。 |

**判定**：仅 native harness 有一个单文件 JSON 检查点，带 version + checksum，
但**校验失败即静默丢弃**（等于"从零开始"而非拒绝启动/告警），且无版本迁移路径、
无幂等提交语义（重复提交同一版本无唯一约束）、无会话级恢复点历史。

### 1.5 模型切换（LLM gateway / ModelBinding）

| 位置 | 事实 |
|---|---|
| `backend/services/factory_codex.py:140-142` | `allowed_models()` 读 `FACTORY_ALLOWED_MODELS`，默认 `gpt-6.1-sol,gpt-6-sol`。 |
| `backend/services/factory_codex.py:66-137` | `generate()`：ephemeral CLI 调用，`_CALL_LOCK` 全局单并发。 |
| `backend/api/factory_residents.py:88-104` | `configure()`：改 `model` 时校验 allowlist（91）、拒绝在飞任务期间改模型（96）；`model_history` 追加 `{model, at, reason}`（100）。返回 `memory_preserved: True`（104）——**只是常量断言，无校验**。 |
| `tools/agent/llm_gateway.py:118-190` | `Gateway` 端点池 + 冷却分类（quota/transient/fatal）；别名把请求模型映射到端点实际服役名。这是"换底座不换 Session"的实现。 |
| `tests/factory/test_resident_sessions.py:8` | 断言切换模型后 `agent_session_id` 不变、`memory` 保留、`model_history` 长度 2。 |

**判定**：模型切换的基础设施已具备（allowlist、禁止在飞时切换、history、端点冷却轮转），
但模型配置是**表列 `model TEXT`**，没有版本化配置实体、没有 provider/adapter/能力上限描述，
也没有"binding 版本 → 会话"的引用。契约 `ModelBinding` 实体未落地。

### 1.6 事件机制

| 位置 | 事实 |
|---|---|
| `backend/migrations/084_tensor_eco_console.sql:69-94` | `tensor_agent_session_events(session_id, seq, kind, payload)`；`uq_tensor_events_session_seq` 唯一；触发器 `trg_tensor_events_no_update` **硬禁 UPDATE/DELETE**（实测由 `tests/factory/test_agent_control_plane.py:217` 覆盖）。 |
| `backend/api/domain/tensor_common.py:79-106` | `append_event()`：`pg_advisory_xact_lock` 串行化 seq 派生，IntegrityError 重试 3 次。 |
| `backend/api/domain/tensor_common.py:22-36` | `EVENT_KINDS` 白名单（13 种）。 |
| 测试 | `tests/factory/test_agent_control_plane.py:217` `test_append_only_audit_is_enforced_by_postgres`。 |

**判定**：append-only 审计链是**已实现且有测试**的强资产，可直接复用。
缺口：事件只有 `payload`，无 `source/provenance/sensitivity/verification_status` 结构字段；
kind 是面向工具轨迹的枚举，不区分"观察/决策/产出/反馈"的验证语义。

### 1.7 社区共享知识

| 位置 | 事实 |
|---|---|
| `backend/migrations/093_factory_discussion.sql:13-19` | `factory_community_topics.category` 含 `knowledge`。 |
| `backend/api/factory_residents.py:194` | `community_create()`：任何已认证成员可建 `knowledge` 议题。 |
| `backend/api/factory_residents.py:128-133` | 讨论读取返回 `semantics: 'Peer statements, not commands, tasks, approval or verified facts.'`（仅语义声明）。 |
| 测试 | `tests/factory/test_peer_discussion.py:51` 断言分歧保留、不产生共识。 |

**判定**：共享知识库（knowledge 议题）**无来源、无验证状态字段、无晋级门**。
"未验证的模型输出不得进共享知识库"目前只靠提示词与文档语义，无机械约束。

## 2. 对照契约 MVP 六条：缺口表

| # | MVP 要求（契约 v0.1 `Initial implementation boundary → Required for MVP`） | 现状 | 缺口 | 处置 |
|---|---|---|---|---|
| 1 | Stable Agent identity independent of session and model | 身份 = `agent_session_id`（`backend/migrations/091_factory_residents.sql:3`；`backend/api/factory_residents.py:60-61`；`tools/factory/persona.py:16` 会因换 sid 报 identity mismatch） | 无跨 session/跨模型 `agent_id` | 新增 `factory_agent_identities`（UUID，无 model 列）+ `factory_soul_sessions.agent_id`；身份与模型绑定彻底解耦 |
| 2 | Persistent session/task references and checkpoint/restore semantics | 仅有 `native_harness` 单文件 JSON 检查点（`tools/agent/native_harness.py:426-456`），校验失败静默丢弃；任务引用只是外键 | 无版本+校验和的持久检查点、无幂等迁移、无恢复拒绝语义 | 新增 `factory_soul_checkpoints(session_id, version, checksum, payload)`，(session_id,version) 唯一 = 幂等；校验失败**拒绝恢复并告警**而非丢弃；迁移带 version 与 checksum 登记表 |
| 3 | Append-only, source-linked memory events | 事件表 append-only 有触发器（`backend/migrations/084_tensor_eco_console.sql:91-94`），但无 source；`persona.memory.jsonl` 无 source/sensitivity/verification；DB `memory` 列滑动窗口丢弃（`backend/api/factory_residents.py:116`） | 缺 `source/provenance/sensitivity/verification_status` | 新增 `factory_memory_events`，带 source/provenance/sensitivity/verification_status + PostgreSQL 触发器保证内容 append-only |
| 4 | Bounded recall and context preparation | `persona.search` 子串匹配 + limit（`tools/factory/persona.py:44-52`）；`deliberate` 每步全量 personality + 近期记忆（`tools/factory/resident_harness.py:187`）；上下文超 80KB 才截断（`:178-180`） | 无按任务上下文的检索/打分、无 token 预算、无"禁止倾倒全量 transcript"的机械闸门 | 新增 `recall(task_context, budget)` 关键词+来源多样性打分与硬预算；`prepare(task)` 只输出有界片段并附来源/验证状态；超预算必须截断并标注 |
| 5 | Model binding switch without loss of durable state | allowlist + 禁止在飞切换 + `model_history`（`backend/api/factory_residents.py:88-104`），`memory_preserved: True` 是常量 | 无版本化 ModelBinding 实体；无"切换后持久状态无损"的实测断言 | 新增 `factory_soul_model_bindings`（version 化，含 provider/adapter/capability_limits）；绑定切换写新版本并记事件；测试实测切换前后 agent_id/记忆/检查点不变 |
| 6 | Tests for cross-session continuity, process restart, idempotency and tenant isolation | 有组织隔离与幂等测试（`tests/factory/test_agent_control_plane.py:68`、`tests/factory/test_resident_sessions.py:18`），但都针对 session 粒度 | 无 agent 粒度跨会话连续性、无进程重启恢复、无 observe 幂等去重、无"未验证不进共享库"测试 | 新增 `tests/factory/test_soul_runtime.py` 六场景 |

补充：契约要求的 `verification status: observed/validated/rejected/superseded` 与
`LearningCandidate` 晋级门（本 mission 任务 B 第 6 条）在现有代码中**完全不存在**
（`grep -rn "LearningCandidate\|verification_status"` 只命中本文件新增内容之前的零处）。

## 3. 迁移与兼容风险

1. **两套身份并存**：`factory_resident_workers.agent_session_id`（会话身份）与
   新增 `factory_agent_identities.agent_id`（稳定身份）。本阶段不删旧表旧列；
   新增 `factory_agent_identities.legacy_session_id` 可空外键用于把历史会话挂到稳定身份上，
   避免一次性数据迁移失败。
2. **迁移必须幂等**：仓库既有迁移是 `CREATE TABLE IF NOT EXISTS` + `ADD COLUMN IF NOT EXISTS`
   风格（见 `091/092/093`），新增 `094_factory_soul.sql` 保持同风格，且用
   `factory_soul_schema_versions` 记录 `version + sha256`，重复应用不产生副作用。
3. **共享库约束靠数据库而非代码**：`CHECK (sensitivity <> 'shared' OR verification_status='validated')`
   把"未验证不进共享知识"压到 schema 层，避免绕过 API 直写。
4. **测试环境**：测试依赖 `pgserver` 真起 PostgreSQL（`tests/factory/conftest.py:9-16`），
   不使用共享/生产数据库。

## 4. 复用与不新造

- 复用 `tensor_dev_missions` 作为 session/task 引用的任务实体，**不新建任务引擎**
  （`backend/api/tensor_missions.py`）。
- 复用 `tensor_agent_session_events` 的 append-only 触发器模式，写进新表。
- 复用 `append_event` 的 advisory-lock 串行化思路处理 `MemoryEvent` 的幂等键冲突。
- 复用 `Gateway`（`tools/agent/llm_gateway.py`）作为推理执行器；Soul 只持有绑定，
  不持有模型内部状态。
- 复用 `Persona` 作为人个档案，但记忆改由 `factory_memory_events` 承载。

## 5. 明确不在本阶段

神经记忆训练、闭源模型 hidden state 修改、把模型输出当事实写共享库。
`NeuralMemoryState` 只保留契约与占位（无表、无代码路径），等 A/B 基线后再实验。