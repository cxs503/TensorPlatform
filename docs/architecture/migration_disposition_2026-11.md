# TP ↔ TensorPhysics 职责迁移处置清单（2026-11）

状态：底稿定谳版（编制完成，取代 #575 §5「迁移清单草案」的草案地位）。
输入 = 边界审计 #575（M1–M20，22/22 引用核验）+ 调用图审计 #573 + R1–R4 已并变更
（TP #577/#578/#581、TensorPhysics #98/#99）。本文回答一个问题：
**每一块现状代码，最终去哪、怎么迁、不破坏谁、哪些直接删**。

所有权声明：TP/TensorPhysics 重构归 owner 指定专人执行；本清单为该线的执行底稿，
agent 仓开发冻结令不因此解除。

## 0. 四态定义与判则

| 态 | 含义 | 退出条件 |
|---|---|---|
| **保留** | 位置不动；只可加列/加约束，不可改既有契约词表 | — |
| **抽取** | 代码迁走（→ TensorPhysics 或领域包），原职责在原位终结 | 判据项绿 + 包装层到期移除 |
| **兼容包装** | 迁移后原路径留 re-export/适配层，调用方零改动 | 包装层标注 deprecated + 移除期限 + 到期零调用 |
| **废弃** | 删除不留包装（仅限调用方已被取代或零调用） | 判据项绿 |

判则（承接 #575 §5 共用未决项）：
1. 凡「实际调用方 ≥2 且无同步改造审批」**一律走兼容包装**，不做大爆炸式改 import。
2. 凡「DB CHECK / 字段正则 / 外部文件路径」类契约，**先落单一契约源（§3）再动代码**。
3. 每步迁移必须带机器判据；判据不绿 = 不迁。

## 1. R1–R4 已执行部分（清单增量，非重复盘点）

| 已落变更 | 对应 M 项 | 新状态 |
|---|---|---|
| R2：`resistance_core` / `resistance_model` 迁入 `api/domain/` 域载荷区 + `services/validation_providers.py` 缝 + `api/domain/physics_providers.py` 四能力注册（`main.py:74-76` 接线） | M9 | **过渡态已落**：`services/resistance_core.py` 已不在 main；残三件见 §2 M9 行 |
| R4：`tests/test_physics_seam_guards.py` 7 守卫（services/ 全目录 AST 禁域物理 import + 缝中立 + 注册面稳定） | M5/M8/M9 | 回归防线已固化，35 绿（与 G1–G3 合跑） |
| R1（TensorPhysics）：`physics_contracts.py` 溯源三态 + 11 内核注册表 | M5/M6 | 判据参数的契约载体已存在于 TensorPhysics 侧 |
| R3（TensorPhysics）：`research_runner` 终态回写 TP RunRegistration（terminal-only，manifest v1） | M20 | 回写通道已通；登记词表双源未收敛（§3 G-C） |

### 1.1 阻力链实际调用路径（main 实核 2026-11-03，外部评估关注项）

```
入口A  api/domain/ship_benchmark.py  POST /calculations/benchmark_validate
  ├─ 默认 in-process: services.ship_resistance.resistance_breakdown
  │   （SHIP_BENCHMARK_REMOTE=1 才走 HTTP hop）
  ├─ 数据审计: services.benchmark_dataset_audit / benchmark_integrity
  └─ validity verdict: api.domain.resistance_core（lazy import，:69）
入口B  api/calculations.py（:8001 部署源）
  └─ services.ship_resistance / holtrop_mennen（services/ 残留）
审计层 services/ship_benchmark_integrity.py:32
  └─ services.validation_providers（R2 缝）
      └─ api.domain.physics_providers（g/ρ/ν + ittc1957 四能力）
```

已证实：缝路径（审计层→providers→域载荷区）全程无 `services→services` 直连域物理；
未收敛：入口A/B 仍直 import `services.ship_resistance`（M9 尾巴，见 §2）。

## 2. 处置总表（M1–M20 定谳）

状态列：`已执行`（R1–R4 落过）/ `待执行` / `待裁`。调用方列为 main 实核（2026-11-03）。

| # | 模块（现状位置） | 处置 | 状态 | 兼容包装 | 实际调用方 | 风险 | 验收判据 |
|---|---|---|---|---|---|---|---|
| M1 | `migrations/086\|095` `tensor_dev_missions` | **保留（TP）** | — | 不适用 | `api/factory_*`、`api/tensor_missions.py`、`tools/agent/*`（表名 82 文件引用） | 只可加列；状态词表冻结 | 词表 diff 前后不变 |
| M2 | TensorPhysics `research_runtime.py` TaskStatus/Store | **保留（TPh）** | — | 不适用 | TPh `worker/scheduler/research_runner` | 与 M1 词表映射缺契约源（§3 G-A） | 映射表 100% 覆盖、无孤儿态 |
| M3 | `api/domain/tensor_common.py:79` append_event | **保留（TP）** | — | 不适用 | 事件桥、生命周期端点、`tensor_executions.py:458` | 084/086 CHECK 与 EVENT_KINDS 白名单 | kind 一致性测试绿 |
| M4 | TensorPhysics `TaskEvent` 流 | **保留（TPh）** | — | 不适用 | `ResearchTaskStore` | 与 M3 双写风险（§3 G-B） | 一事实单源：M4 产、M3 只记登记 |
| M5 | `services/benchmark_audit.py`（真物理误差判决） | **抽取→领域包** + 容差参数经 TP 配置下发 | 待执行 | 是（判决 JSON schema 不变） | `tools/verify_ship_benchmark.py`、`tests/feat` | `PASS_TOL_PCT=15` 是平台精度标准 | 同批输入判决 diff=0 |
| M6 | `services/benchmark_canon.py`（正典裁决 + git show） | **抽取→领域包**；`git show` 移出 services/ | 待执行 | 是（`benchmark_reference.json` v3.0 路径=外部契约） | `services/reference_recovery.py:44` | 正典文件字节级稳定 | 正典字节不变；canon 家族红不新增 |
| M7 | `services/benchmark_integrity.py`（C1–C6） | **拆分**：恒等式族→领域包；证据门调用**保留（TP）** | 待执行 | 是（判词 DERIVED/WITHHELD/REJECTED 跨仓词汇） | `api/domain/ship_benchmark.py:52`、`benchmark_dataset_audit.py:39` | 判词词表跨仓 | 恒等式数值输出差异 0 |
| M8 | `services/benchmark_dataset_audit.py`（facade） | **抽取→领域包**（常量独立定义） | 部分已执行（per-case reference 覆盖已落） | 是 | `benchmark_integrity`、`ship_benchmark.py:47` | 残 `DEFAULT_WATER_DENSITY` 兜底 import 被审方 | 审计基准常量零 import 被审方（R4 守卫扩条） |
| M9 | 阻力物理三件 `services/{ship_resistance,holtrop_mennen,resistance_holtrop}.py` | **抽取→领域包**（`resistance_core/model` 已入 `api/domain/`） | **半已执行（R2）** | 是（`api/calculations.py`=:8001 部署源，响应字段零变化） | 入口A/B 直 import（§1.1） | 残三件被 :8001 部署树消费 | API 响应快照 diff=0 + 守卫 35 绿 |
| M10 | 耐波/推进/桨/几何/静水力 7 件 | **抽取→领域包** | 待执行 | 是（同上部署源约束） | `api/calculations.py:34,35,42,61`、`api/domain/ship_seakeeping.py:18` | :8001 回归快照 | 响应字段零变化 |
| M11 | 冰区/极地域（`ice_*`、`polar/*`、`api/domain/polar.py`） | **抽取→冰区领域包** | 待执行 | 是，分批（polar 28 边最后） | `api/domain/polar.py:30-50`、`ice_uq.py:15` | 冰区战役 DAG 在飞依赖 | TP 核心零 polar import（守卫扩条） |
| M12 | `services/coupling_clock_sync.py`（耦合时钟） | **抽取→TensorPhysics** `coupling.py/scheduler.py` | 待执行 | 是（战役 DAG 调用面） | 冰区战役 DAG | 与 TPh coupling 语义重叠，先语义 diff | 单一耦合时钟实现 + 时间一致性测试 |
| M13 | `services/ice_integration/gate.py`（验收门+守恒） | **抽取→TensorPhysics** `diagnostics.py`；判据参数由领域包给 | 待执行 | 是（判词被报告模板消费） | 冰区 pipeline | DiagnosticRecord 格式对齐 | 诊断格式守卫测试绿 |
| M14 | `services/truth_gates_aggregator.py` + `api/truth_gates.py` | **保留（TP）**（通用信任基础设施） | — | 不适用 | `api/truth_gates.py:32` | 无（纯函数可复算） | 复算一致性测试保持绿 |
| M15 | `services/stability_engine_audit_snapshot.py`（手抄快照） | **废弃手抄形态**；改为自动拉取 :8001 + 哈希入证据存储 | 待执行 | 否（文件头自述「不是生产代码」） | `docs/verification/stability_engine_verification_m1597.md` | 手抄漂移 | 快照哈希与远端一致 |
| M16 | `services/report_gate.py`（六要素报告门） | **保留（TP）**（交付契约）；判据参数外置 | 待执行（仅参数外置） | 参数兼容 | `tools/audit/report_gate_audit.py:58` | 判词回归 | 判据外置后同批报告判词不变 |
| M17 | `tools/ops/rmg.py`（prereg 提案生成） | **抽取→TensorPhysics** `ExperimentPlan` 起草；TP 只留派发 | 待执行 | 是（owner 审批链不动） | owner 审后 `dispatch_helper` | 提案 schema 变更 | schema 由 TPh 契约定义并测试锁 |
| M18 | `tensor_executor.py:391` + `tensor_approvals.py:83`（晋级闸门） | **拆分**：判据→TPh `research_policy`；审批收据**保留（TP）** | 待执行 | 是（`kind` 枚举在 084/086 DB CHECK，不可改） | executor、`auto_approver.py:531` | DB CHECK 硬约束 | 判据可追溯策略版本号 |
| M19 | `factory_strategy.py:173` + `tools/factory/campaign.py:180` | **拆分**：续跑判据→TPh；战役/预算/配额**保留（TP）** | 待执行 | 是（`result.continuation` 读方多处） | 战役工厂 | 决策 IO schema | schema 不变 + 决策回归快照 |
| M20 | `tensorphysics_runs.py`（运行登记）+ `s3_stream.py` | **保留（TP）**（正确边界示范） | 登记词表待收敛（§3 G-C） | 不适用（对外 API） | TPh `platform_registry.py` | `:37-38` 手写正则 vs `_RUN_STATUSES` 双源 | 契约测试：两侧枚举 diff=0 |

汇总：保留 6（M1/M2/M3/M4/M14/M16 主体/M20）｜抽取→领域包 6（M5/M6/M8/M9/M10/M11）｜
抽取→TensorPhysics 2+判据 3（M12/M13 + M17/M18/M19 判据半）｜拆分 4（M7/M15/M18/M19）｜
废弃 1 形态（M15 手抄）。**全部抽取项均带兼容包装**（判则 1）；唯一直接删除的是 M15 手抄形态。

## 3. 单一契约源三缺口（先于一切代码迁移）

| 缺口 | 现状（main 实核） | 落法 |
|---|---|---|
| **G-A** M1↔M2 任务状态词表 | 两侧各自定义，靠人脑映射（`verifying↔?`、`cancelled↔revoked`） | 共享契约文件（JSON，版本号）+ 两侧生成/导入 + diff=0 测试 |
| **G-B** M3↔M4 事件流 | workspace 级（TP）与 task 级（TPh）并存，无禁双写约束 | manifest 引用关联 + 「一事实单源」守卫测试 |
| **G-C** M20↔`platform_registry` 登记词表 | `tensorphysics_runs.py:37-38` 手写正则；TPh 侧 `_RUN_STATUSES`；靠注释 "Keep this in sync" | 同 G-A；两侧枚举 diff=0 契约测试（R3 已通回写通道，此为收尾） |

## 4. 废弃项（真删除，不留包装）

| 项 | 前提判据 |
|---|---|
| D1 绕行形态：prereg「文件名正则当冻结证据」门（`tools/ops/benchmark/gates_check.py:332`） | M17 落地（ExperimentPlan 被真实调用）后删 |
| M15 手抄快照 `services/stability_engine_audit_snapshot.py` | 自动拉取+哈希管线绿后删 |
| M6 的 `git show` 在 services/ 内的调用形态 | 正典裁决入领域包后改证据存储引用 |

## 5. 执行序（依赖序；每步判据绿才进下一步）

1. **G-A/B/C 契约源**（判据：三组 diff=0 契约测试绿）——一切迁移的前置。
2. **M9 尾巴**：残三件 `services/{ship_resistance,holtrop_mennen,resistance_holtrop}.py` → `api/domain/`（判据：API 响应快照 diff=0 + 守卫 35 绿）。
3. **M5/M8 判据参数化**：15% 容差、海水常数经 provider 缝下发（判据：守卫扩条绿——审计基准常量零 import 被审方）。
4. **M12/M13 → TensorPhysics**（前置：coupling.py 语义 diff 报告）。
5. **M17/M18/M19 判据抽取**（审批收据与战役预算留 TP 不动）。
6. **M10 → M11 领域包分批**（M10 先行；M11 polar 28 边单独一批，冰区战役 DAG 空窗期插排）。
7. **§4 废弃项收尾**。

## 6. 待 owner 裁

1. **领域包终态位置**：独立仓 vs TP 子包（`api/domain/` 为过渡区已定；ship 已有「留本地不上 GitHub」裁定，领域包是否同规）。
2. **M10/M9 的 :8001 部署源节奏**：迁移与部署树现代化（当前 deploy 树 57e72968 漂移）是否并轨。
3. **canon 漂移家族 8 红**（m1614 世代债）与 M6 正典文件的关系：先裁债还是随 M6 迁移一并收口。

## 7. 局限

- 调用方计数以静态 import 边为准（#573 采集器口径）；运行时动态调用（lazy import、RPC）以代码标注为准，未做运行时追踪。
- M11 polar 28 边中或含冰区战役在飞任务的临时依赖，执行前需与战役 DAG 对账。
- TensorPhysics 侧 4 个 si 收集错误（torch env）未在本清单范围，不影响本清单结论。
