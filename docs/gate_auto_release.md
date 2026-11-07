# 放门自动化 daemon（gate_auto_release）——设计 · 演练 · 回滚手册

> 战役节点 IN20-R2（续航军 R2）· 任务 5046 · 仓 TensorPlatform
> 产物：`ops/taskplane/gate_auto_release.py`（机制）、`tools/gate_drill.py`（演练）、
> `tests/test_gate_auto_release.py`（10 条 pytest）、`artifacts/gate_auto_release_drill.json`（数字证据）。
> 本页所有数字由脚本从演练 JSON 注入，非手抄。

## 1 为什么做（背景）

放门（campaign gate）此前是**人工 UPDATE**：
`UPDATE campaign_nodes SET status='pending' WHERE id=<gate> AND status='blocked'`。
人工位有三个结构性缺陷：

1. **依赖人类会话**：owner 不在场，门就永远 blocked，下游节点空等（战役 DAG 快照里表现为
   「上游滞留 + 下游空等」）。
2. **无判据**：谁都可以放，放了不留证据——「谁在何时基于什么放了这道门」不可复算。
3. **不可逆**：放错了没有回退路径，只能再人工改一次，且不留痕。

本机制把这三件事变成机器可执行：**判据化（可复算）+ 审计化（可追溯）+ 可回滚（可撤销）**，
并保证机制替身**独立于任何人类会话与 AI 协调者会话**运行（systemd/容器 daemon，
宿主重启自起，无 LLM、无平台 API 令牌）。

## 2 放门判据（先于实现，机器可裁）

对每个 `node_type='gate' AND status='blocked'` 的节点依次下判：

| 判据 | 内容 | 不满足时 |
|---|---|---|
| **C1 上游就绪** | `dependencies` 中每个节点都存在且 `status='done'` | 继续等（`upstream_pending` / `upstream_missing`） |
| **C2 判据工件可解析** | metadata 声明的验收证据路径能在配置 roots 里解析到**非空文件**；`.json` 还需能 json 解析；声明了 `metadata.repo` 时命中路径必须在该仓内 | 继续等（`artifact_missing`） |
| **C3 非治理门** | `metadata.owner_only` 非真；门名/goal 不含治理标记 | 只标 `owner_only=1`，**永不自动放行** |

判据工件路径来源（优先级）：`metadata.gate_criteria.artifacts` →
`metadata.acceptance_artifacts` → 从 `goal` / `acceptance_criteria` 文本抽
`artifacts/…|runs/…|docs/…` 路径（G13–G20 现实门只有自然语言判据，故必须有这一层）。

**保守原则**（三条都偏「不放」）：

* 一条判据路径都没声明（`no_criteria_declared`）→ 只等不放：不可验证的判据不能自动放。
* 工件 JSON 损坏 → 视为不可解析（"可解析"按字面执行）。
* 工件命中在别的仓 → 记 `repo_mismatch` 不放过（防同名工件跨仓误判），路径留在审计供人复核。

**绝不写 `failed`**：判据不满足只记 `wait` 审计，节点状态保持 `blocked`；门的失败只能
由人/裁决链判（honest FAIL 不可覆盖的机制侧对应物）。

## 3 架构与运行方式

```
campaign_nodes (PG)                 gate_auto_release daemon (systemd/容器, 5min)
  gate blocked ──read──► evaluate_gate() ──release──► status=blocked→pending
        ▲                 (纯函数: C1/C2/C3)          │
        │                                              ├─► gate_release_audit (PG 审计表)
        └──────────── rollback(audit_id) ◄─────────────┴─► audit.jsonl (文件级审计流)
工件 roots: /root/tp-agent/work/*/<repo>/artifacts/...（可多根，支持 glob）
```

* **决策纯函数化**：`evaluate_gate(node, nodes_by_id, roots)` 无 IO、无时钟、无随机，
  同一输入必得同一判词 → 可单测、可离线复算（演练就是把生产 metadata 灌进这个函数）。
* **写路径原子幂等**：「条件 UPDATE + 审计 INSERT」单条 CTE；条件
  （`node_type='gate' AND status='blocked'`）不成立时 UPDATE 不打中、审计也不插入 →
  重复轮询不会二次放门、二次记账（生产实测：连跑两轮审计行数不变）。
* **等待去重**：`wait` 只在「判据原因变化」时记一条（跨进程重启以审计表
  `DISTINCT ON (node_id)` 最近原因为锚），审计不随时间线性膨胀。
* **常驻替身**：`gate_auto_release_loop.sh`（单实例 flock + 指数退避重启）、
  `deploy/taskplane/gate-auto-release.service`（systemd 自愈）、并注册进任务平面
  三方一致清单（`supervise.py` DAEMONS / `taskplane.manifest.yaml` /
  `backend/tests/test_taskplane_infra.py`），随任务平面容器常跑。

## 4 G13–G20 六门演练（真实 DB，2026-11-07）

### 4.1 生产只读矩阵（6 门）

| 门 id | 名称 | 上游 | 判据工件解析 | action | reason |
|---|---|---|---|---|---|
| 129 | 纵贯军G13·体系贯穿验收门 | 8/8 done=True | `artifacts/system_pierce_acceptance.json`→not_found | `wait` | `artifact_missing` |
| 144 | 深水区G14·缺口账本验收门 | 11/11 done=True | `artifacts/deepwater_gap_ledger_update.json`→not_found | `wait` | `artifact_missing` |
| 153 | 自回归G15·自回归回路验收门 | 8/8 done=True | `artifacts/autoregression_acceptance.md`→not_found | `wait` | `artifact_missing` |
| 165 | 饱和池G16·池核销门 | 11/11 done=False | （未声明） | `wait` | `no_criteria_declared` |
| 170 | 战史官G18·顶刊报告验收门 | 4/4 done=True | `artifacts/chronicler_acceptance.json`→not_found | `wait` | `artifact_missing` |
| 185 | SPEC军G20·S×E闭环验收门 | 9/9 done=True | `artifacts/spec_impl_acceptance.json`→not_found | `hold_owner_only` | `governance_marker=SPEC军` |

生产全表 `(id,status,node_type)` 快照 sha256 前后一致：
`56e8ab792ceeeb40db68538ab2eb5927…`（`production_untouched=True`），
即**演练未改动任何生产节点状态**。

判读：6 门中 0 门满足、5 门继续等、
1 门留人工位。G13/G14/G15/G18 上游全 done 但**判据工件
尚未落盘**（worktree 内确无 `artifacts/system_pierce_acceptance.json` 等）→ 机制选择继续等
而不是放行，这正是它替代人工后仍能挡住空放行的位置。G16 上游 7 个节点仍 `in-flight`，
且 goal 只声明「脉冲数据在 saturation_pulse 表可查」（无文件工件）→ `no_criteria_declared`。
G20 命中治理标记 `SPEC军` → `hold_owner_only`，并已在生产 metadata 写入 `owner_only=1`。

### 4.2 沙箱写入路径（隔离 schema `gate_drill`，真 UPDATE + 全量回滚）

同六门 + 上游节点复制进 `gate_drill.campaign_nodes`，判据工件在临时根下按
`<repo>/artifacts/…` 同形造出（内容显式标注 `synthetic: true`），跑同一份 `run_once`：

| 门 id | action | reason |
|---|---|---|
| 129 | `release` | `satisfied` |
| 144 | `release` | `satisfied` |
| 153 | `release` | `satisfied` |
| 165 | `wait` | `no_criteria_declared` |
| 170 | `release` | `satisfied` |
| 185 | `hold_owner_only` | `governance_marker=SPEC军` |

随后对每条 `release` 审计逐条 `rollback`：4 条释放全部还原为
`blocked`，`release↔rollback` 配对 4/4 全通过，
审计行字段完整（node/action/from/to/reason/actor/run_id/detail 均非空）。沙箱 schema 用完
即 `DROP SCHEMA … CASCADE`，不污染生产。

结论：**满足者自动放（4/6）+ 不满足者留（G16 上游在飞）+
owner_only 保护负例不放（G20）** 三条主路径都在真 SQL 上跑通，且可逆。

### 4.3 生产审计链（真跑一轮 `--once`，非 dry-run）

`gate_release_audit` 首轮落 7 行（G20 `hold_owner_only` ×1 + 六门 `wait` ×6，另含 plan 21 的
G21 一个 `wait`），二轮再跑**不新增**任何行（幂等）。G20 metadata 变为
`owner_only=1 / owner_only_reason=governance_marker=SPEC军 / owner_only_marked_at=…`。
JSONL 文件流：`artifacts/gate_auto_release/audit.jsonl`；同轮 summary：
`artifacts/gate_auto_release_run_once.json`。

## 5 运维手册（假设明天没有人类来）

```bash
# 常驻（systemd，推荐）
cp deploy/taskplane/gate-auto-release.service /etc/systemd/system/
systemctl enable --now gate-auto-release

# 或宿主壳（单实例 flock + 崩溃指数退避重启）
GATE_PG_USER=foundation GATE_PG_PASSWORD=*** ops/taskplane/gate_auto_release_loop.sh &

# 观测：本轮判词（不写库）
python3 ops/taskplane/gate_auto_release.py --once --dry-run

# 撤销一次自动放门（回滚也进审计；同一行不可二次回滚）
python3 ops/taskplane/gate_auto_release.py --rollback <audit_id>

# 复盘某门为什么没放
psql -d tensorplatform -c "SELECT id,node_id,action,reason,detail->'artifacts' \
  FROM gate_release_audit WHERE node_id=<gate> ORDER BY id DESC LIMIT 5;"

# 重跑六门演练（本地 PG 读权限 + 沙箱 schema 写权限）
runuser -u postgres -- python3 tools/gate_drill.py --pg-host "" --pg-user postgres \
    --json-out /var/tmp/gate_drill.json
```

**回滚语义**：`--rollback <audit_id>` 仅在「审计行是 release、未被回滚过、节点当前仍是
`pending`」三条同时成立时生效：节点退回 `blocked`、原审计行打 `rolled_back=true`、
再插一条 `action='rollback'`（`rollback_of` 指向原行）。任一不满足即拒绝，不留半截状态。

## 6 局限与适用范围（honest）

1. **只判「工件在不在、json 能不能解析」，不判「内容对不对」**：证据真实性/质量由验收链
   （复现判决、报告 QA）负责；本机制是**放门执行器**，不是验收器。工件内容为
   `{"verdict": "pass"}` 的空壳也能过 C2——这是设计边界，不是漏判。
2. **工件解析依赖 filesystem 可见性**：daemon 必须能看到 `work/<mission>/<repo>/artifacts/`。
   若战役工件只存在于远端分支、宿主 worktree 已回收，则判据恒不满足（只会等，不会错放）；
   这类门需人补 `metadata.acceptance_artifacts` 指向常驻路径（如 `runs/` 归档）。
3. **治理门识别是标记 + 关键词启发**：`SPEC军`/`规范修订`/`规范落实`/`治理规则`/
   `gate_class ∈ {governance, spec_revision, policy}`；关键词可能误伤（普通门被判成
   owner_only）。保守方向是「多留人工位」而非「多放」，误伤代价 = 门等人 review，可用
   `gate_class` 显式声明或改名消除，每次标记都落审计可查。
4. **`pending` 只是给下游派发器看得见的信号**：本机制不派单；派单节奏仍由
   `campaign_dispatcher` 决定（放门 ≠ 开单），二者解耦。
5. **生产侧至今尚无「真放」案例**：6 门判据都不满足（工件未落盘/上游在飞/治理门），
   因此生产自动释放路径只有沙箱实证 + 单测覆盖，**没有生产 release 审计行**。
   这是事实陈述不是粉饰；G13/G14/G15/G18 的验收工件落盘后机制会自动接管。
6. **未覆盖**：门被判 `failed` 后的重开（`mission_reopen` 线）、跨 plan 的批量政策
   （如「本波门一律等 60min 再判」）、工件 sha256 与上游报告的对账。

## 7 复现与验证

```bash
python3 -m pytest tests/test_gate_auto_release.py -q          # 10 passed
python3 -m pytest backend/tests/test_taskplane_infra.py -q    # 16 passed（三方一致 + env 覆盖）
python3 tools/make_gate_drill_figure.py                       # artifacts/gate_auto_release_drill.png
```

图示 `artifacts/gate_auto_release_drill.png`：左=六门判据矩阵（上游/工件/非治理 × 6 门），
右=生产与沙箱动作计数 + 审计链（release↔rollback 配对）。
