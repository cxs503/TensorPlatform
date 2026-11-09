# 多租户并发写冲突检测与合并预演门（P173W2-N8 / m7876）

**一句话**：把「40 并发 × 12 仓租户共用一台开发机」这件靠人盯的事，做成三段可机器裁决的门——派单前路径预留、收单时冲突矩阵、推送前三方合并 dry-run——外加一本可复算的冲突台账。

## 1. 案例介绍

### 1.1 物理背景（工程背景）

平台按 12 个仓租户制运行，单个任务在自己的工作树里干活，完成后由平台自动推分支合并。并发度按 40 路设计。若两个任务的写面重叠而系统不知道，会在三个位置同时出事：

1. **派单时**：两个任务被允许写同一片路径，随后者的写入静默覆盖前者（工作树级别丢失）；

2. **收单时**：任务 A、B 都“完成”了但改了同一文件，谁先合谁后合决定谁的内容活下来；

3. **推送时**：分支相互冲突，合并失败或产生半合并，而“合并失败”跟“内容不对”在事后无法区分。

因此需要三处判据，且判据必须**可复算**：同一输入任何时候重跑都得到同一裁决，否则事后无法回答“当时为什么放行”。

### 1.2 几何/参数表（机制参数）

本节点是平台共性机制，没有几何体；等价“参数表”是可配置的门参数（全部由配置注入，代码零业务常量）：

| 参数 | 默认值 | 语义 |
|---|---|---|
| `lease.ttl_seconds` | 21600 | 路径预留生存期；过期预留不参与冲突判定（记 `expired_dropped` 审计） |
| `lease.on_conflict` | `refuse` | 写租约冲突时的处置：拒派 / 降级只读（`downgrade_readonly`） |
| `lease.writable_roots` | `["**"]` | 可预留路径根集合，越界的预留直接拒派 |
| `lease.protected_globs` | `[".git/**"]` | 任何情况下不许预留的路径 |
| `intake.window_seconds` | 86400 | 「同窗口」定义：收单时间差 ≤ 该值 |
| `intake.on_conflict` | `block` | 有阻塞 lane 时收单阻塞 / 仅报告（`report_only`） |
| `intake.require_lease_coverage` | true | 校验收单文件是否都在自己声明的预留面内 |
| `rehearsal.timeout_seconds` | 30 | 单条只读 git 子命令超时（超时即 unknown） |
| `rehearsal.max_merge_tree_calls` | 600 | 批量预演 merge-tree 调用预算（超预算的候选对记 unknown） |
| `rehearsal.detail_limit` | 2000 | 逐对明细上限（其余只计数，控报告体积/内存） |

### 1.3 三段门的状态机

```
派单  ──► [lease 门] ── admit/downgrade ──► 施工 ──► 收单 [冲突矩阵] ── admit/block ──► 推送 [merge-tree dry-run]
                            │                                   │                              │
                          refuse                          block(scope/lease/file)        clean | conflict | unknown
                            └──────────────────── 全部事件 append 进 ledger.jsonl（幂等 + 链表校验 + 可复算）
```

## 2. 怎么做的

### 2.1 方法

**（1）路径模式相交判定**（`tools/tenancy_gate/pattern.py`）：租约记的是模式（`src/**`、`docs/m*/report.md`），两租约是否抢同一片路径等价于「两个模式的语言是否有交集」。单段用状态机 BFS（`*` 可空可吞）做**精确**判定，跨段对整段 `**` 做记忆化递归。超出支持子集的写法（字符类、花括号、绝对路径、`..`）显式拒绝而不是当字面量。

**（2）派单前租约门**（`lease.py`）：先过滤过期租约，再按 `protected_path → outside_writable_roots → lease_conflict` 顺序裁决；写租约之间相交即冲突，只读租约永不阻塞。

**（3）收单冲突矩阵**（`intake.py`）：本单 `changed_files` × 同窗口其他任务，四条阻塞 lane（`file_overlap` / `lease_breach` / `scope_violation` / `invalid_path`）+ 一条记录 lane（`glob_overlap`）。矩阵排序固定，`digest` 由内容派生。

**（4）推送前合并预演**（`rehearsal.py`）：只调用只读 git（`rev-parse` / `merge-base` / `diff` / `merge-tree --write-tree`），不碰工作树、索引与 ref。冲突时进一步用 `git diff -U0`取两边的 base 坐标变更区间并求交，给出 hunk 级冲突区间。任何不能判定的情形（仓不可读、ref 解析失败、无共同祖先、超时、非 0/1 退出码、超预算）一律 `unknown` 且 `allowed=false`。

**（5）台账**（`ledger.py`）：append-only JSONL，`event_id` 由内容派生 ⇒ 重放不产生新行；`prev` 链表 + 重算 `event_id` ⇒ 改行/删行/换序必被发现；入库时同时存**输入**，事后可用 `recompute_matrix` 重跑并比对 `matrix_digest`。

### 2.2 性能取向：为什么两两预演是可行的

N 条分支两两判定是 O(N²)，但 git 按路径合并 ⇒ **路径不相交的对不可能冲突**。所以批量接口先用一次 `git diff --name-only base...branch` 拿到每条分支的变更文件集合做预筛，只有相交的候选对才真跑 `merge-tree`；预筛放行的对记 `reason=prescreen_disjoint`，证据（各自变更文件数）留在报告里可被复核。超出预算的候选对不硬扛，记 `unknown` 并整体 fail-closed。

### 2.3 工具链

Python 3.11.6 / git version 2.41.0 / pytest；无第三方运行时依赖（matplotlib 仅出图）。测试与验收脚本：`tests/test_tenancy_gate.py`、`tools/tenancy_gate/acceptance.py`。

## 3. 计算结果

### 3.1 判据总表（程序化取自 `artifacts/m7876/acceptance.json`）

| 判据 | 要求 | 实测 | 判定 |
|---|---|---|---|
| (a) 检出率 | 12/12 | 12/12 | PASS |
| (b) 假阳性 | 0/8 | 0/8 | PASS |
| (c) 幂等重放 | 3 次逐字节一致 | 矩阵 一致 / 台账 一致 / 事件数 1 | PASS |
| (d) fail-closed | 4 类 unknown 全部拒绝放行 | 4/4 | PASS |
| (e) 性能 | 1000 分支两两预演 <60s | distinct=7.026s / shared_clean=9.687s / same_file=19.074s | PASS |
| (f) 零领域语义 | deny-list 0 命中 | 0 命中 / 13 文件 | PASS |
| (g) pytest | ≥15 | 67 passed | PASS |
| (4) 台账可复算 | 入库 + 查询 + 复算一致 | 20 事件 / 链 OK / 复算 一致 | PASS |

### 3.2 逐场景判别（预注册 20 例）

| ID | 组 | 门 | 场景 | 实测 | 判定 |
|---|---|---|---|---|---|
| L1 | detect | lease | 同一仓同一条路径的写租约 | admitted=False | PASS |
| L2 | detect | lease | 目录前缀租约 vs 其内部文件 | admitted=False | PASS |
| L3 | detect | lease | globstar 尾部租约 vs 深层文件 | admitted=False | PASS |
| L4 | detect | lease | 段内通配 docs/m*/report.md vs 具体任务目录 | admitted=False | PASS |
| L5 | detect | lease | 全仓写租约 ** 与任意写租约冲突 | admitted=False | PASS |
| L6 | detect | lease | 受保护路径（版本库元数据） | admitted=False | PASS |
| L7 | detect | lease | 中段 globstar 嵌套租约 | admitted=False | PASS |
| L8 | detect | lease | 预留越出可写根（配置注入 writable_roots） | admitted=False | PASS |
| I1 | detect | intake | 同窗口同一文件被两任务改动 | decision=block | PASS |
| I2 | detect | intake | 三个同窗口任务里两个文件重叠、一个不重叠 | decision=block | PASS |
| I3 | detect | intake | 改到对方的事件留（lease_breach），对方提交列表尚未包含该文件 | decision=block | PASS |
| I4 | detect | intake | 收单文件越出自己声明的预留面 | decision=block | PASS |
| P1 | clear | lease | 同仓兄弟目录互不相交 | admitted=True | PASS |
| P2 | clear | lease | 只读租约不阻塞写租约 | admitted=True | PASS |
| P3 | clear | lease | 过期租约不参与判定（记录到 expired_dropped） | admitted=True | PASS |
| P4 | clear | lease | 同目录下不相交的通配前缀 | admitted=True | PASS |
| P5 | clear | intake | 同窗口但变更文件互不相交 | decision=admit | PASS |
| P6 | clear | intake | 同文件但对方在窗口之外 | decision=admit | PASS |
| P7 | clear | intake | 不同仓同路径不算冲突（工作树互不共享） | decision=admit | PASS |
| P8 | clear | intake | 路径模式相交但文件级无重叠、且不落在对方写留内：只记风险不阻塞 | decision=admit | PASS |

### 3.3 性能（1000 条分支，全部两两组合 = 499,500 对）

| 分支布局 | 对数 | 预筛放行 | merge-tree 调用 | 冲突对 | unknown 对 | 总耗时(s) | 纯两两比对(s) | 变更集物化(s) | 状态 | 放行 |
|---|---|---|---|---|---|---|---|---|---|---|
| distinct | 499500 | 499500 | 0 | 0 | 0 | 7.026 | 0.143s | 2.533s | clean | True |
| shared_clean | 499500 | 499310 | 190 | 0 | 0 | 9.687 | 0.144s | 3.032s | clean | True |
| same_file | 499500 | 0 | 600 | 600 | 498900 | 19.074 | 0.17s | 3.257s | unknown | False |

解读：

* `distinct`（全部改各自独有文件）：499,500 对全部被预筛放行，**0 次** merge-tree，7.026s —— 主体成本是 1000 次 `git diff`（物化变更集）。

* `shared_clean`（20 条分支对同一文件做相同改动）：190 个候选对真跑 merge-tree，全部 clean，9.687s。

* `same_file`（全撞同一文件的最坏面）：499,500 对全是候选，预算护栏在第 600 次调用后停止，其余 498900 对记 unknown，整体 fail-closed（`allowed=false`），总耗时 19.074s —— **时间被预算封顶，而不是随 N² 爆炸**。

### 3.4 台账

| 项 | 值 |
|---|---|
| 台账文件 | artifacts/m7876/ledger.jsonl |
| 事件数 | 20 |
| 链表校验 | OK |
| 可复算的 intake 事件 | 8 条，全部一致 |
| 查询 demo | {"all": 20, "lease_decision": 12, "intake_matrix": 8, "repo_TenantA": 20, "mission_m2001": 1, "window_after": 0} |
| 台账摘要 | `dbb64822882a2c3cd4f93202274df365526bfde76d6f9fd0bdf48665c0348490` |

### 3.5 图

**fig_scenarios.png** — 逐场景冲突单位数（绿=与预注册期望一致，红=不一致）；左 12 个为必须检出，右 8 个为必须零假阳性

**fig_performance.png** — 三种分支布局下的批量预演耗时；柱标为真实 merge-tree 调用次数（预算护栏限流）

**fig_intake_matrix.png** — 12 个同窗口任务的冲突矩阵（行=peer 任务，列=lane，红=命中）；三条阻塞 lane 与一条记录 lane 互斥组合

## 4. 注意点（踩过的坑与局限）

1. **`git merge-tree -z` 的输出不是“纯文件名列表”**：冲突时信息段（`Auto-merging` / `CONFLICT`）也以 NUL 分隔混在流里，直接 split 会把消息当路径。实现里优先用 `--write-tree --name-only -z --no-messages`（git ≥2.40），并对旧 git 保留分节解析退化路径。

2. **预筛不是“判定”，是“判定前的剪枝”**：`prescreen_disjoint` 放行的依据是「无共同路径 ⇒ git 无法产生内容冲突」。这在默认合并策略下成立；若将来引入 rename 跟踪/自定义 merge driver，该前提需重新论证（配置 `rehearsal.prescreen=false` 可退回全量 merge-tree）。

3. **hunk 级区间是“近似”而非“逐字节”**：用 `git diff -U0` 的行区间求交，覆盖「两边改了同一批行」的典型冲突；对相邻行的语义冲突可能报 `path_level`（`kind` 字段明示）。文件级结论（能否合并）始终以 `merge-tree` 退出码为准，不受此近似影响。

4. **租约过期即失效**：`ttl_seconds` 到了之后同一片路径可被他人预留。这是刻意的时间换空间（避免僵尸租约永久锁死路径），代价是长任务的租约需要续期（续期=追加一条新租约事件，旧事件留痕）。

5. **台账无锁**：`append_events` 是读-判重-追加，多进程同时写同一文件需要用宿主应用的锁（平台侧已有单写者约定）；本节点未实现跨进程锁，属已知局限。

6. **`unknown` 不区分“坏”与“未知”**：门只输出 `allowed=false`，原因码保留在 `reason`，运维要据此分流（仓路径错 vs merge-tree 版本不兼容）。

## 5. 误差判定

本节点是机制类（离散判定），没有连续物理量，因此**没有“与解析解/文献的百分比误差”**；等价判据是「裁决与预注册期望是否逐条一致」：

| 关键量 | 基准 | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 冲突检出率 | 12（预注册 detect 组） | 12 | 0/12 = 0% | 是（0%） |
| 假阳性数 | 0（预注册 clear 组） | 0 | 0/8 = 0% | 是（0%） |
| 幂等一致性 | 3 次重放逐字节相同 | 3/3 相同 | 0 字节差异 | 是（0%） |
| fail-closed 覆盖 | 4/4 类 | 4/4 | 0 缺口 | 是（0%） |
| 性能 | <60s | max 19.074s | -68.2% vs 60s 预算 | 是 |
| 零领域语义命中 | 0 | 0 | 0 | 是（0%） |

**显式回答**：全部关键量相对基准误差为 0（离散判据，非统计量），且均 <3%。唯一带时间量纲的判据（性能）在 60s 预算线内有 40.9s 余量。

## 6. 审计链与复现

```bash
# 全量验收（约 60s，含 1000 分支夹具与出图）
python3 tools/tenancy_gate/acceptance.py --out artifacts/m7876 --figures
# 只跑测试
python3 -m pytest tests/test_tenancy_gate.py -q
# 台账校验 + 查询
python3 -m tools.tenancy_gate.cli --ledger artifacts/m7876/ledger.jsonl verify
python3 -m tools.tenancy_gate.cli --ledger artifacts/m7876/ledger.jsonl query --kind intake_matrix
```

验收 JSON 自摘要：`861c7e5cc7703ee13e0abbc94a24cb85a09c8387983bbdb45eae78510f0c73cc`。注意：该摘要覆盖整份快照（含 `elapsed_s` 与出图信息），故**跨次运行会变**；而逐场景/逐矩阵的 `digest` 只由内容派生，跨次运行稳定——复算判据（c）比对的正是后者。

产物清单：`artifacts/m7876/acceptance.json`（判据全量数字）、`artifacts/m7876/ledger.jsonl`（20 条冲突/裁决事件）、`artifacts/m7876/ledger_report.json`（链校验 + 复算）、`artifacts/m7876/fig_*.png`（3 张图）。

## 7. 集成契约（接进平台流水线的方式，本次未改动生产路径）

本节点交付机制与判据，**没有**改动 `ops/taskplane/tp_pusher.py` 等生产路径——线上管线切换属于需要单独评审的变更。接入点与调用形态如下（退出码即裁决：0 放行 / 2 拒绝）：

| 流水线位置 | 调用 | 裁决语义 |
|---|---|---|
| 派单前（生成任务卡 + EDIT_WHITELIST 时） | `cli lease-check --active <在租租约> --candidate <本单租约>` | 退出码 2 ⇒ 拒派或按 `lease.on_conflict=downgrade_readonly` 降级为只读任务 |
| 收单（finish 上报 changed_files 时） | `cli intake --finish finish.json --peers peers.json` | 退出码 2 ⇒ 不入队合并，按 `reason` 分流（冲突/越界/非法路径） |
| 推送前（tp_pusher 合并前） | `cli rehearse --repo <裸仓> --ours agent/m<id> --theirs main` | 退出码 2 ⇒ 停推；`state=unknown` 与 `conflict` 同等待遇（fail-closed） |
| 批量治理（分支堆两两预演） | `cli rehearse-batch --repo <裸仓> --pairs pairs.json` | 一次性给出 1000 分支两两预演结论，超预算的对记 unknown |
| 查询/审计 | `GET /tenancy/conflicts`、`GET /tenancy/conflicts/verify`、`POST /tenancy/conflicts/recompute` | 只读；返回台账事件、链表校验、按输入重算的矩阵 |

宿主应用挂载查询路由（鉴权/限流由宿主中间件承担）：`app.include_router(tools.tenancy_gate.api.create_router(ledger_path))`。
