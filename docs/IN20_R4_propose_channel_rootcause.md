# IN20-R4 · 自主提案通道根因与疏通（propose_channel）

> 战役：IN20 续航军 R4（TensorPlatform）· 任务 m5048 · 分支 `agent/m5048`
> owner 渐退令前置件：**创新入口不能堵；机制替身必须独立于人类会话与 AI 协调者会话**。
> 判据（本文件末节逐条对账）：9 张积压全数终态 · 根因链文档化 · 防复发守护+负例 · pytest ≥ 6。

## 1. 现象

9 张 `propose-*` 单自 **2026-11-07 00:35:24** 起 `queued` 无人领，历史 38 张成功。同时 `wave_cycle.log`
在 2026-11-02~11-05 几乎无记录（"长期空转"）。

## 2. 证据链（全部可重放）

### E1 积压的模型分布是**单点**的，不是随机的

```sql
SELECT model, count(*), min(created_at) FROM tensor_dev_missions WHERE status='queued' GROUP BY 1;
--  glm-5.1 | 9 | 2026-11-07 00:35:24+00        ← 全部 9 张，且只有这一种模型
```

### E2 每个提案波的三槽位结局：钉死模型名的那个槽 100% 死

| 槽位 task_key | 钉的模型 | 结局（2026-11-07 03:33 实测） |
|---|---|---|
| `propose-p*-w1-1` (p11..p20) | `qwen3.7-plus` | succeeded ×9 |
| `propose-p*-w1-2` (p11..p20) | **`glm-5.1`** | **queued ×9（本单积压）** |
| `propose-p*-w1-3` (p11..p20) | `deepseek-v4-flash` | succeeded ×9 |

同一波、同一 goal 模板、同一 goal 长度、同一派发时刻——**唯一变量是模型名**，
故"9/9 全灭"不可能是内容/预算/优先级问题。

### E3 领取端的过滤式：钉模型 = 必须有一个同名活体 daemon

`backend/api/tensor_missions.py:564`：

```python
if body.model and body.claim_null_model:
    model_filter = "AND (model = :claim_model OR model IS NULL)"
```

即：钉在 `glm-5.1` 的单，**只有** model 恰为 `glm-5.1` 的 worker 进程能领或"未钉模型"的单可被任何人领。
钉住的单不会被别的模型领走 → 该模型一旦无活体 daemon，单就是永久 queued。

### E4 `glm-5.1` 的"活体"是名册幻觉

`factory_resident_workers` 里 `tp-gen-4` 行：`model=glm-5.1, enabled=t, state=idle, heartbeat 8min 前`；
但进程命令行是 `tp_worker.py --model deepseek-v4-flash`。**名册行心跳不证明该 model 有 daemon**
（worker 心跳只按 worker_id 写行，model 列被改过也能继续"心跳"）。

行为级证据（唯一不会撒谎的）：

```sql
SELECT model, max(heartbeat_at) FROM tensor_dev_missions WHERE claimed_by IS NOT NULL GROUP BY 1;
--  deepseek-v4-flash | 2026-11-07 03:21   (活跃)
--  qwen3.7-plus      | 2026-11-07 03:12   (活跃)
--  glm-5.1           | 2026-11-01 04:35   ← 5 天 23 小时前的最后一次 claim
```

**glm-5.1 名下一次真 claim 都没有 6 天**，而 9 张 glm-5.1 单在池里等了 3 小时 → 领取路径对该模型是死的。

### E5 仲裁者永不点火：一个非终态槽位锁死整条创新链

`ops/taskplane/campaign_dispatcher.py`（修复前）：

```python
TERMINAL = {"succeeded", "failed", "stopped", "revoked"}     # 缺 suspended / orphan
props = [_mission(m) for m in aut["proposers"]]
if not all(p and p["status"] in TERMINAL for p in props):
    continue  # 提案者仍在思考        ← 静默 continue：10 天无人发现通道堵死
```

于是 9 个 plan 的 `metadata.autonomy` 恒为 `{"proposers":[a, queued, b], "arbiter": null}`：

```sql
SELECT id, metadata->'autonomy' FROM campaign_plans WHERE id IN (11,12,13,15,16,17,18,19,20);
```

→ 仲裁者（聚合提案→播种下一场战役）永不派发 = **自主提案/创新入口整条断流**。
同类第二例：`p9-w1-2` 自 **2026-11-01** 起状态 `suspended`（既非终态也非 queued），
旧 TERMINAL 集不认 → plan 9 提案波堵 **504207 s（5.8 天）**。

### E6 wave_cycle "长期空转"的真实成因（不是它在睡觉，是它在等人）

```
[2026-11-01 20:34:27] session-005 无 r3_plan.json，失败计 3
[2026-11-01 20:34:27] 熔断：连续 3 场议事会未产出计划，熔断待人工
     …… 2026-11-02 ~ 2026-11-05 日志零记录（进程存活，只写状态文件） ……
[2026-11-05 23:56:45] wave_cycle 启动（人工重启）
[2026-11-06 14:54:12] 轮异常 AssertionError: login 401
```

三处会话依赖：
1. 派发**只**认议事会产出 `r3_plan.json`（外部 LLM CLI 会话 = AI 协调者会话）；会话失败=池不续火。
2. 连败 3 场写 `st["halt"]` → **熔断等人**（渐退令明令禁止的形态：明天没有人类来，机制就躺平）。
3. 空转期间只 `write_status` 不 `log` → 日志零记录，运维无从判断"活着还是在等人"。

### E7 三级缺陷（潜伏，未致本次积压但会致"重派无效"）

`POST /tensor/missions` 对「同 task_key + 同指纹」返回 `HTTP 200 {id: 旧单, idempotent: true}`
（`backend/api/tensor_missions.py:167`），**旧单即便已终态也照返**。旧 `_dispatch_mission` 把该 id
当"新点火"收下 → 「提案者连败→重派第 n 波」实际复用死单，重派从未发生（静默空转）。

## 3. 修复（最小改动，三个文件 + 一个守护）

| 位置 | 改动 | 治什么 |
|---|---|---|
| `campaign_dispatcher.py` | `pick_proposer_models()`：只在**名册活体**模型里钉，全灭则 `[None]`（不钉模型→任何 worker 可领）| 不再造永久领不走的单 |
| 同上 | `TERMINAL` 增 `suspended/orphan` | p9 型 6 天堵塞 |
| 同上 | `_dispatch_mission` 幂等命中**终态**旧单 → 纪元后缀 `-r2/-r3` 重派（在飞则仍复用）| 重派不再复用死单 |
| 同上 | `_note_proposer_stall()`：堵点写日志一次 | 静默 continue → 可见告警 |
| `wave_cycle.py` | `dispatch_fallback_wave()`：无议事会时按 `campaign_nodes` 就绪节点**兜底派发**（`task_key=cnode-<id>` 幂等）；白名单补 `ship-performance-platform` | 会话挂了池也续火（session 无关替身）|
| 同上 | 连败 → `fallback_mode`（**不再 halt 等人**）；每小时 `idle-heartbeat` | 空转可观测、不依赖人工重启 |
| **新增** `ops/taskplane/propose_channel_guard.py` | S1 领取路径守护（带理由撤销 / 改钉活体）、S2 仲裁推进诊断、S3 JSONL 审计 + `rollback-*.json` 回滚；`--loop` 可容器常驻 | 防复发（机制替身，独立于任何会话）|

判定口径（行为证据优先）：
`claimable = 名册活体 ∩ claim 活动证据`；`名册活体 − 活动证据` 单列为 `roster_without_activity`
（E4 的名册幻觉）。宽限 `stall_secs=1800` 内不动手，避免与正常 claim 抢跑。

## 4. 实跑结果（2026-11-07 03:34:06Z，`--apply`）

```
withdraw m4967 propose-p17-w1-2 glm-5.1  (age=10721s)
withdraw m4979 propose-p12-w1-2 glm-5.1  (age=9996s)
withdraw m4984 propose-p11-w1-2 glm-5.1  (age=9298s)
withdraw m4987 propose-p16-w1-2 glm-5.1  (age=8666s)
withdraw m5006 propose-p13-w1-2 glm-5.1  (age=7165s)
withdraw m5009 propose-p18-w1-2 glm-5.1  (age=6898s)
withdraw m5028 propose-p19-w1-2 glm-5.1  (age=5246s)
withdraw m5032 propose-p15-w1-2 glm-5.1  (age=3283s)
withdraw m5035 propose-p20-w1-2 glm-5.1  (age=1844s)
withdraw m1572 propose-p9-w1-2  glm-5.1  (suspended, age=504207s)
```

- 终态核验：`status='stopped'`，`result.stop_condition='unclaimable_model'`，
  `result.guard.reason` 记录完整理由；**全局 queued 归零**。
- 通道回流量（同一 dispatcher，未重启）：5 秒后日志出现
  `AUTONOMY plan {20,19,9,16,13,15} w1 提案齐（2/3 成）→ 仲裁者 m5059..m5064`，
  即积压一清，仲裁立即点火（此前 8 小时零仲裁）。
- 审计/回滚：`artifacts/propose_channel_guard/audit-20261107.jsonl`（逐单 before/after/sql）、
  `rollback-20261107T033406Z.json`（`--rollback` 可把 10 张单原样恢复 queued/suspended）。

## 5. 回归与负例

`tests/test_propose_channel_guard.py`（25 passed）：

- 判定层：活体名册过滤、行为证据窗口、**名册撒谎负例**（名册有 glm-5.1 但零活动 → 仍须撤销）、
  **活体模型负例**（可领单必须 keep，不误伤）、宽限期负例、`repin` 策略、`suspended` 槽位。
- 审计层：dry-run 不碰 DB、apply 写 JSONL+回滚记录、撤销只对 `queued/suspended` 生效、回滚 SQL 恢复原状态。
- 派发层：`pick_proposer_models` 剔除死模型 / 全灭退不钉 / 非候选活体兜底；`TERMINAL` 含 suspended；
  幂等命中终态旧单换纪元、在飞单复用。
- 兜底层：`fallback_rows` 载荷构造（别名归一、白名单裁剪、空 goal 跳过、预算帽）、
  `ready_nodes_sql` 幂等去重、无 session 派发成功/池空降级、**"连败不许 halt 等人"**源码断言。

```
python3 -m pytest tests/test_propose_channel_guard.py -q        → 25 passed
python3 -m pytest tests/test_structure_audit.py tests/test_campaign_throughput.py -q → 23 passed, 1 skipped
```

## 6. 局限与残留风险（honest）

1. **未重启 live daemon**：`campaign_dispatcher.py` / `wave_cycle.py` 的补丁在磁盘上（本分支），
   运行中的进程仍是旧代码。本次疏散靠 `propose_channel_guard.py`（独立于两 daemon，故不需要重启）。
   下一次 daemon 自然重启即生效；`_dispatch_mission` 纪元修复与 `fallback_mode` 行为**尚未在生产进程里跑过**
   （仅脱库单测 + 代码审阅证明）。这是本单最大残留风险，如实记录。
2. **仲裁者钉 `qwen3.6-plus`**：该模型名下的 claim 活动最近一次在 01:11（>1h 窗口），
   新派 6 张 arbiter 若 30min 内无人领，`propose_channel_guard --loop --policy repin` 会自动改钉活体模型；
   本单未常驻 loop（避免越权改平台时序），只留可作为机制替身。
3. **`--policy repin` 会改 `model` 列**语义（提案者异构性下降）：默认 `withdraw`，改钉需显式选择；
   两条路径都有回滚记录。
4. **根因 E4 的成因未追到底**：谁把 `tp-gen-4` 行 model 改成 `glm-5.1`（造成名册幻觉）未定案；
   本单只把"名册不可信"固化进判定口径（行为证据优先），未改 worker 心跳写行逻辑。
5. 平台当前处于 **冻结期（2026-11-02）**：本单是 IN20-R4 战役节点显式指派的平台机制修复
   （渐退令前置件），改动限于 `ops/taskplane/` 的提案通道编排 + 新增守护，未触碰 harness 核心
   （任务机/权限/报告/审计）。

## 7. 判据对账

| 判据 | 结果 |
|---|---|
| 9 张积压全数终态 | ✅ 9/9 `stopped`（带 `result.guard.reason`）；另修 1 张 suspended（m1572）；全局 queued=0 |
| 根因链文档化（证据引用） | ✅ E1–E7，每条附 SQL/源码行/日志原文 |
| 防复发守护 + 负例 | ✅ `propose_channel_guard.py`（S1/S2/S3 + 回滚）+ 名册撒谎/活体不误伤等负例 |
| pytest ≥ 6 | ✅ 25 passed（另 23 passed / 1 skipped 关联套件） |
| 机制独立于人类/AI 会话 | ✅ guard 与 `fallback_mode` 均不依赖议事会；wave_cycle 自动熔断等人逻辑删除 |