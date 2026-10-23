# Tensor Soul Runtime 第一阶段交付报告（RFC #72 / 契约 v0.1 MVP）

- 仓库：TensorPlatform
- 分支：`agent/m200`（基于 main `6a3f63d`；未推送，由收割线代推）
- 任务：mission m200「TENSOR-SOUL:phase1」
- 审计（任务 A 交付物）：[TENSOR_SOUL_AUDIT.md](TENSOR_SOUL_AUDIT.md)
- 机读判据工件：`soul_phase1_validation.json`（由 `scripts/verify_soul_phase1.py` 真实执行生成，本报告所有数字均抄自该文件）
- 报告日期：2026-10-23

> 口径：本阶段只声称**单元/集成测试层面**的实现与验证。部署验证与科学有效性**未做**，见第 5 节 NOT RUN。
> 契约 v0.1 本身是 proposal，本报告不改变该状态：它只报告"契约 MVP 六条在仓库中已有可执行实现与测试"。

## 1. commit SHA 清单（`6a3f63d..HEAD`，按序）

| # | 段 | commit | 说明 |
|---|---|---|---|
| 1 | A 审计 | `4faff59188f15b918b308b4b07f9573617773130` | docs: Tensor Soul 现状审计（RFC #72 Phase 0 对照契约 MVP 六条） |
| 2 | B 实现 | `5a1eae581ae74f61269a79cc2733c223d65cb5e1` | feat(soul): Tensor Soul Runtime 第一阶段 MVP（身份/检查点/记忆事件/recall/绑定/晋级门） |
| 3 | B 实现 | `274f8e8135794a5316f1ca689aeca0f25bead04f` | fix(soul): 091 会话→稳定身份迁移入口 + 迁移登记器接入 bootstrap |
| 4 | B/C 实现+取证 | `e6b364b0dfcc130bcecad365d29fb137ac97f0d1` | fix(soul): recall 中文任务上下文相关性（CJK 二元组）+ 有界 recall/prepare 测试 + 取证脚本 |
| 5 | C 报告 | `18f52def1a4209d1f395863ff8c32d2348b3ff46` | docs: Tensor Soul 第一阶段交付报告 + 取证脚本静态检查扩至报告工件 |
| 6 | C 工件 | `e24cd1d` | docs: 刷新 Soul 第一阶段取证工件（head_commit=18f52de，13/13 判据通过）并标注工件提交口径 |

段 6 之后还有若干**只改文档/笔记、不改代码**的收尾提交（`git log --oneline 18f52de..HEAD` 可列全，
含 `5a97649` 报告数字对齐与 `6bbc883` 交接笔记修正，以及写入本段落的这次提交）。
验收时请以 `git log --format=%H 6a3f63d..HEAD` 为准核对完整 SHA 清单；
代码未被这些收尾提交触碰，可用 `git diff 18f52de..HEAD -- backend tests tools scripts` 应为空来机械核对（本次核对结果：空）。

变更规模（`git diff --shortstat 6a3f63d..e6b364b` 原样抄录）：`11 files changed, 2206 insertions(+), 2 deletions(-)`（不含本报告）。
含本报告与取证工件后（`git diff --shortstat 6a3f63d..HEAD`）为 `13 files changed, 3151 insertions(+), 2 deletions(-)`；
两次删除的 2 行分别是 `backend/factory_app.py` 里被替换的 `for router in (...)` 行与
`backend/services/factory_bootstrap.py` 里被重排的迁移名列表行。
其中 `backend/services/soul_runtime.py` 709 行、`backend/api/soul_runtime.py` 395 行、
`backend/migrations/094_factory_soul.sql` 195 行、`tests/factory/test_soul_runtime.py` 471 行、
`tests/factory/test_soul_migrations.py` 89 行、审计 153 行。

## 2. 实现映射：契约 MVP 六条 → 代码

依据：契约 `docs/architecture/TENSOR_SOUL_CONTRACT_v0.1.md:58-65`（Required for MVP）。

| # | 契约要求 | 实现位置 | 机械保证 |
|---|---|---|---|
| 1 | 稳定 agent 身份，与 session/model 无关 | `backend/migrations/094_factory_soul.sql:17-34`（`factory_agent_identities`，**无 model 列**，`UNIQUE(organization_id,worker_id)`）；`factory_soul_sessions:52-67`（`session_id` 仅标识一次 episode，`UNIQUE(agent_id,episode)`）；`backend/services/soul_runtime.py:63-102`（`create_agent`/`revise_goal` 只涨 revision） | 身份表不含模型字段；切模型只写 `factory_soul_binding_history`（`094:147`）；测试 `test_model_binding_switch_preserves_identity_and_durable_state` |
| 2 | session/task 引用持久化 + checkpoint/restore | `094:52-67`（`task_refs JSONB`、`active_plan`、`current_task_id` 复用 `tensor_dev_missions`）、`094:126-137`（`factory_soul_checkpoints(PK(session_id,version), checksum) `）、`soul_runtime.py:247-307`（`commit_checkpoint`/`restore_checkpoint`/`latest_checkpoint_version`） | `PRIMARY KEY(session_id,version)` → 重复提交幂等、异内容冲突；校验和不符抛 `CheckpointCorruptError`（`soul_runtime.py:49-51`）**拒绝恢复**而非静默丢弃；迁移带版本+sha256 登记（`backend/services/soul_migrations.py:1-66`，登记表 `094:9-15`） |
| 3 | append-only、带来源的 memory event | `094:71-99`（`factory_memory_events`：`source`/`provenance`/`sensitivity`/`verification_status`/`validator`/`idempotency_key` + 触发器 `094:102-123`）；`soul_runtime.py:310-357`（`observe`） | 触发器硬禁 DELETE 与内容 UPDATE，只放行验证状态迁移；`UNIQUE(organization_id,idempotency_key)` |
| 4 | 有界 recall + 上下文准备 | `soul_runtime.py:432-486`（`recall`）、`:489-559`（`prepare`） | 硬字符预算（`DEFAULT_RECALL_CHARS=2400` / `DEFAULT_PREPARE_CHARS=6000`，`soul_runtime.py:32-34`）、扫描上限 400 条、相关性打分（CJK 二元组 + 英文词）、超出即截断并标 `truncated`；`prepare` 只装配 identity/session/task_context/memories 四段，**不回放全量 transcript** |
| 5 | 切换 model binding 不丢持久状态 | `094:37-49`（`factory_soul_model_bindings`，版本化，含 provider/adapter/capability_limits）、`094:147`（binding history）、`soul_runtime.py:119-148`（`register_binding`）、`:229-246`（`switch_binding`） | binding 只被 session 引用（`094:61`），从不是身份键；实测切换前后 agent_id/记忆/检查点不变 |
| 6 | 验证状态 + 晋级门 | `094:84-85`（四种状态 CHECK）、`094:91`（`sensitivity<>'shared' OR verification_status='validated'`）、`094:158-178`（`factory_learning_candidates`，approved 需 `evaluation->>'passed'`）、`soul_runtime.py:359-408`（`set_verification`/`promote_to_shared`）、`:561-666`（`propose_candidate`/`decide_candidate`/`consolidate`） | 未验证内容进共享库在**库约束层**被拒（不是靠调用方自觉）；`consolidate` 只接受 `approved` 候选且逐条复核 `validated`，非 validated 计入 `skipped` |

接口层：`backend/api/soul_runtime.py:161-395`，前缀 `/api/v1/tensor/soul`，23 个路由；已在 `backend/factory_app.py:40` 注册。
迁移接线：`backend/services/factory_bootstrap.py:29-33` 调 `apply_soul_migrations`（不删除 091，双身份并存、平滑迁移）。
CI 触发路径：`.github/workflows/resident-community.yml:8,10,16` 增加 soul 路径与取证脚本。

## 3. 测试执行（真实执行，命令 + 退出码 + 结果）

执行环境：`/srv/tensor/shared/venvs/tensor_eco/bin/python`（Python 3.12.13）；依赖装在本地 `.pydeps`（`PYTHONPATH` 注入）；
测试库由 `tests/factory/conftest.py` 用 `pgserver` 每次起**临时 PostgreSQL**，不连生产库、无 mock。

一次性复现：`PYTHONPATH=/srv/tensor/shared/tensor_eco/agent_work/m200/.pydeps /srv/tensor/shared/venvs/tensor_eco/bin/python scripts/verify_soul_phase1.py`

> 工件口径：`soul_phase1_validation.json` 的 `head_commit` 是**跑测时所处的工作树 HEAD**（代码状态），
> 该文件随后作为一个只含文档/工件的新提交落库，因此审批时的仓库 HEAD 会再前进一格；
> 代码内容与跑测时一致（`git diff <head_commit>..HEAD -- backend tests tools` 应为空）。

| 运行 | 命令 | 退出码 | 结果 | 耗时 |
|---|---|---|---|---|
| 全量 factory | `/srv/tensor/shared/venvs/tensor_eco/bin/python -m pytest tests/factory -q -p no:warnings` | 0 | 95 passed / 0 failed | 72.41 s |
| Soul 专项 | `/srv/tensor/shared/venvs/tensor_eco/bin/python -m pytest tests/factory/test_soul_runtime.py tests/factory/test_soul_migrations.py -q -p no:warnings` | 0 | 11 passed / 0 failed | 16.28 s |
| 非 factory 回归 | `/srv/tensor/shared/venvs/tensor_eco/bin/python -m pytest tests --ignore=tests/factory -q -p no:warnings` | 0 | 23 passed / 0 failed | 0.71 s |

判据总账：**13/13**（3 组运行退出码 + 6 个验收场景用例 + 4 项工件静态检查），见 `soul_phase1_validation.json` 的 `checks_passed/checks_total`。

上表与 §3.1 的耗时、passed 数全部逐字抄自上表所述那次真实执行写入的 `soul_phase1_validation.json`
（`runs[].duration_seconds` / `acceptance_scenarios[].seconds`），未做任何估算或取整；重跑会得到新的计时，
但 `exit_code` 与 `counts` 不变。pytest 末行原样抄录：`95 passed in 68.76s (0:01:08)` / `11 passed in 13.07s` / `23 passed in 0.43s`。
4 项工件静态检查为：审计文档含 ≥10 处 `file:line` 引用、`094` 迁移带 64 位 sha256 + 版本登记表 + 共享级 CHECK、
CI 触发路径含 soul、报告含六场景小节 + NOT RUN + 退出码清单（明细见工件 `static_checks`）。

### 3.1 六场景 ↔ 测试用例（逐个 passed）

| 场景 | 用例（`tests/factory/test_soul_runtime.py`） | 结果 | 耗时 |
|---|---|---|---|
| 场景 1 跨 session 连续性 | `test_agent_identity_and_unfinished_task_survive_independent_sessions`（:76） | passed | 1.069 s |
| 场景 2 进程重启恢复 | `test_latest_checkpoint_restores_after_process_restart`（:135） | passed | 1.397 s |
| 场景 3 模型切换连续 | `test_model_binding_switch_preserves_identity_and_durable_state`（:204） | passed | 1.084 s |
| 场景 4 observe 幂等去重 | `test_observe_is_idempotent_and_detects_conflicting_writes`（:250） | passed | 1.176 s |
| 场景 5 租户（组织）隔离 | `test_tenant_isolation_for_identity_session_and_memory`（:290） | passed | 1.074 s |
| 场景 6 未验证信息隔离 | `test_unverified_model_output_cannot_reach_shared_knowledge`（:336） | passed | 1.351 s |

补充用例（同样真实执行）：`test_recall_and_prepare_are_bounded_and_task_scoped`（:430，覆盖 MVP 第 4 条）；
`tests/factory/test_soul_migrations.py` 4 个（幂等重放、迁移文件变更被拒、契约表齐备、091 会话挂到单一稳定身份）。

### 3.2 测试做了什么（不夸大）

1. **跨 session 连续性**：同一 `agent_id` 开两个独立 session，断言 `episode` 递增、`session_id` 不同、`agent_id` 不变；
   第二 session 续挂同一任务时 `task_refs` **去重**、新增任务追加为 `kind='linked'`；
   第一 session 的 `active_plan`/checkpoint 仍可读；第二 session 在**不重放 transcript** 的前提下用 `recall` 命中第一条观察。
2. **进程重启恢复**：checkpoint 由**独立子进程**（新 SQLAlchemy engine，`subprocess.run`）读回，断言状态一致；
   同版本重复提交不产生第二行；篡改 checksum 后 `restore` 返回拒绝（不是静默从零开始）。
3. **模型切换连续**：切换 binding 后 `agent_id`/记忆事件/checkpoint 全部不变，`binding_history` 追加一条，
   `factory_agent_identities` 无任何模型列被改写。
4. **observe 幂等去重**：相同 `idempotency_key` 重复 observe 只落 1 行；同键异内容报冲突；
   DB 层 DELETE 与内容 UPDATE 均被触发器拒绝（`append-only`）。
5. **租户隔离**：另一组织的 agent 无法以本组织 `agent_id`/`session_id` 读取或 recall，跨租户读取返回 404/空集。
6. **未验证信息隔离**：`observed` 事件直接以 `shared` 落库被 DB CHECK 拒绝；
   `consolidate` 对未 `approved` 候选拒绝；`approved` 候选里未 `validated` 的来源事件被跳过并记录原因。

## 4. 未执行项（NOT RUN）

| 项 | 原因 |
|---|---|
| 神经记忆编码器 / learned state 实验（`NeuralMemoryState`） | 契约 `TENSOR_SOUL_CONTRACT_v0.1.md:67-71` 列为 "Optional experiment after the baseline"；须在 A/B 基线（无记忆 / 结构化文本记忆）建立后才有意义。本阶段**只留契约与审计占位，无表、无代码路径**。 |
| 闭源模型 hidden state 修改 | 契约明示 out of scope；本阶段 `soul_runtime` 不引模型、不发网络请求。 |
| 生产/线集群部署验证 | 本阶段只做单元/集成测试（临时 PostgreSQL）。**部署证据等级 L0**，未取得 `factory_evidence_records` 的 deployment 级证据。 |
| 前端 factory 控制台改动与 console workflow | 未改前端，故未跑。 |
| 真实 LLM 端到端跑通（Gateway 带真实模型切换的在线验证） | 需要生产网关配额；本次用 binding 切换的持久状态断言替代，属**结构验证**而非在线验证。 |
| 科学有效性（记忆机制是否真的提升 Agent 表现） | 无 A/B 实验，**不作任何有效性声明**。 |

## 5. 缺口与后续建议

1. **两套身份并存**（`factory_resident_workers.agent_session_id` vs `factory_agent_identities.agent_id`）：
   本阶段用 `factory_agent_identities.legacy_session_id` + `factory_soul_legacy_sessions` 做挂接，**未做数据搬移**。
   建议下一阶段把 `factory_residents` 的创建/配置流程改为先建稳定身份，再把 091 会话降级为 episode。
2. **resident harness 尚未消费 Soul**：`tools/factory/resident_harness.py` 的 `deliberate()` 仍走 `persona.read()` 全量装配
   （审计 `TENSOR_SOUL_AUDIT.md` 1.3/1.4）。`recall/prepare` 已有稳定接口，建议下一步把它接进 harness 的上下文装配，
   并让 `persona.append` 双写 `factory_memory_events`（带 `source`）。
3. **共享知识的读取面仍是新增接口**：老的 `knowledge` 议题（`backend/api/factory_residents.py:194`）没有验证状态字段。
   建议把 community knowledge 议题收敛为 `factory_memory_events(sensitivity='shared', verified)` 的只读视图，避免双路由语义分叉。
4. **事件有效性/失效链**：`superseded_by` 已建列但只有状态迁移，尚无"新事件自动 supersede 旧事件"的用例；建议补 conflict-resolution 测试。
5. **recall 相关性只做词面匹配**：CJK 二元组 + 英文 token，无语义检索。在接入神经记忆之前，建议先用**固定基线**
   （无记忆 / 全文倾倒 / 结构化 recall）跑 A/B，把 recall 的 `chars_used`、`filtered_irrelevant` 与任务成功率一起报告，
   再决定是否上编码器——这正是契约 "Optional experiment after the baseline" 的顺序。
6. **证据分级**：建议把本次产物登记进 `factory_evidence_records`（L1 单元/集成测试，`code_sha` 填本报告第 1 节 SHA）。

## 6. 纪律声明

- 全程在 mission worktree 分支 `agent/m200` 上开发，**未 push**，未向 main 直推。
- 未使用 GPU；Python 一律 `/srv/tensor/shared/venvs/tensor_eco/bin/python`。
- 未修改闭源模型内部状态；未把任何模型输出当事实写入共享库（该行为在 DB CHECK 层被禁止并有测试覆盖）。
- 事实核实：`feat/tensor-agent-memory` 分支 ahead 0 / behind 152，无独有提交，内容已全在 main，本阶段未基于该分支开发。