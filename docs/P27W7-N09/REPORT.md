# 多租户并发写冲突检测与合并预演门：调度器标准门成熟化（P27W7-N09 / m8057）

**一句话**：把上一代"三段判定各自为战"的并发写门，成熟化成**一条可被平台派单器直接调用的标准门流水线**——路径预留（冲突排队 FIFO）→ changed_files 交集检测 → 三方合并 dry-run → 统一退出码 + 可复算台账；并给出独立参考实现对照的 100% 检出率验收单。

---

## 1. 案例介绍

### 1.1 工程背景（为什么需要"门"而不是"约定"）

平台按 **12 仓租户制**运行，单个任务在自己的工作树里干活，完成后由平台自动推分支合并；并发度按 40 路设计。写面重叠在三个位置同时出事：

1. **派单时**——两个任务被允许写同一片路径，后者的写入静默覆盖前者（工作树级丢失，事后无痕）；
2. **收单时**——A、B 都"完成"了但改了同一文件，谁先合谁后合决定谁的内容活下来，而"谁活下来"没人记录；
3. **推送时**——分支相互冲突，合并失败或产生半合并，而"合并失败"与"内容不对"事后无法区分。

上一代实现（`docs/m7876/REPORT.md`）已经给出三段判定内核（租约 / 冲突矩阵 / merge-tree 预演），但存在三个成熟化缺口：

| 缺口 | 上一代状态 | 本节点补齐 |
|---|---|---|
| G1 预留只判不排 | 冲突即 `admitted=false`，没有排队语义 | 状态机化的 FIFO 队列 + `drain_queue()` 释放后自动放行（幂等） |
| G2 三段不串 | 三个判据分别调用，无统一裁决 | `decide()` 复合裁决：统一 `exit_code` + `resolution` + 逐阶段 digest |
| G3 不进调度器 | 只能由运维手跑 CLI | `dispatch_hook` 接线层 + `dispatch_helper.py --write-paths`，三模式（off/report/enforce） |

### 1.2 "参数表"（机制参数，全部由配置注入，代码零业务常量）

| 参数 | 默认值 | 语义 |
|---|---|---|
| `scheduler.on_conflict` | `queue` | 预留冲突处置：`queue`（FIFO 排队）/ `refuse`（拒派）/ `downgrade_readonly`（降级只读） |
| `scheduler.queue_limit` | 64 | 队列上限；满后新冲突任务 `refuse`（防止无限堆积） |
| `scheduler.max_finishes` | 256 | 状态文件保留的收单记录条数（交集检测的回溯窗口） |
| `scheduler.on_merge_clean` | `auto_merge` | 可自动合并 → 放行（`exit_code=0`） |
| `scheduler.on_merge_conflict` | `manual_arbitration` | 内容冲突 → 人工仲裁（带 hunk 级区间） |
| `scheduler.on_unknown` | `rollback` | 无法判定（仓不可读 / ref 不可解析 / merge-tree 失败 / 超时）→ 回滚，fail-closed |
| `lease.ttl_seconds` | 21600 | 预留生存期；过期预留不参与冲突判定 |
| `lease.protected_globs` | `[".git/**"]` | 任何情况下不许预留 |
| `intake.window_seconds` | 86400 | "同窗口"= 收单时间差上限 |

### 1.3 状态机

```
派单 ──► reserve() ──pass──► 施工 ──► complete() ──pass──► 推送前 merge_dry_run() ──clean──► auto_merge (0)
              │                          │                          │
              ├─conflict─► queue (10) ──drain_queue()──► pass       ├─conflict─► manual_arbitration (11)
              └─hard reason / 上限 ─► refuse (13)                   └─unknown──► rollback (12)
```

退出码取"最严"者：`13 > 12 > 11 > 10 > 0`。**门级**词表为调度语义（0/10/11/12/13），**单阶段** `merge_dry_run` 保留 git 约定（0 可合并 / 1 内容冲突 / 2 无法判定），两者在报告里分别标注、不混用。

---

## 2. 怎么做的

### 2.1 工具链与文件面

| 文件 | 作用 | 行数 |
|---|---|---|
| `tools/tenancy_gate/scheduler_gate.py` | 调度器标准门：预留 / 排队 / 交集 / 合并预演 / 复合裁决 / 运维 CLI | 609 |
| `tools/tenancy_gate/dispatch_hook.py` | 派单器接线层：三模式（off/report/enforce）+ fail-closed 降级 | 144 |
| `tools/tenancy_gate/config.py` | 新增 `scheduler` 配置段与合法性校验（未知键报错，不静默失效） | +24 |
| `tools/agent/dispatch_helper.py` | 平台派单助手：`--write-paths` + `gate_preflight()` / `gate_post_finish()` + 占位 id 改挂真实 mission id | +55 |
| `tests/test_tenancy_gate_scheduler.py` | 58 条 pytest 回归（预留/排队/交集/合并/接线/零语义/摘要可复现） | 495 |
| `tools/tenancy_gate/maturation_acceptance.py` | 验收单生成器（含**独立参考实现**、场景生成、出图、表格式报告注入） | 662 |

运行环境：Python 3.11.6 / git 2.41.0 / pytest；无第三方运行时依赖（matplotlib 仅出图）。

### 2.2 关键设计决定

1. **独立真值**：验收单自带一个与被测模块无关的 globstar 匹配器（正则单段 + 递归多段），逐对穷举"见证路径"判定两模式是否可能相交。被测实现（`pattern.py` 的段积 BFS）与它**不共享代码**——否则"用被测实现验证被测实现"是同义反复。
2. **状态与台账分离**：并发安全状态（在册预留、队列、收单记录）落 `scheduler_state.json`（原子替换写 + 内容 digest）；判定痕迹落 append-only JSONL 台账（`gate_decision` / `lease_decision` / `gate_stage` / `queue_drain`），链表可校验、事件按 `event_id` 幂等。
3. **只读 git**：合并预演只调 `rev-parse` / `merge-base` / `diff` / `merge-tree`，不建删 ref、不写工作树与索引、不改 `.git` 配置——门本身不制造冲突。
4. **可复算**：裁决 `digest` 只由判定内容派生（耗时 `elapsed_ms` 不进摘要），同一输入（含 `ts`）重跑必得同一 `digest`；`test_decide_reproducible_digest` / `test_state_file_is_deterministic` 守住这条。
5. **fail-closed 优先**：`enforce` 模式下状态文件损坏、依赖缺失、git 超时一律 `exit 12 rollback`；`report` 模式（默认）记录但不拦单——**先量测真实冲突率再升级强制**，避免机制上线当天卡死整个派单面。

### 2.3 验证流程（命令逐条）

```bash
# 1) 新门回归（57 条）
python3 -m pytest tests/test_tenancy_gate_scheduler.py -q
# 2) 旧门回归（67 条，确认没有把既有判定改坏）
python3 -m pytest tests/test_tenancy_gate.py -q
# 3) 验收单（判据数字 + 出图 + 报告表格程序化注入）
python3 tools/tenancy_gate/maturation_acceptance.py \
    --out artifacts/p27w7_n09 --figures --report docs/P27W7-N09/RESULTS.md
# 4) 运维 CLI 冒烟
python3 -m tools.tenancy_gate.scheduler_gate --state /tmp/s.json --ledger /tmp/l.jsonl \
    reserve --mission m1 --repo Repo --paths "src/**" --ts 2026-11-09T00:00:00Z
```

---

## 3. 计算结果

> 本节数字全部来自 `artifacts/p27w7_n09/acceptance.json`（digest `5e618b88…`，`overall_pass=true`；两次独立运行摘要逐字节相同），
> 表格由 `maturation_acceptance.py` 程序化生成到 `docs/P27W7-N09/RESULTS.md`，**零手抄**。

### 3.1 判据总表

| 判据 | 场景数 | 达标 | 比率 | 阈值 | 判定 |
|---|---|---|---|---|---|
| A1 同路径并发冲突检出 | 33 | 33 | 100.0% | 100% | PASS |
| A2 changed_files 重叠命中（与独立 oracle 逐文件集比对） | 20 | 20 | 100.0% | 100% | PASS |
| A3 三方合并 dry-run 可自动合并 → `exit_code=0` | 4 | 4 | 100.0% | 100% | PASS |
| A3b 内容冲突 → `exit_code=1` 人工仲裁 | 2 | 2 | 100.0% | — | PASS |
| A3c 无法判定 → `exit_code=2` 回滚 | 1 | 1 | 100.0% | — | PASS |
| A4 负控：无重叠路径无冲突 | 47 | 47 | 100.0% | 100% | PASS |
| A4b 负控：无重叠文件无冲突 | 20 | 20 | 100.0% | 100% | PASS |
| A5 负控：单任务独占路径无冲突 | 1 | 1 | 100.0% | 100% | PASS |
| A6 调度器接线（三模式 + 台账链 + 回归 125 条） | 7 检查 | 7 | 100.0% | 100% | PASS |
| A7 零领域语义（denylist 35 词条 × 16 文件） | 16 | 16 | 命中 0 | 0 | PASS |
| A8 排队动力学（FIFO + 幂等 drain） | 6 任务 | 6 | 100.0% | 100% | PASS |

**独立参考实现一致率 80/80 = 100%**：全部 80 个路径对场景中，门判定与自带 oracle 判定完全一致。

### 3.2 三方合并 dry-run 场景族

| 场景族 | 期望 | 实测 | 退出码 | 裁决 |
|---|---|---|---|---|
| 两分支改不同文件 | clean | clean | 0 | auto_merge |
| 同文件不同 hunk（第 3 行 vs 第 36 行） | clean | clean | 0 | auto_merge |
| 同文件两处不同区域（5-6 行 vs 20-21 行） | clean | clean | 0 | auto_merge |
| 两分支改不同文件（重复场景，验证确定性） | clean | clean | 0 | auto_merge |
| 两分支改同一行（第 12 行） | conflict | conflict | 1 | manual_arbitration |
| 同上（重复场景） | conflict | conflict | 1 | manual_arbitration |
| 仓不可读 | unknown | unknown | 2 | rollback |

注意"**同文件不同 hunk → 可自动合并**"这一族是 A3 的核心：它证明门不是"只要碰同一文件就拦"，而是真的做了三方合并判定——这正是上一代"文件级交集"无法回答的问题。

### 3.3 排队动力学（确定性时间轴）

| 阶段 | 任务 | 裁决 | 队列位置 |
|---|---|---|---|
| 入队 | q0 / q1 / q2 / q3 / q4 / q5 | queued ×6 | 1 … 6 |
| 释放 `owner1`（`src/**`）后 drain | q0 | admitted | 剩余 5 |
| 释放 `owner2`（`docs/**`）后 drain | q1 | admitted | 剩余 4 |

两轮释放共放行 2 个；剩余 4 个因**同文件写面仍互斥**（q2/q4 抢 `src/a.py`，q3/q5 抢 `docs/x.md`）继续排队；重复调用 `drain()` 判定稳定、无新增入账（幂等）。

### 3.4 图

- **图 1**（`fig1_decision_rates.png`）：四条判定率柱状图（同路径检出 / 交集命中 / 无重叠路径清 / 无重叠文件清）全部 100%，红线为阈值。
- **图 2**（`fig2_merge_dryrun_exit_codes.png`）：各场景族 `merge_dry_run` 退出码矩阵，绿=可自动合并(0)、橙=人工仲裁(1)、红=回滚(2)。
- **图 3**（`fig3_queue_dynamics.png`）：队列深度随入队/释放的动力学曲线，验证 FIFO 不插队与幂等性。

---

## 4. 注意点（踩过的坑与局限）

1. **前导 `**` 的模式会被 fail-closed 拒派**：`**`、`**/foo.py` 与受保护面 `.git/**` 可能相交，门直接判 `protected_path` 而非放行。这是刻意行为（宁可拒派也不放行未知写面），但意味着**派单方必须声明具体写面**（`src/**` 而不是 `**`），否则永远进不了门。
2. **"可自动合并"不等于"无冲突风险"**：两分支改同一文件不同 hunk 会被 git 自动合并，语义冲突（例如同一条不变量被两侧各改一半）机器判不出来。门只保证"不会静默丢写"，不保证"合并后语义正确"——后者仍需评审。
3. **`report` 是默认模式**：机制上线默认只记录不拦单。要真拦单必须显式设 `TENANCY_GATE_MODE=enforce`。这是有意的渐进路线（先量冲突率），但也意味着**默认状态下门不提供强制力**，运维需按需切换。
4. **队列不跨进程加锁**：状态文件用原子替换保证"不写坏"，但没有文件锁——两个进程同时 `reserve` 存在 lost-update 窗口。40 并发下建议由一个调度进程串行调用（当前平台派单链路即如此），跨进程强一致需加 flock 或换数据库（未做，如实记录）。
5. **TTL 判定用调用方 `ts`**：为保证可复算，过期判定基于传入时间戳而非墙钟；生产调用方若传错时间戳，等于自废 TTL 保护。
6. **窗口只比 changed_files 交集**：收单侧的语义冲突（同一文件不同区域）由合并预演兜底，交集检测只负责"同文件必查"。

---

## 5. 误差判定（关键量 vs 基准，逐项）

本节点是平台机制（离散判据），基准 = **独立参考实现** 与 **git 自身行为**（可执行真值），不是实验测量值。

| 关键量 | 基准 | 我方结果 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 同路径冲突检出率 | 独立 oracle 判定（80 场景） | 33/33 = 100% | 0% | ✅ 是（0%） |
| changed_files 重叠命中 | 集合交集真值（20 场景，逐文件集精确比对） | 20/20 = 100% | 0% | ✅ 是（0%） |
| 负控假阳性率 | 独立 oracle 判"不相交"（47 场景） | 0/47 = 0% | 0% | ✅ 是（0%） |
| 合并预演判定一致率 | `git merge-tree` 退出码（7 场景） | 7/7 = 100% | 0% | ✅ 是（0%） |
| 裁决可复算性 | 同输入重跑 digest 相等 | 一致 | 0 bit | ✅ 是 |
| 台账链完整性 | sha256 链式事件 | 4 事件链完整 | 0 | ✅ 是 |

**结论**：全部离散判据误差为 0，满足"<3%"要求。声明适用边界：这只证明**机制判定与基准判定一致**，不证明"40 并发实盘零冲突"——实盘冲突率属于观测量，由 `report` 模式台账累积（本轮未接入生产流量，见 §6）。

---

## 6. 诚实申报（做了什么 / 没做什么）

**做了**：调度门流水线（预留/排队/交集/合并/裁决）、派单器接线（三模式）、57 条回归测试、验收单（9 项判据全 PASS）、运维 CLI、配置校验、零领域语义扫描、本报告与三张图。

**过程中发现并修掉的缺陷（系统性排查记录）**：首轮验收单两次独立运行摘要不一致（`ac6be672…` vs `db504462…`）。定位：`merge_dry_run` 把 `repo_path` 与 `merge-base` 解析出的 commit oid 一起放进了摘要——同一份内容在两个临时目录里 commit oid 必然不同，摘要就跟着变；同时验收单自身把 `elapsed_seconds`、figure 路径、pytest 墙钟后缀算进了摘要。修复：摘要只覆盖**判定内容**（状态/退出码/冲突文件/hunk 区间/ref 名/ts），观测字段（`repo_path`/`base_oid`/`rehearsal_digest`/耗时/图路径）留在报告里但不入摘要；并加回归 `test_merge_digest_reproducible_across_independent_repo_copies`（两次独立建仓摘要必须相同、仓路径等观测字段不得污染摘要）。修复后两次独立运行摘要逐字节一致（`5e618b88…`）。

**没做（如实）**：① 状态文件跨进程文件锁未实现（单调度进程安全，多进程有 lost-update 窗口）；② 未接入生产派单流量实测冲突率（`report` 模式已就绪，需平台侧开启 `--write-paths` 才产生真实台账）；③ 语义级冲突（合并成功但逻辑矛盾）未覆盖，属评审范畴；④ 与 `backend/services/scheduler_service.py`（cron 调度器）未接线——本节点接线点选在**任务派单链路** `tools/agent/dispatch_helper.py`，因为并发写面发生在那里。

**回滚**：新增模块为独立文件，回滚只需 `TENANCY_GATE_MODE=off`（或删除 `--write-paths` 参数）；`config.py` 的 `scheduler` 段有默认值，旧配置不受影响；台账 append-only，无需迁移。

---

## 7. 复现清单

| 产物 | 路径 |
|---|---|
| 验收单（JSON，含全部场景行与 digest） | `artifacts/p27w7_n09/acceptance.json` |
| 自动生成判据表 | `artifacts/p27w7_n09/RESULTS.md` = `docs/P27W7-N09/RESULTS.md` |
| 图 1–3 | `artifacts/p27w7_n09/fig1_decision_rates.png`、`fig2_merge_dryrun_exit_codes.png`、`fig3_queue_dynamics.png` |
| 回归测试 | `tests/test_tenancy_gate_scheduler.py`（57 条）+ `tests/test_tenancy_gate.py`（67 条） |
| 门实现 | `tools/tenancy_gate/scheduler_gate.py`、`tools/tenancy_gate/dispatch_hook.py` |
| 接线点 | `tools/agent/dispatch_helper.py`（`--write-paths` / `gate_preflight` / `gate_post_finish`） |