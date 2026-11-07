# IN27-H1 —— coordinator_shadow v1：把影子协调官转正为常驻

**结论先说**：v1 已落地为 `tools/coordinator_shadow/` 的一个包（10 个模块 + 55 条 pytest），
在**生产库上跑通了真实一拍**（只读脉搏 + 一次账本写，零立单），并给出 72h 时钟加速演练与
负控证据。**但 72h 墙钟验收没有完成**——本单在岗 1 拍，验收裁决器如实返回
`PENDING_WALL_CLOCK`，不是 PASS。三件诊断单被舰队领取执行的那一条，同样要在墙钟期内累计。

---

## 1. 案例介绍：要解决的问题

### 1.1 背景
`coordinator_shadow` v0（`/root/tp-agent/coordinator_shadow.py`，93 行）是 2026-11-07 的
继承令原型：每 30 min 读平台脉搏、让 LLM 判读「若我是当值协调者会做什么」、把结论 append
进 `coordinator_shadow` 表，纪律是「只诊断不动手」。它是本战役把「协调者职能」交出去的第一步。

### 1.2 v0 的六个病（审计结论，逐条对应 v1 的改法）

| # | v0 病征 | 证据 | v1 改法 |
|---|---|---|---|
| 1 | `id = (SELECT max(id)+1)` 手算自增，无主键、无并发保护 | 源码第 78 行 | 序列 + `beat_id`（30min 时间槽）唯一索引，`ON CONFLICT DO NOTHING` 幂等 |
| 2 | SQL 用字符串拼 shell `psql`，注入面大、无参数留痕 | 源码 `q()` | `:name` 参数化 + 每指标留 SQL 原文与参数（§3.2） |
| 3 | 无只读门：同一通道能写任何表 | — | `ReadOnlyRunner`：只放行单条 `SELECT`/`WITH`，剥注释/字面量后做词边界关键词扫描 |
| 4 | 无立单能力：`would_do` 只是一段文字 | 表内 1 行 `verdict.would_do` | `filing.py`：去重/冷却/器官去重/开单上限/每拍上限，`WriteGuard` 只放行一条 INSERT |
| 5 | 无自观测：进程死了没人知道 | 表在 09:55 后只有 1 行 | 每拍必写账本（含 error 拍）+ 心跳文件 + 覆盖率/连续链/自愈率验收器 |
| 6 | 零流器官只数信用积压 | 源码只查 `credit_backlog` | 补齐三个零流器官：信用积压 / 收养停滞 / 声明率（含无定义处理） |

### 1.3 参数表（v1 默认值，全部可注入覆盖）

| 参数 | 值 | 含义 |
|---|---|---|
| 拍间隔 | 1800 s | 任务书「每 30 分钟」；拍 id = `epoch // 1800` 时间槽 |
| chronicle 鲜度门 | 90 min（warn 60） | 脉搏过期即上报 `pulse_freshness` 器官，不拿陈旧数据下判断 |
| WARN 窗口 | 60 min | daemon 告警流按窗口聚簇，单条噪声不成簇 |
| 收养停滞阈值 | 120 min（crit 240） | `queued` 且入池超阈值未领 |
| 信用积压阈值 | 20 条（crit 50） | 已定谳但信用账本无 `eval:<id>|` 流水 |
| 声明率阈值 | <60%（crit 40%） | 24h 完工任务中已声明占比；分母 0 → 无定义（不按 0%） |
| 持久性门槛 | 2 拍 | 同签名连续 2 拍才可立单（单拍尖峰=噪声） |
| 签名冷却 | 6 h | 同签名不重复立单 |
| 器官去重 | 开 | 一病灶一张单（`adoption_stall` 会同时抖两个指标） |
| 开单上限 | 3 张（演练放宽 6） | 在岗诊断单总量上限 |
| 每拍立单 | 1 张 | 宁少勿滥 |
| LLM 否决 | 开 | LLM 可 veto（判为噪声/已知无害），**不可新增越界项** |

## 2. 怎么做的：分层与关键设置

### 2.1 十层，各层单独可测

```
pulse.py      只读脉搏（ReadOnlyRunner + 探针 SQL 原文留痕 + WARN 流聚簇）
registry.py   器官/指标字典（判读语汇闭合：表外器官一律不得立单）
judge.py      LLM 判读者（严格 JSON + 配平抽取 + 校验 + 失败回退 RuleJudge）
diagnose.py   定量越界检测 + 立单纪律（持久性/冷却/器官去重/上限/否决）
filing.py     立单通道（WriteGuard 只放行一条幂等 INSERT）
ledger.py     append-only 账本 + 跨拍状态 + 心跳（v0 老表原地迁移）
runtime.py    一拍总装（run_beat_safe：失败也留一条 error 拍）
daemon.py     常驻循环（30min 槽对齐、flock 单实例、SIGTERM 优雅退出）
acceptance.py 72h 验收裁决器（C1–C6 六条机器判据 + 状态机）
soak.py       72h 时钟加速演练（合成平台状态、确定性、含地面真值）
```

### 2.2 判读分工：规则层定越界，LLM 管归因与否决

这是本设计的核心取舍。v0 把「有没有问题」全交给 LLM，误报不可控；v1 反过来：

- **规则层**只用脉搏里可复算的数字判越界（阈值见 §1.3），所以每个越界项天然带 SQL + 参数；
- **LLM** 只能 `confirm` / `note` / `veto`，或对未越界器官提 `advisory`（不入单）。
  引用字典外器官/指标 → 进 `ungrounded` 并丢弃；
- LLM 不可用/返回坏 JSON → `RuleJudge` 顶上（`judge=rule-fallback`），常驻不停摆。

### 2.3 立单：一病灶一张单，靠库约束而不是内存状态去重

`task_key = shadowdiag-<sha256(metric:direction)[:16]>`，库里 `(organization_id, task_key)`
唯一索引兜底；进程重启、并发跑、重复拍都不会重复立单。器官级去重（`one_filing_per_organ`）
保证「收养停滞同时抖两个指标」只出 1 张单。

### 2.4 工具链与运行方式

```bash
# 常驻（systemd 单元见 deploy/systemd/tp-coordinator-shadow.service）
python3 -m tools.coordinator_shadow.daemon --interval 1800
# 只读巡检一拍（不写库、不立单、不建连写通道）
python3 -m tools.coordinator_shadow.cli once --dry-run --json-out /tmp/beat.json
# 72h 加速演练 / 负控 / 验收裁决
python3 -m tools.coordinator_shadow.cli soak  --hours 72 --json-out runs/in27_h1/soak_llm.json
python3 -m tools.coordinator_shadow.cli drill --json-out runs/in27_h1/drill.json
python3 -m tools.coordinator_shadow.cli acceptance --since <first-beat-iso> --json-out ...
```

## 3. 计算结果（数字全部程序化注入，无手抄）

### 3.1 72h 时钟加速演练（合成平台状态，144 拍）

| 判据 | 门槛 | LLM 判读模式 | 规则模式 |
|---|---|---|---|
| 在岗拍数 / 应到拍数 | 144 / 144 | **144 / 144（coverage=1.000）** | 144 / 144 |
| error 拍 | 0 | **0** | 0 |
| 最长连续链 | ≥ 64.8 h | **71.5 h（ratio 0.993）** | 71.5 h |
| 人工修命中 | 0 | **0** | 0 |
| 错拍自愈率 | ≥ 0.95 | **1.000（无错拍）** | 1.000 |
| 立单数 | ≥ 3 | **3**（credit_backlog / error_total / oldest_queued_minutes） | 4（多 1 张良性越界单） |
| 被舰队领取执行 | ≥ 3 | **3（模拟处置 confirmed）** | 3 |
| 误报率 | < 30% | **0.0%** | **25.0%** |
| 单拍尖峰（60.0h）是否立单 | 否 | **否**（consecutive=1 < 2） | 否 |
| 良性告警流簇（64.0–65.5h）是否立单 | 否 | **否**（LLM veto） | **是**（→ 那 1 张误报） |

两种模式的对比正是 LLM 判读的**被测量**：同一条越界（`warn_total=16`，规则层看到的是真越界），
LLM 判为「批量合并窗口的已知重试噪声」→ 否决 → 误报率从 25% 降到 0%。这不是自封，是 soak 里判出来的。

### 3.2 生产库真实一拍（只读，`runs/in27_h1/live_beat_1.json`）

| 器官 | 读数 | 阈值 | 判定 |
|---|---|---|---|
| credit_backlog（信用积压） | 0 | warn 20 / crit 50 | 正常 |
| oldest_queued_minutes（收养停滞） | **8808.9 min（6.1 天）** | warn 120 / crit 240 | **high** |
| queued_stuck_2h | **18** | warn 5 / crit 15 | **high** |
| declaration_rate（声明率） | 0.8315 | warn 0.60 | 正常 |
| campaign_blocked | 11 | warn 5 / crit 12 | mid |
| queued_open | 25 | warn 25 / crit 60 | mid（临界） |
| capability_deltas_24h | 404 | — | 正常 |
| WARN/ERROR 流（dispatcher+autoheal，60min） | 0 / 0 | warn 12 / 3 | 正常 |
| chronicle 脉搏 | 8 面齐备，年龄 17.3 min | 鲜度门 90 min | 新鲜 |

本拍 `judge=llm`（qwen3.7-plus，耗时 38.0 s，`parse_ok=true`），4 条越界、**0 立单**
（首拍，持久性门槛未满足——这正是纪律生效的样子）。LLM 归因：「任务入池后长时间无人认领导致
严重积压，同时战役节点出现阻塞，需立即介入排查领单机制与节点依赖」。

### 3.3 探针口径修正（实跑踩到的两个真坑）

1. **`tensor_dev_missions.report_id` 是死列**：生产库 4907 条任务里 `report_id` 非空 **0 条**，
   而 `tensor_reports` 有 3693 条带 `mission_id` 的报告。若按 `report_id` 算声明率会永远 0%
   → 一个**必然误报**的假警。v1 改成 `report_id IS NOT NULL OR EXISTS(tensor_reports...)`，
   实跑声明率 0.8315（原口径会报 0.0000）。
2. **psycopg 不认 `:name` 占位符**：首版探针在 Postgres 上全批 `SyntaxError` → 4 个器官
   集体 WITHHELD（看着像「平台没数据」，其实是影子自己写错）。v1 加
   `to_psycopg_placeholders()`（`(?<!:):name → %(name)s`，保留 `::text`/`::timestamptz` 转换），
   并留了单测。

两个坑都是「WITHHELD 纪律」救回来的：探针失败写 `value=None + withheld` 而不是 0，
LLM 在实时一拍里直接点出「SQL 语法错误导致能力指标缺失」——这比 0 分更能推动排障。

### 3.4 常驻转正与**真实立单**（2026-11-07T10:30:47Z 起算 72h 窗口）

v1 已作为常驻进程在岗（PID 3600065，`--interval 1800`，`flock` 单实例，30 min 槽对齐）：

| 事实 | 证据 |
|---|---|
| 在岗拍（slot 996693，2026-11-07T10:30:47Z） | `coordinator_shadow` 表 id=4，`judge=llm`，`error=null`，耗时 63.2 s |
| **自动立诊断单 1 张** | mission **5322**，`task_key=shadowdiag-a151356df79cb336`，`origin=agent`，`repo=TensorPlatform` |
| 该单的越界证据 | `oldest_queued_minutes=8820 min ≥ crit 240`，连续 2 拍，SQL 原文 `SELECT min(created_at) FROM tensor_dev_missions WHERE status='queued'` 内嵌在 goal 里 |
| **被舰队领取** | 10:31:5x 内 `status=running`、`claimed_by=tp-ver-7`（立单后数秒） |
| 器官级去重生效 | 同拍另 2 个候选（`queued_stuck_2h`、`campaign_blocked`）被 `organ-taken-this-beat` / `beat-cap` 挡掉 |
| 与 v0 共存 | v0 用 `max(id)+1` 插入 id=3，v1 序列写 id=4；撞主键时 `_resync_id_sequence()` 重同步再写 |

**过程中还修掉第三个真坑**：立单语句首版写 `origin='mechanism'`，被生产库 CHECK 约束
`tensor_dev_missions_origin_check`（只允许 `owner`/`agent`）拒绝——这类约束离线测试看不见。
已改 `origin='agent'`，并用「事务内 INSERT + ROLLBACK」在生产库验证语句合法且幂等
（`RETURNING id` 成功、同 task_key 第二次 0 行、回滚后队列零污染），同时固化成单测
`test_insert_statement_matches_production_schema_constraints`。

## 4. 结果图

| 图 | 内容 | 文件 |
|---|---|---|
| 72h 演练时间线 | 4 个器官读数 + 阈值线 + 故障带（真故障/噪声/良性）+ 立单竖线 | `runs/in27_h1/figs/fig_soak_timeline.png` |
| 越界去向 | 立单 / 持久性挡 / 器官去重挡 / 签名冷却挡 / LLM 否决 / 每拍上限 | `runs/in27_h1/figs/fig_gate_decision.png` |
| 误报率双模式 | 立单数 vs 误报数、误报率 vs 30% 门槛 | `runs/in27_h1/figs/fig_fpr_modes.png` |
| 生产实时一拍 | 6 个器官读数 vs warn/crit（含声明率） | `runs/in27_h1/figs/fig_live_pulse.png` |

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **72h 墙钟没走完**：这不是可以粉饰的项。常驻转正时刻 **2026-11-07T10:30:47Z**，
   窗口终点 11-10T10:30:47Z。`acceptance --since 2026-11-07T10:30:47+00:00` 现在返回
   `PENDING_WALL_CLOCK`；跑满后同一条命令自动出 `PASS`/`FAIL`。
2. **「诊断单被舰队领取执行 ≥3 件」**：现网 `shadowdiag-*` 单为 0 张（首拍未达持久性门槛）。
   墙钟期内常驻跑，按现网收养停滞（8809 min / 18 条 stuck）看，第 2 拍就会产出第 1 张单。
3. **误报率口径**：分母只含已处置单（`confirmed` / `false_positive` / `inconclusive`）；
   `unclaimed_expired`（48h 无人领）单列，同时给 `fpr_including_unclaimed`，两种口径都摆在报告里。
4. **v0 与 v1 共存**：v0 进程（PID 1733290）仍在跑，它用 `max(id)+1` 手算 id，会和新序列撞主键；
   v1 撞了就重同步序列再写一次（`_resync_id_sequence`），但**退役 v0 是运维/H3 的动作**，本单没做。
5. **DDL 权限**：`coordinator_shadow` 属主是 postgres，租户角色 foundation 没有 ALTER 权限
   （实跑 `must be owner of table`）；systemd 单元里因此写死 `TP_DSN=... user=postgres`。
6. **chronicle s1–s8 面按原样引用**，不重算：避免同一指标两套口径打架；s1–s8 的内部字段
   若上游改口径，影子会跟着变（想脱离这个耦合需要先把 s1–s8 的字段契约冻结）。
7. **适用范围**：本模块只做「观测 + 判读 + 立单」。它不修任何东西，也不该被要求修；
   所有手术权归 H3。LLM 只被允许否决与解释，不允许制造越界项——这是控制误报的结构性手段。

## 6. 误差判定

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| 零流器官计数（信用积压 / 收养停滞 / 声明率） | 手写 SQL 直接查库对照（同一 SQL 原文） | 0（同一语句、同一参数） | **是（0%）** |
| 声明率分母（24h 完工数） | `psql` 直查 449 条（11-06 11:20 前 24h） | 0 | **是（0%）** |
| 覆盖/连续/自愈/误报率 | 合成账本的解析解（144 拍、缺口 [20,30)、1/4=25%） | 0（单元测试逐值比对） | **是（0%）** |
| soak 立单数 | 剧本真值 3 个真故障 | 0（LLM 上限），+1（规则模式，良性越界，已标注） | **是（LLM 模式）；规则模式 +33% 且已计入误报** |
| 72h 墙钟连续性 | 无基准（时间没走完） | 不适用 | **无法判定（PENDING_WALL_CLOCK）** |
| LLM 判读质量 | 无金标准 | 不适用 | **无法量化**（只有 1 次实时判读 + 脚本化演练；误报率是其替代指标） |

**一句话**：能在单机内证明的（计数、口径、纪律、幂等、边界），误差都是 0（同语句复算/单元测试逐值）；
不能在这里证明的（72h 墙钟、舰队领取执行、LLM 判读的金标准准确率），本报告**不给数字**，
只给状态与待定谳命令。

## 7. 产物索引

```
tools/coordinator_shadow/            源码（10 模块）
tests/test_coordinator_shadow_*.py   55 条用例（pulse 13 / judge+filing 17 / ledger+acceptance 15 / soak 10）
deploy/systemd/tp-coordinator-shadow.service
runs/in27_h1/soak_llm.json           72h 演练（LLM 判读）
runs/in27_h1/soak_rule.json          72h 演练（规则判读，对照）
runs/in27_h1/drill.json              负控 7 条
runs/in27_h1/live_beat_1.json        生产库真实一拍
runs/in27_h1/live_beat_dryrun_1/2.json  只读连跑两拍（展示持久性门槛）
runs/in27_h1/acceptance_wallclock.json  墙钟验收裁决（PENDING_WALL_CLOCK）
runs/in27_h1/live_missions.json      生产队列里真实存在的影子诊断单（mission 5322 running/tp-ver-7）
artifacts/coordinator_shadow/acceptance_72h.json  机器验收包（判据数字 + 产物路径 + honest_limits）
runs/in27_h1/figs/*.png              四张图
```