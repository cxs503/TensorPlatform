# IN27-H1 · 器官 s2_campaign · `campaign_blocked` 越界诊断（诊断单 7a90c537e3805175）

**结论摘要**：`campaign_blocked = 11` 是**观测口径错**，不是 s2_campaign 器官真的堵了 11 个节点。
`campaign_nodes.status='blocked'` 是两种语义的合流：**8 个 gate 停放**（放门机制持有，判据不满足
"只等不放"是设计纪律）+ **3 个 task 真阻塞**（mission failed ×2 / infra ×5 agent_lost）。
指标把两者一起数 ⇒ 只要有 ≥5 条战役同时在飞，停放门就常年压线，影子每拍读到同一签名。
**修复=改口径（不动阈值）**：`campaign_blocked` 只数任务级真阻塞（`node_type <> 'gate'`），
停放门进 `extra.gate_held` 留档。修后生产实读数 **11 → 3 < warn 5**，dry-run 判读 `findings=[]`。

- 诊断单：`shadowdiag-7a90c537e3805175`（影子 coordinator_shadow v1，beat 996694）
- 快照：`as_of = 2026-11-07T11:00:00+00:00`；复算：`python3 tools/coordinator_shadow/cli.py once --dry-run --json-out /tmp/beat.json`
- 证据目录：`artifacts/in27_diag_7a90c537/`（`beat_after.json`、`blocked_nodes_prod.txt`、`gate_release_audit.txt`、`campaign_blocked_diagnosis.png`）
- 改动：`tools/coordinator_shadow/pulse.py`、`tools/coordinator_shadow/registry.py`、`tests/test_coordinator_shadow_pulse.py`、新增 `tests/test_shadow_campaign_blocked_semantics.py`

## ① 案例介绍（问题背景与现场）

影子协调官是只读观测器官：它按 `registry.METRICS` 逐指标读脉搏、比阈值、连续同签才立单。
`campaign_blocked` 的判据是 `SELECT status, count(*) FROM campaign_nodes GROUP BY status`，
取 `blocked` 一格。2026-11-07T11:00 该格 = 11 ≥ warn 5（crit 12），连续 3 拍不消 → 立诊断单。

现场（生产库实读，`blocked_nodes_prod.txt` 逐节点留证）：

| 类别 | 条数 | 节点（id·block_reason） | 语义 |
|---|---|---|---|
| **gate 停放** | 8 | 129/144/153/170 `artifact_missing`；165 `no_criteria_declared`；285「评价落地验收门」；340「上游 SR 未齐——继承门永不自开（owner+议会双钥匙）」；331 上游 5/6 done | 放门机制持有（`ops/taskplane/gate_auto_release.py`，`FROM_STATUS='blocked'`），**判据不满足只等不放** |
| **task 真阻塞** | 3 | 67 `mission failed x2`（10-31）、211 `mission failed x2`、260 `infra x5 (agent_lost)` | 派发器失败熔断，**需裁决或复派** |

节点状态总盘：`done 310 / in-flight 32 / blocked 11 / pending 10`（11:00 快照）。

## ② 怎么做的（方法/流程）

1. **复算现场**：按诊断单给的 SQL 直连生产库（只读 socket 信任口）group by status → 复现 11。
2. **拆语义**：逐节点拉 `node_type / block_reason / dependencies / metadata.owner_only`；
   再用放门机制的权威审计表 `gate_release_audit`（末次扫描 03:32:24Z）核对 gate 为何仍 blocked：
   `artifact_missing ×3 / no_criteria_declared ×1 / wait(upstream) ×1 / hold_owner_only ×1`。
   结论：gate 的 blocked 是**放门判据 C1–C3 未满足**的等待态，机制明确"绝不写 failed"。
3. **读实现定口径**：`collect_organs()` 原实现 `node_counts.get("blocked")` 一锅端；
   `ops/taskplane/campaign_dispatcher.py` 的 `sync_phase()` 只在**任务失败耗尽宽限**时写
   `status='blocked'`（`mission failed`/`infra xN`/`hollow success`）——即 task 型的 blocked
   才是故障；gate 型的 blocked 由放门机制独占（dispatcher 从不碰 gate，gate 无 mission_id）。
   ⇒ 结构性判据：`node_type <> 'gate'` 为真阻塞；放门判据的权威实现只有 `gate_auto_release` 一处，
   脉搏层**不复制**门语义（避免两套判据漂移）。
4. **最小改动落地**：只改脉搏层一条探针 + 台账描述，阈值 `warn=5/crit=12` 原封不动；
   原始 `blocked` 计数仍在 `campaign_nodes` 指标、`pulse.blocked.counts`、digest 里（信息零丢失）。
5. **验证**：新增 4 条 pytest（生产形状复现 / 仅停放不报警 / 老 schema 留白 / 证据 SQL 可复算）
   + 影子全套 60 条回归 + 生产库 dry-run 实读。

## ③ 计算结果（程序化注入）

| 量 | 旧口径 | 新口径 | 阈值 | 判定 |
|---|---|---|---|---|
| `campaign_blocked`（生产 11:00） | 11 | **3** | warn 5 / crit 12 | 旧=warn 级越界；新=阈值内 |
| 其中 gate 停放（`extra.gate_held`） | 混入 11 | 8（留档，不报警） | — | 设计态 |
| 其中 task 真阻塞 | 混入 11 | 3 | — | 低于 warn |
| `campaign_nodes` 总量 | 363 | 363 | — | 不变 |
| dry-run 判读 findings | `[s2_campaign.campaign_blocked=11]` | `[]` | — | 报警消除 |

## ④ 结果图

`artifacts/in27_diag_7a90c537/campaign_blocked_diagnosis.png`（双栏）：
(a) `blocked=11` 的两语义分解（gate 停放 8 / task 故障 3）叠 warn 5、crit 12 参考线；
(b) 口径修正前 11（越界，红）→ 修正后 3（阈值内，绿），两条阈值线位置不变——**修的是口径不是阈值**。

## ⑤ 注意点（坑与适用范围）

- **坑 1：把"门停放"当阻塞**。停放的 gate 数量 ≈ 在飞战役数，是**战役并发度的线性函数**；
  任何"绝对条数阈值"都会随并发度漂移，唯一稳定的做法是按 `node_type`/语义分开计数。
  这是"连续 3 拍同签不消"的机理：指标值只增不减的单调量，报警不可能自愈。
- **坑 2：误判成"放门 daemon 死了 → 门该放没放"**。带依赖判据（C1 deps 全 done）看，7/8 门都满足 C1，
  第一眼像 daemon 故障；查 `gate_release_audit` 才发现 C2/C3（工件缺失/判据未声明/治理门）本就不满足，
  **即使 daemon 活着也不会放**。诊断必须落到权威判据，不能只看依赖。
- **坑 3（真实发现，另案）**：`gate-auto-release` daemon 当前**没在跑**（无进程；unit 指向的
  `/root/tp-agent/gate_auto_release.py` 已不存在；`gate_release_audit` 末条 03:32Z，而扫描间隔 300s）。
  本次不修（属 `/root/tp-agent` 运行侧，不在本工作树，且重启放门会放大派发；需 ops 单独评审）。
  影响：C1/C2/C3 现已满足的新门（如 285/340 若工件补齐）不会被自动放行 → 战役推进停在这里。
- **适用范围**：口径修正只对 `campaign_nodes` 且 `node_type` 列存在时有效；老 schema 缺列 →
  指标 `None + withheld`，**绝不填 0**（看不见 ≠ 0）。
- **不动的东西**：影子只读边界、立单纪律、去重冷却、阈值；生产库零写入（全程 only-read + dry-run）。

## ⑥ 误差判定

本单是**观测口径/后端缺陷修复**，无物理实验基准，故不适用"vs 实验/参考值误差"口径；
改用可机器复核的口径一致性判据（如实声明，不粉饰）：

| 判据 | 基准（独立来源） | 结果 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 新口径 `campaign_blocked` | 逐节点人工分类（`blocked_nodes_prod.txt` 11 行的 node_type 列：8 gate / 3 task） | 3 | 0 节点 | **是（0%）** |
| 信息不丢失 | 旧口径总量 11 | `blocked_total=11` | 0 | **是（0%）** |
| 阈值未变 | 基线 `registry.METRICS` warn 5 / crit 12 | 5 / 12 | 0 | **是（0%）** |
| 回归面 | 影子测试套 60 条 | 60 passed | 0 | **是** |

唯一"误差"是语义边界约定：`checkpoint/review` 型节点若未来被用于停放，仍会算作真阻塞
（当前生产库 0 例；若引入需同步此口径）。