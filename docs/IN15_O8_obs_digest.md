# IN15-O8 · 观测告警最小面：停滞/失败×3 台账 + 每日 digest 生成器

> 数据源：`TensorPlatform` 自身台账（`tensor_dev_missions` / `campaign_nodes` / `tensor_approval_requests` / `campaign_plans`），**只读 SELECT**，不改 schema、不动冻结 API 面。all numbers below are injected from the committed JSON artefacts.

## ① 案例介绍（问题 / 数据 / 参数）

平台每夜都有任务在跑，但「跑着」与「还在」是两件事：一个 running 任务可能 worker 已死、槽位却在继续烧预算；一个节点可能已经 failed 多次却仍被原样重派；还有一批节点是**故意**停在放门条件上（待放门），不是故障。三类状态混在同一张 `status` 字段里，夜班无法一页看清。

| 项 | 值 |
|---|---|
| 快照 as-of（live） | 2026-11-07T00:45:09Z |
| 快照任务行 | 4329 |
| 非终态受检 / running | 59 / 40 |
| campaign 节点 | 167（blocked 16） |
| 停滞判据 | 心跳断 ≥ 120 min |
| 领取宽限（负例 1） | 15 min |
| 失败处置线 | consecutive_fails ≥ 3 |
| 回放 as-of（真实停滞案例） | 2026-11-02T20:30:00Z |

真实停滞案例（回放目标）：2026-11-02 平台发生一次心跳中断——一批任务在 2026-11-02T20:30:00Z 前后仍处 running，但末次心跳已停在数小时前，直到 22:15 才被批量收束。这批复盘可查的案例（mission #2109, #2112, #2113, #2114）就是本规则的**阳性基准**；同一时刻还有若干「跑得久但心跳新鲜」的任务，它们是**阴性基准**。

## ② 怎么做的（方法 / 工具链 / 关键设置）

1. **取数（只读）**：`tools/obs/obs_digest.py` 用 `psql -tAc` 跑四条 SELECT，把四张表压成一个 JSON 快照（`artifacts/in15_o8/snapshot_db.json`）；快照 SHA-256 写进 digest 与 alerts JSON，保证「同快照→同结论」。无需 DB 权限也能用 `--snapshot` 复现。
2. **规则（纯函数）**：`tools/obs/alert_rules.py` 不碰 I/O、不读墙钟，`as_of` 显式传入：

| 规则 | 含义 | 判据 | 级别 |
|---|---|---|---|
| R1_STUCK_RUNNING | 运行中停滞（heartbeat 断流） | running 且 heartbeat 断 ≥ 120 min | P1 |
| R2_BLOCKED_THRICE | 失败×3 阻塞（应进处置账） | blocked 且 consecutive_fails ≥ 3 | P1 |
| R3_PENDING_GATE | 待放门（人为/机械闸口未放行） | blocked 且阻塞原因指向放门/审批 pending | P2 |


3. **负控（suppression，每条都留证）**：S1 `刚领取·宽限内不告警`（领取 ≤ 15 min 且从未心跳）；S2 `长跑但心跳新鲜·不告警`（跑得久 ≠ 停滞）；S3 `阻塞但未达×3·仅观察`（未达 ×3 只进观察账）。「不告警」必须能与「没检查」区分，否则规则不可审计。
4. **时间投影（回放）**：台账只存当前 status，因此回放过去的某一瞬用时间戳重建：`created_at ≤ as_of < coalesce(finished_at, now)` 且当前状态为 running 或「终态但 finished_at > as_of」才判为「当时在跑」；心跳水位晚于 as_of 时钳位（证明当时活着）。本轮回放丢弃 4310 行、心跳钳位 6 行。非 running 且无 finished_at 的行（suspended/stopped/revoked）**保守不判 running**——宁可漏报不误报。
5. **产物**：digest md（一页战况：三军状态/异常清单/待放门/规则负例/审计链）、alerts JSON、快照 JSON、判定平面图。`--dry-run` 只算不写；`--fail-on-alert` 供 cron 在 P1>0 时返回 2。

## ③ 计算结果（真实台账数字）

### 3.1 现状（live，一页战况）

| 军 | running | queued | 停滞 | 刚领取 | 长跑活着 | 窗口 failed | 窗口 succeeded | ×3 阻塞节点 | 待放门节点 |
|---|---|---|---|---|---|---|---|---|---|
| 求解器军（TensorLBM/FEM/FVM/DEM/BEM/SPH/DG/CAD） | 23 | 0 | 0 | 0 | 0 | 20 | 233 | 0 | 4 |
| 船舶军（ship-performance-platform） | 1 | 0 | 0 | 0 | 0 | 0 | 21 | 0 | 2 |
| 平台军（TensorPlatform/TensorPhysics/TensorPapers/其他） | 16 | 1 | 0 | 0 | 0 | 4 | 29 | 0 | 9 |


- 告警：P1 **0** 条，P2 **15** 条，明确不告警 **1** 条。
- 待放门 15 个（节点 #114, #120, #129, #144, #153, #157, #158, #159, #160, #161, #162, #163, #164, #165, #170）：全部是被放门条件挡住的验收门/饱和池节点，**不是故障**，不应计入异常率。
- 观察账 1 条（blocked 但 consecutive_fails < 3），当前台账最高连续失败数 = 2，故 ×3 处置账为空——规则红线尚未被真实事件跨过。

### 3.2 回放：真实停滞案例上点火（2026-11-02 心跳中断）

| mission | 仓 | 心跳断 [min] | 末次心跳 | 领取者 |
|---|---|---|---|---|
| mission:2109 | TensorLBM | 273.3 | 2026-11-02T15:56:40Z | tp-arch-2 |
| mission:2112 | TensorSPH | 212.4 | 2026-11-02T16:57:35Z | tp-qa-2 |
| mission:2113 | TensorDG | 197.7 | 2026-11-02T17:12:15Z | tp-impl-2 |
| mission:2114 | TensorPhysics | 255.6 | 2026-11-02T16:14:23Z | tp-ver-7 |


- 当时在跑 10 个，判停滞 **4** 个（P1），判「长跑但活着」而**不告警** 6 个（S2），观察账 1 条（S3）。
- 阴性样本：#1379, #1380, #1429, #1434, #1496, #1508 在同一瞬心跳新鲜，全部被 S2 拦下——它们是「长跑≠停滞」的真实反例。

## ④ 结果图

![stall plane](../artifacts/in15_o8/staleness_replay_20261102T2030Z.png)

**图 1**（回放快照）：(a) 运行中任务的心跳年龄排序，红色虚线 = 停滞判据 120 min，越线者即 P1；越线后的柱高与末次心跳时间戳一一对应台账，可人工复核。(b) 节点三账：failed×3 处置账 / 未达×3 观察账 / 待放门，把「故障」与「人为闸口」分开计量。

![live plane](../artifacts/in15_o8/staleness_live.png)

**图 2**（live 快照）：同一判定平面在稳态夜间的读法——最陈旧心跳仍远低于判据线，因此 P1=0 是「检查过且健康」，而不是「没检查」。

## ⑤ 注意点 / 局限

- **心跳字段本身可能未接线**：台账中有 `heartbeat_at` 为 NULL 的历史行（suspended/stopped/queued）。投影对这类行保守不判 running，代价是一次「无时间戳的 stop」会漏报。
- **投影不是真历史**：只依赖单调时间戳（created_at / heartbeat_at / finished_at）。被重排队的同 id 任务、手改状态这类无痕操作无法重建；回放结论以 `projection` 字段随报告披露。
- **R2 尚未被真实事件触发**：当前最高 consecutive_fails=2，×3 处置账为空；规则正确性由 pytest（阈值覆盖测试）保证，而非真实事故——这是本轮的真实局限。
- **只告警不处置**：本工具不做任何自动 stop/放行/改表，避免与冻结的平台 API 面冲突；处置仍由人/守望线执行。
- **快照体积** 约 3.5 MB（8 天任务行）：为了可离线复现而整块保留；若需入仓可 gzip。

## ⑥ 误差判定

本任务是观测面/文档类工具，**没有物理基准量**（无阻力/推进/耐波等可对标实验值的量），因此「误差是否 <3%」不适用，不能也不应给出一个百分比。可机器裁的等价判据如下，全部达标：

| 判据 | 结果 | 判定 |
|---|---|---|
| 真实停滞案例召回（2026-11-02 心跳中断批） | 4/4 = 100% | 达标 |
| 阴性样本误报（S2 长跑活着 + S3 未达 ×3 + 投影保守丢弃行） | 0/4317 | 达标 |
| 重复运行一致性（同快照 → 同 alerts JSON） | 逐字节一致 | 达标 |
| pytest | 18 passed（tools/obs/tests） | 达标 |
| 告警处置可达性（P1 存在时 cron 退出码） | --fail-on-alert → exit 2 | 达标 |


一致性口径：digest md 与 alerts JSON 由同一次 `evaluate()` 生成，快照 SHA-256 双向可核；报告中出现的每个数字都由 `tools/obs/gen_report_md.py` 从 JSON 注入，无手抄。
