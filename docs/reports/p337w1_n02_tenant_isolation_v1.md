# P337W1-N02 多租户隔离机制 v1（12 仓租户制）成熟化交付报告

- 节点：`p337w1-n02-tenant-isolation`
- 仓：TensorPlatform（共性平台，**零领域语义**）
- 机制模块：`backend/services/tenant_isolation/`（7 模块 + 门面）、HTTP 面 `backend/api/tenant_isolation.py`
- 租户事实注入：`config/tenant_registry.json`；域词表注入：`config/domain_word_denylist.json`
- 验收工件：`artifacts/tenant_isolation/p337w1_n02_validation.json`（逐用例证据）
- 生成时间：2026-11-10T16:09:26Z；总耗时 40.1 s

## ① 案例介绍（对象与参数表）

本节点不是物理算例，而是**平台隔离机制**的验收：12 仓租户制下，把「谁能写哪个仓、
谁能读哪些数据、每周期能用多少算力、提交前能否带进领域词条」从口头纪律变成可机器
裁定的门。机制的对象 = 租户（tenant）、资源额度（quota）、路径空间（repo/共享目录）、
数据命名空间（artifacts/reports/events）、提交文本（domain gate）。

租户注册表（`config/tenant_registry.json`，12 条，字段 `name/repo/allowed_domains/
resource_quota/data_classification` 全量注入）：

| tenant | repo | allowed_domains | 数据分级 | CPU 核时/周期 | 内存 GB·h/周期 | 存储 GB | 并发任务 | 周期任务 |
|---|---|---|---|---|---|---|---|---|
| `tenant-lbm` | `TensorLBM` | dom-a1 | internal | 400 | 800 | 60 | 8 | 4 |
| `tenant-fem` | `TensorFEM` | dom-a2 | internal | 500 | 1000 | 80 | 10 | 5 |
| `tenant-fvm` | `TensorFVM` | dom-a3 | internal | 450 | 900 | 70 | 9 | 4 |
| `tenant-dem` | `TensorDEM` | dom-a4 | internal | 300 | 600 | 50 | 6 | 3 |
| `tenant-bem` | `TensorBEM` | dom-a5 | internal | 350 | 700 | 55 | 7 | 3 |
| `tenant-sph` | `TensorSPH` | dom-a6 | internal | 320 | 640 | 45 | 6 | 3 |
| `tenant-dg` | `TensorDG` | dom-a7 | internal | 280 | 560 | 40 | 5 | 2 |
| `tenant-cad` | `TensorCAD` | dom-a8 | public | 200 | 400 | 30 | 4 | 2 |
| `tenant-platform` | `TensorPlatform` | dom-a9 | restricted | 600 | 1200 | 100 | 12 | 6 |
| `tenant-physics` | `TensorPhysics` | dom-b1 | restricted | 380 | 760 | 65 | 8 | 4 |
| `tenant-app-domain` | `ship-performance-platform` | dom-b2 | restricted | 300 | 600 | 50 | 6 | 3 |
| `tenant-papers` | `TensorPapers` | dom-b3 | internal | 150 | 300 | 90 | 3 | 2 |

策略（同文件 `policy`）：默认拒绝（`default_decision=deny`）；共享只读根 `shared`、
`platform_shared`；工件/报告/事件根 `artifacts/tenants`、`tensor_reports/tenants`、
`events/tenants`。

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

**模块切分（每块独立可证）**

| 模块 | 职责 | 关键语义 |
|---|---|---|
| `store.py` | SQLite（默认 `:memory:`，可 `TENANT_ISOLATION_DB` 落盘） | 表 `feature_ledger` / `tenant_budget` / `tenant_claim` / `tenant_grant` / `access_log` / `tenant_event`；每次判定写 `access_log`，可回放 |
| `registry.py` | 载入 + 校验 + 发布租户元数据 | 缺字段/重复仓/空域/非数值额度 → `RegistryError`（**不允许"半个租户"变成宽松租户**）；注册落 `feature_ledger` |
| `quota.py` | 每周期额度准入 | `claim()` 单一入口：未知租户/未知资源/非法额度/超预算一律 reject 并留痕；gauge 型资源（并发/周期任务）支持 `release` |
| `boundary.py` | 写/读路径边界 | 写只允许本仓；`..`、绝对路径、符号链接逃逸、共享根、他仓 → 拒；读 = 本仓 + 共享根 + 显式授权（授权**只能读不能写**） |
| `data_isolation.py` | 工件/报告命名空间 + 事件流 | 存储布局 `<root>/<tenant>/<rel>`；跨租户读需活授权；事件默认 `private`，跨租户可见必须显式 `shared` 标记 |
| `domain_gate.py` | 提交前域语义门 | 复用平台唯一扫描器 `tools/audit/domain_leak_scan.py` + 注入词表；`enforce` 命中即拒，`warn` 只记不拒 |
| `engine.py` | 门面（一只 store 串起全部） | `IsolationEngine` 一处拿到 registry/budget/paths/artifacts/reports/events/gate；`self_check()` 供健康面 |

**零领域语义做法**：机制代码里不出现任何租户名、仓名、域词条；租户身份与额度来自
`config/tenant_registry.json`，域词表来自 `config/domain_word_denylist.json`，域标签只
以不透明 token（`dom-a1`…）形式流通。同时把新增路径追加进词表 `scan_paths`（
`backend/services/tenant_isolation`、`backend/api/tenant_isolation.py`、
`backend/tests/test_tenant_isolation_v2.py`、`tools/audit/tenant_isolation_validation.py`），
使平台级默认扫描（`python tools/audit/domain_leak_scan.py`）**持续**覆盖新机制：
扫描 85 个文件 / 37 个词条 → 命中 0。

**验收工具链**：`tools/audit/tenant_isolation_validation.py` 是机器裁决器——用真实机制
（不是桩）跑 30 个合成用例，**同时跑 6 个"应当放行"的对照**（否则"一律拒绝"也能刷分），
并把每条用例的 reason 码、拒绝率、回归 junit 计数写进 JSON；`--figures` 出图。
pytest 侧另有一份独立用例：`backend/tests/test_tenant_isolation_v2.py`（40 例）。

## ③ 计算结果（判据数字，程序化注入自 JSON）

| 判据 | 内容 | 观测值 | 裁决 |
|---|---|---|---|
| C1 | 租户注册完整 + feature_ledger 可查 | 12/12 租户，ledger 条目 12，complete=true | PASS |
| C2 | 超预算 claim 拒绝率 | 10/10 = 100% | PASS |
| C3 | 跨仓写操作拒绝率 | 10/10 = 100% | PASS |
| C4 | 域语义扫描拒绝率 | 5/5 = 100% | PASS |
| C5 | 跨租户数据访问拒绝率 | 5/5 = 100% | PASS |
| C6 | 回归（mission/事件/报告 + 既有隔离套件） | 108 passed / 0 failed / 11 skipped（共 119） | PASS |
| C7 | 隔离机制代码域词表命中 | 0 命中 / 11 文件 | PASS |

对照（必须放行，证明不是"全拒"）：
| 对照 | reason | 结果 |
|---|---|---|
| quota accept | `within_quota` | True |
| write own repo | `own_repo` | True |
| read own repo | `own_repo` | True |
| domain-clean submit | `allowed`, hits=0 | True |
| artifact write own | `own_namespace` | True |
| artifact read own | `own_namespace` | True |

C2 逐用例（10 个合成超预算 claim）：

| 用例 | 资源 | 申请量 | 期望拒绝码 | 观测拒绝码 | 结果 |
|---|---|---|---|---|---|
| C2-01 | cpu_core_hours | 1000000000.0 | `quota_exceeded` | `quota_exceeded` | 拒 |
| C2-02 | memory_gb_hours | 1000000000.0 | `quota_exceeded` | `quota_exceeded` | 拒 |
| C2-03 | storage_gb | 1000000000.0 | `quota_exceeded` | `quota_exceeded` | 拒 |
| C2-04 | concurrent_tasks | 1000000.0 | `quota_exceeded` | `quota_exceeded` | 拒 |
| C2-05 | periodic_tasks | 1000000.0 | `quota_exceeded` | `quota_exceeded` | 拒 |
| C2-06 | cpu_core_hours | -5 | `invalid_amount` | `invalid_amount` | 拒 |
| C2-07 | cpu_core_hours | 0 | `invalid_amount` | `invalid_amount` | 拒 |
| C2-08 | cpu_core_hours | None | `invalid_amount` | `invalid_amount` | 拒 |
| C2-09 | resource_not_declared | 1 | `unknown_resource` | `unknown_resource` | 拒 |
| C2-10 | concurrent_tasks | 1.5 | `fractional_gauge` | `fractional_gauge` | 拒 |

C3 逐用例（10 个跨仓写）：

| 用例 | 目标路径 | 归属租户 | 观测拒绝码 | 结果 |
|---|---|---|---|---|
| C3-01 | `TensorFEM/pkg/mod.py` | tenant-fem | `cross_tenant_write` | 拒 |
| C3-02 | `TensorLBM/../TensorFEM/pkg/mod.py` | tenant-fem | `path_traversal` | 拒 |
| C3-03 | `/tmp/tenant_isolation_rp8d56us/workspace/TensorFEM/deep/x.py` | tenant-fem | `cross_tenant_write` | 拒 |
| C3-04 | `/tmp/tenant_isolation_rp8d56us/elsewhere/x.py` | None | `outside_tenant_namespace` | 拒 |
| C3-05 | `shared/x.py` | None | `shared_root_read_only` | 拒 |
| C3-06 | `TensorLBM/jump_to_other/x.py` | tenant-fem | `cross_tenant_write` | 拒 |
| C3-07 | `TensorLBM/jump_out/x.py` | None | `outside_tenant_namespace` | 拒 |
| C3-08 | `TensorFEM/./x.py` | tenant-fem | `cross_tenant_write` | 拒 |
| C3-09 | `TensorFEM/` | tenant-fem | `cross_tenant_write` | 拒 |
| C3-10 | `../TensorFEM/x.py` | None | `path_traversal` | 拒 |

C4 逐用例（5 个域语义提交，payload 由词表数据在运行时生成，源码零字面量）：

| 用例 | 形态 | 命中数 | 结果 |
|---|---|---|---|
| C4-01 | standalone_token | 1 | 拒 |
| C4-02 | mixed_case | 1 | 拒 |
| C4-03 | json_value | 1 | 拒 |
| C4-04 | comment_only | 1 | 拒 |
| C4-05 | cjk_embedded | 1 | 拒 |

C5 逐用例（5 个跨租户数据访问）：

| 用例 | 通道 | 观测拒绝码 | 结果 |
|---|---|---|---|
| C5-01 | artifact_read | `cross_tenant_artifact_denied` | 拒 |
| C5-02 | artifact_traversal | `path_traversal` | 拒 |
| C5-03 | report_read | `cross_tenant_artifact_denied` | 拒 |
| C5-04 | event_read | `cross_tenant_event_denied` | 拒 |
| C5-05 | artifact_missing_after_deny | `cross_tenant_artifact_denied` | 拒 |

审计面：`access_log` 共 32 条判定记录；claim 决策分布
`{'concurrent_tasks': (0, 2), 'cpu_core_hours': (1, 4), 'memory_gb_hours': (0, 1), 'periodic_tasks': (0, 1), 'resource_not_declared': (0, 1), 'storage_gb': (0, 1)}`
（(accepted, rejected)），整体 claim 拒绝率 90.9%
（含 1 条对照放行）。

## ④ 结果图

- `artifacts/tenant_isolation/figures/rejection_rates.png`：四组判据拒绝率 vs 100% 目标线。
- `artifacts/tenant_isolation/figures/claim_decisions.png`：按资源的 claim 放行/拒绝堆叠。

**无场量云图说明**：本节点是纯后端机制（准入/边界/隔离），没有物理场量或几何，故不出
云图；替换为"拒绝率 vs 目标线"与"准入决策分布"两张机制图，图数据直接取自验收 JSON。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **NaN 落不进 SQLite**：`claim(amount=nan)` 判为非法国额度后写日志，SQLite 把 NaN 存成
   NULL 触发 `NOT NULL` 违例——修法是把异常额度归一为 0 再留痕，而不是放宽约束。
2. **路径 API 必须根相对**：初版 `write()` 用租户相对、`read()` 用根相对，导致"读自己
   的工件"被误判为跨租户——统一成 `<owner>/<rel>` 根相对后，写侧也多了一道
   `cross_tenant_artifact_write` 检查（更严）。
3. **符号链接逃逸**：只做字符串前缀比较会被 `repoA/link_to_repoB/x` 绕过；实现里统一
   `realpath` 后再比前缀，10 个写用例里 2 个专测这条。
4. **既有隔离套件的 5 个基线 FAILED 是环境缺口 + 1 个测试缺陷**：环境无 `pytest-asyncio`，
   协程测试被 pytest 判"natively unsupported"。加了最小 `pytest_pyfunc_call` shim（真插件
   在场时让位）；另修 1 个测试缺陷（把 `Depends(get_db)` 默认值当 session 传进
   `require_permission`，导致 `db.rollback()` 崩）。基线 5 FAILED → 现 0 FAILED。
5. **发现但未改的残余风险**：`ROLE_PERMISSIONS["engineer"]` 不含老拼写 `datasets:write`
   （只有 `datasets:edit/delete`）；这是权限语义面，属安全相关，**未擅自放宽**，只在该
   测试注释与本报告登记。
6. **本环境主应用 import 缺依赖**：`backend/main.py` 整体 import 需 `cryptography`
   （本沙箱未装，属既有缺口，conftest 已降级 skip 依赖 app 的用例）。因此 HTTP 面不是靠
   进程级 app 启动验证，而是把 `api.tenant_isolation.router` 挂到独立 FastAPI 应用上，
   用 `TestClient` 逐端点断言状态码（409/403/200 见 pytest 用例）。`main.py` 的挂载语句
   按仓内既有 try/except 惯例写，挂载失败只告警不拖垮主应用。
7. **适用范围**：机制在存储层是单进程 SQLite（多副本部署需换 PG 或共享卷，`Store` 已隔离
   到一处可替换）；HTTP 面 engine 是进程内单例，多 worker 各自持库；未做速率/审计归档
   的长期保留策略。

## ⑥ 误差判定（vs 验收基准，逐项显式回答"是否<3%"）

| 关键量 | 基准 | 观测 | 偏差 | <3%？ |
|---|---|---|---|---|
| 超预算 claim 拒绝率 | 100% | 100% | 0.0 pp | 是（0.0%） |
| 跨仓写拒绝率 | 100% | 100% | 0.0 pp | 是（0.0%） |
| 域语义扫描拒绝率 | 100% | 100% | 0.0 pp | 是（0.0%） |
| 跨租户数据访问拒绝率 | 100% | 100% | 0.0 pp | 是（0.0%） |
| 机制代码域词命中 | 0 | 0 | 0 | 是（0 命中） |
| 回归通过率 | 100% | 108/108 | 0 | 是（0 failed） |

没有实验/文献物理基准量（本节点非物理算例），故不适用"物理量误差 <3%"；上表是**机制
判据 vs 设定目标**的偏差，全部为 0；同时用 6 条对照证明放行侧未被误伤。honest 说明：
11 个回归用例 `skipped` 是既有套件自带的跳过（环境依赖），非本次改动引入，未粉饰为通过。
