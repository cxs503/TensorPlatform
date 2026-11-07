# 合并口自动化：PR 判据机（merge_master / TensorPlatform 覆盖）

> 战役节点 IN20-R3（续航军 R3）。目标：把「PR 能不能自动合进 main」从 owner 手并
> 变成机器判 —— 判据全绿则自动合并并回写任务状态；不满足则留在队列并附理由码。
> 机制必须**脱离任何人类会话与 AI 协调者会话**独立运行（容器 daemon + 审计链 +
> 回滚路径），并在治理预算内动作。**零领域语义**：判据只认记录/证据/状态字段。

代码：`tools/merge_master/`（9 个模块，纯 stdlib）
测试：`tests/test_merge_master.py`（17 例）
运行产物：`artifacts/merge_master/`（判决 JSON + 哈希链审计 + 证据图 + 证据摘要）
守护：`ops/merge_master/daemon.sh`、`deploy/merge_master/compose.yml`

---

## 1 判据（pre-merge gate）

| 门 | 输入（provider） | 判定口径 | 失败理由码 |
|---|---|---|---|
| **G1 acceptance** | 任务系统验收记录 | `accepted=true` 且（`decided_by` 或 `report_id` 存在），且状态不属 queued/running/failed | `no_mission_record` / `acceptance_missing` |
| **G2 quality** | 分支 check-runs + main 基线 | 无「**新增**红」：失败检查名 ⊆ main 上已失败名；存在未完成检查亦不通过 | `quality_red` |
| **G2b boundary** | PR 净变更 diff + `data/boundary/pr_boundary_lexicon.json` | `tools/boundary/pr_gate_scan.py` 判决必须 PASS；**拿不到 diff / 门自身故障 → 保守判拒** | `boundary_gate_fail` / `boundary_gate_error` |
| **G3 tests** | 任务记录 `checks` 文本 + 测试类检查 | `N passed`（N≥`--min-tests`，默认 1）且无 failed；或测试类检查绿 | `test_evidence_missing` |
| **G4 conflict** | GitHub 合并性 oracle | `mergeable=true` 且 `mergeable_state ∉ {dirty, conflicting}`；`mergeable=null`（异步未算完）视为未定 | `merge_conflict` / `pr_closed_unmerged` |

「绝对红」口径会把 main 上早已存在的失败算到 PR 头上、使队列永久堵死；本节点沿用
merge_master 既有经验，判 **PR 是否新增红**（责任边界）。边界门（plan 19 B1）作为
**唯一的、永远阻断的**代码路径门接进 pre-merge 链：它不依赖 CI 是否配置、也不依赖
任何人工 review。

**幂等与崩溃恢复**：判据按 `head SHA` 记账；PR 已是 `merged` 事实时只补一次
`pr-gate(merged)` 回写（任务状态 → `succeeded`），不再触发合并。合并成功后进程崩溃
也能由下一轮补齐，不会二次合并。

**预算**：每轮 `--max-merges`（默认 3）、`--max-mutations`（默认 8，合并=2 次变更
调用）、`--max-prs`（默认 60）。超限的 PR **不丢**，落 `queue` 并附
`budget_exhausted`，下一轮继续。

**审计与回滚**：每次判决/合并/回写都写一条 JSONL 且带**前序哈希**（篡改或删行必被
`verify-audit` 指出行号）；每次合并同时落 `rollback` 规格（`git revert -m 1 <merge
commit>` + 验证门 + 状态修复），由 `tools/merge_master/rollback.py` 照单执行。

## 2 三种运行形态（同一套判据与审计）

| 形态 | 命令 | 写动作 | 用途 |
|---|---|---|---|
| plan（默认） | `--out plan.json` | 无 | 巡检 / 证据固化 / 队列归因 |
| rehearsal | `--rehearse` | **记录式桩**（不触网、不改任务库） | 在真实队列上跑通「合并→回滚规格→回写→审计」全链路 |
| execute | `--execute` | 真合并 + 真回写（budget 内） | 治理动作，默认关闭；`--execute` 与 `--disable-gate` 互斥（机制层拒绝） |
| daemon | `daemon --interval 300` | 同上，常驻 | 容器形态，脱离会话运行 |

守护形态：`compose.yml` 里 `restart: always` + healthcheck 跑 `verify-audit`（审计链
断了容器判不健康，而不是默默继续合并）+ 状态卷持久化。合并开关是环境变量
`TP_MM_ALLOW_EXECUTE`（默认 0=只判不并），打开是治理决策，不需改代码。

## 3 实测：真实队列 20 张 PR 清跑

数据来源：GitHub 开放 PR（ground truth）+ 平台任务账本最近 20 张带 PR 的任务记录，
逐张取详情/检查结果/diff 过门。仓库 = `TensorPlatform`。

| 轮次 | 总量 | 合并/计划合并 | 留因 | 幂等跳过 | 说明 |
|---|---|---|---|---|---|
| plan（live queue） | 20 | 0 | 4 | 16 | 判据基线 |
| rehearsal（执行链路） | 20 | 0 | 4 | 16 | 桩动作证明回写/审计/回滚全链路 |
| ablation（关边界门） | 20 | 2（排练合并） | 2 | 16 | 反事实：边界门是 2 张 PR 的唯一阻断项 |

门判决统计（三轮合计，只统计真正进门的 12 次评估）：G1 0 失败 / G2 0 失败 /
G3 0 失败 / **G2b 4 失败（4/4）** / **G4 6 失败（6/12，两只 PR 每轮都判 dirty）**。

留因归因（真实理由，逐张可查 `artifacts/merge_master/*.summary.json`）：

| PR | 判据结果 | 理由码 | 具体证据 |
|---|---|---|---|
| #635 | 边界门红 | `boundary_gate_fail` | 16 处越界，集中在 `tools/repro_digest/csrc/*.c` 的注释/标识符（把物理工况写进平台工具代码） |
| #633 | 边界门红 | `boundary_gate_fail` | 1 处，`tests/test_autonomy_dashboard.py` 里的领域词元组（能力名清单字面量） |
| #631 | 冲突 | `merge_conflict` | `mergeable_state=dirty` |
| #626 | 冲突 | `merge_conflict` | `mergeable_state=dirty` |

审计链：166 条记录（cycle_start 9 / decision 114 / merge 2 / writeback 34 /
cycle_end 7），`verify-audit` 链校验通过，head=`ebd44687cf380c3c…`。
本节点自身代码过边界门：12 文件 / 2336 行 / **0 越界 0 弱词提示**。

反事实（ablation，仅排练写入桩）：关掉边界门后 #635、#633 立刻走通
「合并 → 回滚规格 → `pr-gate(merged)` 回写 → 任务状态 succeeded」全链路 —— 说明执行
链路本身可用，这两张 PR 的阻塞点**唯一且明确**是边界越界。

## 4 注意事项与局限（如实记）

1. **真实执行未开启**：本节点以 plan/rehearsal 验证机制，未对他人 PR 发出真合并。
   理由：合并他人交付物是治理动作，`--execute` 默认关闭且需 `TP_MM_ALLOW_EXECUTE=1`；
   机制正确性由“排练（真实数据全链路）+ 17 例 pytest（真执行断言）”两侧证据覆盖。
2. **队列规模**：清跑窗口 = 4 张真实开放 PR + 16 张已合并 PR（幂等复判），共 20 张；
   开放 PR 不足 10 张是当前世界状态，不是机制限制（`--max-prs` 上限 60）。
3. **边界门精度**：#633 的命中是「领域词作为能力名清单字面量」，属**真阳性但性质
   偏治理而非越界**。建议（未在本节点实施，属另一治理决策）给词表加
   `exempt_data_literals` 一类作用域，或把该清单外置到 `data/`。当前口径是宁可
   误拒也不放行（fail-closed），并把它写进留因理由供人 review。
4. **GitHub 合并性异步**：`mergeable` 首次取常为 `null`，已做一次 1.2s 轮询；仍为
   null 时判 `merge_conflict` 留待下轮（不放行未判）。
5. **回滚是「可执行规格」而非自动回滚**：`rollback.py --execute` 需显式调用；自动
   回滚会引入新的治理风险（二次事故），刻意为人工/机制显式触发。
6. **任务账本索引**：通过 `/tensor/missions` 分页构建 `repo#pr → mission` 映射（默认
   8 页 × 500）。更早的任务可能不在窗口内 → 判 `no_mission_record` 留因（保守）。

## 5 复现

```bash
python3 -m pytest tests/test_merge_master.py -q                 # 17 passed
python3 -m tools.merge_master.cli selfcheck                      # 判据件/词表/凭据自检
python3 -m tools.merge_master.cli plan --repos TensorPlatform \
    --include-recent 20 --out plan_live.json                     # 真实队列清跑
python3 -m tools.merge_master.cli plan --rehearse --include-recent 20 \
    --out rehearsal.json                                         # 执行链路排练
python3 -m tools.merge_master.cli verify-audit                   # 审计链校验
python3 scripts/gen_merge_master_figures.py                      # 证据图
python3 scripts/merge_master_evidence.py --pytest-passed 17      # 证据摘要
```

## 6 runbook（出事怎么办）

| 事件 | 动作 |
|---|---|
| 合并后发现坏了 | `rollback.py <spec> --execute`（规格在审计链 `merge` 记录里），再把任务 `pr-gate(open)` 打回 |
| 审计链校验失败 | 容器 healthcheck 已判不健康；查 `first_bad.line`，冻结执行开关 |
| 队列只增不减 | 看 `queue` 理由码分布：全是 `boundary_gate_fail` → 词表/豁免口径要 review；全是 `merge_conflict` → 走冲突重做线 |
| 预算挡太多 | 调 `TP_MM_MAX_MERGES`；不要关判据 |